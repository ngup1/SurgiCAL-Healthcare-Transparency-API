import pytest
from conftest import BAYSHORE_CCN, NO_QUALITY_CCN, UNKNOWN_CCN
from geo import within

SUMMARY_FIELDS = {
    "ccn",
    "name",
    "address",
    "city",
    "state",
    "zip",
    "phone",
    "hospital_type",
    "ownership",
    "emergency_services",
    "lat",
    "lng",
    "distance_miles",
}


async def test_list_returns_all_california_hospitals_sorted_by_name(client, seed):
    response = await client.get("/hospitals")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == len(seed["hospitals"])
    assert set(rows[0]) == SUMMARY_FIELDS
    assert all(r["state"] == "CA" for r in rows)
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    assert all(r["distance_miles"] is None for r in rows)


async def test_list_pagination_slices_the_full_list(client):
    full = (await client.get("/hospitals")).json()
    page = (await client.get("/hospitals", params={"limit": 5, "offset": 5})).json()
    assert [r["ccn"] for r in page] == [r["ccn"] for r in full[5:10]]


async def test_list_reports_total_count(client, seed):
    response = await client.get("/hospitals", params={"limit": 5})
    assert len(response.json()) == 5
    assert response.headers["X-Total-Count"] == str(len(seed["hospitals"]))


async def test_city_filter(client, seed):
    expected = {h["ccn"] for h in seed["hospitals"] if h["city"] == "Los Angeles"}
    response = await client.get("/hospitals", params={"city": "los angeles"})  # case-insensitive
    assert {r["ccn"] for r in response.json()} == expected
    assert response.headers["X-Total-Count"] == str(len(expected))


async def test_city_with_radius_sorted_by_distance(client, seed):
    center = next(p for p in seed["ca_places"] if p["place_type"] == "city" and p["name"] == "Pasadena")
    rows = (await client.get("/hospitals", params={"city": "Pasadena", "radius_miles": 15})).json()
    ccns = {r["ccn"] for r in rows}
    # Tolerance band: PostGIS measures on a spheroid, the helper on a sphere.
    assert within(seed["hospitals"], center["lat"], center["lng"], miles=14.5) <= ccns
    assert ccns <= within(seed["hospitals"], center["lat"], center["lng"], miles=15.5)
    distances = [r["distance_miles"] for r in rows]
    assert distances == sorted(distances)
    assert all(d <= 15 for d in distances)


async def test_county_filter(client, seed):
    county_cities = {p["name"] for p in seed["ca_places"] if p["place_type"] == "city" and p["county"] == "Orange"}
    expected = {h["ccn"] for h in seed["hospitals"] if h["city"] in county_cities}
    rows = (await client.get("/hospitals", params={"county": "Orange County"})).json()
    assert expected
    assert {r["ccn"] for r in rows} == expected


async def test_detail_includes_quality_metrics(client, seed):
    expected = next(h for h in seed["hospitals"] if h["ccn"] == BAYSHORE_CCN)
    quality = next(q for q in seed["hospital_quality"] if q["ccn"] == BAYSHORE_CCN)
    response = await client.get(f"/hospitals/{BAYSHORE_CCN}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == expected["name"]
    assert body["lat"] == pytest.approx(expected["lat"])
    assert body["lng"] == pytest.approx(expected["lng"])
    assert body["overall_stars"] == quality["overall_stars"]
    assert body["hai_sirs"] == pytest.approx(quality["hai_sirs"])
    assert body["measure_period"] == quality["measure_period"]


async def test_detail_without_quality_row_returns_nulls(client):
    body = (await client.get(f"/hospitals/{NO_QUALITY_CCN}")).json()
    assert body["name"] == "High Sierra Community Hospital"
    assert body["overall_stars"] is None
    assert body["mortality_group"] is None


async def test_detail_unknown_ccn_is_404(client):
    response = await client.get(f"/hospitals/{UNKNOWN_CCN}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Hospital not found"}


async def test_hospital_providers_match_affiliations(client, seed):
    expected = {a["npi"]: a["is_primary"] for a in seed["provider_affiliations"] if a["ccn"] == BAYSHORE_CCN}
    rows = (await client.get(f"/hospitals/{BAYSHORE_CCN}/providers", params={"limit": 200})).json()
    assert {r["npi"]: r["is_primary"] for r in rows} == expected
    # Busiest first; providers without metrics last.
    wrvus = [r["wrvu_estimate"] for r in rows]
    known = [w for w in wrvus if w is not None]
    assert known == sorted(known, reverse=True)
    assert wrvus[: len(known)] == known


async def test_hospital_providers_unknown_ccn_is_404(client):
    response = await client.get(f"/hospitals/{UNKNOWN_CCN}/providers")
    assert response.status_code == 404
    assert response.json() == {"detail": "Hospital not found"}
