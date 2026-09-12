import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.services import db_service
from app.services.prices.price_fetcher import price_fetcher

logger = logging.getLogger("crypto_watchman.digest_service")

SYSTEM_PROMPT = "You are a concise crypto market analyst. Respond in plain text with short paragraphs. No markdown."


async def format_portfolio_data(session: AsyncSession, user_id: int) -> str | None:
    portfolio = await db_service.get_portfolio(session, user_id)
    if not portfolio:
        return None

    lines = []
    total_pnl = 0.0
    count = 0

    for item in portfolio:
        current = await price_fetcher.get_price(item.symbol)
        if current is None:
            continue
        pct = ((current - item.entry_price) / item.entry_price) * 100
        total_pnl += pct
        count += 1
        arrow = "+" if pct >= 0 else ""
        lines.append(f"- {item.symbol}: entry ${item.entry_price:,.2f}, now ${current:,.2f} ({arrow}{pct:.2f}%)")

    if not lines:
        return None

    avg_pnl = total_pnl / count
    header = f"Portfolio ({count} assets) — avg change: {avg_pnl:+.2f}%\n"
    return header + "\n".join(lines)


async def _call_chat_completions(url: str, api_key: str, model: str, prompt: str) -> str | None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        logger.warning(f"LLM API ({url}) returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"LLM API ({url}) failed: {e}")
    return None


async def call_llm(prompt: str) -> str | None:
    providers = []

    # Groq is the primary provider (fast + free tier).
    if settings.GROQ_API_KEY:
        providers.append((
            "https://api.groq.com/openai/v1/chat/completions",
            settings.GROQ_API_KEY,
            settings.GROQ_MODEL,
        ))

    if settings.OPENAI_API_KEY:
        providers.append((
            "https://api.openai.com/v1/chat/completions",
            settings.OPENAI_API_KEY,
            "gpt-4o-mini",
        ))

    if settings.OPENROUTER_API_KEY:
        providers.append((
            "https://openrouter.ai/api/v1/chat/completions",
            settings.OPENROUTER_API_KEY,
            "deepseek/deepseek-chat",
        ))

    for url, api_key, model in providers:
        result = await _call_chat_completions(url, api_key, model, prompt)
        if result:
            return result

    logger.warning("No LLM provider available or all returned errors. Set GROQ_API_KEY (or OPENAI_API_KEY / OPENROUTER_API_KEY) in .env.")
    return None


async def generate_digest(session: AsyncSession, user_id: int) -> str:
    data = await format_portfolio_data(session, user_id)
    if not data:
        return "📭 Your portfolio is empty. Add assets with /add_asset first."

    today = datetime.now().strftime("%B %d, %Y")
    prompt = (
        f"Today is {today}. A user's crypto portfolio performance:\n\n"
        f"{data}\n\n"
        "Write a brief market summary (2-3 sentences). "
        "Highlight the best and worst performer, note the overall trend, "
        "and give one actionable observation."
    )

    llm_text = await call_llm(prompt)
    if llm_text:
        return f"🤖 <b>AI Market Digest</b>\n\n{llm_text}"

    lines = ["📊 <b>Market Digest</b>\n"]
    lines.append(data.replace("\n", "\n\n"))
    return "\n".join(lines)
