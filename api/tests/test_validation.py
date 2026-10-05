"""Malformed input returns 422 naming the bad field (never a 500)."""

import pytest
from conftest import KNEE_CPT, PACEMAKER_ID

ELEVEN_CCNS = ",".join(f"0508{i:02d}" for i in range(11))

# (path, query params, field named in the error)
CASES = [
    ("/hospitals", {"offset": -1}, "offset"),
    ("/hospitals", {"limit": 0}, "limit"),
    ("/hospitals", {"limit": 201}, "limit"),
    ("/hospitals", {"limit": "abc"}, "limit"),
    ("/hospitals", {"lat": 34.05}, "lng"),
    ("/hospitals", {"lat": 134, "lng": -118}, "lat"),
    ("/hospitals", {"lat": 34, "lng": -218}, "lng"),
    ("/hospitals", {"radius_miles": 0}, "radius_miles"),
    ("/hospitals", {"radius_miles": 251}, "radius_miles"),
    ("/hospitals", {"state": "california"}, "state"),
    ("/hospitals/12", {}, "ccn"),
    ("/hospitals/abc/providers", {}, "ccn"),
    ("/hospitals/050801/providers", {"offset": -1}, "offset"),
    ("/providers", {"offset": -5}, "offset"),
    ("/providers", {"lng": -118}, "lat"),
    ("/providers/123", {}, "npi"),
    ("/prices", {}, "cpt"),
    ("/prices", {"cpt": "knee"}, "cpt"),
    ("/prices", {"cpt": KNEE_CPT, "limit": 500}, "limit"),
    ("/prices/compare", {"cpt": KNEE_CPT}, "ccns"),
    ("/prices/compare", {"cpt": KNEE_CPT, "ccns": "050801,xyz"}, "ccns"),
    ("/prices/compare", {"cpt": KNEE_CPT, "ccns": ","}, "ccns"),
    ("/prices/compare", {"cpt": KNEE_CPT, "ccns": ELEVEN_CCNS}, "ccns"),
    ("/prices/compare", {"cpt": "27", "ccns": "050801"}, "cpt"),
    ("/devices", {"q": "k"}, "q"),
    ("/devices", {"product_code": "jwh"}, "product_code"),
    ("/devices/by-procedure/abc", {}, "cpt"),
    ("/devices/hello", {}, "device_id"),
    ("/devices/hello/recalls", {}, "device_id"),
    ("/devices/hello/adverse-events", {}, "device_id"),
    (f"/devices/{PACEMAKER_ID}/recalls", {"offset": -1}, "offset"),
    (f"/devices/{PACEMAKER_ID}/adverse-events", {"event_type": "bad"}, "event_type"),
    ("/search", {}, "q"),
    ("/search", {"q": "k"}, "q"),
    ("/search", {"q": "x" * 101}, "q"),
    ("/search", {"q": "knee", "limit": 0}, "limit"),
    ("/search", {"q": "knee", "limit": 51}, "limit"),
]

# Cases where the bad value is in the URL path rather than the query string.
MALFORMED_PATHS = {
    "/hospitals/12",
    "/hospitals/abc/providers",
    "/providers/123",
    "/devices/by-procedure/abc",
    "/devices/hello",
    "/devices/hello/recalls",
    "/devices/hello/adverse-events",
}


@pytest.mark.parametrize(("path", "params", "field"), CASES)
async def test_malformed_input_is_422_naming_the_field(client, path, params, field):
    response = await client.get(path, params=params)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert [e["field"] for e in body["errors"]] == [field]
    assert f"'{field}'" in body["detail"]
    error = body["errors"][0]
    assert set(error) == {"field", "location", "message", "input"}
    assert error["location"] == ("path" if path in MALFORMED_PATHS else "query")


async def test_every_bad_field_is_reported(client):
    response = await client.get("/hospitals", params={"limit": "abc", "offset": -1})
    body = response.json()
    assert response.status_code == 422
    assert body["detail"] == "Invalid values for 'limit', 'offset'"
    assert {e["field"]: e["input"] for e in body["errors"]} == {"limit": "abc", "offset": "-1"}


async def test_format_errors_use_readable_messages(client):
    body = (await client.get("/providers/123")).json()
    assert body["errors"][0]["message"] == "Must be a 10-digit National Provider Identifier, e.g. 1572628497"
    assert body["detail"] == "Invalid value for 'npi': Must be a 10-digit National Provider Identifier, e.g. 1572628497"
