"""섹터맵 유니버스 생성 스크립트 → sector_universe.json

stock_db.json 의 종목(한국 KOSPI·KOSDAQ, 미국 S&P500·NASDAQ)마다 섹터 분류와(미국은) 발행주식수를 붙인다.
분류·주식수는 자주 바뀌지 않으므로 이 파일을 저장소에 넣고, 시세·시총은 운영에서 주기적으로 받는다.

- 한국: 네이버 모바일 업종 API(업종 79개) → 대분류 14개로 묶음 (KR_GROUP)
- 미국: S&P 500 GICS 섹터(공개 데이터셋 CSV) + 나머지는 yfinance info 의 sector. 반도체는 GICS 하위산업으로 분리
- 미국 발행주식수: yfinance fast_info.shares (시총 = 현재가 × 주식수)

실행: python build_sector_universe.py   (수 분 걸림, 네트워크 필요)
"""
import csv
import io
import json
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
H = {"User-Agent": "Mozilla/5.0"}

# ── 한국: 네이버 업종 → 대분류 ────────────────────────────────────────────────
KR_SECTORS = [
    ("semi", "반도체"), ("it_hw", "IT·전자부품"), ("chem", "2차전지·화학"), ("auto", "자동차"),
    ("ship_mach", "조선·기계"), ("defense", "방산·우주"), ("power", "전력·에너지"), ("bio", "바이오·헬스케어"),
    ("finance", "금융"), ("internet", "인터넷·SW·게임"), ("media", "엔터·미디어"), ("consumer", "소비재·유통"),
    ("materials", "철강·건설·소재"), ("transport", "운송·물류"), ("telecom", "통신"), ("etc", "지주·기타"),
]
KR_GROUP = {
    "반도체와반도체장비": "semi",
    "전자장비와기기": "it_hw", "디스플레이패널": "it_hw", "디스플레이장비및부품": "it_hw", "핸드셋": "it_hw",
    "컴퓨터와주변기기": "it_hw", "전자제품": "it_hw", "통신장비": "it_hw", "사무용전자제품": "it_hw",
    "화학": "chem", "전기제품": "chem",
    "자동차": "auto", "자동차부품": "auto",
    "조선": "ship_mach", "기계": "ship_mach",
    "우주항공과국방": "defense",
    "전기장비": "power", "전기유틸리티": "power", "가스유틸리티": "power", "복합유틸리티": "power",
    "석유와가스": "power", "에너지장비및서비스": "power",
    "제약": "bio", "생물공학": "bio", "건강관리장비와용품": "bio", "건강관리기술": "bio",
    "건강관리업체및서비스": "bio", "생명과학도구및서비스": "bio",
    "은행": "finance", "증권": "finance", "손해보험": "finance", "생명보험": "finance", "기타금융": "finance",
    "카드": "finance", "창업투자": "finance",
    "양방향미디어와서비스": "internet", "소프트웨어": "internet", "IT서비스": "internet",
    "게임엔터테인먼트": "internet", "인터넷과카탈로그소매": "internet",
    "방송과엔터테인먼트": "media", "광고": "media", "출판": "media",
    "식품": "consumer", "음료": "consumer", "담배": "consumer", "화장품": "consumer",
    "섬유,의류,신발,호화품": "consumer", "백화점과일반상점": "consumer", "판매업체": "consumer",
    "전문소매": "consumer", "식품과기본식료품소매": "consumer", "가정용품": "consumer",
    "가정용기기와용품": "consumer", "가구": "consumer", "레저용장비와제품": "consumer",
    "호텔,레스토랑,레저": "consumer", "교육서비스": "consumer", "다각화된소비자서비스": "consumer", "문구류": "consumer",
    "철강": "materials", "비철금속": "materials", "건설": "materials", "건축자재": "materials",
    "건축제품": "materials", "포장재": "materials", "종이와목재": "materials",
    "항공사": "transport", "해운사": "transport", "항공화물운송과물류": "transport",
    "도로와철도운송": "transport", "운송인프라": "transport",
    "무선통신서비스": "telecom", "다각화된통신서비스": "telecom",
    # 상업서비스와공급품, 무역회사와판매업체, 복합기업(지주사), 부동산, 기타 → etc
}

# ── 미국: GICS → 섹터 키 ──────────────────────────────────────────────────────
US_SECTORS = [
    ("semi", "반도체"), ("tech", "정보기술"), ("comm", "커뮤니케이션"), ("discretionary", "경기소비재"),
    ("staples", "필수소비재"), ("health", "헬스케어"), ("finance", "금융"), ("industrials", "산업재"),
    ("energy", "에너지"), ("materials", "소재"), ("utilities", "유틸리티"), ("realestate", "부동산"),
]
GICS = {
    "Information Technology": "tech", "Technology": "tech",
    "Communication Services": "comm",
    "Consumer Discretionary": "discretionary", "Consumer Cyclical": "discretionary",
    "Consumer Staples": "staples", "Consumer Defensive": "staples",
    "Health Care": "health", "Healthcare": "health",
    "Financials": "finance", "Financial Services": "finance",
    "Industrials": "industrials", "Energy": "energy",
    "Materials": "materials", "Basic Materials": "materials",
    "Utilities": "utilities", "Real Estate": "realestate",
}
SEMI_SUB = ("Semiconductors", "Semiconductor Materials & Equipment", "Semiconductor Equipment & Materials")
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# 자동 분류가 비거나 GICS 와 어긋난 종목 손보정 (2026-11-20 생성 결과 확인 후)
OVERRIDE = {
    "KR": {"012510.KS": "internet"},                      # 더존비즈온: 네이버 업종 없음 → 소프트웨어
    "US": {"BK": "finance",                               # BNY Mellon: CSV 에 없고 yfinance 정보 실패
           "SATS": "comm",                                # EchoStar: GICS 커뮤니케이션
           "LYFT": "industrials", "GRAB": "industrials"},  # GICS 승객 운송(산업재)
}


def kr_industries():
    """네이버 업종별 구성 종목 → {6자리코드: 업종명}"""
    g = requests.get("https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100", headers=H, timeout=10).json()
    out = {}
    for grp in g["groups"]:
        page = 1
        while True:
            r = requests.get(f"https://m.stock.naver.com/api/stocks/industry/{grp['no']}?page={page}&pageSize=100",
                             headers=H, timeout=10).json()
            stocks = r.get("stocks") or []
            for s in stocks:
                out[s["itemCode"]] = grp["name"]
            if len(stocks) < 100:
                break
            page += 1
            time.sleep(0.2)
        time.sleep(0.2)
    return out


def main():
    db = json.load(open(os.path.join(HERE, "stock_db.json"), encoding="utf-8"))
    kr = [s for s in db if s["symbol"].endswith((".KS", ".KQ"))]
    us = [s for s in db if not s["symbol"].endswith((".KS", ".KQ"))]

    ind = kr_industries()
    kr_out, kr_missing = {}, []
    for s in kr:
        name = ind.get(s["symbol"].split(".")[0])
        if not name:
            kr_missing.append(s["symbol"])
        kr_out[s["symbol"]] = {"sector": KR_GROUP.get(name, "etc"), "industry": name}

    rows = list(csv.DictReader(io.StringIO(requests.get(SP500_CSV, headers=H, timeout=20).text)))
    gics = {r["Symbol"].replace(".", "-"): r for r in rows}
    import yfinance as yf
    us_out, us_missing = {}, []
    for i, s in enumerate(us):
        t = s["symbol"]
        r = gics.get(t)
        sector, sub = (r["GICS Sector"], r["GICS Sub-Industry"]) if r else (None, None)
        tk = yf.Ticker(t)
        if not sector:
            try:
                info = tk.info or {}
                sector, sub = info.get("sector"), info.get("industry")
            except Exception:
                pass
        shares = None
        try:
            shares = int(tk.fast_info.shares or 0) or None
        except Exception:
            pass
        key = "semi" if (sub and any(x in sub for x in SEMI_SUB)) else GICS.get(sector or "", None)
        if not key or not shares:
            us_missing.append(t)
        us_out[t] = {"sector": key or "tech", "gics": sector, "sub": sub, "shares": shares}
        if i % 50 == 0:
            print(f"US {i}/{len(us)}", flush=True)
        time.sleep(0.15)

    for m, ov in OVERRIDE.items():
        for t, key in ov.items():
            if t in (kr_out if m == "KR" else us_out):
                (kr_out if m == "KR" else us_out)[t]["sector"] = key
    out = {"generated": time.strftime("%Y-%m-%d"),
           "sources": {"KR": "네이버 증권 업종", "US": "S&P 500 GICS(datasets/s-and-p-500-companies) + yfinance"},
           "sectors": {"KR": KR_SECTORS, "US": US_SECTORS}, "KR": kr_out, "US": us_out}
    with open(os.path.join(HERE, "sector_universe.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"KR {len(kr_out)} (업종 없음 {len(kr_missing)}: {kr_missing[:10]})")
    print(f"US {len(us_out)} (섹터/주식수 없음 {len(us_missing)}: {us_missing[:20]})")


if __name__ == "__main__":
    main()
