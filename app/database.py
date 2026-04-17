"""
Database configuration and session management.
Uses Alembic for migrations on startup (run_migrations=True by default).

Exports:
    - Base: SQLAlchemy declarative base
    - get_db: Dependency for getting async database session
    - get_current_user: Dependency for getting authenticated user (imported from auth router)
"""
import logging
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

# Global flag — set to a string error message when the DB is unavailable.
# Checked by middleware to serve a "system down" page gracefully.
db_error: Optional[str] = None

# Create async engine (guarded so an unavailable DB driver / bad URL never
# crashes the import and kills the uvicorn process before it can start).
try:
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
    )
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
except Exception as _engine_exc:  # noqa: BLE001
    engine = None  # type: ignore[assignment]
    AsyncSessionLocal = None  # type: ignore[assignment]
    db_error = str(_engine_exc)
    logger.critical("Database engine could not be created: %s", _engine_exc)

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
    if AsyncSessionLocal is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unavailable")
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
    global db_error

    if engine is None:
        logger.error("Skipping DB init — engine was not created: %s", db_error)
        return

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
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created via create_all (fresh install)")
        except Exception as create_err:
            db_error = str(create_err)
            logger.error("Database init failed: %s", create_err)
