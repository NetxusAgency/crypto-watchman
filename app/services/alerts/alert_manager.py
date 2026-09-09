from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.models import Alert, User, Portfolio
from app.services.prices.price_fetcher import price_fetcher
from app.services.sentiment.sentiment_monitor import sentiment_monitor
from app.services.whale_tracker.whale_tracker import whale_tracker
from app.services import db_service
from app.bot.dispatcher import bot
import logging
import time

logger = logging.getLogger("crypto_watchman.alert_manager")

_last_sentiment_check = 0.0
_seen_whale_txids: set[str] = set()

async def check_all_alerts(session: AsyncSession):
    """
    Check all active alerts in the database.
    Fetches unique prices, checks against conditions, and sends Telegram notifications if triggered.
    """
    # 1. Fetch active alerts with user info
    stmt = select(Alert).where(Alert.is_active == True)
    result = await session.execute(stmt)
    alerts = result.scalars().all()
    
    if not alerts:
        return
        
    # 2. Split alerts by type
    price_alerts = [a for a in alerts if a.alert_type in ("price_above", "price_below", "move_percent")]
    volume_alerts = [a for a in alerts if a.alert_type == "volume_above"]
    volatility_alerts = [a for a in alerts if a.alert_type == "volatility"]
    sentiment_alerts = [a for a in alerts if a.alert_type == "sentiment_spike"]
    whale_alerts = [a for a in alerts if a.alert_type == "whale_alert"]

    # 2a. Fetch prices for price-based alerts
    price_symbols = list({a.symbol for a in price_alerts})
    prices = {}
    for symbol in price_symbols:
        price = await price_fetcher.get_price(symbol)
        if price is not None:
            prices[symbol] = price

    # 2b. Fetch volumes for volume-based alerts
    volume_symbols = list({a.symbol for a in volume_alerts})
    volumes = {}
    for symbol in volume_symbols:
        volume = await price_fetcher.fetch_crypto_volume(symbol)
        if volume is not None:
            volumes[symbol] = volume

    # 2c. Fetch volatility scores for volatility-based alerts
    volatility_symbols = list({a.symbol for a in volatility_alerts})
    zscores = {}
    for symbol in volatility_symbols:
        zscore = await price_fetcher.fetch_volatility_zscore(symbol)
        if zscore is not None:
            zscores[symbol] = zscore

    # 2d. Fetch mention counts for sentiment-based alerts (at most once per 15 min)
    global _last_sentiment_check
    sentiment_counts = {}
    if sentiment_alerts and (time.time() - _last_sentiment_check) > 900:
        sentiment_symbols = list({a.symbol for a in sentiment_alerts})
        for symbol in sentiment_symbols:
            counts = await sentiment_monitor.get_mention_count(symbol)
            if counts is not None:
                sentiment_counts[symbol] = counts["total"]
        _last_sentiment_check = time.time()

    # 2e. Fetch whale transactions for whale alerts
    global _seen_whale_txids
    whale_txs: list[dict] = []
    if whale_alerts:
        whale_txs = await whale_tracker.get_whales()
        if len(_seen_whale_txids) > 1000:
            _seen_whale_txids.clear()

    # 3. Process alerts
    for alert in alerts:
        triggered = False
        message = ""

        if alert.alert_type == "volume_above":
            current_volume = volumes.get(alert.symbol)
            if current_volume is None:
                continue
            if current_volume >= alert.target_value:
                triggered = True
                message = (
                    f"📊 <b>{alert.symbol} Volume Alert Triggered!</b>\n\n"
                    f"24h volume has exceeded your target of <code>${alert.target_value:,.0f}</code>.\n"
                    f"Current 24h Volume: <b>${current_volume:,.0f}</b>"
                )
        elif alert.alert_type == "volatility":
            zscore = zscores.get(alert.symbol)
            if zscore is None:
                continue
            if zscore >= alert.target_value:
                triggered = True
                message = (
                    f"🌊 <b>{alert.symbol} Volatility Alert Triggered!</b>\n\n"
                    f"Daily price move is <b>{zscore:.1f}x</b> the average, exceeding your {alert.target_value}x threshold.\n"
                    f"This indicates unusually high volatility."
                )
        elif alert.alert_type == "sentiment_spike":
            current_mentions = sentiment_counts.get(alert.symbol)
            if current_mentions is None:
                continue
            if current_mentions >= alert.target_value:
                triggered = True
                message = (
                    f"📢 <b>{alert.symbol} Sentiment Spike!</b>\n\n"
                    f"24h mentions have reached <b>{current_mentions:.0f}</b>, exceeding your threshold of {alert.target_value:.0f}.\n"
                    f"Reddit and crypto news activity is elevated."
                )
        elif alert.alert_type == "whale_alert":
            for tx in whale_txs:
                if tx["asset"] != alert.symbol:
                    continue
                txid = tx["txid"]
                if txid in _seen_whale_txids:
                    continue
                if tx["value"] < alert.target_value:
                    continue
                _seen_whale_txids.add(txid)
                triggered = True
                message = (
                    f"🐋 <b>Whale Transaction Detected!</b>\n\n"
                    f"• Asset: <b>{tx['asset']}</b>\n"
                    f"• Value: <b>{tx['value']:,.2f}</b>\n"
                    f"• Tx: <code>{tx['txid']}</code>\n"
                    f"• Source: {tx['source']}"
                )
                to_addr = tx.get("to", "")
                if to_addr:
                    message += f"\n• To: <code>{to_addr}</code>"
                break
        else:
            current_price = prices.get(alert.symbol)
            if current_price is None:
                continue

            if alert.alert_type == "price_above" and current_price >= alert.target_value:
                triggered = True
                message = (
                    f"📈 <b>{alert.symbol} Alert Triggered!</b>\n\n"
                    f"Price has gone <b>above</b> your target value of <code>{alert.target_value:,.4f}</code>.\n"
                    f"Current Price: <b>{current_price:,.4f}</b>"
                )
            elif alert.alert_type == "price_below" and current_price <= alert.target_value:
                triggered = True
                message = (
                    f"📉 <b>{alert.symbol} Alert Triggered!</b>\n\n"
                    f"Price has gone <b>below</b> your target value of <code>{alert.target_value:,.4f}</code>.\n"
                    f"Current Price: <b>{current_price:,.4f}</b>"
                )
            elif alert.alert_type == "move_percent":
                stmt_port = select(Portfolio).where(Portfolio.user_id == alert.user_id, Portfolio.symbol == alert.symbol)
                res_port = await session.execute(stmt_port)
                portfolio_item = res_port.scalar_one_or_none()

                if portfolio_item and portfolio_item.entry_price > 0:
                    pct_change = ((current_price - portfolio_item.entry_price) / portfolio_item.entry_price) * 100
                    threshold = alert.target_value

                    if abs(pct_change) >= threshold:
                        triggered = True
                        direction_word = "up" if pct_change > 0 else "down"
                        message = (
                            f"📊 <b>{alert.symbol} Move Alert Triggered!</b>\n\n"
                            f"Price moved <b>{direction_word} {abs(pct_change):.2f}%</b> from entry, exceeding your {threshold}% threshold.\n"
                            f"Current Price: <b>{current_price:,.4f}</b>"
                        )

        if triggered:
            stmt_user = select(User).where(User.id == alert.user_id)
            res_user = await session.execute(stmt_user)
            user = res_user.scalar_one_or_none()

            if user:
                try:
                    if alert.alert_type in ("price_above", "price_below", "move_percent"):
                        stmt_port = select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.symbol == alert.symbol)
                        res_port = await session.execute(stmt_port)
                        portfolio_item = res_port.scalar_one_or_none()

                        if portfolio_item and portfolio_item.entry_price > 0:
                            change = ((current_price - portfolio_item.entry_price) / portfolio_item.entry_price) * 100
                            message += f"\n\nEntry Price: <code>{portfolio_item.entry_price:,.4f}</code>\n"
                            message += f"Current Change: <b>{change:+.2f}%</b>"

                    await bot.send_message(chat_id=user.telegram_id, text=message, parse_mode="HTML")
                    await db_service.add_notification(session, user.id, message)
                    alert.is_active = False
                    logger.info(f"Alert ID {alert.id} triggered for User ID {user.id} ({alert.symbol}).")
                except Exception as e:
                    logger.error(f"Failed to process triggered alert ID {alert.id}: {e}")

    await session.commit()
