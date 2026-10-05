from conftest import PROVIDER_NPI, UNKNOWN_NPI


async def test_list_defaults_to_first_50_sorted_by_last_name(client):
    rows = (await client.get("/providers")).json()
    assert len(rows) == 50
    last_names = [r["last_name"] for r in rows]
    assert last_names == sorted(last_names)


async def test_list_all_providers(client, seed):
    rows = (await client.get("/providers", params={"limit": 200})).json()
    assert {r["npi"] for r in rows} == {p["npi"] for p in seed["providers"]}


async def test_specialty_filter_is_partial_and_case_insensitive(client, seed):
    expected = {p["npi"] for p in seed["providers"] if "ortho" in p["specialty"].lower()}
    rows = (await client.get("/providers", params={"specialty": "ORTHO", "limit": 200})).json()
    assert expected
    assert {r["npi"] for r in rows} == expected


async def test_city_filter_is_partial(client, seed):
    expected = {p["npi"] for p in seed["providers"] if "san" in p["city"].lower()}
    rows = (await client.get("/providers", params={"city": "san", "limit": 200})).json()
    assert {r["npi"] for r in rows} == expected


async def test_list_within_radius_sorted_by_distance(client):
    rows = (await client.get("/providers", params={"lat": 37.7749, "lng": -122.4194, "radius_miles": 20})).json()
    assert rows
    distances = [r["distance_miles"] for r in rows]
    assert distances == sorted(distances)
    assert all(d <= 20 for d in distances)


async def test_detail_includes_metrics_and_affiliations(client, seed):
    expected = next(p for p in seed["providers"] if p["npi"] == PROVIDER_NPI)
    metrics = next(m for m in seed["provider_metrics"] if m["npi"] == PROVIDER_NPI)
    affiliations = {a["ccn"]: a["is_primary"] for a in seed["provider_affiliations"] if a["npi"] == PROVIDER_NPI}

    response = await client.get(f"/providers/{PROVIDER_NPI}")
    assert response.status_code == 200
    body = response.json()
    assert (body["first_name"], body["last_name"]) == (expected["first_name"], expected["last_name"])
    assert body["specialty"] == expected["specialty"]
    assert body["patient_rating"] == metrics["patient_rating"]
    assert body["patient_demographics"] == metrics["patient_demographics"]
    assert {a["ccn"]: a["is_primary"] for a in body["affiliations"]} == affiliations
    assert body["affiliations"][0]["is_primary"] is True


async def test_detail_without_metrics_returns_nulls(client, seed):
    with_metrics = {m["npi"] for m in seed["provider_metrics"]}
    npi = next(p["npi"] for p in seed["providers"] if p["npi"] not in with_metrics)
    body = (await client.get(f"/providers/{npi}")).json()
    assert body["patient_rating"] is None
    assert body["wrvu_estimate"] is None


async def test_detail_unknown_npi_is_404(client):
    response = await client.get(f"/providers/{UNKNOWN_NPI}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Provider not found"}
