"""추세 셋업 점수 계산 v3 (월가 정통 로직, 일봉을 받아 계산만 한다).

네트워크 호출 없이 1년 일봉(DataFrame: Open·High·Low·Close·Volume)과 시장 환경, 스크리너 펀더멘탈
점수만으로 점수·태그·돌파 기준가를 낸다. 일봉은 AI 봇 장 마감 배치가 이미 받은 것을 그대로 쓴다
(trend_setups.on_batch). 저장·장중 갱신은 trend_setups.py 담당.

점수 체계 (총 100점):
  기술 35:
    - Minervini Stage 2 정의 (15pt) — 5단계 체크, 항목당 3pt
    - BB 수축+상단돌파 (8pt, 주봉)
    - OBV / 거래대금 (5pt)
    - 베이스 패턴 돌파 (7pt)
  모멘텀 40:
    - 다중 시간프레임 (1m+3m+6m, 15pt)
    - 상대 강도 RS (시장 대비 3개월, 15pt)
    - 52주 신고가 + 연속 (10pt)
  펀더멘털 25 (스크리너 스냅샷의 4팩터 점수, 7일 순환 갱신값):
    - 종합 펀더멘탈 점수 (15pt)
    - 성장성 (5pt) · 수익성 (5pt)

조정:
  - 변동성 페널티: ATR/Close > 5% → -10%
  - 약세장 페널티: 시장이 MA200 아래 → -30%
  - Perfect Setup 보너스: 핵심 신호 3+ 동시 → +15%

태그: 점수가 들어간 항목은 부분 점수라도 태그를 남겨, 점수는 있는데 태그가 비는 일이 없게 한다
(예전엔 Stage2 3/5, 1개월만 +5% 같은 부분 점수에는 태그가 없었다).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

NEAR_HIGH = 0.97        # 52주 고점 -3% 이내 → 장중 돌파 후보
PIVOT_LOOKBACK = 20     # BB 수축 후보의 돌파 기준 = 최근 20거래일 고가


def _is_korean(symbol: str) -> bool:
    return symbol.endswith(".KS") or symbol.endswith(".KQ")


def _f(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ── 시장 환경 ────────────────────────────────────────────
def market_context_from_df(df) -> dict:
    """지수 1년 일봉 → {above_ma200, market_return_3m}. 데이터가 모자라면 {}."""
    try:
        if df is None or df.empty or len(df) < 200:
            return {}
        c = df["Close"].dropna()
        curr = float(c.iloc[-1])
        ma200 = float(c.iloc[-200:].mean())
        ret_3m = (curr / float(c.iloc[-63]) - 1) * 100 if len(c) >= 63 and c.iloc[-63] > 0 else 0.0
        return {"above_ma200": curr > ma200, "market_return_3m": round(ret_3m, 2)}
    except Exception as e:
        log.warning(f"market context: {e}")
        return {}


def fetch_market_context(download=None) -> dict:
    """미국/한국 시장 환경 (S&P500, KOSPI). download(tickers, period=...) 를 넘기면 그걸로 받는다."""
    ctx = {"us": {}, "kr": {}}
    for label, sym in (("us", "^GSPC"), ("kr", "^KS11")):
        try:
            if download:
                df = download([sym], period="1y", chunk=1, pause=0, min_rows=60).get(sym)
            else:
                import yfinance as yf
                df = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=False)
            ctx[label] = market_context_from_df(df)
        except Exception as e:
            log.warning(f"market_context {sym}: {e}")
    return ctx


# ── 단일 종목 점수 ───────────────────────────────────────
def score_df(symbol: str, name: str, df_d, market_ctx: dict = None, fund: dict = None) -> Optional[dict]:
    """1년 일봉 → 점수·태그·셋업 플래그·돌파 기준가. 데이터가 모자라면 None."""
    try:
        if df_d is None or df_d.empty or len(df_d) < 60:
            return None
        df_d = df_d[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df_d) < 60:
            return None
        df_w = df_d.resample("W").agg({"Open": "first", "High": "max", "Low": "min",
                                       "Close": "last", "Volume": "sum"}).dropna()
        if len(df_w) < 26:
            return None
        close, high, low, vol = df_d["Close"], df_d["High"], df_d["Low"], df_d["Volume"]
        last_d = _f(close.iloc[-1])
        if not last_d or last_d <= 0:
            return None

        signals = []          # 핵심·주요 태그 (표시 우선)
        minor = []            # (점수, 태그) 부분 점수 태그 — 주요 태그가 2개 미만일 때 채운다
        tech = momentum = fund_pts = 0
        core = 0
        mkey = "kr" if _is_korean(symbol) else "us"
        mctx = (market_ctx or {}).get(mkey) or {}

        # ═══ 기술적 (max 35) ═══
        # 1) Minervini Stage 2 (15pt)
        s2 = 0
        if len(df_d) >= 200:
            ma50_s = close.rolling(50).mean()
            ma150 = _f(close.rolling(150).mean().iloc[-1])
            ma200_s = close.rolling(200).mean()
            ma50, ma200 = _f(ma50_s.iloc[-1]), _f(ma200_s.iloc[-1])
            if ma50 and ma150 and ma200:
                if ma50 > ma150 > ma200:
                    s2 += 1
                ma200_1mo = _f(ma200_s.iloc[-22])
                if ma200_1mo and ma200 > ma200_1mo:
                    s2 += 1
                if last_d > ma50 > ma150:
                    s2 += 1
                ylow = _f(low.min())
                if ylow and last_d >= ylow * 1.30:
                    s2 += 1
                yhigh = _f(high.max())
                if yhigh and last_d >= yhigh * 0.75:
                    s2 += 1
        tech += s2 * 3
        stage2 = s2 >= 4
        if stage2:
            signals.append(f"Stage2 ({s2}/5)")
            core += 1
        elif s2 >= 2:
            minor.append((s2 * 3, f"Stage2 근접 ({s2}/5)"))

        # 2) BB 수축 + 상단 돌파 (8pt, 주봉)
        mid_w = df_w["Close"].rolling(20).mean()
        std_w = df_w["Close"].rolling(20).std()
        upper_w = mid_w + 2 * std_w
        width_w = (4 * std_w) / mid_w
        bb_squeeze = False
        if pd.notna(width_w.iloc[-1]):
            recent, past = width_w.iloc[-3:].mean(), width_w.iloc[-20:-3].mean()
            if pd.notna(past) and past > 0 and recent < past * 0.75:
                tech += 4
                bb_squeeze = True
                signals.append("BB 수축")
        if pd.notna(upper_w.iloc[-1]) and df_w["Close"].iloc[-1] > upper_w.iloc[-1]:
            tech += 4
            signals.append("BB 상단 돌파")
            core += 1

        # 3) OBV / 거래대금 (5pt)
        try:
            obv = (vol * np.sign(close.diff().fillna(0))).cumsum()
            if obv.iloc[-1] >= obv.iloc[-60:].max() * 0.99:
                tech += 3
                signals.append("OBV 신고가")
        except Exception:
            pass
        try:
            tv = vol * close
            tv_curr, tv_avg = float(tv.iloc[-5:].mean()), float(tv.iloc[-60:-5].mean())
            if tv_avg > 0 and tv_curr > tv_avg * 1.5:
                tech += 2
                signals.append(f"거래대금 +{int(tv_curr / tv_avg * 100 - 100)}%")
        except Exception:
            pass

        # 4) 베이스 패턴 돌파 (7pt): 최근 8주 변동폭 < 15% → 상단 돌파
        try:
            last8w = df_w.tail(8)
            if len(last8w) >= 5:
                mean_p = float(last8w["Close"].mean())
                if mean_p > 0:
                    rng = (float(last8w["High"].max()) - float(last8w["Low"].min())) / mean_p
                    if rng < 0.15:
                        if last_d > float(last8w["High"].iloc[:-1].max()):
                            tech += 7
                            signals.append("베이스 돌파")
                            core += 1
                        else:
                            tech += 3
                            signals.append("베이스 형성")
        except Exception:
            pass

        # ═══ 모멘텀 (max 40) ═══
        # 1) 다중 시간프레임 (15pt)
        ret = {}
        for label, n, thr in (("1개월", 22, 5), ("3개월", 63, 10), ("6개월", 126, 20)):
            if len(df_d) >= n and close.iloc[-n] > 0:
                r = (last_d / float(close.iloc[-n]) - 1) * 100
                ret[label] = r
                if r >= thr:
                    momentum += 5
                    minor.append((5, f"{label} +{r:.0f}%"))
        r1, r3, r6 = ret.get("1개월", 0.0), ret.get("3개월", 0.0), ret.get("6개월", 0.0)
        if r1 > 5 and r3 > 10 and r6 > 20:
            signals.append(f"다중 모멘텀 1m+{r1:.0f}% 6m+{r6:.0f}%")
            minor = [m for m in minor if not m[1].startswith(("1개월", "3개월", "6개월"))]
            core += 1

        # 2) 상대 강도 RS (15pt)
        m3 = mctx.get("market_return_3m")
        if m3 is not None and "3개월" in ret:
            rs = r3 - m3
            if rs >= 30:
                momentum += 15
                signals.append(f"RS 강세 +{rs:.0f}%p")
                core += 1
            elif rs >= 15:
                momentum += 10
                signals.append(f"RS +{rs:.0f}%p")
            elif rs >= 5:
                momentum += 5
                minor.append((5, f"시장 대비 +{rs:.0f}%p"))
            elif rs >= 0:
                momentum += 2
                minor.append((2, "시장 대비 우위"))

        # 3) 52주 신고가 + 연속 (10pt)
        high52 = _f(high.iloc[-252:].max())
        new_high = bool(high52 and last_d >= high52 * 0.99)
        near_high = bool(high52 and last_d >= high52 * NEAR_HIGH)
        if new_high:
            momentum += 7
            signals.append("52주 신고가")
            roll_high = high.rolling(252, min_periods=180).max()
            tail, tail_h = close.iloc[-5:], roll_high.iloc[-5:]
            valid = tail_h.notna()
            if valid.any() and int((tail[valid] >= tail_h[valid] * 0.99).sum()) >= 3:
                momentum += 3
                signals.append("연속 신고가")
                core += 1
        elif near_high:
            minor.append((1, f"52주 고점 -{(1 - last_d / high52) * 100:.1f}%"))

        # ═══ 펀더멘털 (max 25, 스크리너 4팩터) ═══
        fund = fund or {}
        fs, gs, qs = _f(fund.get("fundamental_score")), _f(fund.get("growth_score")), _f(fund.get("quality_score"))
        if fs is not None:
            p = int(round(max(0.0, min(100.0, fs)) * 0.15))
            fund_pts += p
            if fs >= 70:
                signals.append(f"펀더멘탈 {fs:.0f}점")
            elif p:
                minor.append((p, f"펀더멘탈 {fs:.0f}점"))
        if gs is not None and gs >= 70:
            fund_pts += 5
            signals.append(f"성장성 {gs:.0f}점")
            if gs >= 85:
                core += 1
        if qs is not None and qs >= 70:
            fund_pts += 5
            signals.append(f"수익성 {qs:.0f}점")

        # ═══ 조정 ═══
        warns = []
        try:
            atr = _f((high - low).rolling(14).mean().iloc[-1])
            if atr and atr / last_d > 0.05:
                pen = round((tech + momentum + fund_pts) * 0.10) // 3
                tech, momentum, fund_pts = max(0, tech - pen), max(0, momentum - pen), max(0, fund_pts - pen)
                warns.append(f"⚠변동성 {atr / last_d * 100:.1f}%")
        except Exception:
            pass
        if mctx.get("above_ma200") is False:
            tech, momentum, fund_pts = round(tech * 0.7), round(momentum * 0.7), round(fund_pts * 0.7)
            warns.append("⚠ 약세장")
        perfect = core >= 3
        if perfect:
            tech, momentum, fund_pts = round(tech * 1.15), round(momentum * 1.15), round(fund_pts * 1.15)
            signals.insert(0, "🔥 Perfect Setup")
        total = int(tech + momentum + fund_pts)

        # 태그 보장: 점수가 있으면 주요 태그가 최소 2개가 되도록 부분 점수 태그로 채운다
        if total > 0 and len(signals) < 2 and minor:
            minor.sort(key=lambda m: -m[0])
            for _, tag in minor:
                if len(signals) >= 2:
                    break
                if tag not in signals:
                    signals.append(tag)
        if total > 0 and not signals:
            signals.append("추세 초기")
        tags = signals[:5] + warns[:1]

        # 돌파 기준가: 52주 고점 -3% 이내면 52주 고점, BB 수축이면 최근 20거래일 고가
        pivot = kind = None
        if near_high:
            pivot, kind = high52, "52주 고점"
        elif bb_squeeze:
            pivot, kind = _f(high.iloc[-PIVOT_LOOKBACK:].max()), "20일 고점"

        change_pct = 0.0
        if len(close) >= 2 and close.iloc[-2] > 0:
            change_pct = round((last_d / float(close.iloc[-2]) - 1) * 100, 2)

        sparkline = []
        closes = [float(c) for c in close.tail(30).tolist()]
        if len(closes) >= 5:
            mn, mx = min(closes), max(closes)
            sparkline = [round((c - mn) / (mx - mn), 3) for c in closes] if mx > mn else [0.5] * len(closes)

        return {
            "ticker": symbol, "name": name, "price": last_d, "change_pct": change_pct,
            "currency": "KRW" if _is_korean(symbol) else "USD",
            "tech_score": int(tech), "momentum_score": int(momentum), "fund_score": int(fund_pts),
            "total_score": total, "signals": tags, "reason": " · ".join(tags[:4]),
            "sparkline": sparkline, "stage2": stage2, "perfect": perfect,
            "new_high": new_high, "near_high": near_high, "bb_squeeze": bb_squeeze,
            "pivot": round(pivot, 4) if pivot else None, "pivot_kind": kind, "high52": high52,
            "avg_volume20": _f(vol.iloc[-20:].mean()), "base_date": df_d.index[-1].date(),
        }
    except Exception as e:
        log.debug(f"score_df({symbol}) failed: {e}")
        return None


def analyze_uptrend(symbol: str, name: str, with_fundamental: bool = False,
                    market_ctx: dict = None, fund: dict = None) -> Optional[dict]:
    """(호환용) 종목 1개를 직접 받아 점수 계산. 배치는 score_df 를 쓴다."""
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return score_df(symbol, name, df, market_ctx, fund)
    except Exception as e:
        log.debug(f"analyze_uptrend({symbol}) failed: {e}")
        return None
