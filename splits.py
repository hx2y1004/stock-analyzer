"""액면분할(·병합) 자동 반영 — 모의투자(사용자·봇 계좌) · 봇 포지션 · 봇 신호 · 실제 포트폴리오.

현재가는 분할 뒤 가격인데 보유 수량·매입가·목표가·손절가가 분할 전 그대로면 수익률이 -87.5%(8:1)처럼 왜곡되고
손절이 잘못 실행된다. yfinance `Ticker.splits`(종목별 12시간 캐시)로 분할을 찾아, 분할 시각 전에 보유하던 몫만 보정한다.

· 멱등성: AppliedStockSplit(account_type, holding_id, ticker, split_date) 유니크 → 같은 보유 건에 같은 분할은 한 번만.
· 모의(사용자·봇): 수량 × 비율, 매입가는 총 원가 ÷ 새 수량. 정수 주식만 다루므로 소수점 단주는
  분할 뒤 현재가로 현금 정산(cash in lieu). 분할 뒤에 더 산 몫(거래 기록으로 계산)은 그대로 둔다.
· 목표가·손절가: 분할 전 기준값으로 보이면(현재가와의 비가 √비율 너머) ÷ 비율. 분할 뒤에 새로 잡은 값은 건드리지 않는다.
· 봇: BotPosition(수량·진입가)과 진행 중 신호(AiSignal: 신호가·진입가·체결가·목표·손절·최고가)를 함께 맞춘다.
  봇 배치의 사후 추적은 yfinance 일봉(분할 반영된 값)을 쓰므로, 추적 전에 반드시 먼저 돌려야 가짜 손절이 안 난다.
· 실제 포트폴리오: 수량 × 비율(소수 4자리), 매입가 ÷ 비율 (현금 계좌가 없어 단주 정산 없음).

호출: bot_engine 장 마감 배치 시작 · 장중 5분 체크에서 -40% 이상 급락 종목 · /api/trading/dashboard · /api/portfolio(사용자별 하루 1회).
app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone

D = {}
_CACHE = {}                 # ticker → (받은 시각, [(분할 시각 UTC naive, 분할일, 비율)])
_TTL = 12 * 3600
_LOOKBACK_DAYS = 400        # 이보다 오래된 분할은 보지 않는다
_LOCK = threading.Lock()
_USER_DAY = {}              # user_id → 마지막으로 점검한 날짜 (하루 1회)
DROP_GUARD = 0.60           # 장중 체크: 현재가가 매입가의 60% 이하(-40% 이상)면 손절 전에 분할부터 확인


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[SPLIT] {msg}")
    except Exception:
        print(f"[SPLIT] {msg}")


def ratio_text(r):
    return f"{r:g}:1" if r >= 1 else f"1:{1 / r:g}"


def fetch_splits(ticker, force=False):
    """[(분할 시각 UTC naive, 분할일(현지), 비율)] 최근 400일, 오래된 순. 실패하면 빈 목록(다음에 다시)."""
    now = time.time()
    hit = _CACHE.get(ticker)
    if hit and not force and now - hit[0] < _TTL:
        return hit[1]
    out = []
    try:
        s = D["yf_splits"](ticker) if D.get("yf_splits") else _yf_splits(ticker)
        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
        for ts, r in (s.items() if s is not None else []):
            r = float(r)
            if not r or abs(r - 1) < 1e-9:
                continue
            t = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t < cutoff:
                continue
            out.append((t.astimezone(timezone.utc).replace(tzinfo=None), t.date(), r))
        out.sort()
        _CACHE[ticker] = (now, out)
    except Exception as e:
        log(f"{ticker} 분할 조회 실패: {e}")
        return hit[1] if hit else []
    return out


def _yf_splits(ticker):
    import yfinance as yf
    return yf.Ticker(ticker).splits


def _adjust_level(v, r, price):
    """목표가·손절가: 분할 전 기준으로 보이면 ÷ 비율. 현재가(분할 뒤)를 알면 비가 √비율 너머일 때만 (분할 뒤 새로 잡은 값 보호)."""
    if v is None:
        return None, False
    if price:
        k = v / price
        pre = k > math.sqrt(r) if r > 1 else k < math.sqrt(r)
        if not pre:
            return v, False
    return v / r, True


def _done(acct, hid, ticker, sdate):
    S = D["AppliedStockSplit"]
    return S.query.filter_by(account_type=acct, holding_id=hid, ticker=ticker, split_date=sdate).first() is not None


def _record(acct, hid, ticker, sdate, r, detail):
    D["db"].session.add(D["AppliedStockSplit"](account_type=acct, holding_id=hid, ticker=ticker, split_date=sdate,
                                               ratio=r, detail=json.dumps(detail, ensure_ascii=False, default=str)))


def _pre_split_qty(user_id, ticker, holding, split_at):
    """분할 시각 전에 들고 있던 수량 (거래 기록 재생). 기록이 없으면 보유 시작 시각으로 판단."""
    Tx = D["Transaction"]
    txs = (Tx.query.filter(Tx.user_id == user_id, Tx.ticker == ticker, Tx.timestamp < split_at)
           .order_by(Tx.timestamp, Tx.id).all())
    if not txs:
        return holding.quantity if (holding.created_at and holding.created_at < split_at) else 0.0
    q = 0.0
    for t in txs:
        if (t.type or "").upper() == "BUY":
            q += t.quantity or 0
        elif (t.type or "").upper() == "SELL":
            q -= t.quantity or 0
    return max(0.0, min(q, holding.quantity or 0))


def _apply_paper(h, split_at, sdate, r, price, fx, bot_users):
    """모의 보유(사용자·봇 계좌) 1건. → 알림용 dict | None"""
    acct = "bot" if h.user_id in bot_users else "paper"
    if _done(acct, h.id, h.ticker, sdate):
        return None
    pre = _pre_split_qty(h.user_id, h.ticker, h, split_at)
    if pre <= 0:
        return None
    old_q, old_p, old_t, old_s = h.quantity, h.purchase_price, h.target_price, h.stop_loss_price
    cost = (old_p or 0) * old_q
    new_float = pre * r + (old_q - pre)
    new_int = math.floor(new_float + 1e-9)
    frac = round(new_float - new_int, 6)
    new_p = cost / new_float if new_float else old_p
    cash = 0.0
    if frac > 1e-6:
        px = price or new_p
        cash = frac * px * (fx if h.currency == "USD" else 1.0)
        u = D["User"].query.get(h.user_id)
        if u is not None:
            u.cash_balance = (u.cash_balance or 0) + cash
    h.target_price, t_adj = _adjust_level(old_t, r, price)
    h.stop_loss_price, s_adj = _adjust_level(old_s, r, price)
    if new_int <= 0:                              # 병합으로 1주도 안 남음 → 전부 현금 정산
        D["db"].session.delete(h)
    else:
        h.quantity, h.purchase_price = float(new_int), new_p
    detail = {"qty": [old_q, new_int], "pre_split_qty": pre, "price": [old_p, new_p], "target": [old_t, h.target_price],
              "stop": [old_s, h.stop_loss_price], "cash_in_lieu_krw": round(cash), "fraction": frac}
    _record(acct, h.id, h.ticker, sdate, r, detail)
    return {"acct": acct, "user_id": h.user_id, "ticker": h.ticker, "name": h.name, "old_q": old_q, "new_q": new_int,
            "cash": cash, "levels": t_adj or s_adj}


def _apply_botpos(pos, split_at, sdate, r):
    if _done("botpos", pos.id, pos.ticker, sdate) or not pos.opened_at or pos.opened_at >= split_at:
        return False
    old = (pos.quantity, pos.entry_price)
    pos.quantity = float(math.floor((pos.quantity or 0) * r + 1e-9))
    if pos.entry_price:
        pos.entry_price = pos.entry_price / r
    _record("botpos", pos.id, pos.ticker, sdate, r, {"qty": [old[0], pos.quantity], "entry": [old[1], pos.entry_price]})
    return True


def _apply_signal(sig, split_at, sdate, r):
    """진행 중 신호: 신호를 낸 시각이 분할 전이면 계획 가격 모두 ÷ 비율 (체결가·최고가는 체결이 분할 전일 때만)."""
    if _done("signal", sig.id, sig.ticker, sdate) or not sig.created_at or sig.created_at >= split_at:
        return False
    before = {k: getattr(sig, k) for k in ("price_at_signal", "entry_price", "target_price", "stop_price", "fill_price", "peak_price")}
    for k in ("price_at_signal", "entry_price", "target_price", "stop_price"):
        if getattr(sig, k):
            setattr(sig, k, getattr(sig, k) / r)
    if sig.filled_date and sig.filled_date < sdate:
        for k in ("fill_price", "peak_price"):
            if getattr(sig, k):
                setattr(sig, k, getattr(sig, k) / r)
    _record("signal", sig.id, sig.ticker, sdate, r, {"before": before})
    return True


def _apply_real(h, split_at, sdate, r):
    if _done("real", h.id, h.ticker, sdate) or not h.created_at or h.created_at >= split_at:
        return None
    old_q, old_p = h.quantity, h.purchase_price
    h.quantity = round((old_q or 0) * r, 4)
    if old_p:
        h.purchase_price = old_p / r
    _record("real", h.id, h.ticker, sdate, r, {"qty": [old_q, h.quantity], "price": [old_p, h.purchase_price]})
    return {"acct": "real", "user_id": h.user_id, "ticker": h.ticker, "name": h.name, "old_q": old_q, "new_q": h.quantity, "cash": 0}


def apply_tickers(tickers, force=False, user_id=None):
    """종목들의 미반영 분할을 반영. user_id 를 주면 그 사용자 보유만(모의·실제), 아니면 전체(봇 포함).
    → [{ticker, ratio, split_date, applied: n, notes: [...]}]"""
    M = D
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return []
    found = {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, len(tickers))) as ex:
        for t, sp in zip(tickers, ex.map(lambda x: fetch_splits(x, force=force), tickers)):
            if sp:
                found[t] = sp
    if not found:
        return []
    out = []
    with _LOCK:
        db = M["db"]
        bot_users = {u.id for u in (M["bot_users"]() or {}).values()} if M.get("bot_users") else set()
        fx = None
        for t, sp in found.items():
            for split_at, sdate, r in sp:
                notes, n = [], 0
                try:
                    price = None
                    try:
                        price = M["price"](t) if M.get("price") else None
                    except Exception:
                        price = None
                    if fx is None and not (t.endswith(".KS") or t.endswith(".KQ")):
                        fx = M["fx"]() if M.get("fx") else 1400.0
                    q = M["PaperHolding"].query.filter_by(ticker=t)
                    if user_id is not None:
                        q = q.filter_by(user_id=user_id)
                    for h in q.all():
                        res = _apply_paper(h, split_at, sdate, r, price, fx or 1.0, bot_users)
                        if res:
                            notes.append(res); n += 1
                    rq = M["Holding"].query.filter_by(ticker=t)
                    if user_id is not None:
                        rq = rq.filter_by(user_id=user_id)
                    for h in rq.all():
                        res = _apply_real(h, split_at, sdate, r)
                        if res:
                            notes.append(res); n += 1
                    if user_id is None:
                        for pos in M["BotPosition"].query.filter_by(ticker=t, status="OPEN").all():
                            n += _apply_botpos(pos, split_at, sdate, r)
                        S = M["AiSignal"]
                        for sig in S.query.filter(S.ticker == t, S.status.in_(["PENDING", "ACTIVE"])).all():
                            n += _apply_signal(sig, split_at, sdate, r)
                    db.session.commit()
                except Exception as e:                     # 유니크 충돌(다른 스레드가 먼저 반영) 등 → 되돌리고 다음에
                    db.session.rollback()
                    log(f"{t} {ratio_text(r)} 반영 실패: {e}")
                    continue
                if n:
                    log(f"{t} {sdate} {ratio_text(r)} 분할 반영 {n}건")
                    out.append({"ticker": t, "ratio": r, "split_date": str(sdate), "applied": n})
                    for x in notes:
                        if x["acct"] in ("paper", "real") and M.get("notify_user"):
                            _notify(x, r)
    return out


def _notify(x, r):
    q = lambda v: f"{v:g}" if isinstance(v, float) else str(v)
    title = f"{x['ticker']} {ratio_text(r)} 액면분할 반영 ({q(x['old_q'])}주 → {q(x['new_q'])}주)"
    body = ("모의투자" if x["acct"] == "paper" else "내 포트폴리오") + " 보유 수량과 매입가를 분할 비율로 맞췄습니다."
    if x.get("cash"):
        body += f" 단주는 현재가로 {round(x['cash']):,}원 현금 정산."
    if x.get("levels"):
        body += " 목표가·손절가도 함께 조정했습니다."
    try:
        D["notify_user"](x["user_id"], "split", title, body, "/trading" if x["acct"] == "paper" else "/#portfolioSection")
    except Exception as e:
        log(f"알림 실패: {e}")


def held_tickers(market=None):
    """보유·진행 중인 모든 종목 (모의·봇·신호·실제)."""
    M = D
    ts = {t for (t,) in M["PaperHolding"].query.with_entities(M["PaperHolding"].ticker).all()}
    ts |= {t for (t,) in M["Holding"].query.with_entities(M["Holding"].ticker).all()}
    S = M["AiSignal"]
    ts |= {t for (t,) in S.query.filter(S.status.in_(["PENDING", "ACTIVE"])).with_entities(S.ticker).all()}
    if market:
        kr = market == "KR"
        ts = {t for t in ts if (t.endswith(".KS") or t.endswith(".KQ")) == kr}
    return ts


def check_market(market):
    """장 마감 배치 시작에서 1일 1회: 그 시장 보유 종목 전체. 오래된 급락 보류 상태도 이때 정리."""
    try:
        prune_hold_states()
    except Exception:
        pass
    return apply_tickers(held_tickers(market))


def check_user(user_id):
    """사용자 화면(/trading 대시보드·내 포트폴리오) 진입 때 하루 1회: 그 사용자 보유 종목만."""
    today = datetime.utcnow().date()
    if _USER_DAY.get(user_id) == today:
        return []
    _USER_DAY[user_id] = today
    M = D
    ts = {t for (t,) in M["PaperHolding"].query.filter_by(user_id=user_id).with_entities(M["PaperHolding"].ticker).all()}
    ts |= {t for (t,) in M["Holding"].query.filter_by(user_id=user_id).with_entities(M["Holding"].ticker).all()}
    return apply_tickers(ts, user_id=user_id)


def guard_drop(tickers):
    """장중 손절 직전 방어: -40% 이상 급락으로 보이는 종목은 분할 여부를 강제로 다시 확인해 반영. → 반영된 종목 집합"""
    res = apply_tickers(tickers, force=True)
    return {x["ticker"] for x in res}


# ── 이중 방어: yfinance splits 반영이 늦을 때 ─────────────────────────────────────
# ① 가격 기록으로 분할 의심 → 증거 강도별로 자동 손절 보류 상한을 다르게 둔다
#    strong: 수정주가 일봉의 매수일 종가가 매입가의 1/1.8 이하 (이미 분할이 반영된 기록)        → 최대 6시간
#    weak  : 수정주가 하락은 없고 최근 하룻밤 갭만 흔한 분할 비율(2·3·4·5·8·10…)과 ±4% 일치  → 최대 1시간
#            (정확히 -50% 같은 실제 폭락도 여기 걸릴 수 있어 짧게). 1시간 안에 strong 이 확인되면 승급.
#    그 사이 yfinance splits 가 들어오면 guard_drop 이 보정하고 손절을 취소한다.
# ② 근거가 없으면(none) -40% 이하 급락을 처음 본 체크에서 1회(4분) 보류하고, 다음 체크에 재판정한다.
#    어떤 경우든 상한이 지나면 released=True → 그 뒤로는 바로 정상 판정 (실제 폭락·상장폐지가 계속 미뤄지지 않음).
# 상태는 DB(drop_hold_states)에 두어 재시작·재배포에도 타이머와 released 를 이어 간다. DB 오류면 메모리로 폴백.
SPLIT_EVIDENCE = 1.8            # 매입가 ÷ 매수일 수정 종가 ≥ 1.8 이면 strong
COMMON_RATIOS = (1.5, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50)
GAP_TOL = 0.04                  # 갭이 분할 비율과 ±4% 안이면 weak
HOLD_MIN_SEC = 4 * 60           # none: 1회 보류 (다음 5분 체크까지)
WEAK_MAX_SEC = 3600             # weak 보류 상한
STRONG_MAX_SEC = 6 * 3600       # strong 보류 상한
HOLD_TTL_DAYS = 7               # 이보다 오래 손대지 않은 보류 상태는 정리
_LEVEL_RANK = {"none": 0, "weak": 1, "strong": 2}
_HOLD = {}                      # 메모리 폴백: (kind, id) → {"first": ts, "ev_first": ts|None, "ev_level": str, "released": bool}
_BARS = {}                      # ticker → (받은 시각, df)
_BARS_TTL = 600
_LAST_PRUNE = {"ts": 0.0}


def _daily_bars(ticker):
    """수정주가 일봉 (토스 우선, 없으면 yfinance 1년). 10분 캐시."""
    hit = _BARS.get(ticker)
    if hit and time.time() - hit[0] < _BARS_TTL:
        return hit[1]
    df = None
    try:
        if D.get("bars"):
            df = D["bars"](ticker)
        else:
            try:
                import toss_api
                df = toss_api.get_candles_df(ticker, interval="1d", min_bars=260)
            except Exception:
                df = None
            if df is None or len(df) == 0:
                import yfinance as yf
                df = yf.Ticker(ticker).history(period="1y", interval="1d")
    except Exception as e:
        log(f"{ticker} 일봉 조회 실패: {e}")
        df = None
    _BARS[ticker] = (time.time(), df)
    return df


def _local_dates(df):
    try:
        return [ts.date() for ts in df.index]
    except Exception:
        return []


def history_evidence(ticker, buy_price, buy_date, df=None):
    """가격 기록으로 본 분할 의심. → (강도 "strong"|"weak"|"none", 이유, 추정 비율)"""
    if df is None:
        df = _daily_bars(ticker)
    if df is None or len(df) == 0 or not buy_price:
        return "none", "", None
    try:
        closes = df["Close"].astype(float)
        dates = _local_dates(df)
        # strong: 매수일(또는 그 직전 거래일) 수정 종가가 매입가보다 1.8배 이상 낮다
        if buy_date is not None:
            prior = [i for i, d in enumerate(dates) if d <= buy_date]
            if prior:
                c = float(closes.iloc[prior[-1]])
                if c > 0 and buy_price / c >= SPLIT_EVIDENCE:
                    return "strong", f"수정주가: 매수일 종가 {c:,.2f} = 매입가 {buy_price:,.2f}의 1/{buy_price / c:.1f}", round(buy_price / c, 2)
        # weak: 최근 거래일 사이 하룻밤 갭만 흔한 분할 비율과 같다 (수정 안 된 기록 · 실제 폭락일 수도)
        opens = df["Open"].astype(float) if "Open" in df else closes
        for i in range(max(1, len(closes) - 3), len(closes)):
            prev, now_o = float(closes.iloc[i - 1]), float(opens.iloc[i]) or float(closes.iloc[i])
            if prev > 0 and now_o > 0:
                g = prev / now_o
                for r in COMMON_RATIOS:
                    if r >= SPLIT_EVIDENCE and abs(g / r - 1) <= GAP_TOL:
                        return "weak", f"갭 {g:.2f}배 = 분할 비율 {r:g}과 일치 ({dates[i - 1]} → {dates[i]})", float(r)
    except Exception as e:
        log(f"{ticker} 기록 확인 실패: {e}")
    return "none", "", None


# ── 보류 상태 저장 (DB 우선, 실패하면 메모리) ─────────────────────────────────────
def _to_dt(ts):
    return datetime.utcfromtimestamp(ts) if ts is not None else None


def _to_ts(dt):
    return (dt - datetime(1970, 1, 1)).total_seconds() if dt is not None else None


def _load_state(kind, hid):
    """→ (state dict | None, DB 사용 가능 여부)"""
    M = D.get("DropHoldState")
    if M is not None:
        try:
            row = M.query.filter_by(kind=kind, target_id=hid).first()
            if row is None:
                return None, True
            return {"first": _to_ts(row.first_seen_at), "ev_first": _to_ts(row.ev_first_at),
                    "ev_level": row.ev_level or "none", "released": bool(row.released)}, True
        except Exception as e:
            try:
                D["db"].session.rollback()
            except Exception:
                pass
            log(f"보류 상태 조회 실패 → 메모리 폴백: {e}")
    st = _HOLD.get((kind, hid))
    return (dict(st) if st else None), False


def _save_state(kind, hid, ticker, st, use_db, now):
    _HOLD[(kind, hid)] = dict(st)                          # 메모리에도 항상 남긴다 (DB 오류 대비)
    if not use_db or D.get("DropHoldState") is None:
        return
    M, db = D["DropHoldState"], D["db"]
    try:
        row = M.query.filter_by(kind=kind, target_id=hid).first()
        if row is None:
            row = M(kind=kind, target_id=hid)
            db.session.add(row)
        row.ticker = ticker
        row.first_seen_at, row.ev_first_at = _to_dt(st["first"]), _to_dt(st.get("ev_first"))
        row.ev_level, row.released, row.updated_at = st.get("ev_level") or "none", bool(st.get("released")), _to_dt(now)
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        log(f"보류 상태 저장 실패 → 메모리만 사용: {e}")


def prune_hold_states(now=None):
    """HOLD_TTL_DAYS 넘게 손대지 않은 보류 상태 정리 (DB · 메모리). → 지운 행 수"""
    now = now if now is not None else time.time()
    cutoff = now - HOLD_TTL_DAYS * 86400
    for k in [k for k, v in _HOLD.items() if (v.get("first") or now) < cutoff]:
        _HOLD.pop(k, None)
    M = D.get("DropHoldState")
    if M is None:
        return 0
    try:
        n = M.query.filter(M.updated_at < _to_dt(cutoff)).delete(synchronize_session=False)
        D["db"].session.commit()
        return n
    except Exception as e:
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        log(f"보류 상태 정리 실패: {e}")
        return 0


def drop_decision(kind, hid, ticker, buy_price, buy_date, price, df=None, now=None):
    """-40% 이하 급락 손절 직전 판정. → ("hold" | "proceed", 이유)"""
    now = now if now is not None else time.time()
    if not buy_price or not price or price > buy_price * DROP_GUARD:
        return "proceed", ""
    if now - _LAST_PRUNE["ts"] > 3600:                     # 가볍게 한 시간에 한 번 정리
        _LAST_PRUNE["ts"] = now
        prune_hold_states(now)
    st, use_db = _load_state(kind, hid)
    if st is None:
        st = {"first": now, "ev_first": None, "ev_level": "none", "released": False}
    if st.get("released"):
        _save_state(kind, hid, ticker, st, use_db, now)
        return "proceed", "보류했던 건 → 정상 판정"
    level, why, _ = history_evidence(ticker, buy_price, buy_date, df=df)
    # 강도는 한 번 확인된 가장 강한 값을 유지 (weak → strong 승급, 거꾸로 내려가지 않음)
    if _LEVEL_RANK[level] > _LEVEL_RANK.get(st.get("ev_level") or "none", 0):
        st["ev_level"] = level
        if st.get("ev_first") is None:
            st["ev_first"] = now
    lv = st.get("ev_level") or "none"
    if lv in ("strong", "weak"):
        cap = STRONG_MAX_SEC if lv == "strong" else WEAK_MAX_SEC
        if now - (st.get("ev_first") or now) < cap:
            _save_state(kind, hid, ticker, st, use_db, now)
            return "hold", f"분할 의심({lv}) → 자동 손절 보류 (최대 {cap // 3600}시간): {why or '이전 체크에서 확인'}"
        st["released"] = True
        _save_state(kind, hid, ticker, st, use_db, now)
        return "proceed", f"분할 의심({lv}) {cap // 3600}시간 동안 splits 확인 안 됨 → 정상 판정"
    if now - st["first"] < HOLD_MIN_SEC:
        _save_state(kind, hid, ticker, st, use_db, now)
        return "hold", "-40% 이하 급락 첫 감지 → 1회 보류, 다음 체크에서 재판정"
    st["released"] = True
    _save_state(kind, hid, ticker, st, use_db, now)
    return "proceed", "1회 보류 후 분할 확인 안 됨 → 정상 판정"
