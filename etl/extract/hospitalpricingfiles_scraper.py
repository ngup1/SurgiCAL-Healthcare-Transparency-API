"""
HospitalPricingFiles.org MRF Link Scraper

Scrapes hospitalpricingfiles.org to discover hospital MRF URLs for a given
state. Uses Playwright for browser automation + seleniumbase for CAPTCHA
solving.

The TPAFS GitHub CSV (previously used) has not been updated since November 2022.
This scraper pulls from a live, actively maintained source instead.

Populates the `hospital_mrf_links` table with source='hospitalpricingfiles'.

Requirements:
    pip install playwright seleniumbase
    playwright install chromium

Usage:
    python -m etl.extract.hospitalpricingfiles_scraper --state CA
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from etl.common.config import get_logger
from etl.common.db import execute_query, upsert_batch

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_format(url: str) -> str | None:
    """Infer file format from URL extension, ignoring query params."""
    path = url.lower().split("?")[0]
    for ext in ("csv", "json", "zip"):
        if path.endswith(f".{ext}"):
            return ext
    return None


def _known_ccns_by_name(state: str) -> dict[str, str]:
    """
    Return a {normalized_name -> ccn} map for hospitals in this state.
    Used for best-effort CCN matching when upserting MRF links.
    """
    rows = execute_query(
        "SELECT ccn, name FROM hospitals WHERE state = %s AND ccn IS NOT NULL",
        (state,),
    )
    return {r["name"].strip().lower(): r["ccn"] for r in rows}


def _match_ccn(hospital_name: str, ccn_map: dict[str, str]) -> str | None:
    """Exact normalized-name match against the CCN map. Returns None if no match."""
    return ccn_map.get(hospital_name.strip().lower())


# ---------------------------------------------------------------------------
# API response parser
# ---------------------------------------------------------------------------

def _extract_hospitals_from_api(data: Any) -> list[dict]:
    """
    Extract hospital name + MRF URL from a hospitalpricingfiles.org API response.

    Handles both direct array responses and wrapped object responses.
    Prefers standard-charges files when multiple files are listed per hospital.
    """
    facilities: list = data if isinstance(data, list) else data.get(
        "facilities", data.get("data", data.get("results", []))
    )

    hospitals = []
    for item in facilities:
        if not isinstance(item, dict):
            continue

        # Hospital name
        text = str(item)
        match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
        name = item.get("name") or item.get("facility_name") or (match.group(0) if match else "Unknown")

        # MRF URL: prefer standard-charges files from the files array
        mrf_url: str | None = None
        for file_obj in item.get("files", []):
            if not isinstance(file_obj, dict):
                continue
            file_url = file_obj.get("url")
            suffix = file_obj.get("filesuffix", "").lower()
            filename = file_obj.get("filename", "").lower()
            if not file_url or suffix not in ("csv", "json", "zip"):
                continue
            is_pricing = any(kw in filename for kw in ("standard", "charge", "price", "mrf"))
            if is_pricing or mrf_url is None:
                mrf_url = file_url
                if is_pricing:
                    break

        # Fallback: regex scan the string representation
        if not mrf_url:
            matches = re.findall(r"(https?://[^\s<>\"]+\.(?:csv|json|zip))", text)
            mrf_url = matches[0] if matches else None

        hospitals.append({"name": name, "city": item.get("city", ""), "mrf_url": mrf_url})

    return hospitals


# ---------------------------------------------------------------------------
# Table-scrape fallback
# ---------------------------------------------------------------------------

async def _scrape_table(page) -> list[dict]:
    """Fallback: parse visible table rows when the API response was empty."""
    result = []
    rows = await page.locator("table tbody tr, .hospital-row").all()
    for row in rows:
        try:
            cells = await row.locator("td").all_text_contents()
            if cells:
                result.append({
                    "name": cells[0].strip() if cells else "Unknown",
                    "city": cells[1].strip() if len(cells) > 1 else "",
                    "mrf_url": None,
                })
        except Exception:
            continue
    return result


# ---------------------------------------------------------------------------
# Core scrape (async)
# ---------------------------------------------------------------------------

async def _scrape_state(state: str, browser, driver) -> list[dict]:
    """
    Navigate to hospitalpricingfiles.org, solve CAPTCHA, click the state on
    the SVG map, and intercept the API response to collect hospital MRF URLs.

    Returns list of {name, city, mrf_url} dicts.
    """
    context = browser.contexts[0]
    page = context.pages[0]

    hospitals_data: list[dict] = []
    api_urls_seen: list[str] = []

    async def handle_response(response):
        url = response.url.lower()
        if any(skip in url for skip in ("google-analytics", "analytics", "tracking", "collect", "gtm")):
            return
        if any(kw in url for kw in ("search", "facility", "hospital", "api")):
            api_urls_seen.append(response.url)
            if "search" in url or "facility" in url:
                try:
                    data = await response.json()
                    extracted = _extract_hospitals_from_api(data)
                    logger.info(f"Extracted {len(extracted)} hospitals from {response.url}")
                    hospitals_data.extend(extracted)
                except Exception as e:
                    logger.debug(f"Could not parse JSON from {response.url}: {e}")

    page.on("response", handle_response)

    logger.info("Navigating to hospitalpricingfiles.org")
    await page.goto("https://hospitalpricingfiles.org/")
    await driver.sleep(3)

    logger.info("Solving CAPTCHA...")
    await driver.solve_captcha()
    await driver.sleep(3)

    logger.info("Waiting for SVG state map...")
    await page.wait_for_selector("svg.us-state-map", timeout=30000)
    await page.wait_for_selector("svg.us-state-map g.outlines path.state", timeout=30000)

    state_path = page.locator(f"svg.us-state-map g.outlines path.{state}.state")
    await state_path.wait_for(state="visible", timeout=10000)
    logger.info(f"Clicking state: {state}")
    await state_path.hover()
    await state_path.click()

    await asyncio.sleep(3)
    await page.wait_for_load_state("networkidle")

    logger.info(f"API responses seen: {len(api_urls_seen)} | hospitals found: {len(hospitals_data)}")

    if not hospitals_data:
        logger.warning("No hospitals from API intercept — falling back to table scrape")
        hospitals_data = await _scrape_table(page)
        logger.info(f"Table scrape found: {len(hospitals_data)} hospitals")

    if not hospitals_data:
        logger.error(f"No hospitals found for {state}. Saving debug screenshot.")
        await page.screenshot(path=f"debug_no_hospitals_{state}.png")

    return hospitals_data


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------

async def _scrape_and_load(state: str) -> int:
    try:
        from seleniumbase import cdp_driver
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ImportError(
            f"Missing dependency: {exc}. "
            "Install with: pip install playwright seleniumbase && playwright install chromium"
        ) from exc

    driver = await cdp_driver.start_async()
    endpoint_url = driver.get_endpoint_url()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(endpoint_url)
        try:
            hospitals = await _scrape_state(state, browser, driver)
        finally:
            await browser.close()

    if not hospitals:
        logger.warning(f"No hospitals scraped for state={state}")
        return 0

    logger.info(f"Building DB records for {len(hospitals)} scraped hospitals...")
    ccn_map = _known_ccns_by_name(state)

    records = []
    skipped = 0
    for hosp in hospitals:
        mrf_url = (hosp.get("mrf_url") or "").strip()
        name = (hosp.get("name") or "").strip()
        if not mrf_url or not name:
            skipped += 1
            continue
        records.append({
            "ccn":                  _match_ccn(name, ccn_map),
            "hospital_name":        name,
            "state":                state.upper(),
            "machine_readable_url": mrf_url,
            "source":               "hospitalpricingfiles",
            "url_status":           "unknown",
            "file_format":          _infer_format(mrf_url),
        })

    if skipped:
        logger.warning(f"Skipped {skipped} hospitals with no MRF URL")

    if not records:
        logger.warning("No valid records to insert after filtering")
        return 0

    count = upsert_batch(
        table="hospital_mrf_links",
        records=records,
        conflict_columns=["hospital_name", "machine_readable_url"],
        update_columns=["url_status", "file_format", "source"],
    )
    logger.info(f"Loaded {count} MRF links for state={state} (source=hospitalpricingfiles)")
    return count


# ---------------------------------------------------------------------------
# Sync entry point (called by run_pipeline.py)
# ---------------------------------------------------------------------------

def run(state: str = "CA") -> int:
    """
    Scrape MRF links from hospitalpricingfiles.org and load into hospital_mrf_links.

    Args:
        state: Two-letter state abbreviation (default: CA).

    Returns:
        Number of records upserted.
    """
    return asyncio.run(_scrape_and_load(state))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape hospital MRF links from hospitalpricingfiles.org"
    )
    parser.add_argument("--state", default="CA", help="State abbreviation (default: CA)")
    args = parser.parse_args()

    print(f"Scraping MRF links for state={args.state}...")
    count = run(state=args.state)
    print(f"Done: {count} records loaded")
