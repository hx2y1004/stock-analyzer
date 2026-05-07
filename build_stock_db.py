"""
주식 검색 DB 빌드 스크립트
실행: python build_stock_db.py
결과: stock_db.json 생성
"""
import json
import time
import requests
import pandas as pd
import FinanceDataReader as fdr

DB_FILE = "stock_db.json"


def fetch_kospi(top_n=200):
    print(f"[1/4] KOSPI 상위 {top_n} 종목 수집 중...")
    df = fdr.StockListing("KOSPI")
    df = df[["Code", "Name", "Marcap"]].dropna()
    df = df[df["Marcap"] > 0].sort_values("Marcap", ascending=False).head(top_n)
    result = []
    for _, row in df.iterrows():
        code = str(row["Code"]).zfill(6)
        result.append({
            "symbol": f"{code}.KS",
            "name": row["Name"],
            "exchange": "KSC",
            "market": "KOSPI",
            "type": "EQUITY",
        })
    print(f"  → {len(result)}개 완료")
    return result


def fetch_kosdaq(top_n=150):
    print(f"[2/4] KOSDAQ 상위 {top_n} 종목 수집 중...")
    df = fdr.StockListing("KOSDAQ")
    df = df[["Code", "Name", "Marcap"]].dropna()
    df = df[df["Marcap"] > 0].sort_values("Marcap", ascending=False).head(top_n)
    result = []
    for _, row in df.iterrows():
        code = str(row["Code"]).zfill(6)
        result.append({
            "symbol": f"{code}.KQ",
            "name": row["Name"],
            "exchange": "KOE",
            "market": "KOSDAQ",
            "type": "EQUITY",
        })
    print(f"  → {len(result)}개 완료")
    return result


def fetch_sp500():
    print("[3/4] S&P 500 collecting...")
    try:
        html = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 (compatible; stockanalyzer/1.0)"},
            timeout=20,
        ).text
        tables = pd.read_html(html)
        df = tables[0][["Symbol", "Security", "GICS Sector"]].dropna()
        result = []
        for _, row in df.iterrows():
            result.append({
                "symbol": str(row["Symbol"]).replace(".", "-"),
                "name": str(row["Security"]),
                "exchange": "NYSE/NASDAQ",
                "market": "S&P500",
                "type": "EQUITY",
            })
        print(f"  -> {len(result)} done")
        return result
    except Exception as e:
        print(f"  ! S&P500 failed: {e}")
        return []


def fetch_nasdaq(top_n=300):
    print(f"[4/4] NASDAQ top {top_n} collecting...")
    try:
        # NASDAQ-100 + 추가 대형주
        html = requests.get(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers={"User-Agent": "Mozilla/5.0 (compatible; stockanalyzer/1.0)"},
            timeout=20,
        ).text
        tables = pd.read_html(html)
        df = None
        for t in tables:
            if "Ticker" in t.columns or "Symbol" in t.columns:
                df = t
                break
        if df is None:
            raise ValueError("테이블을 찾을 수 없음")

        sym_col = "Ticker" if "Ticker" in df.columns else "Symbol"
        name_col = "Company" if "Company" in df.columns else "Security"
        df = df[[sym_col, name_col]].dropna()

        result = []
        for _, row in df.iterrows():
            result.append({
                "symbol": str(row[sym_col]).strip(),
                "name": str(row[name_col]).strip(),
                "exchange": "NASDAQ",
                "market": "NASDAQ100",
                "type": "EQUITY",
            })

        # 추가 대형주 (NASDAQ 상장 주요 종목)
        extra = [
            ("TSLA","Tesla Inc."),("META","Meta Platforms"),("AMZN","Amazon"),
            ("NFLX","Netflix"),("INTC","Intel"),("AMD","AMD"),
            ("QCOM","Qualcomm"),("TXN","Texas Instruments"),("AMAT","Applied Materials"),
            ("LRCX","Lam Research"),("ADI","Analog Devices"),("KLAC","KLA Corp"),
            ("SNPS","Synopsys"),("CDNS","Cadence Design"),("MRVL","Marvell Technology"),
            ("PANW","Palo Alto Networks"),("CRWD","CrowdStrike"),("FTNT","Fortinet"),
            ("ZS","Zscaler"),("OKTA","Okta"),("DDOG","Datadog"),("NET","Cloudflare"),
            ("SNOW","Snowflake"),("PLTR","Palantir"),("COIN","Coinbase"),
            ("RBLX","Roblox"),("UBER","Uber"),("LYFT","Lyft"),("ABNB","Airbnb"),
            ("DASH","DoorDash"),("PINS","Pinterest"),("SNAP","Snap"),
            ("SPOT","Spotify"),("HOOD","Robinhood"),("SOFI","SoFi"),
            ("RIVN","Rivian"),("LCID","Lucid Motors"),("NIO","NIO"),("XPEV","XPeng"),
            ("BIDU","Baidu"),("JD","JD.com"),("PDD","PDD Holdings"),
            ("BILI","Bilibili"),("NTES","NetEase"),("BABA","Alibaba"),
            ("SE","Sea Limited"),("GRAB","Grab"),("GOTO","GoTo"),
            ("ARM","Arm Holdings"),("SMCI","Super Micro Computer"),
        ]
        existing_symbols = {r["symbol"] for r in result}
        for sym, name in extra:
            if sym not in existing_symbols:
                result.append({"symbol": sym, "name": name, "exchange": "NASDAQ", "market": "NASDAQ", "type": "EQUITY"})

        result = result[:top_n]
        print(f"  -> {len(result)} done")
        return result
    except Exception as e:
        print(f"  ! NASDAQ failed: {e}")
        return []


def build():
    all_stocks = []
    seen = set()

    for fetch_fn in [fetch_kospi, fetch_kosdaq, fetch_sp500, fetch_nasdaq]:
        stocks = fetch_fn()
        for s in stocks:
            if s["symbol"] not in seen:
                all_stocks.append(s)
                seen.add(s["symbol"])
        time.sleep(0.5)

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(all_stocks, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Total {len(all_stocks)} stocks -> {DB_FILE}")
    breakdown = {}
    for s in all_stocks:
        m = s.get("market", "etc")
        breakdown[m] = breakdown.get(m, 0) + 1
    for market, cnt in breakdown.items():
        print(f"  {market}: {cnt}")


if __name__ == "__main__":
    build()
