"""
openFDA bulk downloads.

openFDA publishes every dataset as zipped JSON files next to its search API.
Reading those takes a handful of downloads instead of thousands of rate-limited
searches, and needs no API key.

Index of files: https://api.fda.gov/download.json
"""
from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import ijson

from etl.common.config import get_logger, get_settings
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

DOWNLOAD_INDEX_URL = "https://api.fda.gov/download.json"


def bulk_file_urls(client: RateLimitedClient, endpoint: str) -> list[str]:
    """URLs of the files that make up one device dataset, e.g. endpoint='510k'."""
    index = client.get_json(DOWNLOAD_INDEX_URL)
    return [p["file"] for p in index["results"]["device"][endpoint]["partitions"]]


def iter_bulk_records(client: RateLimitedClient, endpoint: str) -> Iterator[dict]:
    """
    Every record of a device dataset.

    Each file is downloaded to disk, stream-parsed (they unzip to gigabytes),
    and deleted once read.
    """
    data_dir = Path(get_settings().data_dir) / "openfda"
    for url in bulk_file_urls(client, endpoint):
        path = data_dir / f"{endpoint}-{url.rsplit('/', 1)[-1]}"
        client.download(url, path)
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    with zf.open(name) as fh:
                        # use_float: plain floats instead of Decimal for the few numeric fields
                        yield from ijson.items(fh, "results.item", use_float=True)
        finally:
            path.unlink(missing_ok=True)
