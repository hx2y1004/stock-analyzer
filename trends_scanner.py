"""추세 상승 종목 스캐너.

기술적(주봉 BB 수축 + 상단돌파 + 거래량 + 장대양봉 + MA 정렬) +
모멘텀(14일 수익률 + 52주 신고가) +
펀더멘털(매출/EPS 성장률 + 컨센서스 상향) 점수로 종합 평가.

총점 max 100 = Tech 50 + Momentum 30 + Fundamental 20
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


# ── 단일 종목 분석 ────────────────────────────────────
def analyze_uptrend(symbol: str, name: str, with_fundamental: bool = False) -> Optional[dict]:
    """단일 종목의 추세 상승 점수 계산. 실패 시 None."""
    try:
        # 약간의 지터 (rate limit 분산)
        time.sleep(random.uniform(0.02, 0.10))

        stock = yf.Ticker(symbol)  # 기본 세션 (Yahoo 봇 차단 회피)

        # 1년 일봉
        df_d = stock.history(period="1y", interval="1d", auto_adjust=False)
        if df_d is None or df_d.empty:
            log.debug(f"[no data] {symbol}")
            return None
        if len(df_d) < 60:
            log.debug(f"[short data] {symbol} only {len(df_d)} rows")
            return None

        # 주봉 (일봉 리샘플)
        df_w = df_d.resample("W").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()
        if len(df_w) < 26:
            return None

        signals = []
        tech = momentum = fund = 0

        # ─── 기술적 (주봉, max 50) ───
        # 1) 볼린저밴드 수축 (10pt)
        bb_mid   = df_w["Close"].rolling(20).mean()
        bb_std   = df_w["Close"].rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_upper - bb_lower) / bb_mid

        if not pd.isna(bb_width.iloc[-1]):
            recent_w = bb_width.iloc[-3:].mean()
            past_w   = bb_width.iloc[-20:-3].mean()
            if not pd.isna(past_w) and past_w > 0 and recent_w < past_w * 0.75:
                tech += 10
                signals.append("볼린저 수축")

        # 2) BB 상단 돌파 (15pt) — 최근 2주 내
        if not pd.isna(bb_upper.iloc[-1]):
            if df_w["Close"].iloc[-1] > bb_upper.iloc[-1]:
                tech += 15
                signals.append("상단 돌파")
            elif len(bb_upper) >= 2 and not pd.isna(bb_upper.iloc[-2]) \
                    and df_w["Close"].iloc[-2] > bb_upper.iloc[-2]:
                tech += 10
                signals.append("상단 돌파(전주)")

        # 3) 거래량 급증 (10pt)
        avg_vol  = df_w["Volume"].iloc[-20:-2].mean()
        last_vol = df_w["Volume"].iloc[-1]
        if avg_vol and avg_vol > 0 and last_vol > avg_vol * 1.5:
            tech += 10
            ratio = int((last_vol / avg_vol - 1) * 100)
            signals.append(f"거래량 +{ratio}%")

        # 4) 장대양봉 2개 이상 (10pt) — 최근 3주 내
        body     = (df_w["Close"] - df_w["Open"]).abs()
        avg_body = body.rolling(20).mean()
        if not avg_body.iloc[-3:].isna().all():
            is_long = (df_w["Close"] > df_w["Open"]) & (body > avg_body * 1.5)
            cnt     = int(is_long.iloc[-3:].sum())
            if cnt >= 2:
                tech += 10
                signals.append(f"장대양봉 {cnt}")

        # 5) MA 상승 정렬 (5pt)
        ma20 = df_w["Close"].rolling(20).mean()
        if len(ma20) >= 6 and not pd.isna(ma20.iloc[-1]) and not pd.isna(ma20.iloc[-6]):
            if ma20.iloc[-1] > ma20.iloc[-3] > ma20.iloc[-6]:
                tech += 5
                signals.append("이평선 상승")

        # ─── 모멘텀 (max 30) ───
        # 1) 14영업일 수익률 5%↑ (15pt)
        if len(df_d) >= 16:
            ret14 = (df_d["Close"].iloc[-1] / df_d["Close"].iloc[-15] - 1) * 100
            if ret14 >= 5:
                momentum += 15
                signals.append(f"14일 +{ret14:.1f}%")

        # 2) 52주 신고가 (10pt) + 연속 신고가 (5pt)
        high_52w = df_d["High"].max()
        last_d   = df_d["Close"].iloc[-1]
        if last_d >= high_52w * 0.99:
            momentum += 10
            signals.append("52주 신고가")
            roll_high = df_d["High"].rolling(252, min_periods=180).max()
            tail = df_d["Close"].iloc[-5:]
            tail_h = roll_high.iloc[-5:]
            valid = tail_h.notna()
            if valid.any():
                consec = int((tail[valid] >= tail_h[valid] * 0.99).sum())
                if consec >= 3:
                    momentum += 5
                    signals.append("연속 신고가")

        # ─── 펀더멘털 (max 20) — 옵션 ───
        if with_fundamental:
            try:
                info = stock.info
                rev_g  = info.get("revenueGrowth")
                eps_g  = info.get("earningsGrowth")
                fwd    = _safe(info.get("forwardEps"))
                trail  = _safe(info.get("trailingEps"))

                if rev_g is not None and rev_g > 0.10:
                    fund += 5
                    signals.append(f"매출 +{rev_g*100:.0f}%")
                if eps_g is not None and eps_g > 0.10:
                    fund += 5
                    signals.append(f"EPS +{eps_g*100:.0f}%")
                if fwd and trail and trail > 0 and fwd > trail * 1.05:
                    fund += 10
                    signals.append("컨센서스 상향")
            except Exception:
                pass

        total = tech + momentum + fund

        # 일변동률
        change_pct = 0.0
        if len(df_d) >= 2:
            prev = df_d["Close"].iloc[-2]
            if prev:
                change_pct = (df_d["Close"].iloc[-1] / prev - 1) * 100

        # 스파크라인용: 최근 30일 종가, 0..1 정규화 (작은 SVG 차트)
        sparkline = []
        try:
            closes = df_d["Close"].tail(30).dropna().tolist()
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
            "price":  float(last_d),
            "change_pct": float(change_pct),
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

    work_done = [0]   # 리스트로 감싸서 클로저에서 mutation
    def _bump():
        work_done[0] += 1
        # 매번 콜백 (서버 측 진행률을 즉시 반영)
        if progress_cb:
            progress_cb(work_done[0], est_total)

    # ─ Pass 1 ─
    pass1 = []
    with ThreadPoolExecutor(max_workers=max_workers_pass1) as ex:
        futs = {ex.submit(analyze_uptrend, sym, name, False): sym for sym, name in candidates}
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
                ex.submit(analyze_uptrend, r["ticker"], r["name"], True): r["ticker"]
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
