"""추세 상승 종목 스캐너 v2 (월가 정통 로직).

점수 체계 (총 100점):
  기술 35:
    - Minervini Stage 2 정의 (15pt) — 5단계 체크
    - BB 수축+상단돌파 (8pt)
    - OBV / 거래대금 (5pt)
    - 베이스 패턴 돌파 (7pt)

  모멘텀 40:
    - 다중 시간프레임 (1m+3m+6m, 15pt)
    - 상대 강도 RS (시장 대비, 15pt)
    - 52주 신고가 + 연속 (10pt)

  펀더멘털 25:
    - 매출 YoY 가속 (8pt)
    - EPS YoY 가속 (7pt)
    - 이익률 (5pt)
    - 컨센서스 상향 (5pt)

조정:
  - 변동성 페널티: ATR/Close > 5% → -10%
  - 약세장 페널티: 시장이 MA200 아래 → -30%
  - Perfect Setup 보너스: 핵심 신호 3+ 동시 → +15%
"""
from __future__ import annotations

import logging
import time
import threading
import random
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
from datetime import datetime
from typing import Optional

import yfinance as yf
import pandas as pd

log = logging.getLogger(__name__)

# yfinance 기본 동작 사용 (커스텀 세션이 Yahoo 봇 차단 유발 가능성)
# Future timeout 으로 행 방지

# ── 캐시/상태 (모듈 전역) ─────────────────────────────
_LOCK    = threading.Lock()
_CACHE   = {}    # market → (ts, result_dict)
_STATUS  = {}    # market → {state, started_at, progress, total, hits, message?}
_ABORT   = {}    # market → True if user requested abort
_CACHE_TTL = 30 * 60   # 30분


# ── 헬퍼 ──────────────────────────────────────────────
def _is_korean(symbol: str) -> bool:
    return symbol.endswith(".KS") or symbol.endswith(".KQ")


def _safe(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


# ── 시장 환경 컨텍스트 (스캔 1회 fetch) ─────────────────────
def fetch_market_context() -> dict:
    """미국/한국 시장 환경: MA200 위치 + 3개월 수익률."""
    ctx = {"us": {}, "kr": {}}
    for label, sym in [("us", "^GSPC"), ("kr", "^KS11")]:
        try:
            df = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=False)
            if df is None or df.empty or len(df) < 200:
                continue
            curr = float(df["Close"].iloc[-1])
            ma200 = float(df["Close"].iloc[-200:].mean())
            ret_3m = 0.0
            if len(df) >= 63:
                prev = float(df["Close"].iloc[-63])
                if prev > 0:
                    ret_3m = (curr / prev - 1) * 100
            ctx[label] = {
                "above_ma200":    curr > ma200,
                "market_return_3m": round(ret_3m, 2),
            }
        except Exception as e:
            log.warning(f"market_context {sym}: {e}")
    log.info(f"[market context] {ctx}")
    return ctx


# ── 단일 종목 분석 v2 ─────────────────────────────────
def analyze_uptrend(symbol: str, name: str, with_fundamental: bool = False,
                    market_ctx: dict = None) -> Optional[dict]:
    """추세 상승 점수 계산 v2 (월가 정통 로직, max 100pt)."""
    try:
        time.sleep(random.uniform(0.02, 0.10))
        stock = yf.Ticker(symbol)

        # 1년 일봉 (Stage2 / 다중모멘텀에 충분한 데이터 필요)
        df_d = stock.history(period="1y", interval="1d", auto_adjust=False)
        if df_d is None or df_d.empty:
            return None
        if len(df_d) < 60:
            return None

        # 주봉
        df_w = df_d.resample("W").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()
        if len(df_w) < 26:
            return None

        signals = []
        tech = momentum = fund = 0
        core_signal_count = 0   # 핵심 신호 카운터 (Perfect Setup 보너스용)

        last_d = float(df_d["Close"].iloc[-1])
        if pd.isna(last_d):
            return None

        # ═════════════════════════════════════════════
        # 기술적 (max 35)
        # ═════════════════════════════════════════════

        # ── 1) Minervini Stage 2 정의 (15pt) ──
        # 5단계 체크. 각 항목 3pt, 4/5 이상 통과 시 핵심 신호 카운트
        s2 = 0
        # (1) MA50 > MA150 > MA200 정배열
        if len(df_d) >= 200:
            ma50  = float(df_d["Close"].rolling(50).mean().iloc[-1])
            ma150 = float(df_d["Close"].rolling(150).mean().iloc[-1])
            ma200 = float(df_d["Close"].rolling(200).mean().iloc[-1])
            if not (pd.isna(ma50) or pd.isna(ma150) or pd.isna(ma200)):
                if ma50 > ma150 > ma200:
                    s2 += 1
                # (2) MA200 1개월 전 대비 상승
                ma200_1mo = float(df_d["Close"].rolling(200).mean().iloc[-22])
                if not pd.isna(ma200_1mo) and ma200 > ma200_1mo:
                    s2 += 1
                # (3) 현재가 > MA50 > MA150
                if last_d > ma50 > ma150:
                    s2 += 1
                # (4) 52주 저점 +30% 이상
                year_low = float(df_d["Low"].min())
                if year_low > 0 and last_d >= year_low * 1.30:
                    s2 += 1
                # (5) 52주 고점 -25% 이내
                year_high = float(df_d["High"].max())
                if year_high > 0 and last_d >= year_high * 0.75:
                    s2 += 1
        tech += s2 * 3
        if s2 >= 4:
            signals.append(f"Stage2 ({s2}/5)")
            core_signal_count += 1

        # ── 2) BB 수축 + 상단 돌파 (8pt) ──
        bb_mid_w   = df_w["Close"].rolling(20).mean()
        bb_std_w   = df_w["Close"].rolling(20).std()
        bb_upper_w = bb_mid_w + 2 * bb_std_w
        bb_width_w = (bb_upper_w - (bb_mid_w - 2 * bb_std_w)) / bb_mid_w
        if not pd.isna(bb_width_w.iloc[-1]):
            recent = bb_width_w.iloc[-3:].mean()
            past   = bb_width_w.iloc[-20:-3].mean()
            if not pd.isna(past) and past > 0 and recent < past * 0.75:
                tech += 4
                signals.append("BB 수축")
        if not pd.isna(bb_upper_w.iloc[-1]) and df_w["Close"].iloc[-1] > bb_upper_w.iloc[-1]:
            tech += 4
            signals.append("BB 상단 돌파")
            core_signal_count += 1

        # ── 3) OBV / 거래대금 (5pt) ──
        # OBV: 가격 상승일 거래량 +, 하락일 -
        try:
            close_diff = df_d["Close"].diff()
            obv = (df_d["Volume"] * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            obv_60_max = obv.iloc[-60:].max()
            if obv.iloc[-1] >= obv_60_max * 0.99:
                tech += 3
                signals.append("OBV 신고가")
        except Exception:
            pass
        # 거래대금: 거래량 × 종가
        try:
            tv      = df_d["Volume"] * df_d["Close"]
            tv_curr = float(tv.iloc[-5:].mean())
            tv_avg  = float(tv.iloc[-60:-5].mean())
            if tv_avg > 0 and tv_curr > tv_avg * 1.5:
                tech += 2
                signals.append(f"거래대금 +{int(tv_curr/tv_avg*100-100)}%")
        except Exception:
            pass

        # ── 4) 베이스 패턴 돌파 (7pt) ──
        # 단순 정의: 최근 8주 동안 변동폭 < 15% (베이스 형성) → 그 후 상단 돌파
        try:
            last8w = df_w.tail(8)
            if len(last8w) >= 5:
                mean_p = float(last8w["Close"].mean())
                if mean_p > 0:
                    base_range = (float(last8w["High"].max()) - float(last8w["Low"].min())) / mean_p
                    if base_range < 0.15:
                        # 베이스 형성됨 - 돌파 확인
                        base_top = float(last8w["High"].iloc[:-1].max())
                        if last_d > base_top:
                            tech += 7
                            signals.append("베이스 돌파")
                            core_signal_count += 1
                        else:
                            tech += 3   # 베이스 형성 자체도 가산
                            signals.append("베이스 형성")
        except Exception:
            pass

        # ═════════════════════════════════════════════
        # 모멘텀 (max 40)
        # ═════════════════════════════════════════════

        # ── 1) 다중 시간프레임 모멘텀 (15pt) ──
        ret_1m = ret_3m = ret_6m = 0.0
        if len(df_d) >= 22:
            ret_1m = (last_d / float(df_d["Close"].iloc[-22]) - 1) * 100
            if ret_1m >= 5:  momentum += 5
        if len(df_d) >= 63:
            ret_3m = (last_d / float(df_d["Close"].iloc[-63]) - 1) * 100
            if ret_3m >= 10: momentum += 5
        if len(df_d) >= 126:
            ret_6m = (last_d / float(df_d["Close"].iloc[-126]) - 1) * 100
            if ret_6m >= 20: momentum += 5
        if ret_1m > 5 and ret_3m > 10 and ret_6m > 20:
            signals.append(f"다중 모멘텀 1m+{ret_1m:.0f}% 6m+{ret_6m:.0f}%")
            core_signal_count += 1

        # ── 2) 상대 강도 RS (15pt) — 시장 대비 ──
        if market_ctx:
            mc_key = "kr" if _is_korean(symbol) else "us"
            market_3m = market_ctx.get(mc_key, {}).get("market_return_3m")
            if market_3m is not None and len(df_d) >= 63:
                rs_diff = ret_3m - market_3m
                if rs_diff >= 30:
                    momentum += 15
                    signals.append(f"RS 강세 +{rs_diff:.0f}%p")
                    core_signal_count += 1
                elif rs_diff >= 15:
                    momentum += 10
                    signals.append(f"RS +{rs_diff:.0f}%p")
                elif rs_diff >= 5:
                    momentum += 5
                elif rs_diff >= 0:
                    momentum += 2

        # ── 3) 52주 신고가 + 연속 (10pt) ──
        high_52w = float(df_d["High"].max())
        if high_52w > 0 and last_d >= high_52w * 0.99:
            momentum += 7
            signals.append("52주 신고가")
            roll_high = df_d["High"].rolling(252, min_periods=180).max()
            tail   = df_d["Close"].iloc[-5:]
            tail_h = roll_high.iloc[-5:]
            valid  = tail_h.notna()
            if valid.any():
                consec = int((tail[valid] >= tail_h[valid] * 0.99).sum())
                if consec >= 3:
                    momentum += 3
                    signals.append("연속 신고가")
                    core_signal_count += 1

        # ═════════════════════════════════════════════
        # 펀더멘털 (max 25, with_fundamental=True 일 때만)
        # ═════════════════════════════════════════════

        if with_fundamental:
            try:
                info = stock.info

                # ── 분기 매출/EPS YoY 가속 (15pt = 8+7) ──
                try:
                    qf = stock.quarterly_income_stmt
                    if qf is not None and not qf.empty:
                        # 매출 YoY 가속 (8pt)
                        for key in ["Total Revenue", "Revenue", "TotalRevenue"]:
                            if key in qf.index:
                                rev = qf.loc[key].dropna().sort_index(ascending=False)
                                if len(rev) >= 5:
                                    yoy_q1 = (float(rev.iloc[0]) / float(rev.iloc[4]) - 1) * 100 if float(rev.iloc[4]) > 0 else 0
                                    if yoy_q1 >= 20:
                                        fund += 4
                                        signals.append(f"매출 YoY +{yoy_q1:.0f}%")
                                    # 가속 (이번 분기 YoY > 직전 분기 YoY + 5%p)
                                    if len(rev) >= 6:
                                        prev_yoy = (float(rev.iloc[1]) / float(rev.iloc[5]) - 1) * 100 if float(rev.iloc[5]) > 0 else 0
                                        if yoy_q1 > prev_yoy + 5 and yoy_q1 > 0:
                                            fund += 4
                                            signals.append("매출 가속")
                                            core_signal_count += 1
                                break
                        # EPS YoY 가속 (7pt)
                        for key in ["Diluted EPS", "Basic EPS", "EPS"]:
                            if key in qf.index:
                                eps_q = qf.loc[key].dropna().sort_index(ascending=False)
                                if len(eps_q) >= 5:
                                    e0 = float(eps_q.iloc[0]); e4 = float(eps_q.iloc[4])
                                    if e4 != 0:
                                        eps_yoy = (e0 / e4 - 1) * 100 if e4 > 0 else 0
                                        if eps_yoy >= 25:
                                            fund += 4
                                            signals.append(f"EPS YoY +{eps_yoy:.0f}%")
                                        if len(eps_q) >= 6:
                                            e1 = float(eps_q.iloc[1]); e5 = float(eps_q.iloc[5])
                                            if e5 > 0:
                                                eps_prev = (e1 / e5 - 1) * 100
                                                if eps_yoy > eps_prev + 5 and eps_yoy > 0:
                                                    fund += 3
                                                    signals.append("EPS 가속")
                                                    core_signal_count += 1
                                break
                except Exception:
                    pass

                # ── 이익률 (5pt) ──
                op_margin = _safe(info.get("operatingMargins"))
                if op_margin is not None:
                    if op_margin >= 0.20:
                        fund += 5
                        signals.append(f"이익률 {op_margin*100:.0f}%")
                    elif op_margin >= 0.10:
                        fund += 3

                # ── 컨센서스 상향 (5pt) ──
                fwd   = _safe(info.get("forwardEps"))
                trail = _safe(info.get("trailingEps"))
                if fwd and trail and trail > 0 and fwd > trail * 1.05:
                    fund += 5
                    signals.append("컨센서스 상향")

            except Exception:
                pass

        # ═════════════════════════════════════════════
        # 조정 (페널티 / 보너스)
        # ═════════════════════════════════════════════

        # ── 변동성 페널티 (ATR/Close > 5%) ──
        try:
            atr_series = (df_d["High"] - df_d["Low"]).rolling(14).mean()
            atr = float(atr_series.iloc[-1])
            if atr and last_d > 0:
                atr_ratio = atr / last_d
                if atr_ratio > 0.05:
                    penalty = round((tech + momentum + fund) * 0.10)
                    tech     = max(0, tech     - penalty // 3)
                    momentum = max(0, momentum - penalty // 3)
                    fund     = max(0, fund     - penalty // 3)
                    signals.append(f"⚠변동성 {atr_ratio*100:.1f}%")
        except Exception:
            pass

        # ── 시장 환경 페널티 (S&P500/KOSPI MA200 아래) ──
        if market_ctx:
            mc_key = "kr" if _is_korean(symbol) else "us"
            if market_ctx.get(mc_key, {}).get("above_ma200") is False:
                tech     = round(tech     * 0.7)
                momentum = round(momentum * 0.7)
                fund     = round(fund     * 0.7)
                signals.append("⚠ 약세장")

        # ── Perfect Setup 보너스 (핵심 신호 3+ 동시) ──
        if core_signal_count >= 3:
            tech     = round(tech     * 1.15)
            momentum = round(momentum * 1.15)
            fund     = round(fund     * 1.15)
            signals.insert(0, "🔥 Perfect Setup")

        total = tech + momentum + fund

        # 일변동률 (NaN 방지)
        change_pct = 0.0
        if len(df_d) >= 2:
            prev = df_d["Close"].iloc[-2]
            if pd.notna(prev) and prev != 0:
                try:
                    cp = (last_d / float(prev) - 1) * 100
                    if pd.notna(cp):
                        change_pct = float(cp)
                except Exception:
                    change_pct = 0.0

        # 스파크라인용: 최근 30일 종가, 0..1 정규화 (작은 SVG 차트)
        sparkline = []
        try:
            closes = [float(c) for c in df_d["Close"].tail(30).dropna().tolist()
                      if pd.notna(c)]
            if len(closes) >= 5:
                mn, mx = min(closes), max(closes)
                if mx > mn:
                    sparkline = [round((c - mn) / (mx - mn), 3) for c in closes]
                else:
                    sparkline = [0.5] * len(closes)
        except Exception:
            sparkline = []

        return {
            "ticker": symbol,
            "name":   name,
            "price":  last_d,
            "change_pct": change_pct,
            "currency": "KRW" if _is_korean(symbol) else "USD",
            "tech_score":     tech,
            "momentum_score": momentum,
            "fund_score":     fund,
            "total_score":    total,
            "signals":        signals[:6],
            "reason":         " · ".join(signals[:4]) if signals else "",
            "sparkline":      sparkline,
        }

    except Exception as e:
        log.debug(f"analyze_uptrend({symbol}) failed: {e}")
        return None


# ── 전체 스캔 (2-pass) ────────────────────────────────
def scan_all(stock_db, market: str = "ALL",
             min_total_score: int = 15,    # 25 → 15 (필터 완화)
             min_tech_momentum: int = 10,  # 20 → 10
             top_for_fund: int = 50,
             final_limit: int = 30,
             max_workers_pass1: int = 4,
             max_workers_pass2: int = 3,
             per_call_timeout: int = 20,   # 10 → 20 (데이터 받을 여유)
             progress_cb=None) -> dict:
    """후보 종목 전체 스캔. 2-pass 로 펀더멘털 호출 최소화.

    Pass 1: 모든 후보의 기술적+모멘텀 점수 산출 (history 1회)
    Pass 2: tech+momentum 합 ≥ min_tech_momentum 인 상위 N개만 펀더멘털 보강 (info 호출)
    """
    # 후보 필터
    candidates = []
    for s in stock_db:
        sym = s.get("symbol")
        if not sym:
            continue
        is_kr = _is_korean(sym)
        if market == "KR" and not is_kr: continue
        if market == "US" and is_kr:     continue
        candidates.append((sym, s.get("name", sym)))

    total_n = len(candidates)
    # 전체 작업량 추정: pass1 + pass2 후보 수
    est_total = total_n + min(top_for_fund, total_n)
    log.info(f"[trends scan] market={market} candidates={total_n} est_total={est_total}")
    started = time.time()

    # 시장 환경 컨텍스트 1회 fetch (모든 종목에 동일하게 전달)
    market_ctx = fetch_market_context()

    work_done = [0]
    def _bump():
        work_done[0] += 1
        if progress_cb:
            progress_cb(work_done[0], est_total)

    # ─ Pass 1 ─
    pass1 = []
    with ThreadPoolExecutor(max_workers=max_workers_pass1) as ex:
        futs = {
            ex.submit(analyze_uptrend, sym, name, False, market_ctx): sym
            for sym, name in candidates
        }
        for fut in as_completed(futs):
            _bump()
            # 사용자 중단 요청 체크
            with _LOCK:
                if _ABORT.get(market):
                    log.info(f"[trends scan] aborted by user at {work_done[0]}/{est_total}")
                    for f in futs:
                        try: f.cancel()
                        except Exception: pass
                    raise RuntimeError("스캔이 사용자에 의해 중단됨")
            try:
                r = fut.result(timeout=per_call_timeout)
                if r:
                    pass1.append(r)
            except FutTimeout:
                try: fut.cancel()
                except Exception: pass
            except Exception:
                pass

    # 상위 후보 → 펀더멘털 보강
    pass1.sort(key=lambda x: (x["tech_score"] + x["momentum_score"]), reverse=True)
    fund_targets = [r for r in pass1 if (r["tech_score"] + r["momentum_score"]) >= min_tech_momentum][:top_for_fund]

    # ─ Pass 2: 펀더멘털 ─
    enriched = {r["ticker"]: r for r in pass1}
    if fund_targets:
        with ThreadPoolExecutor(max_workers=max_workers_pass2) as ex:
            futs = {
                ex.submit(analyze_uptrend, r["ticker"], r["name"], True, market_ctx): r["ticker"]
                for r in fund_targets
            }
            for fut in as_completed(futs):
                _bump()
                try:
                    r = fut.result(timeout=per_call_timeout + 6)  # info 호출은 좀 더 길게
                    if r:
                        enriched[r["ticker"]] = r   # full result로 교체
                except FutTimeout:
                    try: fut.cancel()
                    except Exception: pass
                except Exception:
                    pass

    # 마지막 100% 보장
    if progress_cb:
        progress_cb(est_total, est_total)

    # 최종: 점수 ≥ 기준만 + 정렬 + 컷
    # total_score 는 모듈에서 setting 안 했을 수 있으니 일관되게 재계산
    for r in enriched.values():
        r["total_score"] = r["tech_score"] + r["momentum_score"] + r["fund_score"]

    final = [r for r in enriched.values() if r["total_score"] >= min_total_score]
    # 1차: 총점 desc, 2차: 기술 desc, 3차: 모멘텀 desc, 4차: 펀더 desc
    final.sort(
        key=lambda x: (x["total_score"], x["tech_score"], x["momentum_score"], x["fund_score"]),
        reverse=True,
    )
    final = final[:final_limit]

    elapsed = time.time() - started
    # 진단 정보
    pass1_count = len(pass1)
    score_dist = {
        "≥50": sum(1 for r in enriched.values() if r["total_score"] >= 50),
        "≥30": sum(1 for r in enriched.values() if r["total_score"] >= 30),
        "≥15": sum(1 for r in enriched.values() if r["total_score"] >= 15),
        "≥5":  sum(1 for r in enriched.values() if r["total_score"] >= 5),
    }
    log.info(f"[trends scan] done in {elapsed:.1f}s scanned={total_n} "
             f"pass1_ok={pass1_count} score_dist={score_dist} hits={len(final)}")

    return {
        "scanned_at":   datetime.utcnow().isoformat() + "Z",
        "market":       market,
        "total":        total_n,
        "data_ok":      pass1_count,   # 데이터 받은 종목 수
        "hits":         len(final),
        "score_dist":   score_dist,
        "elapsed_sec":  round(elapsed, 1),
        "items":        final,
    }


# ── 백그라운드 스캔 (폴링용) ───────────────────────────
def _bg_scan(stock_db, market: str):
    def _progress(done, total):
        with _LOCK:
            if market in _STATUS:
                _STATUS[market]["progress"] = done
                _STATUS[market]["total"]    = total

    try:
        data = scan_all(stock_db, market=market, progress_cb=_progress)
        with _LOCK:
            _CACHE[market]  = (time.time(), data)
            _STATUS[market] = {
                "state":       "done",
                "finished_at": time.time(),
                "result":      data,
            }
    except Exception as e:
        log.exception("trends scan failed")
        with _LOCK:
            _STATUS[market] = {"state": "error", "message": str(e)}


def _filter_items_by_market(items, market):
    """ALL 캐시 결과에서 KR/US 만 필터링."""
    if market == "ALL":
        return items
    out = []
    for it in items:
        sym = it.get("ticker", "")
        is_kr = sym.endswith(".KS") or sym.endswith(".KQ")
        if market == "KR" and is_kr: out.append(it)
        elif market == "US" and not is_kr: out.append(it)
    return out


def _derive_from_all_cache(market):
    """ALL 캐시가 있으면 KR/US 결과를 거기서 도출. None 반환 시 도출 불가."""
    if market not in ("KR", "US"):
        return None
    if "ALL" not in _CACHE:
        return None
    ts, all_data = _CACHE["ALL"]
    if time.time() - ts >= _CACHE_TTL:
        return None
    # 필터링된 결과 생성
    filtered_items = _filter_items_by_market(all_data.get("items", []), market)
    # 점수 분포 재계산
    dist = {
        "≥50": sum(1 for r in filtered_items if r.get("total_score", 0) >= 50),
        "≥30": sum(1 for r in filtered_items if r.get("total_score", 0) >= 30),
        "≥15": sum(1 for r in filtered_items if r.get("total_score", 0) >= 15),
        "≥5":  sum(1 for r in filtered_items if r.get("total_score", 0) >= 5),
    }
    return {
        "scanned_at":   all_data.get("scanned_at"),
        "market":       market,
        "total":        all_data.get("total", 0),
        "data_ok":      all_data.get("data_ok"),
        "hits":         len(filtered_items),
        "score_dist":   dist,
        "elapsed_sec":  all_data.get("elapsed_sec", 0),
        "items":        filtered_items,
        "derived_from_all": True,
    }


def start_scan(stock_db, market: str, force: bool = False) -> dict:
    """스캔 시작. 캐시 유효하면 즉시 결과, 진행 중이면 그 상태 반환.

    KR/US 요청 시 ALL 캐시가 있으면 거기서 필터링 → 재스캔 없음.
    """
    market = (market or "ALL").upper()
    if market not in ("ALL", "KR", "US"):
        market = "ALL"

    with _LOCK:
        # 1) 직접 캐시 확인
        if not force and market in _CACHE:
            ts, data = _CACHE[market]
            if time.time() - ts < _CACHE_TTL:
                return {"state": "done", "cached": True, "result": data}

        # 2) ALL 캐시에서 도출 (KR/US 요청 시)
        if not force:
            derived = _derive_from_all_cache(market)
            if derived is not None:
                # 도출 결과를 해당 market 캐시에도 저장 (재계산 절약)
                _CACHE[market] = (time.time(), derived)
                return {"state": "done", "cached": True, "result": derived}

        # 3) 진행 중
        if market in _STATUS and _STATUS[market].get("state") == "running":
            return {
                "state":      "running",
                "started_at": _STATUS[market].get("started_at"),
                "progress":   _STATUS[market].get("progress", 0),
                "total":      _STATUS[market].get("total", 0),
            }

        # 4) 새 스캔 시작 — abort 플래그 초기화
        _ABORT[market] = False
        _STATUS[market] = {
            "state":      "running",
            "started_at": time.time(),
            "progress":   0,
            "total":      0,
        }

    threading.Thread(target=_bg_scan, args=(stock_db, market), daemon=True).start()
    return {"state": "running", "started_at": time.time(), "progress": 0, "total": 0}


def abort_scan(market: str) -> dict:
    """진행 중인 스캔 중단 요청."""
    market = (market or "ALL").upper()
    with _LOCK:
        _ABORT[market] = True
    return {"aborted": True, "market": market}


def get_status(market: str) -> dict:
    """현재 스캔 상태 + 캐시된 결과 반환."""
    market = (market or "ALL").upper()
    if market not in ("ALL", "KR", "US"):
        market = "ALL"

    server_now = time.time()  # 서버 응답 시각 (캐시 진단용)

    with _LOCK:
        # 1) 직접 캐시
        if market in _CACHE:
            ts, data = _CACHE[market]
            if server_now - ts < _CACHE_TTL:
                return {
                    "state":  "done",
                    "cached": True,
                    "cached_at": datetime.fromtimestamp(ts).isoformat() + "Z",
                    "result": data,
                    "server_now": server_now,
                }
        # 2) ALL 캐시에서 도출 (KR/US 요청 시)
        derived = _derive_from_all_cache(market)
        if derived is not None:
            _CACHE[market] = (time.time(), derived)
            return {
                "state":  "done",
                "cached": True,
                "result": derived,
                "server_now": server_now,
            }
        # 3) 진행 상태
        st = dict(_STATUS.get(market, {"state": "idle"}))
        st["server_now"] = server_now
        return st
