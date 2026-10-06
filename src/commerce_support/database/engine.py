from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from commerce_support.config import Settings
from commerce_support.database.exceptions import DatabaseConfigurationError


class Database:
    def __init__(self, settings: Settings) -> None:
        if settings.database_url is None:
            raise DatabaseConfigurationError("DATABASE_URL is required for database access")

        self._engine: AsyncEngine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )
        self.turn_lease_seconds = settings.turn_lease_seconds

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def aclose(self) -> None:
        await self._engine.dispose()

    def __repr__(self) -> str:
        return "Database()"
