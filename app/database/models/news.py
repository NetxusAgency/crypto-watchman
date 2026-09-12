from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NewsArticle(Base):
    """Temporary raw news storage for the AI news engine.

    Rows are short-lived: `expires_at` is enforced by a scheduled cleanup job and
    configured via `NEWS_RETENTION_HOURS`.
    """
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    related_assets: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    processing_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class NewsAnalysis(Base):
    """Per-asset AI verdict built from the collected articles."""
    __tablename__ = "news_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    positive_count: Mapped[int] = mapped_column(Integer, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, default=0)
    overall_sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    impact_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    impact_score: Mapped[int] = mapped_column(Integer, default=0)
    potential_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    time_horizon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_categories: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, index=True)


__all__ = ["NewsArticle", "NewsAnalysis"]