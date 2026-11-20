"""AI 봇 가상 계좌 (Step 3).

- 전략 4개마다 공식 봇 계좌(users.provider='bot', 초기 자본 1억)를 하나씩 둔다.
- 신호와 계좌를 맞춘다(sync): 신호가 체결(ACTIVE)되면 그 체결가로 매수, 청산되면 청산가로 매도.
  매매는 사용자 모의투자와 같은 공용 함수(paper_buy / paper_sell)로 하고 수수료만 0.15%.
- 매수 수량 = 봇 총자산 × 종목당 최대 비중 ÷ 체결가 (현금이 모자라면 현금 한도까지, 1주 미만이면 건너뜀)
- 하루 손실 한도 = 직전 거래일 스냅샷 대비 현재 총자산 변화 (실제 계좌 기준)

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta

D = {}
_SYNC_LOCK = threading.Lock()
INITIAL = 100_000_000
FINAL = ("TARGET_HIT", "STOP_LOSS", "TRAILING_STOP", "TIME_STOP", "TREND_EXIT", "AI_RISK_EXIT")


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[BOTACC] {msg}")
    except Exception:
        print(f"[BOTACC] {msg}", flush=True)


def _kst_today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


# ── 계좌 ───────────────────────────────────────────────────────────────────────
def ensure_accounts():
    """전략마다 봇 계좌가 없으면 만든다. 닉네임이 사용자와 겹치면 뒤에 번호를 붙인다."""
    U, S, db = D["User"], D["BotStrategy"], D["db"]
    for st in S.query.order_by(S.id).all():
        u = U.query.filter_by(provider="bot", provider_id=st.key).first()
        if not u:
            base = f"{st.name} 봇"
            nick, n = base, 2
            while U.query.filter_by(nickname=nick).first():
                nick, n = f"{base}{n}", n + 1
            u = U(provider="bot", provider_id=st.key, name=f"{st.emoji or ''} {st.name}".strip(), email=None,
                  nickname=nick[:30], is_public=True, cash_balance=INITIAL, initial_capital=INITIAL)
            db.session.add(u)
            db.session.flush()
            log(f"봇 계좌 생성: {st.key} → user {u.id}")
        if st.user_id != u.id:
            st.user_id = u.id
    db.session.commit()


def bot_users():
    """{strategy_key: User}"""
    U = D["User"]
    return {u.provider_id: u for u in U.query.filter_by(provider="bot").all()}


def is_bot(user):
    return getattr(user, "provider", None) == "bot"


def _fx():
    try:
        return float(D["fx"]() or 1350.0)
    except Exception:
        return 1350.0


def equity(user, prices=None, fx=None):
    """(총자산, 현금, 평가금액) 원화. prices 가 없으면 현재가 조회(캐시)."""
    H = D["PaperHolding"]
    fx = fx or _fx()
    cash = user.cash_balance if user.cash_balance is not None else INITIAL
    pos = 0.0
    for h in H.query.filter_by(user_id=user.id).all():
        p = (prices or {}).get(h.ticker)
        if p is None:
            p = D["price"](h.ticker) or h.purchase_price
        v = p * h.quantity
        pos += v if (h.currency or "USD") == "KRW" else v * fx
    return cash + pos, cash, pos


def snapshot_all(prices=None):
    """봇 계좌 오늘(KST) 스냅샷 저장 (같은 날 여러 번이면 덮어씀)."""
    fx = _fx()
    for key, u in bot_users().items():
        tot, cash, pos = equity(u, prices, fx)
        D["save_snapshot"](u.id, round(tot), round(cash), round(pos))


def day_change_pct(strategy_key, prices=None):
    """하루 손실 한도 판정: 직전 날짜 스냅샷 대비 현재 총자산 변화(%). 계좌가 없으면 None."""
    u = bot_users().get(strategy_key)
    if not u:
        return None
    A = D["AssetSnapshot"]
    prev = (A.query.filter(A.user_id == u.id, A.date < _kst_today())
            .order_by(A.date.desc()).first())
    base = prev.total_assets_krw if prev else (u.initial_capital or INITIAL)
    tot, _, _ = equity(u, prices)
    return round((tot / base - 1) * 100, 2) if base else None


# ── 신호 ↔ 계좌 동기화 ─────────────────────────────────────────────────────────
def sync(prices=None):
    """체결됐는데 아직 안 산 신호는 매수, 청산됐는데 아직 보유 중이면 매도. 여러 번 불러도 안전.
    → {"bought": n, "sold": n, "skipped": n}"""
    with _SYNC_LOCK:
        return _sync(prices)


def _sync(prices):
    Sig, P, db = D["AiSignal"], D["BotPosition"], D["db"]
    users = bot_users()
    if not users:
        return {"bought": 0, "sold": 0, "skipped": 0}
    strategies = {s.key: s for s in D["BotStrategy"].query.all()}
    fx = _fx()
    out = {"bought": 0, "sold": 0, "skipped": 0}
    linked = {p.signal_id for p in P.query.with_entities(P.signal_id).all()}

    # 1) 매수: 체결가가 있는(=체결된) 신호 중 아직 계좌에 반영 안 된 것. 체결 순서대로
    todo = (Sig.query.filter(Sig.fill_price.isnot(None), Sig.status.in_(["ACTIVE"] + list(FINAL)))
            .order_by(Sig.filled_date, Sig.id).all())
    for sig in todo:
        if sig.id in linked:
            continue
        u, st = users.get(sig.strategy_key), strategies.get(sig.strategy_key)
        if not u or not st:
            continue
        risk = json.loads(st.params_json).get("risk", {})
        fee = json.loads(st.params_json).get("fee_pct", 0.15) / 100
        open_n = P.query.filter_by(user_id=u.id, status="OPEN").count()
        reason, qty = None, 0
        if open_n >= risk.get("max_positions", 10):
            reason = f"최대 보유 {risk.get('max_positions')}종목 도달"
        else:
            tot, cash, _ = equity(u, prices, fx)
            px_krw = sig.fill_price * (1 if sig.market == "KR" else fx)
            qty = math.floor(tot * risk.get("max_weight", 0.1) / px_krw)
            qty = min(qty, math.floor(cash / (px_krw * (1 + fee))))
            if qty < 1:
                reason = "현금 또는 종목당 비중 한도로 1주도 살 수 없음"
        if reason:
            db.session.add(P(signal_id=sig.id, strategy_key=sig.strategy_key, user_id=u.id, ticker=sig.ticker,
                             name=sig.name, currency="KRW" if sig.market == "KR" else "USD",
                             quantity=0, status="SKIPPED", skip_reason=reason))
            db.session.commit()
            out["skipped"] += 1
            continue
        tx, err = D["paper_buy"](u, sig.ticker, sig.name or sig.ticker, qty, float(sig.fill_price), fee_rate=fee,
                                 note=f"[BOT] 신호 #{sig.id} 매수 · {(sig.reason or '')[:200]}", fx=fx, commit=False)
        if err:
            db.session.rollback()
            db.session.add(P(signal_id=sig.id, strategy_key=sig.strategy_key, user_id=u.id, ticker=sig.ticker,
                             name=sig.name, quantity=0, status="SKIPPED", skip_reason=err[:200]))
            db.session.commit()
            out["skipped"] += 1
            continue
        db.session.add(P(signal_id=sig.id, strategy_key=sig.strategy_key, user_id=u.id, ticker=sig.ticker,
                         name=sig.name, currency=tx.currency, quantity=qty, entry_price=sig.fill_price,
                         entry_fx=(fx if tx.currency == "USD" else 1.0), status="OPEN"))
        db.session.commit()
        out["bought"] += 1

    # 2) 매도: 신호가 끝났는데 계좌에 보유 중
    for pos in P.query.filter_by(status="OPEN").all():
        sig = Sig.query.get(pos.signal_id)
        if not sig or sig.status not in FINAL or not sig.exit_price:
            continue
        u = users.get(pos.strategy_key)
        st = strategies.get(pos.strategy_key)
        fee = json.loads(st.params_json).get("fee_pct", 0.15) / 100 if st else 0.0015
        tx, err = D["paper_sell"](u, pos.ticker, pos.quantity, float(sig.exit_price), fee_rate=fee,
                                  note=f"[BOT] 신호 #{sig.id} {sig.status}", fx=fx, commit=False)
        if err:
            db.session.rollback()
            log(f"매도 실패 pos={pos.id}: {err}")
            continue
        exit_fx = fx if pos.currency == "USD" else 1.0
        cost = pos.entry_price * pos.quantity * (pos.entry_fx or 1) * (1 + fee)
        proceeds = sig.exit_price * pos.quantity * exit_fx * (1 - fee)
        pos.exit_price, pos.exit_fx, pos.status, pos.closed_at = sig.exit_price, exit_fx, "CLOSED", datetime.utcnow()
        pos.realized_pnl_krw = round(proceeds - cost)
        pos.pnl_pct = round((proceeds / cost - 1) * 100, 2) if cost else None
        db.session.commit()
        out["sold"] += 1
    if any(out.values()):
        log(f"sync {out}")
    return out


# ── 성적 ───────────────────────────────────────────────────────────────────────
def stats(strategy_key):
    """승률·평균 손익비(평균 수익 ÷ 평균 손실)·MDD·거래 수."""
    P, A = D["BotPosition"], D["AssetSnapshot"]
    closed = P.query.filter_by(strategy_key=strategy_key, status="CLOSED").all()
    wins = [p.pnl_pct for p in closed if (p.pnl_pct or 0) > 0]
    losses = [p.pnl_pct for p in closed if (p.pnl_pct or 0) <= 0]
    u = bot_users().get(strategy_key)
    mdd = None
    if u:
        series = [s.total_assets_krw for s in A.query.filter_by(user_id=u.id).order_by(A.date).all()]
        series = [u.initial_capital or INITIAL] + series
        peak, worst = series[0], 0.0
        for v in series:
            peak = max(peak, v)
            if peak:
                worst = min(worst, (v / peak - 1) * 100)
        mdd = round(worst, 2)
    avg_w = sum(wins) / len(wins) if wins else None
    avg_l = sum(losses) / len(losses) if losses else None
    return {
        "trades": len(closed),
        "open": P.query.filter_by(strategy_key=strategy_key, status="OPEN").count(),
        "skipped": P.query.filter_by(strategy_key=strategy_key, status="SKIPPED").count(),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_win_pct": round(avg_w, 2) if avg_w is not None else None,
        "avg_loss_pct": round(avg_l, 2) if avg_l is not None else None,
        "payoff": round(avg_w / abs(avg_l), 2) if (avg_w is not None and avg_l) else None,
        "realized_pnl_krw": round(sum(p.realized_pnl_krw or 0 for p in closed)),
        "mdd_pct": mdd,
    }
