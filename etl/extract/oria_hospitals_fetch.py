"""
Peek into mrf_lake.duckdb via HTTP range requests on the ZIP URL.

How it works:
  1. Reads the ZIP central directory (via remotezip) to find mrf_lake.duckdb's
     byte offset and size within the ZIP.
  2. If the entry is STORED (uncompressed): spins up a tiny local HTTP proxy that
     translates DuckDB's range requests into equivalent range requests on the ZIP
     URL (shifted by the DuckDB entry's offset).
  3. ATTACHes the proxy URL as a read-only DuckDB database and queries just the
     hospitals + mrf_metadata tables -- downloading only the relevant blocks
     (a few MB, not 45 GB).
  4. Saves results to a CSV used by oria_prices.py to resolve hospital_id -> CCN
     by matching mrf_hospital_name / mrf_source_url against hospital_mrf_links.

If mrf_lake.duckdb is DEFLATE-compressed inside the ZIP this approach won't work;
the script will say so and suggest asking Oria for a direct .duckdb URL.

Usage:
    python -m etl.extract.oria_hospitals_fetch \\
        --url "https://fly.storage.tigris.dev/.../mrf_lake.zip?<presigned>" \\
        --out etl/data/oria_ca_hospitals.csv
"""
from __future__ import annotations

import argparse
import csv
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

try:
    import duckdb
except ImportError:
    raise ImportError("pip install duckdb")

try:
    from remotezip import RemoteZip
except ImportError:
    raise ImportError("pip install remotezip")


DUCKDB_ENTRY = "mrf_lake.duckdb"
_STORED = 0  # ZIP compression method: no compression


# ---------------------------------------------------------------------------
# ZIP navigation
# ---------------------------------------------------------------------------

def _read_local_header_lengths(zip_url: str, header_offset: int) -> tuple[int, int]:
    """
    Read the 30-byte local file header at header_offset.
    Returns (fname_len, extra_len) so we can compute the exact data start.

    The local extra field length often differs from the central-directory extra
    field length (ZIP64 records may be present in one but not the other).
    """
    resp = requests.get(
        zip_url,
        headers={"Range": f"bytes={header_offset}-{header_offset + 29}"},
        timeout=30,
    )
    resp.raise_for_status()
    buf = resp.content
    if len(buf) != 30:
        raise ValueError(f"Expected 30-byte local header, got {len(buf)}")

    sig = struct.unpack_from("<I", buf, 0)[0]
    if sig != 0x04034B50:
        raise ValueError(f"Bad local file header signature: {sig:#010x}")

    fname_len = struct.unpack_from("<H", buf, 26)[0]
    extra_len = struct.unpack_from("<H", buf, 28)[0]
    return fname_len, extra_len


def find_duckdb_in_zip(zip_url: str) -> tuple[int, int]:
    """
    Locate mrf_lake.duckdb in the ZIP central directory.

    Returns (data_offset, data_size) -- the byte range within the ZIP that
    contains the raw (uncompressed) DuckDB file.

    Raises ValueError if the entry is not found or is DEFLATE-compressed.
    """
    print("Reading ZIP central directory...")
    with RemoteZip(zip_url) as zf:
        entries = {info.filename: info for info in zf.infolist()}

    if DUCKDB_ENTRY not in entries:
        available = "\n  ".join(sorted(entries)[:20])
        raise ValueError(
            f"'{DUCKDB_ENTRY}' not found in ZIP.\n"
            f"First 20 entries:\n  {available}"
        )

    info = entries[DUCKDB_ENTRY]
    if info.compress_type != _STORED:
        raise ValueError(
            f"'{DUCKDB_ENTRY}' is compressed (method={info.compress_type}).\n"
            "Range-request peeking requires the entry to be stored uncompressed.\n"
            "Ask Oria for a direct presigned URL to mrf_lake.duckdb instead."
        )

    fname_len, extra_len = _read_local_header_lengths(zip_url, info.header_offset)
    data_offset = info.header_offset + 30 + fname_len + extra_len
    data_size = info.file_size  # equals compress_size for STORED entries

    print(
        f"  Found '{DUCKDB_ENTRY}': "
        f"offset={data_offset:,}  size={data_size / 1e9:.2f} GB"
    )
    return data_offset, data_size


# ---------------------------------------------------------------------------
# Local HTTP proxy
# ---------------------------------------------------------------------------

class _RangeProxyHandler(BaseHTTPRequestHandler):
    """
    Minimal HTTP/1.1 server that proxies range requests for the DuckDB file
    by translating them into range requests on the remote ZIP URL.

    DuckDB's httpfs reads only the blocks it needs (a few MB for a small table),
    so the actual download is tiny despite the 45 GB ZIP.
    """

    zip_url: str = ""
    duckdb_offset: int = 0
    duckdb_size: int = 0

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(self.duckdb_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        range_hdr = self.headers.get("Range", "")
        if range_hdr.startswith("bytes="):
            lo, hi = range_hdr[6:].split("-", 1)
            start = int(lo)
            end = int(hi) if hi else self.duckdb_size - 1
        else:
            start, end = 0, self.duckdb_size - 1

        zip_start = self.duckdb_offset + start
        zip_end = self.duckdb_offset + end

        resp = requests.get(
            self.zip_url,
            headers={"Range": f"bytes={zip_start}-{zip_end}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.content

        self.send_response(206 if range_hdr else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        if range_hdr:
            self.send_header(
                "Content-Range", f"bytes {start}-{end}/{self.duckdb_size}"
            )
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args) -> None:  # suppress access logs
        pass


def _start_proxy(zip_url: str, duckdb_offset: int, duckdb_size: int, port: int) -> HTTPServer:
    handler_cls = type(
        "_Handler",
        (_RangeProxyHandler,),
        {"zip_url": zip_url, "duckdb_offset": duckdb_offset, "duckdb_size": duckdb_size},
    )
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# DuckDB queries
# ---------------------------------------------------------------------------

def _query_hospitals(proxy_url: str, state: str) -> list[dict]:
    """
    ATTACH the proxied DuckDB and pull hospital + MRF URL data for one state.

    mrf_metadata has no hospital_id FK, so we join on facility = hospital_name.
    Both hospitals.mrf_hospital_name and mrf_metadata.source_url are included so
    oria_prices.py can match against hospital_mrf_links by either name or URL.
    """
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"ATTACH '{proxy_url}' AS lake (READ_ONLY);")

    # Primary query: hospitals joined to mrf_metadata by facility name
    rows = con.execute(
        """
        SELECT
            h.hospital_id,
            h.hospital_name,
            h.mrf_hospital_name,
            h.hospital_state,
            m.source_url   AS mrf_source_url
        FROM lake.hospitals h
        LEFT JOIN lake.mrf_metadata m
               ON m.facility = h.hospital_name
        WHERE h.hospital_state = ?
        """,
        [state],
    ).fetchall()

    con.close()
    cols = ["hospital_id", "hospital_name", "mrf_hospital_name", "hospital_state", "mrf_source_url"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch(zip_url: str, state: str = "CA", port: int = 19876) -> list[dict]:
    """Download hospital metadata for one state via ZIP range requests."""
    duckdb_offset, duckdb_size = find_duckdb_in_zip(zip_url)

    print(f"  Starting proxy on 127.0.0.1:{port} ...")
    server = _start_proxy(zip_url, duckdb_offset, duckdb_size, port)
    proxy_url = f"http://127.0.0.1:{port}/mrf_lake.duckdb"

    try:
        print(f"  Querying hospitals (state={state}) via DuckDB range requests ...")
        rows = _query_hospitals(proxy_url, state)
        print(f"  Got {len(rows)} rows")
        return rows
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Peek into mrf_lake.duckdb via ZIP range requests"
    )
    parser.add_argument("--url", required=True, help="Presigned URL to mrf_lake.zip")
    parser.add_argument("--state", default="CA", help="State to fetch (default: CA)")
    parser.add_argument(
        "--out",
        default="etl/data/oria_ca_hospitals.csv",
        help="Output CSV (used by oria_prices.py)",
    )
    parser.add_argument("--port", type=int, default=19876, help="Local proxy port")
    args = parser.parse_args()

    rows = fetch(args.url, state=args.state, port=args.port)

    if not rows:
        print("No rows returned -- check URL and state.")
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
