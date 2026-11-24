import threading

import numpy as np

# 분석 문구 속 가격 표기 자릿수. 원화는 정수, 그 외 통화는 소수 둘째 자리.
# gthread 다중 스레드에서 요청끼리 섞이지 않도록 스레드별로 둔다.
_FMT = threading.local()


def _fp(v):
    """가격을 통화에 맞는 자릿수로 쉼표 포함 문자열로."""
    dec = getattr(_FMT, "dec", 2)
    return f"{v:,.{dec}f}"


def _pct(a, b):
    if b and b != 0:
        return round((a - b) / b * 100, 2)
    return 0


def _f(v):
    """None/NaN/Inf → None"""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (f != f or abs(f) == float('inf')) else f
    except Exception:
        return None


def _dividend_yield_fraction(df, info):
    """배당수익률을 소수(0.0089 = 0.89%)로. 상단 주요 지표(app._calc_dividend_yield)와 같은 순서.

    yfinance 의 dividendYield 는 이미 % 값(0.89 = 0.89%)이라 그대로 *100 하면
    945% 처럼 100배 커진다. 그래서 1) 네이버 DPS/가격 2) dividendRate/가격
    3) dividendYield/100 순으로 쓴다.
    """
    naver = _f(info.get("_naver_div_yield"))
    if naver:
        return naver
    try:
        price = float(df["Close"].iloc[-1])
    except Exception:
        price = None
    rate = _f(info.get("dividendRate"))
    if rate and rate > 0 and price and price > 0:
        return rate / price
    dy = _f(info.get("dividendYield"))
    return dy / 100 if dy is not None else None


def _analyze_fundamental(df, info):
    """밸류에이션·수급·시장위치·재무 기반 투자 판단"""
    details = []
    score = 0
    close = float(df["Close"].iloc[-1])

    # ── 밸류에이션 ──────────────────────────────────────
    pe      = _f(info.get("trailingPE"))
    fwd_pe  = _f(info.get("forwardPE"))
    pb      = _f(info.get("priceToBook"))

    val_items, val_parts, val_score = [], [], 0

    if pe is not None:
        if pe < 0:
            val_parts.append(f"PER({pe:.1f}) — 현재 적자 상태")
            val_score -= 10
            val_items.append({"label": f"PER: {pe:.1f}x (적자)", "up": False})
        elif pe < 10:
            val_parts.append(f"PER({pe:.1f}) — 저평가 구간")
            val_score += 15
            val_items.append({"label": f"PER: {pe:.1f}x (저평가)", "up": True})
        elif pe < 20:
            val_parts.append(f"PER({pe:.1f}) — 적정 밸류에이션")
            val_score += 5
            val_items.append({"label": f"PER: {pe:.1f}x (적정)", "up": None})
        elif pe < 35:
            val_parts.append(f"PER({pe:.1f}) — 다소 고평가")
            val_score -= 5
            val_items.append({"label": f"PER: {pe:.1f}x (고평가)", "up": False})
        else:
            val_parts.append(f"PER({pe:.1f}) — 고평가 주의")
            val_score -= 15
            val_items.append({"label": f"PER: {pe:.1f}x (고평가)", "up": False})

    if fwd_pe is not None and fwd_pe > 0:
        val_items.append({"label": f"선행PER: {fwd_pe:.1f}x", "up": fwd_pe < 20})
        if pe and fwd_pe < pe:
            val_parts.append(f"선행PER({fwd_pe:.1f})이 현재PER보다 낮아 실적 개선 기대")
            val_score += 5

    if pb is not None:
        val_items.append({"label": f"PBR: {pb:.2f}x", "up": pb < 2})
        if pb < 1:
            val_parts.append(f"PBR({pb:.2f}) — 자산 대비 저평가")
            val_score += 5
        elif pb > 5:
            val_parts.append(f"PBR({pb:.2f}) — 자산 대비 고평가")
            val_score -= 5

    score += val_score
    if val_parts:
        if val_score >= 10:
            val_state, val_color = "저평가 (매수 우위)", "bullish"
        elif val_score >= 0:
            val_state, val_color = "적정 (중립)", "neutral"
        else:
            val_state, val_color = "고평가 (주의)", "bearish"
        val_desc = ". ".join(val_parts) + "."
    else:
        val_state, val_color = "데이터 부족", "neutral"
        val_desc = "밸류에이션 데이터를 불러올 수 없습니다. ETF·일부 해외주 또는 적자 기업은 PER이 제공되지 않을 수 있습니다."
        val_items = [{"label": "PER: —", "up": None}]

    details.append({
        "indicator": "밸류에이션",
        "state": val_state, "color": val_color,
        "desc": val_desc, "items": val_items,
    })

    # ── 수급 & 거래량 ──────────────────────────────────
    vol_now   = float(df["Volume"].iloc[-1])
    vol_avg20 = float(df["Volume"].tail(20).mean())
    vol_avg5  = float(df["Volume"].tail(5).mean())
    vol_ratio = vol_now / vol_avg20 * 100 if vol_avg20 > 0 else None
    vol5_vs20 = vol_avg5 / vol_avg20 * 100 if vol_avg20 > 0 else None
    beta      = _f(info.get("beta"))

    sup_items, sup_parts, sup_score = [], [], 0
    sup_tip = None
    if vol_ratio is not None:
        sup_tip = "vol_high" if vol_ratio >= 150 else ("vol_normal" if vol_ratio >= 80 else "vol_low")

    if vol_ratio is not None:
        sup_items.append({"label": f"당일거래량: {vol_ratio:.0f}% (평균대비)", "up": vol_ratio >= 120})
        if vol_ratio >= 200:
            sup_parts.append(f"거래량이 평균 대비 {vol_ratio:.0f}%로 급등 — 세력 매집 또는 이벤트 발생 가능성")
            sup_score += 15
        elif vol_ratio >= 150:
            sup_parts.append(f"거래량이 평균 대비 {vol_ratio:.0f}%로 증가 — 매집 신호")
            sup_score += 10
        elif vol_ratio >= 100:
            sup_parts.append(f"거래량 {vol_ratio:.0f}%로 정상 수준 유지")
        else:
            sup_parts.append(f"거래량 {vol_ratio:.0f}%로 평균 이하 — 관심 감소 신호")
            sup_score -= 10

    if vol5_vs20 is not None:
        sup_items.append({"label": f"5일평균량: {vol5_vs20:.0f}% (20일비)", "up": vol5_vs20 >= 110})
        if vol5_vs20 >= 130:
            sup_parts.append(f"최근 5일 거래량 증가세({vol5_vs20:.0f}%) — 단기 수급 유입")
            sup_score += 5
        elif vol5_vs20 <= 70:
            sup_parts.append(f"최근 5일 거래량 감소세({vol5_vs20:.0f}%) — 단기 수급 이탈")
            sup_score -= 5

    if beta is not None:
        beta_label = "고변동성 (시장보다 민감)" if beta > 1.5 else "저변동성 (방어주 특성)" if beta < 0.7 else "시장 평균 수준"
        sup_items.append({"label": f"베타: {beta:.2f} ({beta_label})", "up": None})
        if beta > 1.5:
            sup_parts.append(f"베타 {beta:.2f}로 시장 평균보다 변동성이 높아 리스크 관리 필요")

    score += sup_score
    if sup_parts:
        if sup_score >= 10:
            sup_state, sup_color = "수급 양호 (매집 우위)", "bullish"
        elif sup_score >= 0:
            sup_state, sup_color = "수급 보통 (중립)", "neutral"
        else:
            sup_state, sup_color = "수급 약화 (이탈 우위)", "bearish"
        sup_desc = ". ".join(sup_parts) + "."
    else:
        sup_state, sup_color = "데이터 부족", "neutral"
        sup_desc = "거래량 데이터를 불러올 수 없습니다."
        sup_items = [{"label": "거래량: —", "up": None}]

    details.append({
        "indicator": "수급 & 거래량",
        "state": sup_state, "color": sup_color,
        "tip_key": sup_tip,   # 쉬운 설명은 거래량 비율로 고른다
        "desc": sup_desc, "items": sup_items,
    })

    # ── 시장 위치 & 모멘텀 ──────────────────────────────
    year_high = float(df["High"].tail(252).max())
    year_low  = float(df["Low"].tail(252).min())
    pos_52w   = (close - year_low) / (year_high - year_low) * 100 if year_high > year_low else 50
    mom20 = ((close / float(df["Close"].iloc[-20]) - 1) * 100) if len(df) >= 21 else None
    mom60 = ((close / float(df["Close"].iloc[-60]) - 1) * 100) if len(df) >= 61 else None

    pos_items = [{"label": f"52주 위치: {pos_52w:.1f}%", "up": pos_52w >= 50}]
    pos_parts, pos_score = [], 0

    if pos_52w >= 80:
        pos_parts.append(f"52주 고점 부근({pos_52w:.1f}%) — 강한 상승 추세 유지 중")
        pos_score += 10
    elif pos_52w >= 55:
        pos_parts.append(f"52주 중·상위권({pos_52w:.1f}%) — 상승 모멘텀 유지")
        pos_score += 5
    elif pos_52w >= 30:
        pos_parts.append(f"52주 중·하위권({pos_52w:.1f}%) — 하락 추세 주의")
        pos_score -= 5
    else:
        pos_parts.append(f"52주 저점 근방({pos_52w:.1f}%) — 강한 하락 혹은 바닥권 접근")
        pos_score -= 10

    if mom20 is not None:
        pos_items.append({"label": f"20일 수익률: {mom20:+.1f}%", "up": mom20 >= 0})
        if mom20 >= 10:
            pos_parts.append(f"20일 수익률 {mom20:+.1f}%로 강한 단기 모멘텀")
            pos_score += 5
        elif mom20 <= -10:
            pos_parts.append(f"20일 수익률 {mom20:+.1f}%로 단기 하락 모멘텀 강함")
            pos_score -= 5

    if mom60 is not None:
        pos_items.append({"label": f"60일 수익률: {mom60:+.1f}%", "up": mom60 >= 0})
        if mom60 >= 15:
            pos_score += 5
        elif mom60 <= -15:
            pos_score -= 5

    pos_items.append({"label": f"52주 고가: {_fp(year_high)}", "up": None})
    pos_items.append({"label": f"52주 저가: {_fp(year_low)}", "up": None})

    score += pos_score
    if pos_score >= 10:
        pos_state, pos_color = "상승 추세 (강세)", "bullish"
    elif pos_score >= 0:
        pos_state, pos_color = "중립 (방향 탐색)", "neutral"
    else:
        pos_state, pos_color = "하락 추세 (약세)", "bearish"

    pos_desc = ". ".join(pos_parts)
    pos_desc += f". 52주 고가({_fp(year_high)})까지 {_pct(year_high, close):+.1f}%, 저가({_fp(year_low)}) 대비 {_pct(close, year_low):+.1f}% 위치합니다."

    details.append({
        "indicator": "시장 위치 & 모멘텀",
        "state": pos_state, "color": pos_color,
        "desc": pos_desc, "items": pos_items,
    })

    # ── 재무 건전성 ──────────────────────────────────────
    roe = _f(info.get("returnOnEquity"))
    de  = _f(info.get("debtToEquity"))
    cr  = _f(info.get("currentRatio"))
    div = _dividend_yield_fraction(df, info)
    rev_growth = _f(info.get("revenueGrowth"))
    earn_growth = _f(info.get("earningsGrowth"))

    fin_items, fin_parts, fin_score = [], [], 0

    if roe is not None:
        roe_pct = roe * 100
        fin_items.append({"label": f"ROE: {roe_pct:.1f}%", "up": roe_pct >= 10})
        if roe_pct >= 20:
            fin_parts.append(f"ROE({roe_pct:.1f}%) 우수 — 자본 효율성 높음")
            fin_score += 10
        elif roe_pct >= 10:
            fin_parts.append(f"ROE({roe_pct:.1f}%) 양호")
            fin_score += 5
        else:
            fin_parts.append(f"ROE({roe_pct:.1f}%) 낮음 — 수익성 개선 필요")
            fin_score -= 5

    if de is not None:
        fin_items.append({"label": f"부채비율: {de:.1f}%", "up": de < 100})
        if de < 50:
            fin_parts.append(f"부채비율({de:.1f}%) 매우 안정적")
            fin_score += 10
        elif de < 100:
            fin_parts.append(f"부채비율({de:.1f}%) 양호")
            fin_score += 5
        elif de < 200:
            fin_parts.append(f"부채비율({de:.1f}%) 주의 수준")
            fin_score -= 5
        else:
            fin_parts.append(f"부채비율({de:.1f}%) 높음 — 레버리지 리스크 존재")
            fin_score -= 10

    if cr is not None:
        fin_items.append({"label": f"유동비율: {cr:.2f}", "up": cr >= 1.5})
        if cr >= 2:
            fin_parts.append(f"유동비율({cr:.2f}) 우수 — 단기 채무 상환 능력 양호")
            fin_score += 5
        elif cr >= 1:
            fin_parts.append(f"유동비율({cr:.2f}) 적정")
        else:
            fin_parts.append(f"유동비율({cr:.2f}) 낮음 — 단기 유동성 주의")
            fin_score -= 5

    if rev_growth is not None:
        rev_pct = rev_growth * 100
        fin_items.append({"label": f"매출성장: {rev_pct:+.1f}%", "up": rev_pct >= 5})
        if rev_pct >= 15:
            fin_parts.append(f"매출 {rev_pct:+.1f}% 성장 — 고성장 기업")
            fin_score += 10
        elif rev_pct >= 5:
            fin_parts.append(f"매출 {rev_pct:+.1f}% 성장 중")
            fin_score += 5
        elif rev_pct < 0:
            fin_parts.append(f"매출 {rev_pct:+.1f}% 감소 — 역성장 주의")
            fin_score -= 5

    if earn_growth is not None:
        eg_pct = earn_growth * 100
        fin_items.append({"label": f"이익성장: {eg_pct:+.1f}%", "up": eg_pct >= 5})
        if eg_pct >= 20:
            fin_score += 5

    if div is not None:
        div_pct = div * 100
        fin_items.append({"label": f"배당수익률: {div_pct:.2f}%", "up": div_pct >= 2})
        if div_pct >= 3:
            fin_parts.append(f"배당수익률({div_pct:.2f}%) 높음 — 안정적 배당 수익 확보")
            fin_score += 5
        elif div_pct >= 1:
            fin_parts.append(f"배당수익률({div_pct:.2f}%)")

    score += fin_score
    if fin_parts:
        if fin_score >= 15:
            fin_state, fin_color = "재무 우수 (안정적)", "bullish"
        elif fin_score >= 0:
            fin_state, fin_color = "재무 양호 (중립)", "neutral"
        else:
            fin_state, fin_color = "재무 주의 (리스크)", "bearish"
        fin_desc = ". ".join(fin_parts) + "."
    else:
        fin_state, fin_color = "데이터 부족", "neutral"
        fin_desc = "재무 데이터를 불러올 수 없습니다. ETF·일부 해외주는 재무 데이터가 제공되지 않을 수 있습니다."
        fin_items = [{"label": "재무데이터: —", "up": None}]

    details.append({
        "indicator": "재무 건전성",
        "state": fin_state, "color": fin_color,
        "desc": fin_desc, "items": fin_items,
    })

    return {"details": details, "score": score}


def analyze_signals(df, info, df_weekly=None, stock=None):
    _FMT.dec = 0 if (info or {}).get("currency") == "KRW" else 2
    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    signals = []
    details = []
    score = 0

    close = float(latest["Close"])

    # ── 이동평균선 ────────────────────────────────────
    ma_analysis = {}
    for ma in ["MA5", "MA20", "MA60", "MA120"]:
        v = latest.get(ma, float("nan"))
        if not np.isnan(v):
            above = bool(close > v)
            ma_analysis[ma] = {"value": round(float(v), 2), "above": above}
            score += 5 if above else -5

    ma5   = ma_analysis.get("MA5",  {}).get("value")
    ma20  = ma_analysis.get("MA20", {}).get("value")
    ma60  = ma_analysis.get("MA60", {}).get("value")
    ma120 = ma_analysis.get("MA120",{}).get("value")

    aligned = all(k in ma_analysis for k in ["MA5","MA20","MA60"])
    if aligned:
        if ma5 > ma20 > ma60:
            signals.append({"type": "bullish", "text": "이동평균선 정배열 — 상승 추세"})
            score += 15
            ma_state, ma_color = "정배열 (상승 추세)", "bullish"
            ma_desc = (
                f"MA5({_fp(ma5)}) > MA20({_fp(ma20)}) > MA60({_fp(ma60)}) 순서로 "
                f"정배열 상태입니다. 단기·중기·장기 이동평균이 모두 우상향하고 있어 "
                f"추세적 상승 흐름이 유지되고 있습니다. "
                f"현재가는 MA5 대비 {_pct(close, ma5):+.1f}%, MA20 대비 {_pct(close, ma20):+.1f}% 위치합니다."
            )
        elif ma5 < ma20 < ma60:
            signals.append({"type": "bearish", "text": "이동평균선 역배열 — 하락 추세"})
            score -= 15
            ma_state, ma_color = "역배열 (하락 추세)", "bearish"
            ma_desc = (
                f"MA5({_fp(ma5)}) < MA20({_fp(ma20)}) < MA60({_fp(ma60)}) 순서로 "
                f"역배열 상태입니다. 단기 이동평균이 장기 이동평균 아래에 있어 "
                f"하락 추세가 지속되고 있습니다. "
                f"현재가는 MA20 대비 {_pct(close, ma20):+.1f}% 위치합니다."
            )
        else:
            ma_state, ma_color = "혼조 (방향 불명확)", "neutral"
            ma_desc = (
                f"이동평균선이 혼조 상태입니다. "
                f"MA5({_fp(ma5)}), MA20({_fp(ma20)}), MA60({_fp(ma60)})이 "
                f"완전한 정·역배열을 이루지 않고 있어 방향성이 불분명합니다. "
                f"추세 확인 후 진입을 권장합니다."
            )
    else:
        ma_state, ma_color, ma_desc = "데이터 부족", "neutral", "이동평균선 데이터가 충분하지 않습니다."

    details.append({
        "indicator": "이동평균선 (MA)", "short": "이동평균(MA)",
        "state": ma_state, "color": ma_color, "desc": ma_desc,
        "metrics": [m for m in [
            {"label": "MA20 대비", "value": f"{_pct(close, ma20):+.1f}%"} if ma20 else None,
            {"label": "MA60 대비", "value": f"{_pct(close, ma60):+.1f}%"} if ma60 else None,
        ] if m],
        "gauge": None,
        "items": [
            {"label": f"MA5  {_fp(ma5)}"   if ma5   else "MA5 —",   "up": ma_analysis.get("MA5",  {}).get("above")},
            {"label": f"MA20 {_fp(ma20)}"  if ma20  else "MA20 —",  "up": ma_analysis.get("MA20", {}).get("above")},
            {"label": f"MA60 {_fp(ma60)}"  if ma60  else "MA60 —",  "up": ma_analysis.get("MA60", {}).get("above")},
            {"label": f"MA120 {_fp(ma120)}" if ma120 else "MA120 —", "up": ma_analysis.get("MA120",{}).get("above")},
        ]
    })

    # ── 추세 강도(ADX) · 강한 상승 판정: 과열 지표(RSI 70↑ · BB 상단)를 '추세 방향 점수'와 분리하는 데 쓴다 ──
    adx_v   = _f(latest.get("ADX"))
    pdi_v   = _f(latest.get("PLUS_DI"))
    mdi_v   = _f(latest.get("MINUS_DI"))
    ma_bull = bool(aligned and ma5 > ma20 > ma60)
    adx_up  = bool(adx_v is not None and adx_v >= 25 and pdi_v is not None and mdi_v is not None and pdi_v > mdi_v)
    strong_up = ma_bull or (adx_up and ma20 is not None and close > ma20)
    overheat_reasons = []                     # 단기 과열 경고 (점수와 별개로 배지만)

    # ── 볼린저밴드 ────────────────────────────────────
    # 중심선(20일 SMA)은 MA20 과 같은 선 → 중심선 위·아래로는 점수를 주지 않는다 (MA20 에서 이미 ±5)
    bb_upper = float(latest.get("BB_upper", float("nan")))
    bb_lower = float(latest.get("BB_lower", float("nan")))
    bb_mid   = float(latest.get("BB_mid",   float("nan")))

    if not any(np.isnan(v) for v in [bb_upper, bb_lower, bb_mid]):
        band = bb_upper - bb_lower
        bb_position  = (close - bb_lower) / band * 100 if band > 0 else 50.0      # 밴드 폭 0 → 가운데
        bb_width_pct = round(band / bb_mid * 100, 1) if bb_mid else 0

        if band > 0 and close >= bb_upper:
            overheat_reasons.append("BB 상단 돌파")
            bb_tip = "upper_break"
            if strong_up:
                signals.append({"type": "info", "text": "⚠️ 볼린저밴드 상단 돌파 — 강한 상승 중 단기 과열"})
                bb_state, bb_color = "상단 돌파 (강한 상승)", "bullish"
                bb_desc = (
                    f"현재가({_fp(close)})가 볼린저밴드 상단({_fp(bb_upper)})을 돌파했습니다. "
                    f"이동평균 정배열·추세 강도(ADX)가 받쳐 주는 강한 상승 구간이라 추세 점수는 깎지 않고, "
                    f"단기 과열 경고만 표시합니다. 밴드를 따라 오르는 '밴드 워크'일 수 있지만 눌림이 올 수 있습니다. "
                    f"밴드 폭(변동성) {bb_width_pct}%"
                )
            else:
                signals.append({"type": "bearish", "text": "볼린저밴드 상단 돌파 — 과매수 주의"})
                score -= 5
                bb_state, bb_color = "상단 돌파 (과매수)", "bearish"
                bb_desc = (
                    f"현재가({_fp(close)})가 볼린저밴드 상단({_fp(bb_upper)})을 돌파했습니다. "
                    f"추세가 뚜렷하지 않은 상태의 상단 이탈은 단기 과매수일 가능성이 높아 조정이나 상단 저항에 주의가 필요합니다. "
                    f"밴드 폭(변동성) {bb_width_pct}%"
                )
        elif band > 0 and close <= bb_lower:
            signals.append({"type": "bullish", "text": "볼린저밴드 하단 이탈 — 반등 가능성"})
            score += 10
            bb_state, bb_color = "하단 이탈 (과매도)", "bullish"
            bb_tip = "lower_break"
            bb_desc = (
                f"현재가({_fp(close)})가 볼린저밴드 하단({_fp(bb_lower)})을 이탈했습니다. "
                f"과매도 구간으로 기술적 반등 가능성이 있습니다. "
                f"단, 강한 하락 추세에서는 하단 이탈이 지속될 수 있으므로 추세 지표와 함께 확인이 필요합니다. "
                f"밴드 폭 {bb_width_pct}%"
            )
        elif close > bb_mid:
            signals.append({"type": "bullish", "text": "볼린저밴드 중심선 위 — 상승 모멘텀"})
            bb_state, bb_color = "중심선 위 (상승 우위)", "bullish"
            bb_tip = "above_mid"
            bb_desc = (
                f"현재가({_fp(close)})가 볼린저밴드 중심선({_fp(bb_mid)}) 위에 위치합니다. "
                f"밴드 내 상위 {bb_position:.1f}% 구간으로 상승 모멘텀이 우세합니다. "
                f"상단({_fp(bb_upper)})까지 {_pct(bb_upper, close):+.1f}% 여유가 있습니다. "
                f"밴드 폭(변동성) {bb_width_pct}%"
            )
        else:
            bb_state, bb_color = "중심선 아래 (하락 우위)", "bearish"
            bb_tip = "below_mid"
            bb_desc = (
                f"현재가({_fp(close)})가 볼린저밴드 중심선({_fp(bb_mid)}) 아래에 위치합니다. "
                f"밴드 내 하위 {bb_position:.1f}% 구간으로 하락 압력이 우세합니다. "
                f"하단({_fp(bb_lower)})까지 {_pct(bb_lower, close):+.1f}%입니다. "
                f"밴드 폭(변동성) {bb_width_pct}%"
            )
    else:
        bb_position  = None
        bb_width_pct = 0
        bb_state, bb_color = "데이터 부족", "neutral"
        bb_tip = None
        bb_desc = "볼린저밴드 데이터가 충분하지 않습니다."

    details.append({
        "indicator": "볼린저밴드 (BB)", "short": "볼린저밴드(BB)",
        "state": bb_state, "color": bb_color, "desc": bb_desc,
        "tip_key": bb_tip,   # 쉬운 설명은 색(판단)이 아니라 실제 위치로 고른다
        "overheat": bb_tip == "upper_break",
        "metrics": ([{"label": "밴드 내 위치", "value": f"{bb_position:.0f}%"}, {"label": "밴드 폭", "value": f"{bb_width_pct}%"}]
                    if bb_position is not None else []),
        "gauge": ({"kind": "bb", "value": round(max(0.0, min(100.0, bb_position)), 1), "min_label": "하단", "max_label": "상단"}
                  if bb_position is not None else None),
        "items": [
            {"label": f"상단  {_fp(bb_upper)}" if not np.isnan(bb_upper) else "상단 —", "up": None},
            {"label": f"중심  {_fp(bb_mid)}"   if not np.isnan(bb_mid)   else "중심 —", "up": None},
            {"label": f"하단  {_fp(bb_lower)}" if not np.isnan(bb_lower) else "하단 —", "up": None},
            {"label": f"위치  {bb_position:.1f}%" if bb_position is not None else "위치 —",
             "up": bb_position > 50 if bb_position is not None else None},
        ]
    })

    # ── 일목균형표 ────────────────────────────────────
    tenkan   = float(latest.get("tenkan",   float("nan")))
    kijun    = float(latest.get("kijun",    float("nan")))
    senkou_a = float(latest.get("senkou_a", float("nan")))
    senkou_b = float(latest.get("senkou_b", float("nan")))

    ichi_items, ichi_parts, ichi_score_delta = [], [], 0

    if not any(np.isnan(v) for v in [tenkan, kijun]):
        if tenkan > kijun:
            ichi_parts.append(f"전환선({_fp(tenkan)}) > 기준선({_fp(kijun)}) → 단기 매수 우위")
            ichi_score_delta += 10
        elif tenkan < kijun:
            ichi_parts.append(f"전환선({_fp(tenkan)}) < 기준선({_fp(kijun)}) → 단기 매도 우위")
            ichi_score_delta -= 10
        else:
            ichi_parts.append(f"전환선 = 기준선({_fp(kijun)}) → 단기 방향 중립")      # 동률은 0점
        ichi_items += [
            {"label": f"전환선 {_fp(tenkan)}", "up": (tenkan > kijun) if tenkan != kijun else None},
            {"label": f"기준선 {_fp(kijun)}",  "up": (tenkan > kijun) if tenkan != kijun else None},
        ]

    cloud_top = cloud_bot = None
    if not any(np.isnan(v) for v in [senkou_a, senkou_b]):
        cloud_top = max(senkou_a, senkou_b)
        cloud_bot = min(senkou_a, senkou_b)
        if close > cloud_top:
            ichi_parts.append(f"구름대({_fp(cloud_bot)}~{_fp(cloud_top)}) 위 → 강한 상승 신호")
            ichi_score_delta += 15
            ichi_state, ichi_color = "구름대 위 (강세)", "bullish"
        elif close < cloud_bot:
            ichi_parts.append(f"구름대({_fp(cloud_bot)}~{_fp(cloud_top)}) 아래 → 강한 하락 신호")
            ichi_score_delta -= 15
            ichi_state, ichi_color = "구름대 아래 (약세)", "bearish"
        else:
            ichi_parts.append(f"구름대({_fp(cloud_bot)}~{_fp(cloud_top)}) 내부 → 방향 탐색 중")
            ichi_state, ichi_color = "구름대 내부 (중립)", "neutral"
        ichi_items.append({"label": f"구름상단 {_fp(cloud_top)}", "up": close > cloud_top})
        ichi_items.append({"label": f"구름하단 {_fp(cloud_bot)}", "up": close > cloud_bot})
    else:
        ichi_state, ichi_color = "데이터 부족", "neutral"

    score += ichi_score_delta
    ichi_desc = " ".join(ichi_parts) if ichi_parts else "일목균형표 데이터가 충분하지 않습니다."
    if ichi_parts:
        signals.append({"type": ichi_color if ichi_color != "neutral" else "info",
                        "text": f"일목균형표: {' / '.join([p.split('→')[1].strip() for p in ichi_parts])}"})
    ichi_metrics = []
    if cloud_top is not None:
        ref = cloud_top if close > cloud_top else cloud_bot if close < cloud_bot else None
        ichi_metrics.append({"label": "구름대 대비", "value": f"{_pct(close, ref):+.1f}%" if ref else "구름 안"})
    if not any(np.isnan(v) for v in [tenkan, kijun]):
        ichi_metrics.append({"label": "전환/기준", "value": "전환 > 기준" if tenkan > kijun else "전환 < 기준" if tenkan < kijun else "같음"})
    details.append({
        "indicator": "일목균형표", "short": "일목균형표",
        "state": ichi_state, "color": ichi_color,
        "desc": ichi_desc, "items": ichi_items, "metrics": ichi_metrics, "gauge": None,
    })

    # ── RSI ──────────────────────────────────────────
    # 70 이상은 '단기 과열' 경고. 강한 상승 추세(정배열·ADX)면 추세 점수는 깎지 않는다 (자기 상쇄 방지)
    rsi = float(latest.get("RSI", float("nan")))
    rsi_tip = None
    if not np.isnan(rsi):
        if rsi >= 70:
            overheat_reasons.append(f"RSI {rsi:.0f}")
            rsi_tip = "bearish"
            if strong_up:
                signals.append({"type": "info", "text": f"⚠️ RSI {rsi:.1f} — 강한 상승 중 단기 과열"})
                rsi_state, rsi_color = f"{rsi:.1f} — 과매수 (단기 과열)", "neutral"
                rsi_desc = (
                    f"RSI가 {rsi:.1f}로 과매수 기준선(70)을 넘었지만 상승 추세가 강해 추세 점수는 깎지 않았습니다. "
                    f"강한 종목은 RSI 70 위에서 한동안 머물기도 합니다. 다만 단기 과열이라 추격 매수보다 눌림을 기다리는 편이 안전합니다."
                )
            else:
                signals.append({"type": "bearish", "text": f"RSI {rsi:.1f} — 과매수 구간"})
                score -= 5
                rsi_state, rsi_color = f"{rsi:.1f} — 과매수", "bearish"
                rsi_desc = (
                    f"RSI가 {rsi:.1f}로 과매수 기준선(70)을 상회합니다. "
                    f"단기적으로 가격이 빠르게 상승했음을 의미하며, 차익 실현 매물이 나올 수 있습니다. "
                    f"RSI가 70 아래로 되돌아올 때 추가 하락이 나타나는 경우가 많습니다."
                )
        elif rsi <= 30:
            signals.append({"type": "bullish", "text": f"RSI {rsi:.1f} — 과매도 구간 (반등 기대)"})
            score += 10
            rsi_state, rsi_color = f"{rsi:.1f} — 과매도", "bullish"
            rsi_tip = "bullish"
            rsi_desc = (
                f"RSI가 {rsi:.1f}로 과매도 기준선(30) 아래입니다. "
                f"단기적으로 가격이 과도하게 하락했을 가능성이 있으며, 기술적 반등 기대감이 높습니다. "
                f"하지만 강한 하락 추세에서는 과매도 상태가 지속될 수 있어 추세 확인이 필요합니다."
            )
        else:
            signals.append({"type": "info", "text": f"RSI {rsi:.1f} — 중립 구간"})
            rsi_state, rsi_color = f"{rsi:.1f} — 중립", "neutral"
            rsi_tip = "neutral"
            dist_to_ob = round(70 - rsi, 1)
            dist_to_os = round(rsi - 30, 1)
            rsi_desc = (
                f"RSI가 {rsi:.1f}로 과매수(70)·과매도(30) 기준선 사이 중립 구간에 있습니다. "
                f"과매수까지 {dist_to_ob}pt, 과매도까지 {dist_to_os}pt 여유가 있습니다. "
                f"{'50 위로 모멘텀이 상승 방향입니다.' if rsi >= 50 else '50 아래로 모멘텀이 하락 방향입니다.'}"
            )
    else:
        rsi_state, rsi_color, rsi_desc = "데이터 부족", "neutral", "RSI 데이터가 충분하지 않습니다."

    details.append({
        "indicator": "RSI (14)", "short": "RSI(14)",
        "state": rsi_state, "color": rsi_color, "desc": rsi_desc, "tip_key": rsi_tip,
        "overheat": (not np.isnan(rsi)) and rsi >= 70,
        "metrics": [{"label": "RSI", "value": f"{rsi:.1f}"},
                    {"label": "모멘텀", "value": "50 위 (상승)" if rsi >= 50 else "50 아래 (하락)"}] if not np.isnan(rsi) else [],
        "gauge": {"kind": "rsi", "value": round(rsi, 1), "min_label": "0", "max_label": "100", "marks": [30, 70]} if not np.isnan(rsi) else None,
        "items": [
            {"label": f"현재 RSI: {rsi:.1f}", "up": rsi >= 50 if not np.isnan(rsi) else None},
            {"label": "과매수 기준: 70", "up": None},
            {"label": "과매도 기준: 30", "up": None},
        ]
    })

    # ── MACD ─────────────────────────────────────────
    macd      = float(latest.get("MACD",        float("nan")))
    macd_sig  = float(latest.get("MACD_signal", float("nan")))
    macd_hist = float(latest.get("MACD_hist",   float("nan")))
    prev_macd = float(prev.get("MACD",          float("nan")))
    prev_sig  = float(prev.get("MACD_signal",   float("nan")))
    prev_hist = float(prev.get("MACD_hist",     float("nan")))
    # 히스토그램 확대/축소: 직전 봉 대비 크기(절댓값) 비교. 방향은 부호로
    hist_expanding = None
    if not np.isnan(macd_hist) and not np.isnan(prev_hist):
        hist_expanding = abs(macd_hist) > abs(prev_hist)
    hist_trend = "확대" if hist_expanding else "축소" if hist_expanding is False else "—"

    if not any(np.isnan(v) for v in [macd, macd_sig, prev_macd, prev_sig]):
        golden = prev_macd < prev_sig and macd > macd_sig
        dead   = prev_macd > prev_sig and macd < macd_sig
        if golden:
            signals.append({"type": "bullish", "text": "MACD 골든크로스 — 매수 신호"})
            score += 15
            macd_state, macd_color = "골든크로스 (매수)", "bullish"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f})을 아래에서 위로 돌파하는 "
                f"골든크로스가 발생했습니다. 단기 상승 전환 신호로 해석되며 "
                f"매수 타이밍으로 주목받습니다. 히스토그램이 0선 위로 전환 중입니다."
            )
        elif dead:
            signals.append({"type": "bearish", "text": "MACD 데드크로스 — 매도 신호"})
            score -= 15
            macd_state, macd_color = "데드크로스 (매도)", "bearish"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f})을 위에서 아래로 돌파하는 "
                f"데드크로스가 발생했습니다. 단기 하락 전환 신호로 해석되며 "
                f"매도 또는 관망이 권장됩니다."
            )
        elif macd > macd_sig:
            signals.append({"type": "bullish", "text": "MACD > 시그널선 — 상승 모멘텀 유지"})
            score += 5
            macd_state, macd_color = "MACD > 시그널 (상승)", "bullish"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f}) 위에 있어 상승 모멘텀이 유지되고 있습니다. "
                f"히스토그램({macd_hist:.4f})이 직전 봉보다 {hist_trend}되어 "
                f"{'상승 모멘텀이 강해지고 있습니다.' if hist_expanding else '상승 모멘텀이 약해지고 있어 주의가 필요합니다.'}"
            )
        else:
            signals.append({"type": "bearish", "text": "MACD < 시그널선 — 하락 모멘텀"})
            score -= 5
            macd_state, macd_color = "MACD < 시그널 (하락)", "bearish"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f}) 아래에 있어 하락 모멘텀이 우세합니다. "
                f"히스토그램({macd_hist:.4f})이 직전 봉보다 {hist_trend}되어 "
                f"{'하락 압력이 커지고 있습니다.' if hist_expanding else '하락 압력이 줄고 있어 반등 신호를 지켜볼 만합니다.'}"
            )
    else:
        macd_state, macd_color = "데이터 부족", "neutral"
        macd_desc = "MACD 데이터가 충분하지 않습니다."
        macd, macd_sig, macd_hist = float("nan"), float("nan"), float("nan")

    details.append({
        "indicator": "MACD", "short": "MACD",
        "state": macd_state, "color": macd_color, "desc": macd_desc,
        "metrics": ([{"label": "히스토그램", "value": f"{_fp(macd_hist)} ({hist_trend})"},
                     {"label": "MACD / 시그널", "value": f"{_fp(macd)} / {_fp(macd_sig)}"}] if not np.isnan(macd) else []),
        "gauge": None,
        "hist_expanding": hist_expanding,
        "items": [
            {"label": f"MACD: {_fp(macd)}"       if not np.isnan(macd)      else "MACD: —",     "up": macd > macd_sig if not np.isnan(macd) else None},
            {"label": f"시그널: {_fp(macd_sig)}"  if not np.isnan(macd_sig)  else "시그널: —",   "up": macd > macd_sig if not np.isnan(macd) else None},
            {"label": f"히스토: {_fp(macd_hist)}" if not np.isnan(macd_hist) else "히스토그램: —","up": macd_hist > 0   if not np.isnan(macd_hist) else None},
        ]
    })

    # ── 수급 · ADX (추세 강도 + OBV 자금 흐름) ─────────────────
    obv_v     = _f(latest.get("OBV"))
    obv_ma    = _f(latest.get("OBV_MA20"))
    obv_flow  = None                          # "in" 유입 / "out" 이탈 / None
    if obv_v is not None and obv_ma is not None and len(df) >= 25:
        obv_5ago = _f(df["OBV"].iloc[-6]) if "OBV" in df.columns else None
        rising = obv_5ago is not None and obv_v > obv_5ago
        if obv_v > obv_ma and rising:
            obv_flow = "in"
        elif obv_v < obv_ma and not rising:
            obv_flow = "out"
    adx_regime = None
    if adx_v is not None:
        adx_regime = "trend" if adx_v >= 25 else "range" if adx_v < 20 else "weak"
    flow_delta = 0
    if adx_regime == "trend" and pdi_v is not None and mdi_v is not None:
        flow_delta += 5 if pdi_v > mdi_v else -5
    if obv_flow == "in":
        flow_delta += 5
    elif obv_flow == "out":
        flow_delta -= 5
    score += flow_delta
    if adx_v is None and obv_flow is None:
        flow_state, flow_color = "데이터 부족", "neutral"
        flow_desc = "ADX·OBV 를 계산할 데이터가 충분하지 않습니다."
    else:
        dir_txt = ("상승" if (pdi_v or 0) > (mdi_v or 0) else "하락")
        regime_txt = {"trend": f"추세장 ({dir_txt} 우위)", "range": "횡보장", "weak": "추세 약함"}.get(adx_regime, "—")
        flow_txt = {"in": "자금 유입", "out": "자금 이탈"}.get(obv_flow, "자금 흐름 중립")
        flow_color = "bullish" if flow_delta > 0 else "bearish" if flow_delta < 0 else "neutral"
        flow_state = f"{regime_txt} · {flow_txt}"
        flow_desc = (
            (f"ADX(14) {adx_v:.1f}: " + ("25 이상이라 방향성이 뚜렷한 추세장입니다. " if adx_regime == "trend"
                                         else "20 미만이라 방향 없는 횡보장입니다. 추세 지표보다 박스권 매매가 잘 맞습니다. " if adx_regime == "range"
                                         else "20~25 로 추세가 막 생기거나 약해지는 구간입니다. ")
             + (f"+DI {pdi_v:.1f} / -DI {mdi_v:.1f} 로 {dir_txt} 쪽 힘이 더 큽니다. " if pdi_v is not None and mdi_v is not None else "")
             if adx_v is not None else "")
            + ({"in": "OBV 가 20일 평균 위에서 올라가고 있어 거래량이 오르는 날에 더 실리는 '자금 유입' 상태입니다.",
                "out": "OBV 가 20일 평균 아래에서 내려가고 있어 거래량이 내리는 날에 더 실리는 '자금 이탈' 상태입니다."}.get(obv_flow,
               "OBV 는 20일 평균 근처로 뚜렷한 자금 흐름이 없습니다."))
        )
        if adx_regime == "trend" or obv_flow:
            signals.append({"type": flow_color if flow_color != "neutral" else "info", "text": f"수급·ADX: {flow_state}"})
    details.append({
        "indicator": "수급 · ADX", "short": "수급·ADX",
        "state": flow_state, "color": flow_color, "desc": flow_desc,
        "tip_key": {"trend": "trend", "range": "range"}.get(adx_regime, "neutral"),
        "metrics": [m for m in [
            {"label": "ADX(14)", "value": f"{adx_v:.1f}"} if adx_v is not None else None,
            {"label": "OBV(20일)", "value": {"in": "유입 ↑", "out": "이탈 ↓"}.get(obv_flow, "중립")} if obv_v is not None else None,
        ] if m],
        "gauge": {"kind": "adx", "value": round(min(adx_v, 60) / 60 * 100, 1), "raw": round(adx_v, 1), "min_label": "횡보",
                  "max_label": "강한 추세", "marks": [round(20 / 60 * 100, 1), round(25 / 60 * 100, 1)]} if adx_v is not None else None,
        "items": [
            {"label": f"ADX {adx_v:.1f}" if adx_v is not None else "ADX —", "up": None},
            {"label": f"+DI {pdi_v:.1f}" if pdi_v is not None else "+DI —", "up": (pdi_v > mdi_v) if pdi_v is not None and mdi_v is not None else None},
            {"label": f"-DI {mdi_v:.1f}" if mdi_v is not None else "-DI —", "up": None},
            {"label": {"in": "OBV 유입", "out": "OBV 이탈"}.get(obv_flow, "OBV 중립"), "up": {"in": True, "out": False}.get(obv_flow)},
        ],
    })

    # ── 펀더멘털 분석 추가 ────────────────────────────
    fund = _analyze_fundamental(df, info)
    fund_details = fund["details"]
    fund_score   = fund["score"]

    # ── 월가 스타일 고급 펀더멘탈 (10개 지표 + 4팩터 등급) ──
    scorecard = None
    if stock is not None:
        try:
            from analysis.advanced import compute_advanced_metrics
            scorecard = compute_advanced_metrics(stock, info, df)
        except Exception as _e:
            scorecard = None

    # ── 종합 판단 (기술 + 펀더멘털 통합) ──────────────
    combined_score = max(-100, min(100, score + fund_score))

    # ── 1개월 기반 박스권 정보 (참고용 구간) ────────────
    # 정식 True Range(전일 종가 갭 포함)의 Wilder 평균. 지표 컬럼이 있으면 그대로, 없으면 여기서 계산
    if "ATR" in df.columns:
        atr_val = _f(df["ATR"].iloc[-1])
    else:
        from analysis.indicators import atr_series as _atr_series
        atr_val = _f(_atr_series(df).iloc[-1])
    atr_use     = atr_val if (atr_val and atr_val > 0) else close * 0.02

    month       = df.tail(21)                          # 최근 21거래일 ≈ 1개월
    month_high  = float(month["High"].max())
    month_low   = float(month["Low"].min())
    month_range = month_high - month_low if month_high > month_low else close * 0.1

    # 매수/매도 추천 박스 (존 차트용 — 참고만)
    entry_low   = round(month_low, 2)
    entry_high  = round(month_low + month_range * 0.30, 2)
    target_low  = round(month_low + month_range * 0.70, 2)
    target_high = round(month_high, 2)

    # ── Plan D: 추세 강도 점수 (0~100) ──────────────────
    _ma20 = _f(df["Close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
    _ma50 = _f(df["Close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    _ma200= _f(df["Close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    _year_high = float(df["High"].tail(252).max()) if len(df) > 0 else close
    _vol_avg20 = float(df["Volume"].tail(20).mean()) if len(df) > 0 else 0
    _vol_last  = float(df["Volume"].iloc[-1])      if len(df) > 0 else 0
    _swing_low_5 = float(df["Low"].tail(5).min())  if len(df) >= 5 else close * 0.95

    trend_score = 0
    if _ma20 and _ma50 and _ma200 and _ma20 > _ma50 > _ma200:
        trend_score += 30                                 # 정배열
    if _ma20 and close > _ma20:
        trend_score += 20                                 # 단기선 위
    if not np.isnan(rsi):
        if 50 <= rsi <= 70:
            trend_score += 20                             # 강세 모멘텀
        elif 40 <= rsi < 50:
            trend_score += 10                             # 약한 강세
    if _year_high > 0 and close >= _year_high * 0.95:
        trend_score += 10                                 # 52주 신고가 5% 이내
    _open_last = _f(df["Open"].iloc[-1]) if "Open" in df.columns and len(df) > 0 else None
    _prev_close = _f(df["Close"].iloc[-2]) if len(df) >= 2 else None
    _up_candle = (_open_last is not None and close > _open_last) or (_open_last is None and _prev_close is not None and close > _prev_close)
    vol_spike_up = vol_spike_down = False
    if _vol_avg20 > 0 and _vol_last > _vol_avg20 * 1.5:
        if _up_candle:
            trend_score += 20                             # 양봉 + 거래량 급증 = 매수세
            vol_spike_up = True
        else:
            vol_spike_down = True                         # 음봉 거래량 급증은 가점 없음 (매도세 신호로만 표시)

    # ── 추세 강도별 진입·손절·목표 (월가 정통 로직) ────
    is_breakout    = _year_high > 0 and close >= _year_high * 0.995
    rsi_overheated = (not np.isnan(rsi)) and rsi > 70

    if trend_score >= 60:
        # 🔥 강한 상승추세
        # ── 1) 손절 먼저 결정 ──
        base_stop = max(_swing_low_5, (_ma20 * 0.99) if _ma20 else (close * 0.95))
        stop_loss = round(base_stop - atr_use * 0.3, 2)

        # ── 2) 진입가: 상황별 ──
        near_ma20 = _ma20 is not None and (close - _ma20) < atr_use
        if is_breakout:
            entry_raw = close                                      # 돌파 매수
        elif near_ma20:
            entry_raw = close                                      # 이미 좋은 자리
        elif rsi_overheated and _ma20:
            # 과열: MA20+0.5ATR 또는 close-1ATR 중 더 높은 쪽 (덜 깊게)
            entry_raw = max(_ma20 + atr_use * 0.5, close - atr_use * 1.0)
        elif rsi_overheated:
            entry_raw = close - atr_use * 1.0
        else:
            # 일반 강세: close-0.5ATR 또는 MA20 중 높은 쪽 (MA20을 floor로)
            entry_raw = max(close - atr_use * 0.5, _ma20 or 0)

        # ── 3) 안전장치 ──
        entry_raw = max(entry_raw, stop_loss + atr_use * 1.0)      # R:R 의미 확보
        entry_raw = min(entry_raw, close)                          # limit buy
        entry_price = round(entry_raw, 2)

        # ── 4) 목표: R:R 1:3 ──
        risk = entry_price - stop_loss
        target_price = round(entry_price + risk * 3, 2) if risk > 0 \
                       else round(close + atr_use * 4, 2)
        trade_recommendation = "strong"

    elif trend_score >= 30:
        # ⚖️ 약한 추세/횡보: MA50 또는 1.5 ATR 풀백 중 더 가까운 쪽
        fallback = close - atr_use * 1.5
        if _ma50 and close > _ma50:
            entry_raw = max(_ma50, fallback)
        else:
            entry_raw = fallback
        entry_raw = min(entry_raw, close)
        entry_price = round(entry_raw, 2)
        stop_loss   = round(entry_price - atr_use * 2.0, 2)
        target_price = round(entry_price + atr_use * 4.0, 2)
        trade_recommendation = "neutral"

    else:
        # ⚠️ 약세/하락: 진입 비추천
        entry_price  = None
        target_price = None
        stop_loss    = round(max(_swing_low_5, close * 0.92) - atr_use * 0.5, 2)
        trade_recommendation = "avoid"

    # ── 진입 신뢰도 (close와의 거리 기반) ──
    entry_confidence = None
    if entry_price is not None and close > 0:
        diff_pct = (close - entry_price) / close * 100
        if diff_pct < 0.5:
            entry_confidence = "immediate"     # 🟢 즉시 진입 가능
        elif diff_pct < 2.0:
            entry_confidence = "wait"          # 🟡 풀백 대기 (limit)
        else:
            entry_confidence = "patient"       # 🟠 깊은 풀백 대기

    # 안전장치: R:R 비율 + 변동성 경고
    risk_reward_ratio  = None
    stop_distance_pct  = None
    if entry_price and stop_loss and target_price and entry_price > stop_loss:
        rr = (target_price - entry_price) / (entry_price - stop_loss)
        risk_reward_ratio = round(rr, 2)
        if rr < 1.5:
            trade_recommendation = "avoid"   # 손익비 부족
        stop_distance_pct = round((entry_price - stop_loss) / entry_price * 100, 1)
        if stop_distance_pct > 15:
            # 변동성 너무 큰 종목 - 그래도 표시는 함 (사용자 판단)
            pass

    # 추세에 맞게 매수/매도 박스(존 차트) 재계산
    if trade_recommendation == "strong" and entry_price and target_price:
        entry_low   = round(entry_price - atr_use, 2)
        entry_high  = round(entry_price, 2)
        target_low  = round(target_price * 0.97, 2)
        target_high = round(target_price, 2)
    elif trade_recommendation == "neutral" and entry_price and target_price:
        entry_low   = round(entry_price - atr_use * 0.5, 2)
        entry_high  = round(entry_price + atr_use * 0.5, 2)
        target_low  = round(target_price - atr_use, 2)
        target_high = round(target_price, 2)
    # avoid 시: 기존 박스권(1개월) 유지 (위에서 이미 계산됨)

    # ── 추세 판단 (MA + 종합점수 + 52주 고/저가 + 크로스 + BB + 거래량) ──────────
    ma20 = _f(df["Close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
    ma50 = _f(df["Close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None

    # ① 52주 고가/저가 기준 (거래일 252일)
    year_high_252 = float(df["High"].tail(252).max())
    year_low_252  = float(df["Low"].tail(252).min())
    new_52w_high  = year_high_252 > 0 and close >= year_high_252 * 0.99   # 실질 신고가
    near_52w_high = year_high_252 > 0 and close >= year_high_252 * 0.97   # 신고가 3% 이내
    near_52w_low  = year_low_252  > 0 and close <= year_low_252  * 1.05   # 신저가 5% 이내

    # ── 주봉 기반 추세 보조 지표 (df_weekly 없으면 스킵) ─────────────────
    # ② 최근 1개월(주봉 5캔들) MA5/MA20 골든·데드크로스
    recent_golden = False
    recent_dead   = False
    dfw = df_weekly
    if dfw is not None and not dfw.empty and \
       "MA5" in dfw.columns and "MA20" in dfw.columns and len(dfw) >= 7:
        ma5_arr  = dfw["MA5"].values
        ma20_arr = dfw["MA20"].values
        # 최근 5주(≈1개월) 내 크로스 탐지 — 최신 주봉(-1)까지 포함
        for i in range(-5, 0):
            p5,  c5  = _f(ma5_arr[i-1]),  _f(ma5_arr[i])
            p20, c20 = _f(ma20_arr[i-1]), _f(ma20_arr[i])
            if None not in (p5, c5, p20, c20):
                if p5 <= p20 and c5 > c20:
                    recent_golden = True
                elif p5 >= p20 and c5 < c20:
                    recent_dead = True

    # ③ 볼린저밴드 폭 확장/수축 (주봉 현재 vs 4주 전)
    bb_expanding   = False
    bb_contracting = False
    bb_dir_bullish = False
    bb_dir_bearish = False
    if dfw is not None and not dfw.empty and \
       all(c in dfw.columns for c in ["BB_upper","BB_lower","BB_mid"]) and len(dfw) >= 6:
        def _bbw(src, idx):
            u = _f(src["BB_upper"].iloc[idx])
            l = _f(src["BB_lower"].iloc[idx])
            m = _f(src["BB_mid"].iloc[idx])
            return (u - l) / m if u and l and m and m > 0 else None
        w_now, w_4 = _bbw(dfw, -1), _bbw(dfw, -5)
        if w_now and w_4:
            ratio = w_now / w_4
            if ratio >= 1.10:
                bb_expanding = True
                bb_mid_now = _f(dfw["BB_mid"].iloc[-1])
                if bb_mid_now:
                    bb_dir_bullish = close > bb_mid_now
                    bb_dir_bearish = close < bb_mid_now
            elif ratio <= 0.90:
                bb_contracting = True

    # ④ 거래량 동반 상승/하락 (주봉 최근 2주 거래량 vs 8주 평균)
    vol_up_confirm   = False
    vol_down_confirm = False
    if dfw is not None and not dfw.empty and len(dfw) >= 10:
        vol_2w  = float(dfw["Volume"].tail(2).mean())
        vol_8w  = float(dfw["Volume"].tail(8).mean())
        p2w_ago = _f(dfw["Close"].iloc[-3])
        close_w = _f(dfw["Close"].iloc[-1])
        if vol_8w > 0 and p2w_ago and p2w_ago > 0 and close_w:
            vol_ratio_w  = vol_2w / vol_8w
            price_2w_ch  = (close_w - p2w_ago) / p2w_ago * 100
            if vol_ratio_w >= 1.30 and price_2w_ch >= 2.0:
                vol_up_confirm = True
            elif vol_ratio_w >= 1.30 and price_2w_ch <= -2.0:
                vol_down_confirm = True

    # ── 추가 트렌드 신호 종합 → effective_score 계산 ──────────────────────
    trend_boost = 0
    if recent_golden:   trend_boost += 15
    if recent_dead:     trend_boost -= 15
    if bb_dir_bullish:  trend_boost += 10
    if bb_dir_bearish:  trend_boost -= 10
    if vol_up_confirm:  trend_boost += 10
    if vol_down_confirm:trend_boost -= 10

    effective_score = max(-100, min(100, combined_score + trend_boost))

    if ma20 and ma50:
        # strong-uptrend
        if (effective_score >= 30 and close > ma20 and ma20 > ma50) or \
           (new_52w_high and effective_score >= 15 and close > ma20):
            trend = "strong-uptrend"
        # uptrend
        elif (effective_score >= 10 and close > ma20) or \
             (near_52w_high and effective_score >= 5 and close > ma20):
            trend = "uptrend"
        # strong-downtrend
        elif (effective_score <= -30 and close < ma20 and ma20 < ma50) or \
             (near_52w_low and effective_score <= -15 and close < ma20):
            trend = "strong-downtrend"
        # downtrend
        elif (effective_score <= -10 and close < ma20) or \
             (near_52w_low and effective_score <= -5 and close < ma20):
            trend = "downtrend"
        else:
            trend = "sideways"
    else:
        trend = "uptrend" if effective_score >= 20 else "downtrend" if effective_score <= -20 else "sideways"

    if combined_score >= 50:   verdict, verdict_color = "강한 매수", "strong-buy"
    elif combined_score >= 20: verdict, verdict_color = "매수",       "buy"
    elif combined_score >= -20:verdict, verdict_color = "중립 / 관망","neutral"
    elif combined_score >= -50:verdict, verdict_color = "매도",        "sell"
    else:                      verdict, verdict_color = "강한 매도",   "strong-sell"

    return {
        "score": combined_score,
        "verdict": verdict,
        "verdict_color": verdict_color,
        "signals": signals,
        "details": details,
        "fundamental_details": fund_details,
        "scorecard": scorecard,  # 월가 스타일 4팩터 + 10개 지표
        "entry_price": entry_price,
        "entry_low":   entry_low,
        "entry_high":  entry_high,
        "target_price": target_price,
        "target_low":  target_low,
        "target_high": target_high,
        "stop_loss": stop_loss,
        "trade_recommendation": trade_recommendation,  # strong / neutral / avoid
        "trend_score":         trend_score,             # 0~100
        "risk_reward_ratio":   risk_reward_ratio,       # 1.5 미만이면 avoid
        "stop_distance_pct":   stop_distance_pct,       # 손절 거리 % (변동성 판단용)
        "entry_confidence":    entry_confidence,        # immediate/wait/patient
        "month_high": round(month_high, 2),
        "month_low":  round(month_low, 2),
        "trend": trend,
        "trend_boost":     trend_boost,
        "new_52w_high":    new_52w_high,
        "near_52w_high":   near_52w_high,
        "near_52w_low":    near_52w_low,
        "recent_golden":   recent_golden,
        "recent_dead":     recent_dead,
        "bb_expanding":    bb_expanding,
        "bb_contracting":  bb_contracting,
        "vol_up_confirm":  vol_up_confirm,
        "vol_down_confirm":vol_down_confirm,
        "rsi":         round(rsi, 2)          if not np.isnan(rsi)  else None,
        "macd":        round(macd, 4)          if not np.isnan(macd) else None,
        "bb_position": round(bb_position, 1)   if bb_position is not None else None,
        "ma_analysis": ma_analysis,
        # 추가 (PR #71): 추세 강도 · 수급 · 과열 경고 · 정식 ATR
        "adx":           round(adx_v, 1) if adx_v is not None else None,
        "plus_di":       round(pdi_v, 1) if pdi_v is not None else None,
        "minus_di":      round(mdi_v, 1) if mdi_v is not None else None,
        "adx_regime":    adx_regime,          # trend(≥25) / weak / range(<20)
        "obv_flow":      obv_flow,            # in / out / None
        "overheat":      bool(overheat_reasons),
        "overheat_reasons": overheat_reasons,
        "strong_up":     bool(strong_up),
        "atr":           round(atr_use, 4),
        "vol_spike_up":  vol_spike_up,
        "vol_spike_down": vol_spike_down,
        "macd_hist_expanding": hist_expanding,
    }
