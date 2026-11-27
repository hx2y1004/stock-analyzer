"""미국 종목 공매도 지표 (Short Interest) — 추가 외부 호출 없이 yfinance `info` 에서만 읽는다.

Yahoo 값의 의미 (FINRA 반월 공시 기준이라 2주가량 늦다):
- shortPercentOfFloat: 유동주식 대비 공매도 잔고 비율. 보통 소수(0.184 = 18.4%), 가끔 퍼센트 그대로 온다.
- shortRatio: 공매도 잔고 ÷ 평균 거래량 = 다 갚는 데 걸리는 거래일 수 (Days to Cover).
- sharesShort / sharesShortPriorMonth: 공매도 주식 수 (이번 공시 · 전월 공시).
"""
from __future__ import annotations

import math

HIGH_PCT, HIGH_DAYS = 15.0, 5.0          # 이 이상이면 '숏 스퀴즈 주의'
ELEV_PCT, ELEV_DAYS = 8.0, 3.0           # 이 이상이면 '공매도 높음'


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_short_pct(v):
    """shortPercentOfFloat → 퍼센트(소수점 1자리). 0<v≤1 은 소수로 보고 ×100, 1<v≤100 은 그대로, 그 밖(0·음수·100 초과)은 None."""
    v = _num(v)
    if v is None or v <= 0:
        return None
    if v <= 1.0:
        return round(v * 100, 1)
    if v <= 100:
        return round(v, 1)
    return None


def short_level(short_pct, short_ratio):
    """→ (level, label)"""
    p, d = short_pct, short_ratio
    if (p is not None and p >= HIGH_PCT) or (d is not None and d >= HIGH_DAYS):
        return "high", "숏 스퀴즈 주의"
    if (p is not None and p >= ELEV_PCT) or (d is not None and d >= ELEV_DAYS):
        return "elevated", "공매도 높음"
    return "normal", "보통"


def is_us_ticker(ticker, currency=None):
    t = (ticker or "").upper()
    return not t.endswith((".KS", ".KQ")) and currency != "KRW"      # 한국 종목만 제외 (이 앱의 해외 종목 = 미국)


def short_interest(info, ticker, currency=None):
    """미국 종목이면 {short_pct, short_ratio, shares_short, shares_short_prior, mom_change_pct, level, label}, 아니면 None.
    공매도 비율·청산일수가 둘 다 없으면 None (화면에 빈 칸을 만들지 않음)."""
    if not info or not is_us_ticker(ticker, currency if currency is not None else info.get("currency")):
        return None
    pct = normalize_short_pct(info.get("shortPercentOfFloat"))
    ratio = _num(info.get("shortRatio"))
    ratio = round(ratio, 1) if ratio is not None and ratio > 0 else None
    if pct is None and ratio is None:
        return None
    cur, prev = _num(info.get("sharesShort")), _num(info.get("sharesShortPriorMonth"))
    cur = int(cur) if cur is not None and cur > 0 else None
    prev = int(prev) if prev is not None and prev > 0 else None
    mom = round((cur / prev - 1) * 100, 1) if cur and prev else None
    level, label = short_level(pct, ratio)
    return {"short_pct": pct, "short_ratio": ratio, "shares_short": cur, "shares_short_prior": prev,
            "mom_change_pct": mom, "level": level, "label": label}
