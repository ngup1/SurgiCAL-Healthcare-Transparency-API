import pytest
from conftest import BAYSHORE_CCN, DOWNTOWN_LA, NO_QUALITY_CCN, UNKNOWN_CCN
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


async def test_list_within_radius_sorted_by_distance(client, seed):
    rows = (await client.get("/hospitals", params={**DOWNTOWN_LA, "radius_miles": 25})).json()
    ccns = {r["ccn"] for r in rows}
    # Tolerance band: PostGIS measures on a spheroid, the helper on a sphere.
    assert within(seed["hospitals"], **DOWNTOWN_LA, miles=24.5) <= ccns
    assert ccns <= within(seed["hospitals"], **DOWNTOWN_LA, miles=25.5)
    distances = [r["distance_miles"] for r in rows]
    assert distances == sorted(distances)
    assert all(d <= 25 for d in distances)


async def test_list_other_state_returns_empty(client):
    # Current behavior; Phase 5 rejects non-CA states with a 422.
    response = await client.get("/hospitals", params={"state": "NV"})
    assert response.status_code == 200
    assert response.json() == []


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


async def test_hospital_providers_unknown_ccn_returns_empty(client):
    # Current behavior; Phase 5 changes this to a 404.
    response = await client.get(f"/hospitals/{UNKNOWN_CCN}/providers")
    assert response.status_code == 200
    assert response.json() == []
