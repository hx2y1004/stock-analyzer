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
- 운영 실행: gunicorn **워커 1개 + gthread 스레드 8개**, timeout 120초. 메모리 캐시와 백그라운드 스캔 스레드(`trends_scanner.py`)가 단일 프로세스를 전제로 하므로 워커 수를 늘리지 않는다.
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
| `models.py` | SQLAlchemy 모델 (users, holdings, paper_holdings, transactions, asset_snapshots, user_badges) |
| `badges.py` | 모의투자 배지 |
| `toss_api.py` | 토스증권 Open API (실시간 시세, 캔들, 환율). 허용 IP 제한이 있어 운영 서버에서만 동작 |
| `trends_scanner.py` | 급등, 트렌드 백그라운드 스캔 |
| `sector_map.py` | 메인 섹터맵: 전체 유니버스 시총가중 집계와 백그라운드 스냅샷. 분류는 `sector_universe.json` (`build_sector_universe.py` 로 생성) |
| `bot_engine.py`, `bot_trader.py`, `notifier.py` | AI 매매 신호 봇: 신호 생성·추적, 봇 가상 계좌 매매, 알림 |
| `analysis/` | 기술적 분석(`indicators.py`, `advanced.py`), 종합 판단(`ai_analysis.py`), 밸류에이션과 DCF(`valuation.py`) |
| `stock_db.json` | 종목 검색 DB (KOSPI, KOSDAQ, S&P500, NASDAQ). `build_stock_db.py`로 생성 |
| `templates/`, `static/` | 프론트엔드 (바닐라 JS, LightweightCharts). 정적 파일 캐시 무효화는 `?v=` 쿼리 숫자를 올린다 |
| `HANDOFF.md` | 세션별 작업 기록. 큰 작업 후 갱신 (민감 정보 금지 규칙 동일) |

## 클라우드 세션에서 작업할 때

- 운영 서버에 접속할 수 없다 (SSH 키 없음). 서버 로그 확인이나 서버 설정 변경은 로컬 PC 세션에서 한다.
- 코드 수정 → `main`에 push → 약 5분 뒤 운영 반영. 사용자가 브랜치나 PR을 원하면 그에 따른다.
