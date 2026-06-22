"""모의투자 배지 시스템.

각 배지는 dict이며, check(ctx) 함수가 True를 반환하면 부여 대상.
ctx는 _build_badge_context()에서 미리 계산해 전달.
"""

# tier → 색상 매핑은 프론트에서 처리
BADGES = [
    # ─── 거래 활성도 ─────────────────────────────────
    {
        "key": "first_buy", "name": "첫걸음", "icon": "🎯", "tier": "bronze",
        "category": "활동", "desc": "첫 매수 완료",
        "check": lambda c: c["buy_count"] >= 1,
    },
    {
        "key": "trade_10", "name": "활발한 거래자", "icon": "🔟", "tier": "silver",
        "category": "활동", "desc": "10거래 달성",
        "check": lambda c: c["tx_count"] >= 10,
    },
    {
        "key": "trade_50", "name": "단골 트레이더", "icon": "📊", "tier": "gold",
        "category": "활동", "desc": "50거래 달성",
        "check": lambda c: c["tx_count"] >= 50,
    },
    {
        "key": "trade_100", "name": "베테랑", "icon": "💯", "tier": "diamond",
        "category": "활동", "desc": "100거래 달성",
        "check": lambda c: c["tx_count"] >= 100,
    },

    # ─── 수익률 누적 ─────────────────────────────────
    {
        "key": "first_profit", "name": "첫 수익", "icon": "📈", "tier": "bronze",
        "category": "수익", "desc": "처음으로 수익권 진입 (+0.1% 이상)",
        "check": lambda c: c["total_return_pct"] >= 0.1,
    },
    {
        "key": "return_10", "name": "10% 돌파", "icon": "🥉", "tier": "bronze",
        "category": "수익", "desc": "총 수익률 +10% 달성",
        "check": lambda c: c["total_return_pct"] >= 10,
    },
    {
        "key": "return_30", "name": "30% 돌파", "icon": "🥈", "tier": "silver",
        "category": "수익", "desc": "총 수익률 +30% 달성",
        "check": lambda c: c["total_return_pct"] >= 30,
    },
    {
        "key": "return_50", "name": "50% 돌파", "icon": "🥇", "tier": "gold",
        "category": "수익", "desc": "총 수익률 +50% 달성",
        "check": lambda c: c["total_return_pct"] >= 50,
    },
    {
        "key": "return_100", "name": "더블링", "icon": "💎", "tier": "diamond",
        "category": "수익", "desc": "총 수익률 +100% 달성 (자산 2배)",
        "check": lambda c: c["total_return_pct"] >= 100,
    },
    {
        "key": "return_200", "name": "전설", "icon": "👑", "tier": "legend",
        "category": "수익", "desc": "총 수익률 +200% 달성 (자산 3배)",
        "check": lambda c: c["total_return_pct"] >= 200,
    },

    # ─── 단일 거래 ───────────────────────────────────
    {
        "key": "single_win_20", "name": "단타 성공", "icon": "🎰", "tier": "silver",
        "category": "트레이딩", "desc": "한 매도에서 +20% 이상 실현",
        "check": lambda c: c["max_single_realized_pct"] >= 20,
    },
    {
        "key": "single_win_50", "name": "홈런", "icon": "🚀", "tier": "gold",
        "category": "트레이딩", "desc": "한 매도에서 +50% 이상 실현",
        "check": lambda c: c["max_single_realized_pct"] >= 50,
    },
    {
        "key": "first_loss_cut", "name": "손절 마스터", "icon": "🛡️", "tier": "bronze",
        "category": "트레이딩", "desc": "처음으로 손절 매도 실행 (리스크 관리)",
        "check": lambda c: c["loss_cut_count"] >= 1,
    },

    # ─── 포지션 관리 ─────────────────────────────────
    {
        "key": "diversified", "name": "분산 투자자", "icon": "🧺", "tier": "silver",
        "category": "포트폴리오", "desc": "동시에 5종목 이상 보유",
        "check": lambda c: c["holdings_count"] >= 5,
    },
    {
        "key": "concentrate", "name": "집중 투자자", "icon": "🎯", "tier": "silver",
        "category": "포트폴리오", "desc": "단일 종목 5천만원 이상 평가금액",
        "check": lambda c: c["max_position_value_krw"] >= 50_000_000,
    },
    {
        "key": "asset_150m", "name": "자산가", "icon": "💰", "tier": "gold",
        "category": "포트폴리오", "desc": "총자산 1.5억원 돌파",
        "check": lambda c: c["total_assets_krw"] >= 150_000_000,
    },

    # ─── 글로벌 ──────────────────────────────────────
    {
        "key": "global_investor", "name": "글로벌 투자자", "icon": "🌏", "tier": "silver",
        "category": "글로벌", "desc": "한국 + 미국 종목 동시 보유",
        "check": lambda c: c["has_kr"] and c["has_us"],
    },
    {
        "key": "kr_5", "name": "한국 통", "icon": "🇰🇷", "tier": "bronze",
        "category": "글로벌", "desc": "한국 주식 5종목 누적 매수",
        "check": lambda c: c["kr_unique_buys"] >= 5,
    },
    {
        "key": "us_5", "name": "월가 입문", "icon": "🇺🇸", "tier": "bronze",
        "category": "글로벌", "desc": "미국 주식 5종목 누적 매수",
        "check": lambda c: c["us_unique_buys"] >= 5,
    },

    # ─── 승률/성과 ───────────────────────────────────
    {
        "key": "win_rate_60", "name": "안정적 트레이더", "icon": "⚖️", "tier": "silver",
        "category": "성과", "desc": "매도 5회 이상, 승률 60% 이상",
        "check": lambda c: c["sell_count"] >= 5 and c["win_rate"] >= 60,
    },
    {
        "key": "win_rate_70", "name": "명사수", "icon": "🎖️", "tier": "gold",
        "category": "성과", "desc": "매도 10회 이상, 승률 70% 이상",
        "check": lambda c: c["sell_count"] >= 10 and c["win_rate"] >= 70,
    },
    {
        "key": "realized_10m", "name": "천만원 클럽", "icon": "🏆", "tier": "gold",
        "category": "성과", "desc": "누적 실현 손익 +1,000만원 돌파",
        "check": lambda c: c["realized_pnl_krw"] >= 10_000_000,
    },

    # ─── 꾸준함 (스냅샷 일수) ─────────────────────────
    {
        "key": "snapshot_3", "name": "3일 접속", "icon": "🔥", "tier": "bronze",
        "category": "꾸준함", "desc": "3일 이상 자산 추적",
        "check": lambda c: c["snapshot_days"] >= 3,
    },
    {
        "key": "snapshot_7", "name": "1주 접속", "icon": "⚡", "tier": "silver",
        "category": "꾸준함", "desc": "7일 이상 자산 추적",
        "check": lambda c: c["snapshot_days"] >= 7,
    },
    {
        "key": "snapshot_30", "name": "한 달 채움", "icon": "🌟", "tier": "gold",
        "category": "꾸준함", "desc": "30일 이상 자산 추적",
        "check": lambda c: c["snapshot_days"] >= 30,
    },

    # ─── 추가: 거래 습관 (활동) ──────────────────────
    {
        "key": "night_owl", "name": "올빼미", "icon": "🦉", "tier": "bronze",
        "category": "활동", "desc": "한밤중(한국시간 0~6시) 거래 10회 — 미국장 시간대 매매",
        "check": lambda c: c["night_owl_count"] >= 10,
    },
    {
        "key": "single_share", "name": "단주의 낭만", "icon": "🪙", "tier": "bronze",
        "category": "활동", "desc": "딱 1주만 매매해보기",
        "check": lambda c: c["has_single_share"],
    },

    # ─── 추가: 매매 기법 (트레이딩) ──────────────────
    {
        "key": "whale", "name": "큰손", "icon": "🐳", "tier": "silver",
        "category": "트레이딩", "desc": "단일 거래 5천만원 이상 체결",
        "check": lambda c: c["max_single_trade_krw"] >= 50_000_000,
    },
    {
        "key": "averaging_down", "name": "물타기 장인", "icon": "💧", "tier": "silver",
        "category": "트레이딩", "desc": "같은 종목을 3번 이상 나눠서 매수",
        "check": lambda c: c["max_same_ticker_buys"] >= 3,
    },
    {
        "key": "speed_trader", "name": "광속 매매", "icon": "⚡", "tier": "silver",
        "category": "트레이딩", "desc": "매수한 날 바로 매도 (당일 단타)",
        "check": lambda c: c["same_day_flip"],
    },

    # ─── 추가: 보유/포지션 (포트폴리오) ──────────────
    {
        "key": "collector", "name": "종목 수집가", "icon": "🗂️", "tier": "gold",
        "category": "포트폴리오", "desc": "서로 다른 20개 종목 거래",
        "check": lambda c: c["unique_tickers_traded"] >= 20,
    },
    {
        "key": "diamond_hands", "name": "다이아몬드 핸드", "icon": "💎", "tier": "gold",
        "category": "포트폴리오", "desc": "-20% 평가손실에도 안 팔고 버티는 중",
        "check": lambda c: c["worst_holding_pct"] <= -20 and c["holdings_count"] >= 1,
    },
    {
        "key": "all_in", "name": "풀베팅", "icon": "🎲", "tier": "gold",
        "category": "포트폴리오", "desc": "현금 비중 3% 미만 — 총알을 거의 다 투입",
        "check": lambda c: c["holdings_count"] >= 1 and c["cash_ratio"] < 0.03,
    },

    # ─── 추가: 승부/관리 (성과) ──────────────────────
    {
        "key": "win_streak_5", "name": "연승 행진", "icon": "🔥", "tier": "silver",
        "category": "성과", "desc": "5연속 익절 매도 성공",
        "check": lambda c: c["max_win_streak"] >= 5,
    },
    {
        "key": "sharp_cut", "name": "칼같은 손절", "icon": "✂️", "tier": "silver",
        "category": "성과", "desc": "손절 매도 5회 — 냉정한 리스크 관리",
        "check": lambda c: c["loss_cut_count"] >= 5,
    },
]

BADGES_BY_KEY = {b["key"]: b for b in BADGES}


def evaluate_badges(ctx, earned_keys):
    """이미 획득한 배지는 건너뛰고 새로 자격이 된 배지 키 목록 반환."""
    new_keys = []
    for b in BADGES:
        if b["key"] in earned_keys:
            continue
        try:
            if b["check"](ctx):
                new_keys.append(b["key"])
        except Exception:
            # ctx 누락 시 안전 무시
            pass
    return new_keys


def badge_public_dict(badge):
    """프론트로 보낼 때 check 함수 제외."""
    return {
        "key":      badge["key"],
        "name":     badge["name"],
        "icon":     badge["icon"],
        "tier":     badge["tier"],
        "category": badge["category"],
        "desc":     badge["desc"],
    }
