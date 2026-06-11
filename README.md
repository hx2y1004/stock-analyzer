<div align="center">

# 📈 StockAnalyzer

**한국·미국 주식 통합 분석 플랫폼**
*월가 스타일 트레이딩 시그널 · 추세 상승 종목 자동 감지 · 모의투자 · AI 코치 · 모바일 PWA*

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?logo=railway&logoColor=white)](https://railway.app)
[![PWA](https://img.shields.io/badge/PWA-Ready-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)

</div>

---

## 🎯 한눈에 보기

StockAnalyzer는 **한국·미국 주식**을 동일한 인터페이스에서 분석·매매할 수 있는 웹 애플리케이션입니다.

### ✨ 핵심 기능

| 기능 | 설명 |
|---|---|
| 🔍 **개별 종목 분석** | 캔들·볼린저·일목·MA·RSI·MACD + 펀더멘털 + AI 인사이트 |
| 🎯 **Plan D 트레이드 추천** | 추세 점수(0~100) → 진입가·목표가·손절가 + R:R 1:3 보장 |
| 📈 **추세 상승 감지** | 887 종목 자동 스캔 · 기술(50)+모멘텀(30)+펀더(20) 점수 |
| 📱 **모의투자** | 매수·매도·수익률 추적 + FIFO 손익 + 포지션 노트 |
| 🏦 **실제 포트폴리오** | 토스증권 계좌 연동 → 실시간 보유 종목 가져오기 |
| 🤖 **AI 매매 코치** | FIFO 통계 기반 맞춤형 매매 습관 피드백 (Gemini) |
| 📊 **AI 포트폴리오 점검** | 비중·집중도·통화 분산 분석 + 리밸런싱 의견 (Gemini) |
| 🏢 **AI 기업 분석** | 기업소개·주요사업·투자 포인트 한국어 요약 (Gemini) |
| 🌍 **시장 컨텍스트** | S&P/Nasdaq/VIX 또는 KOSPI/KOSDAQ/환율 실시간 |
| 📅 **이벤트 캘린더** | 실적 발표·배당락일 D-N 표시 |

---

## 🛠 기술 스택

### Backend
```
Flask 3.1                       웹 프레임워크
Flask-Login                     인증
Flask-SQLAlchemy + PostgreSQL   DB (Railway Managed)
Gunicorn (gthread)              WSGI 서버 (1 worker × 8 threads)
yfinance 1.3                    주가 데이터
pandas / numpy                  데이터 처리
BeautifulSoup4 + requests       네이버 금융 크롤링
Gemini API (gemini-2.5-flash)   AI 기업분석 · 코치 · 포트폴리오 점검
Groq API (Llama)                AI 폴백 (Gemini 한도 초과 시 자동 전환)
```

### Frontend
```
Vanilla JavaScript (ES2020+)
Lightweight Charts v4           차트 라이브러리
Service Worker (v38)            PWA, 캐시 정책 (network-first JS/CSS)
CSS Grid + Flexbox              반응형 레이아웃 (다크 테마)
```

### 인프라
```
Railway         호스팅 (GitHub 연동 자동 배포)
PostgreSQL      Railway Managed DB
GitHub          소스 저장소
Oracle VM       토스 프록시 서버 (고정 IP → 토스 API 화이트리스트)
```

---

## 📊 주요 기능 상세

### 1️⃣ Plan D 트레이드 추천

**추세 점수(0~100)** 기반 강도별 진입 전략:

```
+30점  MA20 > MA50 > MA200 (정배열)
+20점  현재가 > MA20
+20점  RSI 50~70 (강세 모멘텀)
+10점  52주 신고가 5% 이내
+20점  거래량 평균 ×1.5 이상
```

| 등급 | 점수 | R:R |
|---|---|---|
| 🔥 Strong | ≥60 | 1:3 |
| ⚖️ Neutral | 30~59 | 1:2 |
| ⚠️ Avoid | <30 | 비추천 |

### 2️⃣ 추세 상승 감지 (Trend Scanner)

887개 종목(KR 350 + US 537) 자동 스캔:
- 2-pass 구조: 전체 기술+모멘텀 → 상위 50개만 펀더 보강
- 30분 메모리 캐시, 백그라운드 스레드 + 1초 폴링 진행률 표시

### 3️⃣ 모의투자 시스템

- `PaperHolding` 모델로 가상 매수/매도 기록
- **FIFO 손익 계산**: 매도 시 선입선출 방식으로 평균단가·실현 PnL 자동 계산
- **포지션 노트**: 매수/매도 시 메모 입력 → 보유카드·거래내역에 표시
- **📊 분석 바로가기**: 보유 종목 카드에서 클릭 시 메인 분석 페이지로 이동

### 4️⃣ 실제 포트폴리오 (토스증권 연동)

- `RealHolding` 모델로 토스 보유 종목 저장
- 토스 API → Railway 프록시 → 앱 서버 경로 (고정 IP 우회)
- **오너 게이팅**: `TOSS_OWNER_EMAIL` / `TOSS_OWNER_USER_IDS` 환경변수로 특정 계정만 접근 허용
- 한국 종목 심볼 자동 변환 (토스 `A005930` → yfinance `005930.KS`)

### 5️⃣ AI 기능 (Gemini 우선 / Groq 폴백)

모든 AI 기능은 `_ai_chat()` 통합 인터페이스를 통해 동작:

```
GEMINI_API_KEY 있음 → gemini-2.5-flash 시도
                     → 실패 시 gemini-2.5-flash-lite 시도
                     → 실패(429 등) 시 Groq 폴백
GEMINI_API_KEY 없음 → Groq 직접 사용
```

**AI 매매 코치**
- FIFO 통계 블록(승률·평균 보유일·최고/최악 매매·종목별 PnL)을 Python에서 선계산
- 계산된 통계를 프롬프트에 주입 → "모니터링 필요" 같은 모호한 답변 방지
- 비한국어 문자 자동 제거 (`_COACH_FOREIGN_RE`)
- 10분 쿨다운 + 서버 사이드 캐시

**AI 포트폴리오 점검**
- 모의/실제 포트폴리오 모드 전환
- 종목별 비중·집중도·통화(KRW/USD) 분산 분석
- 비중 확대/축소 추천 포함

### 6️⃣ PWA

- `display: standalone` → 홈 화면 아이콘에서 네이티브 앱처럼 실행
- Service Worker v38: JS/CSS network-first, 정적 자산 cache-first
- iOS Safari + Android Chrome 지원

---

## 🔐 보안

- `.env` 파일은 gitignore (자격증명 로컬 전용)
- `FLASK_SECRET_KEY` 미설정 시 운영 환경 기동 차단 (fail-fast)
- 토스 API 디버그 엔드포인트 오너 게이팅 (비오너 → 404)
- `holdings_import_*.sql` gitignore (개인 보유종목 보호)

---

## 🚀 Quick Start

### 1. 클론 & 환경 준비

```bash
git clone https://github.com/hx2y1004/stock-analyzer.git
cd stock-analyzer

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 2. 환경변수 설정

`.env.example`을 `.env`로 복사하고 값 채우기:

```env
DATABASE_URL=postgresql://user:pass@host:5432/dbname
FLASK_SECRET_KEY=your-secret-key

# AI (둘 중 하나만 있어도 동작, 둘 다 있으면 Gemini 우선)
GEMINI_API_KEY=AIza...
GROQ_API_KEY=gsk_...

# 토스 연동 (선택)
TOSS_PROXY_URL=http://your-oracle-vm:port
TOSS_OWNER_EMAIL=your@email.com
TOSS_OWNER_USER_IDS=123,456
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

### 5. Railway 배포

```bash
git push origin main
# Railway GitHub 연동으로 자동 배포
```

`Procfile`:
```
web: gunicorn app:app --workers 1 --threads 8 --worker-class gthread --timeout 120
```

---

## 📁 프로젝트 구조

```
stock-analyzer/
├── app.py                      # Flask 메인 (라우트 + 분석 + AI 엔드포인트)
├── models.py                   # SQLAlchemy 모델
│                               #   User, Transaction, PaperHolding, RealHolding
├── toss_api.py                 # 토스증권 API 연동 (프록시 경유)
├── trends_scanner.py           # 추세 상승 감지 (백그라운드 스캔)
├── auth.py                     # 로그인/회원가입 블루프린트
├── build_stock_db.py           # KR/US 종목 DB 빌드 스크립트
├── stock_db.json               # 자동완성용 종목 DB (887개)
├── Procfile                    # Gunicorn 설정
├── requirements.txt
│
├── analysis/
│   ├── ai_analysis.py          # Plan D 트레이드 추천 + 점수 산출
│   └── indicators.py           # MA / BB / 일목 / RSI / MACD 계산
│
├── templates/
│   ├── index.html              # 메인 분석 페이지
│   └── trading.html            # 모의투자 + 실제 포트폴리오 페이지
│
├── static/
│   ├── css/style.css           # 전체 스타일 (다크 테마)
│   ├── js/main.js              # 분석 페이지 클라이언트 로직
│   ├── js/trading.js           # 모의투자 페이지 클라이언트 로직
│   ├── manifest.json           # PWA 매니페스트
│   ├── service-worker.js       # SW (캐시 정책 v38)
│   └── icons/                  # PWA 아이콘 (180/192/512)
│
└── TOSS_PROXY_SETUP.md         # 토스 프록시 서버 구축 가이드
```

---

## 🌐 주요 API 엔드포인트

### 분석
| Method | Path | 설명 |
|---|---|---|
| `GET` | `/analyze?ticker=AAPL` | 종목 종합 분석 |
| `GET` | `/api/chart?ticker=AAPL&interval=1d` | 캔들 데이터 |
| `GET` | `/api/search?q=apple` | 종목 자동완성 |

### 추세 스캔
| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/trends/scan?market=ALL\|KR\|US` | 스캔 시작 |
| `GET` | `/api/trends/status?market=X` | 진행 상태/결과 폴링 |
| `POST` | `/api/trends/abort?market=X` | 스캔 중단 |

### 모의투자 (로그인 필요)
| Method | Path | 설명 |
|---|---|---|
| `GET` | `/trading` | 모의투자 페이지 |
| `GET` | `/api/trading/dashboard` | 보유 종목 + 거래 내역 |
| `POST` | `/api/trading/buy` | 모의 매수 (note 포함) |
| `POST` | `/api/trading/sell` | 모의 매도 (note 포함) |

### 실제 포트폴리오 (오너 전용)
| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/import-toss` | 토스 보유종목 가져오기 |
| `GET` | `/api/real-portfolio` | 실제 보유 종목 조회 |

### AI 기능 (로그인 필요)
| Method | Path | 설명 |
|---|---|---|
| `GET/POST` | `/api/trading/coach` | AI 매매 코치 (10분 캐시) |
| `GET/POST` | `/api/portfolio/review` | AI 포트폴리오 점검 (10분 캐시) |

---

## 🗺 향후 계획

| 기능 | 설명 |
|---|---|
| 📰 DART 임원 매매 | DART API 연동으로 임원 매수/매도 신호 표시 |
| 🏆 랭킹 시즌제 | 월/분기 시즌 리셋 + 시즌 배지 |
| 📉 백테스트 | 전략별 과거 수익률 시뮬레이션 |
| 🛑 자동 손절 | 목표가/손절가 도달 시 백그라운드 알림 |

---

## 📜 라이선스

MIT License — 자유롭게 사용·수정 가능합니다.

---

<div align="center">

**Made with ❤️ for retail traders**

*Disclaimer: 본 도구는 교육·연구 목적이며, 투자 손실에 대한 책임은 사용자에게 있습니다.*

</div>
