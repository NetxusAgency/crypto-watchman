import html
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TradeAnalysis
from app.services.assistant.analyzer import TradeSetup, analyze_market_setup
from app.services.assistant.indicators import compute_all_indicators
from app.services.assistant.klines import kline_fetcher
from app.services.assistant.strategies import get_strategy_definition

logger = logging.getLogger("crypto_watchman.assistant_service")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ttl_for_timeframe(tf: str) -> timedelta:
    """Cache TTL matches the candle resolution."""
    tf_lower = tf.lower()
    if tf_lower == "15m":
        return timedelta(minutes=10)
    if tf_lower == "1h":
        return timedelta(minutes=30)
    if tf_lower == "4h":
        return timedelta(hours=2)
    return timedelta(hours=6)


async def get_or_create_trade_setup(
    session: AsyncSession,
    symbol: str,
    timeframe: str = "1h",
    strategy_key: str = "general",
    user_id: int | None = None,
    force_refresh: bool = False,
) -> TradeSetup:
    """
    Fetch cached trade setup or run live technical + AI pipeline.
    Ensures responsiveness and limits redundant LLM calls.
    """
    sym_clean = symbol.upper().strip()
    tf_clean = timeframe.lower().strip()
    now = _utcnow()

    # 1. Check existing valid cache
    if not force_refresh:
        stmt = (
            select(TradeAnalysis)
            .where(
                TradeAnalysis.symbol == sym_clean,
                TradeAnalysis.timeframe == tf_clean,
                TradeAnalysis.strategy_key == strategy_key,
                TradeAnalysis.expires_at > now,
            )
            .order_by(TradeAnalysis.created_at.desc())
        )
        res = await session.execute(stmt)
        cached = res.scalar_one_or_none()
        if cached:
            try:
                ind_data = json.loads(cached.indicators_snapshot) if cached.indicators_snapshot else {}
                reasons = json.loads(cached.reasoning) if cached.reasoning.startswith("[") else [cached.reasoning]
                strat_def = get_strategy_definition(cached.strategy_key)
                return TradeSetup(
                    symbol=cached.symbol,
                    timeframe=cached.timeframe,
                    strategy_key=cached.strategy_key,
                    strategy_name=strat_def.name,
                    bias=cached.bias,
                    current_price=cached.current_price,
                    entry_min=cached.entry_min or cached.current_price,
                    entry_max=cached.entry_max or cached.current_price,
                    stop_loss=cached.stop_loss or (cached.current_price * 0.95),
                    take_profit_1=cached.take_profit_1 or (cached.current_price * 1.05),
                    take_profit_2=cached.take_profit_2 or (cached.current_price * 1.10),
                    risk_reward=cached.risk_reward or 1.5,
                    confidence=cached.confidence,
                    indicators_summary=ind_data,
                    reasoning=reasons,
                    invalidation=cached.invalidation,
                )
            except Exception as e:
                logger.warning(f"Error reading cached TradeAnalysis: {e}")

    # 2. Fetch candles and compute indicators
    candles = await kline_fetcher.fetch_candles(sym_clean, tf_clean, limit=100)
    snapshot = compute_all_indicators(sym_clean, tf_clean, candles)
    strategy = get_strategy_definition(strategy_key)

    # 3. Run AI analysis
    setup = await analyze_market_setup(snapshot, strategy, candles)

    # 4. Cache in DB
    try:
        ttl = _ttl_for_timeframe(tf_clean)
        analysis_record = TradeAnalysis(
            user_id=user_id,
            symbol=sym_clean,
            timeframe=tf_clean,
            strategy_key=strategy.key,
            bias=setup.bias,
            current_price=setup.current_price,
            entry_min=setup.entry_min,
            entry_max=setup.entry_max,
            stop_loss=setup.stop_loss,
            take_profit_1=setup.take_profit_1,
            take_profit_2=setup.take_profit_2,
            risk_reward=setup.risk_reward,
            confidence=setup.confidence,
            indicators_snapshot=json.dumps(setup.indicators_summary),
            reasoning=json.dumps(setup.reasoning),
            invalidation=setup.invalidation,
            created_at=now,
            expires_at=now + ttl,
        )
        session.add(analysis_record)
        await session.commit()
    except Exception as e:
        logger.warning(f"Failed to persist TradeAnalysis to DB: {e}")

    return setup


def format_trade_setup_message(setup: TradeSetup) -> str:
    """Format TradeSetup into a clean, professional Telegram HTML card."""
    bias_emoji = "🟢" if setup.bias == "BULLISH" else ("🔴" if setup.bias == "BEARISH" else "⚪")
    bias_label = f"{bias_emoji} <b>{html.escape(setup.bias.title())}</b> ({setup.confidence}% confidence)"

    p = setup.current_price
    sl_pct = ((setup.stop_loss - p) / p) * 100 if p else 0.0
    tp1_pct = ((setup.take_profit_1 - p) / p) * 100 if p else 0.0
    tp2_pct = ((setup.take_profit_2 - p) / p) * 100 if p else 0.0

    ind = setup.indicators_summary
    rsi = ind.get("rsi_14")
    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    ema_str = f"${ema20:,.2f} / ${ema50:,.2f}" if (ema20 and ema50) else "N/A"
    atr = ind.get("atr_14")
    atr_str = f"${atr:,.2f}" if atr is not None else "N/A"

    reason_lines = "\n".join([f"• {html.escape(str(r))}" for r in setup.reasoning])

    return (
        f"🎯 <b>AI Trading Assistant — {html.escape(setup.symbol)} ({html.escape(setup.timeframe.upper())})</b>\n\n"
        f"<b>Strategy:</b> {html.escape(setup.strategy_name)}\n"
        f"<b>Market Bias:</b> {bias_label}\n\n"
        f"💰 <b>Current Price:</b> <code>${p:,.4f}</code>\n"
        f"📍 <b>Entry Zone:</b> <code>${setup.entry_min:,.4f} – ${setup.entry_max:,.4f}</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>${setup.stop_loss:,.4f}</code> ({sl_pct:+.2f}%)\n"
        f"🎯 <b>Take Profit 1:</b> <code>${setup.take_profit_1:,.4f}</code> ({tp1_pct:+.2f}%)\n"
        f"🚀 <b>Take Profit 2:</b> <code>${setup.take_profit_2:,.4f}</code> ({tp2_pct:+.2f}%)\n"
        f"⚖️ <b>Risk/Reward Ratio:</b> <b>1 : {setup.risk_reward:.1f}</b>\n\n"
        f"📊 <b>Technical Confluence:</b>\n"
        f"• <b>RSI (14):</b> {rsi_str}\n"
        f"• <b>EMA (20/50):</b> {ema_str}\n"
        f"• <b>ATR (14):</b> {atr_str}\n\n"
        f"💡 <b>Setup Rationale:</b>\n"
        f"{reason_lines}\n\n"
        f"⚠️ <b>Invalidation Rule:</b>\n"
        f"{html.escape(str(setup.invalidation))}\n\n"
        f"<i>⚠️ Strictly for analysis and educational reference. Never risk more than you can afford to lose.</i>"
    )
