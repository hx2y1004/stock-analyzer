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


def macro_regime(vix=None, tnx=None, gold=None, dxy=None, is_korean=False):
    """VIX · 10년물 · 금 · 달러 → 국면과 보정 점수.

    부담(감점): 10년물 ≥ 4.5% (-2) · 당일 +0.08%p 이상 급등 (-1) · VIX ≥ 25 (-2, ≥ 30 이면 -3)
               · 금 +1.5% 이상 (안전자산 선호, -1) · 달러 인덱스 ≥ 106 이고 상승 (한국 종목 외국인 수급 부담, -1)
    우호(가점): 10년물 < 4.0% (+2) · VIX < 18 (+2) · 달러·금 안정 (|금| < 1%, |달러| < 0.5%, +1) · 셋 다면 +1 추가
    점수는 -6 ~ +6 으로 자르고, +2 이상 우호(Risk-On) / -2 이하 경계(Risk-Off) / 그 사이 중립.
    입력이 모두 없으면 0점 중립 (available=False).
    """
    vix_v = _f((vix or {}).get("value") if isinstance(vix, dict) else vix)
    t = tnx or {}
    g = gold or {}
    d = dxy or {}
    t_v, t_pp = _f(t.get("value")), _f(t.get("change_pp"))
    g_ch = _f(g.get("change_pct"))
    d_v, d_ch = _f(d.get("value")), _f(d.get("change_pct"))
    if all(x is None for x in (vix_v, t_v, g_ch, d_v)):
        return {"available": False, "score": 0, "regime": "neutral", "label": "중립",
                "summary": "매크로 자료 없음 — 보정 없음", "reasons": []}

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
            "summary": (" · ".join(reasons[:3]) + " — " if reasons else "") + head, "reasons": reasons}


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
