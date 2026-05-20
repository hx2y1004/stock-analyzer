# StockAnalyzer 작업 인계 문서

> **다음 세션에서 가장 먼저 이 파일을 읽고 작업 시작할 것**
> 마지막 업데이트: 2026-05-20

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
├─ models.py                 # SQLAlchemy 모델 (User, Holding, Transaction)
├─ trends_scanner.py         # 트렌드 스캐너 v2 (RS/OBV/Base/Perfect Setup)
├─ build_stock_db.py         # 종목 DB 빌더 → stock_db.json
├─ stock_db.json             # 종목명/티커 매핑 + 메타 (자동완성용)
├─ analysis/
│  ├─ ai_analysis.py         # Groq AI 분석 + 번역 + 캐시
│  └─ indicators.py          # 기술지표 (MA, RSI, OBV, 스테이지 등)
├─ templates/
│  ├─ index.html             # 메인 (분석 + 포트폴리오)
│  └─ trading.html           # 모의투자
├─ static/
│  ├─ js/
│  │  ├─ main.js             # 메인 페이지 클라이언트
│  │  ├─ trading.js          # 모의투자 클라이언트
│  │  └─ chart.js            # LightweightCharts 헬퍼
│  ├─ css/style.css          # 전체 스타일 (다크 테마)
│  ├─ icons/                 # PWA 아이콘
│  ├─ manifest.json          # PWA 매니페스트
│  └─ service-worker.js      # SW (현재 sa-v14)
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
- 관계: `holdings`, `transactions` (cascade delete)
- 유니크: `(provider, provider_id)`

### `Holding` (테이블 `holdings`) — 실제 포트폴리오
- `id`, `user_id`, `ticker`, `name`, `quantity`, `purchase_price`, `currency`('USD'|'KRW'), `created_at`

### `Transaction` (테이블 `transactions`) — 모의투자 거래 내역
- `id`, `user_id`, `ticker`, `name`, `type`('buy'|'sell')
- `price` (native), `quantity`, `currency`, `exchange_rate` (당시 USD/KRW; KRW면 1.0)
- `fee_krw` (수수료 0.1% 환산), `amount_krw` (총액 KRW), `realized_pnl_krw` (매도시), `timestamp`

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

### 헬퍼 (app.py 내부)
- `_get_usd_krw_rate()` — yfinance `KRW=X`, **10분 캐시**
- `_to_krw(amount, currency)` — KRW 환산
- `_fetch_current_price(ticker)` — 시세 조회 (대시보드 평가용)
- 수수료: 거래금액의 **0.1%**

---

## 5. 최신 작업 로그 (최신 → 과거 순)

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

### 🔥 즉시 (Railway 복구 직후)
1. 배포 정상 확인 → `/trading` 접속해 1억원 초기 지급 확인
2. 매수/매도 흐름 end-to-end 테스트 (USD 종목 1개 + KRW 종목 1개)
3. 모바일에서 모달/스테퍼/자동완성 UX 점검

### 📋 모의투자 Step 2 (계획됨)
- [ ] **자산 변화 차트** — 일별 총자산 스냅샷 시계열
- [ ] **벤치마크 비교** — KOSPI/S&P500 대비 수익률
- [x] **분석↔모의투자 통합** — 분석 화면에 "모의 매수" 버튼 ✅ 2026-05-20 완료

### 📋 Step 3 (게이미피케이션)
- [ ] 챌린지 시스템
- [ ] 사용자 랭킹 (수익률 기반)

### 📋 Step 4 (고급)
- [ ] 자동 손절
- [ ] 백테스트
- [ ] AI 코치 (매매 패턴 피드백)

---

## 7. ⚠️ 반드시 기억할 제약/관례

### 7.1 Service Worker 캐싱
- **JS/CSS 변경 시 `static/service-worker.js`의 `CACHE_VERSION` 올려야** 사용자에게 반영됨
- 현재: `sa-v15`
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
```

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

- [ ] Railway 복구 대기 (2026-05-20 현재)
- [ ] 모의투자 실제 사용자 테스트 피드백 미수집
- [ ] AI 분석 응답 길이/품질 들쭉날쭉 (fallback 모델 차이)
- [ ] 트렌드 스캐너 풀스캔 시 응답 시간 (gthread 8개라 여유 있지만 모니터 필요)
