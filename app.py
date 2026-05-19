import json
import os
import re
import time
import threading
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
import numpy as np
import pandas as pd
import requests
import yfinance as yf
try:
    from deep_translator import GoogleTranslator as _GTrans
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def _has_korean(text):
        """문자열에 한글 문자가 일정 비율 이상 포함됐는지."""
        if not text:
            return False
        ko_chars = sum(1 for c in text[:200] if '가' <= c <= '힯')
        return ko_chars >= 10   # 200자 중 10자 이상이면 이미 한글

    def _translate_ko(text, max_chars=2000, timeout=4):
        """영문 → 한글 번역. timeout 초과 시 원문 반환."""
        if not text or not text.strip():
            return text
        # 이미 한글이면 번역 스킵 (불필요한 외부 호출 방지)
        if _has_korean(text):
            return text
        # ThreadPoolExecutor 로 timeout 강제 (deep_translator 자체엔 timeout 없음)
        with _TPE(max_workers=1) as ex:
            fut = ex.submit(
                lambda: _GTrans(source='auto', target='ko').translate(text[:max_chars])
            )
            try:
                return fut.result(timeout=timeout)
            except Exception as e:
                # timeout 또는 네트워크 에러 → 원문 그대로
                return text

    def _translate_batch(texts):
        with _TPE(max_workers=6) as ex:
            return list(ex.map(_translate_ko, texts))
except ImportError:
    def _translate_ko(text, max_chars=2000, timeout=4): return text
    def _translate_batch(texts): return texts
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user

from analysis.ai_analysis import analyze_signals
from analysis.indicators import add_all_indicators
from models import db, User, Holding, Transaction, INITIAL_CAPITAL_KRW
from auth import auth_bp

# ── DB URL (로컬: SQLite, Railway: PostgreSQL) ─────────────────────────────────
_db_url = os.environ.get("DATABASE_URL", "sqlite:///portfolio.db")
if _db_url.startswith("postgres://"):          # Railway 구버전 호환
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")
app.config["SQLALCHEMY_DATABASE_URI"]        = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
CORS(app)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = None   # API 위주라 리다이렉트 없음

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "로그인이 필요합니다"}), 401

app.register_blueprint(auth_bp)

with app.app_context():
    db.create_all()
    # ── 기존 User 마이그레이션: cash_balance / initial_capital 컬럼 추가 후
    # 기존 유저들에게도 1억원 초기 자본 부여 ──
    try:
        from sqlalchemy import text
        # PostgreSQL: ALTER TABLE 가 안전하게 동작 (이미 있으면 IF NOT EXISTS 패턴)
        with db.engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS cash_balance DOUBLE PRECISION DEFAULT 100000000"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS initial_capital DOUBLE PRECISION DEFAULT 100000000"
            ))
            # NULL 인 경우 기본값 부여 (기존 유저)
            conn.execute(text(
                "UPDATE users SET cash_balance = 100000000 WHERE cash_balance IS NULL"
            ))
            conn.execute(text(
                "UPDATE users SET initial_capital = 100000000 WHERE initial_capital IS NULL"
            ))
    except Exception as e:
        # SQLite 등에서는 ALTER TABLE 문법이 다를 수 있어 무시
        # (db.create_all() 가 새 컬럼 만들었으면 OK)
        print(f"[migration] User cash columns: {e}")


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return None if (np.isnan(obj) or np.isinf(obj)) else float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

    def encode(self, obj):
        return super().encode(self._sanitize(obj))

    def _sanitize(self, obj):
        if isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
            return None
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._sanitize(v) for v in obj]
        return obj


# Flask 3 JSON provider 로 NumpyEncoder 등록 (NaN → null 자동 변환)
try:
    from flask.json.provider import DefaultJSONProvider

    class _SafeJSONProvider(DefaultJSONProvider):
        ensure_ascii = False   # 한글 그대로

        def dumps(self, obj, **kwargs):
            return json.dumps(obj, cls=NumpyEncoder, ensure_ascii=False)

        def loads(self, s, **kwargs):
            return json.loads(s, **kwargs)

    app.json = _SafeJSONProvider(app)
except Exception as _e:
    app.logger.warning(f"JSON provider registration failed: {_e}")


def safe_float(val):
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return float(val)
    except Exception:
        return None


def _clean_num(s):
    """문자열 숫자 → float 변환 (쉼표, 공백 제거)"""
    if not s:
        return None
    try:
        return float(s.replace(",", "").strip())
    except Exception:
        return None


def _calc_dividend_yield(info, current_price):
    """
    배당수익률 계산 (소수점 형태 반환, 예: 0.0063 = 0.63%).
    yfinance의 dividendYield 필드는 % 형태(0.02=0.02%, 0.55=0.55%)로
    반환되어 신뢰할 수 없으므로, 아래 우선순위로 직접 계산:
      1. 한국 주식: 네이버 DPS/price (info["_naver_div_yield"])
      2. 미국/기타: dividendRate / currentPrice
    """
    price = current_price or safe_float(info.get("currentPrice"))
    if not price or price <= 0:
        return None

    # 1순위: 한국 주식 — 네이버 DPS/price (정확한 소수점 값)
    naver_dy = safe_float(info.get("_naver_div_yield"))
    if naver_dy:
        return naver_dy

    # 2순위: dividendRate / price (미국/ETF 등)
    rate = safe_float(info.get("dividendRate"))
    if rate and rate > 0:
        return rate / price

    return None


def fetch_naver_fundamentals(krx_code):
    """
    네이버 금융 API로 한국 주식 EPS/BPS를 가져옵니다.
    가장 최근 확정(non-consensus) 연간 실적 기준.
    PER/PBR은 호출 측에서 현재가 기준으로 직접 계산합니다.
    """
    try:
        url = f"https://m.stock.naver.com/api/stock/{krx_code}/finance/annual"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return {}

        data = resp.json()
        fi = data.get("financeInfo", {})
        title_list = fi.get("trTitleList", [])
        row_list   = fi.get("rowList", [])

        # 가장 최근 확정(non-consensus) 컬럼 키 찾기
        confirmed = [t for t in title_list if t.get("isConsensus") == "N"]
        if not confirmed:
            return {}
        latest_key = sorted([t["key"] for t in confirmed], reverse=True)[0]

        result = {}
        for row in row_list:
            title  = row.get("title", "")
            cols   = row.get("columns", {})
            latest = cols.get(latest_key, {})
            val    = _clean_num(latest.get("value"))
            if val is None or val == 0:
                continue
            if title == "EPS":
                result["eps"] = val
            elif title == "BPS":
                result["bps"] = val
            elif "배당금" in title or title == "DPS":   # 주당배당금
                result["dps"] = val

        return result
    except Exception:
        return {}


@app.route("/")
def index():
    return render_template("index.html")


# ── PWA: Service Worker (루트 스코프로 서빙) ─────────────
@app.route("/service-worker.js")
def service_worker():
    """SW를 루트(/)에서 서빙해야 전체 사이트 스코프를 가질 수 있음."""
    from flask import send_from_directory, make_response
    resp = make_response(send_from_directory("static", "service-worker.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/manifest.json")
def manifest():
    """편의를 위해 루트에서도 manifest 접근 가능."""
    from flask import send_from_directory
    return send_from_directory("static", "manifest.json")


# ── 추세 상승 감지 ─────────────────────────────────────
import trends_scanner as _trends


@app.route("/api/trends/scan", methods=["POST", "GET"])
def api_trends_scan():
    """스캔 시작 (또는 캐시 반환). 폴링은 /api/trends/status 로.

    Query: market=ALL|KR|US, force=1 (캐시 무시)
    """
    from flask import request, jsonify
    market = (request.args.get("market") or "ALL").upper()
    force  = request.args.get("force") == "1"
    res = _trends.start_scan(STOCK_DB, market, force=force)
    return jsonify(res)


@app.route("/api/trends/status")
def api_trends_status():
    """진행 상태/캐시 결과 조회."""
    from flask import request, jsonify, make_response
    import time as _time
    market = (request.args.get("market") or "ALL").upper()
    resp = make_response(jsonify(_trends.get_status(market)))
    # 모든 캐시 계층 차단: 브라우저 / 프록시 / Service Worker
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    resp.headers["X-Accel-Expires"] = "0"
    # ETag 무효화 (매번 다른 값)
    resp.headers["ETag"]          = f'W/"{_time.time_ns()}"'
    return resp


@app.route("/api/trends/abort", methods=["POST"])
def api_trends_abort():
    """진행 중인 스캔 중단."""
    from flask import request, jsonify
    market = (request.args.get("market") or "ALL").upper()
    return jsonify(_trends.abort_scan(market))


# 레거시: 캐시된 결과만 반환 (없으면 안내)
@app.route("/api/trends")
def api_trends():
    from flask import request, jsonify
    from datetime import datetime
    market = (request.args.get("market") or "ALL").upper()
    st = _trends.get_status(market)
    if st.get("state") == "done" and st.get("result"):
        return jsonify(st["result"])
    return jsonify({
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "market":     market,
        "total":      0,
        "items":      [],
        "message":    "스캔이 실행되지 않았습니다. /api/trends/scan 으로 시작하세요.",
    })


# ── 종목 DB 로드 (앱 시작 시 1회) ─────────────────────
import os

STOCK_DB = []

def load_stock_db():
    global STOCK_DB
    db_path = os.path.join(os.path.dirname(__file__), "stock_db.json")
    if os.path.exists(db_path):
        with open(db_path, encoding="utf-8") as f:
            STOCK_DB = json.load(f)

load_stock_db()


def search_local(query, limit=10):
    q = query.lower().replace(" ", "")
    exact, starts, contains = [], [], []
    for s in STOCK_DB:
        name = s["name"].lower().replace(" ", "")
        sym  = s["symbol"].lower().replace(".", "")
        if name == q or sym == q:
            exact.append(s)
        elif name.startswith(q) or sym.startswith(q):
            starts.append(s)
        elif q in name or q in sym:
            contains.append(s)
    results = exact + starts + contains
    seen, deduped = set(), []
    for s in results:
        if s["symbol"] not in seen:
            deduped.append(s)
            seen.add(s["symbol"])
    return deduped[:limit]


@app.route("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    # 로컬 DB 먼저 검색 (한글 포함 or 2글자 이상 한글)
    has_korean = any('가' <= c <= '힣' or 'ㄱ' <= c <= 'ㆎ' for c in query)
    local = search_local(query, limit=10)

    if has_korean:
        return jsonify(local)

    # 영문: 로컬 DB + yfinance 검색 병합
    try:
        results = yf.Search(query, max_results=8)
        remote = []
        local_syms = {s["symbol"] for s in local}
        for q in results.quotes:
            if q.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            sym = q.get("symbol", "")
            if sym in local_syms:
                continue
            remote.append({
                "symbol": sym,
                "name": q.get("shortname") or q.get("longname") or sym,
                "exchange": q.get("exchange", ""),
                "market": q.get("quoteType", ""),
                "type": q.get("quoteType", ""),
            })
        return jsonify((local + remote)[:10])
    except Exception:
        return jsonify(local)


@app.route("/trading")
def trading():
    return render_template("trading.html")


def build_chart_data(ticker, interval):
    """차트 데이터만 반환 (봉 전환 시 사용)"""
    if interval not in ("1d", "1wk", "1mo"):
        interval = "1d"

    INTERVAL_LABELS = {"1d": "일봉", "1wk": "주봉", "1mo": "월봉"}

    stock = yf.Ticker(ticker)
    df = stock.history(period="max", interval=interval)
    if df.empty:
        return None, f"'{ticker}' 데이터를 찾을 수 없습니다."

    df = df.dropna(subset=["Close", "Open", "High", "Low"])
    if df.empty:
        return None, f"'{ticker}' 유효한 가격 데이터가 없습니다."

    df = add_all_indicators(df)
    df.index = df.index.strftime("%Y-%m-%d")

    def series(col):
        return [safe_float(v) for v in df[col]] if col in df.columns else []

    chart_data = {
        "dates":     df.index.tolist(),
        "open":      [safe_float(v) for v in df["Open"]],
        "high":      [safe_float(v) for v in df["High"]],
        "low":       [safe_float(v) for v in df["Low"]],
        "close":     [safe_float(v) for v in df["Close"]],
        "volume":    [safe_float(v) for v in df["Volume"]],
        "ma5":       series("MA5"),   "ma20": series("MA20"),
        "ma60":      series("MA60"),  "ma120": series("MA120"),
        "bb_upper":  series("BB_upper"), "bb_mid": series("BB_mid"),
        "bb_lower":  series("BB_lower"),
        "tenkan":    series("tenkan"), "kijun": series("kijun"),
        "senkou_a":  series("senkou_a"), "senkou_b": series("senkou_b"),
        "rsi":       series("RSI"),
        "macd":      series("MACD"),  "macd_signal": series("MACD_signal"),
        "macd_hist": series("MACD_hist"),
    }
    return chart_data, INTERVAL_LABELS[interval]


@app.route("/api/chart", methods=["GET"])
def chart_only():
    ticker = request.args.get("ticker", "").strip().upper()
    interval = request.args.get("interval", "1d")
    if not ticker:
        return jsonify({"error": "티커를 입력해주세요"}), 400
    try:
        chart_data, interval_label = build_chart_data(ticker, interval)
        if chart_data is None:
            return jsonify({"error": interval_label}), 404
        return app.response_class(
            response=json.dumps(
                {"chart": chart_data, "interval": interval, "interval_label": interval_label},
                cls=NumpyEncoder, ensure_ascii=False,
            ),
            status=200, mimetype="application/json",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


GRADE_SCORE = {
    "strong buy": 5, "buy": 4, "outperform": 4, "overweight": 4,
    "market outperform": 4, "sector outperform": 4, "accumulate": 4,
    "neutral": 3, "hold": 3, "equal-weight": 3, "market perform": 3,
    "sector perform": 3, "equal weight": 3, "in-line": 3,
    "underperform": 2, "underweight": 2, "sector underperform": 2,
    "sell": 1, "strong sell": 1, "reduce": 1,
}

def fetch_kr_quarterly_data(krx_code):
    """네이버 금융 분기 API → actuals + 컨센서스 estimates 동시 수집.

    yfinance 가 한국 종목 실적 업데이트가 느려서, 네이버 데이터를
    우선적으로 사용하기 위함.

    Returns:
        dict {
            "rev_act":  {"YYYY-MM": value_krw},   # 발표치 매출
            "oi_act":   {"YYYY-MM": value_krw},   # 발표치 영업이익
            "rev_est":  {"YYYY-MM": value_krw},   # 컨센서스 매출
            "oi_est":   {"YYYY-MM": value_krw},   # 컨센서스 영업이익
        }
    """
    _hdrs = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://m.stock.naver.com/domestic/stock/{krx_code}/finance/quarterly",
        "Accept": "application/json",
    }

    def _parse_period(key):
        """컬럼 키 → "YYYY-MM" (다양한 형식 대응)."""
        # "2025.03", "2025.03E", "2025.09(E)"
        m = re.match(r'(\d{4})[.\-_](\d{2})', key)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        # "2025.1Q" / "2025.2Q" / "25.1Q"
        m = re.match(r'(\d{2,4})[.\-_](\d)Q', key, re.I)
        if m:
            y = m.group(1)
            if len(y) == 2:
                y = '20' + y
            q_month = {'1': '03', '2': '06', '3': '09', '4': '12'}
            return f"{y}-{q_month.get(m.group(2), '12')}"
        # "202503"
        m = re.match(r'^(\d{4})(\d{2})$', key)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        return None

    rev_act, oi_act = {}, {}
    rev_est, oi_est = {}, {}

    def _parse_naver_finance(fi: dict):
        """공통 파서: financeInfo 딕셔너리에서 actual/estimate 추출."""
        title_list = fi.get("trTitleList", [])
        row_list   = fi.get("rowList", [])

        act_keys, est_keys = set(), set()
        for t in title_list:
            k = t.get("key")
            if not k:
                continue
            cv = t.get("isConsensus")
            if cv in ("Y", True, "true", "TRUE", 1):
                est_keys.add(k)
            else:
                act_keys.add(k)

        app.logger.info(
            f"[KR qtr] actual={sorted(act_keys)} estimate={sorted(est_keys)}"
        )

        for row in row_list:
            title = row.get("title", "")
            is_rev = title in ("매출액", "매출", "수익", "Revenue", "총매출")
            is_oi  = title in ("영업이익", "영업이익(손실)", "OperatingIncome")
            if not (is_rev or is_oi):
                continue
            for key_set, target_rev, target_oi in (
                (act_keys, rev_act, oi_act),
                (est_keys, rev_est, oi_est),
            ):
                for key in key_set:
                    cell = row.get("columns", {}).get(key, {})
                    if not cell:
                        continue
                    raw = cell.get("value") or cell.get("val") or ""
                    val = _clean_num(str(raw))
                    if val is None:
                        continue
                    period = _parse_period(key)
                    if not period:
                        continue
                    krw_val = val * 1e8
                    if is_rev: target_rev[period] = krw_val
                    else:      target_oi[period]  = krw_val

    # ── 시도 1: m.stock.naver.com/api/stock/.../finance/quarterly ──
    try:
        url  = f"https://m.stock.naver.com/api/stock/{krx_code}/finance/quarterly"
        resp = requests.get(url, headers=_hdrs, timeout=8)
        if resp.status_code == 200:
            _parse_naver_finance(resp.json().get("financeInfo", {}))
    except Exception as e:
        app.logger.error(f"[KR qtr] mobile quarterly error: {e}")

    # ── 시도 2: integration 엔드포인트 (항상 시도, 데이터 머지) ──
    try:
        url2 = f"https://m.stock.naver.com/api/stock/{krx_code}/integration"
        r2 = requests.get(url2, headers=_hdrs, timeout=8)
        if r2.status_code == 200:
            payload = r2.json()
            fi = (payload.get("financeInfo")
                  or payload.get("quarterly")
                  or {})
            if fi:
                before = len(rev_act)
                _parse_naver_finance(fi)
                after = len(rev_act)
                app.logger.info(f"[KR qtr] integration: {before}→{after} periods")
    except Exception as e:
        app.logger.warning(f"[KR qtr] integration failed: {e}")

    # ── 시도 3: wisereport (여러 fin_typ 파라미터 시도) ──
    try:
        from bs4 import BeautifulSoup
        wisereport_urls = [
            f"https://navercomp.wisereport.co.kr/v2/company/cF1002.aspx?cmp_cd={krx_code}&fin_typ=0&freq_typ=Q",
            f"https://navercomp.wisereport.co.kr/v2/company/cF1002.aspx?cmp_cd={krx_code}&fin_typ=4&freq_typ=Q",
            f"https://navercomp.wisereport.co.kr/v2/company/c1030001.aspx?cmp_cd={krx_code}",  # 종합 페이지
        ]
        wisereport_hdrs = {**_hdrs,
            "Referer": f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={krx_code}",
            "Accept": "text/html,application/xhtml+xml,*/*",
        }
        for u in wisereport_urls:
            try:
                r3 = requests.get(u, headers=wisereport_hdrs, timeout=10)
                app.logger.info(f"[KR qtr] wisereport try {u[-60:]} → {r3.status_code} {len(r3.text)}b")
                if r3.status_code != 200 or not r3.text:
                    continue
                soup = BeautifulSoup(r3.text, "html.parser")
                tables = soup.find_all("table")
                if not tables:
                    app.logger.info(f"[KR qtr] wisereport no tables")
                    continue
                before = len(rev_act)
                # 모든 테이블 순회 (페이지에 여러 표가 있음 — 분기 표를 찾아야 함)
                for table in tables:
                    # 헤더: 분기 컬럼명 (예: 2024/12, 2025/03, ..., 2026/03)
                    headers_parsed = []
                    thead = table.find("thead") or table
                    for th in thead.find_all("th"):
                        txt = th.get_text(strip=True)
                        m = re.match(r'(\d{4})[./](\d{2})', txt)
                        headers_parsed.append(f"{m.group(1)}-{m.group(2)}" if m else None)
                    if not any(headers_parsed):
                        continue

                    for tr in table.find_all("tr"):
                        th = tr.find("th")
                        if not th:
                            continue
                        label = th.get_text(strip=True)
                        is_rev = label in ("매출액", "매출", "수익")
                        is_oi  = label in ("영업이익", "영업이익(손실)")
                        if not (is_rev or is_oi):
                            continue
                        tds = tr.find_all("td")
                        data_cols = [h for h in headers_parsed if h]
                        for i, td in enumerate(tds):
                            if i >= len(data_cols):
                                break
                            period = data_cols[i]
                            val = _clean_num(td.get_text(strip=True))
                            if val is None:
                                continue
                            krw_val = val * 1e8
                            if is_rev: rev_act[period] = krw_val
                            else:      oi_act[period]  = krw_val

                after = len(rev_act)
                if after > before:
                    app.logger.info(
                        f"[KR qtr] wisereport SUCCESS: {before}→{after} periods, "
                        f"periods={sorted(rev_act.keys())}"
                    )
                    break   # 성공하면 추가 URL 시도 안 함
            except Exception as e:
                app.logger.warning(f"[KR qtr] wisereport {u[-40:]} failed: {e}")
    except Exception as e:
        app.logger.warning(f"[KR qtr] wisereport block failed: {e}")

    # ── 시도 4: m.stock.naver.com 모바일 페이지 HTML 직접 스크래핑 ──
    # API 가 못 가져오는 최신 분기가 페이지에는 렌더링 되어 있을 수 있음
    try:
        from bs4 import BeautifulSoup
        page_url = f"https://m.stock.naver.com/domestic/stock/{krx_code}/finance/quarterly"
        rp = requests.get(page_url, headers=_hdrs, timeout=10)
        app.logger.info(f"[KR qtr] mobile page → {rp.status_code} {len(rp.text)}b")
        if rp.status_code == 200 and rp.text:
            soup = BeautifulSoup(rp.text, "html.parser")
            tables = soup.find_all("table")
            before = len(rev_act)
            for table in tables:
                # 같은 파싱 로직
                headers_parsed = []
                thead = table.find("thead") or table
                for th in thead.find_all("th"):
                    txt = th.get_text(strip=True)
                    m = re.match(r'(\d{4})[./](\d{2})', txt)
                    headers_parsed.append(f"{m.group(1)}-{m.group(2)}" if m else None)
                if not any(headers_parsed):
                    continue
                for tr in table.find_all("tr"):
                    th = tr.find("th")
                    if not th:
                        continue
                    label = th.get_text(strip=True)
                    is_rev = label in ("매출액", "매출", "수익")
                    is_oi  = label in ("영업이익", "영업이익(손실)")
                    if not (is_rev or is_oi):
                        continue
                    tds = tr.find_all("td")
                    data_cols = [h for h in headers_parsed if h]
                    for i, td in enumerate(tds):
                        if i >= len(data_cols):
                            break
                        period = data_cols[i]
                        val = _clean_num(td.get_text(strip=True))
                        if val is None:
                            continue
                        krw_val = val * 1e8
                        if is_rev: rev_act[period] = krw_val
                        else:      oi_act[period]  = krw_val
            after = len(rev_act)
            if after > before:
                app.logger.info(
                    f"[KR qtr] mobile page scrape: {before}→{after} periods, "
                    f"periods={sorted(rev_act.keys())}"
                )
    except Exception as e:
        app.logger.warning(f"[KR qtr] mobile page scrape failed: {e}")

    app.logger.info(f"[KR qtr] FINAL: rev_act_periods={sorted(rev_act.keys())}")

    return {
        "rev_act": rev_act, "oi_act":  oi_act,
        "rev_est": rev_est, "oi_est":  oi_est,
    }


# 하위 호환: 기존 호출부가 (rev_est, oi_est) 튜플을 기대하면 그것만 반환
def fetch_kr_quarterly_estimates(krx_code):
    data = fetch_kr_quarterly_data(krx_code)
    return data["rev_est"], data["oi_est"]


# ── DART (전자공시시스템) 통합 ─────────────────────────────────
# 금융감독원 공식 공시 데이터 — 가장 빠르고 정확
# 사용 조건: DART_API_KEY 환경변수 설정 (https://opendart.fss.or.kr 무료 발급)

_DART_CORP_CODE_MAP = None   # {stock_code: corp_code} 캐시
_DART_LOAD_LOCK = threading.Lock()


def _load_dart_corp_codes():
    """DART corpCode.xml → {KRX_종목코드: DART_corp_code} 캐싱."""
    global _DART_CORP_CODE_MAP
    if _DART_CORP_CODE_MAP is not None:
        return _DART_CORP_CODE_MAP
    with _DART_LOAD_LOCK:
        if _DART_CORP_CODE_MAP is not None:
            return _DART_CORP_CODE_MAP
        api_key = os.environ.get("DART_API_KEY", "").strip()
        if not api_key:
            _DART_CORP_CODE_MAP = {}
            return _DART_CORP_CODE_MAP
        try:
            import zipfile, io, xml.etree.ElementTree as ET
            url = "https://opendart.fss.or.kr/api/corpCode.xml"
            resp = requests.get(url, params={"crtfc_key": api_key}, timeout=20)
            if resp.status_code != 200:
                app.logger.warning(f"[DART] corp_code download failed: {resp.status_code}")
                _DART_CORP_CODE_MAP = {}
                return _DART_CORP_CODE_MAP
            mapping = {}
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    tree = ET.parse(f)
                    for c in tree.getroot().findall("list"):
                        sc = (c.findtext("stock_code") or "").strip()
                        cc = (c.findtext("corp_code") or "").strip()
                        if sc and cc:
                            mapping[sc] = cc
            _DART_CORP_CODE_MAP = mapping
            app.logger.info(f"[DART] corp_code map loaded: {len(mapping)} stocks")
            return _DART_CORP_CODE_MAP
        except Exception as e:
            app.logger.error(f"[DART] corp_code load error: {e}")
            _DART_CORP_CODE_MAP = {}
            return _DART_CORP_CODE_MAP


def _dart_parse_amount(text):
    """DART 금액 문자열(억원 단위 X — KRW 그대로) → float."""
    if not text:
        return None
    text = str(text).replace(",", "").strip()
    if text in ("-", "", "—"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_dart_quarterly(krx_code: str) -> dict:
    """DART API → 최근 2~3년 분기 매출·영업이익 (단일 분기 기준).

    DART 는 보고서가 누적이므로 단일 분기 = 현재 분기 - 직전 분기.

    Returns:
        {"rev_act": {"YYYY-MM": KRW}, "oi_act": {"YYYY-MM": KRW}}
    """
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        app.logger.warning(f"[DART] {krx_code}: DART_API_KEY 미설정")
        return {"rev_act": {}, "oi_act": {}}

    corp_codes = _load_dart_corp_codes()
    corp_code = corp_codes.get(krx_code)
    if not corp_code:
        app.logger.warning(
            f"[DART] {krx_code}: corp_code 매핑 없음 (corp_codes map size={len(corp_codes)})"
        )
        return {"rev_act": {}, "oi_act": {}}
    app.logger.info(f"[DART] {krx_code}: corp_code={corp_code}")

    rev_cum = {}   # (year, qtr_idx) → cumulative revenue
    oi_cum  = {}

    # 분기 보고서 코드
    quarters = [(1, "11013", "03"), (2, "11012", "06"),
                (3, "11014", "09"), (4, "11011", "12")]

    today = datetime.now()
    cur_year = today.year
    # CFS 우선, 실패 시 OFS 도 시도
    for y in (cur_year - 1, cur_year):
        for qi, reprt, month in quarters:
            found = False
            for fs_div in ("CFS", "OFS"):
                try:
                    resp = requests.get(
                        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                        params={
                            "crtfc_key": api_key,
                            "corp_code": corp_code,
                            "bsns_year": str(y),
                            "reprt_code": reprt,
                            "fs_div":     fs_div,
                        },
                        timeout=12,
                    )
                    d = resp.json()
                    status = d.get("status", "?")
                    msg    = d.get("message", "")
                    if status != "000":
                        app.logger.debug(
                            f"[DART] {krx_code} {y}Q{qi} {fs_div}: status={status} msg={msg}"
                        )
                        continue
                    rev_found, oi_found = False, False
                    for item in d.get("list", []):
                        nm = item.get("account_nm", "")
                        amt = _dart_parse_amount(item.get("thstrm_amount", ""))
                        if amt is None:
                            continue
                        if nm in ("매출액", "수익(매출액)", "영업수익", "매출"):
                            rev_cum[(y, qi)] = amt
                            rev_found = True
                        elif nm in ("영업이익", "영업이익(손실)"):
                            oi_cum[(y, qi)] = amt
                            oi_found = True
                    if rev_found or oi_found:
                        app.logger.info(
                            f"[DART] {krx_code} {y}Q{qi} {fs_div}: "
                            f"rev={'✓' if rev_found else '✗'} "
                            f"oi={'✓' if oi_found else '✗'}"
                        )
                        found = True
                        break    # 이 분기 데이터 잡았으면 다음 fs_div 시도 안 함
                except Exception as e:
                    app.logger.warning(f"[DART] {krx_code} {y}Q{qi} {fs_div} error: {e}")
            if not found:
                # 발표 안 된 분기 (정상). 단, Q1 of current year missing 일 수 있어 로그
                if y == cur_year and qi == 1 and today.month >= 5:
                    app.logger.warning(
                        f"[DART] {krx_code} {y}Q1 데이터 없음 (5월인데도 미공시?)"
                    )

    # 누적 → 단일 분기 변환
    def _to_quarterly(cum_map):
        out = {}
        for (y, qi), v in cum_map.items():
            month = {1: "03", 2: "06", 3: "09", 4: "12"}[qi]
            period = f"{y}-{month}"
            if qi == 1:
                out[period] = v   # Q1 은 이미 단일
            else:
                prev = cum_map.get((y, qi - 1))
                if prev is not None:
                    out[period] = v - prev
                else:
                    # 직전 분기 데이터 없으면 누적 그대로 (마지막 보고서면 ≈ 전체)
                    out[period] = v
        return out

    result = {
        "rev_act": _to_quarterly(rev_cum),
        "oi_act":  _to_quarterly(oi_cum),
    }
    app.logger.info(
        f"[DART] {krx_code}: rev_periods={sorted(result['rev_act'].keys())[-4:]} "
        f"oi_periods={sorted(result['oi_act'].keys())[-4:]}"
    )
    return result


def fetch_kr_analysts(code):
    """네이버 금융 리서치 리포트에서 국내 증권사 목표가·투자의견 수집.

    전략:
      1) 리서치 목록 페이지에서 nid(리포트ID) / 증권사 / 날짜 수집 (증권사 중복 제거)
      2) 개별 리포트 페이지를 ThreadPoolExecutor 로 병렬 요청
         → view_info_1 div 에서 em.money(목표가) + em.coment(투자의견) 파싱
      3) 수집 결과로 컨센서스 요약 계산
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE
    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        app.logger.warning("beautifulsoup4 not installed")
        return {"targets": [], "summary": {}}

    GRADE_MAP = {
        '강력매수': 5, '매수': 4, '비중확대': 4, 'Outperform': 4,
        '중립': 3, '보유': 3, '시장수익률': 3,
        '비중축소': 2, '매도': 1,
    }
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Referer': 'https://finance.naver.com/',
    }

    # ── 1. 리서치 목록 파싱 → (nid, 증권사, 날짜) ────────────────────
    items = []   # [(nid, firm, date)]
    try:
        r = requests.get(
            f"https://finance.naver.com/research/company_list.naver"
            f"?searchType=itemCode&itemCode={code}&page=1",
            headers=hdrs, timeout=10,
        )
        soup = _BS(r.content, 'html.parser', from_encoding='euc-kr')
        table = soup.find('table', class_='type_1')
        if table:
            seen_firms = set()
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) < 5:
                    continue
                # 구조: [종목명, 제목, 증권사, pdf, 날짜, 조회수]
                a_tag = cells[1].find('a')
                if not a_tag:
                    continue
                href = a_tag.get('href', '')
                m = re.search(r'nid=(\d+)', href)
                if not m:
                    continue
                nid  = m.group(1)
                firm = cells[2].get_text(strip=True)
                date = cells[4].get_text(strip=True)
                if not firm or firm in seen_firms:
                    continue
                seen_firms.add(firm)
                items.append((nid, firm, date))
                if len(items) >= 6:
                    break
    except Exception as e:
        app.logger.error(f"KR analyst list fetch: {e}")

    if not items:
        return {"targets": [], "summary": {}}

    # ── 2. 개별 리포트 병렬 요청 → 목표가·투자의견 추출 ─────────────
    def _fetch_detail(item):
        nid, firm, date = item
        try:
            rd = requests.get(
                f"https://finance.naver.com/research/company_read.naver?nid={nid}",
                headers=hdrs, timeout=8,
            )
            s = _BS(rd.content, 'html.parser', from_encoding='euc-kr')
            div = s.find('div', class_='view_info_1')
            target_price, opinion = None, None
            if div:
                money = div.find('em', class_='money')
                if money:
                    strong = money.find('strong')
                    if strong:
                        try:
                            target_price = float(strong.get_text(strip=True).replace(',', ''))
                        except ValueError:
                            pass
                coment = div.find('em', class_='coment')
                if coment:
                    opinion = coment.get_text(strip=True)
            return firm, date, target_price, opinion
        except Exception:
            return firm, date, None, None

    # 개별 fetch 8s, 전체 25s 안에 안 끝나면 강제 종료
    details = []
    from concurrent.futures import as_completed as _as_completed
    with _TPE(max_workers=4) as ex:
        futures = {ex.submit(_fetch_detail, it): it for it in items}
        try:
            for fut in _as_completed(futures, timeout=25):
                try:
                    details.append(fut.result(timeout=8))
                except Exception as e:
                    it = futures[fut]
                    app.logger.warning(f"[kr_analysts] {it[1]} {it[2]} failed: {e}")
                    details.append((it[1], it[2], None, None))
        except Exception as e:
            app.logger.warning(f"[kr_analysts] overall timeout/error: {e}")
            # 남은 futures 의 결과는 (firm, date, None, None) 으로 채움
            for fut in futures:
                if fut.done():
                    continue
                it = futures[fut]
                details.append((it[1], it[2], None, None))

    # ── 3. 결과 정리 ──────────────────────────────────────────────────
    targets = []
    for firm, date, target_price, opinion in details:
        grade = opinion if opinion in GRADE_MAP else '매수'
        score = GRADE_MAP.get(grade, 4)
        targets.append({
            'firm':        firm,
            'grade':       grade,
            'target':      target_price,
            'prior_target': None,
            'action':      '',
            'date':        date,
            'score':       score,
        })

    # ── 4. 컨센서스 요약 계산 ─────────────────────────────────────────
    summary = {}
    prices = [t['target'] for t in targets if t['target']]
    if prices:
        summary['mean'] = round(sum(prices) / len(prices))
        summary['high'] = max(prices)
        summary['low']  = min(prices)
    if targets:
        avg_sc = sum(t['score'] for t in targets) / len(targets)
        summary['recommendation'] = (
            'strong_buy' if avg_sc >= 4.5 else
            'buy'        if avg_sc >= 3.5 else
            'sell'       if avg_sc <  2.5 else 'hold'
        )
        summary['num_analysts'] = len(targets)

    return {"targets": targets, "summary": summary}


def fetch_analysts(stock, info):
    try:
        ud = stock.upgrades_downgrades
        if ud is None or ud.empty:
            return {"consensus": None, "targets": [], "summary": {}}

        ud = ud.reset_index()
        ud["GradeDate"] = pd.to_datetime(ud["GradeDate"], utc=True)
        # 최근 90일 이내, 목표가 있는 것만
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=90)
        recent = ud[ud["GradeDate"] >= cutoff].copy()
        if recent.empty:
            recent = ud.head(20).copy()

        # 회사별 가장 최근 의견만
        recent = recent.sort_values("GradeDate", ascending=False)
        recent = recent.drop_duplicates(subset=["Firm"], keep="first")
        recent = recent[recent["currentPriceTarget"].notna() & (recent["currentPriceTarget"] > 0)]

        # 등급 점수 계산
        def grade_score(g):
            return GRADE_SCORE.get(str(g).lower().strip(), 3)

        recent["score"] = recent["ToGrade"].apply(grade_score)
        recent = recent.sort_values("score", ascending=False).head(5)

        targets = []
        for _, row in recent.iterrows():
            targets.append({
                "firm": row["Firm"],
                "grade": row["ToGrade"],
                "target": safe_float(row["currentPriceTarget"]),
                "prior_target": safe_float(row["priorPriceTarget"]),
                "action": row["Action"],
                "date": row["GradeDate"].strftime("%Y-%m-%d"),
                "score": int(row["score"]),
            })

        # 컨센서스 요약
        apt = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        summary = {
            "mean": safe_float(info.get("targetMeanPrice")),
            "high": safe_float(info.get("targetHighPrice")),
            "low":  safe_float(info.get("targetLowPrice")),
            "recommendation": info.get("recommendationKey", ""),
            "num_analysts": info.get("numberOfAnalystOpinions"),
        }
        return {"targets": targets, "summary": summary}
    except Exception:
        return {"targets": [], "summary": {}}


# ── 시장 컨텍스트 & 이벤트 캘린더 ─────────────────────────
_MARKET_CACHE = {}   # market_kind → (timestamp, data)
_MARKET_TTL   = 600  # 10분 (지수는 빨리 안 바뀜)


def fetch_market_context(is_korean: bool) -> dict:
    """시장 인덱스(S&P/Nasdaq/VIX 또는 KOSPI/KOSDAQ/환율) 현재가·등락률."""
    key = 'KR' if is_korean else 'US'
    now = time.time()
    cached = _MARKET_CACHE.get(key)
    if cached and (now - cached[0]) < _MARKET_TTL:
        return cached[1]

    if is_korean:
        tickers_map = [
            ('KOSPI',   '^KS11'),
            ('KOSDAQ',  '^KQ11'),
            ('환율',     'KRW=X'),
        ]
    else:
        tickers_map = [
            ('S&P 500', '^GSPC'),
            ('Nasdaq',  '^IXIC'),
            ('VIX',     '^VIX'),
        ]

    result = {}
    for label, sym in tickers_map:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period='5d', interval='1d', auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 2:
                continue
            closes = hist['Close'].dropna()
            if len(closes) < 2:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            if prev == 0 or pd.isna(last) or pd.isna(prev):
                continue
            chg_pct = (last - prev) / prev * 100
            result[label] = {
                'symbol':     sym,
                'value':      round(last, 2),
                'change_pct': round(chg_pct, 2),
            }
        except Exception as e:
            app.logger.debug(f"market context {sym}: {e}")

    _MARKET_CACHE[key] = (now, result)
    return result


def _norm_dt(d):
    """다양한 타입(timestamp/int/datetime/str)을 tz-naive Timestamp 로 통일."""
    if d is None:
        return None
    try:
        if isinstance(d, (int, float)):
            return pd.Timestamp(d, unit='s')
        ts = pd.Timestamp(d)
        if ts.tz is not None:
            ts = ts.tz_localize(None)
        return ts
    except Exception:
        return None


# ── 매크로 이벤트 스케줄 (FOMC / CPI / 한국 금통위) ──
# 2026년 일정 (필요 시 매년 업데이트)
_FOMC_DATES = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]
_CPI_DATES = [   # 미국 노동통계국 CPI 발표 (월간)
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-14",
    "2026-05-13", "2026-06-10", "2026-07-15", "2026-08-12",
    "2026-09-10", "2026-10-15", "2026-11-12", "2026-12-10",
]
_BOK_DATES = [   # 한국 금통위
    "2026-01-22", "2026-02-26", "2026-04-09", "2026-05-28",
    "2026-07-09", "2026-08-27", "2026-10-22", "2026-11-26",
]
# 한국 분기/반기/사업 보고서 마감일
_KR_REPORT_DATES = [
    ("2026-03-31", "사업보고서 마감"),
    ("2026-05-15", "1분기 보고서 마감"),
    ("2026-08-14", "반기 보고서 마감"),
    ("2026-11-14", "3분기 보고서 마감"),
]


def _add_macro_events(events: list, is_kr: bool, today: pd.Timestamp) -> None:
    """매크로/시장 이벤트 추가 (가까운 1개씩만 부착)."""
    def _push_first(dates: list, icon: str, label: str, window_days: int):
        for d_str in dates:
            ts = pd.Timestamp(d_str)
            days = (ts.normalize() - today).days
            if 0 <= days <= window_days:
                events.append({
                    'type':  'macro', 'icon': icon, 'label': label,
                    'date':  d_str, 'days': int(days),
                })
                return

    # FOMC (모든 주식에 영향 - 미국 금리)
    _push_first(_FOMC_DATES, '🏦', 'FOMC 회의 (미국 금리)', 90)

    if is_kr:
        # 한국 금통위
        _push_first(_BOK_DATES, '🏦', '한국 금통위 (기준금리)', 90)
        # 분기 보고서 마감 (가장 가까운 것)
        for d_str, label in _KR_REPORT_DATES:
            ts = pd.Timestamp(d_str)
            days = (ts.normalize() - today).days
            if 0 <= days <= 60:
                events.append({
                    'type':  'filing', 'icon': '📑', 'label': label,
                    'date':  d_str, 'days': int(days),
                })
                break
    else:
        # 미국 CPI 발표
        _push_first(_CPI_DATES, '📊', '미국 CPI 발표', 45)


def fetch_upcoming_events(stock, info: dict, ticker: str = "") -> list:
    """다음 실적 발표 / 배당 / 매크로 이벤트 (여러 소스 시도)."""
    events = []
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
    next_earnings_ts = None

    # ── 1) get_earnings_dates ──
    try:
        ed = stock.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            for d in ed.index:
                ts = _norm_dt(d)
                if ts is None:
                    continue
                if ts.normalize() >= today and (next_earnings_ts is None or ts < next_earnings_ts):
                    next_earnings_ts = ts
            app.logger.info(f"[events] get_earnings_dates → next={next_earnings_ts}")
    except Exception as e:
        app.logger.warning(f"[events] get_earnings_dates failed: {e}")

    # ── 2) stock.calendar (Earnings Date) ──
    if next_earnings_ts is None:
        try:
            cal = stock.calendar
            if cal is not None:
                # pandas DataFrame 또는 dict 둘 다 처리
                edates = None
                if hasattr(cal, 'get'):
                    edates = cal.get('Earnings Date') or cal.get('earnings_date')
                elif hasattr(cal, 'loc'):
                    try: edates = cal.loc['Earnings Date'].tolist()
                    except Exception: pass
                if edates:
                    if not isinstance(edates, (list, tuple)):
                        edates = [edates]
                    for d in edates:
                        ts = _norm_dt(d)
                        if ts and ts.normalize() >= today:
                            if next_earnings_ts is None or ts < next_earnings_ts:
                                next_earnings_ts = ts
                    app.logger.info(f"[events] calendar → next={next_earnings_ts}")
        except Exception as e:
            app.logger.warning(f"[events] calendar failed: {e}")

    # ── 3) info 에 있는 earningsTimestamp / earningsDate ──
    if next_earnings_ts is None:
        try:
            for key in ('earningsTimestamp', 'earningsTimestampStart',
                        'earningsTimestampEnd', 'earningsDate'):
                v = info.get(key)
                if v:
                    ts = _norm_dt(v)
                    if ts and ts.normalize() >= today:
                        if next_earnings_ts is None or ts < next_earnings_ts:
                            next_earnings_ts = ts
                        break
            app.logger.info(f"[events] info → next={next_earnings_ts}")
        except Exception as e:
            app.logger.warning(f"[events] info earnings failed: {e}")

    if next_earnings_ts is not None:
        days = (next_earnings_ts.normalize() - today).days
        if 0 <= days <= 180:
            events.append({
                'type':  'earnings',
                'icon':  '📊',
                'label': '실적 발표',
                'date':  next_earnings_ts.strftime('%Y-%m-%d'),
                'days':  int(days),
            })

    # ── 배당락일 ──
    try:
        ex_div = info.get('exDividendDate')
        ts = _norm_dt(ex_div)
        if ts is not None:
            days = (ts.normalize() - today).days
            if 0 <= days <= 365:
                events.append({
                    'type':  'dividend',
                    'icon':  '💰',
                    'label': '배당락일',
                    'date':  ts.strftime('%Y-%m-%d'),
                    'days':  int(days),
                })
    except Exception as e:
        app.logger.warning(f"[events] ex-dividend failed: {e}")

    # ── 배당 지급일 ──
    try:
        div_pay = info.get('dividendDate') or info.get('lastDividendDate')
        ts = _norm_dt(div_pay)
        if ts is not None:
            days = (ts.normalize() - today).days
            if 0 <= days <= 180:
                events.append({
                    'type':  'dividend-pay',
                    'icon':  '💵',
                    'label': '배당 지급일',
                    'date':  ts.strftime('%Y-%m-%d'),
                    'days':  int(days),
                })
    except Exception as e:
        app.logger.warning(f"[events] dividend pay failed: {e}")

    # ── 매크로 이벤트 (FOMC / CPI / 금통위 / 보고서 마감) ──
    try:
        _add_macro_events(events, is_kr, today)
    except Exception as e:
        app.logger.warning(f"[events] macro events failed: {e}")

    events.sort(key=lambda e: e.get('days', 999))
    return events[:7]   # 너무 많으면 잘림 (가까운 7개만)


def fetch_news(stock):
    try:
        news_list = stock.news or []
        raw = []
        for item in news_list[:8]:
            c = item.get("content", {})
            if not c:
                continue
            title = c.get("title", "")
            desc  = c.get("description") or c.get("summary", "")
            desc  = re.sub(r"<[^>]+>", "", desc or "")[:200]
            pub   = c.get("pubDate", "") or c.get("displayTime", "")
            url   = ""
            cu = c.get("canonicalUrl") or c.get("clickThroughUrl") or {}
            if isinstance(cu, dict):
                url = cu.get("url", "")
            provider = ""
            pv = c.get("provider", {})
            if isinstance(pv, dict):
                provider = pv.get("displayName", "")
            if title:
                raw.append({"title": title, "desc": desc, "pub": pub[:10], "url": url, "provider": provider})

        if not raw:
            return []

        # 제목 + 설명 병렬 번역
        titles = _translate_batch([r["title"] for r in raw])
        descs  = _translate_batch([r["desc"]  for r in raw])

        return [
            {**r, "title": titles[i], "title_orig": r["title"], "desc": descs[i]}
            for i, r in enumerate(raw)
        ]
    except Exception:
        return []


def analyze_move_reason(ticker, name, price_change_pct, news_items, stock_data=None, ai_result=None):
    """Groq으로 주가 변동 이유 종합 분석."""
    if price_change_pct is None or abs(price_change_pct) < 0.5:
        return None

    direction = "상승" if price_change_pct > 0 else "하락"
    kind      = "급등" if price_change_pct >= 5 else ("급락" if price_change_pct <= -5 else direction)

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if api_key:
        # ── 기술적 지표 컨텍스트 ──────────────────────────
        tech_ctx = ""
        if ai_result:
            rsi    = ai_result.get("rsi")
            macd   = ai_result.get("macd")
            trend  = ai_result.get("trend", "")
            verdict = ai_result.get("verdict", "")
            score  = ai_result.get("score", 0)
            ma_analysis = ai_result.get("ma_analysis", {})
            ma_trend = ma_analysis.get("trend", "") if ma_analysis else ""

            trend_map = {
                "strong-uptrend": "강한 상승 추세",
                "uptrend": "상승 추세",
                "sideways": "횡보",
                "downtrend": "하락 추세",
                "strong-downtrend": "강한 하락 추세",
            }
            tech_ctx = (
                f"\n[기술적 지표]\n"
                f"- 종합 판단: {verdict} (점수: {score:+d})\n"
                f"- 추세: {trend_map.get(trend, trend)}\n"
                f"- RSI(14): {rsi if rsi else '—'}\n"
                f"- MACD: {macd if macd else '—'}\n"
                f"- 이동평균 추세: {ma_trend}\n"
            )

        # ── 종목 기본 정보 ────────────────────────────────
        stock_ctx = ""
        if stock_data:
            sector   = stock_data.get("sector", "")
            pe       = stock_data.get("pe_ratio")
            year_high = stock_data.get("year_high")
            year_low  = stock_data.get("year_low")
            cur_price = stock_data.get("current_price")
            vol_ratio = ""
            vol  = stock_data.get("volume")
            avg_vol = stock_data.get("avg_volume")
            if vol and avg_vol and avg_vol > 0:
                vr = vol / avg_vol
                vol_ratio = f"{vr:.1f}배 (평균 대비)"
            stock_ctx = (
                f"\n[종목 정보]\n"
                f"- 섹터: {sector or '—'}\n"
                f"- 현재가: {cur_price}\n"
                f"- 52주 고가: {year_high} / 저가: {year_low}\n"
                f"- PER: {pe if pe else '—'}\n"
                f"- 거래량: {vol_ratio or '—'}\n"
            )

        # ── 뉴스 헤드라인 (원문) ──────────────────────────
        news_ctx = ""
        if news_items:
            headlines = "\n".join(
                f"- {n.get('title_orig') or n['title']}" for n in news_items[:6]
            )
            news_ctx = f"\n[최근 뉴스 헤드라인]\n{headlines}"

        prompt = f"""당신은 주식 시장 전문 애널리스트입니다.
아래 데이터를 종합적으로 분석하여 {name}({ticker}) 주식이 오늘 {price_change_pct:+.2f}% {kind}한 이유를 설명하세요.
{stock_ctx}{tech_ctx}{news_ctx}

[분석 지침]
1. 단순히 뉴스를 요약하지 말고, 기술적 지표·시황·뉴스를 종합해 실질적인 원인을 분석하세요.
2. RSI, 추세, 거래량 등 지표가 이번 움직임을 어떻게 뒷받침하는지 언급하세요.
3. 뉴스가 주가에 미친 영향을 구체적으로 설명하세요 (없으면 기술적/시장 요인 위주로).
4. 4~6문장으로 간결하되 인사이트 있게 작성하세요.
5. 한국어로만 답변하세요. 불필요한 서론 없이 바로 분석 내용으로 시작하세요."""

        text = _groq_chat(
            api_key,
            system_msg="당신은 한국어로 답변하는 주식 시장 전문 애널리스트입니다.",
            user_msg=prompt,
            max_tokens=400,
            temperature=0.4,
            label=f"move_reason:{ticker}",
            timeout=20,
        )
        if text:
            return text

    # 폴백
    if not news_items:
        return f"현재 {kind} 관련 정보를 찾을 수 없습니다. 거시경제 지표나 시장 전반의 흐름을 함께 확인해 보세요."
    top = [n['title'] for n in news_items[:3]]
    return "관련 뉴스: " + " / ".join(top)


# ── Groq 응답 캐시 (rate limit 회피) ──
_OVERVIEW_CACHE = {}              # ticker → (timestamp, sections)
_OVERVIEW_TTL   = 60 * 60 * 6     # 6시간 캐시

# Groq 모델 fallback 후보
_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
]


def _groq_chat(api_key, system_msg, user_msg, max_tokens=800, temperature=0.4,
               label="generic", timeout=30):
    """Groq Chat Completion 공용 호출 (모델 fallback + 재시도).

    Returns: 텍스트 또는 None
    """
    for model_name in _GROQ_MODELS:
        for attempt in range(2):
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user",   "content": user_msg},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=timeout,
                )

                if resp.status_code == 429:
                    wait = 1.5 * (attempt + 1)
                    app.logger.warning(f"[groq:{label}] {model_name} 429, retry in {wait}s")
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    wait = 1.5 * (attempt + 1)
                    app.logger.warning(f"[groq:{label}] {model_name} {resp.status_code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 400:
                    body = resp.text[:300] if resp.text else "(empty)"
                    app.logger.error(f"[groq:{label}] {model_name} HTTP {resp.status_code}: {body}")
                    break   # 이 모델 안 됨, 다음 모델 시도

                data = resp.json()

                if "error" in data:
                    err = data["error"]
                    msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    app.logger.error(f"[groq:{label}] {model_name} error: {msg}")
                    if "model" in msg.lower() or "deprecated" in msg.lower() or "decommissioned" in msg.lower():
                        break
                    continue

                if "choices" not in data or not data["choices"]:
                    app.logger.error(f"[groq:{label}] {model_name} no choices: {str(data)[:200]}")
                    break

                text = (data["choices"][0].get("message", {}).get("content") or "").strip()
                if not text:
                    app.logger.error(f"[groq:{label}] {model_name} empty")
                    break

                app.logger.info(f"[groq:{label}] OK model={model_name} attempt={attempt+1}")
                return text

            except requests.exceptions.Timeout:
                app.logger.warning(f"[groq:{label}] {model_name} timeout (attempt {attempt+1})")
                continue
            except Exception as e:
                app.logger.error(f"[groq:{label}] {model_name} {type(e).__name__}: {e}")
                break

    app.logger.warning(f"[groq:{label}] 모든 모델 실패")
    return None


def fetch_company_overview(ticker, name, info, revenue_quarters, currency):
    """Groq으로 기업 소개·주요 사업·분석 인사이트를 한국어로 생성 (6h 캐시 + 재시도)."""
    # ── 캐시 hit ──
    now = time.time()
    cached = _OVERVIEW_CACHE.get(ticker)
    if cached and (now - cached[0]) < _OVERVIEW_TTL:
        app.logger.info(f"[company_overview] {ticker}: cache hit")
        return cached[1]

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        app.logger.warning(
            f"[company_overview] {ticker}: GROQ_API_KEY 미설정 — Render 환경변수 확인 필요"
        )
        return None

    is_krw = (currency == "KRW")
    market = "한국" if is_krw else "미국"

    # 기본 정보 구성
    sector   = info.get("sector", "") or ""
    industry = info.get("industry", "") or ""
    mktcap   = info.get("marketCap")
    pe       = info.get("trailingPE")
    fwd_pe   = info.get("forwardPE")
    eng_desc = (info.get("longBusinessSummary") or "")[:600]

    # 매출 추이 텍스트
    rev_ctx = ""
    if revenue_quarters:
        lines = []
        for q in revenue_quarters:
            v = q.get("actual") or q.get("value")
            if v is None:
                continue
            if is_krw:
                if abs(v) >= 1e12:
                    fv = f"{v/1e12:.1f}조원"
                elif abs(v) >= 1e8:
                    fv = f"{v/1e8:.0f}억원"
                else:
                    fv = f"{v:,.0f}원"
            else:
                if abs(v) >= 1e9:
                    fv = f"${v/1e9:.1f}B"
                elif abs(v) >= 1e6:
                    fv = f"${v/1e6:.0f}M"
                else:
                    fv = f"${v:,.0f}"
            lines.append(f"{q['period']}: {fv}")
        if lines:
            rev_ctx = "\n분기 매출 (최신순): " + " / ".join(lines)

    prompt = f"""당신은 {market} 주식 시장 전문 애널리스트입니다.
아래 정보를 바탕으로 {name}({ticker})에 대해 한국어로 분석해 주세요.

[기업 기본 정보]
- 섹터/산업: {sector} / {industry}
- 시가총액: {mktcap:,} {currency} (있을 경우)
- PER: {pe} / 선행PER: {fwd_pe}{rev_ctx}
- 영문 사업 개요: {eng_desc}

아래 3가지 항목을 각각 작성해 주세요. 각 항목은 해당 레이블로 시작하세요.

[기업소개] 이 회사가 어떤 회사인지 2~3문장으로 쉽고 명확하게 설명하세요. 한국 독자 기준으로 친숙하게 작성하세요.

[주요사업] 이 회사가 주로 어떤 사업으로 돈을 버는지 핵심 매출원 3~5가지를 bullet 형태(• 로 시작)로 간결하게 작성하세요.

[기업분석] 아래 4가지를 모두 포함해 5~7문장으로 작성하세요.
1) 해당 산업({industry}) 내에서 이 기업이 차지하는 시장 지위·경쟁 포지션 (점유율, 경쟁사 대비 강점 등)
2) 해당 산업의 중장기 전망 (성장 동인, 구조적 트렌드, 리스크 요인)
3) 이 기업의 향후 전망 (실적 성장 가능성, 신사업·촉매제, 밸류에이션 관점)
4) 투자 시 주의해야 할 핵심 리스크 1~2가지

모든 답변은 한국어로만 작성하고 불필요한 서론 없이 바로 시작하세요."""

    text = _groq_chat(
        api_key,
        system_msg="당신은 한국어로 답변하는 주식 전문 애널리스트입니다.",
        user_msg=prompt,
        max_tokens=800,
        temperature=0.4,
        label=f"overview:{ticker}",
        timeout=30,
    )
    if not text:
        return None

    # 파싱: [기업소개], [주요사업], [기업분석] 섹션 분리
    import re as _re
    sections = {}
    for key in ["기업소개", "주요사업", "기업분석"]:
        m = _re.search(rf'\[{key}\]\s*(.*?)(?=\[(?:기업소개|주요사업|기업분석)\]|$)', text, _re.DOTALL)
        if m:
            sections[key] = m.group(1).strip()
    result = sections if sections else {"기업소개": text}

    # 캐시 저장 후 반환
    _OVERVIEW_CACHE[ticker] = (now, result)
    app.logger.info(f"[company_overview] {ticker} cached 6h")
    return result


@app.route("/api/analyze", methods=["GET"])
def analyze():
    ticker = request.args.get("ticker", "").strip().upper()
    period = "max"
    interval = request.args.get("interval", "1d")

    if interval not in ("1d", "1wk", "1mo"):
        interval = "1d"

    INTERVAL_LABELS = {"1d": "일봉", "1wk": "주봉", "1mo": "월봉"}
    interval_label = INTERVAL_LABELS[interval]

    # period별 차트 봉 수 (선택한 기간 전체 표시)
    PERIOD_BARS = {
        "3mo": 65,   "6mo": 130,  "1y": 260,
        "2y": 520,   "5y": 1300,  "10y": 2600, "max": 99999,
    }
    chart_bars = PERIOD_BARS.get(period, 130)

    # 주봉/월봉인데 기간이 너무 짧으면 자동 확장
    MIN_PERIOD = {"1wk": "1y", "1mo": "5y"}
    SHORT_PERIODS = ("3mo", "6mo")
    if interval in MIN_PERIOD and period in SHORT_PERIODS:
        period = MIN_PERIOD[interval]

    if not ticker:
        return jsonify({"error": "티커를 입력해주세요"}), 400

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)

        if df.empty:
            return jsonify({"error": f"'{ticker}' 데이터를 찾을 수 없습니다. 티커를 확인해주세요."}), 404

        info = stock.info

        # ── 한국 주식 재무 데이터 보완 (네이버 금융) ────────────────
        is_korean = ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")
        if is_korean:
            krx_code = ticker.split(".")[0]
            naver = fetch_naver_fundamentals(krx_code)

            # 현재가 기준 PER/PBR 직접 계산 (네이버 역사 테이블 값 ×)
            ref_price = safe_float(info.get("currentPrice")) or safe_float(info.get("previousClose"))
            if naver and ref_price:
                if naver.get("eps"):
                    info["trailingEps"] = naver["eps"]
                    info["trailingPE"]  = round(ref_price / naver["eps"], 2)
                if naver.get("bps"):
                    info["bookValue"]   = naver["bps"]
                    info["priceToBook"] = round(ref_price / naver["bps"], 2)

            # 배당수익률: 네이버 DPS / 현재가로 직접 계산
            if naver.get("dps") and ref_price:
                info["_naver_div_yield"] = naver["dps"] / ref_price

        # NaN 행 제거 (한국 주식 등 마지막 행이 NaN일 수 있음)
        df = df.dropna(subset=["Close", "Open", "High", "Low"])
        if df.empty:
            return jsonify({"error": f"'{ticker}' 유효한 가격 데이터가 없습니다."}), 404

        df = add_all_indicators(df)

        # ── 주봉 데이터 (추세 보조 지표용) ──────────────────────────────
        df_weekly = None
        try:
            df_w_raw = stock.history(period="2y", interval="1wk")
            df_w_raw = df_w_raw.dropna(subset=["Close", "Open", "High", "Low"])
            if not df_w_raw.empty:
                df_weekly = add_all_indicators(df_w_raw)
        except Exception:
            df_weekly = None

        ai_result = analyze_signals(df, info, df_weekly=df_weekly)

        # 차트용 데이터
        chart_df = df.tail(chart_bars).copy()
        chart_df.index = chart_df.index.strftime("%Y-%m-%d")

        def series(col):
            return [safe_float(v) for v in chart_df[col]] if col in chart_df.columns else []

        chart_data = {
            "dates": chart_df.index.tolist(),
            "open": [safe_float(v) for v in chart_df["Open"]],
            "high": [safe_float(v) for v in chart_df["High"]],
            "low": [safe_float(v) for v in chart_df["Low"]],
            "close": [safe_float(v) for v in chart_df["Close"]],
            "volume": [safe_float(v) for v in chart_df["Volume"]],
            "ma5": series("MA5"),
            "ma20": series("MA20"),
            "ma60": series("MA60"),
            "ma120": series("MA120"),
            "bb_upper": series("BB_upper"),
            "bb_mid": series("BB_mid"),
            "bb_lower": series("BB_lower"),
            "tenkan": series("tenkan"),
            "kijun": series("kijun"),
            "senkou_a": series("senkou_a"),
            "senkou_b": series("senkou_b"),
            "rsi": series("RSI"),
            "macd": series("MACD"),
            "macd_signal": series("MACD_signal"),
            "macd_hist": series("MACD_hist"),
        }

        current_price = safe_float(df["Close"].iloc[-1])
        prev_close = safe_float(df["Close"].iloc[-2])
        price_change = round(current_price - prev_close, 2) if current_price and prev_close else None
        price_change_pct = round((price_change / prev_close) * 100, 2) if price_change and prev_close else None

        # 52주 고가/저가
        year_high = safe_float(df["High"].tail(252).max())
        year_low = safe_float(df["Low"].tail(252).min())

        # ── 분기 실적 (매출 + EPS, 발표치 + 추정치) ──────────────────────
        revenue_quarters = []
        eps_quarters     = []
        try:
            # 1. 실제 매출 from quarterly_income_stmt
            qf = None
            try:
                qf = stock.quarterly_income_stmt
            except Exception:
                try:
                    qf = stock.quarterly_financials
                except Exception:
                    pass
            rev_by_period = {}          # "YYYY-MM" → actual_revenue
            if qf is not None and not qf.empty:
                rev_row = None
                for key in ["Total Revenue", "Revenue", "TotalRevenue"]:
                    if key in qf.index:
                        rev_row = qf.loc[key]
                        break
                if rev_row is not None:
                    rev = rev_row.dropna().sort_index(ascending=False)
                    for i, (idx, val) in enumerate(rev.items()):
                        if i >= 5:
                            break
                        rev_by_period[str(idx)[:7]] = safe_float(val)

            # 2. EPS 발표치 from quarterly_income_stmt (Diluted EPS / Basic EPS)
            eps_act_by_period = {}   # "YYYY-MM" → actual_eps
            if qf is not None and not qf.empty:
                eps_inc_row = None
                for key in ["Diluted EPS", "Basic EPS", "EPS"]:
                    if key in qf.index:
                        eps_inc_row = qf.loc[key]
                        break
                if eps_inc_row is not None:
                    eps_inc = eps_inc_row.dropna().sort_index(ascending=False)
                    for i, (idx, val) in enumerate(eps_inc.items()):
                        if i >= 5:
                            break
                        eps_act_by_period[str(idx)[:7]] = safe_float(val)

            # 3. EPS 추정치 + 매출 추정치 from earnings_dates / earnings_history
            rev_est_by_period = {}   # "YYYY-MM" → revenue_estimate
            eps_est_by_period = {}   # "YYYY-MM" → eps_estimate
            eps_surp_by_period = {}  # "YYYY-MM" → surprise_pct

            def _get_col(r, *keys):
                """컬럼명 변형 대응: 공백·대소문자 무시 fuzzy match"""
                for k in keys:
                    v = safe_float(r.get(k))
                    if v is not None:
                        return v
                    for col in (r.index if hasattr(r, 'index') else []):
                        if col.strip().lower() == k.strip().lower():
                            return safe_float(r[col])
                return None

            def _earnings_period(ts):
                """earnings 발표일 → 해당 분기 말 월 문자열 (YYYY-MM)"""
                t = pd.Timestamp(ts)
                return (t - pd.Timedelta(days=30)).strftime('%Y-%m')

            def _near_period_exists(period, d, days=45):
                """period와 ±days 이내의 키가 dict d에 있으면 True"""
                if period in d:
                    return True
                try:
                    p_dt = pd.to_datetime(period + '-01')
                    for p in d:
                        if abs((pd.to_datetime(p + '-01') - p_dt).days) <= days:
                            return True
                except Exception:
                    pass
                return False

            # --- 소스 1: get_earnings_dates (Yahoo Finance 컨센서스) ---
            try:
                try:
                    ed = stock.get_earnings_dates(limit=20)
                except Exception:
                    ed = getattr(stock, 'earnings_dates', None)

                if ed is not None and not ed.empty:
                    for date, row in ed.sort_index(ascending=False).iterrows():
                        period  = _earnings_period(date)
                        eps_est = _get_col(row, 'EPS Estimate', 'epsestimate', 'eps estimate')
                        eps_rep = _get_col(row, 'Reported EPS', 'epsactual', 'reported eps')
                        surp    = _get_col(row, 'Surprise(%)', 'epssurprisepct', 'surprise(%)')
                        rev_est = _get_col(row, 'Revenue Estimate', 'revenueestimate')
                        if rev_est and not _near_period_exists(period, rev_est_by_period):
                            rev_est_by_period[period] = rev_est
                        if eps_est is not None and not _near_period_exists(period, eps_est_by_period):
                            eps_est_by_period[period] = eps_est
                        if surp is not None and not _near_period_exists(period, eps_surp_by_period):
                            eps_surp_by_period[period] = surp
                        # 발표치는 quarterly_income_stmt 가 우선 → 근접 분기 없을 때만 보완
                        if eps_rep is not None and not _near_period_exists(period, eps_act_by_period):
                            eps_act_by_period[period] = eps_rep
            except Exception:
                pass

            # --- 소스 2: earnings_history (epsEstimate/epsActual 컬럼 직접 제공) ---
            try:
                eh = getattr(stock, 'earnings_history', None)
                if eh is not None and not eh.empty:
                    for date, row in eh.sort_index(ascending=False).iterrows():
                        period  = _earnings_period(date)
                        eps_est = _get_col(row, 'epsEstimate', 'eps_estimate', 'EPS Estimate')
                        eps_rep = _get_col(row, 'epsActual', 'eps_actual', 'Reported EPS')
                        surp_pct = _get_col(row, 'surprisePercent', 'surprise_percent', 'Surprise(%)')
                        if eps_est is not None and not _near_period_exists(period, eps_est_by_period):
                            eps_est_by_period[period] = eps_est
                        if surp_pct is not None and not _near_period_exists(period, eps_surp_by_period):
                            eps_surp_by_period[period] = round(surp_pct * 100, 1) if abs(surp_pct) < 5 else round(surp_pct, 1)
                        if eps_rep is not None and not _near_period_exists(period, eps_act_by_period):
                            eps_act_by_period[period] = eps_rep
            except Exception:
                pass

            app.logger.info(f"[EPS est] {ticker}: act={list(eps_act_by_period.keys())[:6]} est={list(eps_est_by_period.keys())[:6]}")

            # 4. 매출 리스트 (추정치 매칭, 최신순)
            def _match_estimate(period, est_dict):
                """period에 가장 가까운 추정치 반환 (±45일 허용)."""
                v = est_dict.get(period)
                if v is not None:
                    return v
                for p, e in est_dict.items():
                    try:
                        diff = abs((pd.to_datetime(p + '-01') -
                                    pd.to_datetime(period + '-01')).days)
                        if diff <= 45:
                            return e
                    except Exception:
                        pass
                return None

            for period, actual in sorted(rev_by_period.items(), reverse=True)[:5]:
                revenue_quarters.append({
                    "period":   period,
                    "actual":   actual,
                    "estimate": _match_estimate(period, rev_est_by_period),
                })

            # 5. EPS 리스트 (추정치·surprise 매칭, 최신순 5개로 컷)
            #    earnings_dates/history 합치면 20+ 개 쌓일 수 있으므로 명시적 제한
            sorted_eps = sorted(eps_act_by_period.items(), reverse=True)[:5]
            for period, actual in sorted_eps:
                estimate = _match_estimate(period, eps_est_by_period)
                surp     = eps_surp_by_period.get(period)
                if surp is None and actual is not None and estimate is not None and estimate != 0:
                    surp = round((actual - estimate) / abs(estimate) * 100, 1)
                eps_quarters.append({
                    "period":       period,
                    "actual":       actual,
                    "estimate":     estimate,
                    "surprise_pct": surp,
                })

        except Exception as e:
            app.logger.error(f"quarter_results fetch: {e}")
            revenue_quarters = []
            eps_quarters     = []

        # ── 한국 주식 전용: 분기 영업이익 (발표치 + 추정치) ─────────────
        oi_quarters = []
        if is_korean:
            try:
                krx_code = ticker.split(".")[0]

                # 네이버 분기 API → actuals + estimates 모두 수집
                kr_data = fetch_kr_quarterly_data(krx_code)
                kr_rev_act = kr_data["rev_act"]
                kr_oi_act  = kr_data["oi_act"]
                kr_rev_est = kr_data["rev_est"]
                kr_oi_est  = kr_data["oi_est"]

                # DART (전자공시) 데이터 머지 (가장 빠르고 정확, API key 있을 때만)
                dart_data = fetch_dart_quarterly(krx_code)
                if dart_data["rev_act"]:
                    # DART 값으로 덮어쓰기 (가장 신뢰)
                    for p, v in dart_data["rev_act"].items():
                        kr_rev_act[p] = v
                    for p, v in dart_data["oi_act"].items():
                        kr_oi_act[p] = v
                    app.logger.info(f"[KR qtr] DART merged for {krx_code}")

                app.logger.info(
                    f"[KR qtr] {krx_code}: act_periods={sorted(kr_rev_act.keys())} "
                    f"est_periods={sorted(kr_rev_est.keys())}"
                )

                # ── 매출: 네이버 actuals 우선 머지 (yfinance 보다 최신) ──
                existing_periods = {q["period"] for q in revenue_quarters}
                # 네이버에만 있는 더 최신 actual 분기 추가
                for period, val in sorted(kr_rev_act.items(), reverse=True):
                    if period not in existing_periods:
                        revenue_quarters.append({
                            "period":   period,
                            "actual":   val,
                            "estimate": _match_estimate(period, kr_rev_est),
                        })
                        existing_periods.add(period)
                # 앞으로 발표될 분기 (actual 없고 estimate만 있는 경우) - 1개까지 추가
                future_est_periods = sorted(
                    [p for p in kr_rev_est.keys()
                     if p not in existing_periods
                     and p > max(kr_rev_act.keys(), default="")]
                )
                for period in future_est_periods[:1]:    # 가장 가까운 1개만
                    revenue_quarters.append({
                        "period":   period,
                        "actual":   None,                # 아직 미발표
                        "estimate": kr_rev_est[period],
                    })
                # yfinance actual 보강
                for q in revenue_quarters:
                    if q["period"] in kr_rev_act and q.get("actual") is None:
                        q["actual"] = kr_rev_act[q["period"]]
                    if q.get("estimate") is None:
                        q["estimate"] = _match_estimate(q["period"], kr_rev_est)
                # 최신 5개로 컷 (period DESC)
                revenue_quarters.sort(key=lambda x: x["period"], reverse=True)
                revenue_quarters[:] = revenue_quarters[:5]

                # ── 영업이익: 네이버 actuals + estimates 우선 ──
                yf_oi_act = {}
                if qf is not None and not qf.empty:
                    oi_row = None
                    for key in ["Operating Income", "OperatingIncome", "EBIT"]:
                        if key in qf.index:
                            oi_row = qf.loc[key]
                            break
                    if oi_row is not None:
                        for idx, val in oi_row.dropna().items():
                            period = str(idx)[:7]
                            v = safe_float(val)
                            if v is not None:
                                yf_oi_act[period] = v

                # 네이버 actuals 머지 (네이버가 더 최신)
                merged_oi_act = {**yf_oi_act, **kr_oi_act}
                merged_periods = set(merged_oi_act.keys())

                # 앞으로 발표될 분기 (estimate만 있음) 1개 추가
                future_oi_est = sorted(
                    [p for p in kr_oi_est.keys()
                     if p not in merged_periods
                     and p > max(merged_oi_act.keys(), default="")]
                )
                for period in future_oi_est[:1]:
                    oi_quarters.append({
                        "period":   period,
                        "actual":   None,
                        "estimate": kr_oi_est[period],
                    })
                for period, actual in sorted(merged_oi_act.items(), reverse=True):
                    oi_quarters.append({
                        "period":   period,
                        "actual":   actual,
                        "estimate": _match_estimate(period, kr_oi_est),
                    })

                # 최신 5개로 컷 (period DESC)
                oi_quarters.sort(key=lambda x: x["period"], reverse=True)
                oi_quarters[:] = oi_quarters[:5]
            except Exception as e:
                app.logger.error(f"oi_quarters fetch: {e}")

        stock_data = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "current_price": current_price,
            "prev_close": prev_close,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "market_cap": info.get("marketCap"),
            "volume": safe_float(df["Volume"].iloc[-1]),
            "avg_volume": safe_float(df["Volume"].tail(20).mean()),
            "year_high": year_high,
            "year_low": year_low,
            "pe_ratio": safe_float(info.get("trailingPE")),
            "forward_pe": safe_float(info.get("forwardPE")),
            "pb_ratio": safe_float(info.get("priceToBook")),
            "eps": safe_float(info.get("trailingEps")),
            "dividend_yield": _calc_dividend_yield(info, current_price),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "currency": info.get("currency", "USD"),
            "exchange": info.get("exchange"),
            "business_summary": (
                # 한국 종목인데 영문 summary 만 있으면 자동 번역
                _translate_ko(info.get("longBusinessSummary", ""), max_chars=1500)
                if (ticker.endswith(".KS") or ticker.endswith(".KQ"))
                else info.get("longBusinessSummary", "")
            ),
            "revenue_quarters": revenue_quarters,
            "eps_quarters":     eps_quarters,
            "oi_quarters":      oi_quarters,
        }

        def calc_return(days):
            if len(df) > days:
                try:
                    past = float(df["Close"].iloc[-(days + 1)])
                    cur  = float(df["Close"].iloc[-1])
                    return round((cur / past - 1) * 100, 2) if past else None
                except Exception:
                    return None
            return None

        stock_data["return_5d"]  = calc_return(5)
        stock_data["return_1m"]  = calc_return(21)
        stock_data["return_3m"]  = calc_return(63)
        stock_data["return_1y"]  = calc_return(252)

        news_data    = fetch_news(stock)

        # ── 시장 컨텍스트 + 다가오는 이벤트 ──────────────────────────────
        is_kr_stock  = ticker.endswith('.KS') or ticker.endswith('.KQ')
        try:
            stock_data["market_context"]  = fetch_market_context(is_kr_stock)
        except Exception as e:
            app.logger.warning(f"market_context: {e}")
        try:
            stock_data["upcoming_events"] = fetch_upcoming_events(stock, info, ticker)
        except Exception as e:
            app.logger.warning(f"upcoming_events: {e}")

        # 한국 주식(.KS/.KQ)이면 네이버 금융, 아니면 yfinance 애널리스트 데이터
        if is_kr_stock:
            kr_code      = ticker.split('.')[0]
            analyst_data = fetch_kr_analysts(kr_code)
        else:
            analyst_data = fetch_analysts(stock, info)
        company_overview = fetch_company_overview(
            ticker, stock_data["name"], info,
            stock_data.get("revenue_quarters", []),
            stock_data.get("currency", "USD"),
        )
        if company_overview:
            stock_data["company_overview"] = company_overview
        move_reason  = analyze_move_reason(
            ticker, stock_data["name"],
            stock_data.get("price_change_pct"), news_data,
            stock_data=stock_data, ai_result=ai_result,
        )

        # ── 내 포지션 데이터 (로그인된 경우) ──────────────────────────────
        position_data = None
        if current_user.is_authenticated:
            holding = Holding.query.filter_by(
                user_id=current_user.id, ticker=ticker
            ).first()
            if holding:
                # ticker 기준 정확한 통화
                correct_currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else \
                                   holding.currency or "USD"
                # 이름이 티커로 저장된 경우 stock_data 또는 STOCK_DB에서 교정
                correct_name = holding.name
                if (not correct_name) or correct_name == ticker:
                    correct_name = stock_data.get("name") or correct_name
                    if (not correct_name) or correct_name == ticker:
                        for s in STOCK_DB:
                            if s.get("symbol") == ticker:
                                correct_name = s.get("name") or correct_name
                                break

                # 변경 사항 silent 마이그레이션
                changed = False
                if holding.currency != correct_currency:
                    holding.currency = correct_currency
                    changed = True
                if correct_name and holding.name != correct_name:
                    holding.name = correct_name
                    changed = True
                if changed:
                    try:
                        db.session.commit()
                        app.logger.info(f"[holding] {ticker} migrated → {correct_name}/{correct_currency}")
                    except Exception:
                        db.session.rollback()

                position_data = {
                    **holding.to_dict(),
                    "name":             correct_name,
                    "currency":         correct_currency,
                    "current_price":    current_price,
                    "current_value":    round(current_price * holding.quantity, 2) if current_price else None,
                    "purchase_value":   round(holding.purchase_price * holding.quantity, 2),
                    "recommendation":   _position_recommendation(
                        current_price, holding.purchase_price, ai_result.get("score", 0),
                        ai_result=ai_result,
                    ),
                }

        return app.response_class(
            response=json.dumps(
                {
                    "stock": stock_data, "analysis": ai_result, "chart": chart_data,
                    "interval": interval, "interval_label": interval_label,
                    "news": news_data, "analysts": analyst_data, "move_reason": move_reason,
                    "position": position_data,
                },
                cls=NumpyEncoder,
                ensure_ascii=False,
            ),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500


# ── 트렌드 신호 태그 ─────────────────────────────────────────────────────────
def _signal_tags(r):
    """ai_result에서 추세 결정에 기여한 핵심 신호를 짧은 문자열로 반환."""
    parts = []
    if r.get("new_52w_high"):        parts.append("52주 신고가")
    elif r.get("near_52w_high"):     parts.append("신고가 근방")
    if r.get("near_52w_low"):        parts.append("52주 신저가 근방")
    if r.get("recent_golden"):       parts.append("골든크로스")
    if r.get("recent_dead"):         parts.append("데드크로스")
    if r.get("vol_up_confirm"):      parts.append("거래량 동반 상승")
    elif r.get("vol_down_confirm"):  parts.append("거래량 동반 하락")
    if r.get("bb_expanding"):        parts.append("밴드 확장")
    elif r.get("bb_contracting"):    parts.append("밴드 수축")
    return f" [{' · '.join(parts)}]" if parts else ""


# ── 포지션 매매 추천 ───────────────────────────────────────────────────────────
def _position_recommendation(current_price, purchase_price, score, ai_result=None):
    if not (current_price and purchase_price):
        return None

    pct = (current_price - purchase_price) / purchase_price * 100

    # ai_result에서 추세·구간 데이터 추출
    r          = ai_result or {}
    trend      = r.get("trend", "sideways")
    stop_loss  = r.get("stop_loss")
    entry_high = r.get("entry_high")
    target_low = r.get("target_low")
    month_low  = r.get("month_low")

    is_up    = trend in ("strong-uptrend", "uptrend")
    is_down  = trend in ("strong-downtrend", "downtrend")
    is_strong= trend in ("strong-uptrend", "strong-downtrend")

    in_buy_zone  = entry_high and current_price <= entry_high
    in_sell_zone = target_low and current_price >= target_low
    below_stop   = stop_loss  and current_price <= stop_loss
    great_entry  = month_low  and purchase_price < month_low

    tags = _signal_tags(r)   # e.g. " [52주 신고가 · 골든크로스]"

    # ── 손절선 이탈 ──────────────────────────────────────
    if below_stop:
        if pct > 0:
            action, color = "익절 매도 권장", "bearish"
            reason = f"1개월 지지선 이탈{tags}. {pct:.1f}% 수익이지만 하락 전환 신호로 수익 확정 권장"
        else:
            action, color = "손절 매도 권장", "bearish"
            reason = f"1개월 지지선 이탈{tags} ({pct:.1f}% 손실). 추가 손실 방지를 위해 손절 권장"

    # ── 상승 추세 ────────────────────────────────────────
    elif is_up:
        trend_label = "강한 상승 추세" if is_strong else "상승 추세"
        if in_buy_zone:
            action, color = "추가매수 고려", "bullish"
            reason = f"{trend_label} 눌림목 구간{tags}. 현 {pct:.1f}%, 분할 추가매수 기회"
        elif in_sell_zone:
            if great_entry or pct >= 25:
                action, color = "일부 익절 후 보유", "bullish"
                reason = f"{trend_label} 고점 부근{tags} + {pct:.1f}% 수익. 20~30% 분할 익절 후 나머지 보유 추천"
            else:
                action, color = "보유 유지", "bullish"
                reason = f"{trend_label} 고점 부근{tags}. 수익({pct:.1f}%)이 크지 않아 보유 유지 권장"
        else:
            action, color = "보유 유지", "bullish"
            reason = f"{trend_label} 진행 중{tags}. 현 {pct:.1f}%, 추세가 유지되는 동안 보유 유지"

    # ── 하락 추세 ────────────────────────────────────────
    elif is_down:
        trend_label = "강한 하락 추세" if is_strong else "하락 추세"
        if pct >= 10:
            action, color = "익절 매도 권장", "bearish"
            reason = f"{trend_label} 전환 감지{tags}. {pct:.1f}% 수익 중, 추세 역행 전에 수익 확정 권장"
        elif pct >= 0:
            action, color = "매도 고려", "bearish"
            reason = f"{trend_label}{tags} + 소폭 수익({pct:.1f}%). 반등 시 매도 기회 노리기"
        elif pct >= -10:
            action, color = "손절 고려", "bearish"
            reason = f"{trend_label}{tags} + {pct:.1f}% 손실. 추가 하락 가능성 높아 손절 검토"
        else:
            action, color = "손절 매도 권장", "bearish"
            reason = f"{trend_label}{tags} + {pct:.1f}% 큰 손실. 추세 반전 신호 없으면 추가 손실 방지 우선"

    # ── 횡보 ─────────────────────────────────────────────
    else:
        if in_buy_zone:
            if pct <= -10:
                action, color = "분할매수 또는 손절", "neutral"
                reason = f"횡보 지지선 부근{tags} + {pct:.1f}% 손실. 지지 확인 후 추가매수, 이탈 시 손절"
            else:
                action, color = "보유 관망", "neutral"
                reason = f"횡보 지지선 부근{tags}. 현 {pct:.1f}%, 지지 유지되면 보유 유지"
        elif in_sell_zone:
            action, color = "익절 고려", "neutral"
            reason = f"횡보 저항선 부근{tags} ({pct:.1f}% 수익). 분할 익절로 수익 확정 권장"
        else:
            action, color = "관망", "neutral"
            reason = f"횡보 중간 구간{tags}. 현 {pct:.1f}%, 매수·매도 구간 진입 시 행동 권장"

    return {"action": action, "color": color, "reason": reason,
            "return_pct": round(pct, 2)}


# ── 현재 사용자 정보 ──────────────────────────────────────────────────────────
@app.route("/api/me")
def me():
    if not current_user.is_authenticated:
        return jsonify({"user": None})
    return jsonify({
        "user": {
            "id":            current_user.id,
            "name":          current_user.name,
            "email":         current_user.email,
            "profile_image": current_user.profile_image,
            "provider":      current_user.provider,
        }
    })


# ── 포트폴리오 조회 ───────────────────────────────────────────────────────────
@app.route("/api/portfolio")
def get_portfolio():
    if not current_user.is_authenticated:
        return jsonify([])

    holdings = Holding.query.filter_by(user_id=current_user.id)\
                            .order_by(Holding.created_at.desc()).all()
    if not holdings:
        return jsonify([])

    # 현재가 일괄 조회 (fast_info 병렬)
    tickers = list({h.ticker for h in holdings})
    prices  = {}

    def _fetch_price(ticker):
        try:
            fi = yf.Ticker(ticker).fast_info
            p  = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
            return ticker, float(p) if p else None
        except Exception:
            return ticker, None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        for t, p in ex.map(_fetch_price, tickers):
            if p:
                prices[t] = p

    # 통화·이름 자동 보정 (잘못 저장된 KRW 종목 USD 표시 / 이름이 티커로 저장된 경우)
    # STOCK_DB lookup helper
    db_name_map = {s.get("symbol"): s.get("name") for s in STOCK_DB if s.get("symbol")}

    needs_migration = False
    for h in holdings:
        # 통화
        correct_cur = "KRW" if (h.ticker.endswith(".KS") or h.ticker.endswith(".KQ")) else \
                      (h.currency or "USD")
        if h.currency != correct_cur:
            h.currency = correct_cur
            needs_migration = True
        # 이름 (티커와 같으면 STOCK_DB에서 찾아 교체)
        if (not h.name) or h.name == h.ticker:
            db_name = db_name_map.get(h.ticker)
            if db_name and db_name != h.ticker:
                h.name = db_name
                needs_migration = True
    if needs_migration:
        try:
            db.session.commit()
            app.logger.info("[portfolio] migration applied (currency/name)")
        except Exception:
            db.session.rollback()

    result = []
    for h in holdings:
        d = h.to_dict()
        cp = prices.get(h.ticker)
        d["current_price"] = cp
        if cp and h.purchase_price:
            d["return_pct"]    = round((cp - h.purchase_price) / h.purchase_price * 100, 2)
            d["return_amount"] = round((cp - h.purchase_price) * h.quantity, 2)
        else:
            d["return_pct"] = d["return_amount"] = None
        result.append(d)
    return jsonify(result)


# ── 포트폴리오 추가 ───────────────────────────────────────────────────────────
@app.route("/api/portfolio", methods=["POST"])
@login_required
def add_holding():
    data = request.get_json()
    ticker   = data.get("ticker", "").strip().upper()
    name     = (data.get("name") or "").strip() or ticker
    qty      = float(data.get("quantity", 0))
    price    = float(data.get("purchase_price", 0))
    # 통화는 ticker 기준으로 강제 보정
    currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else \
               (data.get("currency") or "USD")

    # 이름이 티커와 같으면(자동완성 미사용 등) STOCK_DB 에서 조회
    if name == ticker:
        for s in STOCK_DB:
            if s.get("symbol") == ticker:
                name = s.get("name") or name
                break

    if not ticker or qty <= 0 or price <= 0:
        return jsonify({"error": "티커·수량·매입가를 올바르게 입력해주세요"}), 400

    # 같은 티커 이미 존재하면 수량/단가 업데이트 (평균단가)
    existing = Holding.query.filter_by(user_id=current_user.id, ticker=ticker).first()
    if existing:
        total_qty   = existing.quantity + qty
        avg_price   = (existing.purchase_price * existing.quantity + price * qty) / total_qty
        existing.quantity       = round(total_qty, 6)
        existing.purchase_price = round(avg_price, 6)
        db.session.commit()
        return jsonify({"ok": True, "holding": existing.to_dict()})

    holding = Holding(
        user_id=current_user.id, ticker=ticker, name=name,
        quantity=qty, purchase_price=price, currency=currency,
    )
    db.session.add(holding)
    db.session.commit()
    return jsonify({"ok": True, "holding": holding.to_dict()})


# ── 포트폴리오 수정 ───────────────────────────────────────────────────────────
@app.route("/api/portfolio/<int:hid>", methods=["PUT"])
@login_required
def update_holding(hid):
    holding = Holding.query.filter_by(id=hid, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if "quantity"       in data: holding.quantity       = float(data["quantity"])
    if "purchase_price" in data: holding.purchase_price = float(data["purchase_price"])
    db.session.commit()
    return jsonify({"ok": True, "holding": holding.to_dict()})


# ── 포트폴리오 삭제 ───────────────────────────────────────────────────────────
@app.route("/api/portfolio/<int:hid>", methods=["DELETE"])
@login_required
def delete_holding(hid):
    holding = Holding.query.filter_by(id=hid, user_id=current_user.id).first_or_404()
    db.session.delete(holding)
    db.session.commit()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
# 모의투자 API (Paper Trading)
# ══════════════════════════════════════════════════════════════
_FX_CACHE = {"rate": None, "ts": 0}
_FX_TTL   = 10 * 60   # 10분


def _get_usd_krw_rate():
    """USD/KRW 환율 (10분 캐시). 실패 시 기본 1380."""
    now = time.time()
    if _FX_CACHE["rate"] and (now - _FX_CACHE["ts"]) < _FX_TTL:
        return _FX_CACHE["rate"]
    try:
        df = yf.Ticker("KRW=X").history(period="5d", interval="1d", auto_adjust=False)
        if df is not None and not df.empty:
            rate = float(df["Close"].iloc[-1])
            if rate > 0:
                _FX_CACHE["rate"] = rate
                _FX_CACHE["ts"]   = now
                return rate
    except Exception as e:
        app.logger.warning(f"[FX] USD/KRW fetch failed: {e}")
    return _FX_CACHE.get("rate") or 1380.0


def _to_krw(amount, currency, exchange_rate=None):
    """amount 를 KRW 환산. exchange_rate 명시되면 사용, 아니면 현재 환율."""
    if currency == "KRW":
        return amount
    if exchange_rate is None:
        exchange_rate = _get_usd_krw_rate()
    return amount * exchange_rate


def _fetch_current_price(ticker):
    """종목 현재가 (native currency)."""
    try:
        fi = yf.Ticker(ticker).fast_info
        p  = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        return float(p) if p else None
    except Exception:
        return None


@app.route("/api/trading/dashboard")
@login_required
def trading_dashboard():
    """모의투자 대시보드: 총 자산, 현금, 보유 종목, 평가 손익."""
    user = current_user
    # 기존 유저 보호: NULL 이면 1억으로 초기화
    if user.cash_balance is None:
        user.cash_balance = INITIAL_CAPITAL_KRW
    if user.initial_capital is None:
        user.initial_capital = INITIAL_CAPITAL_KRW
    db.session.commit()

    fx = _get_usd_krw_rate()
    holdings = Holding.query.filter_by(user_id=user.id).all()

    positions = []
    positions_value_krw = 0.0
    unrealized_pnl_krw  = 0.0

    if holdings:
        tickers = list({h.ticker for h in holdings})
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            prices = dict(ex.map(lambda t: (t, _fetch_current_price(t)), tickers))

        for h in holdings:
            cp  = prices.get(h.ticker)
            cur = h.currency or "USD"
            value_native = (cp or 0) * h.quantity
            cost_native  = h.purchase_price * h.quantity
            # KRW 환산
            if cur == "KRW":
                value_krw = value_native
                cost_krw  = cost_native
            else:
                value_krw = value_native * fx
                cost_krw  = cost_native  * fx
            pnl_krw = value_krw - cost_krw
            pnl_pct = ((cp or 0) - h.purchase_price) / h.purchase_price * 100 if h.purchase_price else 0
            positions_value_krw += value_krw
            unrealized_pnl_krw  += pnl_krw
            positions.append({
                "id":             h.id,
                "ticker":         h.ticker,
                "name":           h.name,
                "currency":       cur,
                "quantity":       h.quantity,
                "purchase_price": h.purchase_price,
                "current_price":  cp,
                "value_krw":      round(value_krw),
                "cost_krw":       round(cost_krw),
                "pnl_krw":        round(pnl_krw),
                "pnl_pct":        round(pnl_pct, 2),
            })

    total_assets_krw = user.cash_balance + positions_value_krw
    total_return_pct = ((total_assets_krw - user.initial_capital) / user.initial_capital * 100) \
                       if user.initial_capital > 0 else 0

    # 실현 손익 (모든 매도 거래 합)
    realized_pnl_krw = db.session.query(
        db.func.coalesce(db.func.sum(Transaction.realized_pnl_krw), 0)
    ).filter_by(user_id=user.id).scalar() or 0

    # 거래 통계
    tx_count = Transaction.query.filter_by(user_id=user.id).count()
    sells    = Transaction.query.filter_by(user_id=user.id, type="sell").all()
    wins     = sum(1 for s in sells if (s.realized_pnl_krw or 0) > 0)
    win_rate = (wins / len(sells) * 100) if sells else 0

    return jsonify({
        "cash_krw":            round(user.cash_balance),
        "initial_capital_krw": round(user.initial_capital),
        "positions_value_krw": round(positions_value_krw),
        "total_assets_krw":    round(total_assets_krw),
        "total_return_pct":    round(total_return_pct, 2),
        "unrealized_pnl_krw":  round(unrealized_pnl_krw),
        "realized_pnl_krw":    round(realized_pnl_krw),
        "exchange_rate":       round(fx, 2),
        "positions":           positions,
        "stats": {
            "total_trades": tx_count,
            "total_sells":  len(sells),
            "wins":         wins,
            "win_rate":     round(win_rate, 1),
        },
    })


@app.route("/api/trading/buy", methods=["POST"])
@login_required
def trading_buy():
    """모의 매수.
    Body: { ticker, name?, price, quantity }
    """
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    name   = (data.get("name") or "").strip() or ticker
    price  = float(data.get("price") or 0)
    qty    = float(data.get("quantity") or 0)

    if not ticker or price <= 0 or qty <= 0:
        return jsonify({"error": "티커/가격/수량을 올바르게 입력해주세요"}), 400

    user = current_user
    if user.cash_balance is None:
        user.cash_balance = INITIAL_CAPITAL_KRW

    currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "USD"

    # 이름 보강 (STOCK_DB lookup)
    if name == ticker:
        for s in STOCK_DB:
            if s.get("symbol") == ticker:
                name = s.get("name") or name
                break

    fx = _get_usd_krw_rate()
    amount_native = price * qty
    amount_krw    = amount_native * (fx if currency == "USD" else 1)
    fee_krw       = round(amount_krw * 0.001)   # 수수료 0.1%
    total_krw     = amount_krw + fee_krw

    if user.cash_balance < total_krw:
        return jsonify({
            "error": f"현금 부족 (필요 {round(total_krw):,}원, 보유 {round(user.cash_balance):,}원)"
        }), 400

    # 현금 차감
    user.cash_balance -= total_krw

    # Holding 추가 또는 평균단가 업데이트
    existing = Holding.query.filter_by(user_id=user.id, ticker=ticker).first()
    if existing:
        new_qty = existing.quantity + qty
        avg     = (existing.purchase_price * existing.quantity + price * qty) / new_qty
        existing.quantity       = round(new_qty, 6)
        existing.purchase_price = round(avg, 6)
        existing.name           = name
        existing.currency       = currency
    else:
        h = Holding(
            user_id=user.id, ticker=ticker, name=name,
            quantity=qty, purchase_price=price, currency=currency,
        )
        db.session.add(h)

    # Transaction 기록
    tx = Transaction(
        user_id=user.id, ticker=ticker, name=name,
        type="buy", price=price, quantity=qty,
        currency=currency, exchange_rate=(fx if currency == "USD" else 1.0),
        fee_krw=fee_krw, amount_krw=round(amount_krw),
        realized_pnl_krw=0,
    )
    db.session.add(tx)
    db.session.commit()

    return jsonify({
        "ok": True,
        "transaction": tx.to_dict(),
        "cash_balance": round(user.cash_balance),
    })


@app.route("/api/trading/sell", methods=["POST"])
@login_required
def trading_sell():
    """모의 매도.
    Body: { ticker, price, quantity }
    """
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    price  = float(data.get("price") or 0)
    qty    = float(data.get("quantity") or 0)

    if not ticker or price <= 0 or qty <= 0:
        return jsonify({"error": "티커/가격/수량을 올바르게 입력해주세요"}), 400

    user = current_user
    if user.cash_balance is None:
        user.cash_balance = INITIAL_CAPITAL_KRW

    holding = Holding.query.filter_by(user_id=user.id, ticker=ticker).first()
    if not holding:
        return jsonify({"error": "보유하지 않은 종목입니다"}), 400
    if qty > holding.quantity:
        return jsonify({
            "error": f"보유 수량 초과 (보유 {holding.quantity}, 매도 시도 {qty})"
        }), 400

    currency = holding.currency or "USD"
    fx = _get_usd_krw_rate()

    # 매도 금액 KRW 환산
    amount_native = price * qty
    amount_krw    = amount_native * (fx if currency == "USD" else 1)
    fee_krw       = round(amount_krw * 0.001)
    proceeds_krw  = amount_krw - fee_krw

    # 실현 손익 = (매도가 - 매입가) * 수량 (KRW 환산)
    pnl_native = (price - holding.purchase_price) * qty
    pnl_krw    = pnl_native * (fx if currency == "USD" else 1)

    # 현금 증가
    user.cash_balance += proceeds_krw

    # Holding 수량 감소 또는 삭제
    if qty >= holding.quantity:
        db.session.delete(holding)
    else:
        holding.quantity = round(holding.quantity - qty, 6)

    # Transaction 기록
    tx = Transaction(
        user_id=user.id, ticker=ticker, name=holding.name if holding else ticker,
        type="sell", price=price, quantity=qty,
        currency=currency, exchange_rate=(fx if currency == "USD" else 1.0),
        fee_krw=fee_krw, amount_krw=round(amount_krw),
        realized_pnl_krw=round(pnl_krw),
    )
    db.session.add(tx)
    db.session.commit()

    return jsonify({
        "ok": True,
        "transaction": tx.to_dict(),
        "cash_balance": round(user.cash_balance),
        "realized_pnl_krw": round(pnl_krw),
    })


@app.route("/api/trading/transactions")
@login_required
def trading_transactions():
    """거래 내역 (최신순)."""
    limit = min(int(request.args.get("limit", 50)), 200)
    txs = Transaction.query.filter_by(user_id=current_user.id)\
            .order_by(Transaction.timestamp.desc()).limit(limit).all()
    return jsonify([t.to_dict() for t in txs])


@app.route("/api/trading/reset", methods=["POST"])
@login_required
def trading_reset():
    """모의투자 초기화: 현금 1억으로, 보유종목·거래내역 삭제."""
    user = current_user
    Holding.query.filter_by(user_id=user.id).delete()
    Transaction.query.filter_by(user_id=user.id).delete()
    user.cash_balance    = INITIAL_CAPITAL_KRW
    user.initial_capital = INITIAL_CAPITAL_KRW
    db.session.commit()
    return jsonify({"ok": True, "cash_balance": INITIAL_CAPITAL_KRW})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
