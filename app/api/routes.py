"""Read-model API for the Telegram Mini App (Phase 2E).

Exposes the user's data (portfolio, wallets, opportunities, alerts, news,
notifications) as one `/api/dashboard` payload to keep the frontend thin.
The Mini App sits on top of data the bot already collects — no writes here yet.
"""

import html
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.models import Notification, User
from app.database.session import async_session_maker
from app.services import db_service
from app.api import security
from app.services.news.news_service import SENTIMENT_EMOJI, get_latest_analysis
from app.services.opportunity import engine as opportunity_engine
from app.services.prices.price_fetcher import price_fetcher
from app.services.wallet import wallet_service

logger = logging.getLogger("crypto_watchman.api")
router = APIRouter()


async def get_session():
    async with async_session_maker() as session:
        yield session


async def require_telegram_id(
    x_app_token: str | None = Header(default=None, alias="X-App-Token"),
) -> int:
    telegram_id = security.parse_token(x_app_token or "")
    if telegram_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired app token")
    return telegram_id


class AuthPayload(BaseModel):
    init_data: str


@router.post("/auth")
async def auth(payload: AuthPayload):
    """Exchange Telegram WebApp initData for a signed app token."""
    data = security.validate_init_data(
        payload.init_data, settings.TELEGRAM_BOT_TOKEN
    )
    if not data:
        raise HTTPException(status_code=401, detail="Invalid Telegram WebApp initData")
    user_info = data["user"]
    telegram_id = int(user_info["id"])
    async with async_session_maker() as session:
        user = await db_service.get_or_create_user(
            session=session,
            telegram_id=telegram_id,
            username=user_info.get("username"),
        )
    return {
        "token": security.issue_token(telegram_id),
        "user": {
            "id": user.id,
            "telegram_id": telegram_id,
            "plan": user.plan,
            "username": user.username,
        },
    }


def _serialize_wallet(wallet):
    balances = sorted(
        wallet.balances, key=lambda b: b.value_usd, reverse=True
    )[:20]
    return {
        "id": wallet.id,
        "network": wallet.network,
        "label": wallet.label,
        "address": wallet.address,
        "address_short": wallet_service.shorten_address(wallet.address),
        "total_value_usd": round(wallet.total_value_usd or 0.0, 2),
        "balances": [
            {
                "symbol": b.symbol,
                "quantity": b.quantity,
                "value_usd": round(b.value_usd or 0.0, 2),
            }
            for b in balances
        ],
    }


def _serialize_alert(alert):
    label = {
        "price_above": f"Price above {alert.target_value:,.4f}",
        "price_below": f"Price below {alert.target_value:,.4f}",
        "move_percent": f"±{alert.target_value}% from entry",
        "volume_above": f"Volume > ${alert.target_value:,.0f}",
        "volatility": f"Volatility > {alert.target_value}x",
        "sentiment_spike": f"Mentions > {alert.target_value:.0f}/24h",
        "whale_alert": f"Tx ≥ {alert.target_value:.0f} {alert.symbol}",
    }.get(alert.alert_type, f"{alert.alert_type} {alert.target_value:,.4f}")
    return {
        "id": alert.id,
        "symbol": alert.symbol,
        "alert_type": alert.alert_type,
        "target_value": alert.target_value,
        "description": label,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


def _serialize_analysis(analysis):
    return {
        "symbol": analysis.symbol,
        "sentiment": analysis.overall_sentiment or "NEUTRAL",
        "sentiment_emoji": SENTIMENT_EMOJI.get(analysis.overall_sentiment, "⚪"),
        "impact_level": analysis.impact_level or "UNKNOWN",
        "impact_score": analysis.impact_score or 0,
        "direction": analysis.potential_direction or "NEUTRAL",
        "confidence": round(analysis.confidence or 0),
        "event_categories": analysis.event_categories,
        "reason": (html.unescape(analysis.reason) if analysis.reason else None),
        "article_count": analysis.article_count or 0,
        "analyzed_at": analysis.analyzed_at.isoformat() if analysis.analyzed_at else None,
    }


@router.get("/dashboard")
async def dashboard(
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)

    portfolio_rows = await db_service.get_portfolio(session, user.id)
    alerts_rows = await db_service.get_user_alerts(session, user.id)
    wallets_rows = await wallet_service.get_user_wallets(session, user.id)
    scores = await opportunity_engine.get_latest_scores(session)
    score_by_symbol = {s["symbol"]: s for s in scores}

    notif_rows = (
        await session.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.sent_at.desc())
            .limit(20)
        )
    ).scalars().all()

    prices: dict[str, float] = {}
    for p in portfolio_rows:
        try:
            price = await price_fetcher.get_price(p.symbol)
            if price:
                prices[p.symbol] = price
        except Exception as e:
            logger.warning(f"Mini App price fetch failed for {p.symbol}: {e}")

    portfolio: list[dict] = []
    pnl_list: list[float] = []
    for p in portfolio_rows:
        price = prices.get(p.symbol)
        change_pct = None
        if price and p.entry_price:
            change_pct = round(((price - p.entry_price) / p.entry_price) * 100, 2)
            pnl_list.append(change_pct)
        portfolio.append(
            {
                "symbol": p.symbol,
                "entry_price": round(p.entry_price, 6),
                "price": round(price, 6) if price else None,
                "change_pct": change_pct,
            }
        )
    portfolio.sort(key=lambda a: a["change_pct"] if a["change_pct"] is not None else -9999, reverse=True)

    wallet_total = round(sum(w.total_value_usd or 0 for w in wallets_rows), 2)

    opportunities = []
    for p in portfolio_rows:
        s = score_by_symbol.get(p.symbol)
        if s:
            components = [
                {
                    "source": c.get("source"),
                    "score": round(float(c.get("score", 0)), 1),
                }
                for c in s.get("components", [])
                if abs(float(c.get("score", 0))) > 0.05
            ]
            opportunities.append(
                {
                    "symbol": s["symbol"],
                    "total_score": s["total_score"],
                    "direction": s["direction"],
                    "priority": s["priority"],
                    "components": components[:8],
                    "computed_at": s["computed_at"].isoformat() if s["computed_at"] else None,
                }
            )
    opportunities.sort(key=lambda o: o["total_score"], reverse=True)

    news = []
    for p in portfolio_rows:
        analysis = await get_latest_analysis(session, p.symbol)
        if analysis:
            news.append(_serialize_analysis(analysis))
    news.sort(key=lambda n: n.get("analyzed_at") or "", reverse=True)

    return {
        "user": {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "plan": user.plan,
            "username": user.username,
        },
        "limits": {"max_assets": 5 if user.plan == "free" else None},
        "portfolio": portfolio,
        "portfolio_pnl_pct": (
            round(sum(pnl_list) / len(pnl_list), 2) if pnl_list else None
        ),
        "wallets": [_serialize_wallet(w) for w in wallets_rows],
        "wallet_total_usd": wallet_total,
        "opportunities": opportunities,
        "alerts": [_serialize_alert(a) for a in alerts_rows],
        "notifications": [
            {"message": n.message, "sent_at": n.sent_at.isoformat() if n.sent_at else None}
            for n in notif_rows
        ],
        "news": news,
    }