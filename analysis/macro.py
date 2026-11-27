"""거시(매크로) 지표: 미 10년 국채 금리 · 금 선물 · 달러 인덱스 (+ VIX) → 위험선호/안전자산 선호 국면.

네트워크 없음. app.py 가 홈 시황 캐시를 채울 때 받은 일봉 종가로 여기 함수들을 부른다.
  parse_quote(closes, kind)     → 값·전일 대비 변화
  macro_regime(...)             → 국면(우호·중립·경계) · 종합 판단 보정 점수(-6 ~ +6) · 1줄 근거
  risk_free_from_tnx(tnx)       → DCF 무위험이자율 (2% ~ 7% 클램핑, 없으면 4.5%)
"""
import math

MACRO_SYMBOLS = [
    # (symbol, key, label, kind)
    ("^TNX",     "tnx",  "미 10년 국채", "yield"),
    ("GC=F",     "gold", "금 (GC=F)",   "gold"),
    ("DX-Y.NYB", "dxy",  "달러 인덱스",  "index"),
]
MACRO_ADJ_MAX = 6                     # 종합 판단 보정 상·하한
RF_DEFAULT, RF_MIN, RF_MAX = 0.045, 0.02, 0.07


def _f(v):
    try:
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def parse_quote(closes, kind):
    """최근 종가 리스트(오래된 → 최신) → {value, change_pct, change_pp?}. 값이 없으면 None.

    kind == "yield": ^TNX 는 수익률 그 자체(4.35 = 4.35%) → 전일 대비 %p(change_pp)도 함께.
    """
    vals = [x for x in (_f(c) for c in (closes or [])) if x is not None and x > 0]
    if not vals:
        return None
    cur = vals[-1]
    prev = vals[-2] if len(vals) >= 2 else None
    out = {"value": round(cur, 3 if kind == "yield" else 2),
           "change_pct": round((cur / prev - 1) * 100, 2) if prev else None}
    if kind == "yield":
        if not (0.05 < cur < 20):        # 수익률 이상치 (데이터 오류) 방어
            return None
        out["change_pp"] = round(cur - prev, 3) if prev else None
    return out


def risk_free_from_tnx(tnx):
    """^TNX 값(%) → 무위험이자율(소수). 2% ~ 7% 로 자르고, 없으면 4.5%. → (rf, source)"""
    v = _f((tnx or {}).get("value") if isinstance(tnx, dict) else tnx)
    if v is None:
        return RF_DEFAULT, "기본가정"
    return min(RF_MAX, max(RF_MIN, v / 100)), "미 10년 국채"


def macro_regime(vix=None, tnx=None, gold=None, dxy=None, is_korean=False, leading=None):
    """VIX · 10년물 · 금 · 달러 → 국면과 보정 점수.

    부담(감점): 10년물 ≥ 4.5% (-2) · 당일 +0.08%p 이상 급등 (-1) · VIX ≥ 25 (-2, ≥ 30 이면 -3)
               · 금 +1.5% 이상 (안전자산 선호, -1) · 달러 인덱스 ≥ 106 이고 상승 (한국 종목 외국인 수급 부담, -1)
    우호(가점): 10년물 < 4.0% (+2) · VIX < 18 (+2) · 달러·금 안정 (|금| < 1%, |달러| < 0.5%, +1) · 셋 다면 +1 추가
    선행지표(leading, 6단계): 장단기 금리 역전 -1 · 하이일드 스프레드 경계 -2 / 안정 +1 · 구리/금 확장 +1 / 둔화 -1
    점수는 -6 ~ +6 으로 자르고, +2 이상 우호(Risk-On) / -2 이하 경계(Risk-Off) / 그 사이 중립.
    입력이 모두 없으면 0점 중립 (available=False).
    응답: 기존 키(score·regime·label·summary·reasons) 그대로 + base_score(선행지표 제외) · leading_score · leading_indicators(지표별 상태 요약).
    """
    vix_v = _f((vix or {}).get("value") if isinstance(vix, dict) else vix)
    t = tnx or {}
    g = gold or {}
    d = dxy or {}
    t_v, t_pp = _f(t.get("value")), _f(t.get("change_pp"))
    g_ch = _f(g.get("change_pct"))
    d_v, d_ch = _f(d.get("value")), _f(d.get("change_pct"))
    lead_d, lead_minus, lead_plus = leading_adjust(leading)
    lead_brief = {k: {"state": v["state"], "badge": v["badge"], "value": v.get("value"), "change_20d": v.get("change_20d")}
                  for k, v in (leading or {}).items() if isinstance(v, dict) and v.get("state")}
    if all(x is None for x in (vix_v, t_v, g_ch, d_v)) and not lead_brief:
        return {"available": False, "score": 0, "regime": "neutral", "label": "중립",
                "summary": "매크로 자료 없음 — 보정 없음", "reasons": [], "base_score": 0, "leading_score": 0,
                "leading_indicators": {}}

    score, minus, plus = 0, [], []
    if t_v is not None and t_v >= 4.5:
        score -= 2; minus.append(f"국채 금리 {t_v:.2f}% 고공")
    if t_pp is not None and t_pp >= 0.08:
        score -= 1; minus.append(f"금리 급등 +{t_pp:.2f}%p")
    if vix_v is not None and vix_v >= 25:
        score -= 3 if vix_v >= 30 else 2; minus.append(f"VIX {vix_v:.1f} 공포")
    if g_ch is not None and g_ch >= 1.5:
        score -= 1; minus.append(f"금 +{g_ch:.1f}% 안전자산 선호")
    if is_korean and d_v is not None and d_v >= 106 and d_ch is not None and d_ch > 0:
        score -= 1; minus.append(f"달러 강세 {d_v:.1f} (외국인 수급 부담)")

    calm_rate = t_v is not None and t_v < 4.0
    calm_vix = vix_v is not None and vix_v < 18
    calm_fx = g_ch is not None and d_ch is not None and abs(g_ch) < 1.0 and abs(d_ch) < 0.5
    if calm_rate:
        score += 2; plus.append(f"금리 안정 {t_v:.2f}%")
    if calm_vix:
        score += 2; plus.append(f"VIX {vix_v:.1f} 안정")
    if calm_fx:
        score += 1; plus.append("달러·금 안정")
    if calm_rate and calm_vix and calm_fx:
        score += 1

    base = score
    score += lead_d
    minus += lead_minus
    plus += lead_plus
    score = max(-MACRO_ADJ_MAX, min(MACRO_ADJ_MAX, score))
    if score >= 2:
        regime, label = "risk_on", "우호"
    elif score <= -2:
        regime, label = "risk_off", "경계"
    else:
        regime, label = "neutral", "중립"
    reasons = minus + plus
    head = {"risk_on": "위험선호 구간", "risk_off": "안전자산 선호 구간", "neutral": "방향 탐색 구간"}[regime]
    return {"available": True, "score": score, "regime": regime, "label": label,
            "summary": (" · ".join(reasons[:3]) + " — " if reasons else "") + head, "reasons": reasons,
            "base_score": max(-MACRO_ADJ_MAX, min(MACRO_ADJ_MAX, base)), "leading_score": lead_d, "leading_indicators": lead_brief}


def macro_line(tnx=None, gold=None, dxy=None, regime=None):
    """시장 컨텍스트 1줄: '국채 금리 4.42% · 금 강세 · 달러 약세 — 안전자산 선호 구간'"""
    parts = []
    t_v = _f((tnx or {}).get("value"))
    if t_v is not None:
        pp = _f((tnx or {}).get("change_pp"))
        parts.append(f"국채 금리 {t_v:.2f}%" + (f"({'+' if pp >= 0 else ''}{pp:.2f}%p)" if pp is not None else ""))
    g = _f((gold or {}).get("change_pct"))
    if g is not None:
        parts.append("금 강세" if g >= 0.5 else "금 약세" if g <= -0.5 else "금 보합")
    d = _f((dxy or {}).get("change_pct"))
    if d is not None:
        parts.append("달러 강세" if d >= 0.2 else "달러 약세" if d <= -0.2 else "달러 보합")
    if not parts:
        return None
    tail = {"risk_on": "위험선호 구간", "risk_off": "안전자산 선호 구간", "neutral": "중립 구간"}.get((regime or {}).get("regime"), "")
    return " · ".join(parts) + (f" — {tail}" if tail else "")


# ════════════════════════════════════════════════════════════════════════════════
# 선행지표 3종 (6단계): 장단기 금리차 · 하이일드 스프레드 · 구리/금 비율
#   1순위 FRED 공개 CSV (API 키 불필요) · 폴백 yfinance · 둘 다 실패면 그 지표만 생략
#   정상 6시간 · 실패 15분 캐시, 호출당 4초. 홈 시황 요청을 막지 않도록 백그라운드 1개 스레드로만 갱신.
# ════════════════════════════════════════════════════════════════════════════════
import csv as _csv
import io as _io
import threading as _threading
import time as _time
from datetime import date as _date, datetime as _datetime

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
LEADING_TTL = 6 * 3600
LEADING_NEG_TTL = 15 * 60
LEADING_TIMEOUT = 4
CHG_N = 20                                  # 20영업일(관측치) 변화
LEADING_YF = ("HG=F", "GC=F", "^TNX", "2YY=F", "HYG", "LQD")


def parse_fred_csv(text):
    """FRED fredgraph.csv → [(date, float)] 오래된 순. 헤더 이름(DATE / observation_date)과 무관하게 1·2열을 쓰고,
    결측값 '.' · 빈칸 · 숫자 아님은 건너뛴다."""
    out = []
    if not text:
        return out
    for row in _csv.reader(_io.StringIO(text.lstrip("﻿"))):
        if len(row) < 2:
            continue
        try:
            d = _date.fromisoformat(row[0].strip()[:10])
        except ValueError:
            continue                          # 헤더 · 잘못된 날짜
        v = _f(row[1].strip()) if row[1].strip() not in ("", ".") else None
        if v is not None:
            out.append((d, v))
    out.sort(key=lambda x: x[0])
    return out


def _chg(series, n=CHG_N):
    """마지막 값 − n 관측치 전 값 (자료가 모자라면 가장 오래된 값과 비교, 2개 미만이면 None)."""
    if len(series) < 2:
        return None
    ref = series[-1 - n] if len(series) > n else series[0]
    return series[-1][1] - ref[1]


def yield_curve(series, source="FRED T10Y2Y"):
    """10Y−2Y (%p) → 역전(<0) · 주의(0~0.25, 평탄) · 정상."""
    if not series:
        return None
    v, ch = series[-1][1], _chg(series)
    if v < 0:
        state, badge = "inverted", "역전"
    elif v < 0.25:
        state, badge = "caution", "주의"
    else:
        state, badge = "normal", "정상"
    return {"key": "t10y2y", "label": "10Y-2Y 금리차", "value": round(v, 2), "unit": "%p",
            "change_20d": round(ch, 2) if ch is not None else None, "change_unit": "%p",
            "as_of": series[-1][0].isoformat(), "state": state, "badge": badge, "source": source}


def hy_spread(series, source="FRED BAMLH0A0HYM2"):
    """하이일드 스프레드 (%) → 안정(<3.5) · 보통(3.5~4.5) · 경계(≥4.5 또는 20영업일 +0.5%p 이상 급등)."""
    if not series:
        return None
    v, ch = series[-1][1], _chg(series)
    if v >= 4.5 or (ch is not None and ch >= 0.5):
        state, badge = "alert", "경계"
    elif v < 3.5:
        state, badge = "stable", "안정"
    else:
        state, badge = "normal", "보통"
    return {"key": "hy", "label": "하이일드 스프레드", "value": round(v, 2), "unit": "%",
            "change_20d": round(ch, 2) if ch is not None else None, "change_unit": "%p",
            "as_of": series[-1][0].isoformat(), "state": state, "badge": badge, "source": source}


def _ratio_series(a, b):
    """두 [(date, close)] 를 같은 날짜끼리 나눈 비율 시리즈."""
    bm = dict(b)
    return [(d, x / bm[d]) for d, x in a if bm.get(d)]


def hy_proxy(hyg, lqd):
    """FRED 실패 시: HYG/LQD 비율 20거래일 변화율(%). 하이일드가 우량 회사채보다 약하면(-2% 이하) 경계, +1% 이상 안정."""
    r = _ratio_series(hyg, lqd)
    if len(r) < 2:
        return None
    ref = r[-1 - CHG_N] if len(r) > CHG_N else r[0]
    ch = (r[-1][1] / ref[1] - 1) * 100
    if ch <= -2:
        state, badge = "alert", "경계"
    elif ch >= 1:
        state, badge = "stable", "안정"
    else:
        state, badge = "normal", "보통"
    return {"key": "hy", "label": "하이일드 (HYG/LQD)", "value": None, "unit": "%", "ratio": round(r[-1][1], 4),
            "change_20d": round(ch, 2), "change_unit": "%", "as_of": r[-1][0].isoformat(),
            "state": state, "badge": badge, "source": "yfinance HYG/LQD 근사 (FRED 실패)"}


def copper_gold(hg, gc):
    """구리(HG=F, $/lb) ÷ 금(GC=F, $/oz) × 1000 → 20거래일 변화율 +3% 이상 확장 · −3% 이하 둔화 · 그 사이 중립."""
    r = _ratio_series(hg, gc)
    if len(r) < 2:
        return None
    ref = r[-1 - CHG_N] if len(r) > CHG_N else r[0]
    ch = (r[-1][1] / ref[1] - 1) * 100
    if ch >= 3:
        state, badge = "expansion", "확장"
    elif ch <= -3:
        state, badge = "slowdown", "둔화"
    else:
        state, badge = "neutral", "중립"
    return {"key": "copper_gold", "label": "구리/금 비율", "value": round(r[-1][1] * 1000, 3), "unit": "×1000",
            "change_20d": round(ch, 2), "change_unit": "%", "as_of": r[-1][0].isoformat(),
            "state": state, "badge": badge, "source": "yfinance HG=F / GC=F"}


def leading_adjust(li):
    """선행지표 → (점수 가감, 부담 근거, 우호 근거). 장단기 역전 −1 · 하이일드 경계 −2 / 안정 +1 · 구리/금 확장 +1 / 둔화 −1."""
    li = li or {}
    d, minus, plus = 0, [], []
    yc, hy, cg = li.get("t10y2y"), li.get("hy"), li.get("copper_gold")
    if yc and yc["state"] == "inverted":
        d -= 1; minus.append(f"장단기 금리 역전 {yc['value']:+.2f}%p")
    if hy and hy["state"] == "alert":
        d -= 2; minus.append(f"하이일드 스프레드 경계 {hy['value']:.2f}%" if hy.get("value") is not None else "하이일드 채권 약세")
    elif hy and hy["state"] == "stable":
        d += 1; plus.append("신용 스프레드 안정")
    if cg and cg["state"] == "expansion":
        d += 1; plus.append(f"구리/금 확장 {cg['change_20d']:+.1f}%")
    elif cg and cg["state"] == "slowdown":
        d -= 1; minus.append(f"구리/금 둔화 {cg['change_20d']:+.1f}%")
    return d, minus, plus


def build_leading(fred_get, yf_get):
    """fred_get(series_id) → csv 텍스트 | None, yf_get(symbols) → {symbol: [(date, close)]}.
    → {"t10y2y", "hy", "copper_gold", "fetched_at"} (실패한 지표는 None)."""
    out = {"t10y2y": None, "hy": None, "copper_gold": None}
    fred = {}
    for sid in ("T10Y2Y", "BAMLH0A0HYM2"):
        try:
            fred[sid] = parse_fred_csv(fred_get(sid))[-120:]
        except Exception:
            fred[sid] = []
    yfd = {}
    try:
        yfd = yf_get(LEADING_YF) or {}
    except Exception:
        yfd = {}
    out["t10y2y"] = yield_curve(fred["T10Y2Y"]) if fred["T10Y2Y"] else None
    if not out["t10y2y"]:                     # 폴백: ^TNX − 2년물 수익률 선물(2YY=F), 같은 날짜끼리
        two = dict(yfd.get("2YY=F") or [])
        approx = [(d, v - two[d]) for d, v in (yfd.get("^TNX") or []) if d in two]
        out["t10y2y"] = yield_curve(approx, source="yfinance 근사 (^TNX − 2YY=F, FRED 실패)") if approx else None
    out["hy"] = hy_spread(fred["BAMLH0A0HYM2"]) if fred["BAMLH0A0HYM2"] else hy_proxy(yfd.get("HYG") or [], yfd.get("LQD") or [])
    out["copper_gold"] = copper_gold(yfd.get("HG=F") or [], yfd.get("GC=F") or [])
    out["fetched_at"] = _datetime.utcnow().isoformat() + "Z"
    return out


def _fred_get(sid):
    import requests
    r = requests.get(FRED_URL.format(sid=sid), timeout=LEADING_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    return r.text if r.status_code == 200 else None


def _yf_get(symbols):
    import yfinance as yf
    df = yf.download(" ".join(symbols), period="3mo", interval="1d", group_by="ticker", progress=False,
                     threads=False, auto_adjust=False, timeout=LEADING_TIMEOUT)
    out = {}
    for s in symbols:
        try:
            c = df[s]["Close"].dropna()
            out[s] = [(ix.date(), float(v)) for ix, v in c.items() if float(v) > 0 or s == "2YY=F"]
        except Exception:
            continue
    return out


_LEAD = {"data": None, "exp": 0.0, "busy": False}
_LEAD_LOCK = _threading.Lock()


def leading_refresh(fred_get=None, yf_get=None, now=None):
    """동기 갱신 (백그라운드 스레드·테스트용). 지표가 하나라도 있으면 6시간, 없으면 15분 뒤 다시."""
    now = _time.time() if now is None else now
    try:
        data = build_leading(fred_get or _fred_get, yf_get or _yf_get)
    except Exception:
        data = None
    ok = bool(data and any(data.get(k) for k in ("t10y2y", "hy", "copper_gold")))
    with _LEAD_LOCK:
        if ok:
            _LEAD["data"] = data
        _LEAD["exp"] = now + (LEADING_TTL if ok else LEADING_NEG_TTL)
        _LEAD["busy"] = False
    return data if ok else None


def leading_snapshot(now=None, background=True):
    """캐시된 선행지표 (없으면 None). 만료됐으면 백그라운드 스레드 1개로만 갱신 — 요청을 기다리게 하지 않는다."""
    now = _time.time() if now is None else now
    with _LEAD_LOCK:
        data, stale, busy = _LEAD["data"], now >= _LEAD["exp"], _LEAD["busy"]
        if stale and not busy and background:
            _LEAD["busy"] = True
    if stale and not busy and background:
        _threading.Thread(target=leading_refresh, daemon=True, name="macro-leading").start()
    return data
