"""종목 스크리너 · 종목 비교.

스냅샷(screener_snapshots, 종목당 1행)을 기존 배치가 채우고, 요청은 DB 쿼리만으로 답한다.
  · 가격·등락률·시총  ← 섹터맵 갱신(sector_map.refresh) 때 함께 (장중 15분 · 마감 후)
  · 기술 지표          ← AI 봇 장 마감 배치(bot_engine.run_market)가 계산한 값
  · 펀더멘탈·주당 지표·목표가 ← 7일보다 오래된 종목만 백그라운드로 6초에 1종목씩 (분기 단위로 바뀌는 값)
  · PER·선행PER·PBR·배당률·상승여력은 저장된 주당 지표 × 최신 가격으로 다시 계산 (추가 조회 없음)
처음 배포 직후(기술 지표가 비어 있으면) 40종목씩 나눠 한 번 채운다.

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import gc
import math
import os
import threading
import time
from datetime import datetime, timedelta, date

D = {}
FUND_MAX_AGE = timedelta(days=7)
FUND_PACE_SEC = 6
_HIST = {}                     # 비교용 1년 일봉 메모리 캐시: ticker → (ts, df)
_HIST_TTL, _HIST_MAX = 600, 30
_HIST_LOCK = threading.Lock()
_state = {"warming_tech": False, "fund_running": False}


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[SCREENER] {msg}")
    except Exception:
        print(f"[SCREENER] {msg}", flush=True)


def _f(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _num(s):
    """'1,234원' · '5.69배' · '0.92%' → float"""
    if s is None:
        return None
    t = str(s).replace(",", "").replace("원", "").replace("배", "").replace("%", "").strip()
    return _f(t)


def market_of(t):
    return "KR" if t.upper().endswith((".KS", ".KQ")) else "US"


def _sector_names():
    u = D["universe"]()
    return {m: dict(u["sectors"][m]) for m in ("KR", "US")}


# ── 행 준비 · 파생 지표 ──────────────────────────────────────────────────────────
def seed_rows():
    """유니버스 전체에 빈 행을 만들어 둔다 (펀더멘탈 순환 갱신이 바로 시작할 수 있게)."""
    M, db = D["Model"], D["db"]
    u = D["universe"]()
    names = _sector_names()
    have = {t for (t,) in db.session.query(M.ticker).all()}
    n = 0
    for m in ("KR", "US"):
        for t, v in u[m].items():
            if t in have:
                continue
            db.session.add(M(ticker=t, name=v.get("name") or t, market=m, sector=v.get("sector"),
                             sector_name=names[m].get(v.get("sector"))))
            n += 1
    if n:
        db.session.commit()
        log(f"빈 행 {n}개 생성")
    return n


def derive(r):
    """주당 지표 · 목표가 × 현재가 → PER · 선행PER · PBR · 배당률 · 상승여력"""
    p = r.price
    if not p or p <= 0:
        return
    r.per = round(p / r.eps, 2) if (r.eps and r.eps > 0) else None
    r.forward_per = round(p / r.forward_eps, 2) if (r.forward_eps and r.forward_eps > 0) else None
    r.pbr = round(p / r.bps, 2) if (r.bps and r.bps > 0) else None
    r.dividend_yield = round(r.dps / p * 100, 2) if (r.dps and r.dps > 0) else (0.0 if r.dps == 0 else None)
    r.upside_pct = round((r.target_price / p - 1) * 100, 1) if (r.target_price and r.target_price > 0) else None


def upsert_prices(market, rows):
    """섹터맵 갱신 결과(종목별 price · change_pct · market_cap · sector) 반영."""
    M, db = D["Model"], D["db"]
    try:
        with D["app"].app_context():
            existing = {r.ticker: r for r in M.query.filter_by(market=market).all()}
            names = _sector_names()[market]
            now = datetime.utcnow()
            for x in rows:
                r = existing.get(x["ticker"])
                if r is None:
                    r = M(ticker=x["ticker"], market=market)
                    db.session.add(r)
                r.name = x.get("name") or r.name
                r.sector, r.sector_name = x.get("sector"), names.get(x.get("sector"))
                r.price, r.change_pct = _f(x.get("price")), _f(x.get("change_pct"))
                if x.get("market_cap"):
                    r.market_cap = _f(x["market_cap"])
                r.price_updated_at = r.updated_at = now
                derive(r)
            db.session.commit()
    except Exception as e:
        log(f"가격 반영 실패 {market}: {e}")
        try:
            D["db"].session.rollback()
        except Exception:
            pass


def trend_score(x, high52=None):
    """0~100 추세 점수: 이동평균 정배열 30 · 20일선 위 15 · MACD 상승 15 · RSI 50~70 15 · 일목 구름 위 10 · 52주 고점 10% 이내 15"""
    s = 0
    c = x.get("close")
    if x.get("ma5") and x.get("ma20") and x.get("ma60") and x["ma5"] > x["ma20"] > x["ma60"]:
        s += 30
    if c and x.get("ma20") and c > x["ma20"]:
        s += 15
    if x.get("macd") is not None and x.get("macd_signal") is not None and x["macd"] > x["macd_signal"]:
        s += 15
    r = x.get("rsi")
    if r is not None:
        s += 15 if 50 <= r <= 70 else 7 if (45 <= r < 50 or 70 < r <= 75) else 0
    if x.get("above_cloud"):
        s += 10
    if c and high52 and c >= high52 * 0.9:
        s += 15
    return s


def tech_fields(x, df):
    hi = _f(df["High"].iloc[-252:].max()) if len(df) else None
    lo = _f(df["Low"].iloc[-252:].min()) if len(df) else None
    return {
        "rsi14": _f(x.get("rsi")), "bb_pos": _f(x.get("bb_pos")), "volume_ratio": _f(x.get("vol_ratio")),
        "ma_aligned": bool(x.get("ma5") and x.get("ma20") and x.get("ma60") and x["ma5"] > x["ma20"] > x["ma60"]),
        "trend_score": trend_score(x, hi), "high52": hi, "low52": lo,
    }


def upsert_technicals(market, feats, data):
    """AI 봇 배치가 계산한 지표(feats: ticker → compute_features 결과, data: ticker → 일봉) 반영."""
    M, db = D["Model"], D["db"]
    try:
        with D["app"].app_context():
            existing = {r.ticker: r for r in M.query.filter_by(market=market).all()}
            now = datetime.utcnow()
            n = 0
            for t, x in feats.items():
                df = data.get(t)
                r = existing.get(t)
                if r is None or df is None:
                    continue
                for k, v in tech_fields(x, df).items():
                    setattr(r, k, v)
                r.tech_updated_at = r.updated_at = now
                n += 1
            db.session.commit()
            return n
    except Exception as e:
        log(f"기술 지표 반영 실패 {market}: {e}")
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        return 0


def warm_technicals():
    """기술 지표가 비어 있는 시장만 40종목씩 받아 채운다 (최초 배포용, 한 번에 한 묶음만 메모리에)."""
    if _state["warming_tech"]:
        return
    _state["warming_tech"] = True
    try:
        M = D["Model"]
        for market in ("KR", "US"):
            with D["app"].app_context():
                todo = [r.ticker for r in M.query.filter_by(market=market).filter(M.tech_updated_at.is_(None)).all()]
            if not todo:
                continue
            log(f"{market} 기술 지표 워밍업 {len(todo)}종목")
            for i in range(0, len(todo), 40):
                while D["bot_lock"]():          # 봇 배치·장중 체크 중이면 기다림
                    time.sleep(10)
                part = todo[i:i + 40]
                data = D["download_daily"](part, period="1y", chunk=40, pause=0)
                feats = {}
                for t, df in data.items():
                    try:
                        feats[t] = D["compute_features"](df)
                    except Exception:
                        pass
                upsert_technicals(market, feats, data)
                del data, feats
                gc.collect()
                time.sleep(2)
        log("기술 지표 워밍업 끝")
    except Exception as e:
        log(f"기술 지표 워밍업 실패: {e}")
    finally:
        _state["warming_tech"] = False


# ── 펀더멘탈 (7일 순환) ─────────────────────────────────────────────────────────
def fetch_fundamentals(ticker):
    """종목 1개의 주당 지표·목표가·52주 고저·4팩터 점수. 실패한 항목은 None."""
    import yfinance as yf
    out = {}
    stock = yf.Ticker(ticker)
    try:
        info = stock.info or {}
    except Exception:
        info = {}
    if market_of(ticker) == "KR":
        # 네이버 한 번으로 EPS(최근 4분기)·추정EPS·BPS·주당배당금·목표가·52주 고저
        try:
            import requests
            j = requests.get(f"https://m.stock.naver.com/api/stock/{ticker.split('.')[0]}/integration",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
            tot = {x.get("code"): x.get("value") for x in (j.get("totalInfos") or [])}
            out.update(eps=_num(tot.get("eps")), forward_eps=_num(tot.get("cnsEps")), bps=_num(tot.get("bps")),
                       dps=_num(tot.get("dividend")), high52=_num(tot.get("highPriceOf52Weeks")),
                       low52=_num(tot.get("lowPriceOf52Weeks")),
                       target_price=_num((j.get("consensusInfo") or {}).get("priceTargetMean")))
        except Exception as e:
            log(f"{ticker} 네이버 지표 실패: {e}")
    else:
        out.update(eps=_f(info.get("trailingEps")), forward_eps=_f(info.get("forwardEps")),
                   bps=_f(info.get("bookValue")), dps=_f(info.get("dividendRate")) or (0.0 if info else None),
                   target_price=_f(info.get("targetMeanPrice")),
                   high52=_f(info.get("fiftyTwoWeekHigh")), low52=_f(info.get("fiftyTwoWeekLow")))
    try:
        from analysis.advanced import compute_advanced_metrics
        hist = stock.history(period="1y", interval="1d", auto_adjust=False)
        fac = (compute_advanced_metrics(stock, info, hist) or {}).get("factors") or {}
        sc = {k: _f((fac.get(k) or {}).get("score")) for k in ("quality", "value", "growth", "momentum")}
        qvg = [sc[k] for k in ("quality", "value", "growth") if sc[k] is not None]
        out.update(quality_score=sc["quality"], value_score=sc["value"], growth_score=sc["growth"],
                   momentum_score=sc["momentum"], fundamental_score=round(sum(qvg) / len(qvg), 1) if qvg else None)
    except Exception as e:
        log(f"{ticker} 4팩터 실패: {e}")
    return out


def refresh_fundamentals(ticker):
    """펀더멘탈을 받아 스냅샷에 저장. 실패해도 갱신 시각은 남겨 같은 종목만 계속 붙잡지 않게 한다."""
    M, db = D["Model"], D["db"]
    data = fetch_fundamentals(ticker)
    with D["app"].app_context():
        r = M.query.filter_by(ticker=ticker).first()
        if r is None:
            return None
        for k, v in data.items():
            if v is not None or k in ("target_price",):
                setattr(r, k, v)
        r.fund_updated_at = r.updated_at = datetime.utcnow()
        derive(r)
        db.session.commit()
        return {k: getattr(r, k) for k in ("fundamental_score", "per", "pbr", "upside_pct")}


def _next_stale():
    M = D["Model"]
    cutoff = datetime.utcnow() - FUND_MAX_AGE
    with D["app"].app_context():
        # 한 번도 안 받은 종목 → 오래된 종목 순, 같은 조건이면 시총 큰 종목부터
        r = (M.query.filter((M.fund_updated_at.is_(None)) | (M.fund_updated_at < cutoff))
             .order_by(M.fund_updated_at.isnot(None), M.fund_updated_at.asc(), M.market_cap.is_(None), M.market_cap.desc())
             .first())
        return r.ticker if r else None


def _loop():
    time.sleep(40)
    try:
        with D["app"].app_context():
            seed_rows()
    except Exception as e:
        log(f"빈 행 생성 실패: {e}")
    try:
        warm_technicals()
    except Exception as e:
        log(f"워밍업 오류: {e}")
    while True:
        try:
            if D["bot_lock"]():
                time.sleep(15)
                continue
            t = _next_stale()
            if not t:
                time.sleep(600)
                continue
            _state["fund_running"] = True
            refresh_fundamentals(t)
            gc.collect()
        except Exception as e:
            log(f"펀더멘탈 갱신 오류: {e}")
            try:
                D["db"].session.rollback()
            except Exception:
                pass
        finally:
            _state["fund_running"] = False
        time.sleep(FUND_PACE_SEC)


_started = False


def start():
    global _started
    if _started or os.environ.get("SCREENER_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="screener").start()
    log("백그라운드 시작 (SCREENER_SCHEDULER=0 으로 끌 수 있음)")


# ── 조회 ───────────────────────────────────────────────────────────────────────
PRESETS = [
    {"key": "quality_oversold", "emoji": "💎", "name": "우량주 과매도",
     "desc": "펀더멘탈 70점 이상인데 RSI 35 이하 또는 볼린저 밴드 하단 25% 이내로 눌린 종목", "sort": "fundamental"},
    {"key": "breakout_volume", "emoji": "🚀", "name": "추세 돌파 + 수급",
     "desc": "5·20·60일 이동평균 정배열, 거래량이 평균의 130% 이상, RSI 45~70", "sort": "volume_ratio"},
    {"key": "value", "emoji": "🏷️", "name": "저평가 가치주",
     "desc": "펀더멘탈 75점 이상, PER 0~15배, 목표가까지 상승여력 15% 이상", "sort": "upside_pct"},
    {"key": "momentum_today", "emoji": "🔥", "name": "당일 모멘텀",
     "desc": "오늘 3% 이상 오르고 거래량이 평균의 150% 이상", "sort": "change_pct"},
    {"key": "ai_signals", "emoji": "🤖", "name": "AI 봇 최근 신호",
     "desc": "최근 7일 안에 AI 봇 매수 신호가 나온 종목", "sort": "fundamental"},
]
SORTS = {"fundamental": "fundamental_score", "change_pct": "change_pct", "upside_pct": "upside_pct",
         "volume_ratio": "volume_ratio", "market_cap": "market_cap", "rsi": "rsi14", "per": "per",
         "trend": "trend_score"}


def _ai_signal_tickers(days=7):
    S = D["AiSignal"]
    since = date.today() - timedelta(days=days)
    rows = S.query.filter(S.signal_date >= since, S.side == "BUY").all()
    out = {}
    for s in rows:
        out.setdefault(s.ticker, []).append(s.strategy_key)
    return out


def query(args):
    """GET /api/screener 파라미터 → 결과 dict. DB 쿼리만 사용."""
    M = D["Model"]
    q = M.query.filter(M.price.isnot(None))
    market = (args.get("market") or "ALL").upper()
    if market in ("KR", "US"):
        q = q.filter(M.market == market)
    preset = args.get("preset") or ""
    ai_map = {}
    if preset == "quality_oversold":
        q = q.filter(M.fundamental_score >= 70, (M.rsi14 <= 35) | (M.bb_pos <= 25))
    elif preset == "breakout_volume":
        q = q.filter(M.ma_aligned.is_(True), M.volume_ratio >= 1.3, M.rsi14 >= 45, M.rsi14 <= 70)
    elif preset == "value":
        q = q.filter(M.fundamental_score >= 75, M.per > 0, M.per <= 15, M.upside_pct >= 15)
    elif preset == "momentum_today":
        q = q.filter(M.change_pct >= 3, M.volume_ratio >= 1.5)
    elif preset == "ai_signals":
        ai_map = _ai_signal_tickers()
        q = q.filter(M.ticker.in_(list(ai_map) or ["__none__"]))

    def num(k):
        v = args.get(k)
        return _f(v) if v not in (None, "") else None
    if args.get("sector"):
        q = q.filter(M.sector == args.get("sector"))
    if num("min_fundamental") is not None:
        q = q.filter(M.fundamental_score >= num("min_fundamental"))
    if num("min_rsi") is not None:
        q = q.filter(M.rsi14 >= num("min_rsi"))
    if num("max_rsi") is not None:
        q = q.filter(M.rsi14 <= num("max_rsi"))
    if num("max_per") is not None:
        q = q.filter(M.per > 0, M.per <= num("max_per"))
    if num("min_upside") is not None:
        q = q.filter(M.upside_pct >= num("min_upside"))

    pdef = next((p for p in PRESETS if p["key"] == preset), None)
    sort_key = args.get("sort_by") or (pdef or {}).get("sort") or "market_cap"
    col = getattr(M, SORTS.get(sort_key, "market_cap"))
    asc = (args.get("order") or "desc").lower() == "asc"
    q = q.order_by(col.is_(None), col.asc() if asc else col.desc(), M.market_cap.desc())
    try:
        limit = max(1, min(200, int(args.get("limit", 50))))
    except ValueError:
        limit = 50
    total = q.count()
    rows = q.limit(limit).all()
    return {"total": total, "items": [row_dict(r, ai_map.get(r.ticker)) for r in rows],
            "preset": pdef, "sort_by": sort_key, "order": "asc" if asc else "desc", "meta": coverage(market)}


def row_dict(r, ai=None):
    d = {k: getattr(r, k) for k in ("ticker", "name", "market", "sector", "sector_name", "price", "change_pct", "market_cap",
                                     "per", "forward_per", "pbr", "dividend_yield", "rsi14", "bb_pos", "volume_ratio",
                                     "ma_aligned", "trend_score", "fundamental_score", "upside_pct")}
    if ai:
        d["ai_strategies"] = sorted(set(ai))
    return d


def coverage(market="ALL"):
    """기준 시각과 채워진 정도 (빈 상태·집계 중 안내용)."""
    M = D["Model"]
    q = M.query
    if market in ("KR", "US"):
        q = q.filter(M.market == market)
    total = q.count()

    def latest(col):
        r = q.with_entities(col).filter(col.isnot(None)).order_by(col.desc()).first()
        return r[0].isoformat() + "Z" if r else None
    return {"universe": total,
            "with_price": q.filter(M.price.isnot(None)).count(),
            "with_tech": q.filter(M.tech_updated_at.isnot(None)).count(),
            "with_fund": q.filter(M.fund_updated_at.isnot(None)).count(),
            "price_updated_at": latest(M.price_updated_at), "tech_updated_at": latest(M.tech_updated_at),
            "fund_updated_at": latest(M.fund_updated_at),
            "warming": _state["warming_tech"]}


def sectors():
    names = _sector_names()
    return {m: [{"key": k, "name": n} for k, n in names[m].items()] for m in ("KR", "US")}


# ── 비교 ───────────────────────────────────────────────────────────────────────
def _history(tickers):
    """1년 일봉 (10분 메모리 캐시, 없는 종목만 한 번에 받음)."""
    now = time.time()
    with _HIST_LOCK:
        have = {t: v[1] for t, v in _HIST.items() if t in tickers and now - v[0] < _HIST_TTL}
    need = [t for t in tickers if t not in have]
    if need:
        got = D["download_daily"](need, period="1y", chunk=len(need), pause=0, min_rows=20)
        with _HIST_LOCK:
            for t, df in got.items():
                _HIST[t] = (now, df)
                have[t] = df
            for k in sorted(_HIST, key=lambda k: _HIST[k][0])[:max(0, len(_HIST) - _HIST_MAX)]:
                _HIST.pop(k, None)
    return have


def compare(tickers):
    """최대 3종목 비교 데이터. 스냅샷 재사용 + 1년 일봉 한 번 + (없을 때만) 펀더멘탈 1회."""
    M = D["Model"]
    tickers = [t for t in dict.fromkeys(t.strip().upper() for t in tickers if t.strip())][:3]
    hist = _history(tickers)
    stale = []
    for t in tickers:
        r = M.query.filter_by(ticker=t).first()
        if r is None or r.fund_updated_at is None or r.fund_updated_at < datetime.utcnow() - FUND_MAX_AGE:
            stale.append(t)
    for t in stale:                                    # 스냅샷에 없으면 행 만들고 펀더멘탈 1회
        if not M.query.filter_by(ticker=t).first():
            u = D["universe"]()
            info = u.get(market_of(t), {}).get(t, {})
            D["db"].session.add(M(ticker=t, market=market_of(t), name=info.get("name") or D["name_of"](t) or t,
                                  sector=info.get("sector"), sector_name=_sector_names()[market_of(t)].get(info.get("sector"))))
            D["db"].session.commit()
    if stale:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda t: _safe_refresh(t), stale))
        D["db"].session.expire_all()
    out = []
    for t in tickers:
        r = M.query.filter_by(ticker=t).first()
        df = hist.get(t)
        item = row_dict(r) if r else {"ticker": t, "name": t, "market": market_of(t)}
        item.update({k: getattr(r, k) for k in ("quality_score", "value_score", "growth_score", "momentum_score",
                                                "target_price", "high52", "low52")} if r else {})
        if df is not None and len(df) >= 20:
            x = D["compute_features"](df) if len(df) >= 70 else None
            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            if r is None or r.price is None:
                item["price"], item["change_pct"] = close, round((close / prev - 1) * 100, 2)
            if x:
                tf = tech_fields(x, df)
                item.update({k: v for k, v in tf.items() if k not in ("high52", "low52") or not item.get(k)})
                item["ma"] = {"ma5": x.get("ma5"), "ma20": x.get("ma20"), "ma60": x.get("ma60")}
            hi = item.get("high52") or _f(df["High"].max())
            lo = item.get("low52") or _f(df["Low"].min())
            px = item.get("price") or close
            item["pos52"] = round((px - lo) / (hi - lo) * 100, 1) if (hi and lo and hi > lo) else None
            s = df["Close"]
            series = {}
            for label, n in (("3M", 63), ("6M", 126), ("1Y", len(s))):
                part = s.iloc[-min(n, len(s)):]
                base = float(part.iloc[0])
                series[label] = [{"date": idx.strftime("%Y-%m-%d"), "value": round((float(v) / base - 1) * 100, 2)}
                                 for idx, v in part.items()] if base else []
            item["returns"] = {k: (v[-1]["value"] if v else None) for k, v in series.items()}
            item["series"] = series
        else:
            item["series"], item["returns"] = {}, {}
        out.append(item)
    return {"items": out, "tickers": tickers}


def _safe_refresh(t):
    try:
        with D["app"].app_context():
            return refresh_fundamentals(t)
    except Exception as e:
        log(f"{t} 비교용 펀더멘탈 실패: {e}")
