"""AI 매매 신호 봇 엔진 — Step 1: 신호 생성 파이프라인.

흐름 (시장별, 장 마감 후 하루 1회):
  1) 대상 종목 일봉을 묶음으로 내려받고 지표 계산
  2) 기존 신호 사후 추적: PENDING → 다음 장 시가로 체결(B 방식) 또는 취소, ACTIVE → 익절·손절·트레일링·시간·추세·AI 악재 청산
  3) 공통 금지 필터(저유동성 · 실적 D-3 · 같은 봇·종목 24시간 쿨다운)
  4) 봇별 1차 정량 필터 → 후보 종목
  5) 후보만 펀더멘탈(4팩터 점수·DCF·애널리스트 목표가) 조회
  6) AI 평가: 종목당 하루 1회만 호출해 모든 봇이 공유 (하루 예산 안에서, 보유 종목 우선)
  7) 봇별 최종 판정 → 손절·목표가 → 손익비 미달이면 기각 → 하루 신규·최대 보유 제한 → 신호 저장

app.py 와 순환 import 를 피하려고 필요한 함수는 init(deps) 로 넘겨받는다.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd

D = {}                      # app.py 가 넘겨주는 의존성 (app, db, 모델, 헬퍼 함수)
_RUN_LOCK = threading.Lock()

# ── 기본 전략 (DB 에 없을 때만 넣는다. 이후 튜닝은 DB 의 params_json 으로) ──────────────
DEFAULT_STRATEGIES = [
    {
        "key": "aggressive_momentum", "name": "공격형 모멘텀", "emoji": "🔥", "kind": "momentum", "use_ai": True,
        "params": {
            "horizon_days": 10,
            "entry": {"vol_ratio_min": 1.5, "rsi_min": 50, "rsi_max": 70, "require_catalyst": True},
            "ai_conf_min": 75,
            "exit": {"stop_pct": 4.0, "stop_atr_mult": 1.5, "target_pct_min": 8.0, "target_pct_max": 12.0,
                     "rr_min": 1.8, "trail_trigger_pct": 6.0, "trail_pct": 3.0, "time_stop_days": 10},
            "risk": {"max_weight": 0.15, "max_positions": 6, "max_new_per_day": 3, "daily_loss_limit_pct": 3.0},
            "fee_pct": 0.15,
        },
    },
    {
        "key": "balanced_swing", "name": "균형형 스윙", "emoji": "⚖️", "kind": "swing", "use_ai": True,
        "params": {
            "horizon_days": 25,
            "fund_min": 65,
            "breakout": {"vol_ratio_min": 1.3, "rsi_min": 45, "rsi_max": 68},
            "rebound": {"fund_min": 70, "rsi_max": 30, "bb_max": 20, "vol_ratio_min": 1.0},
            "ai_conf_min": 75,
            "exit": {"stop_pct_min": 6.0, "stop_pct_max": 7.0, "stop_atr_mult": 2.0, "rr_min": 2.0, "target_rr": 2.5,
                     "trail_trigger_pct": 10.0, "trail_pct": 5.0, "time_stop_days": 25, "trend_exit": True},
            "risk": {"max_weight": 0.10, "max_positions": 10, "max_new_per_day": 3, "daily_loss_limit_pct": 3.0},
            "fee_pct": 0.15,
        },
    },
    {
        "key": "conservative_value", "name": "안정형 가치", "emoji": "🛡️", "kind": "value", "use_ai": True,
        "params": {
            "horizon_days": 60,
            "fund_min": 80, "upside_min_pct": 15.0, "bb_max": 30, "vix_max": 30,
            "ai_conf_min": 80,
            "exit": {"stop_pct": 10.0, "target_pct_min": 15.0, "target_pct_max": 25.0, "rr_min": 1.5,
                     "time_stop_days": 60},
            "risk": {"max_weight": 0.10, "max_positions": 10, "max_new_per_day": 2, "daily_loss_limit_pct": 2.0},
            "fee_pct": 0.15,
        },
    },
    {
        # 균형형과 100% 같은 정량 규칙, AI 검증만 생략한 대조군 (하루 제한도 균형형과 동일)
        "key": "baseline_quant", "name": "기준선 정량", "emoji": "📊", "kind": "swing", "use_ai": False,
        "params": None,     # → balanced_swing 파라미터를 복사
    },
]

COMMON = {"vol_ratio_floor": 0.3, "earnings_block_days": 3, "cooldown_hours": 24,
          "max_fund_candidates": 40}

FINAL_STATUSES = ("TARGET_HIT", "STOP_LOSS", "TRAILING_STOP", "TIME_STOP", "TREND_EXIT", "AI_RISK_EXIT")


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[BOT] {msg}")
    except Exception:
        print(f"[BOT] {msg}", flush=True)


# ── 전략 로딩 ──────────────────────────────────────────────────────────────────
def seed_strategies():
    """기본 전략이 DB 에 없으면 넣는다 (이미 있으면 사용자가 바꾼 값을 존중해 건드리지 않음)."""
    Model, db = D["BotStrategy"], D["db"]
    by_key = {s["key"]: s for s in DEFAULT_STRATEGIES}
    for s in DEFAULT_STRATEGIES:
        if Model.query.filter_by(key=s["key"]).first():
            continue
        params = s["params"] or by_key["balanced_swing"]["params"]
        db.session.add(Model(key=s["key"], name=s["name"], emoji=s["emoji"], kind=s["kind"],
                             use_ai=s["use_ai"], enabled=True, params_json=json.dumps(params, ensure_ascii=False)))
    db.session.commit()


def load_strategies():
    out = []
    for row in D["BotStrategy"].query.filter_by(enabled=True).order_by(D["BotStrategy"].id).all():
        out.append({"key": row.key, "name": row.name, "emoji": row.emoji, "kind": row.kind,
                    "use_ai": bool(row.use_ai), "params": json.loads(row.params_json)})
    return out


# ── 시장 / 시간 ────────────────────────────────────────────────────────────────
def market_of(ticker):
    return "KR" if ticker.upper().endswith((".KS", ".KQ")) else "US"


def _tz(market):
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")


def market_today(market):
    return datetime.now(_tz(market)).date()


def universe(market):
    syms = []
    for s in D["STOCK_DB"]:
        sym = s.get("symbol")
        if sym and market_of(sym) == market and s.get("type", "EQUITY") == "EQUITY":
            syms.append((sym, s.get("name") or sym))
    return syms


# ── 데이터 ─────────────────────────────────────────────────────────────────────
def download_daily(tickers, period="1y", chunk=40, pause=1.5):
    """yfinance 묶음 다운로드 → {ticker: DataFrame(Open,High,Low,Close,Volume)}. 실패한 종목은 빠진다."""
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period=period, interval="1d", group_by="ticker", auto_adjust=False,
                              threads=True, progress=False)
        except Exception as e:
            log(f"download 실패 {part[0]}…: {e}")
            continue
        for t in part:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df) >= 70:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    out[t] = df
            except Exception:
                pass
        time.sleep(pause)          # Yahoo 요청 제한 완화
    return out


def compute_features(df):
    """마지막 봉 기준 판단에 필요한 값들."""
    from analysis.indicators import add_all_indicators
    d = add_all_indicators(df.copy())
    hl = d["High"] - d["Low"]
    hc = (d["High"] - d["Close"].shift()).abs()
    lc = (d["Low"] - d["Close"].shift()).abs()
    d["ATR"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    d["VOL_AVG20"] = d["Volume"].rolling(20).mean().shift(1)     # 오늘을 뺀 평균
    last, prev = d.iloc[-1], d.iloc[-2]

    def f(row, col):
        v = row.get(col)
        try:
            v = float(v)
            return None if (math.isnan(v) or math.isinf(v)) else v
        except Exception:
            return None

    close = f(last, "Close")
    cloud_top = max([x for x in (f(last, "senkou_a"), f(last, "senkou_b")) if x is not None], default=None)
    bb_u, bb_l = f(last, "BB_upper"), f(last, "BB_lower")
    bb_pos = ((close - bb_l) / (bb_u - bb_l) * 100) if (bb_u and bb_l and bb_u > bb_l) else None
    pbb_u, pbb_l, pclose = f(prev, "BB_upper"), f(prev, "BB_lower"), f(prev, "Close")
    pbb_pos = ((pclose - pbb_l) / (pbb_u - pbb_l) * 100) if (pbb_u and pbb_l and pbb_u > pbb_l) else None
    vol_avg = f(last, "VOL_AVG20")
    macd, sig, pmacd, psig = f(last, "MACD"), f(last, "MACD_signal"), f(prev, "MACD"), f(prev, "MACD_signal")
    hist, phist = f(last, "MACD_hist"), f(prev, "MACD_hist")
    # 최근 3봉 안의 골든크로스
    golden = False
    for k in range(1, 4):
        a, b = d.iloc[-k], d.iloc[-k - 1]
        if (f(a, "MACD") or 0) > (f(a, "MACD_signal") or 0) and (f(b, "MACD") or 0) <= (f(b, "MACD_signal") or 0):
            golden = True
    return {
        "date": d.index[-1].date(), "close": close, "open": f(last, "Open"), "high": f(last, "High"),
        "low": f(last, "Low"), "prev_close": pclose,
        "ma5": f(last, "MA5"), "ma20": f(last, "MA20"), "ma60": f(last, "MA60"),
        "prev_ma20": f(prev, "MA20"),
        "rsi": f(last, "RSI"), "prev_rsi": f(prev, "RSI"),
        "macd": macd, "macd_signal": sig, "macd_hist": hist, "prev_macd_hist": phist,
        "macd_up": (macd is not None and sig is not None and macd > sig and (hist or 0) > (phist or 0)),
        "macd_golden": golden,
        "macd_dead": (macd is not None and sig is not None and macd < sig and (pmacd or 0) >= (psig or 0)),
        "cloud_top": cloud_top, "above_cloud": (close is not None and cloud_top is not None and close > cloud_top),
        "bb_pos": bb_pos, "prev_bb_pos": pbb_pos,
        "atr": f(last, "ATR"),
        "vol_ratio": (float(last["Volume"]) / vol_avg) if vol_avg else None,
        "low20": float(d["Low"].iloc[-20:].min()), "high60": float(d["High"].iloc[-61:-1].max()),
        "bullish_candle": (close is not None and f(last, "Open") is not None and close > f(last, "Open")),
        "chg_pct": ((close / pclose - 1) * 100) if (close and pclose) else None,
    }


# ── 1차 정량 필터 (펀더멘탈·AI 전) ─────────────────────────────────────────────
def _rebound_confirmed(x, p_rsi_max=None, p_bb_max=None, vol_min=1.0):
    """과매도/하단 구간에서 반등 확인: (전일 또는 당일 RSI≤기준 또는 BB≤기준) + RSI 상승 전환 + 양봉 + 거래량."""
    oversold = False
    if p_rsi_max is not None and ((x["rsi"] or 99) <= p_rsi_max or (x["prev_rsi"] or 99) <= p_rsi_max):
        oversold = True
    if p_bb_max is not None and ((x["bb_pos"] if x["bb_pos"] is not None else 99) <= p_bb_max
                                 or (x["prev_bb_pos"] if x["prev_bb_pos"] is not None else 99) <= p_bb_max):
        oversold = True
    turning = x["rsi"] is not None and x["prev_rsi"] is not None and x["rsi"] > x["prev_rsi"]
    return oversold and turning and x["bullish_candle"] and (x["vol_ratio"] or 0) >= vol_min


def quant_pass(strat, x):
    """→ (통과 여부, 경로, 정량 점수 0~100, 근거 dict)."""
    p, kind = strat["params"], strat["kind"]
    ev = {}
    if kind == "momentum":
        e = p["entry"]
        conds = {
            "ma5>ma20": bool(x["ma5"] and x["ma20"] and x["ma5"] > x["ma20"]),
            "above_cloud": x["above_cloud"],
            "vol": (x["vol_ratio"] or 0) >= e["vol_ratio_min"],
            "macd_up": x["macd_up"],
            "rsi_band": x["rsi"] is not None and e["rsi_min"] <= x["rsi"] <= e["rsi_max"],
        }
        ok = all(conds.values())
        score = _score([min(100, (x["vol_ratio"] or 0) / 3 * 100), 100 - abs((x["rsi"] or 60) - 60) * 4,
                        100 if conds["above_cloud"] else 40])
        return ok, "momentum", score, {"conds": conds}
    if kind == "swing":
        b, r = p["breakout"], p["rebound"]
        crossed20 = bool(x["prev_close"] and x["prev_ma20"] and x["prev_close"] <= x["prev_ma20"]
                         and x["close"] > (x["ma20"] or 1e18))
        aligned = bool(x["ma5"] and x["ma20"] and x["ma60"] and x["ma5"] > x["ma20"] > x["ma60"])
        brk = {
            "ma_align_or_cross20": aligned or crossed20,
            "above_cloud": x["above_cloud"],
            "macd_golden": x["macd_golden"],
            "vol": (x["vol_ratio"] or 0) >= b["vol_ratio_min"],
            "rsi_band": x["rsi"] is not None and b["rsi_min"] <= x["rsi"] <= b["rsi_max"],
        }
        reb_ok = _rebound_confirmed(x, p_rsi_max=r["rsi_max"], p_bb_max=r["bb_max"], vol_min=r["vol_ratio_min"])
        if all(brk.values()):
            return True, "breakout", _score([min(100, (x["vol_ratio"] or 0) / 2.5 * 100),
                                             100 - abs((x["rsi"] or 56) - 56) * 4, 90]), {"conds": brk}
        if reb_ok:
            return True, "rebound", _score([70, min(100, (x["vol_ratio"] or 0) * 60),
                                            100 - (x["bb_pos"] or 50)]), {"rebound": True}
        return False, None, 0, {"conds": brk}
    if kind == "value":
        ok = _rebound_confirmed(x, p_bb_max=p["bb_max"], vol_min=1.0)
        return ok, "value_rebound", _score([70, 100 - (x["bb_pos"] or 50)]), {"bb_pos": x["bb_pos"]}
    return False, None, 0, {}


def _score(vals):
    vals = [max(0.0, min(100.0, float(v))) for v in vals if v is not None]
    return int(round(sum(vals) / len(vals))) if vals else 0


# ── 펀더멘탈 (후보만) ──────────────────────────────────────────────────────────
def fundamentals(ticker, df):
    """4팩터 점수(펀더멘탈 = Quality·Value·Growth 평균), DCF 적정가, 애널리스트 목표가, 다음 실적일."""
    import yfinance as yf
    from analysis.advanced import compute_advanced_metrics
    out = {"fund_score": None, "factors": None, "dcf_fair": None, "analyst_target": None, "earnings_date": None}
    stock = yf.Ticker(ticker)
    try:
        info = stock.info or {}
    except Exception:
        info = {}
    out["analyst_target"] = _num(info.get("targetMeanPrice"))
    try:
        sc = compute_advanced_metrics(stock, info, df)
        fac = sc.get("factors", sc) if isinstance(sc, dict) else {}
        vals = [((fac.get(k) or {}).get("score")) for k in ("quality", "value", "growth")]
        vals = [v for v in vals if v is not None]
        out["fund_score"] = round(sum(vals) / len(vals), 1) if vals else None
        out["factors"] = {k: (fac.get(k) or {}).get("score") for k in ("quality", "value", "growth", "momentum")}
    except Exception as e:
        log(f"{ticker} 4팩터 실패: {e}")
    try:
        price = float(df["Close"].iloc[-1])
        cur = info.get("currency") or ("KRW" if market_of(ticker) == "KR" else "USD")
        dcf = D["compute_dcf"](ticker, info, *D["get_financials"](ticker, stock), price, cur,
                               risk_free_rate=D["get_risk_free_rate"](cur))
        if dcf and dcf.get("fair_value"):
            out["dcf_fair"] = float(dcf["fair_value"])
            out["dcf_reliability"] = dcf.get("reliability")
    except Exception:
        pass
    if market_of(ticker) == "US":           # 국내 종목은 실적 발표일 출처가 없어 생략 (화면에 표시)
        try:
            for ev in D["fetch_upcoming_events"](stock, info, ticker) or []:
                if "실적" in (ev.get("label") or "") and ev.get("date"):
                    out["earnings_date"] = str(ev["date"])[:10]
                    break
        except Exception:
            pass
    out["info"] = info
    return out


def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except Exception:
        return None


# ── AI 평가 (종목당 하루 1회, 모든 봇 공유) ─────────────────────────────────────
def _budget_key(day):
    return f"bot_ai_budget:{day.isoformat()}"


def _budget_used(day):
    hit = D["ai_cache"].get(_budget_key(day))
    return int((hit[0] or {}).get("n", 0)) if hit else 0


def _budget_add(day, n=1):
    used = _budget_used(day) + n
    D["ai_cache"].put(_budget_key(day), None, "bot_ai_budget", {"n": used}, None, 3 * 86400)
    return used


def ai_budget_limit():
    try:
        return int(os.environ.get("AI_DAILY_BUDGET", "60"))
    except ValueError:
        return 60


def get_or_create_assessment(ticker, name, as_of, x, fund, news):
    """같은 종목·날짜 평가가 있으면 재사용. 없으면 예산 안에서 1회 생성. 예산 초과·실패면 None."""
    Model, db = D["AiStockAssessment"], D["db"]
    row = Model.query.filter_by(ticker=ticker, as_of=as_of).first()
    if row:
        return row
    kst_day = datetime.now(_tz("KR")).date()
    if _budget_used(kst_day) >= ai_budget_limit():
        return None

    def _make():
        again = Model.query.filter_by(ticker=ticker, as_of=as_of).first()
        if again:
            return again.id
        heads = [{"t": n.get("title_orig") or n.get("title"), "s": (n.get("desc") or "")[:140]} for n in (news or [])[:6]]
        facts = {
            "종가": x.get("close"), "등락률": round(x.get("chg_pct") or 0, 2), "RSI": round(x.get("rsi") or 0, 1),
            "거래량_평균대비": round(x.get("vol_ratio") or 0, 2), "MA5>MA20": bool(x.get("ma5") and x.get("ma20") and x["ma5"] > x["ma20"]),
            "구름대위": x.get("above_cloud"), "BB위치": round(x.get("bb_pos") or 0, 1),
            "펀더멘탈점수": (fund or {}).get("fund_score"), "팩터": (fund or {}).get("factors"),
            "DCF적정가": (fund or {}).get("dcf_fair"), "애널리스트목표가": (fund or {}).get("analyst_target"),
        }
        user = (f"종목: {name} ({ticker})\n[정량 지표]\n{json.dumps(facts, ensure_ascii=False)}\n"
                f"[최근 뉴스]\n{json.dumps(heads, ensure_ascii=False)}\n\n"
                "이 종목에 대해 앞으로 2~8주 매수 관점의 위험·호재를 평가하세요. 숫자를 새로 만들지 말고 주어진 정보만 쓰세요.\n"
                '출력 JSON: {"confidence": 0~100 정수(매수 근거의 신뢰도), "verdict": "pass"|"caution"|"reject", '
                '"catalyst": 단기 호재·모멘텀 근거가 뉴스에 있으면 true, "fatal_risk": 상장폐지·분식·대규모 소송·급격한 실적 붕괴 등 치명적 악재면 true, '
                '"structural_risk": 사업 구조적 악화(점유율 상실·규제·장기 역성장)면 true, '
                '"summary": 한국어 한 줄 근거(60자 안팎), "positives": [최대 3개 짧은 문장], "risks": [최대 3개 짧은 문장]}')
        out = D["ai_json"]("당신은 리스크 관리가 엄격한 증권 애널리스트입니다. 순수 한글로 쓰고 한자는 쓰지 않습니다. "
                           "반드시 JSON 객체 하나만 출력합니다.", user, max_tokens=500, label="bot_assess", ticker=ticker)
        _budget_add(kst_day)
        if not out:
            return None
        conf = out.get("confidence")
        try:
            conf = max(0, min(100, int(conf)))
        except Exception:
            conf = None
        verdict = str(out.get("verdict") or "").lower()
        row = Model(ticker=ticker, as_of=as_of, confidence=conf,
                    verdict=verdict if verdict in ("pass", "caution", "reject") else "caution",
                    catalyst=bool(out.get("catalyst")), fatal_risk=bool(out.get("fatal_risk")),
                    structural_risk=bool(out.get("structural_risk")),
                    summary=str(out.get("summary") or "")[:300],
                    payload_json=json.dumps({"out": out, "facts": facts, "news": heads}, ensure_ascii=False),
                    model_used=D["last_model"]())
        db.session.add(row)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            again = Model.query.filter_by(ticker=ticker, as_of=as_of).first()
            return again.id if again else None
        return row.id

    rid = D["ai_cache"].single_flight(f"bot_assess:{ticker}:{as_of}", _make, wait_timeout=60)
    return Model.query.get(rid) if rid else None


# ── 손절·목표가 (손익비 미달이면 기각) ─────────────────────────────────────────
def plan_levels(strat, path, x, fund):
    """→ (entry, stop, target, rr, 기각사유|None)."""
    p, kind = strat["params"], strat["kind"]
    e = x["close"]
    atr = x["atr"] or 0
    ex = p["exit"]
    if kind == "momentum":
        stop = max(e * (1 - ex["stop_pct"] / 100), e - ex["stop_atr_mult"] * atr)     # 보수적 = 진입가에 더 가까운 쪽
        risk = e - stop
        target = max(e + ex["rr_min"] * risk, e * (1 + ex["target_pct_min"] / 100))
        target = min(target, e * (1 + ex["target_pct_max"] / 100))
        resistance = x["high60"] if x["high60"] and x["high60"] > e * 1.01 else None
        if resistance:
            target = min(target, resistance)
    elif kind == "swing":
        raw = max(x["low20"] * 0.995, e - ex["stop_atr_mult"] * atr)
        stop = min(max(raw, e * (1 - ex["stop_pct_max"] / 100)), e * (1 - ex["stop_pct_min"] / 100))
        risk = e - stop
        # 목표는 2.5R(저항선이 더 가까우면 저항선). 기각 기준은 rr_min(2.0) — 다음 장 소폭 갭 상승에도 바로 취소되지 않게 여유를 둔다
        target = e + ex.get("target_rr", ex["rr_min"]) * risk
        resistance = x["high60"] if x["high60"] and x["high60"] > e * 1.01 else None
        if resistance:
            target = min(target, resistance)
    else:  # value
        stop = e * (1 - ex["stop_pct"] / 100)
        risk = e - stop
        refs = [v for v in ((fund or {}).get("dcf_fair"), (fund or {}).get("analyst_target")) if v]
        cap = e * (1 + ex["target_pct_max"] / 100)
        target = min(max(refs), cap) if refs else e * (1 + ex["target_pct_min"] / 100)
        if target < e * (1 + ex["target_pct_min"] / 100):
            return e, stop, target, (target - e) / risk if risk > 0 else 0, "목표가 상승 여력 부족"
    if risk <= 0:
        return e, stop, target, 0, "손절 거리 계산 불가"
    rr = (target - e) / risk
    if rr < ex["rr_min"]:
        return e, stop, target, rr, f"손익비 {rr:.2f} < 기준 {ex['rr_min']}"
    return e, stop, target, rr, None


# ── 사후 추적 ──────────────────────────────────────────────────────────────────
def _fee_adj_pnl(entry, exit_, fee_pct):
    return ((exit_ * (1 - fee_pct / 100)) / (entry * (1 + fee_pct / 100)) - 1) * 100


def track_signals(market, data, strategies):
    """PENDING/ACTIVE 신호를 일봉으로 추적. data: {ticker: df}."""
    S, db = D["AiSignal"], D["db"]
    strat = {s["key"]: s for s in strategies}
    rows = S.query.filter(S.market == market, S.status.in_(["PENDING", "ACTIVE"])).all()
    changed = 0
    for sig in rows:
        df = data.get(sig.ticker)
        st = strat.get(sig.strategy_key)
        if df is None or st is None:
            continue
        p, ex = st["params"], st["params"]["exit"]
        fee = p.get("fee_pct", 0.15)
        bars = df[df.index.date > (sig.filled_date or sig.signal_date)]
        if sig.status == "PENDING":
            if bars.empty:
                continue
            nb = bars.iloc[0]
            op = float(nb["Open"])
            # B 방식: 신호 다음 장 시가로 체결. 시가가 계획을 깨뜨리면 취소
            if op <= sig.stop_price:
                sig.status, sig.status_reason = "CANCELLED", f"다음 장 시가 {op:.2f}가 손절가 이하"
            else:
                rr = (sig.target_price - op) / (op - sig.stop_price) if op < sig.target_price else 0
                if rr < ex["rr_min"]:
                    sig.status, sig.status_reason = "CANCELLED", f"갭 상승으로 손익비 {rr:.2f} < {ex['rr_min']}"
                else:
                    sig.status, sig.fill_price, sig.filled_date = "ACTIVE", op, bars.index[0].date()
                    sig.peak_price, sig.holding_days = op, 0
            changed += 1
            if sig.status != "ACTIVE":
                continue
        # 체결일 봉부터 매번 처음부터 다시 계산한다 (여러 번 돌아도 보유일·고점이 중복 누적되지 않음)
        sig.holding_days, sig.peak_price = 0, sig.fill_price
        _advance_active(sig, df[df.index.date >= sig.filled_date], st, fee)
        changed += 1
    db.session.commit()
    return changed


def _advance_active(sig, bars, st, fee):
    ex = st["params"]["exit"]
    for idx, b in bars.iterrows():
        d = idx.date()
        if sig.filled_date and d < sig.filled_date:
            continue
        if d != sig.filled_date:
            sig.holding_days = (sig.holding_days or 0) + 1
        o, h, l, c = float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Close"])
        stop = sig.stop_price
        # 트레일링 스탑: 수익이 기준 이상이면 고점 대비 일정 % 아래로 손절선을 올린다
        if ex.get("trail_trigger_pct") and sig.peak_price:
            if sig.peak_price >= sig.fill_price * (1 + ex["trail_trigger_pct"] / 100):
                stop = max(stop, sig.peak_price * (1 - ex["trail_pct"] / 100))
        exit_px, status = None, None
        first_bar = (d == sig.filled_date)
        if not first_bar and o <= stop:
            exit_px, status = o, ("TRAILING_STOP" if stop > sig.stop_price else "STOP_LOSS")
        elif not first_bar and o >= sig.target_price:
            exit_px, status = o, "TARGET_HIT"
        elif l <= stop:                         # 같은 봉에서 손절·목표 모두 닿으면 보수적으로 손절 먼저
            exit_px, status = stop, ("TRAILING_STOP" if stop > sig.stop_price else "STOP_LOSS")
        elif h >= sig.target_price:
            exit_px, status = sig.target_price, "TARGET_HIT"
        elif (sig.holding_days or 0) >= ex["time_stop_days"]:
            exit_px, status = c, "TIME_STOP"
        sig.peak_price = max(sig.peak_price or h, h)
        if status:
            sig.status, sig.exit_price, sig.exit_date = status, exit_px, d
            sig.pnl_pct = round(_fee_adj_pnl(sig.fill_price, exit_px, fee), 2)
            return


def apply_extra_exits(sig_rows, feats, strategies, assessments):
    """종가 기준 추가 청산: 기술적 추세 붕괴(스윙), AI 악재(AI 봇)."""
    strat = {s["key"]: s for s in strategies}
    for sig in sig_rows:
        if sig.status != "ACTIVE":
            continue
        st, x = strat.get(sig.strategy_key), feats.get(sig.ticker)
        if not st or not x or x["date"] < (sig.filled_date or x["date"]):
            continue
        fee = st["params"].get("fee_pct", 0.15)
        reason = None
        if st["kind"] == "swing" and st["params"]["exit"].get("trend_exit"):
            if (x["prev_rsi"] or 0) >= 70 > (x["rsi"] or 0) and x["macd_dead"] and x["close"] < (x["ma20"] or 0):
                reason = ("TREND_EXIT", "RSI 70 이탈 + MACD 데드크로스 + 20일선 이탈")
        a = assessments.get(sig.ticker)
        if st["use_ai"] and a is not None and (a.fatal_risk or (st["kind"] == "value" and a.structural_risk)):
            reason = ("AI_RISK_EXIT", f"AI 악재 감지: {a.summary or ''}"[:200])
        if reason:
            sig.status, sig.status_reason = reason
            sig.exit_price, sig.exit_date = x["close"], x["date"]
            sig.pnl_pct = round(_fee_adj_pnl(sig.fill_price, x["close"], fee), 2)


# ── 배치 본체 ──────────────────────────────────────────────────────────────────
def run_market(market, dry_run=False, tickers=None, data=None, vix=None):
    """한 시장의 하루치 배치. data/tickers/vix 를 넘기면 다운로드 대신 사용(테스트용)."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "이미 실행 중"}
    t0 = time.time()
    try:
        S, db = D["AiSignal"], D["db"]
        strategies = load_strategies()
        names = dict(universe(market))
        if tickers is None:
            tickers = list(names)
        external = data is not None          # 테스트처럼 데이터를 직접 넘긴 경우엔 날짜 검사 생략
        if data is None:
            data = download_daily(tickers)
        if not data:
            return {"ok": False, "error": "시세 데이터 없음"}
        if vix is None:
            try:
                vdf = download_daily(["^VIX"], period="6mo", chunk=1, pause=0)
                vix = float(vdf["^VIX"]["Close"].iloc[-1]) if "^VIX" in vdf else None
            except Exception:
                vix = None
        data = _drop_unfinished_bar(market, data)
        feats = {}
        for t, df in data.items():
            try:
                feats[t] = compute_features(df)
            except Exception:
                pass
        as_of = max((x["date"] for x in feats.values()), default=None)
        summary = {"market": market, "as_of": str(as_of), "tickers": len(feats), "vix": vix,
                   "tracked": 0, "candidates": {}, "rejected": {}, "signals": {}, "ai_calls_used": 0}

        # 2) 사후 추적
        summary["tracked"] = track_signals(market, data, strategies)
        if not external and as_of != market_today(market):
            # 휴장일이거나 장중 수동 실행(오늘 봉 미완성) → 추적만 하고 신규 신호는 만들지 않음
            summary.update(ok=True, skipped="오늘 완성된 봉이 없음 (휴장일 또는 장 마감 전)",
                           seconds=round(time.time() - t0, 1))
            log(f"run {market}: 신규 신호 생략 — as_of={as_of}")
            return summary

        # 3)~4) 공통 필터 + 정량 1차
        cooldown_from = datetime.utcnow() - timedelta(hours=COMMON["cooldown_hours"])
        recent = {(r.strategy_key, r.ticker) for r in
                  S.query.filter(S.market == market, S.created_at >= cooldown_from).all()}
        cand = {}          # ticker → [(strat, path, qscore, ev)]
        for t, x in feats.items():
            if x["date"] != as_of:                       # 오늘 봉이 없는 종목(거래정지 등)
                continue
            if x["vol_ratio"] is None or x["vol_ratio"] < COMMON["vol_ratio_floor"]:
                continue
            for st in strategies:
                if (st["key"], t) in recent:
                    continue
                ok, path, qs, ev = quant_pass(st, x)
                if ok:
                    cand.setdefault(t, []).append((st, path, qs, ev))
        for st in strategies:
            summary["candidates"][st["key"]] = sum(1 for v in cand.values() for c in v if c[0]["key"] == st["key"])

        # 5) 펀더멘탈: 정량 점수 높은 순으로 상한까지
        ranked = sorted(cand, key=lambda t: -max(c[2] for c in cand[t]))[:COMMON["max_fund_candidates"]]
        fund = {}
        for t in ranked:
            try:
                fund[t] = fundamentals(t, data[t])
            except Exception as e:
                log(f"{t} 펀더멘탈 실패: {e}")
                fund[t] = {}
            time.sleep(0.3)

        # 6) AI 평가: 보유(ACTIVE) 종목 먼저, 그다음 AI 봇 후보 (종목당 1회 공유)
        ai_keys = {s["key"] for s in strategies if s["use_ai"]}
        active = S.query.filter(S.market == market, S.status == "ACTIVE").all()
        need_ai = [s.ticker for s in active if s.strategy_key in ai_keys]
        need_ai += [t for t in ranked if any(c[0]["use_ai"] for c in cand[t])]
        seen, assess = set(), {}
        before = _budget_used(datetime.now(_tz("KR")).date())
        for t in need_ai:
            if t in seen or t not in feats:
                continue
            seen.add(t)
            if dry_run:
                continue
            news = _news_for(t)
            a = get_or_create_assessment(t, names.get(t, t), as_of, feats[t], fund.get(t), news)
            if a is not None:
                assess[t] = a
        summary["ai_calls_used"] = _budget_used(datetime.now(_tz("KR")).date()) - before
        summary["ai_budget_left"] = max(0, ai_budget_limit() - _budget_used(datetime.now(_tz("KR")).date()))

        apply_extra_exits(active, feats, strategies, assess)

        # 7) 봇별 최종 판정
        open_count = {}
        for r in S.query.filter(S.status.in_(["PENDING", "ACTIVE"])).all():
            open_count[r.strategy_key] = open_count.get(r.strategy_key, 0) + 1
        proposals = {st["key"]: [] for st in strategies}
        for t in ranked:
            x, fd = feats[t], fund.get(t) or {}
            for st, path, qs, ev in cand[t]:
                p = st["params"]
                why = _final_check(st, path, x, fd, assess.get(t), vix, as_of)
                if why:
                    summary["rejected"].setdefault(st["key"], []).append({"ticker": t, "why": why})
                    continue
                e, stop, target, rr, rej = plan_levels(st, path, x, fd)
                if rej:
                    summary["rejected"].setdefault(st["key"], []).append({"ticker": t, "why": rej})
                    continue
                a = assess.get(t)
                conf = round(0.6 * a.confidence + 0.4 * qs) if (st["use_ai"] and a and a.confidence is not None) else qs
                if st["use_ai"] and conf < p["ai_conf_min"]:
                    summary["rejected"].setdefault(st["key"], []).append({"ticker": t, "why": f"신뢰도 {conf} < {p['ai_conf_min']}"})
                    continue
                proposals[st["key"]].append(dict(t=t, st=st, path=path, qs=qs, ev=ev, x=x, fd=fd, a=a,
                                                 e=e, stop=stop, target=target, rr=rr, conf=conf))
        created = []
        for st in strategies:
            r = st["params"]["risk"]
            made_today = S.query.filter(S.strategy_key == st["key"], S.signal_date == as_of).count()   # 두 시장 합산
            slots = max(0, min(r["max_new_per_day"] - made_today, r["max_positions"] - open_count.get(st["key"], 0)))
            dl = day_loss_pct(st["key"], st, data, as_of)
            summary.setdefault("day_pnl_pct", {})[st["key"]] = dl
            if dl <= -r["daily_loss_limit_pct"]:
                if proposals[st["key"]]:
                    summary["rejected"].setdefault(st["key"], []).append(
                        {"ticker": "*", "why": f"오늘 손실 {dl}% ≤ -{r['daily_loss_limit_pct']}% → 신규 매수 중단"})
                slots = 0
            picks = sorted(proposals[st["key"]], key=lambda z: (-z["conf"], -z["rr"]))[:slots]
            for z in picks:
                sig = S(strategy_key=st["key"], ticker=z["t"], name=names.get(z["t"], z["t"]), market=market,
                        side="BUY", signal_date=as_of, price_at_signal=z["x"]["close"], entry_price=z["e"],
                        target_price=round(z["target"], 4), stop_price=round(z["stop"], 4), rr=round(z["rr"], 2),
                        confidence=int(z["conf"]), quant_score=int(z["qs"]),
                        assessment_id=(z["a"].id if (st["use_ai"] and z["a"]) else None),
                        reason=_one_line(st, z), evidence_json=json.dumps(_evidence(st, z), ensure_ascii=False, default=str),
                        status="PENDING")
                if not dry_run:
                    db.session.add(sig)
                created.append((st["key"], z["t"]))
            summary["signals"][st["key"]] = [t for k, t in created if k == st["key"]]
            dropped = len(proposals[st["key"]]) - len(picks)
            if dropped > 0:
                summary["rejected"].setdefault(st["key"], []).append({"ticker": "*", "why": f"하루 신규·최대 보유 제한으로 {dropped}건 보류"})
        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
        summary["seconds"] = round(time.time() - t0, 1)
        summary["ok"] = True
        log(f"run {market} as_of={as_of} tickers={len(feats)} signals={sum(len(v) for v in summary['signals'].values())} "
            f"ai_calls={summary['ai_calls_used']} {summary['seconds']}s")
        if not dry_run:
            D["ai_cache"].put(f"bot_run:{market}:{as_of}", None, "bot_run", json.loads(json.dumps(summary, default=str)),
                              None, 30 * 86400)
        return summary
    finally:
        _RUN_LOCK.release()


def _drop_unfinished_bar(market, data):
    """장 마감 전(현지 시각이 배치 시각 이전)에 돌리면 오늘 봉은 아직 미완성이라 뺀다."""
    now = datetime.now(_tz(market))
    if (now.hour, now.minute) >= RUN_AFTER[market] or now.weekday() >= 5:
        return data
    today = now.date()
    return {t: (df[df.index.date < today] if len(df) and df.index[-1].date() == today else df)
            for t, df in data.items()}


def day_loss_pct(strategy_key, st, data, as_of):
    """하루 손실 한도 판정용 근사치(%): 봇 자산 대비 오늘 손익 = Σ(포지션 비중 × 오늘 등락).
    보유 중이거나 오늘 청산된 신호만 본다. 봇 실계좌(Step 3)가 생기면 실제 자산 변화로 바꾼다."""
    S = D["AiSignal"]
    w = st["params"]["risk"]["max_weight"]
    total = 0.0
    rows = S.query.filter(S.strategy_key == strategy_key, S.fill_price.isnot(None),
                          (S.status == "ACTIVE") | (S.exit_date == as_of)).all()
    for r in rows:
        df = data.get(r.ticker)
        if df is None or df.empty or df.index[-1].date() != as_of:
            continue
        base = r.fill_price if r.filled_date == as_of else (float(df["Close"].iloc[-2]) if len(df) > 1 else None)
        end = r.exit_price if (r.exit_date == as_of and r.exit_price) else float(df["Close"].iloc[-1])
        if base:
            total += w * (end / base - 1) * 100
    return round(total, 2)


def _news_for(ticker):
    try:
        if market_of(ticker) == "KR":
            return D["fetch_kr_news"](ticker.split(".")[0]) or []
        import yfinance as yf
        return D["fetch_news"](yf.Ticker(ticker)) or []
    except Exception:
        return []


def _final_check(st, path, x, fd, a, vix, as_of):
    """펀더멘탈·실적일·VIX·AI 조건. 통과면 None, 아니면 사유."""
    p = st["params"]
    ed = fd.get("earnings_date")
    if ed:
        try:
            days = (date.fromisoformat(ed) - as_of).days
            if 0 <= days <= COMMON["earnings_block_days"]:
                return f"실적 발표 D-{days}"
        except Exception:
            pass
    fs = fd.get("fund_score")
    if st["kind"] == "swing":
        need = p["rebound"]["fund_min"] if path == "rebound" else p["fund_min"]
        if fs is None or fs < need:
            return f"펀더멘탈 {fs} < {need}"
    if st["kind"] == "value":
        if fs is None or fs < p["fund_min"]:
            return f"펀더멘탈 {fs} < {p['fund_min']}"
        refs = [v for v in (fd.get("dcf_fair"), fd.get("analyst_target")) if v]
        up = (max(refs) / x["close"] - 1) * 100 if refs else None
        if up is None or up < p["upside_min_pct"]:
            return f"상승 여력 {None if up is None else round(up, 1)}% < {p['upside_min_pct']}%"
        if vix is not None and vix >= p["vix_max"]:
            return f"VIX {vix:.1f} ≥ {p['vix_max']} (신규 진입 보류)"
    if st["use_ai"]:
        if a is None:
            return "AI 평가 없음 (예산 초과 또는 실패)"
        if a.verdict == "reject" or a.fatal_risk:
            return "AI 거부 / 치명적 악재"
        if st["kind"] == "value" and a.structural_risk:
            return "AI 구조적 악재"
        if st["kind"] == "momentum" and p["entry"].get("require_catalyst") and not a.catalyst:
            return "AI 단기 호재 미확인"
    return None


def _one_line(st, z):
    a = z["a"] if st["use_ai"] else None
    path = {"momentum": "모멘텀", "breakout": "추세 돌파", "rebound": "과매도 반등", "value_rebound": "가치 반등"}.get(z["path"], z["path"])
    tail = f" · {a.summary}" if (a and a.summary) else ""
    return f"{path} · 손익비 {z['rr']:.1f} · RSI {z['x']['rsi']:.0f}{tail}"[:300]


def _evidence(st, z):
    x = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in z["x"].items()}
    return {"strategy": st["key"], "path": z["path"], "features": x, "quant": z["ev"], "quant_score": z["qs"],
            "fundamentals": {k: v for k, v in (z["fd"] or {}).items() if k != "info"},
            "ai": ({"id": z["a"].id, "confidence": z["a"].confidence, "verdict": z["a"].verdict,
                    "summary": z["a"].summary} if (st["use_ai"] and z["a"]) else None),
            "levels": {"entry": z["e"], "stop": z["stop"], "target": z["target"], "rr": z["rr"]},
            "params": st["params"]}


# ── 스케줄러 (장 마감 후 하루 1회, 여러 프로세스여도 한 곳만 실행) ──────────────────
RUN_AFTER = {"KR": (15, 45), "US": (16, 30)}     # 각 시장 현지 시각


def _claim(market, day):
    """DB 유니크 키로 '오늘 이 시장 배치'를 선점. 이미 누가 했으면 False."""
    Model, db = D["AiCache"], D["db"]
    try:
        db.session.add(Model(cache_key=f"bot_lock:{market}:{day.isoformat()}", ticker=None, cache_type="bot_lock",
                             payload_json="{}", created_at=datetime.utcnow(),
                             expires_at=datetime.utcnow() + timedelta(days=3)))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _scheduler_loop():
    time.sleep(30)
    while True:
        try:
            with D["app"].app_context():
                for market in ("KR", "US"):
                    now = datetime.now(_tz(market))
                    hh, mm = RUN_AFTER[market]
                    if now.weekday() >= 5 or (now.hour, now.minute) < (hh, mm):
                        continue
                    if _claim(market, now.date()):
                        log(f"scheduler: {market} {now.date()} 배치 시작")
                        try:
                            run_market(market)
                        except Exception as e:
                            log(f"scheduler: {market} 배치 실패: {e}")
                        finally:
                            D["db"].session.remove()
        except Exception as e:
            log(f"scheduler loop 오류: {e}")
        time.sleep(60)


_started = False


def start_scheduler():
    global _started
    if _started or os.environ.get("BOT_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_scheduler_loop, daemon=True, name="bot-scheduler").start()
    log("scheduler 시작 (BOT_SCHEDULER=0 으로 끌 수 있음)")
