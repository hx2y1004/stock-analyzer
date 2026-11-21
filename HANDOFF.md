# StockAnalyzer 작업 인계 문서

> **다음 세션에서 가장 먼저 이 파일을 읽고 작업 시작할 것**
> 마지막 업데이트: 2026-11-21 KST (모바일 반응형 일괄 정리 PR #31, 추세 셋업 2단 하이브리드 · 메뉴 개편 PR #30)

---

## 🆕 2026-11-19~20 세션 (클라우드 세션, Opus) — PR #1~#10

> 이 세션은 운영 서버에 접속할 수 없는 클라우드 컨테이너에서 진행했다. 운영 로그·`.env`는 확인하지 못했다.
> 배포 환경은 Railway가 아니라 **Oracle VM**이다 (CLAUDE.md 참고). 아래 "현재 상태(2026-07-25)" 절의 Railway 언급은 옛 정보.

### ⚠️ 운영에서 확인 필요 (1·2번은 2026-11-20 확인 완료)
0. **SSH 없이 확인하는 법 (PR #21)**: 운영 사이트에 소유자 계정으로 로그인 → `/api/debug/ai` 의 `config.set` 에
   주요 환경변수가 설정됐는지(true/false, 값은 안 보임)와 `config.warnings` 가 나온다. 이 화면 자체가 "Not Found" 면
   `TOSS_OWNER_EMAIL`/`TOSS_OWNER_USER_IDS` 가 없거나 로그인 계정이 소유자가 아니다.
   **2026-11-20 확인 결과 (운영 `/api/debug/ai`)**: `FLASK_SECRET_KEY`, `TOSS_OWNER_EMAIL`, `TOSS_OWNER_USER_IDS`,
   `DATABASE_URL`, `BASE_URL`, Google·카카오 로그인 키, `TOSS_CLIENT_ID`, `DART_API_KEY`, Gemini·Groq 키 모두 설정됨.
   `AI_BACKUP_API_KEY` 만 없음(선택). `warnings` 없음. Gemini 는 `gemini-3.5-flash-lite` 부터 자동 선택, 봇 스케줄러 켜짐, AI 하루 예산 60.
   로컬 PC `.env` 에는 Groq 키만 확인함(로컬 실행용).
   운영 서버 SSH 개인 키 파일: 사용자 PC 의 로컬 저장소 폴더에 있음(`ssh-key-*.key`, git 제외). 2026-11-21 접속 확인. 서비스 이름 `stock-analyzer`, 환경변수는 앱 폴더 밖의 systemd EnvironmentFile 에서 읽음
1. ~~운영 `.env`에 `FLASK_SECRET_KEY` 가 있는지~~ → **확인 완료(2026-11-20, 설정됨)**. 없으면 PR #7 이후 프로세스마다 임시 랜덤 키를 써서
   재시작/배포 때마다 로그인이 풀린다.
2. ~~`TOSS_OWNER_EMAIL` 또는 `TOSS_OWNER_USER_IDS` 가 있는지~~ → **확인 완료(2026-11-20, 둘 다 설정됨, 소유자 계정으로 진단 화면 접근 확인)**.
   없으면 운영에서 토스 계좌 불러오기, `/api/debug/toss`, `/api/debug/ai`, 봇 수동 실행이 차단된다.
3. ~~미국 뉴스 제목 번역~~ → 운영에서도 영어로 나와 원인 확인: `deep_translator`는 translate.google.com 웹페이지를 긁는데
   서버(데이터센터) IP는 Google 봇 확인(`/sorry`)으로 막힌다. PR #11에서 **AI(Gemini/Groq) 일괄 번역**으로 바꿈
   (`_translate_news_items`, 원문 제목 기준 캐시, 실패 시 Google → 원문, Google 실패는 10분간 재시도 안 함, 실패 로그 `[translate]`).
   운영 로그에서 `[news_translate]`/`[translate]` 경고가 없는지 확인.
4-1. **AI 호출 자체가 실패하던 문제 (PR #12)**: 운영 화면의 "하락 이유"가 AI 해설 대신 "관련 뉴스: …" 폴백으로 나왔다.
   코드에 고정된 모델이 종료됨 — Groq `llama-3.x` 계열은 2026-08-16 무료/개발자 등급 종료(보도 기준),
   Gemini `gemini-2.5-flash`도 종료 일정(12/1 또는 10/16로 보도가 엇갈림, 공식 문서는 이 환경에서 접속 불가해 미확인).
   → 모델을 **제공사 모델 목록 API로 자동 선택**(6시간 캐시), 환경변수 `GEMINI_MODELS`/`GROQ_MODELS`(콤마)로 고정 가능,
   조회 실패 시 기본 목록(`gemini-flash-latest` 등 / `openai/gpt-oss-120b` 등).
   **소유자 전용 진단 `/api/debug/ai`** (`?refresh=1` 목록 재조회, `?test=1` 실제 한 줄 호출): 키 설정 여부, 선택 모델, 최근 오류.
4-2. **PR #12 직후 부가 섹션 전체 실패 (PR #13)**: AI가 다시 동작하면서 `/api/analyze/extra`가 AI 3번(번역·기업소개·등락사유)을
   차례로 기다려 60초를 넘김 → 브라우저가 포기. 지금은 공용 실행기 `_BG_EXEC`로 동시 실행, 뉴스·애널리스트 준비 후 AI는 3초만 대기,
   번역은 3초만 대기(늦으면 원문). 늦은 AI는 백그라운드로 끝까지 돌아 캐시를 채우고 응답에 `partial: true` →
   화면이 15초 뒤 최대 2번 다시 받아 채움. 부분 결과는 20초만 캐시. 급등락 사유 10분 캐시 `_MOVE_REASON_CACHE`. Gemini 호출 timeout 40→25초.
4. AI 호출(기업 소개·급등락 사유) 소요 시간. 이 환경엔 AI 키가 없어 측정 못 함. PR #10으로 첫 화면은 AI를 기다리지 않음.

### AI 호출 구조 (PR #15) — 무료 한도 초과 대응
- **AI 호출 지점**: 기업 소개(`company_profile`), 종목 인사이트(`daily_insight` = 급등락 사유 + 밸류에이션 해설 + 미번역 뉴스), 매매 코치·포트폴리오 점검(사용자가 누를 때만).
- **종목 페이지 = AI 최대 2회**: 기업 소개(DB 30일) + `get_stock_insight()` 1회 JSON 호출(DB 4시간, 등락률이 2%p 이상 바뀌면 갱신).
  뉴스 번역은 기사 URL 해시 기준 **영구** 캐시(`news_translation:<id>`), 한국 종목 뉴스는 번역 안 함.
  밸류에이션 해설은 인사이트에 포함 — `/api/dcf/comment`는 캐시만 읽는다.
- **캐시**: `ai_cache` 테이블(models.`AiCache`, `db.create_all`이 생성) + 메모리 LRU 2단(`ai_cache.py`). 재시작·배포 후에도 유지.
  같은 키의 AI 호출은 `ai_cache.single_flight()`로 1번만(동시 접속·새로고침 연타).
- **공급자 체인**: Gemini → Groq → (선택) `AI_BACKUP_*` OpenAI 호환. 429 는 재시도하지 않고 모델별 쿨다운
  (`429_rpm` 20~300초 / `429_rpd` Gemini 는 태평양 자정, 그 외 1시간), 404 모델은 6시간 건너뜀.
- **로그**: `[AI] ticker=… task=… cache_hit=true|false provider=… model=… latency_ms=… status=ok|429_rpm|429_rpd|error`
  → 한도가 분당인지 일일인지 로그로 바로 구분.
- 로컬 테스트 시 주의: 가짜 AI로 시험하면 로컬 `instance/portfolio.db`의 `ai_cache`에 남는다 → 시험 후 비울 것.
- **PR #16 (국내 종목 부가 섹션 전체 실패)**: 부가 섹션이 분석 맥락을 프로세스 메모리에서만 찾고, 없으면 분석 전체를 동기로
  다시 돌렸다 → 다른 프로세스/재시작 후엔 느린 국내 종목에서 60초 초과로 뉴스·애널리스트까지 실패.
  지금은 맥락(`analysis_ctx`, 1시간)과 압축 분석 본문(`analysis_body`, 5분, 메모리 LRU 미사용·만료 즉시 삭제)을 DB에도 저장,
  뉴스·애널리스트는 맥락과 무관하게 먼저 시작, 맥락이 정말 없으면 재분석은 백그라운드(8초만 대기, AI 는 보충 요청으로).
  응답에 `ai_pending` → 기다리는 중엔 실패 배너 안 띄움. 로그 `[extra] ticker=… total=… parts={…} ctx=yes|no problems=[…]`.

### 모바일 반응형 일괄 정리 (PR #31) — 갤럭시 폴드 커버·펼침, 360~720px
- **발굴 세부 필터 오버플로우**: `.sc-filter-grid` 가 `repeat(2, 1fr)` 라 열 최소 폭이 입력창 고유 폭(약 298px)을 따라가
  360px 에서 카드 밖으로 313px 넘쳤다. `minmax(0, 1fr)` + 입력창 `width:100%; min-width:0; box-sizing:border-box`, 480px 이하 1열.
- **헤더**: 로고를 `<span class="logo-icon">` + `<span class="logo-text">` 로 나눠 430px 이하에선 글자 span 만 숨김(`::first-letter` 핵 삭제).
  메뉴 5개 축소를 480px(7px/12px) → 400px(5px/11.5px) → 350px(4px/11px) 단계로. 이름을 숨기는 480px 이하에선 프로필을 32px 정원형 배지로.
  (430px 이하에서는 기존대로 아바타를 숨기고 종·로그아웃만)
- **전역**: `html, body { overflow-x: clip }` (sticky 헤더 유지). AI 봇 모달 `width: min(520px, calc(100vw - 24px))`.
  스크리너 모바일 카드 종목명 줄바꿈, `.ab-scroll` 터치 스크롤, 종목 상세 DCF 기준점 480px 이하 한 줄씩(라벨 왼쪽·값 오른쪽),
  모의투자 빠른 매수 버튼 인라인 스타일 → `.qb-submit` (768px 이하 전체 폭).
- 확인 방법: 화면 밖 요소 검사(스크롤 컨테이너 안은 제외)를 body clip 과 무관하게 따로 돌린다 — clip 때문에 scrollWidth 만으로는 넘침이 가려진다.

### 추세 셋업 2단 하이브리드 + 메뉴 개편 (PR #30)
- **메뉴**: `홈(/) · 발굴(/screener) · AI 봇 · 모의투자 · 랭킹` (주소는 그대로). 종목 상세의 `← 홈으로`.
- **홈 하단 탭**: `내 포트폴리오 | 관심종목` 2개 (포트폴리오 기본). 추세 탭과 예전 수동 스캔 JS(887종목 × 73초 재스캔) 삭제.
  오늘의 급등락 카드 머리에 `🔥 오늘의 추세 셋업 보러가기 →` (`/screener?tab=trends`).
- **발굴 3탭** `종목 스크리너 | 추세 셋업 | 종목 비교`. `?tab=trends` 는 서버가 첫 탭을 정해 깜빡임 없음.
  추세 셋업 카드: 스파크라인·점수·배지·태그·돌파 기준가·☆ 관심종목·+ 비교 담기(스크리너와 같은 플로팅 바구니 → 비교 탭).
  시장(전체/한국/미국)·패턴(전체/Perfect/장중 돌파/Stage2/52주 신고가) 필터는 받은 데이터로 즉시 거름(재요청 없음).
- **1단 장 마감 확정**: `bot_engine.run_market` 의 `on_features` 훅 → `app._on_batch_features` 가 스크리너 기술 지표와
  `trend_setups.on_batch` 를 함께 부른다. 봇이 받은 1년 일봉 재사용(추가 다운로드는 지수 2개뿐), 종목당 약 5ms.
  펀더멘탈 25점은 스크리너 스냅샷 4팩터(종합 15 · 성장성 5 · 수익성 5) → 종목별 재무 조회 없음.
  돌파 기준가: 52주 고점 -3% 이내면 52주 고점, 아니고 주봉 BB 수축이면 최근 20거래일 고가.
- **2단 장중**: `sector_map` on_rows → `app._on_sector_rows` 가 스크리너 가격과 `trend_setups.apply_live` 를 부른다(행에 거래량 추가).
  확정일 당일 시세는 장중 값으로 쓰지 않는다(base_date < 현지 오늘일 때만). 장 마감 재확정 시 live_* 초기화.
  `POST /api/trends/refresh`: 장이 열린 시장의 후보 상위 50개만, 한국은 네이버 폴링 다종목 1요청(약 1.4초), 미국은 Yahoo 묶음(1~3초). 시장별 60초 쿨다운.
- **태그 누락 수정**: 부분 점수(Stage2 2~3/5, 1·3·6개월 단독, 시장 대비 +5%p, 52주 고점 근접)도 태그를 남기고,
  주요 태그가 2개 미만이면 부분 점수 태그로 채운다. 실제 887종목 재스캔에서 점수 > 0 인데 태그가 없는 종목 0개.
- **진행률 937 vs 887**: 예전 2-pass 스캔의 `총 + 펀더 보강 50` 이 원인. 이제 전체 재스캔(소유자 전용 `POST /api/trends/scan`,
  워밍업)도 분모 = 실제 유니버스(한국 350 + 미국 537 = 887).
- **배포 직후**: `trend_setups` 표가 비었거나 5일 넘게 오래되면 시작 70초 뒤 40종목씩 한 번 채운다(`TRENDS_SCHEDULER=0` 으로 끔).
  새 표는 `db.create_all()` 로 생성(기존 표 변경 없음).
- 운영에서 볼 것: 로그 `[TRENDS] KR 장 마감 확정 N종목`, `[TRENDS] … 재스캔 끝`. `/screener?tab=trends` 머리말의 패턴 날짜·시세 시각.

### 종목 스크리너 · 나란히 비교 (PR #27)
- `screener_snapshots` 테이블(종목당 1행): 가격·등락률·시총, PER·선행PER·PBR·배당률, RSI·BB 위치·거래량 배수·MA 정배열·추세 점수,
  펀더멘탈(Q·V·G 평균)과 세부 점수, 목표가·상승여력, 52주 고저, 주당 지표(EPS·추정EPS·BPS·주당배당금)
- 채우는 곳 (요청 때 외부 조회 없음)
  - 가격: `sector_map.refresh` 끝에 `screener.upsert_prices` (장중 15분·마감 후). PER 등은 주당 지표 × 최신 가격으로 다시 계산
  - 기술 지표: `bot_engine.run_market` 이 계산한 feats 로 `screener.upsert_technicals` (장 마감 배치)
  - 펀더멘탈: 7일보다 오래된 종목만 6초에 1종목 (한국: 네이버 integration 1회 + 4팩터, 미국: yfinance info + 4팩터). 봇 작업 중이면 쉼
  - 첫 배포: 빈 행 생성 → 기술 지표가 비어 있으면 40종목씩 한 번 채움 → 펀더멘탈은 시총 큰 순으로 약 2시간에 걸쳐 채워짐 (화면에 "펀더멘탈 집계 중 N/887")
  - `SCREENER_SCHEDULER=0` 이면 펀더멘탈 순환·워밍업 끔
- (PR #28) 한국 PER 기준을 종목 상세·스크리너·비교 모두 **최근 4분기(TTM) EPS** 로 통일 (`app.kr_per_basis`, 네이버 integration). TTM 값 자체가 없을 때만 직전 연간 EPS, TTM 적자면 PER 표시 안 함. 상세 PER·PBR 은 화면 현재가 ÷ 주당 지표로 계산. 라벨 `PER (TTM)` (연간 폴백이면 `PER (연간)`)
- (PR #28) 프리셋 강조 1개만(터치 호버·포커스 잔상 제거), 비교 칩은 이름 받기 전 티커 1번만, `/screener?tab=compare` 는 서버가 초기 탭을 그려 깜빡임 없음, 비교 차트 좌우 여백으로 첫 날짜 라벨 안 잘림
- API: `GET /api/screener` (market·preset·sector·min_fundamental·min/max_rsi·max_per·min_upside·sort_by·order·limit, DB 쿼리만),
  `GET /api/screener/meta` (프리셋·섹터·정렬·집계 현황), `GET /api/compare?tickers=` (최대 3, 1년 일봉 10분 메모리 캐시·최대 30종목,
  펀더멘탈이 없거나 오래된 종목만 그 자리에서 1회 받아 스냅샷에 저장)
- 프리셋 5종: 우량주 과매도, 추세 돌파+수급, 저평가 가치주, 당일 모멘텀, AI 봇 최근 신호(7일)
- 화면 `/screener` (상단 메뉴 '스크리너'): 스크리너 탭(시장·프리셋·세부 필터·관심종목·비교 담기·플로팅 비교 바), 비교 탭(자동완성·예시 칩·
  URL `?tab=compare&tickers=` 동기화·3M/6M/1Y 상대 수익률 차트·항목별 비교 표, 화면에 보이는 자릿수 기준 단독 최고값에만 '우위' 배지)
- 로컬 측정: 프리셋 응답 4~76ms, 비교 첫 호출 1.0초·캐시 38ms, 기술 지표 워밍업 +6MB, 펀더멘탈 100종목 후 추가 증가 없음(2.6초/종목)
- 기존 한계: `/api/search` 는 미국 종목의 한글 이름(예: '애플')을 찾지 못함. 영문·티커로 검색
- (PR #29) 한국 0종목 버그: 스크리너 가격은 섹터맵이 '갱신될 때'만 넘어오는데, PR #27 배포가 토요일 새벽이라 한국은 다음 갱신(월요일)까지 가격이 비었다
  (미국은 금요일 마감 갱신이 배포 뒤라 정상). → `screener.backfill_prices()`: 가격이 90% 미만으로 채워진 시장은 저장된 섹터맵 스냅샷으로 채움
  (기준 시각 = 스냅샷 시각). 서버 시작 때와 펀더멘탈 순환이 쉴 때(10분마다) 확인. 그래도 비면 기술 지표 워밍업이 마지막 일봉 종가로 채우고 기준 시각은 그 봉의 마감 시각

### PWA 웹 푸시 (PR #25) + README 최신화
- `push_subscriptions` 테이블(기기별, endpoint 유니크, 사용자당 최대 10대). `webpush.py`: VAPID 확인·구독 저장/해제·발송·404/410 만료 구독 자동 삭제
- 켜짐 조건: `VAPID_PUBLIC_KEY`·`VAPID_PRIVATE_KEY` 가 있고 pywebpush 를 import 할 수 있을 때. 아니면 푸시만 꺼지고 서버·인앱 알림은 정상
- `pywebpush==1.14.1` 고정: 최신 2.5.0 은 aiohttp 를 끌어와 import 시 +25MB, 1.14.1 은 +11MB. 실제로 보낼 때만 import
  (이 클라우드 컨테이너의 시스템 파이썬은 http-ece 빌드가 실패했지만 깨끗한 venv 에서는 정상 설치됨. 운영에서 설치가 실패해도 푸시만 꺼진다)
- `notifier.py`: 인앱에 새로 저장된 알림만 푸시. 한 배치에서 한 사용자에게 4건 이상이면 요약 1건("AI 봇 새 신호 N건 도착 — 탭해서 확인하기" → /ai-bots)
  페이로드: 신규 `[봇] 매수 신호 · 티커` / `신뢰도 · 진입/목표/손절 — 근거`, url `/stock/<티커>`. 청산·취소는 url `/ai-bots#<전략키>`
- API: `GET /api/push/vapid-public-key`, `POST /api/push/subscribe`·`unsubscribe`·`test`. 소유자 진단 `/api/debug/ai` 의 `config.push`
- 서비스워커 `sa-v56`: `push`(icon·badge·tag) · `notificationclick`(열린 창 포커스 후 이동, 없으면 새 창)
  - (PR #26) 안드로이드 상단바 아이콘이 흰 사각형으로 보이던 문제: 상단바는 배지 이미지의 알파(모양)만 쓴다 → 투명 배경 + 흰 모양 `static/icons/badge-96.png`(`scripts/generate_icons.py` 의 `draw_badge`)로 교체, `sa-v57`
- 2026-11-21 운영 적용 완료: 서버 환경변수 파일(서비스가 읽는 EnvironmentFile, 앱 폴더 밖)에 VAPID 3개 추가 → `/api/debug/ai` 에서 push.enabled=true, 안드로이드 테스트 푸시 수신 확인
- 알림 설정 모달에 '모바일/브라우저 푸시' 영역: 켜짐/꺼짐/차단됨/미지원/서버 미설정, 켜기·끄기, 테스트 푸시. iPhone 은 홈 화면에 추가한 앱에서만
- 운영에서 켜려면: 서버에서 `.env.example` 의 한 줄 명령으로 키 생성 → `.env` 에 `VAPID_PUBLIC_KEY`·`VAPID_PRIVATE_KEY`·`VAPID_SUBJECT` → 재시작
- 이 환경에서 확인 못 한 것: 실제 FCM/Mozilla/Apple 푸시 서버로의 전달(헤드리스 크롬은 푸시 서비스가 없음). 암호화·VAPID 서명·만료 처리는 HTTP 만 가짜로 두고 확인
- 같은 PR: `/ai-bots` 차트 축 라벨 함수가 값 없음(null)에서 오류 나던 것 수정. README 를 현재 구조(Oracle VM·DuckDNS·자동 배포·봇·섹터맵·푸시)로 전면 갱신

### 섹터맵 개편 (PR #24) — 전체 유니버스 시총가중 + 상승/하락 분포 + 백그라운드 스냅샷
- 대상: `stock_db.json` 전체(한국 350 / 미국 537). 섹터 분류·미국 발행주식수는 `sector_universe.json`(정적, `build_sector_universe.py` 로 생성)
  - 한국: 네이버 업종 79개 → 대분류 16개(반도체, IT·전자부품, 2차전지·화학 …). 미국: S&P 500 GICS 11개 + 반도체 분리, 나머지 나스닥은 yfinance
  - 자동 분류가 틀린 종목은 스크립트의 `OVERRIDE` 로 손보정. 분기에 한 번쯤 다시 생성 권장(편입·주식수 변화)
- 시세: 한국은 네이버 '시가총액순' 목록 API(100종목씩, 가격·등락률·시총), 미국은 yfinance 5일 일봉 40개 묶음 → 시총 = 현재가 × 발행주식수
- 집계: 섹터 등락률 = Σ(시총×등락률)/Σ시총, 트리맵 면적 = 섹터 시총 비중, 섹터마다 total/advancing/declining/unchanged. 시총 없는 종목은 가중치에서만 빠짐(분포엔 포함), 섹터 전체가 시총 없으면 단순 평균(`basis: simple`)
- 저장: `ai_cache` 의 `sectormap:KR`/`sectormap:US` 한 줄(종목당 6개 필드) + 메모리. `/api/market/sectormap` 은 이 스냅샷만 즉시 반환(로컬 2~3ms). 없으면 `warming: true` 를 주고 백그라운드로 한 번 생성
- 갱신(`sector_map.py` 스레드, `SECTORMAP_SCHEDULER=0` 으로 끔): 장중 15분마다 매시 7·22·37·52분, 마감 후 확정 1회(한국 15:40 · 미국 16:20 현지), 주말·휴장일(받은 데이터 날짜 ≠ 오늘) 스킵. 봇 장중 체크는 매 5분 정각으로 맞춰 겹치지 않게 했고, 봇 작업 중이면 섹터맵이 잠깐 미룸
- 로컬 측정: 한국 갱신 약 26초, 미국 약 44초. 프로세스 메모리 +6MB(한국) / +22MB(미국, 첫 회 pandas·yfinance 로딩 포함), 이후 반복해도 증가 없음. 스냅샷 JSON 한국 49KB · 미국 74KB
- 같은 PR: 시황 카드의 KOSPI·KOSDAQ 을 네이버 값으로. Yahoo 는 장 마감 후 당일 봉 종가가 비어 전날 값(+1.39%)이 오늘처럼 보였다(실제 -5.21%)

### AI 매매 신호 봇 Step 3 (PR #19) — 봇 가상 계좌 · 자동 매매 · /ai-bots
- 봇 계좌: 전략마다 `users` 행 1개(`provider='bot'`, `provider_id`=전략 키, 1억 원). 앱 시작 시 `bot_trader.ensure_accounts()` 가 없으면 만든다. 로그인 불가(OAuth 제공자가 아님)
- 새 테이블 `bot_positions`: 신호 1건 ↔ 봇 계좌 거래. OPEN/CLOSED/SKIPPED(현금·비중·보유 수 한도로 못 삼)
- 모의투자 매수·매도 로직을 `paper_buy()` / `paper_sell()` 공용 함수로 분리. 사용자 화면(수수료 0.1%, 서버 실시간가)과 봇(0.15%, 신호 체결가)이 같은 코드를 쓴다
- 매수 수량 = 봇 총자산 × 종목당 최대 비중 ÷ 체결가(현금 한도 안), 1주 미만이면 SKIPPED
- 장중 스케줄러(5분마다, 한국 09:00~15:30 KST · 미국 09:30~16:00 뉴욕): Yahoo 5분봉으로 대기 신호를 오늘 시가에 체결(B 방식), 보유 신호는 오늘 고가·저가로 손절·트레일링·익절. 시간 청산·추세 이탈·AI 악재는 장 마감 배치에서. Yahoo 장중 시세는 지연될 수 있다
- 체결·청산이 생기면 `bot_trader.sync()` 가 봇 계좌에 매수·매도하고 스냅샷 저장. 장 마감 배치 끝에도 한 번 더 동기화(장중 체크를 놓쳐도 일봉 기준으로 따라잡음). 여러 번 불러도 중복 매매 없음
- 하루 손실 한도: 근사치 대신 실제 봇 계좌 총자산을 직전 날짜 스냅샷과 비교 (`bot_trader.day_change_pct`)
- 같은 봇이 이미 대기·보유 중인 종목엔 새 신호를 내지 않음
- 전략 파라미터: 앱 시작 시 기본값에 새로 생긴 키만 DB 에 채운다(기존 값은 유지). PR #18의 균형형 목표 2.5R(`target_rr`)이 운영 DB 에 반영되지 않던 문제를 이걸로 해결
- 랭킹: 봇은 첫 거래 전에도 표시, `[AI BOT]` 배지, `?bots=0` 이면 유저만. 화면에 'AI 봇 포함 / 유저만 보기' 토글(선택은 브라우저에 기억)
- `/ai-bots` 페이지(상단 메뉴 'AI 봇'): 다음 배치 시각, 봇 카드 4개(수익률·승률·손익비·MDD), 봇 4 + KOSPI·S&P 500 수익률 비교 차트(7D/30D/90D/전체, 기간 시작 0%), AI 검증 효과(균형형 vs 기준선), 봇 상세(보유 포지션, 신호 사후 검증 로그, 진입 근거 모달), 면책 문구
- API: `/api/ai-bots/overview`, `/api/ai-bots/chart?period=`, `/api/ai-bots/<전략키>`
- 손익비 = 평균 수익률 ÷ 평균 손실률(청산 거래 기준), MDD = 봇 일별 자산 스냅샷 기준
- 같은 PR: 관심종목 카드에 현재가·등락률(`/api/watchlist/quotes`), ✕ 문자 대신 SVG 아이콘, 알림 설정 모달 하단 닫기 버튼, 원화 매입가 정수 표시, 한국 종목 선행PER을 네이버 컨센서스(기준 연도 표기)로, 상세 하단 애널리스트·뉴스 높이 맞춤, 430px 이하 로고 아이콘만
- 같은 PR에서 고친 기존 문제: 네이버 실시간 시세 응답 형식이 바뀌어(`result.areas[].datas` → `datas`, `closePrice` 등) 파서가 늘 None 을 돌려주던 것. 한국 종목 현재가가 다시 네이버 기준으로 나온다. 장 마감 뒤에는 시간외(NXT) 가격이 섞일 수 있어 Yahoo 종가와 조금 다를 수 있음
- 로컬 검증 스크립트는 저장소 밖에 있다. 돌리기 전 `instance/portfolio.db` 백업 필수
- (PR #20) 봇 상세를 탭 3개로 정리: 보유 포지션 / 매매 이력 / 신호 사후 검증 (건수 배지, 마지막 탭은 브라우저에 기억, 좌우 화살표 키 이동)
- 매매 이력 API `/api/ai-bots/<전략키>/trades?side=all|buy|sell&page=&per=` — 봇 계좌의 `transactions` 를 최신순으로. 거래 메모의 `신호 #id` 로 신호·포지션을 찾아 매수는 근거 버튼, 매도는 청산 사유와 수수료 반영 손익(`bot_positions` 기준)을 붙인다. 화면은 전체/매수/매도 필터와 20건씩 더보기, 좁은 화면에서는 거래별 카드
- 장중 청산 때 보유 거래일(`holding_days`)이 갱신되지 않던 문제 수정 (평일 수로 계산, 공휴일 미반영)

### AI 매매 신호 봇 Step 2 (PR #18) — 관심종목 · 신호 알림
- 새 테이블 2개: `watchlist_items`(사용자·종목 유니크, 최대 100개), `notifications`(사용자·신호·종류 유니크라 중복 저장 없음, 60일 지나면 삭제)
- `notifier.py`: Provider 구조. 지금은 사이트 안 알림(`InAppProvider`)만 있고, 웹 푸시·이메일 등은 `NotifyProvider.send()` 를 구현해 `PROVIDERS` 에 등록한 뒤 `NOTIFY_PROVIDERS` 환경변수로 켠다
- 발송 규칙: 새 매수 신호는 구독 조건(봇 여러 개, 범위 전체/보유(실제+모의투자)/관심종목, 최소 신뢰도)에 맞는 사용자에게. 청산·취소 알림은 그 신호의 매수 알림을 받은 사용자에게만
- 봇 배치(`bot_engine.run_market`)가 끝나면 자동 발송. 알림 실패는 배치 결과에 영향 없음
- API: `/api/watchlist`(GET·POST, DELETE `/<ticker>`), `/api/signal-subscription`(GET·PUT), `/api/notifications`(`?limit=0` 이면 안 읽은 개수만), `/api/notifications/read`(POST `{"ids":[..]}` 또는 `{"all":true}`)
- 화면: 헤더 종(`templates/_auth_area.html` 의 `.header-right`, 로그인 시 서버가 그림) + 드롭다운 + 알림 설정 모달(`static/js/notify.js`, 모든 페이지 공통). 종목 화면 ☆ 버튼, 메인 '관심종목' 탭. 안 읽은 개수는 화면이 보일 때만 60초마다 갱신
- 430px 이하에서는 헤더가 넘치지 않게 프로필 아바타를 숨긴다
- 같은 PR에서 고친 Step 1 문제: `dry_run` 인데 신호 추적 결과가 저장되던 것
- 주의: app.py 에 이미 `_stock_name(ticker, market)`(섹터맵용)이 있다. 비슷한 이름의 함수를 새로 만들면 덮어써서 다른 기능이 깨진다 (이번에 한 번 겪음)

### AI 매매 신호 봇 Step 1 (PR #17) — 신호 생성 파이프라인
- 새 테이블 4개 (`db.create_all()` 이 생성, 기존 테이블 변경 없음): `bot_strategies`, `ai_stock_assessments`, `ai_signals`, `user_signal_subscriptions`(Step 2에서 사용)
- `bot_engine.py`: 장 마감 후 시장별 하루 1회 배치 (한국 평일 15:45 KST 이후, 미국 평일 16:30 뉴욕시각 이후). 60초마다 확인하는 백그라운드 스레드, `ai_cache` 테이블의 `bot_lock:{시장}:{날짜}` 키로 하루 한 번만 실행
- 흐름: 일봉 1년치 묶음 다운로드 → 기존 신호 추적 → 공통 필터(거래량 평균 30% 미만, 미국 실적 D-3, 봇·종목 24시간 쿨다운) → 봇별 정량 1차 → 상위 40종목만 펀더멘탈 → AI 평가(종목당 하루 1회, 모든 봇 공유) → 봇별 판정 → 손익비 미달 기각 → 하루 제한 → `ai_signals` 저장
- 봇 4개: 공격형 모멘텀 / 균형형 스윙 / 안정형 가치 / 기준선 정량(균형형과 같은 규칙, AI 없음). 파라미터는 `bot_strategies.params_json` (앱 시작 시 없을 때만 기본값을 넣고, 있으면 건드리지 않음)
- 체결 방식 B: 신호 다음 장 시가로 체결. 시가 ≤ 손절가이거나 갭 상승으로 손익비가 기준 미만이면 `CANCELLED`
- 청산: 일봉 고가·저가로 손절/목표/트레일링, 같은 봉에서 둘 다 닿으면 손절 우선, 시간 청산, 균형형 추세 붕괴, AI 치명적 악재. 수익률은 매수·매도 수수료 0.15%씩 반영
- 하루 제한: 신규 매수 공격형·균형형·기준선 3건, 안정형 2건 (두 시장 합산, 시장별 배분은 아래 보완 참고). 하루 손실 한도(-3%/-3%/-2%/-3%)는 "비중 × 오늘 등락" 근사치로 판정 → Step 3에서 봇 계좌가 생기면 실제 자산 변화로 교체
- AI 예산: `AI_DAILY_BUDGET`(기본 60, 한국 날짜 기준). 보유 중인 AI 봇 종목을 먼저 평가하고, 남는 예산으로 후보를 평가. 예산이 없으면 AI 봇은 그 종목을 "AI 평가 없음"으로 기각 (기준선은 영향 없음)
- 보완(PR #18): AI 호출 사이 최소 4초(`BOT_AI_INTERVAL`) → 60회면 약 4분. 하루 신규 상한은 한국이 '전체-1'까지만 써서 미국 몫 최소 1건 보장 (3건 → 한국 2·미국 1~3, 2건 → 한국 1·미국 1~2, `params.risk.market_max_new`로 변경 가능). 안정형 목표가 근거는 애널리스트 목표가 + DCF(신뢰도 '낮음'은 제외). Yahoo 401로 둘 다 없으면 '상승 여력 확인 불가'로 기각하고, 빠진 값은 근거의 `missing`에 기록
- 한국 종목 실적 D-3 필터는 출처가 없어 생략 (`/api/bots/signals` 응답 `kr_earnings_filter: "skipped"`)
- 휴장일이나 장중 수동 실행처럼 오늘 완성된 봉이 없으면 추적만 하고 신규 신호는 만들지 않음
- API: `/api/bots/signals`(목록), `/api/bots/signals/<id>`(근거·AI 평가 포함), `/api/bots/runs`(배치 요약: 후보 수, 기각 사유, AI 호출 수), `/api/admin/bots/run`(소유자 전용 수동 실행, POST `{"market":"KR"|"US","dry_run":false}`)
- 운영 확인할 것: 첫 배치 뒤 서버 로그의 `[BOT] run ...` 줄(소요 시간, AI 호출 수), 메모리 사용량. 미국 537종목 다운로드는 40개씩 묶어서 받는다
- 로컬 검증 스크립트(가짜 시세·AI로 31개 항목)는 저장소에 넣지 않았다. 로컬 DB를 쓰므로 돌리기 전에 `instance/portfolio.db` 를 백업할 것

### 운영 판단 기준 변경 (PR #7)
- `IS_PRODUCTION = bool(DATABASE_URL or RAILWAY_ENVIRONMENT)` — 예전엔 `RAILWAY_ENVIRONMENT`만 봐서 Oracle에서 보호가 꺼져 있었다.
- 시크릿 키 없을 때 **기동을 막지 않는다** (막으면 자동 롤백되므로). 대신 임시 랜덤 키.

### 데이터 출처 변경 (외부 서비스가 바뀌어 조용히 빈 값이 되던 것들)
| 데이터 | 원인 | 현재 |
|---|---|---|
| 한국 분기 실적 (`fetch_kr_quarterly_data`) | 네이버 `.../finance/quarterly` 404 | `.../finance/quarter`(단수). 성공 시 폴백 5개 생략 (7s → 0.9s) |
| 한국 애널리스트 (`fetch_kr_analysts`) | `finance.naver.com/research/*` → `stock.naver.com` 리다이렉트(CSR) | 모바일 JSON API `m.stock.naver.com/api/research/stock/{code}` + `/api/research/company/{id}` |
| 한국 뉴스 | yfinance `Ticker.news`가 Yahoo `/xhr/ncp` 폐기로 404 | 네이버 `m.stock.naver.com/api/news/stock/{code}` (`fetch_kr_news`) |
| 미국 뉴스 | 같은 원인 | **yfinance 1.3.0 → 1.8.0** (GraphQL 엔드포인트). 1.3.0과 분석 응답 비교해 동일 확인 |
- 네이버 모바일 API는 비공식이라 언제든 바뀔 수 있다. 빈 결과는 짧게(2~10분)만 캐시하므로 주소만 고치면 바로 회복.
- 공통 메모리 캐시 헬퍼 `_ttl_cached()` (빈 결과 짧은 TTL, deepcopy, lock, 개수 상한).

### 로딩 속도 (PR #2, #8, #10)
- `/api/analyze` 응답의 99%가 전체 기간 차트였다 (AAPL 4.8MB). 차트 실수 소수 4자리(`_cf`), 거래량 정수(`_cv`),
  32KB 넘는 JSON gzip(`_gzip_large_json`, 표준 라이브러리) → AAPL 전송 0.62MB.
- 분석 본문 5분 캐시 `_ANALYZE_CACHE` (종목·봉 단위, 직렬화 JSON, 총 40MB 상한). **내 포지션은 `_position_for()`로 요청마다 계산**해 캐시에 사용자 정보가 섞이지 않음.
- 뉴스·애널리스트·AI 기업 소개·AI 급등락 사유는 **`/api/analyze/extra`** 로 분리 (analyze 캐시의 stock_data/info/ai_result 재사용, 5분 캐시).
  화면은 스켈레톤 → 채움, 실패 시 재시도 버튼. 본 분석은 90초 타임아웃 + 재시도 버튼.
- 측정(이 컨테이너, 한국 사이트 TLS가 요청당 ~0.9s라 운영보다 느림): 삼성전자 첫 요청 21.8s → 약 13~15s, 재요청 0.07s.

### 데이터·로직 버그 수정 (PR #1, #4, #7)
- 배당수익률 100배 오류(945%) — yfinance `dividendYield`는 이미 % 값. `_dividend_yield_fraction()`로 상단 지표와 같은 순서.
- BB·거래량 "쉬운 설명"을 판단 색이 아니라 실제 상태(`tip_key`)로 선택. 장중 거래량 집계 중 안내.
- 분기 매출 추세 배지: 발표 전 분기(actual null) 제외.
- 손절가 문구를 실제 계산식(최근 저점 + ATR)에 맞춤. 판단이 `avoid`일 때 매수 구간·가이드 문구를 참고용으로 표시.
- 분석 문구 가격: 원화 정수, 그 외 소수 2자리 (`_fp`, 스레드별 자릿수). EPS 7일 하향 열 이름 오타(`downLast7Days`).
- 한국 종목명은 `stock_db.json` 한글명, 섹터/거래소 한글 표기. 차트 가격축 통화별 포맷.

### 검색·모의투자·포트폴리오 UX
- 한글 종목명 + Enter → 검색 API로 티커 변환. 서버도 `/stock/<이름>` → `/stock/<티커>` 302.
- 종목 페이지 검색창엔 종목명 표시, 실제 티커는 `data-ticker` (`_inputTicker()`가 analyze/차트 주기 전환에 사용).
- 빠른 매수: 정확한 티커/이름이면 바로 매수 모달, Enter, 인라인 오류. 입력을 고치면 이전 선택 해제(다른 종목으로 열리던 버그).
- 닉네임 "나중에" 7일 기억(localStorage). 배지는 요약 바 + "전체 보기" (모바일 페이지 길이 절반).
- `/api/portfolio?summary=1` → 원화 환산 요약(총평가·투자금·손익·통화/종목 비중). 파라미터 없으면 기존 목록 그대로. 목록 내부 스크롤 제거.

### UI (PR #3, #9, #10)
- Pretendard(CDN, 시스템 글꼴 폴백), 숫자 tabular-nums, 카드/탭/목록 정리. **다크 테마·상승 초록/하락 빨강 유지는 사용자 결정**.
- `style.css` 끝의 "UI 정리 (v55)" 블록이 앞 규칙을 덮는다. **모바일 값은 그 뒤에 다시 선언해야 한다** (헤더 로그아웃 버튼이 밀린 적 있음).
- 헤더 로그인 영역은 `templates/_auth_area.html`로 서버 렌더(깜빡임 제거), 공통 푸터 `templates/_footer.html`(출처·지연·면책).
- RSI/MACD 범례, 시간축 `rightOffset`, 등락률 `fmtPct()` 통일, 닫기/삭제/새로고침 아이콘 SVG.
- 국내 종목 AI 기업 소개 프롬프트는 해외 기업 비유 금지로 분기.
- 정적 파일 버전: `style.css?v=61`, `main.js?v=61`, `trading.js?v=57`, `leaderboard.js?v=54`.

### 이 세션의 로컬 검증 방법 (재사용 가능)
- 로그인 흉내: 로컬 SQLite에 테스트 사용자 생성 → `app.test_client().session_transaction()`으로 `_user_id` 넣고
  나온 `session` 쿠키를 Playwright 컨텍스트에 주입. **로컬 개발 키에서만 동작** (운영 판단이면 위조 차단됨).
- 클라우드 환경 네트워크 허용 목록에 Yahoo/네이버/jsdelivr 등이 필요하다 (막히면 조용히 빈 데이터가 됨).

### 남은 후보
- 종목 페이지 차트를 전체 기간 대신 최근 N년만 보내고 필요할 때 더 불러오기 (전송량 추가 감소).
- 로컬 SQLite에서 `ALTER TABLE ... IF NOT EXISTS` 마이그레이션 로그 노이즈 (기능 영향 없음).
- 상단 메뉴 "포트폴리오" 바로가기 (모바일 헤더 공간 부족으로 보류).

---

## 🚀 현재 상태 (2026-07-25)

- **Railway 정상 동작** (HOBBY 플랜, 배포 정상)
- 최신 커밋: `89fac4b` (밸류에이션 탭 편입 + 모바일 검색 수정) / SW **`sa-v53`** / HTML `?v=53`
- **토스증권 Open API 운영 동작 확인 완료** ✅ (시세/차트/환율 + 계좌 import + 고정 IP 프록시)
- **AI 전부 Gemini 우선** — 코치/점검/기업분석/급등이유/DCF해설 모두 `_ai_chat()` 경유
- **모의투자 신뢰성**: 매수/매도 체결가는 서버 실시간가로 강제 (수익률 조작 차단)
- **🆕 페이지 구조**: 홈(/) = 시황 대시보드(지수/섹터맵/급등락), 분석 = **`/stock/<ticker>` 별도 페이지**
- **🆕 DCF 밸류에이션**: `analysis/valuation.py` — 역산 성장률/이익률 중심 표시 + AI 해설

### ✅ 최신 세션 핵심 (2026-07-25, Opus)
- **상세분석 탭 재구성** (`89fac4b`): 독립 DCF 카드가 너무 크게 튀어나와 있어
  탭으로 편입. [기술적 분석(+매수/매도 구간 통합)] [투자 판단] [💰 밸류에이션].
  DCF 산출 불가 종목은 탭 버튼 자체를 숨기고, 분석할 때마다 기술적 탭으로 초기화.
- **모바일 검색창 찌그러짐 수정** (`89fac4b`): 480px 쿼리의 옛 `.search-btn{width:100%}`가
  살아있어 버튼이 폭 독식 → 입력창 압착. 버튼 `width:auto/flex:0 0 auto`,
  입력 래퍼 `flex:1 1 auto + min-width:0`으로 해결.
- **DCF 밸류에이션 카드** (`5f001e2`, `5da2506`): anthropics/financial-services의
  dcf-model 스킬(Apache-2.0) 방법론을 `analysis/valuation.py`로 이식.
  숫자는 Python 결정적 계산, AI는 해설만(`/api/dcf/comment`, 6h 캐시).
  검증 중 실버그 4개 수정(운전자본 부호/수준비례, Blume 베타, 컨센서스 블렌딩).
  UI는 단일 적정주가 대신 **역산**("현재가가 반영 중인 성장률/이익률") 중심,
  둘 다 산출 불가면 beyond_model("재무 범위 밖") 안내. 신뢰도 뱃지 3단계.
- **홈 시황 대시보드 + /stock/<ticker> 분리** (`571cd41`): 시장현황 지수(1분 갱신,
  장중 상태 점), 급등락 Top5(대표주 55개). analyze() 한 곳 게이트로 모든 진입점이
  전용 페이지로 이동. body.stock-mode로 홈 요소 숨김. /?ticker= 하위호환.
- **실전 섹터맵(핀비즈 스타일)** (`1bbad2d`): GICS/네이버테마 대신 매매 단위
  큐레이션 그룹(한/미 각 12개, `_SECTORMAP_KR/US`). 타일 크기=시총 근사(weight),
  색=등락률 7단계. `/api/market/sectormap` 10분 캐시. KR 44개 티커 실측 OK.
- **대시보드 통합 토글 + 홈 재배치** (`1bbad2d`): 섹터맵·급등락 시장 토글 하나로
  통합, 기본값=장 열린 시장(KR장중→KR, US장중→US, 마감 시 KST 06~17시→KR).
  홈 순서: 검색→시황→추세감지/포트폴리오 (JS DOM 재배치).
- **UX 통일** (`6f30be7`, `60d76ad`): 섹터 강도 탭 제거(섹터맵으로 대체, API는 유지),
  섹터맵 클릭→구성종목 **모달**(sectorModal), 탭 섹션·검색 영역을 시황과 같은
  max-width 1200 카드 + 알약 탭 톤으로 리디자인.

### ✅ 직전 세션 핵심 (2026-06-12, Opus)
- **모의매매 체결가 서버 강제** (`616739a`): `trading_buy/sell`이 클라 price 무시,
  `_fetch_current_price`로 서버 실시간가 체결. `/api/realtime-price` 신설, 프론트 가격칸 readonly+1초 폴링
- **분석 페이지 모의매수 메모칸** 추가 (`616739a`)
- **랭킹 실시간 평가** (`94be6ed`): `_realtime_total_map`으로 전원 보유종목 현재가 재평가, 90초 캐시
- **분석 헤더 현재가 1초 갱신** (`882c207`): 장중 현재가·등락 폴링
- **보유종목 토스 스타일 토글** (`c9a24f1`): 평가금/현재가 · 원/$ (exchange_rate 환산)
- **토글 active 버그 수정** (`75f25c0`): `loadAssetHistory`가 전역 `.ah-period-btn` active를
  지워 보유종목/리뷰 토글 초기선택이 안 보이던 문제 → `.asset-history-card`로 범위 한정
- **랭킹: 초기화 횟수 배지 🔄 + 빈 포트폴리오(1억 그대로) 제외** (`dcb475a`):
  `User.reset_count` 컬럼 + 안기명 2회 보정(마이그레이션 멱등)
- **재미 배지 10종** (`da8e7c0`) → **기존 카테고리로 분배** (`b916a5a`):
  활동/트레이딩/포트폴리오/성과로 분산, 스타일 카테고리 제거
- **분석: 고점 대비 현재가(drawdown) 위젯** (`8aeb12c`): 주요지표 상단 1주/1개월/전체 3칼럼

### ✅ 직전 세션 핵심 (2026-06-09~12)
- **보안/안정성 4종**: `/api/debug/toss` 오너 게이팅(비오너 404), import-toss 빈 응답 가드,
  `FLASK_SECRET_KEY` 운영 fail-fast, `requests==2.33.1` 고정
- **포지션 노트**: Transaction.note(300자) + 매수/매도 모달 메모칸 + 보유카드/거래내역 표시
- **AI 매매 코치**: FIFO 통계 선계산(`_coach_stats_block`) → 프롬프트 주입, 10분 쿨다운+캐시
- **AI 포트폴리오 점검**: 모의/실제 토글, 비중·집중도·통화 분산 분석
- **Gemini 전환**: `_gemini_chat()` + `_ai_chat()` (Gemini→Groq 폴백). 전 AI 기능 적용
- **README.md 전면 갱신** (`819af22`) — Railway/Gemini/신규 기능 모두 반영됨

### ✅ 고정 IP 프록시 완성 (2026-06-08 세션)
- **Oracle Cloud 무료 VM**: 리전 South Korea North(Chuncheon), Ubuntu 22.04,
  Shape `VM.Standard.E2.1.Micro` (Always Free)
- **VM 공인 IP: `<ORACLE_VM_IP>`** (Ephemeral — 재부팅 유지하려면 Reserved 전환 권장)
- **tinyproxy 가동 중** (HTTP/HTTPS CONNECT 모두 검증 완료, egress IP = VM IP 확인)
  - 인증: BasicAuth — **실제 자격증명은 로컬 `.env`의 `TOSS_PROXY_URL` 참조** (공개 저장소라 여기 기록 금지. ⚠️ 비밀번호는 영숫자만 — tinyproxy가 특수문자 파싱 불가)
  - 포트 <PROXY_PORT> (Oracle Security List + VM iptables 둘 다 오픈)
- **토스 콘솔에 `<ORACLE_VM_IP>` 등록 완료**
- **Railway 환경변수**: `TOSS_PROXY_URL` — 값은 로컬 `.env`와 동일하게 설정
  → 설정 시 토스가 항상 VM IP로 봐서 **재배포해도 안 끊김**
- 🔒 2026-06-08: 초기 비밀번호가 본 문서에 평문 커밋되어 공개 노출됨 → **즉시 교체(rotation) 완료**.
  구 비밀번호는 무효. git 히스토리의 옛 값은 더 이상 유효하지 않음
- SSH 키: `<SSH_KEY_FILE>` (프로젝트 폴더, **.gitignore 처리됨 — 커밋 금지**)
  - 접속: `ssh -i <SSH_KEY_FILE> ubuntu@<ORACLE_VM_IP>`
- 설치 시 겪은 함정 4가지 → `TOSS_PROXY_SETUP.md`에 모두 반영

### ✅ 토스 핵심 기능 모두 동작 확인됨
- 시세/차트/환율: `chart_source:"toss"`, `realtime_source:"toss"` 확인
- **토스 계좌 → 실제 포트폴리오 import 버튼 성공** (10종목)
- 권한 게이팅: `TOSS_OWNER_USER_IDS=<OWNER_USER_IDS>` (카카오 id1 + 구글 id2 = 둘 다 <OWNER>)

### ⚠️ 토스 IP 허용목록 — (프록시로 해결됨)
- 토스 `live` API는 **콘솔 등록 IP에서만** 토큰 발급됨
- 과거: Railway egress IP가 재배포마다 바뀌어 매번 재등록 필요했음
- **현재: 고정 IP 프록시(`TOSS_PROXY_URL`)로 영구 해결** ✅
- IPv6 우회: `toss_api.py`에서 `urllib3 HAS_IPV6=False`로 IPv4 강제 (유지)
- 진단: `/api/debug/toss` → `proxy_egress_ip: <ORACLE_VM_IP>`, `toss_token_ok: true` 확인

### 다음 우선 작업 후보 (사용자 결정 대기)
- [ ] VM 공인 IP를 **Reserved(예약)로 전환** — 재부팅 시 IP 유지 (현재 Ephemeral)
- [ ] Railway Variables의 `TOSS_PROXY_UR` (오타, L 빠진 변수) 삭제
- [ ] **DART API 임원 매매 연동** — 스코어카드 "DART 공시 보러가기 ↗" 자리 대체
- [ ] **랭킹 시즌제** (월간/분기 리셋 + 시즌 배지) / **백테스트** / **자동 손절**
- [x] ~~AI 코치~~ / ~~포지션 노트~~ — 2026-06-09~11 완료

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
- **AI**: Gemini `gemini-2.5-flash`(우선) → Groq `llama-3.3-70b-versatile`(폴백) — `_ai_chat()` 공용
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
│  ├─ valuation.py           # NEW — DCF 밸류에이션 (역산 성장률/이익률, 민감도)
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
  - `reset_count` Integer, default 0 (모의투자 1억 초기화 횟수, 2026-06-12 추가)
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
- `GET /api/trading/dashboard` — 총자산, 현금, 포지션(시가평가), 미실현/실현 손익, 통계, `exchange_rate`
- `POST /api/trading/buy` — body: `{ticker, name, quantity, note?}` — ⚠️ price는 서버 실시간가 강제(미전송)
- `POST /api/trading/sell` — body: `{ticker, quantity, note?}` — ⚠️ price 서버 강제, reset_count 미반환
- `GET /api/realtime-price?ticker=` — 단일 종목 실시간 단가(모달/헤더 1초 폴링용, 서버 3초 캐시)
- `GET /api/trading/transactions` — 거래 내역
- `POST /api/trading/reset` — 전체 초기화 + 1억원 재지급 (`reset_count` +1)

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

### 페이지 / 시황 대시보드 / DCF (NEW — 2026-07-25)
- `GET /` — 홈 = 시황 대시보드 (검색→시황→추세감지/포트폴리오 탭, JS DOM 재배치)
- `GET /stock/<ticker>` — 분석 전용 페이지 (같은 index.html, JS가 URL 경로로 모드 전환)
- `GET /api/market/overview` — 주요 지수 6종 + kr_open/us_open (1분 캐시)
- `GET /api/market/movers` — 대표주 55개 급등/급락 Top5 (KR/US, 10분 캐시)
- `GET /api/market/sectormap?market=KR|US` — 실전 섹터맵 12그룹+구성종목 (10분 캐시)
- `GET /api/dcf/comment?ticker=` — DCF AI 해설 (종목별 6h 캐시)
- analyze 응답에 `stock.dcf` 포함 (`compute_dcf` — analysis/valuation.py)
- 헬퍼: `_get_financials`(재무3표 6h 캐시), `_get_risk_free_rate`(^TNX 12h, KR은 None),
  `_SECTORMAP_KR/US`(큐레이션 그룹), `_stock_name`

---

## 5. 최신 작업 로그 (최신 → 과거 순)

### 📌 2026-07-25 (저녁) — 상세분석 탭 재구성 + 모바일 검색 수정 (`89fac4b`)

**1) 밸류에이션을 상세분석 탭으로 편입**
- 문제: DCF가 독립 `<section class="card dcf-card">`로 분석 화면 중간에 통째로
  튀어나와 비중이 과했음.
- 탭 재구성: [📊 기술적 분석] [📈 투자 판단] [💰 밸류에이션]
  - 기술적 분석 = 기존 지표(`#detailList`) + 매수/매도 구간(`#zonesList`) 통합.
    `#technicalContainer`로 감싸고 구간 앞에 `.detail-subhead` 소제목·구분선.
  - 비워진 3번째 자리에 `#valuationPanel` → 그 안에 기존 `#dcfCard` 이동.
  - DCF는 상세분석 카드 내부이므로 자체 배경/테두리/패딩·카드 제목 제거
    (`#valuationPanel .dcf-card{background:transparent;border:none;padding:0}`),
    탭 라벨이 제목 역할. 신뢰도 뱃지만 우측 정렬로 유지.
- **탭 조건부 노출**: 종목마다 DCF 산출 가능 여부가 다름(적자기업 등 None) →
  `#valuationTabBtn`을 `renderDcf`에서 show/hide. 또 매 분석마다
  `switchDetailTab('technical')`로 초기화(이전 종목의 밸류 탭이 남지 않게).
- `switchDetailTab`은 technical/fundamental/valuation 3개 컨테이너 토글로 변경
  (`data-tab="zones"` 제거됨).

**2) 모바일 검색창 찌그러짐 (480px 이하)**
- 원인: 480px 미디어쿼리에 **옛 세로배치용** `.search-btn{width:100%}`가 남아있는데,
  카드형 개편 때 나중 쿼리에서 `flex-direction:row`만 되돌림 → 버튼이 100% 폭을
  요구해 입력창이 거의 0으로 압착됨.
- 수정: 480px 쿼리를 row 유지로 정리 + `.search-btn{width:auto; flex:0 0 auto}`,
  `.search-input-wrap{flex:1 1 auto; min-width:0}` (640px·480px 양쪽 쿼리 모두).
  카드형 전환 후 남아있던 모바일 `.search-title` 20px → 14px 정리.
- 교훈: 레이아웃 방향을 바꿀 땐 **같은 브레이크포인트의 옛 규칙(width/flex)도
  함께 정리**할 것. `flex-direction`만 되돌리면 잔여 규칙이 살아남음.

### 📌 2026-07-25 — DCF 밸류에이션 + 홈 시황 대시보드/분석 페이지 분리 (Opus 세션)

**1) DCF 밸류에이션** (`5f001e2` → `5da2506`)
- 출처: anthropics/financial-services 레포의 dcf-model 스킬(Apache-2.0).
  핀비즈/기관용 Excel 산출물 대신 계산 절차만 `analysis/valuation.py`로 이식.
- 원칙: AI 코치의 `_coach_stats_block` 패턴 — 숫자는 Python 결정적 계산, AI는 해석만.
- 절차: 과거재무 → 컨센서스 블렌딩 성장 3시나리오(bear/base/bull, 고성장은 10년 fade)
  → EBIT→NOPAT→FCF → CAPM WACC(Blume 베타조정) → mid-year 할인 → 영구성장 터미널
  → EV−순부채 → 주당가치 + 민감도 5×5.
- **검증 중 잡은 실버그 4개** (전 종목 -60~98% 고평가로 나오던 원인):
  ① 운전자본 부호 이중 반영(AAPL FCF 138B 과대→87.8B, 실제 98.8B)
  ② 운전자본을 매출 '수준'에 비례→'증가분' 비례로(wc_per_drev)
  ③ 원본 베타 사용→Blume 조정(0.67β+0.33, NVDA 2.21→1.81)
  ④ 과거 CAGR만 사용→컨센서스(info.revenueGrowth) 60% 블렌딩(삼성 -67%→-38.5%)
- **UI 설계 — 역산이 주인공**: 과거재무 DCF는 고배수 성장주를 구조적으로 낮게
  평가하므로 적정주가 단정 대신 3단계 표시:
  역산 성장률(마진 고정) → 없으면 역산 영업이익률(성장 고정, TSLA 5.9%→66%)
  → 둘 다 없으면 beyond_model "재무 범위 밖"(AMD: 성장60%·마진70%로도 도달불가
  = 시장이 재무제표 밖 요인 반영 중이라는 신호로 안내).
- 현재가/DCF적정가(범위)/애널목표가 3개 기준점 + 신뢰도 뱃지(high/medium/low)
  + 경고 + 가정 접이식. `GET /api/dcf/comment` AI 해설(종목별 6h 캐시, 비차단 로드).
- 재무제표 `_get_financials` 6h 캐시, DCF 계산은 매 요청 실시간가로(역산값 최신 유지).
  무위험수익률: US=^TNX 12h 캐시, KR=yfinance 미제공→상수 3.2% (가정 UI 노출).
- ⚠️ 마진을 고정하는 이유(사용자 질문): 마진 확대는 DCF에서 가장 남용되는 가정
  → 가정하지 않고 역산해서 드러내는 방향. HANDOFF에 기록해둠.

**2) 홈 시황 대시보드 + /stock/<ticker> 분리** (`571cd41`)
- 홈(/)이 검색 전 텅 비던 구조 → 시황 대시보드로 개편.
- `GET /api/market/overview`: ^KS11/^KQ11/KRW=X/^GSPC/^IXIC/^VIX 배치(1분 캐시)
  + kr_open/us_open. `GET /api/market/movers`: 대표주 55개 Top5 급등/급락(10분 캐시).
- `GET /stock/<ticker>` 라우트 = 같은 index.html, JS가 `PAGE_TICKER`(URL 경로)로
  홈/분석 모드 전환. **analyze() 한 곳 게이트**: 홈이면 gotoStock() 이동,
  분석 페이지에서 다른 종목 검색해도 URL 이동 → 모든 진입점 자동 커버.
- body.stock-mode CSS로 홈 요소(대시보드/포트폴리오탭) 숨김 — 비동기 로드 후에도 유지.
- 구형 /?ticker=X → /stock/X 리다이렉트.

**3) 실전 섹터맵 (핀비즈 스타일)** (`1bbad2d`)
- 사용자 피드백: GICS 11개(미국)·네이버 테마 200개(한국)는 실전 매매와 안 맞음.
  핀비즈는 공식 임베드 미제공(iframe 차단) → 동일 문법 자체 구현.
- `_SECTORMAP_US/KR`: 매매 단위 큐레이션 12그룹씩(빅테크/반도체/AI소프트웨어/
  방산/원자력/크립토... · 반도체/2차전지/방산/조선/바이오/엔터...), 대표주 구성.
- `GET /api/market/sectormap`: yf.download 배치 1회, 그룹 등락=구성종목 단순평균,
  10분 캐시. weight(시총 근사 1~10) → 타일 크기(hm-xl/lg/md/sm), 색=등락률 7단계.
  큰 타일엔 대표종목 미리보기. KR 44개 티커 전부 실측 확인.
- 시장 토글 통합(setDashMarket): 섹터맵+급등락 동시 전환. 기본값=열린 시장,
  둘 다 마감이면 KST 06~17시→KR, 그 외→US. 사용자가 토글하면 유지.

**4) UX 통일** (`6f30be7`, `60d76ad`)
- 섹터 강도 탭 제거(섹터맵이 대체). JS는 기존 가드로 안전, `/api/sectors/strength`
  API·함수는 유지(구성종목 API는 사용 중이지 않지만 보존).
- 섹터맵 클릭 → 인라인 드릴다운 대신 **모달**(#sectorModal): 이름/현재가/등락 3열,
  응답에 members 포함이라 추가 요청 없음.
- 추세감지/내포트폴리오 섹션: 풀폭 밑줄 탭 → 시황과 같은 1200px 카드+알약 탭.
- 검색 영역: 풀폭 그라데이션 히어로 → 같은 톤 카드(제목 "🔍 종목 검색" 인라인).
- 홈 순서(JS DOM 재배치): 검색 → 시황 → 추세감지/포트폴리오 탭.

### 📌 2026-06-12 (저녁) — 모의매매 조작차단 + 랭킹 실시간평가 + UI 다수 (Opus 세션)

**1) 모의매매 체결가 서버 강제 (수익률 조작 차단)** (`616739a`)
- 문제: `trading_buy/sell`이 클라이언트가 보낸 `price`를 그대로 신뢰 → API 직접 호출로
  임의 가격 체결 가능(수익률 조작). 프론트 가격칸도 자유 편집됨.
- 해결: 백엔드가 클라 price 무시, `_fetch_current_price(ticker)`로 **서버 실시간가 체결**.
  실패 시 503. body에서 price 미전송.
- `_fetch_current_price` 강화: 토스 → 네이버(한국) → yfinance 순 + **3초 캐시**
  (`_fetch_current_price_uncached` 분리). 토스가 한·미 모두 1순위(폴백은 보조).
- `GET /api/realtime-price?ticker=` 신설 (모달/헤더 1초 폴링용).
- 프론트(trading.js/main.js): 가격칸 `readonly`+`🔴실시간` 라벨, 모달 열림 동안 1초 폴링,
  닫으면 중지. 분석 페이지 모의매수/매도 모달에 **메모칸** 추가(note 전송).

**2) 랭킹 실시간 평가** (`94be6ed`)
- 문제: `total` 랭킹이 각 유저 최신 `AssetSnapshot` 의존 → 본인이 대시보드 열어야만 갱신.
- 해결: `_realtime_total_map(users)` — 랭킹 후보 전원 보유종목을 모아 고유 티커만 일괄
  시세 조회 → 유저별 (현금+평가금) 총자산 실시간 계산, **90초 캐시**(`_RT_TOTAL_CACHE`).
- `total`은 100% 실시간. `7d/30d`는 시작점=과거 스냅샷, 끝점=실시간 총자산.
- 반환 dict에 `has_holdings` 포함(미참여자 제외용, 아래 4번).

**3) 분석 헤더 현재가 1초 갱신** (`882c207`)
- 장중(`is_market_open`)이면 `/api/realtime-price` 1초 폴링으로 현재가+등락 갱신.
  전일종가 baseline = 분석시점(현재가−등락)으로 등락 재계산. 새 분석 시작/장마감 시 중지.
- `startStockPricePolling`/`stopStockPricePolling` (main.js). `window._lastStock.current_price`도 갱신.

**4) 보유종목 토스 스타일 토글 + 버그수정** (`c9a24f1`, `75f25c0`)
- trading.html 보유종목 헤더에 **평가금/현재가** · **원/$** 토글 2종.
  `_convCur`(exchange_rate로 native↔표시통화 환산) + `_fmtCur`. 손익·매입가도 환산, %는 통화무관.
- ⚠️ 버그(`75f25c0`): `loadAssetHistory`가 `document.querySelectorAll('.ah-period-btn')`로
  **페이지 전체** active 제거 → 같은 클래스 쓰는 보유종목/리뷰 토글 초기 active까지 삭제됐음.
  → `.asset-history-card` 범위로 한정. (교훈: 공유 클래스 토글은 컨테이너 범위 필수)

**5) 랭킹: 초기화 횟수 배지 + 미참여자 제외** (`dcb475a`)
- `User.reset_count` 컬럼(마이그레이션 `ADD COLUMN ... DEFAULT 0`), `trading_reset`에서 +1.
- 안기명 2회 보정: `UPDATE users SET reset_count=2 WHERE nickname='안기명' AND COALESCE(reset_count,0)=0` (멱등).
- leaderboard 응답에 `reset_count`, leaderboard.js 닉네임 옆 `🔄N` 배지(.lb-reset-tag).
- 보유종목 0 + 총자산 == 초기금(1억)이면 랭킹 제외(사실상 미참여).

**6) 재미 배지 10종 추가 후 카테고리 분배** (`da8e7c0` → `b916a5a`)
- 신규: 올빼미🦉 큰손🐳 단주의낭만🪙 물타기장인💧 종목수집가🗂️ 광속매매⚡
  다이아몬드핸드💎 연승행진🔥 칼같은손절✂️ 풀베팅🎲 (25→35개).
- `_build_badge_context`에 통계 9종 추가: night_owl_count, max_single_trade_krw,
  has_single_share, max_same_ticker_buys, unique_tickers_traded, worst_holding_pct,
  max_win_streak, cash_ratio, same_day_flip.
- 처음엔 전부 "스타일" 카테고리 → 사용자 요청으로 활동/트레이딩/포트폴리오/성과에 분산,
  스타일 제거. 분포: 활동6 수익6 트레이딩6 포트폴리오6 글로벌3 성과5 꾸준함3.

**7) 분석: 고점 대비 현재가(drawdown) 위젯** (`8aeb12c`)
- analyze에 `calc_drawdown(days)` — 기간 내 최고가(High) 대비 현재가 %, 신고가 갱신 여부.
  `stock_data["drawdowns"] = {1w, 1m, all}`.
- 주요지표 카드 상단 미니위젯(3칼럼): `renderDrawdowns`. 하락폭 색상(노랑→주황→빨강),
  신고가는 초록 🔺. 모바일 3칼럼 유지 반응형.
- 위치 선정: 52주 고점/저점과 같은 가격위치/리스크 맥락 → 기술적 "주요 지표"(투자판단 아님).

**Railway 장애 메모**: 이 세션 중 Railway 인시던트로 webhook 누락 → 일부 커밋 자동배포 안 됨.
빈 커밋(`e66a6be`) push로 재트리거 해결. 수동 Redeploy가 막히면 빈 커밋이 우회책.

### 📌 2026-06-12 (낮) — 전 AI 기능 Gemini 우선 전환 + README 갱신 (`9e31d80`, `819af22`)

**1) 기업분석·급등이유도 Gemini 우선으로** (`9e31d80`)
- `analyze_move_reason` / `fetch_company_overview`의 `_groq_chat` 직접 호출을
  `_ai_chat`(Gemini→Groq 폴백)으로 교체 → **이제 4개 AI 기능 전부 동일 경로**
- Railway에 `GEMINI_API_KEY` 등록 완료 (사용자 확인)
- 기업분석은 6h 캐시(`_OVERVIEW_CACHE`)라 기존 종목은 캐시 만료 후 Gemini 반영

**2) README.md 전면 업데이트** (`819af22`)
- Render→Railway, Groq전용→Gemini우선 등 구식 내용 전부 교체
- 모의투자/토스연동/AI코치/포트폴리오점검/포지션노트/보안 섹션 추가
- 향후 계획 표 (DART 임원매매 / 시즌제 / 백테스트 / 자동손절)

### 📌 2026-06-09~11 — 포지션 노트 + AI 코치 + 포트폴리오 점검 + Gemini 도입 (다수 커밋, 최종 `7bb0eab`)

**1) 보안/안정성 4종 세트**
- `/api/debug/toss`: `_is_toss_owner()` 게이팅 — 비오너는 404 (정보 노출 차단)
- `import-toss`: 토스 응답 빈 리스트면 404 에러 반환 (기존 보유 지우는 사고 방지)
- `FLASK_SECRET_KEY`: `RAILWAY_ENVIRONMENT` 감지 시 미설정이면 RuntimeError로 기동 차단
- `requirements.txt`: `requests==2.33.1` 버전 고정

**2) 포지션 노트** (Transaction.note)
- `models.py`: `Transaction.note` String(300) + `to_dict()` 반영
- 마이그레이션: `ALTER TABLE transactions ADD COLUMN IF NOT EXISTS note VARCHAR(300)`
- buy/sell API가 `note` 받음 (300자 절단). dashboard가 티커별 최신 노트를 포지션에 포함
- 프런트: 매수/매도 모달에 `<textarea>`(buyNote/sellNote), 보유카드 `.pf-note`,
  거래내역 `tx-note-row`. XSS 방지용 `escapeHtml()` 추가

**3) AI 매매 코치** (`GET/POST /api/trading/coach`)
- 핵심 설계: AI에게 원시 거래 던지지 않고 **Python에서 FIFO 통계 선계산** 후 주입
  - `_coach_stats_block(txs)`: 승률, 평균 보유일 버킷, 최고/최악 매매, 종목별 PnL Top3, 노트 작성률
- 프롬프트 규칙: 통계 인용 의무, "모니터링 필요" 류 모호 표현 금지, 항목마다 실행 제안
- `_COACH_FOREIGN_RE`: 한글+ASCII 외 문자 제거 (Groq Llama가 러시아어 토큰 뱉던 버그 해결)
- 10분 쿨다운 + `_COACH_CACHE` 서버 캐시. 프런트 trading.html 코치 카드

**4) AI 포트폴리오 점검** (`GET/POST /api/portfolio/review`)
- `mode=paper|real` — 모의(PaperHolding)/실제(Holding) 토글
- 종목별 비중, 집중도(최대 종목 비중), KRW/USD 통화 분산 계산 → AI 의견
- 10분 쿨다운 + `_REVIEW_CACHE`. 프런트 토글 버튼 + 카드 (`_reviewMode`, `_reviewResults`)

**5) 보유종목 📊 분석 바로가기**
- 보유카드에 분석 버튼 → `/?ticker=XXX` 이동
- `main.js`: DOMContentLoaded에서 `?ticker=` 쿼리 감지 시 자동 `analyze()` 실행

**6) Gemini API 도입** (`7bb0eab`)
- 저번 실패 원인: Gemini 키를 Groq 엔드포인트로 보내는 잘못된 코드였음
- `_GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]`
- `_gemini_chat()`: v1beta `generateContent`, `thinkingBudget: 0` 필수(생각 토큰이 출력 예산 잠식),
  모델 간 폴백 + 429 재시도. `gemini-2.0-flash`는 429 빈발이라 제외
- `_ai_chat()`: GEMINI_API_KEY 있으면 Gemini 우선, 실패 시 Groq 폴백
- 무료 한도: flash 분당 10/일 250 수준 — 쿨다운+캐시 덕에 충분
- SW/캐시 버전 v35 → **v38** (이 기간 3회 bump)

**1) Oracle Cloud VM + tinyproxy 고정 IP 프록시 구축 완료** (코드 변경 없음, 인프라)
- VM `<ORACLE_VM_IP>` (Chuncheon, Ubuntu 22.04, E2.1.Micro Always Free)
- tinyproxy <PROXY_PORT> + BasicAuth 가동 (자격증명은 `.env`의 `TOSS_PROXY_URL`), HTTP/HTTPS 검증 OK
- 토스 콘솔 IP 등록 + Railway `TOSS_PROXY_URL` 설정 → 재배포 IP 변동 영구 해결
- **설치 중 막혔던 함정 4가지 (TOSS_PROXY_SETUP.md에 반영)**:
  1. **apt가 IPv6로 나가 timeout** → `apt-get -o Acquire::ForceIPv4=true` 필요
     (cloud-init이 이 때문에 실패 → 수동 설치함)
  2. **비밀번호 특수문자 파싱 불가** → tinyproxy가 `!` 등 특수문자에서
     "Syntax error" → 영숫자 전용 비밀번호로 변경. (DefaultErrorFile/StatFile/
     ViaProxyName 등 따옴표 필요한 줄도 제거하는 게 안전)
  3. **systemd Type=forking 멈춤** → "activating"에서 행. override.conf로
     `Type=simple` + `ExecStart=/usr/bin/tinyproxy -d`(foreground) 고정
  4. **iptables 순서** → Oracle Ubuntu INPUT 체인 끝에 REJECT catch-all 있음.
     <PROXY_PORT> ACCEPT를 REJECT **앞**(예: 5번)에 삽입해야 함 (`-I INPUT 5 ...`)
- SSH 개인키 `<SSH_KEY_FILE>`는 `.gitignore`에 `*.key`, `ssh-key-*` 추가로 보호

**2) 랭킹 페이지 로그인 상태 미반영 버그 수정** (`bfab441`)
- 증상: 로그인 상태로 `/leaderboard` 들어가도 "로그인" 버튼이 계속 떴음
- 원인: `leaderboard.js` initAuth가 `/api/me` 응답을 `me.id`로 직접 읽음.
  실제 응답은 `{ user: {...} }` 구조라 항상 undefined → authArea 갱신 안 됨
- 해결: `data.user.id`로 수정 (trading.js와 동일 패턴), `profile-area` 마크업 통일
- 캐시 버전 v34→v35 (SW `sa-v35` + 템플릿 `?v=35` 3개 파일)

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
  미제공이라 user_id로). <OWNER> = id1(kakao)+id2(google) → `TOSS_OWNER_USER_IDS=<OWNER_USER_IDS>`
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

### ✅ 최근 완료 (2026-06-09~12 세션)
- [x] 보안 4종 (debug/toss 게이팅, import 가드, SECRET_KEY fail-fast, requests 고정)
- [x] **포지션 노트** (Transaction.note + 모달/카드/내역 UI)
- [x] **AI 매매 코치** (FIFO 통계 기반, Gemini)
- [x] **AI 포트폴리오 점검** (모의/실제, 비중·집중도·통화)
- [x] 보유종목 📊 분석 바로가기 (`?ticker=` 자동 분석)
- [x] **Gemini API 전환** — 전 AI 기능 `_ai_chat()` (Gemini→Groq)
- [x] README.md 전면 갱신

### ✅ 이전 완료 (2026-05-22 저녁 세션)
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
- [ ] **랭킹 시즌제** (월간/시즌별 리셋 + 시즌 배지) — 다음 추천 1순위
- [ ] DART API 임원 매매 연동 (스코어카드 "DART 공시 보러가기 ↗" 대체) — DART 키 이미 있음
- [ ] 자동 손절 (매수 시 손절가 입력 → 도달 시 알림/자동 매도)
- [ ] 백테스트 (과거 데이터로 전략 검증)
- [x] ~~AI 코치~~ / ~~포지션 노트~~ — 완료 (2026-06-09~11)

### 📋 데이터 보강 (옵션)
- [ ] **DART API 키 등록** — 한국 임원 주식 변동 자동 fetch
  - 등록: https://opendart.fss.or.kr/ (무료)
  - 환경변수 `DART_API_KEY` 추가하면 됨
- [ ] **KRX 공식 공매도 API** — 더 정확한 실시간 공매도

---

## 7. ⚠️ 반드시 기억할 제약/관례

### 7.1 Service Worker 캐싱
- **JS/CSS 변경 시 `static/service-worker.js`의 `CACHE_VERSION` 올려야** 사용자에게 반영됨
- 현재: `sa-v35`
- HTML 의 정적 자산은 **`?v=NN` 쿼리 캐시 버스팅** 사용 (현재 v=35) —
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

### 7.9 AI 호출 (Gemini 우선 / Groq 폴백)
- **새 AI 기능은 반드시 `_ai_chat()` 사용** — Gemini 우선(`_gemini_chat`) → 실패 시 Groq 폴백
- `_gemini_chat()`: `thinkingBudget: 0` 필수 (Gemini 2.5는 기본 thinking이 출력 토큰 잠식),
  `_GEMINI_MODELS` 폴백 + 429 재시도 내장
- `_groq_chat()`: model fallback 체인 + 재시도 + 한자 자동 변환(`_strip_hanja`)
- 시스템 메시지에 "순수 한글로만, 한자(漢字) 절대 금지" 명시 필수
- 코치 출력은 `_COACH_FOREIGN_RE`로 비한글·비ASCII 추가 제거 (Llama 러시아어 토큰 대응)
- Gemini 무료 한도(flash 일 250회) 보호: 고빈도 신규 기능은 쿨다운/캐시 먼저 설계할 것

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

### 7.19 페이지 구조 / 화면 관례 (2026-07-25)
- **홈(`/`) = 시황 대시보드, 분석 = `/stock/<ticker>`**. 같은 `index.html`을 쓰고
  JS `PAGE_TICKER`(URL 경로 파싱)로 모드 전환. 홈 요소 숨김은 `body.stock-mode` CSS
  (비동기 로드 후에도 유지되도록 클래스 방식 — 개별 hidden 토글 금지)
- **종목 이동은 `gotoStock(ticker)` 하나로 통일.** `analyze()` 앞단에 게이트가 있어
  홈에서 호출되면 자동으로 `/stock/`으로 이동 → 새 진입점(카드/리스트) 추가 시
  별도 처리 불필요, `analyze()`나 `gotoStock()`만 부르면 됨
- **분석 상세 탭 = 3개**: technical(지표+매수/매도구간) / fundamental / valuation.
  각각 `#technicalContainer` `#fundamentalContainer` `#valuationPanel` 토글.
  밸류에이션 탭은 DCF 산출 가능할 때만 노출(`#valuationTabBtn`)
- **카드 톤 통일**: 홈/분석 모든 섹션은 `max-width:1200px` + `.card`(bg2/border/radius).
  풀폭 그라데이션 히어로·풀폭 배경 섹션은 쓰지 않음
- **공유 클래스 토글 주의**: `.ah-period-btn`은 자산차트·보유종목·리뷰·대시보드가
  공유 → active 갱신 시 반드시 컨테이너 범위로 한정 (`.asset-history-card .ah-period-btn` 등).
  전역 `document.querySelectorAll('.ah-period-btn')`는 다른 토글 선택을 지움
- **미디어쿼리 정리**: 레이아웃 방향(`flex-direction`)을 바꿀 땐 같은 브레이크포인트의
  옛 `width:100%`/`flex` 규칙도 함께 제거할 것 (모바일 검색창 압착 사고 원인)

---

## 8. 외부 의존성 / 환경변수

### .env 필수 키
```
GEMINI_API_KEY=...         # AI 1순위 (코치/점검/기업분석/급등이유) — Railway에도 등록됨
GROQ_API_KEY=...           # AI 폴백 (Gemini 실패/한도 시)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
KAKAO_CLIENT_ID=...
KAKAO_CLIENT_SECRET=...
DATABASE_URL=...           # Railway가 자동 주입 (로컬은 SQLite로 폴백)
FLASK_SECRET_KEY=...
TOSS_CLIENT_ID=...         # 토스증권 Open API (미설정 시 yfinance/네이버 폴백)
TOSS_CLIENT_SECRET=...     # ⚠️ live 키는 IP 허용목록 등록 필요 (콘솔)
TOSS_OWNER_USER_IDS=<OWNER_USER_IDS>    # 토스 계좌 import 권한 (<OWNER> kakao=1, google=2)
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
cd C:\Users\<USER>\Documents\stock-analyzer
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
- [x] ~~토스 IP 재등록 (재배포마다)~~ — **해결됨** (Oracle VM 고정 IP 프록시, 2026-06-08)
- [x] ~~`/api/debug/toss` 노출~~ — 오너 게이팅 완료 (비오너 404, 2026-06-09)
- [ ] **Oracle VM IP가 Ephemeral** — 재부팅 시 IP 바뀌면 토스 콘솔 재등록 필요.
  Oracle 콘솔에서 Reserved IP로 전환 권장
- [ ] **Gemini 무료 한도 모니터링** — flash 일 250회. 사용자 늘면 코치/점검 쿨다운 상향 또는 유료 검토
- [ ] **토스 다른 계좌(연금/ISA) 미노출** — Open API가 개발자 키 연동 1계좌만 제공 (API 한계)

---

## 11. Git 상태 스냅샷 (2026-07-25)

```
main 브랜치 (origin/main과 동기화됨, 모두 정상 배포)

89fac4b ux: 밸류에이션 탭 편입 + 모바일 검색창 찌그러짐 수정                 ← HEAD
af0160a docs: HANDOFF.md 최신화 (2026-07-25 DCF/시황대시보드 세션)
60d76ad ux: 검색 영역을 시황 대시보드 카드 톤으로 통일
6f30be7 ux: 섹터 강도 탭 제거 + 섹터맵 구성종목 모달 + 탭 섹션 톤 통일
1bbad2d feat: 실전 섹터맵(핀비즈 스타일) + 대시보드 통합 토글 + 홈 재배치
571cd41 feat: 홈 시황 대시보드 + 분석 전용 페이지(/stock/<ticker>) 분리
5da2506 feat: DCF 역산 영업이익률 추가 — 산출 불가 화면 제거
5f001e2 feat: DCF 밸류에이션 카드 (financial-services 방법론 이식)
1f1e2ab docs: HANDOFF.md 최신화 (2026-06-12 세션)
b916a5a refactor: 신규 배지 10종 기존 카테고리로 분배 (스타일 제거)
8aeb12c feat: 분석 페이지 고점 대비 현재가(drawdown) 위젯
da8e7c0 feat: 재미 배지 10종 추가 (스타일 카테고리, 25→35개)
dcb475a feat: 랭킹 초기화 횟수 배지 + 빈 포트폴리오(1억) 제외
75f25c0 fix: 자산차트 토글이 보유종목/리뷰 토글 active까지 지우던 버그
e66a6be chore: 재배포 트리거 (Railway 장애 우회)
c9a24f1 feat: 모의투자 보유종목 토스 스타일 토글 (평가금/현재가 · 원/$)
882c207 feat: 분석 페이지 헤더 현재가 1초 실시간 갱신
94be6ed feat: 랭킹 수익률 실시간 평가 (본인 접속 무관 갱신)
616739a fix: 모의매매 체결가 서버 실시간가 강제 (조작 차단) + 분석 매수 메모칸
819af22 docs: README 전면 업데이트
9e31d80 feat: 기업분석·급등이유 AI를 Gemini 우선으로 전환
7bb0eab feat: AI 코치/포트폴리오 점검 Gemini 전환 (_gemini_chat + _ai_chat)
...     (2026-06-09~11: 보안4종 / 포지션노트 / AI코치 / 포트폴리오점검 / 분석버튼)
a9f5d08 docs: 프록시 셋업 문서 갱신
bfab441 fix: 랭킹 페이지 로그인 상태 미반영
232b5d2 feat: toss_to_sql 스크립트 UTF-8 파일 출력 + .sql gitignore
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

**Service Worker**: `sa-v53`
**HTML 캐시 버스팅 쿼리**: `?v=53`

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
