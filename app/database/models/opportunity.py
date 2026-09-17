from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OpportunitySignal(Base):
    """One component of a computed opportunity score (catalyst evidence).

    `source` is one of news / volume / momentum / social / listing / whale / liquidity.
    `direction` is +1 (bullish), -1 (bearish) or 0 (neutral). `strength` is 0..1.
    `score` = weight * strength * direction (signed).
    """
    __tablename__ = "opportunity_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(20), index=True)
    weight: Mapped[int] = mapped_column(Integer)
    direction: Mapped[int] = mapped_column(Integer, default=0)
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class OpportunityScore(Base):
    """Latest weighted opportunity score per symbol (-100..+100, signed) with priority."""
    __tablename__ = "opportunity_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    total_score: Mapped[float] = mapped_column(Float)
    direction: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[str] = mapped_column(String(20), index=True)
    components: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


__all__ = ["OpportunitySignal", "OpportunityScore"]