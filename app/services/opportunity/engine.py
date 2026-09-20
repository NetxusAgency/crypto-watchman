import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OpportunityScore, OpportunitySignal
from app.services import db_service
from app.services.market_digest.digest_service import call_llm
from app.services.opportunity.signals import collect_signals

logger = logging.getLogger("crypto_watchman.opportunity_engine")

PRIORITY_ICONS = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}
SOURCE_ICONS = {
    "news": "📰", "volume": "📊", "momentum": "📈",
    "social": "📢", "listing": "🚀", "whale": "🐋", "liquidity": "💧",
}

_ALERTABLE = {"HIGH", "CRITICAL"}


def priority_for(score: float) -> str:
    abs_score = abs(score)
    if abs_score >= 60:
        return "CRITICAL"
    if abs_score >= 40:
        return "HIGH"
    if abs_score >= 20:
        return "MEDIUM"
    return "LOW"


def is_elevated(old_priority: str | None, new_priority: str) -> bool:
    """True when a score newly reached HIGH/CRITICAL, or upgraded HIGH -> CRITICAL."""
    old = old_priority or "LOW"
    if new_priority in _ALERTABLE and old not in _ALERTABLE:
        return True
    return new_priority == "CRITICAL" and old == "HIGH"


def total_from_components(components: list) -> tuple[float, int]:
    """Signed weighted total in [-100, 100]. `components` items expose `.score`."""
    total = sum(c.score for c in components)
    total = max(-100.0, min(100.0, round(total, 2)))
    direction = 1 if total > 0 else (-1 if total < 0 else 0)
    return total, direction


async def compute_opportunity(session: AsyncSession, symbol: str) -> dict:
    """Gather signals, compute the weighted score and return a result dict."""
    symbol = symbol.upper().strip()
    components = await collect_signals(session, symbol)
    total, direction = total_from_components(components)
    return {
        "symbol": symbol,
        "total_score": total,
        "direction": direction,
        "priority": priority_for(total),
        "components": [c for c in components if c.score != 0],
        "evidence": "\n".join(
            f"{c.source}: {c.evidence}" for c in components if c.evidence
        ),
    }


async def persist_opportunity(session: AsyncSession, result: dict, signals: list) -> None:
    """Replace the stored score for a symbol with a fresh computation."""
    symbol = result["symbol"]
    await session.execute(
        delete(OpportunityScore).where(OpportunityScore.symbol == symbol)
    )
    await session.execute(
        delete(OpportunitySignal).where(OpportunitySignal.symbol == symbol)
    )
    session.add(
        OpportunityScore(
            symbol=symbol,
            total_score=result["total_score"],
            direction=result["direction"],
            priority=result["priority"],
            components=json.dumps(
                [
                    {
                        "source": c.source,
                        "weight": c.weight,
                        "direction": c.direction,
                        "strength": c.strength,
                        "score": c.score,
                        "evidence": c.evidence,
                    }
                    for c in signals
                ],
                default=str,
            ),
            evidence=result["evidence"],
        )
    )
    for c in signals:
        session.add(
            OpportunitySignal(
                symbol=symbol,
                source=c.source,
                weight=c.weight,
                direction=c.direction,
                strength=c.strength,
                score=c.score,
                evidence=c.evidence,
            )
        )
    await session.commit()


async def refresh_event(session: AsyncSession, symbol: str) -> dict:
    result = await compute_opportunity(session, symbol)
    await persist_opportunity(session, result, result["components"])
    return result


async def refresh_opportunities(session: AsyncSession, symbols: list[str] | None = None) -> list[dict]:
    """Background scan: compute + store scores for the given (or all portfolio) symbols.

    Returns the opportunity dicts that newly reached HIGH/CRITICAL, so the caller
    can push auto-alerts to users (never repeats while a level stays elevated).
    """
    if symbols is None:
        symbols = await db_service.all_portfolio_symbols(session)
    elevated: list[dict] = []
    for raw_symbol in symbols:
        symbol = raw_symbol.upper().strip()
        try:
            signals = await collect_signals(session, symbol)
            total, direction = total_from_components(signals)
            result = {
                "symbol": symbol,
                "total_score": total,
                "direction": direction,
                "priority": priority_for(total),
                "components": [c for c in signals if c.score != 0],
                "evidence": "\n".join(f"{c.source}: {c.evidence}" for c in signals if c.evidence),
            }
            old_priority = await _stored_priority(session, symbol)
            await persist_opportunity(session, result, signals)
            if is_elevated(old_priority, result["priority"]):
                result["components"] = [
                    {
                        "source": c.source,
                        "score": c.score,
                        "evidence": c.evidence,
                    }
                    for c in result["components"]
                ]
                elevated.append(result)
        except Exception as e:
            logger.warning(f"Opportunity scan failed for {symbol}: {e}")
    return elevated


async def _stored_priority(session: AsyncSession, symbol: str) -> str | None:
    row = (
        await session.execute(select(OpportunityScore.priority).where(OpportunityScore.symbol == symbol))
    ).scalar_one_or_none()
    return row or None


async def get_latest_scores(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(OpportunityScore).order_by(OpportunityScore.total_score.desc())
        )
    ).scalars().all()
    out = []
    for row in rows:
        try:
            components = json.loads(row.components or "[]")
        except json.JSONDecodeError:
            components = []
        out.append(
            {
                "symbol": row.symbol,
                "total_score": row.total_score,
                "direction": row.direction,
                "priority": row.priority,
                "computed_at": row.computed_at,
                "components": components,
                "evidence": row.evidence or "",
            }
        )
    return out


def format_opportunity(item: dict) -> str:
    total = item["total_score"]
    priority = item["priority"]
    arrow = "🟢" if item["direction"] > 0 else ("🔴" if item["direction"] < 0 else "⚪")
    bits = [
        f"{SOURCE_ICONS.get(c['source'], '•')} {c['source']} "
        f"{'+' if c['score'] >= 0 else ''}{round(c['score'], 1)}"
        for c in item["components"]
        if abs(c["score"]) > 0.05
    ]
    part = " · ".join(bits[:6]) if bits else "no catalysts"
    return (
        f"{arrow} <b>{item['symbol']}</b> {total:+.0f} · "
        f"{PRIORITY_ICONS.get(priority, '')} <b>{priority}</b>\n"
        f"&nbsp;&nbsp;&nbsp;&nbsp;{part}"
    )


def format_opportunities(items: list[dict], ai_note: str | None = None) -> str:
    if not items:
        return (
            "💡 <b>Opportunities</b>\n\n"
            "No opportunities scored yet. Add assets with /add_asset, then tap "
            "🔄 Recompute so the engine can scan for catalysts."
        )
    lines = ["💡 <b>Trade Opportunities</b>\n"]
    for item in items:
        lines.append(format_opportunity(item))
    if ai_note:
        lines.append(f"\n🤖 <i>{ai_note}</i>")
    lines.append("\n<i>Scores: news 20 · volume 20 · momentum 15 · social 15 · listing 15 · whale 10 · liquidity 5</i>")
    return "\n".join(lines)


async def ai_note_for(scores: list[dict]) -> str | None:
    """Short AI interpretation of the top opportunities (falls back to None)."""
    top = [s for s in scores if s["direction"] != 0][:3]
    if not top:
        return None
    lines = []
    for s in top:
        signals = ", ".join(f"{c['source']} {round(c['score'], 1)}" for c in s["components"])
        lines.append(f"{s['symbol']}: total {s['total_score']:+.0f} ({s['priority']}) — {signals}")
    prompt = (
        "Ranked crypto opportunity scores from a deterministic engine. "
        "Give one short, actionable 1-2 sentence interpretation of the strongest setup "
        "and note the biggest offsetting risk. Plain text, no markdown:\n\n"
        + "\n".join(lines)
    )
    text = await call_llm(prompt, max_tokens=200)
    if not text:
        return None
    return text.strip().replace("\n", " ")[:400]