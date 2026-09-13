from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Asset(Base):
    """Dynamic asset registry driving multi-asset whale monitoring.

    `whale_threshold` is the min on-chain value (native units) considered a whale.
    `tier` is a reputation/liquidity tier (high / medium / low) used later by the
    opportunity engine. `flags` holds comma-separated tags like "stablecoin", "listed".
    """
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    chain: Mapped[str] = mapped_column(String(30), index=True)
    contract_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decimals: Mapped[int] = mapped_column(Integer, default=18)
    whale_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    flags: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WhaleTransaction(Base):
    """Stored large on-chain transactions, deduplicated by (asset, txid)."""
    __tablename__ = "whale_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    asset: Mapped[str] = mapped_column(String(20), index=True)
    chain: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float] = mapped_column(Float)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    txid: Mapped[str] = mapped_column(String(128))
    from_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50))
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    direction_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class WhalePreference(Base):
    """Per-user override of which assets the whale scanner should monitor.

    If a user has no rows here, the full asset registry is scanned.
    """
    __tablename__ = "whale_preferences"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_whale_pref_user_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="whale_preferences")


__all__ = ["Asset", "WhaleTransaction", "WhalePreference"]