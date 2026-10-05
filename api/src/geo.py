"""PostGIS helpers for "within N miles" filters and distance columns."""

METERS_PER_MILE = 1609.34


def spatial_where(
    lat: float | None, lng: float | None, radius_miles: float, column: str = "location"
) -> tuple[str, list]:
    """WHERE fragment limiting `column` to radius_miles of the point ("" when no point)."""
    if lat is None or lng is None:
        return "", []
    return f"ST_DWithin({column}, ST_MakePoint(%s, %s)::geography, %s)", [lng, lat, radius_miles * METERS_PER_MILE]


def distance_select(lat: float | None, lng: float | None, column: str = "location") -> tuple[str, list]:
    """SELECT expression for distance_miles from the point (NULL when no point)."""
    if lat is None or lng is None:
        return "NULL AS distance_miles", []
    return (
        f"ROUND((ST_Distance({column}, ST_MakePoint(%s, %s)::geography) / {METERS_PER_MILE})::numeric, 1)"
        " AS distance_miles",
        [lng, lat],
    )
