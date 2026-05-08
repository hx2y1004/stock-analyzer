from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    provider      = db.Column(db.String(20),  nullable=False)   # 'google' | 'kakao'
    provider_id   = db.Column(db.String(100), nullable=False)
    name          = db.Column(db.String(100))
    email         = db.Column(db.String(200))
    profile_image = db.Column(db.String(500))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    holdings      = db.relationship(
        "Holding", backref="user", lazy=True, cascade="all, delete-orphan"
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
