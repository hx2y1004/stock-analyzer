"""토스증권 Open API 클라이언트.

제공: 실시간 시세(prices), 캔들(candles), 환율(exchange-rate).
인증: OAuth2 Client Credentials → access_token (24h) 캐싱.

펀더멘털(PER, 시가총액 등)은 토스가 미제공 → app.py에서 yfinance 유지.

환경변수:
    TOSS_CLIENT_ID, TOSS_CLIENT_SECRET
"""
import os
import time
import threading
import logging

import requests
import pandas as pd

log = logging.getLogger("toss_api")

_BASE = "https://openapi.tossinvest.com"
_TIMEOUT = 8

# ── 토큰 캐시 ──────────────────────────────────────────────
_token_lock = threading.Lock()
_token = {"access_token": None, "expires_at": 0.0}

# ── 일봉 raw 캐시 (요청 간 중복 호출 방지, 60초) ──
_daily_cache = {}          # toss_symbol -> (ts, DataFrame)
_DAILY_TTL = 60


def is_enabled():
    """토스 API 자격증명이 설정되어 있으면 True."""
    return bool(os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET"))


def _get_token():
    """access_token 발급/캐시. 만료 60초 전 갱신."""
    now = time.time()
    if _token["access_token"] and now < _token["expires_at"] - 60:
        return _token["access_token"]
    with _token_lock:
        now = time.time()
        if _token["access_token"] and now < _token["expires_at"] - 60:
            return _token["access_token"]
        cid = os.environ.get("TOSS_CLIENT_ID")
        csec = os.environ.get("TOSS_CLIENT_SECRET")
        if not cid or not csec:
            return None
        try:
            r = requests.post(
                f"{_BASE}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": csec,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            j = r.json() or {}
            tok = j.get("access_token")
            exp = j.get("expires_in") or 86400
            if not tok:
                log.warning("[toss] token response missing access_token")
                return None
            _token["access_token"] = tok
            _token["expires_at"] = time.time() + float(exp)
            return tok
        except Exception as e:
            log.warning(f"[toss] token fetch failed: {e}")
            return None


def _headers(account=None):
    tok = _get_token()
    if not tok:
        return None
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    if account:
        h["X-Tossinvest-Account"] = str(account)
    return h


def _extract_list(payload):
    """응답에서 객체 배열을 방어적으로 추출.
    가능한 형태: [...] / {"prices":[...]} / {"candles":[...]} / {"data":[...]} /
                 {"result":[...]} / {"items":[...]} / {"content":[...]}
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("prices", "candles", "data", "result", "items", "content", "list"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
        # 한 단계 더: 첫 dict 값이 list면 사용
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def _f(v):
    """문자열/숫자 → float (None 안전)."""
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if v == "":
                return None
        return float(v)
    except Exception:
        return None


# ── 심볼 변환 ──────────────────────────────────────────────
def to_toss_symbol(ticker):
    """yfinance 티커 → 토스 심볼.
    005930.KS / 005930.KQ → 005930 ; AAPL → AAPL
    """
    t = (ticker or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t.split(".")[0]
    return t


def is_eligible(ticker):
    """토스로 조회 가능한 일반 종목인지 (지수/환율/선물 제외)."""
    t = (ticker or "").strip().upper()
    if not t:
        return False
    if t.startswith("^") or "=" in t:   # ^KS11, ^GSPC, KRW=X 등
        return False
    if t.endswith(".KS") or t.endswith(".KQ"):
        return True
    # 미국 티커: 영문/숫자/.- 만
    import re
    return bool(re.fullmatch(r"[A-Z0-9.\-]{1,8}", t))


# ── 시세 ───────────────────────────────────────────────────
def get_prices(tickers):
    """여러 종목 실시간 시세.
    tickers: yfinance 티커 리스트
    Returns: { yfinance_ticker: {"price": float, "currency": str, "timestamp": str} }
    """
    h = _headers()
    if not h or not tickers:
        return {}
    # yfinance 티커 → 토스 심볼 매핑 (역추적용)
    sym_map = {}
    for t in tickers:
        if is_eligible(t):
            sym_map[to_toss_symbol(t)] = t
    if not sym_map:
        return {}
    out = {}
    syms = list(sym_map.keys())
    # 최대 200개씩
    for i in range(0, len(syms), 200):
        batch = syms[i:i + 200]
        try:
            r = requests.get(
                f"{_BASE}/api/v1/prices",
                params={"symbols": ",".join(batch)},
                headers=h, timeout=_TIMEOUT,
            )
            r.raise_for_status()
            for p in _extract_list(r.json()):
                sym = (p.get("symbol") or "").upper()
                yf_t = sym_map.get(sym)
                if not yf_t:
                    continue
                price = _f(p.get("lastPrice") or p.get("price") or p.get("close"))
                if price is None:
                    continue
                out[yf_t] = {
                    "price": price,
                    "currency": p.get("currency"),
                    "timestamp": p.get("timestamp"),
                }
        except Exception as e:
            log.warning(f"[toss] get_prices failed: {e}")
    return out


def get_price(ticker):
    """단일 종목 실시간 시세. Returns float 또는 None."""
    d = get_prices([ticker])
    info = d.get(ticker)
    return info["price"] if info else None


# ── 캔들 ───────────────────────────────────────────────────
def _get_candles_raw(symbol, count=200, before=None, adjusted=True):
    """토스 일봉 캔들 1페이지. Returns list[dict]."""
    h = _headers()
    if not h:
        return []
    params = {
        "symbol": symbol,
        "interval": "1d",
        "count": min(max(int(count), 1), 200),
        "adjusted": "true" if adjusted else "false",
    }
    if before:
        params["before"] = before
    try:
        r = requests.get(f"{_BASE}/api/v1/candles", params=params, headers=h, timeout=_TIMEOUT)
        r.raise_for_status()
        return _extract_list(r.json())
    except Exception as e:
        log.warning(f"[toss] candles {symbol} failed: {e}")
        return []


def _candles_to_df(rows):
    """토스 캔들 dict 리스트 → yfinance 호환 DataFrame.
    컬럼: Open/High/Low/Close/Volume, tz-aware DatetimeIndex (오름차순).
    """
    recs = []
    for c in rows:
        ts = c.get("timestamp")
        o = _f(c.get("openPrice"));  hi = _f(c.get("highPrice"))
        lo = _f(c.get("lowPrice"));  cl = _f(c.get("closePrice"))
        vol = _f(c.get("volume")) or 0
        if ts is None or cl is None:
            continue
        recs.append((ts, o, hi, lo, cl, vol))
    if not recs:
        return None
    df = pd.DataFrame(recs, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.index.name = "Date"
    return df


def _get_daily_df(symbol, min_bars):
    """일봉 DataFrame (필요한 만큼 페이지네이션, 60초 캐시)."""
    now = time.time()
    cached = _daily_cache.get(symbol)
    if cached and (now - cached[0] < _DAILY_TTL) and len(cached[1]) >= min_bars:
        return cached[1]

    rows = []
    before = None
    seen_before = set()
    # 200개씩, min_bars 채울 때까지 (최대 10페이지 = 2000봉 ≈ 8년)
    for _ in range(10):
        batch = _get_candles_raw(symbol, count=200, before=before)
        if not batch:
            break
        rows.extend(batch)
        # 다음 페이지: 가장 오래된 timestamp 이전
        try:
            oldest = min(c.get("timestamp") for c in batch if c.get("timestamp"))
        except (ValueError, TypeError):
            break
        if not oldest or oldest in seen_before:
            break
        seen_before.add(oldest)
        before = oldest
        if len(batch) < 200 or len(rows) >= min_bars:
            break

    df = _candles_to_df(rows)
    if df is None or df.empty:
        return None
    _daily_cache[symbol] = (now, df)
    return df


def get_candles_df(ticker, interval="1d", min_bars=260, adjusted=True):
    """yfinance 호환 가격 DataFrame 반환 (토스 캔들 기반).
    interval: 1d / 1wk / 1mo  (주/월봉은 일봉 리샘플)
    실패 시 None.
    """
    if not is_enabled() or not is_eligible(ticker):
        return None
    sym = to_toss_symbol(ticker)

    # 주/월봉이면 리샘플 위해 더 많은 일봉 필요
    if interval == "1wk":
        need = max(min_bars * 6, 260)
    elif interval == "1mo":
        need = max(min_bars * 23, 520)
    else:
        need = min_bars

    df = _get_daily_df(sym, need)
    if df is None or df.empty:
        return None

    if interval in ("1wk", "1mo"):
        rule = "W-FRI" if interval == "1wk" else "ME"
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        df = df.resample(rule).agg(agg).dropna(subset=["Close"])

    return df


# ── 환율 ───────────────────────────────────────────────────
def get_exchange_rate(base="USD", quote="KRW"):
    """환율. Returns float 또는 None."""
    h = _headers()
    if not h:
        return None
    try:
        r = requests.get(
            f"{_BASE}/api/v1/exchange-rate",
            params={"baseCurrency": base, "quoteCurrency": quote},
            headers=h, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        j = r.json() or {}
        # 응답이 dict 또는 {"data":{...}} 형태 가능
        if "rate" not in j and "midRate" not in j:
            inner = j.get("data") or j.get("result")
            if isinstance(inner, dict):
                j = inner
        rate = _f(j.get("rate")) or _f(j.get("midRate"))
        return rate if (rate and rate > 0) else None
    except Exception as e:
        log.warning(f"[toss] exchange_rate failed: {e}")
        return None
