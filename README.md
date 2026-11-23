<div align="center">

# 📈 StockAnalyzer

**한국·미국 주식 통합 분석 웹앱**
*종목 종합 분석 · 시총가중 섹터맵 · 모의투자와 랭킹 · 멀티 AI 봇 가상 매매 · PWA 모바일 푸시*

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![PWA](https://img.shields.io/badge/PWA-Ready-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)

</div>

> ⚠️ 이 서비스의 분석, AI 해설, 매수·매도 구간, AI 봇 신호와 가상 수익률은 참고·알고리즘 검증용 정보이며 투자 권유가 아닙니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| 🔍 **종목 종합 분석** | 캔들·볼린저·일목·이동평균·RSI·MACD, 매수/손절/목표 구간, 4팩터 점수(퀄리티·가치·성장·모멘텀), DCF 적정가, 애널리스트 의견, 다가오는 실적·배당 일정 |
| 🤖 **AI 해설 (통합 호출 + 캐싱)** | 종목당 한 번의 AI 호출로 등락 사유·밸류에이션 코멘트·영문 뉴스 번역을 함께 생성. 결과는 DB(`ai_cache`)와 메모리에 캐시. Gemini(가벼운 모델 우선 자동 선택) → 한도 초과 시 Groq → 선택적 백업 공급자 순으로 폴백 |
| 🗺 **시황 대시보드** | KOSPI·KOSDAQ·환율·S&P 500·나스닥·VIX, 오늘의 급등락 |
| 🧩 **시총가중 섹터맵** | 한국 약 350 / 미국 약 537종목 전체를 섹터별 **시가총액 가중 등락률**로 집계, 트리맵 면적 = 섹터 시총 비중, 섹터별 상승/하락/보합 종목 수, 클릭하면 구성 종목(시총순·상승순·하락순) |
| 📈 **추세 셋업 (발굴 › 추세 셋업)** | 2단 하이브리드: 장 마감 배치가 전체 887종목의 Stage2·BB 수축/돌파·OBV·상대강도·다중 모멘텀·52주 신고가·펀더멘탈 점수와 **돌파 기준가**를 확정해 DB에 저장(화면은 즉시 표시), 장중에는 섹터맵 15분 시세로 전날 BB 수축·52주 고점 -3% 이내 후보가 기준가를 넘으면 **⚡ 장중 돌파**. `🔄 현시점 갱신`은 후보 상위 50개만 2~3초 안에 다시 받음 |
| 🔎 **원클릭 스크리너 · 종목 비교** | 한국·미국 전체 종목을 프리셋 5종(우량주 과매도·추세 돌파+수급·저평가 가치주·당일 모멘텀·AI 봇 최근 신호)과 세부 필터로 즉시 걸러 보기. 2~3종목을 상대 수익률 차트와 항목별 비교 표(우위 표시)로 나란히 비교 |
| 💼 **포트폴리오 · 관심종목** | 실제 보유 종목(토스증권 계좌 불러오기, 소유자 전용), 관심종목과 현재가·등락률 |
| 🎮 **모의투자 · 랭킹** | 1억 원 가상 계좌, 서버 실시간가 체결, 수수료 0.1%, 자산 곡선, 배지, 닉네임 랭킹(7일·30일·전체) |
| 🤖 **멀티 AI 봇 4종** | 공격형 모멘텀 · 균형형 스윙 · 안정형 가치 · 기준선 정량(AI 미사용 대조군). 장 마감 후 신호 생성 → 다음 장 시가 체결 → 장중 손절·익절 확인. 봇마다 1억 원 가상 계좌, `/ai-bots`에서 수익률 비교 차트, AI 검증 효과, 보유 포지션, **매매 이력**, 신호 사후 검증 |
| 🔔 **성향별 신호 알림** | 받을 봇·종목 범위(전체/보유/관심종목)·최소 신뢰도를 고르면 헤더 종 아이콘으로 알림. **PWA 웹 푸시**를 켜면 앱을 닫아도 휴대폰·PC로 알림 (한 번에 4건 이상이면 요약 1건) |
| 📱 **PWA** | 홈 화면에 설치해 앱처럼 실행. 서비스워커 캐시와 푸시 처리 |

---

## 운영 환경

```
사용자 ─ HTTPS ─ <your-subdomain>.duckdns.org:<PORT>
                    │
            Oracle Cloud VM (메모리 1GB + 스왑)
            ├─ gunicorn: 워커 1개 × gthread 스레드 8개, timeout 120초
            ├─ 백그라운드 스레드: 섹터맵 스냅샷 · AI 봇 스케줄러 · 스크리너 펀더멘탈 순환 · 추세 셋업 워밍업(표가 비었을 때만)
            └─ PostgreSQL (DATABASE_URL)
```

- **자동 배포**: `main`에 push하면 서버가 약 5분 안에 가져가 재시작합니다. 재시작 후 응답이 없으면 직전 커밋으로 자동 롤백합니다. 그래서 `main`은 항상 배포 가능한 상태로 둡니다(push 전 최소 `python -c "import app"`).
- **워커는 1개만**: 메모리 캐시와 백그라운드 스레드가 단일 프로세스를 전제로 합니다.
- **메모리를 아끼는 구조**: 섹터맵·AI 결과는 미리 만든 스냅샷/캐시를 돌려주고, 무거운 라이브러리(웹 푸시 등)는 실제로 쓸 때만 불러옵니다.
- **DB 마이그레이션 도구 없음**: 새 테이블은 앱 시작 시 `db.create_all()`이 만듭니다. 기존 테이블 구조를 바꿀 때는 운영 데이터 호환을 따로 챙겨야 합니다.
- 토스증권 Open API는 허용 IP 제한이 있어 운영 서버에서만 동작합니다(`TOSS_PROXY_SETUP.md`). 그 밖의 환경에서는 yfinance·네이버로 폴백합니다.
- `Procfile`, `railway.toml`은 예전 Railway 배포용으로 지금은 쓰지 않습니다.

### 데이터 출처
Yahoo Finance(yfinance), 네이버 증권(국내 시세·업종·시가총액·지수·뉴스·리포트), 토스증권 Open API, 금융감독원 DART, S&P 500 GICS 공개 데이터셋. 시세는 제공처 사정에 따라 지연될 수 있습니다.

---

## 빠른 시작 (로컬)

```bash
git clone https://github.com/<OWNER>/stock-analyzer.git
cd stock-analyzer
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 필요한 값만 채움 (비워 두면 해당 기능은 폴백)
python app.py                      # http://127.0.0.1:5000
```

- `DATABASE_URL`을 비워 두면 SQLite(`instance/portfolio.db`)를 씁니다.
- API 키가 없어도 화면과 대부분의 로직은 확인할 수 있습니다(AI는 비활성 또는 대체 문구, 시세는 yfinance).
- 로컬 개발 중 백그라운드 작업을 끄려면 `BOT_SCHEDULER=0`, `SECTORMAP_SCHEDULER=0`, `SCREENER_SCHEDULER=0`, `TRENDS_SCHEDULER=0`.

### 데이터 파일 다시 만들기 (가끔)

```bash
python build_stock_db.py          # stock_db.json: 검색·스캔 유니버스 (KOSPI·KOSDAQ·S&P 500·NASDAQ)
python build_sector_universe.py   # sector_universe.json: 섹터 분류 + 미국 발행주식수 (분기 1회 권장, 수 분 소요)
```

---

## 환경변수 (`.env.example` 참고)

실제 값은 서버의 `.env`에만 둡니다. 저장소·문서·커밋에는 절대 넣지 않습니다.

| 구분 | 변수 | 설명 |
|---|---|---|
| 기본 | `FLASK_SECRET_KEY` | 세션 서명 키. 운영에서 없으면 재시작 때마다 로그인이 풀림 |
| | `DATABASE_URL` | PostgreSQL 접속 주소. 비우면 로컬 SQLite |
| | `BASE_URL` | OAuth 콜백 기준 주소 (예: `https://<your-subdomain>.duckdns.org:<PORT>`) |
| 로그인 | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET` | Google·카카오 OAuth |
| AI | `GEMINI_API_KEY`, `GROQ_API_KEY` | 둘 중 하나만 있어도 동작. 둘 다 있으면 Gemini 우선 |
| | `AI_BACKUP_BASE_URL`, `AI_BACKUP_API_KEY`, `AI_BACKUP_MODEL` | (선택) OpenAI 호환 백업 공급자 |
| | `GEMINI_MODELS`, `GROQ_MODELS` | (선택) 모델 고정. 비우면 공급자 모델 목록에서 자동 선택 |
| 토스·공시 | `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `TOSS_PROXY_URL` | (선택) 토스증권 시세·계좌 |
| | `TOSS_OWNER_EMAIL` 또는 `TOSS_OWNER_USER_IDS` | 소유자 전용 기능(계좌 불러오기, 진단 화면, 봇 수동 실행) 허용 계정 |
| | `DART_API_KEY` | (선택) 국내 분기 실적 |
| AI 봇 | `AI_DAILY_BUDGET` (기본 60), `BOT_AI_INTERVAL` (기본 4초), `BOT_SCHEDULER` | 봇 AI 평가 하루 상한·호출 간격·스케줄러 끄기 |
| 로그 | `LOG_LEVEL` | 기본 `INFO`(배치·스케줄러 진행: `[BOT]`·`[TRENDS]`·`[SCREENER]`·`[SECTORMAP]`·`[AI]`·`[intraday]` 등). `DEBUG` 면 요청별 세부 진단(`[KR qtr]`·`[DART]`·`[events]`)까지, `WARNING` 이면 경고 이상만 |
| 섹터맵 · 스크리너 · 추세 셋업 | `SECTORMAP_SCHEDULER`, `SCREENER_SCHEDULER`, `TRENDS_SCHEDULER` | `0`이면 각각의 백그라운드 갱신 끔 (추세 셋업은 첫 워밍업만 끄고, 장 마감 확정·장중 돌파는 봇 배치·섹터맵이 계속 갱신) |
| 알림 | `NOTIFY_PROVIDERS` | 기본 `inapp` (사이트 안 알림) |
| 웹 푸시 | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | (선택) PWA 푸시. 비워 두면 푸시만 꺼지고 나머지는 정상. 키 만드는 한 줄 명령은 `.env.example`에 있음 |

운영 설정 상태는 소유자 계정으로 로그인한 뒤 `/api/debug/ai`에서 값 없이 **설정됨(true)/미설정(false)**으로 확인할 수 있습니다.

---

## 백그라운드 작업 (운영 단일 프로세스)

| 작업 | 시각 (시장 현지) | 내용 |
|---|---|---|
| AI 봇 장 마감 배치 | 한국 평일 15:45 · 미국 평일 16:30 | 전체 유니버스 정량 필터 → 후보 펀더멘털 → 종목당 하루 1회 AI 평가(봇 공유) → 봇별 판정·손익비 기각·하루 한도 → 신호 저장, 신호 추적, 봇 계좌 동기화, 알림·푸시 |
| AI 봇 장중 체크 | 장중 매 5분 정각 | 대기 신호를 다음 장 시가로 체결, 보유 신호 손절·트레일링·익절, 봇 계좌 매매 |
| 섹터맵 스냅샷 | 장중 매시 7·22·37·52분, 마감 후 한국 15:40 · 미국 16:20 | 시세 받아 시총가중 집계 후 `ai_cache`에 저장. 주말·휴장일 스킵 |
| 스크리너 스냅샷 | 위 두 작업에 함께 + 펀더멘탈은 6초에 1종목 | 섹터맵 갱신 때 가격, 봇 배치 때 기술 지표, 7일 지난 종목만 펀더멘탈·목표가 갱신 |
| 추세 셋업 확정 | AI 봇 장 마감 배치에 함께 | 봇이 받은 1년 일봉을 재사용해 전체 종목 점수·태그·돌파 기준가를 `trend_setups`에 저장 (추가 다운로드 없음) |
| 추세 셋업 장중 돌파 | 섹터맵 장중 갱신에 함께 | 돌파 후보의 현재가·등락률·거래량만 덮어씀 (계산 없음) |

---

## 프로젝트 구조

```
stock-analyzer/
├── app.py                    # Flask 본체: 페이지 라우트와 /api/* (분석, 시황, 섹터맵, 포트폴리오, 모의투자, 랭킹, AI 봇, 알림, 푸시)
├── auth.py                   # Google·카카오 OAuth
├── models.py                 # SQLAlchemy 모델 (아래 표)
├── ai_cache.py               # AI 결과 영구 캐시 (메모리 LRU + DB), 동시 호출 1회로 합치기
├── bot_engine.py             # AI 봇 신호 엔진: 전략 정의, 배치, 신호 추적, 장중 체크, 스케줄러
├── bot_trader.py             # 봇 가상 계좌: 신호 ↔ 모의투자 매매 동기화, 성적(승률·손익비·MDD)
├── notifier.py               # 신호 알림 발송 (사이트 안 알림 + 웹 푸시 묶음 발송)
├── webpush.py                # 웹 푸시: VAPID 확인, 구독 저장, 발송, 만료 구독 정리
├── sector_map.py             # 시총가중 섹터맵 집계와 백그라운드 스냅샷
├── screener.py               # 스크리너 스냅샷 갱신·조회, 종목 비교
├── trends_scanner.py         # 추세 셋업 점수 계산 (일봉 → 점수·태그·돌파 기준가, 네트워크 없음)
├── trend_setups.py           # 추세 셋업 저장·장중 돌파·현시점 갱신·워밍업
├── toss_api.py               # 토스증권 Open API
├── badges.py                 # 모의투자 배지
├── analysis/
│   ├── indicators.py         # 기술 지표
│   ├── advanced.py           # 펀더멘털 지표와 4팩터 점수
│   ├── ai_analysis.py        # 종합 판단, 매수·손절·목표 구간
│   └── valuation.py          # DCF 적정가
├── build_stock_db.py         # → stock_db.json (종목 유니버스)
├── build_sector_universe.py  # → sector_universe.json (섹터 분류·발행주식수)
├── templates/                # index(홈: 시황·종목 분석), screener(발굴: 스크리너·추세 셋업·비교), ai_bots(AI 봇), trading(모의투자), leaderboard(랭킹) + 공통 헤더·푸터
├── static/
│   ├── js/                   # main · trading · leaderboard · ai_bots · screener · notify(알림 종·설정·푸시·관심종목) · chart
│   ├── css/style.css         # 다크 테마
│   ├── service-worker.js     # 캐시 정책 + push / notificationclick
│   ├── manifest.json, icons/ # PWA
├── scripts/                  # 아이콘 생성, 토스 보유종목 SQL 변환
├── HANDOFF.md                # 작업 기록 (세션별 변경, 운영 확인 사항)
├── CLAUDE.md                 # 작업 규칙 요약
└── TOSS_PROXY_SETUP.md       # 토스 고정 IP 프록시 구성
```

### 주요 테이블

| 테이블 | 용도 |
|---|---|
| `users` | 사용자(OAuth)와 AI 봇 계좌(`provider='bot'`), 모의투자 현금·초기 자본, 닉네임 |
| `holdings` / `paper_holdings` / `transactions` | 실제 보유 / 모의투자 보유 / 모의투자 거래 내역 |
| `asset_snapshots`, `user_badges` | 일별 자산, 배지 |
| `ai_cache` | AI 결과·섹터맵 스냅샷·봇 배치 요약·잠금 |
| `bot_strategies`, `ai_stock_assessments`, `ai_signals`, `bot_positions` | 봇 전략 파라미터, 종목별 AI 평가, 신호와 사후 추적, 신호 ↔ 봇 계좌 거래 |
| `applied_stock_splits` | 반영한 액면분할 기록 (보유 건 × 분할 유니크 → 같은 분할 중복 반영 방지) |
| `drop_hold_states` | 장중 -40% 이하 급락 손절 보류 상태 (분할 의심 강도·첫 감지 시각·released, 재시작에도 유지, 7일 지나면 정리) |
| `user_signal_subscriptions`, `watchlist_items`, `notifications`, `push_subscriptions` | 알림 구독 조건, 관심종목, 사이트 안 알림, 기기별 웹 푸시 구독 |
| `screener_snapshots` | 스크리너용 종목별 경량 스냅샷 (가격·밸류에이션·기술 지표·펀더멘탈) |
| `trend_setups` | 추세 셋업 종목별 스냅샷 (장 마감 확정 점수·태그·돌파 기준가 + 장중 현재가·돌파 시각) |

---

## 주요 API

| 구분 | 경로 |
|---|---|
| 분석 | `GET /api/analyze?ticker=`, `GET /api/analyze/extra`, `GET /api/chart?interval=10m\|1h\|1d\|1wk\|1mo`, `GET /api/search?q=` |
| 시황 | `GET /api/market/overview`, `GET /api/market/sectormap?market=KR\|US`, `GET /api/market/movers` |
| 추세 셋업 | `GET /api/trends/setups?market=ALL\|KR\|US`, `POST /api/trends/refresh` (현시점 갱신, 시장별 60초 쿨다운), `POST /api/trends/scan` (소유자 전체 재스캔), `GET /api/trends/status` |
| 포트폴리오 | `GET/POST /api/portfolio`, `PUT/DELETE /api/portfolio/<id>`, `POST /api/portfolio/import-toss` (소유자) |
| 관심종목 | `GET/POST /api/watchlist`, `DELETE /api/watchlist/<ticker>`, `GET /api/watchlist/quotes` |
| 모의투자 | `GET /api/trading/dashboard`, `POST /api/trading/buy` (선택 `target_price`·`stop_loss_price`), `POST /api/trading/sell`, `PATCH /api/trading/holdings/<ticker>/targets`, `GET /api/trading/transactions`, `GET /api/trading/history` |
| 랭킹 | `GET /api/leaderboard?metric=total\|7d\|30d&bots=1\|0` |
| AI 봇 | `GET /api/ai-bots/overview`, `GET /api/ai-bots/chart?period=`, `GET /api/ai-bots/<봇>`, `GET /api/ai-bots/<봇>/trades`, `GET /api/stock-bots/<종목>` (종목 상세 봇 현황), `GET /api/bots/signals`, `GET /api/bots/signals/<id>`, `POST /api/admin/bots/run` (소유자) |
| 알림 | `GET/PUT /api/signal-subscription`, `GET /api/notifications`, `POST /api/notifications/read` |
| 스크리너 · 비교 | `GET /api/screener`, `GET /api/screener/meta`, `GET /api/compare?tickers=A,B,C` |
| 웹 푸시 | `GET /api/push/vapid-public-key`, `POST /api/push/subscribe`, `POST /api/push/unsubscribe`, `POST /api/push/test` |
| 진단 (소유자) | `GET /api/debug/ai`, `GET /api/debug/toss` |

---

## PWA 웹 푸시 켜기

1. 운영 서버에서 키를 만듭니다 (`pywebpush` 설치 후 한 줄 명령, `.env.example` 참고).
2. 출력된 `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`와 `VAPID_SUBJECT=mailto:<연락 받을 주소>`를 서버 `.env`에 넣고 앱을 재시작합니다.
3. 사이트에서 헤더 종 → **알림 설정** → **이 기기에서 푸시 받기** → **테스트 푸시 보내기**.
   - Android·PC 크롬/엣지/삼성 인터넷/파이어폭스 지원
   - iPhone·iPad는 Safari에서 **홈 화면에 추가**한 앱으로 열었을 때만 가능 (iOS 16.4 이상)
4. 키를 바꾸면 기존 기기 구독은 다시 켜야 합니다.

---

## 보안

- 비밀 값은 환경변수로만 읽고, `.env`와 `ssh-key-*`는 `.gitignore` 대상입니다.
- 모의투자 체결가는 클라이언트 값이 아니라 서버 실시간가로 강제합니다.
- 소유자 전용 기능과 진단 화면은 다른 사용자에게 404를 돌려줍니다.
- 웹 푸시 구독은 본인 기기만 저장·해제할 수 있고, 만료된 구독은 발송 중 자동 삭제됩니다.

---

## 라이선스

MIT License
