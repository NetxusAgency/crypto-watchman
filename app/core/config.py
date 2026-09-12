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
    OPENAI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # Security
    SECRET_KEY: str = Field(default="change-me-to-a-very-secure-key")

    # Admin
    ADMIN_TELEGRAM_ID: int | None = None

settings = Settings()
