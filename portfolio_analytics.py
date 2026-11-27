"""홈 '내 포트폴리오' 리스크 · 배당 진단 (계산만, 네트워크 없음 → 단위 테스트 가능).

입력
- rows: 보유 종목 [{ticker, name, currency, quantity, current_price, purchase_price}]
- fx: USD/KRW 환율 (USD 외 통화는 fx_rates 로, 없으면 원화로 보지 않고 USD 환율을 쓴다)
- profiles: {ticker: 배당·섹터 프로필} — app._dividend_profile() 이 yfinance info·배당 이력으로 만든다
    sector_name: 한글 섹터 (없으면 '기타')
    dividend_rate: 주당 연간 배당금 (종목 통화, yfinance dividendRate)
    trailing_rate: 주당 최근 12개월 배당금 (trailingAnnualDividendRate)
    yield_pct: 배당수익률 % (정규화된 값, 예: 2.4 = 2.4%)
    history: 최근 1년 배당 [(date, 주당 금액)] (배당락일 기준)
    ex_date: 다음(또는 최근) 배당락일 date | None
    price: 현재가 (보유 행에 현재가가 없을 때 대신)
"""
from __future__ import annotations

from datetime import date, timedelta

TOP_SECTORS = 5
CONCENTRATED_PCT = 40.0
UPCOMING_MAX = 6
NON_SECTORS = ("기타", "ETF")


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _pct_list(items, total):
    """[(key, value)] → 소수 1자리 % 목록, 합계를 정확히 100.0 으로 맞춘다(가장 큰 항목에 반올림 차이)."""
    if total <= 0 or not items:
        return []
    out = [[k, round(v / total * 100, 1)] for k, v in items]
    diff = round(100.0 - sum(p for _, p in out), 1)
    if diff:
        i = max(range(len(out)), key=lambda j: items[j][1])
        out[i][1] = round(out[i][1] + diff, 1)
    return out


def annual_dividend_per_share(p, price):
    """주당 연간 배당금 (종목 통화). dividendRate → 최근 12개월 이력 합 → trailingAnnualDividendRate → 배당수익률% × 현재가.
    배당수익률은 % 값(2.4 = 2.4%)으로 받아 100 으로 나눈다 → 100배 스케일 오류 방지."""
    rate = _f(p.get("dividend_rate"))
    if rate and rate > 0:
        return rate, "dividendRate"
    hist = sum(_f(a) or 0 for _, a in (p.get("history") or []))
    if hist > 0:
        return hist, "history"
    tr = _f(p.get("trailing_rate"))
    if tr and tr > 0:
        return tr, "trailing"
    y = _f(p.get("yield_pct"))
    if y and 0 < y < 30 and price:
        return price * y / 100, "yield"
    return 0.0, None


def _month_weights(p):
    """1~12월 배분 비율 (합 1). 최근 1년 이력이 있으면 그 금액 비율, 없으면 배당락 월 기준 분기(미국)·연배당(한국) 추정, 그것도 없으면 균등."""
    hist = [(d, _f(a) or 0) for d, a in (p.get("history") or []) if d]
    tot = sum(a for _, a in hist)
    w = [0.0] * 12
    if tot > 0:
        for d, a in hist:
            w[d.month - 1] += a / tot
        return w, "history"
    ex = p.get("ex_date")
    if ex:
        months = [ex.month] if p.get("annual") else [((ex.month - 1 + 3 * k) % 12) + 1 for k in range(4)]
        for m in months:
            w[m - 1] = 1 / len(months)
        return w, "annual" if p.get("annual") else "quarterly"
    return [1 / 12] * 12, "even"


def _spread(total, weights):
    """정수 원 배분: 반올림 차이를 가장 큰 달에 몰아 합계 == total."""
    vals = [round(total * w) for w in weights]
    diff = int(round(total)) - sum(vals)
    if diff:
        i = max(range(12), key=lambda j: weights[j])
        vals[i] += diff
    return vals


def _next_ex(p, today):
    """다가오는 배당락일: info 의 exDividendDate 가 오늘 이후면 그대로, 아니면 이력의 평균 간격으로 다음 예정일 추정 (400일 안)."""
    ex = p.get("ex_date")
    if ex and ex >= today:
        return ex, False
    dates = sorted(d for d, _ in (p.get("history") or []) if d)
    last = max([d for d in [ex] + dates if d], default=None)
    if not last:
        return None, False
    if len(dates) >= 2:
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 20]
        step = sorted(gaps)[len(gaps) // 2] if gaps else 365
    else:
        step = 365 if p.get("annual") else 91
    nxt = last
    for _ in range(20):
        if nxt >= today:
            break
        nxt += timedelta(days=step)
    return (nxt, True) if today <= nxt <= today + timedelta(days=400) else (None, False)


def compute(rows, fx, profiles, today=None, fx_rates=None):
    today = today or date.today()
    fx_rates = dict(fx_rates or {})
    fx_rates.setdefault("KRW", 1.0)
    fx_rates.setdefault("USD", fx)
    holdings, total = [], 0.0
    for r in rows or []:
        t = r.get("ticker")
        q = _f(r.get("quantity")) or 0
        if not t or q <= 0:
            continue
        p = profiles.get(t) or {}
        cur = (r.get("currency") or "USD").upper()
        price = _f(r.get("current_price")) or _f(p.get("price")) or _f(r.get("purchase_price")) or 0
        rate = fx_rates.get(cur, fx)
        v = price * q * rate
        holdings.append({"t": t, "name": r.get("name") or t, "cur": cur, "q": q, "price": price, "rate": rate, "v": v, "p": p})
        total += v

    empty_div = {"annual_dividend_krw": 0, "monthly_avg_krw": 0, "portfolio_yield_pct": 0.0, "months": [0] * 12,
                 "payers": 0, "basis": None, "upcoming": []}
    if not holdings or total <= 0:
        return {"count": len(holdings), "total_value_krw": round(total), "fx_usd_krw": round(fx, 2),
                "sectors": [], "max_sector_name": None, "max_sector_pct": None, "concentrated": False,
                "currencies": [], "krw_pct": None, "fx_exposure_pct": None, "dividends": empty_div}

    # ① 섹터
    by_sec = {}
    for h in holdings:
        s = (h["p"].get("sector_name") or "").strip() or "기타"
        by_sec[s] = by_sec.get(s, 0) + h["v"]
    sec_items = sorted(by_sec.items(), key=lambda x: -x[1])
    # 편중 판정은 실제 업종만: 'ETF'(여러 업종 묶음)·'기타'(섹터 모름)는 한 업종 쏠림이 아니므로 제외
    real = [(k, v) for k, v in sec_items if k not in NON_SECTORS]
    max_name, max_v = real[0] if real else (None, 0)
    if len(sec_items) > TOP_SECTORS:
        rest = sum(v for _, v in sec_items[TOP_SECTORS:])
        head = [(k, v) for k, v in sec_items[:TOP_SECTORS] if k != "기타"]
        other = rest + sum(v for k, v in sec_items[:TOP_SECTORS] if k == "기타")
        sec_items = head + [("기타", other)]
    sectors = [{"name": k, "pct": pct, "value_krw": round(dict(sec_items)[k])} for k, pct in _pct_list(sec_items, total)]
    max_pct = next((s["pct"] for s in sectors if s["name"] == max_name), round(max_v / total * 100, 1)) if max_name else None

    # ② 통화
    by_cur = {}
    for h in holdings:
        by_cur[h["cur"]] = by_cur.get(h["cur"], 0) + h["v"]
    cur_items = sorted(by_cur.items(), key=lambda x: -x[1])
    currencies = [{"currency": k, "pct": pct, "value_krw": round(by_cur[k])} for k, pct in _pct_list(cur_items, total)]
    krw_pct = next((c["pct"] for c in currencies if c["currency"] == "KRW"), 0.0)

    # ③ 배당
    months_total = [0.0] * 12
    annual_total, payers, basis, upcoming = 0.0, 0, {}, []
    for h in holdings:
        dps, src = annual_dividend_per_share(h["p"], h["price"])
        if dps <= 0:
            continue
        annual_krw = dps * h["q"] * h["rate"]
        payers += 1
        basis[h["t"]] = src
        annual_total += annual_krw
        w, _how = _month_weights(h["p"])
        for i in range(12):
            months_total[i] += annual_krw * w[i]
        nxt, est = _next_ex(h["p"], today)
        if nxt:
            hist = h["p"].get("history") or []
            per = (_f(hist[-1][1]) if hist else None) or dps / max(1, len([x for x in w if x > 0]) or 1)
            upcoming.append({"ticker": h["t"], "name": h["name"], "ex_date": nxt.isoformat(), "d_day": (nxt - today).days,
                             "estimated": est, "est_payout_krw": round(per * h["q"] * h["rate"])})
    upcoming.sort(key=lambda x: (x["d_day"], x["ticker"]))
    annual_round = round(annual_total)
    month_vals = _spread(annual_round, [m / annual_total for m in months_total]) if annual_total > 0 else [0] * 12
    return {
        "count": len(holdings), "total_value_krw": round(total), "fx_usd_krw": round(fx, 2),
        "sectors": sectors, "max_sector_name": max_name, "max_sector_pct": max_pct, "concentrated": bool(max_pct and max_pct >= CONCENTRATED_PCT),
        "currencies": currencies, "krw_pct": krw_pct, "fx_exposure_pct": round(100 - krw_pct, 1),
        "dividends": {"annual_dividend_krw": annual_round, "monthly_avg_krw": round(annual_total / 12),
                      "portfolio_yield_pct": round(annual_total / total * 100, 2), "months": month_vals,
                      "payers": payers, "basis": basis, "upcoming": upcoming[:UPCOMING_MAX]},
    }
