# StockAnalyzer 작업 인계 문서

> **다음 세션에서 가장 먼저 이 파일을 읽고 작업 시작할 것**
> 마지막 업데이트: 2026-05-22 KST

---

## 🚀 현재 상태 (2026-05-21)

- **Railway 정상 동작** (HOBBY 플랜, 배포 정상)
- 최신 커밋: `3cf54ea` (전일 종가 계산 버그 수정)
- Service Worker: **`sa-v26`**
- **이번 세션에서 14개의 큰 기능/수정 추가** — §5에 모두 기록

### 다음 우선 작업 후보 (사용자 결정 대기)
- [ ] **자산 변화 차트** — 모의투자 일별 총자산 스냅샷 시계열 (DB 모델 추가 필요)
- [ ] **벤치마크 비교** — KOSPI/S&P500 대비 모의투자 수익률
- [ ] **챌린지 시스템 / 사용자 랭킹**
- [ ] **DART API 키 등록** — 한국 임원 주식 변동 보고 자동 fetch (API 키만 필요)
- [ ] **자동 손절 / 백테스트 / AI 코치**

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
│  ├─ ai_analysis.py         # Groq AI 분석 + 번역 + 캐시 + 스코어카드 연결
│  ├─ advanced.py            # 월가 4팩터 스코어카드 (Q/V/G/M + 10개 지표)
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
│  └─ service-worker.js      # SW (현재 sa-v26)
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

### 시장 데이터 / 섹터 (신규 — 2026-05-21)
- `GET /api/sectors/strength?market=US|KR&force=1` — 섹터/테마 강도 랭킹
- `GET /api/sectors/constituents?market=US&ticker=XLK` — US 섹터 ETF 대표 종목 (15개, 등락률순)
- `GET /api/sectors/constituents?market=KR&theme_no=123` — KR 테마 구성 종목 (20개)

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

---

## 5. 최신 작업 로그 (최신 → 과거 순)

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

### ✅ 최근 완료 (2026-05-22 세션)
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

### 📋 모의투자 Step 2 (계획됨)
- [ ] **자산 변화 차트** — 일별 총자산 스냅샷 시계열 (새 DB 모델 필요)
- [ ] **벤치마크 비교** — KOSPI/S&P500 대비 수익률

### 📋 Step 3 (게이미피케이션)
- [ ] 챌린지 시스템
- [ ] 사용자 랭킹 (수익률 기반)

### 📋 Step 4 (고급)
- [ ] 자동 손절
- [ ] 백테스트
- [ ] AI 코치 (매매 패턴 피드백)

### 📋 데이터 보강 (옵션)
- [ ] **DART API 키 등록** — 한국 임원 주식 변동 자동 fetch
  - 등록: https://opendart.fss.or.kr/ (무료)
  - 환경변수 `DART_API_KEY` 추가하면 됨
- [ ] **KRX 공식 공매도 API** — 더 정확한 실시간 공매도

---

## 7. ⚠️ 반드시 기억할 제약/관례

### 7.1 Service Worker 캐싱
- **JS/CSS 변경 시 `static/service-worker.js`의 `CACHE_VERSION` 올려야** 사용자에게 반영됨
- 현재: `sa-v26`
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

- [x] ~~Railway "Limited Access"~~ 해결됨 (2026-05-21)
- [ ] **한국 테마 상세 페이지 스크래핑 일부 실패** 가능성 — 네이버가 페이지 구조 바꿀 때마다 `_fetch_kr_theme_constituents` 셀렉터 영향
  - 자동 폴백 로직 있음 (`code= 링크 가장 많은 테이블`) 이지만 0행 반환 시 Railway 로그 `[constituents KR] {no}: parsed 0 rows` 확인
- [ ] **yfinance fast_info 일부 필드 불안정** — `previous_close`는 폐기했지만 `last_price`도 가끔 None 반환
  - 폴백으로 히스토리 마지막 종가 사용 중
- [ ] **한국 종목 Piotroski F-Score / 내부자 데이터 부족** — yfinance 한계, DART API 키 등록하면 보강 가능
- [ ] AI 분석 응답 길이/품질 들쭉날쭉 (fallback 모델 차이)
- [ ] 트렌드 스캐너 풀스캔 시 응답 시간 (gthread 8개라 여유 있지만 모니터 필요)
- [ ] 모의투자 일별 자산 스냅샷 DB 모델 미구현 (자산 변화 차트 위해 필요)

---

## 11. Git 상태 스냅샷 (2026-05-22)

```
main 브랜치 (origin/main과 동기화됨, 모두 정상 배포)

3cf54ea fix: 전일 종가 계산 오류 (fast_info.previous_close 신뢰 안함)         ← HEAD
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

**Service Worker**: `sa-v26`
**최신 변경 핵심 파일**:
- `app.py` (실시간 시세, 섹터 강도, 한국 보충, 프롬프트, prev_close 수정)
- `analysis/advanced.py` (NEW — 월가 스코어카드)
- `analysis/ai_analysis.py` (analyze_signals에 stock 인자 추가, scorecard 키)
- `templates/index.html` (섹터 탭, 스코어카드 패널, 가격 신선도 표시, 스테퍼)
- `templates/trading.html` (스테퍼, 자산 새로고침 버튼)
- `static/js/main.js` (스코어카드, 섹터, 도움말 토글, 가격 표시)
- `static/js/trading.js` (refreshDashboard)
- `static/css/style.css` (모든 신규 UI 스타일)
