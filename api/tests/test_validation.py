"""Malformed input returns 422 naming the bad field (never a 500)."""

import pytest
from conftest import KNEE_CPT, PACEMAKER_ID

ELEVEN_CCNS = ",".join(f"0508{i:02d}" for i in range(11))

# (path, query params, field named in the error)
CASES = [
    ("/v1/hospitals", {"offset": -1}, "offset"),
    ("/v1/hospitals", {"limit": 0}, "limit"),
    ("/v1/hospitals", {"limit": 201}, "limit"),
    ("/v1/hospitals", {"limit": "abc"}, "limit"),
    ("/v1/hospitals", {"city": "Pasadena", "radius_miles": 0}, "radius_miles"),
    ("/v1/hospitals", {"city": "Pasadena", "radius_miles": 101}, "radius_miles"),
    ("/v1/hospitals", {"zip": "9410"}, "zip"),
    ("/v1/hospitals", {"city": "x"}, "city"),
    ("/v1/hospitals/12", {}, "ccn"),
    ("/v1/hospitals/abc/providers", {}, "ccn"),
    ("/v1/hospitals/050801/providers", {"offset": -1}, "offset"),
    ("/v1/providers", {"offset": -5}, "offset"),
    ("/v1/providers", {"zip": "abcde"}, "zip"),
    ("/v1/providers/123", {}, "npi"),
    ("/v1/prices", {}, "cpt"),
    ("/v1/prices", {"cpt": "knee"}, "cpt"),
    ("/v1/prices", {"cpt": KNEE_CPT, "limit": 500}, "limit"),
    ("/v1/prices/compare", {"cpt": KNEE_CPT}, "ccns"),
    ("/v1/prices/compare", {"cpt": KNEE_CPT, "ccns": "050801,xyz"}, "ccns"),
    ("/v1/prices/compare", {"cpt": KNEE_CPT, "ccns": ","}, "ccns"),
    ("/v1/prices/compare", {"cpt": KNEE_CPT, "ccns": ELEVEN_CCNS}, "ccns"),
    ("/v1/prices/compare", {"cpt": "27", "ccns": "050801"}, "cpt"),
    ("/v1/devices", {"q": "k"}, "q"),
    ("/v1/devices", {"product_code": "jwh"}, "product_code"),
    ("/v1/devices/by-procedure/abc", {}, "cpt"),
    ("/v1/devices/hello", {}, "device_id"),
    ("/v1/devices/hello/recalls", {}, "device_id"),
    ("/v1/devices/hello/adverse-events", {}, "device_id"),
    (f"/v1/devices/{PACEMAKER_ID}/recalls", {"offset": -1}, "offset"),
    (f"/v1/devices/{PACEMAKER_ID}/adverse-events", {"event_type": "bad"}, "event_type"),
    ("/v1/search", {}, "q"),
    ("/v1/search", {"q": "k"}, "q"),
    ("/v1/search", {"q": "x" * 101}, "q"),
    ("/v1/search", {"q": "knee", "limit": 0}, "limit"),
    ("/v1/search", {"q": "knee", "limit": 51}, "limit"),
]

# Cases where the bad value is in the URL path rather than the query string.
MALFORMED_PATHS = {
    "/v1/hospitals/12",
    "/v1/hospitals/abc/providers",
    "/v1/providers/123",
    "/v1/devices/by-procedure/abc",
    "/v1/devices/hello",
    "/v1/devices/hello/recalls",
    "/v1/devices/hello/adverse-events",
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
    response = await client.get("/v1/hospitals", params={"limit": "abc", "offset": -1})
    body = response.json()
    assert response.status_code == 422
    assert body["detail"] == "Invalid values for 'limit', 'offset'"
    assert {e["field"]: e["input"] for e in body["errors"]} == {"limit": "abc", "offset": "-1"}


async def test_format_errors_use_readable_messages(client):
    body = (await client.get("/v1/providers/123")).json()
    assert body["errors"][0]["message"] == "Must be a 10-digit National Provider Identifier, e.g. 1572628497"
    assert body["detail"] == "Invalid value for 'npi': Must be a 10-digit National Provider Identifier, e.g. 1572628497"
