"""Read-model API for the Telegram Mini App (Phase 2E).

Exposes the user's data (portfolio, wallets, opportunities, alerts, news,
notifications) as one `/api/dashboard` payload to keep the frontend thin.
The Mini App sits on top of data the bot already collects — no writes here yet.
"""

import html
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
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
    init_data: str = ""
    widget: dict | None = None
    dev_token: str = ""


class BrokerConnectionPayload(BaseModel):
    label: str = ""
    platform: str = "ctrader"
    account_id: str = ""
    access_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    # "demo" (simulated funds) or "live" (real money). Live needs the global
    # kill-switch; demo is safe to arm without it.
    mode: str = "demo"
    is_live: bool = False


class ArmConnectionPayload(BaseModel):
    is_live: bool = True


class LiveExecutePayload(BaseModel):
    symbol: str
    strategy_key: str = ""
    strategy_name: str = ""
    connection_id: int | None = None
    dry_run: bool = True


class OAuthStartPayload(BaseModel):
    # The Open API application credentials from id.ctrader.com. Never echoed
    # back to the client after the authorize URL is built.
    client_id: str
    client_secret: str
    mode: str = "demo"
    label: str = ""


def _scalar_masked_connection(c) -> dict:
    from app.services.broker import mask_secret

    return {
        "id": c.id,
        "label": c.label,
        "platform": c.platform,
        "account_id": c.account_id,
        "mode": (c.mode or "demo"),
        "is_live": bool(c.is_live),
        "is_active": bool(c.is_active),
        "live_enabled_global": bool(settings.LIVE_TRADING_ENABLED),
        "has_token": bool(c.access_token_enc),
        "access_token_masked": mask_secret(c.access_token_enc),
        "has_refresh_token": bool(getattr(c, "refresh_token_enc", "")),
        "token_expires_at": (
            c.token_expires_at.isoformat() if getattr(c, "token_expires_at", None) else None
        ),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/meta")
async def meta():
    """Public bootstrap data used by the Mini App frontend."""
    token = settings.TELEGRAM_BOT_TOKEN or ""
    bot_id = token.split(":")[0] if token and ":" in token and token != "placeholder_token" else ""
    return {
        "service": "watchman-mini-app",
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "bot_id": bot_id,
        "public_base_url": settings.PUBLIC_BASE_URL,
        "dev_auth_available": bool(settings.MINI_APP_DEV_TOKEN) and bool(settings.ADMIN_TELEGRAM_ID),
    }


@router.post("/auth")
async def auth(payload: AuthPayload):
    """Exchange Telegram WebApp initData, a Login Widget payload, or a dev token
    for a signed app token."""
    telegram_id = None
    username = None

    if payload.init_data:
        data = security.validate_init_data(
            payload.init_data, settings.TELEGRAM_BOT_TOKEN
        )
        if data:
            user_info = data["user"]
            telegram_id = int(user_info["id"])
            username = user_info.get("username")

    if telegram_id is None and payload.widget:
        widget_user = security.validate_widget_fields(
            payload.widget, settings.TELEGRAM_BOT_TOKEN
        )
        if widget_user:
            telegram_id = widget_user["id"]
            username = widget_user.get("username")

    if telegram_id is None:
        if settings.ADMIN_TELEGRAM_ID and security.is_valid_dev_token(payload.dev_token):
            telegram_id = settings.ADMIN_TELEGRAM_ID
            username = "dev"

    if telegram_id is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram WebApp initData")

    logger.info("Mini App auth OK via %r for tg %s",
                "initData" if payload.init_data else ("widget" if payload.widget else "dev_token"),
                telegram_id)
    async with async_session_maker() as session:
        user = await db_service.get_or_create_user(
            session=session,
            telegram_id=telegram_id,
            username=username,
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


@router.get("/auth/return", include_in_schema=False)
async def auth_return(request: Request, session: AsyncSession = Depends(get_session)):
    """Full-page Telegram Login Widget fallback.

    `oauth.telegram.org` redirects the browser back to `return_to` (this
    endpoint) with the signed fields in the query string; we validate
    server-side and bounce the browser back to the Mini App with a token in
    the URL fragment (never in logs/server).
    """
    fields = {k: v for k, v in request.query_params.items()}

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    user_info = security.validate_widget_fields(fields, settings.TELEGRAM_BOT_TOKEN)
    if not user_info:
        logger.warning("Mini App return-auth rejected: bad widget signature")
        return RedirectResponse(f"{base}/app/#app_error=1", status_code=302)

    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=user_info["id"],
        username=user_info.get("username"),
    )
    token = security.issue_token(user_info["id"])
    logger.info("Mini App redirect-auth OK for tg %s (plan=%s)", user_info["id"], user.plan)
    return RedirectResponse(f"{base}/app/#app_token={token}", status_code=302)


@router.post("/auth/code")
async def mini_app_login_code():
    """Mint a one-time code the user sends to the bot as `/login CODE`."""
    from app.services import login_codes

    code = await login_codes.create_login_code()
    logger.info("Issued Mini App login code %s", code)
    return {"code": code}


@router.get("/auth/poll")
async def mini_app_login_poll(code: str, session: AsyncSession = Depends(get_session)):
    """Polled by the Mini App until the /login code is approved in the bot."""
    from app.services import login_codes

    telegram_id = await login_codes.check_login_code(code)
    if telegram_id is None:
        return {"status": "pending"}

    user = await db_service.get_or_create_user(
        session=session, telegram_id=telegram_id
    )
    logger.info("Mini App code-login OK for tg %s (plan=%s)", telegram_id, user.plan)
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


@router.get("/trading/live")
async def trading_live_overview(
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    """Live-execution read-model: global kill-switch, saved connections (masked),
    the user's saved strategies + presets, and recent live trades."""
    from app.services.trading.strategy_engine import preset_specs

    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)
    connections = await db_service.get_user_broker_connections(session, user.id)

    live_trades = await db_service.get_user_live_trades(session, user.id, limit=20)

    saved_strategies = await db_service.get_user_strategies(session, user.id, limit=20)

    return {
        "live_enabled_global": bool(settings.LIVE_TRADING_ENABLED),
        "connections": [_scalar_masked_connection(c) for c in connections],
        "armed_count": sum(1 for c in connections if c.is_live and c.is_active),
        "strategies": [
            {"key": key, "name": spec.name, "description": spec.description, "direction": spec.direction,
             "timeframes": spec.timeframes}
            for key, spec in preset_specs().items()
        ],
        "saved_strategies": [
            {"key": s.key, "name": s.name, "description": s.description,
             "timeframes": [tf.strip() for tf in (s.timeframes or "").split(",") if tf.strip()]}
            for s in saved_strategies
        ],
        "recent_live_trades": [
            {
                "id": t.id,
                "plan_id": t.plan_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "strategy_key": t.strategy_key,
                "strategy_name": t.strategy_name,
                "status": t.status,
                "dry_run": bool(t.dry_run),
                "reason": t.reason,
                "entry_price": round(t.entry_price, 6),
                "quantity": t.quantity,
                "order_id": t.order_id,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            }
            for t in live_trades
        ],
    }


@router.get("/trading/live/connections")
async def trading_live_connections(
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)
    connections = await db_service.get_user_broker_connections(session, user.id)
    return {
        "connections": [_scalar_masked_connection(c) for c in connections],
        "live_enabled_global": bool(settings.LIVE_TRADING_ENABLED),
    }


@router.post("/trading/live/connections")
async def save_trading_connection(
    payload: BrokerConnectionPayload,
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    """Save or update a broker connection. Secrets are encrypted at rest with
    Fernet (SECRET_KEY-derived); the API never returns plaintext secrets."""
    from app.services.broker import encrypt_secret

    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)

    existing = await db_service.get_user_broker_connections(session, user.id)
    match = next(
        (c for c in existing if (payload.account_id and c.account_id == payload.account_id) or False),
        None,
    )

    updates = {
        "label": payload.label,
        "platform": payload.platform or "ctrader",
        "account_id": payload.account_id,
        "is_live": payload.is_live,
    }
    # Only overwrite non-empty secret fields; masked/blank ones leave the stored value.
    if payload.access_token:
        updates["access_token_enc"] = encrypt_secret(payload.access_token)
    if payload.client_id:
        updates["client_id_enc"] = encrypt_secret(payload.client_id)
    if payload.client_secret:
        updates["client_secret_enc"] = encrypt_secret(payload.client_secret)

    connection = await db_service.save_broker_connection(
        session,
        user.id,
        connection_id=match.id if match else None,
        mode=payload.mode or "demo",
        **updates,
    )
    return {
        "ok": True,
        "connection": _scalar_masked_connection(connection),
    }


@router.post("/trading/live/connections/{connection_id}/arm")
async def arm_trading_connection(
    connection_id: int,
    payload: ArmConnectionPayload,
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    """Arm (is_live=True) or disarm a connection. Arming is the explicit per-account
    consent gate for live execution and is only ever safe with one armed account."""
    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)

    # Never allow more than one armed connection per user (safety-first).
    if payload.is_live:
        connections = await db_service.get_user_broker_connections(session, user.id)
        for other in connections:
            if other.id != connection_id and other.is_live:
                await db_service.set_broker_connection_live(
                    session, user.id, other.id, is_live=False
                )

    ok = await db_service.set_broker_connection_live(
        session, user.id, connection_id, is_live=payload.is_live
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Broker connection not found")
    return {"ok": True, "connection_id": connection_id, "is_live": bool(payload.is_live)}


@router.delete("/trading/live/connections/{connection_id}")
async def delete_trading_connection(
    connection_id: int,
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)
    ok = await db_service.delete_broker_connection(session, user.id, connection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Broker connection not found")
    return {"ok": True, "connection_id": connection_id}


@router.post("/trading/live/ctid/start")
async def start_ctid_authorization(
    payload: OAuthStartPayload,
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    """Start a cTrader Open API (cTID) OAuth authorization.

    The Mini App sends its Open API application Client ID/Secret + the chosen
    account mode; we build the cTrader authorize URL (with a one-time state) and
    hand it back so the browser can do the grant. Client/secret are never
    returned; the callback stores them encrypted on the new BrokerConnection.
    """
    from app.services.trading.ctid_oauth import CTraderOAuthError, start_authorization

    if not payload.client_id or not payload.client_secret:
        raise HTTPException(
            status_code=400,
            detail="Open API Client ID and Client Secret are required. Create an "
                   "application at id.ctrader.com -> Open API first.",
        )
    try:
        authorize_url = start_authorization(
            telegram_id=telegram_id,
            client_id=payload.client_id.strip(),
            mode=(payload.mode or "demo").lower(),
            label=payload.label.strip(),
        )
    except CTraderOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "authorize_url": authorize_url,
        "redirect_uri": settings.CTRADER_OAUTH_REDIRECT_URI,
    }


@router.get("/trading/live/ctid/callback")
async def ctid_oauth_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """OAuth redirect target hit by the browser after the user grants access.

    Exchanges the `code` for tokens, discovers the account id, saves an encrypted
    BrokerConnection row, then bounces the browser back to the Mini App with a
    `?ctid=ok` (or `?ctid=error`) so app.js can refresh.
    """
    from app.services.trading.ctid_oauth import (
        CTraderOAuthError,
        consume_pending_state,
        exchange_code,
    )
    from app.services.broker import decrypt_secret, encrypt_secret
    from app.execution.ctrader import CTraderClient

    params = dict(request.query_params)
    redirect_base = settings.PUBLIC_BASE_URL.rstrip("/")
    if params.get("error"):
        return RedirectResponse(f"{redirect_base}/app?ctid=error", status_code=302)

    state = params.get("state", "")
    code = params.get("code", "")
    try:
        pending = consume_pending_state(state)
        client_id = decrypt_secret(pending.client_id_enc)
        client_secret = decrypt_secret(pending.client_secret_enc)
        if not client_id or not client_secret:
            raise CTraderOAuthError("Missing application credentials in pending state.")
    except CTraderOAuthError as exc:
        return RedirectResponse(f"{redirect_base}/app?ctid=error&reason={exc}", status_code=302)

    try:
        body = await exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
        )
        access_token = body.get("access_token", "")
        refresh_token = body.get("refresh_token", "")
        expires_at = _oauth_expires_at(body)
    except CTraderOAuthError as exc:
        logger.warning("cTID code exchange failed: %s", exc)
        return RedirectResponse(f"{redirect_base}/app?ctid=error&reason=exchange-failed", status_code=302)

    client = CTraderClient()
    account_id = ""
    try:
        accounts = await client.get_accounts(access_token)
        if accounts:
            account_id = str(accounts[0].account_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cTID callback: could not read accounts: %s", exc)

    user = await db_service.get_or_create_user(session, telegram_id=pending.telegram_id)
    connection = await db_service.save_broker_connection(
        session,
        user.id,
        label=pending.label or f"cTrader {account_id or 'account'}",
        platform="ctrader",
        account_id=account_id or f"ctid-{pending.telegram_id}-{state[:8]}",
        access_token_enc=encrypt_secret(access_token),
        refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else "",
        client_id_enc=encrypt_secret(client_id),
        client_secret_enc=encrypt_secret(client_secret),
        token_expires_at=expires_at,
        is_live=False,
        mode=pending.mode,
    )
    # Refresh lazily the first time if we could not parse the expiry.
    logger.info(
        "cTID OAuth connected account %s (mode=%s) for user %s",
        account_id or "unknown", pending.mode, pending.telegram_id,
    )
    return RedirectResponse(f"{redirect_base}/app?ctid=ok", status_code=302)


def _oauth_expires_at(body: dict):
    from app.services.trading.ctid_oauth import expires_in_to_utc

    expires_in = body.get("expires_in")
    if expires_in is None:
        expires_in = 3600  # conservative default when the grant omits it
    try:
        return expires_in_to_utc(expires_in)
    except (TypeError, ValueError):
        return None


@router.post("/trading/live/execute")
async def execute_live_trade(
    payload: LiveExecutePayload,
    session: AsyncSession = Depends(get_session),
    telegram_id: int = Depends(require_telegram_id),
):
    """Dry-run (default) or a live trade proposal.

    `dry_run=True` exercises the entire pipeline (broker truth + setup + risk +
    sizing) but never touches the real broker.

    `dry_run=False` validates the same pipeline and persists a PENDING_CONFIRM
    trade. It does NOT place an order — a Telegram message with ✅/❌ is sent to
    the user, and the order only fires when they confirm. For `mode == "live"`
    connections this additionally requires settings.LIVE_TRADING_ENABLED; demo
    accounts only need the connection to be armed.
    """
    from app.services.trading.live import LiveExecutionService
    from app.services.assistant.klines import kline_fetcher

    user = await db_service.get_or_create_user(session, telegram_id=telegram_id)
    symbol = payload.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    dry_run = bool(payload.dry_run)

    connection = None
    if payload.connection_id:
        connections = await db_service.get_user_broker_connections(session, user.id)
        connection = next(
            (c for c in connections if c.id == payload.connection_id), None
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Broker connection not found")
    else:
        connections = await db_service.get_user_broker_connections(session, user.id)
        candidates = [c for c in connections if c.is_active]
        if dry_run and candidates:
            connection = candidates[0]
        else:
            armed = [c for c in candidates if c.is_live]
            connection = armed[0] if armed else None
    if connection is None:
        raise HTTPException(
            status_code=400,
            detail="No armed active broker connection. Save and arm a connection first.",
        )

    strategy_key = payload.strategy_key or "momentum"
    service = LiveExecutionService()
    result = await service.propose_trade(
        session,
        user=user,
        connection=connection,
        symbol=symbol,
        strategy_key=strategy_key,
        strategy_name=payload.strategy_name or strategy_key,
        dry_run=dry_run,
        kline_source=kline_fetcher,
        notify=not dry_run,
    )
    return {
        "allowed": result.allowed,
        "reason": result.reason,
        "trade": result.trade.to_dict() if result.trade else None,
        "plan": result.plan_dict,
        "risk": result.risk_dict,
        "dry_run": dry_run,
        "awaiting_confirmation": result.awaiting_confirmation,
        "confirmation_text": result.confirmation_text,
    }