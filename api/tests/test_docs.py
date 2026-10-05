"""Swagger UI / OpenAPI content: every endpoint should work on the first click of "Execute"."""

import pytest

EXPECTED_TAGS = {"search", "prices", "hospitals", "providers", "devices", "places", "health"}


@pytest.fixture
async def spec(client):
    return (await client.get("/openapi.json")).json()


def operations(spec):
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            yield path, method, op


async def test_root_redirects_to_docs(client):
    response = await client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


async def test_describes_a_healthcare_transparency_api(spec):
    info = spec["info"]
    assert info["title"] == "SurgiCAL API"
    assert "transparency" in info["summary"].lower()
    assert "marketplace" not in (info["summary"] + info["description"]).lower()


async def test_every_tag_is_described(spec):
    tags = {t["name"]: t.get("description") for t in spec["tags"]}
    assert set(tags) == EXPECTED_TAGS
    assert all(tags.values())
    used = {tag for _, _, op in operations(spec) for tag in op["tags"]}
    assert used == EXPECTED_TAGS


async def test_every_operation_has_a_summary_and_description(spec):
    for path, _, op in operations(spec):
        assert op.get("summary"), path
        if path != "/health":
            assert op.get("description"), path


async def test_422_documents_the_real_error_shape(spec):
    schema = spec["components"]["schemas"]["ValidationErrorResponse"]
    assert set(schema["properties"]) == {"code", "detail", "errors", "suggestions"}
    for path, _, op in operations(spec):
        if op.get("parameters"):
            ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ValidationErrorResponse"), path


async def test_every_endpoint_documents_its_response_schema(spec):
    for path, _, op in operations(spec):
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema.get("$ref") or schema.get("items", {}).get("$ref"), f"{path} has no response model"


async def test_list_endpoints_document_total_count_header(spec):
    for path in ["/hospitals", "/providers", "/prices", "/devices", "/hospitals/{ccn}/providers"]:
        assert "X-Total-Count" in spec["paths"][path]["get"]["responses"]["200"]["headers"], path


@pytest.mark.parametrize(
    "path",
    [
        "/hospitals/{ccn}",
        "/hospitals/{ccn}/providers",
        "/providers/{npi}",
        "/devices/{device_id}",
        "/devices/{device_id}/recalls",
        "/devices/{device_id}/adverse-events",
    ],
)
async def test_detail_endpoints_document_404(spec, path):
    assert "404" in spec["paths"][path]["get"]["responses"]


async def test_only_required_parameters_are_prefilled(spec):
    # Swagger UI fills examples into the form; a pre-filled optional filter would narrow results.
    for path, _, op in operations(spec):
        for param in op.get("parameters", []):
            has_example = bool(param.get("examples"))
            assert has_example == param["required"], f"{path} {param['name']}"


def example_requests(op, path):
    """(url, query) for the pre-filled form, then once per alternative example in each dropdown."""
    required = [p for p in op.get("parameters", []) if p["required"]]
    defaults = {p["name"]: next(iter(p["examples"].values()))["value"] for p in required}
    variants = [defaults] + [
        {**defaults, p["name"]: ex["value"]} for p in required for ex in list(p["examples"].values())[1:]
    ]
    for values in variants:
        url, query = path, {}
        for p in required:
            if p["in"] == "path":
                url = url.replace("{" + p["name"] + "}", str(values[p["name"]]))
            else:
                query[p["name"]] = values[p["name"]]
        yield url, query


async def test_every_example_returns_data(client, spec):
    checked = 0
    for path, _, op in operations(spec):
        for url, query in example_requests(op, path):
            response = await client.get(url, params=query)
            assert response.status_code == 200, f"{url} {query}: {response.text[:200]}"
            body = response.json()
            assert body, f"{url} {query} returned an empty result"
            if isinstance(body, dict) and set(body) == {"procedures", "providers", "hospitals", "devices"}:
                assert any(body.values()), f"{url} {query} found nothing"
            checked += 1
    assert checked >= 20
