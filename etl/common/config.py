"""Configuration and environment setup for ETL pipeline."""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ETL_DIR = Path(__file__).resolve().parents[1]

# Load .env from etl/ regardless of working directory. Importing this module is
# what makes etl/.env visible, so anything that reads os.environ imports it first.
load_dotenv(ETL_DIR / ".env")


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    # A single connection string (what Neon gives you) takes precedence over DB_*.
    database_url: str = os.getenv("DATABASE_URL", "")
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "surgical")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_ssl_mode: str = os.getenv("DB_SSL_MODE", "require")

    request_delay_ms: int = int(os.getenv("REQUEST_DELAY_MS", "100"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # Downloads (openFDA bulk files, hospital price files) are written here, not held in memory.
    data_dir: str = os.getenv("ETL_DATA_DIR", str(ETL_DIR / "data"))

    # openFDA allows about 1,000 requests a day without a key and 120,000 with one.
    openfda_api_key: str = os.getenv("OPENFDA_API_KEY", "")

    # Medicare Physician & Other Practitioners, by Provider and Service. CMS publishes a new
    # dataset ID with each year of data, so both are settings.
    cms_utilization_dataset_id: str = os.getenv(
        "CMS_UTILIZATION_DATASET_ID", "335e5f35-eca6-482d-87b3-f99883e213e3"
    )
    cms_utilization_year: int = int(os.getenv("CMS_UTILIZATION_YEAR", "2024"))
    # CMS Physician Fee Schedule relative value file (a new one each calendar year).
    cms_rvu_url: str = os.getenv(
        "CMS_RVU_URL", "https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip"
    )

    # Hospital price files larger than this are skipped (bytes on disk, after download).
    mrf_max_file_mb: int = int(os.getenv("MRF_MAX_FILE_MB", "4096"))
    # What decides the size of the prices table. The defaults fit Neon's free 1 GB:
    # prices for the 150 most-performed procedures, one row per payer.
    # MRF_CPT_LIMIT=0 keeps every code; MRF_PLAN_DETAIL=true keeps one row per plan.
    mrf_cpt_limit: int = int(os.getenv("MRF_CPT_LIMIT", "150"))
    mrf_plan_detail: bool = os.getenv("MRF_PLAN_DETAIL", "false").strip().lower() in ("1", "true", "yes")

    trilliant_share_file: str = os.getenv("TRILLIANT_SHARE_FILE", "")
    trilliant_share_name: str = os.getenv("TRILLIANT_SHARE_NAME", "provider_directory")
    trilliant_schema_name: str = os.getenv("TRILLIANT_SCHEMA_NAME", "directory")
    trilliant_table_name: str = os.getenv("TRILLIANT_TABLE_NAME", "directory_providers")

    @property
    def db_connect_kwargs(self) -> dict:
        """Connection kwargs for psycopg2 — safe with special chars in password."""
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


logging.basicConfig(
    level=getattr(logging, get_settings().log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# httpx logs every request at INFO, which drowns out the step logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
