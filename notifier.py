"""봇 신호 알림 발송.

구조
- Provider: 알림 한 건을 실제로 전달하는 방법. 지금은 사이트 안 알림(InAppProvider)만 있다.
  웹 푸시·이메일·텔레그램 등을 붙이려면 NotifyProvider 를 상속해 send() 를 구현하고 PROVIDERS 에 등록한다.
  어떤 Provider 를 쓸지는 환경변수 NOTIFY_PROVIDERS (콤마 구분, 기본 "inapp").
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


def _dispatch(new_signals, closed_signals):
    db, N = D["db"], D["Notification"]
    providers = _providers()
    names = _strategy_names()
    sent = 0
    for sig in new_signals:
        bot = names.get(sig.strategy_key, sig.strategy_key)
        title = f"{bot} · {sig.name} 매수 신호"
        body = (f"진입 {_fmt_px(sig.entry_price, sig.market)} · 목표 {_fmt_px(sig.target_price, sig.market)} · "
                f"손절 {_fmt_px(sig.stop_price, sig.market)} · 신뢰도 {sig.confidence} · 다음 장 시가 체결 가정")
        for uid in subscribers_for(sig):
            for p in providers:
                sent += bool(p.send(uid, "signal_new", title, body, f"/stock/{sig.ticker}", sig.id))
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
            for p in providers:
                sent += bool(p.send(uid, kind, title, body, f"/stock/{sig.ticker}", sig.id))
    # 오래된 알림 정리
    N.query.filter(N.created_at < datetime.utcnow() - timedelta(days=RETENTION_DAYS)).delete(synchronize_session=False)
    db.session.commit()
    if sent:
        log(f"보냄 {sent}건 (신규 {len(new_signals)}, 청산·취소 {len(closed_signals)})")
    return sent
