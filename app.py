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
import toss_api
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
from models import db, User, Holding, PaperHolding, Transaction, AssetSnapshot, UserBadge, INITIAL_CAPITAL_KRW
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
    # ⚠️ create_all() 이 paper_holdings 를 만들기 전에, 테이블이 원래 있었는지 먼저 확인.
    # (없었다 = 최초 분리 → 기존 holdings 를 모의투자로 1회 이전해야 함)
    _paper_holdings_existed = True
    try:
        from sqlalchemy import text as _text0
        with db.engine.begin() as _c0:
            _paper_holdings_existed = bool(
                _c0.execute(_text0("SELECT to_regclass('public.paper_holdings')")).scalar()
            )
    except Exception:
        _paper_holdings_existed = True   # 알 수 없으면 이전 안 함 (안전)

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
            # asset_snapshots 테이블 (자산 변화 차트용)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS asset_snapshots (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    total_assets_krw DOUBLE PRECISION NOT NULL,
                    cash_krw DOUBLE PRECISION NOT NULL,
                    positions_value_krw DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_user_snapshot_date UNIQUE (user_id, date)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_asset_snapshots_user_date "
                "ON asset_snapshots (user_id, date)"
            ))
            # 랭킹/프로필: nickname + is_public
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(30) UNIQUE"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT TRUE"
            ))
            # user_badges 테이블
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_badges (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    badge_key VARCHAR(50) NOT NULL,
                    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_user_badge_key UNIQUE (user_id, badge_key)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_user_badges_user "
                "ON user_badges (user_id)"
            ))
            # ── paper_holdings: 모의투자 보유종목 (실제 포트폴리오 holdings와 분리) ──
            # 테이블(create_all 이 이미 생성)이 '원래 없었을 때만' 기존 holdings 를 1회 이전.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_paper_holdings_user "
                "ON paper_holdings (user_id)"
            ))
            if not _paper_holdings_existed:
                # 최초 분리: 기존 holdings(모의+실제 혼재)를 모의투자로 이전,
                # 실제 포트폴리오(holdings)는 빈 상태로 시작.
                conn.execute(text("""
                    INSERT INTO paper_holdings
                        (user_id, ticker, name, quantity, purchase_price, currency, created_at)
                    SELECT user_id, ticker, name, quantity, purchase_price, currency, created_at
                    FROM holdings
                """))
                conn.execute(text("DELETE FROM holdings"))
                print("[migration] holdings → paper_holdings 이전 완료 (실제 포트폴리오 초기화)")
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


# 네이버 실시간 시세 캐시 (남용 방지 — 30초)
_NAVER_RT_CACHE = {}
_NAVER_RT_TTL = 30  # seconds


def fetch_naver_realtime_price(krx_code: str):
    """네이버 금융 폴링 API → 한국 주식 실시간 시세 (~1분 지연).

    yfinance(15~20분 지연)보다 훨씬 빠름. 장중 한국 종목에 우선 사용.

    Returns:
        dict {current_price, previous_close, market_status, source} 또는 None
    """
    if not krx_code:
        return None

    # 30초 캐시
    now_ts = time.time()
    cached = _NAVER_RT_CACHE.get(krx_code)
    if cached and (now_ts - cached[0]) < _NAVER_RT_TTL:
        return cached[1]

    try:
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{krx_code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://finance.naver.com/item/main.naver?code={krx_code}",
            "Accept": "application/json, text/plain, */*",
        }
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        areas = (data.get("result") or {}).get("areas") or []
        if not areas:
            return None
        datas = areas[0].get("datas") or []
        if not datas:
            return None
        d = datas[0]
        current = safe_float(d.get("nv"))    # 현재가
        prev    = safe_float(d.get("pcv"))   # 전일 종가
        if not current:
            return None
        result = {
            "current_price":  current,
            "previous_close": prev,
            "market_status":  d.get("ms"),   # "OPEN" / "CLOSE"
            "source":         "naver",
        }
        _NAVER_RT_CACHE[krx_code] = (now_ts, result)
        return result
    except Exception as e:
        try:
            app.logger.warning(f"[naver-realtime] {krx_code}: {e}")
        except Exception:
            pass
        return None


# ── 한국 종목 보충 데이터: 공매도 / 외국인·기관 수급 ─────────────
_KR_SUPPLEMENT_CACHE = {}
_KR_SUPPLEMENT_TTL = 1800  # 30분 (일별 데이터라 자주 변경 안 됨)


def _naver_request(url):
    """네이버 모바일/PC 페이지 GET — 인코딩 처리 포함."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
    }
    r = requests.get(url, headers=headers, timeout=8)
    if r.status_code != 200:
        return None
    # 인코딩 자동 감지
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return r.content.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return r.content.decode("euc-kr", errors="replace")


def fetch_kr_short_interest(krx_code: str):
    """네이버 일별 공매도 → 최근일 공매도 비율 + 5일 평균.

    URL: https://finance.naver.com/item/dailyShort.naver?code=005930
    컬럼: 날짜 | 공매도거래량 | 거래량 | 공매도비중(%) | 공매도잔고(주) | 공매도잔고비중(%)

    Returns: dict {date, short_ratio_pct, short_balance_pct, short_5d_avg_pct} 또는 None
    """
    if not krx_code:
        return None
    try:
        from bs4 import BeautifulSoup
        url = f"https://finance.naver.com/item/dailyShort.naver?code={krx_code}"
        html = _naver_request(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        def _num(s):
            if not s: return None
            t = s.strip().replace(",", "").replace("+", "").replace("%", "")
            if not t or t == "-": return None
            try: return float(t)
            except ValueError: return None

        # 데이터 테이블: rows 안에 첫번째 셀이 날짜 형식인 것
        rows = []
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            date_txt = tds[0].get_text(strip=True)
            if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_txt):
                continue
            short_qty   = _num(tds[1].get_text(strip=True))
            total_qty   = _num(tds[2].get_text(strip=True))
            short_ratio = _num(tds[3].get_text(strip=True))
            bal_qty     = _num(tds[4].get_text(strip=True)) if len(tds) > 4 else None
            bal_pct     = _num(tds[5].get_text(strip=True)) if len(tds) > 5 else None
            rows.append({
                "date": date_txt,
                "short_ratio": short_ratio,
                "balance_pct": bal_pct,
            })
            if len(rows) >= 10:
                break

        if not rows:
            return None

        latest = rows[0]
        five = [r["short_ratio"] for r in rows[:5] if r["short_ratio"] is not None]
        five_avg = round(sum(five) / len(five), 2) if five else None

        return {
            "date":              latest["date"],
            "short_ratio_pct":   latest["short_ratio"],
            "short_balance_pct": latest["balance_pct"],
            "short_5d_avg_pct":  five_avg,
        }
    except Exception as e:
        try: app.logger.warning(f"[kr-short] {krx_code}: {e}")
        except Exception: pass
        return None


def fetch_kr_supply_demand(krx_code: str):
    """네이버 외국인/기관 → 외국인 보유율 + 최근 5일 순매수.

    URL: https://finance.naver.com/item/frgn.naver?code=005930
    컬럼: 날짜 | 종가 | 전일비 | 등락률 | 거래량 | 기관순매매 | 외국인순매매 | 보유주식수 | 보유율

    Returns: dict {foreign_ratio_pct, foreign_net_5d, inst_net_5d, latest_date}
    """
    if not krx_code:
        return None
    try:
        from bs4 import BeautifulSoup
        url = f"https://finance.naver.com/item/frgn.naver?code={krx_code}"
        html = _naver_request(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        def _num(s):
            if not s: return None
            t = s.strip().replace(",", "").replace("+", "").replace("%", "")
            if not t or t == "-": return None
            try: return float(t)
            except ValueError: return None

        rows = []
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 9:
                continue
            date_txt = tds[0].get_text(strip=True)
            if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_txt):
                continue
            # 부호 보정: 빨강(상승=매수) 또는 파랑(하락=매도)
            def _signed(td):
                v = _num(td.get_text(strip=True))
                if v is None:
                    return None
                cls = " ".join(td.get("class", []))
                if "nv01" in cls or "down" in cls:
                    return -v
                return v
            inst_net    = _signed(tds[5])
            foreign_net = _signed(tds[6])
            foreign_pct = _num(tds[8].get_text(strip=True))
            rows.append({
                "date": date_txt,
                "inst_net": inst_net,
                "foreign_net": foreign_net,
                "foreign_pct": foreign_pct,
            })
            if len(rows) >= 5:
                break

        if not rows:
            return None

        inst_5d    = sum((r["inst_net"]    or 0) for r in rows[:5])
        foreign_5d = sum((r["foreign_net"] or 0) for r in rows[:5])

        return {
            "latest_date":      rows[0]["date"],
            "foreign_ratio_pct": rows[0]["foreign_pct"],
            "foreign_net_5d":    int(foreign_5d) if foreign_5d else 0,
            "inst_net_5d":       int(inst_5d) if inst_5d else 0,
        }
    except Exception as e:
        try: app.logger.warning(f"[kr-supply] {krx_code}: {e}")
        except Exception: pass
        return None


def fetch_kr_supplements(krx_code: str):
    """한국 종목 보충 데이터(공매도/수급) 통합 — 30분 캐시."""
    if not krx_code:
        return None
    now_ts = time.time()
    cached = _KR_SUPPLEMENT_CACHE.get(krx_code)
    if cached and (now_ts - cached[0]) < _KR_SUPPLEMENT_TTL:
        return cached[1]
    result = {
        "short":  fetch_kr_short_interest(krx_code),
        "supply": fetch_kr_supply_demand(krx_code),
    }
    _KR_SUPPLEMENT_CACHE[krx_code] = (now_ts, result)
    return result


# ── 섹터/테마 강도 (Sector Strength) ───────────────────────────
US_SECTOR_ETFS = [
    ("XLK",  "기술 (Technology)"),
    ("XLC",  "통신 서비스"),
    ("XLY",  "임의 소비재"),
    ("XLP",  "필수 소비재"),
    ("XLF",  "금융"),
    ("XLV",  "헬스케어"),
    ("XLI",  "산업재"),
    ("XLE",  "에너지"),
    ("XLB",  "소재"),
    ("XLU",  "유틸리티"),
    ("XLRE", "부동산"),
]

_SECTOR_CACHE = {}
_SECTOR_TTL = 300  # 5분


def _fetch_us_sector_strength():
    """미국 11개 SPDR 섹터 ETF 강도 점수."""
    import math
    now_ts = time.time()
    cached = _SECTOR_CACHE.get("US")
    if cached and (now_ts - cached[0]) < _SECTOR_TTL:
        return cached[1]

    results = []
    try:
        tickers = [t for t, _ in US_SECTOR_ETFS]
        data = yf.download(
            " ".join(tickers),
            period="3mo", interval="1d",
            group_by="ticker", progress=False, threads=True, auto_adjust=False,
        )
    except Exception as e:
        app.logger.warning(f"[sector US] download failed: {e}")
        return []

    for ticker, name in US_SECTOR_ETFS:
        try:
            try:
                df = data[ticker]
            except (KeyError, TypeError):
                continue
            df = df.dropna(subset=["Close"])
            if df.empty or len(df) < 22:
                continue
            close = df["Close"]; vol = df["Volume"]
            cur = float(close.iloc[-1])
            chg_1d = (cur / float(close.iloc[-2])  - 1) * 100 if len(close) >= 2  else 0.0
            chg_1w = (cur / float(close.iloc[-6])  - 1) * 100 if len(close) >= 6  else 0.0
            chg_1m = (cur / float(close.iloc[-22]) - 1) * 100 if len(close) >= 22 else 0.0
            avg_vol = float(vol.tail(20).mean()) or 1.0
            vol_ratio = float(vol.iloc[-1]) / avg_vol if avg_vol else 1.0
            # 강도 점수: 모멘텀 가중 + 거래량 로그 보정
            score = (0.3 * chg_1d + 0.4 * chg_1w + 0.3 * chg_1m
                     + math.log(max(vol_ratio, 0.1)) * 3.0)
            results.append({
                "ticker":    ticker,
                "name":      name,
                "price":     round(cur, 2),
                "change_1d": round(chg_1d, 2),
                "change_1w": round(chg_1w, 2),
                "change_1m": round(chg_1m, 2),
                "vol_ratio": round(vol_ratio, 2),
                "score":     round(score, 2),
            })
        except Exception as e:
            app.logger.warning(f"[sector US] {ticker}: {e}")
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    _SECTOR_CACHE["US"] = (now_ts, results)
    return results


def _fetch_kr_theme_strength():
    """네이버 금융 테마 페이지(6페이지) 스크래핑 → 한국 테마 강도."""
    from bs4 import BeautifulSoup
    now_ts = time.time()
    cached = _SECTOR_CACHE.get("KR")
    if cached and (now_ts - cached[0]) < _SECTOR_TTL:
        return cached[1]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/sise/theme.naver",
    }
    results = []
    seen_names = set()

    def _parse_num(text):
        if not text: return None
        t = text.strip().replace(",", "").replace("+", "").replace("%", "")
        if not t or t == "-": return None
        try: return float(t)
        except ValueError: return None

    for page in range(1, 7):  # ~200개
        try:
            url = f"https://finance.naver.com/sise/theme.naver?&page={page}"
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            # 네이버는 EUC-KR
            try:
                html = r.content.decode("euc-kr", errors="replace")
            except Exception:
                html = r.text
            soup = BeautifulSoup(html, "html.parser")
            # 테마 테이블 — class type_1 theme
            table = soup.find("table", class_=lambda c: c and "type_1" in c)
            if not table:
                continue
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                name_a = tds[0].find("a")
                if not name_a:
                    continue
                theme_name = name_a.get_text(strip=True)
                if not theme_name or theme_name in seen_names:
                    continue
                href = name_a.get("href", "")
                theme_url = "https://finance.naver.com" + href if href.startswith("/") else href
                # 테마 번호 추출 (?no=123)
                m_no = re.search(r"[?&]no=(\d+)", href or "")
                theme_no = m_no.group(1) if m_no else None
                # 컬럼: [0]테마명 [1]전일대비 [2]최근3일 [3]전일대비등락그래프?
                # 실제 네이버 구조: [0]테마명 [1]전일대비 [2]최근3일등락률 [3]전일대비등락그래프
                #                  [4]상승 [5]보합 [6]하락 [7]주도주
                chg_1d = _parse_num(tds[1].get_text(strip=True))
                chg_3d = _parse_num(tds[2].get_text(strip=True))
                # 일부 페이지에서 컬럼 위치가 다를 수 있어 안전하게
                up = flat = down = None
                # 마지막 직전 세 컬럼이 상승/보합/하락
                if len(tds) >= 7:
                    up   = _parse_num(tds[-4].get_text(strip=True)) if len(tds) >= 8 else _parse_num(tds[4].get_text(strip=True))
                    flat = _parse_num(tds[-3].get_text(strip=True)) if len(tds) >= 8 else _parse_num(tds[5].get_text(strip=True))
                    down = _parse_num(tds[-2].get_text(strip=True)) if len(tds) >= 8 else _parse_num(tds[6].get_text(strip=True))
                if chg_1d is None:
                    continue
                if chg_3d is None: chg_3d = chg_1d
                up   = int(up)   if up   is not None else 0
                flat = int(flat) if flat is not None else 0
                down = int(down) if down is not None else 0
                total = max(up + flat + down, 1)
                breadth = (up - down) / total * 100  # -100 ~ +100
                # 강도 점수: 모멘텀 가중 + 폭(breadth) 보정
                score = 0.45 * chg_1d + 0.35 * chg_3d + 0.20 * (breadth * 0.5)
                # 주도주(있으면)
                leaders = []
                if len(tds) >= 8:
                    leader_cell = tds[-1]
                    for a in leader_cell.find_all("a")[:3]:
                        nm = a.get_text(strip=True)
                        if nm:
                            leaders.append(nm)
                seen_names.add(theme_name)
                results.append({
                    "name":      theme_name,
                    "url":       theme_url,
                    "theme_no":  theme_no,
                    "change_1d": round(chg_1d, 2),
                    "change_3d": round(chg_3d, 2),
                    "up":        up,
                    "flat":      flat,
                    "down":      down,
                    "breadth":   round(breadth, 1),
                    "leaders":   leaders,
                    "score":     round(score, 2),
                })
        except Exception as e:
            app.logger.warning(f"[sector KR] page {page}: {e}")
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    _SECTOR_CACHE["KR"] = (now_ts, results)
    return results


@app.route("/api/sectors/strength", methods=["GET"])
def sectors_strength():
    """섹터(미국 ETF) / 테마(한국 네이버) 강도 점수 랭킹."""
    market = (request.args.get("market") or "US").upper()
    force  = request.args.get("force") == "1"
    if force:
        _SECTOR_CACHE.pop(market, None)
    if market == "KR":
        data = _fetch_kr_theme_strength()
    else:
        data = _fetch_us_sector_strength()
        market = "US"
    return jsonify({
        "market": market,
        "sectors": data,
        "count": len(data),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    })


# ── 섹터 구성종목 (대표 종목 상승률 순) ───────────────────────
# SPDR 11개 섹터 ETF의 대표 종목 ~15개 (구성 비중 상위, 2025 기준)
US_SECTOR_HOLDINGS = {
    "XLK":  ["NVDA","MSFT","AAPL","AVGO","ORCL","CRM","CSCO","AMD","ACN","ADBE","IBM","TXN","NOW","INTU","QCOM","PLTR","ADI","AMAT","MU","LRCX"],
    "XLC":  ["META","GOOGL","GOOG","NFLX","TMUS","DIS","CMCSA","VZ","T","CHTR","WBD","EA","TTWO","FOX","FOXA","OMC","IPG"],
    "XLY":  ["AMZN","TSLA","HD","MCD","BKNG","LOW","TJX","NKE","SBUX","CMG","ABNB","ORLY","AZO","MAR","GM","F","DRI","HLT","ROST","LULU"],
    "XLP":  ["COST","WMT","PG","KO","PEP","PM","MO","MDLZ","CL","TGT","KMB","GIS","KHC","SYY","STZ","HSY","KR","ADM","DG","EL"],
    "XLF":  ["BRK-B","JPM","V","MA","BAC","WFC","GS","MS","AXP","BLK","C","SPGI","PGR","CB","MMC","SCHW","BX","FI","CME","ICE"],
    "XLV":  ["LLY","UNH","JNJ","ABBV","MRK","TMO","ABT","PFE","DHR","ISRG","AMGN","BMY","SYK","GILD","MDT","CI","BSX","ELV","VRTX","REGN"],
    "XLI":  ["GE","RTX","CAT","UBER","HON","UNP","BA","ETN","LMT","DE","ADP","UPS","NOC","WM","GD","ITW","CSX","EMR","MMM","FDX"],
    "XLE":  ["XOM","CVX","COP","WMB","EOG","SLB","KMI","PSX","MPC","OXY","VLO","HES","FANG","OKE","BKR","TRGP","HAL","DVN","EQT","APA"],
    "XLB":  ["LIN","SHW","FCX","ECL","APD","DD","NUE","NEM","CTVA","DOW","PPG","VMC","MLM","IFF","STLD","CF","ALB","LYB","PKG","IP"],
    "XLU":  ["NEE","SO","DUK","CEG","SRE","AEP","D","PCG","XEL","EXC","ED","WEC","ETR","ES","DTE","PEG","EIX","AWK","FE","AEE"],
    "XLRE": ["PLD","AMT","EQIX","WELL","SPG","CCI","DLR","O","PSA","CBRE","EXR","VICI","AVB","SBAC","CSGP","WY","ARE","EQR","IRM","INVH"],
}

_CONSTITUENTS_CACHE = {}
_CONSTITUENTS_TTL = 300  # 5분

# 미국 주요 ETF/대형주 영문명 매핑 (STOCK_DB에 없는 것 보강)
_US_NAME_FALLBACK = {
    "BRK-B": "Berkshire Hathaway", "GOOGL": "Alphabet Class A", "GOOG": "Alphabet Class C",
    "META": "Meta Platforms", "BKNG": "Booking Holdings", "TMUS": "T-Mobile US",
    "CMCSA": "Comcast", "WBD": "Warner Bros. Discovery", "EA": "Electronic Arts",
    "TTWO": "Take-Two Interactive", "OMC": "Omnicom Group", "IPG": "Interpublic Group",
    "ORLY": "O'Reilly Automotive", "AZO": "AutoZone", "MAR": "Marriott International",
    "HLT": "Hilton Worldwide", "DRI": "Darden Restaurants", "TJX": "TJX Companies",
    "ROST": "Ross Stores", "LULU": "Lululemon", "STZ": "Constellation Brands",
    "MDLZ": "Mondelez", "KMB": "Kimberly-Clark", "GIS": "General Mills",
    "KHC": "Kraft Heinz", "SYY": "Sysco", "EL": "Estee Lauder", "DG": "Dollar General",
    "SPGI": "S&P Global", "PGR": "Progressive", "BLK": "BlackRock", "BX": "Blackstone",
    "FI": "Fiserv", "CME": "CME Group", "ICE": "Intercontinental Exchange",
    "MS": "Morgan Stanley", "GS": "Goldman Sachs", "AXP": "American Express",
    "LLY": "Eli Lilly", "UNH": "UnitedHealth Group", "JNJ": "Johnson & Johnson",
    "ABBV": "AbbVie", "TMO": "Thermo Fisher", "ABT": "Abbott Labs", "DHR": "Danaher",
    "ISRG": "Intuitive Surgical", "AMGN": "Amgen", "BMY": "Bristol-Myers Squibb",
    "SYK": "Stryker", "GILD": "Gilead Sciences", "MDT": "Medtronic", "CI": "Cigna",
    "BSX": "Boston Scientific", "ELV": "Elevance Health", "VRTX": "Vertex Pharma",
    "REGN": "Regeneron", "MRK": "Merck", "PFE": "Pfizer",
    "RTX": "RTX Corp", "HON": "Honeywell", "UNP": "Union Pacific", "ETN": "Eaton",
    "LMT": "Lockheed Martin", "ADP": "Automatic Data Processing", "UPS": "UPS",
    "NOC": "Northrop Grumman", "WM": "Waste Management", "GD": "General Dynamics",
    "ITW": "Illinois Tool Works", "CSX": "CSX Corp", "EMR": "Emerson Electric",
    "FDX": "FedEx",
    "WMB": "Williams Companies", "EOG": "EOG Resources", "SLB": "Schlumberger",
    "KMI": "Kinder Morgan", "PSX": "Phillips 66", "MPC": "Marathon Petroleum",
    "OXY": "Occidental Petroleum", "VLO": "Valero Energy", "FANG": "Diamondback Energy",
    "OKE": "Oneok", "BKR": "Baker Hughes", "TRGP": "Targa Resources",
    "HAL": "Halliburton", "DVN": "Devon Energy", "EQT": "EQT Corp", "APA": "APA Corp",
    "LIN": "Linde", "SHW": "Sherwin-Williams", "FCX": "Freeport-McMoRan", "ECL": "Ecolab",
    "APD": "Air Products", "DD": "DuPont", "NUE": "Nucor", "NEM": "Newmont",
    "CTVA": "Corteva", "DOW": "Dow Inc", "PPG": "PPG Industries", "VMC": "Vulcan Materials",
    "MLM": "Martin Marietta", "IFF": "Intl Flavors & Fragrances", "STLD": "Steel Dynamics",
    "CF": "CF Industries", "ALB": "Albemarle", "LYB": "LyondellBasell",
    "PKG": "Packaging Corp", "IP": "International Paper",
    "NEE": "NextEra Energy", "SO": "Southern Company", "DUK": "Duke Energy",
    "CEG": "Constellation Energy", "SRE": "Sempra", "AEP": "American Electric Power",
    "D": "Dominion Energy", "PCG": "PG&E", "XEL": "Xcel Energy", "EXC": "Exelon",
    "ED": "Consolidated Edison", "WEC": "WEC Energy", "ETR": "Entergy", "ES": "Eversource",
    "DTE": "DTE Energy", "PEG": "Public Service Enterprise", "EIX": "Edison Intl",
    "AWK": "American Water Works", "FE": "FirstEnergy", "AEE": "Ameren",
    "PLD": "Prologis", "AMT": "American Tower", "EQIX": "Equinix", "WELL": "Welltower",
    "SPG": "Simon Property Group", "CCI": "Crown Castle", "DLR": "Digital Realty",
    "O": "Realty Income", "PSA": "Public Storage", "CBRE": "CBRE Group",
    "EXR": "Extra Space Storage", "VICI": "VICI Properties", "AVB": "AvalonBay",
    "SBAC": "SBA Communications", "CSGP": "CoStar Group", "WY": "Weyerhaeuser",
    "ARE": "Alexandria Real Estate", "EQR": "Equity Residential", "IRM": "Iron Mountain",
    "INVH": "Invitation Homes",
    "NVDA": "NVIDIA", "MSFT": "Microsoft", "AAPL": "Apple", "AVGO": "Broadcom",
    "ORCL": "Oracle", "CRM": "Salesforce", "CSCO": "Cisco", "AMD": "AMD",
    "ACN": "Accenture", "ADBE": "Adobe", "IBM": "IBM", "TXN": "Texas Instruments",
    "NOW": "ServiceNow", "INTU": "Intuit", "QCOM": "Qualcomm", "PLTR": "Palantir",
    "ADI": "Analog Devices", "AMAT": "Applied Materials", "MU": "Micron", "LRCX": "Lam Research",
    "NFLX": "Netflix", "DIS": "Disney", "VZ": "Verizon", "T": "AT&T", "CHTR": "Charter Comm.",
    "FOX": "Fox Corp", "FOXA": "Fox Corp A",
    "AMZN": "Amazon", "TSLA": "Tesla", "HD": "Home Depot", "MCD": "McDonald's",
    "LOW": "Lowe's", "NKE": "Nike", "SBUX": "Starbucks", "CMG": "Chipotle",
    "ABNB": "Airbnb", "GM": "General Motors", "F": "Ford",
    "COST": "Costco", "WMT": "Walmart", "PG": "Procter & Gamble", "KO": "Coca-Cola",
    "PEP": "PepsiCo", "PM": "Philip Morris", "MO": "Altria", "CL": "Colgate-Palmolive",
    "TGT": "Target", "HSY": "Hershey", "KR": "Kroger", "ADM": "Archer-Daniels-Midland",
    "JPM": "JPMorgan Chase", "V": "Visa", "MA": "Mastercard", "BAC": "Bank of America",
    "WFC": "Wells Fargo", "C": "Citigroup", "CB": "Chubb", "MMC": "Marsh McLennan",
    "SCHW": "Charles Schwab",
    "GE": "General Electric", "CAT": "Caterpillar", "UBER": "Uber", "BA": "Boeing",
    "DE": "Deere", "MMM": "3M",
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips", "HES": "Hess Corp",
}


def _lookup_us_name(ticker: str) -> str:
    """STOCK_DB → 폴백 매핑 → 티커 자체 순으로 종목명 반환."""
    if not ticker:
        return ""
    # 1) STOCK_DB
    try:
        for s in STOCK_DB:
            if s.get("symbol", "").upper() == ticker.upper():
                return s.get("name") or ticker
    except Exception:
        pass
    # 2) 폴백 매핑
    return _US_NAME_FALLBACK.get(ticker.upper(), ticker)


def _fetch_us_sector_constituents(etf_ticker: str, limit: int = 15):
    """미국 섹터 ETF의 대표 종목들 + 당일 변화율 (상승률 순)."""
    holdings = US_SECTOR_HOLDINGS.get(etf_ticker.upper())
    if not holdings:
        return []

    cache_key = f"US:{etf_ticker.upper()}"
    now_ts = time.time()
    cached = _CONSTITUENTS_CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _CONSTITUENTS_TTL:
        return cached[1]

    results = []
    try:
        data = yf.download(
            " ".join(holdings), period="5d", interval="1d",
            group_by="ticker", progress=False, threads=True, auto_adjust=False,
        )
    except Exception as e:
        app.logger.warning(f"[constituents US] download failed: {e}")
        return []

    for tk in holdings:
        try:
            try:
                df = data[tk]
            except (KeyError, TypeError):
                continue
            df = df.dropna(subset=["Close"])
            if df.empty or len(df) < 2:
                continue
            cur  = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            chg  = (cur / prev - 1) * 100 if prev else 0.0
            results.append({
                "ticker":    tk,
                "name":      _lookup_us_name(tk),
                "price":     round(cur, 2),
                "change_1d": round(chg, 2),
                "currency":  "USD",
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["change_1d"], reverse=True)
    results = results[:limit]
    _CONSTITUENTS_CACHE[cache_key] = (now_ts, results)
    return results


def _fetch_kr_theme_constituents(theme_no: str, limit: int = 20):
    """네이버 테마 상세 페이지 → 구성 종목 + 당일 등락률 (상승률 순)."""
    if not theme_no:
        return []
    cache_key = f"KR:{theme_no}"
    now_ts = time.time()
    cached = _CONSTITUENTS_CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _CONSTITUENTS_TTL:
        return cached[1]

    from bs4 import BeautifulSoup
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/sise/theme.naver",
    }
    url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_no}"

    def _parse_num(text):
        if not text: return None
        t = text.strip().replace(",", "").replace("+", "").replace("%", "")
        if not t or t in ("-", ""): return None
        try: return float(t)
        except ValueError: return None

    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            app.logger.warning(f"[constituents KR] {theme_no}: HTTP {r.status_code}")
            return []
        # 인코딩: 자동 감지 → euc-kr 폴백
        html = None
        for enc in ("euc-kr", "cp949", "utf-8"):
            try:
                html = r.content.decode(enc, errors="strict")
                break
            except UnicodeDecodeError:
                continue
        if html is None:
            html = r.content.decode("euc-kr", errors="replace")

        soup = BeautifulSoup(html, "html.parser")

        # 종목 행을 가진 모든 테이블 후보 수집 (code= 링크가 3개 이상 있는 테이블)
        candidate_table = None
        max_rows = 0
        for tbl in soup.find_all("table"):
            stock_links = tbl.find_all("a", href=lambda h: h and "code=" in h)
            if len(stock_links) > max_rows:
                candidate_table = tbl
                max_rows = len(stock_links)
        if not candidate_table or max_rows < 1:
            app.logger.warning(f"[constituents KR] {theme_no}: no stock table found")
            return []

        results = []
        seen_codes = set()

        for tr in candidate_table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            name_a = tds[0].find("a", href=lambda h: h and "code=" in h)
            if not name_a:
                continue
            name = name_a.get_text(strip=True)
            if not name:
                continue
            href = name_a.get("href", "")
            m = re.search(r"code=(\d+)", href)
            if not m:
                continue
            code = m.group(1)
            if code in seen_codes:
                continue
            seen_codes.add(code)

            # 컬럼 위치 — 네이버 테마 상세는 보통:
            # [0]종목명 [1]현재가 [2]전일비(이미지) [3]등락률 [4]매수호가 [5]매도호가 [6]거래량 [7]전일거래량
            price   = _parse_num(tds[1].get_text(strip=True)) if len(tds) > 1 else None
            chg_pct = _parse_num(tds[3].get_text(strip=True)) if len(tds) > 3 else None
            if chg_pct is None:
                # 폴백: 마지막 % 숫자 셀 찾기
                for td in tds[1:6]:
                    txt = td.get_text(strip=True)
                    if "%" in txt:
                        chg_pct = _parse_num(txt)
                        if chg_pct is not None:
                            break
            if chg_pct is None:
                continue

            # 부호 보정: 네이버 등락률은 절대값으로 표기 → 색상/이미지로 음수 판별
            is_down = False
            for cell in tds[:5]:
                # 이미지 alt/src
                for im in cell.find_all("img"):
                    src = ((im.get("src") or "") + " " + (im.get("alt") or "")).lower()
                    if "down" in src or "ico_d" in src or "low" in src or "하락" in src:
                        is_down = True
                # 색상 클래스
                cls_str = " ".join(cell.get("class", []))
                if "nv01" in cls_str or "down" in cls_str:
                    is_down = True
                # 텍스트에 '-' 직접 (드물지만)
                txt = cell.get_text(strip=True)
                if txt.startswith("-") and "%" in txt:
                    is_down = True

            if is_down and chg_pct > 0:
                chg_pct = -chg_pct

            results.append({
                "ticker":    f"{code}.KS",
                "code":      code,
                "name":      name,
                "price":     price,
                "change_1d": round(chg_pct, 2),
                "currency":  "KRW",
            })

        if not results:
            app.logger.warning(f"[constituents KR] {theme_no}: parsed 0 rows from {max_rows} links")

        # 상승률 순 정렬
        results.sort(key=lambda x: x["change_1d"] if x["change_1d"] is not None else -999, reverse=True)
        results = results[:limit]
        _CONSTITUENTS_CACHE[cache_key] = (now_ts, results)
        return results
    except Exception as e:
        app.logger.warning(f"[constituents KR] {theme_no}: {e}")
        return []


@app.route("/api/sectors/constituents", methods=["GET"])
def sector_constituents():
    """섹터/테마 구성 종목을 등락률 순으로 반환.

    Query:
        market: US | KR
        ticker (US): SPDR ETF 티커 (XLK 등)
        theme_no (KR): 네이버 테마 번호
    """
    market = (request.args.get("market") or "US").upper()
    if market == "KR":
        theme_no = (request.args.get("theme_no") or "").strip()
        stocks = _fetch_kr_theme_constituents(theme_no)
    else:
        ticker = (request.args.get("ticker") or "").strip().upper()
        stocks = _fetch_us_sector_constituents(ticker)
    return jsonify({
        "market": market,
        "stocks": stocks,
        "count": len(stocks),
    })


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


def _get_price_df(ticker, interval, min_bars, yf_period="max"):
    """가격 DataFrame (OHLCV) 반환. 토스 우선, yfinance 폴백.
    Returns: (df, source)  — source ∈ {"toss", "yfinance"}, 실패 시 (None, None)
    """
    # 1순위: 토스증권 캔들
    if toss_api.is_enabled() and toss_api.is_eligible(ticker):
        try:
            tdf = toss_api.get_candles_df(ticker, interval=interval, min_bars=min_bars)
            # 지표 계산에 충분한 최소량 확보됐을 때만 채택
            if tdf is not None and len(tdf) >= max(20, int(min_bars * 0.4)):
                return tdf, "toss"
        except Exception as e:
            app.logger.warning(f"[price_df] toss {ticker} {interval} failed: {e}")
    # 2순위: yfinance
    try:
        df = yf.Ticker(ticker).history(period=yf_period, interval=interval)
        if df is not None and not df.empty:
            return df, "yfinance"
    except Exception as e:
        app.logger.warning(f"[price_df] yfinance {ticker} {interval} failed: {e}")
    return None, None


def build_chart_data(ticker, interval):
    """차트 데이터만 반환 (봉 전환 시 사용)"""
    if interval not in ("1d", "1wk", "1mo"):
        interval = "1d"

    INTERVAL_LABELS = {"1d": "일봉", "1wk": "주봉", "1mo": "월봉"}

    # 봉별 목표 봉 수 (지표 + 충분한 히스토리)
    CHART_MIN_BARS = {"1d": 600, "1wk": 250, "1mo": 130}
    df, _src = _get_price_df(ticker, interval, CHART_MIN_BARS.get(interval, 600))
    if df is None or df.empty:
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

        prompt = f"""당신은 트레이딩 데스크에서 일하는 시니어 트레이더로, {name}({ticker}) 주식의 오늘 {price_change_pct:+.2f}% {kind} 움직임의 **진짜 원인**을 분석합니다.
{stock_ctx}{tech_ctx}{news_ctx}

[분석 프레임워크 — 다음 우선순위로 추론]
1) **명확한 캐털리스트(촉발 사건)**: 실적 발표, 가이던스 변경, 인수합병, 규제, 신제품, 애널리스트 의견 변동 — 뉴스에서 확인 가능한가?
2) **섹터/시장 전반 동조**: 동종 업종이 같이 움직였나? 시장 전반(S&P/KOSPI) 분위기와 연동되나?
3) **기술적 트리거**: 주요 지지/저항선 돌파/이탈, 이평선 데드/골든크로스, RSI 과매수/과매도 진입 — 차트 패턴이 매도/매수를 촉발했나?
4) **수급 변화**: 거래량 급증(평소 대비 1.5배+)이 누구의 행동인지 — 기관 청산? 외국인 매수? 차익 실현?

[출력 형식 — 4~6문장, 다음 요소를 자연스럽게 포함]
- **핵심 원인 1~2가지**: 가장 가능성 높은 것 (뉴스가 있으면 헤드라인 핵심을 짧게 인용, 없으면 기술적/거시 요인)
- **시장 맥락**: 이 움직임이 섹터/시장 전체와 같은 방향인지 다른지
- **데이터 근거**: RSI/MACD/거래량 중 의미 있는 1~2개만 골라 "이 지표가 이런 의미"로 해석 (단순 수치 나열 금지)
- **모니터링 포인트**: 이 추세가 지속될 시그널 또는 반전될 조건 (예: "다음 분기 가이던스가 핵심" / "OO 가격선 회복하면 반등 신호")

[작성 규칙]
1) 모든 텍스트를 **순수 한글**로만. **한자(漢字) 절대 금지**.
   - 잘못된 예: 賣出, 投資, 經濟, 産業, 倉庫 / 올바른 예: 매출, 투자, 경제, 산업, 창고
2) 영문 약어(RSI, MACD, EPS, AI 등)와 회사명·티커는 그대로 OK.
3) 추측 표현("보인다", "추정된다")은 데이터가 약할 때만 신중히 사용.
4) 단순 나열식이 아닌 **인과관계가 보이는 문장**으로 — "A 때문에 B가 되었고, 이는 C 가능성을 시사한다" 식.
5) 불필요한 서론 없이 바로 분석 시작."""

        text = _groq_chat(
            api_key,
            system_msg=(
                "당신은 한국 투자자를 위해 주가 움직임의 원인을 분석하는 시니어 트레이더입니다. "
                "반드시 순수 한글로만 답변하며, 한자(漢字)는 절대 사용하지 않습니다. "
                "한자어 단어도 모두 한글로 풀어 씁니다 (例: 倉庫→창고, 賣出→매출). "
                "영문 약어와 회사명·티커만 영문 그대로 사용합니다."
            ),
            user_msg=prompt,
            max_tokens=550,
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


# 한자 정규식 (CJK 통합 한자 + 확장 A + 호환 한자)
_HANJA_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# 자주 등장하는 한자 → 한글 (보험용 후처리)
_HANJA_MAP = {
    "倉庫": "창고", "賣出": "매출", "賣買": "매매", "投資": "투자",
    "經濟": "경제", "産業": "산업", "企業": "기업", "電子商去來": "전자상거래",
    "電子商": "전자상", "販賣": "판매", "會員": "회원", "專用": "전용",
    "收益": "수익", "利益": "이익", "市場": "시장", "競爭": "경쟁",
    "成長": "성장", "增加": "증가", "減少": "감소", "業界": "업계",
    "供給": "공급", "需要": "수요", "金融": "금융", "證券": "증권",
    "株式": "주식", "株價": "주가", "去來": "거래", "報告": "보고",
    "發表": "발표", "豫想": "예상", "戰略": "전략", "技術": "기술",
    "産業": "산업", "革新": "혁신", "規制": "규제", "海外": "해외",
    "國內": "국내", "全世界": "전세계", "世界": "세계",
}


def _strip_hanja(text: str, label: str = "") -> str:
    """한자가 섞여 있으면 일반 매핑으로 변환 + 남은 한자는 로그."""
    if not text:
        return text
    if not _HANJA_RE.search(text):
        return text
    cleaned = text
    for hanja, hangul in _HANJA_MAP.items():
        cleaned = cleaned.replace(hanja, hangul)
    # 잔여 한자 카운트 로깅 (디버깅용)
    remaining = _HANJA_RE.findall(cleaned)
    if remaining:
        try:
            app.logger.warning(
                f"[hanja-detected] {label}: {len(remaining)} chars left -> {''.join(remaining[:30])}"
            )
        except Exception:
            pass
        # 남은 한자도 제거 (괄호 등은 살리고)
        cleaned = _HANJA_RE.sub("", cleaned)
    return cleaned


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

                # 한자 후처리 (한자 → 한글 매핑 + 잔여 제거)
                text = _strip_hanja(text, label=label)

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

    # 시가총액 포맷 (None 대응)
    if is_krw and mktcap:
        if mktcap >= 1e12:
            mktcap_str = f"{mktcap/1e12:.1f}조원"
        elif mktcap >= 1e8:
            mktcap_str = f"{mktcap/1e8:.0f}억원"
        else:
            mktcap_str = f"{mktcap:,.0f}원"
    elif mktcap:
        if mktcap >= 1e9:
            mktcap_str = f"${mktcap/1e9:.1f}B"
        elif mktcap >= 1e6:
            mktcap_str = f"${mktcap/1e6:.0f}M"
        else:
            mktcap_str = f"${mktcap:,.0f}"
    else:
        mktcap_str = "정보 없음"

    pe_str     = f"{pe:.1f}" if pe else "정보 없음"
    fwd_pe_str = f"{fwd_pe:.1f}" if fwd_pe else "정보 없음"

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

    prompt = f"""당신은 {market} 주식 시장의 시니어 애널리스트로, 한국 개인 투자자를 위한 리서치 리포트를 작성합니다.

[기업 기본 정보]
- 종목: {name} ({ticker})
- 섹터/산업: {sector} / {industry}
- 시가총액: {mktcap_str}
- PER: {pe_str} / 선행PER: {fwd_pe_str}{rev_ctx}
- 영문 사업 개요: {eng_desc}

아래 3개 섹션을 정확한 레이블로 시작해 작성하세요. 각 섹션은 의사결정에 활용 가능한 깊이로.

[기업소개]
2~3문장. 다음을 포함:
- 핵심 비즈니스 한 줄 정의 + 한국 투자자에게 친숙한 유사 기업/서비스로 비유 (예: 월마트 = 이마트+홈플러스+코스트코, Visa = 비씨카드 결제망 글로벌판)
- 본사 위치와 주요 시장 (미국/글로벌/특정 지역)
- 위상감을 알 수 있는 한 줄 (시가총액 순위·점유율·매출 규모 중 가장 인상적인 것)

[주요사업]
3~5개의 핵심 매출원. 각 항목은 다음 형식의 bullet:
"• [사업부문명]: 1문장 설명 (가능하면 매출 비중 % 또는 규모 명시)"
나열이 아니라 비즈니스 모델이 한눈에 보이도록.

[기업분석]
5~8문장. 다음 4가지를 모두 다루되, **위에 주어진 숫자(매출 추이, PER 등)를 직접 인용**해 구체적으로 작성:
1) **시장 지위 & 경쟁 구도**: 동종 산업 핵심 경쟁사 1~2곳을 실명 거론 (예: 코카콜라 vs 펩시, 엔비디아 vs AMD). 차별화 포인트(브랜드·기술·네트워크 효과·원가 우위 등)를 구체적으로.
2) **산업의 중장기 흐름**: {industry} 산업이 어디로 가고 있는지 — AI/금리/규제/인구·소비 트렌드/구조 변화 등 외부 동인과 연결.
3) **이 기업의 현 위치**: 주어진 매출 추이를 보고 가속화/안정/둔화/턴어라운드 중 어느 국면인지 판단. PER이 동종 대비 비싼지/싼지 한 줄 평가.
4) **핵심 리스크 1~2가지**: 단기(분기 실적) 또는 중기(구조적). "어떤 시그널이 나오면 위험한가"를 구체적으로 적기.

[작성 규칙 — 반드시 준수]
1) 모든 텍스트를 **순수 한글**로만 작성. **한자(漢字) 절대 사용 금지**.
   - 잘못된 예: 倉庫, 賣出, 投資, 産業, 經濟 / 올바른 예: 창고, 매출, 투자, 산업, 경제
   - 일본식 약자 한자도 금지 (国, 経, 経済, 売 등)
2) 영문 회사명·티커·약어(AI, EPS, ROE, GDP 등)는 영문 그대로 OK.
3) 모호한 일반론("성장 가능성 있다", "주의가 필요하다") 금지. 주어진 데이터를 인용해 구체적으로.
4) 한국 투자자가 직관적으로 이해할 비유 적극 활용.
5) 각 섹션은 정확한 레이블 [기업소개] / [주요사업] / [기업분석] 로 시작.
6) 불필요한 서론·결론·인사말 없이 바로 본문."""

    text = _groq_chat(
        api_key,
        system_msg=(
            "당신은 한국 투자자를 위한 시니어 주식 애널리스트입니다. "
            "반드시 순수 한글로만 답변하며, 한자(漢字)는 절대 사용하지 않습니다. "
            "한자어로 표현되는 모든 단어(倉庫·賣出·投資·經濟 등)도 반드시 한글(창고·매출·투자·경제)로 풀어 씁니다. "
            "영문 약어와 회사명만 영어 그대로 사용 가능합니다."
        ),
        user_msg=prompt,
        max_tokens=1200,
        temperature=0.35,
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


def _is_market_open(ticker: str) -> bool:
    """대략적인 시장 개장 여부 (KR=Asia/Seoul 9:00–15:30, US=America/New_York 9:30–16:00).
    공휴일은 고려하지 않음(yfinance가 자체 처리)."""
    try:
        is_kr = ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")
        if is_kr:
            now = pd.Timestamp.now(tz="Asia/Seoul")
            if now.weekday() >= 5:
                return False
            t = now.time()
            return (t >= pd.Timestamp("09:00").time()) and (t <= pd.Timestamp("15:30").time())
        else:
            now = pd.Timestamp.now(tz="America/New_York")
            if now.weekday() >= 5:
                return False
            t = now.time()
            return (t >= pd.Timestamp("09:30").time()) and (t <= pd.Timestamp("16:00").time())
    except Exception:
        return False


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
        # 가격 df: 토스 우선, yfinance 폴백 (펀더멘털용 stock 객체는 yfinance 유지)
        # 지표(MA120 등) 계산 위해 chart_bars + 버퍼만큼 확보
        df_min_bars = max(chart_bars + 130, 260) if chart_bars < 99999 else 800
        df, price_source = _get_price_df(ticker, interval, df_min_bars, yf_period=period)

        if df is None or df.empty:
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
            df_w_raw, _wsrc = _get_price_df(ticker, "1wk", 104, yf_period="2y")
            if df_w_raw is not None:
                df_w_raw = df_w_raw.dropna(subset=["Close", "Open", "High", "Low"])
                if not df_w_raw.empty:
                    df_weekly = add_all_indicators(df_w_raw)
        except Exception:
            df_weekly = None

        ai_result = analyze_signals(df, info, df_weekly=df_weekly, stock=stock)

        # ── 한국 종목 보충: 공매도 + 외국인/기관 수급 ──────────────
        if is_korean:
            try:
                krx_code_supp = ticker.split(".")[0]
                kr_supp = fetch_kr_supplements(krx_code_supp)
                sc = ai_result.get("scorecard") or {}
                metrics = sc.get("metrics") if isinstance(sc, dict) else None
                if metrics is None and sc is not None:
                    sc["metrics"] = {}; metrics = sc["metrics"]
                if metrics is not None and kr_supp:
                    sh = kr_supp.get("short") or {}
                    su = kr_supp.get("supply") or {}
                    # 공매도 비율을 short_pct_of_float에 매핑 (UI 호환)
                    if sh.get("short_ratio_pct") is not None:
                        metrics["short_pct_of_float"]   = sh["short_ratio_pct"]
                        metrics["kr_short_balance_pct"] = sh.get("short_balance_pct")
                        metrics["kr_short_5d_avg_pct"]  = sh.get("short_5d_avg_pct")
                        metrics["kr_short_date"]        = sh.get("date")
                    # 외국인/기관 수급 (한국 전용)
                    if su:
                        metrics["kr_foreign_ratio_pct"] = su.get("foreign_ratio_pct")
                        metrics["kr_foreign_net_5d"]    = su.get("foreign_net_5d")
                        metrics["kr_inst_net_5d"]       = su.get("inst_net_5d")
                        metrics["kr_supply_date"]       = su.get("latest_date")
                    # DART 공시 직링크 (corp_code 없이 종목코드로 검색)
                    metrics["kr_dart_url"] = (
                        f"https://dart.fss.or.kr/dsab007/main.do?option=corp&textCrpNm={krx_code_supp}"
                    )
            except Exception as _e:
                app.logger.warning(f"[kr-supplements] inject failed: {_e}")

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

        # ── 실시간 가격 시도 ──────────────────────────────────────────
        # 우선순위:
        #   1. 한국 종목 → 네이버 폴링 API (~1분 지연)
        #   2. 폴백 → yfinance fast_info.last_price (15~20분 지연)
        # ⚠️ fast_info.previous_close는 가끔 틀린 값을 줘서(이틀 전 종가 등)
        #    이전 종가는 항상 히스토리(df)에서 직접 계산합니다.
        history_close      = safe_float(df["Close"].iloc[-1])
        history_prev_close = safe_float(df["Close"].iloc[-2]) if len(df) >= 2 else None

        # 마지막 히스토리 봉이 '오늘' 인지 판별 → prev_close 정확도 결정
        is_last_bar_today = False
        try:
            last_ts = df.index[-1]
            market_tz = "Asia/Seoul" if is_korean else "America/New_York"
            if hasattr(last_ts, "tz"):
                last_ts_local = (
                    last_ts.tz_localize(market_tz) if last_ts.tz is None
                    else last_ts.tz_convert(market_tz)
                )
                today_market = pd.Timestamp.now(tz=market_tz).normalize()
                is_last_bar_today = last_ts_local.normalize() >= today_market
        except Exception:
            pass

        realtime_price  = None
        realtime_source = None  # "toss" | "naver" | "yfinance"

        # 1) 토스증권 우선 (한국+미국 모두 실시간)
        if toss_api.is_enabled() and toss_api.is_eligible(ticker):
            try:
                tp = toss_api.get_price(ticker)
                if tp:
                    realtime_price  = safe_float(tp)
                    realtime_source = "toss"
            except Exception:
                pass

        # 2) 한국 종목 → 네이버 폴백 (~1분 지연)
        if not realtime_price and is_korean:
            nv = fetch_naver_realtime_price(ticker.split(".")[0])
            if nv and nv.get("current_price"):
                realtime_price  = nv.get("current_price")
                realtime_source = "naver"

        # 3) 폴백: yfinance fast_info.last_price (previous_close는 신뢰 안함)
        if not realtime_price:
            try:
                fi = stock.fast_info
                realtime_price = safe_float(getattr(fi, "last_price", None))
                if realtime_price:
                    realtime_source = "yfinance"
            except Exception:
                pass

        # 시장 개장 여부 (대략적 — KST/EST 기준)
        is_market_open_now = _is_market_open(ticker)

        # ── 가격/이전 종가 결정 (히스토리 우선) ──
        # 실시간가가 있고 (장중 OR 히스토리 종가와 다름)면 실시간 사용
        realtime_useful = (
            realtime_price is not None and
            (is_market_open_now or
             (history_close and abs(realtime_price - history_close) > 1e-6))
        )

        if realtime_useful:
            current_price = realtime_price
            # 마지막 봉이 오늘 인트라데이면 → 이전 종가는 iloc[-2] (어제)
            # 마지막 봉이 어제 종가면 → iloc[-1] 자체가 이전 종가
            prev_close  = history_prev_close if is_last_bar_today else history_close
            is_realtime = is_market_open_now
        else:
            # 실시간 데이터 없음 → 가장 최근 종가 표시
            current_price = history_close
            prev_close    = history_prev_close
            is_realtime   = False
            realtime_source = None

        price_change     = round(current_price - prev_close, 2) if (current_price and prev_close) else None
        price_change_pct = round((price_change / prev_close) * 100, 2) if (price_change and prev_close) else None

        # 데이터 기준 시각 (히스토리 마지막 봉)
        try:
            last_bar_ts = df.index[-1]
            data_timestamp = last_bar_ts.isoformat() if last_bar_ts else None
        except Exception:
            data_timestamp = None

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
            "is_realtime": is_realtime,
            "is_market_open": is_market_open_now,
            "data_timestamp": data_timestamp,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "realtime_source": realtime_source,  # "toss" | "naver" | "yfinance" | null
            "chart_source": price_source,        # "toss" | "yfinance" (디버그용)
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
        import traceback
        app.logger.error(f"[analyze] {ticker if 'ticker' in locals() else '?'} failed: {e}\n{traceback.format_exc()}")
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
    """USD/KRW 환율 (10분 캐시). 토스 우선, yfinance 폴백, 기본 1380."""
    now = time.time()
    if _FX_CACHE["rate"] and (now - _FX_CACHE["ts"]) < _FX_TTL:
        return _FX_CACHE["rate"]

    # 1순위: 토스증권 환율 API
    if toss_api.is_enabled():
        try:
            rate = toss_api.get_exchange_rate("USD", "KRW")
            if rate and rate > 0:
                _FX_CACHE["rate"] = rate
                _FX_CACHE["ts"]   = now
                return rate
        except Exception as e:
            app.logger.warning(f"[FX] toss rate failed: {e}")

    # 2순위: yfinance KRW=X
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
    """종목 현재가 (native currency). 토스 우선, yfinance 폴백."""
    # 1순위: 토스
    if toss_api.is_enabled() and toss_api.is_eligible(ticker):
        try:
            p = toss_api.get_price(ticker)
            if p:
                return float(p)
        except Exception:
            pass
    # 2순위: yfinance fast_info
    try:
        fi = yf.Ticker(ticker).fast_info
        p  = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        return float(p) if p else None
    except Exception:
        return None


_BENCHMARK_CACHE = {}   # symbol -> {"ts": epoch, "data": [{date, close}, ...]}
_BENCHMARK_TTL = 3600   # 1시간 캐시

def _fetch_benchmark_history(symbol, lookback_days=400):
    """KOSPI/S&P500 일봉 히스토리 (1시간 캐시).
    Returns: list of {"date": "YYYY-MM-DD", "close": float}
    """
    import time
    now = time.time()
    cached = _BENCHMARK_CACHE.get(symbol)
    if cached and (now - cached["ts"] < _BENCHMARK_TTL):
        return cached["data"]
    try:
        days = max(lookback_days, 30) + 10
        df = yf.Ticker(symbol).history(period=f"{days}d", auto_adjust=False)
        if df is None or df.empty:
            return cached["data"] if cached else []
        data = [
            {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])}
            for idx, row in df.iterrows() if row.get("Close") is not None
        ]
        _BENCHMARK_CACHE[symbol] = {"ts": now, "data": data}
        return data
    except Exception as e:
        app.logger.warning(f"[benchmark] {symbol} fetch failed: {e}")
        return cached["data"] if cached else []


def _normalize_benchmark(history, start_date_str):
    """벤치마크 종가 시계열을 start_date 기준 % 수익률로 변환.
    Returns: list of {"date": "YYYY-MM-DD", "return_pct": float}
    """
    if not history or not start_date_str:
        return []
    # start_date 이후 첫 종가를 baseline으로 사용
    baseline = None
    out = []
    for pt in history:
        if pt["date"] < start_date_str:
            continue
        if baseline is None:
            baseline = pt["close"]
            if not baseline or baseline <= 0:
                baseline = None
                continue
        out.append({
            "date": pt["date"],
            "return_pct": round((pt["close"] / baseline - 1) * 100, 3),
        })
    return out


def _build_badge_context(user, dashboard_summary, positions, holdings_count):
    """배지 평가용 context 빌더.
    dashboard_summary: dict with total_assets_krw, total_return_pct, realized_pnl_krw, etc.
    positions: 보유 종목 (KRW 평가금액 포함)
    holdings_count: 동시 보유 종목 수
    """
    txs = Transaction.query.filter_by(user_id=user.id).all()
    buy_count  = sum(1 for t in txs if t.type == "buy")
    sell_count = sum(1 for t in txs if t.type == "sell")
    sells      = [t for t in txs if t.type == "sell"]
    wins       = sum(1 for s in sells if (s.realized_pnl_krw or 0) > 0)
    win_rate   = (wins / len(sells) * 100) if sells else 0

    # 단일 매도 최대 실현 수익률 (price 기준)
    max_single_pct = 0.0
    loss_cut_count = 0
    for s in sells:
        # 매수 평균가를 정확히 다시 찾기 어려우므로, realized_pnl_krw 기반 근사
        if s.amount_krw and s.amount_krw > 0:
            pct = (s.realized_pnl_krw or 0) / s.amount_krw * 100
            if pct > max_single_pct:
                max_single_pct = pct
            if -15 <= pct <= -1:
                loss_cut_count += 1

    # 한국/미국 종목 누적 매수
    kr_tickers = set()
    us_tickers = set()
    for t in txs:
        if t.type != "buy":
            continue
        if t.currency == "KRW":
            kr_tickers.add(t.ticker)
        else:
            us_tickers.add(t.ticker)

    # 현재 보유 한/미 여부
    has_kr = any(p.get("currency") == "KRW" for p in positions)
    has_us = any(p.get("currency") == "USD" for p in positions)

    # 단일 종목 최대 평가금액
    max_pos_value = max((p.get("value_krw") or 0) for p in positions) if positions else 0

    # 스냅샷 일수
    snapshot_days = AssetSnapshot.query.filter_by(user_id=user.id).count()

    return {
        "user_id":                 user.id,
        "total_assets_krw":        dashboard_summary["total_assets_krw"],
        "total_return_pct":        dashboard_summary["total_return_pct"],
        "realized_pnl_krw":        dashboard_summary["realized_pnl_krw"],
        "tx_count":                len(txs),
        "buy_count":               buy_count,
        "sell_count":              sell_count,
        "win_rate":                win_rate,
        "max_single_realized_pct": max_single_pct,
        "loss_cut_count":          loss_cut_count,
        "holdings_count":          holdings_count,
        "max_position_value_krw":  max_pos_value,
        "has_kr":                  has_kr,
        "has_us":                  has_us,
        "kr_unique_buys":          len(kr_tickers),
        "us_unique_buys":          len(us_tickers),
        "snapshot_days":           snapshot_days,
    }


def _check_and_award_badges(user, ctx):
    """ctx에 따라 새로 자격이 된 배지 부여. 새로 받은 키 리스트 반환."""
    from badges import evaluate_badges
    earned = {b.badge_key for b in UserBadge.query.filter_by(user_id=user.id).all()}
    new_keys = evaluate_badges(ctx, earned)
    if not new_keys:
        return []
    try:
        for k in new_keys:
            db.session.add(UserBadge(user_id=user.id, badge_key=k))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[badge] award failed for user {user.id}: {e}")
        return []
    return new_keys


def _save_today_snapshot(user_id, total_assets_krw, cash_krw, positions_value_krw):
    """오늘 날짜로 자산 스냅샷 upsert (시장 타임존 KST 기준)."""
    try:
        from datetime import date
        import pytz
        today = datetime.now(pytz.timezone("Asia/Seoul")).date()
    except Exception:
        from datetime import date
        today = date.today()

    try:
        existing = AssetSnapshot.query.filter_by(user_id=user_id, date=today).first()
        if existing:
            existing.total_assets_krw    = total_assets_krw
            existing.cash_krw            = cash_krw
            existing.positions_value_krw = positions_value_krw
        else:
            snap = AssetSnapshot(
                user_id=user_id, date=today,
                total_assets_krw=total_assets_krw,
                cash_krw=cash_krw,
                positions_value_krw=positions_value_krw,
            )
            db.session.add(snap)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[snapshot] failed for user {user_id}: {e}")


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
    holdings = PaperHolding.query.filter_by(user_id=user.id).all()

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

    # 오늘 자산 스냅샷 저장 (자산 변화 차트용)
    _save_today_snapshot(
        user.id,
        total_assets_krw=total_assets_krw,
        cash_krw=user.cash_balance,
        positions_value_krw=positions_value_krw,
    )

    # 배지 자동 부여
    dashboard_summary = {
        "total_assets_krw": total_assets_krw,
        "total_return_pct": total_return_pct,
        "realized_pnl_krw": realized_pnl_krw,
    }
    new_badges = _check_and_award_badges(
        user,
        _build_badge_context(user, dashboard_summary, positions, len(holdings)),
    )

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
        "newly_earned_badges": new_badges,   # 이번 호출에 새로 받은 배지 키 목록
        "nickname":            user.nickname,
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

    # 모의투자 보유 추가 또는 평균단가 업데이트
    existing = PaperHolding.query.filter_by(user_id=user.id, ticker=ticker).first()
    if existing:
        new_qty = existing.quantity + qty
        avg     = (existing.purchase_price * existing.quantity + price * qty) / new_qty
        existing.quantity       = round(new_qty, 6)
        existing.purchase_price = round(avg, 6)
        existing.name           = name
        existing.currency       = currency
    else:
        h = PaperHolding(
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

    holding = PaperHolding.query.filter_by(user_id=user.id, ticker=ticker).first()
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


@app.route("/api/trading/history")
@login_required
def trading_history():
    """자산 변화 차트 데이터.
    Query: days=7|30|90|365|all (default 30)
    Returns: { snapshots, benchmarks: {kospi, sp500}, initial_capital_krw }
    """
    from datetime import date, timedelta
    days_param = (request.args.get("days") or "30").strip().lower()
    if days_param == "all":
        cutoff = None
    else:
        try:
            n = int(days_param)
        except Exception:
            n = 30
        cutoff = date.today() - timedelta(days=n)

    user = current_user
    q = AssetSnapshot.query.filter_by(user_id=user.id)
    if cutoff:
        q = q.filter(AssetSnapshot.date >= cutoff)
    snaps = q.order_by(AssetSnapshot.date.asc()).all()

    initial = user.initial_capital or INITIAL_CAPITAL_KRW

    snap_series = []
    for s in snaps:
        snap_series.append({
            "date":             s.date.isoformat() if s.date else None,
            "total_assets_krw": round(s.total_assets_krw),
            "return_pct":       round((s.total_assets_krw / initial - 1) * 100, 3) if initial > 0 else 0,
        })

    # 벤치마크 시작일 = 사용자 첫 스냅샷일 (없으면 오늘)
    start_date_str = snap_series[0]["date"] if snap_series else date.today().isoformat()

    lookback = 400 if days_param == "all" else max(int(days_param) if days_param.isdigit() else 30, 30) + 30

    kospi_hist = _fetch_benchmark_history("^KS11", lookback_days=lookback)
    sp500_hist = _fetch_benchmark_history("^GSPC", lookback_days=lookback)

    return jsonify({
        "snapshots":           snap_series,
        "initial_capital_krw": round(initial),
        "benchmarks": {
            "kospi": _normalize_benchmark(kospi_hist, start_date_str),
            "sp500": _normalize_benchmark(sp500_hist, start_date_str),
        },
        "start_date": start_date_str,
    })


@app.route("/api/me/badges")
@login_required
def my_badges():
    """획득한 배지 목록 + 전체 배지 정의."""
    from badges import BADGES, badge_public_dict
    earned = {b.badge_key: b.earned_at for b in UserBadge.query.filter_by(user_id=current_user.id).all()}
    all_badges = []
    for b in BADGES:
        pub = badge_public_dict(b)
        pub["earned"]    = b["key"] in earned
        pub["earned_at"] = earned[b["key"]].isoformat() if pub["earned"] else None
        all_badges.append(pub)
    return jsonify({
        "badges":      all_badges,
        "earned_count": len(earned),
        "total_count":  len(BADGES),
    })


@app.route("/api/me/nickname", methods=["GET", "POST"])
@login_required
def my_nickname():
    """닉네임 조회/설정.
    POST body: { "nickname": "..." } — 2~20자, 한글/영문/숫자/_ 만
    """
    if request.method == "GET":
        return jsonify({"nickname": current_user.nickname})

    import re
    data = request.get_json() or {}
    nick = (data.get("nickname") or "").strip()
    if not nick:
        return jsonify({"error": "닉네임을 입력해주세요"}), 400
    if len(nick) < 2 or len(nick) > 20:
        return jsonify({"error": "닉네임은 2~20자여야 합니다"}), 400
    if not re.fullmatch(r"[가-힣a-zA-Z0-9_]+", nick):
        return jsonify({"error": "한글, 영문, 숫자, 언더스코어만 사용 가능합니다"}), 400

    # 중복 확인 (자기 자신 제외)
    existing = User.query.filter(User.nickname == nick, User.id != current_user.id).first()
    if existing:
        return jsonify({"error": "이미 사용 중인 닉네임입니다"}), 400

    current_user.nickname = nick
    db.session.commit()
    return jsonify({"ok": True, "nickname": nick})


@app.route("/api/leaderboard")
def leaderboard():
    """전체 랭킹.
    Query: metric=total|7d|30d (default total), limit=50
    Returns: [{rank, nickname, profile_image, return_pct, total_assets_krw, is_me}]
    """
    from datetime import date, timedelta
    metric = (request.args.get("metric") or "total").lower()
    limit  = min(int(request.args.get("limit", 50)), 100)
    if metric not in ("total", "7d", "30d"):
        metric = "total"

    # 닉네임 설정 + 공개 유저만
    users = User.query.filter(
        User.nickname.isnot(None),
        User.is_public.is_(True),
    ).all()

    rows = []
    for u in users:
        if metric == "total":
            initial = u.initial_capital or INITIAL_CAPITAL_KRW
            # 최신 스냅샷 또는 현재 cash 기반
            last = AssetSnapshot.query.filter_by(user_id=u.id)\
                    .order_by(AssetSnapshot.date.desc()).first()
            if not last:
                continue
            ret_pct = (last.total_assets_krw / initial - 1) * 100 if initial > 0 else 0
            total_krw = last.total_assets_krw
        else:
            days = 7 if metric == "7d" else 30
            cutoff = date.today() - timedelta(days=days)
            recent_snaps = AssetSnapshot.query.filter(
                AssetSnapshot.user_id == u.id,
                AssetSnapshot.date >= cutoff,
            ).order_by(AssetSnapshot.date.asc()).all()
            if len(recent_snaps) < 2:
                continue
            start_v = recent_snaps[0].total_assets_krw
            end_v   = recent_snaps[-1].total_assets_krw
            if start_v <= 0:
                continue
            ret_pct = (end_v / start_v - 1) * 100
            total_krw = end_v
        rows.append({
            "user_id":          u.id,
            "nickname":         u.nickname,
            "profile_image":    u.profile_image,
            "return_pct":       round(ret_pct, 2),
            "total_assets_krw": round(total_krw),
        })

    # 정렬 + 랭크 부여
    rows.sort(key=lambda r: r["return_pct"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["is_me"] = (current_user.is_authenticated and r["user_id"] == current_user.id)
        # 다른 사람의 user_id는 노출 안함 (is_me만 필요)
        if not r["is_me"]:
            r.pop("user_id", None)

    return jsonify({
        "metric": metric,
        "ranks":  rows[:limit],
        "total_participants": len(rows),
    })


@app.route("/leaderboard")
def leaderboard_page():
    return render_template("leaderboard.html")


@app.route("/api/trading/reset", methods=["POST"])
@login_required
def trading_reset():
    """모의투자 초기화: 현금 1억으로, 모의 보유종목·거래내역 삭제.
    (실제 포트폴리오 Holding은 건드리지 않음)
    """
    user = current_user
    PaperHolding.query.filter_by(user_id=user.id).delete()
    Transaction.query.filter_by(user_id=user.id).delete()
    AssetSnapshot.query.filter_by(user_id=user.id).delete()
    UserBadge.query.filter_by(user_id=user.id).delete()
    user.cash_balance    = INITIAL_CAPITAL_KRW
    user.initial_capital = INITIAL_CAPITAL_KRW
    db.session.commit()
    return jsonify({"ok": True, "cash_balance": INITIAL_CAPITAL_KRW})


@app.route("/api/debug/toss")
def debug_toss():
    """토스 API 진단: 서버 egress IP + 토큰 발급 성공 여부.
    배포 환경의 outbound IP를 토스 콘솔 허용목록에 등록할 때 사용.
    """
    out = {"toss_enabled": toss_api.is_enabled()}
    # 1) 서버가 외부로 나갈 때의 공인 IP (토스 콘솔에 등록할 IP)
    try:
        out["egress_ip"] = requests.get("https://api.ipify.org", timeout=5).text
    except Exception as e:
        out["egress_ip"] = None
        out["egress_ip_error"] = str(e)
    # 2) 토스 토큰 발급 테스트 (성공 = IP 등록됨)
    try:
        tok = toss_api._get_token()
        out["toss_token_ok"] = bool(tok)
    except Exception as e:
        out["toss_token_ok"] = False
        out["toss_token_error"] = str(e)
    # 3) 환율 한 번 호출해보기 (실데이터 확인)
    try:
        out["toss_fx_usdkrw"] = toss_api.get_exchange_rate("USD", "KRW")
    except Exception:
        out["toss_fx_usdkrw"] = None
    return jsonify(out)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
