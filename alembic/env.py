"""
Alembic migration environment.
Configured for async SQLAlchemy with aiosqlite.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models so Alembic can detect them for autogenerate
from app.models import (  # noqa: F401
    Base,
    User,
    FinancialInstitution, Account, CategoryRule, AiExtraction,
    Insight, Budget, AdvisorReport, Statement, Transaction, Fee,
    InterestCharge, RewardsSummary, CategorySummary, Payment,
    LiabilityTemplate, MonthlyRecord, MonthlyLiability
)


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode (no live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using async engine."""
    # Import here to avoid circular imports at module level
    from app.config import settings

    configuration = config.get_section(config.config_ini_section, {})
    # Use the app's database URL (strip async driver for alembic sync usage)
    db_url = settings.get_database_url
    configuration["sqlalchemy.url"] = db_url.replace(
        "sqlite+aiosqlite", "sqlite"
    ).replace(
        "postgresql+asyncpg", "postgresql"
    )

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # aiosqlite/asyncpg needs the async driver
        url=settings.get_database_url,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
