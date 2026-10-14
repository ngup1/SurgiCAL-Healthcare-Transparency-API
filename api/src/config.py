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
    # Seconds a request waits for a database connection before failing with a 503.
    db_pool_timeout: float = 5.0

    # fastapi-best-practices: be able to hide docs; on by default for the public demo.
    show_docs: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]
    # Baked into the image by CI; reported by /health.
    git_commit: str = "unknown"
    log_level: str = "INFO"
    # One JSON object per log line; set LOG_JSON=false for plain text when developing locally.
    log_json: bool = True

    # Requests per minute per client, per worker process (src/rate_limit.py).
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    rate_limit_search_per_minute: int = 30
    # Header holding the real client IP, set by the host's edge and not forgeable by
    # clients (e.g. True-Client-IP behind Cloudflare). Empty: the connection's address.
    rate_limit_client_ip_header: str = ""
    # Temporary: GET /debug/headers echoes the request headers the app receives, to
    # confirm the header above arrives and can't be forged. Leave off otherwise.
    debug_headers: bool = False

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
