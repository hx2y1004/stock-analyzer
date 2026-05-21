"""
월가 트레이더 관점 고급 펀더멘탈 분석 — Tier 1 + Tier 2

10개 지표:
  Tier 1 (밸류/품질/성장/모멘텀 핵심)
    1. FCF Yield + FCF Margin
    2. PEG Ratio
    3. Margins (Gross/Operating/Net) + YoY
    4. EPS Revision Trend
    5. EV/EBITDA

  Tier 2 (보조 시그널)
    6. Total Shareholder Yield (배당+자사주)
    7. Short Interest
    8. Insider Activity (6M)
    9. Piotroski F-Score (0~9)
   10. Net Debt / EBITDA

→ Q/V/G/M 4팩터 + 종합 등급 (A+ ~ F) 변환
"""

import numpy as np
import pandas as pd


# ── 유틸 ─────────────────────────────────────────────────────────
def _f(v):
    if v is None: return None
    try:
        x = float(v)
        return None if (x != x or abs(x) == float("inf")) else x
    except Exception:
        return None


def _safe_div(a, b):
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


# ── Piotroski F-Score (0~9) ─────────────────────────────────────
def _piotroski_f_score(stock, info):
    """9개 재무 시그널을 점수화 (0~9). yfinance 데이터 부족 시 None."""
    try:
        bs   = stock.balance_sheet       # 최근 4년 (열=연도, 행=항목)
        inc  = stock.financials          # income statement
        cf   = stock.cashflow            # cash flow statement
        if bs is None or inc is None or cf is None:
            return None
        if bs.empty or inc.empty or cf.empty:
            return None
        # 2개년 이상 필요
        if len(bs.columns) < 2 or len(inc.columns) < 2 or len(cf.columns) < 2:
            return None

        def _row(df, *keys):
            for k in keys:
                if k in df.index:
                    return df.loc[k]
            return None

        ni       = _row(inc, "Net Income", "Net Income Common Stockholders")
        rev      = _row(inc, "Total Revenue", "Revenue")
        gross_p  = _row(inc, "Gross Profit")
        ta       = _row(bs,  "Total Assets")
        ltd      = _row(bs,  "Long Term Debt", "Long Term Debt Noncurrent")
        ca       = _row(bs,  "Current Assets")
        cl       = _row(bs,  "Current Liabilities")
        shares   = _row(bs,  "Share Issued", "Ordinary Shares Number", "Common Stock Shares Issued")
        ocf      = _row(cf,  "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")

        if ni is None or ta is None or ocf is None:
            return None

        cur  = ni.index[0]     # 최근
        prev = ni.index[1]     # 1년 전

        score = 0
        # 1. Net Income > 0
        if _f(ni.get(cur)) and _f(ni.get(cur)) > 0: score += 1
        # 2. ROA > 0  (= NI / Total Assets)
        roa_cur = _safe_div(ni.get(cur), ta.get(cur))
        if roa_cur and roa_cur > 0: score += 1
        # 3. Operating CF > 0
        ocf_cur = _f(ocf.get(cur))
        if ocf_cur and ocf_cur > 0: score += 1
        # 4. OCF > Net Income (이익의 질)
        ni_cur = _f(ni.get(cur))
        if ocf_cur and ni_cur and ocf_cur > ni_cur: score += 1
        # 5. ROA 개선 YoY
        roa_prev = _safe_div(ni.get(prev), ta.get(prev))
        if roa_cur is not None and roa_prev is not None and roa_cur > roa_prev: score += 1
        # 6. Long-term Debt 감소 YoY
        if ltd is not None:
            l_cur = _f(ltd.get(cur)); l_prev = _f(ltd.get(prev))
            if l_cur is not None and l_prev is not None and l_cur < l_prev: score += 1
        # 7. Current Ratio 개선 YoY
        if ca is not None and cl is not None:
            cr_cur  = _safe_div(ca.get(cur),  cl.get(cur))
            cr_prev = _safe_div(ca.get(prev), cl.get(prev))
            if cr_cur is not None and cr_prev is not None and cr_cur > cr_prev: score += 1
        # 8. 신주 발행 없음 (shares 감소 또는 동일)
        if shares is not None:
            s_cur = _f(shares.get(cur)); s_prev = _f(shares.get(prev))
            if s_cur is not None and s_prev is not None and s_cur <= s_prev * 1.01: score += 1
        # 9. Gross Margin 개선 YoY
        if gross_p is not None and rev is not None:
            gm_cur  = _safe_div(gross_p.get(cur),  rev.get(cur))
            gm_prev = _safe_div(gross_p.get(prev), rev.get(prev))
            if gm_cur is not None and gm_prev is not None and gm_cur > gm_prev: score += 1

        return score
    except Exception:
        return None


# ── EPS Revision Trend ──────────────────────────────────────────
def _eps_revision_trend(stock):
    """애널리스트 EPS 추정치 30/90일 상향 비율.

    반환: { up_30d, down_30d, up_90d, down_90d, score_pct } 또는 None.
    score_pct ≈ (up - down) / total * 100  (가장 가까운 분기 기준)
    """
    try:
        rev = stock.eps_revisions
        if rev is None or rev.empty:
            return None
        # 일반적으로 행: 0q, +1q, 0y, +1y / 열: upLast7days, upLast30days, downLast30days, ...
        row = rev.iloc[0]  # 현재 분기
        up30   = _f(row.get("upLast30days"))   or 0
        down30 = _f(row.get("downLast30days")) or 0
        up7    = _f(row.get("upLast7days"))    or 0
        down7  = _f(row.get("downLast7days"))  or 0
        total30 = up30 + down30
        score30 = (up30 - down30) / total30 * 100 if total30 > 0 else 0
        return {
            "up_30d":    int(up30),
            "down_30d":  int(down30),
            "up_7d":     int(up7),
            "down_7d":   int(down7),
            "score_pct": round(score30, 1),
        }
    except Exception:
        return None


# ── Insider Activity (6개월) ─────────────────────────────────────
def _insider_activity(stock):
    """최근 6개월 내부자 매매. 매수 - 매도 net count + net value."""
    try:
        df = stock.insider_transactions
        if df is None or df.empty:
            return None
        # 날짜 컬럼 정규화
        date_col = None
        for c in ["Start Date", "startDate", "Date"]:
            if c in df.columns:
                date_col = c
                break
        if date_col:
            df = df.copy()
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=180)
            df = df[df[date_col].dt.tz_localize(None) >= cutoff] if df[date_col].dt.tz is not None \
                 else df[df[date_col] >= cutoff]

        # 매수/매도 분류
        text_col = None
        for c in ["Transaction", "transactionText", "Text", "Type"]:
            if c in df.columns:
                text_col = c
                break
        val_col = None
        for c in ["Value", "valueAtTransaction"]:
            if c in df.columns:
                val_col = c
                break

        buys = 0; sells = 0; net_value = 0.0
        if text_col is not None:
            for _, r in df.iterrows():
                txt = str(r.get(text_col, "")).lower()
                v = _f(r.get(val_col)) if val_col else 0
                v = v or 0
                if "buy" in txt or "purchase" in txt or "acquir" in txt:
                    buys += 1
                    net_value += v
                elif "sell" in txt or "sale" in txt or "dispos" in txt:
                    sells += 1
                    net_value -= v

        return {
            "buys": buys,
            "sells": sells,
            "net": buys - sells,
            "net_value_usd": round(net_value, 0),
        }
    except Exception:
        return None


# ── Buyback Yield (자사주 매입 수익률) ───────────────────────────
def _buyback_yield(stock, market_cap):
    """최근 연간 자사주 매입 / 시가총액."""
    try:
        if not market_cap:
            return None
        cf = stock.cashflow
        if cf is None or cf.empty:
            return None
        for key in ["Repurchase Of Capital Stock", "Common Stock Repurchased",
                    "Net Common Stock Issuance", "Repurchase Of Common Stock"]:
            if key in cf.index:
                v = _f(cf.loc[key].iloc[0])
                if v is not None:
                    # 매입은 음수로 기록됨 → abs로 양수화
                    return round(abs(v) / market_cap * 100, 2)
        return None
    except Exception:
        return None


# ── 메인: 모든 고급 지표 계산 ─────────────────────────────────────
def compute_advanced_metrics(stock, info, df):
    """월가 스타일 펀더멘탈 지표 10개 + 4팩터 점수.

    Returns: {
        metrics: {...},
        factors: { quality, value, growth, momentum, overall },
    }
    """
    metrics = {}

    market_cap   = _f(info.get("marketCap"))
    revenue      = _f(info.get("totalRevenue"))
    fcf          = _f(info.get("freeCashflow"))
    ev           = _f(info.get("enterpriseValue"))
    ebitda       = _f(info.get("ebitda"))
    ev_ebitda    = _f(info.get("enterpriseToEbitda"))
    total_debt   = _f(info.get("totalDebt"))
    total_cash   = _f(info.get("totalCash"))

    # 1) FCF Yield + Margin
    metrics["fcf_yield"]  = round(fcf / market_cap * 100, 2) if (fcf and market_cap) else None
    metrics["fcf_margin"] = round(fcf / revenue * 100, 2)    if (fcf and revenue)    else None

    # 2) PEG
    peg          = _f(info.get("trailingPegRatio")) or _f(info.get("pegRatio"))
    metrics["peg"] = round(peg, 2) if peg is not None else None

    # 3) Margins
    gm = _f(info.get("grossMargins"))
    om = _f(info.get("operatingMargins"))
    nm = _f(info.get("profitMargins"))
    metrics["gross_margin"]     = round(gm * 100, 2) if gm is not None else None
    metrics["operating_margin"] = round(om * 100, 2) if om is not None else None
    metrics["net_margin"]       = round(nm * 100, 2) if nm is not None else None

    # 마진 YoY 변화 (financials에서 계산)
    try:
        inc = stock.financials
        if inc is not None and not inc.empty and len(inc.columns) >= 2:
            cur, prev = inc.columns[0], inc.columns[1]
            if "Total Revenue" in inc.index and "Operating Income" in inc.index:
                rev_c  = _f(inc.loc["Total Revenue"].get(cur))
                rev_p  = _f(inc.loc["Total Revenue"].get(prev))
                opi_c  = _f(inc.loc["Operating Income"].get(cur))
                opi_p  = _f(inc.loc["Operating Income"].get(prev))
                om_c = _safe_div(opi_c, rev_c); om_p = _safe_div(opi_p, rev_p)
                if om_c is not None and om_p is not None:
                    metrics["operating_margin_yoy"] = round((om_c - om_p) * 100, 2)  # pp 변화
            if "Gross Profit" in inc.index and "Total Revenue" in inc.index:
                gp_c  = _f(inc.loc["Gross Profit"].get(cur))
                gp_p  = _f(inc.loc["Gross Profit"].get(prev))
                rv_c  = _f(inc.loc["Total Revenue"].get(cur))
                rv_p  = _f(inc.loc["Total Revenue"].get(prev))
                gm_c = _safe_div(gp_c, rv_c); gm_p = _safe_div(gp_p, rv_p)
                if gm_c is not None and gm_p is not None:
                    metrics["gross_margin_yoy"] = round((gm_c - gm_p) * 100, 2)
    except Exception:
        pass

    # 4) EPS Revision
    metrics["eps_revision"] = _eps_revision_trend(stock)

    # 5) EV/EBITDA
    metrics["ev_ebitda"] = round(ev_ebitda, 2) if ev_ebitda is not None else None

    # 6) Shareholder Yield
    div_yield_raw = _f(info.get("dividendYield"))  # yfinance는 % 단위로 줌 (예: 2.5 = 2.5%)
    # 일관성 — _calc_dividend_yield의 결과를 우선 사용해야 정확하지만, info에서 직접
    div_pct = div_yield_raw if (div_yield_raw is not None and div_yield_raw > 1) else \
              (div_yield_raw * 100 if div_yield_raw else None)
    metrics["dividend_yield_pct"] = round(div_pct, 2) if div_pct is not None else None

    buyback_pct = _buyback_yield(stock, market_cap)
    metrics["buyback_yield_pct"] = buyback_pct

    if metrics["dividend_yield_pct"] is not None or buyback_pct is not None:
        sh_y = (metrics["dividend_yield_pct"] or 0) + (buyback_pct or 0)
        metrics["shareholder_yield_pct"] = round(sh_y, 2)
    else:
        metrics["shareholder_yield_pct"] = None

    # 7) Short Interest
    metrics["short_ratio"]        = _f(info.get("shortRatio"))               # 일수
    spf = _f(info.get("shortPercentOfFloat"))
    metrics["short_pct_of_float"] = round(spf * 100, 2) if (spf and spf < 1) else (round(spf, 2) if spf else None)

    # 8) Insider Activity
    metrics["insider"] = _insider_activity(stock)

    # 9) Piotroski F-Score
    metrics["f_score"] = _piotroski_f_score(stock, info)

    # 10) Net Debt / EBITDA
    if total_debt is not None and total_cash is not None and ebitda and ebitda > 0:
        metrics["net_debt_to_ebitda"] = round((total_debt - total_cash) / ebitda, 2)
    else:
        metrics["net_debt_to_ebitda"] = None

    # ── 4팩터 점수 계산 (각 0~100) ────────────────────────
    factors = _calculate_factor_scores(metrics, info, df)

    return {"metrics": metrics, "factors": factors}


# ── 점수 변환 헬퍼: 값 → 0~100 점수 ──────────────────────────────
def _score_band(v, bands, higher_better=True):
    """v를 (cutoffs, scores) bands로 변환. bands=[(threshold, score), ...]
    higher_better=False면 v가 작을수록 점수 높음.

    예: _score_band(roe_pct, [(5, 30), (10, 50), (15, 70), (20, 90), (30, 100)])
    """
    if v is None:
        return None
    sorted_bands = sorted(bands, key=lambda x: x[0])
    if not higher_better:
        # 작을수록 좋음 → 역순
        for threshold, score in sorted_bands:
            if v <= threshold:
                return score
        return sorted_bands[-1][1]
    # 클수록 좋음
    last_score = sorted_bands[0][1] - 10
    for threshold, score in sorted_bands:
        if v < threshold:
            return last_score
        last_score = score
    return sorted_bands[-1][1]


def _grade_from_score(score):
    if score is None: return "—"
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B+"
    if score >= 60: return "B"
    if score >= 50: return "C+"
    if score >= 40: return "C"
    if score >= 30: return "D"
    return "F"


def _avg_present(values):
    """None 제외 평균. 모두 None이면 None."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _calculate_factor_scores(metrics, info, df):
    """Q/V/G/M 4팩터 각각 0~100 + 종합 등급."""
    m = metrics

    # ── Quality (수익성·자본효율·재무건전성) ──────────
    roe_pct = _f(info.get("returnOnEquity"))
    if roe_pct is not None and -1 < roe_pct < 1:
        roe_pct *= 100
    roa_pct = _f(info.get("returnOnAssets"))
    if roa_pct is not None and -1 < roa_pct < 1:
        roa_pct *= 100

    q_scores = [
        _score_band(roe_pct,                  [(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
        _score_band(m.get("fcf_margin"),      [(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
        _score_band(m.get("operating_margin"),[(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
        _score_band(m.get("f_score"),         [(2, 20), (4, 40), (5, 60), (6, 75), (7, 90), (8, 100)]),
        _score_band(m.get("net_debt_to_ebitda"),
                    [(0, 100), (1, 90), (2, 75), (3, 60), (4, 40), (5, 20)], higher_better=False),
    ]

    # ── Value (밸류에이션 매력) ──────────────────────────
    pe = _f(info.get("trailingPE"))
    v_scores = [
        _score_band(pe,                  [(10, 100), (15, 90), (20, 75), (30, 60), (40, 40), (60, 20)], higher_better=False),
        _score_band(m.get("peg"),        [(0.5, 100), (1.0, 90), (1.5, 75), (2.0, 60), (3.0, 40), (5.0, 20)], higher_better=False),
        _score_band(m.get("ev_ebitda"),  [(8, 100), (12, 90), (16, 75), (20, 60), (30, 40), (50, 20)], higher_better=False),
        _score_band(m.get("fcf_yield"),  [(1, 20), (3, 50), (5, 75), (7, 90), (10, 100)]),
    ]

    # ── Growth (성장률) ───────────────────────────────────
    rev_g  = _f(info.get("revenueGrowth"))
    earn_g = _f(info.get("earningsGrowth"))
    rev_pct  = rev_g * 100 if rev_g is not None else None
    earn_pct = earn_g * 100 if earn_g is not None else None

    g_scores = [
        _score_band(rev_pct,                  [(0, 20), (5, 40), (10, 60), (20, 80), (30, 95), (50, 100)]),
        _score_band(earn_pct,                 [(0, 20), (10, 40), (20, 60), (40, 80), (60, 95), (100, 100)]),
        _score_band(m.get("gross_margin_yoy"),[(-2, 20), (-0.5, 40), (0, 60), (1, 75), (2, 90), (5, 100)]),
    ]

    # ── Momentum (가격 + 펀더 모멘텀) ────────────────────
    # 가격 모멘텀 (1M)
    mom_1m = None
    try:
        if len(df) > 22:
            mom_1m = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-22]) - 1) * 100
    except Exception:
        pass
    eps_rev = m.get("eps_revision") or {}
    rev_score_pct = eps_rev.get("score_pct")  # -100 ~ +100

    mo_scores = [
        _score_band(mom_1m,            [(-10, 10), (-5, 30), (0, 50), (5, 70), (10, 85), (20, 100)]),
        _score_band(rev_score_pct,     [(-50, 10), (-20, 30), (0, 50), (20, 70), (50, 90), (80, 100)]),
        _score_band(m.get("operating_margin_yoy"),
                                       [(-2, 20), (-0.5, 40), (0, 60), (1, 75), (2, 90), (5, 100)]),
    ]

    # 종합
    q_score  = _avg_present(q_scores)
    v_score  = _avg_present(v_scores)
    g_score  = _avg_present(g_scores)
    mo_score = _avg_present(mo_scores)
    overall  = _avg_present([q_score, v_score, g_score, mo_score])

    return {
        "quality":  {"score": round(q_score, 1)  if q_score  is not None else None,
                     "grade": _grade_from_score(q_score),
                     "components": q_scores},
        "value":    {"score": round(v_score, 1)  if v_score  is not None else None,
                     "grade": _grade_from_score(v_score),
                     "components": v_scores},
        "growth":   {"score": round(g_score, 1)  if g_score  is not None else None,
                     "grade": _grade_from_score(g_score),
                     "components": g_scores},
        "momentum": {"score": round(mo_score, 1) if mo_score is not None else None,
                     "grade": _grade_from_score(mo_score),
                     "components": mo_scores},
        "overall":  {"score": round(overall, 1)  if overall  is not None else None,
                     "grade": _grade_from_score(overall)},
    }
