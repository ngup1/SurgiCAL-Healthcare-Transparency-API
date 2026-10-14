"""Place lookups shared by the extractors: ZIP centre points and canonical city names."""

from __future__ import annotations

import unicodedata

from etl.common.db import execute, execute_query

# Spelled-out words that addresses and the gazetteer write differently.
_ABBREVIATIONS = {"saint": "st", "mount": "mt", "fort": "ft"}


def fold(name: str) -> str:
    """
    Lowercase, strip accents and punctuation, abbreviate: 'La Cañada Flintridge' ->
    'la canada flintridge', 'Saint Helena' and 'St. Helena' -> 'st helena'.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = "".join(ch if ch.isalnum() else " " for ch in ascii_name.lower()).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def point_ewkt(lat: float, lng: float) -> str:
    """PostGIS text form of a point, accepted by a GEOGRAPHY(Point, 4326) column."""
    return f"SRID=4326;POINT({lng} {lat})"


class PlaceIndex:
    """
    ca_places held in memory, for geocoding addresses to a ZIP centre point and
    for rewriting city names the way the API's city filter expects them.
    """

    def __init__(self, rows: list[dict]):
        self.zips: dict[str, tuple[float, float]] = {}
        self.cities: dict[str, tuple[str, float, float]] = {}
        for row in rows:
            if row["place_type"] == "zip":
                self.zips[row["name"]] = (row["lat"], row["lng"])
            elif row["place_type"] == "city":
                self.cities[fold(row["name"])] = (row["name"], row["lat"], row["lng"])

    @classmethod
    def load(cls) -> PlaceIndex:
        return cls(
            execute_query(
                """
                SELECT place_type, name, ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
                FROM ca_places
                WHERE place_type IN ('zip', 'city')
                """
            )
        )

    def city_name(self, raw: str | None) -> str | None:
        """
        The gazetteer's spelling of a city ('LOS ANGELES' -> 'Los Angeles',
        'MCKINLEYVILLE' -> 'McKinleyville'), or the input title-cased when the
        gazetteer doesn't have it.
        """
        if not raw or not raw.strip():
            return None
        match = self.cities.get(fold(raw))
        return match[0] if match else raw.strip().title()

    def locate(self, zip_code: str | None, city: str | None = None) -> str | None:
        """Point for a ZIP's centre, falling back to the city's; None if neither is known."""
        point = self.zips.get((zip_code or "").strip()[:5])
        if point is None and city:
            match = self.cities.get(fold(city))
            point = (match[1], match[2]) if match else None
        return point_ewkt(*point) if point else None


def add_postal_cities(table: str, state: str, min_rows: int = 1) -> int:
    """
    Add city names used in `table` (hospitals or providers) that the gazetteer lacks.

    Addresses use postal city names, which include neighbourhoods the Census does
    not list as places (Van Nuys, La Jolla, Hollywood). Without a ca_places row the
    API can't find those rows by city or county, so each such name becomes a city at
    the ZIP centre point most of its rows share. `min_rows` keeps out one-off typos.

    Returns the number of places added.
    """
    # `table` is a fixed name from the calling step, never input.
    return execute(
        f"""
        INSERT INTO ca_places (place_type, name, county, location)
        SELECT DISTINCT ON (city) 'city', city, county, location
        FROM (
          SELECT t.city, z.county, z.location, count(*) AS n,
                 sum(count(*)) OVER (PARTITION BY t.city) AS city_rows
          FROM {table} t
          JOIN ca_places z ON z.place_type = 'zip' AND ST_DWithin(z.location, t.location, 1)
          WHERE t.state = %s AND t.city <> ''
            AND NOT EXISTS (
              SELECT 1 FROM ca_places c WHERE c.place_type = 'city' AND lower(c.name) = lower(t.city)
            )
          GROUP BY t.city, z.county, z.location
        ) candidates
        WHERE city_rows >= %s
        ORDER BY city, n DESC
        ON CONFLICT (place_type, name) DO NOTHING
        """,
        (state, min_rows),
    )
