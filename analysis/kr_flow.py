"""국내 종목 외국인·기관·개인 수급 (최근 20거래일) — 온디맨드 조회 + 30분 LRU 캐시(200종목).

데이터
- 1순위: 네이버 모바일 JSON `https://m.stock.naver.com/api/stock/{code}/trend?pageSize=20&page=1`
  (일별: bizdate · closePrice · foreignerPureBuyQuant · organPureBuyQuant · individualPureBuyQuant · foreignerHoldRatio)
- 2순위: 네이버 금융 HTML `finance.naver.com/item/frgn.naver?code=` 의 일별 표 (예전 서버 렌더링 형식일 때만;
  2026-11 기준으로는 새 사이트로 리다이렉트되어 표가 없을 수 있다 → 그때는 실패로 보고 2분 뒤 다시)

금액은 '순매수 주수 × 그날 종가' 로 추정한다 (실제 체결 단가와 다를 수 있음).
개인은 응답에 개인 순매수 주수가 있으면 그것으로, 없으면 -(외국인 + 기관) 으로 추정 (기타법인 등은 개인에 섞임).
"""
from __future__ import annotations

import html as _html
import re
import threading
import time
from collections import OrderedDict

TTL = 30 * 60
NEG_TTL = 2 * 60
MAX_ITEMS = 200
DAYS = 20
CHART_DAYS = 10
STREAK_BADGE = 3
JSON_URL = "https://m.stock.naver.com/api/stock/{code}/trend?pageSize=20&page=1"
HTML_URL = "https://finance.naver.com/item/frgn.naver?code={code}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
           "Referer": "https://m.stock.naver.com/", "Accept-Language": "ko-KR,ko;q=0.9"}

_cache = OrderedDict()            # code -> (ts, payload | None)
_lock = threading.Lock()


def is_kr(ticker):
    return (ticker or "").upper().endswith((".KS", ".KQ"))


def _num(v):
    if v is None:
        return None
    s = re.sub(r"[,\s%+]", "", str(v))
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(s):
    s = re.sub(r"\D", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None


# ── 파싱 ──────────────────────────────────────────────────────────────────────
def parse_json(items):
    """네이버 trend JSON → 일별 행 (최신 먼저)."""
    rows = []
    for it in items or []:
        d, close = _date(it.get("bizdate")), _num(it.get("closePrice"))
        if not d or not close:
            continue
        rows.append({"date": d, "close": close, "foreign_shares": _num(it.get("foreignerPureBuyQuant")) or 0.0,
                     "inst_shares": _num(it.get("organPureBuyQuant")) or 0.0,
                     "retail_shares": _num(it.get("individualPureBuyQuant")),
                     "foreign_hold_ratio": _num(it.get("foreignerHoldRatio"))})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def parse_html(text):
    """예전 frgn.naver 일별 표: 날짜 · 종가 · 전일비 · 등락률 · 거래량 · 기관 순매매 · 외국인 순매매 · 외국인 보유주수 · 보유율."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text or "", re.S | re.I):
        tds = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", td))).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
        if len(tds) < 9 or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", tds[0]):
            continue
        close = _num(tds[1])
        if not close:
            continue
        rows.append({"date": tds[0].replace(".", "-"), "close": close, "inst_shares": _num(tds[5]) or 0.0,
                     "foreign_shares": _num(tds[6]) or 0.0, "retail_shares": None, "foreign_hold_ratio": _num(tds[8])})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


# ── 요약 ──────────────────────────────────────────────────────────────────────
def _streak(vals):
    """최신부터 같은 부호가 몇 일 이어졌나: 양수면 +N(연속 순매수), 음수면 -N, 최신이 0이면 0."""
    if not vals or not vals[0]:
        return 0
    sign = 1 if vals[0] > 0 else -1
    n = 0
    for v in vals:
        if v and (v > 0) == (sign > 0):
            n += 1
        else:
            break
    return sign * n


def summarize(rows):
    """일별 행(최신 먼저) → 화면용 요약."""
    rows = (rows or [])[:DAYS]
    if not rows:
        return {"available": False, "reason": "no_data"}
    for r in rows:
        r["foreign_krw"] = r["foreign_shares"] * r["close"]
        r["inst_krw"] = r["inst_shares"] * r["close"]
        if r.get("retail_shares") is not None:
            r["retail_krw"] = r["retail_shares"] * r["close"]
            r["retail_est"] = False
        else:
            r["retail_shares"] = -(r["foreign_shares"] + r["inst_shares"])
            r["retail_krw"] = -(r["foreign_krw"] + r["inst_krw"])
            r["retail_est"] = True

    def window(n):
        part = rows[:n]
        out = {"days": len(part)}
        for k in ("foreign", "inst", "retail"):
            out[f"{k}_krw"] = round(sum(r[f"{k}_krw"] for r in part))
            out[f"{k}_shares"] = round(sum(r[f"{k}_shares"] for r in part))
        return out

    s5, s20 = window(5), window(DAYS)
    fs = _streak([r["foreign_shares"] for r in rows])
    ist = _streak([r["inst_shares"] for r in rows])
    badges = []
    if fs >= STREAK_BADGE:
        badges.append({"text": f"외국인 {fs}일 연속 순매수", "tone": "up"})
    elif fs <= -STREAK_BADGE:
        badges.append({"text": f"외국인 {-fs}일 연속 순매도", "tone": "down"})
    if ist >= STREAK_BADGE:
        badges.append({"text": f"기관 {ist}일 연속 순매수", "tone": "up"})
    elif ist <= -STREAK_BADGE:
        badges.append({"text": f"기관 {-ist}일 연속 순매도", "tone": "down"})
    if s5["foreign_krw"] > 0 and s5["inst_krw"] > 0 and rows[0]["foreign_shares"] > 0 and rows[0]["inst_shares"] > 0:
        badges.append({"text": "쌍끌이 매수", "tone": "both"})
    chart = list(reversed(rows[:CHART_DAYS]))          # 오래된 날 → 최근 (오름차순)
    return {
        "available": True,
        "as_of": rows[0]["date"], "days": len(rows),
        "foreign_hold_ratio": rows[0].get("foreign_hold_ratio"),
        "sum_5d": s5, "sum_20d": s20,
        "foreign_streak": fs, "inst_streak": ist, "badges": badges,
        "retail_estimated": any(r["retail_est"] for r in rows),
        "daily": [{"date": r["date"], "label": r["date"][5:].replace("-", "."), "close": r["close"],
                   "foreign_krw": round(r["foreign_krw"]), "inst_krw": round(r["inst_krw"]), "retail_krw": round(r["retail_krw"])} for r in chart],
    }


# ── 조회 + 캐시 ───────────────────────────────────────────────────────────────
def _fetch_rows(code, get):
    try:
        r = get(JSON_URL.format(code=code), headers=HEADERS, timeout=5)
        if r.status_code == 200:
            rows = parse_json(r.json())
            if rows:
                return rows, "naver_json"
    except Exception:
        pass
    try:
        r = get(HTML_URL.format(code=code), headers={**HEADERS, "Referer": "https://finance.naver.com/"}, timeout=5, allow_redirects=False)
        if r.status_code == 200:
            raw = r.content
            text = None
            for enc in ("euc-kr", "cp949", "utf-8"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            rows = parse_html(text or "")
            if rows:
                return rows, "naver_html"
    except Exception:
        pass
    return [], None


def get_flow(ticker, get=None, now=None):
    """→ 요약 dict. 해외 종목은 {"available": False, "reason": "not_kr"}. 30분 캐시, 실패는 2분 동안 다시 묻지 않음."""
    if not is_kr(ticker):
        return {"available": False, "reason": "not_kr"}
    code = ticker.split(".")[0]
    now = now if now is not None else time.time()
    with _lock:
        hit = _cache.get(code)
        if hit:
            ts, payload = hit
            ttl = TTL if payload and payload.get("available") else NEG_TTL
            if now - ts < ttl:
                _cache.move_to_end(code)
                return payload
    if get is None:
        import requests
        get = requests.get
    rows, source = _fetch_rows(code, get)
    payload = summarize(rows) if rows else {"available": False, "reason": "fetch_failed"}
    if payload.get("available"):
        payload["source"] = source
    with _lock:
        _cache[code] = (now, payload)
        _cache.move_to_end(code)
        while len(_cache) > MAX_ITEMS:
            _cache.popitem(last=False)
    return payload
