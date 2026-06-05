"""토스 보유종목 → 실제 포트폴리오(holdings) INSERT SQL 생성기.

Railway egress IP가 계속 바뀌어 서버에서 토스 호출이 막힐 때의 우회용.
로컬 PC(토스에 IP 등록됨)에서 실행 → 생성된 SQL을 Railway DB 콘솔에 붙여넣기.

사용법:
    python scripts/toss_to_sql.py <USER_ID>

USER_ID 확인: Railway DB에서  SELECT id, nickname, provider, email FROM users;
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import toss_api


def load_symbol_set():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock_db.json")
    syms = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for s in json.load(f):
                if s.get("symbol"):
                    syms.add(s["symbol"])
    return syms


def to_ticker(symbol, market, symset):
    s = (symbol or "").upper()
    if market == "KR" or s.isdigit():
        for suf in (".KS", ".KQ"):
            if (s + suf) in symset:
                return s + suf
        return s + ".KS"
    return s


def sql_str(v):
    return "'" + str(v).replace("'", "''") + "'"


def main():
    if len(sys.argv) < 2:
        print("사용법: python scripts/toss_to_sql.py <USER_ID>")
        sys.exit(1)
    user_id = int(sys.argv[1])

    if not toss_api.is_enabled():
        print("[오류] .env 에 TOSS_CLIENT_ID/SECRET 이 없습니다.")
        sys.exit(1)

    items = toss_api.get_account_holdings()
    if not items:
        print("[오류] 토스 보유종목을 가져오지 못했습니다 (IP 등록/토큰 확인).")
        sys.exit(1)

    symset = load_symbol_set()

    print("-- ▼ 아래 전체를 Railway Postgres 콘솔에 붙여넣어 실행하세요 --")
    print("BEGIN;")
    print(f"DELETE FROM holdings WHERE user_id = {user_id};")
    n = 0
    for it in items:
        qty = it.get("quantity"); avg = it.get("avg_price")
        if not qty or not avg or qty <= 0:
            continue
        ticker = to_ticker(it.get("symbol"), it.get("market"), symset)
        currency = "KRW" if ticker.endswith((".KS", ".KQ")) else "USD"
        name = it.get("name") or ticker
        print(
            "INSERT INTO holdings (user_id, ticker, name, quantity, purchase_price, currency, created_at) "
            f"VALUES ({user_id}, {sql_str(ticker)}, {sql_str(name)}, {round(qty,6)}, "
            f"{round(avg,6)}, {sql_str(currency)}, NOW());"
        )
        n += 1
    print("COMMIT;")
    print(f"-- ▲ 총 {n}개 종목 --")


if __name__ == "__main__":
    main()
