from datetime import datetime, timezone
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ListingEvent(Base):
    """New exchange listing, recorded when the listing monitor detects it.

    `symbol` is the normalized base asset (e.g. BTC) used by the opportunity
    engine; `pair` keeps the raw exchange pair (e.g. BTCUSDT / BTC-USD).
    """
    __tablename__ = "listing_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(30), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    pair: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


__all__ = ["ListingEvent"]