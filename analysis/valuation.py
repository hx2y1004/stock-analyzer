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
}


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
    beta = _clamp(0.67 * raw_beta + 0.33, 0.5, 2.2)

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
                          da_pct, capex_pct, wc_per_drev, wacc, terminal_g):
    """매출 성장경로 → 연도별 FCF → mid-year 할인 → EV.

    wc_per_drev: 운전자본 현금흐름 / 매출 '증가분' 비율.
      운전자본은 매출 수준이 아니라 **증가분**에 비례한다. 수준에 비례시키면
      성숙기업이 매년 매출의 N%를 영구히 잃는 비현실적 구조가 된다.
    Returns: (rows, pv_sum, pv_terminal, ev)
    """
    rows = []
    rev = base_rev
    pv_sum = 0.0
    last_fcf = None
    years = len(growth_path)

    for i, g in enumerate(growth_path, start=1):
        prev_rev = rev
        rev = rev * (1 + g)
        ebit = rev * ebit_margin
        nopat = ebit * (1 - tax_rate)
        da = rev * da_pct
        capex = rev * capex_pct
        # 현금흐름표 부호를 그대로 유지 (음수 = 현금 유출) → 더한다
        wc_cf = wc_per_drev * (rev - prev_rev)
        fcf = nopat + da - capex + wc_cf
        # mid-year convention: 현금흐름이 연중 고르게 발생한다고 가정
        period = i - 0.5
        pv = fcf / ((1 + wacc) ** period)
        pv_sum += pv
        last_fcf = fcf
        rows.append({
            "year": i,
            "revenue": round(rev),
            "growth_pct": round(g * 100, 1),
            "ebit": round(ebit),
            "fcf": round(fcf),
            "pv": round(pv),
        })

    # 터미널 밸류 (영구성장) — 성장률은 반드시 WACC 미만
    if wacc <= terminal_g or last_fcf is None or last_fcf <= 0:
        return rows, pv_sum, None, None
    tv = last_fcf * (1 + terminal_g) / (wacc - terminal_g)
    pv_terminal = tv / ((1 + wacc) ** (years - 0.5))
    return rows, pv_sum, pv_terminal, pv_sum + pv_terminal


def _equity_per_share(ev, net_debt, shares):
    if ev is None or not shares or shares <= 0:
        return None
    equity = ev - (net_debt or 0)
    if equity <= 0:
        return None
    return equity / shares


def compute_dcf(ticker, info, income_stmt, cashflow, balance_sheet,
                current_price, currency, risk_free_rate=None):
    """DCF 적정주가 산출. 데이터 부족 시 None.

    Returns dict:
      fair_value, upside_pct, scenarios{bear,base,bull}, assumptions, projection,
      sensitivity, warnings
    """
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

    base_rev = _first(rev)
    base_ebit = _first(ebit_s)
    if not base_rev or base_rev <= 0 or base_ebit is None:
        return None  # 핵심 데이터 없음 → DCF 불가

    # ── 2) 비율 산출 (최신연도 기준) ─────────────────────
    ebit_margin = _clamp(base_ebit / base_rev, -0.20, 0.70)
    if ebit_margin <= 0:
        return None  # 영업적자 기업은 DCF 부적합

    tax_v, pretax_v = _first(tax_s), _first(pretax_s)
    tax_rate = 0.22
    if tax_v is not None and pretax_v and pretax_v > 0:
        tax_rate = _clamp(tax_v / pretax_v, 0.05, 0.40)

    da_pct = _clamp((_first(da_s) or 0) / base_rev, 0.0, 0.40)
    capex_pct = _clamp(abs(_first(capex_s) or 0) / base_rev, 0.0, 0.40)

    # 운전자본: 매출 '증가분' 대비 비율로 산출 (수준 대비가 아님)
    wc_per_drev = 0.0
    prev_rev = rev[1] if len(rev) > 1 else None
    wc_v = _first(wc_s)
    if wc_v is not None and prev_rev and (base_rev - prev_rev) != 0:
        wc_per_drev = _clamp(wc_v / (base_rev - prev_rev), -0.30, 0.30)

    # ── 3) 성장 시나리오 ────────────────────────────────
    # 과거 CAGR만 쓰면 왜곡이 큼(예: AAPL 4년 CAGR 1.8%, 삼성 3.3%).
    # 애널리스트 컨센서스 매출성장률을 우선 반영하고 과거치와 블렌딩한다.
    hist_cagr = _cagr(rev)
    consensus_g = _f(info.get("revenueGrowth"))

    if hist_cagr is None and consensus_g is None:
        hist_cagr = 0.05
        warnings.append("성장률 데이터 없음 → 5% 가정")

    growth_src = []
    if consensus_g is not None:
        # 사이클 최고점(예: 반도체 +69%)은 지속 불가 → 상한 제한
        consensus_g = _clamp(consensus_g, -0.10, 0.35)
        growth_src.append(("컨센서스", consensus_g))
    if hist_cagr is not None:
        hist_cagr = _clamp(hist_cagr, -0.10, 0.30)
        growth_src.append(("과거CAGR", hist_cagr))

    if len(growth_src) == 2:
        # 컨센서스 60% + 과거 40% — 단기 전망을 우선하되 과거 추세로 완충
        base_g = 0.6 * growth_src[0][1] + 0.4 * growth_src[1][1]
    else:
        base_g = growth_src[0][1]
    base_g = _clamp(base_g, 0.0, 0.30)

    # 고성장 기업은 10년 예측 (5년 만에 영구성장률로 떨어뜨리면 구조적 저평가)
    years = HIGH_GROWTH_YEARS if base_g > 0.15 else PROJECTION_YEARS

    def _path(start_g, n=None):
        """시작 성장률 → 영구성장률로 선형 감쇠(fade)."""
        n = n or years
        return [start_g + (a["terminal_g"] - start_g) * (i / n) for i in range(n)]

    scen_growth = {
        "bear": _path(_clamp(base_g * 0.6, 0.0, 0.25)),
        "base": _path(base_g),
        "bull": _path(_clamp(base_g * 1.4, 0.0, 0.35)),
    }

    # ── 4) WACC ─────────────────────────────────────────
    rf = risk_free_rate if risk_free_rate else a["rf_default"]
    beta = _f(info.get("beta"))
    market_cap = _f(info.get("marketCap"))
    shares = _f(info.get("sharesOutstanding"))
    total_debt = _first(debt_s) or 0
    cash = _first(cash_s) or 0

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
            da_pct, capex_pct, wc_per_drev, wacc, a["terminal_g"]
        )
        ps = _equity_per_share(ev, net_debt, shares)
        scenarios[name] = {
            "fair_value": round(ps, 2) if ps else None,
            "upside_pct": round((ps / current_price - 1) * 100, 1)
                          if (ps and current_price) else None,
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
                da_pct, capex_pct, wc_per_drev, wacc, a["terminal_g"]
            )
            ps_i = _equity_per_share(ev_i, net_debt, shares)
            if ps_i is None:
                lo = mid
                continue
            if ps_i < current_price:
                lo = mid
            else:
                hi = mid
        if hi < 0.595:                      # 상한에 붙지 않았으면 유효한 해
            implied_growth = round(hi * 100, 1)

    # ── 5-c) 신뢰도 판정 ────────────────────────────────
    # 과거 재무 기반 DCF는 미래 옵션가치(신사업·플랫폼 전환 등)를 담지 못한다.
    # 괴리가 클수록 '고평가 판정'이 아니라 '이 방법론으로는 설명 안 됨'에 가깝다.
    gap = scenarios["base"]["upside_pct"]
    if gap is None:
        reliability = "low"
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
                da_pct, capex_pct, wc_per_drev, w, g
            )
            ps = _equity_per_share(ev_s, net_debt, shares)
            row.append(round(ps, 2) if ps else None)
        sens.append(row)

    return {
        "fair_value": fair,
        "current_price": current_price,
        "upside_pct": scenarios["base"]["upside_pct"],
        "currency": currency,
        "scenarios": scenarios,
        "implied_growth_pct": implied_growth,   # 현재가가 반영 중인 매출성장률 (역산)
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
            "ebit_margin_pct": round(ebit_margin * 100, 1),
            "hist_cagr_pct": round(hist_cagr * 100, 1) if hist_cagr is not None else None,
            "consensus_growth_pct": round(consensus_g * 100, 1) if consensus_g is not None else None,
            "base_growth_pct": round(base_g * 100, 1),
            "capex_pct": round(capex_pct * 100, 1),
            "da_pct": round(da_pct * 100, 1),
            "years": years,
            "net_debt": round(net_debt),
            "terminal_share_pct": round(base_tv_share, 1) if base_tv_share else None,
            "rf_source": "^TNX" if (market == "US" and risk_free_rate) else "기본가정",
        },
        "projection": base_projection,
        "sensitivity": {
            "wacc_axis": [round(w * 100, 2) for w in wacc_axis],
            "g_axis": [round(g * 100, 2) for g in g_axis],
            "grid": sens,
        },
        "warnings": warnings,
    }
