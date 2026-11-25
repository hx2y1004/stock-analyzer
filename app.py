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
import ai_cache
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
        global _TR_LAST_WARN
        if not text or not text.strip():
            return text
        # 이미 한글이면 번역 스킵 (불필요한 외부 호출 방지)
        if _has_korean(text):
            return text
        # 최근 10분 안에 Google 번역이 막혔으면 바로 원문 (서버 IP 는 자주 막혀 요청마다 수 초를 낭비했다)
        if time.time() - _TR_LAST_WARN < 600:
            return text
        # ThreadPoolExecutor 로 timeout 강제 (deep_translator 자체엔 timeout 없음)
        with _TPE(max_workers=1) as ex:
            fut = ex.submit(
                lambda: _GTrans(source='auto', target='ko').translate(text[:max_chars])
            )
            try:
                return fut.result(timeout=timeout)
            except Exception as e:
                # timeout 또는 네트워크 에러 → 원문 그대로. 원인은 10분에 한 번 로그로 남긴다
                # (데이터센터 IP 는 Google 이 /sorry 봇 확인 페이지로 돌려보내 실패하는 경우가 많다)
                if time.time() - _TR_LAST_WARN > 600:
                    _TR_LAST_WARN = time.time()
                    print(f"[translate] google 번역 실패 → 원문 사용: {type(e).__name__}: {str(e)[:160]}", flush=True)
                return text

    _TR_LAST_WARN = 0.0

    def _translate_batch(texts):
        with _TPE(max_workers=6) as ex:
            return list(ex.map(_translate_ko, texts))
except ImportError:
    def _translate_ko(text, max_chars=2000, timeout=4): return text
    def _translate_batch(texts): return texts
from flask import Flask, jsonify, render_template, request, redirect
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user

from analysis.ai_analysis import analyze_signals
from analysis.indicators import add_all_indicators
from analysis.valuation import compute_dcf, ttm_from_quarters
from analysis import macro as macro_mod
from models import AppliedStockSplit, DropHoldState, DisclosureEvent
from models import (db, User, Holding, PaperHolding, Transaction, AssetSnapshot, UserBadge, AiCache, INITIAL_CAPITAL_KRW,
                    BotStrategy, AiStockAssessment, AiSignal, UserSignalSubscription, WatchlistItem, Notification,
                    BotPosition, PushSubscription, ScreenerSnapshot, TrendSetup)
from auth import auth_bp

# ── DB URL (로컬: SQLite, Railway: PostgreSQL) ─────────────────────────────────
_db_url = os.environ.get("DATABASE_URL", "sqlite:///portfolio.db")
if _db_url.startswith("postgres://"):          # Railway 구버전 호환
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)

# 로그 레벨: 설정하지 않으면 app.logger 의 유효 레벨이 루트 기본값 WARNING 이라 운영(gunicorn)에서 info 가 모두 버려졌다
# ([BOT]·[TRENDS]·[SCREENER]·[SECTORMAP]·[AI] 등 배치·스케줄러 진행 로그). LOG_LEVEL 로 조정 (DEBUG/INFO/WARNING/ERROR).
# 출력은 Flask 기본 핸들러(요청 중엔 wsgi.errors, 그 밖엔 stderr) → systemd 저널. 핸들러를 더 붙이지 않아 줄이 중복되지 않는다.
import logging as _logging
_LOG_LEVEL_NAME = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
_lvl = _logging.getLevelName(_LOG_LEVEL_NAME)          # 'INFO' → 20, 모르는 이름이면 문자열이 돌아온다
app.logger.setLevel(_lvl if isinstance(_lvl, int) else _logging.INFO)

# 운영 여부: 운영 DB(DATABASE_URL)가 있거나 예전 Railway 배포면 운영으로 본다.
# (예전에는 RAILWAY_ENVIRONMENT 만 봐서 Oracle 서버에서는 운영 보호가 꺼져 있었다)
IS_PRODUCTION = bool(os.environ.get("DATABASE_URL") or os.environ.get("RAILWAY_ENVIRONMENT"))

_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not _secret:
    if IS_PRODUCTION:
        # 공개된 기본 키를 쓰면 누구나 세션 쿠키를 위조할 수 있다. 기동을 막으면 자동 롤백되므로
        # 대신 프로세스마다 임의 키를 만든다 (재시작 시 로그인이 풀리는 것 외에는 안전).
        import secrets as _secrets
        _secret = _secrets.token_hex(32)
        print("[security] FLASK_SECRET_KEY 미설정 — 임시 랜덤 키 사용 (재시작 시 로그인 해제). "
              ".env 에 FLASK_SECRET_KEY 를 설정하세요.", flush=True)
    else:
        _secret = "dev-only-change-in-production"
app.secret_key = _secret
app.config["SQLALCHEMY_DATABASE_URI"]        = _db_url


@app.after_request
def _gzip_large_json(resp):
    """큰 JSON 응답(분석·차트: 수백 KB~수 MB)을 gzip 으로 압축한다. 의존성 없이 표준 라이브러리만 사용.
    이미 인코딩된 응답, 스트리밍 응답, 작은 응답, gzip 미지원 클라이언트는 건드리지 않는다."""
    try:
        if (resp.status_code != 200 or resp.direct_passthrough
                or resp.mimetype != "application/json"
                or "Content-Encoding" in resp.headers
                or "gzip" not in (request.headers.get("Accept-Encoding") or "").lower()):
            return resp
        data = resp.get_data()
        if len(data) < 32 * 1024:
            return resp
        import gzip as _gzip
        resp.set_data(_gzip.compress(data, compresslevel=5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass
    return resp
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
CORS(app)

db.init_app(app)
ai_cache.init(app, db, AiCache, app.logger)   # AI 결과 영구 캐시 (ai_cache 테이블은 아래 db.create_all() 이 만든다)

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
            # 포지션 노트: 거래별 매매 이유 메모
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS note VARCHAR(300)"
            ))
            # 모의투자 초기화 횟수
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_count INTEGER DEFAULT 0"
            ))
            # 일회성 보정: 컬럼 도입 전 초기화 이력 반영 (멱등 — 이미 값 있으면 스킵)
            conn.execute(text(
                "UPDATE users SET reset_count = 2 WHERE nickname = '안기명' AND COALESCE(reset_count, 0) = 0"
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

    # 모의투자 자동 청산 예약 컬럼 (2026-11). 이미 있으면 건너뛴다 — 운영 PostgreSQL · 로컬 SQLite 모두 무중단
    try:
        from sqlalchemy import inspect as _sa_inspect, text as _text_m
        _cols = {c["name"] for c in _sa_inspect(db.engine).get_columns("paper_holdings")}
        with db.engine.begin() as _cm:
            for _col in ("target_price", "stop_loss_price"):
                if _col not in _cols:
                    _cm.execute(_text_m(f"ALTER TABLE paper_holdings ADD COLUMN {_col} DOUBLE PRECISION"))
                    print(f"[migration] paper_holdings.{_col} 추가")
    except Exception as e:
        print(f"[migration] paper_holdings 예약 컬럼: {e}")

    # 공시·수주 알림 설정 컬럼 (2026-11). disclosure_events 테이블은 위 create_all 이 만든다
    try:
        from sqlalchemy import inspect as _sa_inspect2, text as _text_d
        _cols2 = {c["name"] for c in _sa_inspect2(db.engine).get_columns("user_signal_subscriptions")}
        if "disclosure_alerts" not in _cols2:
            with db.engine.begin() as _cd:
                _cd.execute(_text_d("ALTER TABLE user_signal_subscriptions ADD COLUMN disclosure_alerts BOOLEAN DEFAULT FALSE"))
            print("[migration] user_signal_subscriptions.disclosure_alerts 추가")
    except Exception as e:
        print(f"[migration] disclosure_alerts 컬럼: {e}")


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


def _cf(val):
    """차트용 실수: 소수 넷째 자리로 반올림 (352.45001220703125 → 352.45).
    분석 응답의 99% 가 차트 배열이라 자릿수만 줄여도 전송량이 절반 가까이 준다."""
    v = safe_float(val)
    return None if v is None else round(v, 4)


def _cv(val):
    """차트용 거래량: 정수."""
    v = safe_float(val)
    return None if v is None else int(round(v))


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
        # 2026-11 확인: 응답이 {"datas": [...]} (새 형식)으로 바뀌어 예전 파서가 늘 None 을 돌려줬다.
        # 새 형식: closePrice(현재가), compareToPreviousClosePrice(전일 대비), marketStatus. 예전 형식도 계속 지원.
        def _n(v):
            return safe_float(str(v).replace(",", "")) if v not in (None, "") else None
        if data.get("datas"):
            d = data["datas"][0]
            current = _n(d.get("closePrice"))
            diff    = _n(d.get("compareToPreviousClosePrice"))
            prev    = (current - diff) if (current is not None and diff is not None) else None
            status  = d.get("marketStatus")
        else:
            areas = (data.get("result") or {}).get("areas") or []
            datas = (areas[0].get("datas") or []) if areas else []
            if not datas:
                return None
            d = datas[0]
            current = safe_float(d.get("nv"))    # 현재가
            prev    = safe_float(d.get("pcv"))   # 전일 종가
            status  = d.get("ms")
        if not current:
            return None
        result = {
            "current_price":  current,
            "previous_close": prev,
            "market_status":  status,   # "OPEN" / "CLOSE"
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


def _ttl_cached(cache: dict, key, ttl_ok: int, ttl_empty: int, is_empty, fetch, max_entries: int = 300):
    """종목별 결과 메모리 캐시 (단일 프로세스 전제).

    - 데이터가 있으면 ttl_ok 초, 비어 있으면(일시 오류 가능) ttl_empty 초만 보관
    - 호출 측이 결과를 수정해도 캐시가 오염되지 않도록 깊은 복사본을 반환
    - 항목이 max_entries 를 넘으면 가장 오래된 것부터 제거
    """
    import copy
    now_ts = time.time()
    hit = cache.get(key)
    if hit and (now_ts - hit[0]) < hit[1]:
        return copy.deepcopy(hit[2])
    result = fetch()
    ttl = ttl_empty if is_empty(result) else ttl_ok
    with _TTL_CACHE_LOCK:   # gthread 다중 스레드에서 정리 중 dict 변경 충돌 방지
        cache[key] = (now_ts, ttl, copy.deepcopy(result))
        if len(cache) > max_entries:
            for k, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[: len(cache) - max_entries]:
                cache.pop(k, None)
    return result


_TTL_CACHE_LOCK = threading.Lock()
_KR_QUARTERLY_CACHE = {}
_KR_QUARTERLY_TTL = 6 * 3600       # 분기 실적: 자주 바뀌지 않음
_KR_ANALYST_CACHE = {}
_KR_ANALYST_TTL = 3600             # 증권사 리포트: 하루 몇 건 수준
_KR_EMPTY_TTL = 600                # 빈 결과(조회 실패 포함)는 10분만 보관


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


# ── 홈 시황 대시보드 ────────────────────────────────────────
_MARKET_OVERVIEW_CACHE = {"ts": 0.0, "data": None}
_MARKET_OVERVIEW_TTL = 60          # 지수는 1분 캐시

_OVERVIEW_INDICES = [
    # (symbol, label, market, kind)  kind: index|fx|vix
    ("^KS11",  "KOSPI",   "KR", "index"),
    ("^KQ11",  "KOSDAQ",  "KR", "index"),
    ("KRW=X",  "USD/KRW", "KR", "fx"),
    ("^GSPC",  "S&P 500", "US", "index"),
    ("^IXIC",  "나스닥",   "US", "index"),
    ("^VIX",   "VIX",     "US", "vix"),
]


_NAVER_INDEX = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}


def _naver_index(sym):
    """한국 지수는 네이버 값을 쓴다. Yahoo 는 장 마감 후 당일 봉 종가가 비어(NaN) 전날 값이 '오늘'로 보이는 경우가 있었다
    (2026-11-20: KOSPI 실제 4,869.96 -5.21% 인데 5,137.89 +1.39% 로 표시). 실패하면 None → Yahoo 폴백."""
    code = _NAVER_INDEX.get(sym)
    if not code:
        return None
    try:
        r = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json() or {}
        val = safe_float(str(j.get("closePrice", "")).replace(",", ""))
        chg = safe_float(str(j.get("fluctuationsRatio", "")).replace(",", ""))
        if not val:
            return None
        return {"value": round(val, 2), "change_pct": round(chg, 2) if chg is not None else None}
    except Exception:
        return None


@app.route("/api/market/overview")
def market_overview():
    """홈 대시보드용 주요 지수 + 거시 지표 스냅샷 (1분 캐시)."""
    return jsonify(_market_overview_payload())


def _market_overview_payload():
    """6개 지수 + 미 10년물·금·달러 인덱스 + 매크로 국면. 홈 시황 캐시(1분)를 공유한다 (한 번의 yf.download)."""
    now = time.time()
    c = _MARKET_OVERVIEW_CACHE
    if c["data"] and (now - c["ts"]) < _MARKET_OVERVIEW_TTL:
        return c["data"]

    out = []
    macro = {}
    try:
        symbols = [s for s, *_ in _OVERVIEW_INDICES] + [s for s, *_ in macro_mod.MACRO_SYMBOLS]
        data = yf.download(
            " ".join(symbols), period="5d", interval="1d",
            group_by="ticker", progress=False, threads=True, auto_adjust=False,
        )
        for sym, label, mkt, kind in _OVERVIEW_INDICES:
            nv = _naver_index(sym)
            if nv:
                out.append({"symbol": sym, "label": label, "market": mkt, "kind": kind, **nv})
                continue
            try:
                df = data[sym].dropna(subset=["Close"])
                if df.empty:
                    continue
                cur = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
                chg = ((cur / prev - 1) * 100) if prev else None
                out.append({
                    "symbol": sym, "label": label, "market": mkt, "kind": kind,
                    "value": round(cur, 2),
                    "change_pct": round(chg, 2) if chg is not None else None,
                })
            except Exception:
                continue
        # 거시 지표: 티커별로 실패해도 그 지표만 None
        for sym, key, label, kind in macro_mod.MACRO_SYMBOLS:
            try:
                q = macro_mod.parse_quote(data[sym]["Close"].tolist(), kind)
            except Exception:
                q = None
            macro[key] = {"symbol": sym, "label": label, "kind": kind, **q} if q else None
    except Exception as e:
        app.logger.warning(f"[market_overview] {e}")

    vix = next((it for it in out if it.get("symbol") == "^VIX"), None)
    macro["regime"] = macro_mod.macro_regime(vix, macro.get("tnx"), macro.get("gold"), macro.get("dxy"), is_korean=False)
    macro["regime_kr"] = macro_mod.macro_regime(vix, macro.get("tnx"), macro.get("gold"), macro.get("dxy"), is_korean=True)
    payload = {
        "indices": out,
        "macro": macro,
        "kr_open": _is_market_open("005930.KS"),
        "us_open": _is_market_open("AAPL"),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    if out:
        _MARKET_OVERVIEW_CACHE.update(ts=now, data=payload)
    return payload


_MACRO_FAIL = {"ts": 0.0}
_MACRO_MAX_AGE = 15 * 60          # 종목 분석은 15분 안의 홈 시황 캐시를 그대로 쓴다 (없으면 한 번 받아 채움)


def get_macro_snapshot(is_korean=False):
    """→ {tnx, gold, dxy, regime} (각 지표는 없으면 None). 실패해도 예외 없이 빈 국면(0점)을 돌려준다."""
    try:
        c = _MARKET_OVERVIEW_CACHE
        now = time.time()
        if c.get("data") and (now - c["ts"]) < _MACRO_MAX_AGE:
            d = c["data"]
        elif now - _MACRO_FAIL["ts"] < 60:          # 직전 1분 안에 실패했으면 다시 받지 않는다 (분석 지연 방지)
            d = None
        else:
            d = _market_overview_payload()
            if not (d or {}).get("indices"):
                _MACRO_FAIL["ts"] = now
        m = (d or {}).get("macro") or {}
    except Exception as e:
        app.logger.debug(f"[macro] {e}")
        m = {}
    reg = m.get("regime_kr" if is_korean else "regime") or macro_mod.macro_regime()
    return {"tnx": m.get("tnx"), "gold": m.get("gold"), "dxy": m.get("dxy"), "regime": reg}


# ── 섹터맵: 전체 유니버스 시총가중 + 상승/하락 분포 (sector_map.py 가 백그라운드로 스냅샷 갱신) ─────
def _stock_name(ticker, market):
    for s in STOCK_DB:
        if s.get("symbol") == ticker:
            return s.get("name") or ticker
    if market == "US":
        return _lookup_us_name(ticker) or ticker
    return ticker


@app.route("/api/market/sectormap")
def market_sectormap():
    """최신 섹터맵 스냅샷을 즉시 돌려준다 (요청 때 시세를 받지 않음).
    스냅샷이 아직 없으면 백그라운드로 한 번 만들기 시작하고 warming=true 를 돌려준다."""
    market = "KR" if (request.args.get("market") or "US").upper() == "KR" else "US"
    snap = sector_map.get(market)
    if not snap:
        sector_map.warmup_async((market,))
        return jsonify({"market": market, "warming": True, "groups": []})
    return jsonify(snap)


# 급등/급락 관찰 유니버스 — 유동성 큰 대표주 (한국 25 + 미국 30)
_MOVERS_KR = [
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS",
    "000270.KS", "068270.KS", "005490.KS", "035420.KS", "035720.KS",
    "051910.KS", "006400.KS", "012450.KS", "042700.KS", "011200.KS",
    "086520.KQ", "247540.KQ", "028300.KQ", "196170.KQ", "066970.KQ",
    "105560.KS", "055550.KS", "329180.KS", "010130.KS", "009540.KS",
]
_MOVERS_US = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "MU",
    "PLTR", "COIN", "UBER", "SMCI", "ARM", "MRVL", "TSM", "ASML",
    "JPM", "V", "LLY", "UNH", "XOM", "BA",
]
_MOVERS_CACHE = {"ts": 0.0, "data": None}
_MOVERS_TTL = 60 * 10              # 10분 캐시
_MOVERS_BASE = {}                  # market → {ticker: row(전일 종가 포함)}


@app.route("/api/market/movers")
def market_movers():
    """홈 대시보드용 급등/급락 랭킹 (대표주 유니버스, 10분 캐시)."""
    now = time.time()
    c = _MOVERS_CACHE
    if c["data"] and (now - c["ts"]) < _MOVERS_TTL:
        return jsonify(_movers_live(c["data"]))

    def _calc(tickers, market):
        rows = []
        try:
            data = yf.download(
                " ".join(tickers), period="5d", interval="1d",
                group_by="ticker", progress=False, threads=True, auto_adjust=False,
            )
        except Exception as e:
            app.logger.warning(f"[movers {market}] download: {e}")
            return rows
        for t in tickers:
            try:
                df = data[t].dropna(subset=["Close"])
                if df is None or len(df) < 2:
                    continue
                cur = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                if not prev:
                    continue
                # 종목명: STOCK_DB 우선
                name = t
                for s in STOCK_DB:
                    if s.get("symbol") == t:
                        name = s.get("name") or t
                        break
                if name == t and market == "US":
                    name = _lookup_us_name(t) or t
                rows.append({
                    "ticker": t, "name": name, "market": market,
                    "price": round(cur, 2),
                    "change_pct": round((cur / prev - 1) * 100, 2),
                    "prev_close": prev,
                })
            except Exception:
                continue
        return rows

    kr = _calc(_MOVERS_KR, "KR")
    us = _calc(_MOVERS_US, "US")
    for m, rows in (("KR", kr), ("US", us)):
        if rows:
            _MOVERS_BASE[m] = {r["ticker"]: r for r in rows}      # 전체 30종목 (실시간 순위 재계산용)

    def _split(rows):
        s = sorted(rows, key=lambda r: r["change_pct"], reverse=True)
        n = min(8, len(s) // 2)                      # 급등·급락 각 8개 (유니버스 30종목이라 겹치지 않음)
        return {"gainers": s[:n], "losers": s[-n:][::-1] if n else []}

    payload = {
        "KR": _split(kr),
        "US": _split(us),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    if kr or us:
        _MOVERS_CACHE.update(ts=now, data=payload)
    return jsonify(_movers_live(payload))


def _movers_live(payload):
    """급등락: 10분 캐시의 종목·전일 종가는 그대로 두고, 장이 열린 시장은 현재가·등락률을 토스 실시간(20초 공용 캐시)으로
    다시 계산해 순위를 매긴다. 토스 미설정·실패면 캐시 그대로."""
    import copy
    out = copy.deepcopy(payload)
    for m in ("KR", "US"):
        blk = out.get(m) or {}
        rows = (blk.get("gainers") or []) + (blk.get("losers") or [])
        if not rows or not _session_open(m):
            continue
        base = _MOVERS_BASE.get(m) or {}
        try:
            got = toss_api.get_prices_cached([r["ticker"] for r in base.values()] or [r["ticker"] for r in rows])
        except Exception:
            got = {}
        if not got:
            continue
        pool = list(base.values()) or rows
        kprev = prev_closes_today([r["ticker"] for r in pool]) if m == "KR" else {}
        live = []
        for r in pool:
            info, prev = got.get(r["ticker"]), kprev.get(r["ticker"]) or r.get("prev_close")
            if info and info.get("price") and prev:
                live.append({**r, "price": round(float(info["price"]), 2), "change_pct": toss_api.change_pct(info["price"], prev), "live": True})
            else:
                live.append(r)
        srt = sorted([r for r in live if r.get("change_pct") is not None], key=lambda r: r["change_pct"], reverse=True)
        n = min(8, len(srt) // 2)
        out[m] = {"gainers": srt[:n], "losers": srt[-n:][::-1] if n else []}
        out["live"] = True
    return out


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


def _display_name(ticker: str, info: dict) -> str:
    """분석 화면용 종목명. 한국 종목(.KS/.KQ)은 STOCK_DB의 한글명을 우선 쓰고,
    그 외는 yfinance 영문명(longName → shortName) → 티커 순."""
    if ticker and ticker.upper().endswith((".KS", ".KQ")):
        kr = _lookup_us_name(ticker)
        if kr and kr.upper() != ticker.upper():
            return kr
    return (info or {}).get("longName") or (info or {}).get("shortName") or ticker


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


_KR_INTEG_CACHE = {}


def fetch_naver_integration(krx_code):
    """네이버 종목 요약(integration) 한 번으로 최근 4분기(TTM) EPS·추정 EPS·BPS·주당배당금·목표가·52주 고저.
    종목 상세·스크리너·비교가 같은 값을 쓰도록 한국 PER 의 기준으로 삼는다. 10분 캐시 (빈 결과 2분)."""
    def _fetch():
        try:
            j = requests.get(f"https://m.stock.naver.com/api/stock/{krx_code}/integration",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
        except Exception:
            return {}
        def n(v):
            return safe_float(str(v).replace(",", "").replace("원", "").replace("배", "").replace("%", "").strip()) if v not in (None, "", "-", "N/A") else None
        tot = {x.get("code"): x.get("value") for x in (j.get("totalInfos") or [])}
        out = {"eps": n(tot.get("eps")), "cns_eps": n(tot.get("cnsEps")), "bps": n(tot.get("bps")),
               "dps": n(tot.get("dividend")), "high52": n(tot.get("highPriceOf52Weeks")),
               "low52": n(tot.get("lowPriceOf52Weeks")),
               "target": n((j.get("consensusInfo") or {}).get("priceTargetMean"))}
        return {k: v for k, v in out.items() if v is not None}
    return _ttl_cached(_KR_INTEG_CACHE, krx_code, 600, 120, is_empty=lambda d: not d, fetch=_fetch, max_entries=200)


def kr_per_basis(krx_code):
    """한국 종목 PER 기준 EPS: 최근 4분기(TTM) 우선, TTM 값 자체가 없을 때만 직전 확정 연간 EPS.
    → (eps, 'TTM'|'연간') | (None, 'TTM')=적자 | (None, None)=자료 없음"""
    integ = fetch_naver_integration(krx_code) or {}
    if integ.get("eps") is not None:
        # TTM 이 적자(0 이하)면 PER 은 의미가 없어 표시하지 않는다 (흑자였던 연간 값으로 바꾸지 않음)
        return (integ["eps"], "TTM") if integ["eps"] > 0 else (None, "TTM")
    annual = fetch_naver_fundamentals(krx_code) or {}
    if annual.get("eps") and annual["eps"] > 0:
        return annual["eps"], "연간"
    return None, None


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
        # 확정 다음 해 컨센서스(추정) 컬럼 → 선행 EPS (기준 연도가 분명해 Yahoo forwardPE 대신 쓴다)
        cns = sorted([t["key"] for t in title_list if t.get("isConsensus") == "Y" and t["key"] > latest_key])

        result = {}
        for row in row_list:
            title  = row.get("title", "")
            cols   = row.get("columns", {})
            if title == "EPS" and cns:
                ce = _clean_num((cols.get(cns[0]) or {}).get("value"))
                if ce and ce > 0:
                    result["cns_eps"], result["cns_year"] = ce, cns[0][:4]
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


@app.route("/stock/<path:ticker>")
def stock_page(ticker):
    """종목 분석 전용 페이지. 같은 템플릿을 쓰고 JS가 URL 경로를 보고
    홈(시황 대시보드) / 분석 모드를 전환한다.
    /stock/삼성전자 처럼 종목명으로 들어오면 STOCK_DB 에서 티커를 찾아 넘겨준다."""
    if not ticker.isascii():
        q = ticker.replace(" ", "")
        names = [(s.get("name", "").replace(" ", ""), s.get("symbol")) for s in STOCK_DB if s.get("symbol")]
        sym = next((sy for nm, sy in names if nm == q), None) \
           or next((sy for nm, sy in names if nm.startswith(q)), None)
        if sym:
            from urllib.parse import quote
            return redirect("/stock/" + quote(sym), code=302)
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


# ── 추세 셋업 (장 마감 확정 + 장중 경량 갱신, trend_setups.py) ──────────────────
import trend_setups


# 회사 로고: 키 없는 공개 로고 경로 ({symbol} 자리에 티커). 환경변수로 바꿀 수 있고 빈 값이면 이니셜 배지만.
LOGO_URL_TEMPLATE = os.environ.get("LOGO_URL_TEMPLATE", "https://financialmodelingprep.com/image-stock/{symbol}.png")


@app.context_processor
def _inject_logo_tpl():
    return {"logo_url_template": LOGO_URL_TEMPLATE}
import krx_ref


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _trends_market():
    m = (request.args.get("market") or "ALL").upper()
    return m if m in ("ALL", "KR", "US") else "ALL"


@app.route("/api/trends/setups")
def api_trends_setups():
    """장 마감 확정 셋업 + 장중 돌파 덮어쓰기. DB 조회만 (즉시 응답)."""
    return _no_store(jsonify(trend_setups.payload(_trends_market())))


@app.route("/api/trends/refresh", methods=["POST"])
def api_trends_refresh():
    """'현시점 갱신': 장이 열린 시장의 돌파 후보 + 화면 상위 셋업(200종목 이하) 현재가를 토스 실시간으로 다시 받아
    돌파 여부 갱신 (토스 실패분은 네이버/Yahoo, 시장별 15초 쿨다운)."""
    market = _trends_market()
    res = trend_setups.refresh_live(market)
    out = trend_setups.payload(market)
    out["refresh"] = res
    return _no_store(jsonify(out))


@app.route("/api/trends/scan", methods=["POST"])
def api_trends_scan():
    """소유자 진단: 유니버스 전체 재스캔(백그라운드, 40종목씩). 일반 사용자는 장 마감 확정본만 본다."""
    if not _is_toss_owner():
        return jsonify({"error": "소유자만 전체 재스캔을 실행할 수 있습니다. 셋업은 장 마감 배치로 자동 갱신됩니다."}), 403
    return jsonify(trend_setups.start_rebuild(_trends_market()))


@app.route("/api/trends/status")
def api_trends_status():
    """전체 재스캔 진행률 (분모 = 실제 유니버스 종목 수)."""
    return _no_store(jsonify(trend_setups.build_status(_trends_market())))


@app.route("/api/trends")
def api_trends():
    """(호환) 예전 경로 → 셋업 목록."""
    return _no_store(jsonify(trend_setups.payload(_trends_market())))


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


# ── 분봉(10m) · 시간봉(1h): 토스 우선 → yfinance 폴백, 짧은 메모리 캐시 ──────────────
INTRADAY_INTERVALS = ("10m", "1h")
# 분·시간봉 출처 로그 (앱 로거의 자식: LOG_LEVEL 과 앱 핸들러를 그대로 따른다)
_intra_log = app.logger.getChild("intraday")
_INTRA_CACHE = {}                         # (ticker, interval) → (ts, df, source)
_INTRA_TTL = {"10m": 60, "1h": 180}
_INTRA_LOCK = threading.Lock()


def _yf_intraday(ticker, interval):
    """yfinance 분·시간봉 (과거 구간 베이스). 1h: 3개월, 10m: 5분봉 1개월 → 10분 리샘플 (yfinance 에 10m 없음)."""
    if interval == "1h":
        ydf = yf.Ticker(ticker).history(period="3mo", interval="1h")
    else:
        ydf = yf.Ticker(ticker).history(period="1mo", interval="5m")
        if ydf is not None and not ydf.empty:
            ydf = toss_api.resample_minutes(ydf, 10, toss_api.market_of(ticker))
    if ydf is None or ydf.empty:
        return None
    ydf = ydf[["Open", "High", "Low", "Close", "Volume"]]
    tz = toss_api.EXCHANGE_TZ[toss_api.market_of(ticker)]
    return ydf.tz_convert(tz) if ydf.index.tz is not None else ydf.tz_localize("UTC").tz_convert(tz)


def merge_intraday(base, latest):
    """과거(yfinance) 위에 최신(토스 1분봉 리샘플) 구간을 덮어쓴다.
    토스 구간의 첫 봉 시각 이후의 base 봉은 모두 버리고 토스 봉으로 잇는다 → 중복 시각 없음, 오름차순."""
    if latest is None or latest.empty:
        return base
    if base is None or base.empty:
        return latest
    latest = latest.tz_convert(base.index.tz)
    cut = latest.index.min()
    out = pd.concat([base[base.index < cut], latest])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _get_intraday_df(ticker, interval):
    """분·시간봉. 토스 1분봉 리샘플(최신 3~4거래일, 한국 장 마감 동시호가·당일 실시간 포함)을
    yfinance 과거 구간 위에 덮어써 지표 워밍업 봉 수를 확보한다.
    Returns (df, source) — source: 'toss_hybrid'(병합) | 'toss'(토스만) | 'yfinance'(토스 실패) | None."""
    key = (ticker, interval)
    now = time.time()
    with _INTRA_LOCK:
        hit = _INTRA_CACHE.get(key)
        if hit and now - hit[0] < _INTRA_TTL[interval]:
            return hit[1], hit[2]
    tdf, n_min = None, 0
    if toss_api.is_enabled() and toss_api.is_eligible(ticker):
        try:
            tdf, n_min = toss_api.get_intraday_df(ticker, interval)
        except Exception as e:
            app.logger.warning(f"[intraday] toss 1m {ticker} {interval} failed: {e}")
            tdf, n_min = None, 0
    ydf = None
    try:
        ydf = _yf_intraday(ticker, interval)
    except Exception as e:
        app.logger.warning(f"[intraday] yfinance {ticker} {interval} failed: {e}")
    if tdf is not None and not tdf.empty:
        df = merge_intraday(ydf, tdf)
        src = "toss_hybrid" if ydf is not None and not ydf.empty else "toss"
    else:
        df, src = ydf, ("yfinance" if ydf is not None else None)
    _intra_log.info(f"[intraday] {ticker} {interval}: toss_1m={n_min} → resampled={0 if tdf is None else len(tdf)}, "
                    f"total={0 if df is None else len(df)}, source={src}")
    if df is not None:
        with _INTRA_LOCK:
            _INTRA_CACHE[key] = (now, df, src)
            if len(_INTRA_CACHE) > 60:                   # 메모리 보호: 오래된 것부터 정리
                for k, _ in sorted(_INTRA_CACHE.items(), key=lambda kv: kv[1][0])[:20]:
                    _INTRA_CACHE.pop(k, None)
    return df, src


def build_chart_data(ticker, interval):
    """차트 데이터만 반환 (봉 전환 시 사용).
    일·주·월봉: dates = 'YYYY-MM-DD'. 10분·1시간봉: dates = Unix 초(정수), tz = 거래소 시간대.
    chart_data["source"] 에 'toss' | 'toss_hybrid' | 'yfinance'."""
    if interval not in ("10m", "1h", "1d", "1wk", "1mo"):
        interval = "1d"

    INTERVAL_LABELS = {"10m": "10분", "1h": "1시간", "1d": "일봉", "1wk": "주봉", "1mo": "월봉"}
    intraday = interval in INTRADAY_INTERVALS

    if intraday:
        df, _src = _get_intraday_df(ticker, interval)
    else:
        # 봉별 목표 봉 수 (지표 + 충분한 히스토리)
        CHART_MIN_BARS = {"1d": 600, "1wk": 250, "1mo": 130}
        df, _src = _get_price_df(ticker, interval, CHART_MIN_BARS.get(interval, 600))
    if df is None or df.empty:
        return None, f"'{ticker}' 데이터를 찾을 수 없습니다."

    df = df.dropna(subset=["Close", "Open", "High", "Low"])
    if df.empty:
        return None, f"'{ticker}' 유효한 가격 데이터가 없습니다."

    df = add_all_indicators(df)
    tz_name = None
    if intraday:
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        tz_name = "Asia/Seoul" if ticker.upper().endswith((".KS", ".KQ")) else "America/New_York"
        df.index = [int(t.timestamp()) for t in idx]
    else:
        df.index = df.index.strftime("%Y-%m-%d")

    def series(col):
        return [_cf(v) for v in df[col]] if col in df.columns else []

    chart_data = {
        "dates":     df.index.tolist(),
        "open":      [_cf(v) for v in df["Open"]],
        "high":      [_cf(v) for v in df["High"]],
        "low":       [_cf(v) for v in df["Low"]],
        "close":     [_cf(v) for v in df["Close"]],
        "volume":    [_cv(v) for v in df["Volume"]],
        "ma5":       series("MA5"),   "ma20": series("MA20"),
        "ma60":      series("MA60"),  "ma120": series("MA120"),
        "bb_upper":  series("BB_upper"), "bb_mid": series("BB_mid"),
        "bb_lower":  series("BB_lower"),
        "tenkan":    series("tenkan"), "kijun": series("kijun"),
        "senkou_a":  series("senkou_a"), "senkou_b": series("senkou_b"),
        "rsi":       series("RSI"),
        "macd":      series("MACD"),  "macd_signal": series("MACD_signal"),
        "macd_hist": series("MACD_hist"),
        "source":    _src, "intraday": intraday, "tz": tz_name,
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
                {"chart": chart_data, "interval": interval, "interval_label": interval_label,
                 "chart_source": chart_data.get("source")},
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
    """fetch_kr_quarterly_data 의 캐시 래퍼 (6시간, 빈 결과는 10분)."""
    return _ttl_cached(
        _KR_QUARTERLY_CACHE, krx_code, _KR_QUARTERLY_TTL, _KR_EMPTY_TTL,
        is_empty=lambda d: not any((d or {}).get(k) for k in ("rev_act", "oi_act", "rev_est", "oi_est")),
        fetch=lambda: _fetch_kr_quarterly_data_uncached(krx_code),
    )


def _fetch_kr_quarterly_data_uncached(krx_code):
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

        app.logger.debug(
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

    # ── 시도 1: m.stock.naver.com/api/stock/.../finance/quarter ──
    # (2026-11 확인: 예전 경로 .../finance/quarterly 는 404, 단수형 quarter 가 정상)
    try:
        url  = f"https://m.stock.naver.com/api/stock/{krx_code}/finance/quarter"
        resp = requests.get(url, headers=_hdrs, timeout=8)
        if resp.status_code == 200:
            _parse_naver_finance(resp.json().get("financeInfo", {}))
        else:
            app.logger.warning(f"[KR qtr] mobile quarter API → {resp.status_code}")
    except Exception as e:
        app.logger.error(f"[KR qtr] mobile quarterly error: {e}")

    # 시도 1 로 발표치를 얻었으면 아래 폴백(요청 최대 5개, 수 초 소요)은 건너뛴다
    if rev_act and oi_act:
        app.logger.debug(f"[KR qtr] FINAL(api): rev_act_periods={sorted(rev_act.keys())}")
        return {
            "rev_act": rev_act, "oi_act":  oi_act,
            "rev_est": rev_est, "oi_est":  oi_est,
        }

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
                app.logger.debug(f"[KR qtr] integration: {before}→{after} periods")
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
                app.logger.debug(f"[KR qtr] wisereport try {u[-60:]} → {r3.status_code} {len(r3.text)}b")
                if r3.status_code != 200 or not r3.text:
                    continue
                soup = BeautifulSoup(r3.text, "html.parser")
                tables = soup.find_all("table")
                if not tables:
                    app.logger.debug(f"[KR qtr] wisereport no tables")
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
                    app.logger.debug(
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
        app.logger.debug(f"[KR qtr] mobile page → {rp.status_code} {len(rp.text)}b")
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
                app.logger.debug(
                    f"[KR qtr] mobile page scrape: {before}→{after} periods, "
                    f"periods={sorted(rev_act.keys())}"
                )
    except Exception as e:
        app.logger.warning(f"[KR qtr] mobile page scrape failed: {e}")

    app.logger.debug(f"[KR qtr] FINAL: rev_act_periods={sorted(rev_act.keys())}")

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


def dart_single_quarters(raw):
    """DART 손익 항목 → 분기 단독(3개월) 금액 {"YYYY-MM": KRW}. 네트워크 없음.

    raw: {(year, q): (thstrm_amount, thstrm_add_amount)}  q = 1(11013) · 2(11012) · 3(11014) · 4(11011 사업보고서)
      - 분기·반기 보고서(q=1~3): thstrm_amount = 그 분기 3개월 단독, thstrm_add_amount = 연초부터 누적.
        → 누적값이 있으면 '누적(q) - 누적(q-1)' 로 단독을 구하고(정의에 상관없이 맞는 값), 없으면 thstrm_amount 를 그대로 단독으로 쓴다.
          예전 코드는 thstrm_amount 를 누적으로 보고 직전 분기를 또 빼서
          삼성전자 영업이익이 26.Q2 89.5조 → 32.3조, 26.Q3 107.4조 → 17.9조 로 나왔다 (2026-11 수정).
      - 사업보고서(q=4): thstrm_amount = 연간 → 4분기 단독 = 연간 - 3분기 누적.
        3분기 누적을 모르면(Q1~Q3 단독 합도 없으면) 4분기는 비운다 (연간 값을 4분기로 잘못 그리지 않게).
    """
    month = {1: "03", 2: "06", 3: "09", 4: "12"}
    cum, single = {}, {}
    for (y, q), (amt, add) in raw.items():
        if q == 4:
            cum[(y, 4)] = amt
        elif q == 1:
            cum[(y, 1)] = add if add is not None else amt
            single[(y, 1)] = amt
        else:
            if add is not None:
                cum[(y, q)] = add
            single[(y, q)] = amt                     # 분·반기 보고서의 당기금액 = 3개월 단독
    for (y, q) in list(single):
        if q in (2, 3) and (y, q) in cum and (y, q - 1) in cum:
            single[(y, q)] = cum[(y, q)] - cum[(y, q - 1)]
    for (y, q), annual in [(k, v) for k, v in cum.items() if k[1] == 4]:
        q3 = cum.get((y, 3))
        if q3 is None and all((y, k) in single for k in (1, 2, 3)):
            q3 = single[(y, 1)] + single[(y, 2)] + single[(y, 3)]
        if q3 is not None:
            single[(y, 4)] = annual - q3
    return {f"{y}-{month[q]}": v for (y, q), v in single.items()}


def fetch_dart_quarterly(krx_code: str) -> dict:
    """DART API → 최근 2~3년 분기 매출·영업이익 (분기 단독 3개월 기준, 변환은 dart_single_quarters).

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
    app.logger.debug(f"[DART] {krx_code}: corp_code={corp_code}")

    rev_raw = {}   # (year, qtr_idx) → (thstrm_amount, thstrm_add_amount)
    oi_raw  = {}

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
                        add = _dart_parse_amount(item.get("thstrm_add_amount", ""))
                        if nm in ("매출액", "수익(매출액)", "영업수익", "매출") and (y, qi) not in rev_raw:
                            rev_raw[(y, qi)] = (amt, add)
                            rev_found = True
                        elif nm in ("영업이익", "영업이익(손실)") and (y, qi) not in oi_raw:
                            oi_raw[(y, qi)] = (amt, add)
                            oi_found = True
                    if rev_found or oi_found:
                        app.logger.debug(
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

    result = {
        "rev_act": dart_single_quarters(rev_raw),
        "oi_act":  dart_single_quarters(oi_raw),
    }
    app.logger.debug(
        f"[DART] {krx_code}: rev_periods={sorted(result['rev_act'].keys())[-4:]} "
        f"oi_periods={sorted(result['oi_act'].keys())[-4:]}"
    )
    return result


def fetch_kr_analysts(code):
    """fetch_kr_analysts 의 캐시 래퍼 (1시간, 빈 결과는 10분)."""
    return _ttl_cached(
        _KR_ANALYST_CACHE, code, _KR_ANALYST_TTL, _KR_EMPTY_TTL,
        is_empty=lambda d: not (d or {}).get("targets"),
        fetch=lambda: _fetch_kr_analysts_uncached(code),
    )


def _fetch_kr_analysts_uncached(code):
    """네이버 증권 리서치에서 국내 증권사 목표가·투자의견 수집.

    2026-11 확인: finance.naver.com/research/* HTML 페이지가 stock.naver.com
    (클라이언트 렌더링)으로 리다이렉트되어 예전 HTML 파싱이 동작하지 않음.
    모바일 JSON API 를 사용한다.
      1) /api/research/stock/{code}       → 종목 리포트 목록 (id, 증권사, 날짜)
      2) /api/research/company/{id}       → 리포트별 goalPrice, opinion (병렬)
      3) 수집 결과로 컨센서스 요약 계산
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _as_completed

    GRADE_MAP = {
        '강력매수': 5, '매수': 4, '비중확대': 4,
        '중립': 3, '보유': 3, '시장수익률': 3,
        '비중축소': 2, '매도': 1,
    }
    # API 의 영문 투자의견 → 화면용 한글
    OPINION_KO = {
        'strong buy': '강력매수', 'buy': '매수', 'trading buy': '매수',
        'outperform': '비중확대', 'overweight': '비중확대',
        'hold': '중립', 'neutral': '중립', 'marketperform': '시장수익률',
        'market perform': '시장수익률',
        'underperform': '비중축소', 'underweight': '비중축소', 'reduce': '비중축소',
        'sell': '매도',
        '없음': '의견 없음', 'nr': '의견 없음', 'not rated': '의견 없음',
    }
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Referer': 'https://m.stock.naver.com/',
    }

    # ── 1. 종목 리포트 목록 → (id, 증권사, 날짜), 증권사 중복 제거 최대 6개 ──
    items = []
    try:
        r = requests.get(f"https://m.stock.naver.com/api/research/stock/{code}",
                         headers=hdrs, timeout=8)
        if r.status_code != 200:
            app.logger.warning(f"[kr_analysts] list {code} → {r.status_code}")
        else:
            seen_firms = set()
            for it in r.json() or []:
                rid  = it.get('researchId')
                firm = (it.get('brokerName') or '').strip()
                if not rid or not firm or firm in seen_firms:
                    continue
                seen_firms.add(firm)
                items.append((rid, firm, it.get('writeDate') or ''))
                if len(items) >= 6:
                    break
    except Exception as e:
        app.logger.error(f"KR analyst list fetch: {e}")

    if not items:
        return {"targets": [], "summary": {}}

    # ── 2. 리포트별 목표가·투자의견 (병렬) ─────────────────────────────
    def _fetch_detail(item):
        rid, firm, date = item
        try:
            rd = requests.get(f"https://m.stock.naver.com/api/research/company/{rid}",
                              headers=hdrs, timeout=8)
            rc = (rd.json() or {}).get('researchContent') or {}
            target_price = None
            gp = str(rc.get('goalPrice') or '').replace(',', '').strip()
            if gp:
                try:
                    target_price = float(gp) or None   # 0 = 목표가 없음
                except ValueError:
                    pass
            return firm, date, target_price, (rc.get('opinion') or '').strip() or None
        except Exception:
            return firm, date, None, None

    details = []
    with _TPE(max_workers=6) as ex:
        futures = {ex.submit(_fetch_detail, it): it for it in items}
        try:
            for fut in _as_completed(futures, timeout=20):
                details.append(fut.result())
        except Exception as e:
            app.logger.warning(f"[kr_analysts] overall timeout/error: {e}")
            for fut, it in futures.items():
                if not fut.done():
                    details.append((it[1], it[2], None, None))
    details.sort(key=lambda d: d[1], reverse=True)   # 최신 리포트 먼저

    # ── 3. 결과 정리 ──────────────────────────────────────────────────
    targets = []
    for firm, date, target_price, opinion in details:
        grade = OPINION_KO.get((opinion or '').lower(), opinion)
        targets.append({
            'firm':         firm,
            'grade':        grade if grade else '의견 없음',
            'target':       target_price,
            'prior_target': None,
            'action':       '',
            'date':         date,
            'score':        GRADE_MAP.get(grade),   # 알 수 없는 의견은 None (평균에서 제외)
        })

    # ── 4. 컨센서스 요약 계산 ─────────────────────────────────────────
    summary = {}
    prices = [t['target'] for t in targets if t['target']]
    if prices:
        summary['mean'] = round(sum(prices) / len(prices))
        summary['high'] = max(prices)
        summary['low']  = min(prices)
    scores = [t['score'] for t in targets if t['score'] is not None]
    if scores:
        avg_sc = sum(scores) / len(scores)
        summary['recommendation'] = (
            'strong_buy' if avg_sc >= 4.5 else
            'buy'        if avg_sc >= 3.5 else
            'sell'       if avg_sc <  2.5 else 'hold'
        )
    if targets:
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


def extended_hours_flag(current_price, regular_close, live, market_open, is_korean, session=None):
    """상단 현재가가 정규장 종가가 아닌 시간외(프리·애프터마켓, 국내 시간외·대체거래소) 체결가인가.
    live: 현재가가 실시간 시세(토스·네이버·Yahoo)이거나 일봉 끝에 붙은 시간외 봉에서 왔는가.
    정규장이 아닐 때 정규장 종가와 0.05% 이상이면서 최소 단위(1원 / 0.01달러) 이상 다르면 True.
    토스 응답에 세션 필드가 있으면(현재 토스 시세 응답에는 없음) 'extended' 는 작은 차이도 True, 'regular' 는 False."""
    if not live or market_open or not current_price or not regular_close:
        return False
    diff = abs(current_price - regular_close)
    if session == "regular":
        return False
    if session == "extended":
        return diff > 1e-9
    unit = 1.0 if is_korean else 0.01
    return diff >= unit - 1e-9 and diff / regular_close * 100 >= 0.05


def last_regular_session_date(is_korean, now=None):
    """가장 최근에 '끝난' 정규장 날짜 (시장 현지). 주말은 건너뛰고 공휴일은 모른다
    (공휴일엔 그날 봉이 없으니 아래 비교에서 문제되지 않는다)."""
    tz = "Asia/Seoul" if is_korean else "America/New_York"
    now = now if now is not None else pd.Timestamp.now(tz=tz)
    close_t = pd.Timestamp("15:30").time() if is_korean else pd.Timestamp("16:00").time()
    if now.weekday() < 5 and now.time() >= close_t:
        return now.date()
    d = now.normalize() - pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d.date()


def regular_session_close(df, is_korean, market_open, now=None):
    """일봉에서 '끝난 정규장'의 종가를 찾는다. 토스 일봉은 프리마켓·주간거래 체결로 다음 거래일 봉을 미리 만들 때가 있어
    마지막 봉 종가가 시간외 가격일 수 있다. → (정규장 종가, 그 전 종가, 끝에 시간외 봉이 붙었나, 정규장 봉 시각)"""
    n = len(df)
    last = (safe_float(df["Close"].iloc[-1]), safe_float(df["Close"].iloc[-2]) if n >= 2 else None, False, df.index[-1])
    if market_open or n == 0:
        return last
    try:
        sess = last_regular_session_date(is_korean, now)
        tz = "Asia/Seoul" if is_korean else "America/New_York"
        idx = df.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert(tz)
        dates = [ts.date() for ts in idx]
        i = max((k for k, d in enumerate(dates) if d <= sess), default=None)
        if i is None or i == n - 1:
            return last
        return (safe_float(df["Close"].iloc[i]), safe_float(df["Close"].iloc[i - 1]) if i >= 1 else None, True, df.index[i])
    except Exception:
        return last


_CTX_REUSE_MAX_AGE = 12 * 3600    # 다른 화면이 받아 둔 캐시는 12시간까지 재사용 (주말·장 마감 뒤에도 마지막 값)


def _cached_quote(label_or_symbol):
    """이미 메모리에 있는 지수·환율 값만 찾는다 (외부 호출 없음).
    순서: 종목 상세 시장 컨텍스트 캐시(KR/US) → 홈 시황 대시보드 캐시. → {value, change_pct} | None"""
    now = time.time()
    names = {"S&P 500": ("S&P 500", "^GSPC"), "VIX": ("VIX", "^VIX"), "USD/KRW": ("환율", "KRW=X")}
    label, sym = names.get(label_or_symbol, (label_or_symbol, None))
    for key in ("US", "KR"):
        hit = _MARKET_CACHE.get(key)
        if hit and (now - hit[0]) < _CTX_REUSE_MAX_AGE:
            v = hit[1].get(label)
            if v and v.get("value") is not None:
                return {"value": v["value"], "change_pct": v.get("change_pct")}
    ov = _MARKET_OVERVIEW_CACHE
    if ov.get("data") and (now - ov["ts"]) < _CTX_REUSE_MAX_AGE:
        for it in ov["data"].get("indices") or []:
            if sym and it.get("symbol") == sym and it.get("value") is not None:
                return {"value": it["value"], "change_pct": it.get("change_pct")}
    return None


def _stock_sector_context(ticker, market):
    """섹터맵 스냅샷(메모리 → DB)에서 이 종목이 속한 섹터의 시총가중 등락률·상승/하락 수. 미매칭이면 None."""
    try:
        meta = (sector_map.universe().get(market) or {}).get(ticker)
        if not meta or not meta.get("sector"):
            return None
        snap = sector_map.get(market)
        if not snap:
            return None
        g = next((g for g in snap.get("groups") or [] if g.get("key") == meta["sector"]), None)
        if not g:
            return None
        return {"kind": "sector", "name": g.get("name"), "change_pct": g.get("change_pct"),
                "advancing": g.get("advancing_count"), "declining": g.get("declining_count"),
                "total": g.get("total_count"), "data_date": snap.get("data_date")}
    except Exception as e:
        app.logger.debug(f"[market_ctx] sector {ticker}: {e}")
        return None


def build_market_context(ticker, is_korean, stock_change_pct=None):
    """종목 상세 '시장 컨텍스트' 5줄 + 규칙 기반 '시장 분위기 요약'.
    기본 3줄은 fetch_market_context(10분 캐시), 추가 2줄은 이미 메모리·DB에 있는 값만 쓴다.
      - 미국 종목: S&P 500 · Nasdaq · VIX + USD/KRW 환율 + 소속 섹터
      - 한국 종목: KOSPI · KOSDAQ · 환율 + S&P 500 + 소속 섹터
    → (rows: {label: {...}}, mood: {tone, title, text} | None)"""
    base = fetch_market_context(is_korean)
    rows = dict(base)                      # 캐시 dict 를 건드리지 않게 복사
    if is_korean:
        sp = _cached_quote("S&P 500")
        if sp:
            rows["S&P 500"] = {"symbol": "^GSPC", **sp}
    else:
        fx = _cached_quote("USD/KRW")
        if not fx and _FX_CACHE.get("rate") and (time.time() - _FX_CACHE["ts"]) < _CTX_REUSE_MAX_AGE:
            fx = {"value": round(_FX_CACHE["rate"], 2), "change_pct": None}     # 모의투자 환율 캐시 (등락률 없음)
        if fx:
            rows["USD/KRW 환율"] = {"symbol": "KRW=X", "kind": "fx", **fx}
    sec = _stock_sector_context(ticker, "KR" if is_korean else "US")
    if sec:
        rows["소속 섹터"] = sec
    vix = (rows.get("VIX") or _cached_quote("VIX") or {}).get("value")
    if is_korean:
        idx_label = "KOSDAQ" if ticker.endswith(".KQ") else "KOSPI"
    else:
        idx_label = "S&P 500"
    idx = (rows.get(idx_label) or {}).get("change_pct")
    mood = market_mood(vix, idx_label, idx, sec, stock_change_pct)
    # 국채 금리·금·달러 흐름 1줄 (홈 시황 캐시 재사용)
    try:
        m = get_macro_snapshot(is_korean)
        line = macro_mod.macro_line(m.get("tnx"), m.get("gold"), m.get("dxy"), m.get("regime"))
    except Exception:
        line = None
    if line:
        mood = dict(mood) if mood else {"tone": "neutral", "title": "시장 분위기", "text": ""}
        mood["macro"] = line
        mood["macro_regime"] = (m.get("regime") or {}).get("regime")
    return rows, mood


def market_mood(vix, idx_label, idx_chg, sector, stock_chg):
    """VIX · 대표 지수 · 소속 섹터 · 종목 등락을 조합한 1~2줄 요약 (규칙 기반, AI 호출 없음).
    tone: danger | caution | good | info | neutral"""
    sec_chg = sector.get("change_pct") if sector else None
    sec_name = (sector or {}).get("name")
    f = lambda v: f"{'+' if v > 0 else ''}{v:.2f}%"
    if vix is not None and vix >= 30:
        return {"tone": "danger", "title": f"변동성 경계 (VIX {vix:.2f})",
                "text": "시장 전반 약세 구간, 신규 진입·추격 매수 주의"}
    if idx_chg is None:
        return None
    up, down = idx_chg >= 0.3, idx_chg <= -0.3
    strong = (sec_chg is not None and sec_chg > 0) or (sec_chg is None and stock_chg is not None and stock_chg > 0)
    if down and strong:
        who = f"소속 섹터({sec_name} {f(sec_chg)})" if sec_chg is not None else f"종목({f(stock_chg)})"
        return {"tone": "info", "title": "시장 대비 상대 강세",
                "text": f"{idx_label} {f(idx_chg)} 약세장 속에서도 {who} 방어력 양호"}
    if vix is not None and vix >= 20 and down:
        return {"tone": "caution", "title": f"변동성 확대 (VIX {vix:.2f})",
                "text": f"{idx_label} {f(idx_chg)} 약세, 분할 접근·손절 기준 점검"}
    if up and sec_chg is not None and sec_chg > 0:
        return {"tone": "good", "title": "우호적 시장 환경",
                "text": f"지수({idx_label} {f(idx_chg)})와 소속 섹터({sec_name} {f(sec_chg)})가 함께 강세 흐름"}
    if down and sec_chg is not None and sec_chg < 0:
        return {"tone": "caution", "title": "동반 약세",
                "text": f"지수({idx_label} {f(idx_chg)})와 소속 섹터({sec_name} {f(sec_chg)})가 함께 하락, 보수적 접근"}
    if up and sec_chg is not None and sec_chg < 0:
        return {"tone": "neutral", "title": "섹터 소외",
                "text": f"지수({idx_label} {f(idx_chg)})는 올랐지만 소속 섹터({sec_name} {f(sec_chg)})는 약세"}
    if up:
        return {"tone": "good", "title": "지수 강세", "text": f"{idx_label} {f(idx_chg)} 상승 흐름"}
    if down:
        return {"tone": "caution", "title": "지수 약세", "text": f"{idx_label} {f(idx_chg)} 하락, 보수적 접근"}
    return {"tone": "neutral", "title": "보합권", "text": f"{idx_label} {f(idx_chg)}, 뚜렷한 방향성 없는 시장"}


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
            app.logger.debug(f"[events] get_earnings_dates → next={next_earnings_ts}")
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
                    app.logger.debug(f"[events] calendar → next={next_earnings_ts}")
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
            app.logger.debug(f"[events] info → next={next_earnings_ts}")
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


_KR_NEWS_CACHE = {}
_KR_NEWS_TTL = 600        # 10분


def fetch_kr_news(krx_code, limit=8):
    """한국 종목 뉴스: 네이버 모바일 API (한글 기사라 번역 불필요). 10분 캐시.

    yfinance 1.3.0 의 stock.news 는 Yahoo 가 /xhr/ncp 를 폐기해 404 로 항상 빈 목록이다.
    """
    return _ttl_cached(
        _KR_NEWS_CACHE, krx_code, _KR_NEWS_TTL, 120,
        is_empty=lambda v: not v,
        fetch=lambda: _fetch_kr_news_uncached(krx_code, limit),
    )


def _fetch_kr_news_uncached(krx_code, limit=8):
    import html as _html
    try:
        r = requests.get(
            f"https://m.stock.naver.com/api/news/stock/{krx_code}?pageSize={limit}&page=1",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"},
            timeout=8,
        )
        if r.status_code != 200:
            app.logger.warning(f"[kr_news] {krx_code} → {r.status_code}")
            return []
        out = []
        for group in r.json() or []:
            # 같은 사건의 기사 묶음 → 대표 기사 1건만
            items = (group or {}).get("items") or []
            if not items:
                continue
            it = items[0]
            title = _html.unescape(re.sub(r"<[^>]+>", "", it.get("titleFull") or it.get("title") or "")).strip()
            if not title:
                continue
            desc = _html.unescape(re.sub(r"<[^>]+>", "", it.get("body") or "")).strip()[:200]
            dt = str(it.get("datetime") or "")          # YYYYMMDDHHMM
            pub = f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}" if len(dt) >= 12 else ""
            url = it.get("mobileNewsUrl") or (
                f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}"
                if it.get("officeId") and it.get("articleId") else "")
            out.append({"title": title, "title_orig": title, "desc": desc, "pub": pub,
                        "url": url, "provider": it.get("officeName") or ""})
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        app.logger.warning(f"[kr_news] {krx_code}: {e}")
        return []


# 느린 외부 호출(AI 등)을 시간 예산 안에서만 기다리기 위한 공용 실행기.
# 예산을 넘긴 작업은 버리지 않고 끝까지 돌아 각자의 캐시를 채운다 → 다음 조회에서 바로 쓰인다.
from concurrent.futures import ThreadPoolExecutor as _BgTPE
_BG_EXEC = _BgTPE(max_workers=6, thread_name_prefix="bg-extra")


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

        # 제목 + 설명 번역 (AI 일괄 → Google → 원문)
        # 번역은 종목 통합 AI 호출(get_stock_insight)에서 기사별로 한 번만 하고 영구 캐시한다
        titles, descs = [r["title"] for r in raw], [r["desc"] for r in raw]

        return [
            {**r, "title": titles[i], "title_orig": r["title"], "desc": descs[i]}
            for i, r in enumerate(raw)
        ]
    except Exception:
        return []



# ── DCF 재무제표 캐시 ──────────────────────────────────────
# 재무제표는 분기당 1회만 갱신되므로 길게 캐시. DCF 계산 자체는 순수 수학이라
# 매 요청마다 실시간 가격으로 재계산한다(역산 성장률이 현재가를 반영해야 함).
_FIN_CACHE = {}                   # ticker → (timestamp, income_stmt, cashflow, balance_sheet)
_FIN_TTL   = 60 * 60 * 6          # 6시간

def _get_financials(ticker, stock):
    """연간 재무제표 3종 (6시간 캐시). Returns (income_stmt, cashflow, balance_sheet)."""
    now = time.time()
    c = _FIN_CACHE.get(ticker)
    if c and (now - c[0]) < _FIN_TTL:
        return c[1], c[2], c[3]
    inc = cf = bs = None
    try:
        inc = stock.income_stmt
    except Exception:
        pass
    try:
        cf = stock.cashflow
    except Exception:
        pass
    try:
        bs = stock.balance_sheet
    except Exception:
        pass
    _FIN_CACHE[ticker] = (now, inc, cf, bs)
    return inc, cf, bs


_CONS_G_CACHE = {}


def _get_consensus_rev_growth(ticker, stock):
    """애널리스트 '내년(+1y)' 매출 성장률 컨센서스(소수, 6시간 캐시). 없으면 None.

    info.revenueGrowth 는 최근 분기 실적의 전년 대비 성장률이라 컨센서스가 아니다.
    """
    now = time.time()
    c = _CONS_G_CACHE.get(ticker)
    if c and (now - c[0]) < _FIN_TTL:
        return c[1]
    g = None
    try:
        re_df = stock.revenue_estimate
        if re_df is not None and not re_df.empty and "growth" in re_df.columns:
            for row in ("+1y", "0y"):
                if row in re_df.index:
                    g = safe_float(re_df.loc[row, "growth"])
                    if g is not None:
                        break
    except Exception:
        g = None
    _CONS_G_CACHE[ticker] = (now, g)
    return g


_RF_CACHE = {"ts": 0.0, "value": None}
_RF_TTL   = 60 * 60 * 12          # 12시간

def _get_risk_free_rate(currency):
    """무위험수익률. 미국은 홈 시황 캐시의 실시간 10년물(^TNX / 100, 2%~7% 클램핑), 없으면 12시간 캐시 조회, 그래도 없으면 4.5%.
    한국은 yfinance 미제공 → None(모듈 기본가정 사용)."""
    if currency == "KRW":
        return None
    tnx = (get_macro_snapshot(False) or {}).get("tnx")
    if tnx and tnx.get("value"):
        return macro_mod.risk_free_from_tnx(tnx)[0]
    now = time.time()
    if _RF_CACHE["value"] and (now - _RF_CACHE["ts"]) < _RF_TTL:
        return _RF_CACHE["value"]
    try:
        h = yf.Ticker("^TNX").history(period="5d")
        if h is not None and not h.empty:
            v = float(h["Close"].iloc[-1]) / 100
            if 0.001 < v < 0.15:
                v = macro_mod.risk_free_from_tnx(v * 100)[0]
                _RF_CACHE.update(ts=now, value=v)
                return v
    except Exception:
        pass
    return _RF_CACHE["value"] or macro_mod.RF_DEFAULT


# ── Groq 응답 캐시 (rate limit 회피) ──

# ── AI 모델 선택 ──────────────────────────────────────────────────────────────
# 모델 이름을 코드에 고정하면 제공사가 모델을 종료할 때 AI 기능이 조용히 전부 멈춘다
# (2026-08 Groq Llama 3.x 종료, Gemini 2.5 도 종료 일정). 그래서
#   1) 환경변수 GEMINI_MODELS / GROQ_MODELS (콤마 구분) 가 있으면 그대로 쓰고
#   2) 없으면 각 제공사의 모델 목록 API 로 "지금 쓸 수 있는" 모델을 골라 6시간 캐시하고
#   3) 목록 조회도 실패하면 아래 기본 목록을 쓴다.
# 요약·번역·짧은 해설 위주라 빠르고 무료 한도가 넉넉한 Flash-Lite 를 먼저 쓴다
_GEMINI_FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest",
                           "gemini-2.5-flash-lite", "gemini-2.5-flash"]
_GROQ_FALLBACK_MODELS   = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
_AI_MODEL_CACHE = {}          # provider → (ts, [models], source)
_AI_MODEL_TTL = 6 * 3600
_AI_LAST_ERROR = {}           # provider → (ts, message)  (진단용, 키 값은 넣지 않음)


_AI_LAST_OK = {}              # provider → ts (마지막 성공)


def _ai_note_error(provider, msg):
    _AI_LAST_ERROR[provider] = (time.time(), str(msg)[:300])


def _ai_note_ok(provider):
    _AI_LAST_OK[provider] = time.time()


def _ai_error_kind(msg):
    """오류 문구 → 화면에 보여줄 분류 (키 값·원문은 노출하지 않는다)."""
    m = (msg or "").lower()
    if "http 401" in m or "http 403" in m or "api key" in m or "api_key" in m or "permission" in m:
        return "auth"
    if "http 429" in m or "quota" in m or "rate" in m or "exhausted" in m:
        return "quota"
    if "http 404" in m or "not found" in m or "decommission" in m or "deprecat" in m:
        return "model"
    if "timeout" in m or "connection" in m or "proxy" in m:
        return "network"
    return "unknown"


def _ai_status():
    """화면용 AI 상태 요약: ok / no_key / auth / quota / model / network / unknown."""
    providers = [p for p, env in (("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY"))
                 if os.environ.get(env, "").strip()]
    if not providers:
        return {"state": "no_key", "providers": []}
    last_ok = max((_AI_LAST_OK.get(p, 0) for p in providers), default=0)
    last_err = max((_AI_LAST_ERROR.get(p, (0, ""))  for p in providers), key=lambda x: x[0], default=(0, ""))
    if last_ok and last_ok >= last_err[0]:
        return {"state": "ok", "providers": providers}
    if not last_err[0]:
        return {"state": "ok", "providers": providers}      # 아직 호출 기록 없음
    return {"state": _ai_error_kind(last_err[1]), "providers": providers}


def _pick_gemini_models(names):
    """정식 Flash 계열(gemini-<버전>-flash[-lite])만. Flash-Lite 우선(빠르고 한도 넉넉) → 같은 종류 안에선 최신 버전 먼저."""
    picked = []
    for n in names:
        m = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", n)
        if m:
            picked.append((0 if m.group(2) else 1, -float(m.group(1)), n))
    picked.sort()
    out = [n for _, _, n in picked][:3]
    # 별칭(-latest)이 있으면 마지막 보험으로
    out += [n for n in ("gemini-flash-lite-latest", "gemini-flash-latest") if n in names and n not in out]
    return out


def _pick_groq_models(ids):
    """대화용 모델만, 큰 모델 우선."""
    ids = [i for i in ids if not re.search(r"whisper|guard|tts|playai|compound|prompt|embed|vision|orpheus", i, re.I)]
    order = [r"gpt-oss-120b", r"qwen", r"llama.*70b", r"llama-4", r"gpt-oss-20b", r"llama.*(8b|instant)"]
    out = []
    for pat in order:
        for i in ids:
            if re.search(pat, i, re.I) and i not in out:
                out.append(i)
    return out[:3]


def _ai_models(provider, api_key):
    env = os.environ.get("GEMINI_MODELS" if provider == "gemini" else "GROQ_MODELS", "").strip()
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    hit = _AI_MODEL_CACHE.get(provider)
    if hit and (time.time() - hit[0]) < _AI_MODEL_TTL:
        return hit[1]
    models, source = [], "fallback"
    try:
        if provider == "gemini":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": api_key, "pageSize": 200}, timeout=10)
            if r.status_code == 200:
                names = [m.get("name", "").split("/", 1)[-1] for m in r.json().get("models", [])
                         if "generateContent" in (m.get("supportedGenerationMethods") or [])]
                models = _pick_gemini_models(names)
            else:
                _ai_note_error(provider, f"models list HTTP {r.status_code}: {r.text[:150]}")
        else:
            r = requests.get("https://api.groq.com/openai/v1/models",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            if r.status_code == 200:
                ids = [m.get("id", "") for m in r.json().get("data", []) if m.get("active", True)]
                models = _pick_groq_models(ids)
            else:
                _ai_note_error(provider, f"models list HTTP {r.status_code}: {r.text[:150]}")
        if models:
            source = "discovered"
    except Exception as e:
        _ai_note_error(provider, f"models list {type(e).__name__}: {e}")
    if not models:
        models = list(_GEMINI_FALLBACK_MODELS if provider == "gemini" else _GROQ_FALLBACK_MODELS)
    _AI_MODEL_CACHE[provider] = (time.time(), models, source)
    app.logger.info(f"[ai] {provider} models ({source}): {models}")
    return models


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


# ── AI 공급자 호출 (공통) ────────────────────────────────────────────────────
# 순서: Gemini(주력) → Groq → (선택) OpenAI 호환 백업(AI_BACKUP_*). 한도(429)·5xx·타임아웃이면 다음으로.
# 429 를 받은 모델은 다시 두드리지 않는다: 분당 한도면 잠시, 일일 한도면 리셋 시각까지 건너뛴다.
# (예전엔 429 마다 같은 모델 2번 재시도 + 모델 4개 순회 → 논리 호출 1번이 실제 요청 최대 8번이었다)
_AI_COOLDOWN = {}           # (provider, model) → (until_ts, status)
_AI_LAST_MODEL = threading.local()   # 이 스레드에서 마지막으로 성공한 "provider/model" (캐시 model_used 기록용)
_AI_COOLDOWN_LOCK = threading.Lock()


def _ai_log(ticker, task, cache_hit, provider, model, latency_ms, status):
    """원인 추적용 한 줄 로그. 분당(429_rpm)/일일(429_rpd) 한도를 구분해 남긴다."""
    app.logger.info(f"[AI] ticker={ticker or '-'} task={task} cache_hit={'true' if cache_hit else 'false'} "
                    f"provider={provider} model={model or '-'} latency_ms={int(latency_ms)} status={status}")


def _next_pacific_midnight_ts():
    """Gemini 일일 한도는 미국 태평양 자정에 리셋된다."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import timedelta as _td
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        nxt = (now + _td(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        return nxt.timestamp()
    except Exception:
        return time.time() + 3600


def _classify_429(body, headers):
    """429 응답 → ('429_rpd' | '429_rpm', 쉬어야 할 초)."""
    t = (body or "").lower()
    daily = any(k in t for k in ("perday", "per day", "per_day", "requests per day", "(rpd)", "rpd", "daily"))
    if daily:
        return "429_rpd", None
    try:
        ra = float((headers or {}).get("retry-after") or 0)
    except (TypeError, ValueError):
        ra = 0
    return "429_rpm", max(20.0, min(ra or 60.0, 300.0))


def _cooldown_set(provider, model, status, seconds=None, until=None):
    with _AI_COOLDOWN_LOCK:
        _AI_COOLDOWN[(provider, model)] = (until or time.time() + (seconds or 60), status)


def _cooldown_active(provider, model):
    with _AI_COOLDOWN_LOCK:
        hit = _AI_COOLDOWN.get((provider, model))
        if hit and hit[0] > time.time():
            return hit[1]
        return None


def _gemini_once(api_key, model, system_msg, user_msg, max_tokens, temperature, json_mode, timeout):
    """Gemini 1회 호출 → (text, status, http_status, body, headers)."""
    gen_cfg = {"temperature": temperature, "maxOutputTokens": max(max_tokens, 256),
               "thinkingConfig": {"thinkingBudget": 0}}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    for _ in range(2):      # thinkingConfig 를 모르는 모델이면 빼고 한 번 더
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={"system_instruction": {"parts": [{"text": system_msg}]},
                  "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
                  "generationConfig": gen_cfg},
            timeout=timeout)
        if resp.status_code == 400 and "thinking" in resp.text.lower() and "thinkingConfig" in gen_cfg:
            gen_cfg.pop("thinkingConfig")
            continue
        break
    if resp.status_code != 200:
        return None, None, resp.status_code, resp.text[:400], resp.headers
    data = resp.json()
    cands = data.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    text = "".join(p.get("text", "") for p in parts).strip()
    return (text or None), ("ok" if text else "error"), 200, "" if text else str(data)[:300], resp.headers


def _openai_compat_once(base_url, api_key, model, system_msg, user_msg, max_tokens, temperature,
                        json_mode, timeout):
    """OpenAI 호환 Chat Completions 1회 호출 (Groq / 백업 공급자)."""
    is_reasoning = "gpt-oss" in model
    body = {"model": model,
            "messages": [{"role": "system", "content": system_msg},
                         {"role": "user", "content": user_msg}],
            "temperature": temperature,
            # 추론 모델은 생각 토큰도 출력 한도에 포함 → 넉넉히
            "max_tokens": max_tokens * 3 if is_reasoning else max_tokens}
    if is_reasoning:
        body["reasoning_effort"] = "low"
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for _ in range(3):      # 모르는 선택 파라미터면 빼고 재시도
        resp = requests.post(f"{base_url.rstrip('/')}/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                             json=body, timeout=timeout)
        low = resp.text.lower() if resp.status_code == 400 else ""
        if "reasoning" in low and "reasoning_effort" in body:
            body.pop("reasoning_effort"); continue
        if "response_format" in low and "response_format" in body:
            body.pop("response_format"); continue
        break
    if resp.status_code != 200:
        return None, None, resp.status_code, resp.text[:400], resp.headers
    data = resp.json()
    ch = data.get("choices") or []
    text = ((ch[0].get("message") or {}).get("content") or "").strip() if ch else ""
    return (text or None), ("ok" if text else "error"), 200, "" if text else str(data)[:300], resp.headers


def _ai_providers():
    """설정된 공급자 목록 [(이름, 호출함수, 모델들)]."""
    out = []
    g = os.environ.get("GEMINI_API_KEY", "").strip()
    if g:
        out.append(("gemini", lambda m, *a: _gemini_once(g, m, *a), _ai_models("gemini", g)))
    q = os.environ.get("GROQ_API_KEY", "").strip()
    if q:
        out.append(("groq", lambda m, *a: _openai_compat_once("https://api.groq.com/openai/v1", q, m, *a),
                    _ai_models("groq", q)))
    bu, bk = os.environ.get("AI_BACKUP_BASE_URL", "").strip(), os.environ.get("AI_BACKUP_API_KEY", "").strip()
    bm = [m.strip() for m in os.environ.get("AI_BACKUP_MODEL", "").split(",") if m.strip()]
    if bu and bk and bm:
        out.append(("backup", lambda m, *a: _openai_compat_once(bu, bk, m, *a), bm))
    return out


def _ai_chat(system_msg, user_msg, max_tokens=800, temperature=0.4, label="generic",
             ticker=None, json_mode=False, timeout=25):
    """모든 AI 기능의 공통 진입점. 공급자/모델을 차례로 시도하고 텍스트(또는 None)를 돌려준다."""
    task = label.split(":")[0]
    for provider, call, models in _ai_providers():
        for model in models:
            if _cooldown_active(provider, model):
                continue
            t0 = time.time()
            try:
                text, status, http, body, headers = call(model, system_msg, user_msg, max_tokens,
                                                         temperature, json_mode, timeout)
            except requests.exceptions.Timeout:
                _ai_log(ticker, task, False, provider, model, (time.time() - t0) * 1000, "error")
                _ai_note_error(provider, f"{model} timeout")
                _cooldown_set(provider, model, "error", seconds=30)
                continue
            except Exception as e:
                _ai_log(ticker, task, False, provider, model, (time.time() - t0) * 1000, "error")
                _ai_note_error(provider, f"{model} {type(e).__name__}: {e}")
                continue
            ms = (time.time() - t0) * 1000
            if status == "ok":
                _ai_log(ticker, task, False, provider, model, ms, "ok")
                _ai_note_ok(provider)
                _AI_LAST_MODEL.v = f"{provider}/{model}"
                return text if json_mode else _strip_hanja(text, label=label)
            if http == 429:
                kind, wait = _classify_429(body, headers)
                _ai_log(ticker, task, False, provider, model, ms, kind)
                _ai_note_error(provider, f"{model} HTTP 429 {'quota per day' if kind == '429_rpd' else 'rate per minute'}")
                if kind == "429_rpd":
                    _cooldown_set(provider, model, kind,
                                  until=_next_pacific_midnight_ts() if provider == "gemini" else time.time() + 3600)
                else:
                    _cooldown_set(provider, model, kind, seconds=wait)
                continue
            _ai_log(ticker, task, False, provider, model, ms, "error")
            _ai_note_error(provider, f"{model} HTTP {http}: {(body or '')[:150]}")
            if http == 404:                      # 모델 종료 → 오래 건너뛰고 목록 다시 조회
                _cooldown_set(provider, model, "error", seconds=6 * 3600)
                _AI_MODEL_CACHE.pop(provider, None)
            elif http and http >= 500:
                _cooldown_set(provider, model, "error", seconds=30)
    return None


def _ai_json(system_msg, user_msg, max_tokens, label, ticker=None, temperature=0.3):
    """JSON 객체를 돌려받는 호출. 파싱 실패면 None."""
    text = _ai_chat(system_msg, user_msg, max_tokens=max_tokens, temperature=temperature,
                    label=label, ticker=ticker, json_mode=True)
    if not text:
        return None
    try:
        body = text[text.index("{"): text.rindex("}") + 1]
        return json.loads(body)
    except Exception as e:
        app.logger.warning(f"[AI] ticker={ticker or '-'} task={label} JSON 파싱 실패: {e}")
        return None


_PROFILE_TTL = 30 * 24 * 3600     # 기업 소개는 자주 안 바뀐다 → 30일 (DB 영구 캐시)


def fetch_company_overview(ticker, name, info, revenue_quarters, currency):
    """기업 소개·주요 사업·분석 (AI). DB 캐시 30일 + 같은 종목 동시 생성 1회로 제한."""
    key = f"company_profile:{ticker}"
    t0 = time.time()
    hit = ai_cache.get(key)
    if hit:
        _ai_log(ticker, "company_profile", True, "cache", hit[1], (time.time() - t0) * 1000, "ok")
        return hit[0]

    def _make():
        again = ai_cache.get(key)               # 기다리는 사이 다른 요청이 만들었으면 그대로
        if again:
            return again[0]
        result = _generate_company_overview(ticker, name, info, revenue_quarters, currency)
        if result:
            ai_cache.put(key, ticker, "company_profile", result, getattr(_AI_LAST_MODEL, "v", None), _PROFILE_TTL)
        return result

    return ai_cache.single_flight(key, _make, wait_timeout=30)


def _generate_company_overview(ticker, name, info, revenue_quarters, currency):
    now = time.time()
    if not (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GROQ_API_KEY", "").strip()):
        app.logger.warning(f"[company_overview] {ticker}: GEMINI_API_KEY / GROQ_API_KEY 미설정")
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

    # 국내 종목은 "한국의 애플+인텔" 같은 해외 기업 비유가 오히려 어색하다 → 비유 지시를 빼고 실제 사업으로 설명
    _is_kr = ticker.upper().endswith((".KS", ".KQ"))
    intro_rule = (
        "- 핵심 비즈니스 한 줄 정의. 국내 기업이므로 다른 기업(특히 해외 기업)에 빗대는 비유는 쓰지 말고 실제 제품·서비스로 설명\n"
        "- 주요 시장 (내수/수출 비중이나 주요 고객)"
        if _is_kr else
        "- 핵심 비즈니스 한 줄 정의 + 한국 투자자에게 친숙한 유사 기업/서비스로 비유 (예: 월마트 = 이마트+홈플러스+코스트코, Visa = 비씨카드 결제망 글로벌판)\n"
        "- 본사 위치와 주요 시장 (미국/글로벌/특정 지역)"
    )
    analogy_rule = (
        "4) 비유 대신 실제 사업 내용과 숫자로 설명 (국내 기업을 해외 기업에 빗대지 말 것)."
        if _is_kr else
        "4) 한국 투자자가 직관적으로 이해할 비유 적극 활용."
    )

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
{intro_rule}
- 위상감을 알 수 있는 한 줄 (시가총액 순위·점유율·매출 규모 중 가장 인상적인 것)

[주요사업]
3~4개의 핵심 매출원. 각 항목은 다음 형식의 bullet:
"• [사업부문명]: 1문장 설명 (가능하면 매출 비중 % 또는 규모 명시)"
나열이 아니라 비즈니스 모델이 한눈에 보이도록.

[기업분석]
4~5문장. 다음 4가지를 모두 다루되, **위에 주어진 숫자(매출 추이, PER 등)를 직접 인용**해 구체적으로 작성:
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
{analogy_rule}
5) 각 섹션은 정확한 레이블 [기업소개] / [주요사업] / [기업분석] 로 시작.
6) 불필요한 서론·결론·인사말 없이 바로 본문."""

    text = _ai_chat(
        system_msg=(
            "당신은 한국 투자자를 위한 시니어 주식 애널리스트입니다. "
            "반드시 순수 한글로만 답변하며, 한자(漢字)는 절대 사용하지 않습니다. "
            "한자어로 표현되는 모든 단어(倉庫·賣出·投資·經濟 등)도 반드시 한글(창고·매출·투자·경제)로 풀어 씁니다. "
            "영문 약어와 회사명만 영어 그대로 사용 가능합니다."
        ),
        user_msg=prompt,
        max_tokens=900,
        temperature=0.35,
        label="company_profile",
        ticker=ticker,
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
    return sections if sections else {"기업소개": text}


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


def _position_for(ticker, stock_data, current_price, ai_result):
    """분석 응답의 '내 포지션' (로그인 사용자별). 분석 캐시와 분리해 요청마다 계산한다."""
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
                "portfolio":        _portfolio_weight(holding, current_price, correct_currency),
            }
    return position_data


def _portfolio_weight(holding, current_price, currency):
    """내 포트폴리오 안에서 이 종목의 평가금 비중(%). 외부 시세는 부르지 않는다.
    이 종목은 분석 현재가, 다른 종목은 스크리너 스냅샷 가격(섹터맵이 장중 15분마다 갱신)을 쓰고,
    스냅샷에 없으면 매입가로 본다. 통화가 섞여 있으면 USD를 환율(10분 캐시)로 원화 환산한다."""
    try:
        rows = Holding.query.filter_by(user_id=holding.user_id).all()
        if not rows:
            return None
        if len(rows) == 1:
            return {"weight_pct": 100.0, "count": 1, "estimated": 0}
        others = [h.ticker for h in rows if h.id != holding.id]
        snap = dict(db.session.query(ScreenerSnapshot.ticker, ScreenerSnapshot.price)
                    .filter(ScreenerSnapshot.ticker.in_(others), ScreenerSnapshot.price.isnot(None)).all())
        curs = {("KRW" if (h.ticker.endswith(".KS") or h.ticker.endswith(".KQ")) else (h.currency or "USD")) for h in rows}
        fx = _get_usd_krw_rate() if len(curs) > 1 else 1.0
        total = mine = 0.0
        estimated = 0
        for h in rows:
            cur = "KRW" if (h.ticker.endswith(".KS") or h.ticker.endswith(".KQ")) else (h.currency or "USD")
            if h.id == holding.id:
                price = current_price or h.purchase_price
            else:
                price = snap.get(h.ticker)
                if not price:
                    price = h.purchase_price
                    estimated += 1
            v = float(price or 0) * float(h.quantity or 0) * (fx if (cur == "USD" and len(curs) > 1) else 1.0)
            total += v
            if h.id == holding.id:
                mine = v
        if total <= 0:
            return None
        return {"weight_pct": round(mine / total * 100, 1), "count": len(rows), "estimated": estimated}
    except Exception as e:
        app.logger.debug(f"[position] 비중 계산 실패: {e}")
        return None


# ── 분석 응답 캐시 (종목·봉 단위, 5분) ─────────────────────────────────────
# 같은 종목을 여러 사람이 보거나 새로고침할 때 15~20초 걸리는 분석을 반복하지 않는다.
# 본문은 직렬화된 JSON 문자열로 보관하고(파이썬 객체보다 작음), 전체 크기를 제한한다.
# 현재가는 화면이 따로 폴링해 갱신하므로 5분 지연돼도 표시 가격은 최신이다.
_ANALYZE_CACHE = {}                 # (ticker, interval) → (ts, body, ctx)
_ANALYZE_TTL = 300
_ANALYZE_VER = "v11"      # 응답 필드가 바뀌면 올린다 → 재시작 전 DB 에 남은 옛 형식 본문을 쓰지 않음
_ANALYZE_CACHE_MAX_BYTES = 40 * 1024 * 1024
_ANALYZE_LOCK = threading.Lock()


def _analyze_cache_get(key):
    hit = _ANALYZE_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _ANALYZE_TTL:
        return hit
    return None


def _analyze_cache_put(key, body, ctx, ts=None):
    with _ANALYZE_LOCK:
        _ANALYZE_CACHE[key] = (ts or time.time(), body, ctx)
        # 오래된 것부터 지워 전체 크기를 제한 (운영 메모리 1GB)
        total = sum(len(v[1]) for v in _ANALYZE_CACHE.values())
        for k, v in sorted(_ANALYZE_CACHE.items(), key=lambda kv: kv[1][0]):
            if total <= _ANALYZE_CACHE_MAX_BYTES or k == key:
                continue
            total -= len(v[1])
            _ANALYZE_CACHE.pop(k, None)


def _analyze_response(base_body, position_data):
    body = base_body[:-1] + ', "position": ' + json.dumps(position_data, cls=NumpyEncoder, ensure_ascii=False) + "}"
    return app.response_class(response=body, status=200, mimetype="application/json")


# ── 종목 AI 인사이트: 급등락 사유 + 밸류에이션 해설 + 미번역 뉴스 → 단 1회 JSON 호출 ────────
_INSIGHT_TTL = 4 * 3600          # daily_insight: 4시간
_INSIGHT_REFRESH_PCT = 2.0       # 장중 등락률이 이만큼(%p) 바뀌면 사유를 새로 만든다
_NEWS_ONLY_GAP = 30 * 60         # 인사이트는 캐시돼 있고 새 기사만 있을 때, 번역 호출은 종목당 30분에 1번
_NEWS_ONLY_LAST = {}             # ticker → ts
_INSIGHT_FAIL = {}               # ticker → ts  (실패 직후 5분은 재시도 안 함)


def _news_id(n):
    """기사 식별자: URL(없으면 원문 제목) 해시. 같은 기사는 다시 번역하지 않는다."""
    import hashlib
    basis = n.get("url") or n.get("title_orig") or n.get("title") or ""
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _lead(text, max_chars=160):
    """본문 대신 앞 1~2문장만 (입력 토큰 절약)."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    out = " ".join(parts[:2])
    return out[:max_chars]


# 공시·수주 레이더(disclosure_events) → 종목 상세 · AI 급등락 사유. DB 조회만 (외부 호출 없음)
DISC_LABEL_KO = {"contract": "수주·계약", "buyback": "자사주·소각", "insider_buy": "내부자 매수",
                 "offering": "유상증자·주의", "material_8k": "8-K 주요사항"}


def _recent_disclosures(ticker, days, limit):
    """최근 N일 공시 (최신순, 최대 limit 건). 실패해도 빈 목록."""
    try:
        return disclosures.query("ALL", "ALL", ticker, days, limit)
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[disclosure] {ticker} 조회 실패: {e}")
        return []


def _disc_label(d):
    if d.get("category") == "contract" and d.get("sentiment") == "negative":
        return "계약 해지"
    return DISC_LABEL_KO.get(d.get("category"), d.get("category") or "공시")


def _disc_day(d):
    """공시일 'MM/DD' (한국 종목은 한국 시각, 미국 종목은 뉴욕 시각 기준)."""
    from zoneinfo import ZoneInfo
    try:
        t = datetime.fromisoformat(str(d.get("disclosed_at")).replace("Z", "+00:00"))
        tz = ZoneInfo("Asia/Seoul" if d.get("market") == "KR" else "America/New_York")
        t = t.astimezone(tz)
        return f"{t.month}/{t.day:02d}"
    except Exception:
        return ""


def _disc_line(d):
    """AI 입력 한 줄: '11/24 [수주·계약] 단일판매·공급계약체결 — 계약금액 1조 2,345억원 · 최근 매출 대비 11.62% (DART)'"""
    summ = f" — {d['summary']}" if d.get("summary") else ""
    return f"{_disc_day(d)} [{_disc_label(d)}] {d.get('title') or ''}{summ} ({d.get('source') or ''})".strip()


def _move_reason_fallback(chg, news, disc=None):
    if chg is None or abs(chg) < 0.5:
        return None
    kind = "급등" if chg >= 5 else ("급락" if chg <= -5 else ("상승" if chg > 0 else "하락"))
    parts = []
    if disc:                                          # 공시가 있으면 공시를 먼저
        parts.append("최근 공시: " + " / ".join(f"[{_disc_label(d)}] {d.get('title')}"
                                               + (f" ({d['summary']})" if d.get("summary") else "") for d in disc[:3]))
    if news:
        parts.append("관련 뉴스: " + " / ".join(n["title"] for n in news[:3]))
    if not parts:
        return f"현재 {kind} 관련 정보를 찾을 수 없습니다. 거시경제 지표나 시장 전반의 흐름을 함께 확인해 보세요."
    return " · ".join(parts)


def _dcf_brief(dcf):
    a = dcf.get("assumptions") or {}
    if dcf.get("model") == "pb_roe":
        return (f"금융주라 DCF 대신 적정 PBR 모델. 현재가 {dcf.get('current_price')}, 적정가 {dcf.get('fair_value')} "
                f"(상승여력 {dcf.get('upside_pct')}%), ROE {a.get('roe_pct')}%, 자기자본비용 {a.get('cost_equity_pct')}%, "
                f"적정 PBR {a.get('fair_pb')} / 현재 PBR {a.get('pbr_now')}, 현재가가 반영한 ROE {dcf.get('implied_roe_pct')}%, "
                f"애널리스트 목표가 {dcf.get('analyst_target')}")
    return (f"현재가 {dcf.get('current_price')}, DCF 적정주가 {dcf.get('fair_value')} "
            f"(상승여력 {dcf.get('upside_pct')}%), 시나리오 비관/기본/낙관 "
            f"{(dcf.get('scenarios') or {}).get('bear', {}).get('fair_value')}/"
            f"{(dcf.get('scenarios') or {}).get('base', {}).get('fair_value')}/"
            f"{(dcf.get('scenarios') or {}).get('bull', {}).get('fair_value')}, "
            f"현재가가 반영한 매출성장률(역산) {dcf.get('implied_growth_pct')}%, 역산 영업이익률 {dcf.get('implied_margin_pct')}% "
            f"(현재 {a.get('ebit_margin_pct')}%), 적용 성장률 {a.get('base_growth_pct')}% "
            f"(애널리스트 내년 매출 성장률 {a.get('consensus_growth_pct')}%, 최근 분기 YoY {a.get('recent_growth_pct')}%, "
            f"과거 CAGR {a.get('hist_cagr_pct')}%), WACC {a.get('wacc_pct')}%, "
            f"애널리스트 목표가 {dcf.get('analyst_target')}, 신뢰도 {dcf.get('reliability')}")


def get_stock_insight(ticker, name, stock_data, ai_result, news, is_kr, disclosures_recent=None):
    """→ {"move_reason", "valuation_comment", "news_tr": {id: {"title","summary"}}, "ai": bool}

    캐시 우선: daily_insight(4시간) · news_translation(영구). 필요한 것만 모아 AI 를 최대 1번 부른다.
    disclosures_recent: 최근 7일 공시(최대 3건, DB). 있으면 급등락 사유의 최우선 근거로 프롬프트에 넣고,
    공시 구성이 바뀌면 캐시된 사유를 새로 만든다.
    """
    disc = list(disclosures_recent or [])[:3]
    disc_sig = ",".join(str(d.get("id")) for d in disc)
    chg = stock_data.get("price_change_pct")
    dcf = stock_data.get("dcf")
    want_move = chg is not None and abs(chg) >= 0.5
    want_val = bool(dcf)
    t0 = time.time()

    key = f"daily_insight:{ticker}"
    hit = ai_cache.get(key)
    ins = hit[0] if hit else None
    stale = (ins is None) or (want_move and abs((ins.get("chg") or 0) - chg) >= _INSIGHT_REFRESH_PCT) \
        or (want_move and (ins.get("disc") or "") != disc_sig)          # 새 공시가 들어오면 사유를 다시

    tr, pending = {}, []
    if not is_kr:                                   # 한국 종목 뉴스는 이미 한글
        for n in news[:8]:
            nid = _news_id(n)
            h = ai_cache.get(f"news_translation:{nid}")
            if h:
                tr[nid] = h[0]
            else:
                pending.append((nid, n))

    need_ins = stale and (want_move or want_val)
    news_ok = pending and (need_ins or time.time() - _NEWS_ONLY_LAST.get(ticker, 0) > _NEWS_ONLY_GAP)
    recently_failed = time.time() - _INSIGHT_FAIL.get(ticker, 0) < 300
    has_ai = bool(_ai_providers())

    def _result(src, used_ai):
        mv = (src or {}).get("move_reason") if want_move else None
        return {"move_reason": mv or _move_reason_fallback(chg, [
                    {"title": (tr.get(_news_id(n)) or {}).get("title") or n["title"]} for n in news], disc),
                "valuation_comment": (src or {}).get("valuation_comment") if want_val else None,
                "news_tr": tr, "ai": used_ai}

    if not has_ai or recently_failed or (not need_ins and not news_ok):
        if ins:
            _ai_log(ticker, "daily_insight", True, "cache", hit[1], (time.time() - t0) * 1000, "ok")
        return _result(ins, bool(ins))

    def _make():
        nonlocal ins
        ask_move, ask_val = need_ins and want_move, need_ins and want_val
        items = pending if news_ok else []
        lines = [f"종목: {name} ({ticker})"]
        req = []
        if ask_move and disc:
            req.append(f'- "move_reason": 오늘 {chg:+.2f}% 움직임의 가능한 원인 2~3문장. 아래 [최근 7일 주요 공시]를 뉴스보다 '
                       "최우선 근거로 삼아, 공시 종류와 핵심 수치(계약금액·매출 대비 %, 자사주 취득·소각, 유상증자 희석, 임원 장내 매수액 등)를 "
                       "먼저 짚을 것. 뉴스·기술적 요인은 보조 근거로만. 공시가 원인이라고 단정하지 말고 가능성으로 표현할 것.")
        elif ask_move:
            req.append(f'- "move_reason": 오늘 {chg:+.2f}% 움직임의 가능한 원인 2~3문장. 아래 헤드라인에 근거가 있으면 짧게 인용, '
                       "없으면 기술적·시장 요인. 단정하지 말 것.")
        if ask_val:
            req.append('- "valuation_comment": 아래 DCF 결과 해설 2~3문장. 주어진 숫자만 인용, 역산 값 중심, '
                       "신뢰도 low면 모델 한계를 언급, 매수·매도 권유 금지.")
        if items:
            req.append('- "news": 번역할 기사마다 {"id": 그대로, "title": 자연스러운 한국어 제목, '
                       '"summary": 한국어 한 줄 요약(40자 안팎)}')
        lines.append("[작성할 항목]\n" + "\n".join(req))
        if ask_move and disc:                        # 공시가 먼저 (최우선 근거)
            lines.append("[최근 7일 주요 공시 — 최우선 근거]\n" + "\n".join(f"- {_disc_line(d)}" for d in disc))
        if ask_move and ai_result:
            vr = ""
            if stock_data.get("volume") and stock_data.get("avg_volume"):
                vr = f", 거래량 평균의 {stock_data['volume'] / stock_data['avg_volume']:.1f}배"
            lines.append(f"[기술 지표] 판단 {ai_result.get('verdict')}, 추세 {ai_result.get('trend')}, "
                         f"RSI {ai_result.get('rsi')}{vr}")
            heads = [n.get("title_orig") or n["title"] for n in news[:6]]
            if heads:
                lines.append("[최근 헤드라인]\n" + "\n".join(f"- {h}" for h in heads))
        if ask_val:
            lines.append("[DCF] " + _dcf_brief(dcf))
        if items:
            lines.append("[번역할 기사]\n" + json.dumps(
                [{"id": nid, "t": n.get("title_orig") or n["title"], "s": _lead(n.get("desc"))} for nid, n in items],
                ensure_ascii=False))
        lines.append('출력: JSON 객체 하나 {"move_reason": ..., "valuation_comment": ..., "news": [...]} '
                     "— 요청하지 않은 항목은 null.")
        max_tok = 120 + (220 if ask_move else 0) + (240 if ask_val else 0) + 70 * len(items)
        if items:
            _NEWS_ONLY_LAST[ticker] = time.time()
        out = _ai_json(
            "당신은 한국 개인 투자자를 위한 증권 애널리스트입니다. 순수 한글로 쓰고 한자는 쓰지 않습니다. "
            "영문 약어·회사명·티커는 그대로 둡니다. 반드시 JSON 객체 하나만 출력합니다.",
            "\n\n".join(lines), max_tokens=max_tok, label="daily_insight", ticker=ticker)
        if not out:
            _INSIGHT_FAIL[ticker] = time.time()
            return ins
        model = getattr(_AI_LAST_MODEL, "v", None)
        # 기사 번역: 기사별 영구 캐시
        valid = {nid for nid, _ in items}
        for o in (out.get("news") or []):
            if isinstance(o, dict) and o.get("id") in valid and o.get("title"):
                val = {"title": _strip_hanja(str(o["title"]).strip()),
                       "summary": _strip_hanja(str(o.get("summary") or "").strip())}
                tr[o["id"]] = val
                ai_cache.put(f"news_translation:{o['id']}", ticker, "news_translation", val, model, None)
        if need_ins:
            new = dict(ins or {})
            if ask_move and out.get("move_reason"):
                new["move_reason"] = _strip_hanja(str(out["move_reason"]).strip())
            if ask_val and out.get("valuation_comment"):
                new["valuation_comment"] = _strip_hanja(str(out["valuation_comment"]).strip())
            new["chg"] = chg
            if ask_move:
                new["disc"] = disc_sig               # 이 사유가 어떤 공시 구성으로 만들어졌는지
            if new.get("move_reason") or new.get("valuation_comment"):
                ai_cache.put(key, ticker, "daily_insight", new, model, _INSIGHT_TTL)
                ins = new
        return ins

    try:
        res = ai_cache.single_flight(f"insight:{ticker}", _make, wait_timeout=25)
    except Exception as e:
        app.logger.warning(f"[AI] ticker={ticker} task=daily_insight 실패: {e}")
        res = ins
    # 다른 요청이 만든 결과를 기다렸다면 번역도 캐시에서 다시 읽는다
    for nid, _ in pending:
        if nid not in tr:
            h = ai_cache.get(f"news_translation:{nid}")
            if h:
                tr[nid] = h[0]
    return _result(res, bool(res))


# ── 느린 부가 섹션 (뉴스·애널리스트·AI 기업 소개·AI 급등락 사유) ─────────────────
_EXTRA_CACHE = {}          # ticker → (ts, payload)
_EXTRA_TTL = 300


@app.route("/api/analyze/extra", methods=["GET"])
def analyze_extra():
    """/api/analyze 가 먼저 화면을 그린 뒤 부르는 부가 섹션 (뉴스·애널리스트·AI 3종).

    어떤 부분이 실패해도 나머지는 돌려준다(항상 200). 뉴스·애널리스트는 분석 맥락 없이 바로 시작하고,
    AI 는 분석 맥락(메모리 → DB → 백그라운드 재분석 순)을 찾은 경우에만 돌린다.
    """
    ticker = request.args.get("ticker", "").strip().upper()
    interval = request.args.get("interval", "1d")
    if interval not in ("1d", "1wk", "1mo"):
        interval = "1d"
    if not ticker:
        return jsonify({"error": "티커를 입력해주세요"}), 400

    hit = _EXTRA_CACHE.get(ticker)
    if hit and (time.time() - hit[0]) < _EXTRA_TTL:
        return jsonify(hit[1])

    t_start = time.time()
    is_kr = ticker.endswith((".KS", ".KQ"))
    stock = yf.Ticker(ticker)
    code = ticker.split(".")[0]
    out = {"news": [], "analysts": None, "move_reason": None, "company_overview": None,
           "valuation_comment": None}
    timing, problems = {}, []

    def _timed(name, fn, *a):
        t0 = time.time()
        try:
            return fn(*a)
        finally:
            timing[name] = round(time.time() - t0, 2)

    # ① 외부 데이터: 분석 맥락과 무관하게 바로 시작
    f_news = _BG_EXEC.submit(_timed, "news", lambda: fetch_kr_news(code) if is_kr else fetch_news(stock))
    ctx_holder = {}

    def _analysts():
        if is_kr:
            return fetch_kr_analysts(code)
        info = (ctx_holder.get("ctx") or {}).get("info") or (stock.info or {})
        return fetch_analysts(stock, info)
    f_an = _BG_EXEC.submit(_timed, "analysts", _analysts)

    # ② 분석 맥락: 메모리(같은 프로세스) → DB(다른 프로세스·재시작 후) → 백그라운드 재분석(8초까지만 대기)
    ctx = None
    try:
        mem = _analyze_cache_get((ticker, interval)) or _analyze_cache_get((ticker, "1d"))
        if mem:
            ctx = mem[2]
        else:
            dbh = ai_cache.get(f"analysis_ctx:{ticker}")
            if dbh:
                ctx = dbh[0]
            else:
                problems.append("ctx_miss")
                def _reanalyze():
                    with app.test_request_context(f"/api/analyze?ticker={ticker}&interval={interval}"):
                        analyze()
                    m = _analyze_cache_get((ticker, interval))
                    return m[2] if m else None
                # 재분석은 백그라운드에서 끝까지 돌고(결과는 메모리·DB 맥락 캐시에 남음), 여기선 8초까지만 기다린다
                fut = _BG_EXEC.submit(lambda: ai_cache.single_flight(f"reanalyze:{ticker}", _reanalyze))
                try:
                    ctx = fut.result(timeout=8)
                except Exception:
                    ctx = None
    except Exception as e:
        problems.append(f"ctx_error:{type(e).__name__}")
        app.logger.warning(f"[extra] ticker={ticker} 분석 맥락 조회 실패: {e}")
    ctx_holder["ctx"] = ctx

    deadline = time.time() + 10

    def _take(fut, key, default):
        try:
            out[key] = fut.result(timeout=max(0.1, deadline - time.time()))
            return True
        except Exception as e:
            if not fut.done():
                problems.append(f"{key}_slow")
                return False            # 시간 초과 — 백그라운드에서 계속 돈다
            problems.append(f"{key}_error:{type(e).__name__}")
            app.logger.warning(f"[extra] ticker={ticker} {key} 실패: {e}")
            out[key] = default
            return True

    done_news = _take(f_news, "news", [])
    news_list = out["news"] or []
    done_an = _take(f_an, "analysts", None)

    # ③ AI: 맥락이 있을 때만. 뉴스·애널리스트 결과와 무관하게 진행(뉴스가 없으면 없는 대로)
    ai_done = False
    if ctx:
        stock_data = dict(ctx.get("stock_data") or {})
        info = ctx.get("info") or {}
        ai_result = ctx.get("ai_result") or {}
        name = stock_data.get("name") or ticker
        f_ov = _BG_EXEC.submit(_timed, "overview", fetch_company_overview, ticker, name, info,
                               stock_data.get("revenue_quarters", []), stock_data.get("currency", "USD"))
        disc7 = _recent_disclosures(ticker, 7, 3)      # 요청 스레드에서 DB 조회 (백그라운드 스레드엔 앱 맥락이 없음)
        f_ins = _BG_EXEC.submit(_timed, "insight", get_stock_insight, ticker, name, stock_data, ai_result,
                                news_list, is_kr, disc7)
        deadline = min(deadline, time.time() + 3) if (done_news and done_an) else deadline
        done_ov = _take(f_ov, "company_overview", None)
        ins = None
        try:
            ins = f_ins.result(timeout=max(0.1, deadline - time.time()))
            done_ins = True
        except Exception as e:
            done_ins = f_ins.done()
            if not done_ins:
                problems.append("insight_slow")
            else:
                problems.append(f"insight_error:{type(e).__name__}")
                app.logger.warning(f"[extra] ticker={ticker} insight 실패: {e}")
        if ins:
            out["move_reason"] = ins.get("move_reason")
            out["valuation_comment"] = ins.get("valuation_comment")
            tr = ins.get("news_tr") or {}
            for n in news_list:
                t = tr.get(_news_id(n))
                if t:
                    n["title_orig"] = n.get("title_orig") or n["title"]
                    n["title"] = t.get("title") or n["title"]
                    if t.get("summary"):
                        n["desc"] = t["summary"]
        else:
            out["move_reason"] = _move_reason_fallback(stock_data.get("price_change_pct"), news_list, disc7)
        ai_done = done_ov and done_ins
    partial = not (done_news and done_an and ai_done)
    out["partial"] = partial
    out["ai_status"] = _ai_status()
    # 이번 응답이 아직 AI 를 기다리는 중이면 '실패'로 표시하지 않는다 (화면 깜빡임 방지)
    out["ai_pending"] = bool(partial)
    app.logger.info(f"[extra] ticker={ticker} total={time.time() - t_start:.2f}s parts={timing} "
                    f"partial={partial} ctx={'yes' if ctx else 'no'} problems={problems or '-'}")
    try:
        out = json.loads(json.dumps(out, cls=NumpyEncoder, ensure_ascii=False))
    except Exception as e:
        app.logger.warning(f"[extra] ticker={ticker} 직렬화 실패: {e}")
    with _ANALYZE_LOCK:
        # 부분 결과는 캐시하지 않는다 → 화면이 다시 요청하면 그 사이 끝난 AI 결과(DB 캐시)로 채워진다
        if not partial:
            _EXTRA_CACHE[ticker] = (time.time(), out)
        if len(_EXTRA_CACHE) > 200:   # 오래된 것 정리
            for k, _ in sorted(list(_EXTRA_CACHE.items()), key=lambda kv: kv[1][0])[:50]:
                _EXTRA_CACHE.pop(k, None)
    return jsonify(out)


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

    # 5분 안에 같은 종목·봉을 분석했으면 그 결과를 쓰고, 내 포지션만 새로 붙인다
    cached = _analyze_cache_get((ticker, interval))
    if not cached:
        # 다른 프로세스나 재시작 전에 만든 결과가 DB 에 있으면 그것을 쓴다
        try:
            dbb = ai_cache.get(f"analysis_body:{_ANALYZE_VER}:{ticker}:{interval}", use_mem=False)
            dbc = ai_cache.get(f"analysis_ctx:{ticker}") if dbb else None
            if dbb and dbc:
                import gzip as _gz, base64 as _b64
                body = _gz.decompress(_b64.b64decode(dbb[0]["b"])).decode("utf-8")
                _analyze_cache_put((ticker, interval), body, dbc[0], ts=dbb[0].get("ts"))   # 남은 수명만큼만
                cached = _analyze_cache_get((ticker, interval))
                app.logger.debug(f"[analyze] ticker={ticker} DB 캐시 사용")
        except Exception as e:
            app.logger.warning(f"[analyze] ticker={ticker} DB 캐시 읽기 실패: {e}")
    if cached:
        ctx = cached[2]
        try:
            pos = _position_for(ticker, ctx["stock_data"], ctx["current_price"], ctx["ai_result"])
        except Exception:
            pos = None
        return _analyze_response(cached[1], pos)

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
            # PER: 스크리너·비교와 같은 기준 = 최근 4분기(TTM) EPS, 없으면 직전 연간 EPS
            integ = fetch_naver_integration(krx_code) or {}
            kr_eps, kr_basis = kr_per_basis(krx_code)
            if kr_basis and not kr_eps:                    # 최근 4분기 적자 → PER 없음
                info["trailingPE"] = None
                info["_pe_basis"] = kr_basis
            if kr_eps:
                info["trailingEps"] = kr_eps
                info["_pe_basis"] = kr_basis
                if ref_price:
                    info["trailingPE"] = round(ref_price / kr_eps, 2)
            kr_bps = integ.get("bps") or (naver or {}).get("bps")
            if kr_bps and ref_price:
                info["bookValue"]   = kr_bps
                info["priceToBook"] = round(ref_price / kr_bps, 2)
            if integ.get("dps"):
                naver = dict(naver or {}, dps=integ["dps"])        # 배당도 같은 출처
            if naver and ref_price:
                # 선행PER: Yahoo forwardPE 는 어느 해 추정치인지 불분명하고(삼성전자 2.4x 등) 네이버와 어긋난다.
                # 네이버 컨센서스(확정 다음 해) EPS 로 계산하고 기준 연도를 같이 내려준다.
                if naver.get("cns_eps"):
                    info["forwardPE"] = round(ref_price / naver["cns_eps"], 2)
                    info["_fwd_pe_year"] = naver["cns_year"][2:]
                    info["_fwd_pe_basis"] = f"네이버 컨센서스 {naver['cns_year']}년 추정 EPS {naver['cns_eps']:,.0f}원 기준"
                else:
                    info["forwardPE"] = None       # 출처가 불분명한 값은 표시하지 않음

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

        try:
            _macro = get_macro_snapshot(is_korean)
        except Exception:
            _macro = None
        ai_result = analyze_signals(df, info, df_weekly=df_weekly, stock=stock,
                                    macro=(_macro or {}).get("regime"))

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
            return [_cf(v) for v in chart_df[col]] if col in chart_df.columns else []

        chart_data = {
            "dates": chart_df.index.tolist(),
            "open": [_cf(v) for v in chart_df["Open"]],
            "high": [_cf(v) for v in chart_df["High"]],
            "low": [_cf(v) for v in chart_df["Low"]],
            "close": [_cf(v) for v in chart_df["Close"]],
            "volume": [_cv(v) for v in chart_df["Volume"]],
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
        toss_session    = None  # 토스 응답에 세션 구분 필드가 있을 때만 "extended" | "regular"

        # 1) 토스증권 우선 (한국+미국 모두 실시간)
        if toss_api.is_enabled() and toss_api.is_eligible(ticker):
            try:
                tp = toss_api.get_price_info(ticker)
                if tp and tp.get("price"):
                    realtime_price  = safe_float(tp["price"])
                    realtime_source = "toss"
                    toss_session    = tp.get("session")
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
        # 정규장 종가: 장이 닫혀 있을 때 일봉 끝에 시간외 봉(토스 프리마켓·주간거래)이 붙어 있으면 그 앞 봉
        regular_close, regular_prev, extended_bar, regular_bar_ts = regular_session_close(df, is_korean, is_market_open_now)
        if extended_bar:
            # 마지막 봉 종가(=시간외 가격)와 토스 실시간가가 같아 '실시간 아님'으로 빠지던 경우도 시간외 가격으로 본다
            if not realtime_useful:
                current_price = history_close
                realtime_source = price_source
            prev_close = regular_close
            price_change     = round(current_price - prev_close, 2) if (current_price and prev_close) else None
            price_change_pct = round((price_change / prev_close) * 100, 2) if (price_change and prev_close) else None
        is_extended_hours_price = extended_hours_flag(
            current_price, regular_close, bool(realtime_source) or extended_bar, is_market_open_now, is_korean, toss_session)

        # 한국 종목: 전일 종가·정규장 종가는 KRX 공식값(네이버 closePrice − 전일 대비)으로 맞춘다.
        # 토스 일봉은 대체거래소(NXT) 시간외 체결이 섞여 전날 봉 종가가 KRX 종가와 다를 수 있어
        # (253450 11/20: 토스 30,600 vs KRX 31,350) 화면마다 등락률이 달라지던 것을 막는다.
        if is_korean and current_price:
            try:
                kref = krx_ref.refs([ticker]).get(ticker)
            except Exception:
                kref = None
            if kref:
                if is_market_open_now:
                    if kref.get("date") == krx_ref.today_kst():
                        prev_close = kref["prev"]
                else:
                    regular_close = kref["close"]                  # 마지막 정규장(KRX) 종가
                    if not (realtime_useful or extended_bar):
                        current_price = regular_close              # 실시간가가 없으면 토스 일봉 대신 KRX 종가
                    is_extended_hours_price = extended_hours_flag(
                        current_price, regular_close, bool(realtime_source) or extended_bar, False, True, toss_session)
                    prev_close = regular_close if is_extended_hours_price else kref["prev"]
                price_change     = round(current_price - prev_close, 2) if (current_price and prev_close) else None
                price_change_pct = round((price_change / prev_close) * 100, 2) if (price_change and prev_close) else None

        # 데이터 기준 시각 (정규장 마지막 봉; 시간외 봉은 '장 마감 · ○/○ 기준' 에 쓰지 않는다)
        try:
            last_bar_ts = regular_bar_ts if extended_bar else df.index[-1]
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

            app.logger.debug(f"[EPS est] {ticker}: act={list(eps_act_by_period.keys())[:6]} est={list(eps_est_by_period.keys())[:6]}")

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

        # ── 분기 영업이익: 미국 = quarterly_income_stmt 발표치, 한국 = 네이버·DART (발표치 + 추정치) ──
        oi_quarters = []
        if not is_korean:
            try:
                if qf is not None and not qf.empty:
                    for key in ["Operating Income", "OperatingIncome", "EBIT"]:
                        if key in qf.index:
                            for idx, val in qf.loc[key].dropna().sort_index(ascending=False).items():
                                v = safe_float(val)
                                if v is not None and len(oi_quarters) < 5:
                                    oi_quarters.append({"period": str(idx)[:7], "actual": v, "estimate": None})
                            break
            except Exception as e:
                app.logger.debug(f"us oi_quarters: {e}")
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
                    app.logger.debug(f"[KR qtr] DART merged for {krx_code}")

                app.logger.debug(
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
            "name": _display_name(ticker, info),
            "current_price": current_price,
            "prev_close": prev_close,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "is_realtime": is_realtime,
            "is_market_open": is_market_open_now,
            "data_timestamp": data_timestamp,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "realtime_source": realtime_source,  # "toss" | "naver" | "yfinance" | null
            "regular_close": regular_close,      # 최신 일봉(정규장) 종가
            "is_extended_hours_price": is_extended_hours_price,   # 현재가에 시간외(프리·애프터마켓) 체결 반영
            "chart_source": price_source,        # "toss" | "yfinance" (디버그용)
            "market_cap": info.get("marketCap"),
            "volume": safe_float(df["Volume"].iloc[-1]),
            "avg_volume": safe_float(df["Volume"].tail(20).mean()),
            "year_high": year_high,
            "year_low": year_low,
            "pe_ratio": (round(current_price / info["trailingEps"], 2)
                         if (is_korean and info.get("_pe_basis") and current_price and info.get("trailingEps"))
                         else safe_float(info.get("trailingPE"))),
            "pe_basis": info.get("_pe_basis") or ("TTM" if info.get("trailingPE") else None),
            "forward_pe": safe_float(info.get("forwardPE")),
            "forward_pe_year": info.get("_fwd_pe_year"),
            "forward_pe_basis": info.get("_fwd_pe_basis") or ("Yahoo 애널리스트 추정 EPS 기준" if info.get("forwardPE") else None),
            "pb_ratio": (round(current_price / info["bookValue"], 2)
                         if (is_korean and current_price and safe_float(info.get("bookValue")))
                         else safe_float(info.get("priceToBook"))),
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

        # 기간별 고점 대비 현재가 하락률 (drawdown) — 현재가가 고점이면 신고가 갱신
        def calc_drawdown(days=None):
            try:
                sub = df if days is None else df.tail(days)
                if sub is None or len(sub) == 0:
                    return None
                col = "High" if "High" in sub.columns else "Close"
                high = float(sub[col].max())
                if not high or high <= 0 or current_price is None:
                    return None
                pct = (current_price / high - 1) * 100
                return {
                    "high":    round(high, 4),
                    "pct":     round(pct, 2),                      # 0 또는 음수 (고점 아래일 때)
                    "at_high": current_price >= high - 1e-9,       # 신고가 갱신 중
                }
            except Exception:
                return None

        stock_data["drawdowns"] = {
            "1w":  calc_drawdown(5),
            "1m":  calc_drawdown(21),
            "all": calc_drawdown(None),
        }

        # ── DCF 밸류에이션 ────────────────────────────────
        # 재무제표 조회(네트워크 3회)만 캐시하고, DCF 계산은 순수 수학이라
        # 매번 실시간 가격으로 재계산 → 역산 성장률이 항상 현재가 기준.
        try:
            stock_data["dcf"] = compute_dcf(
                ticker, info,
                *_get_financials(ticker, stock),
                current_price, stock_data.get("currency", "USD"),
                risk_free_rate=_get_risk_free_rate(stock_data.get("currency")),
                consensus_growth=_get_consensus_rev_growth(ticker, stock),
                # 최근 4개 분기 확정 실적(위에서 모은 분기 매출·영업이익)이 다 있으면 TTM 으로 출발
                ttm=ttm_from_quarters(revenue_quarters, oi_quarters),
            )
        except Exception as e:
            app.logger.warning(f"[dcf] {ticker}: {e}")
            stock_data["dcf"] = None

        # ── 밸류에이션 풋볼필드: DCF 범위 · 애널리스트 목표가 범위 · 52주 범위 (DCF 가 없어도 나머지는 그린다) ──
        _d = stock_data.get("dcf") or {}
        _sc = _d.get("scenarios") or {}
        _vals = [(_sc.get(k) or {}).get("fair_value") for k in ("bear", "base", "bull")]
        stock_data["valuation_ranges"] = {
            "current": current_price,
            "dcf": ({"low": min(v for v in _vals if v), "mid": _vals[1], "high": max(v for v in _vals if v),
                     "label": "적정 PBR" if _d.get("model") == "pb_roe" else "DCF 적정가"}
                    if _vals[1] and all(_vals) else None),
            "analyst": ({"low": safe_float(info.get("targetLowPrice")), "mid": safe_float(info.get("targetMeanPrice")),
                         "high": safe_float(info.get("targetHighPrice")), "count": info.get("numberOfAnalystOpinions")}
                        if safe_float(info.get("targetLowPrice")) and safe_float(info.get("targetHighPrice")) else None),
            "week52": ({"low": year_low, "high": year_high} if year_low and year_high else None),
        }

        is_kr_stock  = ticker.endswith('.KS') or ticker.endswith('.KQ')

        # ── 시장 컨텍스트 + 다가오는 이벤트 ──────────────────────────────
        try:
            stock_data["market_context"], stock_data["market_mood"] = build_market_context(
                ticker, is_kr_stock, stock_data.get("price_change_pct"))
        except Exception as e:
            app.logger.warning(f"market_context: {e}")
        try:
            stock_data["upcoming_events"] = fetch_upcoming_events(stock, info, ticker)
        except Exception as e:
            app.logger.warning(f"upcoming_events: {e}")

        # 뉴스·애널리스트·AI 기업 소개·AI 급등락 사유는 느려서(특히 AI 호출) 첫 화면을 붙잡지 않도록
        # /api/analyze/extra 로 분리했다. 화면은 시세·차트·판단을 먼저 그리고 이 섹션들을 나중에 채운다.
        news_data = analyst_data = move_reason = None

        # ── 내 포지션 데이터 (로그인된 경우) ──────────────────────────────
        position_data = _position_for(ticker, stock_data, current_price, ai_result)

        # 사용자와 무관한 본문은 캐시해 두고, 포지션만 요청마다 붙인다
        base_body = json.dumps(
            {
                "stock": stock_data, "analysis": ai_result, "chart": chart_data,
                "interval": interval, "interval_label": interval_label,
                "news": news_data, "analysts": analyst_data, "move_reason": move_reason,
                "extras_pending": True,
            },
            cls=NumpyEncoder, ensure_ascii=False,
        )
        _ctx = {"stock_data": stock_data, "info": info, "current_price": current_price, "ai_result": ai_result}
        _analyze_cache_put((ticker, interval), base_body, _ctx)
        try:
            # numpy 값 등을 JSON 으로 정리해 DB 에 1시간 보관 (가벼운 맥락만, 차트 제외)
            _ctx_json = json.loads(json.dumps(_ctx, cls=NumpyEncoder, ensure_ascii=False))
            _BG_EXEC.submit(ai_cache.put, f"analysis_ctx:{ticker}", ticker, "analysis_ctx", _ctx_json, None, 3600)
            # 분석 본문(차트 포함, 수 MB)도 압축해 5분 보관 → 다른 프로세스·재시작 후 재진입도 즉시
            import gzip as _gz, base64 as _b64
            _packed = {"b": _b64.b64encode(_gz.compress(base_body.encode("utf-8"), 5)).decode("ascii"), "ts": time.time()}
            _BG_EXEC.submit(ai_cache.put, f"analysis_body:{_ANALYZE_VER}:{ticker}:{interval}", ticker, "analysis_body",
                            _packed, None, _ANALYZE_TTL, False, True)
        except Exception as e:
            app.logger.warning(f"[analyze] ticker={ticker} 맥락 저장 실패: {e}")
        return _analyze_response(base_body, position_data)

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
            # 토스 계좌 불러오기 버튼 노출용. 실제 권한 검사는 POST /api/portfolio/import-toss 가 그대로 한다(403).
            "can_import_toss": _can_import_toss(),
        }
    })


def _can_import_toss() -> bool:
    """토스 계좌 연동이 가능한 계정인가: 토스 API 설정 + 소유자(TOSS_OWNER_EMAIL / TOSS_OWNER_USER_IDS).
    소유자 판정은 import-toss 와 같은 _is_toss_owner() (소유자 미지정이면 로컬 개발에서만 허용)."""
    try:
        return bool(toss_api.is_enabled() and _is_toss_owner())
    except Exception:
        return False


# ── 포트폴리오 조회 ───────────────────────────────────────────────────────────
@app.route("/api/portfolio")
def get_portfolio():
    if not current_user.is_authenticated:
        return jsonify([])
    try:
        splits.check_user(current_user.id)  # 하루 1회 액면분할 반영 (실제 포트폴리오 수량·매입가)
    except Exception as e:
        app.logger.warning(f"[SPLIT] 포트폴리오 점검 실패: {e}")

    holdings = Holding.query.filter_by(user_id=current_user.id)\
                            .order_by(Holding.created_at.desc()).all()
    want_summary = request.args.get("summary") == "1"
    if not holdings:
        return jsonify({"holdings": [], "summary": None} if want_summary else [])

    # 현재가 일괄 조회: 토스 1회 배치(20초 공용 캐시) → 없는 종목만 yfinance fast_info 병렬
    tickers = list({h.ticker for h in holdings})
    prices  = {}
    try:
        for t, info in toss_api.get_prices_cached(tickers).items():
            if info.get("price"):
                prices[t] = float(info["price"])
    except Exception:
        pass

    def _fetch_price(ticker):
        try:
            fi = yf.Ticker(ticker).fast_info
            p  = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
            return ticker, float(p) if p else None
        except Exception:
            return ticker, None

    rest = [t for t in tickers if t not in prices]
    if rest:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(rest))) as ex:
            for t, p in ex.map(_fetch_price, rest):
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
    if want_summary:
        return jsonify({"holdings": result, "summary": _portfolio_summary(result)})
    return jsonify(result)


def _portfolio_summary(rows):
    """실제 포트폴리오 요약 (원화 환산). 달러 종목은 현재 환율로 환산한다.

    매입 당시 환율은 저장돼 있지 않으므로 투자금도 현재 환율로 환산한다 → 환차손익은 반영되지 않음.
    현재가를 못 가져온 종목은 평가금액을 투자금과 같다고 보고, 손익 계산에서는 제외한다.
    """
    fx = _get_usd_krw_rate()
    total_value = total_cost = pl_value = pl_cost = 0.0
    by_cur, by_stock = {}, []
    priced = 0
    for r in rows:
        qty  = float(r.get("quantity") or 0)
        cost = float(r.get("purchase_price") or 0) * qty
        cp   = r.get("current_price")
        value = float(cp) * qty if cp else cost
        cur = r.get("currency") or "USD"
        rate = 1.0 if cur == "KRW" else fx
        v_krw, c_krw = value * rate, cost * rate
        total_value += v_krw
        total_cost  += c_krw
        if cp:
            priced += 1
            pl_value += v_krw
            pl_cost  += c_krw
        by_cur[cur] = by_cur.get(cur, 0) + v_krw
        by_stock.append({"ticker": r.get("ticker"), "name": r.get("name") or r.get("ticker"), "value_krw": v_krw})
    pl = pl_value - pl_cost
    weights = lambda v: round(v / total_value * 100, 1) if total_value > 0 else 0
    by_stock.sort(key=lambda x: -x["value_krw"])
    return {
        "total_value_krw": round(total_value),
        "total_cost_krw":  round(total_cost),
        "pl_krw":          round(pl),
        "pl_pct":          round(pl / pl_cost * 100, 2) if pl_cost > 0 else None,
        "fx_usd_krw":      round(fx, 2),
        "currency_weights": {c: weights(v) for c, v in sorted(by_cur.items(), key=lambda x: -x[1])},
        "top_weights": [{"ticker": s["ticker"], "name": s["name"], "pct": weights(s["value_krw"])} for s in by_stock[:5]],
        "count": len(rows),
        "priced_count": priced,
    }


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


# ── 토스 계좌 → 실제 포트폴리오 import ─────────────────────────────────────────
_STOCK_DB_SYMBOLS = None

def _stock_db_symbol_set():
    """STOCK_DB의 심볼 집합 (한 번만 빌드)."""
    global _STOCK_DB_SYMBOLS
    if _STOCK_DB_SYMBOLS is None:
        _STOCK_DB_SYMBOLS = {s.get("symbol") for s in STOCK_DB if s.get("symbol")}
    return _STOCK_DB_SYMBOLS


def _toss_symbol_to_ticker(symbol, market):
    """토스 심볼 → 앱 티커. 미국=그대로, 한국=.KS/.KQ 판별(STOCK_DB), 기본 .KS."""
    s = (symbol or "").upper()
    if market == "KR" or s.isdigit():
        syms = _stock_db_symbol_set()
        for suf in (".KS", ".KQ"):
            if (s + suf) in syms:
                return s + suf
        return s + ".KS"
    return s


@app.route("/api/portfolio/import-toss", methods=["POST"])
@login_required
def import_toss_portfolio():
    """토스 계좌 보유종목을 실제 포트폴리오(Holding)로 동기화.
    토스 계좌는 앱 소유자 키에 연동된 것이므로 TOSS_OWNER_EMAIL로 권한 제한.
    """
    if not toss_api.is_enabled():
        return jsonify({"error": "토스 API가 설정되지 않았습니다"}), 400

    # 소유자 게이팅: 토스 API는 client_credentials(소유자 본인 계좌)만 지원.
    # 같은 사람이 카카오/구글 등 여러 로그인을 쓰므로, 둘 중 하나로 허용:
    #   - TOSS_OWNER_EMAIL: 콤마구분 이메일 목록 (구글 등)
    #   - TOSS_OWNER_USER_IDS: 콤마구분 user id 목록 (카카오는 이메일 미제공 → id로)
    owner_emails = {e.strip() for e in os.environ.get("TOSS_OWNER_EMAIL", "").lower().split(",") if e.strip()}
    owner_ids    = {i.strip() for i in os.environ.get("TOSS_OWNER_USER_IDS", "").split(",") if i.strip()}
    if not (owner_emails or owner_ids) and IS_PRODUCTION:
        # 소유자 미지정 상태의 운영에서는 아무나 소유자 계좌를 불러올 수 있으므로 차단
        return jsonify({"error": "토스 계좌 연동 소유자가 설정되지 않았습니다 (TOSS_OWNER_EMAIL 또는 TOSS_OWNER_USER_IDS)"}), 403
    if owner_emails or owner_ids:
        my_email = (current_user.email or "").strip().lower()
        my_id    = str(current_user.id)
        allowed = (my_email and my_email in owner_emails) or (my_id in owner_ids)
        if not allowed:
            return jsonify({
                "error": f"이 계정은 토스 계좌 연동 권한이 없습니다. "
                         f"본인 계정이면 환경변수에 추가하세요 → "
                         f"user_id={my_id}, email={my_email or '(없음)'}, provider={current_user.provider}"
            }), 403

    # 토큰 발급 가능 여부 먼저 확인 (IP 미등록/프록시 미설정 시 여기서 막힘)
    if not toss_api._get_token():
        return jsonify({
            "error": "토스 인증 실패 — 서버 IP가 토스 콘솔에 등록되지 않았습니다. "
                     "고정 IP 프록시(TOSS_PROXY_URL) 설정 또는 현재 서버 IP 등록이 필요합니다. "
                     "(/api/debug/toss 로 현재 IP 확인)"
        }), 502

    try:
        items = toss_api.get_account_holdings()
    except Exception as e:
        return jsonify({"error": f"토스 보유종목 조회 실패: {e}"}), 502
    if items is None:
        return jsonify({"error": "토스 계좌를 찾을 수 없습니다 (계좌 미연동 또는 일시적 오류)"}), 404
    if not items:
        # 빈 응답일 때 그대로 진행하면 기존 포트폴리오만 전부 삭제됨 → 중단
        return jsonify({"error": "토스 보유종목이 비어 있습니다 — 기존 포트폴리오를 유지합니다"}), 404

    # 동기화: 기존 실제 포트폴리오 교체 (토스가 진실의 원천)
    Holding.query.filter_by(user_id=current_user.id).delete()
    imported = []
    for it in items:
        qty = it.get("quantity")
        avg = it.get("avg_price")
        if not qty or not avg or qty <= 0:
            continue
        ticker = _toss_symbol_to_ticker(it.get("symbol"), it.get("market"))
        currency = "KRW" if ticker.endswith((".KS", ".KQ")) else "USD"
        name = it.get("name") or ticker
        db.session.add(Holding(
            user_id=current_user.id, ticker=ticker, name=name,
            quantity=round(qty, 6), purchase_price=round(avg, 6), currency=currency,
        ))
        imported.append(ticker)
    db.session.commit()
    return jsonify({"ok": True, "imported": len(imported), "tickers": imported})


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


_CURRENT_PRICE_CACHE = {}   # ticker -> (epoch, price)
_CURRENT_PRICE_TTL = 3       # 3초 — 1초 폴링 부하 완화 (모의투자 체결 정확도엔 영향 미미)

def _fetch_current_price(ticker):
    """종목 실시간 현재가 (native currency). 토스 → 네이버(한국) → yfinance 순. 3초 캐시."""
    now = time.time()
    cached = _CURRENT_PRICE_CACHE.get(ticker)
    if cached and (now - cached[0]) < _CURRENT_PRICE_TTL:
        return cached[1]
    price = _fetch_current_price_uncached(ticker)
    if price:
        _CURRENT_PRICE_CACHE[ticker] = (now, price)
    return price


def _fetch_current_price_uncached(ticker):
    """종목 실시간 현재가 (캐시 없음). 토스 → 네이버(한국) → yfinance 순."""
    # 1순위: 토스
    if toss_api.is_enabled() and toss_api.is_eligible(ticker):
        try:
            info = toss_api.get_prices_cached([ticker], ttl=_CURRENT_PRICE_TTL)   # 공용 캐시 (다른 화면의 배치 결과 재사용)
            p = (info.get(ticker) or {}).get("price")
            if p:
                return float(p)
        except Exception:
            pass
    # 2순위: 한국 종목은 네이버 실시간 폴링 (~1분 지연, yfinance보다 빠름)
    if ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ"):
        try:
            nv = fetch_naver_realtime_price(ticker.split(".")[0])
            if nv and nv.get("current_price"):
                return float(nv["current_price"])
        except Exception:
            pass
    # 3순위: yfinance fast_info
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
        by_date = {}
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                c = safe_float(row.get("Close"))          # NaN(장 마감 뒤 빈 당일 봉) 은 버린다
                if c and c > 0:
                    by_date[idx.strftime("%Y-%m-%d")] = c
        # 한국 지수는 Yahoo 일봉이 하루 이상 늦거나 당일 종가가 비는 일이 잦다
        # (2026-11-25 새벽: ^KS11 이 11/23 까지만 있고 11/24 종가 5,006.03 이 없음) → 네이버 일별 종가로 덮어쓰고 이어 붙인다
        if symbol in _NAVER_INDEX:
            for d, c in _naver_index_daily(_NAVER_INDEX[symbol]).items():
                by_date[d] = c
        if not by_date:
            return cached["data"] if cached else []
        data = [{"date": d, "close": by_date[d]} for d in sorted(by_date)]
        _BENCHMARK_CACHE[symbol] = {"ts": now, "data": data}
        return data
    except Exception as e:
        app.logger.warning(f"[benchmark] {symbol} fetch failed: {e}")
        return cached["data"] if cached else []


def _naver_index_daily(code, n=20):
    """네이버 지수 일별 종가 {YYYY-MM-DD: close} (최근 n 거래일, 장중이면 오늘 현재가 포함). 실패하면 {}."""
    try:
        r = requests.get(f"https://m.stock.naver.com/api/index/{code}/price",
                         params={"pageSize": n, "page": 1},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return {}
        out = {}
        for it in r.json() or []:
            d = str(it.get("localTradedAt") or "")[:10]
            c = safe_float(str(it.get("closePrice", "")).replace(",", ""))
            if len(d) == 10 and c and c > 0:
                out[d] = c
        return out
    except Exception as e:
        app.logger.debug(f"[benchmark] naver {code}: {e}")
        return {}


def _ffill_benchmark(series, extra_dates):
    """주말·휴장일(내 자산 스냅샷은 있는데 지수 종가가 없는 날)에 직전 거래일 수익률을 채워 선이 끊기지 않게.
    series: [{date, return_pct}] (오름차순), extra_dates: 'YYYY-MM-DD' 목록. 첫 지수 값 이전 날짜는 채우지 않는다."""
    if not series:
        return series
    have = {p["date"]: p["return_pct"] for p in series}
    first = series[0]["date"]
    dates = sorted(set(have) | {d for d in extra_dates if d and d >= first})
    out, last = [], None
    for d in dates:
        if d in have:
            last = have[d]
            out.append({"date": d, "return_pct": last})
        elif last is not None:
            out.append({"date": d, "return_pct": last, "filled": True})
    return out


def _max_drawdown_pct(user_id):
    """일별 자산 스냅샷 기준 최대 낙폭(%). 고점 대비 가장 크게 빠진 비율, 데이터 2개 미만이면 0."""
    rows = [r[0] for r in db.session.query(AssetSnapshot.total_assets_krw)
            .filter_by(user_id=user_id).order_by(AssetSnapshot.date.asc()).all() if r[0]]
    peak, mdd = None, 0.0
    for v in rows:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            mdd = min(mdd, (v / peak - 1) * 100)
    return round(mdd, 2)


def _normalize_benchmark(history, start_date_str):
    """벤치마크 종가 시계열을 start_date 기준 % 수익률로 변환.
    Returns: list of {"date": "YYYY-MM-DD", "return_pct": float}
    """
    if not history or not start_date_str:
        return []
    # 기준 = 시작일 당일(또는 그 직전 거래일) 종가. 주말·휴장일에 시작해도 그날 0% 로 선이 시작되고
    # 다음 거래일까지 비지 않는다 (예전엔 '시작일 이후 첫 종가'라 주말 시작이면 월요일까지 지수선이 없었다)
    before = [pt for pt in history if pt["date"] <= start_date_str and pt.get("close")]
    after = [pt for pt in history if pt["date"] > start_date_str and pt.get("close")]
    if before:
        base_pt = before[-1]
    elif after:
        base_pt = after[0]
    else:
        return []
    baseline = base_pt["close"]
    if not baseline or baseline <= 0:
        return []
    out = []
    if base_pt["date"] < start_date_str:
        out.append({"date": start_date_str, "return_pct": 0.0, "filled": True})
    for pt in history:
        if pt["date"] < start_date_str or not pt.get("close"):
            continue
        out.append({"date": pt["date"], "return_pct": round((pt["close"] / baseline - 1) * 100, 3)})
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

    # ── 재미 배지용 추가 통계 ──
    from collections import Counter
    # 올빼미: 한국시간 0~6시(미국장 시간대) 거래 수 (timestamp는 UTC 저장)
    night_owl_count = sum(
        1 for t in txs if t.timestamp and 0 <= ((t.timestamp.hour + 9) % 24) < 6
    )
    # 단일 거래 최대 금액 (KRW)
    max_single_trade_krw = max((t.amount_krw or 0) for t in txs) if txs else 0
    # 1주 매매 경험
    has_single_share = any(abs((t.quantity or 0) - 1) < 1e-9 for t in txs)
    # 동일 종목 최다 매수 횟수 (물타기)
    buy_ticker_counts = Counter(t.ticker for t in txs if t.type == "buy")
    max_same_ticker_buys = max(buy_ticker_counts.values()) if buy_ticker_counts else 0
    # 누적 거래 고유 종목 수
    unique_tickers_traded = len({t.ticker for t in txs})
    # 현재 보유 중 최저 손익률 (가장 큰 평가손실, 다이아몬드 핸드)
    worst_holding_pct = min((p.get("pnl_pct") or 0) for p in positions) if positions else 0
    # 최대 연속 익절 (시간순 매도)
    sorted_sells = sorted(sells, key=lambda s: s.timestamp or datetime.min)
    max_win_streak = _cur_streak = 0
    for s in sorted_sells:
        if (s.realized_pnl_krw or 0) > 0:
            _cur_streak += 1
            max_win_streak = max(max_win_streak, _cur_streak)
        else:
            _cur_streak = 0
    # 현금 비중 (풀베팅)
    _total = dashboard_summary["total_assets_krw"]
    cash_ratio = ((user.cash_balance or 0) / _total) if _total > 0 else 1.0
    # 매수 당일 매도 (광속 매매) — 같은 종목 같은 날 buy+sell
    _buy_days, _sell_days = {}, {}
    for t in txs:
        if not t.timestamp:
            continue
        d = t.timestamp.date()
        (_buy_days if t.type == "buy" else _sell_days).setdefault(t.ticker, set()).add(d)
    same_day_flip = any(_buy_days.get(tk, set()) & days for tk, days in _sell_days.items())

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
        # 재미 배지용
        "night_owl_count":         night_owl_count,
        "max_single_trade_krw":    max_single_trade_krw,
        "has_single_share":        has_single_share,
        "max_same_ticker_buys":    max_same_ticker_buys,
        "unique_tickers_traded":   unique_tickers_traded,
        "worst_holding_pct":       worst_holding_pct,
        "max_win_streak":          max_win_streak,
        "cash_ratio":              cash_ratio,
        "same_day_flip":           same_day_flip,
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
    try:
        splits.check_user(user.id)          # 하루 1회: 보유 종목에 미반영 액면분할이 있으면 수량·단가 보정
    except Exception as e:
        app.logger.warning(f"[SPLIT] 대시보드 점검 실패: {e}")
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

        # 종목별 최근 포지션 노트 (최신 거래 메모 1개)
        latest_notes = {}
        note_txs = Transaction.query.filter(
            Transaction.user_id == user.id,
            Transaction.ticker.in_(tickers),
            Transaction.note.isnot(None),
        ).order_by(Transaction.timestamp.desc()).all()
        for t in note_txs:
            latest_notes.setdefault(t.ticker, t.note)

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
                "note":           latest_notes.get(h.ticker),
                "target_price":   h.target_price,
                "stop_loss_price": h.stop_loss_price,
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
            "max_drawdown_pct": _max_drawdown_pct(user.id),
        },
        "real_holdings_count": Holding.query.filter_by(user_id=user.id).count(),
        "newly_earned_badges": new_badges,   # 이번 호출에 새로 받은 배지 키 목록
        "nickname":            user.nickname,
    })


@app.route("/api/realtime-price")
@login_required
def realtime_price():
    """종목 실시간 단가 (모의 매수/매도 모달 1초 폴링용).
    Returns: { ticker, price, currency } / 실패 시 price=null
    """
    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker 필요"}), 400
    price = _fetch_current_price(ticker)
    currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "USD"
    # 정규장 시간인지 (모달의 '실시간' / '장 마감 종가' 배지). 공휴일은 판단하지 못한다
    try:
        market_open = trend_setups.session_progress("KR" if currency == "KRW" else "US") is not None
    except Exception:
        market_open = None
    return jsonify({
        "ticker": ticker,
        "price": round(float(price), 6) if price else None,
        "currency": currency,
        "market_open": market_open,
    })


PAPER_FEE_RATE = 0.001        # 사용자 모의투자 수수료 0.1% (봇은 0.15%를 따로 넘긴다)


def _stock_db_name(ticker, fallback):
    for s in STOCK_DB:
        if s.get("symbol") == ticker:
            return s.get("name") or fallback
    return fallback


def paper_buy(user, ticker, name, qty, price, fee_rate=PAPER_FEE_RATE, note=None, fx=None, commit=True):
    """모의투자 매수 공용 로직 (사용자 화면 매수 · AI 봇 자동 매수가 함께 쓴다).
    price 는 호출 측이 정한 체결가(사용자: 서버 실시간가, 봇: 신호 체결가). → (Transaction, None) | (None, 오류 문구)"""
    if user.cash_balance is None:
        user.cash_balance = INITIAL_CAPITAL_KRW
    currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "USD"
    if name == ticker:
        name = _stock_db_name(ticker, name)
    fx = fx or _get_usd_krw_rate()
    amount_native = price * qty
    amount_krw    = amount_native * (fx if currency == "USD" else 1)
    fee_krw       = round(amount_krw * fee_rate)
    total_krw     = amount_krw + fee_krw
    if user.cash_balance < total_krw:
        return None, f"현금 부족 (필요 {round(total_krw):,}원, 보유 {round(user.cash_balance):,}원)"

    user.cash_balance -= total_krw
    existing = PaperHolding.query.filter_by(user_id=user.id, ticker=ticker).first()
    if existing:
        new_qty = existing.quantity + qty
        avg     = (existing.purchase_price * existing.quantity + price * qty) / new_qty
        existing.quantity       = round(new_qty, 6)
        existing.purchase_price = round(avg, 6)
        existing.name           = name
        existing.currency       = currency
    else:
        db.session.add(PaperHolding(user_id=user.id, ticker=ticker, name=name,
                                    quantity=qty, purchase_price=price, currency=currency))
    tx = Transaction(
        user_id=user.id, ticker=ticker, name=name,
        type="buy", price=price, quantity=qty,
        currency=currency, exchange_rate=(fx if currency == "USD" else 1.0),
        fee_krw=fee_krw, amount_krw=round(amount_krw),
        realized_pnl_krw=0, note=note,
    )
    db.session.add(tx)
    if commit:
        db.session.commit()
    return tx, None


def paper_sell(user, ticker, qty, price, fee_rate=PAPER_FEE_RATE, note=None, fx=None, commit=True):
    """모의투자 매도 공용 로직. → (Transaction, None) | (None, 오류 문구)"""
    if user.cash_balance is None:
        user.cash_balance = INITIAL_CAPITAL_KRW
    holding = PaperHolding.query.filter_by(user_id=user.id, ticker=ticker).first()
    if not holding:
        return None, "보유하지 않은 종목입니다"
    if qty > holding.quantity:
        return None, f"보유 수량 초과 (보유 {holding.quantity}, 매도 시도 {qty})"
    currency = holding.currency or "USD"
    fx = fx or _get_usd_krw_rate()
    amount_native = price * qty
    amount_krw    = amount_native * (fx if currency == "USD" else 1)
    fee_krw       = round(amount_krw * fee_rate)
    proceeds_krw  = amount_krw - fee_krw
    # 실현 손익 = (매도가 - 매입가) * 수량 (KRW 환산)
    pnl_native = (price - holding.purchase_price) * qty
    pnl_krw    = pnl_native * (fx if currency == "USD" else 1)
    user.cash_balance += proceeds_krw
    hold_name = holding.name
    if qty >= holding.quantity:
        db.session.delete(holding)
    else:
        holding.quantity = round(holding.quantity - qty, 6)
    tx = Transaction(
        user_id=user.id, ticker=ticker, name=hold_name or ticker,
        type="sell", price=price, quantity=qty,
        currency=currency, exchange_rate=(fx if currency == "USD" else 1.0),
        fee_krw=fee_krw, amount_krw=round(amount_krw),
        realized_pnl_krw=round(pnl_krw), note=note,
    )
    db.session.add(tx)
    if commit:
        db.session.commit()
    return tx, None


@app.route("/api/trading/buy", methods=["POST"])
@login_required
def trading_buy():
    """모의 매수.
    Body: { ticker, name?, quantity, note? }
    ⚠️ 체결가는 클라이언트 입력이 아닌 서버 실시간가로 강제 (수익률 조작 방지).
    """
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    name   = (data.get("name") or "").strip() or ticker
    qty    = float(data.get("quantity") or 0)
    note   = (data.get("note") or "").strip()[:300] or None

    if not ticker or qty <= 0:
        return jsonify({"error": "티커/수량을 올바르게 입력해주세요"}), 400
    targets, terr = _parse_exit_targets(data)
    if terr:
        return jsonify({"error": terr}), 400

    # 체결가 = 서버 실시간가 (클라이언트가 보낸 price 무시 → 조작 차단)
    price = _fetch_current_price(ticker)
    if not price or price <= 0:
        return jsonify({"error": "현재가를 가져올 수 없어 매수할 수 없습니다. 잠시 후 다시 시도해주세요"}), 503
    # 예약은 체결가 기준으로 검증 (목표가 > 체결가 > 손절가). 어긋나면 매수 자체를 하지 않는다
    verr = _validate_exit_targets(targets, float(price))
    if verr:
        return jsonify({"error": verr, "price": float(price)}), 400

    tx, err = paper_buy(current_user, ticker, name, qty, float(price), note=note, commit=False)
    if err:
        return jsonify({"error": err}), 400
    if targets:
        db.session.flush()
        h = PaperHolding.query.filter_by(user_id=current_user.id, ticker=ticker).first()
        for k, v in targets.items():
            setattr(h, k, v)
    db.session.commit()
    h = PaperHolding.query.filter_by(user_id=current_user.id, ticker=ticker).first()
    return jsonify({
        "ok": True,
        "transaction": tx.to_dict(),
        "cash_balance": round(current_user.cash_balance),
        "targets": {"target_price": h.target_price, "stop_loss_price": h.stop_loss_price} if h else None,
    })


def _parse_exit_targets(data):
    """body 의 target_price · stop_loss_price → ({필드: 값|None}, 오류). 키가 없으면 그 필드는 건드리지 않는다.
    빈 문자열·null·0 은 '해제'(None)."""
    import math
    out = {}
    for k in ("target_price", "stop_loss_price"):
        if k not in data:
            continue
        v = data.get(k)
        if v in (None, "", 0, "0"):
            out[k] = None
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None, ("목표가" if k == "target_price" else "손절가") + "는 숫자로 입력해 주세요"
        if not math.isfinite(v) or v <= 0:
            return None, ("목표가" if k == "target_price" else "손절가") + "는 0보다 커야 합니다"
        out[k] = round(v, 4)
    return out, None


def _validate_exit_targets(targets, price):
    """목표가 > 현재가, 손절가 < 현재가. → 오류 문구 | None"""
    t, s = (targets or {}).get("target_price"), (targets or {}).get("stop_loss_price")
    if t is not None and not t > price:
        return f"목표가는 현재가({price:,.2f})보다 높아야 합니다"
    if s is not None and not s < price:
        return f"손절가는 현재가({price:,.2f})보다 낮아야 합니다"
    return None


@app.route("/api/trading/holdings/<path:ticker>/targets", methods=["PATCH", "PUT"])
@login_required
def trading_holding_targets(ticker):
    """보유 종목의 목표가·손절가 수정/해제.
    Body: { target_price?: number|null, stop_loss_price?: number|null }  (보낸 키만 바뀜, null = 해제)"""
    ticker = (ticker or "").strip().upper()
    h = PaperHolding.query.filter_by(user_id=current_user.id, ticker=ticker).first()
    if not h:
        return jsonify({"error": "보유하지 않은 종목입니다"}), 404
    targets, terr = _parse_exit_targets(request.get_json(silent=True) or {})
    if terr:
        return jsonify({"error": terr}), 400
    if any(v is not None for v in targets.values()):
        price = _fetch_current_price(ticker)
        if not price or price <= 0:
            return jsonify({"error": "현재가를 가져올 수 없어 예약을 검증하지 못했습니다. 잠시 후 다시 시도해주세요"}), 503
        verr = _validate_exit_targets({k: v for k, v in targets.items() if v is not None}, float(price))
        if verr:
            return jsonify({"error": verr, "price": float(price)}), 400
    for k, v in targets.items():
        setattr(h, k, v)
    db.session.commit()
    return jsonify({"ok": True, "ticker": ticker, "target_price": h.target_price, "stop_loss_price": h.stop_loss_price})


def paper_auto_exits(market, quotes=None):
    """장중 5분 체크(bot_engine 스케줄러)에서 호출: 목표가·손절가가 예약된 사용자 모의 보유 종목만 시세 확인 → 전량 자동 매도.
    예약이 하나도 없으면 쿼리 1번 후 바로 끝 (시세 조회 없음). quotes 는 테스트용 {ticker: 현재가}.
    중복 체결 방지: 체결 직전에 행을 잠그고(PostgreSQL FOR UPDATE) 예약·수량을 다시 확인한 뒤 예약을 지우고 매도한다."""
    is_kr = lambda t: t.upper().endswith((".KS", ".KQ"))
    rows = (PaperHolding.query
            .filter((PaperHolding.target_price.isnot(None)) | (PaperHolding.stop_loss_price.isnot(None)))
            .with_entities(PaperHolding.id, PaperHolding.ticker).all())
    rows = [r for r in rows if is_kr(r.ticker) == (market == "KR")]
    if not rows:
        return {"checked": 0, "closed": 0}
    tickers = sorted({r.ticker for r in rows})
    if quotes is None:
        # 토스 1회 배치(20초 공용 캐시) → 없는 종목만 기존 경로(토스 단건 → 네이버 → yfinance)
        quotes = {}
        try:
            quotes = {t: float(i["price"]) for t, i in toss_api.get_prices_cached(tickers).items() if i.get("price")}
        except Exception:
            pass
        rest = [t for t in tickers if t not in quotes]
        if rest:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(rest))) as ex:
                quotes.update(dict(zip(rest, ex.map(_fetch_current_price, rest))))
    # 오손절 방어: 매입가 대비 -40% 이상(분할 비율만큼 떨어진 것처럼 보임)이면 먼저 액면분할 여부를 확인.
    # 분할이면 수량·매입가·목표가·손절가를 보정하고 이번 회차 자동 청산은 건너뛴다 (다음 5분 체크에서 보정값으로 판정)
    split_hit = set()
    try:
        held = PaperHolding.query.filter(PaperHolding.id.in_([r.id for r in rows])).all()
        # 같은 종목을 여러 사람이 들고 있을 수 있어 보유 건마다 본다 (한 건이라도 -40% 이하면 확인)
        sus = sorted({h.ticker for h in held if h.purchase_price and quotes.get(h.ticker)
                      and quotes[h.ticker] <= h.purchase_price * splits.DROP_GUARD})
        if sus:
            split_hit = splits.guard_drop(sus)
            if split_hit:
                app.logger.info(f"[paper_exit] 액면분할 반영 → 이번 회차 자동 청산 건너뜀: {sorted(split_hit)}")
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[paper_exit] 분할 확인 실패: {e}")
    # 이중 방어: yfinance splits 가 아직 비어 있어도 (a) 가격 기록이 분할을 가리키면 보류, (b) 첫 감지면 1회 보류 후 재판정
    hold_ids = set()
    try:
        for h in PaperHolding.query.filter(PaperHolding.id.in_([r.id for r in rows])).all():
            px = quotes.get(h.ticker)
            if (h.ticker in split_hit or not px or h.stop_loss_price is None or px > h.stop_loss_price
                    or not h.purchase_price or px > h.purchase_price * splits.DROP_GUARD):
                continue
            first = (Transaction.query.filter(Transaction.user_id == h.user_id, Transaction.ticker == h.ticker,
                                              db.func.lower(Transaction.type) == "buy")
                     .order_by(Transaction.timestamp).first())
            bdt = (first.timestamp if first else h.created_at)
            verdict, why = splits.drop_decision("paper", h.id, h.ticker, h.purchase_price, bdt.date() if bdt else None, px)
            if verdict == "hold":
                hold_ids.add(h.id)
                app.logger.info(f"[paper_exit] {h.ticker} #{h.id} 손절 보류: {why}")
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[paper_exit] 급락 보류 판정 실패: {e}")
    fx = _get_usd_krw_rate()
    closed = []
    for r in rows:
        px = quotes.get(r.ticker)
        if not px or px <= 0 or r.ticker in split_hit or r.id in hold_ids:
            continue
        try:
            h = PaperHolding.query.filter_by(id=r.id).with_for_update().first()
            if not h or not h.quantity or h.quantity <= 0:
                db.session.rollback()
                continue
            kind = None
            if h.target_price is not None and px >= h.target_price:
                kind = "익절"
            elif h.stop_loss_price is not None and px <= h.stop_loss_price:
                kind = "손절"
            if not kind:
                db.session.rollback()
                continue
            user = User.query.get(h.user_id)
            level = h.target_price if kind == "익절" else h.stop_loss_price
            pnl_pct = (px / h.purchase_price - 1) * 100 if h.purchase_price else 0.0
            ticker, name, qty, cur = h.ticker, h.name, h.quantity, h.currency or "USD"
            h.target_price = h.stop_loss_price = None          # 같은 예약으로 두 번 팔지 않게 먼저 지운다
            fmt = (lambda v: f"{round(v):,}원") if cur == "KRW" else (lambda v: f"${v:,.2f}")
            tx, err = paper_sell(user, ticker, qty, float(px), fx=fx, commit=False,
                                 note=f"[자동 {kind}] {'목표가' if kind == '익절' else '손절가'} {fmt(level)} 도달")
            if err:
                db.session.rollback()
                continue
            db.session.commit()
            closed.append((user.id, ticker, name, kind, pnl_pct, px, tx.realized_pnl_krw, cur, qty))
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f"[paper_exit] {r.ticker} 실패: {e}")
    for uid, ticker, name, kind, pnl_pct, px, pnl_krw, cur, qty in closed:
        fmt_px = f"{round(px):,}원" if cur == "KRW" else f"${px:,.2f}"
        label = ticker if cur != "KRW" else name
        try:
            notifier.notify_user(uid, "paper_exit", f"[모의투자 자동 {kind}] {label} {pnl_pct:+.1f}% 체결",
                                 f"{name} {qty:g}주 · 체결가 {fmt_px} · 실현 손익 {int(round(pnl_krw or 0)):+,}원",
                                 "/trading", tag=f"paper-exit-{ticker}")
        except Exception as e:
            app.logger.warning(f"[paper_exit] 알림 실패 user={uid}: {e}")
    if closed:
        app.logger.info(f"[paper_exit] {market} 자동 청산 {len(closed)}건")
    return {"checked": len(rows), "closed": len(closed)}


@app.route("/api/trading/sell", methods=["POST"])
@login_required
def trading_sell():
    """모의 매도.
    Body: { ticker, quantity, note? }
    ⚠️ 체결가는 클라이언트 입력이 아닌 서버 실시간가로 강제 (수익률 조작 방지).
    """
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    qty    = float(data.get("quantity") or 0)
    note   = (data.get("note") or "").strip()[:300] or None

    if not ticker or qty <= 0:
        return jsonify({"error": "티커/수량을 올바르게 입력해주세요"}), 400

    holding = PaperHolding.query.filter_by(user_id=current_user.id, ticker=ticker).first()
    if not holding:
        return jsonify({"error": "보유하지 않은 종목입니다"}), 400
    if qty > holding.quantity:
        return jsonify({
            "error": f"보유 수량 초과 (보유 {holding.quantity}, 매도 시도 {qty})"
        }), 400

    # 체결가 = 서버 실시간가 (클라이언트가 보낸 price 무시 → 조작 차단)
    price = _fetch_current_price(ticker)
    if not price or price <= 0:
        return jsonify({"error": "현재가를 가져올 수 없어 매도할 수 없습니다. 잠시 후 다시 시도해주세요"}), 503

    tx, err = paper_sell(current_user, ticker, qty, float(price), note=note)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({
        "ok": True,
        "transaction": tx.to_dict(),
        "cash_balance": round(current_user.cash_balance),
        "realized_pnl_krw": tx.realized_pnl_krw,
    })


@app.route("/api/trading/transactions")
@login_required
def trading_transactions():
    """거래 내역 (최신순)."""
    limit = min(int(request.args.get("limit", 50)), 200)
    txs = Transaction.query.filter_by(user_id=current_user.id)\
            .order_by(Transaction.timestamp.desc()).limit(limit).all()
    return jsonify([t.to_dict() for t in txs])


# ── AI 매매 코치 ────────────────────────────────────────────
# 허용 문자: ASCII(영문·숫자·문장부호), 한글(음절·자모), 일반 문장부호(·…—– 따옴표), ₩
_COACH_FOREIGN_RE = re.compile(
    "[^ -~가-힣ㄱ-ㆎ"
    "·–—‘’“”…₩\n]+"
)
def _coach_stats_block(txs):
    """거래 목록에서 결정적 통계를 계산해 프롬프트용 텍스트 블록 생성.
    FIFO로 매수-매도를 짝지어 라운드트립별 보유기간·손익을 구한다.
    (해석을 AI에 맡기지 않고 수치를 직접 계산해야 피드백이 구체적이 됨)
    """
    chrono = sorted(txs, key=lambda t: t.timestamp or datetime.min)
    buys  = [t for t in chrono if t.type == "buy"]
    sells = [t for t in chrono if t.type == "sell"]
    total_fees = sum((t.fee_krw or 0) for t in chrono)

    # FIFO 라운드트립 매칭
    lots, trips = {}, []
    for t in chrono:
        if t.type == "buy":
            lots.setdefault(t.ticker, []).append([t.quantity, t.price, t.timestamp])
            continue
        fxk = (t.exchange_rate or 1.0) if t.currency == "USD" else 1.0
        remain = t.quantity
        q = lots.get(t.ticker) or []
        while remain > 1e-9 and q:
            lot = q[0]
            take = min(lot[0], remain)
            days = None
            if t.timestamp and lot[2]:
                days = max(0, (t.timestamp - lot[2]).days)
            trips.append({
                "ticker": t.ticker, "name": t.name, "days": days,
                "pnl": (t.price - lot[1]) * take * fxk,
            })
            lot[0] -= take
            remain -= take
            if lot[0] <= 1e-9:
                q.pop(0)

    out = [f"[통계] (단위: KRW)"]
    out.append(f"- 총 거래 {len(chrono)}건 (매수 {len(buys)}, 매도 {len(sells)}), "
               f"수수료 합계 {round(total_fees):,}원")

    if trips:
        wins = [r for r in trips if r["pnl"] > 0]
        win_rate = len(wins) / len(trips) * 100
        total_pnl = sum(r["pnl"] for r in trips)
        out.append(f"- 완결 매매(매수→매도 짝) {len(trips)}건: 승 {len(wins)} / "
                   f"패 {len(trips)-len(wins)} (승률 {win_rate:.0f}%), "
                   f"실현손익 합 {round(total_pnl):+,}원")

        dated = [r for r in trips if r["days"] is not None]
        if dated:
            avg_days = sum(r["days"] for r in dated) / len(dated)
            short  = [r for r in dated if r["days"] < 3]
            longer = [r for r in dated if r["days"] >= 3]
            def _seg(rs):
                if not rs:
                    return "0건"
                w = sum(1 for r in rs if r["pnl"] > 0)
                return (f"{len(rs)}건 승률 {w/len(rs)*100:.0f}% "
                        f"손익 {round(sum(r['pnl'] for r in rs)):+,}원")
            out.append(f"- 평균 보유 {avg_days:.1f}일 / "
                       f"3일 미만 단타: {_seg(short)} / 3일 이상: {_seg(longer)}")

        best = max(trips, key=lambda r: r["pnl"])
        worst = min(trips, key=lambda r: r["pnl"])
        out.append(f"- 최고 수익 매매: {best['name']}({best['ticker']}) "
                   f"{round(best['pnl']):+,}원 (보유 {best['days']}일) / "
                   f"최대 손실 매매: {worst['name']}({worst['ticker']}) "
                   f"{round(worst['pnl']):+,}원 (보유 {worst['days']}일)")

        # 종목별 회전 (거래 많은 순 TOP3)
        per = {}
        for r in trips:
            d = per.setdefault(r["ticker"], {"name": r["name"], "n": 0, "pnl": 0.0})
            d["n"] += 1
            d["pnl"] += r["pnl"]
        top = sorted(per.values(), key=lambda d: -d["n"])[:3]
        out.append("- 같은 종목 반복 매매 TOP3: " + ", ".join(
            f"{d['name']} {d['n']}회(손익 {round(d['pnl']):+,}원)" for d in top))

    noted = [t for t in chrono if t.note]
    out.append(f"- 메모 있는 거래 {len(noted)}건 / 전체 {len(chrono)}건")
    if noted and trips:
        noted_tickers = {t.ticker for t in noted if t.type == "buy"}
        nt = [r for r in trips if r["ticker"] in noted_tickers]
        if nt:
            out.append(f"- 메모를 남긴 종목의 완결 매매 {len(nt)}건 "
                       f"손익 합 {round(sum(r['pnl'] for r in nt)):+,}원")
    return "\n".join(out)


_COACH_CACHE = {}            # user_id -> {"text", "ts", "iso", "tx_count"}
_COACH_TTL      = 6 * 3600   # 같은 거래 수면 6시간 캐시
_COACH_COOLDOWN = 10 * 60    # 강제 재분석 최소 간격 10분
_COACH_MIN_TX   = 5


@app.route("/api/trading/coach", methods=["GET", "POST"])
@login_required
def trading_coach():
    """매매 기록(+포지션 노트)을 AI에 보내 패턴 피드백 생성.
    GET: 캐시된 분석만 반환 (없으면 204) — 페이지 로드 시 표시용.
    POST: 새 분석 생성 (캐시/쿨다운 적용).
    """
    user = current_user
    cached = _COACH_CACHE.get(user.id)
    now = time.time()

    if request.method == "GET":
        if cached and (now - cached["ts"] < _COACH_TTL):
            return jsonify({"ok": True, "feedback": cached["text"],
                            "generated_at": cached["iso"], "cached": True})
        return ("", 204)

    txs = Transaction.query.filter_by(user_id=user.id)\
            .order_by(Transaction.timestamp.desc()).limit(200).all()
    if len(txs) < _COACH_MIN_TX:
        return jsonify({
            "error": f"거래가 {_COACH_MIN_TX}건 이상 쌓이면 분석할 수 있어요 (현재 {len(txs)}건)"
        }), 400

    # 거래 수 그대로면 캐시 반환, 쿨다운 내 재요청도 캐시 반환
    if cached:
        same_data = cached.get("tx_count") == len(txs) and (now - cached["ts"] < _COACH_TTL)
        in_cooldown = (now - cached["ts"]) < _COACH_COOLDOWN
        if same_data or in_cooldown:
            return jsonify({"ok": True, "feedback": cached["text"],
                            "generated_at": cached["iso"], "cached": True})

    if not (os.environ.get("GEMINI_API_KEY", "").strip()
            or os.environ.get("GROQ_API_KEY", "").strip()):
        return jsonify({"error": "AI 분석을 사용할 수 없습니다 (API 키 미설정)"}), 503

    stats_block = _coach_stats_block(txs)

    # 최근 거래 20건만 원본 첨부 (통계가 본체, 원본은 참고)
    lines = []
    for t in list(reversed(txs))[-20:]:
        d = t.timestamp.strftime("%m/%d") if t.timestamp else "?"
        pnl  = f" 실현손익 {round(t.realized_pnl_krw or 0):+,}원" if t.type == "sell" else ""
        note = f' 메모:"{t.note}"' if t.note else ""
        lines.append(
            f"{d} {'매도' if t.type == 'sell' else '매수'} {t.name}({t.ticker}) "
            f"{t.quantity}주 @{t.price:,g} {t.currency}{pnl}{note}"
        )

    system_msg = (
        "너는 모의투자 사용자의 매매 기록을 분석하는 한국어 투자 코치다. "
        "투자 권유나 종목 추천은 절대 하지 않는다. 매매 '습관과 패턴'에 대해서만 피드백한다.\n"
        "규칙:\n"
        "1. 모든 피드백은 [통계] 블록의 구체적 수치를 반드시 인용한다.\n"
        "2. '모니터링이 필요합니다', '주의가 필요합니다', '도움이 될 수 있습니다', "
        "'영향을 미치는 요인을 분석할 필요가 있습니다' 같은 모호한 마무리는 금지. "
        "각 항목은 사용자가 다음 매매에서 시도할 수 있는 구체적 행동 하나로 끝낸다.\n"
        "3. 통계에서 손익에 가장 크게 기여한/깎은 패턴부터 우선 다룬다. "
        "단순 사실 나열(거래가 많았다 등)만으로 한 항목을 쓰지 않는다.\n"
        "4. 출력: 피드백 2~4개, 각 줄 '- '로 시작하는 목록만. 항목당 1~2문장, 존댓말. "
        "마크다운 강조(**) 금지.\n"
        "5. 한국어로만 작성 (종목명·티커는 영문 허용). "
        "러시아어·중국어·일본어 등 다른 언어 문자 절대 금지."
    )
    user_msg = stats_block + "\n\n[최근 거래 20건]\n" + "\n".join(lines)

    text = _ai_chat(system_msg, user_msg,
                    max_tokens=600, temperature=0.5, label="coach")
    if not text:
        return jsonify({"error": "AI 분석 생성에 실패했습니다. 잠시 후 다시 시도해주세요."}), 502

    # 다국어 모델이 흘리는 외국어 토큰(러시아어 등) 제거 — 한글/영문/숫자/문장부호만 허용
    text = _COACH_FOREIGN_RE.sub("", text)
    text = re.sub(r"  +", " ", text).strip()

    iso = datetime.utcnow().isoformat() + "Z"
    _COACH_CACHE[user.id] = {"text": text, "ts": now, "iso": iso, "tx_count": len(txs)}
    return jsonify({"ok": True, "feedback": text, "generated_at": iso, "cached": False})


# ── AI DCF 해설 ────────────────────────────────────────────
# 숫자는 valuation.py 가 결정적으로 계산. AI는 '해석'만 한다.


@app.route("/api/dcf/comment")
def dcf_comment():
    """밸류에이션 AI 해설. 이제 종목 인사이트 통합 호출(/api/analyze/extra)이 만들어 캐시에 둔 것을 돌려준다.
    (예전처럼 여기서 따로 AI 를 부르지 않는다 → 종목당 호출 1회 절약)"""
    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker 필요"}), 400
    hit = ai_cache.get(f"daily_insight:{ticker}")
    text = (hit[0] or {}).get("valuation_comment") if hit else None
    if not text:
        return jsonify({"error": "아직 해설이 없습니다"}), 404
    return jsonify({"ok": True, "comment": text, "cached": True})


# ── AI 포트폴리오 점검 ──────────────────────────────────────
_REVIEW_CACHE = {}             # (user_id, mode) -> {"text", "ts", "iso"}
_REVIEW_COOLDOWN = 10 * 60     # 10분


@app.route("/api/portfolio/review", methods=["GET", "POST"])
@login_required
def portfolio_review():
    """포트폴리오 점검: 비중·집중도·통화 편중·손익 분포를 계산해 AI 의견 생성.
    mode=paper(모의투자) | real(실제 포트폴리오)
    GET: 캐시만 반환 (없으면 204). POST: 새 점검 생성 (10분 쿨다운).
    """
    if request.method == "GET":
        mode = (request.args.get("mode") or "paper").strip()
    else:
        mode = ((request.get_json(silent=True) or {}).get("mode") or "paper").strip()
    if mode not in ("paper", "real"):
        return jsonify({"error": "mode는 paper 또는 real이어야 합니다"}), 400

    user = current_user
    key = (user.id, mode)
    cached = _REVIEW_CACHE.get(key)
    now = time.time()

    if request.method == "GET":
        if cached and (now - cached["ts"] < _REVIEW_COOLDOWN * 6):   # 1시간 표시 유지
            return jsonify({"ok": True, "feedback": cached["text"],
                            "generated_at": cached["iso"], "cached": True})
        return ("", 204)

    if cached and (now - cached["ts"] < _REVIEW_COOLDOWN):
        return jsonify({"ok": True, "feedback": cached["text"],
                        "generated_at": cached["iso"], "cached": True})

    Model = PaperHolding if mode == "paper" else Holding
    holdings = Model.query.filter_by(user_id=user.id).all()
    if len(holdings) < 2:
        return jsonify({"error": "보유 종목이 2개 이상일 때 점검할 수 있어요 "
                                 f"(현재 {len(holdings)}개)"}), 400

    if not (os.environ.get("GEMINI_API_KEY", "").strip()
            or os.environ.get("GROQ_API_KEY", "").strip()):
        return jsonify({"error": "AI 분석을 사용할 수 없습니다 (API 키 미설정)"}), 503

    # 현재가 + 평가액 계산
    fx = _get_usd_krw_rate()
    tickers = list({h.ticker for h in holdings})
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        prices = dict(ex.map(lambda t: (t, _fetch_current_price(t)), tickers))

    rows = []
    for h in holdings:
        cp = prices.get(h.ticker) or h.purchase_price
        cur = h.currency or "USD"
        value_krw = cp * h.quantity * (fx if cur == "USD" else 1)
        pnl_pct = ((cp - h.purchase_price) / h.purchase_price * 100) if h.purchase_price else 0
        rows.append({"name": h.name, "ticker": h.ticker, "currency": cur,
                     "value": value_krw, "pnl_pct": pnl_pct})

    pos_total = sum(r["value"] for r in rows) or 1
    cash = (user.cash_balance or 0) if mode == "paper" else None
    grand = pos_total + (cash or 0)
    rows.sort(key=lambda r: -r["value"])

    krw_w = sum(r["value"] for r in rows if r["currency"] == "KRW") / pos_total * 100
    top1_w = rows[0]["value"] / pos_total * 100
    top3_w = sum(r["value"] for r in rows[:3]) / pos_total * 100

    lines = [f"[포트폴리오] ({'모의투자' if mode == 'paper' else '실제 보유'}, "
             f"종목 {len(rows)}개, 주식 평가액 {round(pos_total):,}원)"]
    if cash is not None:
        lines.append(f"- 현금 {round(cash):,}원 (총자산의 {cash/grand*100:.1f}%)")
    lines.append(f"- 집중도: 1위 종목 {top1_w:.1f}%, 상위 3종목 {top3_w:.1f}% (주식 내 비중)")
    lines.append(f"- 통화: 한국주식 {krw_w:.1f}% / 미국주식 {100-krw_w:.1f}%")
    lines.append("- 종목별 (비중은 주식 내, 수익률은 평가손익):")
    for r in rows:
        lines.append(f"  · {r['name']}({r['ticker']}) {r['value']/pos_total*100:.1f}% "
                     f"수익률 {r['pnl_pct']:+.1f}%")

    system_msg = (
        "너는 개인 포트폴리오 점검 코치다. 새 종목 매수 추천은 절대 하지 않는다. "
        "대신 '현재 보유 중인 종목'에 한해 비중 확대/축소/유지 의견은 수치 근거와 함께 제시할 수 있다.\n"
        "점검 관점: 1) 특정 종목 집중도 2) 한/미 통화 편중 3) 현금 비중(있는 경우) "
        "4) 손익 분포 (큰 손실 종목 방치, 수익 종목 비중 등).\n"
        "규칙:\n"
        "1. 모든 의견은 [포트폴리오] 블록의 구체적 수치를 인용한다.\n"
        "2. '모니터링 필요', '주의가 필요' 같은 모호한 마무리 금지. "
        "각 항목은 구체적이고 실행 가능한 의견으로 끝낸다.\n"
        "3. 출력: 2~4개 항목, 각 줄 '- '로 시작하는 목록만. 항목당 1~2문장, 존댓말. "
        "마크다운 강조(**) 금지.\n"
        "4. 한국어로만 작성 (종목명·티커는 영문 허용). "
        "러시아어·중국어·일본어 등 다른 언어 문자 절대 금지."
    )

    text = _ai_chat(system_msg, "\n".join(lines),
                    max_tokens=600, temperature=0.4, label="review")
    if not text:
        return jsonify({"error": "AI 점검 생성에 실패했습니다. 잠시 후 다시 시도해주세요."}), 502

    text = _COACH_FOREIGN_RE.sub("", text)
    text = re.sub(r"  +", " ", text).strip()

    iso = datetime.utcnow().isoformat() + "Z"
    _REVIEW_CACHE[key] = {"text": text, "ts": now, "iso": iso}
    return jsonify({"ok": True, "feedback": text, "generated_at": iso, "cached": False})


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

    snap_dates = [s["date"] for s in snap_series if s["date"]]
    return jsonify({
        "snapshots":           snap_series,
        "initial_capital_krw": round(initial),
        "benchmarks": {
            "kospi": _ffill_benchmark(_normalize_benchmark(kospi_hist, start_date_str), snap_dates),
            "sp500": _ffill_benchmark(_normalize_benchmark(sp500_hist, start_date_str), snap_dates),
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


# ── 랭킹 실시간 평가 (본인 접속과 무관하게 전 참가자 수익률 갱신) ──
_RT_TOTAL_CACHE = {"ts": 0.0, "data": {}}   # {user_id: total_assets_krw}
_RT_TOTAL_TTL = 90                          # 90초 — 시세 일괄 조회 부하 완화

def _realtime_total_map(users):
    """랭킹 후보 유저들의 현재 총자산(KRW) 맵을 실시간 계산.
    모든 보유종목을 모아 고유 티커만 일괄 시세 조회 → 유저별 (현금 + 평가금액).
    90초 캐시 (누가 랭킹을 열든 그 시점 최신가로 전원 재평가)."""
    now = time.time()
    if _RT_TOTAL_CACHE["data"] and (now - _RT_TOTAL_CACHE["ts"]) < _RT_TOTAL_TTL:
        return _RT_TOTAL_CACHE["data"]

    fx = _get_usd_krw_rate()
    user_ids = [u.id for u in users]
    holdings = PaperHolding.query.filter(PaperHolding.user_id.in_(user_ids)).all() if user_ids else []

    # 고유 티커만 병렬 시세 조회 (dashboard와 동일 패턴, _fetch_current_price는 3초 캐시)
    tickers = list({h.ticker for h in holdings})
    prices = {}
    if tickers:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            prices = dict(ex.map(lambda t: (t, _fetch_current_price(t)), tickers))

    pos_value = {}
    holders = set()
    for h in holdings:
        holders.add(h.user_id)
        cp = prices.get(h.ticker) or 0
        val_native = cp * h.quantity
        val_krw = val_native if (h.currency or "USD") == "KRW" else val_native * fx
        pos_value[h.user_id] = pos_value.get(h.user_id, 0.0) + val_krw

    out = {}
    for u in users:
        cash = u.cash_balance if u.cash_balance is not None else INITIAL_CAPITAL_KRW
        out[u.id] = {
            "total":        cash + pos_value.get(u.id, 0.0),
            "has_holdings": u.id in holders,
        }

    _RT_TOTAL_CACHE["ts"] = now
    _RT_TOTAL_CACHE["data"] = out
    return out


@app.route("/api/leaderboard")
def leaderboard():
    """전체 랭킹.
    Query: metric=total|7d|30d (default total), limit=50
    Returns: [{rank, nickname, profile_image, return_pct, total_assets_krw, is_me}]
    수익률은 실시간 평가 — 각 참가자의 보유종목을 현재가로 재계산하므로
    본인 로그인 여부와 무관하게 항상 최신.
    """
    from datetime import date, timedelta
    metric = (request.args.get("metric") or "total").lower()
    limit  = min(int(request.args.get("limit", 50)), 100)
    if metric not in ("total", "7d", "30d"):
        metric = "total"
    include_bots = (request.args.get("bots", "1") != "0")     # AI 봇 포함(기본) / 유저만

    # 닉네임 설정 + 공개 유저만
    users = User.query.filter(
        User.nickname.isnot(None),
        User.is_public.is_(True),
    ).all()

    # 전 참가자 현재 총자산 실시간 평가 (본인 접속과 무관하게 갱신)
    totals = _realtime_total_map(users)

    rows = []
    for u in users:
        info = totals.get(u.id)
        if not info:
            continue
        cur_total = info["total"]
        initial = u.initial_capital or INITIAL_CAPITAL_KRW
        is_bot = (u.provider == "bot")
        if is_bot and not include_bots:
            continue
        # 보유 종목 없고 초기금(1억) 그대로면 = 사실상 미참여 → 랭킹 제외 (AI 봇은 첫 거래 전에도 표시)
        if not is_bot and not info["has_holdings"] and abs(cur_total - initial) < 1:
            continue
        if metric == "total":
            ret_pct = (cur_total / initial - 1) * 100 if initial > 0 else 0
            total_krw = cur_total
        else:
            # 시작점은 과거 스냅샷, 끝점은 실시간 총자산 (끝점은 본인 접속 무관 갱신)
            days = 7 if metric == "7d" else 30
            cutoff = date.today() - timedelta(days=days)
            start_snap = AssetSnapshot.query.filter(
                AssetSnapshot.user_id == u.id,
                AssetSnapshot.date >= cutoff,
            ).order_by(AssetSnapshot.date.asc()).first()
            if not start_snap and is_bot:
                start_snap = type("S", (), {"total_assets_krw": initial})()   # 봇은 스냅샷이 쌓이기 전이면 시작 자본 기준
            if not start_snap:
                continue
            start_v = start_snap.total_assets_krw
            end_v   = cur_total
            if start_v <= 0:
                continue
            ret_pct = (end_v / start_v - 1) * 100
            total_krw = end_v
        # 방어: 비현실적/손상 데이터 제외 (모의투자는 1억 시작 → +100000%(1000배) 초과는 쓰레기)
        import math
        if ret_pct is None or math.isnan(ret_pct) or math.isinf(ret_pct):
            continue
        if ret_pct > 100000 or total_krw > 1e13:   # 100조원 초과 = 손상 데이터
            app.logger.warning(f"[leaderboard] 비정상 데이터 제외: user={u.id} ret={ret_pct}")
            continue
        rows.append({
            "user_id":          u.id,
            "nickname":         u.nickname,
            "profile_image":    u.profile_image,
            "return_pct":       round(ret_pct, 2),
            "total_assets_krw": round(total_krw),
            "reset_count":      u.reset_count or 0,
            "is_bot":           is_bot,
            "bot_key":          u.provider_id if is_bot else None,
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
        "include_bots": include_bots,
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
    user.reset_count     = (user.reset_count or 0) + 1
    db.session.commit()
    return jsonify({"ok": True, "cash_balance": INITIAL_CAPITAL_KRW, "reset_count": user.reset_count})


def _is_toss_owner() -> bool:
    """TOSS_OWNER_EMAIL / TOSS_OWNER_USER_IDS 기준 소유자 여부.
    소유자 목록이 비어 있으면 로컬 개발 환경에서만 허용 (운영에선 차단)."""
    owner_emails = {e.strip() for e in os.environ.get("TOSS_OWNER_EMAIL", "").lower().split(",") if e.strip()}
    owner_ids    = {i.strip() for i in os.environ.get("TOSS_OWNER_USER_IDS", "").split(",") if i.strip()}
    if not (owner_emails or owner_ids):
        return not IS_PRODUCTION      # 소유자 미지정: 로컬 개발에서만 허용
    if not current_user.is_authenticated:
        return False
    my_email = (current_user.email or "").strip().lower()
    return bool(my_email and my_email in owner_emails) or (str(current_user.id) in owner_ids)


@app.route("/api/debug/ai")
def debug_ai():
    """AI 진단: 키 설정 여부(값은 숨김), 선택된 모델, 최근 오류. 소유자 전용 (외부인에겐 404).
    ?refresh=1 이면 모델 목록을 다시 조회한다."""
    if not _is_toss_owner():
        return jsonify({"error": "Not Found"}), 404
    if request.args.get("refresh") == "1":
        _AI_MODEL_CACHE.clear()
    out = {}
    for provider, env in (("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY")):
        key = os.environ.get(env, "").strip()
        info = {"key_set": bool(key)}
        if key:
            info["models"] = _ai_models(provider, key)
            hit = _AI_MODEL_CACHE.get(provider)
            info["source"] = hit[2] if hit else ("env" if os.environ.get(provider.upper() + "_MODELS") else None)
        err = _AI_LAST_ERROR.get(provider)
        if err:
            info["last_error"] = {"at": datetime.utcfromtimestamp(err[0]).isoformat() + "Z", "message": err[1]}
        out[provider] = info
    # 운영 설정 점검: 값은 절대 내보내지 않고 "설정됨(True)/미설정(False)"만 (SSH 없이 .env 확인용)
    def _has(name):
        return bool(os.environ.get(name, "").strip())
    out["config"] = {
        "is_production": IS_PRODUCTION,
        "set": {name: _has(name) for name in (
            "FLASK_SECRET_KEY", "TOSS_OWNER_EMAIL", "TOSS_OWNER_USER_IDS", "DATABASE_URL", "BASE_URL",
            "GOOGLE_CLIENT_ID", "KAKAO_REST_API_KEY", "TOSS_CLIENT_ID", "DART_API_KEY", "AI_BACKUP_API_KEY")},
        "warnings": [w for w, bad in (
            ("FLASK_SECRET_KEY 미설정: 재시작·배포 때마다 모든 사용자 로그인이 풀립니다",
             IS_PRODUCTION and not _has("FLASK_SECRET_KEY")),
            ("AI 키가 하나도 없음: AI 해설·번역·AI 봇 평가가 동작하지 않습니다",
             not (_has("GEMINI_API_KEY") or _has("GROQ_API_KEY") or _has("AI_BACKUP_API_KEY"))),
        ) if bad],
        "bot": {"scheduler": os.environ.get("BOT_SCHEDULER", "1") != "0", "ai_daily_budget": bot_engine.ai_budget_limit()},
        "push": {"enabled": webpush.status()[0], "reason": webpush.status()[1],
                 "vapid_subject_set": _has("VAPID_SUBJECT"), "devices": PushSubscription.query.count()},
    }
    test = request.args.get("test") == "1"
    if test:
        out["test_reply"] = _ai_chat("짧게 답하세요.", "한국어로 '정상'이라고만 답하세요.", max_tokens=20, label="debug")
    return jsonify(out)


@app.route("/api/debug/toss")
def debug_toss():
    """토스 API 진단: 서버 egress IP + 토큰 발급 성공 여부.
    배포 환경의 outbound IP를 토스 콘솔 허용목록에 등록할 때 사용.
    소유자 전용 — 외부인에게는 존재하지 않는 경로처럼 404.
    """
    if not _is_toss_owner():
        return jsonify({"error": "Not Found"}), 404
    out = {"toss_enabled": toss_api.is_enabled()}
    # 1) 서버 직접 egress IP (프록시 미사용 시 토스가 보는 IP)
    try:
        out["egress_ip"] = requests.get("https://api.ipify.org", timeout=5).text
    except Exception as e:
        out["egress_ip"] = None
        out["egress_ip_error"] = str(e)
    # 1-b) 프록시 설정 시: 프록시 경유 egress IP (이게 토스 콘솔에 등록할 고정 IP)
    _px = os.environ.get("TOSS_PROXY_URL", "").strip()
    out["toss_proxy_set"] = bool(_px)
    if _px:
        try:
            out["proxy_egress_ip"] = requests.get(
                "https://api.ipify.org", timeout=8,
                proxies={"http": _px, "https": _px},
            ).text
        except Exception as e:
            out["proxy_egress_ip"] = None
            out["proxy_egress_ip_error"] = str(e)
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
    out["price_cache"] = toss_api.price_cache_stats()       # 공용 실시간 시세 캐시 (적중·호출·크기)
    return jsonify(out)

# ── AI 매매 신호 봇 (Step 1: 신호 생성 파이프라인, Step 2: 관심종목·구독·알림) ──────────
import bot_engine
import bot_trader
import notifier

import webpush
webpush.init({"app": app, "db": db, "PushSubscription": PushSubscription})
notifier.init({
    "app": app, "db": db, "Notification": Notification, "UserSignalSubscription": UserSignalSubscription,
    "WatchlistItem": WatchlistItem, "Holding": Holding, "PaperHolding": PaperHolding, "BotStrategy": BotStrategy,
    "webpush": webpush,
})

bot_engine.init({
    "app": app, "db": db, "ai_cache": ai_cache, "STOCK_DB": STOCK_DB,
    "BotStrategy": BotStrategy, "AiStockAssessment": AiStockAssessment, "AiSignal": AiSignal, "AiCache": AiCache,
    "ai_json": _ai_json, "last_model": lambda: getattr(_AI_LAST_MODEL, "v", None),
    "compute_dcf": compute_dcf, "get_financials": _get_financials, "get_risk_free_rate": _get_risk_free_rate,
    "fetch_upcoming_events": fetch_upcoming_events, "fetch_kr_news": fetch_kr_news, "fetch_news": fetch_news,
    "notify": notifier.dispatch,
    "sync": bot_trader.sync, "snapshot": bot_trader.snapshot_all, "day_change": bot_trader.day_change_pct,
})
bot_trader.init({
    "app": app, "db": db, "User": User, "PaperHolding": PaperHolding, "AssetSnapshot": AssetSnapshot,
    "BotStrategy": BotStrategy, "AiSignal": AiSignal, "BotPosition": BotPosition,
    "paper_buy": paper_buy, "paper_sell": paper_sell, "fx": _get_usd_krw_rate, "price": _fetch_current_price,
    "save_snapshot": _save_today_snapshot,
})
with app.app_context():
    try:
        bot_engine.seed_strategies()
        bot_trader.ensure_accounts()          # 전략마다 1억 원 봇 계좌
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"[BOT] 전략·계좌 초기화 실패: {e}")
bot_engine.D["paper_exit"] = paper_auto_exits           # 장중 5분 체크 → 모의투자 목표가·손절가 자동 청산
# 액면분할 자동 반영 (모의·봇·신호·실제 포트폴리오). 봇 장 마감 배치 시작 · 장중 급락 방어에서 부른다
import splits
splits.init({
    "app": app, "db": db, "AppliedStockSplit": AppliedStockSplit, "PaperHolding": PaperHolding, "Holding": Holding,
    "Transaction": Transaction, "User": User, "BotPosition": BotPosition, "AiSignal": AiSignal,
    "DropHoldState": DropHoldState,           # 급락 손절 보류 상태 (재시작에도 유지)
    "bot_users": bot_trader.bot_users, "price": lambda t: _fetch_current_price(t), "fx": lambda: _get_usd_krw_rate(),
    "notify_user": notifier.notify_user,
})
bot_engine.D["split_market"] = splits.check_market
bot_engine.D["split_guard"] = splits.guard_drop
bot_engine.D["drop_decision"] = splits.drop_decision       # 이중 방어: 분할 의심 보류 · 급락 1회 보류
bot_engine.D["split_evidence"] = splits.history_evidence
# 공시·수주 레이더 (DART · SEC EDGAR). 새 스레드 없이 봇 스케줄러 루프에서 주기적으로 (DART 15분 · SEC 30분)
import disclosures
import sector_map as _sector_map_for_disc
disclosures.init({
    "app": app, "db": db, "DisclosureEvent": DisclosureEvent, "Holding": Holding, "PaperHolding": PaperHolding,
    "WatchlistItem": WatchlistItem, "universe": _sector_map_for_disc.universe, "notify": notifier.dispatch_disclosures,
    "kr_prices": lambda: {m_["ticker"]: m_["price"] for g_ in ((_sector_map_for_disc.get("KR") or {}).get("groups") or [])
                          for m_ in (g_.get("members") or []) if m_.get("price")},   # 내부자 매수 소액 필터용 (네트워크 없음)
})
with app.app_context():
    disclosures.migrate_8k_contract()          # 예전 8-K Item 1.01(material_8k) → 수주·계약(contract)
bot_engine.D["disclosures_tick"] = disclosures.tick
bot_engine.start_scheduler()

import sector_map
import screener
screener.init({
    "app": app, "db": db, "Model": ScreenerSnapshot, "AiSignal": AiSignal, "universe": sector_map.universe,
    "download_daily": bot_engine.download_daily, "compute_features": bot_engine.compute_features,
    "bot_lock": lambda: bot_engine._RUN_LOCK.locked(),
    "name_of": lambda t: _stock_name(t, "KR" if t.endswith((".KS", ".KQ")) else "US"),
    "kr_integration": fetch_naver_integration, "kr_per_basis": kr_per_basis,
    "sector_snapshot": lambda m: sector_map.get(m),
})
trend_setups.init({
    "app": app, "db": db, "Model": TrendSetup, "Snap": ScreenerSnapshot, "universe": bot_engine.universe,
    "download_daily": bot_engine.download_daily, "drop_unfinished": bot_engine._drop_unfinished_bar,
    "bot_lock": lambda: bot_engine._RUN_LOCK.locked(),
})


def _on_batch_features(market, feats, data):
    """장 마감 배치 → 스크리너 기술 지표 + 추세 셋업 확정 (같은 일봉 재사용, 추가 다운로드 없음)."""
    screener.upsert_technicals(market, feats, data)
    trend_setups.on_batch(market, data)


def _on_sector_rows(market, rows):
    """섹터맵 15분 갱신 → 스크리너 가격 + 추세 셋업 장중 돌파."""
    screener.upsert_prices(market, rows)
    trend_setups.apply_live(market, rows)


bot_engine.D["on_features"] = _on_batch_features
sector_map.init({"app": app, "ai_cache": ai_cache, "bot_lock": bot_engine._RUN_LOCK,
                 "on_rows": _on_sector_rows})
sector_map.start()
screener.start()
trend_setups.start()


def _signal_dict(s):
    return {
        "id": s.id, "strategy": s.strategy_key, "ticker": s.ticker, "name": s.name, "market": s.market,
        "side": s.side, "signal_date": s.signal_date.isoformat() if s.signal_date else None,
        "price_at_signal": s.price_at_signal, "entry_price": s.entry_price, "target_price": s.target_price,
        "stop_price": s.stop_price, "rr": s.rr, "confidence": s.confidence, "quant_score": s.quant_score,
        "reason": s.reason, "status": s.status, "status_reason": s.status_reason,
        "fill_price": s.fill_price, "filled_date": s.filled_date.isoformat() if s.filled_date else None,
        "exit_price": s.exit_price, "exit_date": s.exit_date.isoformat() if s.exit_date else None,
        "pnl_pct": s.pnl_pct, "holding_days": s.holding_days,
    }


@app.route("/api/bots/signals")
def bot_signals():
    """최근 봇 신호 목록 (공개 읽기). ?strategy=&status=&market=&limit="""
    q = AiSignal.query
    strategy = (request.args.get("strategy") or "").strip()
    status = (request.args.get("status") or "").strip().upper()
    market = (request.args.get("market") or "").strip().upper()
    if strategy:
        q = q.filter(AiSignal.strategy_key == strategy)
    if status:
        q = q.filter(AiSignal.status == status)
    if market:
        q = q.filter(AiSignal.market == market)
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    rows = q.order_by(AiSignal.created_at.desc(), AiSignal.id.desc()).limit(limit).all()
    strategies = [{"key": s.key, "name": s.name, "emoji": s.emoji, "kind": s.kind, "use_ai": s.use_ai,
                   "enabled": s.enabled} for s in BotStrategy.query.order_by(BotStrategy.id).all()]
    return jsonify({"strategies": strategies, "signals": [_signal_dict(s) for s in rows],
                    "kr_earnings_filter": "skipped",
                    "disclaimer": "알고리즘 검증용 가상 모의 데이터이며 실제 투자 권유가 아닙니다."})


@app.route("/api/bots/signals/<int:sid>")
def bot_signal_detail(sid):
    s = AiSignal.query.get_or_404(sid)
    out = _signal_dict(s)
    try:
        out["evidence"] = json.loads(s.evidence_json) if s.evidence_json else None
    except Exception:
        out["evidence"] = None
    if s.assessment_id:
        a = AiStockAssessment.query.get(s.assessment_id)
        if a:
            out["ai"] = {"confidence": a.confidence, "verdict": a.verdict, "summary": a.summary,
                         "catalyst": a.catalyst, "fatal_risk": a.fatal_risk, "structural_risk": a.structural_risk,
                         "model": a.model_used, "as_of": a.as_of.isoformat()}
            try:
                payload = json.loads(a.payload_json or "{}").get("out") or {}
                out["ai"]["positives"], out["ai"]["risks"] = payload.get("positives"), payload.get("risks")
            except Exception:
                pass
    return jsonify(out)


@app.route("/api/bots/runs")
def bot_runs():
    """최근 배치 요약 (후보 수, 기각 사유, 생성 신호, AI 호출 수)."""
    rows = (AiCache.query.filter_by(cache_type="bot_run").order_by(AiCache.created_at.desc()).limit(10).all())
    out = []
    for r in rows:
        try:
            out.append(json.loads(r.payload_json))
        except Exception:
            pass
    return jsonify({"runs": out, "ai_budget_limit": bot_engine.ai_budget_limit()})


@app.route("/api/admin/bots/run", methods=["POST"])
def admin_bots_run():
    """소유자 전용 수동 실행. JSON {"market": "KR"|"US", "dry_run": bool}. 백그라운드로 돌고 즉시 응답."""
    if not _is_toss_owner():
        return jsonify({"error": "Not Found"}), 404
    body = request.get_json(silent=True) or {}
    market = str(body.get("market", "KR")).upper()
    if market not in ("KR", "US"):
        return jsonify({"error": "market 은 KR 또는 US"}), 400
    dry = bool(body.get("dry_run"))

    def _job():
        with app.app_context():
            try:
                bot_engine.run_market(market, dry_run=dry)
            finally:
                db.session.remove()
    _BG_EXEC.submit(_job)
    return jsonify({"ok": True, "started": market, "dry_run": dry,
                    "note": "결과는 /api/bots/runs 에서 확인 (수 분 소요)"})



# ── 관심종목 ─────────────────────────────────────────────────────────────────
_WATCHLIST_MAX = 100
_WATCH_NAME_MAP = None


def _watch_stock_name(ticker):
    global _WATCH_NAME_MAP
    if _WATCH_NAME_MAP is None:
        _WATCH_NAME_MAP = {s.get("symbol", "").upper(): s.get("name") for s in STOCK_DB if s.get("symbol")}
    return _WATCH_NAME_MAP.get(ticker.upper())


def _norm_ticker(raw):
    t = (raw or "").strip().upper()
    return t if (1 <= len(t) <= 30 and re.fullmatch(r"[A-Z0-9.\-^=]+", t)) else None


@app.route("/api/watchlist")
@login_required
def watchlist_list():
    rows = WatchlistItem.query.filter_by(user_id=current_user.id).order_by(WatchlistItem.created_at.desc()).all()
    return jsonify({"items": [{"ticker": r.ticker, "name": r.name or r.ticker,
                               "added": r.created_at.isoformat() if r.created_at else None} for r in rows],
                    "max": _WATCHLIST_MAX})


@app.route("/api/watchlist", methods=["POST"])
@login_required
def watchlist_add():
    body = request.get_json(silent=True) or {}
    ticker = _norm_ticker(body.get("ticker"))
    if not ticker:
        return jsonify({"error": "종목 코드가 올바르지 않습니다"}), 400
    if WatchlistItem.query.filter_by(user_id=current_user.id, ticker=ticker).first():
        return jsonify({"ok": True, "ticker": ticker, "already": True})
    if WatchlistItem.query.filter_by(user_id=current_user.id).count() >= _WATCHLIST_MAX:
        return jsonify({"error": f"관심종목은 최대 {_WATCHLIST_MAX}개까지 담을 수 있습니다"}), 400
    name = _watch_stock_name(ticker) or str(body.get("name") or ticker)[:200]
    db.session.add(WatchlistItem(user_id=current_user.id, ticker=ticker, name=name))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()          # 동시에 두 번 눌러 유니크 충돌 → 이미 추가된 것
    return jsonify({"ok": True, "ticker": ticker, "name": name})


@app.route("/api/watchlist/<path:ticker>", methods=["DELETE"])
@login_required
def watchlist_remove(ticker):
    t = _norm_ticker(ticker)
    if t:
        WatchlistItem.query.filter_by(user_id=current_user.id, ticker=t).delete()
        db.session.commit()
    return jsonify({"ok": True})


def _mkt_of(t):
    return "KR" if t.upper().endswith((".KS", ".KQ")) else "US"


def _session_open(market):
    return trend_setups.session_progress(market) is not None


def _snapshot_prev_closes(tickers):
    """스크리너 스냅샷(섹터맵이 장중 15분마다 갱신)에서 '오늘 기준 전일 종가'. 외부 호출 없음.
    스냅샷 가격이 오늘(시장 현지) 것이면 price / (1 + 등락률), 그 전 날짜 것이면 그 가격 자체가 전일 종가."""
    if not tickers:
        return {}
    out = {}
    try:
        rows = (ScreenerSnapshot.query.filter(ScreenerSnapshot.ticker.in_(list(tickers)), ScreenerSnapshot.price.isnot(None))
                .with_entities(ScreenerSnapshot.ticker, ScreenerSnapshot.market, ScreenerSnapshot.price,
                               ScreenerSnapshot.change_pct, ScreenerSnapshot.price_updated_at).all())
        for t, m, price, chg, at in rows:
            if not price:
                continue
            today = trend_setups.market_today(m)
            d = None
            if at:
                try:
                    d = pd.Timestamp(at, tz="UTC").tz_convert(trend_setups._tz(m)).date()
                except Exception:
                    d = None
            if d == today and chg is not None:
                out[t] = price / (1 + chg / 100)
            elif d is not None and d < today:
                out[t] = price
    except Exception as e:
        app.logger.debug(f"[live] 전일 종가 조회 실패: {e}")
    return out


def prev_closes_today(tickers):
    """오늘 시세의 등락률 기준이 되는 전일 종가. 한국 종목은 KRX 공식 기준가(krx_ref), 그 외·실패는 스크리너 스냅샷.
    종목 상세·추세 셋업·스크리너·관심종목·급등락·섹터맵이 같은 값을 쓰게 하는 공용 함수."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    out = {}
    try:
        out.update(krx_ref.prev_closes_today([t for t in tickers if krx_ref.is_kr(t)]))
    except Exception as e:
        app.logger.debug(f"[live] KRX 기준가 실패: {e}")
    rest = [t for t in tickers if t not in out]
    if rest:
        out.update(_snapshot_prev_closes(rest))
    return out


def live_quotes(tickers, ttl=20, fallback=True):
    """여러 종목 실시간 현재가·등락률. 토스 get_prices(200종목 묶음, 공용 캐시) 1순위 →
    등락률은 저장된 전일 종가(스냅샷) 대비 → 토스에 없거나 전일 종가를 모르는 종목만 기존 _quote(네이버·yfinance).
    → {ticker: {"price", "prev_close", "change_pct", "source"}}"""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    got = {}
    try:
        got = toss_api.get_prices_cached(tickers, ttl=ttl)
    except Exception as e:
        app.logger.debug(f"[live] 토스 실패: {e}")
    prevs = prev_closes_today([t for t in got]) if got else {}
    out, rest = {}, []
    for t in tickers:
        info = got.get(t)
        if info and info.get("price"):
            prev = prevs.get(t) or info.get("prev_close")
            out[t] = {"price": float(info["price"]), "prev_close": prev,
                      "change_pct": toss_api.change_pct(info["price"], prev, info), "source": "toss"}
            if out[t]["change_pct"] is None:
                rest.append(t)                         # 등락률 기준(전일 종가)만 기존 경로에서
        else:
            rest.append(t)
    if fallback and rest:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(rest))) as ex:
            for t, q in zip(rest, ex.map(_quote, rest)):
                if t in out:                           # 토스 현재가 + 기존 경로의 전일 종가
                    prev = q.get("prev_close")
                    out[t].update(prev_close=prev, change_pct=toss_api.change_pct(out[t]["price"], prev))
                elif q.get("price"):
                    out[t] = {**q, "source": "fallback"}
    return out


_QUOTE_CACHE = {}            # ticker → (ts, {"price", "prev_close", "change_pct"})
_QUOTE_TTL = 30


def _quote(ticker):
    """현재가 + 전일 대비 등락률. 한국: 토스 현재가(가능할 때) + 네이버 전일 종가, 그 외 yfinance. 30초 캐시."""
    hit = _QUOTE_CACHE.get(ticker)
    if hit and time.time() - hit[0] < _QUOTE_TTL:
        return hit[1]
    price = prev = None
    try:
        if ticker.endswith((".KS", ".KQ")):
            nv = fetch_naver_realtime_price(ticker.split(".")[0]) or {}
            price, prev = nv.get("current_price"), nv.get("previous_close")
            if toss_api.is_enabled() and toss_api.is_eligible(ticker):
                price = _fetch_current_price(ticker) or price
        else:
            fi = yf.Ticker(ticker).fast_info
            price = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
    except Exception:
        pass
    out = {"price": float(price) if price else None, "prev_close": float(prev) if prev else None,
           "change_pct": round((price / prev - 1) * 100, 2) if (price and prev) else None}
    if price:
        _QUOTE_CACHE[ticker] = (time.time(), out)
    return out


@app.route("/api/watchlist/quotes")
@login_required
def watchlist_quotes():
    """관심종목 현재가·등락률 (목록은 /api/watchlist 로 먼저 그리고, 시세는 이걸로 채운다)."""
    rows = WatchlistItem.query.filter_by(user_id=current_user.id).all()
    tickers = [r.ticker for r in rows]
    quotes = live_quotes(tickers)          # 토스 1회 배치(20초 공용 캐시) → 없으면 네이버·yfinance
    return jsonify({"quotes": {t: {**quotes.get(t, {"price": None, "prev_close": None, "change_pct": None}),
                                   "currency": "KRW" if t.endswith((".KS", ".KQ")) else "USD"}
                               for t in tickers}})


# ── 신호 알림 구독 ─────────────────────────────────────────────────────────────
def _strategies_public():
    return [{"key": s.key, "name": s.name, "emoji": s.emoji, "kind": s.kind, "use_ai": s.use_ai}
            for s in BotStrategy.query.filter_by(enabled=True).order_by(BotStrategy.id).all()]


@app.route("/api/signal-subscription")
@login_required
def signal_subscription_get():
    sub = UserSignalSubscription.query.filter_by(user_id=current_user.id).first()
    cfg = ({"enabled": bool(sub.enabled), "strategies": [k for k in (sub.strategy_keys or "").split(",") if k],
            "scope": sub.scope, "min_confidence": sub.min_confidence, "disclosure_alerts": bool(sub.disclosure_alerts)}
           if sub else {"enabled": False, "strategies": [], "scope": "all", "min_confidence": 70, "disclosure_alerts": False})
    return jsonify({"subscription": cfg, "strategies": _strategies_public(),
                    "scopes": [{"key": "all", "label": "전체 종목"}, {"key": "portfolio", "label": "보유 종목 (실제 + 모의투자)"},
                               {"key": "watchlist", "label": "관심종목"}]})


@app.route("/api/signal-subscription", methods=["PUT", "POST"])
@login_required
def signal_subscription_put():
    body = request.get_json(silent=True) or {}
    valid = {s["key"] for s in _strategies_public()}
    keys = [k for k in (body.get("strategies") or []) if isinstance(k, str) and k in valid]
    scope = body.get("scope") if body.get("scope") in ("all", "portfolio", "watchlist") else "all"
    try:
        min_conf = max(0, min(100, int(body.get("min_confidence", 70))))
    except (TypeError, ValueError):
        min_conf = 70
    enabled = bool(body.get("enabled", True)) and bool(keys)
    sub = UserSignalSubscription.query.filter_by(user_id=current_user.id).first()
    if not sub:
        sub = UserSignalSubscription(user_id=current_user.id, strategy_keys="")
        db.session.add(sub)
    sub.strategy_keys, sub.scope, sub.min_confidence, sub.enabled = ",".join(keys), scope, min_conf, enabled
    if "disclosure_alerts" in body:              # 보유·관심종목 핵심 공시 알림 (봇 신호 알림과 따로)
        sub.disclosure_alerts = bool(body.get("disclosure_alerts"))
    sub.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "subscription": {"enabled": enabled, "strategies": keys, "scope": scope,
                                                 "min_confidence": min_conf, "disclosure_alerts": bool(sub.disclosure_alerts)}})


# ── 공시·수주 레이더 ─────────────────────────────────────────────────────────────
@app.route("/api/disclosures")
def disclosures_list():
    """?market=ALL|KR|US &category=ALL|contract|buyback|insider_buy|offering|material_8k &ticker= &days=14 &limit=50"""
    a = request.args
    market = (a.get("market") or "ALL").upper()
    category = (a.get("category") or "ALL").lower()
    try:
        days = max(1, min(30, int(a.get("days", 14))))
        limit = max(1, min(200, int(a.get("limit", 50))))
    except ValueError:
        days, limit = 14, 50
    items = disclosures.query(market, category if category in disclosures.CATEGORIES else "ALL",
                              (a.get("ticker") or "").strip() or None, days, limit)
    try:
        can_refresh = bool(current_user.is_authenticated and _is_toss_owner())
    except Exception:
        can_refresh = False
    return jsonify({"items": items, "status": disclosures.STATUS, "days": days, "can_refresh": can_refresh,
                    "sources": {"KR": "OpenDART", "US": "SEC EDGAR"}})


@app.route("/api/disclosures/refresh", methods=["POST"])
@login_required
def disclosures_refresh():
    """소유자 전용: 주기와 상관없이 지금 수집 (운영 점검·로컬 확인용)"""
    if not _is_toss_owner():
        return jsonify({"error": "권한이 없습니다"}), 403
    body = request.get_json(silent=True) or {}
    if body.get("backfill") or request.args.get("backfill") in ("1", "true"):
        # 최근 14일 백필 (알림 없음). 이 요청에서 최대 90초 하고, 남은 건 스케줄러 tick 이 이어서 한다
        markets = [m for m in (body.get("markets") or ["KR", "US"]) if m in ("KR", "US")] or ["KR", "US"]
        disclosures.start_backfill(markets)
        res = disclosures.run(names=["-"], backfill_budget=40)  # 정기 수집기는 돌리지 않음, 시장마다 40초 · 나머지는 스케줄러가
        if res.get("busy"):
            return jsonify({"ok": False, "busy": True, "error": "지금 수집이 진행 중이에요. 잠시 뒤 다시 확인해 주세요.",
                            "status": disclosures.STATUS}), 409
        return jsonify({"ok": True, "backfill": {m: disclosures._backfilled[m] for m in markets}, "saved": res,
                        "status": disclosures.STATUS})
    names = [n for n in body.get("sources") or [] if n in ("dart", "sec")] or None
    res = disclosures.run(names=names, force=True)          # 진행 중인 백필도 시장마다 20초씩 이어서
    if res.get("busy"):
        return jsonify({"ok": False, "busy": True, "error": "지금 수집이 진행 중이에요. 잠시 뒤 다시 확인해 주세요.",
                        "status": disclosures.STATUS}), 409
    return jsonify({"ok": True, "saved": res, "status": disclosures.STATUS})


# ── 알림 (헤더 종 아이콘) ─────────────────────────────────────────────────────
@app.route("/api/notifications")
@login_required
def notifications_list():
    try:
        limit = max(0, min(50, int(request.args.get("limit", 20))))
    except ValueError:
        limit = 20
    q = Notification.query.filter_by(user_id=current_user.id)
    unread = q.filter_by(is_read=False).count()
    rows = q.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all() if limit else []
    return jsonify({"unread": unread, "items": [
        {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "signal_id": n.signal_id,
         "read": bool(n.is_read), "created_at": n.created_at.isoformat() + "Z" if n.created_at else None}
        for n in rows]})


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    """{"ids": [..]} 이면 그것만, {"all": true} 이면 전부 읽음 처리."""
    body = request.get_json(silent=True) or {}
    q = Notification.query.filter_by(user_id=current_user.id, is_read=False)
    if not body.get("all"):
        ids = [i for i in (body.get("ids") or []) if isinstance(i, int)][:200]
        if not ids:
            return jsonify({"ok": True, "updated": 0})
        q = q.filter(Notification.id.in_(ids))
    n = q.update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "updated": n})



# ── AI 봇 검증 대시보드 (Step 3) ─────────────────────────────────────────────
BOT_DISCLAIMER = "AI 매매 신호 및 가상 봇 수익률은 알고리즘 검증용 모의 데이터이며 실제 투자 권유가 아닙니다."
BOT_COLORS = {"aggressive_momentum": "#3987e5", "balanced_swing": "#d95926",
              "conservative_value": "#199e70", "baseline_quant": "#c98500"}
BENCH_COLORS = {"KOSPI": "#d55181", "S&P 500": "#9085e9"}


def _bot_summary(st, p):
    ex, r = p.get("exit", {}), p.get("risk", {})
    lim = (f"종목당 최대 {int(r.get('max_weight', 0) * 100)}% · 최대 {r.get('max_positions')}종목 · "
           f"하루 신규 {r.get('max_new_per_day')}건 · 하루 손실 -{r.get('daily_loss_limit_pct')}%면 신규 중단")
    if st.key == "aggressive_momentum":
        rule = (f"단기 상승 돌파: 5일선>20일선, 구름대 위, 거래량 평균의 {p['entry']['vol_ratio_min']}배 이상, MACD 상승, "
                f"RSI {p['entry']['rsi_min']}~{p['entry']['rsi_max']} + AI 단기 호재 확인(신뢰도 {p['ai_conf_min']}↑)")
        exit_ = (f"손절 -{ex['stop_pct']:g}% 또는 1.5ATR 중 가까운 쪽 · 목표 +{ex['target_pct_min']:g}~{ex['target_pct_max']:g}% · "
                 f"+{ex['trail_trigger_pct']:g}% 이후 고점 대비 -{ex['trail_pct']:g}% 트레일링 · 최대 {ex['time_stop_days']}거래일")
    elif st.kind == "swing":
        ai = f" + AI 검증(신뢰도 {p['ai_conf_min']}↑)" if st.use_ai else " (AI 검증 없음 · 균형형과 같은 규칙의 대조군)"
        rule = f"추세 돌파 또는 과매도 반등 + 펀더멘탈 {p['fund_min']}점↑{ai}"
        exit_ = (f"손절 {ex['stop_pct_min']:g}~{ex['stop_pct_max']:g}% · 목표 {ex.get('target_rr', ex['rr_min']):g}R(저항선 우선) · "
                 f"+{ex['trail_trigger_pct']:g}% 이후 -{ex['trail_pct']:g}% 트레일링 · 추세 이탈 · 최대 {ex['time_stop_days']}거래일")
    else:
        rule = (f"펀더멘탈 {p['fund_min']}점↑ + 적정가·목표가 대비 {p['upside_min_pct']:g}%↑ 저평가 + 하단 반등 확인 + "
                f"AI 구조적 악재 없음(신뢰도 {p['ai_conf_min']}↑) · VIX {p['vix_max']} 이상이면 신규 보류")
        exit_ = f"손절 -{ex['stop_pct']:g}% · 목표 +{ex['target_pct_min']:g}~{ex['target_pct_max']:g}% · 최대 {ex['time_stop_days']}거래일"
    return {"rule": rule, "exit": exit_, "limits": lim, "fee": f"매수·매도 수수료 각 {p.get('fee_pct', 0.15)}%"}


_BOT_ACCT_CACHE = {}                 # user_id -> (시각, 계좌 dict). 개요·상세·차트 마지막 점이 같은 평가를 쓰도록
_BOT_ACCT_TTL = 60
_BOT_ACCT_LOCK = threading.Lock()


def _bot_account(u, fx=None):
    now = time.time()
    with _BOT_ACCT_LOCK:
        hit = _BOT_ACCT_CACHE.get(u.id)
    if hit and now - hit[0] < _BOT_ACCT_TTL:
        return dict(hit[1])
    tot, cash, pos = bot_trader.equity(u, fx=fx)
    init = u.initial_capital or bot_trader.INITIAL
    acc = {"total_krw": round(tot), "cash_krw": round(cash), "positions_krw": round(pos),
           "initial_krw": round(init), "return_pct": round((tot / init - 1) * 100, 2) if init else 0}
    with _BOT_ACCT_LOCK:
        _BOT_ACCT_CACHE[u.id] = (now, acc)
    return dict(acc)


@app.route("/ai-bots")
def ai_bots_page():
    return render_template("ai_bots.html")


@app.route("/api/ai-bots/overview")
def ai_bots_overview():
    users = bot_trader.bot_users()
    fx = _get_usd_krw_rate()
    bots = []
    for st in BotStrategy.query.order_by(BotStrategy.id).all():
        p = json.loads(st.params_json)
        u = users.get(st.key)
        sig_counts = dict(db.session.query(AiSignal.status, db.func.count(AiSignal.id))
                          .filter(AiSignal.strategy_key == st.key).group_by(AiSignal.status).all())
        bots.append({
            "key": st.key, "name": st.name, "emoji": st.emoji, "kind": st.kind, "use_ai": st.use_ai,
            "enabled": st.enabled, "color": BOT_COLORS.get(st.key, "#3987e5"),
            "nickname": u.nickname if u else None, "summary": _bot_summary(st, p),
            "account": _bot_account(u, fx) if u else None, "stats": bot_trader.stats(st.key),
            "signals": {"total": sum(sig_counts.values()), "pending": sig_counts.get("PENDING", 0),
                        "active": sig_counts.get("ACTIVE", 0), "cancelled": sig_counts.get("CANCELLED", 0)},
        })
    by = {b["key"]: b for b in bots}
    cmp_ = None
    if "balanced_swing" in by and "baseline_quant" in by:
        def pick(b):
            st = b["stats"]
            return {"key": b["key"], "name": b["name"], "emoji": b["emoji"],
                    "return_pct": (b["account"] or {}).get("return_pct"), "win_rate": st["win_rate"],
                    "mdd_pct": st["mdd_pct"], "payoff": st["payoff"], "trades": st["trades"]}
        cmp_ = {"ai": pick(by["balanced_swing"]), "baseline": pick(by["baseline_quant"])}
    runs = []
    for r in AiCache.query.filter_by(cache_type="bot_run").order_by(AiCache.created_at.desc()).limit(4).all():
        try:
            d = json.loads(r.payload_json)
            runs.append({"market": d.get("market"), "as_of": d.get("as_of"), "tickers": d.get("tickers"),
                         "signals": sum(len(v) for v in (d.get("signals") or {}).values()),
                         "ai_calls": d.get("ai_calls_used"), "skipped": d.get("skipped")})
        except Exception:
            pass
    return jsonify({"bots": bots, "comparison": cmp_, "next_batch": bot_engine.next_batch_times(),
                    "initial_capital": bot_trader.INITIAL, "recent_runs": runs,
                    "ai_budget_limit": bot_engine.ai_budget_limit(), "kr_earnings_filter": "skipped",
                    "disclaimer": BOT_DISCLAIMER})


@app.route("/api/ai-bots/chart")
def ai_bots_chart():
    """봇 4개 + KOSPI + S&P 500 수익률(%) 곡선. 기간 시작일을 0%로 맞춘다. ?period=7D|30D|90D|ALL"""
    from datetime import date as _date, timedelta as _td
    period = (request.args.get("period") or "ALL").upper()
    days = {"7D": 7, "30D": 30, "90D": 90}.get(period)
    users = bot_trader.bot_users()
    today = datetime.now(bot_engine._tz("KR")).date()
    starts = [u.created_at.date() for u in users.values() if u.created_at]
    first = min(starts) if starts else today
    start = max(first, today - _td(days=days)) if days else first
    fx = _get_usd_krw_rate()
    series = []
    for st in BotStrategy.query.order_by(BotStrategy.id).all():
        u = users.get(st.key)
        if not u:
            continue
        init = u.initial_capital or bot_trader.INITIAL
        pts = {(u.created_at.date() if u.created_at else first): init}
        for snp in AssetSnapshot.query.filter_by(user_id=u.id).order_by(AssetSnapshot.date).all():
            pts[snp.date] = snp.total_assets_krw
        pts[today] = _bot_account(u, fx)["total_krw"]       # 마지막 점: 카드·상세와 같은 60초 스냅샷
        ordered = sorted(pts.items())
        base = next((v for d_, v in reversed(ordered) if d_ <= start), ordered[0][1])
        data = [{"date": d_.isoformat(), "return_pct": round((v / base - 1) * 100, 3)}
                for d_, v in ordered if d_ >= start]
        series.append({"key": st.key, "name": f"{st.emoji} {st.name}", "color": BOT_COLORS.get(st.key),
                       "kind": "bot", "data": data})
    for label, sym in (("KOSPI", "^KS11"), ("S&P 500", "^GSPC")):
        hist = _fetch_benchmark_history(sym, lookback_days=max(30, (today - start).days + 10))
        # 시작일 직전 거래일 종가를 0%로 (시작일이 휴장일이어도 기준이 생기게)
        prior = [h for h in hist if h["date"] <= start.isoformat()]
        base_date = prior[-1]["date"] if prior else start.isoformat()
        data = [{"date": x["date"], "return_pct": x["return_pct"]} for x in _normalize_benchmark(hist, base_date)]
        series.append({"key": label, "name": label, "color": BENCH_COLORS[label], "kind": "benchmark", "data": data})
    return jsonify({"period": period, "start": start.isoformat(), "series": series,
                    "points": max((len(s["data"]) for s in series if s["kind"] == "bot"), default=0)})


@app.route("/api/ai-bots/<key>")
def ai_bots_detail(key):
    st = BotStrategy.query.filter_by(key=key).first_or_404()
    u = bot_trader.bot_users().get(key)
    p = json.loads(st.params_json)
    fx = _get_usd_krw_rate()
    positions = []
    if u:
        for pos in BotPosition.query.filter_by(user_id=u.id, status="OPEN").all():
            sig = AiSignal.query.get(pos.signal_id)
            cur = _fetch_current_price(pos.ticker) or pos.entry_price
            positions.append({
                "ticker": pos.ticker, "name": pos.name, "currency": pos.currency, "quantity": pos.quantity,
                "entry_price": pos.entry_price, "current_price": cur,
                "return_pct": round((cur / pos.entry_price - 1) * 100, 2) if pos.entry_price else None,
                "target_price": sig.target_price if sig else None, "stop_price": sig.stop_price if sig else None,
                "to_target_pct": round((sig.target_price / cur - 1) * 100, 2) if (sig and cur) else None,
                "to_stop_pct": round((sig.stop_price / cur - 1) * 100, 2) if (sig and cur) else None,
                "value_krw": round(cur * pos.quantity * (fx if pos.currency == "USD" else 1)),
                "filled_date": sig.filled_date.isoformat() if (sig and sig.filled_date) else None,
                "holding_days": sig.holding_days if sig else None, "signal_id": pos.signal_id,
            })
    acct = {p_.signal_id: p_ for p_ in BotPosition.query.filter_by(strategy_key=key).all()}
    log_rows = AiSignal.query.filter_by(strategy_key=key).order_by(AiSignal.signal_date.desc(), AiSignal.id.desc()).limit(100).all()
    log = []
    for s_ in log_rows:
        d = _signal_dict(s_)
        a = acct.get(s_.id)
        d["account"] = ({"status": a.status, "quantity": a.quantity, "skip_reason": a.skip_reason,
                         "realized_pnl_krw": a.realized_pnl_krw, "pnl_pct": a.pnl_pct} if a else None)
        d["has_ai"] = bool(s_.assessment_id)
        log.append(d)
    return jsonify({"key": st.key, "name": st.name, "emoji": st.emoji, "kind": st.kind, "use_ai": st.use_ai,
                    "signals_total": AiSignal.query.filter_by(strategy_key=key).count(),
                    "trades_total": Transaction.query.filter_by(user_id=u.id).count() if u else 0,
                    "color": BOT_COLORS.get(st.key), "summary": _bot_summary(st, p),
                    "account": _bot_account(u, fx) if u else None, "stats": bot_trader.stats(key),
                    "positions": positions, "signals": log, "disclaimer": BOT_DISCLAIMER})



_BOT_SIG_RE = re.compile(r"신호 #(\d+)")


@app.route("/api/stock-bots/<path:ticker>")
def stock_bots(ticker):
    """종목 상세의 'AI 봇 현황' 미니 카드. 이 종목을 지금 보유 중이거나 최근 7일 안에 신호를 낸 봇만.
    DB만 읽는다(시세 조회 없음). 수익률은 화면이 분석 현재가로 계산한다."""
    from datetime import timedelta as _td
    ticker = (ticker or "").strip().upper()[:30]
    if not ticker:
        return jsonify({"ticker": ticker, "bots": []})
    since = datetime.utcnow() - _td(days=7)
    open_pos = {p.strategy_key: p for p in BotPosition.query.filter_by(ticker=ticker, status="OPEN").all()}
    recent = {}
    for sg in (AiSignal.query.filter(AiSignal.ticker == ticker, AiSignal.created_at >= since)
               .order_by(AiSignal.created_at.desc(), AiSignal.id.desc()).all()):
        recent.setdefault(sg.strategy_key, sg)
    keys = set(open_pos) | set(recent)
    bots = []
    for st in BotStrategy.query.order_by(BotStrategy.id).all():
        if st.key not in keys:
            continue
        pos = open_pos.get(st.key)
        sig = AiSignal.query.get(pos.signal_id) if pos else recent.get(st.key)
        bots.append({
            "key": st.key, "name": st.name, "emoji": st.emoji, "color": BOT_COLORS.get(st.key),
            "holding": ({"entry_price": pos.entry_price, "quantity": pos.quantity, "currency": pos.currency,
                         "opened_at": pos.opened_at.isoformat() + "Z" if pos.opened_at else None} if pos else None),
            "signal": ({"id": sig.id, "side": sig.side, "status": sig.status, "signal_date": sig.signal_date.isoformat() if sig.signal_date else None,
                        "entry_price": sig.entry_price, "target_price": sig.target_price, "stop_price": sig.stop_price,
                        "pnl_pct": sig.pnl_pct} if sig else None),
        })
    return jsonify({"ticker": ticker, "bots": bots})


@app.route("/api/ai-bots/<key>/trades")
def ai_bots_trades(key):
    """봇 계좌의 실제 체결 내역 (모의투자 거래 내역 테이블 재사용). 최신순.
    ?side=all|buy|sell &page=1 &per=20 (최대 100)"""
    BotStrategy.query.filter_by(key=key).first_or_404()
    u = bot_trader.bot_users().get(key)
    side = (request.args.get("side") or "all").lower()
    try:
        page = max(1, int(request.args.get("page", 1)))
        per = max(1, min(100, int(request.args.get("per", 20))))
    except ValueError:
        page, per = 1, 20
    if not u:
        return jsonify({"items": [], "page": page, "has_more": False, "counts": {"all": 0, "buy": 0, "sell": 0}})
    base = Transaction.query.filter_by(user_id=u.id)
    counts = {"all": base.count(), "buy": base.filter_by(type="buy").count(), "sell": base.filter_by(type="sell").count()}
    q = base.filter_by(type=side) if side in ("buy", "sell") else base
    rows = q.order_by(Transaction.timestamp.desc(), Transaction.id.desc()).offset((page - 1) * per).limit(per + 1).all()
    has_more = len(rows) > per
    rows = rows[:per]
    # 거래 메모의 "신호 #id" 로 신호·포지션을 찾아 사유와 수수료 반영 손익을 붙인다
    sig_ids = {int(m.group(1)) for r in rows for m in [_BOT_SIG_RE.search(r.note or "")] if m}
    sigs = {x.id: x for x in AiSignal.query.filter(AiSignal.id.in_(sig_ids)).all()} if sig_ids else {}
    poss = {x.signal_id: x for x in BotPosition.query.filter(BotPosition.signal_id.in_(sig_ids)).all()} if sig_ids else {}
    items = []
    for r in rows:
        m = _BOT_SIG_RE.search(r.note or "")
        sid = int(m.group(1)) if m else None
        sig, pos = sigs.get(sid), poss.get(sid)
        fee_rate = (r.fee_krw / r.amount_krw * 100) if r.amount_krw else None
        it = {
            "id": r.id, "time": r.timestamp.isoformat() + "Z" if r.timestamp else None, "side": r.type,
            "ticker": r.ticker, "name": r.name, "currency": r.currency, "quantity": r.quantity, "price": r.price,
            "exchange_rate": r.exchange_rate, "amount_krw": round(r.amount_krw or 0), "fee_krw": round(r.fee_krw or 0),
            "fee_pct": round(fee_rate, 3) if fee_rate is not None else None,
            "signal_id": sid, "has_ai": bool(sig and sig.assessment_id),
        }
        if r.type == "sell":
            if pos and pos.status == "CLOSED" and pos.realized_pnl_krw is not None:
                # 매수·매도 수수료까지 반영한 원화 손익 (봇 성적표와 같은 기준)
                it["realized_pnl_krw"], it["pnl_pct"], it["pnl_basis"] = round(pos.realized_pnl_krw), pos.pnl_pct, "net"
            else:
                cost = (r.amount_krw or 0) - (r.realized_pnl_krw or 0)
                it["realized_pnl_krw"] = round(r.realized_pnl_krw or 0)
                it["pnl_pct"] = round((r.realized_pnl_krw or 0) / cost * 100, 2) if cost else None
                it["pnl_basis"] = "gross"
            it["exit_status"] = sig.status if sig else None
            it["exit_reason"] = sig.status_reason if sig else None
            it["holding_days"] = sig.holding_days if sig else None
        items.append(it)
    return jsonify({"items": items, "page": page, "per": per, "has_more": has_more, "side": side, "counts": counts})



# ── 웹 푸시 (PWA 모바일·브라우저 알림) ─────────────────────────────────────────
@app.route("/api/push/vapid-public-key")
def push_vapid_public_key():
    """푸시 켜짐 여부와 공개키. 로그인했다면 등록된 기기 수도."""
    ok, reason = webpush.status()
    out = {"enabled": ok, "public_key": webpush.public_key() if ok else None, "reason": None if ok else reason}
    if current_user.is_authenticated:
        out["devices"] = [{"device": r.user_agent, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None}
                          for r in PushSubscription.query.filter_by(user_id=current_user.id).all()]
    if not ok:
        out["reason"] = "서버에 푸시 알림이 아직 설정되지 않았습니다"   # 상세 원인(키·라이브러리)은 소유자 진단 화면에서
    return jsonify(out)


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    """현재 기기의 PushSubscription.toJSON() 저장 (같은 기기면 갱신)."""
    if not webpush.enabled():
        return jsonify({"error": "서버에 푸시 알림이 설정되지 않았습니다"}), 503
    body = request.get_json(silent=True) or {}
    row, err = webpush.save_subscription(current_user.id, body.get("subscription") or body,
                                         request.headers.get("User-Agent", ""))
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "device": row.user_agent,
                    "devices": PushSubscription.query.filter_by(user_id=current_user.id).count()})


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    body = request.get_json(silent=True) or {}
    n = webpush.remove_subscription(current_user.id, body.get("endpoint"))
    return jsonify({"ok": True, "removed": n})


@app.route("/api/push/test", methods=["POST"])
@login_required
def push_test():
    """내 등록 기기 전체로 테스트 푸시 1건."""
    if not webpush.enabled():
        return jsonify({"error": "서버에 푸시 알림이 설정되지 않았습니다"}), 503
    if not PushSubscription.query.filter_by(user_id=current_user.id).count():
        return jsonify({"error": "이 계정에 푸시를 켠 기기가 없습니다"}), 400
    r = webpush.send_to_user(current_user.id, {
        "title": "StockAnalyzer 테스트 알림",
        "body": "푸시 알림이 정상적으로 연결됐어요. AI 봇 신호가 나오면 이렇게 알려 드립니다.",
        "url": "/ai-bots", "tag": "push-test"})
    return jsonify({"ok": r["sent"] > 0, **r})



# ── 스크리너 · 종목 비교 ─────────────────────────────────────────────────────────
@app.route("/screener")
def screener_page():
    return render_template("screener.html")


@app.route("/api/screener/meta")
def screener_meta():
    return jsonify({"presets": screener.PRESETS, "sectors": screener.sectors(), "coverage": screener.coverage("ALL"),
                    "sorts": [{"key": "fundamental", "name": "펀더멘탈 점수"}, {"key": "change_pct", "name": "등락률"},
                              {"key": "upside_pct", "name": "목표가 상승여력"}, {"key": "volume_ratio", "name": "거래량 배수"},
                              {"key": "market_cap", "name": "시가총액"}, {"key": "trend", "name": "추세 점수"},
                              {"key": "rsi", "name": "RSI"}, {"key": "per", "name": "PER"}]})


@app.route("/api/screener")
def screener_api():
    """스냅샷 DB 쿼리만으로 필터·정렬. market, preset, sector, min_fundamental, min_rsi, max_rsi, max_per,
    min_upside, sort_by, order, limit"""
    t0 = time.time()
    out = screener.query(request.args)
    out["live"] = _screener_live_overlay(out.get("items") or [])
    out["took_ms"] = round((time.time() - t0) * 1000, 1)
    return jsonify(out)


SCREENER_LIVE_MAX = 60


def _screener_live_overlay(items):
    """필터·정렬은 DB 스냅샷 그대로, 반환되는 상위 60종목 중 장이 열린 시장 종목의 현재가·등락률만
    토스 실시간(20초 공용 캐시)으로 덧씌운다. 등락률은 스냅샷에서 구한 전일 종가 대비. → {count, at}"""
    open_m = {m for m in ("KR", "US") if _session_open(m)}
    targets = [it for it in items[:SCREENER_LIVE_MAX] if it.get("market") in open_m]
    if not targets:
        return {"count": 0}
    try:
        got = toss_api.get_prices_cached([it["ticker"] for it in targets])
    except Exception:
        got = {}
    if not got:
        return {"count": 0}
    prevs = prev_closes_today(list(got))
    n = 0
    for it in targets:
        info = got.get(it["ticker"])
        if not info or not info.get("price"):
            continue
        p = float(info["price"])
        chg = toss_api.change_pct(p, prevs.get(it["ticker"]), info)
        if chg is None:
            continue
        if it.get("price") and it.get("market_cap"):
            it["market_cap"] = it["market_cap"] * p / it["price"]
        it["price"], it["change_pct"], it["live"] = p, chg, True
        n += 1
    return {"count": n, "at": datetime.utcnow().isoformat() + "Z"}


_COMPARE_TICKER_RE = re.compile(r"^[A-Z0-9.\-^=]{1,30}$")


@app.route("/api/compare")
def compare_api():
    """?tickers=005930.KS,000660.KS (최대 3개)"""
    raw = [t.strip().upper() for t in (request.args.get("tickers") or "").split(",") if t.strip()]
    tickers = [t for t in raw if _COMPARE_TICKER_RE.match(t)][:3]
    if not tickers:
        return jsonify({"error": "비교할 종목을 1~3개 골라 주세요"}), 400
    t0 = time.time()
    out = screener.compare(tickers)
    out["took_ms"] = round((time.time() - t0) * 1000)
    return app.response_class(json.dumps(out, cls=NumpyEncoder, ensure_ascii=False), mimetype="application/json")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
