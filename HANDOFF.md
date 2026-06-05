# StockAnalyzer 작업 인계 문서

> **다음 세션에서 가장 먼저 이 파일을 읽고 작업 시작할 것**
> 마지막 업데이트: 2026-06-05 KST (토스 연동 세션)

---

## 🚀 현재 상태 (2026-06-05)

- **Railway 정상 동작** (HOBBY 플랜, 배포 정상)
- 최신 커밋: `232b5d2` (toss_to_sql UTF-8 출력 + gitignore)
- Service Worker: **`sa-v34`**
- 정적 자산 캐시 버스팅: HTML에 `?v=34` 쿼리 사용 중
- **토스증권 Open API 운영 동작 확인 완료** ✅ (시세/차트/환율 + 계좌 import)

### ✅ 토스 핵심 기능 모두 동작 확인됨
- 시세/차트/환율: `chart_source:"toss"`, `realtime_source:"toss"` 확인
- **토스 계좌 → 실제 포트폴리오 import 버튼 성공** (10종목)
- 권한 게이팅: `TOSS_OWNER_USER_IDS=1,2` (카카오 id1 + 구글 id2 = 둘 다 윤현호)

### ⚠️ 토스 IP 허용목록 — 핵심 운영 이슈
- 토스 `live` API는 **콘솔 등록 IP에서만** 토큰 발급됨
- **Railway egress IP는 배포당 고정이지만 재배포마다 바뀜** (관측: 52.9.x → 54.177.x
  → 162.220.232.x ...). 한 배포 안에선 안정적(요청마다 안 바뀜).
- **재배포할 때마다 `/api/debug/toss`로 새 egress IP 확인 → 토스 콘솔에 등록** 필요
- 등록 안 하면 자동 yfinance/네이버 폴백 (앱은 정상, 한국 데이터만 지연)
- ⚠️ **IPv6 우회**: `toss_api.py`에서 `urllib3 HAS_IPV6=False`로 IPv4 강제 (해결됨)
- 🔧 **영구 해결책 = 고정 IP 프록시** (`TOSS_PROXY_URL`) — `TOSS_PROXY_SETUP.md` 참고
  (Oracle Cloud 무료 VM + tinyproxy). 코드는 준비됨, VM 세팅만 남음 (사용자 Oracle
  로그인 이슈로 보류 중). 프록시 IP는 1회 등록하면 재배포해도 안 바뀜.

### 다음 우선 작업 후보 (사용자 결정 대기)
- [ ] **고정 IP 프록시(VM) 완성** — 재배포마다 IP 재등록 안 하려면 (`TOSS_PROXY_SETUP.md`)
- [ ] **DART API 키 등록** — 한국 임원 주식 변동 보고 자동 fetch
- [ ] **자동 손절** / **백테스트** / **AI 코치** / **랭킹 시즌제** / **포지션 노트**

---

## 0. 이 문서 사용법

매번 컨텍스트가 리셋되므로, 이 파일이 프로젝트의 **단일 진실 공급원(SSOT)** 역할.
- 작업 끝나면 **§5(최신 작업 로그) 맨 위에 새 항목 추가**
- 프로젝트 구조/제약/관례가 바뀌면 §1~§4도 갱신
- 토큰 절약을 위해 코드 본문 인용은 최소화하고, 파일 경로 + 함수명 위주로 기록

---

## 1. 프로젝트 개요

**StockAnalyzer** — 한국/미국 주식 종합 분석 + 모의투자 PWA
- **배포**: Railway (NIXPACKS, Gunicorn `--workers 1 --threads 8 --worker-class gthread --timeout 120`)
- **DB**: PostgreSQL (Railway 호스팅, 로컬은 SQLite `instance/`)
- **인증**: Google/Kakao OAuth (Flask-Login)
- **프론트**: Vanilla JS + LightweightCharts v4, PWA (Service Worker + manifest)
- **AI**: Groq `llama-3.3-70b-versatile` (+ fallback 모델들)
- **데이터 소스**: yfinance, DART(전자공시), 네이버 금융 스크래핑(mobile API + integration + wisereport)

---

## 2. 디렉토리 구조

```
stock-analyzer/
├─ app.py                    # Flask 메인 (모든 라우트/API)
├─ auth.py                   # OAuth (Google/Kakao) 블루프린트
├─ models.py                 # SQLAlchemy 모델 (User, Holding, Transaction, AssetSnapshot, UserBadge)
├─ badges.py                 # 25개 배지 정의 + evaluate 엔진
├─ toss_api.py               # 토스증권 Open API (시세/캔들/환율/계좌/보유종목 + OAuth2)
├─ scripts/toss_to_sql.py    # 토스 보유종목 → holdings INSERT SQL 생성 (IP 우회용)
├─ TOSS_PROXY_SETUP.md       # 토스 고정 IP 프록시 설정 가이드 (Oracle VM + tinyproxy)
├─ trends_scanner.py         # 트렌드 스캐너 v2 (RS/OBV/Base/Perfect Setup)
├─ build_stock_db.py         # 종목 DB 빌더 → stock_db.json
├─ stock_db.json             # 종목명/티커 매핑 + 메타 (자동완성용)
├─ analysis/
│  ├─ ai_analysis.py         # Groq AI 분석 + 번역 + 캐시 + 스코어카드 연결
│  ├─ advanced.py            # 월가 4팩터 스코어카드 (Q/V/G/M + 10개 지표)
│  └─ indicators.py          # 기술지표 (MA, RSI, OBV, 스테이지 등)
├─ templates/
│  ├─ index.html             # 메인 (분석 + 포트폴리오)
│  ├─ trading.html           # 모의투자 (자산 차트 + 배지 + 닉네임 모달)
│  └─ leaderboard.html       # NEW — 랭킹 페이지
├─ static/
│  ├─ js/
│  │  ├─ main.js             # 메인 페이지 클라이언트
│  │  ├─ trading.js          # 모의투자 + 자산 차트 + 배지/닉네임
│  │  ├─ leaderboard.js      # NEW — 랭킹 페이지 클라이언트
│  │  └─ chart.js            # LightweightCharts 헬퍼
│  ├─ css/style.css          # 전체 스타일 (다크 테마)
│  ├─ icons/                 # PWA 아이콘
│  ├─ manifest.json          # PWA 매니페스트
│  └─ service-worker.js      # SW (현재 sa-v31)
├─ scripts/                  # 일회성 스크립트들
├─ instance/                 # 로컬 SQLite
├─ railway.toml              # Railway 배포 설정
├─ Procfile                  # gunicorn 시작 명령
├─ requirements.txt
├─ runtime.txt               # Python 버전
└─ .env / .env.example       # GROQ_API_KEY, GOOGLE_*, KAKAO_*, DATABASE_URL 등
```

---

## 3. 데이터 모델 (models.py)

### `User` (테이블 `users`)
- `id`, `provider`('google'|'kakao'), `provider_id`, `name`, `email`, `profile_image`, `created_at`
- **모의투자 필드**:
  - `cash_balance` Float, default `INITIAL_CAPITAL_KRW` (1억원)
  - `initial_capital` Float, default 1억원 (수익률 계산 기준)
- **랭킹/프로필 필드** (2026-05-22 추가):
  - `nickname` String(30), unique, nullable (설정 안 하면 랭킹 미노출)
  - `is_public` Boolean, default True (랭킹 공개 여부)
- 관계: `holdings`, `transactions`, `asset_snapshots`, `badges` (cascade delete)
- 유니크: `(provider, provider_id)`

### `Holding` (테이블 `holdings`) — 실제 포트폴리오
- `id`, `user_id`, `ticker`, `name`, `quantity`, `purchase_price`, `currency`('USD'|'KRW'), `created_at`

### `Transaction` (테이블 `transactions`) — 모의투자 거래 내역
- `id`, `user_id`, `ticker`, `name`, `type`('buy'|'sell')
- `price` (native), `quantity`, `currency`, `exchange_rate` (당시 USD/KRW; KRW면 1.0)
- `fee_krw` (수수료 0.1% 환산), `amount_krw` (총액 KRW), `realized_pnl_krw` (매도시), `timestamp`

### `AssetSnapshot` (테이블 `asset_snapshots`) — NEW
- `id`, `user_id`, `date` (DATE, KST 기준 일별)
- `total_assets_krw`, `cash_krw`, `positions_value_krw`
- `created_at`
- 유니크: `(user_id, date)` — 같은 날 1회만 (upsert)
- `trading_dashboard()` 호출 시마다 자동 upsert
- `trading_reset` 시 모두 삭제

### `UserBadge` (테이블 `user_badges`) — NEW
- `id`, `user_id`, `badge_key` (String 50)
- `earned_at` DateTime
- 유니크: `(user_id, badge_key)`
- `badges.py`의 BADGES 정의와 키로 연결
- `trading_reset` 시 모두 삭제

### 상수
- `INITIAL_CAPITAL_KRW = 100_000_000` (모의투자 초기자본 1억원)

### DB 마이그레이션 패턴
`app.py`의 `with app.app_context()` 안에서 raw SQL:
```python
conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS cash_balance DOUBLE PRECISION DEFAULT 100000000"))
```
> Postgres `IF NOT EXISTS` 필수. 새 컬럼 추가 시 여기에 반드시 한 줄 추가.

---

## 4. 주요 API 엔드포인트 (app.py)

### 인증/유저
- `GET /api/me` — 현재 로그인 유저 정보
- `/auth/google`, `/auth/kakao`, `/auth/logout`

### 분석
- `POST /analyze` — 티커 받아 종합 분석 (가격, 지표, AI, 펀더멘털 등)
- `GET /api/autocomplete?q=...` — 종목 자동완성 (stock_db.json 기반)
- `GET /api/trends/...` — 트렌드 스캐너 (start/status/result)
- `GET /api/trends/status` — **절대 캐싱 안 함** (SW에서 명시적 제외)

### 포트폴리오 (실제 보유)
- `GET/POST/PATCH/DELETE /api/holdings/...` — Holding CRUD
- 매입가/수량 편집 모달 지원

### 모의투자 (Paper Trading)
- `GET /api/trading/dashboard` — 총자산, 현금, 포지션(시가평가), 미실현/실현 손익, 통계
- `POST /api/trading/buy` — body: `{ticker, name, price, quantity, currency}`
- `POST /api/trading/sell` — body: `{ticker, price, quantity}`
- `GET /api/trading/transactions` — 거래 내역
- `POST /api/trading/reset` — 전체 초기화 + 1억원 재지급

### 시장 데이터 / 섹터 (2026-05-21)
- `GET /api/sectors/strength?market=US|KR&force=1` — 섹터/테마 강도 랭킹
- `GET /api/sectors/constituents?market=US&ticker=XLK` — US 섹터 ETF 대표 종목 (15개, 등락률순)
- `GET /api/sectors/constituents?market=KR&theme_no=123` — KR 테마 구성 종목 (20개)

### 자산 변화 차트 (NEW — 2026-05-22 저녁)
- `GET /api/trading/history?days=7|30|90|all` — 일별 자산 스냅샷 + KOSPI/S&P500 벤치마크 (시작일 기준 % 정규화)
- 응답: `{ snapshots[], benchmarks: {kospi[], sp500[]}, initial_capital_krw, start_date }`

### 챌린지/랭킹/닉네임 (NEW — 2026-05-22 저녁)
- `GET  /api/me/badges` — 획득 배지 + 전체 정의 (잠금 포함)
- `GET  /api/me/nickname` — 내 닉네임
- `POST /api/me/nickname` body `{nickname}` — 설정 (2~20자, 한글/영문/숫자/_)
- `GET  /api/leaderboard?metric=total|7d|30d&limit=50` — 랭킹 (스냅샷 기반)
- `GET  /leaderboard` — 랭킹 페이지
- dashboard 응답에 `newly_earned_badges`, `nickname` 추가됨

### 토스 계좌 / 진단 (NEW — 2026-06-05)
- `POST /api/portfolio/import-toss` — 토스 계좌 보유종목을 실제 포트폴리오(Holding)로 동기화
  - 소유자 게이팅: `TOSS_OWNER_EMAIL`(콤마) 또는 `TOSS_OWNER_USER_IDS`(콤마) 중 매칭
  - 기존 실제 포트폴리오 교체(sync), 한국=.KS/.KQ 자동판별
- `GET  /api/debug/toss` — egress IP + 토큰 발급 여부 + 프록시 IP 진단 (IP 등록용)

### 헬퍼 (app.py 내부)
- `_get_usd_krw_rate()` — yfinance `KRW=X`, **10분 캐시**
- `_to_krw(amount, currency)` — KRW 환산
- `_fetch_current_price(ticker)` — 시세 조회 (대시보드 평가용)
- 수수료: 거래금액의 **0.1%**
- `fetch_naver_realtime_price(krx_code)` — 한국 실시간 시세 (~1분 지연, 30초 캐시)
- `fetch_kr_short_interest(krx_code)` — 네이버 일별 공매도 스크래핑
- `fetch_kr_supply_demand(krx_code)` — 네이버 외국인/기관 수급 스크래핑
- `fetch_kr_supplements(krx_code)` — 위 두 함수 통합 (30분 캐시)
- `_is_market_open(ticker)` — KST/EST 기준 장 개장 판별
- `_strip_hanja(text, label)` — Groq 응답 한자 → 한글 변환 + 잔여 한자 제거
- `_groq_chat(...)` — 모든 Groq 호출 공용 (model fallback + retry + 한자 필터 자동 적용)
- `US_SECTOR_HOLDINGS` — SPDR 11개 ETF별 대표 종목 ~20개 (하드코딩)
- `_US_NAME_FALLBACK` — 미국 주요 종목 영문명 매핑 ~150개
- **자산차트/배지 (저녁 세션)**:
  - `_save_today_snapshot(user_id, total, cash, positions)` — KST 일별 자산 스냅샷 upsert
  - `_fetch_benchmark_history(symbol, lookback_days)` — KOSPI/S&P500 yfinance 1h 캐시
  - `_normalize_benchmark(history, start_date_str)` — 시작일 기준 % 변환
  - `_build_badge_context(user, summary, positions, holdings_count)` — 배지 평가용 ctx 빌더
  - `_check_and_award_badges(user, ctx)` — 자격 충족된 배지 자동 부여, 새 키 리스트 반환
- **토스 (2026-06-05)**:
  - `_get_price_df(ticker, interval, min_bars)` — 토스 캔들 우선 → yfinance 폴백
  - `_toss_symbol_to_ticker(symbol, market)` — 토스 심볼 → 앱 티커 (.KS/.KQ)
  - `toss_api.*` — `get_prices/get_price`, `get_candles_df`, `get_exchange_rate`,
    `get_accounts`, `get_account_holdings`(다계좌 합산), `_proxies/_req`(프록시)

---

## 5. 최신 작업 로그 (최신 → 과거 순)

### 📌 2026-06-05 — 토스 계좌 import + 포트폴리오 분리 + 프록시 지원 (다수 커밋)
이번 세션 토스 관련 대규모 작업. 핵심 흐름:

**1) 토스 시세/차트/환율 연동** (`1625299`, `b4ea46f`)
- `_get_price_df` 토스 캔들 우선 + yfinance 폴백 (build_chart_data, analyze)
- `_get_usd_krw_rate`/`_fetch_current_price`/실시간 시세 토스 우선
- 실동작 수정 3건: IPv4 강제(HAS_IPV6=False), 캔들 중첩응답 파싱(result.candles +
  nextBefore 페이지네이션), 한국 캔들 날짜 밀림(시장 오프셋 보존)

**2) 포트폴리오 ↔ 모의투자 테이블 분리** (`1ed7bb2`)
- 신규 `PaperHolding` 모델 — 모의투자 전용. `/api/trading/*` → PaperHolding
- `/api/portfolio/*` + 분석 "내 포지션" → Holding (실제 보유)
- 마이그레이션: create_all 전 `_paper_holdings_existed` 체크 → 최초 분리 시
  기존 holdings를 paper_holdings로 1회 이전, 실제 포트폴리오는 빈 상태로 시작
- 랭킹/스냅샷/배지는 모의투자 기반 → 실제 포트폴리오 자동 제외

**3) 랭킹 방어** (`651080a`)
- 비현실적 수익률(>100000%) / 손상자산(>100조) 제외 (테스트 쓰레기 데이터 대응)
- 별도로 DB에서 비정상 계정 정리 SQL 제공함

**4) 토스 계좌 import** (`35e5f1c`, `7864c55`, `b774480`)
- `POST /api/portfolio/import-toss` — 토스 보유종목 → 실제 포트폴리오 동기화
- `toss_api.get_accounts/get_account_holdings`(다계좌 합산)
- 소유자 게이팅: `TOSS_OWNER_EMAIL`(콤마) + `TOSS_OWNER_USER_IDS`(콤마, 카카오는 이메일
  미제공이라 user_id로). 윤현호 = id1(kakao)+id2(google) → `TOSS_OWNER_USER_IDS=1,2`
- 프런트 "↻ 토스 계좌 불러오기" 버튼 → **import 성공 확인됨 (10종목)**

**5) 고정 IP 프록시 지원** (`b4008b7`, `65ddef9`)
- Railway egress IP가 재배포마다 바뀌는 문제 → `TOSS_PROXY_URL` 경유 지원
- `toss_api._req`로 모든 토스 호출 프록시 라우팅. 미설정 시 직접 연결
- `TOSS_PROXY_SETUP.md` 가이드 (Oracle 무료 VM + tinyproxy). VM은 사용자 보류 중

**6) IP 우회 SQL 스크립트** (`ba8705c`, `232b5d2`)
- `scripts/toss_to_sql.py` — 로컬(IP 등록됨)에서 토스 보유종목 → INSERT SQL(UTF-8 파일)
  생성 → Railway DB 콘솔 붙여넣기. 서버 import가 IP로 막힐 때 우회용
- 생성 파일 `holdings_import_*.sql`은 gitignore

### 📌 2026-05-22 심야 — 토스증권 Open API 연동 (`1625299`)
**목적**: 시세/차트/환율을 토스증권 공식 데이터로 (특히 한국 종목 정확도 ↑).
yfinance/네이버는 폴백으로 유지 → 토스 실패/미커버 시 자동 대체.

**`toss_api.py` (NEW)**:
- OAuth2 Client Credentials → access_token 24h 캐싱 (thread-safe)
- `get_prices/get_price`: 실시간 시세 (한국+미국)
- `get_candles_df`: 일봉 페이지네이션(최대 ~2000봉)+60초 캐시, 주/월봉은 일봉 리샘플(W-FRI/ME)
- `get_exchange_rate`: USD/KRW
- `to_toss_symbol` (005930.KS→005930), `is_eligible` (지수^/환율=X 제외)
- `_extract_list`: 응답 래퍼 방어적 파싱

**`app.py` 통합**:
- `_get_price_df(ticker, interval, min_bars)`: 토스 캔들 우선 → yfinance 폴백
  → `build_chart_data()` + `analyze()` df/df_weekly에 적용
- `_get_usd_krw_rate()`: 토스 환율 우선
- `_fetch_current_price()`: 토스 우선 (대시보드)
- analyze 실시간 시세: **토스 → 네이버(KR) → yfinance**
- 응답에 `realtime_source="toss"`, `chart_source` 추가
- 펀더멘털(stock.info)은 토스 미제공 → yfinance 유지

**프런트**: main.js 실시간 출처 라벨 "토스 실시간" 추가

**엔드포인트** (토스 base `https://openapi.tossinvest.com`):
- `POST /oauth2/token` (form: grant_type=client_credentials, client_id, client_secret)
- `GET /api/v1/prices?symbols=005930,AAPL` (최대 200개, 콤마구분)
- `GET /api/v1/candles?symbol=X&interval=1m|1d&count=1~200&before=ISO&adjusted=true`
- `GET /api/v1/exchange-rate?baseCurrency=USD&quoteCurrency=KRW`

**⚠️ 미해결**: `live` 키가 IP 허용목록 필요 → 콘솔에서 IP 등록 전까지 401 (폴백 동작)

### 📌 2026-05-22 저녁 — 모의투자 포지션 카드 색상도 개선 (`d69cd95`)
**문제**: ed3cf61 에서 분석 페이지 포트폴리오 카드(`main.js _pfCardHTML`)만 수정.
모의투자 페이지는 별도 함수(`trading.js renderPositions`)가 카드를 그리는데 옛
`score-high/mid/low/neg` 클래스 그대로였음.

**해결** (`static/js/trading.js`):
- renderPositions의 badgeCls/dirClass 로직을 main.js와 동일하게 통일
- 방향 우선 (`score-gain-strong/gain/flat/loss/loss-strong`)
- 카드에 `pf-dir-gain/loss/flat` 추가 → 좌측 컬러 보더
- 손익 금액 `pf-amount` 클래스 (굵기/크기 강조)
- 캐시 버스팅 `?v=29 → ?v=30`, SW `sa-v30 → sa-v31`

### 📌 2026-05-22 저녁 — SW 캐시 우회 (`994ac68`)
**문제**: 클라이언트가 옛 Service Worker 캐시로 인해 새 main.js/style.css를 못 받음
**해결**: 모든 HTML의 정적 자산에 `?v=29` 쿼리 추가 → URL 자체가 달라져 옛 SW
캐시 미스 → 강제 네트워크 fetch. SW `sa-v29 → sa-v30`.

### 📌 2026-05-22 저녁 — 포트폴리오 카드 수익/손실 한눈에 (`ed3cf61`)
**문제**: 기존 뱃지 색이 강도 기준 (high=노랑, mid=파랑, low=회색, neg=빨강) 이라
+10%와 -3% 가 모두 파랑·노랑으로 보여 수익/손실 즉시 구분이 어려움.

**해결** (`static/js/main.js` + `static/css/style.css`):
- 뱃지 색은 방향이 1순위: 녹(이익) / 회(보합) / 적(손실)
- 진하기는 절댓값: `gain-strong(15%+) / gain / flat / loss / loss-strong(-15%+)`
- 카드 좌측 4px 컬러 보더 (`pf-dir-gain/loss/flat`)
- 좌측 그라데이션 배경 (수익=연녹/손실=연적)
- 손익 금액 `+/-원` 13px bold 강조
- ⚠️ 분석 페이지의 _pfCardHTML 만 수정 — trading.js는 d69cd95에서 보완

### 📌 2026-05-22 저녁 — 챌린지(배지 25종) + 랭킹 + 닉네임 (`7ae81d7`)
**DB**:
- User에 `nickname`(unique), `is_public` 컬럼 추가
- 신규 `UserBadge` 모델
- PostgreSQL 마이그레이션 SQL 추가

**`badges.py` (NEW)**:
- 25개 배지 7카테고리 5티어: 활동(4) / 수익(6) / 트레이딩(3) / 포트폴리오(3)
  / 글로벌(3) / 성과(3) / 꾸준함(3)
- 티어: 브론즈/실버/골드/다이아/전설
- `evaluate_badges(ctx, earned_keys)` — 이미 획득한 키 제외하고 새로 자격된 키 반환
- `badge_public_dict(b)` — 프론트로 보낼 때 check 함수 제외

**`app.py` 통합**:
- `_build_badge_context()` — 거래 통계/포지션/스냅샷 일수 등 ctx 빌드
- `_check_and_award_badges()` — DB에 새 배지 insert + 새 키 반환
- `trading_dashboard()`에서 자동 호출, 응답에 `newly_earned_badges` 포함

**새 API**:
- `GET /api/me/badges`, `GET/POST /api/me/nickname`, `GET /api/leaderboard`
- `GET /leaderboard` 페이지 라우트

**UI**:
- `/leaderboard` 신규 페이지 (탭 3개: 전체/30일/7일, 금/은/동 메달, 내 행 하이라이트)
- `/trading` 닉네임 변경 버튼(🏷️) + 모달 (최초 1회 자동 안내)
- `/trading` 🏅 내 배지 섹션 (카테고리별 그룹, 잠금/획득 시각화, 티어 색상)
- 새 배지 획득 시 우측 하단 토스트 알림 (3.2초 간격 순차)
- 헤더 nav에 `🏆 랭킹` 링크 추가 (모든 페이지)

**규칙**:
- 닉네임 설정 안 한 사용자는 랭킹에 등장 안 함 (옵트인)
- 다른 유저의 user_id는 응답에서 제거 (프라이버시)
- `trading_reset` 시 배지/스냅샷도 삭제 (완전 리셋)

### 📌 2026-05-22 — 시가총액 None 포맷 오류 수정 (`fdaa822`)
**문제**: 288980.KS 분석 시 "unsupported format string passed to NoneType.__format__"
**원인**: `fetch_company_overview` 프롬프트의 `{mktcap:,} {currency}` 가 yfinance의
None 값을 만나면 크래시. 일부 한국 소형주는 marketCap이 None.

**해결**:
- 시가총액/PER/선행PER 모두 None-safe 포맷 헬퍼로 분리 (조원/억원 단위 자동 선택)
- analyze() except 블록에 traceback 로깅 추가 (향후 디버깅용)

### 📌 2026-05-22 저녁 — 자산 변화 차트 + KOSPI/S&P500 벤치마크 (`4e0bf61`)
**DB**:
- 새 모델 `AssetSnapshot` (user_id, date, total_assets_krw, cash_krw, positions_value_krw)
- `(user_id, date)` 유니크 → KST 기준 일별 1회 upsert
- PostgreSQL CREATE TABLE IF NOT EXISTS + index

**`app.py`**:
- `_save_today_snapshot()` — `trading_dashboard()` 호출 시 자동 upsert
- `_fetch_benchmark_history(symbol, days)` — ^KS11/^GSPC yfinance, 1시간 메모리 캐시
- `_normalize_benchmark(history, start_date)` — 시작일 첫 종가를 100% 기준으로 정규화
- 새 엔드포인트 `GET /api/trading/history?days=7|30|90|all`
- `trading_reset()`에서 스냅샷 함께 삭제

**프런트** (`trading.html` + `trading.js`):
- LightweightCharts CDN 추가
- `📈 자산 변화 추이` 카드 (포트폴리오 요약 ↔ 빠른 매수 사이)
- 기간 토글 7D/30D/90D/전체
- 3-line chart: 내 자산(녹) / KOSPI(파) / S&P500(주황) 모두 `% return`
- 헤더 우측 비교 요약 (`내 +5.3% · KOSPI +1.2% · S&P +0.8%`)
- 0% baseline 점선
- 데이터 부족 시 안내 문구

### 📌 2026-05-22 — 전일 종가 계산 오류 수정 (`3cf54ea`)
**문제**: RKLB처럼 변동성 큰 종목에서 `fast_info.previous_close`가 이틀 전 종가 반환 → 실제 -5.1%인데 +3.6%로 표시

**해결** (`app.py` analyze() 함수):
- `fast_info.previous_close` 완전 폐기, 히스토리에서 직접 판단
- `is_last_bar_today` 플래그: `df.index[-1]`이 시장 타임존 기준 오늘인지 판별
- 오늘 인트라데이 봉이면 → `iloc[-2]`가 어제 종가
- 어제 봉이면 (pre-market 등) → `iloc[-1]`이 어제 종가
- 모든 시나리오(장중/장마감/pre-market/주말) 검증 통과

### 📌 2026-05-22 — Groq AI 프롬프트 강화 + 한자 자동 변환 (`5535259`)
**기업분석 프롬프트 v2** (`fetch_company_overview`):
- 시니어 애널리스트 페르소나, 한국 투자자용 톤
- [기업소개] 한국 유사 기업 비유 의무 (월마트=이마트+홈플러스)
- [주요사업] 매출 비중/규모 의무 → 비즈니스 모델 한눈에
- [기업분석] 주어진 매출 추이/PER 직접 인용, 경쟁사 실명 거론 의무
- max_tokens 800 → 1200, temp 0.4 → 0.35

**급락/급등 이유 v2** (`analyze_move_reason`):
- 시니어 트레이더 페르소나
- 4단계 우선순위 프레임워크 (캐털리스트→섹터/시장→기술적→수급)
- 출력 4요소: 핵심 원인 / 시장 맥락 / 데이터 근거 / **모니터링 포인트**
- max_tokens 400 → 550

**한자 방지 3중 안전장치**:
1. `system_msg`에 한자 절대 금지 + 한자어→한글 풀어쓰기 의무 명시
2. `user_msg`에 倉庫→창고, 賣出→매출 예시 명시
3. `_strip_hanja()` 응답 후처리: 35개 자주 등장 한자어 한글 변환 + 잔여 한자 정규식 제거 + 로그
- `_HANJA_RE` 정규식: CJK 통합 한자 + 확장 A + 호환 한자
- `_HANJA_MAP` 매핑: 35개

### 📌 2026-05-22 — 스코어카드 모바일 탭 도움말 (`7b61525`)
**문제**: HTML `title` 속성은 모바일에서 동작 안 함 → ⓘ 설명을 볼 수 없음

**해결** (`static/js/main.js`):
- `toggleScHelp(iconEl, event)`: ⓘ 탭 시 anchor(metric row / factor card / header) 안에 도움말 박스 추가
- 같은 ⓘ 재탭 → 토글로 닫힘
- 다른 ⓘ 탭 → 이전 박스 자동 닫힘
- 빈 공간 클릭 → 모든 박스 닫힘 (전역 click listener)
- 데스크톱은 기존 `title` hover 그대로 유지
- 4팩터/헤더 ⓘ도 `_helpInline()` 헬퍼로 통일
- 활성화 ⓘ → `m-help-open` 클래스 → 파란 배경 강조
- 💡 이모지 prefix, fade-in 애니메이션
- 모바일: ⓘ 22×22 (데스크톱 18×18) 터치 영역 확보

### 📌 2026-05-22 — 섹터 강도 모바일 새로고침 버튼 컴팩트 (`a49abc9`)
- 모바일(≤640px)에서 `🔄 새로고침` 텍스트 줄바꿈 문제
- 데스크톱은 텍스트 유지, 모바일은 아이콘 🔄 만 원형 36×36
- `<span class="btn-label-desktop">` 모바일에서 display:none
- `title`/`aria-label` 보존 접근성 OK

### 📌 2026-05-22 — 섹터 강도 상위 5개만 표시 (`6c126a0`)
- `TOP_N = 5` 상수, 기본 5개 표시
- "더 보기 ▼ (N개 더)" 클릭 시 전체 토글 (기존 동작 유지)

### 📌 2026-05-22 — 한국 종목 보충 데이터 (`4a9a837`)
**배경**: yfinance가 한국 종목 내부자/공매도 데이터 미수집 → 스코어카드 비어있음

**백엔드** (`app.py`):
- `fetch_kr_short_interest(krx_code)`: 네이버 일별 공매도 페이지 스크래핑
  → 당일 공매도 비중 / 잔고 비중 / 5일 평균
- `fetch_kr_supply_demand(krx_code)`: 네이버 외국인/기관 페이지 스크래핑
  → 외국인 보유율 / 외국인+기관 5일 순매수 (부호 색상 보정)
- `fetch_kr_supplements(krx_code)`: 30분 캐시 통합 fetcher
- `analyze()`: 한국 종목이면 `scorecard.metrics`에 `kr_*` 필드 주입
- DART 공시 검색 URL 자동 생성 (corp_code 없이 종목코드로 직링크)

**프런트**: 한국 종목 분석 시 별도 섹션 **🇰🇷 한국 시장 시그널** 자동 등장
- 외국인 보유율 / 외국인·기관 5일 순매수 / 공매도 잔고+5일평균
- 부호 색상 (good=green / bad=red), 주식수 단위 자동 변환 (천/만/억주)
- 내부자 매매 행은 **DART 공시 보러가기 ↗** 링크로 대체
- 모든 지표 ⓘ 툴팁 (kr_foreign, kr_for_net, kr_inst_net, kr_short_bal, kr_short_5d)

### 📌 2026-05-22 — 스코어카드 ⓘ 툴팁 + 한국 한계 안내 (`3efd4e3`)
- `_SC_HELP` 객체: 각 지표 설명 (의미/임계값/판단 기준)
- `_helpIcon(key)` 헬퍼: ⓘ HTML 생성
- 4팩터 카드 hover 시 무엇을 측정하는지 표시
- 종합 등급 헤더에 부제목 추가
- 한국 종목 분석 시 노란 안내 박스: 일부 지표 yfinance 미수집 가능

### 📌 2026-05-22 — 월가 스타일 펀더멘탈 스코어카드 (`c37ce96`)
**새 모듈** `analysis/advanced.py`:
- **Tier 1 (5)**: FCF Yield/Margin, PEG, Margins+YoY, EPS Revision, EV/EBITDA
- **Tier 2 (5)**: Shareholder Yield, Short Interest, Insider Activity, Piotroski F-Score, Net Debt/EBITDA
- **Q/V/G/M 4팩터** 점수 (각 0~100):
  - Quality   = ROE + FCF Margin + Op Margin + F-Score + Net Debt
  - Value     = P/E + PEG + EV/EBITDA + FCF Yield
  - Growth    = 매출/EPS 성장 + 마진 추세
  - Momentum  = 1M 가격 + EPS Revision + 영업마진 YoY
- **종합 등급**: A+/A/B+/B/C+/C/D/F (4팩터 평균)

**ai_analysis.py 통합**:
- `analyze_signals(df, info, df_weekly, stock=None)` — `stock` 인자 추가
- `scorecard` 키로 응답에 포함

**UI** (분석 페이지 "투자 판단" 탭 최상단):
- 종합 등급 + 4팩터 그리드 (각 카드별 등급/점수/막대)
- 등급별 색상 (A=green, B=blue, C=yellow, D=orange, F=red)
- 접이식 상세 (10개 지표 카테고리별)
- 좋음/나쁨 임계값으로 색상 강조

### 📌 2026-05-22 — 미국 종목명 표시 + 한국 테마 스크래퍼 견고화 + 모의투자 새로고침 (`a687b9f`)
**1) 미국 종목명**:
- `_US_NAME_FALLBACK` 매핑 ~150개 (SPDR 상위 종목)
- `_lookup_us_name(ticker)`: STOCK_DB 우선, 없으면 폴백
- 카드 표시: `Apple (AAPL)` / `Micron (MU)` 형식

**2) 한국 테마 상세 스크래퍼 견고화** (`_fetch_kr_theme_constituents`):
- 셀렉터: `type_5` 하드코딩 → "code= 링크가 가장 많은 테이블" 자동 선택
- 인코딩 자동 감지 (euc-kr → cp949 → utf-8)
- 등락률 컬럼 자동 폴백 (% 포함 셀 탐색)
- 음수 부호 보정: img/CSS class/text 모두 검사

**3) 모의투자 자산 새로고침** (`trading.html` + `trading.js`):
- 작은 원형 ↻ 버튼 (초기화 옆)
- `refreshDashboard(btn)`: 시세/평가금액 재fetch
- spinning 클래스 회전 애니메이션 (최소 400ms 표시)

### 📌 2026-05-22 — 섹터/테마 카드 펼치면 대표 종목 상승률 순 (`5193c4c`)
**백엔드**:
- `US_SECTOR_HOLDINGS`: SPDR ETF별 대표 종목 ~20개 하드코딩
- `_fetch_us_sector_constituents(etf_ticker)`: yfinance 일괄 fetch → 당일 등락률 → 상위 15개
- `_fetch_kr_theme_constituents(theme_no)`: 네이버 테마 상세 스크래핑 → 상위 20개
- 새 엔드포인트 `/api/sectors/constituents`
- KR 테마 강도 응답에 `theme_no` 추가

**프런트**:
- 섹터 카드 클릭 → 인라인 드로어 열림 (▼ 회전)
- 외부 링크는 ↗ 별도 버튼
- 종목 행 클릭 → 분석 페이지로 자동 검색
- 5분 캐시, 모바일 반응형 그리드

### 📌 2026-05-22 — 섹터/테마 강도 랭킹 탭 (`b52dfcb`)
**백엔드** (`app.py`):
- `US_SECTOR_ETFS`: SPDR 11개 (XLK/XLC/XLY/XLP/XLF/XLV/XLI/XLE/XLB/XLU/XLRE)
- `_fetch_us_sector_strength()`: yfinance 일괄 fetch → 강도 점수 = `0.3*1D + 0.4*1W + 0.3*1M + log(거래량비) * 3`
- `_fetch_kr_theme_strength()`: 네이버 테마 페이지 6페이지 스크래핑 (~200개)
  → 강도 점수 = `0.45*1D + 0.35*3D + 0.20*상승종목폭`
  → 주도주 3개 포함
- 5분 메모리 캐시, `?force=1` 무효화
- `/api/sectors/strength?market=US|KR` 엔드포인트

**프런트**:
- 메인 페이지 '추세 상승 감지' ↔ '내 포트폴리오' 사이에 **🔥 섹터 강도** 탭
- 1/2/3위 금/은/동 색상 강조

### 📌 2026-05-22 — 한국 실시간 시세 네이버 폴링 API (`9d2a845`)
- `fetch_naver_realtime_price(krx_code)`: `polling.finance.naver.com/api/realtime/domestic/stock/{code}`
- 30초 메모리 캐시, 5초 타임아웃
- 한국 종목(.KS/.KQ)은 네이버 우선 (~1분 지연), 실패 시 yfinance fast_info 폴백 (15~20분 지연)
- `stock_data.realtime_source` 노출 (`naver` / `yfinance` / null)
- LIVE 뱃지 옆에 출처 라벨 ("네이버 ~1분" / "Yahoo 15~20분")

### 📌 2026-05-22 — 장중 실시간 가격 + LIVE 뱃지 (`5c6ddf8`)
- `yf.Ticker(t).fast_info.last_price` 로 실시간 시세 fetch
- `_is_market_open(ticker)`: 시장 개장 판별 (KST 9:00-15:30 / EST 9:30-16:00)
- `stock_data`에 `is_realtime` / `is_market_open` / `data_timestamp` 추가
- UI: 🔴 LIVE 뱃지 (맥동 빨간 점) / 🕐 장중 지연 / 🔒 장 마감 + 날짜
- ⚠️ 5/22 발견된 `fast_info.previous_close` 버그 → `3cf54ea`에서 수정

### 📌 2026-05-22 — 숫자 스테퍼 UX 개선 (`8ad2d6a`)
- `▲ / ▼` 작은 세로 화살표 → **`[−]` input `[+]`** 가로 레이아웃
- 버튼 크기 30×18 → **44×44** (터치 친화)
- 기호: ▲▼ → **− / +** (명확)
- 입력창 가운데 정렬, scale(0.95) → scale(0.88) (피드백 강화)
- 적용 범위: index.html 6곳 + trading.html 4곳 + CSS

### 📌 2026-05-20 저녁 — Railway 배포 트러블슈팅 (미해결, 내일 이어서)
**발생 순서**
1. 아침: Railway Major Outage 복구 확인 → 정상 동작 확인
2. 오후: 분석↔모의투자 통합 코드 작업 → 커밋 `68d0575` push → Railway 자동 재배포 트리거
3. 그러나 **"Trial maxed out"** 상태로 배포 실패 → HOBBY 플랜 $5 결제 완료
4. 결제 후에도 "Limited Access — Deploys have been paused temporarily" 메시지 지속
5. 빈 커밋 4번 push 시도 (`a6edb29`, `66bd357`, `d165ac9`, `f7c7f41`) → 모두 REMOVED 처리
6. ACTIVE는 여전히 옛 배포 (`fix: Service Worker /static/js/* 전체 network-first`, 23시간 전)

**원인 추정**
- Railway 결제 시스템과 deployment 시스템 간 동기화 지연
- 또는 짧은 시간에 빈 커밋 반복 push로 abuse detection 트리거 가능성
- Railway 내부 시스템 버그 가능성

**시도한 것**
- ✅ 빈 커밋 push로 강제 트리거 (효과 없음)
- ✅ Railway 대시보드에서 QUEUED 배포 Cancel
- ❌ Railway Discord 문의 (내일 아침 진행 예정)
- ❌ 서비스 Restart (시도 안 함)

**해결 방향 (내일)**
- Railway Discord/Help 문의가 가장 빠를 것
- 안 풀리면 Render.com 또는 Fly.io로 이전 (코드는 GitHub에 안전)

### 📌 2026-05-20 — 분석↔모의투자 통합 (Step 2 첫 항목)
**목적**: 분석 페이지에서 바로 모의 매수/매도 가능하게 → UX 큰 개선, DB 변경 없음

**변경 파일**
- `templates/index.html`:
  - 차트 아래 `tradeActions` 영역 재구성:
    - 신규 `#paperTradeActions` div: "💰 모의 매수" 버튼 (항상 노출, 로그인 시) + "💸 모의 매도" (보유 시) + "📊 모의투자 대시보드" 링크
    - 기존 `#tradeActions` 버튼들은 "📁 포트폴리오 추가매수/매도"로 라벨 정정 (이전엔 "모의 매수"라고 잘못 적혀 있었음)
  - 신규 모달 2개: `#paperBuyModal`, `#paperSellModal` (trading.html과 동일한 UX, ID만 분리)
- `static/js/main.js`:
  - `analyze()` 끝에 `window._lastStock = data.stock` 저장
  - `renderPaperTradeActions(stock)` 신규: 로그인 시 모의 매수 버튼 항상 노출, `/api/trading/dashboard`로 해당 티커 보유 확인 후 매도 버튼 조건부 노출
  - 매수: `openPaperBuyFromAnalysis`, `updatePaperBuySummary`, `submitPaperBuy`
  - 매도: `openPaperSellFromAnalysis`, `setPaperSellMax`, `updatePaperSellSummary`, `submitPaperSell`
  - 입력 검증: `_stepPaper(modalType, inputId, dir)` (KRW 100원/USD 1$ 단위)
  - 성공 시 `confirm()` → "/trading 으로 이동할까요?" 선택 가능
- `static/css/style.css`: `.trade-btn.link` 스타일 + `#paperTradeActions + #tradeActions` 여백
- `static/service-worker.js`: `CACHE_VERSION` `sa-v14` → **`sa-v15`** (JS/CSS/HTML 변경 반영)

**테스트 체크리스트** (Railway 복구 후)
- [ ] 비로그인 → 분석 시 모의 매수 버튼 안 보임
- [ ] 로그인 + 미보유 종목 분석 → 모의 매수만 보임
- [ ] 모의 매수 → 보유 후 같은 종목 다시 분석 → 매도 버튼도 보임
- [ ] USD 종목 (AAPL) + KRW 종목 (005930.KS) 각각 매수/매도
- [ ] 매수/매도 후 "/trading 이동" confirm 동작

### 📌 2026-05-20 — Railway Major Outage 대응 + HANDOFF 정비
- **차단 이슈**: Railway Edge Network 대규모 장애 (5/19 22:29 UTC 시작, 22:43 UTC 원인 파악)
- 상위 클라우드 제공자 장애 → Railway 대시보드 + 배포 앱 모두 다운
- 마지막 empty commit `337aded` push해둠 (복구 시 자동 재배포 트리거)
- 복구 확인: https://railway.statuspage.io/ → "Resolved"
- 이 HANDOFF.md 생성 (다음 세션 컨텍스트 절약용)

### 📌 2026-05-20 이전 — 모의투자 Step 1 구현 (1억원 초기자본)
**백엔드**
- `models.py`: `INITIAL_CAPITAL_KRW = 100_000_000`, User에 `cash_balance`/`initial_capital`, `Transaction` 모델 신규
- `app.py`: 마이그레이션 SQL + 5개 API (`/api/trading/*`) + 환율/시세 헬퍼

**프론트엔드**
- `templates/trading.html` 완전 재작성: 로그인 게이트, 대시보드(자산/현금/포지션/손익), 빠른매수, 보유종목, 거래내역, 매수/매도 모달
- `static/js/trading.js` (380+줄) 신규: `checkAuthAndLoad`, `loadDashboard`, `renderSummary/Positions/Transactions`, 모달, 자동완성, `_stepTradeNum`
- `static/css/style.css`: `.portfolio-summary`, `.ps-*`, `.tx-table`, `.trade-summary`, `.sell-max-btn` 등 + 768px 모바일

### 📌 그 이전 — 버그/개선 작업들
1. **프로필 이미지 거대화 버그** (`/trading`): `.user-avatar` → `.profile-img` + inline style 강제 (28x28)
2. **Service Worker 캐싱 문제**: `sa-v14`로 bump, `/static/js/*` 전체를 **network-first**로 변경 (이전엔 main.js만)
3. **EPS 차트**: 최근 5분기로 제한 (`sorted(..., reverse=True)[:5]`)
4. **포트폴리오 편집 모달**: 매입 평균가/수량 직접 수정
5. (이전) Plan D 매매 추천 (R:R 1:3, Stage 2), Trend Scanner v2 (35/40/25 가중치), DART/네이버 통합

---

## 6. 다음 할 일 (우선순위 순)

### ✅ 최근 완료 (2026-05-22 저녁 세션)
- [x] **자산 변화 차트** + KOSPI/S&P500 벤치마크 (4e0bf61)
- [x] 시가총액 None 포맷 오류 수정 (fdaa822)
- [x] **챌린지 25배지** + **랭킹 시스템** + **닉네임 프로필** (7ae81d7)
- [x] 포트폴리오 카드 수익/손실 색상 한눈에 — 분석 페이지 (ed3cf61)
- [x] SW 캐시 우회 ?v= 쿼리 패치 (994ac68)
- [x] 모의투자 페이지 포지션 카드도 색상 개선 (d69cd95)

### ✅ 이전 완료 (2026-05-22 낮 세션)
- [x] 분석↔모의투자 통합
- [x] 숫자 스테퍼 UX (`[−] input [+]`)
- [x] 장중 실시간 가격 + LIVE 뱃지 (yfinance + 네이버 폴링)
- [x] 섹터/테마 강도 랭킹 탭 + 구성종목 펼침
- [x] 미국 종목명 표시 + 한국 테마 스크래퍼 견고화
- [x] 모의투자 자산 새로고침 버튼
- [x] 월가 4팩터 펀더멘탈 스코어카드 (Q/V/G/M + 10개 지표)
- [x] 스코어카드 ⓘ 툴팁 (데스크톱 hover + 모바일 탭)
- [x] 한국 종목 보충 데이터 (공매도/외국인-기관 수급/DART 링크)
- [x] Groq AI 프롬프트 강화 + 한자 자동 변환
- [x] 전일 종가 계산 버그 수정

### 📋 Step 4 (고급 — 다음 후보)
- [ ] DART API 키 등록 (한국 임원 매매 자동 fetch — 키만 발급받으면 됨)
- [ ] 자동 손절 (매수 시 손절가 입력 → 도달 시 알림/자동 매도)
- [ ] 백테스트 (과거 데이터로 전략 검증)
- [ ] AI 코치 (매매 패턴 학습 후 피드백)
- [ ] 랭킹 시즌제 (월간/시즌별 리셋 + 시즌 배지)
- [ ] 포지션 노트 (종목별 매매 일지/메모)

### 📋 데이터 보강 (옵션)
- [ ] **DART API 키 등록** — 한국 임원 주식 변동 자동 fetch
  - 등록: https://opendart.fss.or.kr/ (무료)
  - 환경변수 `DART_API_KEY` 추가하면 됨
- [ ] **KRX 공식 공매도 API** — 더 정확한 실시간 공매도

---

## 7. ⚠️ 반드시 기억할 제약/관례

### 7.1 Service Worker 캐싱
- **JS/CSS 변경 시 `static/service-worker.js`의 `CACHE_VERSION` 올려야** 사용자에게 반영됨
- 현재: `sa-v31`
- HTML 의 정적 자산은 **`?v=NN` 쿼리 캐시 버스팅** 사용 (현재 v=30) —
  옛 SW 캐시 우회용. CSS/JS 큰 변경 시 SW 버전과 함께 v 도 올릴 것
- `/static/js/*`, `style.css`, `manifest.json` → **network-first** (실시간 반영)
- `/api/trends/status` → 캐싱 완전 제외
- 기타 정적자산 → cache-first
- HTML 네비게이션 → network-first (오프라인 시 캐시된 `/`)

### 7.2 DB 마이그레이션
- `models.py`에 컬럼 추가 = `app.py` `with app.app_context()`에 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` 라인 추가 **필수**
- Railway 재배포 시에만 적용되므로 push 후 확인

### 7.3 환율
- `_get_usd_krw_rate()` 10분 캐시 → 테스트 시 즉시 반영 안 됨
- KRW 종목은 환율 1.0으로 처리

### 7.4 수수료
- 모든 모의 거래에 **0.1%** 고정 적용 (실제 증권사와 다름)

### 7.5 git 커밋
- **사용자가 명시적으로 요청할 때만** 커밋
- Co-Authored-By 라인 포함
- 마지막 empty commit `337aded` = Railway 재배포 트리거용

### 7.6 코드 스타일
- 한국어 주석 OK, UI 텍스트는 한국어
- 이모지는 UI에만 (코드 주석/메시지엔 자제)
- Vanilla JS (프레임워크 없음) — main.js와 trading.js 모두

### 7.7 JSON NaN 처리
- yfinance/numpy NaN 값이 JSON 직렬화 깨뜨림 — `app.py`에 sanitizer 있음, 새 응답 추가 시 통과시킬 것

### 7.8 setInterval 네이밍
- 페이지마다 다른 변수명 사용 (충돌 방지) — main.js와 trading.js 중복 주의

### 7.9 Groq AI 호출
- **`_groq_chat()` 공용 헬퍼 사용** — model fallback 체인 + 재시도 + 한자 자동 변환 자동 적용
- 시스템 메시지에 "순수 한글로만, 한자(漢字) 절대 금지" 명시 필수
- 새로운 AI 프롬프트 추가 시 같은 패턴 따를 것

### 7.10 전일 종가 계산
- ⚠️ **`fast_info.previous_close` 신뢰 금지** — 가끔 이틀 전 종가 반환하는 버그
- 항상 `df.index[-1]`의 시장 타임존 기준 날짜로 `is_last_bar_today` 판별 후
  - 오늘 인트라데이 봉: `iloc[-2]`가 prev_close
  - 어제 종가 봉: `iloc[-1]`이 prev_close

### 7.11 스코어카드 도움말 시스템
- 새 지표 추가 시 `_SC_HELP[key]`에 설명 추가, `_helpIcon(key)`로 ⓘ 자동 생성
- 텍스트는 `\n`으로 줄바꿈 OK (HTML에서 `<br>`로 변환됨)
- 데스크톱 hover + 모바일 탭 둘 다 동작

### 7.12 한국 종목 보충 데이터
- `fetch_kr_supplements(krx_code)` 자동 호출 → `scorecard.metrics.kr_*` 주입
- 30분 캐시. force 무효화는 아직 없음 (필요 시 추가)

### 7.13 자산 스냅샷 (자산 변화 차트)
- `trading_dashboard()` 호출 시마다 KST 기준 오늘 자산 자동 upsert
- 하루 1회 (사용자가 들어와야 기록됨). 매일 자동 cron은 없음
- ⚠️ 종가 기준이 아니라 호출 시점의 평가금액 → 차트는 "참고용" 트렌드
- 정확한 일별 마감 종가가 필요하면 별도 daily cron 필요 (TODO)

### 7.14 배지 시스템
- `badges.py`의 `BADGES` 배열에서 추가/수정
- 각 배지는 `{key, name, icon, tier, category, desc, check(ctx)}` 형식
- ctx 키 추가 시 `_build_badge_context()`도 함께 수정 필수
- 새 배지는 다음 dashboard 호출에서 자동 평가/부여
- 기존 사용자에게 소급 적용됨 (이미 자격 충족 상태면 다음 접속 시 부여)

### 7.15 랭킹 노출 정책
- 닉네임 설정 + `is_public=True` 사용자만 노출 (옵트인)
- 다른 사용자의 `user_id`는 API 응답에서 제거 (프라이버시)
- 본인 행에만 `is_me: true` + `user_id` 포함

### 7.16 토스증권 API
- `toss_api.is_enabled()` 로 가드 → 키 없으면 모든 함수 무동작 (yfinance/네이버 폴백)
- `toss_api.is_eligible(ticker)` 로 지수(^)/환율(=X) 제외 — 벤치마크는 항상 yfinance
- 캔들은 일봉/분봉만 → 주/월봉은 일봉 리샘플. 응답이 `result.candles` 중첩 +
  `nextBefore` 페이지네이션. 일봉 날짜는 시장 오프셋 보존(한국 날짜 밀림 방지)
- 펀더멘털(PER/시총 등)은 토스 미제공 → `stock.info`는 yfinance 유지
- **IP 허용목록 필수** (live 키) — 콘솔에 IP 등록 안 하면 401(unidentified-client)
- IPv6로 나가면 등록 IP와 달라 401 → `HAS_IPV6=False`로 IPv4 강제 (적용됨)
- 새 가격/시세 소스 추가 시 `_get_price_df()` / 실시간 우선순위 체인에 통합

### 7.17 토스 계좌 import (실제 포트폴리오)
- 토스 Open API는 `client_credentials`만 지원 = **개발자 본인 계좌만** 접근 가능.
  사용자별 OAuth(authorization_code) **미지원** → 다른 사람 계좌 연동 불가
- `POST /api/portfolio/import-toss` = 소유자 본인 토스 계좌 → Holding 동기화(교체)
- 소유자 게이팅: `TOSS_OWNER_EMAIL`(콤마) **또는** `TOSS_OWNER_USER_IDS`(콤마).
  카카오 로그인은 이메일 미제공 → **반드시 user_id로 게이팅** (현재 `1,2`)
- 서버 import는 Railway IP가 토스에 등록돼 있어야 동작. 막히면 `scripts/toss_to_sql.py`
  로 로컬에서 SQL 생성 → DB 콘솔 붙여넣기 우회

### 7.18 토스 고정 IP 프록시 (재배포 IP 변동 해결)
- `TOSS_PROXY_URL` 설정 시 모든 토스 호출이 프록시 경유 (`toss_api._req`)
- 프록시 IP를 토스에 1회 등록 → 재배포해도 안 바뀜
- 설정 가이드: `TOSS_PROXY_SETUP.md` (Oracle 무료 VM + tinyproxy)
- 미설정 시 직접 연결 (현재 상태) → 재배포마다 `/api/debug/toss`로 IP 확인 후 재등록

---

## 8. 외부 의존성 / 환경변수

### .env 필수 키
```
GROQ_API_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
KAKAO_CLIENT_ID=...
KAKAO_CLIENT_SECRET=...
DATABASE_URL=...           # Railway가 자동 주입 (로컬은 SQLite로 폴백)
FLASK_SECRET_KEY=...
TOSS_CLIENT_ID=...         # 토스증권 Open API (미설정 시 yfinance/네이버 폴백)
TOSS_CLIENT_SECRET=...     # ⚠️ live 키는 IP 허용목록 등록 필요 (콘솔)
TOSS_OWNER_USER_IDS=1,2    # 토스 계좌 import 권한 (윤현호 kakao=1, google=2)
TOSS_OWNER_EMAIL=          # (대안) 이메일 게이팅, 콤마구분. 카카오는 이메일 없어 id 사용
TOSS_PROXY_URL=            # (선택) 고정 IP 프록시. 설정 시 토스에 프록시 IP 1회 등록
```
> ⚠️ 실제 키는 `.env`(gitignore)에만. `.env.example`엔 플레이스홀더만 (커밋되는 파일)
> Railway Variables에도 동일 키 등록되어 있음 (TOSS_CLIENT_*, TOSS_OWNER_USER_IDS)

### Python 패키지 (requirements.txt 주요)
- Flask 3.x, Flask-Login, Flask-SQLAlchemy, psycopg2-binary
- yfinance, pandas, numpy
- groq, deep-translator
- gunicorn

### 외부 API/소스
- **Groq**: AI 분석 (모델 fallback 체인 있음, 재시도 로직 포함)
- **yfinance**: 가격/펀더멘털 (history, info, quarterly_income_stmt, get_earnings_dates, earnings_history)
- **DART (오픈DART)**: 한국 분기 재무
- **네이버 금융**: mobile API + integration + wisereport 스크래핑
- **Google Translate** (deep_translator): 영문 → 한글, 타임아웃 보호
- **토스증권 Open API**: 시세/캔들/환율 (`toss_api.py`, OAuth2, IP 허용목록 필요)

---

## 9. 로컬 개발 실행

```powershell
# Windows PowerShell
cd C:\Users\yhh10\Documents\stock-analyzer
python app.py
# → http://localhost:5000
```

Service Worker가 옛 버전 캐시할 수 있으므로 새 JS/CSS 테스트는 **Ctrl+Shift+R** (하드 새로고침) 또는 DevTools > Application > Clear storage.

---

## 10. 미해결/주시 중

- [x] ~~Railway "Limited Access"~~ 해결됨 (2026-05-21)
- [x] ~~모의투자 일별 자산 스냅샷 DB 모델~~ 완료 (4e0bf61)
- [ ] **한국 테마 상세 페이지 스크래핑 일부 실패** 가능성 — 네이버가 페이지 구조 바꿀 때마다 `_fetch_kr_theme_constituents` 셀렉터 영향
  - 자동 폴백 로직 있음 (`code= 링크 가장 많은 테이블`) 이지만 0행 반환 시 Railway 로그 `[constituents KR] {no}: parsed 0 rows` 확인
- [ ] **yfinance fast_info 일부 필드 불안정** — `previous_close`는 폐기했지만 `last_price`도 가끔 None 반환
  - 폴백으로 히스토리 마지막 종가 사용 중
- [ ] **한국 종목 Piotroski F-Score / 내부자 데이터 부족** — yfinance 한계, DART API 키 등록하면 보강 가능
- [ ] AI 분석 응답 길이/품질 들쭉날쭉 (fallback 모델 차이)
- [ ] 트렌드 스캐너 풀스캔 시 응답 시간 (gthread 8개라 여유 있지만 모니터 필요)
- [ ] **자산 스냅샷이 종가 기준이 아님** — dashboard 호출 시점의 평가금액
  - 정확한 일별 종가 기록 위해 매일 16:00 KST + 17:00 EST cron 도입 검토
- [ ] **랭킹 캐싱 없음** — 사용자 늘면 `/api/leaderboard` 가 매 호출마다 모든 사용자 스냅샷 조회
  - 사용자 100명 초과 시 5분 캐시 도입
- [ ] **🔑 토스 IP 재등록 (재배포마다)** — Railway egress IP가 배포마다 바뀜.
  재배포 후 토스가 yfinance로 폴백되면 `/api/debug/toss`로 IP 확인 → 콘솔 등록.
  영구 해결은 `TOSS_PROXY_URL`(Oracle VM, `TOSS_PROXY_SETUP.md`) — VM 세팅 보류 중
- [ ] **`/api/debug/toss` 진단 엔드포인트** — 운영 안정화 후 제거 고려 (현재는 IP 확인용 필요)
- [ ] **토스 다른 계좌(연금/ISA) 미노출** — Open API가 개발자 키 연동 1계좌만 제공 (API 한계)

---

## 11. Git 상태 스냅샷 (2026-06-05)

```
main 브랜치 (origin/main과 동기화됨, 모두 정상 배포)

232b5d2 feat: toss_to_sql 스크립트 UTF-8 파일 출력 + .sql gitignore        ← HEAD
ba8705c feat: 토스 보유종목 → holdings INSERT SQL 생성 스크립트 (IP 우회용)
b774480 feat: 토스 import 다계좌 합산 + user_id 게이팅(카카오 이메일 미제공)
7864c55 fix: 토스 import - 복수 owner 이메일 허용 + IP 미등록 시 명확한 에러
65ddef9 docs: 토스 고정 IP 프록시 설정 가이드 (Oracle Cloud 무료 VM + tinyproxy)
b4008b7 feat: 토스 API 고정 IP 프록시 지원 (TOSS_PROXY_URL)
35e5f1c feat: 토스 계좌 보유종목 → 실제 포트폴리오 import
651080a fix: 랭킹에서 비현실적/손상 데이터 제외
1ed7bb2 feat: 포트폴리오(실제 보유) ↔ 모의투자 테이블 분리
b4ea46f fix: 토스 API 실동작 수정 — IPv4 강제 + 중첩 응답 파싱 + 날짜 보존
5484e02 feat: /api/debug/toss 진단 엔드포인트
968b694 docs: 토스 IP 허용목록 해결 + 운영 동작 확인 기록
1625299 feat: 토스증권 Open API 연동 — 시세/차트/환율 (yfinance 폴백)
c07aab7 docs: HANDOFF.md 최신화 — 2026-05-22 저녁 세션 6건 반영
d69cd95 ux: 모의투자 페이지의 포지션 카드도 수익/손실 색상 개선 적용
994ac68 fix: 옛 Service Worker 캐시 우회 — JS/CSS에 ?v=29 쿼리 추가
ed3cf61 ux: 포트폴리오 카드 수익/손실 한눈에 보이도록 개선
7ae81d7 feat: 챌린지(배지 25종) + 랭킹 시스템 + 닉네임 프로필
fdaa822 fix: 시가총액 None일 때 format 오류로 분석 실패하던 문제 수정
4e0bf61 feat: 자산 변화 차트 + KOSPI/S&P500 벤치마크 비교
5a9938f docs: HANDOFF.md 최신화 — 2026-05-22 세션 작업 14건 반영
3cf54ea fix: 전일 종가 계산 오류 (fast_info.previous_close 신뢰 안함)
5535259 feat: Groq AI 프롬프트 강화 + 한자 자동 변환/제거
7b61525 feat: 스코어카드 ⓘ 모바일에서도 탭하면 인라인 도움말 펼침
a49abc9 ux: 섹터 강도 새로고침 버튼 모바일에서 아이콘만 (원형 36x36)
6c126a0 ux: 섹터 강도 상위 5개만 기본 표시 (20 → 5)
4a9a837 feat: 한국 종목 보충 데이터 (네이버 공매도 + 외국인/기관 + DART)
3efd4e3 feat: 스코어카드 지표 설명 툴팁 + 한국 종목 데이터 한계 안내
c37ce96 feat: 월가 스타일 펀더멘탈 스코어카드 (Q/V/G/M 4팩터 + 10개 지표)
a687b9f fix: 미국 종목명 표시 + 한국 테마 스크래퍼 견고화 + 모의투자 새로고침
5193c4c feat: 섹터/테마 카드 펼치면 대표 종목 상승률 순으로 표시
b52dfcb feat: 섹터/테마 강도 랭킹 탭 추가 (모멘텀+거래량/breadth 점수)
9d2a845 feat: 한국 주식 실시간 시세를 네이버 폴링 API로 (~1분 지연)
5c6ddf8 feat: 장중 실시간 가격 + LIVE 뱃지 / 장 마감 시각 표시
8ad2d6a ux: 숫자 스테퍼 버튼을 [-] input [+] 가로 레이아웃으로 개선
685c7b9 chore: deploy latest - 분석페이지 모의투자 버튼 포함
... (이전 작업)
68d0575 feat: 분석↔모의투자 통합 - 분석 페이지에서 바로 모의 매수/매도
```

**Service Worker**: `sa-v34`
**HTML 캐시 버스팅 쿼리**: `?v=34`

**2026-06-05 토스 세션 신규/수정 파일**:
- `toss_api.py` — 시세/캔들/환율/계좌/보유종목 + OAuth2 + 프록시(`_req`) + 다계좌 합산
- `scripts/toss_to_sql.py` (NEW) — 토스 보유종목 → SQL 생성 (IP 우회)
- `TOSS_PROXY_SETUP.md` (NEW) — 고정 IP 프록시 가이드
- `models.py` — `PaperHolding` 모델 추가
- `app.py` — `_get_price_df`, 토스 시세/환율 통합, paper_holdings 마이그레이션,
  trading 라우트 PaperHolding 전환, `/api/portfolio/import-toss`, `/api/debug/toss`,
  랭킹 방어 가드, `_toss_symbol_to_ticker`
- `templates/index.html` — 포트폴리오 탭 "실제 보유" 안내 + 토스 불러오기 버튼
- `static/js/main.js` — `importTossPortfolio()`, 토스 실시간 라벨
- `.env.example` — TOSS_* 키들

**2026-05-22 저녁 세션 신규/수정 파일**:
- `models.py` — `AssetSnapshot`, `UserBadge` 모델 추가 + User.nickname/is_public
- `badges.py` (NEW) — 25개 배지 정의 + evaluate 엔진
- `app.py` — 마이그레이션 SQL, 스냅샷 헬퍼, 벤치마크 fetcher, 배지 ctx 빌더/부여 엔진,
  `/api/trading/history`, `/api/me/badges`, `/api/me/nickname`, `/api/leaderboard`,
  `/leaderboard` 라우트, 시가총액 None 안전 포맷
- `templates/leaderboard.html` (NEW)
- `templates/index.html`, `templates/trading.html` — 랭킹 nav 링크, ?v= 캐시 버스팅
- `templates/trading.html` — 자산 차트 카드 + LightweightCharts CDN + 닉네임 모달 +
  배지 섹션 + 새 배지 토스트
- `static/js/main.js` — 포트폴리오 카드 색상 로직 개편 (방향 우선)
- `static/js/trading.js` — 자산 차트, 배지 로딩/렌더링, 닉네임 모달, 포지션 카드 색상
- `static/js/leaderboard.js` (NEW)
- `static/css/style.css` — 자산 차트, 랭킹, 배지 그리드/토스트, 카드 방향 색상
