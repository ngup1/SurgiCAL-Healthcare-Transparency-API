"""Application settings, read from environment variables (and api/.env when present)."""

from psycopg.conninfo import make_conninfo
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
    # Connections per worker process (uvicorn runs 2 workers in the image).
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # fastapi-best-practices: be able to hide docs; on by default for the public demo.
    show_docs: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]
    # Baked into the image by CI; reported by /health.
    git_commit: str = "unknown"

    @property
    def db_conninfo(self) -> str:
        """libpq connection string: DATABASE_URL as-is, or built from DB_*."""
        if self.database_url:
            return self.database_url
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
            sslmode=self.db_ssl_mode,
        )


settings = Settings()
