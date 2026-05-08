import numpy as np


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

    pos_items.append({"label": f"52주 고가: {year_high:,.2f}", "up": None})
    pos_items.append({"label": f"52주 저가: {year_low:,.2f}", "up": None})

    score += pos_score
    if pos_score >= 10:
        pos_state, pos_color = "상승 추세 (강세)", "bullish"
    elif pos_score >= 0:
        pos_state, pos_color = "중립 (방향 탐색)", "neutral"
    else:
        pos_state, pos_color = "하락 추세 (약세)", "bearish"

    pos_desc = ". ".join(pos_parts)
    pos_desc += f". 52주 고가({year_high:,.2f})까지 {_pct(year_high, close):+.1f}%, 저가({year_low:,.2f}) 대비 {_pct(close, year_low):+.1f}% 위치합니다."

    details.append({
        "indicator": "시장 위치 & 모멘텀",
        "state": pos_state, "color": pos_color,
        "desc": pos_desc, "items": pos_items,
    })

    # ── 재무 건전성 ──────────────────────────────────────
    roe = _f(info.get("returnOnEquity"))
    de  = _f(info.get("debtToEquity"))
    cr  = _f(info.get("currentRatio"))
    div = _f(info.get("dividendYield"))
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


def analyze_signals(df, info):
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
                f"MA5({ma5:,.2f}) > MA20({ma20:,.2f}) > MA60({ma60:,.2f}) 순서로 "
                f"정배열 상태입니다. 단기·중기·장기 이동평균이 모두 우상향하고 있어 "
                f"추세적 상승 흐름이 유지되고 있습니다. "
                f"현재가는 MA5 대비 {_pct(close, ma5):+.1f}%, MA20 대비 {_pct(close, ma20):+.1f}% 위치합니다."
            )
        elif ma5 < ma20 < ma60:
            signals.append({"type": "bearish", "text": "이동평균선 역배열 — 하락 추세"})
            score -= 15
            ma_state, ma_color = "역배열 (하락 추세)", "bearish"
            ma_desc = (
                f"MA5({ma5:,.2f}) < MA20({ma20:,.2f}) < MA60({ma60:,.2f}) 순서로 "
                f"역배열 상태입니다. 단기 이동평균이 장기 이동평균 아래에 있어 "
                f"하락 추세가 지속되고 있습니다. "
                f"현재가는 MA20 대비 {_pct(close, ma20):+.1f}% 위치합니다."
            )
        else:
            ma_state, ma_color = "혼조 (방향 불명확)", "neutral"
            ma_desc = (
                f"이동평균선이 혼조 상태입니다. "
                f"MA5({ma5:,.2f}), MA20({ma20:,.2f}), MA60({ma60:,.2f})이 "
                f"완전한 정·역배열을 이루지 않고 있어 방향성이 불분명합니다. "
                f"추세 확인 후 진입을 권장합니다."
            )
    else:
        ma_state, ma_color, ma_desc = "데이터 부족", "neutral", "이동평균선 데이터가 충분하지 않습니다."

    details.append({
        "indicator": "이동평균선 (MA)",
        "state": ma_state, "color": ma_color, "desc": ma_desc,
        "items": [
            {"label": f"MA5  {ma5:,.2f}"   if ma5   else "MA5 —",   "up": ma_analysis.get("MA5",  {}).get("above")},
            {"label": f"MA20 {ma20:,.2f}"  if ma20  else "MA20 —",  "up": ma_analysis.get("MA20", {}).get("above")},
            {"label": f"MA60 {ma60:,.2f}"  if ma60  else "MA60 —",  "up": ma_analysis.get("MA60", {}).get("above")},
            {"label": f"MA120 {ma120:,.2f}" if ma120 else "MA120 —", "up": ma_analysis.get("MA120",{}).get("above")},
        ]
    })

    # ── 볼린저밴드 ────────────────────────────────────
    bb_upper = float(latest.get("BB_upper", float("nan")))
    bb_lower = float(latest.get("BB_lower", float("nan")))
    bb_mid   = float(latest.get("BB_mid",   float("nan")))

    if not any(np.isnan(v) for v in [bb_upper, bb_lower, bb_mid]):
        bb_position  = (close - bb_lower) / (bb_upper - bb_lower) * 100
        bb_width_pct = round((bb_upper - bb_lower) / bb_mid * 100, 1)

        if close >= bb_upper:
            signals.append({"type": "bearish", "text": "볼린저밴드 상단 돌파 — 과매수 주의"})
            score -= 10
            bb_state, bb_color = "상단 돌파 (과매수)", "bearish"
            bb_desc = (
                f"현재가({close:,.2f})가 볼린저밴드 상단({bb_upper:,.2f})을 돌파했습니다. "
                f"통계적으로 상단 이탈은 전체의 약 5%로 단기 과매수 가능성이 높습니다. "
                f"단기 조정 또는 상단 저항에 주의가 필요합니다. 밴드 폭(변동성) {bb_width_pct}%"
            )
        elif close <= bb_lower:
            signals.append({"type": "bullish", "text": "볼린저밴드 하단 이탈 — 반등 가능성"})
            score += 10
            bb_state, bb_color = "하단 이탈 (과매도)", "bullish"
            bb_desc = (
                f"현재가({close:,.2f})가 볼린저밴드 하단({bb_lower:,.2f})을 이탈했습니다. "
                f"과매도 구간으로 기술적 반등 가능성이 있습니다. "
                f"단, 강한 하락 추세에서는 하단 이탈이 지속될 수 있으므로 추세 지표와 함께 확인이 필요합니다. "
                f"밴드 폭 {bb_width_pct}%"
            )
        elif close > bb_mid:
            signals.append({"type": "bullish", "text": "볼린저밴드 중심선 위 — 상승 모멘텀"})
            score += 5
            bb_state, bb_color = "중심선 위 (상승 우위)", "bullish"
            bb_desc = (
                f"현재가({close:,.2f})가 볼린저밴드 중심선({bb_mid:,.2f}) 위에 위치합니다. "
                f"밴드 내 상위 {bb_position:.1f}% 구간으로 상승 모멘텀이 우세합니다. "
                f"상단({bb_upper:,.2f})까지 {_pct(bb_upper, close):+.1f}% 여유가 있습니다. "
                f"밴드 폭(변동성) {bb_width_pct}%"
            )
        else:
            bb_state, bb_color = "중심선 아래 (하락 우위)", "bearish"
            bb_desc = (
                f"현재가({close:,.2f})가 볼린저밴드 중심선({bb_mid:,.2f}) 아래에 위치합니다. "
                f"밴드 내 하위 {bb_position:.1f}% 구간으로 하락 압력이 우세합니다. "
                f"하단({bb_lower:,.2f})까지 {_pct(bb_lower, close):+.1f}%입니다. "
                f"밴드 폭(변동성) {bb_width_pct}%"
            )
    else:
        bb_position  = None
        bb_width_pct = 0
        bb_state, bb_color = "데이터 부족", "neutral"
        bb_desc = "볼린저밴드 데이터가 충분하지 않습니다."

    details.append({
        "indicator": "볼린저밴드 (BB)",
        "state": bb_state, "color": bb_color, "desc": bb_desc,
        "items": [
            {"label": f"상단  {bb_upper:,.2f}" if not np.isnan(bb_upper) else "상단 —", "up": None},
            {"label": f"중심  {bb_mid:,.2f}"   if not np.isnan(bb_mid)   else "중심 —", "up": None},
            {"label": f"하단  {bb_lower:,.2f}" if not np.isnan(bb_lower) else "하단 —", "up": None},
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
            ichi_parts.append(f"전환선({tenkan:,.2f}) > 기준선({kijun:,.2f}) → 단기 매수 우위")
            ichi_score_delta += 10
        else:
            ichi_parts.append(f"전환선({tenkan:,.2f}) < 기준선({kijun:,.2f}) → 단기 매도 우위")
            ichi_score_delta -= 10
        ichi_items += [
            {"label": f"전환선 {tenkan:,.2f}", "up": tenkan > kijun},
            {"label": f"기준선 {kijun:,.2f}",  "up": tenkan > kijun},
        ]

    cloud_top = cloud_bot = None
    if not any(np.isnan(v) for v in [senkou_a, senkou_b]):
        cloud_top = max(senkou_a, senkou_b)
        cloud_bot = min(senkou_a, senkou_b)
        if close > cloud_top:
            ichi_parts.append(f"구름대({cloud_bot:,.2f}~{cloud_top:,.2f}) 위 → 강한 상승 신호")
            ichi_score_delta += 15
            ichi_state, ichi_color = "구름대 위 (강세)", "bullish"
        elif close < cloud_bot:
            ichi_parts.append(f"구름대({cloud_bot:,.2f}~{cloud_top:,.2f}) 아래 → 강한 하락 신호")
            ichi_score_delta -= 15
            ichi_state, ichi_color = "구름대 아래 (약세)", "bearish"
        else:
            ichi_parts.append(f"구름대({cloud_bot:,.2f}~{cloud_top:,.2f}) 내부 → 방향 탐색 중")
            ichi_state, ichi_color = "구름대 내부 (중립)", "neutral"
        ichi_items.append({"label": f"구름상단 {cloud_top:,.2f}", "up": close > cloud_top})
        ichi_items.append({"label": f"구름하단 {cloud_bot:,.2f}", "up": close > cloud_bot})
    else:
        ichi_state, ichi_color = "데이터 부족", "neutral"

    score += ichi_score_delta
    ichi_desc = " ".join(ichi_parts) if ichi_parts else "일목균형표 데이터가 충분하지 않습니다."
    if ichi_parts:
        signals.append({"type": ichi_color if ichi_color != "neutral" else "info",
                        "text": f"일목균형표: {' / '.join([p.split('→')[1].strip() for p in ichi_parts])}"})
    details.append({
        "indicator": "일목균형표",
        "state": ichi_state, "color": ichi_color,
        "desc": ichi_desc, "items": ichi_items,
    })

    # ── RSI ──────────────────────────────────────────
    rsi = float(latest.get("RSI", float("nan")))
    if not np.isnan(rsi):
        if rsi >= 70:
            signals.append({"type": "bearish", "text": f"RSI {rsi:.1f} — 과매수 구간"})
            score -= 10
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
            rsi_desc = (
                f"RSI가 {rsi:.1f}로 과매도 기준선(30) 아래입니다. "
                f"단기적으로 가격이 과도하게 하락했을 가능성이 있으며, 기술적 반등 기대감이 높습니다. "
                f"하지만 강한 하락 추세에서는 과매도 상태가 지속될 수 있어 추세 확인이 필요합니다."
            )
        else:
            signals.append({"type": "info", "text": f"RSI {rsi:.1f} — 중립 구간"})
            rsi_state, rsi_color = f"{rsi:.1f} — 중립", "neutral"
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
        "indicator": "RSI (14)",
        "state": rsi_state, "color": rsi_color, "desc": rsi_desc,
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
            hist_trend = "확대" if not np.isnan(macd_hist) and macd_hist > 0 else "축소"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f}) 위에 있어 상승 모멘텀이 유지되고 있습니다. "
                f"히스토그램({macd_hist:.4f})이 {hist_trend} 중으로 "
                f"{'모멘텀이 강화되고 있습니다.' if hist_trend == '확대' else '모멘텀이 약화되고 있어 주의가 필요합니다.'}"
            )
        else:
            signals.append({"type": "bearish", "text": "MACD < 시그널선 — 하락 모멘텀"})
            score -= 5
            macd_state, macd_color = "MACD < 시그널 (하락)", "bearish"
            macd_desc = (
                f"MACD({macd:.4f})가 시그널선({macd_sig:.4f}) 아래에 있어 하락 모멘텀이 우세합니다. "
                f"히스토그램({macd_hist:.4f})이 음수 구간에 있으며 "
                f"반등 시그널이 나타날 때까지 관망을 권장합니다."
            )
    else:
        macd_state, macd_color = "데이터 부족", "neutral"
        macd_desc = "MACD 데이터가 충분하지 않습니다."
        macd, macd_sig, macd_hist = float("nan"), float("nan"), float("nan")

    details.append({
        "indicator": "MACD",
        "state": macd_state, "color": macd_color, "desc": macd_desc,
        "items": [
            {"label": f"MACD: {macd:.3f}"       if not np.isnan(macd)      else "MACD: —",     "up": macd > macd_sig if not np.isnan(macd) else None},
            {"label": f"시그널: {macd_sig:.3f}"  if not np.isnan(macd_sig)  else "시그널: —",   "up": macd > macd_sig if not np.isnan(macd) else None},
            {"label": f"히스토: {macd_hist:.3f}" if not np.isnan(macd_hist) else "히스토그램: —","up": macd_hist > 0   if not np.isnan(macd_hist) else None},
        ]
    })

    # ── 펀더멘털 분석 추가 ────────────────────────────
    fund = _analyze_fundamental(df, info)
    fund_details = fund["details"]
    fund_score   = fund["score"]

    # ── 종합 판단 (기술 + 펀더멘털 통합) ──────────────
    combined_score = max(-100, min(100, score + fund_score))

    # ── 1개월 기반 구간 계산 ────────────────────────────
    atr_series  = (df["High"] - df["Low"]).rolling(14).mean()
    atr_val     = _f(atr_series.iloc[-1])

    month       = df.tail(21)                          # 최근 21거래일 ≈ 1개월
    month_high  = float(month["High"].max())
    month_low   = float(month["Low"].min())
    month_range = month_high - month_low if month_high > month_low else close * 0.1

    # 손절: 1개월 최저가 - ATR (지지선 완전 이탈 기준)
    stop_loss   = round(month_low - (atr_val or month_range * 0.1), 2)

    # 매수 추천 구간: 1개월 범위 하위 30%
    entry_low   = round(month_low, 2)
    entry_high  = round(month_low + month_range * 0.30, 2)
    entry_price = round((entry_low + entry_high) / 2, 2)

    # 매도 추천 구간: 1개월 범위 상위 30%
    target_low  = round(month_low + month_range * 0.70, 2)
    target_high = round(month_high, 2)
    target_price = target_low   # 기존 호환용 (매도 시작점)

    # ── 추세 판단 (MA + 종합점수) ────────────────────────
    ma20 = _f(df["Close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else None
    ma50 = _f(df["Close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None

    if ma20 and ma50:
        if combined_score >= 30 and close > ma20 and ma20 > ma50:
            trend = "strong-uptrend"
        elif combined_score >= 10 and close > ma20:
            trend = "uptrend"
        elif combined_score <= -30 and close < ma20 and ma20 < ma50:
            trend = "strong-downtrend"
        elif combined_score <= -10 and close < ma20:
            trend = "downtrend"
        else:
            trend = "sideways"
    else:
        trend = "uptrend" if combined_score >= 20 else "downtrend" if combined_score <= -20 else "sideways"

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
        "entry_price": entry_price,
        "entry_low":   entry_low,
        "entry_high":  entry_high,
        "target_price": target_price,
        "target_low":  target_low,
        "target_high": target_high,
        "stop_loss": stop_loss,
        "month_high": round(month_high, 2),
        "month_low":  round(month_low, 2),
        "trend": trend,
        "rsi":         round(rsi, 2)          if not np.isnan(rsi)  else None,
        "macd":        round(macd, 4)          if not np.isnan(macd) else None,
        "bb_position": round(bb_position, 1)   if bb_position is not None else None,
        "ma_analysis": ma_analysis,
    }
