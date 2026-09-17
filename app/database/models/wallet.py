from datetime import datetime, timezone
from sqlalchemy import BigInteger, ForeignKey, String, Float, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(64), index=True)
    network: Mapped[str] = mapped_column(String(20), default="ethereum")
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="wallets")
    balances = relationship("WalletBalance", back_populates="wallet", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "network", "address", name="uq_wallet_user_network_address"),
    )


class WalletBalance(Base):
    __tablename__ = "wallet_balances"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    contract_address: Mapped[str] = mapped_column(String(64), default="native")
    network: Mapped[str] = mapped_column(String(20), default="ethereum")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    wallet = relationship("Wallet", back_populates="balances")


__all__ = ["Wallet", "WalletBalance"]