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

# ⚠️ 토스 API는 콘솔에 등록된 IPv4 허용목록에서만 동작.
# 듀얼스택 환경에서 IPv6로 나가면 등록 IP와 달라 401(unidentified-client)이 남.
# urllib3가 IPv4만 쓰도록 강제 (프로세스 전역; 앱의 다른 호출도 IPv4 사용 — 모두 IPv4 지원).
try:
    import urllib3.util.connection as _urllib3_conn
    _urllib3_conn.HAS_IPV6 = False
except Exception:
    pass

_BASE = "https://openapi.tossinvest.com"
_TIMEOUT = 8


def _proxies():
    """토스 전용 고정 IP 프록시 (TOSS_PROXY_URL 설정 시).
    Railway egress IP가 재배포마다 바뀌는 문제 → 고정 IP 프록시 경유 시
    토스에는 프록시 IP만 1회 등록하면 됨.
    예: http://user:pass@proxyhost:3128
    """
    url = os.environ.get("TOSS_PROXY_URL", "").strip()
    if not url:
        return None
    return {"http": url, "https": url}


def _req(method, url, **kwargs):
    """프록시 자동 적용 requests 래퍼."""
    px = _proxies()
    if px:
        kwargs.setdefault("proxies", px)
    return requests.request(method, url, **kwargs)


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
            r = _req(
                "POST",
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


def _extract_list(payload, _depth=0):
    """응답에서 객체 배열을 방어적으로 추출 (중첩 대응).
    가능한 형태: [...] / {"prices":[...]} / {"result":{"candles":[...]}} 등
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and _depth < 3:
        for k in ("prices", "candles", "data", "result", "items", "content", "list"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
        # 중첩 dict 재귀 (예: result.candles)
        for v in payload.values():
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                got = _extract_list(v, _depth + 1)
                if got:
                    return got
    return []


def _extract_next_before(payload):
    """캔들 응답에서 다음 페이지 커서(nextBefore) 추출."""
    if isinstance(payload, dict):
        if payload.get("nextBefore"):
            return payload["nextBefore"]
        for v in payload.values():
            if isinstance(v, dict) and v.get("nextBefore"):
                return v["nextBefore"]
    return None


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
            r = _req(
                "GET",
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
                    "session": _session_of(p),
                }
        except Exception as e:
            log.warning(f"[toss] get_prices failed: {e}")
    return out


def get_price(ticker):
    """단일 종목 실시간 시세. Returns float 또는 None."""
    d = get_prices([ticker])
    info = d.get(ticker)
    return info["price"] if info else None


def get_price_info(ticker):
    """단일 종목 실시간 시세 + 부가 정보. → {"price", "currency", "timestamp", "session"} | None"""
    return get_prices([ticker]).get(ticker)


# 응답 스키마에 세션 구분 필드가 있는지 공식 문서로 확인하지 못해, 이름에 session·market status 류가
# 들어간 필드를 찾아 값으로 판별한다. 없으면 None → app.py 가 가격 비교로 판단한다.
_SESSION_KEYS = ("session", "marketstatus", "tradingstatus", "markettype", "pricetype", "tradingtype")
_EXT_WORDS = ("PRE", "AFTER", "EXTENDED", "OVERTIME", "POST", "NIGHT", "NXT", "OUT_OF_HOURS")


def _session_of(p):
    """→ "extended" | "regular" | None"""
    try:
        for k, v in p.items():
            if not isinstance(v, str) or not any(w in k.lower().replace("_", "") for w in _SESSION_KEYS):
                continue
            u = v.upper()
            if any(w in u for w in _EXT_WORDS):
                return "extended"
            if "REGULAR" in u or u in ("OPEN", "NORMAL"):
                return "regular"
    except Exception:
        pass
    return None


# ── 캔들 ───────────────────────────────────────────────────
def _get_candles_page(symbol, count=200, before=None, adjusted=True, interval="1d", raise_http=False):
    """토스 캔들 1페이지. Returns (list[dict], next_before).
    interval 기본 1d. 분봉·시간봉 값은 공식 문서를 확인하지 못해 get_intraday_df 가 후보값을 시도한다.
    raise_http=True 면 HTTP 오류(4xx 등)를 호출한 쪽으로 올려 '미지원 간격'을 구분할 수 있게 한다."""
    h = _headers()
    if not h:
        return [], None
    params = {
        "symbol": symbol,
        "interval": interval,
        "count": min(max(int(count), 1), 200),
        "adjusted": "true" if adjusted else "false",
    }
    if before:
        params["before"] = before
    try:
        r = _req("GET", f"{_BASE}/api/v1/candles", params=params, headers=h, timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        return _extract_list(j), _extract_next_before(j)
    except Exception as e:
        if raise_http and isinstance(e, requests.HTTPError):
            raise
        log.warning(f"[toss] candles {symbol} {interval} failed: {e}")
        return [], None


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
    # 원래 시장 오프셋 보존 (KR +09:00 / US 등) → 로컬 거래일 날짜가 정확.
    # 단일 종목은 오프셋이 균일해 tz-aware로 유지됨. 혼합 시 UTC 폴백.
    ts = pd.to_datetime(df["ts"], utc=False, errors="coerce")
    if getattr(ts.dt, "tz", None) is None:
        ts = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["ts"] = ts
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
        batch, next_before = _get_candles_page(symbol, count=200, before=before)
        if not batch:
            break
        rows.extend(batch)
        if len(rows) >= min_bars or len(batch) < 200:
            break
        # 다음 페이지: 응답의 nextBefore 우선, 없으면 가장 오래된 timestamp
        if not next_before:
            try:
                next_before = min(c.get("timestamp") for c in batch if c.get("timestamp"))
            except (ValueError, TypeError):
                break
        if not next_before or next_before in seen_before:
            break
        seen_before.add(next_before)
        before = next_before

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


# ── 분봉 · 시간봉 (토스 1분봉 → 10분·1시간 리샘플) ─────────────────────────────
# 토스 /api/v1/candles 는 interval 1m(1분봉)과 1d(일봉)만 공식 지원한다 (5m·10m·1h·60m 미지원, 2026-11 사용자 확인).
# 그래서 분·시간봉은 1분봉을 받아 pandas 로 묶는다. 과거 구간은 app.py 가 yfinance 로 채우고 토스 구간으로 최신 부분을 덮어쓴다.
MINUTE_MAX_PAGES = 8            # 200봉 × 8 = 1600분 ≈ 정규장 4거래일 (한국·미국 모두 하루 390분)
MINUTE_TIME_BUDGET = 2.0        # 초. 이 시간을 넘기면 받은 데까지만 쓴다
SESSION_OPEN = {"KR": (9, 0), "US": (9, 30)}
EXCHANGE_TZ = {"KR": "Asia/Seoul", "US": "America/New_York"}
_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
_minute_cache = {}              # toss_symbol → (ts, DataFrame)
_MINUTE_TTL = 20


def market_of(ticker):
    t = (ticker or "").upper()
    return "KR" if t.endswith(".KS") or t.endswith(".KQ") else "US"


def get_minute_df(ticker, max_pages=MINUTE_MAX_PAGES, time_budget=MINUTE_TIME_BUDGET):
    """토스 1분봉 (최근 → 과거로 페이지를 넘기며 최대 max_pages, time_budget 초 안에서).
    Returns DataFrame (거래소 시간대 tz-aware 인덱스, 오름차순) 또는 None."""
    if not is_enabled() or not is_eligible(ticker):
        return None
    sym = to_toss_symbol(ticker)
    now = time.time()
    hit = _minute_cache.get(sym)
    if hit and now - hit[0] < _MINUTE_TTL:
        return hit[1]
    rows, before, seen = [], None, set()
    t0 = time.time()
    for _ in range(max_pages):
        batch, nxt = _get_candles_page(sym, count=200, before=before, interval="1m")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 200 or time.time() - t0 > time_budget:
            break
        if not nxt:
            try:
                nxt = min(c.get("timestamp") for c in batch if c.get("timestamp"))
            except (ValueError, TypeError):
                break
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        before = nxt
    df = _candles_to_df(rows)
    if df is None or df.empty:
        return None
    df = df.tz_convert(EXCHANGE_TZ[market_of(ticker)]) if getattr(df.index, "tz", None) is not None \
        else df.tz_localize("UTC").tz_convert(EXCHANGE_TZ[market_of(ticker)])
    _minute_cache[sym] = (now, df)
    return df


def resample_minutes(df, minutes, market):
    """1분봉 → N분봉. 버킷은 거래소 장 시작 시각에 맞춘다 (미국 1시간봉 = 09:30·10:30…, 한국 = 09:00·10:00…).
    라벨은 버킷 시작 시각(yfinance 와 같음). 거래 없는 버킷은 버린다."""
    if df is None or df.empty:
        return df
    oh, om = SESSION_OPEN[market]
    offset = pd.Timedelta(minutes=(oh * 60 + om) % minutes)
    out = (df[["Open", "High", "Low", "Close", "Volume"]]
           .resample(f"{minutes}min", origin="start_day", offset=offset, label="left", closed="left")
           .agg(_AGG).dropna(subset=["Close"]))
    return out


def get_intraday_df(ticker, interval):
    """토스 1분봉을 받아 10m / 1h 로 리샘플. Returns (df, n_minute) — 실패 시 (None, 0).
    가장 오래된 날은 1분봉이 중간부터 잘려 있을 수 있어(페이지 한도) 그날은 버리고 완전한 날부터 쓴다."""
    if interval not in ("10m", "1h"):
        return None, 0
    m1 = get_minute_df(ticker)
    if m1 is None or m1.empty:
        return None, 0
    days = m1.index.normalize()
    if days.nunique() > 1:
        m1 = m1[days > days.min()]
    market = market_of(ticker)
    out = resample_minutes(m1, 10 if interval == "10m" else 60, market)
    return out, len(m1)


# ── 환율 ───────────────────────────────────────────────────
def get_exchange_rate(base="USD", quote="KRW"):
    """환율. Returns float 또는 None."""
    h = _headers()
    if not h:
        return None
    try:
        r = _req(
            "GET",
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


# ── 계좌 / 보유종목 ────────────────────────────────────────
def get_accounts():
    """연동된 계좌 목록. Returns list[{accountNo, accountSeq, accountType}]."""
    h = _headers()
    if not h:
        return []
    try:
        r = _req("GET", f"{_BASE}/api/v1/accounts", headers=h, timeout=_TIMEOUT)
        r.raise_for_status()
        return _extract_list(r.json())
    except Exception as e:
        log.warning(f"[toss] get_accounts failed: {e}")
        return []


def _holdings_for_account(account_seq):
    """단일 계좌 보유종목 조회. Returns list 또는 None(오류)."""
    h = _headers(account=account_seq)
    if not h:
        return None
    try:
        r = _req("GET", f"{_BASE}/api/v1/holdings", headers=h, timeout=_TIMEOUT)
        r.raise_for_status()
        result = (r.json() or {}).get("result") or {}
        items = result.get("items") or []
        out = []
        for it in items:
            out.append({
                "symbol":     (it.get("symbol") or "").upper(),
                "name":       it.get("name"),
                "market":     it.get("marketCountry"),   # KR / US
                "currency":   it.get("currency"),
                "quantity":   _f(it.get("quantity")),
                "avg_price":  _f(it.get("averagePurchasePrice")),
                "last_price": _f(it.get("lastPrice")),
                "account_seq": account_seq,
            })
        return out
    except Exception as e:
        log.warning(f"[toss] holdings(account={account_seq}) failed: {e}")
        return None


def get_account_holdings(account_seq=None):
    """보유종목 조회.
    - account_seq 지정: 해당 계좌만
    - 미지정: 연동된 '모든 계좌' 합산 (같은 종목은 수량 합산 + 가중평균 단가)
    Returns: list[{symbol, name, market, currency, quantity, avg_price, last_price}]
             또는 None (계좌 없음/실패)
    """
    if account_seq is not None:
        return _holdings_for_account(account_seq)

    accts = get_accounts()
    if not accts:
        return None

    merged = {}
    any_ok = False
    for a in accts:
        seq = a.get("accountSeq")
        items = _holdings_for_account(seq)
        if items is None:
            continue
        any_ok = True
        for it in items:
            key = (it["symbol"], it.get("market"))
            if key in merged:
                ex = merged[key]
                q1, q2 = (ex["quantity"] or 0), (it["quantity"] or 0)
                tot = q1 + q2
                if tot > 0:
                    ex["avg_price"] = (
                        (ex["avg_price"] or 0) * q1 + (it["avg_price"] or 0) * q2
                    ) / tot
                ex["quantity"] = tot
            else:
                merged[key] = dict(it)
    if not any_ok:
        return None
    return list(merged.values())
