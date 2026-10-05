"""Location filters (city / county / zip / radius) and California-only coverage (DEPLOYMENT_PLAN.md §3a)."""

import pytest


async def location_error(client, params, path="/hospitals"):
    response = await client.get(path, params=params)
    assert response.status_code == 422
    return response.json()


@pytest.mark.parametrize("path", ["/hospitals", "/providers", "/prices?cpt=27447"])
async def test_other_states_are_outside_coverage(client, path):
    body = await location_error(client, {"state": "NV"}, path)
    assert body["code"] == "location_outside_coverage"
    assert body["detail"] == "SurgiCAL currently covers California only."
    assert body["errors"][0]["field"] == "state"


async def test_state_is_case_insensitive(client):
    assert (await client.get("/hospitals", params={"state": "ca"})).status_code == 200


async def test_out_of_state_zip_is_detected_without_a_lookup(client):
    body = await location_error(client, {"zip": "89502"})
    assert body["code"] == "location_outside_coverage"
    assert body["errors"][0] == {
        "field": "zip",
        "location": "query",
        "message": "ZIP 89502 is outside California; SurgiCAL covers California only.",
        "input": "89502",
    }


async def test_unknown_city_says_not_in_coverage(client):
    body = await location_error(client, {"city": "Reno"})
    assert body["code"] == "location_not_found"
    assert body["detail"] == "'Reno' is not a California city in our coverage area."
    assert body["suggestions"] == []


async def test_misspelled_city_suggests_the_right_one(client):
    body = await location_error(client, {"city": "Pasedena"})
    assert body["code"] == "location_not_found"
    assert body["suggestions"][0] == "Pasadena"
    assert "Did you mean: Pasadena" in body["detail"]


async def test_misspelled_county_suggests_the_right_one(client):
    body = await location_error(client, {"county": "Los Angelas"})
    assert body["suggestions"][0] == "Los Angeles"


async def test_nevada_county_is_in_california(client, seed):
    rows = (await client.get("/hospitals", params={"county": "Nevada"})).json()
    assert [r["name"] for r in rows] == ["High Sierra Community Hospital"]


async def test_known_place_without_hospitals_is_empty_not_an_error(client):
    response = await client.get("/hospitals", params={"city": "Modesto"})
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_only_one_of_city_county_zip(client):
    body = await location_error(client, {"city": "Pasadena", "county": "Los Angeles"})
    assert body["code"] == "invalid_location_query"
    assert body["detail"] == "Use only one of city, county or zip."


async def test_radius_needs_a_city_or_zip(client):
    for params in ({"radius_miles": 10}, {"county": "Orange", "radius_miles": 10}):
        body = await location_error(client, params)
        assert body["code"] == "invalid_location_query"
        assert body["errors"][0]["field"] == "radius_miles"


async def test_zip_defaults_to_a_10_mile_radius(client):
    rows = (await client.get("/hospitals", params={"zip": "94107"})).json()
    assert rows
    assert all(r["distance_miles"] <= 10 for r in rows)
    assert rows[0]["ccn"] == "050801"  # Bayshore is in 94107


async def test_location_error_shape_matches_validation_errors(client):
    body = await location_error(client, {"city": "Pasedena"})
    assert set(body) == {"code", "detail", "errors", "suggestions"}
    assert set(body["errors"][0]) == {"field", "location", "message", "input"}


# --- /places ---


async def test_places_prefix_matches_first(client):
    rows = (await client.get("/places", params={"q": "san"})).json()
    assert rows
    assert all(r["name"].lower().startswith("san") for r in rows[:5])


async def test_places_filter_by_type(client, seed):
    rows = (await client.get("/places", params={"q": "los angeles", "type": "county"})).json()
    assert rows == [{"name": "Los Angeles", "type": "county", "county": "Los Angeles"}]


async def test_places_finds_both_city_and_county(client):
    rows = (await client.get("/places", params={"q": "los angeles"})).json()
    assert {(r["name"], r["type"]) for r in rows[:2]} == {("Los Angeles", "city"), ("Los Angeles", "county")}


async def test_every_place_works_as_a_filter(client, seed):
    for place in seed["ca_places"]:
        params = {place["place_type"]: place["name"]}
        response = await client.get("/hospitals", params=params)
        assert response.status_code == 200, f"{params}: {response.text[:200]}"
