"""
Database configuration and session management.
Uses Alembic for migrations on startup (run_migrations=True by default).
"""
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

# Create async engine
engine = create_async_engine(
    settings.get_database_url,
    echo=settings.debug,
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base class for all models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting database session.

    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database on application startup.

    Strategy:
      1. Run Alembic migrations (handles schema changes on existing DBs).
      2. Fall back to create_all for any tables Alembic doesn't cover
         (e.g. during fresh install before first migration).
    """
    # Run Alembic migrations (handles existing DBs with data)
    try:
        import asyncio
        from alembic.config import Config
        from alembic import command

        def run_alembic():
            alembic_cfg = Config("alembic.ini")
            command.upgrade(alembic_cfg, "head")

        # Run in thread pool to avoid blocking the async loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_alembic)
        logger.info("Alembic migrations applied successfully")
    except Exception as e:
        logger.warning(f"Alembic migration failed (may be first run): {e}")
        # Fall back to create_all for fresh installs
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created via create_all (fresh install)")
