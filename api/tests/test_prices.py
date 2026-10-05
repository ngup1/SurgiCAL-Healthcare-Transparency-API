from conftest import BAYSHORE_CCN, KNEE_CPT, UNKNOWN_CCN

SECOND_CCN = "050802"


def seed_prices(seed, cpt, ccns=None):
    return [p for p in seed["prices"] if p["cpt"] == cpt and (ccns is None or p["ccn"] in ccns)]


async def test_prices_for_procedure_sorted_cheapest_first(client, seed):
    rows = (await client.get("/prices", params={"cpt": KNEE_CPT, "limit": 200})).json()
    assert len(rows) == len(seed_prices(seed, KNEE_CPT))
    assert {r["procedure_name"] for r in rows} == {"Total knee replacement (arthroplasty)"}
    rates = [r["negotiated_rate"] for r in rows]
    assert rates == sorted(rates)


async def test_prices_include_hospital_quality(client, seed):
    stars = {q["ccn"]: q["overall_stars"] for q in seed["hospital_quality"]}
    rows = (await client.get("/prices", params={"cpt": KNEE_CPT, "limit": 200})).json()
    assert all(r["overall_stars"] == stars.get(r["ccn"]) for r in rows)


async def test_payer_filter_is_partial_and_case_insensitive(client, seed):
    expected = [p for p in seed_prices(seed, KNEE_CPT) if "aetna" in p["payer"].lower()]
    rows = (await client.get("/prices", params={"cpt": KNEE_CPT, "payer": "aetna", "limit": 200})).json()
    assert expected
    assert len(rows) == len(expected)
    assert all("Aetna" in r["payer"] for r in rows)


async def test_prices_within_radius(client):
    params = {"cpt": KNEE_CPT, "lat": 37.7749, "lng": -122.4194, "radius_miles": 30, "limit": 200}
    rows = (await client.get("/prices", params=params)).json()
    assert rows
    assert all(r["distance_miles"] <= 30 for r in rows)


async def test_unknown_procedure_returns_empty(client):
    response = await client.get("/prices", params={"cpt": "99999"})
    assert response.status_code == 200
    assert response.json() == []


async def test_compare_returns_only_requested_hospitals(client, seed):
    ccns = {BAYSHORE_CCN, SECOND_CCN}
    rows = (await client.get("/prices/compare", params={"cpt": KNEE_CPT, "ccns": ",".join(ccns)})).json()
    assert {r["ccn"] for r in rows} == ccns
    assert len(rows) == len(seed_prices(seed, KNEE_CPT, ccns))
    keys = [(r["hospital_name"], r["payer"]) for r in rows]
    assert keys == sorted(keys)


async def test_compare_ignores_unknown_hospitals(client, seed):
    params = {"cpt": KNEE_CPT, "ccns": f"{BAYSHORE_CCN}, {UNKNOWN_CCN}"}
    rows = (await client.get("/prices/compare", params=params)).json()
    assert {r["ccn"] for r in rows} == {BAYSHORE_CCN}
