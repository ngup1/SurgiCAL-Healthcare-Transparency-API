"""Application settings, read from environment variables (and api/.env when present)."""

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A full connection string (what Neon/Render give you) takes precedence over DB_*.
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "surgical"
    db_user: str = "postgres"
    db_password: str = ""
    db_ssl_mode: str = "require"

    # fastapi-best-practices: be able to hide docs; on by default for the public demo.
    show_docs: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]
    # Baked into the image by CI; reported by /health.
    git_commit: str = "unknown"

    @property
    def db_connect_args(self) -> dict[str, Any]:
        """Keyword arguments for the database driver's connect()."""
        if self.database_url:
            return {"dsn": self.database_url}
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
            "sslmode": self.db_ssl_mode,
        }


settings = Settings()
