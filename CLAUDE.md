# CLAUDE.md

주식 분석 웹앱 (Flask). 이 파일은 Claude 세션(로컬, 클라우드)이 작업 전에 읽는 규칙과 구조 요약이다.

## 꼭 지킬 규칙

- **민감 정보 커밋 금지.** 서버 IP, 포트, 도메인, SSH 키, API 키와 시크릿, 비밀번호, DB 접속 주소, 이메일, 실명, 증권 계좌 정보는 코드, 문서, 커밋 메시지 어디에도 넣지 않는다. 문서에 필요하면 `<ORACLE_VM_IP>`, `<PROXY_PORT>`, `<OWNER>` 같은 자리표시자를 쓴다. 커밋 전에 `git diff --cached`로 확인한다.
- 비밀 값은 환경변수로만 읽는다. 변수 이름은 `.env.example` 참고. `.env`와 `ssh-key-*`는 `.gitignore` 대상이다.
- 이 저장소는 2026-11에 민감 정보 제거를 위해 기록을 다시 작성했다. 강제 push(`--force`)는 사용자가 명시적으로 요청할 때만 한다.

## 배포 (push = 운영 반영)

- `main`에 push하면 운영 서버가 약 5분 안에 자동으로 가져가 재시작한다. 재시작 후 응답이 없으면 직전 커밋으로 자동 롤백한다.
- 그래서 `main`은 항상 배포 가능한 상태여야 한다. push 전에 최소한 `python -c "import app"`이 성공하는지 확인한다.
- `requirements.txt`가 바뀌면 서버가 패키지를 다시 설치한다. 운영 Python은 3.11이다 (pandas 3, numpy 2.4는 3.11 이상 필요).
- 운영 실행: gunicorn **워커 1개 + gthread 스레드 8개**, timeout 120초. 메모리 캐시와 백그라운드 스레드(섹터맵, 봇 스케줄러, 스크리너, `trend_setups.py`)가 단일 프로세스를 전제로 하므로 워커 수를 늘리지 않는다.
- 운영 DB는 PostgreSQL (`DATABASE_URL`). 로컬에서 비워 두면 SQLite(`instance/portfolio.db`)를 쓴다. 모델 변경 시 운영 데이터와 호환되는지 신경 쓴다 (마이그레이션 도구 없음).
- 운영 서버 메모리가 작다 (1GB + 스왑). 무거운 의존성 추가는 피한다.
- `Procfile`, `railway.toml`은 예전 Railway 배포용이다. 지금은 쓰지 않는다.

## 로컬 실행

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env    # 값은 직접 채움. 비워 두면 일부 기능은 폴백 동작
python app.py           # http://127.0.0.1:5000
```

API 키가 없으면 토스 시세는 yfinance로, AI 기능은 비활성 또는 대체 동작한다. 화면과 로직 수정 확인은 키 없이도 가능하다.

## 구조

| 경로 | 역할 |
|---|---|
| `app.py` | Flask 앱 본체. 페이지 라우트와 `/api/*` (분석, 차트, 시황, 섹터맵, 검색, 포트폴리오, 모의투자, 랭킹 등) |
| `auth.py` | Google, 카카오 OAuth 로그인. 콜백 주소는 `BASE_URL` 기준 |
| `models.py` | SQLAlchemy 모델 (users, holdings, paper_holdings, transactions, asset_snapshots, user_badges, ai_cache, 봇·알림·푸시 테이블). 전체 목록은 README |
| `portfolio_analytics.py` | 홈 '내 포트폴리오' 리스크 · 배당 계산(네트워크 없음): 섹터 비중·편중(ETF·기타 제외 업종 기준 40%), 통화 노출, 연간·월별 예상 배당(dividendRate 우선), 다가오는 배당락일. API `/api/portfolio/analytics` 가 `_dividend_profile`(12시간 캐시)로 입력을 만든다 |
| `badges.py` | 모의투자 배지 |
| `toss_api.py` | 토스증권 Open API (실시간 시세, 캔들, 환율). 허용 IP 제한이 있어 운영 서버에서만 동작. 여러 화면의 실시간가는 `get_prices_cached`(종목별 20초 공용 캐시, 200종목 묶음) 1순위, 실패 시 네이버/yfinance |
| `market_hours.py` | 한·미 거래일·장 시간 (표준 라이브러리만). 미국 휴장일·조기폐장 13:00 ET 자동 계산, KRX 고정 휴장일·대체공휴일 규칙 + 연도별 음력·선거일 표(`KR_YEARLY`, 2025~2028 — 2029년부터는 표 추가 필요), 연말 휴장·연초/수능일 개장 시각. `is_market_open`·`session_window`·`market_status`·`next_trading_day` |
| `krx_ref.py` | 한국 종목 KRX 공식 전일 종가(네이버 실시간 폴링 `종가 − 전일 대비`, 50종목 묶음·1분 캐시). 종목 상세·추세 셋업·스크리너·섹터맵·급등락의 등락률 기준을 통일 |
| `trends_scanner.py`, `trend_setups.py` | 추세 셋업. 점수 계산(일봉 입력, 네트워크 없음)과 저장. 장 마감 배치가 봇 일봉을 재사용해 `trend_setups` 확정, 장중엔 섹터맵 시세로 돌파만 덮어씀. 화면은 `/screener?tab=trends` |
| `sector_map.py` | 메인 섹터맵: 전체 유니버스 시총가중 집계와 백그라운드 스냅샷. 분류는 `sector_universe.json` (`build_sector_universe.py` 로 생성) |
| `splits.py` | 액면분할 자동 반영 (yfinance splits 12시간 캐시 → 모의·봇 계좌·봇 신호·실제 포트폴리오 수량·단가·목표/손절 보정, `applied_stock_splits` 로 멱등). 봇 장 마감 배치 시작·장중 -40% 급락 방어·사용자 화면 하루 1회 |
| `bot_engine.py`, `bot_trader.py`, `notifier.py` | AI 매매 신호 봇: 신호 생성·추적, 봇 가상 계좌 매매, 알림 |
| `webpush.py` | PWA 웹 푸시 (VAPID 키가 없으면 자동으로 꺼짐, pywebpush 는 보낼 때만 import) |
| `screener.py` | 스크리너 스냅샷(`screener_snapshots`) 갱신·조회와 종목 비교. 가격은 섹터맵, 기술 지표는 봇 배치에서 받고 펀더멘탈은 7일 순환 |
| `disclosures.py` | 공시·수주 레이더(`disclosure_events`): DART 공급계약·자사주·유상증자(15분) · SEC Form 4 장내 매수·8-K(30분). 봇 스케줄러 루프에 합류(새 스레드 없음), 30일 보관, 보유·관심종목 알림. 화면은 `/screener?tab=disclosures` |
| `analysis/` | 기술적 분석(`indicators.py`, `advanced.py`), 종합 판단(`ai_analysis.py`), 밸류에이션과 DCF(`valuation.py`) |
| `stock_db.json` | 종목 검색 DB (KOSPI, KOSDAQ, S&P500, NASDAQ). `build_stock_db.py`로 생성 |
| `static/js/chart.js` | 종목 상세 기술적 분석 차트 (LightweightCharts 4.1: 툴바, 동기화, 터치, 전체화면). main.js 는 `renderCharts(data)` 만 부른다 |
| `templates/`, `static/` | 프론트엔드 (바닐라 JS, LightweightCharts). 정적 파일 캐시 무효화는 `?v=` 쿼리 숫자를 올린다 |
| `HANDOFF.md` | 세션별 작업 기록. 큰 작업 후 갱신 (민감 정보 금지 규칙 동일) |

## 클라우드 세션에서 작업할 때

- 운영 서버에 접속할 수 없다 (SSH 키 없음). 서버 로그 확인이나 서버 설정 변경은 로컬 PC 세션에서 한다.
- 코드 수정 → `main`에 push → 약 5분 뒤 운영 반영. 사용자가 브랜치나 PR을 원하면 그에 따른다.
