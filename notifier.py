"""봇 신호 알림 발송.

구조
- Provider: 알림 한 건을 실제로 전달하는 방법. 지금은 사이트 안 알림(InAppProvider)만 있다.
  웹 푸시·이메일·텔레그램 등을 붙이려면 NotifyProvider 를 상속해 send() 를 구현하고 PROVIDERS 에 등록한다.
  어떤 Provider 를 쓸지는 환경변수 NOTIFY_PROVIDERS (콤마 구분, 기본 "inapp").
- 웹 푸시(webpush.py)는 VAPID 키가 있을 때만 켜진다. 인앱에 새로 저장된 알림만 사용자 기기로 보내고,
  한 배치에서 한 사용자에게 4건 이상이면 요약 1건으로 묶는다.
- dispatch(new_signals, closed_signals): 봇 배치가 끝난 뒤 호출.
  · 새 매수 신호 → 구독 조건(봇·범위·최소 신뢰도)에 맞는 사용자에게
  · 청산·취소 → 그 신호의 매수 알림을 받았던 사용자에게만 (받은 적 없는 신호의 청산 알림은 혼란스러움)

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

D = {}

STATUS_KO = {
    "TARGET_HIT": "목표가 도달", "STOP_LOSS": "손절", "TRAILING_STOP": "트레일링 스탑",
    "TIME_STOP": "보유 기간 만료", "TREND_EXIT": "추세 이탈", "AI_RISK_EXIT": "AI 악재 감지",
    "CANCELLED": "체결 전 취소",
}
SCOPES = ("all", "portfolio", "watchlist")
RETENTION_DAYS = 60


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[NOTIFY] {msg}")
    except Exception:
        print(f"[NOTIFY] {msg}", flush=True)


# ── Provider ───────────────────────────────────────────────────────────────────
class NotifyProvider:
    name = "base"

    def send(self, user_id, kind, title, body, link, signal_id):
        """전달 성공이면 True. 같은 사용자·신호·종류는 한 번만 보내도록 구현한다."""
        raise NotImplementedError


class InAppProvider(NotifyProvider):
    """notifications 테이블에 저장 → 헤더 종 아이콘에 표시. (사용자, 신호, 종류) 유니크라 중복 저장 안 됨."""
    name = "inapp"

    def send(self, user_id, kind, title, body, link, signal_id):
        N, db = D["Notification"], D["db"]
        if signal_id and N.query.filter_by(user_id=user_id, signal_id=signal_id, kind=kind).first():
            return False
        db.session.add(N(user_id=user_id, kind=kind, title=title[:200], body=(body or "")[:500],
                         link=(link or "")[:300], signal_id=signal_id))
        return True


PROVIDERS = {"inapp": InAppProvider}


def _providers():
    names = [n.strip() for n in os.environ.get("NOTIFY_PROVIDERS", "inapp").split(",") if n.strip()]
    out = [PROVIDERS[n]() for n in names if n in PROVIDERS]
    return out or [InAppProvider()]


# ── 구독 대상 계산 ───────────────────────────────────────────────────────────────
def _user_tickers(user_id, scope):
    """범위에 해당하는 종목 집합. all 이면 None (제한 없음)."""
    if scope == "portfolio":
        rows = D["Holding"].query.filter_by(user_id=user_id).all() + D["PaperHolding"].query.filter_by(user_id=user_id).all()
        return {r.ticker.upper() for r in rows}
    if scope == "watchlist":
        return {r.ticker.upper() for r in D["WatchlistItem"].query.filter_by(user_id=user_id).all()}
    return None


def subscribers_for(sig):
    """새 신호를 받아야 할 user_id 목록."""
    Sub = D["UserSignalSubscription"]
    out = []
    for sub in Sub.query.filter_by(enabled=True).all():
        keys = {k.strip() for k in (sub.strategy_keys or "").split(",") if k.strip()}
        if sig.strategy_key not in keys:
            continue
        if (sig.confidence or 0) < (sub.min_confidence or 0):
            continue
        tick = _user_tickers(sub.user_id, sub.scope if sub.scope in SCOPES else "all")
        if tick is not None and sig.ticker.upper() not in tick:
            continue
        out.append(sub.user_id)
    return out


def _fmt_px(v, market):
    if v is None:
        return "-"
    return f"{v:,.0f}원" if market == "KR" else f"${v:,.2f}"


def _strategy_names():
    return {s.key: f"{s.emoji or ''} {s.name}".strip() for s in D["BotStrategy"].query.all()}


# ── 발송 ───────────────────────────────────────────────────────────────────────
def dispatch(new_signals, closed_signals):
    """봇 배치 직후 호출. 실패해도 배치 결과에는 영향이 없게 예외를 삼킨다. → 보낸 건수."""
    try:
        return _dispatch(new_signals or [], closed_signals or [])
    except Exception as e:
        D["db"].session.rollback()
        log(f"dispatch 실패: {e}")
        return 0


PUSH_BUNDLE_AT = 4               # 한 배치에서 한 사용자에게 갈 푸시가 이만큼 이상이면 요약 1건으로 묶음


def _core_reason(sig):
    """신호 근거 한 줄: AI 요약이 있으면 그것, 없으면 진입 경로 (reason 의 마지막/첫 토막)."""
    parts = [p.strip() for p in (sig.reason or "").split("·") if p.strip()]
    if not parts:
        return ""
    tail = parts[-1]
    return tail if not tail.startswith(("RSI", "손익비")) else parts[0]


def push_payload(sig, kind, bot_name):
    """웹 푸시 1건의 내용. title 예: [균형형 스윙] 매수 신호 · NVDA"""
    label = bot_name.split(" ", 1)[-1] if bot_name else sig.strategy_key   # 이모지 뺀 봇 이름
    tk = sig.ticker.split(".")[0] if sig.market == "KR" else sig.ticker
    who = f"{sig.name} ({tk})" if sig.market == "KR" and sig.name else tk
    if kind == "signal_new":
        core = _core_reason(sig)
        body = (f"신뢰도 {sig.confidence} · 진입 {_fmt_px(sig.entry_price, sig.market)} / 목표 {_fmt_px(sig.target_price, sig.market)}"
                f" / 손절 {_fmt_px(sig.stop_price, sig.market)}" + (f" — {core}" if core else ""))
        return {"title": f"[{label}] 매수 신호 · {who}", "body": body[:180], "url": f"/stock/{sig.ticker}",
                "tag": f"sig-{sig.id}"}
    if kind == "signal_exit":
        pnl = f"{sig.pnl_pct:+.2f}%" if sig.pnl_pct is not None else "-"
        return {"title": f"[{label}] {STATUS_KO.get(sig.status, sig.status)} · {who}",
                "body": f"청산 {_fmt_px(sig.exit_price, sig.market)} · 수익률 {pnl} (수수료 반영) · 보유 {sig.holding_days or 0}일",
                "url": f"/ai-bots#{sig.strategy_key}", "tag": f"sig-{sig.id}-exit"}
    return {"title": f"[{label}] 신호 취소 · {who}", "body": (sig.status_reason or "다음 장 시가가 계획 조건을 벗어남")[:180],
            "url": f"/ai-bots#{sig.strategy_key}", "tag": f"sig-{sig.id}-cancel"}


def bundle_pushes(items):
    """한 사용자의 이번 배치 푸시 목록 → 실제로 보낼 목록. PUSH_BUNDLE_AT 건 이상이면 요약 1건."""
    if len(items) < PUSH_BUNDLE_AT:
        return items
    n_new = sum(1 for it in items if it["kind"] == "signal_new")
    n_other = len(items) - n_new
    title = f"AI 봇 새 신호 {n_new}건 도착 — 탭해서 확인하기" if n_new else f"AI 봇 알림 {len(items)}건 — 탭해서 확인하기"
    body = " · ".join(x for x in (f"새 매수 신호 {n_new}건" if n_new else "", f"청산·취소 {n_other}건" if n_other else "") if x)
    return [{"kind": "bundle", "payload": {"title": title, "body": body, "url": "/ai-bots", "tag": "ai-bots-batch"}}]


def _dispatch(new_signals, closed_signals):
    db, N = D["db"], D["Notification"]
    providers = _providers()
    names = _strategy_names()
    sent = 0
    pushes = {}                                    # user_id → [{"kind", "payload"}]  (인앱에 새로 저장된 것만)

    def deliver(uid, kind, title, body, link, sig):
        nonlocal sent
        new_inapp = False
        for p in providers:
            ok = bool(p.send(uid, kind, title, body, link, sig.id))
            sent += ok
            if ok and isinstance(p, InAppProvider):
                new_inapp = True
        if new_inapp:                              # 이미 받은 알림이면 푸시도 다시 안 보냄
            pushes.setdefault(uid, []).append({"kind": kind, "payload": push_payload(sig, kind, names.get(sig.strategy_key))})

    for sig in new_signals:
        bot = names.get(sig.strategy_key, sig.strategy_key)
        title = f"{bot} · {sig.name} 매수 신호"
        body = (f"진입 {_fmt_px(sig.entry_price, sig.market)} · 목표 {_fmt_px(sig.target_price, sig.market)} · "
                f"손절 {_fmt_px(sig.stop_price, sig.market)} · 신뢰도 {sig.confidence} · 다음 장 시가 체결 가정")
        for uid in subscribers_for(sig):
            deliver(uid, "signal_new", title, body, f"/stock/{sig.ticker}", sig)
    for sig in closed_signals:
        kind = "signal_cancel" if sig.status == "CANCELLED" else "signal_exit"
        bot = names.get(sig.strategy_key, sig.strategy_key)
        label = STATUS_KO.get(sig.status, sig.status)
        if kind == "signal_exit":
            pnl = f"{sig.pnl_pct:+.2f}%" if sig.pnl_pct is not None else "-"
            title = f"{bot} · {sig.name} {label}"
            body = f"청산 {_fmt_px(sig.exit_price, sig.market)} · 수익률 {pnl} (수수료 반영) · 보유 {sig.holding_days or 0}일"
        else:
            title = f"{bot} · {sig.name} 신호 취소"
            body = sig.status_reason or "다음 장 시가가 계획 조건을 벗어남"
        receivers = [n.user_id for n in N.query.filter_by(signal_id=sig.id, kind="signal_new").all()]
        for uid in receivers:
            deliver(uid, kind, title, body, f"/stock/{sig.ticker}", sig)
    # 오래된 알림 정리
    N.query.filter(N.created_at < datetime.utcnow() - timedelta(days=RETENTION_DAYS)).delete(synchronize_session=False)
    db.session.commit()
    if sent:
        log(f"보냄 {sent}건 (신규 {len(new_signals)}, 청산·취소 {len(closed_signals)})")
    _send_pushes(pushes)
    return sent


def notify_user(user_id, kind, title, body, link, tag=None):
    """신호와 무관한 개별 알림 (예: 모의투자 자동 청산). 인앱 저장 + 웹 푸시. → 인앱 저장 여부"""
    N, db = D["Notification"], D["db"]
    try:
        db.session.add(N(user_id=user_id, kind=kind, title=title[:200], body=(body or "")[:500], link=(link or "")[:300]))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log(f"알림 저장 실패 user={user_id}: {e}")
        return False
    _send_pushes({user_id: [{"kind": kind, "payload": {"title": title, "body": body or "", "url": link or "/",
                                                        "tag": tag or f"{kind}-{user_id}"}}]})
    return True


# ── 공시·수주 레이더 알림 (disclosures.py → 새로 저장된 공시만) ─────────────────────────────
DISC_LABEL = {("DART", "contract"): "DART 수주", ("DART", "buyback"): "DART 자사주", ("DART", "offering"): "DART 유상증자", ("DART", "insider_buy"): "DART 내부자 매수",
              ("SEC", "insider_buy"): "SEC 내부자 매수", ("SEC", "material_8k"): "SEC 8-K",
              ("SEC", "offering"): "SEC 유상증자", ("SEC", "buyback"): "SEC 자사주", ("SEC", "contract"): "SEC 계약"}


def disclosure_receivers(tickers):
    """공시 알림을 켠 사용자 중 그 종목을 보유(실제·모의)하거나 관심종목에 둔 사람 → {user_id: {ticker}}"""
    Sub = D["UserSignalSubscription"]
    want = {t.upper() for t in tickers}
    out = {}
    for sub in Sub.query.filter_by(disclosure_alerts=True).all():
        mine = (_user_tickers(sub.user_id, "portfolio") | _user_tickers(sub.user_id, "watchlist")) & want
        if mine:
            out[sub.user_id] = mine
    return out


def disclosure_push(ev):
    """푸시 1건: title 예 '[DART 수주] 삼성E&A · 단일판매·공급계약체결' → 누르면 종목 상세"""
    label = DISC_LABEL.get((ev.source, ev.category), f"{ev.source} 공시")
    tk = ev.ticker.split(".")[0] if ev.market == "KR" else ev.ticker
    who = ev.name or tk
    title = f"[{label}] {who} · {ev.title}"
    return {"title": title[:120], "body": (ev.summary or "")[:180], "url": f"/stock/{ev.ticker}", "tag": f"disc-{ev.id}"}


def dispatch_disclosures(events):
    """새 공시 → 알림을 켠 보유·관심종목 사용자에게 인앱 + 웹 푸시. 한 번에 4건 이상이면 푸시는 요약 1건. → 인앱 저장 건수"""
    try:
        return _dispatch_disclosures(events or [])
    except Exception as e:
        D["db"].session.rollback()
        log(f"공시 알림 실패: {e}")
        return 0


def _dispatch_disclosures(events):
    if not events:
        return 0
    N, db = D["Notification"], D["db"]
    recv = disclosure_receivers({e.ticker for e in events})
    pushes, sent = {}, 0
    for uid, mine in recv.items():
        for ev in events:
            if ev.ticker.upper() not in mine:
                continue
            p = disclosure_push(ev)
            db.session.add(N(user_id=uid, kind="disclosure", title=p["title"][:200], body=(ev.summary or "")[:500],
                             link=p["url"][:300]))
            sent += 1
            pushes.setdefault(uid, []).append({"kind": "disclosure", "payload": p})
    db.session.commit()
    for uid, items in list(pushes.items()):
        if len(items) >= PUSH_BUNDLE_AT:
            pushes[uid] = [{"kind": "bundle", "payload": {"title": f"보유·관심종목 핵심 공시 {len(items)}건 — 탭해서 확인하기",
                                                          "body": " · ".join(dict.fromkeys(i["payload"]["title"].split("] ", 1)[-1].split(" · ")[0] for i in items))[:180],
                                                          "url": "/screener?tab=disclosures", "tag": "disclosures-batch"}}]
    if sent:
        log(f"공시 알림 {sent}건 ({len(recv)}명)")
    _send_pushes(pushes)
    return sent


def _send_pushes(pushes):
    """웹 푸시 (VAPID 미설정이면 아무것도 안 함). 실패해도 인앱 알림·배치에는 영향 없음."""
    wp = D.get("webpush")
    if not pushes or not wp or not wp.enabled():
        return None
    total = {"users": 0, "sent": 0, "failed": 0, "removed": 0, "bundled": 0}
    for uid, items in pushes.items():
        out_items = bundle_pushes(items)
        total["users"] += 1
        total["bundled"] += int(len(out_items) == 1 and out_items[0]["kind"] == "bundle")
        for it in out_items:
            try:
                r = wp.send_to_user(uid, it["payload"])
            except Exception as e:
                D["db"].session.rollback()
                log(f"푸시 오류 user={uid}: {e}")
                continue
            for k in ("sent", "failed", "removed"):
                total[k] += r[k]
    log(f"푸시 {total}")
    return total
