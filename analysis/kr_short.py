"""국내 종목 공매도 잔고 · 거래 비중 (KRX 정보데이터시스템 외부 제공 화면, 로그인 불필요).

출처: 네이버 증권 '공매도현황' 탭이 iframe 으로 띄우는 KRX 화면(`/comm/srt/srtLoader/index.cmd`)이 부르는
`getJsonData.cmd` 의 외부용(_OUT) 조회. KRX 웹 로그인·증권 계좌 없이 응답한다.
- MDCSTAT30502_OUT: 종목별 공매도 순보유잔고 추이 {RPT_DUTY_OCCR_DD, BAL_QTY, LIST_SHRS, BAL_AMT, MKTCAP, BAL_RTO}
  잔고는 보고 의무 발생일 기준이라 거래일보다 2영업일(T+2) 늦다.
- MDCSTAT30102_OUT: 개별 종목 공매도 거래 {TRD_DD, CVSRTSELL_TRDVAL, ACC_TRDVAL, TRDVAL_WT(거래대금 비중 %) …}
  2025-03-04 부터 KRX + NXT 합산.

잔고 비중 = 공매도 순보유 잔고수량 ÷ 상장주식수 × 100 (KRX BAL_RTO 와 같은 정의, 소수 둘째 자리까지 직접 계산).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
LOADER = "https://data.krx.co.kr/comm/srt/srtLoader/index.cmd?screenId={screen}&isuCd={code}"
BLD_BAL = "dbms/MDC_OUT/STAT/srt/MDCSTAT30502_OUT"
BLD_TRD = "dbms/MDC_OUT/STAT/srt/MDCSTAT30102_OUT"
TTL = 2 * 3600            # 정상 응답 2시간 (잔고는 하루 1번 갱신)
NEG_TTL = 5 * 60          # 실패 5분 동안 다시 묻지 않음
TIMEOUT = 4
MAX_ENTRIES = 300
KST = timezone(timedelta(hours=9))

# 잔고 비중(%) 단계: 0.5 미만 낮음 · 0.5~2 보통 · 2~5 주의 · 5 이상 높음
LEVELS = ((5.0, "high", "높음"), (2.0, "caution", "주의"), (0.5, "normal", "보통"), (0.0, "low", "낮음"))

_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_lock = threading.Lock()


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def code_of(ticker):
    """'005930.KS' → '005930'. 국내 종목이 아니면 None."""
    t = (ticker or "").strip().upper()
    if not t.endswith((".KS", ".KQ")):
        return None
    c = t.rsplit(".", 1)[0]
    return c if len(c) == 6 and c.isalnum() else None


def is_supported(ticker):
    """보통주만: 우선주(끝자리 0 아님: 005935·00088K 등)는 잔고 자료가 거의 없어 제외."""
    c = code_of(ticker)
    return bool(c) and c.isdigit() and c[-1] == "0"


def isin(code):
    """6자리 단축코드 → 보통주 ISIN (KR7 + 코드 + 00 + 검증숫자). 005930 → KR7005930003."""
    body = f"KR7{code}00"
    digits = "".join(str(int(ch, 36)) for ch in body)        # 문자는 10~35 로
    total = 0
    for i, ch in enumerate(reversed(digits)):                # 오른쪽부터 홀수 자리 ×2 (Luhn)
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return body + str((10 - total % 10) % 10)


def level_of(pct):
    for th, lv, label in LEVELS:
        if pct >= th:
            return lv, label
    return "low", "낮음"


def _date(s):
    try:
        return datetime.strptime(str(s).strip(), "%Y/%m/%d").date()
    except (TypeError, ValueError):
        return None


def parse_balance(rows):
    """MDCSTAT30502_OUT OutBlock_1 → 최신 먼저 [{date, qty, shares, amt, pct}] (값이 이상한 행은 버림)."""
    out = []
    for r in rows or []:
        d, q, sh = _date(r.get("RPT_DUTY_OCCR_DD")), _num(r.get("BAL_QTY")), _num(r.get("LIST_SHRS"))
        if not d or q is None or q < 0 or not sh or sh <= 0:
            continue
        out.append({"date": d, "qty": int(q), "shares": int(sh), "amt": _num(r.get("BAL_AMT")), "pct": q / sh * 100})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def parse_trade(rows):
    """MDCSTAT30102_OUT OutBlock_1 → 최신 먼저 [{date, pct, value, total}] (거래대금 비중 %)."""
    out = []
    for r in rows or []:
        d = _date(r.get("TRD_DD"))
        wt, val, tot = _num(r.get("TRDVAL_WT")), _num(r.get("CVSRTSELL_TRDVAL")), _num(r.get("ACC_TRDVAL"))
        if wt is None and val is not None and tot:
            wt = val / tot * 100
        if not d or wt is None or wt < 0 or wt > 100:
            continue
        out.append({"date": d, "pct": wt, "value": val, "total": tot})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def summarize(bal, trd=None):
    """잔고 행(필수) + 거래 행(선택) → 화면·프롬프트용 dict. 잔고가 없으면 None."""
    if not bal:
        return None
    b0 = bal[0]
    pct = round(b0["pct"], 2)
    def chg(i):
        return round(b0["pct"] - bal[i]["pct"], 2) + 0.0 if len(bal) > i else None   # -0.0 방지
    lv, label = level_of(pct)
    t0 = (trd or [None])[0]
    return {
        "available": True,
        "balance_pct": pct,
        "balance_qty": b0["qty"],
        "balance_amt": int(b0["amt"]) if b0["amt"] is not None else None,
        "listed_shares": b0["shares"],
        "change_1d_pp": chg(1),
        "change_5d_pp": chg(5),
        "as_of": b0["date"].isoformat(),
        "as_of_label": b0["date"].strftime("%m.%d"),
        "trade_pct": round(t0["pct"], 1) if t0 else None,
        "trade_as_of": t0["date"].isoformat() if t0 else None,
        "trade_as_of_label": t0["date"].strftime("%m.%d") if t0 else None,
        "level": lv,
        "label": label,
        "source": "KRX",
    }


def _default_get():
    import requests
    return requests.post


def _query(post, bld, screen, code, start, end):
    r = post(URL, data={"bld": bld, "locale": "ko_KR", "searchType": "2", "isuCd": isin(code),
                        "strtDd": start, "endDd": end, "share": "1", "money": "1", "csvxls_isNo": "false"},
             headers={"Referer": LOADER.format(screen=screen, code=code), "X-Requested-With": "XMLHttpRequest",
                      "User-Agent": "Mozilla/5.0"},
             timeout=TIMEOUT)
    if getattr(r, "status_code", 0) != 200:
        return None
    try:
        js = r.json()
    except Exception:
        return None
    rows = js.get("OutBlock_1") if isinstance(js, dict) else None
    return rows if isinstance(rows, list) else None


def get_short(ticker, post=None, now=None):
    """→ summarize() 결과 또는 {"available": False, "reason": ...}. 정상 2시간 · 실패 5분 캐시, 최대 300종목."""
    if not is_supported(ticker):
        return {"available": False, "reason": "not_supported"}
    code = code_of(ticker)
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(code)
        if hit and hit[0] > now:
            _cache.move_to_end(code)
            return hit[1]
    post = post or _default_get()
    today = datetime.fromtimestamp(now, KST).date()
    start, end = (today - timedelta(days=21)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
    res = None
    try:
        bal = parse_balance(_query(post, BLD_BAL, "MDCSTAT305", code, start, end))
        if bal:
            try:
                trd = parse_trade(_query(post, BLD_TRD, "MDCSTAT301", code, (today - timedelta(days=10)).strftime("%Y%m%d"), end))
            except Exception:
                trd = []
            res = summarize(bal, trd)
    except Exception:
        res = None
    out = res or {"available": False, "reason": "fetch_failed"}
    with _lock:
        _cache[code] = (now + (TTL if res else NEG_TTL), out)
        _cache.move_to_end(code)
        while len(_cache) > MAX_ENTRIES:
            _cache.popitem(last=False)
    return out
