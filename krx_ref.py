"""한국 종목 공식 기준가(KRX 정규장 전일 종가) — 네이버 실시간 폴링 응답의 closePrice − compareToPreviousClosePrice.

토스 일봉(종목 상세 1순위)은 대체거래소(NXT) 시간외 체결까지 합친 값으로 보여, 전날 봉 종가가 KRX 정규장 종가와
다를 수 있다(예: 253450 스튜디오드래곤 11/20 토스 30,600 vs KRX 31,350). 종목 상세·추세 셋업·스크리너·섹터맵·급등락이
같은 전일 종가로 등락률을 계산하도록 한국 종목 기준가는 여기서 가져온다. 외부 의존 없음(앱 순환 import 없음).

ref(t) = {"close": 마지막 정규장 체결가/종가, "prev": 그 거래일의 전일 종가, "date": 그 거래일(현지),
          "status": OPEN|CLOSE|…, "change_pct": 등락률}
  · date 가 오늘이면 prev 가 오늘 등락률의 기준.
  · date 가 오늘보다 앞(개장 전)이면 close 가 오늘 시세(프리마켓 등)의 기준, prev 는 그날 등락률의 기준.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36", "Referer": "https://finance.naver.com/"}
_CACHE = {}                    # ticker → (받은 시각, ref)
_LOCK = threading.Lock()
TTL = 60                       # 기준가는 하루 동안 그대로라 1분이면 충분 (현재가 용도로 쓰지 않음)
_KST = timezone(timedelta(hours=9))
STATS = {"calls": 0, "hits": 0}


def is_kr(t):
    return bool(t) and t.upper().endswith((".KS", ".KQ"))


def today_kst():
    return datetime.now(_KST).date()


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def refs(tickers, ttl=TTL):
    """{ticker: ref}. 한국 종목만, 50종목씩 네이버 1회 요청. 실패한 종목은 빠진다."""
    want = list(dict.fromkeys(t for t in tickers if is_kr(t)))
    if not want:
        return {}
    now = time.time()
    out, miss = {}, []
    with _LOCK:
        for t in want:
            hit = _CACHE.get(t)
            if hit and now - hit[0] < ttl:
                out[t] = hit[1]
            else:
                miss.append(t)
        STATS["hits"] += len(out)
    code2t = {t.split(".")[0]: t for t in miss}
    codes = list(code2t)
    for i in range(0, len(codes), 50):
        part = codes[i:i + 50]
        STATS["calls"] += 1
        try:
            r = requests.get("https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(part),
                             headers=_H, timeout=4)
            datas = (r.json() or {}).get("datas") or []
        except Exception:
            datas = []
        ts = time.time()
        for d in datas:
            t = code2t.get(d.get("itemCode"))
            close, diff = _num(d.get("closePrice")), _num(d.get("compareToPreviousClosePrice"))
            if not t or not close or diff is None:
                continue
            day = None
            try:
                day = datetime.fromisoformat(str(d.get("localTradedAt"))).astimezone(_KST).date()
            except (TypeError, ValueError):
                pass
            ref = {"close": close, "prev": close - diff, "date": day, "status": d.get("marketStatus"),
                   "change_pct": _num(d.get("fluctuationsRatio")),
                   "volume": _num(d.get("accumulatedTradingVolumeRaw") or d.get("accumulatedTradingVolume"))}
            with _LOCK:
                _CACHE[t] = (ts, ref)
            out[t] = ref
    return out


def prev_for_today(ref):
    """오늘(한국 날짜) 시세의 등락률 기준이 되는 전일 종가."""
    if not ref:
        return None
    if ref.get("date") and ref["date"] < today_kst():
        return ref["close"]            # 아직 오늘 정규장 전 → 마지막 정규장 종가가 기준
    return ref["prev"]


def prev_closes_today(tickers):
    """{ticker: 오늘 기준 전일 종가} (한국 종목만)."""
    return {t: p for t, r in refs(tickers).items() if (p := prev_for_today(r))}
