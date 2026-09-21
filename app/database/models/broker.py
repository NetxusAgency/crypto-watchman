from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), default="ctrader")
    label: Mapped[str] = mapped_column(String(100), default="cTrader account")
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    client_id_enc: Mapped[str] = mapped_column(Text, default="")
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    # Account type: "demo" (simulated funds on the real platform) or "live".
    # Demo accounts may be armed without the global kill-switch; live ones
    # additionally require settings.LIVE_TRADING_ENABLED.
    mode: Mapped[str] = mapped_column(String(10), default="demo")
    # Arming flag: explicit per-user consent for real orders on this account.
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="broker_connections")
    live_trades = relationship("LiveTrade", back_populates="connection", cascade="all, delete-orphan")


class LiveTrade(Base):
    __tablename__ = "live_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plan_id: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # LONG / SHORT
    strategy_key: Mapped[str] = mapped_column(String(50))
    strategy_name: Mapped[str] = mapped_column(String(120))
    timeframe: Mapped[str] = mapped_column(String(10))
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_id: Mapped[str] = mapped_column(String(60), default="")
    # PENDING_CONFIRM / PLACED / REJECTED / CLOSED
    status: Mapped[str] = mapped_column(String(20), default="PENDING_CONFIRM")
    reason: Mapped[str] = mapped_column(Text, default="")
    # True = simulated execution (never touches the broker).
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="live_trades")
    connection = relationship("BrokerConnection", back_populates="live_trades")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "strategy_key": self.strategy_key,
            "strategy_name": self.strategy_name,
            "timeframe": self.timeframe,
            "entry_price": round(self.entry_price, 6),
            "quantity": self.quantity,
            "stop_loss": round(self.stop_loss, 6),
            "take_profit_1": round(self.take_profit_1, 6) if self.take_profit_1 else None,
            "take_profit_2": round(self.take_profit_2, 6) if self.take_profit_2 else None,
            "order_id": self.order_id,
            "status": self.status,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "realized_pnl": round(self.realized_pnl, 2) if self.realized_pnl is not None else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


__all__ = ["BrokerConnection", "LiveTrade"]