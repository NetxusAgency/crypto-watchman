from datetime import datetime, timezone
from sqlalchemy import BigInteger, ForeignKey, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="Paper Account #1")
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    initial_balance: Mapped[float] = mapped_column(Float, default=10_000.0)
    cash: Mapped[float] = mapped_column(Float, default=10_000.0)
    equity: Mapped[float] = mapped_column(Float, default=10_000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="paper_accounts")
    positions = relationship("PaperPosition", back_populates="account", cascade="all, delete-orphan")


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy_key: Mapped[str] = mapped_column(String(50))
    timeframe: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str] = mapped_column(String(10))  # LONG / SHORT
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN/CLOSED
    close_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)  # STOP/TP1/TP2/INVALIDATION/CANCEL
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account = relationship("PaperAccount", back_populates="positions")
    fills = relationship("PaperFill", back_populates="position", cascade="all, delete-orphan")


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("paper_positions.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(10))  # BUY / SELL
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    filled_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    position = relationship("PaperPosition", back_populates="fills")


__all__ = ["PaperAccount", "PaperPosition", "PaperFill"]