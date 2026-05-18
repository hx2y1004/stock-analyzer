<div align="center">

# 📈 StockAnalyzer

**한국·미국 주식 통합 분석 플랫폼**
*월가 스타일 트레이딩 시그널 · 추세 상승 종목 자동 감지 · 모바일 PWA 지원*

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![yfinance](https://img.shields.io/badge/yfinance-1.3-7B61FF)](https://github.com/ranaroussi/yfinance)
[![LightweightCharts](https://img.shields.io/badge/Lightweight%20Charts-v4-26A69A)](https://tradingview.github.io/lightweight-charts/)
[![Render](https://img.shields.io/badge/Deploy-Render-46E3B7?logo=render&logoColor=white)](https://render.com)
[![PWA](https://img.shields.io/badge/PWA-Ready-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)

</div>

---

## 🎯 한눈에 보기

StockAnalyzer는 **한국·미국 주식**을 동일한 인터페이스에서 분석할 수 있는 웹 애플리케이션입니다.
단순 차트 뷰어가 아닌, **월가 정통 트레이딩 로직(Risk:Reward 1:3 보장, 추세 강도 기반 진입가)** 과
**자동 추세 종목 스캐너**를 갖춘 실전형 분석 도구입니다.

### ✨ 핵심 기능

| 기능 | 설명 |
|---|---|
| 🔍 **개별 종목 분석** | 캔들·볼린저·일목·MA·RSI·MACD + 펀더멘털 + AI 인사이트 |
| 🎯 **Plan D 트레이드 추천** | 추세 점수(0~100) → 진입가·목표가·손절가 + R:R 1:3 보장 |
| 📈 **추세 상승 감지** | 887 종목 자동 스캔 · 기술(50)+모멘텀(30)+펀더(20) 점수 |
| 💼 **모의 포트폴리오** | 매수·매도·수익률 추적 + 추가매수 추천 |
| 🌍 **시장 컨텍스트** | S&P/Nasdaq/VIX 또는 KOSPI/KOSDAQ/환율 실시간 |
| 📅 **이벤트 캘린더** | 실적 발표·배당락일 D-N 표시 |
| 📱 **PWA 지원** | 모바일 홈 화면 추가 시 네이티브 앱처럼 동작 |

---

## 🛠 기술 스택

### Backend
```
Flask 3.1                  웹 프레임워크
Flask-Login                인증
Flask-SQLAlchemy + PostgreSQL    DB
Gunicorn (gthread)         WSGI 서버 (1 worker × 8 threads)
yfinance 1.3               주가 데이터
pandas / numpy             데이터 처리
BeautifulSoup4 + requests  네이버 금융 크롤링
Groq AI                    종목 인사이트 생성 (기업분석/주가변동 이유)
```

### Frontend
```
Vanilla JavaScript (ES2020+)
Lightweight Charts v4      차트 라이브러리
Service Worker             PWA, 캐시 정책
CSS Grid + Flexbox         반응형 레이아웃
```

### 인프라
```
Render          호스팅 (자동 배포)
GitHub          소스 + CI
PostgreSQL      Render Managed DB
```

---

## 📊 핵심 기능 상세

### 1️⃣ Plan D 트레이드 추천 (월가 정통 로직)

**추세 점수(0~100)** 를 계산하고 강도별로 다른 진입 전략 제시:

```
+30점  MA20 > MA50 > MA200 (정배열)
+20점  현재가 > MA20
+20점  RSI 50~70 (강세 모멘텀)
+10점  52주 신고가 5% 이내
+20점  거래량 평균 ×1.5 이상
```

| 등급 | 점수 | 진입 추천가 | 손절 | 목표 | R:R |
|---|---|---|---|---|---|
| 🔥 **Strong** | ≥60 | 신고가 돌파→즉시 / MA20 근처→즉시 / 일반→`close-0.5ATR` (MA20 floor) | `max(swing low, MA20) − 0.3ATR` | 진입 + **3×리스크** | **1:3** |
| ⚖️ **Neutral** | 30~59 | `max(MA50, close-1.5ATR)` | 진입 − 2ATR | 진입 + 4ATR | **1:2** |
| ⚠️ **Avoid** | <30 | 비추천 (null) | 보유자 참고용 | 비추천 | — |

**안전장치**:
- `entry ≥ stop + 1×ATR` (논리 모순 차단)
- R:R 1.5 미만 → 자동 avoid
- 진입 신뢰도: 🟢 immediate / 🟡 wait / 🟠 patient

### 2️⃣ 추세 상승 감지 (Trend Scanner)

887개 종목(KR 350 + US 537)을 백그라운드에서 스캔하고 점수화:

```python
총점 = 기술(50) + 모멘텀(30) + 펀더멘털(20)
```

| 카테고리 | 항목 | 점수 |
|---|---|---|
| **기술 (50)** | 주봉 BB 수축 | 10 |
| | BB 상단 돌파 (당주/전주) | 15 / 10 |
| | 거래량 평균 +50%↑ | 10 |
| | 최근 3주 장대양봉 2개↑ | 10 |
| | MA20 상승 정렬 | 5 |
| **모멘텀 (30)** | 14일 수익률 +5%↑ | 15 |
| | 52주 신고가 (1% 이내) | 10 |
| | 연속 신고가 | 5 |
| **펀더 (20)** | 매출 성장 +10%↑ | 5 |
| | EPS 성장 +10%↑ | 5 |
| | 컨센서스 상향 (Forward > Trailing×1.05) | 10 |

**최적화**:
- 2-pass 스캐닝 (pass1: 기술+모멘텀 전체, pass2: 상위 50개만 펀더 보강)
- 30분 메모리 캐시
- ALL 캐시에서 KR/US 자동 필터 도출 (재스캔 불필요)
- 백그라운드 스레드 + 1초 폴링 진행률 라이브 표시

### 3️⃣ 분기 실적 차트 (Earnings)

- **매출 / EPS (미국) / 영업이익 (한국)** 3가지 분기 데이터
- **추정치 vs 발표치**: 노란 점선 박스 오버레이 (동일 폭 65% 완전 겹침)
- 미국: `quarterly_income_stmt` + `earnings_history`
- 한국: 네이버 금융 분기 컨센서스 API (`isConsensus` 필드 활용)

### 4️⃣ PWA (Progressive Web App)

- `display: standalone` → 홈 화면 아이콘 누르면 자체 윈도우 실행
- Service Worker v12 (sa-v12)
  - JS/CSS: **network-first** (배포 즉시 반영)
  - 정적 자산: cache-first
  - `/api/trends/status`: **캐시 차단** (실시간 폴링)
- 자동 1분 update 체크 (사용자 인터럽트 없이 백그라운드 갱신)
- iOS Safari + Android Chrome 모두 지원

---

## 🚀 Quick Start

### 1. 클론 & 환경 준비

```bash
git clone https://github.com/hx2y1004/stock-analyzer.git
cd stock-analyzer

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 2. 환경변수 설정

`.env.example` 을 `.env` 로 복사하고 값 채우기:

```env
DATABASE_URL=postgresql://user:pass@host:5432/dbname
GROQ_API_KEY=gsk_...              # AI 기업분석/주가변동 이유 생성용
FLASK_SECRET_KEY=your_secret
```

### 3. 종목 DB 빌드 (첫 실행 시)

```bash
python build_stock_db.py
# → stock_db.json 생성 (KR 350 + US 537 종목)
```

### 4. 로컬 실행

```bash
python app.py
# http://localhost:5000
```

### 5. 프로덕션 배포 (Render)

```bash
git push origin main
# Render 자동 배포 (Procfile 사용)
```

`Procfile`:
```
web: gunicorn app:app --workers 1 --threads 8 --worker-class gthread --timeout 120
```

> 💡 **Render 무료 티어**에서 gthread 워커 1개로 동시 요청 + 백그라운드 스캔 동작합니다.

---

## 📁 프로젝트 구조

```
stock-analyzer/
├── app.py                      # Flask 메인 (라우트 + 분석 엔드포인트)
├── trends_scanner.py           # 추세 상승 감지 (백그라운드 스캔)
├── auth.py                     # 로그인/회원가입 블루프린트
├── models.py                   # SQLAlchemy 모델 (User, Holding)
├── build_stock_db.py           # KR/US 종목 DB 빌드 스크립트
├── stock_db.json               # 자동완성용 종목 DB (887개)
├── Procfile                    # Gunicorn 설정
│
├── analysis/
│   ├── ai_analysis.py          # Plan D 트레이드 추천 + 점수 산출
│   └── indicators.py           # MA / BB / 일목 / RSI / MACD 계산
│
├── templates/
│   ├── index.html              # 메인 분석 페이지
│   └── trading.html            # 모의투자 페이지
│
├── static/
│   ├── css/style.css           # 전체 스타일 (다크 테마)
│   ├── js/main.js              # 클라이언트 로직 (차트, 폴링, 렌더)
│   ├── manifest.json           # PWA 매니페스트
│   ├── service-worker.js       # SW (캐시 정책)
│   └── icons/                  # PWA 아이콘 (180/192/512)
│
└── scripts/
    └── generate_icons.py       # PIL 기반 PWA 아이콘 생성
```

---

## 🎨 화면 구성

```
┌──────────────────────────────────────────────────────────────┐
│ StockAnalyzer            [분석] [모의투자]    [프로필] [로그아웃]│
├──────────────────────────────────────────────────────────────┤
│ ┌────────────┐ ┌────────────────────────────────────────┐    │
│ │추세상승감지│ │     내 포트폴리오                       │    │
│ └────────────┘ └────────────────────────────────────────┘    │
│ [전체] [한국] [미국]                          [🔍 스캔]       │
│ ┌──────────────────────────────────────────────────────┐    │
│ │ 🥇 #1  AAPL          ▁▂▃▅▇█  $259.00  [80/100]      │    │
│ │ 🥈 #2  MSFT          ▁▂▄▅▇█  $445.20  [78/100]      │    │
│ │ 🥉 #3  NVDA          ▁▂▃▆█  $1245.50  [75/100]      │    │
│ │ #4    GOOGL          ▁▂▃▅▆  $185.30   [70/100]      │    │
│ └──────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────┤
│ ┌──────────────────────┐ ┌──────────────────────┐            │
│ │ 기술적 분석 차트     │ │ 종합 분석 판단        │            │
│ │ [캔들][라인][1d][1w] │ │  🔥 강한 매수 신호    │            │
│ │ [📈MA][📊BB][☁️일목] │ │  진입 추천가 / 목표 / │            │
│ │  ━━━ 차트 ━━━        │ │  손절가 / R:R 1:3.00  │            │
│ │  ━━━ RSI ━━━         │ ├──────────────────────┤            │
│ │  ━━━ MACD ━━━        │ │ 주요 지표             │            │
│ │ [모의매수][매도][삭제]│ ├──────────────────────┤            │
│ │ 📊 내 포지션 (수익률)│ │ 🌍 시장 컨텍스트       │            │
│ │   진행률 바 (매입~목표)│ │  S&P / Nasdaq / VIX  │            │
│ └──────────────────────┘ ├──────────────────────┤            │
│                          │ 📅 다가오는 이벤트     │            │
│                          │  📊 실적 D-12 / 💰D-28│            │
│                          └──────────────────────┘            │
├──────────────────────────────────────────────────────────────┤
│ 상세 분석: [기술적] [투자 판단] [매수/매도 구간]              │
├──────────────────────────────────────────────────────────────┤
│ 애널리스트 의견 | 뉴스                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 🌐 API 엔드포인트

### 분석
| Method | Path | 설명 |
|---|---|---|
| `GET` | `/analyze?ticker=AAPL` | 종목 종합 분석 (차트 + 지표 + 트레이드 추천) |
| `GET` | `/api/chart?ticker=AAPL&interval=1d` | 캔들 데이터만 |
| `GET` | `/api/search?q=apple` | 종목 자동완성 (로컬 DB + yfinance) |

### 추세 스캔
| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/trends/scan?market=ALL\|KR\|US` | 백그라운드 스캔 시작 |
| `GET` | `/api/trends/status?market=X` | 진행 상태/결과 폴링 (캐시 차단 헤더) |
| `POST` | `/api/trends/abort?market=X` | 스캔 중단 |

### 포트폴리오 (로그인 필요)
| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/portfolio` | 보유 종목 + 실시간 수익률 |
| `POST` | `/api/portfolio` | 종목 추가 |
| `PUT` | `/api/portfolio/<id>` | 수량 업데이트 |
| `DELETE` | `/api/portfolio/<id>` | 종목 삭제 |

### PWA
| Method | Path | 설명 |
|---|---|---|
| `GET` | `/service-worker.js` | 서비스 워커 (Service-Worker-Allowed: /) |
| `GET` | `/manifest.json` | PWA 매니페스트 |

---

## 🔧 주요 디자인 결정

### Why Plan D (Hybrid)?
박스권 30%/70% 기반 단순 진입가는 추세를 무시함. 실제 트레이더는 **추세 강도에 따라 다른 전략**을 씁니다.
- 강세장에서 깊은 풀백 대기 = 추세 꺾이는 신호
- MA20이 자연스러운 풀백 지점
- R:R 1:3 보장이 일관된 수익 유지의 핵심

### Why setTimeout chain over setInterval?
- setInterval은 브라우저가 백그라운드/CPU 부하 시 throttle
- setTimeout 체이닝: 직전 폴링 완료 후 다음 시작 → race condition 없음
- 명시적 stop flag로 종료 제어 명확

### Why gthread Worker?
- Sync 워커 1개 + 백그라운드 스레드 = GIL 점유로 status 응답 막힘
- gthread 1×8: 스캔 스레드 + 폴링 응답 스레드 분리
- Render 무료 티어 메모리(512MB) 안에서 동작

### Why NumpyEncoder + Safe JSON Provider?
- Python NaN/Inf → JS `JSON.parse` 실패
- Flask 기본 jsonify는 NaN을 비표준 `NaN` 으로 출력
- Flask 3 JSON Provider에 NumpyEncoder 등록 → 전역 sanitize

---

## 📜 라이선스

MIT License — 자유롭게 사용·수정 가능합니다.

---

## 🙋 컨택트

이슈/제안: [GitHub Issues](https://github.com/hx2y1004/stock-analyzer/issues)

<div align="center">

**Made with ❤️ for retail traders**

*Disclaimer: 본 도구는 교육·연구 목적이며, 투자 손실에 대한 책임은 사용자에게 있습니다.*

</div>
