from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.config import settings
from app.database.models import (
    User,
    Portfolio,
    Alert,
    Subscription,
    Notification,
BrokerConnection,
    LiveTrade,
    TradingStrategy,
)
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


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Retrieve user by telegram_id without modifying or creating."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_portfolio(session: AsyncSession, user_id: int) -> list[Portfolio]:
    """Retrieve all assets in a user's portfolio."""
    stmt = select(Portfolio).where(Portfolio.user_id == user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def all_portfolio_symbols(session: AsyncSession) -> list[str]:
    """Distinct asset symbols across all users (for global news ingestion)."""
    stmt = select(Portfolio.symbol).distinct()
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


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
async def get_user_broker_connections(session: AsyncSession, user_id: int) -> list[BrokerConnection]:
    """All of the user's saved broker connections (decrypted secrets handled by caller)."""
    stmt = (
        select(BrokerConnection)
        .where(BrokerConnection.user_id == user_id)
        .order_by(BrokerConnection.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_broker_connection(session: AsyncSession, user_id: int, connection_id: int) -> BrokerConnection | None:
    stmt = select(BrokerConnection).where(
        BrokerConnection.id == connection_id, BrokerConnection.user_id == user_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def save_broker_connection(
    session: AsyncSession,
    user_id: int,
    *,
    connection_id: int | None = None,
    label: str = "",
    platform: str = "ctrader",
    account_id: str = "",
    access_token_enc: str = "",
    client_id_enc: str = "",
    client_secret_enc: str = "",
    is_live: bool = False,
) -> BrokerConnection:
    """Create or update a broker connection; secrets arrive already encrypted."""
    connection = None
    if connection_id is not None:
        connection = await get_broker_connection(session, user_id, connection_id)
    if connection is None:
        stmt = select(BrokerConnection).where(
            BrokerConnection.user_id == user_id, BrokerConnection.account_id == account_id
        )
        result = await session.execute(stmt)
        connection = result.scalar_one_or_none()

    if connection is None:
        connection = BrokerConnection(
            user_id=user_id,
            label=label or f"{platform} {account_id}",
            platform=platform,
            account_id=account_id,
        )
        session.add(connection)
    connection.label = label or connection.label
    connection.platform = platform or connection.platform
    if account_id:
        connection.account_id = account_id
    if access_token_enc:
        connection.access_token_enc = access_token_enc
    if client_id_enc:
        connection.client_id_enc = client_id_enc
    if client_secret_enc:
        connection.client_secret_enc = client_secret_enc
    if is_live != connection.is_live:
        connection.is_live = is_live
    await session.commit()
    await session.refresh(connection)
    return connection


async def set_broker_connection_live(session: AsyncSession, user_id: int, connection_id: int, is_live: bool) -> bool:
    """Arm (is_live=True) or disarm a connection. Returns False if not found."""
    connection = await get_broker_connection(session, user_id, connection_id)
    if not connection:
        return False
    connection.is_live = is_live
    connection.is_active = True
    await session.commit()
    return True


async def delete_broker_connection(session: AsyncSession, user_id: int, connection_id: int) -> bool:
    stmt = delete(BrokerConnection).where(
        BrokerConnection.id == connection_id, BrokerConnection.user_id == user_id
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0


async def get_user_live_trades(session: AsyncSession, user_id: int, limit: int = 30) -> list[LiveTrade]:
    stmt = (
        select(LiveTrade)
        .where(LiveTrade.user_id == user_id)
        .order_by(LiveTrade.opened_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_strategies(session: AsyncSession, user_id: int, limit: int = 20) -> list[TradingStrategy]:
    stmt = (
        select(TradingStrategy)
        .where(TradingStrategy.user_id == user_id)
        .order_by(TradingStrategy.updated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
