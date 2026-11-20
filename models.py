from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


# 모의투자 초기 자본 (1억원)
INITIAL_CAPITAL_KRW = 100_000_000


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    provider      = db.Column(db.String(20),  nullable=False)   # 'google' | 'kakao'
    provider_id   = db.Column(db.String(100), nullable=False)
    name          = db.Column(db.String(100))
    email         = db.Column(db.String(200))
    profile_image = db.Column(db.String(500))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # 모의투자
    cash_balance    = db.Column(db.Float, default=INITIAL_CAPITAL_KRW)   # 현재 보유 현금 (KRW)
    initial_capital = db.Column(db.Float, default=INITIAL_CAPITAL_KRW)   # 시작 자본 (수익률 계산용)

    # 랭킹/프로필
    nickname        = db.Column(db.String(30), unique=True, nullable=True)
    is_public       = db.Column(db.Boolean, default=True)   # 랭킹 공개 여부
    reset_count     = db.Column(db.Integer, default=0)      # 모의투자 1억 초기화 횟수

    holdings = db.relationship(
        "Holding", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    transactions = db.relationship(
        "Transaction", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    __table_args__ = (
        db.UniqueConstraint("provider", "provider_id", name="uq_provider_id"),
    )


class Holding(db.Model):
    __tablename__   = "holdings"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker          = db.Column(db.String(30),  nullable=False)
    name            = db.Column(db.String(200), nullable=False)
    quantity        = db.Column(db.Float,  nullable=False)
    purchase_price  = db.Column(db.Float,  nullable=False)
    currency        = db.Column(db.String(10), default="USD")
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":             self.id,
            "ticker":         self.ticker,
            "name":           self.name,
            "quantity":       self.quantity,
            "purchase_price": self.purchase_price,
            "currency":       self.currency,
        }


class PaperHolding(db.Model):
    """모의투자 보유 종목 (1억 시뮬레이션). 실제 포트폴리오(Holding)와 분리."""
    __tablename__   = "paper_holdings"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker          = db.Column(db.String(30),  nullable=False)
    name            = db.Column(db.String(200), nullable=False)
    quantity        = db.Column(db.Float,  nullable=False)
    purchase_price  = db.Column(db.Float,  nullable=False)
    currency        = db.Column(db.String(10), default="USD")
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":             self.id,
            "ticker":         self.ticker,
            "name":           self.name,
            "quantity":       self.quantity,
            "purchase_price": self.purchase_price,
            "currency":       self.currency,
        }


class Transaction(db.Model):
    """모의투자 매매 내역."""
    __tablename__ = "transactions"
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker       = db.Column(db.String(30),  nullable=False)
    name         = db.Column(db.String(200), nullable=False)
    type         = db.Column(db.String(10), nullable=False)     # 'buy' | 'sell'
    price        = db.Column(db.Float,  nullable=False)         # 거래 단가 (native currency)
    quantity     = db.Column(db.Float,  nullable=False)
    currency     = db.Column(db.String(10), nullable=False)     # 'USD' | 'KRW'
    exchange_rate = db.Column(db.Float, default=1.0)            # 당시 USD/KRW 환율 (KRW일 때는 1.0)
    fee_krw      = db.Column(db.Float, default=0)               # 수수료 (KRW 환산)
    amount_krw   = db.Column(db.Float, nullable=False)          # 거래 총액 KRW (수수료 제외)
    realized_pnl_krw = db.Column(db.Float, default=0)           # 매도 시 실현 손익 (KRW)
    note         = db.Column(db.String(300))                    # 포지션 노트 (매매 이유 메모, 선택)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":            self.id,
            "ticker":        self.ticker,
            "name":          self.name,
            "type":          self.type,
            "price":         self.price,
            "quantity":      self.quantity,
            "currency":      self.currency,
            "exchange_rate": self.exchange_rate,
            "fee_krw":       self.fee_krw,
            "amount_krw":    self.amount_krw,
            "realized_pnl_krw": self.realized_pnl_krw,
            "note":          self.note,
            "timestamp":     self.timestamp.isoformat() if self.timestamp else None,
        }


class AssetSnapshot(db.Model):
    """모의투자 일별 자산 스냅샷 (자산 변화 차트용)."""
    __tablename__ = "asset_snapshots"
    id                  = db.Column(db.Integer, primary_key=True)
    user_id             = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date                = db.Column(db.Date, nullable=False)
    total_assets_krw    = db.Column(db.Float, nullable=False)
    cash_krw            = db.Column(db.Float, nullable=False)
    positions_value_krw = db.Column(db.Float, nullable=False)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "date", name="uq_user_snapshot_date"),
    )

    def to_dict(self):
        return {
            "date":                self.date.isoformat() if self.date else None,
            "total_assets_krw":    round(self.total_assets_krw),
            "cash_krw":            round(self.cash_krw),
            "positions_value_krw": round(self.positions_value_krw),
        }


class UserBadge(db.Model):
    """사용자가 획득한 배지."""
    __tablename__ = "user_badges"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    badge_key  = db.Column(db.String(50), nullable=False)   # BADGES 의 key
    earned_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "badge_key", name="uq_user_badge_key"),
    )

    def to_dict(self):
        return {
            "badge_key": self.badge_key,
            "earned_at": self.earned_at.isoformat() if self.earned_at else None,
        }


class AiCache(db.Model):
    """AI 생성 결과 영구 캐시. 재시작·재배포에도 남아 무료 한도를 아낀다.

    cache_type: company_profile(30일) / news_translation(영구, expires_at=None) / daily_insight(수 시간)
    """
    __tablename__ = "ai_cache"
    id           = db.Column(db.Integer, primary_key=True)
    cache_key    = db.Column(db.String(200), nullable=False, unique=True, index=True)
    ticker       = db.Column(db.String(30), index=True)
    cache_type   = db.Column(db.String(40), nullable=False, index=True)
    payload_json = db.Column(db.Text, nullable=False)
    model_used   = db.Column(db.String(120))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at   = db.Column(db.DateTime, nullable=True)        # None = 만료 없음


# ── AI 매매 신호 봇 (Step 1) ────────────────────────────────────────────────
class BotStrategy(db.Model):
    """봇 전략 설정. 파라미터는 JSON 이라 코드 수정 없이 튜닝·추가할 수 있다."""
    __tablename__ = "bot_strategies"
    id          = db.Column(db.Integer, primary_key=True)
    key         = db.Column(db.String(40), nullable=False, unique=True)   # aggressive_momentum ...
    name        = db.Column(db.String(60), nullable=False)
    emoji       = db.Column(db.String(8))
    kind        = db.Column(db.String(20), nullable=False)                # momentum | swing | value
    use_ai      = db.Column(db.Boolean, default=True)
    enabled     = db.Column(db.Boolean, default=True)
    params_json = db.Column(db.Text, nullable=False)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)   # Step 3: 봇 모의투자 계정
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow)


class AiStockAssessment(db.Model):
    """종목·날짜별 공통 AI 평가. 여러 봇이 같은 평가를 공유한다(종목당 하루 1회 호출)."""
    __tablename__ = "ai_stock_assessments"
    id          = db.Column(db.Integer, primary_key=True)
    ticker      = db.Column(db.String(30), nullable=False, index=True)
    as_of       = db.Column(db.Date, nullable=False, index=True)          # 시장 기준 날짜
    confidence  = db.Column(db.Integer)                                   # 0~100
    verdict     = db.Column(db.String(10))                                # pass | caution | reject
    catalyst    = db.Column(db.Boolean, default=False)                    # 단기 호재·모멘텀
    fatal_risk  = db.Column(db.Boolean, default=False)                    # 치명적 악재
    structural_risk = db.Column(db.Boolean, default=False)                # 구조적 악재
    summary     = db.Column(db.String(300))
    payload_json = db.Column(db.Text)                                     # 전체 응답 + 입력 요약
    model_used  = db.Column(db.String(120))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("ticker", "as_of", name="uq_ai_assess_ticker_date"),)


class AiSignal(db.Model):
    """봇별 신호 불변 스냅샷 + 사후 결과.

    status: PENDING(다음 장 체결 대기) → ACTIVE → TARGET_HIT | STOP_LOSS | TRAILING_STOP | TIME_STOP
            | TREND_EXIT | AI_RISK_EXIT,  또는 CANCELLED(다음 장 시가가 계획을 깨뜨림)
    """
    __tablename__ = "ai_signals"
    id            = db.Column(db.Integer, primary_key=True)
    strategy_key  = db.Column(db.String(40), nullable=False, index=True)
    ticker        = db.Column(db.String(30), nullable=False, index=True)
    name          = db.Column(db.String(200))
    market        = db.Column(db.String(4))                               # KR | US
    side          = db.Column(db.String(4), nullable=False, default="BUY")
    signal_date   = db.Column(db.Date, nullable=False)                    # 판단에 쓴 일봉 날짜
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    price_at_signal = db.Column(db.Float, nullable=False)                 # 신호 당시 종가
    entry_price   = db.Column(db.Float)                                   # 계획 진입가(=신호 종가)
    target_price  = db.Column(db.Float)
    stop_price    = db.Column(db.Float)
    rr            = db.Column(db.Float)                                   # 손익비
    confidence    = db.Column(db.Integer)                                 # AI+정량 결합 (AI 미사용 봇은 정량)
    quant_score   = db.Column(db.Integer)
    assessment_id = db.Column(db.Integer, db.ForeignKey("ai_stock_assessments.id"), nullable=True)
    reason        = db.Column(db.String(300))                             # 한 줄 근거
    evidence_json = db.Column(db.Text)                                    # 판단 근거 전체 (불변)
    status        = db.Column(db.String(16), nullable=False, default="PENDING", index=True)
    status_reason = db.Column(db.String(200))
    fill_price    = db.Column(db.Float)                                   # 다음 장 시가 체결가
    filled_date   = db.Column(db.Date)
    peak_price    = db.Column(db.Float)                                   # 트레일링용 최고가
    exit_price    = db.Column(db.Float)
    exit_date     = db.Column(db.Date)
    pnl_pct       = db.Column(db.Float)                                   # 수수료 반영 수익률(%)
    holding_days  = db.Column(db.Integer)                                 # 보유 거래일


class UserSignalSubscription(db.Model):
    """신호 알림 구독 (Step 2 화면에서 사용)."""
    __tablename__ = "user_signal_subscriptions"
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    strategy_keys  = db.Column(db.String(300), nullable=False)            # 콤마 구분
    scope          = db.Column(db.String(20), nullable=False, default="all")   # all | portfolio | watchlist
    min_confidence = db.Column(db.Integer, default=70)
    enabled        = db.Column(db.Boolean, default=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow)


class WatchlistItem(db.Model):
    """관심종목 (보유와 무관하게 지켜보는 종목). 신호 알림 범위 'watchlist' 에 쓰인다."""
    __tablename__ = "watchlist_items"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    ticker     = db.Column(db.String(30), nullable=False)
    name       = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),)


class Notification(db.Model):
    """사용자 알림 (헤더 종 아이콘). 지금은 봇 신호만 쓴다.
    kind: signal_new(새 매수 신호) | signal_exit(청산) | signal_cancel(체결 전 취소)"""
    __tablename__ = "notifications"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind       = db.Column(db.String(30), nullable=False)
    title      = db.Column(db.String(200), nullable=False)
    body       = db.Column(db.String(500))
    link       = db.Column(db.String(300))
    signal_id  = db.Column(db.Integer, db.ForeignKey("ai_signals.id"), nullable=True, index=True)
    is_read    = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (db.UniqueConstraint("user_id", "signal_id", "kind", name="uq_notif_user_signal_kind"),)
