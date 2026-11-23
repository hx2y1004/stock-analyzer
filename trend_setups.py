"""추세 셋업 2단 하이브리드 (장 마감 확정 + 장중 경량 갱신).

1단 (장 마감 확정, 무거운 계산은 하루 1번)
  · AI 봇 장 마감 배치(bot_engine.run_market: 한국 15:45, 미국 16:30 ET = 한국 06:30)가 이미 받은
    유니버스 전체 1년 일봉으로 trends_scanner.score_df 를 돌려 trend_setups 표에 저장 (추가 다운로드 없음).
  · 펀더멘탈 점수는 스크리너 스냅샷의 4팩터 점수(7일 순환 갱신)를 쓴다 → 종목별 재무 조회 없음.
  · 표가 비었거나 오래됐으면(배포 직후 등) 백그라운드에서 40종목씩 나눠 한 번 채운다.
2단 (장중 경량 갱신, 계산 없음)
  · 섹터맵 15분 갱신(sector_map.refresh → on_rows)의 현재가·등락률·거래량을 돌파 후보에만 덮어쓴다.
  · 전날 'BB 수축' 또는 '52주 고점 -3% 이내' 후보의 현재가가 돌파 기준가(pivot)를 넘으면 ⚡ 장중 돌파.
  · '현시점 갱신'(refresh_live) 은 돌파 후보 + 화면에 보이는 상위 셋업(합쳐 200종목 이하)을 토스 실시간 시세
    (200종목 1번 호출, 20초 공용 캐시)로 다시 받는다. 토스 미설정·실패 종목만 네이버(한국) / Yahoo(미국)로 채운다.
    등락률은 확정 종가(전일 종가) 대비로 계산하고 거래량은 기존 값을 둔다. 시장별 15초 쿨다운.
요청은 DB 조회만 한다. app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time
from datetime import datetime, timedelta

import toss_api
import trends_scanner

D = {}
MIN_SCORE = 15            # 목록에 올릴 최소 총점 (장중 돌파 종목은 예외)
LIST_LIMIT = 40           # 시장별 최대 표시 개수
REFRESH_MAX = 200         # '현시점 갱신' 대상 상한 (토스 1번 호출 = 200종목)
REFRESH_COOLDOWN = 15     # 초
BREAKOUT_PIN_MIN = 25     # ⚡ 장중 돌파를 맨 위로 올리는 최소 총점 (하락 추세 저점수 종목이 돌파만으로 1위가 되지 않게)
_CTX = {"ts": 0, "v": None}
_LIVE_TS = {}             # market → 마지막 현시점 갱신 시각
_REFRESH_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
_STATUS = {}              # market → {state, progress, total, started_at, finished_at, message}
SESSION = {"KR": ((9, 0), (15, 30)), "US": ((9, 30), (16, 0))}


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[TRENDS] {msg}")
    except Exception:
        print(f"[TRENDS] {msg}", flush=True)


def market_of(t):
    return "KR" if t.upper().endswith((".KS", ".KQ")) else "US"


def _tz(market):
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")


def market_today(market):
    return datetime.now(_tz(market)).date()


def session_progress(market, now=None):
    """정규장 경과 비율 (0~1). 장 밖이면 None (주말 포함, 공휴일은 모름)."""
    now = now or datetime.now(_tz(market))
    if now.weekday() >= 5:
        return None
    (oh, om), (ch, cm) = SESSION[market]
    o = now.replace(hour=oh, minute=om, second=0, microsecond=0)
    c = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
    if not (o <= now <= c):
        return None
    return (now - o).total_seconds() / (c - o).total_seconds()


def universe_count(market):
    return len(D["universe"](market))


# ── 1단: 장 마감 확정 ────────────────────────────────────────────────────────────
def market_context():
    """S&P500·KOSPI 환경 (1시간 캐시)."""
    if _CTX["v"] is None or time.time() - _CTX["ts"] > 3600:
        _CTX["v"] = trends_scanner.fetch_market_context(D.get("download_daily"))
        _CTX["ts"] = time.time()
    return _CTX["v"]


def _fund_map(market):
    S = D["Snap"]
    rows = S.query.filter_by(market=market).with_entities(
        S.ticker, S.fundamental_score, S.growth_score, S.quality_score).all()
    return {t: {"fundamental_score": f, "growth_score": g, "quality_score": q} for t, f, g, q in rows}


def _score_all(market, data, names, ctx, funds):
    out = []
    for t, df in data.items():
        if market_of(t) != market:
            continue
        r = trends_scanner.score_df(t, names.get(t) or t, df, ctx, funds.get(t))
        if r:
            out.append(r)
    return out


def _save(market, results):
    """점수 결과 저장. 패턴을 새로 확정했으니 장중 값(live_*)은 비운다."""
    M, db = D["Model"], D["db"]
    existing = {r.ticker: r for r in M.query.filter(M.ticker.in_([x["ticker"] for x in results])).all()} if results else {}
    now = datetime.utcnow()
    for x in results:
        r = existing.get(x["ticker"])
        if r is None:
            r = M(ticker=x["ticker"], market=market)
            db.session.add(r)
        r.name = x["name"]
        r.price, r.change_pct = x["price"], x["change_pct"]
        r.tech_score, r.momentum_score, r.fund_score, r.total_score = (
            x["tech_score"], x["momentum_score"], x["fund_score"], x["total_score"])
        r.signals = json.dumps(x["signals"], ensure_ascii=False)
        r.sparkline = json.dumps(x["sparkline"])
        for k in ("stage2", "perfect", "new_high", "near_high", "bb_squeeze", "pivot", "pivot_kind",
                  "high52", "avg_volume20", "base_date"):
            setattr(r, k, x[k])
        r.computed_at = now
        r.live_price = r.live_change_pct = r.live_volume = r.live_at = r.breakout_at = None
    db.session.commit()
    return len(results)


def on_batch(market, data):
    """AI 봇 장 마감 배치 훅: 이미 받은 일봉(data: ticker → df)으로 유니버스 전체를 다시 확정."""
    t0 = time.time()
    try:
        with D["app"].app_context():
            names = dict(D["universe"](market))
            n = _save(market, _score_all(market, data, names, market_context(), _fund_map(market)))
        log(f"{market} 장 마감 확정 {n}종목 ({time.time() - t0:.1f}s)")
        return n
    except Exception as e:
        log(f"{market} 장 마감 확정 실패: {e}")
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        return 0


def rebuild(market, chunk=40):
    """일봉을 40종목씩 직접 받아 확정 (배포 직후 워밍업 · 소유자 진단용 전체 재스캔). 진행률 = 유니버스 종목 수 기준."""
    if not _BUILD_LOCK.acquire(blocking=False):
        return False
    try:
        univ = D["universe"](market)
        names = dict(univ)
        tickers = list(names)
        _STATUS[market] = {"state": "running", "progress": 0, "total": len(tickers), "started_at": time.time()}
        ctx = market_context()
        with D["app"].app_context():
            funds = _fund_map(market)
        saved = 0
        for i in range(0, len(tickers), chunk):
            while D["bot_lock"]():                     # 봇 배치·장중 체크 중이면 기다림
                time.sleep(10)
            part = tickers[i:i + chunk]
            data = D["download_daily"](part, period="1y", chunk=chunk, pause=0)
            data = D["drop_unfinished"](market, data)    # 장중이면 미완성 오늘 봉은 뺀다
            with D["app"].app_context():
                saved += _save(market, _score_all(market, data, names, ctx, funds))
            _STATUS[market]["progress"] = min(len(tickers), i + len(part))
            del data
            gc.collect()
            time.sleep(1)
        _STATUS[market].update(state="done", finished_at=time.time(), saved=saved)
        log(f"{market} 재스캔 끝: {saved}/{len(tickers)}종목")
        return True
    except Exception as e:
        _STATUS[market] = {"state": "error", "message": str(e)}
        log(f"{market} 재스캔 실패: {e}")
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        return False
    finally:
        _BUILD_LOCK.release()


def start_rebuild(market):
    """소유자 진단: 백그라운드 전체 재스캔. market=ALL 이면 한국 → 미국 순서."""
    markets = ["KR", "US"] if market == "ALL" else [market]
    if _BUILD_LOCK.locked():
        return {"state": "running"}
    for m in markets:
        _STATUS[m] = {"state": "queued", "progress": 0, "total": universe_count(m)}

    def run():
        for m in markets:
            rebuild(m)
    threading.Thread(target=run, daemon=True, name="trends-rebuild").start()
    return {"state": "running", "markets": markets}


def build_status(market):
    markets = ["KR", "US"] if market == "ALL" else [market]
    st = {m: dict(_STATUS.get(m) or {"state": "idle"}) for m in markets}
    total = sum(universe_count(m) for m in markets)
    progress = sum((st[m].get("progress") or 0) if st[m].get("state") != "done" else universe_count(m)
                   for m in markets if st[m].get("state") in ("running", "done", "queued"))
    states = {s["state"] for s in st.values()}
    state = "running" if states & {"running", "queued"} else ("error" if "error" in states else
                                                               ("done" if states == {"done"} else "idle"))
    return {"state": state, "progress": progress, "total": total, "markets": st}


# ── 2단: 장중 경량 갱신 ──────────────────────────────────────────────────────────
def apply_live(market, rows):
    """현재가 목록(rows: ticker·price·change_pct·volume)을 셋업 종목에 덮어쓴다. 돌파 판정은 기준가(pivot)가 있는 종목만.
    → 반영한 종목 수"""
    M, db = D["Model"], D["db"]
    try:
        with D["app"].app_context():
            today = market_today(market)
            want = {x.get("ticker") for x in rows if x.get("ticker")}
            if not want:
                return 0
            cands = {r.ticker: r for r in M.query.filter(M.market == market).all() if r.ticker in want}
            if not cands:
                return 0
            now = datetime.utcnow()
            n = 0
            for x in rows:
                r = cands.get(x.get("ticker"))
                p = x.get("price")
                if r is None or not p:
                    continue
                # 패턴 확정일(또는 그 이전) 시세 = 확정에 쓴 종가 → 장중 값이 아니다.
                # 시세 날짜를 모르면 현지 오늘 날짜로 대신 판단 (주말엔 금요일 종가가 오므로 날짜가 있으면 그걸 쓴다)
                d = _as_date(x.get("date")) or today
                if r.base_date and d <= r.base_date:
                    continue
                r.live_price = float(p)
                r.live_change_pct = x.get("change_pct")
                if x.get("volume") is not None:
                    r.live_volume = x.get("volume")
                r.live_at = now
                if r.pivot and r.live_price > r.pivot and r.breakout_at is None:
                    r.breakout_at = now
                n += 1
            db.session.commit()
            return n
    except Exception as e:
        log(f"{market} 장중 반영 실패: {e}")
        try:
            D["db"].session.rollback()
        except Exception:
            pass
        return 0


def _as_date(v):
    if not v:
        return None
    try:
        return v if hasattr(v, "year") else datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_toss_live(market, base):
    """토스 실시간 시세 (공용 20초 캐시, 200종목씩). base: {ticker: (확정 종가, 확정일)} → rows.
    등락률은 확정 종가가 오늘 이전 것이면 그것을 전일 종가로 보고 계산한다. 거래량은 넣지 않는다(기존 값 유지)."""
    got = toss_api.get_prices_cached(list(base))
    if not got:
        return []
    today = market_today(market)
    out = []
    for t, info in got.items():
        p = info.get("price")
        if not p:
            continue
        close, bdate = base.get(t, (None, None))
        prev = close if (close and bdate and bdate < today) else None
        out.append({"ticker": t, "price": float(p), "change_pct": toss_api.change_pct(p, prev, info),
                    "volume": None, "date": today, "source": "toss"})
    return out


def _fetch_kr_live(tickers):
    """네이버 실시간 폴링 (한 요청에 여러 종목, 약 1분 지연)."""
    import requests
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36", "Referer": "https://finance.naver.com/"}
    code2t = {t.split(".")[0]: t for t in tickers}
    codes = list(code2t)
    out = []

    def num(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None
    for i in range(0, len(codes), 50):          # 한 요청에 50종목까지 확인 (약 1초)
        try:
            r = requests.get("https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(codes[i:i + 50]),
                             headers=H, timeout=4)
            for d in (r.json() or {}).get("datas") or []:
                t = code2t.get(d.get("itemCode"))
                p = num(d.get("closePrice"))
                if t and p:
                    out.append({"ticker": t, "price": p, "change_pct": num(d.get("fluctuationsRatio")),
                                "volume": num(d.get("accumulatedTradingVolume")),
                                "date": (d.get("localTradedAt") or "")[:10] or None})
        except Exception as e:
            log(f"네이버 시세 실패: {e}")
    return out


def _fetch_us_live(tickers):
    """Yahoo 묶음 다운로드 (5일 일봉: 마지막 봉 = 오늘 진행 중 값)."""
    import pandas as pd
    import yfinance as yf
    out = []
    if not tickers:
        return out
    try:
        raw = yf.download(tickers, period="5d", interval="1d", group_by="ticker", auto_adjust=False,
                          threads=True, progress=False, timeout=8)
    except Exception as e:
        log(f"Yahoo 시세 실패: {e}")
        return out
    for t in tickers:
        try:
            df = (raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw)[["Close", "Volume"]].dropna()
            if len(df) < 2:
                continue
            cur, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
            if cur > 0 and prev > 0:
                out.append({"ticker": t, "price": round(cur, 4), "change_pct": round((cur / prev - 1) * 100, 2),
                            "volume": float(df["Volume"].iloc[-1]), "date": df.index[-1].date()})
        except Exception:
            continue
    return out


def refresh_live(market="ALL"):
    """'현시점 갱신': 장이 열린 시장의 돌파 후보 상위 50개 이하만 다시 받는다. → {refreshed, skipped, ...}"""
    markets = ["KR", "US"] if market == "ALL" else [market]
    res = {"refreshed": 0, "markets": {}}
    if not _REFRESH_LOCK.acquire(blocking=False):
        res["busy"] = True
        return res
    try:
        M = D["Model"]
        open_m = [m for m in markets if session_progress(m) is not None]
        for m in markets:
            if m not in open_m:
                res["markets"][m] = "closed"
                continue
            left = REFRESH_COOLDOWN - (time.time() - _LIVE_TS.get(m, 0))
            if left > 0:
                res["markets"][m] = "cooldown"
                res["cooldown"] = max(res.get("cooldown", 0), int(left) + 1)
                continue
        todo = [m for m in open_m if res["markets"].get(m) is None]
        if not todo:
            return res
        per = max(1, REFRESH_MAX // len(todo))
        res["sources"] = {}
        for m in todo:
            with D["app"].app_context():
                # 대상: 돌파 후보(점수순) + 화면에 보이는 상위 셋업 → 합쳐 per 개 이하
                cand = M.query.filter(M.market == m, M.pivot.isnot(None)).order_by(M.total_score.desc()).limit(per).all()
                top = M.query.filter(M.market == m, M.total_score >= MIN_SCORE).order_by(M.total_score.desc()).limit(LIST_LIMIT).all()
                base = {}
                for r in top + cand:
                    if len(base) >= per:
                        break
                    base.setdefault(r.ticker, (r.price, r.base_date))
            rows = _fetch_toss_live(m, base)
            got = {x["ticker"] for x in rows}
            rest = [t for t in base if t not in got]           # 토스 미설정·실패·미지원 → 기존 경로
            if rest:
                rows += _fetch_kr_live(rest) if m == "KR" else _fetch_us_live(rest)
            n = apply_live(m, rows)
            _LIVE_TS[m] = time.time()
            res["markets"][m] = "ok"
            res["refreshed"] += n
            res["sources"][m] = {"toss": len(got), "fallback": len(rest), "targets": len(base)}
        return res
    finally:
        _REFRESH_LOCK.release()


# ── 조회 ───────────────────────────────────────────────────────────────────────
def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def item_dict(r):
    live = bool(r.live_at and r.computed_at and r.live_at >= r.computed_at and r.live_price)
    breakout = bool(live and r.pivot and r.live_price > r.pivot)
    vol_pace = None
    if live and breakout and r.live_volume and r.avg_volume20:
        prog = session_progress(r.market)
        vol_pace = round(r.live_volume / (r.avg_volume20 * max(0.1, prog if prog is not None else 1.0)), 2)
    try:
        signals = json.loads(r.signals or "[]")
    except ValueError:
        signals = []
    try:
        spark = json.loads(r.sparkline or "[]")
    except ValueError:
        spark = []
    return {
        "ticker": r.ticker, "name": r.name, "market": r.market,
        "currency": "KRW" if r.market == "KR" else "USD",
        "price": r.live_price if live else r.price,
        "change_pct": r.live_change_pct if live and r.live_change_pct is not None else r.change_pct,
        "base_price": r.price, "live": live, "live_at": _iso(r.live_at) if live else None,
        "live_label": ("장중" if session_progress(r.market) is not None else "마감 시세") if live else None,
        "tech_score": r.tech_score, "momentum_score": r.momentum_score, "fund_score": r.fund_score,
        "total_score": r.total_score, "signals": signals, "sparkline": spark,
        "stage2": bool(r.stage2), "perfect": bool(r.perfect), "new_high": bool(r.new_high) or bool(
            breakout and r.pivot_kind == "52주 고점"),
        "near_high": bool(r.near_high), "bb_squeeze": bool(r.bb_squeeze),
        "pivot": r.pivot, "pivot_kind": r.pivot_kind, "breakout": breakout,
        "breakout_at": _iso(r.breakout_at) if breakout else None,
        "volume_pace": vol_pace, "base_date": r.base_date.isoformat() if r.base_date else None,
    }


def _rank(d):
    """정렬: 총점 BREAKOUT_PIN_MIN 이상인 ⚡ 장중 돌파 → 맨 위, 나머지는 총점·기술 점수 순 (저점수 돌파는 점수 자리에)."""
    pinned = bool(d["breakout"] and (d["total_score"] or 0) >= BREAKOUT_PIN_MIN)
    return (not pinned, -(d["total_score"] or 0), -(d["tech_score"] or 0))


def payload(market="ALL"):
    """GET /api/trends/setups → 시장별 상위 셋업 + 기준 시각·종목 수. DB 조회만."""
    M = D["Model"]
    markets = ["KR", "US"] if market == "ALL" else [market]
    items, meta = [], {}
    for m in markets:
        q = M.query.filter(M.market == m)
        analyzed = q.count()
        rows = q.filter((M.total_score >= MIN_SCORE) | (M.live_price > M.pivot)).all()
        ds = [item_dict(r) for r in rows]
        ds = [d for d in ds if d["total_score"] >= MIN_SCORE or d["breakout"]]
        ds.sort(key=_rank)
        newest = M.query.filter(M.market == m).order_by(M.computed_at.desc()).first()
        lives = [d["live_at"] for d in ds if d["live_at"]]
        meta[m] = {
            "universe": universe_count(m), "analyzed": analyzed, "hits": len(ds),
            "breakouts": sum(1 for d in ds if d["breakout"]),
            "base_date": newest.base_date.isoformat() if newest and newest.base_date else None,
            "computed_at": _iso(newest.computed_at) if newest else None,
            "live_at": max(lives) if lives else None,
            "session_open": session_progress(m) is not None,
        }
        items.extend(ds[:LIST_LIMIT])
    items.sort(key=_rank)
    st = build_status(market)
    return {
        "market": market, "items": items, "meta": meta,
        "universe": sum(meta[m]["universe"] for m in markets),
        "analyzed": sum(meta[m]["analyzed"] for m in markets),
        "hits": sum(meta[m]["hits"] for m in markets),
        "building": st["state"] == "running", "build": st if st["state"] != "idle" else None,
        "min_score": MIN_SCORE, "breakout_pin_min": BREAKOUT_PIN_MIN, "refresh_cooldown": REFRESH_COOLDOWN,
    }


# ── 백그라운드 워밍업 ────────────────────────────────────────────────────────────
def _needs_build(market):
    M = D["Model"]
    with D["app"].app_context():
        newest = M.query.filter(M.market == market).order_by(M.base_date.desc()).first()
    if newest is None or newest.base_date is None:
        return True
    return (market_today(market) - newest.base_date) > timedelta(days=5)


def _warm():
    time.sleep(70)                      # 스크리너·섹터맵 시작과 겹치지 않게 조금 늦게
    for m in ("KR", "US"):
        try:
            if _needs_build(m):
                log(f"{m} 셋업 표가 비었거나 오래됨 → 워밍업")
                rebuild(m)
        except Exception as e:
            log(f"{m} 워밍업 오류: {e}")


_started = False


def start():
    global _started
    if _started or os.environ.get("TRENDS_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_warm, daemon=True, name="trends-warm").start()
    log("워밍업 확인 시작 (TRENDS_SCHEDULER=0 으로 끌 수 있음)")
