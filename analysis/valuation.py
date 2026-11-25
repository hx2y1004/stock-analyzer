"""DCF(현금흐름할인) 밸류에이션.

방법론 출처: anthropics/financial-services 의 dcf-model 스킬 (Apache License 2.0)
 → 기관용 Excel 산출물 대신, 동일한 계산 절차를 Python으로 이식해 웹 카드로 렌더.

핵심 원칙 (AI 코치의 _coach_stats_block 과 동일한 패턴):
  숫자는 전부 여기서 **결정적으로** 계산한다. AI에게 계산을 시키지 않는다.
  AI는 산출된 숫자를 '해석'만 한다.

절차: 과거 3~5년 분석 → 매출 3시나리오 → EBIT→NOPAT→FCF
      → CAPM 기반 WACC → mid-year 할인 → 영구성장 터미널 → EV−순부채 → 주당가치
"""
import math

# ── 시장별 가정 ────────────────────────────────────────────
# rf: 무위험수익률, erp: 시장위험프리미엄, g: 영구성장률
MARKET_ASSUMPTIONS = {
    "US": {"rf_default": 0.045, "erp": 0.050, "terminal_g": 0.025},
    # 한국 국고채 금리는 yfinance 미제공 → 상수 기본값 (UI에 가정값 노출)
    "KR": {"rf_default": 0.032, "erp": 0.065, "terminal_g": 0.020},
}

PROJECTION_YEARS = 5        # 성숙 기업
# Blume 조정 베타 범위. 예전 [0.5, 2.2] 는 고베타 대형주(SK하이닉스 원베타 2.47 → 1.98)의 WACC 를 15% 넘게 만들었다
BETA_MIN, BETA_MAX = 0.6, 1.5
MARGIN_MAX = 0.85           # 영업이익률 상한 (고마진 반도체·소프트웨어)
TERMINAL_REINVEST = 0.015   # 영구 단계 순설비투자(CapEx - 감가상각) / 매출
TARGET_HIST_W = 2 / 3       # 목표 영업이익률에서 과거(흑자 연도 중앙값) 비중
CYCLE_PEAK_X = 1.3          # 출발 마진 > 흑자 연도 중앙값 × 1.3 이면 경기 정점으로 본다
HIGH_GROWTH_YEARS = 10      # 고성장 기업(과거 CAGR 15%↑) — 감쇠 기간을 길게

# yfinance 재무제표 행 이름 후보 (한국/미국 스키마 차이 흡수)
_ROWS = {
    "revenue":  ["Total Revenue", "Operating Revenue"],
    "ebit":     ["EBIT", "Operating Income", "Total Operating Income As Reported"],
    "tax":      ["Tax Provision", "Income Tax Expense"],
    "pretax":   ["Pretax Income", "Income Before Tax"],
    # 한국 종목은 'Depreciation Amortization Depletion' 행이 없음 → 폴백 필수
    "da":       ["Depreciation And Amortization",
                 "Depreciation Amortization Depletion",
                 "Depreciation"],
    "capex":    ["Capital Expenditure", "Purchase Of PPE"],
    "wc":       ["Change In Working Capital"],
    "debt":     ["Total Debt"],
    "cash":     ["Cash And Cash Equivalents",
                 "Cash Cash Equivalents And Short Term Investments"],
    "interest": ["Interest Expense", "Interest Expense Non Operating"],
    "minority": ["Minority Interest", "Minority Interests"],
}

# 은행·보험·증권: 예금·보험부채가 영업 자산이라 FCFF DCF 가 성립하지 않는다 → P/B-ROE 모델
_FIN_INDUSTRY_KEYS = ("bank", "insurance", "insurer", "capital markets", "mortgage finance")


def _is_financial(info):
    industry = str((info or {}).get("industry") or "").lower()
    return any(k in industry for k in _FIN_INDUSTRY_KEYS)


def _f(v):
    """None/NaN 안전 float 변환."""
    try:
        if v is None:
            return None
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def _series(df, key):
    """재무제표에서 행 하나를 최신연도순 리스트로. 없으면 []."""
    if df is None or getattr(df, "empty", True):
        return []
    for name in _ROWS.get(key, []):
        if name in df.index:
            vals = [_f(v) for v in df.loc[name].tolist()]
            if any(v is not None for v in vals):
                return vals
    return []


def _first(vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _cagr(vals):
    """최신연도순 리스트에서 연평균성장률. 데이터 부족/음수면 None."""
    clean = [v for v in vals if v is not None and v > 0]
    if len(clean) < 2:
        return None
    latest, oldest = clean[0], clean[-1]
    periods = len(clean) - 1
    try:
        return (latest / oldest) ** (1 / periods) - 1
    except (ValueError, ZeroDivisionError):
        return None


def _calc_wacc(market, beta, market_cap, total_debt, tax_rate, interest_exp, rf):
    """CAPM 기반 가중평균자본비용."""
    a = MARKET_ASSUMPTIONS[market]
    raw_beta = beta if beta else 1.0
    # Blume 조정: 베타는 장기적으로 1로 회귀 → 원본 베타 그대로 쓰면 고베타주가 과도하게 할인됨
    beta = _clamp(0.67 * raw_beta + 0.33, BETA_MIN, BETA_MAX)

    cost_equity = rf + beta * a["erp"]

    # 타인자본비용: 실제 이자비용/부채, 산출 불가 시 무위험+스프레드
    cost_debt = None
    if interest_exp and total_debt and total_debt > 0:
        cost_debt = abs(interest_exp) / total_debt
    if not cost_debt or not (0.005 < cost_debt < 0.25):
        cost_debt = rf + 0.02
    cost_debt_after_tax = cost_debt * (1 - tax_rate)

    e = market_cap or 0
    d = total_debt or 0
    total = e + d
    if total <= 0:
        return None, None, None, beta
    wacc = (cost_equity * e / total) + (cost_debt_after_tax * d / total)
    return _clamp(wacc, 0.05, 0.20), cost_equity, cost_debt, beta


def _project_and_discount(base_rev, growth_path, ebit_margin, tax_rate,
                          da_pct, capex_pct, wc_per_drev, wacc, terminal_g,
                          margin_end=None, capex_end=None):
    """매출 성장경로 → 연도별 FCF → mid-year 할인 → EV.

    ebit_margin → margin_end: 1년차부터 N년차까지 영업이익률을 선형으로 수렴 (None 이면 고정).
      최근 피크/바텀 마진을 영구히 쓰지 않게 한다.
    capex_pct → capex_end: 성장률이 영구성장률로 줄어드는 속도에 맞춰 설비투자율도 감가상각 + α 로 수렴 (None 이면 고정).
      예전엔 증설기 CapEx(예: 하이닉스 매출의 29%)를 10년차·터미널까지 그대로 빼서 NOPAT 이 거의 다 상쇄됐다.
    wc_per_drev: 매출 '증가분' 1원당 묶이는 운전자본(0 ~ 0.25, 양수 = 투자). 현금흐름 = -wc_per_drev × Δ매출.
    터미널: N+1년차 정상화 현금흐름(수렴한 마진·설비투자, 영구성장분 운전자본) / (WACC - g),
            할인은 기말 기준 (1 + WACC)^N.
    Returns: (rows, pv_sum, pv_terminal, ev)
    """
    rows = []
    rev = base_rev
    pv_sum = 0.0
    years = len(growth_path)
    m_end = ebit_margin if margin_end is None else margin_end
    c_end = capex_pct if capex_end is None else capex_end

    for i, g in enumerate(growth_path, start=1):
        w = i / years if years else 1.0
        m = ebit_margin + (m_end - ebit_margin) * w
        cx = capex_pct + (c_end - capex_pct) * w
        prev_rev = rev
        rev = rev * (1 + g)
        ebit = rev * m
        nopat = ebit * (1 - tax_rate)
        da = rev * da_pct
        capex = rev * cx
        # 성장분만큼 운전자본에 현금이 묶인다 (음수 = 현금 유출)
        wc_cf = -wc_per_drev * max(rev - prev_rev, 0.0)
        fcf = nopat + da - capex + wc_cf
        # mid-year convention: 현금흐름이 연중 고르게 발생한다고 가정
        period = i - 0.5
        pv = fcf / ((1 + wacc) ** period)
        pv_sum += pv
        rows.append({
            "year": i,
            "revenue": round(rev),
            "growth_pct": round(g * 100, 1),
            "margin_pct": round(m * 100, 1),
            "ebit": round(ebit),
            "fcf": round(fcf),
            "pv": round(pv),
        })

    # 터미널 밸류 (영구성장) — 성장률은 반드시 WACC 미만
    if wacc <= terminal_g or not rows:
        return rows, pv_sum, None, None
    rev_t = rev * (1 + terminal_g)
    fcf_t = (rev_t * m_end * (1 - tax_rate) + rev_t * da_pct - rev_t * c_end
             - wc_per_drev * max(rev_t - rev, 0.0))
    if fcf_t <= 0:
        return rows, pv_sum, None, None
    tv = fcf_t / (wacc - terminal_g)
    pv_terminal = tv / ((1 + wacc) ** years)
    return rows, pv_sum, pv_terminal, pv_sum + pv_terminal


def ttm_from_quarters(rev_q, oi_q):
    """최근 4개 분기 확정 실적 → (TTM 매출, TTM 영업이익). 4개 분기가 연속으로 다 있어야 하고, 아니면 None.

    rev_q / oi_q: [{"period": "YYYY-MM", "actual": float|None}, ...] (순서 무관, 추정치만 있는 분기는 무시)
    """
    def act(rows):
        return {r["period"]: _f(r.get("actual")) for r in (rows or []) if r and r.get("period") and _f(r.get("actual")) is not None}
    rv, oi = act(rev_q), act(oi_q)
    common = sorted(set(rv) & set(oi), reverse=True)[:4]
    if len(common) < 4:
        return None
    ym = [int(p[:4]) * 12 + int(p[5:7]) for p in common]
    if any(ym[i] - ym[i + 1] != 3 for i in range(3)):       # 3개월 간격으로 이어져야 (빠진 분기 없음)
        return None
    r_sum, o_sum = sum(rv[p] for p in common), sum(oi[p] for p in common)
    if r_sum <= 0:
        return None
    return {"revenue": r_sum, "ebit": o_sum, "latest": common[0], "periods": common[::-1]}


def _equity_per_share(ev, net_debt, shares, minority=0.0):
    """EV → 주주가치: 순부채와 비지배지분(연결 자회사의 남의 몫)을 뺀다."""
    if ev is None or not shares or shares <= 0:
        return None
    equity = ev - (net_debt or 0) - (minority or 0)
    if equity <= 0:
        return None
    return equity / shares


def compute_pb_roe(info, current_price, currency, risk_free_rate=None):
    """금융주 적정가: 적정 P/B = (ROE - g) / (COE - g), 적정가 = 적정 P/B × 주당순자산.

    은행·보험은 예금·보험부채가 곧 영업이라 FCFF DCF 가 성립하지 않는다(Damodaran 의 금융주 밸류에이션).
    ROE 가 영구성장률 이하이거나 자료가 없으면 '미적용' 안내만 돌려준다.
    """
    market = "KR" if currency == "KRW" else "US"
    a = MARKET_ASSUMPTIONS[market]
    rf = risk_free_rate if risk_free_rate else a["rf_default"]
    g = a["terminal_g"]
    beta_raw = _f(info.get("beta")) or 1.0
    beta = _clamp(0.67 * beta_raw + 0.33, BETA_MIN, BETA_MAX)
    coe = rf + beta * a["erp"]
    roe = _f(info.get("returnOnEquity"))
    bvps = _f(info.get("bookValue"))
    pbr_now = _f(info.get("priceToBook"))
    if bvps and current_price and (pbr_now is None or pbr_now <= 0):
        pbr_now = current_price / bvps
    # 주당순자산 통화가 주가와 다른 경우(ADR 등) 대비: 현재 PBR 로 역산한 값과 크게 다르면 그쪽을 쓴다
    if bvps and pbr_now and current_price:
        implied_bvps = current_price / pbr_now
        if not (0.5 < bvps / implied_bvps < 2.0):
            bvps = implied_bvps
    note = "DCF 미적용 (금융주 — PBR·ROE·애널리스트 목표가 참고)"
    base = {"model": "pb_roe", "is_financial": True, "fair_value": None, "current_price": current_price,
            "currency": currency, "message": note, "scenarios": {}, "warnings": [],
            "analyst_target": _f(info.get("targetMeanPrice")), "analyst_count": info.get("numberOfAnalystOpinions")}
    if roe is None or not bvps or bvps <= 0 or coe <= g or roe <= g:
        base["warnings"].append("ROE 가 자기자본비용 모델의 성장률 이하이거나 자료가 없어 적정 PBR 을 계산하지 않았습니다.")
        return base

    def fair(r):
        pb = max((r - g) / (coe - g), 0.0)
        return pb, pb * bvps

    scen = {}
    for name, r in (("bear", roe * 0.8), ("base", roe), ("bull", roe * 1.2)):
        pb, fv = fair(r)
        scen[name] = {"fair_value": round(fv, 2) if fv > 0 else None, "fair_pb": round(pb, 2), "roe_pct": round(r * 100, 1),
                      "upside_pct": round((fv / current_price - 1) * 100, 1) if (fv > 0 and current_price) else None}
    fair_pb, fv = fair(roe)
    implied_roe = (g + pbr_now * (coe - g)) if pbr_now else None     # 현재 PBR 을 정당화하는 ROE
    base.update({
        "fair_value": scen["base"]["fair_value"],
        "upside_pct": scen["base"]["upside_pct"],
        "scenarios": scen,
        "message": "금융주라 FCF 기반 DCF 대신 적정 PBR(ROE·자기자본비용) 모델로 계산했습니다.",
        "implied_roe_pct": round(implied_roe * 100, 1) if implied_roe is not None else None,
        "reliability": "medium",
        "assumptions": {
            "roe_pct": round(roe * 100, 1), "cost_equity_pct": round(coe * 100, 2),
            "terminal_g_pct": round(g * 100, 2), "fair_pb": round(fair_pb, 2),
            "pbr_now": round(pbr_now, 2) if pbr_now else None, "bvps": round(bvps, 2),
            "beta": round(beta, 2), "beta_raw": round(beta_raw, 2),
            "risk_free_pct": round(rf * 100, 2), "erp_pct": round(a["erp"] * 100, 2),
            "rf_source": "미 10년 국채" if (market == "US" and risk_free_rate) else "기본가정",
        },
    })
    return base


def compute_dcf(ticker, info, income_stmt, cashflow, balance_sheet,
                current_price, currency, risk_free_rate=None, consensus_growth=None, ttm=None):
    """DCF 적정주가 산출. 데이터 부족 시 None. 금융주는 compute_pb_roe 결과(model='pb_roe').

    consensus_growth: 애널리스트 내년 매출 성장률 컨센서스(소수). 없으면 최근 분기 YoY 로 대신한다.
    ttm: ttm_from_quarters() 결과 {revenue, ebit, latest}. 있으면 연간 재무 대신 최근 4분기 합으로 출발한다.
    Returns dict:
      fair_value, upside_pct, scenarios{bear,base,bull}, assumptions, projection,
      sensitivity, warnings
    """
    if _is_financial(info):
        return compute_pb_roe(info, current_price, currency, risk_free_rate)
    market = "KR" if currency == "KRW" else "US"
    a = MARKET_ASSUMPTIONS[market]
    warnings = []

    # ── 1) 과거 재무 추출 ────────────────────────────────
    rev = _series(income_stmt, "revenue")
    ebit_s = _series(income_stmt, "ebit")
    tax_s = _series(income_stmt, "tax")
    pretax_s = _series(income_stmt, "pretax")
    da_s = _series(cashflow, "da")
    capex_s = _series(cashflow, "capex")
    wc_s = _series(cashflow, "wc")
    debt_s = _series(balance_sheet, "debt")
    cash_s = _series(balance_sheet, "cash")
    int_s = _series(income_stmt, "interest")

    annual_rev = _first(rev)
    annual_ebit = _first(ebit_s)
    if not annual_rev or annual_rev <= 0 or annual_ebit is None:
        return None  # 핵심 데이터 없음 → DCF 불가
    # 출발점: 최근 4개 분기(TTM) 확정 실적이 있으면 그것, 없으면 최근 연간
    if ttm and _f(ttm.get("revenue")) and ttm["revenue"] > 0 and _f(ttm.get("ebit")) is not None:
        base_rev, base_ebit = float(ttm["revenue"]), float(ttm["ebit"])
        base_src = f"TTM (~{ttm.get('latest', '')})"
    else:
        base_rev, base_ebit = annual_rev, annual_ebit
        base_src = "연간"

    # ── 2) 비율 산출 ─────────────────────────────────────
    # 영업이익률: 출발(최근 TTM·연간) → 목표(정상 마진)로 N년에 걸쳐 수렴.
    #  목표 = 최근 3~5년 흑자 연도 마진의 중앙값 2/3 + 출발 마진 1/3. 적자 연도(사이클 바닥)는 중앙값에서 뺀다.
    #  (경기민감주의 피크 마진이 10년 뒤까지 이어진다고 보지 않도록 과거 쪽에 더 무게)
    #  예전: 적자 연도까지 넣은 3년 단순 평균(하이닉스 20%)을 영구히 고정 → 최신 실적과 동떨어짐.
    hist_margins = []
    for r_v, e_v in list(zip(rev, ebit_s))[:5]:
        if r_v and r_v > 0 and e_v is not None:
            hist_margins.append(e_v / r_v)
    margin_latest = annual_ebit / annual_rev
    start_margin = _clamp(base_ebit / base_rev, -0.20, MARGIN_MAX)
    pos = sorted(m for m in hist_margins if m > 0)
    hist_med = (pos[len(pos) // 2] if len(pos) % 2 else (pos[len(pos) // 2 - 1] + pos[len(pos) // 2]) / 2) if pos else None
    target_margin = (_clamp(TARGET_HIST_W * hist_med + (1 - TARGET_HIST_W) * start_margin, -0.20, MARGIN_MAX)
                     if hist_med is not None else start_margin)
    if target_margin <= 0 or (start_margin <= 0 and target_margin <= 0):
        return None  # 영업적자가 이어지는 기업은 DCF 부적합
    ebit_margin = start_margin
    margin_years = len(hist_margins) or 1

    tax_v, pretax_v = _first(tax_s), _first(pretax_s)
    tax_rate = 0.22
    if tax_v is not None and pretax_v and pretax_v > 0:
        tax_rate = _clamp(tax_v / pretax_v, 0.05, 0.40)

    # 설비투자·감가상각 비율은 연간 현금흐름표 기준 (분기 현금흐름은 계절성이 커서 쓰지 않음)
    da_pct = _clamp((_first(da_s) or 0) / annual_rev, 0.0, 0.40)
    capex_pct = _clamp(abs(_first(capex_s) or 0) / annual_rev, 0.0, 0.40)
    # 영구 단계 설비투자율: 감가상각 + 1.5%p 로 수렴 (이미 그보다 낮으면 그대로)
    capex_end = min(capex_pct, da_pct + TERMINAL_REINVEST)

    # 운전자본 투자율: 매출 증가분 대비 운전자본 현금 유출 (최근 2~3년 평균, 0~25%)
    #  예전: wc / Δ매출 을 그대로 써서 '매출 감소 + 운전자본 유출' 이 겹치면 부호가 양수로 뒤집혀
    #  성장할수록 현금이 생기는 모델이 됐다. → 투자율 = -wc/Δ매출 을 [0, 0.25] 로 자르고 평균.
    wc_rates = []
    for i in range(min(3, len(rev) - 1, len(wc_s))):
        r0, r1, w = rev[i], rev[i + 1], wc_s[i]
        if r0 is None or r1 is None or w is None or r1 <= 0:
            continue
        d_rev = r0 - r1
        if abs(d_rev) < r1 * 0.01:           # 매출 변화가 1% 미만이면 비율이 폭주 → 제외
            continue
        wc_rates.append(_clamp(-w / d_rev, 0.0, 0.25))
    wc_per_drev = sum(wc_rates) / len(wc_rates) if wc_rates else 0.0

    # ── 3) 성장 시나리오 ────────────────────────────────
    # 과거 CAGR만 쓰면 왜곡이 큼(예: AAPL 4년 CAGR 1.8%, 삼성 3.3%) → 앞을 보는 성장률과 블렌딩.
    #  - consensus_g: 애널리스트 '내년' 매출 성장률 컨센서스 (app 이 revenue_estimate 에서 넘김, 없으면 None)
    #  - recent_g:    info.revenueGrowth = 최근 분기 매출의 전년 동기 대비 성장률 (실적, 컨센서스 아님)
    # 예전엔 recent_g 를 '컨센서스' 라고 불렀다.
    hist_cagr = _cagr(rev)
    consensus_g = _f(consensus_growth)
    recent_g = _f(info.get("revenueGrowth"))
    if consensus_g is not None:
        consensus_g = _clamp(consensus_g, -0.10, 0.35)
    if recent_g is not None:
        # 사이클 최고점(예: 반도체 +69%)은 지속 불가 → 상한 제한
        recent_g = _clamp(recent_g, -0.10, 0.35)
    if hist_cagr is not None:
        hist_cagr = _clamp(hist_cagr, -0.10, 0.30)

    # TTM 으로 출발하면 최근 분기 YoY 는 이미 출발 매출에 들어 있다 → 또 쓰면 같은 성장을 두 번 반영(이중 계상).
    #  그래서 TTM 출발일 때는 앞을 보는 애널리스트 컨센서스만, 없으면 과거 연평균만 쓴다.
    use_recent = recent_g is not None and not base_src.startswith("TTM")
    forward_g, forward_src = (consensus_g, "consensus") if consensus_g is not None else \
                             (recent_g, "recent") if use_recent else (None, None)
    if forward_g is None and hist_cagr is None:
        hist_cagr = 0.05
        warnings.append("성장률 데이터 없음 → 5% 가정")
    if forward_g is not None and hist_cagr is not None:
        # 앞을 보는 성장률 60% + 과거 40% — 단기 전망을 우선하되 과거 추세로 완충
        base_g = 0.6 * forward_g + 0.4 * hist_cagr
    else:
        base_g = forward_g if forward_g is not None else hist_cagr
    base_g = _clamp(base_g, 0.0, 0.30)

    # 고성장 기업은 10년 예측 (5년 만에 영구성장률로 떨어뜨리면 구조적 저평가)
    # 단, 최근 마진이 과거 흑자 연도 중앙값의 1.3배를 넘는 '경기 정점' 경기민감주(메모리 반도체 등)는
    #  지금의 고성장이 구조적이라고 보기 어려워 10년 고성장 투영을 쓰지 않는다 (5년 안에 영구성장률로 감쇠)
    cycle_peak = hist_med is not None and hist_med > 0 and start_margin > hist_med * CYCLE_PEAK_X
    years = HIGH_GROWTH_YEARS if (base_g > 0.15 and not cycle_peak) else PROJECTION_YEARS
    if cycle_peak:
        warnings.append(
            f"최근 영업이익률({start_margin * 100:.0f}%)이 과거 흑자 연도 중앙값({hist_med * 100:.0f}%)보다 크게 높은 "
            f"경기 정점 구간으로 보고, 고성장 기간을 {years}년으로 짧게 잡았습니다.")

    def _path(start_g, n=None):
        """시작 성장률 → 영구성장률로 선형 감쇠(fade). 마지막 해(N년차)가 정확히 영구성장률.

        1년차 = start + (g∞ - start)/n … N년차 = g∞ ((i + 1) / n). 예전 (i / n) 은 N년차에도 g∞ 에 못 미쳤다.
        """
        n = n or years
        return [start_g + (a["terminal_g"] - start_g) * ((i + 1) / n) for i in range(n)]

    scen_start = {"bear": _clamp(base_g * 0.6, 0.0, 0.25), "base": base_g, "bull": _clamp(base_g * 1.4, 0.0, 0.35)}
    scen_growth = {k: _path(v) for k, v in scen_start.items()}

    # ── 4) WACC ─────────────────────────────────────────
    rf = risk_free_rate if risk_free_rate else a["rf_default"]
    beta = _f(info.get("beta"))
    market_cap = _f(info.get("marketCap"))
    shares = _f(info.get("sharesOutstanding"))
    total_debt = _first(debt_s) or 0
    cash = _first(cash_s) or 0
    minority = max(_first(_series(balance_sheet, "minority")) or 0, 0)

    if not market_cap or not shares:
        return None
    if beta is None:
        beta = 1.0
        warnings.append("베타 정보 없음 → 1.0 가정")

    raw_beta = beta
    wacc, cost_equity, cost_debt, beta = _calc_wacc(
        market, beta, market_cap, total_debt, tax_rate, _first(int_s), rf
    )
    if wacc is None:
        return None

    net_debt = total_debt - cash

    # ── 5) 시나리오별 밸류에이션 ────────────────────────
    scenarios = {}
    base_projection = None
    base_ev, base_tv_share = None, None
    for name, path in scen_growth.items():
        rows, pv_sum, pv_tv, ev = _project_and_discount(
            base_rev, path, ebit_margin, tax_rate,
            da_pct, capex_pct, wc_per_drev, wacc, a["terminal_g"],
            margin_end=target_margin, capex_end=capex_end,
        )
        ps = _equity_per_share(ev, net_debt, shares, minority)
        scenarios[name] = {
            "fair_value": round(ps, 2) if ps else None,
            "upside_pct": round((ps / current_price - 1) * 100, 1)
                          if (ps and current_price) else None,
            "start_growth_pct": round(scen_start[name] * 100, 1),     # 1년차 직전 출발 성장률 (N년차엔 영구성장률)
        }
        if name == "base":
            base_projection = rows
            base_ev = ev
            base_tv_share = (pv_tv / ev * 100) if (pv_tv and ev) else None

    fair = scenarios["base"]["fair_value"]
    if not fair:
        return None

    # ── 5-b) 역산 DCF: 현재가를 정당화하는 매출성장률 ──
    # 단일 적정주가보다 해석이 안전함 ("시장은 연 X% 성장을 가정 중")
    implied_growth = None
    if current_price and current_price > 0:
        lo, hi = -0.10, 0.60
        for _ in range(40):
            mid = (lo + hi) / 2
            _, _, _, ev_i = _project_and_discount(
                base_rev, _path(mid), ebit_margin, tax_rate,
                da_pct, capex_pct, wc_per_drev, wacc, a["terminal_g"],
                margin_end=target_margin, capex_end=capex_end,
            )
            ps_i = _equity_per_share(ev_i, net_debt, shares, minority)
            if ps_i is None:
                lo = mid
                continue
            if ps_i < current_price:
                lo = mid
            else:
                hi = mid
        # 상한·하한 어느 쪽에도 붙지 않았을 때만 유효한 해 (하한에 붙으면 '역성장해도 비싸지 않음' = 해 없음)
        if lo > -0.095 and hi < 0.595:
            implied_growth = round(hi * 100, 1)

    # ── 5-b2) 역산 #2: 현재가를 정당화하는 영업이익률 ──
    # 마진 확대는 DCF에서 가장 남용되는 가정이므로 '가정하지 않고 역산'한다.
    # 성장률은 기본 시나리오로 고정 → 마진만 풀어 시장이 전제한 수익성을 드러냄.
    implied_margin = None
    if current_price and current_price > 0:
        lo, hi = 0.005, MARGIN_MAX
        for _ in range(40):
            mid = (lo + hi) / 2
            _, _, _, ev_m = _project_and_discount(
                base_rev, scen_growth["base"], mid, tax_rate,
                da_pct, capex_pct, wc_per_drev, wacc, a["terminal_g"],
                margin_end=mid, capex_end=capex_end,
            )
            ps_m = _equity_per_share(ev_m, net_debt, shares, minority)
            if ps_m is None or ps_m < current_price:
                lo = mid
            else:
                hi = mid
        if lo > 0.006 and hi < MARGIN_MAX - 0.005:   # 상한·하한에 붙지 않았을 때만 유효한 해
            implied_margin = round(hi * 100, 1)

    # ── 5-c) 신뢰도 판정 ────────────────────────────────
    # 과거 재무 기반 DCF는 미래 옵션가치(신사업·플랫폼 전환 등)를 담지 못한다.
    # 괴리가 클수록 '고평가 판정'이 아니라 '이 방법론으로는 설명 안 됨'에 가깝다.
    gap = scenarios["base"]["upside_pct"]
    # 성장·수익성 어느 쪽으로도 현재가를 설명할 수 없는 극단 케이스
    beyond_model = (implied_growth is None and implied_margin is None)

    if gap is None:
        reliability = "low"
    elif beyond_model:
        reliability = "low"
        warnings.append(
            "현재 주가는 이 모델의 설명 범위를 벗어납니다. 매출이 연 60% 성장하거나 "
            "영업이익률이 85%까지 올라도 도달하지 않는 수준으로, 시장은 재무제표에 "
            "아직 나타나지 않은 요인(신사업 확대·시장 재편 기대 등)을 반영하고 있습니다."
        )
    elif gap < -70 or implied_growth is None:
        reliability = "low"
        warnings.append(
            "현재 주가를 과거 재무만으로는 설명할 수 없습니다. "
            "신사업·성장 옵션가치가 큰 기업일 수 있어 DCF 적정주가는 참고만 하세요."
        )
    elif abs(gap) > 30:
        reliability = "medium"
        warnings.append("DCF 결과와 시장가격의 괴리가 큽니다. 가정값을 함께 확인하세요.")
    else:
        reliability = "high"

    if base_tv_share and base_tv_share > 100:
        # 예측기간 FCF 합이 음수 = 성장에 현금이 계속 투입되는 구조
        warnings.append(
            f"예측 기간({years}년) 동안 잉여현금흐름이 마이너스입니다. "
            "성장에 현금이 계속 투입되는 구조라 가치 대부분이 장기 추정에 의존합니다."
        )
    elif base_tv_share and base_tv_share > 80:
        warnings.append(
            f"기업가치의 {base_tv_share:.0f}%가 {years + 1}년차 이후 추정에서 나옵니다 "
            "(장기 가정에 민감)."
        )

    # ── 6) 민감도 5×5 (WACC × 영구성장률) ───────────────
    sens = []
    wacc_axis = [wacc + d for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    g_axis = [a["terminal_g"] + d for d in (-0.010, -0.005, 0, 0.005, 0.010)]
    for w in wacc_axis:
        row = []
        for g in g_axis:
            if w <= g:
                row.append(None)
                continue
            _, _, _, ev_s = _project_and_discount(
                base_rev, scen_growth["base"], ebit_margin, tax_rate,
                da_pct, capex_pct, wc_per_drev, w, g,
                margin_end=target_margin, capex_end=capex_end,
            )
            ps = _equity_per_share(ev_s, net_debt, shares, minority)
            row.append(round(ps, 2) if ps else None)
        sens.append(row)

    return {
        "model": "dcf",
        "is_financial": False,
        "fair_value": fair,
        "current_price": current_price,
        "upside_pct": scenarios["base"]["upside_pct"],
        "currency": currency,
        "scenarios": scenarios,
        "implied_growth_pct": implied_growth,   # 현재가가 반영 중인 매출성장률 (역산, 마진 고정)
        "implied_margin_pct": implied_margin,   # 현재가가 반영 중인 영업이익률 (역산, 성장 고정)
        "beyond_model": beyond_model,           # 성장·마진 어느 쪽으로도 설명 불가
        "reliability": reliability,             # high | medium | low
        # 교차검증용 애널리스트 컨센서스 목표가 (DCF와 독립적인 제3의 기준점)
        "analyst_target": _f(info.get("targetMeanPrice")),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "assumptions": {
            "wacc_pct": round(wacc * 100, 2),
            "cost_equity_pct": round(cost_equity * 100, 2),
            "cost_debt_pct": round(cost_debt * 100, 2) if cost_debt else None,
            "beta": round(beta, 2),
            "beta_raw": round(raw_beta, 2) if raw_beta else None,
            "risk_free_pct": round(rf * 100, 2),
            "erp_pct": round(a["erp"] * 100, 2),
            "terminal_g_pct": round(a["terminal_g"] * 100, 2),
            "tax_rate_pct": round(tax_rate * 100, 1),
            "ebit_margin_pct": round(start_margin * 100, 1),          # 출발 마진 (TTM 또는 최근 연간)
            "target_margin_pct": round(target_margin * 100, 1),       # N년차 목표 마진 (흑자 연도 중앙값 2/3 + 출발 마진 1/3)
            "hist_margin_median_pct": round(hist_med * 100, 1) if hist_med is not None else None,
            "ebit_margin_latest_pct": round(margin_latest * 100, 1),  # 최근 연간
            "ebit_margin_years": margin_years,
            "base_revenue": round(base_rev),
            "base_revenue_source": base_src,                          # TTM (~YYYY-MM) / 연간
            "shares": round(shares),
            "capex_end_pct": round(capex_end * 100, 1),
            "cycle_peak": bool(cycle_peak),               # N년차 설비투자율 (감가상각 + 1.5%p 로 수렴)
            "wc_invest_pct": round(wc_per_drev * 100, 1),            # 매출 증가분 대비 운전자본 투자율
            "minority_interest": round(minority) if minority else 0,
            "hist_cagr_pct": round(hist_cagr * 100, 1) if hist_cagr is not None else None,
            "consensus_growth_pct": round(consensus_g * 100, 1) if consensus_g is not None else None,   # 애널리스트 내년 매출 성장률
            "recent_growth_pct": round(recent_g * 100, 1) if recent_g is not None else None,           # 최근 분기 매출 YoY (실적)
            "forward_growth_source": forward_src,                    # consensus / recent / None
            "base_growth_pct": round(base_g * 100, 1),
            "capex_pct": round(capex_pct * 100, 1),
            "da_pct": round(da_pct * 100, 1),
            "years": years,
            "net_debt": round(net_debt),
            "terminal_share_pct": round(base_tv_share, 1) if base_tv_share else None,
            "rf_source": "미 10년 국채" if (market == "US" and risk_free_rate) else "기본가정",
        },
        "projection": base_projection,
        "sensitivity": {
            "wacc_axis": [round(w * 100, 2) for w in wacc_axis],
            "g_axis": [round(g * 100, 2) for g in g_axis],
            "grid": sens,
        },
        "warnings": warnings,
    }
