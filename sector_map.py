"""메인 대시보드 섹터맵 — 전체 유니버스 시총가중 집계 + 상승/하락 분포 + 백그라운드 스냅샷.

- 대상: sector_universe.json (stock_db.json 의 한국 ~350 / 미국 ~537 종목 + 섹터 분류 + 미국 발행주식수)
- 시세
  · 한국: 네이버 모바일 '시가총액순' 목록 API (100종목씩, 현재가·등락률·시가총액 한 번에)
  · 미국: yfinance 일봉 묶음 다운로드(40개씩) → 현재가·전일 대비, 시총 = 현재가 × 발행주식수
- 저장: 종목당 6개 필드(ticker, name, sector, market_cap, price, change_pct)와 섹터 집계만
  ai_cache 테이블의 `sectormap:{KR|US}` 한 줄 + 메모리. API 는 이 스냅샷만 즉시 돌려준다.
- 갱신(백그라운드 스레드, 시장 현지 시각)
  · 장중 15분마다 매시 7·22·37·52분 (봇 장중 체크는 매 5분 정각 → 겹치지 않음, 봇 작업 중이면 잠깐 미룸)
  · 장 마감 후 확정 1회 (한국 15:40, 미국 16:20)
  · 주말 · 휴장일(받은 데이터 날짜가 오늘이 아님) 은 그날 더 조회하지 않음
  · 서버 시작 때 스냅샷이 없으면 한 번 미리 만든다

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import gc
import json
import math
import os
import threading
import time
from datetime import datetime

import market_hours

D = {}
_MEM = {}                      # market → payload
_REFRESH_LOCK = threading.Lock()
_UNIV = None
HERE = os.path.dirname(os.path.abspath(__file__))
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

SESSION = {"KR": ((9, 0), (15, 30)), "US": ((9, 30), (16, 0))}
FINAL_AT = {"KR": (15, 40), "US": (16, 20)}


def _final_at(market, w):
    """장 마감 스냅샷 시각: 평소 FINAL_AT, 마감이 다른 날(미국 조기폐장 13:00 · 한국 수능일 16:30)은 그 마감 + 같은 간격."""
    if w[1] == SESSION[market][1]:
        return FINAL_AT[market]
    gap = (FINAL_AT[market][0] * 60 + FINAL_AT[market][1]) - (SESSION[market][1][0] * 60 + SESSION[market][1][1])
    t = w[1][0] * 60 + w[1][1] + gap
    return (t // 60, t % 60)
SLOT_MIN, SLOT_OFFSET = 15, 7  # 장중 15분 간격, 매 슬롯 7분째


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[SECTORMAP] {msg}")
    except Exception:
        print(f"[SECTORMAP] {msg}", flush=True)


def _tz(market):
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")


def universe():
    global _UNIV
    if _UNIV is None:
        with open(os.path.join(HERE, "sector_universe.json"), encoding="utf-8") as f:
            _UNIV = json.load(f)
        names = {s["symbol"]: s.get("name") for s in json.load(open(os.path.join(HERE, "stock_db.json"), encoding="utf-8"))}
        for m in ("KR", "US"):
            for t, v in _UNIV[m].items():
                v["name"] = names.get(t) or t
    return _UNIV


def _num(s):
    """'1,234' → 1234.0, 'N/A'·빈 값·NaN·무한대 → None"""
    try:
        v = float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ── 시세 받기 ──────────────────────────────────────────────────────────────────
def fetch_kr():
    """→ (rows, 데이터 날짜). 네이버 시총순 목록에서 우리 종목을 찾을 때까지 페이지를 넘긴다(시장당 최대 10쪽)."""
    import requests
    univ = universe()["KR"]
    want = {t.split(".")[0]: t for t in univ}
    found, dates = {}, {}
    for cat in ("KOSPI", "KOSDAQ"):
        need = {c for c, t in want.items() if t.endswith(".KS" if cat == "KOSPI" else ".KQ")}
        for page in range(1, 11):
            if need <= set(found):
                break
            r = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{cat}?page={page}&pageSize=100",
                             headers=H, timeout=10)
            r.raise_for_status()
            stocks = r.json().get("stocks") or []
            for s in stocks:
                code = s.get("itemCode")
                if code in want:
                    found[code] = s
                    d = (s.get("localTradedAt") or "")[:10]
                    if d:
                        dates[d] = dates.get(d, 0) + 1
            if len(stocks) < 100:
                break
            time.sleep(0.2)
    rows = []
    for code, t in want.items():
        s = found.get(code)
        if not s:
            continue
        price, chg, mv = _num(s.get("closePrice")), _num(s.get("fluctuationsRatio")), _num(s.get("marketValue"))
        if price is None or chg is None:
            continue
        rows.append({"ticker": t, "name": univ[t]["name"], "sector": univ[t]["sector"],
                     "market_cap": mv * 1e8 if mv else None,          # 네이버 시총 단위: 억원
                     "price": price, "change_pct": round(chg, 2),
                     # KRX 공식 전일 종가 (종가 − 전일 대비). 토스 경량 갱신 때 등락률 기준으로 쓴다
                     "prev_close": (price - _num(s.get("compareToPreviousClosePrice"))) if _num(s.get("compareToPreviousClosePrice")) is not None else None,
                     "volume": _num(s.get("accumulatedTradingVolume")),
                     "date": (s.get("localTradedAt") or "")[:10] or None})          # 시세 날짜 (장중 돌파 판정용)
    data_date = max(dates, key=dates.get) if dates else None
    return rows, data_date


def fetch_us(chunk=40, pause=1.0):
    """→ (rows, 데이터 날짜). 5일 일봉 묶음 다운로드: 마지막 봉 = 오늘(장중이면 진행 중 값), 그 전 봉 = 전일 종가."""
    import pandas as pd
    import yfinance as yf
    univ = universe()["US"]
    tickers = list(univ)
    rows, dates = [], {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period="5d", interval="1d", group_by="ticker", auto_adjust=False,
                              threads=True, progress=False)
        except Exception as e:
            log(f"US 다운로드 실패 {part[0]}…: {e}")
            continue
        for t in part:
            try:
                full = (raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw)[["Close", "Volume"]].dropna()
                df = full["Close"]
                if len(df) < 2:
                    continue
                cur, prev = _num(df.iloc[-1]), _num(df.iloc[-2])
                if not cur or not prev:
                    continue
                d = str(df.index[-1].date())
                dates[d] = dates.get(d, 0) + 1
                sh = univ[t].get("shares")
                rows.append({"ticker": t, "name": univ[t]["name"], "sector": univ[t]["sector"],
                             "market_cap": cur * sh if sh else None,
                             "price": round(cur, 2), "change_pct": round((cur / prev - 1) * 100, 2),
                             "volume": _num(full["Volume"].iloc[-1]), "date": d})
            except Exception:
                continue
        del raw
        time.sleep(pause)
    data_date = max(dates, key=dates.get) if dates else None
    return rows, data_date


# ── 토스 실시간 경량 갱신 ─────────────────────────────────────────────────────────
# 전체 받기(네이버 시총순 목록 / Yahoo 5일 일봉 묶음)는 무겁고(미국 540종목 다운로드) 시세가 늦다.
# 그날 처음(또는 1시간마다) 한 번 전체로 받아 전일 종가·시총·거래량 기준을 잡아 두고,
# 그 사이 15분 슬롯은 토스 실시간 시세(200종목씩 묶음, 공용 20초 캐시)만 받아 현재가·등락률·시총을 다시 계산한다.
_BASE = {}                  # market → {"date": 시세 날짜, "rows": {ticker: row}, "full_at": epoch}
FULL_EVERY = 3600           # 이 간격마다는 전체로 받아 거래량·기준을 새로 고침


def _toss_enabled():
    try:
        import toss_api
        return toss_api.is_enabled()
    except Exception:
        return False


def fetch_toss(market):
    """→ (rows, 데이터 날짜) | None. 오늘 기준(_BASE)이 없거나 토스가 절반 이상 실패하면 None → 전체 받기."""
    import toss_api
    base = _BASE.get(market)
    today = str(datetime.now(_tz(market)).date())
    if not base or base["date"] != today:
        return None
    tickers = list(base["rows"])
    got = toss_api.get_prices_cached(tickers)
    if len(got) < len(tickers) * 0.5:
        log(f"{market} 토스 시세 {len(got)}/{len(tickers)} → 전체 받기로")
        return None
    rows = []
    for t, b in base["rows"].items():
        info = got.get(t)
        r = dict(b)
        if info and info.get("price") and b.get("price") and b.get("change_pct") is not None:
            prev = b.get("prev_close") or b["price"] / (1 + b["change_pct"] / 100)   # 전일 종가 (한국은 KRX 공식값)
            p = float(info["price"])
            r["price"] = round(p, 2) if market == "US" else p
            r["change_pct"] = round((p / prev - 1) * 100, 2)
            if b.get("market_cap"):
                r["market_cap"] = b["market_cap"] * p / b["price"]
            r["date"] = today
        rows.append(r)
    return rows, today


# ── 집계 ───────────────────────────────────────────────────────────────────────
def aggregate(rows, sector_defs):
    """섹터별 시총가중 등락률·총시총·상승/하락/보합 수. 시총 없는 종목은 가중치에서만 빠지고 분포에는 들어간다."""
    by = {}
    for r in rows:
        by.setdefault(r["sector"], []).append(r)
    total_cap = sum(r["market_cap"] or 0 for r in rows) or 0
    groups = []
    for key, name in sector_defs:
        mem = by.get(key) or []
        if not mem:
            continue
        capped = [r for r in mem if r["market_cap"] and r["market_cap"] > 0]
        cap = sum(r["market_cap"] for r in capped)
        if cap > 0:
            chg = sum(r["market_cap"] * r["change_pct"] for r in capped) / cap
            basis = "cap_weighted"
        else:
            chg = sum(r["change_pct"] for r in mem) / len(mem)
            basis = "simple"
        adv = sum(1 for r in mem if r["change_pct"] > 0)
        dec = sum(1 for r in mem if r["change_pct"] < 0)
        mem.sort(key=lambda r: -(r["market_cap"] or 0))
        groups.append({
            "key": key, "name": name, "market_cap": round(cap), "cap_share": round(cap / total_cap * 100, 2) if total_cap else 0,
            "change_pct": round(chg, 2), "basis": basis,
            "total_count": len(mem), "advancing_count": adv, "declining_count": dec,
            "unchanged_count": len(mem) - adv - dec, "no_cap_count": len(mem) - len(capped),
            "members": mem,
        })
    groups.sort(key=lambda g: -g["market_cap"])
    return groups


# ── 스냅샷 ─────────────────────────────────────────────────────────────────────
def _key(market):
    return f"sectormap:{market}"


def get(market):
    """최신 스냅샷 (메모리 → DB). 없으면 None."""
    if market in _MEM:
        return _MEM[market]
    try:
        with D["app"].app_context():          # 백그라운드 스레드에서도 부를 수 있게
            hit = D["ai_cache"].get(_key(market), use_mem=False)
    except Exception:
        hit = None
    if hit and hit[0]:
        _MEM[market] = hit[0]
        return hit[0]
    return None


def refresh(market, reason="manual"):
    """시세를 받아 집계하고 스냅샷 저장. → payload | None (실패 시 이전 스냅샷 유지)"""
    if not _REFRESH_LOCK.acquire(blocking=False):
        return None
    t0 = time.time()
    try:
        got, via = None, "full"
        base = _BASE.get(market)
        if reason == "intraday" and _toss_enabled() and base and time.time() - base["full_at"] < FULL_EVERY:
            got = fetch_toss(market)
            via = "toss" if got else "full"
        if got:
            rows, data_date = got
        else:
            rows, data_date = fetch_kr() if market == "KR" else fetch_us()
            if rows:
                _BASE[market] = {"date": data_date, "rows": {r["ticker"]: r for r in rows}, "full_at": time.time()}
        if not rows:
            log(f"{market} 시세 없음 ({reason}) → 이전 스냅샷 유지")
            return None
        univ = universe()
        groups = aggregate(rows, univ["sectors"][market])
        payload = {
            "market": market, "updated_at": datetime.utcnow().isoformat() + "Z", "data_date": data_date,
            "reason": reason, "universe_count": len(univ[market]), "covered_count": len(rows),
            "advancing_count": sum(g["advancing_count"] for g in groups),
            "declining_count": sum(g["declining_count"] for g in groups),
            "unchanged_count": sum(g["unchanged_count"] for g in groups),
            "basis": "시가총액 가중 평균 등락률", "groups": groups,
            "source": ("토스증권 실시간 (전일 종가·거래량은 " + ("네이버 증권" if market == "KR" else "Yahoo Finance") + ")") if via == "toss"
                      else ("네이버 증권" if market == "KR" else "Yahoo Finance (시총 = 현재가 × 발행주식수)"),
            "via": via,
        }
        _MEM[market] = payload
        if D.get("on_breadth"):
            try:
                D["on_breadth"](market, payload)    # 시장 폭 일별 스냅샷 UPSERT (추가 외부 호출 없음)
            except Exception as e:
                log(f"{market} 시장 폭 저장 실패: {e}")
        if D.get("on_rows"):
            try:
                D["on_rows"](market, rows)          # 스크리너 스냅샷의 가격·등락률·시총 갱신
            except Exception as e:
                log(f"{market} 스크리너 반영 실패: {e}")
        try:
            with D["app"].app_context():
                D["ai_cache"].put(_key(market), None, "sectormap", payload, None, None, use_mem=False)
        except Exception as e:
            log(f"{market} DB 저장 실패: {e}")
        log(f"{market} 갱신({reason}, {via}) {len(rows)}/{len(univ[market])}종목 data_date={data_date} {time.time() - t0:.1f}s")
        return payload
    except Exception as e:
        log(f"{market} 갱신 실패({reason}): {e}")
        return None
    finally:
        _REFRESH_LOCK.release()
        gc.collect()


def warmup_async(markets=("KR", "US")):
    """스냅샷이 없는 시장만 백그라운드로 한 번 만든다."""
    todo = [m for m in markets if get(m) is None]
    if not todo:
        return False

    def _run():
        for m in todo:
            if get(m) is None:
                refresh(m, "warmup")
    threading.Thread(target=_run, daemon=True, name="sectormap-warmup").start()
    return True


# ── 스케줄러 ──────────────────────────────────────────────────────────────────
_state = {m: {"slot": None, "holiday": None, "final": None} for m in ("KR", "US")}


def _busy():
    lock = D.get("bot_lock")
    return bool(lock and lock.locked())


def _tick(market, now):
    st = _state[market]
    today = now.date()
    if now.weekday() >= 5 or st["holiday"] == today:
        return None
    w = market_hours.session_window(market, today)
    if not w:                                 # 거래소 휴장일 (시세 조회 없이 바로)
        return None
    (oh, om), (ch, cm) = w
    hm = (now.hour, now.minute)
    if (oh, om) <= hm < (ch, cm) and now.minute % SLOT_MIN >= SLOT_OFFSET:
        slot = (today, now.hour, now.minute // SLOT_MIN)
        if st["slot"] != slot:
            if _busy():
                return "deferred"            # 봇 작업 중 → 다음 확인(30초 뒤)에 다시
            st["slot"] = slot
            p = refresh(market, "intraday")
            if p and p.get("data_date") and p["data_date"] != str(today):
                st["holiday"] = today        # 오늘 데이터가 아님 → 휴장일
                log(f"{market} {today} 휴장으로 판단, 오늘은 더 조회하지 않음")
            return "intraday"
    elif hm >= _final_at(market, w) and st["final"] != today:
        if _busy():
            return "deferred"
        st["final"] = today
        p = refresh(market, "close")
        if p and p.get("data_date") and p["data_date"] != str(today):
            st["holiday"] = today
        return "close"
    return None


def _loop():
    time.sleep(20)
    warmup_async()
    while True:
        try:
            for m in ("KR", "US"):
                _tick(m, datetime.now(_tz(m)))
        except Exception as e:
            log(f"scheduler 오류: {e}")
        time.sleep(30)


_started = False


def start():
    global _started
    if _started or os.environ.get("SECTORMAP_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="sectormap").start()
    log("scheduler 시작 (SECTORMAP_SCHEDULER=0 으로 끌 수 있음)")
