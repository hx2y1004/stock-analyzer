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
TICK_BUDGET = 40.0              # 초. 한 번의 tick 에서 수집에 쓰는 최대 시간
DART_PAGES_COLD = 20            # 재시작 직후 첫 조회: 오늘 공시 최대 2,000건까지 훑는다
DART_PAGES_WARM = 5             # 이후 15분마다: 최신 500건 (이미 본 접수번호를 만나면 멈춤)
DART_DOC_PER_RUN = 10           # 공급계약 원문(계약금액·매출 대비 %) 조회는 한 번에 10건까지
SEC_GAP = 0.2                   # 초. SEC 요청 간격
SEC_FEED_PAGES = 4              # 최신 공시 피드 페이지(100건씩) 상한
SEC_FILINGS_PER_RUN = 30        # Form 4 / 8-K 상세 조회 상한 (한 번에)
SEC_ITEMS = {"1.01": ("중요 계약 체결", "positive"), "2.01": ("인수·자산 취득 완료", "positive"),
             "2.02": ("실적 발표", "neutral")}


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
    if category in CATEGORIES:
        q = q.filter(E.category == category)
    if ticker:
        q = q.filter(E.ticker == ticker.upper())
    rows = q.order_by(E.disclosed_at.desc(), E.id.desc()).limit(max(1, min(int(limit or 50), 200))).all()
    return [to_dict(r) for r in rows]


def to_dict(r):
    return {"id": r.id, "market": r.market, "ticker": r.ticker, "name": r.name, "source": r.source, "category": r.category,
            "sentiment": r.sentiment, "title": r.title, "summary": r.summary, "source_url": r.source_url,
            "disclosed_at": r.disclosed_at.isoformat() + "Z" if r.disclosed_at else None}


# ══ 한국: OpenDART ═══════════════════════════════════════════════════════════════
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
DART_DOC = "https://opendart.fss.or.kr/api/document.xml"
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
    if v >= 1e12:                                   # 1조 2,345억원
        jo, eok = int(v // 1e12), int(round((v % 1e12) / 1e8))
        if eok >= 10000:
            jo, eok = jo + 1, 0
        return f"{jo:,}조원" if eok == 0 else f"{jo:,}조 {eok:,}억원"
    if v >= 1e8:
        return f"{v / 1e8:,.0f}억원"
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


def collect_dart(tg=None, now=None, deadline=None, get=None):
    """오늘(한국 날짜) 공시목록 → 대상 종목의 공급계약·자사주·유상증자 이벤트 목록."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        STATUS["KR"]["note"] = "DART_API_KEY 없음"
        return []
    import requests
    get = get or requests.get
    now = now or datetime.now(KST)
    day = now.astimezone(KST).date()
    if _dart_seen["day"] != day:
        _dart_seen.update(day=day, ids=set())
    cold = not _dart_seen["ids"]
    tg = tg if tg is not None else targets()
    code2t = {}
    for t, nm in tg["KR"].items():
        code2t.setdefault(t.split(".")[0].upper(), (t, nm))
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
            cls = classify_dart(it.get("report_nm"))
            if not cls:
                continue
            cat, senti, label = cls
            t, nm = hit
            rno = str(it.get("rcept_no"))
            summary = {"contract": "공급계약 체결", "buyback": "주주환원 (자사주)", "offering": "신주 발행 — 주식 수 희석 주의"}[cat]
            if cat == "contract" and senti == "negative":
                summary = "기존 공급계약 해지"
            if cat == "contract" and senti == "positive" and docs < DART_DOC_PER_RUN:
                docs += 1
                det = _dart_contract_detail(key, rno, get)
                parts = [f"계약금액 {fmt_krw(det['amount'])}" if det.get("amount") else "",
                         f"최근 매출 대비 {det['ratio']:g}%" if det.get("ratio") is not None else ""]
                summary = " · ".join(p for p in parts if p) or summary
            rdt = str(it.get("rcept_dt") or "")
            try:
                d0 = datetime.strptime(rdt, "%Y%m%d").date()
            except ValueError:
                d0 = day
            at = _utcnow() if d0 == day else datetime(d0.year, d0.month, d0.day, 9, tzinfo=KST).astimezone(timezone.utc).replace(tzinfo=None)
            out.append({"market": "KR", "ticker": t, "name": it.get("corp_name") or nm, "source": "DART", "category": cat,
                        "sentiment": senti, "title": label, "summary": summary,
                        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
                        "external_id": f"DART:{rno}", "disclosed_at": at})
        if not items or len(items) < 100 or (not cold and len(fresh) < len(items)):
            break                                     # 마지막 페이지 · 이미 본 구간에 닿음
        time.sleep(0.1)
    return out


# ══ 미국: SEC EDGAR ═════════════════════════════════════════════════════════════
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FEED = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_ARCH = "https://www.sec.gov/Archives/edgar/data"
_sec = {"last": 0.0, "lock": threading.Lock(), "blocked_until": 0.0, "map": None, "map_at": 0.0,
        "seen": set(), "scan_at": {}}


def sec_user_agent():
    return os.environ.get("SEC_USER_AGENT", "").strip() or "StockAnalyzer (contact@example.com)"


def sec_get(url, params=None, get=None):
    """SEC 요청: User-Agent 헤더 + 요청 간 0.2초 이상. 403/429 면 10분 쉰다. 실패하면 None."""
    import requests
    get = get or requests.get
    if time.time() < _sec["blocked_until"]:
        return None
    with _sec["lock"]:
        wait = SEC_GAP - (time.time() - _sec["last"])
        if wait > 0:
            time.sleep(wait)
        try:
            r = get(url, params=params, headers={"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}, timeout=12)
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


def collect_sec(tg=None, now=None, deadline=None, get=None):
    """Form 4 장내 매수 · 8-K 핵심 항목 이벤트 목록."""
    tg = tg if tg is not None else targets()
    tmap = sec_ticker_map(get=get)
    if not tmap:
        STATUS["US"]["note"] = "SEC 티커 매핑을 받지 못함"
        return []
    cik2t = {}
    for t, nm in tg["US"].items():
        c = tmap.get(t.upper().replace(".", "-"))
        if c:
            cik2t.setdefault(c, (t, nm))
    E = D["DisclosureEvent"]
    out, budget = [], SEC_FILINGS_PER_RUN
    for form in ("4", "8-K"):
        since = _sec["scan_at"].get(form)
        started = _utcnow()
        feed = _scan_feed(form, (since - timedelta(minutes=10)) if since else None, deadline, get=get)
        cand, seen_acc = [], set()
        for f in feed:
            if f["form"] not in (("4", "4/A") if form == "4" else ("8-K", "8-K/A")):
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
            else:
                items = parse_8k_items(f["summary"])
                if not items:
                    budget -= 1
                    r = sec_get(f"{_filing_dir(f['cik'], f['accession'])}/{f['accession']}.hdr.sgml", get=get)
                    items = parse_8k_items(r.text) if r is not None else []
                    if r is None:
                        continue
                keep = [i for i in items if i in SEC_ITEMS]
                if keep:
                    senti = "positive" if any(SEC_ITEMS[i][1] == "positive" for i in keep) else "neutral"
                    ev = {"category": "material_8k", "sentiment": senti,
                          "title": "8-K · " + " · ".join(SEC_ITEMS[i][0] for i in keep),
                          "summary": " · ".join(f"Item {i} {SEC_ITEMS[i][0]}" for i in keep) + ("" if len(keep) == len(items) else
                                     f" (그 외 {', '.join(i for i in items if i not in SEC_ITEMS)})")}
            _sec["seen"].add(f["accession"])
            if ev:
                ev.update(market="US", ticker=t, name=nm or f["company"], source="SEC", source_url=index_url,
                          external_id=f"SEC:{f['accession']}", disclosed_at=f["updated"])
                out.append(ev)
        if not deadline or time.time() <= deadline:
            _sec["scan_at"][form] = started
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
STATUS = {"KR": {"last_run": None, "saved": 0, "note": None}, "US": {"last_run": None, "saved": 0, "note": None}}
_last = {}
_cleanup_day = {"d": None}


def run(names=None, force=False, now=None):
    """주기가 된(또는 force) 수집기를 돌리고 새 이벤트를 저장·알림 → {이름: 새로 저장된 건수}."""
    now = now or datetime.now(timezone.utc)
    deadline = time.time() + TICK_BUDGET
    res, tg = {}, None
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
