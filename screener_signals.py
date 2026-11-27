"""스크리너 선행지표 요약 채우기 (7단계). 요청 때는 절대 외부 호출을 하지 않는다 — 이 모듈이 백그라운드로 채운 표만 조인.

- 대상: screener_snapshots 의 종목 (한국 ~350 · 미국 ~537)
- 종류별 갱신 주기와 출처 (모두 기존 모듈 · 캐시 재사용)
  · earn     12시간  analysis.earnings.get_earnings        (모든 종목)
  · flow      3시간  analysis.kr_flow.get_flow             (한국만)
  · kr_short  6시간  analysis.kr_short.get_short           (한국만, 보통주)
  · us_short 72시간  analysis.short_interest (yfinance info) (미국만 — FINRA 반월 공시라 자주 볼 필요 없음)
- 한 번에 작업 BATCH 개를 동시 WORKERS(3)개로 처리하고 쉬었다 다시. 봇 배치·장중 체크 중이면 기다린다.
  처음 받는 종목 → 오래된 종목 순, 같으면 시장별 시가총액 순위(한국·미국 번갈아).
- 실패도 갱신 시각을 남겨(값은 비움) 같은 종목만 계속 붙잡지 않는다.

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import gc
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

D = {}
WORKERS = 3
BATCH = 9
PAUSE_SEC = 3
TTL = {"earn": timedelta(hours=12), "flow": timedelta(hours=3), "kr_short": timedelta(hours=6), "us_short": timedelta(hours=72)}
COL = {"earn": "earn_updated_at", "flow": "flow_updated_at", "kr_short": "short_updated_at", "us_short": "short_updated_at"}
_state = {"running": False, "last_batch": None, "done": 0}


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[SCR-SIG] {msg}")
    except Exception:
        print(f"[SCR-SIG] {msg}", flush=True)


def is_kr(t):
    return (t or "").upper().endswith((".KS", ".KQ"))


def kinds_for(ticker):
    return ["earn", "flow", "kr_short"] if is_kr(ticker) else ["earn", "us_short"]


# ── 결과 → 행 (네트워크 없음, 단위 테스트 대상) ─────────────────────────────────────
def apply(row, kind, res, now=None):
    """모듈 결과를 행에 반영. res 가 None/available False 면 그 종류 값만 비우고 갱신 시각은 남긴다."""
    now = now or datetime.utcnow()
    ok = bool(res) and res.get("available", True) is not False
    if kind == "earn":
        s = (res or {}).get("summary") or {} if ok else {}
        n = (res or {}).get("next_earnings") if ok else None
        row.earn_beat_streak = s.get("beat_streak") if ok else None
        row.earn_miss_streak = s.get("miss_streak") if ok else None
        row.earn_beats_4q = s.get("beats") if ok else None
        row.earn_quarters = s.get("quarters") if ok else None
        row.earn_avg_surprise = s.get("avg_surprise_pct") if ok else None
        row.earn_next_date = date.fromisoformat(n["date_kst"]) if n and n.get("date_kst") else None
        row.earn_updated_at = now
    elif kind == "flow":
        s5 = (res or {}).get("sum_5d") or {} if ok else {}
        row.flow_foreign_5d = s5.get("foreign_krw") if ok else None
        row.flow_inst_5d = s5.get("inst_krw") if ok else None
        row.flow_foreign_streak = res.get("foreign_streak") if ok else None
        row.flow_inst_streak = res.get("inst_streak") if ok else None
        row.flow_updated_at = now
    elif kind == "kr_short":
        row.short_bal_pct = res.get("balance_pct") if ok else None
        row.short_bal_chg_5d = res.get("change_5d_pp") if ok else None
        row.short_updated_at = now
    elif kind == "us_short":
        row.short_pct = res.get("short_pct") if ok else None
        row.short_ratio = res.get("short_ratio") if ok else None
        row.short_updated_at = now
    return row


# ── 할 일 고르기 ─────────────────────────────────────────────────────────────────
def due(limit=BATCH, now=None):
    """→ [(ticker, kind)] 갱신이 필요한 작업 (처음 → 오래된 순, 같으면 시총 큰 순)."""
    M, S = D["Snapshot"], D["Model"]
    now = now or datetime.utcnow()
    rows = (D["db"].session.query(M.ticker, M.market_cap, S)
            .outerjoin(S, S.ticker == M.ticker).filter(M.price.isnot(None)).all())
    # 시총은 시장마다 통화가 달라(원·달러) 그대로 비교하면 한국이 모두 앞선다 → 시장 안 순위로 번갈아
    rank = {}
    for mk in ("KR", "US"):
        mine = sorted((r for r in rows if is_kr(r[0]) == (mk == "KR")), key=lambda r: -(r[1] or 0))
        rank.update({r[0]: i for i, r in enumerate(mine)})
    tasks = []
    for t, cap, s in rows:
        for k in kinds_for(t):
            at = getattr(s, COL[k]) if s is not None else None
            if at is None or now - at >= TTL[k]:
                tasks.append((at is not None, at or datetime.min, rank.get(t, 10 ** 6), t, k))
    tasks.sort()
    return [(t, k) for *_, t, k in tasks[:limit]]


def _fetch(task):
    t, k = task
    fn = D["fetch"].get(k)
    try:
        return t, k, fn(t) if fn else None
    except Exception:
        return t, k, None


def run_batch(limit=BATCH, now=None):
    """작업 limit 개를 동시 WORKERS 개로 받아 저장. → 처리 수"""
    with D["app"].app_context():
        tasks = due(limit, now)
    if not tasks:
        return 0
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(tasks)), thread_name_prefix="scrsig") as ex:
        results = list(ex.map(_fetch, tasks))
    S, db = D["Model"], D["db"]
    with D["app"].app_context():
        try:
            have = {r.ticker: r for r in S.query.filter(S.ticker.in_({t for t, _ in tasks})).all()}
            for t, k, res in results:
                row = have.get(t)
                if row is None:
                    row = have[t] = S(ticker=t, market="KR" if is_kr(t) else "US")
                    db.session.add(row)
                apply(row, k, res, now)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            log(f"저장 실패: {e}")
            return 0
    _state["done"] += len(tasks)
    _state["last_batch"] = datetime.utcnow()
    return len(tasks)


def _loop():
    time.sleep(90)                     # 서버 시작 직후 섹터맵·스크리너 워밍업과 겹치지 않게
    while True:
        try:
            if D["bot_lock"]():
                time.sleep(15)
                continue
            _state["running"] = True
            n = run_batch()
            _state["running"] = False
            gc.collect()
            time.sleep(PAUSE_SEC if n else 600)
        except Exception as e:
            _state["running"] = False
            log(f"루프 오류: {e}")
            time.sleep(60)


_started = False


def start():
    global _started
    if _started or os.environ.get("SCREENER_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="screener-signals").start()
    log("백그라운드 시작 (동시 3개, SCREENER_SCHEDULER=0 으로 끔)")


# ── 조회 도우미 ─────────────────────────────────────────────────────────────────
def coverage(market="ALL"):
    """선행지표 채움 정도 (수집 중 안내용)."""
    M, S = D["Snapshot"], D["Model"]
    q = D["db"].session.query(M.ticker).filter(M.price.isnot(None))
    if market in ("KR", "US"):
        q = q.filter(M.market == market)
    total = q.count()
    kr_total = q.filter(M.market == "KR").count() if market != "US" else 0
    sq = S.query
    if market in ("KR", "US"):
        sq = sq.filter(S.market == market)
    return {"universe": total, "kr_universe": kr_total,
            "with_earn": sq.filter(S.earn_updated_at.isnot(None)).count(),
            "with_flow": sq.filter(S.flow_updated_at.isnot(None)).count(),
            "with_short": sq.filter(S.short_updated_at.isnot(None)).count(),
            "running": _state["running"]}


def d_day(next_date, today=None):
    if not next_date:
        return None
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    d = (next_date - today).days
    return d if d >= 0 else None


def sig_dict(s, today=None):
    if s is None:
        return {}
    return {"earn_beat_streak": s.earn_beat_streak, "earn_miss_streak": s.earn_miss_streak, "earn_beats_4q": s.earn_beats_4q,
            "earn_quarters": s.earn_quarters, "earn_avg_surprise": s.earn_avg_surprise,
            "earn_d_day": d_day(s.earn_next_date, today),
            "flow_foreign_5d": s.flow_foreign_5d, "flow_inst_5d": s.flow_inst_5d,
            "flow_foreign_streak": s.flow_foreign_streak, "flow_inst_streak": s.flow_inst_streak,
            "short_pct": s.short_pct, "short_ratio": s.short_ratio, "short_bal_pct": s.short_bal_pct,
            "short_bal_chg_5d": s.short_bal_chg_5d}
