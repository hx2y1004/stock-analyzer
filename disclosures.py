"""공시·수주 레이더 (1차): 한국 OpenDART · 미국 SEC EDGAR 핵심 공시를 한 테이블(disclosure_events)에 모은다.

구조 (2차에 나라장터 · USAspending.gov 를 붙이기 쉽게)
- 수집기(collector) 하나 = "외부 소스 조회 → 이벤트 dict 목록" 을 돌려주는 함수. 저장·중복 방지·알림은 공통(save_events).
  이벤트 dict: market, ticker, name, source, category, sentiment, title, summary, source_url, external_id, disclosed_at(UTC naive)
- 새 수집기는 COLLECTORS 에 (이름, 시장, 주기(분), 실행 가능 시간 판정, 함수) 로 등록하면 tick() 이 주기에 맞춰 부른다.

운영 원칙
- 새 스레드를 만들지 않는다. bot_engine 스케줄러 루프(20초마다)가 tick() 을 부르고, 주기가 된 수집기만 돈다.
- 한 번에 쓰는 시간을 TICK_BUDGET 초로 묶어 장중 5분 체크가 밀리지 않게 한다.
- 대상은 섹터맵 유니버스(KR ~350 · US ~537) + 사용자 보유(실제·모의)·관심종목.
- DART: 기존 DART_API_KEY 재사용 (없으면 한국 수집은 건너뜀).
- SEC: 키 없음. 모든 요청에 User-Agent(SEC_USER_AGENT) 헤더, 요청 사이 0.2초 이상 간격 (SEC 공정 사용 정책: 초당 10회 이하).

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import html
import io
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

D = {}
KST, ET_TZ = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")

CATEGORIES = ("contract", "buyback", "offering", "insider_buy", "material_8k")
CATEGORY_KO = {"contract": "수주", "buyback": "자사주", "offering": "유상증자", "insider_buy": "내부자 매수", "material_8k": "8-K"}
RETENTION_DAYS = 30
TICK_BUDGET = 30.0              # 초. 한 번의 tick 에서 정기 수집에 쓰는 최대 시간
BACKFILL_MARKET_BUDGET = 20.0   # 초. 백필은 시장마다 따로 (한국이 다 써도 미국은 자기 20초로 같은 tick 에 진행)
DART_PAGES_COLD = 20            # 재시작 직후 첫 조회: 오늘 공시 최대 2,000건까지 훑는다
DART_PAGES_WARM = 5             # 이후 15분마다: 최신 500건 (이미 본 접수번호를 만나면 멈춤)
DART_DOC_PER_RUN = 10           # 공급계약 원문(계약금액·매출 대비 %) 조회는 한 번에 10건까지
SEC_GAP = 0.2                   # 초. SEC 요청 간격
SEC_FEED_PAGES = 4              # 최신 공시 피드 페이지(100건씩) 상한
SEC_FILINGS_PER_RUN = 30        # Form 4 / 8-K 상세 조회 상한 (한 번에)
SEC_ITEMS = {"1.01": ("중요 계약 체결", "positive"), "2.01": ("인수·자산 취득 완료", "positive"),
             "2.02": ("실적 발표", "neutral")}
# 최초 백필: 시장별 DB 가 0건인 프로세스 첫 tick 에 최근 14일을 알림 없이 채운다 (여러 tick 에 나눠, tick 마다 TICK_BUDGET 안에서)
BACKFILL_DAYS = 14
DART_BACKFILL_TYPES = ("B", "I", "D")  # 주요사항보고(자기주식 취득·유상증자) · 거래소공시(공급계약·주식소각) · 지분공시(임원·주요주주 소유)
DART_BACKFILL_PAGES = 30              # 유형마다 최대 30쪽(100건씩)
DART_BACKFILL_PAGES_BY_TYPE = {"D": 60}   # 지분공시는 하루 수백 건이라 14일치를 보려면 쪽이 더 필요
DART_ELESTOCK_PER_RUN = 15            # 임원·주요주주 소유보고(elestock.json) 호출 상한: 정기 수집 한 번에
DART_ELESTOCK_BACKFILL_PER_RUN = 40   # 백필 한 tick 에 (남은 후보는 다음 tick 에 이어서)
INSIDER_MIN_KRW = 30_000_000          # 추정 매수 금액(증감 주수 × 현재가)이 이보다 작으면 노이즈로 제외
INSIDER_MIN_SHARES = 100              # 현재가를 모를 때는 증감 주수 기준
INSIDER_PENDING_MAX = 300             # 정기 수집 대기열 상한 (API 오류가 길어져도 무한히 쌓이지 않게)
DART_BACKFILL_DOCS = 30               # 공급계약 원문(계약금액·매출 대비 %) 조회 상한
SEC_BACKFILL_FORM4 = 600              # Form 4 XML 조회 상한 (전체)
SEC_BACKFILL_FORM4_PER_CO = 10        # 회사마다 최신 Form 4 최대 10건
BACKFILL_MAX_ERRORS = 20              # 같은 단계에서 연속 오류가 이만큼이면 오류로 끝낸다 (무한 반복 방지)
DART_FATAL = {"010": "등록되지 않은 DART API 키", "011": "사용할 수 없는 DART API 키", "012": "접근할 수 없는 IP (DART)",
              "901": "DART API 키 기간 만료"}
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# 미국 유상증자 · 자사주 매입 (한국 DART 와 같은 4개 분류)
SEC_CONTRACT_ITEM = "1.01"            # 8-K Item 1.01 중요 계약 체결 → '수주·계약'(contract)
SEC_OFFERING_ITEM = "3.02"            # 8-K Item 3.02 미등록 증권 발행(사모 유상증자) → 항목 번호만으로 판정
SEC_BUYBACK_ITEMS = ("8.01", "7.01")  # 8-K 기타 사항 · Reg FD → 문서 앞부분에 자사주 매입 키워드가 있으면 buyback
SEC_FORMS = ("4", "8-K", "424B5")     # 실시간 피드에서 보는 양식 (424B5 = 공모·ATM 신주 발행 설명서)
DOC_HEAD_BYTES = 131072               # 문서는 앞부분 128KB 만 읽는다 (iXBRL 8-K 는 <ix:header>·표지만 25~60KB 라 16KB 로는 본문에 못 닿았다)
# EDGAR 전문 검색(EFTS): 8-K 본문과 첨부 보도자료(EX-99.1)를 함께 색인 → 미국 자사주 매입 발표를 직접 찾는다
SEC_EFTS = "https://efts.sec.gov/LATEST/search-index"
EFTS_QUERY = ('"share repurchase" OR "stock repurchase" OR "repurchase program" OR "buyback program" '
              'OR "repurchase authorization" OR "repurchase of common stock"')
EFTS_PAGES = 3                        # 100건씩 최대 3쪽 (300건)
EFTS_DAYS = 2                         # 정기 수집: 최근 48시간
EFTS_BACKFILL_DAYS = 14               # 최근 14일에 미국 자사주가 0건이면 1번 소급
EFTS_DOCS_PER_RUN = 20                # 정기 수집에서 문서(보도자료) 확인 상한
EFTS_BACKFILL_DOCS_PER_RUN = 30       # 백필 한 번(tick)에 문서 확인 상한 — 남은 후보는 다음 tick 에 이어서
SEC_BACKFILL_DOCS_PER_CO = 6          # 백필: 회사마다 문서 확인(8-K 8.01/7.01 · 424B5) 최대 6건
SEC_BACKFILL_DOCS = 400               # 백필: 문서 확인 전체 상한


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[DISCLOSURE] {msg}")
    except Exception:
        print(f"[DISCLOSURE] {msg}", flush=True)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 대상 종목 ──────────────────────────────────────────────────────────────────
def targets():
    """→ {"KR": {ticker: name}, "US": {ticker: name}}: 섹터맵 유니버스 + 사용자 보유(실제·모의)·관심종목."""
    out = {"KR": {}, "US": {}}
    try:
        u = D["universe"]()
        for m in ("KR", "US"):
            for t, v in (u.get(m) or {}).items():
                out[m][t] = (v or {}).get("name") or t
    except Exception as e:
        log(f"유니버스 읽기 실패: {e}")
    for model in ("Holding", "PaperHolding", "WatchlistItem"):
        M = D.get(model)
        if M is None:
            continue
        try:
            for r in M.query.with_entities(M.ticker, M.name).distinct().all():
                t = (r[0] or "").upper()
                if not t:
                    continue
                m = "KR" if t.endswith((".KS", ".KQ")) else "US"
                if m == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                    continue                         # 지수·환율 등은 제외
                out[m].setdefault(t, r[1] or t)
        except Exception as e:
            D["db"].session.rollback()
            log(f"{model} 종목 읽기 실패: {e}")
    return out


# ── 저장 · 알림 · 정리 (모든 수집기 공통) ─────────────────────────────────────────
def save_events(events):
    """새 external_id 만 저장 → 새로 저장된 행 목록. 중복은 조용히 건너뛴다."""
    E, db = D["DisclosureEvent"], D["db"]
    events = [e for e in events if e and e.get("external_id")]
    if not events:
        return []
    uniq = {}
    for e in events:
        uniq.setdefault(e["external_id"], e)
    ids = list(uniq)
    have = set()
    for i in range(0, len(ids), 300):
        have |= {r[0] for r in db.session.query(E.external_id).filter(E.external_id.in_(ids[i:i + 300])).all()}
    rows = []
    for xid, e in uniq.items():
        if xid in have:
            continue
        rows.append(E(market=e["market"], ticker=e["ticker"], name=(e.get("name") or e["ticker"])[:200], source=e["source"],
                      category=e["category"], sentiment=e.get("sentiment") or "neutral", title=e["title"][:300],
                      summary=(e.get("summary") or "")[:500] or None, source_url=(e.get("source_url") or "")[:500] or None,
                      external_id=xid[:120], disclosed_at=e.get("disclosed_at") or _utcnow()))
    if not rows:
        return []
    try:
        db.session.add_all(rows)
        db.session.commit()
        return rows
    except Exception:
        db.session.rollback()                         # 동시에 같은 공시가 들어온 경우: 한 건씩
        saved = []
        for r in rows:
            try:
                db.session.add(r)
                db.session.commit()
                saved.append(r)
            except Exception:
                db.session.rollback()
        return saved


def _notify(rows):
    fn = D.get("notify")
    if not fn or not rows:
        return 0
    try:
        return fn(rows) or 0
    except Exception as e:
        D["db"].session.rollback()
        log(f"알림 실패: {e}")
        return 0


def cleanup(now=None):
    E, db = D["DisclosureEvent"], D["db"]
    cut = (now or _utcnow()) - timedelta(days=RETENTION_DAYS)
    n = E.query.filter(E.disclosed_at < cut).delete(synchronize_session=False)
    db.session.commit()
    return n


def query(market="ALL", category="ALL", ticker=None, days=14, limit=50):
    E = D["DisclosureEvent"]
    q = E.query.filter(E.disclosed_at >= _utcnow() - timedelta(days=max(1, min(int(days or 14), RETENTION_DAYS))))
    if market in ("KR", "US"):
        q = q.filter(E.market == market)
    if category == "contract":
        q = q.filter(D["db"].or_(E.category == "contract",
                                 D["db"].and_(E.category == "material_8k", E.summary.like("%Item 1.01%"))))
    elif category == "material_8k":                    # Item 1.01 이 든 예전 행은 위 contract 쪽으로 보이므로 여기선 뺀다
        q = q.filter(E.category == "material_8k", D["db"].not_(E.summary.like("%Item 1.01%")))
    elif category in CATEGORIES:
        q = q.filter(E.category == category)
    if ticker:
        q = q.filter(E.ticker == ticker.upper())
    rows = q.order_by(E.disclosed_at.desc(), E.id.desc()).limit(max(1, min(int(limit or 50), 200))).all()
    return [to_dict(r) for r in rows]


def _norm_category(r):
    """예전에 material_8k 로 저장된 8-K Item 1.01 은 contract 로 보여 준다."""
    if r.category == "material_8k" and r.source == "SEC" and "Item 1.01" in (r.summary or ""):
        return "contract"
    return r.category


def migrate_8k_contract():
    """서버 기동 시 1번: 예전에 material_8k 로 저장된 SEC 8-K Item 1.01 행을 contract 로 바꾼다. → 바꾼 행 수"""
    E, db = D["DisclosureEvent"], D["db"]
    try:
        n = (E.query.filter(E.source == "SEC", E.category == "material_8k", E.summary.like("%Item 1.01%"))
             .update({"category": "contract"}, synchronize_session=False))
        db.session.commit()
        if n:
            log(f"8-K Item 1.01 {n}건 → 수주·계약(contract)")
        return n
    except Exception as e:
        db.session.rollback()
        log(f"8-K 분류 변환 실패 (조회 때 보정함): {e}")
        return 0


def to_dict(r):
    return {"id": r.id, "market": r.market, "ticker": r.ticker, "name": r.name, "source": r.source, "category": _norm_category(r),
            "sentiment": r.sentiment, "title": r.title, "summary": r.summary, "source_url": r.source_url,
            "disclosed_at": r.disclosed_at.isoformat() + "Z" if r.disclosed_at else None}


# ══ 한국: OpenDART ═══════════════════════════════════════════════════════════════
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
DART_DOC = "https://opendart.fss.or.kr/api/document.xml"
DART_ELESTOCK = "https://opendart.fss.or.kr/api/elestock.json"
_dart_seen = {"day": None, "ids": set()}         # 오늘 이미 훑은 접수번호 (대상 아닌 공시 포함) → 다음 조회에서 거기서 멈춤


def _norm_report(nm):
    """보고서명 정규화: 공백 제거 · 가운뎃점(ㆍ·・) 통일."""
    return re.sub(r"\s+", "", str(nm or "")).replace("ㆍ", "·").replace("・", "·").replace("‧", "·")


def classify_dart(report_nm):
    """보고서명 → (category, sentiment, 짧은 제목) | None. 3가지 핵심 공시만."""
    n = _norm_report(report_nm)
    fix = "정정" in n[:8]
    tag = " (정정)" if fix else ""
    if "단일판매·공급계약" in n or "단일판매공급계약" in n:
        if "해지" in n:
            return "contract", "negative", "단일판매·공급계약 해지" + tag
        return "contract", "positive", "단일판매·공급계약체결" + tag
    if "자기주식취득" in n and "결정" in n:
        label = "자기주식 취득 신탁계약 체결" if "신탁" in n else "자기주식 취득 결정"
        return "buyback", "positive", label + tag
    if "주식소각결정" in n or "자기주식소각" in n:
        return "buyback", "positive", "자기주식 소각 결정" + tag
    if "유상증자결정" in n:                       # '유무상증자결정' 포함, '무상증자결정' 만 있는 건 제외
        return "offering", "negative", ("유무상증자 결정" if "유무상" in n else "유상증자 결정") + tag
    return None


def fmt_krw(v):
    if v is None:
        return None
    if v < 0:
        return "-" + fmt_krw(-v)
    if v >= 1e12:                                   # 1조 2,345억원
        jo, eok = int(v // 1e12), int(round((v % 1e12) / 1e8))
        if eok >= 10000:
            jo, eok = jo + 1, 0
        return f"{jo:,}조원" if eok == 0 else f"{jo:,}조 {eok:,}억원"
    if v >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    if v >= 1e4:                                    # 4,500만원
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def parse_contract_text(text):
    """공급계약 원문 텍스트 → {"amount": 원, "ratio": %}. 서식이 조금씩 달라 넉넉하게 찾는다."""
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or "")))
    out = {"amount": None, "ratio": None}
    m = re.search(r"계약\s*금액\s*(?:총액)?\s*\(\s*원\s*\)\s*:?\s*([\d,]{4,})", t) or re.search(r"계약\s*금액[^0-9]{0,30}?([\d,]{6,})", t)
    if m:
        try:
            out["amount"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"매출액\s*대비\s*\(\s*%\s*\)\s*:?\s*([\d.]+)", t) or re.search(r"매출액\s*대비[^0-9]{0,15}([\d.]+)\s*%?", t)
    if m:
        try:
            out["ratio"] = float(m.group(1))
        except ValueError:
            pass
    return out


def _dart_contract_detail(key, rcept_no, get):
    """document.xml (zip) → 계약금액·매출 대비 %. 실패하면 빈 dict."""
    try:
        r = get(DART_DOC, params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=15)
        if r.status_code != 200 or not r.content.startswith(b"PK"):
            return {}
        text = ""
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for nm in z.namelist()[:3]:
                raw = z.read(nm)
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        text += raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
        return parse_contract_text(text)
    except Exception as e:
        log(f"DART 원문 실패 {rcept_no}: {e}")
        return {}


# ── 한국 내부자 매수: 임원·주요주주특정증권등소유상황보고서 → elestock.json 증감 수 ──
_INSIDER_REPORTS = ("임원·주요주주특정증권등소유상황보고서", "임원주요주주특정증권등소유상황보고서")
_dart_ins_pending = []          # 정기 수집에서 호출 상한에 걸려 다음 run 으로 넘긴 후보 [{"it", "hit", "tries"}]


def is_insider_report(report_nm):
    n = _norm_report(report_nm)
    return any(k in n for k in _INSIDER_REPORTS)


def _int(v):
    """'1,234' · '-500' · '+30' → int, '-'·빈 값 → None."""
    t = re.sub(r"[,\s]", "", str(v if v is not None else ""))
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", t):
        return None
    return int(float(t))


def _clean(v):
    t = str(v or "").strip()
    return "" if t in ("-", "없음", "해당없음", "해당사항없음") else t


def _kst_at(it, day):
    """목록 항목의 접수일 → disclosed_at (UTC naive). 오늘이면 지금 시각, 과거면 그날 09:00 KST."""
    try:
        d0 = datetime.strptime(str(it.get("rcept_dt") or ""), "%Y%m%d").date()
    except ValueError:
        d0 = day
    return _utcnow() if d0 == day else datetime(d0.year, d0.month, d0.day, 9, tzinfo=KST).astimezone(timezone.utc).replace(tzinfo=None)


def insider_event(it, hit, row, price, day):
    """목록 1건 + elestock 행 → insider_buy 이벤트 | None.
    특정증권등 소유 증감 수(sp_stock_lmp_irds_cnt) 가 양수(순매수)이고, 추정 금액(증감 주수 × 현재가)이 3,000만 원 이상
    (현재가를 모르면 100주 이상)일 때만."""
    irds = _int(row.get("sp_stock_lmp_irds_cnt"))
    if not irds or irds <= 0:
        return None
    est = irds * price if price else None
    if est is not None and est < INSIDER_MIN_KRW:
        return None
    if est is None and irds < INSIDER_MIN_SHARES:
        return None
    t, nm = hit
    pos = _clean(row.get("isu_exctv_ofcps")) or _clean(row.get("isu_main_shrholdr")) or "임원·주요주주"
    who = _clean(row.get("repror")) or _clean(it.get("flr_nm"))
    head = f"{pos} {who}".strip()
    fix = "정정" in _norm_report(it.get("report_nm"))[:8]
    rate = _clean(row.get("sp_stock_lmp_rate"))
    extra = [f"추정 약 {fmt_krw(est)}" if est is not None else "", f"지분 {rate}%" if rate else ""]
    extra = ", ".join(x for x in extra if x)
    rno = str(it.get("rcept_no"))
    return {"market": "KR", "ticker": t, "name": it.get("corp_name") or nm, "source": "DART", "category": "insider_buy",
            "sentiment": "positive", "title": f"{head} 지분 매수" + (" (정정)" if fix else ""),
            "summary": f"{head} +{irds:,}주" + (f" ({extra})" if extra else ""),
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
            "external_id": f"DART:{rno}", "disclosed_at": _kst_at(it, day)}


def _elestock_rows(key, corp_code, get):
    """elestock.json → 행 목록 | None(오류). 013(데이터 없음)은 빈 목록."""
    try:
        j = get(DART_ELESTOCK, params={"crtfc_key": key, "corp_code": corp_code}, timeout=10).json()
    except Exception as e:
        log(f"DART elestock 실패 {corp_code}: {type(e).__name__}")
        return None
    st = str(j.get("status") or "")
    if st == "000":
        return j.get("list") or []
    if st == "013":
        return []
    log(f"DART elestock 응답 {st} {j.get('message') or ''}".strip())
    return None


def _kr_prices():
    """섹터맵 스냅샷(이미 메모리·DB 에 있는 값)의 한국 종목 현재가 {ticker: price}. 없으면 빈 dict (네트워크 호출 없음)."""
    fn = D.get("kr_prices")
    if not fn:
        return {}
    try:
        return fn() or {}
    except Exception as e:
        log(f"현재가 읽기 실패: {e}")
        return {}


def process_insider(key, get, queue, budget, day, deadline=None, cache=None, prices=None, retry=False):
    """대기열(queue, 앞에서부터) 의 임원·주요주주 보고서 후보를 처리 → (이벤트 목록, elestock 호출 수).
    같은 corp_code 는 이번 처리에서 한 번만 부른다(cache). 호출 상한(budget)·시간이 다 되면 남은 후보는 queue 에 그대로 둔다.
    retry=True(정기 수집)면 elestock 에 아직 이 접수번호가 안 보일 때 다음 run 에 2번까지 다시 본다."""
    cache = {} if cache is None else cache
    out, calls, keep = [], 0, []
    price_map = prices
    while queue:
        c = queue.pop(0)
        it = c["it"]
        cc = str(it.get("corp_code") or "").strip()
        if not cc:
            continue
        if cc not in cache:
            if calls >= budget or (deadline and time.time() > deadline):
                keep.append(c)
                continue
            cache[cc] = _elestock_rows(key, cc, get)
            calls += 1
        rows = cache[cc]
        if rows is None:                               # API 오류 → 다음에 다시
            keep.append(c)
            continue
        rno = str(it.get("rcept_no"))
        row = next((r for r in rows if str(r.get("rcept_no")) == rno), None)
        if row is None:
            if retry and c.get("tries", 0) < 2:
                keep.append({**c, "tries": c.get("tries", 0) + 1})
            continue
        if price_map is None:
            price_map = _kr_prices()
        ev = insider_event(it, c["hit"], row, price_map.get(c["hit"][0]), day)
        if ev:
            out.append(ev)
    queue.extend(keep)
    return out, calls


def _dart_code_map(tg):
    code2t = {}
    for t, nm in tg["KR"].items():
        code2t.setdefault(t.split(".")[0].upper(), (t, nm))
    return code2t


def _dart_event(it, hit, key, get, day, want_doc):
    """DART 목록 1건 → 이벤트 dict | None. want_doc 이면 공급계약 원문에서 계약금액·매출 대비 % 를 읽는다."""
    cls = classify_dart(it.get("report_nm"))
    if not cls:
        return None
    cat, senti, label = cls
    t, nm = hit
    rno = str(it.get("rcept_no"))
    summary = {"contract": "공급계약 체결", "buyback": "주주환원 (자사주)", "offering": "신주 발행 — 주식 수 희석 주의"}[cat]
    if cat == "contract" and senti == "negative":
        summary = "기존 공급계약 해지"
    if cat == "contract" and senti == "positive" and want_doc:
        det = _dart_contract_detail(key, rno, get)
        parts = [f"계약금액 {fmt_krw(det['amount'])}" if det.get("amount") else "",
                 f"최근 매출 대비 {det['ratio']:g}%" if det.get("ratio") is not None else ""]
        summary = " · ".join(p for p in parts if p) or summary
    at = _kst_at(it, day)
    return {"market": "KR", "ticker": t, "name": it.get("corp_name") or nm, "source": "DART", "category": cat,
            "sentiment": senti, "title": label, "summary": summary,
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
            "external_id": f"DART:{rno}", "disclosed_at": at}


def _bf_fail(m, msg, fatal=False):
    """백필 오류 기록. 치명적이거나 연속 오류가 많으면 오류 상태로 끝내 무한 반복을 막는다."""
    st = _bf[m]
    st["errors"] = st.get("errors", 0) + 1
    st["note"] = msg
    if fatal or st["errors"] >= BACKFILL_MAX_ERRORS:
        st.update(done=True, state="error")
    log(f"{m} 백필 {'중단' if st.get('state') == 'error' else '재시도 예정'}: {msg}")


def _bf_pages(ty):
    return DART_BACKFILL_PAGES_BY_TYPE.get(ty, DART_BACKFILL_PAGES)


def _bf_types(st):
    return tuple(st.get("types") or DART_BACKFILL_TYPES)


def _dart_backfill(key, tg, now, deadline, get, emit):
    """최근 14일 (bgn_de = 오늘 - 14일 ~ end_de = 오늘). 공시유형(기본 B·I·D, _bf["KR"]["types"] 로 지정 가능)마다 최대 30쪽(D 60쪽).
    한 쪽을 받을 때마다 찾은 공시를 바로 저장(emit)한다. 임원·주요주주 보고서는 대기열(st["ins"])에 넣고 tick 마다
    elestock 40회까지 확인. 시간 예산이 끝나면 위치를 _bf["KR"] 에 남기고 다음 tick 에 이어서."""
    st = _bf["KR"]
    day = now.astimezone(KST).date()
    types = _bf_types(st)
    st.setdefault("bgn", (day - timedelta(days=BACKFILL_DAYS)).strftime("%Y%m%d"))
    st.setdefault("end", day.strftime("%Y%m%d"))
    st.setdefault("ti", 0); st.setdefault("page", 1); st.setdefault("docs", 0); st.setdefault("calls", 0)
    st.setdefault("pages_done", 0); st.setdefault("ins", []); st.setdefault("ins_calls", 0)
    st.setdefault("total", sum(_bf_pages(t) for t in types))
    code2t = _dart_code_map(tg)
    ins = {"left": DART_ELESTOCK_BACKFILL_PER_RUN, "cache": {}, "prices": None}

    def drain():
        if not st["ins"] or ins["left"] <= 0:
            return
        if ins["prices"] is None:
            ins["prices"] = _kr_prices()
        evs, n = process_insider(key, get, st["ins"], ins["left"], day, deadline=deadline, cache=ins["cache"], prices=ins["prices"])
        ins["left"] -= n
        st["ins_calls"] += n
        emit(evs)

    drain()                                            # 지난 tick 에서 남은 내부자 후보부터
    while st["ti"] < len(types):
        if deadline and time.time() > deadline:
            return                                     # 다음 tick 에 이어서
        ty = types[st["ti"]]
        try:
            r = get(DART_LIST, params={"crtfc_key": key, "bgn_de": st["bgn"], "end_de": st["end"], "pblntf_ty": ty,
                                       "page_no": st["page"], "page_count": 100}, timeout=10)
            j = r.json()
        except Exception as e:
            _bf_fail("KR", f"DART 연결 실패 ({type(e).__name__})")
            return                                     # 같은 쪽부터 다시
        st["calls"] += 1
        status = str(j.get("status") or "")
        if status in DART_FATAL:
            _bf_fail("KR", f"{DART_FATAL[status]} ({status})", fatal=True)
            return
        if status not in ("000", "013"):
            _bf_fail("KR", f"DART 응답 {status} {j.get('message') or ''}".strip())
            return                                     # 요청 제한(020)·점검(800) 등: 다음 tick 에 같은 쪽부터
        st["errors"], st["note"] = 0, None
        items = (j.get("list") or []) if status == "000" else []
        evs = []
        for it in items:
            hit = code2t.get(str(it.get("stock_code") or "").strip().upper())
            if not hit:
                continue
            if is_insider_report(it.get("report_nm")):
                st["ins"].append({"it": it, "hit": hit})
                continue
            cls = classify_dart(it.get("report_nm"))
            want_doc = bool(cls) and cls[:2] == ("contract", "positive") and st["docs"] < DART_BACKFILL_DOCS
            st["docs"] += int(want_doc)
            ev = _dart_event(it, hit, key, get, day, want_doc)
            if ev:
                evs.append(ev)
        emit(evs)                                      # 이 쪽에서 찾은 건 지금 바로 DB 에
        drain()
        st["pages_done"] += 1
        if not items or len(items) < 100 or st["page"] >= _bf_pages(ty):
            st["total"] -= _bf_pages(ty) - st["page"]             # 이 유형은 끝 → 남은 쪽 수를 전체에서 뺀다
            st["ti"] += 1; st["page"] = 1
        else:
            st["page"] += 1
            time.sleep(0.1)
    drain()
    if st["ins"]:
        return                                         # 내부자 후보가 남음 → 다음 tick 에 elestock 이어서
    st["done"] = True


def collect_dart(tg=None, now=None, deadline=None, get=None, backfill=False, emit=None):
    """오늘(한국 날짜) 공시목록 → 대상 종목의 공급계약·자사주·유상증자 · 임원·주요주주 순매수(insider_buy) 이벤트 목록.
    backfill=True 면 최근 14일 (진행 상태는 _bf["KR"], 끝나면 _bf["KR"]["done"] = True)."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        STATUS["KR"]["note"] = "DART_API_KEY 미설정"
        if backfill:
            _bf_fail("KR", "DART_API_KEY 미설정", fatal=True)      # 키가 없으면 채울 수 없음 (다시 시도하지 않음)
        return []
    import requests
    get = get or requests.get
    now = now or datetime.now(KST)
    tg = tg if tg is not None else targets()
    if backfill:
        out = []
        _dart_backfill(key, tg, now, deadline, get, emit or out.extend)
        return out
    day = now.astimezone(KST).date()
    if _dart_seen["day"] != day:
        _dart_seen.update(day=day, ids=set())
    cold = not _dart_seen["ids"]
    code2t = _dart_code_map(tg)
    out, docs = [], 0
    for page in range(1, (DART_PAGES_COLD if cold else DART_PAGES_WARM) + 1):
        if deadline and time.time() > deadline:
            break
        try:
            r = get(DART_LIST, params={"crtfc_key": key, "bgn_de": day.strftime("%Y%m%d"), "end_de": day.strftime("%Y%m%d"),
                                       "page_no": page, "page_count": 100}, timeout=10)
            j = r.json()
        except Exception as e:
            log(f"DART 목록 실패 p{page}: {e}")
            break
        if j.get("status") not in ("000",):
            if j.get("status") != "013":              # 013 = 조회된 데이터 없음
                log(f"DART 목록 응답 {j.get('status')} {j.get('message')}")
            break
        items = j.get("list") or []
        fresh = [it for it in items if it.get("rcept_no") not in _dart_seen["ids"]]
        for it in fresh:
            _dart_seen["ids"].add(it.get("rcept_no"))
            hit = code2t.get(str(it.get("stock_code") or "").strip().upper())
            if not hit:
                continue
            if is_insider_report(it.get("report_nm")):
                _dart_ins_pending.append({"it": it, "hit": hit, "tries": 0})
                continue
            cls = classify_dart(it.get("report_nm"))
            want_doc = bool(cls) and cls[:2] == ("contract", "positive") and docs < DART_DOC_PER_RUN
            docs += int(want_doc)
            ev = _dart_event(it, hit, key, get, day, want_doc)
            if ev:
                out.append(ev)
        if not items or len(items) < 100 or (not cold and len(fresh) < len(items)):
            break                                     # 마지막 페이지 · 이미 본 구간에 닿음
        time.sleep(0.1)
    if _dart_ins_pending:
        del _dart_ins_pending[:-INSIDER_PENDING_MAX]   # 너무 쌓이면 오래된 것부터 버린다
        evs, _ = process_insider(key, get, _dart_ins_pending, DART_ELESTOCK_PER_RUN, day, deadline=deadline, retry=True)
        out.extend(evs)
    return out


# ══ 미국: SEC EDGAR ═════════════════════════════════════════════════════════════
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FEED = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_ARCH = "https://www.sec.gov/Archives/edgar/data"
_sec = {"last": 0.0, "lock": threading.Lock(), "blocked_until": 0.0, "map": None, "map_at": 0.0,
        "seen": set(), "scan_at": {}}


_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")


def _first_email(raw, strip_mailto=False):
    """'a@x.com, b@y.com' · 'mailto:a@x.com' → 첫 번째 올바른 이메일 | None (https:// 같은 URL 은 버림)."""
    for part in re.split(r"[,;\s]+", str(raw or "")):
        part = part.strip()
        if strip_mailto and part.lower().startswith("mailto:"):
            part = part[7:].strip()
        if _EMAIL_RE.match(part):
            return part
    return None


def sec_user_agent():
    """SEC 요청 User-Agent. SEC_USER_AGENT 가 없으면 이미 있는 연락처(TOSS_OWNER_EMAIL → VAPID_SUBJECT 의 mailto:)로
    'StockAnalyzer {email}' 을 만든다. 셋 다 없을 때만 기본값. (이메일은 로그에 남기지 않는다)"""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    email = _first_email(os.environ.get("TOSS_OWNER_EMAIL")) or _first_email(os.environ.get("VAPID_SUBJECT"), strip_mailto=True)
    return f"StockAnalyzer {email}" if email else "StockAnalyzer (contact@example.com)"


def sec_get(url, params=None, get=None, max_bytes=None):
    """SEC 요청: User-Agent 헤더 + 요청 간 0.2초 이상. 403/429 면 10분 쉰다. 실패하면 None.
    max_bytes 를 주면 스트림으로 앞부분만 읽고 끊는다 → r.head (문자열). 큰 설명서·8-K 본문을 다 받지 않는다."""
    import requests
    get = get or requests.get
    if time.time() < _sec["blocked_until"]:
        return None
    hdr = {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}
    with _sec["lock"]:
        wait = SEC_GAP - (time.time() - _sec["last"])
        if wait > 0:
            time.sleep(wait)
        _sec["last_status"] = None
        try:
            if max_bytes:
                r = get(url, params=params, headers=hdr, timeout=12, stream=True)
            else:
                r = get(url, params=params, headers=hdr, timeout=12)
            _sec["last_status"] = r.status_code
            if max_bytes and r.status_code == 200:
                r.head = _read_head(r, max_bytes)
        except Exception as e:
            log(f"SEC 요청 실패 {url}: {e}")
            return None
        finally:
            _sec["last"] = time.time()
    if r.status_code in (403, 429):
        _sec["blocked_until"] = time.time() + 600
        log(f"SEC {r.status_code} — 10분 쉼 (User-Agent·요청 간격 확인)")
        return None
    return r if r.status_code == 200 else None


def _read_head(r, max_bytes):
    """스트림 응답에서 앞 max_bytes 만 읽고 연결을 닫는다 → 문자열."""
    buf = b""
    try:
        it = getattr(r, "iter_content", None)
        if it is None:
            return (r.text or "")[:max_bytes]
        for chunk in it(chunk_size=8192):
            buf += chunk or b""
            if len(buf) >= max_bytes:
                break
    finally:
        try:
            r.close()
        except Exception:
            pass
    return buf[:max_bytes].decode(getattr(r, "encoding", None) or "utf-8", "replace")


def sec_ticker_map(get=None):
    """company_tickers.json (하루 1회) → {TICKER: cik(int)}. 클래스주는 'BRK-B' 형식."""
    if _sec["map"] is not None and time.time() - _sec["map_at"] < 86400:
        return _sec["map"]
    r = sec_get(SEC_TICKERS, get=get)
    if r is None:
        return _sec["map"] or {}
    try:
        m = {}
        for v in (r.json() or {}).values():
            t = str(v.get("ticker") or "").upper().replace(".", "-")
            if t:
                m.setdefault(t, int(v.get("cik_str")))
        _sec.update(map=m, map_at=time.time())
    except Exception as e:
        log(f"SEC 티커 매핑 실패: {e}")
    return _sec["map"] or {}


_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_text):
    """EDGAR 최신 공시 Atom → [{form, cik, role, company, accession, url, updated(UTC), summary}]"""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for e in root.findall(f"{_ATOM}entry"):
        title = (e.findtext(f"{_ATOM}title") or "").strip()
        m = re.match(r"\s*([^\s].*?)\s+-\s+(.*?)\s+\((\d{6,10})\)\s+\((\w+)\)\s*$", title)
        if not m:
            continue
        link = e.find(f"{_ATOM}link")
        href = link.get("href") if link is not None else ""
        eid = e.findtext(f"{_ATOM}id") or ""
        am = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", eid) or re.search(r"(\d{10}-\d{2}-\d{6})", href)
        if not am:
            continue
        upd = e.findtext(f"{_ATOM}updated") or ""
        try:
            u = datetime.fromisoformat(upd.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            u = _utcnow()
        out.append({"form": m.group(1).strip(), "company": m.group(2).strip(), "cik": int(m.group(3)), "role": m.group(4),
                    "accession": am.group(1), "url": href, "updated": u,
                    "summary": html.unescape(re.sub(r"<[^>]+>", " ", e.findtext(f"{_ATOM}summary") or ""))})
    return out


def _txt(node, path):
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    v = el.findtext("value")
    v = v if v is not None else el.text
    return v.strip() if v and v.strip() else None


def _flag(v):
    return str(v or "").strip().lower() in ("1", "true")


def parse_form4(xml_text):
    """Form 4 XML → {symbol, owners:[{name, role}], buys:[{date, shares, price}], shares, value, avg_price}.
    buys 는 실제 장내 매수(transactionCode == 'P', 취득 'A')만. 옵션 행사(M)·증여(G)·보상(A)·매도(S) 등은 버린다."""
    xml_text = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text or "").strip()
    root = ET.fromstring(xml_text)
    owners = []
    for ro in root.findall("reportingOwner"):
        nm = (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if rel is not None:
            if _flag(rel.findtext("isOfficer")):
                roles.append((rel.findtext("officerTitle") or "Officer").strip())
            if _flag(rel.findtext("isDirector")):
                roles.append("Director")
            if _flag(rel.findtext("isTenPercentOwner")):
                roles.append("10% 주주")
            if _flag(rel.findtext("isOther")) and rel.findtext("otherText"):
                roles.append(rel.findtext("otherText").strip())
        owners.append({"name": nm, "role": " · ".join(dict.fromkeys(roles)) or "내부자"})
    buys = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip().upper()
        ad = _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode")
        if code != "P" or (ad and ad.upper() != "A"):
            continue
        try:
            sh = float(_txt(tx, "transactionAmounts/transactionShares") or 0)
        except ValueError:
            sh = 0
        try:
            px = float(_txt(tx, "transactionAmounts/transactionPricePerShare") or "nan")
        except ValueError:
            px = float("nan")
        if sh > 0:
            buys.append({"date": _txt(tx, "transactionDate"), "shares": sh, "price": None if px != px else px})
    shares = sum(b["shares"] for b in buys)
    priced = [b for b in buys if b["price"]]
    value = sum(b["shares"] * b["price"] for b in priced) if priced else None
    avg = (value / sum(b["shares"] for b in priced)) if priced else None
    return {"symbol": (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper(), "owners": owners,
            "buys": buys, "shares": shares, "value": value, "avg_price": avg}


def form4_summary(p):
    o = p["owners"][0] if p["owners"] else {"name": "내부자", "role": "내부자"}
    who = f"{_name_order(o['name'])} ({o['role']})" + (f" 외 {len(p['owners']) - 1}명" if len(p["owners"]) > 1 else "")
    px = f" × ${p['avg_price']:,.2f}" if p.get("avg_price") else ""
    tot = f" · 총 ${p['value']:,.0f}" if p.get("value") else ""
    return f"{who} · {p['shares']:,.0f}주{px}{tot} 장내 매수"


_CORP_WORDS = re.compile(r"\b(INC|CORP|CORPORATION|CO|LLC|L\.?P|LP|LTD|TRUST|FUND|HOLDINGS?|PARTNERS|CAPITAL|GROUP|"
                         r"MANAGEMENT|ADVISORS?|BANK|PLC|SA|AG|NV|INVESTMENTS?|FOUNDATION)\b\.?", re.I)


def _name_order(nm):
    """EDGAR 개인 'LAST FIRST M' → 'First M Last'. 회사·펀드 이름은 순서를 바꾸지 않는다."""
    nm = (nm or "").strip()
    parts = nm.split()
    if not parts:
        return "내부자"
    if _CORP_WORDS.search(nm):
        keep = {"LLC", "LP", "L.P.", "PLC", "AG", "SA", "NV", "II", "III", "IV", "USA", "US"}
        return " ".join(w if (not w.isupper() or w in keep) else w.capitalize() for w in parts)
    if len(parts) >= 2 and nm.isupper():
        return " ".join(w.capitalize() for w in parts[1:] + parts[:1])
    return nm


def parse_8k_items(text):
    """피드 요약 또는 헤더(.hdr.sgml)에서 8-K 항목 번호 → ['1.01', '2.02', …]"""
    found = re.findall(r"(?:Item\s+|<ITEMS>\s*)(\d\.\d{2})", text or "", flags=re.I)
    return list(dict.fromkeys(found))


def _filing_dir(cik, acc):
    return f"{SEC_ARCH}/{int(cik)}/{acc.replace('-', '')}"


def _form4_xml_url(cik, acc, get=None):
    r = sec_get(f"{_filing_dir(cik, acc)}/index.json", get=get)
    if r is None:
        return None
    try:
        items = ((r.json() or {}).get("directory") or {}).get("item") or []
    except Exception:
        return None
    names = [i.get("name") for i in items if str(i.get("name") or "").lower().endswith(".xml")]
    names = [n for n in names if "filingsummary" not in n.lower()]
    if not names:
        return None
    names.sort(key=lambda n: (0 if "form4" in n.lower() or "f4" in n.lower() else 1, len(n)))
    return f"{_filing_dir(cik, acc)}/{names[0]}"


def _scan_feed(form, since, deadline, get=None):
    """최신 공시 피드를 since(UTC) 이후까지 넘겨 본다."""
    out = []
    for page in range(SEC_FEED_PAGES):
        if deadline and time.time() > deadline:
            break
        r = sec_get(SEC_FEED, params={"action": "getcurrent", "type": form, "company": "", "dateb": "",
                                      "owner": "include", "start": page * 100, "count": 100, "output": "atom"}, get=get)
        if r is None:
            break
        rows = parse_feed(r.text)
        out += rows
        if not rows or len(rows) < 100 or (since and min(x["updated"] for x in rows) < since):
            break
    return out


def _8k_event(items, cik, acc, t, nm, at):
    """8-K 1.01·2.01·2.02. Item 1.01(중요 계약 체결)이 있으면 한국 공급계약과 같은 '수주·계약'(contract)으로."""
    keep = [i for i in items if i in SEC_ITEMS]
    if not keep:
        return None
    senti = "positive" if any(SEC_ITEMS[i][1] == "positive" for i in keep) else "neutral"
    cat = "contract" if SEC_CONTRACT_ITEM in keep else "material_8k"
    return {"market": "US", "ticker": t, "name": nm, "source": "SEC", "category": cat, "sentiment": senti,
            "title": "8-K · " + " · ".join(SEC_ITEMS[i][0] for i in keep),
            "summary": " · ".join(f"Item {i} {SEC_ITEMS[i][0]}" for i in keep) + ("" if len(keep) == len(items) else
                       f" (그 외 {', '.join(i for i in items if i not in SEC_ITEMS)})"),
            "source_url": f"{_filing_dir(cik, acc)}/{acc}-index.htm", "external_id": f"SEC:{acc}", "disclosed_at": at}


_EQUITY_RE = re.compile(r"\b(common stock|common shares|ordinary shares|class [a-c] common|american depositary shares|"
                        r"at[- ]the[- ]market)\b")
_DEBT_RE = re.compile(r"\b(senior notes|subordinated notes|structured notes|medium[- ]term notes|debentures|notes due|"
                      r"fixed[- ]rate notes|floating[- ]rate notes|market[- ]linked|certificates of deposit|"
                      r"callable (contingent )?(interest|coupon) notes|buffered|trigger notes)\b")
_EQUITY_OFFER_RE = re.compile(r"\b(offering|offer|issue|sell|selling|sale of)\b[^.]{0,120}?"
                              r"\b(shares of (our )?(class [a-c] )?(common stock|ordinary shares)|common stock|ordinary shares|"
                              r"american depositary shares)\b")
_LINKED_RE = re.compile(r"\b(notes?|securities)\b[^.]{0,100}\blinked to\b|\bauto[- ]?callable\b")
_ATM_RE = re.compile(r"\bat[- ]the[- ]market\b|\bsales agents?\b|\bequity distribution agreement\b")
_BUYBACK_RE = re.compile(r"\b(share repurchase|stock repurchase|repurchase program|buyback program|share buyback|stock buyback|"
                         r"repurchase up to|repurchase of [^.;]{0,80}?common (?:stock|shares)|"
                         r"repurchase (?:its |our )?shares|repurchase authori[sz]ation|share retirement|stock retirement)\b")
# 발표 문맥: 승인·발표·확대·신규·추가·'최대 $' · 소각 등. 이게 근처에 없으면 상투적 언급(위험 요인·과거 실적표)으로 본다
_BUYBACK_CTX_RE = re.compile(r"\b(authoriz\w*|approv\w*|announc\w*|expand\w*|increas\w*|new|additional|program|up to|"
                             r"retire\w*|cancel\w*|launch\w*|adopt\w*)\b")
# 오탐: 매입 중단·종료·'매입하지 않았다'·미래 예측 면책 문구
_BUYBACK_NEG_RE = re.compile(r"\b(suspend\w*|terminat\w*|paus\w*|discontinu\w*|no (?:shares|stock) (?:were |was )?repurchased|"
                             r"did not repurchase|has not repurchased)\b")
_USD_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion)?", re.I)


def _plain(text):
    """HTML → 소문자 평문 (태그 제거 · 엔티티 해제 · 공백 정리)."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip().lower()


def fmt_usd(v):
    if v is None:
        return None
    if v >= 1e9:
        return f"${v / 1e9:,.2f}".rstrip("0").rstrip(".") + "B"
    if v >= 1e6:
        return f"${v / 1e6:,.1f}".rstrip("0").rstrip(".") + "M"
    return f"${v:,.0f}"


def _usd_near(t, pos, span=300):
    """t[pos] 주변에서 가장 가까운 달러 금액 → 숫자 (million/billion 반영)."""
    best = None
    for m in _USD_RE.finditer(t, max(0, pos - span), min(len(t), pos + span)):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        v *= 1e9 if unit == "billion" else 1e6 if unit == "million" else 1
        if v < 1e6:
            continue                                   # 주당 가격 같은 작은 금액은 버림
        d = abs(m.start() - pos)
        if best is None or d < best[0]:
            best = (d, v)
    return best[1] if best else None


def classify_424b5(text):
    """424B5 설명서 앞부분 → 보통주 유상증자면 {"title", "summary"}, 채권·구조화 채권이면 None.
    은행·증권사가 매일 내는 Senior Notes · Medium-Term Notes · 주가 연계(linked to) 채권은 유상증자가 아니다."""
    t = _plain(text)
    if not t:
        return None
    head = t[:5000]
    eq, debt = len(_EQUITY_RE.findall(t)), len(_DEBT_RE.findall(t))
    if eq == 0 or _LINKED_RE.search(head):
        return None
    offer = _EQUITY_OFFER_RE.search(head)
    if debt and (debt >= eq or not offer):
        return None                                    # 채권 위주 (기본 설명서의 '보통주' 언급만 있는 경우 포함)
    atm = bool(_ATM_RE.search(t))
    amt = None
    m = re.search(r"aggregate offering price of up to", t)
    if m:
        amt = _usd_near(t, m.end(), 120)
    shares = re.search(r"([\d,]{5,}) (?:shares of (?:our )?(?:class [a-c] )?(?:common stock|ordinary shares)|american depositary shares)", head)
    parts = []
    if amt:
        parts.append(f"최대 {fmt_usd(amt)}")
    if shares:
        parts.append(f"{shares.group(1)}주")
    what = "시장가(ATM) 신주 발행" if atm else "보통주 공모 신주 발행"
    return {"title": "424B5 · ATM 유상증자 (시장가 신주 발행)" if atm else "424B5 · 공모 유상증자 (신주 발행)",
            "summary": " · ".join(parts + [what]) + " — 주식 수 희석 주의"}


def classify_buyback(text):
    """8-K·보도자료 앞부분 → 자사주 매입 '발표'면 {"summary", "amount"} | None.
    키워드 주변(±200자)에 승인·발표·확대·신규·최대 같은 발표 문맥이 있고, 중단·종료 같은 부정 문맥이 없을 때만."""
    t = _plain(text)
    for m in _BUYBACK_RE.finditer(t):
        win = t[max(0, m.start() - 200): m.end() + 200]
        if not _BUYBACK_CTX_RE.search(win) or _BUYBACK_NEG_RE.search(win):
            continue
        amt = _usd_near(t, m.start())
        return {"summary": f"자사주 매입 {fmt_usd(amt)} 발표" if amt else "자사주 매입 프로그램 발표", "amount": amt}
    return None


def needs_8k_doc(items):
    """문서 앞부분을 확인해야 하는 8-K: 3.02(이미 유상증자)가 아니고 8.01·7.01 이 있는 것."""
    return SEC_OFFERING_ITEM not in items and any(i in items for i in SEC_BUYBACK_ITEMS)


def sec_8k_event(items, cik, acc, t, nm, at, head=None):
    """8-K 1건 → 이벤트 | None. 우선순위: Item 3.02 유상증자(희석 주의) > 8.01/7.01 자사주 매입(문서 확인) > 1.01·2.01·2.02."""
    base = {"market": "US", "ticker": t, "name": nm, "source": "SEC", "source_url": f"{_filing_dir(cik, acc)}/{acc}-index.htm",
            "external_id": f"SEC:{acc}", "disclosed_at": at}
    others = lambda skip: ", ".join(i for i in items if i not in skip)
    if SEC_OFFERING_ITEM in items:
        o = others({SEC_OFFERING_ITEM})
        return dict(base, category="offering", sentiment="negative", title="8-K 사모 유상증자·신주 발행 (Item 3.02)",
                    summary="Item 3.02 미등록 주식 발행 — 주식 수 희석 주의" + (f" (함께: {o})" if o else ""))
    if head is not None and needs_8k_doc(items):
        bb = classify_buyback(head)
        if bb:
            it = next(i for i in items if i in SEC_BUYBACK_ITEMS)
            o = others({it})
            return dict(base, category="buyback", sentiment="positive", title=f"8-K 자사주 매입 발표 (Item {it})",
                        summary=bb["summary"] + (f" (함께: {o})" if o else ""), external_id=_bb_xid(acc))
    ev = _8k_event(items, cik, acc, t, nm, at)
    return dict(base, **{k: ev[k] for k in ("category", "sentiment", "title", "summary")}) if ev else None


def _424b5_event(head, cik, acc, t, nm, at):
    c = classify_424b5(head)
    if not c:
        return None
    return {"market": "US", "ticker": t, "name": nm, "source": "SEC", "category": "offering", "sentiment": "negative",
            "title": c["title"], "summary": c["summary"], "source_url": f"{_filing_dir(cik, acc)}/{acc}-index.htm",
            "external_id": f"SEC:{acc}", "disclosed_at": at}


def _primary_doc_url(cik, acc, form, get=None, doc=None):
    """본문 문서 주소. 제출 목록의 primaryDocument 가 있으면 그것, 없으면 index.json 에서 고른다 (+1 요청)."""
    if doc and not doc.lower().endswith(".xml"):
        return f"{_filing_dir(cik, acc)}/{doc.split('/')[-1]}"
    r = sec_get(f"{_filing_dir(cik, acc)}/index.json", get=get)
    if r is None:
        return None
    try:
        items = ((r.json() or {}).get("directory") or {}).get("item") or []
    except Exception:
        return None
    names = [str(i.get("name") or "") for i in items]
    names = [n for n in names if n.lower().endswith((".htm", ".html", ".txt")) and "-index" not in n.lower()
             and not re.match(r"(?i)^(r\d+\.htm|filingsummary)", n) and n.lower() != f"{acc}.txt"]
    hint = form.lower().replace("-", "")
    pick = [n for n in names if hint in n.lower().replace("-", "")] or [n for n in names if not re.match(r"(?i)ex|.*ex-?\d", n)] or names
    return f"{_filing_dir(cik, acc)}/{pick[0]}" if pick else None


def _sec_time(s, fallback_date=None):
    """'2026-11-20T21:05:12.000Z' → UTC naive. 없으면 제출일 자정(ET)."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        try:
            d0 = datetime.strptime(str(fallback_date), "%Y-%m-%d")
            return d0.replace(tzinfo=ET_TZ).astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            return _utcnow()


def parse_submissions(js, since_date):
    """data.sec.gov/submissions JSON → [{form, accession, date, at, doc, items}] (since_date 이후 Form 4 · 8-K · 424B5 만)."""
    rec = ((js or {}).get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out = []
    for i, f in enumerate(forms):
        if f not in ("4", "4/A", "8-K", "8-K/A", "424B5"):
            continue
        fd = (rec.get("filingDate") or [None] * len(forms))[i]
        if not fd or fd < since_date:
            continue
        out.append({"form": f, "accession": (rec.get("accessionNumber") or [""] * len(forms))[i], "date": fd,
                    "at": _sec_time((rec.get("acceptanceDateTime") or [None] * len(forms))[i], fd),
                    "doc": (rec.get("primaryDocument") or [""] * len(forms))[i] or "",
                    "items": [x.strip() for x in str((rec.get("items") or [""] * len(forms))[i] or "").split(",") if x.strip()]})
    return out


def _efts_hits(js):
    """EFTS 응답 → [{adsh, ciks(int), file, file_type, form, file_date, name}]. 필드가 비면 _id("adsh:파일명")에서 복구."""
    out = []
    for h in ((js or {}).get("hits") or {}).get("hits") or []:
        src = h.get("_source") or {}
        _id = str(h.get("_id") or "")
        adsh = src.get("adsh") or (_id.split(":", 1)[0] if ":" in _id else None)
        fname = _id.split(":", 1)[1] if ":" in _id else None
        ciks = []
        for c in src.get("ciks") or []:
            try:
                ciks.append(int(str(c)))
            except ValueError:
                pass
        if not adsh or not ciks:
            continue
        out.append({"adsh": adsh, "ciks": ciks, "file": fname, "file_type": str(src.get("file_type") or "").upper(),
                    "form": src.get("form") or (src.get("root_forms") or [""])[0], "file_date": src.get("file_date"),
                    "name": (src.get("display_names") or [None])[0]})
    return out


def _bb_xid(adsh):
    """EFTS 로 찾은 자사주 매입의 고유 키. 같은 8-K 가 실적 발표(2.02 → 8-K 분류)로 이미 저장돼 있어도 자사주 건은 따로 남긴다."""
    return f"SEC:{adsh}:bb"


def efts_candidates(cik2t, now, days, get=None, deadline=None):
    """EFTS 로 최근 days 일 8-K 중 자사주 매입 키워드가 있는 공시 → 추적 유니버스 종목만, 공시(adsh)당 1건.
    같은 공시에서 8-K 본문과 EX-99.1 이 둘 다 걸리면 보도자료(EX-99.x)를 우선한다. → [후보] (최대 EFTS_PAGES 요청)"""
    end = now.astimezone(ET_TZ).date()
    start = end - timedelta(days=max(days - 1, 0))
    by = {}
    for p in range(EFTS_PAGES):
        if deadline and time.time() > deadline:
            break
        r = sec_get(SEC_EFTS, params={"q": EFTS_QUERY, "forms": "8-K", "dateRange": "custom",
                                      "startdt": start.isoformat(), "enddt": end.isoformat(), "from": p * 100}, get=get)
        if r is None:
            break
        try:
            js = r.json()
        except ValueError:
            break
        hits = _efts_hits(js)
        for h in hits:
            cik = next((c for c in h["ciks"] if c in cik2t), None)
            if cik is None:
                continue                                   # 추적 유니버스 밖
            cur = by.get(h["adsh"])
            ex = h["file_type"].startswith("EX-99")
            if cur is None or (ex and not cur["file_type"].startswith("EX-99")):
                by[h["adsh"]] = dict(h, cik=cik)
        if len(((js or {}).get("hits") or {}).get("hits") or []) < 100:
            break
    return sorted(by.values(), key=lambda c: (c.get("file_date") or ""), reverse=True)


def efts_event(c, head, cik2t):
    """EFTS 후보 + 문서 앞부분 → 자사주 매입 이벤트 | None."""
    bb = classify_buyback(head or "")
    if not bb:
        return None
    t, nm = cik2t[c["cik"]]
    ex = c["file_type"].startswith("EX-99")
    try:
        d = datetime.fromisoformat(str(c.get("file_date"))[:10]).replace(hour=16, tzinfo=ET_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        d = _utcnow()
    return {"market": "US", "ticker": t, "name": nm or c.get("name") or t, "source": "SEC", "category": "buyback",
            "sentiment": "positive", "title": "8-K 자사주 매입 발표" + (" (보도자료)" if ex else ""),
            "summary": bb["summary"], "source_url": f"{_filing_dir(c['cik'], c['adsh'])}/{c['adsh']}-index.htm",
            "external_id": _bb_xid(c["adsh"]), "disclosed_at": d}


def _efts_known(adshs):
    """이미 자사주로 저장된 공시(EFTS 키 또는 예전 8-K 본문 판정 'SEC:adsh' + buyback)."""
    E = D["DisclosureEvent"]
    if not adshs:
        return set()
    rows = D["db"].session.query(E.external_id, E.category).filter(
        E.external_id.in_([_bb_xid(a) for a in adshs] + [f"SEC:{a}" for a in adshs])).all()
    known = set()
    for xid, cat in rows:
        a = xid[4:].split(":")[0]
        if xid.endswith(":bb") or cat == "buyback":
            known.add(a)
    return known


def collect_sec_buybacks(cik2t, now, days=EFTS_DAYS, get=None, deadline=None, doc_budget=EFTS_DOCS_PER_RUN, cands=None):
    """EFTS 후보 → 문서(보도자료 우선) 앞 128KB 확인 → 자사주 매입 이벤트 목록.
    cands 를 주면 검색 없이 그 목록을 처리한다(백필 이어서). 문서 확인은 doc_budget 건까지, 처리한 후보는 목록에서 뺀다."""
    if cands is None:
        cands = efts_candidates(cik2t, now, days, get=get, deadline=deadline)
    known = _efts_known([c["adsh"] for c in cands])
    out = []
    while cands and doc_budget > 0:
        if deadline and time.time() > deadline:
            break
        c = cands.pop(0)
        if c["adsh"] in known:
            continue
        url = (f"{_filing_dir(c['cik'], c['adsh'])}/{c['file']}" if c.get("file")
               else _primary_doc_url(c["cik"], c["adsh"], "8-K", get=get))
        doc_budget -= 1
        r = sec_get(url, get=get, max_bytes=DOC_HEAD_BYTES) if url else None
        if r is None:
            if _sec_blocked():
                cands.insert(0, c)                      # 막혔으면 다음에 다시
                break
            continue
        ev = efts_event(c, getattr(r, "head", None), cik2t)
        if ev:
            out.append(ev)
            known.add(c["adsh"])
    return out


def _sec_blocked():
    return time.time() < _sec["blocked_until"]


def _sec_backfill(cik2t, now, deadline, get, emit):
    """최근 14일: 대상 회사를 하나씩 — 최근 제출 목록 1번 → 8-K 는 항목으로 바로 판정·저장, 그 회사 Form 4 는 원본 XML 을
    최신부터 최대 10건 바로 받아 장내 매수(P)만 저장. 회사 단위로 진행 위치를 _bf["US"] 에 남겨 다음 tick 에 이어서.
    SEC 가 막으면(403/429) 위치를 그대로 두고 쉰 뒤 같은 회사부터 다시."""
    st = _bf["US"]
    E = D["DisclosureEvent"]
    if "ciks" not in st:
        st.update(ciks=sorted(cik2t), i=0, cur=None, calls=0, f4=0, docs=0, tries=0,
                  since=(now.astimezone(ET_TZ).date() - timedelta(days=BACKFILL_DAYS)).isoformat())
    st["total"] = len(st["ciks"])
    while st["i"] < len(st["ciks"]):
        if deadline and time.time() > deadline:
            return
        if _sec_blocked():
            st["note"] = "SEC 요청 제한(403/429) — 잠시 뒤 이어서"
            return
        cik = st["ciks"][st["i"]]
        t, nm = cik2t[cik]
        if st["cur"] is None:                          # 이 회사 제출 목록
            r = sec_get(SEC_SUBMISSIONS.format(cik=cik), get=get)
            st["calls"] += 1
            if r is None:
                if _sec_blocked():
                    st["note"] = "SEC 요청 제한(403/429) — 잠시 뒤 이어서"
                    st["errors"] = st.get("errors", 0) + 1
                    if st["errors"] >= BACKFILL_MAX_ERRORS:
                        _bf_fail("US", "SEC 요청이 계속 거부됨 (User-Agent 확인)", fatal=True)
                    return
                code = _sec.get("last_status")
                if code is not None and 400 <= code < 500:
                    st["tries"], st["i"] = 0, st["i"] + 1        # 404 등: 이 회사는 목록이 없음 → 건너뜀
                    continue
                st["tries"] += 1                                 # 연결 실패·5xx: 다음 tick 에 같은 회사 (세 번까지)
                if st["tries"] < 3:
                    st["note"] = "SEC 연결 오류 — 다음 회차에 이어서"
                    return
                st["tries"], st["i"] = 0, st["i"] + 1
                continue
            st["tries"], st["errors"], st["note"] = 0, 0, None
            try:
                rows = parse_submissions(r.json(), st["since"])
            except Exception as e:
                log(f"SEC 제출 목록 파싱 실패 CIK {cik}: {e}")
                rows = []
            # 8-K 중 문서 확인이 필요 없는 것(3.02 유상증자 · 1.01/2.01/2.02)은 바로 저장
            emit([ev for ev in (sec_8k_event(f["items"], cik, f["accession"], t, nm, f["at"])
                                for f in rows if f["form"].startswith("8-K") and not needs_8k_doc(f["items"])) if ev])
            docs = sorted([("424" if f["form"] == "424B5" else "8k", f["at"], f["accession"], f["doc"], f["items"]) for f in rows
                           if f["form"] == "424B5" or (f["form"].startswith("8-K") and needs_8k_doc(f["items"]))],
                          key=lambda x: x[1], reverse=True)
            f4 = sorted([("f4", f["at"], f["accession"], f["doc"], []) for f in rows if f["form"] in ("4", "4/A")],
                        key=lambda x: x[1], reverse=True)
            accs = [x[2] for x in docs + f4]
            have = set()
            if accs:
                have = {x[0] for x in D["db"].session.query(E.external_id)
                        .filter(E.external_id.in_([f"SEC:{a}" for a in accs])).all()}
            q = [x for x in docs if f"SEC:{x[2]}" not in have][:SEC_BACKFILL_DOCS_PER_CO] + \
                [x for x in f4 if f"SEC:{x[2]}" not in have][:SEC_BACKFILL_FORM4_PER_CO]
            st["cur"] = {"q": q, "k": 0}
        cur = st["cur"]
        while cur["k"] < len(cur["q"]):                # 이 회사 문서 확인 · Form 4 → 찾는 대로 바로 저장
            if deadline and time.time() > deadline:
                return
            if _sec_blocked():
                st["note"] = "SEC 요청 제한(403/429) — 잠시 뒤 이어서"
                return
            kind, at, acc, doc, items = cur["q"][cur["k"]]
            if (kind == "f4" and st["f4"] >= SEC_BACKFILL_FORM4) or (kind != "f4" and st["docs"] >= SEC_BACKFILL_DOCS):
                cur["k"] += 1
                continue
            if kind != "f4":                           # 8-K(8.01/7.01) · 424B5: 문서 앞 16KB
                url = _primary_doc_url(cik, acc, "424B5" if kind == "424" else "8-K", get=get, doc=doc)
                r = sec_get(url, get=get, max_bytes=DOC_HEAD_BYTES) if url else None
                st["calls"] += 1
                if r is None and _sec_blocked():
                    return                             # 같은 문서부터 다시
                cur["k"] += 1
                st["docs"] += 1
                head = getattr(r, "head", None) if r is not None else None
                ev = (_424b5_event(head, cik, acc, t, nm, at) if kind == "424" and head
                      else sec_8k_event(items, cik, acc, t, nm, at, head) if kind == "8k" else None)
                if ev:
                    emit([ev])
                continue
            raw = doc.split("/")[-1] if doc.lower().endswith(".xml") else None   # 'xslF345X05/form4.xml' → 원본 'form4.xml'
            url = f"{_filing_dir(cik, acc)}/{raw}" if raw else _form4_xml_url(cik, acc, get=get)
            r = sec_get(url, get=get) if url else None
            st["calls"] += 1
            if r is None and _sec_blocked():
                return                                 # 같은 Form 4 부터 다시
            cur["k"] += 1
            st["f4"] += 1
            if r is None:
                continue
            try:
                p = parse_form4(r.text)
            except ET.ParseError as e:
                log(f"Form 4 XML 파싱 실패 {acc}: {e}")
                continue
            _sec["seen"].add(acc)
            if p["buys"]:
                emit([{"market": "US", "ticker": t, "name": nm, "source": "SEC", "category": "insider_buy",
                       "sentiment": "positive", "title": "Form 4 · 내부자 장내 매수", "summary": form4_summary(p),
                       "source_url": f"{_filing_dir(cik, acc)}/{acc}-index.htm", "external_id": f"SEC:{acc}",
                       "disclosed_at": at}])
        st["cur"], st["i"] = None, st["i"] + 1
    st["done"] = True


def collect_sec(tg=None, now=None, deadline=None, get=None, backfill=False, emit=None):
    """Form 4 장내 매수 · 8-K 핵심 항목 이벤트 목록. backfill=True 면 최근 14일 (진행 상태는 _bf["US"])."""
    tg = tg if tg is not None else targets()
    tmap = sec_ticker_map(get=get)
    if not tmap:
        STATUS["US"]["note"] = "SEC 티커 매핑을 받지 못함"
        if backfill:
            _bf_fail("US", "SEC 티커 매핑(company_tickers.json)을 받지 못함")   # 다음 tick 에 재시도 (연속 20번이면 종료)
        return []
    cik2t = {}
    for t, nm in tg["US"].items():
        c = tmap.get(t.upper().replace(".", "-"))
        if c:
            cik2t.setdefault(c, (t, nm))
    if backfill:
        out = []
        _sec_backfill(cik2t, now or datetime.now(timezone.utc), deadline, get, emit or out.extend)
        return out
    E = D["DisclosureEvent"]
    out, budget = [], SEC_FILINGS_PER_RUN
    for form in SEC_FORMS:
        since = _sec["scan_at"].get(form)
        started = _utcnow()
        feed = _scan_feed(form, (since - timedelta(minutes=10)) if since else None, deadline, get=get)
        cand, seen_acc = [], set()
        for f in feed:
            if f["form"] not in {"4": ("4", "4/A"), "8-K": ("8-K", "8-K/A"), "424B5": ("424B5",)}[form]:
                continue
            if form == "4" and f["role"] != "Issuer":
                continue                              # Form 4 는 발행회사(Issuer) 항목만 (보고자 항목과 중복)
            if f["cik"] not in cik2t or f["accession"] in _sec["seen"] or f["accession"] in seen_acc:
                continue
            seen_acc.add(f["accession"])
            cand.append(f)
        if cand:
            have = {r[0] for r in D["db"].session.query(E.external_id)
                    .filter(E.external_id.in_([f"SEC:{c['accession']}" for c in cand])).all()}
            cand = [c for c in cand if f"SEC:{c['accession']}" not in have]
        for f in cand:
            if budget <= 0 or (deadline and time.time() > deadline):
                break
            t, nm = cik2t[f["cik"]]
            index_url = f"{_filing_dir(f['cik'], f['accession'])}/{f['accession']}-index.htm"
            ev = None
            if form == "4":
                budget -= 1
                xu = _form4_xml_url(f["cik"], f["accession"], get=get)
                r = sec_get(xu, get=get) if xu else None
                if r is None:
                    continue                          # 다음 회차에 다시 (seen 에 넣지 않음)
                try:
                    p = parse_form4(r.text)
                except ET.ParseError as e:
                    log(f"Form 4 XML 파싱 실패 {f['accession']}: {e}")
                    p = None
                if p and p["buys"]:
                    ev = {"category": "insider_buy", "sentiment": "positive", "title": "Form 4 · 내부자 장내 매수",
                          "summary": form4_summary(p)}
            elif form == "424B5":                      # 공모·ATM 유상증자: 설명서 앞 16KB (채권 발행은 거름)
                budget -= 1
                url = _primary_doc_url(f["cik"], f["accession"], "424B5", get=get)
                r = sec_get(url, get=get, max_bytes=DOC_HEAD_BYTES) if url else None
                if r is None:
                    continue                          # 다음 회차에 다시
                ev = _424b5_event(r.head, f["cik"], f["accession"], t, nm, f["updated"])
            else:
                items = parse_8k_items(f["summary"])
                if not items:
                    budget -= 1
                    r = sec_get(f"{_filing_dir(f['cik'], f['accession'])}/{f['accession']}.hdr.sgml", get=get)
                    items = parse_8k_items(r.text) if r is not None else []
                    if r is None:
                        continue
                head = None
                if needs_8k_doc(items):               # 8.01 · 7.01: 자사주 매입 발표인지 본문 앞 16KB 확인
                    budget -= 1
                    url = _primary_doc_url(f["cik"], f["accession"], "8-K", get=get)
                    r = sec_get(url, get=get, max_bytes=DOC_HEAD_BYTES) if url else None
                    if r is None and _sec_blocked():
                        continue
                    head = getattr(r, "head", None) if r is not None else None
                ev = sec_8k_event(items, f["cik"], f["accession"], t, nm, f["updated"], head)
            _sec["seen"].add(f["accession"])
            if ev:
                ev.update(market="US", ticker=t, name=nm or f["company"], source="SEC", source_url=index_url,
                          external_id=_bb_xid(f['accession']) if ev.get("category") == "buyback" else f"SEC:{f['accession']}",
                          disclosed_at=f["updated"])
                out.append(ev)
        if not deadline or time.time() <= deadline:
            _sec["scan_at"][form] = started
    # 자사주 매입: 실시간 피드의 8-K 본문만으로는 대부분 놓친다(발표가 EX-99.1 보도자료·Item 2.02 에 있음) → EFTS 최근 48시간
    if not (deadline and time.time() > deadline):
        try:
            out.extend(collect_sec_buybacks(cik2t, now or datetime.now(timezone.utc), days=EFTS_DAYS, get=get, deadline=deadline))
        except Exception as e:
            log(f"EFTS 자사주 수집 실패: {e}")
    if len(_sec["seen"]) > 20000:
        _sec["seen"].clear()
    return out


# ══ 스케줄 ═══════════════════════════════════════════════════════════════════════
def _kr_window(now):
    k = now.astimezone(KST)
    return k.weekday() < 5 and (8, 0) <= (k.hour, k.minute) < (19, 30)       # 장중 + 장 마감 후 공시까지


def _us_window(now):
    e = now.astimezone(ET_TZ)
    return e.weekday() < 5 and (6, 0) <= (e.hour, e.minute) < (22, 30)


# (이름, 시장, 주기(분), 실행 시간 판정, 수집 함수). 2차: ("g2b", "KR", 60, ...) · ("usaspending", "US", 60, ...)
COLLECTORS = [("dart", "KR", 15, _kr_window, collect_dart), ("sec", "US", 30, _us_window, collect_sec)]
STATUS = {"KR": {"last_run": None, "saved": 0, "note": None, "backfill": None},
          "US": {"last_run": None, "saved": 0, "note": None, "backfill": None},
          "tick": {"last": None, "count": 0, "busy": False}}
_last = {}
_cleanup_day = {"d": None}
# 최초 백필: 이 프로세스에서 시장별로 한 번. None = 아직 DB 확인 전, True = 끝남(또는 필요 없음), False = 진행 중
_backfilled = {"KR": None, "US": None}
_bf = {"KR": {}, "US": {}}                         # 진행 상태 (여러 tick 에 걸쳐 이어서)
BACKFILL_COLLECTOR = {"KR": collect_dart, "US": collect_sec}
_RUN_GUARD = threading.Lock()                      # 스케줄러 tick 과 수동 수집이 겹치지 않게


# EFTS 자사주 백필 상태: None = 아직 DB 확인 전 · {"cands": [...], "saved": n} = 진행 중 · True = 끝남(또는 필요 없음)
_efts_bf = {"state": None}


def _check_efts_backfill(now):
    """최근 14일 미국 자사주(buyback) 가 0건이면 EFTS 14일 소급을 예약한다 (프로세스마다 1번)."""
    if _efts_bf["state"] is not None:
        return
    E = D["DisclosureEvent"]
    try:
        since = (now.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=EFTS_BACKFILL_DAYS))
        none_recent = E.query.filter(E.market == "US", E.category == "buyback", E.disclosed_at >= since).first() is None
    except Exception as e:
        D["db"].session.rollback()
        log(f"EFTS 백필 확인 실패: {e}")
        return
    if none_recent:
        _efts_bf["state"] = {"cands": None, "saved": 0, "started": _utcnow().isoformat() + "Z"}
        log(f"US 자사주 최근 {EFTS_BACKFILL_DAYS}일 0건 → EFTS {EFTS_BACKFILL_DAYS}일 백필 시작")
    else:
        _efts_bf["state"] = True


def _run_efts_backfill(now, tg, budget=None, get=None):
    """진행 중인 EFTS 백필을 이어서 (알림 없음). → 이번에 저장한 건수"""
    st = _efts_bf["state"]
    if not isinstance(st, dict):
        return 0
    deadline = time.time() + (budget or BACKFILL_MARKET_BUDGET)
    try:
        tmap = sec_ticker_map(get=get)
        if not tmap:
            return 0                                   # 다음 tick 에 다시
        cik2t = {}
        for t, nm in tg["US"].items():
            c = tmap.get(t.upper().replace(".", "-"))
            if c:
                cik2t.setdefault(c, (t, nm))
        if st["cands"] is None:
            st["cands"] = efts_candidates(cik2t, now, EFTS_BACKFILL_DAYS, get=get, deadline=deadline)
        evs = collect_sec_buybacks(cik2t, now, get=get, deadline=deadline, doc_budget=EFTS_BACKFILL_DOCS_PER_RUN, cands=st["cands"])
        n = len(save_events(evs))                      # 과거 공시라 알림 없음
    except Exception as e:
        D["db"].session.rollback()
        log(f"EFTS 백필 오류: {str(e)[:80]}")
        return 0
    st["saved"] += n
    if not st["cands"]:
        log(f"US 자사주 EFTS 백필 완료: {st['saved']}건 저장 (알림 없음)")
        _efts_bf["state"] = True
    return n


# 나중에 추가된 분류: 시장 전체에 공시가 있어도 이 분류가 하나도 없으면 14일 소급 백필 (미국 유상증자·자사주는 PR #68 에 추가)
BACKFILL_IF_MISSING = {"US": ("offering", "buyback")}
# 한국 내부자 매수(PR #88): 최근 14일에 KR insider_buy 가 0건이면 지분공시(D)만 14일 소급 (B·I 는 이미 채워져 있으므로)
KR_INSIDER_BACKFILL_TYPES = ("D",)


def _check_backfill():
    """첫 tick: 시장별로 DB 공시가 0건이거나, 나중에 추가된 분류(BACKFILL_IF_MISSING)가 모두 0건이면 14일 백필을 예약한다
    (시간창과 무관, 프로세스마다 1번). 이미 있는 공시는 external_id 로 건너뛰므로 새 분류만 채워진다. DB 오류면 다음 tick 에 다시 확인."""
    E = D["DisclosureEvent"]
    for m in ("KR", "US"):
        if _backfilled[m] is not None:
            continue
        try:
            empty = E.query.filter(E.market == m).first() is None
            cats = BACKFILL_IF_MISSING.get(m)
            missing = (not empty and bool(cats)
                       and E.query.filter(E.market == m, E.category.in_(cats)).first() is None)
            ins_missing = False
            if m == "KR" and not empty:
                since = _utcnow() - timedelta(days=BACKFILL_DAYS)
                ins_missing = E.query.filter(E.market == "KR", E.category == "insider_buy", E.disclosed_at >= since).first() is None
        except Exception as e:
            D["db"].session.rollback()
            log(f"백필 확인 실패 {m}: {e}")
            continue
        if empty:
            start_backfill([m], reason="DB 0건")
        elif missing:
            start_backfill([m], reason=f"{'·'.join(cats)} 0건 → 새 분류 소급")
        elif ins_missing:
            start_backfill([m], reason=f"최근 {BACKFILL_DAYS}일 내부자 매수 0건 → 지분공시(D) 소급", types=KR_INSIDER_BACKFILL_TYPES)
        else:
            _backfilled[m] = True


def _bf_status(m):
    """화면용 백필 상태: state(running · done · error) · 진행률 · 저장 건수 · 사유."""
    st = _bf[m]
    state = st.get("state") or ("done" if st.get("done") else "running")
    if m == "KR":
        prog = {"done": st.get("pages_done", 0), "total": st.get("total", sum(_bf_pages(t) for t in _bf_types(st))), "unit": "쪽"}
    else:
        prog = {"done": st.get("i", 0), "total": st.get("total", 0), "unit": "종목"}
    return {"state": state, "progress": prog, "saved": st.get("saved", 0), "note": st.get("note"),
            "started": st.get("started"), "finished": st.get("finished")}


def start_backfill(markets=("KR", "US"), reason="수동", types=None):
    """14일 백필을 (다시) 시작한다. 진행 상태를 비우고 다음 run 부터 이어서 처리.
    types: 한국 공시유형만 골라서 (예: ("D",) = 지분공시만). 없으면 DART_BACKFILL_TYPES 전부."""
    for m in markets:
        _backfilled[m] = False
        _bf[m] = {"started": _utcnow().isoformat() + "Z", "saved": 0}
        if types and m == "KR":
            _bf[m]["types"] = tuple(types)
        STATUS[m]["backfill"] = _bf_status(m)
        log(f"{m} 최근 {BACKFILL_DAYS}일 백필 시작 ({reason})")


def _run_backfill(now, tg, budget=None):
    """진행 중인 백필을 시장마다 독립 예산(기본 20초)으로 이어서 한다. 찾은 공시는 그 자리에서 바로 저장(커밋)하고,
    알림은 절대 보내지 않는다. → {시장: 이번에 저장한 건수}"""
    res = {}
    for m in ("KR", "US"):
        if _backfilled[m] is not False:
            continue
        st = _bf[m]
        res[m] = 0

        def emit(evs, m=m, st=st):
            if not evs:
                return
            rows = save_events(evs)                   # notify 없음 (과거 공시)
            st["saved"] = st.get("saved", 0) + len(rows)
            res[m] += len(rows)

        deadline = time.time() + (budget or BACKFILL_MARKET_BUDGET)       # 시장마다 따로
        try:
            BACKFILL_COLLECTOR[m](tg=tg, now=now, deadline=deadline, backfill=True, emit=emit)
        except Exception as e:
            D["db"].session.rollback()
            _bf_fail(m, f"백필 오류: {str(e)[:80]}")
        if st.get("done"):
            _backfilled[m] = True
            st["finished"] = _utcnow().isoformat() + "Z"
            log(f"{m} 백필 {'완료' if st.get('state') != 'error' else '중단'}: {st.get('saved', 0)}건 저장 "
                f"(요청 {st.get('calls', 0)}회, 알림 없음){' — ' + st['note'] if st.get('note') else ''}")
        STATUS[m]["backfill"] = _bf_status(m)
    return res


def run(names=None, force=False, now=None, budget=None, backfill_budget=None):
    """(1) 진행 중인 최초 백필을 시장마다 독립 예산으로 이어서 (알림 없음) → (2) 주기가 된(또는 force) 수집기를 돌리고
    새 이벤트를 저장·알림. → {이름: 새로 저장된 건수} (백필은 'backfill_KR' · 'backfill_US').
    다른 곳에서 이미 돌고 있으면 기다리지 않고 {"busy": True}."""
    if not _RUN_GUARD.acquire(blocking=False):
        return {"busy": True}
    try:
        STATUS["tick"].update(last=_utcnow().isoformat() + "Z", count=STATUS["tick"]["count"] + 1, busy=True)
        return _run(names, force, now, budget, backfill_budget)
    finally:
        STATUS["tick"]["busy"] = False
        _RUN_GUARD.release()


def _run(names, force, now, budget, backfill_budget):
    now = now or datetime.now(timezone.utc)
    res, tg = {}, None
    _check_backfill()
    if any(v is False for v in _backfilled.values()):
        tg = targets()
        for m, n in _run_backfill(now, tg, backfill_budget).items():
            res[f"backfill_{m}"] = n
    _check_efts_backfill(now)
    if isinstance(_efts_bf["state"], dict):
        tg = tg or targets()
        res["backfill_US_buyback"] = _run_efts_backfill(now, tg, backfill_budget)
    deadline = time.time() + (budget or TICK_BUDGET)
    for name, market, every, window, fn in COLLECTORS:
        if names and name not in names:
            continue
        due = force or (window(now) and (name not in _last or now - _last[name] >= timedelta(minutes=every)))
        if not due or time.time() > deadline:
            continue
        _last[name] = now
        if tg is None:
            tg = targets()
        STATUS[market]["note"] = None
        try:
            events = fn(tg=tg, now=now, deadline=deadline)
            rows = save_events(events)
        except Exception as e:
            D["db"].session.rollback()
            log(f"{name} 수집 실패: {e}")
            STATUS[market]["note"] = f"수집 실패: {str(e)[:80]}"
            continue
        STATUS[market].update(last_run=_utcnow().isoformat() + "Z", saved=len(rows))
        res[name] = len(rows)
        if rows:
            log(f"{name}: 새 공시 {len(rows)}건 ({', '.join(sorted({r.ticker for r in rows})[:8])})")
            _notify(rows)
    if _cleanup_day["d"] != now.date():
        _cleanup_day["d"] = now.date()
        try:
            n = cleanup()
            if n:
                log(f"{RETENTION_DAYS}일 지난 공시 {n}건 정리")
        except Exception as e:
            D["db"].session.rollback()
            log(f"정리 실패: {e}")
    return res


def tick():
    """bot_engine 스케줄러 루프가 20초마다 부른다. 주기가 아니면 아무것도 안 한다 (외부 호출 0)."""
    if os.environ.get("DISCLOSURES_ENABLED", "1") == "0":
        return {}
    return run()
