import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Bot settings
    TELEGRAM_BOT_TOKEN: str = Field(default="placeholder_token")
    # Bot username (used for the browser "Sign in with Telegram" widget)
    TELEGRAM_BOT_USERNAME: str = ""

    # DB settings
    DATABASE_URL: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/crypto_watchman")

    @field_validator("DATABASE_URL", mode="before")
    def fix_database_url(cls, v: str) -> str:
        if isinstance(v, str) and v:
            if v.startswith("postgres://"):
                v = v.replace("postgres://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
                v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            # Neon & asyncpg compatibility adjustments
            if "asyncpg" in v:
                import re
                v = re.sub(r"sslmode=[^&]*", "ssl=require", v)
                v = re.sub(r"[?&]channel_binding=[^&]*", "", v)
                if "?" not in v and "&" in v:
                    v = v.replace("&", "?", 1)
        return v

    # Redis settings
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Celery settings
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/2")

    # API keys
    COINGECKO_API_KEY: str | None = None
    TWELVEDATA_API_KEY: str | None = None
    ETHERSCAN_API_KEY: str | None = None
    COVALENT_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-20b"

    # Web3 wallet monitoring (Phase 2D)
    WALLET_REFRESH_MINUTES: int = Field(default=30, ge=5)

    # AI News engine
    NEWS_RETENTION_HOURS: int = Field(default=48, ge=1)
    NEWS_REFRESH_MINUTES: int = Field(default=10, ge=1)
    NEWS_MAX_QUERIES_PER_ASSET: int = Field(default=3, ge=1, le=5)

    # Multi-asset whale monitoring
    WHALE_SCAN_MINUTES: int = Field(default=5, ge=1)
    WHALE_MAX_ITEMS_PER_ASSET: int = Field(default=50, ge=1, le=200)

    # Opportunity engine (Phase 2F)
    OPPORTUNITY_SCAN_MINUTES: int = Field(default=15, ge=5)

    # Paper trading monitor (Phase 2G)
    TRADING_SCAN_MINUTES: int = Field(default=15, ge=5)
    TRADING_MARK_MINUTES: int = Field(default=5, ge=1)

    # Live trading (Phase 2H / LIVE_EXECUTION)
    # Global master kill-switch: while False nothing can reach a real broker,
    # regardless of per-user arming.
    LIVE_TRADING_ENABLED: bool = False
    # Max % of live equity a single position may consume (notional cap).
    LIVE_MAX_POSITION_PCT: float = Field(default=10.0, ge=0.5, le=100.0)
    # Hard daily loss limit for live accounts (% of balance at day start).
    LIVE_DAILY_LOSS_LIMIT_PCT: float = Field(default=5.0, ge=0.5, le=100.0)
    # cTrader Connect API v2 base URL.
    CTRADER_API_BASE_URL: str = Field(default="https://connect.ctrader.com/api/v2")

    # Mini App (Phase 2E)
    PUBLIC_BASE_URL: str = Field(default="http://localhost:8000")
    # Mini App auth token lifetime (hours)
    MINI_APP_TOKEN_TTL_HOURS: int = Field(default=24, ge=1)
    # Secret dev bypass that authenticates as ADMIN_TELEGRAM_ID when opening the
    # Mini App from a plain browser (?dev_token=...). Empty = disabled.
    MINI_APP_DEV_TOKEN: str = ""
    # Lifetime of the one-time bot login code (seconds) minted by the Mini App.
    MINI_APP_LOGIN_CODE_TTL_SECONDS: int = Field(default=300, ge=60)

    # Security
    SECRET_KEY: str = Field(default="change-me-to-a-very-secure-key")

    # Admin
    ADMIN_TELEGRAM_ID: int | None = None

settings = Settings()
