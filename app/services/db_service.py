from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.config import settings
from app.database.models import User, Portfolio, Alert, Subscription, Notification
from datetime import datetime, timedelta, timezone

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None = None) -> User:
    """Get user by telegram_id, or create a new user if not exists."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        plan = "pro" if settings.ADMIN_TELEGRAM_ID and telegram_id == settings.ADMIN_TELEGRAM_ID else "free"
        user = User(
            telegram_id=telegram_id,
            username=username,
            plan=plan
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        if username and user.username != username:
            user.username = username
        if settings.ADMIN_TELEGRAM_ID and telegram_id == settings.ADMIN_TELEGRAM_ID and user.plan != "pro":
            user.plan = "pro"
        await session.commit()
        await session.refresh(user)
        
    return user

async def get_portfolio(session: AsyncSession, user_id: int) -> list[Portfolio]:
    """Retrieve all assets in a user's portfolio."""
    stmt = select(Portfolio).where(Portfolio.user_id == user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def add_portfolio_asset(session: AsyncSession, user_id: int, symbol: str, entry_price: float) -> Portfolio:
    """Add a new asset to a user's portfolio, or update entry price if it already exists."""
    symbol = symbol.upper().strip()
    stmt = select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol)
    result = await session.execute(stmt)
    asset = result.scalar_one_or_none()
    
    if asset:
        asset.entry_price = entry_price
    else:
        asset = Portfolio(
            user_id=user_id,
            symbol=symbol,
            entry_price=entry_price
        )
        session.add(asset)
    
    await session.commit()
    await session.refresh(asset)
    return asset

async def remove_portfolio_asset(session: AsyncSession, user_id: int, symbol: str) -> bool:
    """Remove an asset from a user's portfolio."""
    symbol = symbol.upper().strip()
    stmt = delete(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol)
    result = await session.execute(stmt)
    
    # Also delete associated alerts for this symbol
    stmt_alerts = delete(Alert).where(Alert.user_id == user_id, Alert.symbol == symbol)
    await session.execute(stmt_alerts)
    
    await session.commit()
    return result.rowcount > 0

async def add_alert(session: AsyncSession, user_id: int, symbol: str, alert_type: str, target_value: float) -> Alert:
    """Add a price alert."""
    symbol = symbol.upper().strip()
    alert = Alert(
        user_id=user_id,
        symbol=symbol,
        alert_type=alert_type,
        target_value=target_value,
        is_active=True
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert

async def get_user_alerts(session: AsyncSession, user_id: int) -> list[Alert]:
    """Get active alerts for a user."""
    stmt = select(Alert).where(Alert.user_id == user_id, Alert.is_active == True)
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def deactivate_alert(session: AsyncSession, alert_id: int) -> bool:
    """Deactivate a triggered or cancelled alert."""
    stmt = select(Alert).where(Alert.id == alert_id)
    result = await session.execute(stmt)
    alert = result.scalar_one_or_none()
    if alert:
        alert.is_active = False
        await session.commit()
        return True
    return False

async def remove_alert_by_id(session: AsyncSession, user_id: int, alert_id: int) -> bool:
    """Delete alert by ID."""
    stmt = delete(Alert).where(Alert.user_id == user_id, Alert.id == alert_id)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0

async def add_notification(session: AsyncSession, user_id: int, message: str) -> Notification:
    """Record a sent notification in history."""
    notif = Notification(user_id=user_id, message=message)
    session.add(notif)
    await session.commit()
    await session.refresh(notif)
    return notif
