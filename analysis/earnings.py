"""어닝 서프라이즈 · 실적 발표 일정 (yfinance 무료 데이터, 유료 키 없음).

yfinance 1.8.0 실호출 확인(2026-11-27, NVDA·AAPL·MSFT·005930.KS·069500.KS):
- get_earnings_dates(limit=12): 가장 풍부. 인덱스 = 발표 시각(America/New_York tz), 열 'EPS Estimate'·'Reported EPS'·'Surprise(%)'.
  미래 1건(실제 EPS 없음) + 과거 11건. 미국 종목은 시각으로 장전/장후 판단 가능(예: 16:00 ET = 장후).
  국내 종목도 내려오지만 시각은 한국 발표 시각을 뉴욕 시간으로 바꾼 값이라 장전/장후로 쓰지 않는다(KST 날짜만 사용).
- earnings_history: 최근 4분기만, 인덱스 = 회계 분기 말일(NVDA 는 1·4·7·10월 말). 분기 라벨 맞추기와 예비 이력으로 쓴다.
- calendar: 다음 발표일(날짜만)과 'Earnings Average'. earnings_dates 에 미래 일정이 없을 때만 쓴다.
- ETF(069500.KS 등)는 셋 다 비어 있음 → {"available": False}.

서프라이즈율 = Yahoo Surprise(%) (반올림 전 예상치 기준), 없으면 (실제 − 예상) ÷ |예상| × 100. |서프라이즈| < 1% 는 inline(부합), 그 이상 beat / miss.
"""
from __future__ import annotations

import calendar as _cal
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
TTL = 6 * 3600              # 정상 응답(자료 없음 포함) 6시간
NEG_TTL = 15 * 60           # 조회 실패·시간 초과 15분
TIMEOUT = 4                 # 핵심 조회(일정+이력 동시)를 기다리는 최대 초 (calendar 보조 조회는 따로 4초)
MAX_ENTRIES = 300
MAX_WORKERS = 4             # 1GB 서버: 동시에 도는 yfinance 호출 최대 4개 (모듈 전체 공용)
INLINE_PCT = 1.0
HISTORY_MAX = 8

_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="earn")
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_lock = threading.Lock()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def is_kr(ticker):
    return (ticker or "").upper().endswith((".KS", ".KQ"))


def is_supported(ticker):
    """국내 우선주(단축코드 끝자리가 0 아님)는 실적 자료가 없어 호출하지 않는다."""
    t = (ticker or "").strip().upper()
    if not t:
        return False
    if is_kr(t):
        c = t.rsplit(".", 1)[0]
        return len(c) == 6 and c.isdigit() and c[-1] == "0"
    return True


def verdict(surprise_pct):
    if surprise_pct is None:
        return None
    if abs(surprise_pct) < INLINE_PCT:
        return "inline"
    return "beat" if surprise_pct > 0 else "miss"


def surprise(est, act):
    if est is None or act is None or est == 0:
        return None
    return round((act - est) / abs(est) * 100, 2)


def _month_end(y, m):
    return date(y, m, _cal.monthrange(y, m)[1])


def _shift_quarters(q, k):
    """분기 말일 q 에서 k 분기(3개월 × k) 이동한 월말."""
    m0 = q.year * 12 + (q.month - 1) + 3 * k
    return _month_end(m0 // 12, m0 % 12 + 1)


def _to_dt(v):
    """pandas Timestamp / datetime / date → tz-aware datetime (tz 없으면 뉴욕 시간으로 본다)."""
    if v is None:
        return None
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=ET)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=ET)
    return None


def _event(dt, kr, has_time=True):
    """발표 시각 → {date, date_kst, time_kst, session}. 국내 종목은 KST 날짜만, 미국은 ET 날짜 + 장전/장후."""
    k = dt.astimezone(KST)
    if kr:
        return {"date": k.date(), "date_kst": k.date(), "time_kst": None, "session": None}
    e = dt.astimezone(ET)
    sess, tk = None, None
    if has_time and (e.hour, e.minute) != (0, 0):
        if (e.hour, e.minute) < (9, 30):
            sess = "BMO"
        elif e.hour >= 16:
            sess = "AMC"
        tk = k.strftime("%H:%M")
    return {"date": e.date(), "date_kst": k.date() if tk else e.date(), "time_kst": tk, "session": sess}


def _rows_from_dates(df, kr):
    """earnings_dates DataFrame → 발표 시각 오름차순 [{dt, ev, est, act}] (같은 날 중복 제거)."""
    out, seen = [], set()
    if df is None or getattr(df, "empty", True):
        return out
    cols = {str(c).lower(): c for c in df.columns}
    ce = next((cols[c] for c in cols if "estimate" in c), None)
    ca = next((cols[c] for c in cols if "reported" in c or "actual" in c), None)
    cs = next((cols[c] for c in cols if "surprise" in c), None)
    for idx, row in df.iterrows():
        dt = _to_dt(idx)
        if dt is None:
            continue
        ev = _event(dt, kr)
        if ev["date_kst"] in seen:
            continue
        seen.add(ev["date_kst"])
        out.append({"dt": dt, "ev": ev, "est": _num(row[ce]) if ce is not None else None,
                    "act": _num(row[ca]) if ca is not None else None,
                    "sp": _num(row[cs]) if cs is not None else None})
    out.sort(key=lambda r: r["dt"])
    return out


def _quarters_from_history(df):
    """earnings_history → [(분기 말일, est, act)] 오름차순."""
    out = []
    if df is None or getattr(df, "empty", True):
        return out
    cols = {str(c).lower(): c for c in df.columns}
    ce, ca = cols.get("epsestimate"), cols.get("epsactual")
    for idx, row in df.iterrows():
        d = _to_dt(idx)
        if d is None:
            continue
        out.append((d.date(), _num(row[ce]) if ce is not None else None, _num(row[ca]) if ca is not None else None))
    out.sort(key=lambda x: x[0])
    return out


def _label(q):
    return q.strftime("%y.%m") if q else None


def build(ticker, dates_df=None, history_df=None, cal=None, today=None):
    """세 가지 yfinance 응답 → 화면·프롬프트용 dict. 쓸 만한 자료가 없으면 {"available": False, "reason": "no_data"}."""
    kr = is_kr(ticker)
    today = today or datetime.now(KST).date()
    rows = _rows_from_dates(dates_df, kr)
    quarters = _quarters_from_history(history_df)

    # ── 과거 서프라이즈 ──
    past = [r for r in rows if r["act"] is not None and r["ev"]["date_kst"] <= today][-HISTORY_MAX:]
    hist = []
    if past:
        qe = [None] * len(past)
        for i, r in enumerate(past):                      # 발표일 0~120일 전 분기 말 = 그 분기
            cands = [q for q, _, _ in quarters if 0 < (r["ev"]["date_kst"] - q).days <= 120]
            qe[i] = max(cands) if cands else None
        known = [(i, q) for i, q in enumerate(qe) if q]
        if known:                                        # 나머지는 연속 분기로 보고 3개월씩 이어 붙임
            for i in range(len(qe)):
                if qe[i] is None:
                    j, q = min(known, key=lambda x: abs(x[0] - i))
                    qe[i] = _shift_quarters(q, i - j)
        for r, q in zip(past, qe):
            s = round(r["sp"], 2) if r.get("sp") is not None else surprise(r["est"], r["act"])   # Yahoo 값 = 반올림 전 예상치 기준
            hist.append({"quarter_end": q.isoformat() if q else None, "label": _label(q) or r["ev"]["date"].strftime("%m.%d"),
                         "report_date": r["ev"]["date"].isoformat(), "eps_estimate": r["est"], "eps_actual": r["act"],
                         "surprise_pct": s, "result": verdict(s)})
    elif quarters:                                       # earnings_dates 가 비면 earnings_history 4분기로
        for q, est, act in quarters[-HISTORY_MAX:]:
            if act is None:
                continue
            s = surprise(est, act)
            hist.append({"quarter_end": q.isoformat(), "label": _label(q), "report_date": None, "eps_estimate": est,
                         "eps_actual": act, "surprise_pct": s, "result": verdict(s)})
    hist = [h for h in hist if h["result"]]              # 예상치가 없어 판정 못 한 분기는 버림

    # ── 다음 발표 ──
    nxt = None
    fut = [r for r in rows if r["act"] is None and r["ev"]["date_kst"] >= today]
    cal = cal if isinstance(cal, dict) else {}
    cal_est = _num(cal.get("Earnings Average"))
    if fut:
        r = fut[0]
        nxt = dict(r["ev"], eps_estimate=r["est"] if r["est"] is not None else cal_est)
    else:
        ds = cal.get("Earnings Date") or []
        ds = ds if isinstance(ds, (list, tuple)) else [ds]
        cands = sorted(d for d in (_to_dt(x) for x in ds) if d)
        for d in cands:
            if d.date() >= today:
                nxt = {"date": d.date(), "date_kst": d.date(), "time_kst": None, "session": None, "eps_estimate": cal_est}
                break
    if nxt:
        nxt = {"date": nxt["date"].isoformat(), "date_kst": nxt["date_kst"].isoformat(), "time_kst": nxt["time_kst"],
               "session": nxt["session"], "d_day": (nxt["date_kst"] - today).days,
               "eps_estimate": round(nxt["eps_estimate"], 4) if nxt["eps_estimate"] is not None else None}

    if not hist and not nxt:
        return {"available": False, "reason": "no_data"}

    # ── 요약 (최근 4분기 · 스트릭) ──
    last4 = hist[-4:]
    beats = sum(1 for h in last4 if h["result"] == "beat")
    sp = [h["surprise_pct"] for h in last4 if h["surprise_pct"] is not None]
    beat_streak = miss_streak = 0
    for h in reversed(hist):
        if h["result"] == "beat" and not miss_streak:
            beat_streak += 1
        elif h["result"] == "miss" and not beat_streak:
            miss_streak += 1
        else:
            break
    summary = {"quarters": len(last4), "beats": beats, "beat_ratio": f"{beats}/{len(last4)}" if last4 else None,
               "avg_surprise_pct": round(sum(sp) / len(sp), 1) if sp else None,
               "beat_streak": beat_streak, "miss_streak": miss_streak}
    return {"available": True, "ticker": ticker, "currency": "KRW" if kr else "USD",
            "next_earnings": nxt, "history": hist, "summary": summary, "source": "yfinance"}


def _fetch_yf(ticker):
    """yfinance 3종 조회 (공용 풀, 호출당 TIMEOUT 초). 핵심 두 호출이 모두 실패하면 예외."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    f_dates = _pool.submit(lambda: tk.get_earnings_dates(limit=12))
    f_hist = _pool.submit(lambda: tk.earnings_history)
    res, errs = {}, 0
    deadline = time.monotonic() + TIMEOUT                       # 두 호출이 같이 4초 안에
    for key, fut in (("dates", f_dates), ("history", f_hist)):
        try:
            res[key] = fut.result(timeout=max(0.05, deadline - time.monotonic()))
        except Exception:
            res[key] = None
            errs += 1
    if errs == 2:
        raise TimeoutError("earnings fetch failed")
    d = res["dates"]
    rc = [c for c in d.columns if "reported" in str(c).lower()] if d is not None and not d.empty else []
    has_future = bool(rc) and bool(d[rc[0]].isna().any())          # 미래 일정이 있으면 calendar 생략
    res["calendar"] = None
    if not has_future:
        try:
            res["calendar"] = _pool.submit(lambda: tk.calendar).result(timeout=TIMEOUT)
        except Exception:
            pass
    return res


def _fresh(out, now):
    """캐시(최대 6시간)가 자정을 넘겨도 D-day 가 맞게 오늘(KST) 기준으로 다시 계산. 이미 지난 일정은 뺀다."""
    n = out.get("next_earnings") if out else None
    if not n:
        return out
    d = (date.fromisoformat(n["date_kst"]) - datetime.fromtimestamp(now, KST).date()).days
    if d == n["d_day"]:
        return out
    return dict(out, next_earnings=dict(n, d_day=d) if d >= 0 else None)


def get_earnings(ticker, fetch=None, now=None):
    """→ build() 결과. 정상·자료 없음 6시간, 실패 15분 캐시 (최대 300종목 LRU)."""
    t = (ticker or "").strip().upper()
    if not is_supported(t):
        return {"available": False, "reason": "not_supported"}
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(t)
        if hit and hit[0] > now:
            _cache.move_to_end(t)
            return _fresh(hit[1], now)
    ttl = TTL
    try:
        raw = (fetch or _fetch_yf)(t)
        out = build(t, raw.get("dates"), raw.get("history"), raw.get("calendar"),
                    today=datetime.fromtimestamp(now, KST).date())
    except Exception:
        out, ttl = {"available": False, "reason": "fetch_failed"}, NEG_TTL
    with _lock:
        _cache[t] = (now + ttl, out)
        _cache.move_to_end(t)
        while len(_cache) > MAX_ENTRIES:
            _cache.popitem(last=False)
    return out


def peek(ticker, now=None):
    """캐시에 있는 결과만 (네트워크 없음). 없거나 만료면 None — AI 프롬프트용."""
    t = (ticker or "").strip().upper()
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(t)
    return _fresh(hit[1], now) if hit and hit[0] > now else None


def get_many(tickers, fetch=None, max_workers=MAX_WORKERS):
    """여러 종목 (포트폴리오 캘린더). 캐시 재사용, 동시 조회 최대 4."""
    ts = sorted({(t or "").strip().upper() for t in tickers if t})
    if not ts:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, MAX_WORKERS, len(ts)))) as ex:
        return dict(zip(ts, ex.map(lambda t: get_earnings(t, fetch=fetch), ts)))


def upcoming(results, names=None, today=None, days=60):
    """get_many 결과 → 오늘(KST)부터 days 일 안 실적 발표 [{ticker, name, date, d_day, eps_estimate, beat_streak, session}] 날짜순."""
    names = names or {}
    out = []
    for t, r in (results or {}).items():
        n = (r or {}).get("next_earnings") if (r or {}).get("available") else None
        if not n or n.get("d_day") is None or not 0 <= n["d_day"] <= days:
            continue
        out.append({"ticker": t, "name": names.get(t) or t, "date": n["date"], "date_kst": n["date_kst"], "d_day": n["d_day"],
                    "session": n.get("session"), "eps_estimate": n.get("eps_estimate"), "currency": r.get("currency"),
                    "beat_streak": (r.get("summary") or {}).get("beat_streak", 0)})
    out.sort(key=lambda x: (x["d_day"], x["ticker"]))
    return out


def brief(r):
    """AI 프롬프트 한 줄. 자료 없으면 None."""
    if not r or not r.get("available"):
        return None
    p = []
    n = r.get("next_earnings")
    if n:
        p.append(f"다음 실적 발표 {n['date']} (D-{n['d_day']}"
                 + {"BMO": ", 장전", "AMC": ", 장후"}.get(n.get("session"), "") + ")"
                 + (f", 예상 EPS {n['eps_estimate']:g}" if n.get("eps_estimate") is not None else ""))
    s = r.get("summary") or {}
    if s.get("quarters"):
        t = f"최근 {s['quarters']}분기 Beat {s['beat_ratio']}"
        if s.get("avg_surprise_pct") is not None:
            t += f", 평균 서프라이즈 {s['avg_surprise_pct']:+.1f}%"
        if s.get("beat_streak", 0) >= 2:
            t += f", {s['beat_streak']}분기 연속 Beat"
        elif s.get("miss_streak", 0) >= 2:
            t += f", {s['miss_streak']}분기 연속 Miss"
        p.append(t)
    return " · ".join(p) + " (yfinance 컨센서스 기준)" if p else None
