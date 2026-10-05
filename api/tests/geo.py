"""Distance helper for building expected results from seed coordinates."""

import math


def miles_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance. PostGIS uses a spheroid, so compare with a tolerance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def within(rows: list[dict], lat: float, lng: float, miles: float) -> set[str]:
    return {r["ccn"] for r in rows if miles_between(lat, lng, r["lat"], r["lng"]) <= miles}
