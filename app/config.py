"""
Application configuration using Pydantic Settings.
Loads configuration from environment variables and .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """
    Application settings.

    All settings can be overridden via environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",  # e.g. legacy IMAGE_DPI after native-PDF refactor
    )

    # Application
    app_name: str = "Personal Finance Intelligence"
    app_version: str = "2.0.0"
    debug: bool = True

    # Database
    database_url: str = "sqlite+aiosqlite:///./statements.db"

    # File Upload
    upload_dir: str = "./static/uploads"
    max_file_size_mb: int = 10

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Claude AI (Anthropic)
    anthropic_api_key: Optional[str] = None

    # Model for statement extraction (vision).
    # "claude-haiku-4-5"   — fast, cheap (~15× cheaper than Sonnet), good for structured PDFs
    # "claude-sonnet-4-5"  — most accurate, higher cost
    extraction_model: str = "claude-haiku-4-5"

    # Max output tokens for extraction response.
    # Increase if you have very long statements (40+ transactions per page).
    extraction_max_tokens: int = 16000

    # Financial defaults
    default_currency: str = "BDT"

    # Authentication
    secret_key: str = "super_secret_jwt_key_please_change_in_production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Google OAuth
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None

    @property
    def max_file_size_bytes(self) -> int:
        """Convert max file size from MB to bytes"""
        return self.max_file_size_mb * 1024 * 1024

    @property
    def get_database_url(self) -> str:
        """Transform DATABASE_URL for Railway's Postgres and SQLAlchemy's asyncpg"""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


# Global settings instance
settings = Settings()
