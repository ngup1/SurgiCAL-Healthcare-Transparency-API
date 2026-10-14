"""
Hospital Price File Discovery

Finds each hospital's machine-readable price file (MRF) through the index CMS
has required since 2024: a text file at the root of the hospital's website,
<website>/cms-hpt.txt, listing every location's price file:

    location-name: Example Medical Center
    source-page-url: https://example.org/price-transparency
    mrf-url: https://example.org/files/123456789_example-medical-center_standardcharges.json
    contact-name: ...
    contact-email: ...

Inputs kept by hand in etl/reference/:
- hospital_websites.csv  ccn, website. CMS publishes no hospital websites, so this
  is the list of where to look. A health system's hospitals share one website.
- mrf_overrides.csv      ccn, hospital_name, machine_readable_url. For hospitals whose
  index is missing or whose entry can't be matched to a CCN by name.

Populates the `hospital_mrf_links` table (source 'cms_hpt_txt' or 'manual').
"""
from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import httpx

from etl.common.config import ETL_DIR, get_logger
from etl.common.db import execute, execute_query, upsert_batch
from etl.common.http import RateLimitedClient
from etl.common.places import fold
from etl.extract.mrf_parser import sync_payer_aliases

logger = get_logger(__name__)

WEBSITES_FILE = ETL_DIR / "reference" / "hospital_websites.csv"
OVERRIDES_FILE = ETL_DIR / "reference" / "mrf_overrides.csv"

# Words that say nothing about which hospital a name refers to.
NAME_NOISE = {
    "hospital", "hospitals", "medical", "center", "centre", "ctr", "med", "the", "of", "and", "at", "inc",
    "llc", "health", "healthcare", "campus", "system", "dba",
}
# A location name this close to a hospital's name is that hospital, whatever the address says.
MATCH_THRESHOLD_NAME_ONLY = 0.85
# For a hospital in a ZIP code the file names but at a different street number.
MATCH_THRESHOLD_SAME_ZIP = 0.5

_NUMBER_RE = re.compile(r"(?<!\d)\d{1,6}(?!\d)")


def parse_cms_hpt(text: str) -> list[dict]:
    """
    Entries of a cms-hpt.txt file as {location_name, mrf_url}.

    Keys are case-insensitive, entries are separated by blank lines, and one
    location may list several files.
    """
    entries: list[dict] = []
    location: str | None = None
    for line in text.lstrip("﻿").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "location-name":
            location = value
        elif key == "mrf-url" and value.lower().startswith("http") and location:
            entries.append({"location_name": location, "mrf_url": value})
    return entries


def name_tokens(name: str) -> set[str]:
    return {t for t in fold(name).split() if t not in NAME_NOISE}


def name_similarity(a: str, b: str) -> float:
    """
    0..1 likeness of two hospital names, ignoring generic words: the better of
    shared-word overlap and character similarity of what remains.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return SequenceMatcher(None, fold(a), fold(b)).ratio() * 0.8
    overlap = len(ta & tb) / len(ta | tb)
    chars = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(overlap, chars)


def header_numbers(header: bytes | None) -> set[str] | None:
    """
    The numbers (street numbers, ZIP codes) in the part of a price file that
    describes the hospital: the first rows of a CSV, or a JSON file up to where
    its charges start. None when there is no header to read.
    """
    if not header or header[:2] == b"PK":  # nothing fetched, or a zip archive
        return None
    text = header.decode("utf-8", errors="replace")
    stripped = text.lstrip("\ufeff \r\n\t")
    if stripped.startswith("{"):
        text = stripped.split('"standard_charge_information"', 1)[0][:8192]
    else:
        text = "\n".join(stripped.splitlines()[:2])
    return set(_NUMBER_RE.findall(text))


def street_number(address: str) -> str:
    """Leading number of a street address: '27200 CALAROGA AVE' -> '27200'."""
    match = re.match(r"\s*(\d+)", address or "")
    return match.group(1) if match else ""


def match_ccn(
    location_name: str,
    numbers: set[str] | None,
    hospitals: dict[str, dict],
    state_zips: set[str],
) -> str | None:
    """
    CCN of the hospital a price file belongs to, or None.

    `numbers` come from the file's own description of the hospital
    (header_numbers), `hospitals` is {ccn: {name, address, zip}} and `state_zips`
    is every ZIP code in the state.

    The file's address decides, because names mislead: a health system has a
    St. Joseph Hospital in two cities, a St. John's that reads almost the same,
    and lists its hospitals in every state in one index. So a hospital is only
    considered if its ZIP code is in the file, and among those the choice is, in
    order: a near-identical name, the hospital at the file's street number, or
    a similar name. Sharing a ZIP code alone is not enough; two hospitals often do.

    Only when the file could not be read (numbers is None) does the name decide
    by itself, and then it must be near-identical to exactly one hospital.
    """
    def similarity(ccn: str) -> float:
        return name_similarity(location_name, hospitals[ccn]["name"])

    if numbers is None:
        same_name = [c for c in hospitals if similarity(c) >= MATCH_THRESHOLD_NAME_ONLY]
        return same_name[0] if len(same_name) == 1 else None

    at_zip = [c for c, h in hospitals.items() if h["zip"][:5] in numbers]
    at_address = [c for c in at_zip if street_number(hospitals[c]["address"]) in numbers]
    same_name = [c for c in at_zip if similarity(c) >= MATCH_THRESHOLD_NAME_ONLY]

    for candidates in ([c for c in same_name if c in at_address], same_name):
        if candidates:
            return max(candidates, key=similarity)
    # The name is some other hospital's, at an address the file doesn't give: a system
    # that prints one billing address in every hospital's file. Wrong prices are worse
    # than none, so leave it for mrf_overrides.csv.
    if any(similarity(c) >= MATCH_THRESHOLD_NAME_ONLY for c in hospitals if c not in at_zip):
        return None
    if at_address:
        return max(at_address, key=similarity)
    similar = [c for c in at_zip if similarity(c) >= MATCH_THRESHOLD_SAME_ZIP]
    return max(similar, key=similarity) if similar else None


def infer_format(url: str) -> str | None:
    """Infer file format from URL extension, ignoring query params."""
    path = url.lower().split("?")[0]
    for ext in ("csv", "json", "zip", "gz"):
        if path.endswith(f".{ext}"):
            return ext
    return None


def _read_csv(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def fetch_cms_hpt(client: RateLimitedClient, website: str) -> str | None:
    """The website's cms-hpt.txt, trying the host with and without 'www.'; None if it has none."""
    base = website.rstrip("/")
    alternate = base.replace("://www.", "://", 1) if "://www." in base else base.replace("://", "://www.", 1)
    for candidate in (base, alternate):
        try:
            response = client.get_once(f"{candidate}/cms-hpt.txt")
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.debug(f"No cms-hpt.txt at {candidate}: {exc}")
            continue
        if "mrf-url" in response.text.lower():
            return response.text
    return None


def fetch_header(client: RateLimitedClient, url: str) -> bytes | None:
    """The start of a price file, or None if it can't be fetched."""
    try:
        return client.peek(url)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.debug(f"Could not read the start of {url}: {exc}")
        return None


def run(state: str = "CA") -> int:
    """
    Discover price-file links for the state's hospitals and load them into hospital_mrf_links.

    Returns:
        Number of links loaded.
    """
    hospitals = {
        r["ccn"]: {"name": r["name"], "address": r["address"] or "", "zip": r["zip"] or ""}
        for r in execute_query("SELECT ccn, name, address, zip FROM hospitals WHERE state = %s", (state,))
    }
    state_zips = {r["name"] for r in execute_query("SELECT name FROM ca_places WHERE place_type = 'zip'")}
    websites: dict[str, int] = {}  # website -> hospitals registered to it
    for row in _read_csv(WEBSITES_FILE):
        if row["ccn"] in hospitals and row["website"]:
            websites[row["website"]] = websites.get(row["website"], 0) + 1

    logger.info(
        f"Looking for cms-hpt.txt on {len(websites)} websites covering "
        f"{sum(websites.values())} of {len(hospitals)} {state} hospitals"
    )

    def discover(website: str) -> list[tuple[dict, set[str] | None]] | None:
        """A website's index entries, each with the numbers from its file's header."""
        text = fetch_cms_hpt(client, website)
        if text is None:
            return None
        return [(e, header_numbers(fetch_header(client, e["mrf_url"]))) for e in parse_cms_hpt(text)]

    records: list[dict] = []
    unmatched = 0
    sites_without_index = []
    sites = sorted(websites)
    # Hundreds of small requests to different hosts: run them a few at a time, without
    # retries (a site with no index is expected, and the next run looks again).
    with RateLimitedClient(delay_ms=0, timeout=30.0) as client, ThreadPoolExecutor(max_workers=8) as pool:
        for website, found in zip(sites, pool.map(discover, sites), strict=True):
            if found is None:
                sites_without_index.append(website)
                continue
            for entry, numbers in found:
                ccn = match_ccn(entry["location_name"], numbers, hospitals, state_zips)
                if ccn is None:
                    unmatched += 1
                    logger.debug(f"No hospital matches {entry['location_name']!r} on {website}")
                    continue
                records.append({
                    "ccn": ccn,
                    "hospital_name": entry["location_name"][:300],
                    "state": state,
                    "machine_readable_url": entry["mrf_url"],
                    "source": "cms_hpt_txt",
                    "file_format": infer_format(entry["mrf_url"]),
                    "is_override": False,
                })

    if sites_without_index:
        sample = ", ".join(sites_without_index[:10])
        logger.warning(f"{len(sites_without_index)} websites served no cms-hpt.txt: {sample} ...")
    logger.info(f"Matched {len(records)} price files to hospitals; {unmatched} index entries matched no hospital")

    overrides = [
        {
            "ccn": row["ccn"],
            "hospital_name": row["hospital_name"] or hospitals[row["ccn"]]["name"],
            "state": state,
            "machine_readable_url": row["machine_readable_url"],
            "source": "manual",
            "file_format": infer_format(row["machine_readable_url"]),
            "is_override": True,
        }
        for row in _read_csv(OVERRIDES_FILE)
        if row["ccn"] in hospitals and row["machine_readable_url"]
    ]
    # The alias rules the price step applies, kept in a table so they can be queried.
    sync_payer_aliases()
    # A hospital with an override uses only its override.
    overridden = {r["ccn"] for r in overrides}
    records = [r for r in records if r["ccn"] not in overridden] + overrides

    if not records:
        logger.warning("No price-file links found")
        return 0

    count = upsert_batch(
        table="hospital_mrf_links",
        records=records,
        conflict_columns=["hospital_name", "machine_readable_url"],
        update_columns=["ccn", "source", "file_format", "is_override", "updated_at"],
    )
    # Links that are no longer listed (a hospital publishes a new file name each year) go away.
    current = [(r["hospital_name"], r["machine_readable_url"]) for r in records]
    removed = execute(
        "DELETE FROM hospital_mrf_links WHERE state = %s AND (hospital_name, machine_readable_url) NOT IN %s",
        (state, tuple(current)),
    )

    covered = len({r["ccn"] for r in records})
    logger.info(f"Loaded {count} price-file links for {covered} of {len(hospitals)} hospitals; {removed} stale removed")
    return count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Discover hospital price files from cms-hpt.txt")
    parser.add_argument("--state", default="CA", help="State abbreviation (default: CA)")
    args = parser.parse_args()
    run(state=args.state)
