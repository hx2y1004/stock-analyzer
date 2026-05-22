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
