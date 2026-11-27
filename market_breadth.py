"""시장 폭(Market Breadth · A/D Line) 일별 스냅샷과 추세 판정 (6단계). 추가 외부 호출 0건.

- 저장: sector_map.refresh() 가 집계한 상승·하락·보합 종목 수를 market_breadth_daily 에 (market, trade_date) 유니크로 UPSERT.
  같은 날 여러 번(장중 15분마다·마감 확정) 들어오면 마지막 값으로 덮어써 하루 1줄. index_close 는 홈 시황 캐시
  (S&P 500 / KOSPI)에 값이 있을 때만 채우고, 없으면 기존 값을 지우지 않는다(COALESCE).
- 판정(analyze): 5거래일 미만이면 '수집 중 (N/5일)'. 그 이상이면 최근 최대 20거래일의 누적 A/D(순상승 종목 수 합)와
  지수 등락을 비교 → 고점 다이버전스 주의(지수↑ A/D↓) · 바닥 다이버전스(지수↓ A/D↑) · 약세 동조 · 시장 폭 약화 · 건강한 흐름.

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

from datetime import date, datetime

D = {}
MIN_DAYS, WINDOW = 5, 20
IDX_MOVE = 1.0          # 지수 변화 ±1% 이상이면 방향 있음
AD_MOVE = 2.0           # 누적 순상승 종목 수 ÷ 누적 종목 수 ±2% 이상이면 방향 있음
INDEX_SYMBOL = {"US": "^GSPC", "KR": "^KS11"}


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[BREADTH] {msg}")
    except Exception:
        print(f"[BREADTH] {msg}", flush=True)


def _d(v):
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def upsert(market, trade_date, advancers, decliners, unchanged=0, index_close=None):
    """(market, trade_date) 1줄 UPSERT. PostgreSQL·SQLite 모두 INSERT … ON CONFLICT DO UPDATE (동시에 여러 번 와도 1줄)."""
    M, db = D["MarketBreadthDaily"], D["db"]
    adv, dec, unc = int(advancers or 0), int(decliners or 0), int(unchanged or 0)
    vals = {"market": market, "trade_date": _d(trade_date), "advancers": adv, "decliners": dec, "unchanged": unc,
            "total": adv + dec + unc, "ad_diff": adv - dec, "index_close": index_close, "updated_at": datetime.utcnow()}
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:                                            # 그 외 DB: 조회 후 갱신 (유니크 제약이 최종 방어)
        row = M.query.filter_by(market=market, trade_date=vals["trade_date"]).first()
        if row is None:
            db.session.add(M(**vals))
        else:
            for k, v in vals.items():
                if k != "index_close" or v is not None:
                    setattr(row, k, v)
        db.session.commit()
        return
    from sqlalchemy import func
    stmt = insert(M.__table__).values(**vals)
    upd = {k: stmt.excluded[k] for k in ("advancers", "decliners", "unchanged", "total", "ad_diff", "updated_at")}
    upd["index_close"] = func.coalesce(stmt.excluded.index_close, M.__table__.c.index_close)
    db.session.execute(stmt.on_conflict_do_update(index_elements=["market", "trade_date"], set_=upd))
    db.session.commit()


def set_index_close(market, trade_date, close):
    """그날 줄이 이미 있을 때만 지수 종가를 채운다 (홈 시황을 받을 때 — 추가 호출 없음). → 갱신 여부"""
    if not close:
        return False
    M, db = D["MarketBreadthDaily"], D["db"]
    row = M.query.filter_by(market=market, trade_date=_d(trade_date)).first()
    if row is None or row.index_close == close:
        return False
    row.index_close = float(close)
    db.session.commit()
    return True


def recent(market, n=WINDOW):
    M = D["MarketBreadthDaily"]
    rows = M.query.filter_by(market=market).order_by(M.trade_date.desc()).limit(n).all()
    return [{"trade_date": r.trade_date, "advancers": r.advancers, "decliners": r.decliners, "unchanged": r.unchanged,
             "total": r.total, "ad_diff": r.ad_diff, "index_close": r.index_close} for r in reversed(rows)]


def analyze(rows):
    """rows: 오래된 → 최신 [{trade_date, advancers, decliners, total, ad_diff, index_close}] → 추세 판정 dict."""
    rows = list(rows or [])[-WINDOW:]
    n = len(rows)
    last = rows[-1] if rows else None
    base = {"days": n, "min_days": MIN_DAYS,
            "as_of": last["trade_date"].isoformat() if last and hasattr(last["trade_date"], "isoformat") else (str(last["trade_date"]) if last else None),
            "advancers": last["advancers"] if last else None, "decliners": last["decliners"] if last else None,
            "ad_ratio": round(last["advancers"] / (last["advancers"] + last["decliners"]) * 100, 1)
                        if last and (last["advancers"] + last["decliners"]) else None}
    if n < MIN_DAYS:
        return {**base, "state": "collecting", "tone": "neu", "label": f"시장 폭 추세 수집 중 ({n}/{MIN_DAYS}일)",
                "ad_net_pct": None, "index_change_pct": None, "ad_line": None}
    ad_sum = sum(r["ad_diff"] for r in rows)
    tot = sum(r["total"] for r in rows) or 1
    ad_net = ad_sum / tot * 100
    idx = [r["index_close"] for r in rows if r.get("index_close")]
    idx_chg = (idx[-1] / idx[0] - 1) * 100 if len(idx) >= 2 else None
    ad_dir = 1 if ad_net >= AD_MOVE else -1 if ad_net <= -AD_MOVE else 0
    ix_dir = None if idx_chg is None else (1 if idx_chg >= IDX_MOVE else -1 if idx_chg <= -IDX_MOVE else 0)
    if ix_dir is None:                               # 지수 종가가 모자람 → A/D 만으로
        state, tone, label = (("improving", "up", "시장 폭 개선") if ad_dir > 0 else ("weakening", "down", "시장 폭 약화")
                              if ad_dir < 0 else ("flat", "neu", "시장 폭 보합"))
    elif ix_dir > 0 and ad_dir < 0:
        state, tone, label = "bearish_divergence", "down", "고점 다이버전스 주의"
    elif ix_dir < 0 and ad_dir > 0:
        state, tone, label = "bullish_divergence", "up", "바닥 다이버전스"
    elif ix_dir < 0 and ad_dir <= 0:
        state, tone, label = "weak", "down", "약세 동조"
    elif ix_dir == 0 and ad_dir < 0:
        state, tone, label = "weakening", "down", "시장 폭 약화"
    else:
        state, tone, label = "healthy", "up", "건강한 흐름"
    return {**base, "state": state, "tone": tone, "label": label, "ad_net_pct": round(ad_net, 1),
            "index_change_pct": round(idx_chg, 2) if idx_chg is not None else None, "ad_line": ad_sum, "window_days": n}


def trend(market):
    """DB 최근 20거래일 → analyze(). 실패하면 수집 중 0일."""
    try:
        return analyze(recent(market))
    except Exception as e:
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        log(f"{market} 추세 계산 실패: {e}")
        return analyze([])
