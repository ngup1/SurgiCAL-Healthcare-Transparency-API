from conftest import BAYSHORE_CCN, KNEE_CPT, PACEMAKER_ID, PROVIDER_NPI

GROUPS = {"procedures", "providers", "hospitals", "devices"}


async def search(client, q, **params):
    response = await client.get("/search", params={"q": q, **params})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == GROUPS
    return body


async def test_single_word_matches_inside_long_names(client, seed):
    body = await search(client, "knee")
    assert KNEE_CPT in {p["code"] for p in body["procedures"]}
    knee_devices = {d["id"] for d in seed["devices"] if "knee" in d["brand_name"].lower()}
    assert {d["id"] for d in body["devices"]} == knee_devices


async def test_hospital_by_name(client):
    body = await search(client, "bayshore")
    assert [h["ccn"] for h in body["hospitals"]] == [BAYSHORE_CCN]


async def test_provider_by_last_name(client):
    body = await search(client, "castellanos")
    assert PROVIDER_NPI in {p["npi"] for p in body["providers"]}


async def test_matches_across_groups(client):
    body = await search(client, "pacemaker")
    assert "33208" in {p["code"] for p in body["procedures"]}
    assert PACEMAKER_ID in {d["id"] for d in body["devices"]}


async def test_results_sorted_by_relevance(client):
    body = await search(client, "knee")
    for group in GROUPS:
        scores = [r["relevance"] for r in body[group]]
        assert scores == sorted(scores, reverse=True)
        assert all(0 < s <= 1 for s in scores)


async def test_limit_applies_per_group(client):
    body = await search(client, "knee", limit=1)
    assert all(len(body[group]) <= 1 for group in GROUPS)


async def test_no_matches_returns_empty_groups(client):
    body = await search(client, "zzqxjv")
    assert all(body[group] == [] for group in GROUPS)
