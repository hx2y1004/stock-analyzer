"""한국(KRX)·미국(NYSE/NASDAQ) 거래일·장 시간 판정 — 외부 패키지 없음(표준 라이브러리만).

- 미국: 연방 공휴일 규칙으로 해마다 계산 (토요일이면 금요일, 일요일이면 월요일 대체 휴장).
  NYSE 예외: 1/1 이 토요일이면 전년 12/31 은 열린다 (2028 등). 조기폐장 13:00 ET: 독립기념일 전날(7/3, 평일·휴장 아님),
  추수감사절 다음 날, 크리스마스 이브(12/24, 평일·휴장 아님). 특별 휴장(국가 애도일 등)은 US_SPECIAL.
  2026~2028 은 NYSE 공식 발표(2025-12-23)와 대조해 맞춤.
- 한국: 양력 고정 휴장일 + 대체공휴일 규칙(토·일 → 다음 평일) + 연도별 음력·선거·임시공휴일 표(KR_YEARLY).
  연말 휴장은 12월 마지막 평일. 연초 첫 거래일·수능일은 개장 시각이 늦다(KR_LATE_OPEN).
  ※ KR_YEARLY 에 없는 해(2029~)는 설날·추석·부처님오신날을 모르므로 표를 추가해야 한다 (has_kr_calendar 로 확인).

함수: holiday_name · session_window · is_trading_day · is_market_open · market_status · next_trading_day · prev_trading_day
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
REGULAR = {"KR": ((9, 0), (15, 30)), "US": ((9, 30), (16, 0))}
US_EARLY_CLOSE = (13, 0)


def _market(m):
    m = (m or "").upper()
    if m in ("KR", "KRX", "KS", "KQ"):
        return "KR"
    return "US"


# ── 미국 ──────────────────────────────────────────────────────────────────────
def _nth_weekday(y, month, weekday, n):
    """n 번째 요일 (weekday: 월=0). n=-1 이면 마지막."""
    if n > 0:
        d = date(y, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(y):
    """그레고리력 부활절 (Anonymous Gregorian algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _observed(d):
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


US_SPECIAL = {date(2025, 1, 9): "카터 전 대통령 국가 애도일"}


@lru_cache(maxsize=64)
def _us_year(y):
    """→ ({날짜: 휴장 사유}, {조기폐장 날짜: 사유})"""
    h = {}
    nyd = date(y, 1, 1)
    if nyd.weekday() != 5:                       # 토요일이면 NYSE 는 대체 휴장 없음 (전년 12/31 개장)
        h[_observed(nyd)] = "신정"
    h[_nth_weekday(y, 1, 0, 3)] = "마틴 루터 킹 데이"
    h[_nth_weekday(y, 2, 0, 3)] = "대통령의 날"
    h[_easter(y) - timedelta(days=2)] = "성금요일"
    h[_nth_weekday(y, 5, 0, -1)] = "메모리얼 데이"
    if y >= 2022:
        h[_observed(date(y, 6, 19))] = "준틴스"
    h[_observed(date(y, 7, 4))] = "독립기념일"
    h[_nth_weekday(y, 9, 0, 1)] = "노동절(Labor Day)"
    tg = _nth_weekday(y, 11, 3, 4)
    h[tg] = "추수감사절"
    h[_observed(date(y, 12, 25))] = "크리스마스"
    for d, name in US_SPECIAL.items():
        if d.year == y:
            h[d] = name
    early = {}
    for d, name in ((date(y, 7, 3), "독립기념일 전날"), (tg + timedelta(days=1), "추수감사절 다음 날"), (date(y, 12, 24), "크리스마스 이브")):
        if d.weekday() < 5 and d not in h:
            early[d] = name
    return h, early


# ── 한국 ──────────────────────────────────────────────────────────────────────
# 양력 고정 휴장일. sub=True 면 토·일과 겹칠 때 다음 평일 대체공휴일 (국경일·어린이날·성탄절·노동절, 2021~ 확대 규정)
_KR_FIXED = [
    ((1, 1), "신정", False, None),
    ((3, 1), "삼일절", True, None),
    ((5, 1), "근로자의 날", True, 2027),          # 2027~ 대체공휴일 대상 (2027 월력요항: 5/1 토 → 5/3)
    ((5, 5), "어린이날", True, None),
    ((6, 6), "현충일", False, None),
    ((7, 17), "제헌절", True, None),             # 2026 법정공휴일 재지정 (KRX 2026-07-17 휴장 발표) → _KR_SINCE
    ((8, 15), "광복절", True, None),
    ((10, 3), "개천절", True, None),
    ((10, 9), "한글날", True, None),
    ((12, 25), "크리스마스", True, None),
]
_KR_SINCE = {(7, 17): 2026}                     # 이 해부터 휴장

# 연도별 음력 공휴일 · 선거일 · 임시공휴일 · 겹침에 따른 대체공휴일 (주말이어도 적어 둔다: 대체 판정에 쓰임)
KR_YEARLY = {
    2025: {
        date(2025, 1, 27): "임시공휴일(설 연휴)", date(2025, 1, 28): "설날 연휴", date(2025, 1, 29): "설날", date(2025, 1, 30): "설날 연휴",
        date(2025, 5, 6): "대체공휴일(어린이날·부처님오신날)",
        date(2025, 6, 3): "제21대 대통령선거",
        date(2025, 10, 5): "추석 연휴", date(2025, 10, 6): "추석", date(2025, 10, 7): "추석 연휴", date(2025, 10, 8): "대체공휴일(추석)",
    },
    2026: {
        date(2026, 2, 16): "설날 연휴", date(2026, 2, 17): "설날", date(2026, 2, 18): "설날 연휴",
        date(2026, 5, 24): "부처님오신날", date(2026, 5, 25): "대체공휴일(부처님오신날)",
        date(2026, 6, 3): "전국동시지방선거",
        date(2026, 9, 24): "추석 연휴", date(2026, 9, 25): "추석", date(2026, 9, 26): "추석 연휴",
    },
    2027: {
        date(2027, 2, 6): "설날 연휴", date(2027, 2, 7): "설날", date(2027, 2, 8): "설날 연휴", date(2027, 2, 9): "대체공휴일(설날)",
        date(2027, 5, 13): "부처님오신날",
        date(2027, 9, 14): "추석 연휴", date(2027, 9, 15): "추석", date(2027, 9, 16): "추석 연휴",
    },
    2028: {
        date(2028, 1, 26): "설날 연휴", date(2028, 1, 27): "설날", date(2028, 1, 28): "설날 연휴",
        date(2028, 4, 12): "제23대 국회의원선거",
        date(2028, 5, 2): "부처님오신날",
        date(2028, 10, 2): "추석 연휴", date(2028, 10, 3): "추석", date(2028, 10, 4): "추석 연휴", date(2028, 10, 5): "대체공휴일(개천절·추석)",
    },
}
# 개장이 늦은 날 (연초 첫 거래일은 아래에서 자동): 대학수학능력시험일 10:00 개장 · 16:30 마감
KR_LATE_OPEN = {date(2025, 11, 13): ((10, 0), (16, 30), "수능일"), date(2026, 11, 19): ((10, 0), (16, 30), "수능일")}


def has_kr_calendar(y):
    return y in KR_YEARLY


@lru_cache(maxsize=64)
def _kr_year(y):
    h = dict(KR_YEARLY.get(y, {}))
    fixed = {}
    for (mo, da), name, sub, sub_since in _KR_FIXED:
        if _KR_SINCE.get((mo, da), 0) > y:
            continue
        d = date(y, mo, da)
        fixed[d] = (name, sub and (sub_since is None or y >= sub_since))
    for d, (name, _s) in fixed.items():
        h.setdefault(d, name)
    # 대체공휴일: 토·일과 겹치는 대상 공휴일 → 그 뒤 첫 '평일이면서 휴일 아닌 날'
    for d, (name, sub) in sorted(fixed.items()):
        if not sub or d.weekday() < 5:
            continue
        n = d + timedelta(days=1)
        while n.weekday() >= 5 or n in h:
            n += timedelta(days=1)
        h[n] = f"대체공휴일({name})"
    # 연말 휴장: 12월 마지막 평일
    last = date(y, 12, 31)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    h.setdefault(last, "연말 휴장")
    return h


# ── 공통 ──────────────────────────────────────────────────────────────────────
def _as_date(d):
    return d.date() if isinstance(d, datetime) else d


def holiday_name(market, d):
    """휴장 사유 (평일 휴장일만). 주말·거래일이면 None."""
    m, d = _market(market), _as_date(d)
    if d.weekday() >= 5:
        return None
    return (_kr_year(d.year) if m == "KR" else _us_year(d.year)[0]).get(d)


def is_trading_day(market, d):
    d = _as_date(d)
    return d.weekday() < 5 and holiday_name(market, d) is None


def early_close_name(market, d):
    if _market(market) != "US":
        return None
    return _us_year(_as_date(d).year)[1].get(_as_date(d))


def session_window(market, d):
    """정규장 ((시, 분), (시, 분)) — 휴장·주말이면 None. 미국 조기폐장일은 13:00 마감, 한국 연초 첫 거래일 10:00 개장."""
    m, d = _market(market), _as_date(d)
    if not is_trading_day(m, d):
        return None
    if m == "US":
        return (REGULAR["US"][0], US_EARLY_CLOSE) if early_close_name("US", d) else REGULAR["US"]
    if d in KR_LATE_OPEN:
        o, c, _ = KR_LATE_OPEN[d]
        return (o, c)
    first = date(d.year, 1, 1)
    while not is_trading_day("KR", first):
        first += timedelta(days=1)
    if d == first:
        return ((10, 0), REGULAR["KR"][1])           # 연초 개장일 10:00 개장
    return REGULAR["KR"]


def _local(market, now):
    tz = TZ[_market(market)]
    if now is None:
        return datetime.now(tz)
    return now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)


def is_market_open(market, now=None):
    now = _local(market, now)
    w = session_window(market, now.date())
    return bool(w) and w[0] <= (now.hour, now.minute) < w[1]


def market_status(market, now=None):
    """→ {open, holiday, early_close, label('장중'|'휴장'|'마감'), session}"""
    m = _market(market)
    now = _local(m, now)
    d = now.date()
    hol = holiday_name(m, d)
    w = session_window(m, d)
    is_open = bool(w) and w[0] <= (now.hour, now.minute) < w[1]
    return {"open": is_open, "holiday": hol, "early_close": bool(early_close_name(m, d)) if w else False,
            "label": "장중" if is_open else ("휴장" if hol else "마감"),
            "session": [f"{w[0][0]:02d}:{w[0][1]:02d}", f"{w[1][0]:02d}:{w[1][1]:02d}"] if w else None}


def next_trading_day(market, d, include=False):
    """d 다음(include=True 면 d 포함) 첫 거래일."""
    d = _as_date(d)
    if not include:
        d += timedelta(days=1)
    for _ in range(30):
        if is_trading_day(market, d):
            return d
        d += timedelta(days=1)
    return d


def prev_trading_day(market, d, include=False):
    d = _as_date(d)
    if not include:
        d -= timedelta(days=1)
    for _ in range(30):
        if is_trading_day(market, d):
            return d
        d -= timedelta(days=1)
    return d
