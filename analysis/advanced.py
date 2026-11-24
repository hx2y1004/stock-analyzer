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
F_SCORE_ITEMS = [
    ("roa",       "ROA > 0"),
    ("cfo",       "영업현금흐름 > 0"),
    ("d_roa",     "ROA 개선"),
    ("accrual",   "영업현금흐름 > 순이익"),
    ("d_lever",   "장기부채/총자산 감소"),
    ("d_cr",      "유동비율 개선"),
    ("no_dilute", "신주 발행 없음"),
    ("d_gm",      "매출총이익률 개선"),
    ("d_turn",    "총자산회전율 개선"),
]


def _piotroski_detail(stock, info=None):
    """Piotroski(2000) 9개 시그널. 항목별 True/False/None(데이터 없음)과 합계.

    수익성 4: ROA>0 · CFO>0 · ΔROA>0 · CFO>순이익(발생액)
    재무구조 3: Δ(장기부채/총자산)<0 (무차입 유지 포함) · Δ유동비율>0 · 신주 발행 없음
    효율성 2: Δ매출총이익률>0 · Δ총자산회전율(매출/총자산)>0
    예전 코드는 '순이익>0' 과 'ROA>0' 을 따로 세서 같은 조건을 두 번 셌고, 총자산회전율이 빠져 있었다.
    """
    try:
        bs, inc, cf = stock.balance_sheet, stock.financials, stock.cashflow
        if bs is None or inc is None or cf is None or bs.empty or inc.empty or cf.empty:
            return None
        if len(bs.columns) < 2 or len(inc.columns) < 2 or len(cf.columns) < 2:
            return None

        def _row(df, *keys):
            for k in keys:
                if k in df.index:
                    return df.loc[k]
            return None

        def _v(row, col):
            return _f(row.get(col)) if row is not None else None

        ni      = _row(inc, "Net Income", "Net Income Common Stockholders")
        rev     = _row(inc, "Total Revenue", "Revenue", "Operating Revenue")
        gross_p = _row(inc, "Gross Profit")
        ta      = _row(bs,  "Total Assets")
        ltd     = _row(bs,  "Long Term Debt", "Long Term Debt Noncurrent", "Long Term Debt And Capital Lease Obligation")
        ca      = _row(bs,  "Current Assets")
        cl      = _row(bs,  "Current Liabilities")
        shares  = _row(bs,  "Ordinary Shares Number", "Share Issued", "Common Stock Shares Issued")
        ocf     = _row(cf,  "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
        if ni is None or ta is None or ocf is None:
            return None

        cur, prev = ni.index[0], ni.index[1]
        res = {}
        roa_c = _safe_div(_v(ni, cur), _v(ta, cur))
        roa_p = _safe_div(_v(ni, prev), _v(ta, prev))
        cfo_c, ni_c = _v(ocf, cur), _v(ni, cur)
        res["roa"] = None if roa_c is None else roa_c > 0
        res["cfo"] = None if cfo_c is None else cfo_c > 0
        res["d_roa"] = None if (roa_c is None or roa_p is None) else roa_c > roa_p
        res["accrual"] = None if (cfo_c is None or ni_c is None) else cfo_c > ni_c

        # 장기부채/총자산 비율 감소. 두 해 모두 장기부채 0(무차입)이면 통과
        l_c = _v(ltd, cur) if ltd is not None else None
        l_p = _v(ltd, prev) if ltd is not None else None
        if ltd is None:
            l_c = l_p = None
        lev_c = _safe_div(l_c, _v(ta, cur)) if l_c is not None else None
        lev_p = _safe_div(l_p, _v(ta, prev)) if l_p is not None else None
        if l_c == 0 and (l_p in (0, None)):
            res["d_lever"] = True
        elif lev_c is None or lev_p is None:
            res["d_lever"] = None
        else:
            res["d_lever"] = lev_c < lev_p or (lev_c == 0 and lev_p == 0)

        cr_c = _safe_div(_v(ca, cur), _v(cl, cur)) if (ca is not None and cl is not None) else None
        cr_p = _safe_div(_v(ca, prev), _v(cl, prev)) if (ca is not None and cl is not None) else None
        res["d_cr"] = None if (cr_c is None or cr_p is None) else cr_c > cr_p

        s_c, s_p = _v(shares, cur), _v(shares, prev)
        res["no_dilute"] = None if (s_c is None or s_p is None) else s_c <= s_p * 1.01

        gm_c = _safe_div(_v(gross_p, cur), _v(rev, cur)) if (gross_p is not None and rev is not None) else None
        gm_p = _safe_div(_v(gross_p, prev), _v(rev, prev)) if (gross_p is not None and rev is not None) else None
        res["d_gm"] = None if (gm_c is None or gm_p is None) else gm_c > gm_p

        at_c = _safe_div(_v(rev, cur), _v(ta, cur)) if rev is not None else None
        at_p = _safe_div(_v(rev, prev), _v(ta, prev)) if rev is not None else None
        res["d_turn"] = None if (at_c is None or at_p is None) else at_c > at_p

        items = [{"key": k, "label": lab, "pass": res.get(k)} for k, lab in F_SCORE_ITEMS]
        return {"score": sum(1 for it in items if it["pass"] is True),
                "available": sum(1 for it in items if it["pass"] is not None),
                "items": items}
    except Exception:
        return None


def _piotroski_f_score(stock, info):
    """호환용: 합계 점수만 (0~9). 데이터 부족 시 None."""
    d = _piotroski_detail(stock, info)
    return d["score"] if d else None


def _interest_coverage(stock):
    """이자보상배율 = 영업이익 / 이자비용 (최근 연도). → (배율, 무차입 여부)."""
    try:
        inc = stock.financials
        if inc is None or inc.empty:
            return None, False
        col = inc.columns[0]
        ebit = None
        for k in ("Operating Income", "EBIT", "Total Operating Income As Reported"):
            if k in inc.index:
                ebit = _f(inc.loc[k].get(col))
                if ebit is not None:
                    break
        intr = None
        for k in ("Interest Expense", "Interest Expense Non Operating"):
            if k in inc.index:
                intr = _f(inc.loc[k].get(col))
                if intr is not None:
                    break
        if ebit is None:
            return None, False
        if intr is None:
            return None, False                 # 이자비용을 따로 공시하지 않음 (예: 최근 Apple) → 알 수 없음
        if abs(intr) < 1e-9:
            return None, True                  # 이자비용 0 → 사실상 무차입
        return round(ebit / abs(intr), 1), False
    except Exception:
        return None, False


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
        # Yahoo 열 이름은 downLast7Days (D 대문자). 예전 표기도 함께 확인
        down7  = _f(row.get("downLast7Days", row.get("downLast7days"))) or 0
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
    """최근 연간 자사주 매입 / 시가총액(%) → (매입 수익률, 순발행 희석률).

    현금흐름표 부호: 매입(현금 유출)은 음수, 발행(현금 유입)은 양수.
    - 'Repurchase …' 행: 음수일 때만 매입으로 인정 (abs)
    - 'Net Common Stock Issuance'(순발행) 폴백: 음수 = 순매입 → 인정, 양수 = 순발행(유상증자·스톡옵션 희석) → 매입 0 + 희석률
    예전에는 순발행 행도 abs() 를 씌워 유상증자 기업이 자사주 매입 가점을 받았다.
    """
    try:
        if not market_cap:
            return None, None
        cf = stock.cashflow
        if cf is None or cf.empty:
            return None, None

        def _latest(key):
            if key in cf.index:
                return _f(cf.loc[key].iloc[0])
            return None

        net_iss = _latest("Net Common Stock Issuance")
        dilution = round(net_iss / market_cap * 100, 2) if (net_iss is not None and net_iss > 0) else None
        for key in ["Repurchase Of Capital Stock", "Common Stock Repurchased", "Repurchase Of Common Stock"]:
            v = _latest(key)
            if v is not None:
                bb = round(-v / market_cap * 100, 2) if v < 0 else 0.0
                return bb, dilution
        if net_iss is not None:
            if net_iss < 0:
                return round(-net_iss / market_cap * 100, 2), None
            return 0.0, dilution
        return None, None
    except Exception:
        return None, None


def _dividend_pct(info, price=None):
    """배당수익률(%). 최신 yfinance 의 dividendYield 는 이미 % 단위(0.5 = 0.5%).

    1) 네이버 DPS/가격(소수) 2) 주당 배당금/가격 으로 교차 검증해 dividendYield 가 % 인지 소수인지 고른다.
    예전에는 dividendYield ≤ 1 이면 ×100 해서 0.5% 저배당주가 50% 로 뻥튀기됐다.
    """
    naver = _f(info.get("_naver_div_yield"))
    if naver:
        return naver * 100
    price = price or _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
    ref = None
    for k in ("dividendRate", "trailingAnnualDividendRate"):
        rate = _f(info.get(k))
        if rate and rate > 0 and price and price > 0:
            ref = rate / price * 100
            break
    raw = _f(info.get("dividendYield"))
    if raw is None:
        return ref
    if raw <= 0:
        return 0.0 if ref is None else ref
    if ref is not None:
        # % 로 본 값과 소수로 본 값 중 실제 배당금/가격에 가까운 쪽
        return raw if abs(raw - ref) <= abs(raw * 100 - ref) else raw * 100
    return raw


_FIN_INDUSTRY_KEYS = ("bank", "insurance", "insurer", "capital markets", "mortgage finance")


def is_financial(info):
    """은행·보험·증권 여부 (유동비율·EBITDA·FCF 가 의미 없는 업종).

    카드·결제(Credit Services: Visa 등)·거래소·금융 데이터 회사는 일반 기업처럼 FCF 가 있어 제외한다.
    """
    industry = str(info.get("industry") or "").lower()
    return any(k in industry for k in _FIN_INDUSTRY_KEYS)


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
    try:
        last_px = float(df["Close"].iloc[-1]) if df is not None and len(df) else None
    except Exception:
        last_px = None
    div_pct = _dividend_pct(info, last_px)
    metrics["dividend_yield_pct"] = round(div_pct, 2) if div_pct is not None else None

    buyback_pct, dilution_pct = _buyback_yield(stock, market_cap)
    metrics["buyback_yield_pct"] = buyback_pct
    metrics["dilution_pct"] = dilution_pct          # 순발행(희석) / 시가총액 %, 없으면 None

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

    # 9) Piotroski F-Score (항목별 결과 포함)
    # 금융업은 유동비율·매출총이익률이 정의되지 않아 F-Score 를 계산하지 않는다
    fdet = None if is_financial(info) else _piotroski_detail(stock, info)
    metrics["f_score"] = fdet["score"] if fdet else None
    metrics["f_score_detail"] = fdet

    # 9-b) 이자보상배율 · 부채비율 · 유동비율 · ROE (재무 건전성 표)
    icr, no_interest = _interest_coverage(stock)
    metrics["interest_coverage"] = icr
    metrics["no_interest_expense"] = no_interest
    de = _f(info.get("debtToEquity"))               # yfinance: % 값 (150 = 150%)
    metrics["debt_to_equity_pct"] = round(de, 1) if de is not None else None
    cr = _f(info.get("currentRatio"))
    metrics["current_ratio"] = round(cr, 2) if cr is not None else None
    roe = _f(info.get("returnOnEquity"))            # yfinance: 소수 (0.15 = 15%, 1.5 = 150%)
    metrics["roe_pct"] = round(roe * 100, 1) if roe is not None else None
    per = _f(info.get("trailingPE"))
    metrics["per"] = round(per, 1) if per is not None else None
    rg, eg = _f(info.get("revenueGrowth")), _f(info.get("earningsGrowth"))
    metrics["revenue_growth_pct"] = round(rg * 100, 1) if rg is not None else None
    metrics["earnings_growth_pct"] = round(eg * 100, 1) if eg is not None else None
    pb = _f(info.get("priceToBook"))
    metrics["pbr"] = round(pb, 2) if pb is not None else None
    metrics["is_financial"] = is_financial(info)

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


def _avg_present(values, expected=None):
    """None 제외 평균. 항목이 절반도 안 되면 50(중립) 쪽으로 당긴다.

    예전에는 5개 중 1개만 있어도 그 1개가 100점이면 팩터 전체가 100점이 됐다.
    present < expected/2 → 가중치 w = present / (expected/2) 로 50 과 섞는다.
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    n = expected or len(values)
    half = n / 2.0
    if half > 0 and len(vals) < half:
        w = len(vals) / half
        avg = avg * w + 50 * (1 - w)
    return avg


def _value_band(v, bands, negative_score=15):
    """낮을수록 좋은 배수(PER·EV/EBITDA). 0 이하(적자·음수 EBITDA)는 만점이 아니라 낮은 점수."""
    if v is None:
        return None
    if v <= 0:
        return negative_score
    return _score_band(v, bands, higher_better=False)


def _calculate_factor_scores(metrics, info, df):
    """Q/V/G/M 4팩터 각각 0~100 + 종합 등급."""
    m = metrics
    fin = bool(m.get("is_financial"))

    # ── Quality (수익성·자본효율·재무건전성) ──────────
    # yfinance returnOnEquity/returnOnAssets 는 항상 소수 → 무조건 ×100 (예전 '-1<x<1 일 때만' 은 ROE 150% 를 1.5% 로 만들었다)
    roe_pct = _f(info.get("returnOnEquity"))
    roe_pct = roe_pct * 100 if roe_pct is not None else None
    roa_pct = _f(info.get("returnOnAssets"))
    roa_pct = roa_pct * 100 if roa_pct is not None else None

    icr = m.get("interest_coverage")
    icr_score = 100 if (icr is None and m.get("no_interest_expense")) else \
        _score_band(icr, [(1, 10), (1.5, 25), (3, 45), (5, 60), (8, 75), (12, 90), (20, 100)])

    if fin:
        # 금융업: 유동비율·EBITDA·FCF 가 정의되지 않음 → ROE·ROA 중심
        q_scores = [
            _score_band(roe_pct, [(0, 20), (5, 40), (8, 55), (10, 65), (12, 75), (15, 90), (20, 100)]),
            _score_band(roa_pct, [(0, 20), (0.3, 35), (0.6, 50), (0.9, 65), (1.2, 80), (1.5, 90), (2, 100)]),
        ]
    else:
        q_scores = [
            _score_band(roe_pct,                  [(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
            _score_band(m.get("fcf_margin"),      [(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
            _score_band(m.get("operating_margin"),[(0, 20), (5, 40), (10, 60), (15, 75), (20, 90), (30, 100)]),
            _score_band(m.get("f_score"),         [(2, 20), (4, 40), (5, 60), (6, 75), (7, 90), (8, 100)]),
            _score_band(m.get("net_debt_to_ebitda"),
                        [(0, 100), (1, 90), (2, 75), (3, 60), (4, 40), (5, 20)], higher_better=False),
            icr_score,
        ]

    # ── Value (밸류에이션 매력) ──────────────────────────
    pe = _f(info.get("trailingPE"))
    if fin:
        v_scores = [
            _value_band(pe, [(8, 100), (10, 90), (13, 75), (16, 60), (20, 40), (30, 20)]),
            _value_band(m.get("pbr"), [(0.5, 100), (0.8, 85), (1.0, 70), (1.5, 55), (2.5, 35), (4, 20)]),
            _score_band(m.get("dividend_yield_pct"), [(1, 30), (2, 50), (3, 70), (4.5, 85), (6, 100)]),
        ]
    else:
        peg = m.get("peg")
        v_scores = [
            _value_band(pe,               [(10, 100), (15, 90), (20, 75), (30, 60), (40, 40), (60, 20)]),
            # PEG 음수 = 이익 역성장 또는 적자 → 의미 없는 값이라 제외
            _score_band(peg, [(0.5, 100), (1.0, 90), (1.5, 75), (2.0, 60), (3.0, 40), (5.0, 20)], higher_better=False)
                if (peg is not None and peg > 0) else None,
            _value_band(m.get("ev_ebitda"), [(8, 100), (12, 90), (16, 75), (20, 60), (30, 40), (50, 20)]),
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
        None if fin else _score_band(m.get("gross_margin_yoy"), [(-2, 20), (-0.5, 40), (0, 60), (1, 75), (2, 90), (5, 100)]),
    ]

    # ── Momentum (가격 + 펀더 모멘텀) ────────────────────
    mom_1m = mom_6m = None
    try:
        if len(df) > 22:
            mom_1m = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-22]) - 1) * 100
        if len(df) > 126:
            mom_6m = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-127]) - 1) * 100
    except Exception:
        pass
    m["return_6m_pct"] = round(mom_6m, 1) if mom_6m is not None else None
    eps_rev = m.get("eps_revision") or {}
    rev_score_pct = eps_rev.get("score_pct")  # -100 ~ +100

    mo_scores = [
        _score_band(mom_1m,            [(-10, 10), (-5, 30), (0, 50), (5, 70), (10, 85), (20, 100)]),
        _score_band(mom_6m,            [(-20, 10), (-10, 30), (0, 50), (10, 70), (25, 85), (50, 100)]),
        _score_band(rev_score_pct,     [(-50, 10), (-20, 30), (0, 50), (20, 70), (50, 90), (80, 100)]),
        None if fin else _score_band(m.get("operating_margin_yoy"),
                                     [(-2, 20), (-0.5, 40), (0, 60), (1, 75), (2, 90), (5, 100)]),
    ]
    if fin:
        mo_scores = mo_scores[:3]

    # 종합
    q_score  = _avg_present(q_scores)
    v_score  = _avg_present(v_scores)
    g_score  = _avg_present([x for x in g_scores if not (fin and x is None)] if fin else g_scores)
    mo_score = _avg_present(mo_scores)
    overall  = _avg_present([q_score, v_score, g_score, mo_score])

    def _pack(sc, comps):
        return {"score": round(sc, 1) if sc is not None else None, "grade": _grade_from_score(sc),
                "components": comps, "coverage": f"{sum(1 for c in comps if c is not None)}/{len(comps)}"}

    return {
        "quality":  _pack(q_score, q_scores),
        "value":    _pack(v_score, v_scores),
        "growth":   _pack(g_score, g_scores),
        "momentum": _pack(mo_score, mo_scores),
        "overall":  {"score": round(overall, 1)  if overall  is not None else None,
                     "grade": _grade_from_score(overall)},
        "is_financial": fin,
    }
