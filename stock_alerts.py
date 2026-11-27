"""내 보유·관심종목 맞춤 알림 (PWA 웹 푸시 + 헤더 종 인앱 알림).

규칙(stock_alert_rules, 사용자·종목당 1행)
- 목표가 이상 · 손절가 이하: 장중 5분 체크(bot_engine 스케줄러, 시장별 정규장 + _RUN_LOCK 안)에서 판정. 한 번 울리면 그 조건을 자동 해제.
- RSI(14, 일봉 + 현재가) ≤ 30 · ≥ 70: 켠 종목만 30분 캐시로 계산, 같은 (사용자, 종목, 종류) 24시간 쿨다운.
- 공시 · 내부자 매수 · 실적 D-1 (30분마다, 시장 시간과 무관):
  · 공시: disclosures.py 가 저장한 DisclosureEvent (DART 공급계약·자사주·유상증자·임원 장내 매수 / SEC Form 4 매수·8-K).
    알림 규칙 종목도 수집 대상에 들어간다(disclosures.targets). 공시 레이더 알림을 이미 받는 사용자(보유·관심)는 겹치지 않게 건너뜀.
  · 미국 내부자 매수: analysis.advanced.recent_insider_buys (yfinance insider_transactions, 종목별 6시간 캐시).
    같은 종목 SEC Form 4 매수 공시가 ±3일 안에 이미 있으면 중복이라 건너뜀.
  · 실적 D-1: analysis.earnings 6시간 캐시 (d_day == 1).

중복 발송 방지: stock_alert_logs (user_id, dedup_key) 유니크. 발송 전에 이력 행을 먼저 커밋하고, 커밋에 성공한 쪽만 보낸다
(동시에 두 프로세스가 돌아도 한 번). 웹 푸시는 webpush.send_to_user (만료 구독 404/410 자동 삭제).

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

D = {}
KST = ZoneInfo("Asia/Seoul")
RSI_LOW, RSI_HIGH = 30.0, 70.0
RSI_TTL = 30 * 60
RSI_COOLDOWN = timedelta(hours=24)
EVENTS_EVERY = 30 * 60
INSIDER_TTL = 6 * 3600
INSIDER_DAYS = 14
MAX_RULES_PER_USER = 50
PUSH_BUNDLE_AT = 4
LOG_KEEP_DAYS = 90
TYPE_KO = {"target": "목표가 도달", "stop": "손절가 도달", "rsi_low": "RSI 과매도", "rsi_high": "RSI 과매수",
           "disclosure": "신규 공시", "insider_buy": "내부자 매수", "earnings": "실적 발표 D-1"}

_rsi_cache = {}            # ticker -> (expires, rsi)
_insider_cache = {}        # ticker -> (expires, [buys])
_state = {"events_at": 0.0}
_lock = threading.Lock()


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[ALERT] {msg}")
    except Exception:
        print(f"[ALERT] {msg}", flush=True)


def is_kr(t):
    return (t or "").upper().endswith((".KS", ".KQ"))


def market_of(t):
    return "KR" if is_kr(t) else "US"


def fmt_px(v, ticker):
    if v is None:
        return "-"
    return f"{v:,.0f}원" if is_kr(ticker) else f"${v:,.2f}"


def who(rule):
    tk = rule.ticker.split(".")[0] if is_kr(rule.ticker) else rule.ticker
    return f"{rule.stock_name} ({tk})" if rule.stock_name and rule.stock_name.upper() != rule.ticker.upper() else tk


# ── 규칙 CRUD 도우미 (API 에서 사용) ───────────────────────────────────────────────
def rule_dict(r):
    return {"ticker": r.ticker, "stock_name": r.stock_name, "target_price": r.target_price, "stop_price": r.stop_price,
            "rsi_oversold": bool(r.rsi_oversold), "rsi_overbought": bool(r.rsi_overbought),
            "disclosure_enabled": bool(r.disclosure_enabled), "enabled": bool(r.enabled), "market": market_of(r.ticker),
            "currency": "KRW" if is_kr(r.ticker) else "USD", "active": has_condition(r),
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None}


def log_dict(g):
    return {"ticker": g.ticker, "rule_type": g.rule_type, "type_label": TYPE_KO.get(g.rule_type, g.rule_type),
            "title": g.title, "body": g.body, "fired_at": g.fired_at.isoformat() + "Z" if g.fired_at else None}


def has_condition(r):
    return bool(r.target_price or r.stop_price or r.rsi_oversold or r.rsi_overbought or r.disclosure_enabled)


def _pos(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("가격은 숫자로 입력해 주세요")
    if not (f > 0) or f != f or f > 1e9:
        raise ValueError("가격은 0보다 커야 합니다")
    return f


def apply_fields(rule, body, partial=False):
    """요청 본문 → 규칙. partial 이면 들어온 키만 바꾼다. 잘못된 값이면 ValueError."""
    for k in ("target_price", "stop_price"):
        if not partial or k in body:
            setattr(rule, k, _pos(body.get(k)))
    for k in ("rsi_oversold", "rsi_overbought", "disclosure_enabled", "enabled"):
        if k in body:
            setattr(rule, k, bool(body.get(k)))
        elif not partial and k != "enabled":
            setattr(rule, k, False)
    if body.get("stock_name"):
        rule.stock_name = str(body["stock_name"])[:200]
    if rule.target_price and rule.stop_price and rule.stop_price >= rule.target_price:
        raise ValueError("손절가는 목표가보다 낮아야 합니다")
    rule.updated_at = datetime.utcnow()
    return rule


# ── 발송 (중복 방지 포함) ────────────────────────────────────────────────────────
def claim(user_id, ticker, rule_type, dedup_key, title, body):
    """이력 행을 먼저 커밋 → 성공한 경우만 True (유니크 위반 = 이미 보냄)."""
    L, db = D["StockAlertLog"], D["db"]
    try:
        db.session.add(L(user_id=user_id, ticker=ticker, rule_type=rule_type, dedup_key=dedup_key[:200],
                         title=title[:200], body=(body or "")[:500], fired_at=datetime.utcnow()))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def in_cooldown(user_id, ticker, rule_type, now=None, span=RSI_COOLDOWN):
    L = D["StockAlertLog"]
    since = (now or datetime.utcnow()) - span
    return L.query.filter(L.user_id == user_id, L.ticker == ticker, L.rule_type == rule_type, L.fired_at >= since).first() is not None


def deliver(fired):
    """fired: [{"user_id", "ticker", "rule_type", "title", "body"}] (이미 claim 성공한 것) → 인앱 저장 + 웹 푸시. 사용자당 4건 이상이면 푸시는 요약 1건."""
    if not fired:
        return {"inapp": 0, "push": None}
    N, db = D["Notification"], D["db"]
    by_user = {}
    for f in fired:
        db.session.add(N(user_id=f["user_id"], kind="stock_alert", title=f["title"][:200], body=(f["body"] or "")[:500],
                         link=f"/stock/{f['ticker']}"))
        by_user.setdefault(f["user_id"], []).append(f)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log(f"인앱 저장 실패: {e}")
    wp = D.get("webpush")
    total = {"sent": 0, "failed": 0, "removed": 0}
    if not wp or not wp.enabled():
        return {"inapp": len(fired), "push": None}
    for uid, items in by_user.items():
        if len(items) >= PUSH_BUNDLE_AT:
            names = " · ".join(dict.fromkeys(i["ticker"] for i in items))
            payloads = [{"title": f"내 종목 알림 {len(items)}건 — 탭해서 확인하기", "body": names[:180], "url": "/?tab=alerts",
                         "tag": "stock-alerts-batch"}]
        else:
            payloads = [{"title": i["title"], "body": i["body"] or "", "url": f"/stock/{i['ticker']}",
                         "tag": f"alert-{i['rule_type']}-{i['ticker']}"} for i in items]
        for p in payloads:
            try:
                r = wp.send_to_user(uid, p)
                for k in total:
                    total[k] += r.get(k, 0)
            except Exception as e:
                db.session.rollback()
                log(f"푸시 오류 user={uid}: {e}")
    return {"inapp": len(fired), "push": total}


def _fire(out, rule, rule_type, key, title, body):
    if claim(rule.user_id, rule.ticker, rule_type, key, title, body):
        out.append({"user_id": rule.user_id, "ticker": rule.ticker, "rule_type": rule_type, "title": title, "body": body})


# ── 가격 · RSI (장중 5분) ─────────────────────────────────────────────────────────
def _active_rules(**flt):
    R = D["StockAlertRule"]
    return R.query.filter_by(enabled=True, **flt).all()


def rsi_for(tickers, live=None, now=None):
    """켜진 종목만 RSI(14). 30분 캐시. 일봉 종가의 마지막 값을 현재가로 바꿔 장중 값에 가깝게."""
    now = time.time() if now is None else now
    out, need = {}, []
    with _lock:
        for t in tickers:
            hit = _rsi_cache.get(t)
            if hit and hit[0] > now:
                out[t] = hit[1]
            else:
                need.append(t)
    if need:
        got = {}
        try:
            got = D["rsi_fetch"](need, live or {})
        except Exception as e:
            log(f"RSI 계산 실패: {e}")
        with _lock:
            for t in need:
                v = got.get(t)
                _rsi_cache[t] = (now + (RSI_TTL if v is not None else 5 * 60), v)
                out[t] = v
    return out


def check_prices(market, quotes=None, now=None):
    """장중 5분 체크에서 호출 (시장 정규장일 때만 불림). 규칙이 없으면 쿼리 1번으로 끝. → 발송 결과"""
    rules = [r for r in _active_rules() if market_of(r.ticker) == market
             and (r.target_price or r.stop_price or r.rsi_oversold or r.rsi_overbought)]
    if not rules:
        return {"checked": 0, "fired": 0}
    tickers = sorted({r.ticker for r in rules})
    quotes = quotes if quotes is not None else (D["quotes"](tickers) or {})
    rsi_t = sorted({r.ticker for r in rules if r.rsi_oversold or r.rsi_overbought})
    rsis = rsi_for(rsi_t, quotes, now=now) if rsi_t else {}
    db, out = D["db"], []
    day = datetime.now(KST).strftime("%Y%m%d")
    for r in rules:
        px = quotes.get(r.ticker)
        if px:
            if r.target_price and px >= r.target_price:
                tp = r.target_price
                _fire(out, r, "target", f"target:{r.ticker}:{tp:g}:{day}", f"🎯 목표가 도달 · {who(r)}",
                      f"현재가 {fmt_px(px, r.ticker)} ≥ 목표가 {fmt_px(tp, r.ticker)} — 알림은 1회 후 자동 해제돼요")
                r.target_price = None                      # 1회 발송 후 해제 (5분마다 반복 방지)
            if r.stop_price and px <= r.stop_price:
                sp = r.stop_price
                _fire(out, r, "stop", f"stop:{r.ticker}:{sp:g}:{day}", f"⚠️ 손절가 도달 · {who(r)}",
                      f"현재가 {fmt_px(px, r.ticker)} ≤ 손절가 {fmt_px(sp, r.ticker)} — 알림은 1회 후 자동 해제돼요")
                r.stop_price = None
        v = rsis.get(r.ticker)
        if v is not None:
            for on, cond, typ, word in ((r.rsi_oversold, v <= RSI_LOW, "rsi_low", "과매도"), (r.rsi_overbought, v >= RSI_HIGH, "rsi_high", "과매수")):
                if on and cond and not in_cooldown(r.user_id, r.ticker, typ):
                    _fire(out, r, typ, f"{typ}:{r.ticker}:{day}", f"📉 RSI {word} · {who(r)}" if typ == "rsi_low" else f"📈 RSI {word} · {who(r)}",
                          f"RSI(14) {v:.1f} {'≤ 30' if typ == 'rsi_low' else '≥ 70'}" + (f" · 현재가 {fmt_px(px, r.ticker)}" if px else "")
                          + " — 같은 알림은 24시간 쉬어요")
    try:
        db.session.commit()                                 # 자동 해제 반영
    except Exception as e:
        db.session.rollback()
        log(f"규칙 해제 저장 실패: {e}")
    res = deliver(out)
    if out:
        log(f"{market} 가격·RSI 알림 {len(out)}건 {res.get('push')}")
    return {"checked": len(rules), "fired": len(out)}


# ── 공시 · 내부자 매수 · 실적 D-1 (30분) ─────────────────────────────────────────────
def _insider_buys(ticker, now):
    with _lock:
        hit = _insider_cache.get(ticker)
        if hit and hit[0] > now:
            return hit[1]
    try:
        buys = D["insider_buys"](ticker) or []
        ttl = INSIDER_TTL
    except Exception:
        buys, ttl = [], 15 * 60
    with _lock:
        _insider_cache[ticker] = (now + ttl, buys)
    return buys


def check_events(now=None, force=False):
    """30분마다 (스케줄러 루프 매 회차 호출 → 주기 안 됐으면 바로 끝)."""
    t = time.time() if now is None else now
    if not force and t - _state["events_at"] < EVENTS_EVERY:
        return None
    _state["events_at"] = t
    rules = _active_rules(disclosure_enabled=True)
    if not rules:
        return {"checked": 0, "fired": 0}
    E, db, out = D["DisclosureEvent"], D["db"], []
    tickers = sorted({r.ticker for r in rules})
    utcnow = datetime.utcnow()
    # ① 공시 (DisclosureEvent, 최근 2일 저장분)
    evs = E.query.filter(E.ticker.in_(tickers), E.created_at >= utcnow - timedelta(days=2)).all()
    radar = {}
    try:
        radar = D["radar_receivers"](tickers) if D.get("radar_receivers") else {}
    except Exception:
        db.session.rollback()
    by_t = {}
    for e in evs:
        by_t.setdefault(e.ticker.upper(), []).append(e)
    for r in rules:
        if r.ticker.upper() in (radar.get(r.user_id) or set()):
            continue                                        # 공시 레이더 알림으로 이미 받음
        for e in by_t.get(r.ticker.upper(), []):
            if r.created_at and e.disclosed_at < r.created_at - timedelta(hours=1):
                continue                                    # 규칙을 만들기 전 공시는 보내지 않음
            typ = "insider_buy" if e.category == "insider_buy" else "disclosure"
            _fire(out, r, typ, f"disc:{e.external_id}", f"{'🟢 내부자 매수' if typ == 'insider_buy' else '📢 신규 공시'} · {who(r)}",
                  f"[{e.source}] {e.title}" + (f" — {e.summary}" if e.summary else ""))
    # ② 미국 내부자 매수 (yfinance, 6시간 캐시)
    sec_buys = {}
    for e in evs:
        if e.category == "insider_buy" and e.source == "SEC":
            sec_buys.setdefault(e.ticker.upper(), []).append(e.disclosed_at.date())
    for tk in [x for x in tickers if not is_kr(x)]:
        for b in _insider_buys(tk, t):
            if any(abs((b["date"] - d).days) <= 3 for d in sec_buys.get(tk.upper(), [])):
                continue
            for r in [r for r in rules if r.ticker == tk]:
                if r.created_at and b["date"] < (r.created_at.date() - timedelta(days=1)):
                    continue                                # 규칙 만들기 전 거래는 '신규' 가 아님
                amt = f" · ${b['value'] / 1e6:,.1f}M" if b.get("value") else ""
                _fire(out, r, "insider_buy", f"insider:{tk}:{b['date'].isoformat()}:{b.get('insider', '')[:60]}",
                      f"🟢 내부자 매수 · {who(r)}",
                      f"{b.get('insider') or '내부자'}{' (' + b['position'] + ')' if b.get('position') else ''} "
                      f"{b['date'].strftime('%m.%d')} 장내 매수{amt}")
    # ③ 실적 발표 D-1 (earnings 6시간 캐시)
    try:
        ers = D["earnings_many"](tickers) or {}            # 6시간 캐시 · 동시 조회 최대 4
    except Exception as e:
        log(f"실적 일정 조회 실패: {e}")
        ers = {}
    for tk in tickers:
        er = ers.get(tk.upper()) or ers.get(tk)
        n = (er or {}).get("next_earnings") if (er or {}).get("available") else None
        if not n or n.get("d_day") != 1:
            continue
        sess = {"BMO": " 장전", "AMC": " 장후"}.get(n.get("session"), "")
        est = n.get("eps_estimate")
        for r in [r for r in rules if r.ticker == tk]:
            _fire(out, r, "earnings", f"earn:{tk}:{n['date']}", f"🗓 내일 실적 발표 · {who(r)}",
                  f"{n['date'][5:].replace('-', '.')}{sess} 발표 예정" + (f" · 예상 EPS {fmt_px(est, tk) if is_kr(tk) else f'${est:.2f}'}" if est is not None else ""))
    res = deliver(out)
    # 오래된 이력 정리
    try:
        L = D["StockAlertLog"]
        L.query.filter(L.fired_at < utcnow - timedelta(days=LOG_KEEP_DAYS)).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
    if out:
        log(f"공시·내부자·실적 알림 {len(out)}건 {res.get('push')}")
    return {"checked": len(rules), "fired": len(out)}
