"""
Application configuration using Pydantic Settings.
Loads configuration from environment variables and .env file.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """
    Application settings.

    All settings can be overridden via environment variables.
    """

    # Application
    app_name: str = "Credit Card Statement Analyzer"
    debug: bool = True

    # Database
    database_url: str = "sqlite+aiosqlite:///./statements.db"

    # File Upload
    upload_dir: str = "./static/uploads"
    max_file_size_mb: int = 10

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def max_file_size_bytes(self) -> int:
        """Convert max file size from MB to bytes"""
        return self.max_file_size_mb * 1024 * 1024

    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
