from dataclasses import dataclass
import json
import logging
import re
from typing import Sequence
from sqlalchemy import select, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import TradingStrategy
from app.services.market_digest.digest_service import call_llm

logger = logging.getLogger("crypto_watchman.strategies")


@dataclass
class StrategyDefinition:
    key: str
    name: str
    description: str
    timeframes: str
    indicators: str
    rules: str


PRESET_STRATEGIES: list[StrategyDefinition] = [
    StrategyDefinition(
        key="trend_pullback",
        name="Trend Following / Pullback",
        description="Identifies continuation entries when price pulls back to key moving averages in an established trend.",
        timeframes="15m,1h,4h,1d",
        indicators="EMA20,EMA50,RSI14,ATR14",
        rules=(
            "Trend alignment requires EMA 20 > EMA 50 for longs, or EMA 20 < EMA 50 for shorts. "
            "Enter on pullbacks to the dynamic EMA 20/50 support/resistance zone when RSI normalizes. "
            "Stop loss is positioned beyond the recent swing low/high or 1.5x ATR. "
            "Take profit targets are set at recent swing highs/lows and next resistance/support levels."
        ),
    ),
    StrategyDefinition(
        key="breakout",
        name="Breakout & Momentum",
        description="Captures explosive moves when price breaches established horizontal support or resistance levels.",
        timeframes="15m,1h,4h,1d",
        indicators="Resistance,Support,MACD,ATR14",
        rules=(
            "Identify consolidation ranges where price tests key resistance (bullish) or support (bearish). "
            "Entry triggers on confirmed break or retest of the broken level with MACD histogram confirmation. "
            "Stop loss placed safely inside the broken consolidation zone. "
            "Targets set at measured-move extensions (1.5x and 2.5x the consolidation height)."
        ),
    ),
    StrategyDefinition(
        key="mean_reversion",
        name="Mean Reversion / Counter-Trend",
        description="Exploits market extremes when price is stretched outside Bollinger Bands with divergent RSI.",
        timeframes="15m,1h,4h,1d",
        indicators="BollingerBands,RSI14,SMA20",
        rules=(
            "Triggers when RSI < 30 (oversold) and price touches/pierces Lower Bollinger Band for longs, "
            "or RSI > 70 (overbought) and price touches Upper Bollinger Band for shorts. "
            "Target 1 is the 20-period middle moving average; Target 2 is the opposite band. "
            "Stop loss placed strictly beyond the extreme candle wick."
        ),
    ),
    StrategyDefinition(
        key="general",
        name="Full Technical Diagnostic",
        description="Comprehensive technical analysis synthesizing trend, momentum, support/resistance, and risk parameters.",
        timeframes="15m,1h,4h,1d",
        indicators="EMA,RSI,MACD,ATR,BollingerBands,Support,Resistance",
        rules=(
            "Synthesize all indicators (EMA 20/50, RSI, MACD histogram, ATR, Support & Resistance). "
            "Determine the dominant market structure and produce an actionable trade setup with clear "
            "Entry Zone, Stop Loss, TP1, TP2, and calculated Risk:Reward ratio."
        ),
    ),
]

STRATEGIES_MAP = {s.key: s for s in PRESET_STRATEGIES}


async def seed_preset_strategies(session: AsyncSession) -> int:
    """Ensure system preset strategies exist in the database."""
    created = 0
    for s in PRESET_STRATEGIES:
        stmt = select(TradingStrategy).where(
            TradingStrategy.key == s.key,
            TradingStrategy.is_system == True,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            strategy = TradingStrategy(
                user_id=None,
                key=s.key,
                name=s.name,
                description=s.description,
                timeframes=s.timeframes,
                indicators=s.indicators,
                rules=s.rules,
                is_system=True,
                version=1,
            )
            session.add(strategy)
            created += 1
    if created:
        await session.commit()
    return created


def get_strategy_definition(key: str) -> StrategyDefinition:
    """Retrieve strategy definition by key, falling back to general diagnostic."""
    return STRATEGIES_MAP.get(key, STRATEGIES_MAP["general"])


def is_custom_key(key: str) -> bool:
    """Return True if the key refers to a user-defined (non-system) strategy."""
    return bool(key) and key.startswith("custom_")


def strategy_definition_from_row(row: TradingStrategy) -> StrategyDefinition:
    """Build a StrategyDefinition from a TradingStrategy DB row."""
    return StrategyDefinition(
        key=row.key,
        name=row.name,
        description=row.description or "",
        timeframes=row.timeframes,
        indicators=row.indicators,
        rules=row.rules,
    )


async def get_strategy_definition_any(
    session: AsyncSession,
    key: str,
    user_id: int | None = None,
) -> StrategyDefinition:
    """Resolve a strategy definition from presets or (for custom keys) the DB.

    Falls back to the general diagnostic when nothing matches.
    """
    if key in STRATEGIES_MAP:
        return STRATEGIES_MAP[key]

    if is_custom_key(key) and session is not None:
        stmt = select(TradingStrategy).where(
            and_(
                TradingStrategy.key == key,
                or_(
                    TradingStrategy.user_id == user_id,
                    TradingStrategy.is_system == True,
                ),
            )
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            return strategy_definition_from_row(row)

    return STRATEGIES_MAP["general"]


async def add_user_strategy(
    session: AsyncSession,
    user_id: int,
    name: str,
    rules: str,
    description: str = "",
    timeframes: str = "15m,1h,4h,1d",
    indicators: str = "EMA,RSI,MACD,ATR",
    version: int = 1,
) -> TradingStrategy:
    """Create a user-defined strategy and assign it a unique custom key."""
    strategy = TradingStrategy(
        user_id=user_id,
        key="_pending",
        name=name.strip(),
        description=description.strip(),
        timeframes=timeframes,
        indicators=indicators,
        rules=rules.strip(),
        is_system=False,
        version=version,
    )
    session.add(strategy)
    await session.flush()
    strategy.key = f"custom_{strategy.id}"
    await session.commit()
    await session.refresh(strategy)
    return strategy


async def get_user_strategies(
    session: AsyncSession,
    user_id: int,
) -> list[TradingStrategy]:
    """Return a user's custom strategies, newest first."""
    stmt = (
        select(TradingStrategy)
        .where(
            and_(
                TradingStrategy.user_id == user_id,
                TradingStrategy.is_system == False,
            )
        )
        .order_by(TradingStrategy.id.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_user_strategy(
    session: AsyncSession,
    user_id: int,
    key: str,
) -> bool:
    """Delete a user's custom strategy by key. Returns True if deleted."""
    stmt = delete(TradingStrategy).where(
        and_(
            TradingStrategy.user_id == user_id,
            TradingStrategy.key == key,
            TradingStrategy.is_system == False,
        )
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0


SYSTEM_DOC_EXTRACT_PROMPT = (
    "You are a meticulous trading-strategy analyst. You read trading strategy documents and "
    "carefully extract EVERY distinct strategy described in them. "
    "A single document may describe several independent strategies; split them apart precisely. "
    "For each strategy you must preserve its full logic: the exact market conditions needed to enter, "
    "confirmation signals, indicators, entry trigger, stop-loss placement, and profit targets. "
    "Respond ONLY with a raw JSON array of objects, no markdown fences and no commentary, "
    'matching this schema: [{"name": "...", "description": "...", "timeframes": "...", '
    '"indicators": "...", "rules": "complete, detailed rules with entry & exit logic"}]'
)


def _extract_json_array(text: str | None) -> list[dict] | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # Try a raw top-level array first.
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end != -1:
        try:
            arr = json.loads(cleaned[start : end + 1])
            if isinstance(arr, list):
                return [s for s in arr if isinstance(s, dict)]
        except Exception:
            pass  # fall through to object extraction

    # Otherwise look for a wrapper object that holds the array.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        try:
            obj = json.loads(cleaned[start : end + 1])
        except Exception:
            return None
        if isinstance(obj, list):
            return [s for s in obj if isinstance(s, dict)]
        if isinstance(obj, dict):
            for key in ("strategies", "strategy_list", "trading_strategies", "strategies_list", "items"):
                arr = obj.get(key)
                if isinstance(arr, list):
                    return [s for s in arr if isinstance(s, dict)]
    return None


def _visible_text(text: str) -> str:
    """Squash whitespace for the strategy-name heuristic."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_heading(line: str) -> str:
    """Turn a detected heading line into a clean strategy name."""
    name = re.sub(r"^#+\s*", "", line).strip()
    name = re.sub(r"^\d+[.)]\s*", "", name).strip()
    name = re.sub(r"^(?:trading\s+|ai\s+)?strategy\b\s*[:,-]?\s*", "", name, flags=re.I)
    name = name.strip("#").strip().strip(":").strip()
    if not name:
        name = "Imported Strategy"
    return name[:100]


async def _categorize_document_with_llm(
    doc_text: str, filename: str
) -> list[dict]:
    truncated = doc_text[:8000]
    prompt = (
        f"A user uploaded a trading strategy document named '{filename}'. "
        "It may contain one or more separate trading strategies.\n\n"
        "=== DOCUMENT CONTENT ===\n"
        f"{truncated}\n"
        "=== END DOCUMENT ===\n\n"
        "Carefully identify every distinct strategy in the document. "
        "Each 'name' must be short and descriptive. Each 'rules' field must contain the "
        "COMPLETE logic: exact market conditions that allow entry, confirmation signals, "
        "entry trigger, stop loss, and targets. Do not invent rules that are not present. "
        "Respond with ONLY the raw JSON array."
    )
    raw = await call_llm(
        prompt,
        max_tokens=2000,
        system_prompt=SYSTEM_DOC_EXTRACT_PROMPT,
    )
    items = _extract_json_array(raw)

    valid = []
    for item in items or []:
        name = str(item.get("name") or "").strip()[:100]
        rules = str(item.get("rules") or "").strip()
        if not name or len(rules) < 20:
            continue
        valid.append(
            {
                "name": name,
                "description": str(item.get("description") or "").strip()[:500],
                "timeframes": str(item.get("timeframes") or "15m,1h,4h,1d")[:50],
                "indicators": str(item.get("indicators") or "EMA,RSI,MACD,ATR")[:100],
                "rules": rules[:2000],
            }
        )
    return valid


def _fallback_document_split(doc_text: str) -> list[dict]:
    """Heuristic split when the LLM is unavailable: headings become strategy names."""
    lines = doc_text.splitlines()

    strategy_heading = re.compile(r"^\s*(?:#+\s*)?(?:\d+[.)]\s*)?(?:trading\s+|ai\s+)?strategy\b", re.IGNORECASE)
    numbered_heading = re.compile(r"^\s*(?:#+\s*)?\d+[.)]\s+[^\n]{2,90}$")
    md_heading = re.compile(r"^\s*#+\s+[^\n]+$")

    # First pass: prefer explicit "strategy" headings; fall back to numbered/markdown headings.
    heading_idx: list[int] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or len(s) > 120:
            continue
        if strategy_heading.match(s):
            heading_idx.append(i)
    if not heading_idx:
        for i, line in enumerate(lines):
            s = line.strip()
            if not s or len(s) > 120:
                continue
            if numbered_heading.match(s) and not s.endswith("."):
                heading_idx.append(i)
            elif md_heading.match(s):
                heading_idx.append(i)

    strategies: list[dict] = []
    if heading_idx:
        bounds = heading_idx + [len(lines)]
        for idx, start in enumerate(heading_idx):
            name = _clean_heading(lines[start])
            body = [
                l.strip()
                for l in lines[start + 1 : bounds[idx + 1]]
                if l.strip()
            ]
            rules = "\n".join(body).strip()
            if name and len(rules) >= 20:
                strategies.append(
                    {
                        "name": name[:100],
                        "description": "Heuristically extracted from the uploaded document.",
                        "timeframes": "15m,1h,4h,1d",
                        "indicators": "EMA,RSI,MACD,ATR",
                        "rules": rules[:2000],
                    }
                )

    if strategies:
        return strategies

    # No usable headings found: import the full text as a single strategy.
    return [
        {
            "name": "Imported Strategy",
            "description": "Strategy imported from an uploaded document.",
            "timeframes": "15m,1h,4h,1d",
            "indicators": "EMA,RSI,MACD,ATR",
            "rules": _visible_text(doc_text)[:2000],
        }
    ]


async def import_strategies_from_document(
    session: AsyncSession,
    user_id: int,
    doc_text: str,
    filename: str,
    source_note: str | None = None,
) -> list[TradingStrategy]:
    """Categorize a strategy document into one or more strategies and persist them.

    The LLM categorization is best-effort: a deterministic heading-based splitter
    always runs first so the import can never fail on an unreadable AI response.
    """
    if not doc_text.strip():
        raise ValueError("The document contains no readable text.")

    items = _fallback_document_split(doc_text)
    try:
        llm_items = await _categorize_document_with_llm(doc_text, filename)
        if llm_items:
            items = llm_items
    except Exception as e:
        logger.warning(f"LLM doc categorization failed, using heuristic split: {e}")

    created: list[TradingStrategy] = []
    source = source_note or f"Imported from {filename}"
    for item in items:
        description = f"{source}. {item['description']}".strip().strip(".")
        strategy = await add_user_strategy(
            session=session,
            user_id=user_id,
            name=item["name"],
            rules=item["rules"],
            description=description,
            timeframes=item["timeframes"],
            indicators=item["indicators"],
        )
        created.append(strategy)
    return created
