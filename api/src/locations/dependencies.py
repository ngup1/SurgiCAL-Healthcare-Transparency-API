"""`valid_location`: the city / county / zip / radius parameters shared by list endpoints."""

import re

from fastapi import Depends, Query
from psycopg import AsyncConnection

from src.database import get_db
from src.locations import service
from src.locations.constants import (
    CA_ZIP_RANGE,
    COVERED_STATE,
    DEFAULT_ZIP_RADIUS_MILES,
    MAX_RADIUS_MILES,
    ZIP_PATTERN,
)
from src.locations.exceptions import InvalidLocationQuery, LocationNotFound, LocationOutsideCoverage
from src.locations.schemas import LocationFilter


async def valid_location(
    state: str = Query(COVERED_STATE, description="Only `CA` is covered"),
    city: str | None = Query(None, min_length=2, max_length=80, description="California city, e.g. `Pasadena`"),
    county: str | None = Query(
        None, min_length=2, max_length=80, description="California county, e.g. `Los Angeles` or `Orange County`"
    ),
    zip: str | None = Query(
        None,
        pattern=ZIP_PATTERN,
        description=f"California ZIP code; searches within `radius_miles` (default {DEFAULT_ZIP_RADIUS_MILES})",
    ),
    radius_miles: float | None = Query(
        None, gt=0, le=MAX_RADIUS_MILES, description="With `city` or `zip`: search this far from its center"
    ),
    conn: AsyncConnection = Depends(get_db),
) -> LocationFilter:
    """
    Resolve location parameters against California places. Raises a 422 with a specific
    `code` for places outside California, unknown places, or conflicting parameters.
    """
    if state.strip().upper() != COVERED_STATE:
        raise LocationOutsideCoverage("state", state, "SurgiCAL currently covers California only.")

    given = {name: value for name, value in (("city", city), ("county", county), ("zip", zip)) if value}
    if len(given) > 1:
        raise InvalidLocationQuery(list(given)[1], list(given.values())[1], "Use only one of city, county or zip.")
    if radius_miles is not None and not (city or zip):
        raise InvalidLocationQuery("radius_miles", radius_miles, "radius_miles needs a city or zip to measure from.")

    if zip:
        if not CA_ZIP_RANGE[0] <= int(zip) <= CA_ZIP_RANGE[1]:
            raise LocationOutsideCoverage(
                "zip", zip, f"ZIP {zip} is outside California; SurgiCAL covers California only."
            )
        place = await service.find_place(conn, "zip", zip)
        if place is None:
            raise LocationNotFound("zip", zip, [])
        return LocationFilter(lat=place["lat"], lng=place["lng"], radius_miles=radius_miles or DEFAULT_ZIP_RADIUS_MILES)

    if city:
        place = await service.find_place(conn, "city", city.strip())
        if place is None:
            raise LocationNotFound("city", city, await service.suggest_places(conn, "city", city.strip()))
        if radius_miles is not None:
            return LocationFilter(lat=place["lat"], lng=place["lng"], radius_miles=radius_miles)
        return LocationFilter(city=place["name"])

    if county:
        name = re.sub(r"\s+county$", "", county.strip(), flags=re.IGNORECASE)
        place = await service.find_place(conn, "county", name)
        if place is None:
            raise LocationNotFound("county", county, await service.suggest_places(conn, "county", name))
        return LocationFilter(county=place["name"])

    return LocationFilter()
