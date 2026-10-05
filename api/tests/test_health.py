async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "commit" in response.json()


async def test_swagger_ui_is_served(client):
    response = await client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text


async def test_openapi_lists_all_endpoints(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert set(paths) == {
        "/health",
        "/hospitals",
        "/hospitals/{ccn}",
        "/hospitals/{ccn}/providers",
        "/providers",
        "/providers/{npi}",
        "/prices",
        "/prices/compare",
        "/devices",
        "/devices/by-procedure/{cpt}",
        "/devices/{device_id}",
        "/devices/{device_id}/recalls",
        "/devices/{device_id}/adverse-events",
        "/search",
    }
