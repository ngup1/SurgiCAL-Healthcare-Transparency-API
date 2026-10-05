# SurgiCAL API: Deployment-Readiness Plan

**Goal:** Make the API deployable as Docker containers backed by **mock data**, with a public **Swagger UI** where anyone can try every endpoint. The work follows [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices).

**Constraints**
- The AWS RDS database is gone, so all data is fictional and generated (see [§6](#6-mock-data)).
- **Coverage is California only**, the same as the production data. The API rejects locations outside California with a clear message (see [§3a](#3a-location-search-california-only)).
- All 14 existing endpoints keep their **paths**. For location, the query parameters change from `lat`/`lng` to `city` / `county` / `zip`, as requested.
- The ETL pipeline (`etl/`) is unchanged. `migrations/` gains one additive file (`011_ca_places.sql`), and the API reuses the migration SQL to build its schema.

---

## 1. Key decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Where the mock data lives | **Postgres 16 + PostGIS container, seeded at startup** | The existing SQL relies on PostGIS (`ST_DWithin`, `ST_Distance`) and pg_trgm (`%`, `similarity`). Seeding a real Postgres keeps those code paths working, so the API can switch back to real data later by changing only the connection settings. The alternative, an in-memory Python mock, would mean rewriting distance and fuzzy search in Python and maintaining a second code path that real data never uses. |
| D2 | Database driver | **psycopg 3 (async) + `psycopg_pool`** | The guide prefers `async` routes with non-blocking I/O, and psycopg 3 supports async natively. It uses the same `%s` placeholders as psycopg2, so the SQL moves over almost unchanged. The pool removes the cost of opening a new SSL connection on every request (gap #2 in `API_GUIDE.md`). |
| D3 | ORM or raw SQL | **Keep raw SQL**, moved into each domain's `service.py` | The guide's "SQL-first" section supports this. The queries are already written and tuned for PostGIS. |
| D4 | Migrations | **Keep `migrations/*.sql`** and run them through Postgres's init scripts | The guide recommends Alembic, but the ETL pipeline shares these files. Moving to Alembic is a separate job, listed in [§8](#8-deviations-from-the-guide). |
| D5 | Response shapes | **Same JSON shapes as today**, now typed with Pydantic `response_model`s. List endpoints also send an `X-Total-Count` header | This keeps existing clients working. The typed models show up in Swagger, which closes gap #1 in `API_GUIDE.md`. |
| D6 | Swagger UI | **FastAPI's built-in `/docs`**, turned on by a setting, with `/` redirecting to `/docs` | Nothing extra to build or host. The guide says to hide docs by default, so `SHOW_DOCS` controls it and the public demo turns it on. |
| D8 | Location input | **Place names (`city`, `county`, `zip`) looked up in a `ca_places` table** that stores California places and their centre points (migration 011) | People search by place, not coordinates. A place's centre point still drives the existing PostGIS radius queries, and a place that isn't in the table gives a helpful error instead of an empty result. |
| D7 | Hosting | **GitHub Actions → GitHub Container Registry → Render** (Docker web service + managed Postgres with PostGIS). Locally: `docker compose`. For a quick demo from your laptop, a Cloudflare quick tunnel | Teaches the standard CI/CD flow. GitHub runs the pipeline and stores the image; Render runs it. See Phase 9. |

---

## 2. Target structure

The guide organizes code **by domain**: each resource gets a folder with the same set of files.

```
api/
├── src/
│   ├── main.py                 # app factory, lifespan (DB pool), routers, CORS, docs toggle
│   ├── config.py               # global Settings (pydantic-settings): DB, CORS, ENVIRONMENT, SHOW_DOCS
│   ├── database.py             # AsyncConnectionPool, get_db dependency, fetch_all/fetch_one helpers
│   ├── schemas.py              # CustomModel base class (shared serialization config)
│   ├── exceptions.py           # NotFound base error + exception handlers (DB errors → 503)
│   ├── pagination.py           # Pagination dependency (limit/offset with ge/le), X-Total-Count helper
│   ├── geo.py                  # spatial_where / distance_select (from dependencies.py)
│   ├── locations/
│   │   ├── router.py           # GET /places (autocomplete for valid CA cities/counties/ZIPs)
│   │   ├── schemas.py          # LocationQuery, Place, LocationFilter
│   │   ├── service.py          # resolve_place, suggest_places (trigram "did you mean")
│   │   ├── dependencies.py     # valid_location → LocationFilter or 422
│   │   └── exceptions.py       # LocationOutsideCoverage, LocationNotFound
│   ├── health/
│   │   └── router.py           # /health (liveness), /health/ready (runs SELECT 1)
│   ├── hospitals/
│   │   ├── router.py           # GET /hospitals, /hospitals/{ccn}, /hospitals/{ccn}/providers
│   │   ├── schemas.py          # HospitalSummary, HospitalDetail, HospitalProvider
│   │   ├── service.py          # SQL queries
│   │   ├── dependencies.py     # valid_hospital_ccn → raises HospitalNotFound
│   │   ├── exceptions.py       # HospitalNotFound
│   │   └── constants.py        # CMS group values, hospital types
│   ├── providers/              # same files; valid_provider_npi
│   ├── prices/                 # same files; validates the ccns list
│   ├── devices/                # same files; valid_device_id (UUID)
│   └── search/                 # router, schemas, service
├── seed/
│   ├── generate.py             # deterministic mock-data generator ✅ done
│   ├── data/*.json             # fixtures, one file per table ✅ done
│   └── seed.sql                # INSERTs for Postgres ✅ done
├── db/
│   └── Dockerfile              # postgis image + migrations + seed.sql in /docker-entrypoint-initdb.d
├── tests/
│   ├── conftest.py             # AsyncClient(ASGITransport), seeded test database
│   └── test_<domain>.py
├── Dockerfile                  # production image for the API
├── .dockerignore
├── pyproject.toml              # dependencies + ruff + pytest config
└── .env.example
```

**Where the current files go**

| Today | Moves to |
|---|---|
| `api/main.py` | `src/main.py` (adds lifespan, settings, exception handlers) |
| `api/dependencies.py: get_db*` | `src/database.py` |
| `api/dependencies.py: spatial_where, distance_select` | `src/geo.py` |
| `api/routers/<x>.py` | `src/<x>/router.py` (HTTP layer) + `src/<x>/service.py` (SQL) |
| `api/requirements.txt` | `pyproject.toml` (the Makefile and Dockerfile are updated to match) |

Following the guide, modules in other domains are imported by name, e.g. `from src.hospitals import service as hospital_service`.

---

## 3. Endpoint matrix

Every endpoint stays at the same path. "Changes" lists what each one gains. One endpoint is new: `GET /places`.

| Endpoint | Module | Response model | Changes |
|---|---|---|---|
| `GET /health` | `health` | `HealthStatus` | Unchanged (liveness check). New `GET /health/ready` checks the database |
| `GET /hospitals` | `hospitals` | `list[HospitalSummary]` | `Pagination` + `LocationQuery` dependencies (§3a), `X-Total-Count` header |
| `GET /hospitals/{ccn}` | `hospitals` | `HospitalDetail` | `valid_hospital_ccn` dependency; `ccn` must match `^\d{6}$` |
| `GET /hospitals/{ccn}/providers` | `hospitals` | `list[HospitalProvider]` | Reuses `valid_hospital_ccn`, so an unknown hospital returns **404 instead of `[]`** |
| `GET /providers` | `providers` | `list[ProviderSummary]` | `Pagination` + `LocationQuery`. `specialty` filter unchanged |
| `GET /providers/{npi}` | `providers` | `ProviderDetail` (with `affiliations`) | `valid_provider_npi`; `npi` must match `^\d{10}$` |
| `GET /prices` | `prices` | `list[PriceRow]` | `cpt` must match `^\d{5}$`; `Pagination` + `LocationQuery` (applied to the hospital) |
| `GET /prices/compare` | `prices` | `list[PriceComparisonRow]` | Accepts `ccns=a,b` (as today) **and** `ccns=a&ccns=b`; at most 10 hospitals |
| `GET /devices` | `devices` | `list[DeviceSummary]` | `Pagination`; `q` gets `min_length=2` |
| `GET /devices/by-procedure/{cpt}` | `devices` | `list[DeviceForProcedure]` | `cpt` pattern check |
| `GET /devices/{device_id}` | `devices` | `DeviceDetail` | `device_id: UUID`, so a malformed ID returns **422 instead of 500** |
| `GET /devices/{device_id}/recalls` | `devices` | `list[DeviceRecall]` | Reuses `valid_device_id`, so unknown devices return 404 |
| `GET /devices/{device_id}/adverse-events` | `devices` | `list[AdverseEvent]` | Reuses `valid_device_id`; `event_type` becomes an enum (`death`, `injury`, `malfunction`) |
| `GET /search` | `search` | `SearchResults` | The four queries run concurrently with `asyncio.gather`, each on its own pooled connection. Word matching ✅ **done** (behavior change 4) |
| `GET /places` **(new)** | `locations` | `list[Place]` | `q` (≥ 2 characters), `type` (`city`/`county`/`zip`). Lets Swagger users find valid place names |

**Shared dependencies** (written once, used by many routes):

```python
# src/pagination.py
class Pagination(CustomModel):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)            # offset=-1 now returns 422 instead of 500

# src/locations/schemas.py
class LocationQuery(CustomModel):
    state: Literal["CA"] = "CA"          # anything else → 422 "covers California only"
    city: str | None = None
    county: str | None = None            # "Los Angeles" or "Los Angeles County"
    zip: str | None = Field(None, pattern=r"^\d{5}$")
    radius_miles: float | None = Field(None, gt=0, le=100)
    # model_validator: at most one of city / county / zip;
    #                  radius_miles only with city or zip → otherwise 422
```

Both are used as `Annotated[Model, Query()]` (FastAPI ≥ 0.115 supports query-parameter models), so Swagger still shows each field as its own parameter. The `valid_location` dependency takes a `LocationQuery`, looks the place up in `ca_places`, and hands the service a `LocationFilter` (a canonical name, a list of cities, or a centre point and radius).

### 3a. Location search (California only)

| Request | Meaning | SQL used |
|---|---|---|
| *(no location)* | All of California | none |
| `city=Pasadena` | Hospitals or providers in that city | `city = 'Pasadena'` (canonical name, case-insensitive input) |
| `city=Pasadena&radius_miles=15` | Within 15 miles of the city centre, nearest first, with `distance_miles` | `ST_DWithin` around the city centre point |
| `county=Los Angeles` | Every city in the county | `city IN (cities in that county)` |
| `zip=90033` | Within `radius_miles` (default 10) of the ZIP centre | `ST_DWithin` around the ZIP centre point |

**Handling places outside coverage.** Every failure returns 422 with a machine-readable `code`, so a client can show the right message:

| Input | Response |
|---|---|
| `state=NV` | `{"code": "location_outside_coverage", "detail": "SurgiCAL currently covers California only."}` |
| `zip=89502` | Same code. **Out-of-state ZIPs are detected without a lookup**, because California ZIPs fall in 90001–96162 |
| `city=Reno` | `{"code": "location_not_found", "detail": "'Reno' is not a California city in our coverage area.", "suggestions": []}` |
| `city=Pasedena` | `{"code": "location_not_found", ..., "suggestions": ["Pasadena"]}`: trigram "did you mean" from `ca_places` |
| `county=Nevada` | **Valid.** Nevada County, California (Truckee). This is why the API takes `county` and `state` as separate fields |
| `city=Modesto` | **Valid place, no hospitals** → `200 []`. A known place with no results is not an error |
| `city=X&county=Y` | `{"code": "invalid_location_query", "detail": "Use only one of city, county or zip."}` |

A city name alone can't prove the place is outside California: "Reno" could be a typo. So the API says the city isn't in its California coverage area rather than claiming it's in another state. ZIP codes and `state` *can* be checked without doubt, so their messages are definite.

**Production data:** fill `ca_places` from the Census Gazetteer files (places, counties and ZIP code areas, filtered to California). That is one new ETL extractor; the mock version is already generated.

**Behavior changes visible to existing callers** (all approved):

1. **Location by place name.** `lat`/`lng` are removed from `/hospitals`, `/providers` and `/prices` and replaced by `city`/`county`/`zip` (§3a). `state` only accepts `CA`.
2. **Unknown parent resource.** `/hospitals/{ccn}/providers` and `/devices/{id}/recalls|adverse-events` return 404 for an unknown ID instead of an empty list.
3. **Malformed input** returns 422 instead of 500 ✅ **implemented** in the current code (`api/validation.py`, `api/exceptions.py`). The body is `{"code": "validation_error", "detail": "...", "errors": [{"field", "location", "message", "input"}]}`, and the location errors in §3a use the same shape.
4. **Search matching** ✅ **implemented** in `api/routers/search.py` and `api/routers/devices.py`. `/search` and `/devices?q=` use `column % q`, which compares the query with the **whole** string and needs a similarity ≥ 0.3. Short queries therefore miss long names: `knee` scores 0.14 against "Total knee replacement (arthroplasty)" and `bayshore` scores 0.28 against "Bayshore Regional Medical Center", so both return nothing. That happens with real data too. The fix is to switch to word similarity: `q <% column` for the filter and `word_similarity(q, column)` for ranking. Word similarity scores how well the query matches the best part of the string, and the existing GIN trigram indexes support the `<%` operator. Each of the queries above then scores 1.0.

---

## 4. Phased implementation

Each phase ends in a working, testable state.

**Order of work:** 1 → 2 → **9** → 3 → 4 → 5 → 6 → 7 → 8. Deployment (Phase 9) moves up right after the tests, so the pipeline exists early. Every later phase is then tested and deployed automatically when it merges to `main`. This is also how teams usually work: set up the delivery pipeline first, then ship small changes through it.

### Phase 1: Local database with mock data ✅ done
- [x] `api/db/Dockerfile`: PostGIS 16 image with `migrations/*.sql` and `api/seed/seed.sql` (as `999_seed.sql`) in `/docker-entrypoint-initdb.d/`. The base is `imresamu/postgis:16-3.4` because the official `postgis/postgis` tags are Intel-only; a build argument (`POSTGIS_IMAGE`) switches back to the official image.
- [x] `docker-compose.yml` has two services. `db` exposes host port 5433 so it doesn't clash with a local Postgres, keeps its data in a named volume, and has a `pg_isready` healthcheck over TCP, so it only reports healthy after the seed finishes. `api` waits for that healthcheck.
- [x] `api/.env.example` for running the API outside Docker against the compose database. Root `.dockerignore` keeps `.venv`, `etl` and the JSON fixtures out of the build context.
- [x] Makefile: `make up`, `make down`, `make db-up`, `make db-reset` (re-runs migrations and seed), `make seed-data` (regenerates the mock data).
- [x] Search fix checked against real Postgres: `?q=knee` returns `27447`, `?q=bayshore` returns `050801`. The old `%` operator returns 0 rows for both.
- **Result:** `make up` starts both containers from scratch in about 30 seconds. A 25-check smoke test across all 14 endpoints passes. Image sizes: api 551 MB (Phase 8 target < 200 MB), db 888 MB.

### Phase 2: Pin current behavior with tests ✅ done
- [x] `api/requirements-dev.txt` (pytest, pytest-asyncio, httpx) and `api/pytest.ini`. `make install-dev` and `make test` (accepts `ARGS="-k search"` to filter).
- [x] `tests/conftest.py`: an async `httpx.AsyncClient(transport=ASGITransport(app))` client, following the guide's testing section. It points at the compose database (port 5433) unless `DB_*` variables are already set, so CI can use its own database. If the database isn't reachable, the run stops with "start it with `make db-up`".
- [x] Expected values are read from `api/seed/data/*.json`, the same fixtures the seed is built from, so regenerating the mock data doesn't break the tests.
- [x] **85 tests:** hospitals 9, providers 8, prices 7, devices 12, search 7, health/OpenAPI 3, validation 39 (37 bad-input cases plus the error format).
- [x] Tests that record behavior Phase 5 will change are marked `# Current behavior; Phase 5 ...` (unknown sub-resource → `[]`, `state=NV` → `[]`), so it's clear which assertions get flipped.
- [x] Checked that the tests can fail: putting back the old `%` search operator fails 3 search tests.
- **Result:** `make test` takes about 1 second, and all 85 tests pass against the current code.

### Phase 3: Restructure into `src/` by domain ✅ done
- [x] `api/src/` organized by domain, following the guide: `hospitals/`, `providers/`, `prices/`, `devices/`, `search/`, `health/`. Each has a `router.py` (HTTP only) and a `service.py` (the SQL, moved over unchanged), plus `exceptions.py`, `constants.py` and `dependencies.py` where needed. Shared modules: `config.py`, `database.py` (connection plus `fetch_all` and `fetch_one`), `constants.py` (ID formats), `exceptions.py`, `geo.py`, `docs.py`.
- [x] `src/config.py`: `Settings(BaseSettings)` from `pydantic-settings`, covering `DATABASE_URL` or `DB_*`, `SHOW_DOCS`, `CORS_ORIGINS` (no longer hard-coded) and `GIT_COMMIT`.
- [x] Domain errors (`HospitalNotFound`, `ProviderNotFound`, `DeviceNotFound`) subclass `NotFound`; a single handler turns them into 404s.
- [x] `ccns` parsing moved into a reusable dependency (`prices/dependencies.py: valid_ccn_list`). The geo helpers take a column name instead of string-replacing `location`.
- [x] Entry point is now `src.main:app` (Dockerfile, Makefile). The tests import `src.*`; `conftest.py` clears `DATABASE_URL` unless `TEST_DATABASE_URL` is set, so a hosted database in `api/.env` is never used by the tests.
- **Result:** all 95 tests pass with no changes apart from imports, and the 25/25 smoke checks pass against the rebuilt image.

### Phase 4: Async database and connection pool ✅ done
- [x] `psycopg2` replaced by **psycopg 3** plus `psycopg_pool`. `src/database.py` creates an `AsyncConnectionPool`, which the app's `lifespan` opens at startup (`wait=False`, so `/health` answers even if the database is briefly down) and closes at shutdown. `get_db` borrows a connection for each request.
- [x] Pool connections use `autocommit` (read-only queries) and `prepare_threshold=None`. The latter matters because transaction-mode poolers like Neon's `-pooler` endpoint can send the next query to a different server connection, where a prepared statement wouldn't exist.
- [x] Every route, service and dependency is `async def` with `await`ed queries (the guide's async rules: no blocking calls in async routes). `/prices/compare` uses `= ANY(%s)` with a list instead of building placeholders.
- [x] `/search` runs its four queries concurrently (`asyncio.gather`), each on its own pooled connection.
- [x] `conftest.py` runs the app lifespan around the test client (ASGITransport doesn't send lifespan events). `db/seed_remote.py` was ported to psycopg 3, so the project no longer depends on psycopg2.
- **Result** (old sync image vs new, same local database, 500 requests at 25 concurrent):

  | | Before | After |
  |---|---|---|
  | New DB connections | 501 | **17** |
  | Throughput | 236 req/s | **369 req/s** |
  | p50 / p95 latency | 75 / 272 ms | **46 / 187 ms** |

  Against Neon, where every new connection also does a TLS handshake, the difference should be larger. All 95 tests and the 25/25 smoke checks pass.

### Phase 5: Response models, validation, dependencies
- [ ] Write the Pydantic response models listed in §3 for every endpoint, and set `response_model` on each route.
- [ ] Add the `Pagination` dependency, plus `valid_hospital_ccn`, `valid_provider_npi` and `valid_device_id`. Following the guide's REST section, the path variable has the same name everywhere so these dependencies can be reused and chained.
- [ ] Add pattern checks for CPT, CCN and NPI, plus the `event_type` enum.
- [x] Switch fuzzy search to `<%` / `word_similarity` (behavior change 4). Still to do: a test that `/search?q=knee` returns procedure `27447`.
- [ ] Build the `locations` module (§3a): `LocationQuery`, the `valid_location` dependency, coverage errors with `code` and `suggestions`, and `GET /places`. Replace `lat`/`lng` on `/hospitals`, `/providers` and `/prices`.
- [ ] Tests for every row of the §3a error table.
- [ ] Apply the other behavior changes from §3 and update the Phase 2 tests to match.
- **Done when:** every endpoint in `/docs` shows its response schema, and invalid input never produces a 500.

### Phase 6: Errors, health, observability
- [ ] `src/exceptions.py`: domain exceptions (`HospitalNotFound`, ...) inherit from a `NotFound` base that the app turns into a 404. A `psycopg.OperationalError` becomes **503** `{"detail": "Database unavailable"}`, and any other unexpected error becomes 500 with the details logged only on the server.
- [ ] `/health` stays a liveness check. `/health/ready` runs `SELECT 1` and is used by Docker's `HEALTHCHECK` and the host's health check.
- [ ] Structured JSON logging, a request-ID middleware, and access logs from uvicorn.
- [ ] Read CORS origins from `CORS_ORIGINS`, with no hard-coded `localhost:5173`.

### Phase 7: Swagger UI for public testing ✅ done
- [x] Header reads "SurgiCAL API · Healthcare transparency API", with a short description of how to try the API and its error behavior. There's no note about fictional data in Swagger; the README covers that.
- [x] `openapi_tags` describe each area, ordered search → prices → hospitals → providers → devices → health. Every route has a `summary` and a plain-language description; optional filters describe their format with examples, e.g. "`aetna`, `medicare`, `cash`".
- [x] **Required and path parameters are pre-filled** with seed-data values (`openapi_examples`, in `api/docs.py`), several with dropdown alternatives. Optional filters are deliberately left empty, since a pre-filled filter would silently narrow the results.
- [x] `tryItOutEnabled`: every endpoint opens ready to Execute. `displayRequestDuration` is on, and the schema list at the bottom is hidden.
- [x] The 422 response documents the real error format (`ValidationErrorResponse`) instead of FastAPI's default. Detail endpoints document their 404.
- [x] `GET /` redirects to `/docs`. `SHOW_DOCS=false` hides `/docs`, `/redoc` and `/openapi.json` (on by default, so the deployed demo needs no settings).
- [x] `tests/test_docs.py` (10 tests) checks: every tag is described, every operation has a summary, only required parameters are pre-filled, and **every example value (including dropdown alternatives) returns data**, so "Try it out → Execute" works on the first click.

### Phase 8: Production Docker image ✅ done (moved ahead of Phase 9)
- [x] Two-stage `api/Dockerfile` on `python:3.12-slim`. Removed `gcc` and `libpq-dev` (187 MB), since `psycopg2-binary` bundles libpq. The virtualenv is created `--without-pip` and filled by the build stage's pip, so pip never ships.
- [x] Trimmed `uvicorn[standard]` to `uvicorn` + `uvloop` + `httptools`. `watchfiles` (for `--reload`) moved to `requirements-dev.txt`.
- [x] Runs as a non-root `app` user (uid 10001), with `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`.
- [x] Listens on `$PORT` (default 8000), with 2 workers, `--proxy-headers` and `--forwarded-allow-ips='*'` for running behind a host's load balancer.
- [x] `HEALTHCHECK` calls `/health` using Python's standard library, since the slim image has no curl. Switch it to `/health/ready` in Phase 6.
- [x] `api/Dockerfile.dockerignore` (a build-context ignore file for this Dockerfile only): sends `api/` minus tests, seed data, docs and dev files, so new modules are included automatically.
- **Result:**

  | | Before | After |
  |---|---|---|
  | On disk (unpacked) | 386 MB | **191 MB** (149 MB of that is the base image) |
  | Compressed (registry size) | 127 MB | **57 MB** |

  Docker Desktop's `docker images` column shows a larger number (551 MB before) because it adds compressed and unpacked copies together. Registries such as ghcr.io store the compressed size.
- Later: if Phase 3 renames the module to `src.main:app`, update the `CMD`.

### Phase 9: CI/CD and deployment (runs right after Phase 2)
GitHub doesn't run the API. It runs the pipeline and stores the image, and Render runs the containers.
```
push ──► GitHub Actions ──► ghcr.io (image registry) ──► Render (runs API) ──► managed Postgres
         lint, test, build    tagged by commit SHA       auto-deploy on main     PostGIS + pg_trgm
```

**Stage A: CI (every push and pull request) ✅ done**
- [x] `ruff check` + `ruff format --check`, configured in `api/ruff.toml`; `make lint` and `make format`. The generator's data tables are exempt from line length and formatting.
- [x] `.github/workflows/api-ci.yml` runs on pushes that touch the API, migrations, compose file or workflow, on pull requests to `main`, and on demand. A newer push cancels the older run.
  - **Lint** job (~12 s).
  - **Test** job (~1 min): `docker compose up --wait db` gives CI the **same seeded database as local**, then pytest runs (85 tests). After that, `docker compose up --wait api` builds the production image, and a smoke test checks `/health`, all 27 hospitals and `/search?q=knee` against the running container.
- [x] First runs green. Actions pinned to `checkout@v7` and `setup-python@v7` (Node 24).
- Note: a push that changes `.github/workflows/` needs a token with the `workflow` scope. The `gh` CLI login has it (run `gh auth setup-git` once to make git use it).

**Stage B: Registry (merges to `main`) ✅ done**
- [x] `publish` job in `api-ci.yml`: runs only on pushes to `main`, after `lint` and `test` pass. Logs in to `ghcr.io` with the built-in `GITHUB_TOKEN` (`packages: write`) and pushes `ghcr.io/<owner>/surgical-api` tagged `:sha-<commit>` (fixed, for deploys and rollbacks) and `:latest`. Builds for both `linux/amd64` (Render) and `linux/arm64` (Apple Silicon). Reuses cached layers between runs. Runs on `main` are never cancelled midway.
- [x] Published from the public repo `ngup1/surgical-api`. The package is **public**, inheriting the repo's visibility: an anonymous `docker pull ghcr.io/ngup1/surgical-api:latest` works, with no credentials needed by Render. The pulled image passes the 25-check smoke test, and its `org.opencontainers.image.revision` label matches the commit.

**Stage C: Deploy (Render runs the API, Neon hosts the database)**
Neon instead of Render Postgres: Render's free databases expire after ~30 days and are then deleted. Neon's free tier doesn't expire (it sleeps when idle) and supports PostGIS and pg_trgm.
- [x] The API accepts `DATABASE_URL` (a single connection string), which takes precedence over the `DB_*` variables.
- [x] `make seed-db` (`api/db/seed_remote.py`): records applied migrations in `schema_migrations`, applies each new file in its own transaction, then loads `seed.sql`. Safe to re-run. Tested on an empty database: 11 migrations applied, then all 85 tests pass against it through `DATABASE_URL`.
- [x] The `publish` job calls Render's deploy hook (the `RENDER_DEPLOY_HOOK` secret) with `imgURL=…:sha-<commit>`, so Render runs the exact image CI built. The step is skipped while the secret is unset.
- [x] Neon project (Postgres 16, AWS US East 2 / Ohio), seeded with `make seed-db` over the **direct** connection string. Note: ProtonVPN blocked the Postgres handshake (TCP connected, but there was no reply to the SSL request), so seeding from the Mac needs the VPN off.
- [x] Render web service `https://surgical-api.onrender.com`, created from the existing image, in the Ohio region (same as Neon), with `DATABASE_URL` set to Neon's **pooled** string and health check `/health`. 25/25 smoke checks pass against it, with responses in about 0.1–0.2 s.
- [x] `RENDER_DEPLOY_HOOK` secret set. `/health` reports `commit` (baked into the image via the `GIT_COMMIT` build argument), so you can check which commit is live.
- **Done when:** merging to `main` updates `https://<name>.onrender.com/docs` with no manual steps.

**Demo fallback:** run `make up`, then `cloudflared tunnel --url http://localhost:8000` for a temporary public HTTPS URL from your laptop.

---

## 5. Configuration (`.env.example`)

```dotenv
ENVIRONMENT=local            # local | staging | production
SHOW_DOCS=true               # public demo: true
APP_VERSION=0.2.0

DB_HOST=db
DB_PORT=5432
DB_NAME=surgical
DB_USER=surgical
DB_PASSWORD=surgical
DB_SSL_MODE=disable          # "require" for managed Postgres
DB_POOL_MIN=1
DB_POOL_MAX=10

CORS_ORIGINS=["http://localhost:5173"]
```

---

## 6. Mock data

**Already generated** by `api/seed/generate.py`. The generator is deterministic (`random.Random(42)`), so running it again produces the same IDs and values.

```bash
python3 api/seed/generate.py      # rewrites seed/data/*.json and seed/seed.sql
```

| Table | Rows | What it covers |
|---|---|---|
| `cpt_codes` | 21 | Joints, spine, general surgery, cardiac, eye, gynecology, plus scans and IV, so the surgical/non-surgical flag has examples of both |
| `ca_places` | 89 | 37 cities (12 with no hospital, e.g. Modesto), 25 counties, 27 ZIPs. Every hospital and provider city is listed |
| `hospitals` | 27 | California only, 25 cities in 18 counties. Includes 3 critical-access hospitals |
| `hospital_quality` | 26 | One hospital (`051382`, High Sierra) has **no** quality row, so the API's LEFT JOINs return nulls |
| `providers` | 110 | 11 specialties with NUCC taxonomy codes. NPIs pass the real NPI check-digit (Luhn) validation |
| `provider_affiliations` | 190 | Each provider has one primary hospital and up to 2 others within 40 miles |
| `provider_metrics` | 99 | About 10% of providers have no metrics row (nulls again) |
| `prices` | ~2,700 | Medicare, cash, and 3 commercial payers per hospital, with PPO and HMO plans. Cash < commercial; Medicare ≈ 42% of commercial |
| `devices` | 28 | Fictional manufacturers. Class II and III devices, with 510(k) (`K…`) or PMA (`P…`) numbers |
| `device_procedure_map` | 36 | Every surgical CPT code except `15823` (eyelid lift) has at least one device, so `/devices/by-procedure/15823` shows the empty-list case |
| `device_recalls` | 12 | Class I, II and III recalls; open, completed and terminated |
| `device_adverse_events` | 93 | Malfunction, injury and death reports, with outcomes, problems and narratives |

**Example values for Swagger** (same on every run of the generator unless the generator code changes):

| Parameter | Value | Notes |
|---|---|---|
| `ccn` | `050801` | Bayshore Regional Medical Center, San Francisco (large hospital, every procedure) |
| `ccn` (no quality data) | `051382` | High Sierra Community Hospital, Truckee |
| `npi` | `1572628497` | Daniel Castellanos, orthopaedic surgeon, Bakersfield |
| `cpt` | `27447` | Total knee replacement. Priced at every medium and large hospital |
| `device_id` | `68670191-dbe2-5dbd-a693-5f7118cab2c2` | Cardiovance Pulse DR Pacemaker: 1 recall, 8 adverse events |
| `county` | `Los Angeles` | 6 hospitals across 5 cities |
| `city` + `radius_miles` | `Pasadena`, `15` | Radius search from a city centre |
| `city` | `Modesto` | Valid place with no hospitals → `200 []` |
| `county` | `Nevada` | Nevada County, CA → High Sierra Community Hospital |
| `city` / `zip` (errors) | `Reno` / `89502` / `Pasedena` | Not found / outside California / "did you mean Pasadena" |
| `q` (search) | `knee`, `bayshore`, `castellanos`, `meridian`, `pacemaker` | Each finds a different result group (with word matching) |

**What is fictional:** every hospital, person, manufacturer, brand, phone number (all `555-`), recall number and report key. CCNs are in CMS's numbering format but use made-up numbers. **What is real:** CPT code numbers (the descriptions are paraphrased, not the copyrighted AMA wording), FDA product-code format, NUCC taxonomy codes, CMS comparison wording, California's county names and which county each city is in, and approximate city coordinates.

---

## 7. Risks and open questions

| Risk | Mitigation |
|---|---|
| Hosting needs Postgres with PostGIS and pg_trgm | Confirm the extensions on the chosen host before Phase 9. The fallback is a VM running `docker compose` |
| A public, unauthenticated API could be abused | All responses are capped (`limit ≤ 200`), the data is mock, and the methods are GET-only. Add rate limiting (e.g. `slowapi`) in Phase 6 if the URL is shared widely |
| The behavior changes in §3 could break a client | No client currently depends on the API (the old frontend isn't part of this repo). Recheck before Phase 5 if one is added |
| County search misses hospitals in places not in `ca_places` | The production gazetteer includes Census unincorporated places. If gaps remain, add a `county` column to `hospitals` filled from CMS data, which includes the county |
| The guide says to hide docs, but the goal is public docs | `SHOW_DOCS` defaults to `false`, and the demo deployment sets it to `true` explicitly |

**Open questions for you**
1. Add a `/v1` prefix now? It would change every path, so this plan leaves it out to keep the endpoints the same.

---

## 8. Deviations from the guide

| Guide says | This plan | Reason |
|---|---|---|
| Alembic migrations with date-slug file names | Keep `migrations/NNN_*.sql` | Shared with the ETL. Moving to Alembic can come later |
| SQLAlchemy Core with naming conventions | Raw SQL via psycopg 3 | The queries already exist and depend on PostGIS; the guide's SQL-first advice still applies |
| Singular table names (`post`, not `posts`) | Keep `hospitals`, `providers`, ... | Renaming the schema is out of scope and would break the ETL |
| Hide docs by default | Docs controlled by `SHOW_DOCS` and turned on for the demo | Public testing is a stated goal |
| `BaseSettings` split by domain | One global `Settings` for now | No domain needs its own settings yet. Split when auth or another external service is added |
