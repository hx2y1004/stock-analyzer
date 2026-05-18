import json
import os
import re
from dotenv import load_dotenv
load_dotenv()
import numpy as np
import pandas as pd
import requests
import yfinance as yf
try:
    from deep_translator import GoogleTranslator as _GTrans
    from concurrent.futures import ThreadPoolExecutor as _TPE
    def _translate_ko(text):
        if not text or not text.strip(): return text
        try:
            return _GTrans(source='auto', target='ko').translate(text[:500])
        except Exception:
            return text
    def _translate_batch(texts):
        with _TPE(max_workers=6) as ex:
            return list(ex.map(_translate_ko, texts))
except ImportError:
    def _translate_ko(text): return text
    def _translate_batch(texts): return texts
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user

from analysis.ai_analysis import analyze_signals
from analysis.indicators import add_all_indicators
from models import db, User, Holding
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

def fetch_kr_quarterly_estimates(krx_code):
    """네이버 금융 분기 컨센서스 API에서 매출액·영업이익 추정치를 수집.

    Returns:
        (rev_est, oi_est): 각각 {"YYYY-MM": value_in_krw} dict
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

    rev_est, oi_est = {}, {}

    # ── 방법 1: m.stock.naver.com 모바일 API ─────────────────────────
    try:
        url  = f"https://m.stock.naver.com/api/stock/{krx_code}/finance/quarterly"
        resp = requests.get(url, headers=_hdrs, timeout=8)
        if resp.status_code == 200:
            fi         = resp.json().get("financeInfo", {})
            title_list = fi.get("trTitleList", [])
            row_list   = fi.get("rowList", [])

            # isConsensus 가 "Y" / True / 1 인 컬럼만 추정치
            est_keys = set()
            for t in title_list:
                cv = t.get("isConsensus")
                if cv in ("Y", True, "true", "TRUE", 1):
                    est_keys.add(t["key"])

            app.logger.info(f"[KR est] {krx_code}: est_keys={list(est_keys)[:5]}, rows={[r.get('title') for r in row_list[:8]]}")

            for row in row_list:
                title = row.get("title", "")
                is_rev = title in ("매출액", "매출", "수익", "Revenue", "총매출")
                is_oi  = title in ("영업이익", "영업이익(손실)", "OperatingIncome")
                if not (is_rev or is_oi):
                    continue
                for key in est_keys:
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
                    # 네이버 단위: 억원 → KRW
                    if is_rev:
                        rev_est[period] = val * 1e8
                    else:
                        oi_est[period] = val * 1e8
    except Exception as e:
        app.logger.error(f"[KR est] mobile API error: {e}")

    # ── 방법 2: PC 웹 JSON API (폴백) ────────────────────────────────
    if not rev_est and not oi_est:
        try:
            url2 = (
                f"https://finance.naver.com/item/coinfo.naver"
                f"?code={krx_code}&target=finsum_quarterly"
            )
            r2 = requests.get(url2, headers={**_hdrs, "Referer": "https://finance.naver.com/"}, timeout=8)
            # 간단 파싱: 쿼터별 매출·영업이익 행 찾기
            # (실패해도 무방 — 추정치 없이 발표치만 보여줌)
        except Exception:
            pass

    return rev_est, oi_est


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

    with _TPE(max_workers=4) as ex:
        details = list(ex.map(_fetch_detail, items))

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

        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "당신은 한국어로 답변하는 주식 시장 전문 애널리스트입니다."},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 400,
                },
                timeout=15,
            )
            data = resp.json()
            if "choices" in data:
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    return text
            else:
                app.logger.warning(f"Groq no choices: {data}")
        except Exception as e:
            app.logger.error(f"Groq API exception: {e}")

    # 폴백
    if not news_items:
        return f"현재 {kind} 관련 정보를 찾을 수 없습니다. 거시경제 지표나 시장 전반의 흐름을 함께 확인해 보세요."
    top = [n['title'] for n in news_items[:3]]
    return "관련 뉴스: " + " / ".join(top)


def fetch_company_overview(ticker, name, info, revenue_quarters, currency):
    """Groq으로 기업 소개·주요 사업·분석 인사이트를 한국어로 생성."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
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

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "당신은 한국어로 답변하는 주식 전문 애널리스트입니다."},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 800,
            },
            timeout=20,
        )
        data = resp.json()
        if "choices" in data:
            text = data["choices"][0]["message"]["content"].strip()
            if text:
                # 파싱: [기업소개], [주요사업], [기업분석] 섹션 분리
                import re as _re
                sections = {}
                for key in ["기업소개", "주요사업", "기업분석"]:
                    m = _re.search(rf'\[{key}\]\s*(.*?)(?=\[(?:기업소개|주요사업|기업분석)\]|$)', text, _re.DOTALL)
                    if m:
                        sections[key] = m.group(1).strip()
                if sections:
                    return sections
                return {"기업소개": text}
    except Exception as e:
        app.logger.error(f"fetch_company_overview error: {e}")
    return None


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

            for period, actual in sorted(rev_by_period.items(), reverse=True):
                revenue_quarters.append({
                    "period":   period,
                    "actual":   actual,
                    "estimate": _match_estimate(period, rev_est_by_period),
                })

            # 5. EPS 리스트 (추정치·surprise 매칭, 최신순)
            for period, actual in sorted(eps_act_by_period.items(), reverse=True):
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

                # 네이버 분기 컨센서스 → 매출·영업이익 추정치 동시 수집
                kr_rev_est, kr_oi_est = fetch_kr_quarterly_estimates(krx_code)
                app.logger.info(f"[KR est] {krx_code}: rev_est={kr_rev_est}, oi_est={kr_oi_est}")

                # 매출 추정치 보완: earnings_dates 값 없으면 네이버 값 사용
                if kr_rev_est:
                    for q in revenue_quarters:
                        if q["estimate"] is None:
                            q["estimate"] = _match_estimate(q["period"], kr_rev_est)

                # 발표치: quarterly_income_stmt["Operating Income"]
                if qf is not None and not qf.empty:
                    oi_row = None
                    for key in ["Operating Income", "OperatingIncome", "EBIT"]:
                        if key in qf.index:
                            oi_row = qf.loc[key]
                            break
                    if oi_row is not None:
                        oi_actuals = oi_row.dropna().sort_index(ascending=False)
                        for i, (idx, val) in enumerate(oi_actuals.items()):
                            if i >= 5:
                                break
                            period   = str(idx)[:7]
                            actual   = safe_float(val)
                            estimate = _match_estimate(period, kr_oi_est)
                            oi_quarters.append({
                                "period":   period,
                                "actual":   actual,
                                "estimate": estimate,
                            })
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
            "business_summary": info.get("longBusinessSummary", ""),
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
        # 한국 주식(.KS/.KQ)이면 네이버 금융, 아니면 yfinance 애널리스트 데이터
        is_kr_stock  = ticker.endswith('.KS') or ticker.endswith('.KQ')
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
                position_data = {
                    **holding.to_dict(),
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
    name     = data.get("name", ticker)
    qty      = float(data.get("quantity", 0))
    price    = float(data.get("purchase_price", 0))
    currency = data.get("currency", "USD")

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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
