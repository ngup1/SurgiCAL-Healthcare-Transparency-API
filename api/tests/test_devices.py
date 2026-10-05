from collections import Counter

from conftest import KNEE_CPT, NO_DEVICE_CPT, PACEMAKER_ID, UNKNOWN_DEVICE_ID


async def test_list_all_devices_sorted_by_brand(client, seed):
    rows = (await client.get("/devices")).json()
    assert len(rows) == len(seed["devices"])
    brands = [r["brand_name"] for r in rows]
    assert brands == sorted(brands)


async def test_filter_by_product_code(client, seed):
    expected = {d["id"] for d in seed["devices"] if d["fda_product_code"] == "NIQ"}
    rows = (await client.get("/devices", params={"product_code": "NIQ"})).json()
    assert {r["id"] for r in rows} == expected


async def test_filter_by_manufacturer_and_specialty(client, seed):
    expected = {
        d["id"]
        for d in seed["devices"]
        if "meridian" in d["manufacturer"].lower() and "ortho" in d["medical_specialty"].lower()
    }
    params = {"manufacturer": "meridian", "medical_specialty": "ortho"}
    rows = (await client.get("/devices", params=params)).json()
    assert expected
    assert {r["id"] for r in rows} == expected


async def test_brand_search_matches_a_word(client, seed):
    expected = {d["id"] for d in seed["devices"] if "meridian" in d["brand_name"].lower()}
    rows = (await client.get("/devices", params={"q": "meridian"})).json()
    assert {r["id"] for r in rows} == expected


async def test_devices_by_procedure(client, seed):
    expected = {m["device_id"]: m["usage_type"] for m in seed["device_procedure_map"] if m["cpt"] == KNEE_CPT}
    rows = (await client.get(f"/devices/by-procedure/{KNEE_CPT}")).json()
    assert {r["id"]: r["usage_type"] for r in rows} == expected


async def test_procedure_without_devices_returns_empty(client):
    response = await client.get(f"/devices/by-procedure/{NO_DEVICE_CPT}")
    assert response.status_code == 200
    assert response.json() == []


async def test_detail_includes_recall_and_event_summary(client, seed):
    recalls = [r for r in seed["device_recalls"] if r["device_id"] == PACEMAKER_ID]
    events = Counter(e["event_type"] for e in seed["device_adverse_events"] if e["device_id"] == PACEMAKER_ID)

    response = await client.get(f"/devices/{PACEMAKER_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["brand_name"] == "Cardiovance Pulse DR Pacemaker"
    # Up to the 10 most recent recalls.
    recent = {r["recall_number"] for r in body["recent_recalls"]}
    assert len(recent) == min(10, len(recalls))
    assert recent <= {r["recall_number"] for r in recalls}
    assert {e["event_type"]: e["count"] for e in body["adverse_event_summary"]} == dict(events)


async def test_detail_unknown_device_is_404(client):
    response = await client.get(f"/devices/{UNKNOWN_DEVICE_ID}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Device not found"}


async def test_recalls_newest_first(client, seed):
    expected = {r["recall_number"] for r in seed["device_recalls"] if r["device_id"] == PACEMAKER_ID}
    rows = (await client.get(f"/devices/{PACEMAKER_ID}/recalls")).json()
    assert {r["recall_number"] for r in rows} == expected
    dates = [r["recall_date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


async def test_adverse_events_newest_first(client, seed):
    expected = {e["mdr_report_key"] for e in seed["device_adverse_events"] if e["device_id"] == PACEMAKER_ID}
    rows = (await client.get(f"/devices/{PACEMAKER_ID}/adverse-events")).json()
    assert {r["mdr_report_key"] for r in rows} == expected
    dates = [r["event_date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


async def test_adverse_events_filter_by_type(client, seed):
    expected = {
        e["mdr_report_key"]
        for e in seed["device_adverse_events"]
        if e["device_id"] == PACEMAKER_ID and e["event_type"] == "injury"
    }
    rows = (await client.get(f"/devices/{PACEMAKER_ID}/adverse-events", params={"event_type": "injury"})).json()
    assert {r["mdr_report_key"] for r in rows} == expected


async def test_sub_resources_of_unknown_device_are_404(client):
    for path in ("recalls", "adverse-events"):
        response = await client.get(f"/devices/{UNKNOWN_DEVICE_ID}/{path}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Device not found"}
