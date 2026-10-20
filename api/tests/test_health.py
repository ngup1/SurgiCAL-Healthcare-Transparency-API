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
        "/health/ready",
        "/v1/hospitals",
        "/v1/hospitals/{ccn}",
        "/v1/hospitals/{ccn}/providers",
        "/v1/providers",
        "/v1/providers/{npi}",
        "/v1/prices",
        "/v1/prices/compare",
        "/v1/devices",
        "/v1/devices/by-procedure/{cpt}",
        "/v1/devices/{device_id}",
        "/v1/devices/{device_id}/recalls",
        "/v1/devices/{device_id}/adverse-events",
        "/v1/search",
        "/v1/places",
    }


async def test_unversioned_data_paths_are_gone(client):
    assert (await client.get("/hospitals")).status_code == 404
    assert (await client.get("/v1/hospitals")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_readiness_checks_the_database(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
