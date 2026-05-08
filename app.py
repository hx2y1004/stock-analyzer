import json
import os
import re
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from analysis.ai_analysis import analyze_signals
from analysis.indicators import add_all_indicators

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")
CORS(app)


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

        return result
    except Exception:
        return {}


@app.route("/")
def index():
    return render_template("index.html")


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
        results = []
        for item in news_list[:10]:
            c = item.get("content", {})
            if not c:
                continue
            title = c.get("title", "")
            desc = c.get("description") or c.get("summary", "")
            desc = re.sub(r"<[^>]+>", "", desc or "")[:200]
            pub = c.get("pubDate", "") or c.get("displayTime", "")
            url = ""
            cu = c.get("canonicalUrl") or c.get("clickThroughUrl") or {}
            if isinstance(cu, dict):
                url = cu.get("url", "")
            provider = ""
            pv = c.get("provider", {})
            if isinstance(pv, dict):
                provider = pv.get("displayName", "")
            if title:
                results.append({
                    "title": title,
                    "desc": desc,
                    "pub": pub[:10],
                    "url": url,
                    "provider": provider,
                })
        return results
    except Exception:
        return []


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

            # 배당수익률: yfinance가 한국 주식에 대해 이미 %로 반환 (0.55 = 0.55%)
            # 프론트에서 ×100 하기 때문에 0.0055로 정규화
            dy = info.get("dividendYield")
            if dy and dy > 0.15:   # 15% 초과 = % 형태로 온 것
                info["dividendYield"] = dy / 100

        # NaN 행 제거 (한국 주식 등 마지막 행이 NaN일 수 있음)
        df = df.dropna(subset=["Close", "Open", "High", "Low"])
        if df.empty:
            return jsonify({"error": f"'{ticker}' 유효한 가격 데이터가 없습니다."}), 404

        df = add_all_indicators(df)
        ai_result = analyze_signals(df, info)

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
            "pb_ratio": safe_float(info.get("priceToBook")),
            "eps": safe_float(info.get("trailingEps")),
            "dividend_yield": safe_float(info.get("dividendYield")),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "currency": info.get("currency", "USD"),
            "exchange": info.get("exchange"),
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

        news_data = fetch_news(stock)
        analyst_data = fetch_analysts(stock, info)

        return app.response_class(
            response=json.dumps(
                {"stock": stock_data, "analysis": ai_result, "chart": chart_data,
                 "interval": interval, "interval_label": interval_label,
                 "news": news_data, "analysts": analyst_data},
                cls=NumpyEncoder,
                ensure_ascii=False,
            ),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
