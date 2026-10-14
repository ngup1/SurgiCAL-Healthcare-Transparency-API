"""
Census Gazetteer Extractor

Loads the place gazetteer the API uses for city / county / ZIP search, and that
the hospital and provider steps use to geocode addresses to a ZIP centre point.

Sources (US Census Bureau):
- Gazetteer files: places, counties and ZIP Code Tabulation Areas with centre points
- Place-by-county codes and the ZCTA-to-county relationship file, for each place's county

Populates the `ca_places` table.
"""
from __future__ import annotations

import csv
import io
import zipfile
from math import cos, radians

from etl.common.config import get_logger
from etl.common.db import replace_rows
from etl.common.http import RateLimitedClient
from etl.common.places import add_postal_cities, point_ewkt

logger = get_logger(__name__)

GAZETTEER_YEAR = 2024
GAZETTEER_URL = f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{GAZETTEER_YEAR}_Gazetteer"
PLACES_URL = GAZETTEER_URL + "/{year}_gaz_place_{fips}.txt"
COUNTIES_URL = GAZETTEER_URL + "/{year}_gaz_counties_{fips}.txt"
ZCTA_URL = GAZETTEER_URL + "/{year}_Gaz_zcta_national.zip"
PLACE_COUNTY_URL = (
    "https://www2.census.gov/geo/docs/reference/codes2020/place_by_cou/st{fips}_{state}_place_by_county2020.txt"
)
ZCTA_COUNTY_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"

STATE_FIPS = {"CA": "06"}

# Census appends the legal type to a place's name: "Hayward city", "Acampo CDP".
PLACE_SUFFIXES = (" city and county", " city", " town", " CDP", " village", " municipality")
# Lower sorts first when two places share a name: incorporated, then census-designated.
FUNCSTAT_RANK = {"A": 0, "S": 1}


def _rows(text: str, delimiter: str) -> list[dict]:
    """Parse a delimited Census file; header names and values come padded with spaces."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=delimiter)
    header = [h.strip() for h in next(reader)]
    return [dict(zip(header, (v.strip() for v in row), strict=False)) for row in reader if row]


def strip_place_suffix(name: str) -> str:
    for suffix in PLACE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Squared flat-earth distance; only used to pick the nearer of a few counties."""
    return (a[0] - b[0]) ** 2 + ((a[1] - b[1]) * cos(radians(a[0]))) ** 2


def build_counties(county_rows: list[dict]) -> dict[str, dict]:
    """County FIPS (5 digits) -> {name, point}."""
    return {
        r["GEOID"]: {
            "name": r["NAME"].removesuffix(" County"),
            "point": (float(r["INTPTLAT"]), float(r["INTPTLONG"])),
        }
        for r in county_rows
    }


def build_city_records(place_rows: list[dict], place_county_rows: list[dict], counties: dict[str, dict]) -> list[dict]:
    """One record per city name, with the county it is in (the nearest, if it spans several)."""
    counties_of_place: dict[str, list[str]] = {}
    for r in place_county_rows:
        counties_of_place.setdefault(r["STATEFP"] + r["PLACEFP"], []).append(r["STATEFP"] + r["COUNTYFP"])

    by_name: dict[str, tuple[tuple, dict]] = {}
    for r in place_rows:
        point = (float(r["INTPTLAT"]), float(r["INTPTLONG"]))
        candidates = [c for c in counties_of_place.get(r["GEOID"], []) if c in counties]
        if not candidates:
            continue
        county = min(candidates, key=lambda c: _distance_sq(point, counties[c]["point"]))
        name = strip_place_suffix(r["NAME"])
        # Same name twice (a city and a CDP elsewhere): keep the incorporated, then the larger.
        rank = (FUNCSTAT_RANK.get(r.get("FUNCSTAT", ""), 2), -int(r.get("ALAND") or 0))
        if name not in by_name or rank < by_name[name][0]:
            by_name[name] = (
                rank,
                {
                    "place_type": "city",
                    "name": name,
                    "county": counties[county]["name"],
                    "location": point_ewkt(*point),
                },
            )
    return [record for _, record in by_name.values()]


def build_zip_records(zcta_rows: list[dict], zcta_county_rows: list[dict], counties: dict[str, dict]) -> list[dict]:
    """One record per ZIP whose largest part lies in one of `counties`."""
    best: dict[str, tuple[int, str]] = {}
    for r in zcta_county_rows:
        zcta, county = r.get("GEOID_ZCTA5_20", ""), r.get("GEOID_COUNTY_20", "")
        if not zcta or not county:
            continue
        area = int(r.get("AREALAND_PART") or 0)
        if zcta not in best or area > best[zcta][0]:
            best[zcta] = (area, county)

    records = []
    for r in zcta_rows:
        _, county = best.get(r["GEOID"], (0, ""))
        if county not in counties:
            continue
        point = (float(r["INTPTLAT"]), float(r["INTPTLONG"]))
        records.append({
            "place_type": "zip",
            "name": r["GEOID"],
            "county": counties[county]["name"],
            "location": point_ewkt(*point),
        })
    return records


def run(state: str = "CA") -> int:
    """
    Load cities, counties and ZIP codes for the state into ca_places, replacing what was there.

    Returns:
        Number of places loaded.
    """
    fips = STATE_FIPS.get(state)
    if fips is None:
        raise ValueError(f"No FIPS code configured for state {state!r}; add it to STATE_FIPS")
    fmt = {"year": GAZETTEER_YEAR, "fips": fips, "state": state.lower()}

    logger.info(f"Starting Census gazetteer extraction for state={state}")
    with RateLimitedClient(timeout=120.0) as client:
        county_rows = _rows(client.get(COUNTIES_URL.format(**fmt)).text, "\t")
        place_rows = _rows(client.get(PLACES_URL.format(**fmt)).text, "\t")
        place_county_rows = _rows(client.get(PLACE_COUNTY_URL.format(**fmt)).text, "|")
        zcta_county_rows = _rows(client.get(ZCTA_COUNTY_URL).text, "|")
        with zipfile.ZipFile(io.BytesIO(client.get(ZCTA_URL.format(**fmt)).content)) as zf:
            zcta_rows = _rows(zf.read(zf.namelist()[0]).decode("utf-8"), "\t")

    counties = build_counties(county_rows)
    records = [
        {"place_type": "county", "name": c["name"], "county": c["name"], "location": point_ewkt(*c["point"])}
        for c in counties.values()
    ]
    records += build_city_records(place_rows, place_county_rows, counties)
    records += build_zip_records(zcta_rows, zcta_county_rows, counties)

    if len(records) < 100:
        raise RuntimeError(f"Gazetteer produced only {len(records)} places; refusing to replace ca_places")

    count = replace_rows("ca_places", records, "TRUE", ())
    # Replacing the table dropped the postal city names earlier loads added; put them back.
    count += add_postal_cities("hospitals", state) + add_postal_cities("providers", state, min_rows=5)
    logger.info(f"Loaded {count} places for state={state}")
    return count


if __name__ == "__main__":
    run()
