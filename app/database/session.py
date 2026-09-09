import os
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

class AsyncEngineProxy:
    """Proxy for AsyncEngine that recreates the engine if the process PID changes (e.g. after a fork in Celery)."""
    def __init__(self):
        self._engine = None
        self._pid = None

    def _get_engine(self):
        current_pid = os.getpid()
        if self._engine is None or self._pid != current_pid:
            self._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=False,
                future=True,
                pool_pre_ping=True
            )
            self._pid = current_pid
        return self._engine

    def __getattr__(self, name):
        return getattr(self._get_engine(), name)


class AsyncSessionMakerProxy:
    """Proxy for async_sessionmaker that recreates it if the process PID changes."""
    def __init__(self):
        self._maker = None
        self._pid = None

    def _get_maker(self):
        current_pid = os.getpid()
        if self._maker is None or self._pid != current_pid:
            self._maker = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            self._pid = current_pid
        return self._maker

    def __call__(self, *args, **kwargs):
        return self._get_maker()(*args, **kwargs)

# Create proxy instances
engine = AsyncEngineProxy()
async_session_maker = AsyncSessionMakerProxy()


# Base class for models
class Base(DeclarativeBase):
    pass

# Dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
