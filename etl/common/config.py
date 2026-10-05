"""Configuration and environment setup for ETL pipeline."""

import os
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from etl/ regardless of working directory
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "surgical")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_ssl_mode: str = os.getenv("DB_SSL_MODE", "require")
    request_delay_ms: int = int(os.getenv("REQUEST_DELAY_MS", "100"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    trilliant_share_file: str = os.getenv("TRILLIANT_SHARE_FILE", "")
    trilliant_share_name: str = os.getenv("TRILLIANT_SHARE_NAME", "provider_directory")
    trilliant_schema_name: str = os.getenv("TRILLIANT_SCHEMA_NAME", "directory")
    trilliant_table_name: str = os.getenv("TRILLIANT_TABLE_NAME", "directory_providers")
    aws_region: str = os.getenv("AWS_REGION", "us-west-2")
    s3_oria_bucket: str = os.getenv("S3_ORIA_BUCKET", "")

    @property
    def db_connect_kwargs(self) -> dict:
        """Connection kwargs for psycopg2 — safe with special chars in password."""
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
            "sslmode": self.db_ssl_mode,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


logging.basicConfig(
    level=getattr(logging, get_settings().log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
