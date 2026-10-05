# SurgiCAL API Guide

This guide explains how the SurgiCAL API is built, why it uses FastAPI, and how well it follows REST conventions. Its examples come from the code in `api/`.

---

## 1. What the API does

The API serves read-only data to the React frontend (`http://localhost:5173`). A user can:

- find **hospitals** and see their Medicare quality ratings
- find **providers** (surgeons and doctors) and see where they practice
- compare **prices** for a procedure (by CPT code) across hospitals and insurers
- check the **devices** used in a procedure, with their FDA recalls and adverse-event reports
- run one **search** across all of the above

Every endpoint is `GET`. The data is loaded into Postgres by the ETL pipeline in `etl/`, and the API only reads it.

---

## 2. Project layout

```
api/
├── main.py            # Creates the FastAPI app, sets up CORS, mounts the routers
├── dependencies.py    # DB connection dependency and PostGIS SQL helpers
├── routers/
│   ├── hospitals.py   # /hospitals
│   ├── providers.py   # /providers
│   ├── prices.py      # /prices
│   ├── devices.py     # /devices
│   └── search.py      # /search
├── requirements.txt   # fastapi, uvicorn, psycopg2, python-dotenv
└── Dockerfile         # Runs `uvicorn api.main:app` on port 8000
```

The pieces fit together in three layers:

| Layer | File | Job |
|---|---|---|
| **App** | `main.py` | Creates `FastAPI()`, adds middleware, and mounts each router under a URL prefix |
| **Routers** | `routers/*.py` | One file per resource. Each function handles one URL |
| **Dependencies** | `dependencies.py` | Shared code that routes ask for, mainly the database connection |

There is no service or repository layer. Each route function builds its SQL and runs it directly with `psycopg2`, and there are no ORM models or Pydantic schemas. That keeps the code short and easy to follow. Section 6 covers what that costs.

---

## 3. Following one request

Here is what happens for `GET /prices?cpt=27447&lat=34.05&lng=-118.24&payer=aetna`:

```
Browser (React, localhost:5173)
   │  GET /prices?cpt=27447&lat=34.05&lng=-118.24&payer=aetna
   ▼
Uvicorn (ASGI server, port 8000)
   │  turns raw HTTP into an ASGI event
   ▼
CORSMiddleware                      ← main.py
   │  checks the Origin header and adds Access-Control-* headers
   ▼
FastAPI router matching
   │  "/prices" prefix → prices.router, ""  → search_prices()
   ▼
Parameter parsing and validation    ← based on the function signature
   │  cpt: str (required)       → missing? automatic 422 error
   │  lat/lng: float | None     → "abc"? automatic 422 error
   │  limit: int = Query(50, le=200) → 500? automatic 422 error
   ▼
Dependency injection
   │  conn = Depends(get_db) → opens a psycopg2 connection
   ▼
search_prices() runs
   │  builds a WHERE clause, adds PostGIS distance SQL, runs the query
   ▼
Response
   │  list of RealDictRow → FastAPI's jsonable_encoder → JSON
   ▼
Dependency cleanup
      get_db's `finally:` closes the connection
```

Notice that the route function never checks whether `cpt` is present or whether `lat` is a number. FastAPI does that from the type hints before the function runs. Section 4 explains how.

---

## 4. FastAPI: what it is and why it matters

### What it is

FastAPI is a Python web framework built on two libraries:

- **Starlette** handles the web side: routing, middleware, requests and responses. It runs on **ASGI**, the async successor to WSGI.
- **Pydantic** handles data: it parses, validates and serializes values based on Python type hints.

**Uvicorn** is the server that actually listens on port 8000 and passes requests to the app (see the `CMD` line in the `Dockerfile`).

### The main idea: type hints are the source of truth

In Flask, a route usually looks like this:

```python
# Flask style
@app.route("/hospitals")
def list_hospitals():
    state = request.args.get("state", "CA")
    limit = int(request.args.get("limit", 50))   # crashes on "abc"
    if limit > 200:
        return {"error": "limit too large"}, 400  # written by hand
    ...
```

In FastAPI (`routers/hospitals.py`), the function signature does all of that:

```python
@router.get("")
def list_hospitals(
    state: str = Query("CA"),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    radius_miles: float = Query(25),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    conn=Depends(get_db),
):
```

From this one signature, FastAPI:

1. **Parses** each value from the query string and converts it to the right type.
2. **Validates** it. `le=200` rejects `limit=500` with a 422 response that names the field and the reason.
3. **Documents** it. Every parameter, default and limit appears in the OpenAPI spec at `/openapi.json` and in the interactive UI at `/docs`.
4. **Injects** the database connection through `Depends`.

The docs, the validation and the code all come from the same place, so they can't drift apart. That is the main reason FastAPI matters.

### Where each FastAPI feature appears in this code

| Feature | Where | What it does here |
|---|---|---|
| `FastAPI()` app object | `main.py` | Sets the title, description and version shown in `/docs` |
| `APIRouter` and `include_router(prefix=...)` | `main.py`, every router | Splits the API by resource. Each router file uses paths relative to its prefix |
| `tags=[...]` | `main.py` | Groups the endpoints into sections in `/docs` |
| Path parameters | `/{ccn}`, `/{npi}`, `/{device_id}` | Take a value out of the URL and pass it to the function argument with the same name |
| `Query(...)` | every list endpoint | `...` makes a parameter required (`cpt`, `q`). `le=` and `min_length=` add limits. `description=` shows up in the docs |
| `Depends(get_db)` | every route | Dependency injection. See below |
| Generator dependency (`yield`) | `dependencies.py: get_db` | Code before `yield` runs before the request, code after runs once the response is sent. This gives setup and teardown, like a context manager |
| `HTTPException(404)` | hospital, provider and device detail endpoints | Turns into a JSON error: `{"detail": "Hospital not found"}` |
| Middleware | `CORSMiddleware` in `main.py` | Lets the Vite dev server call the API from a browser |
| Automatic JSON encoding | every return value | Dicts, lists, `Decimal`, `date` and so on are converted to JSON for you |
| Automatic docs | `/docs`, `/redoc`, `/openapi.json` | Generated for free. **Try this first** when exploring the API |

### Dependency injection, explained

```python
# dependencies.py
def get_db() -> Generator:
    conn = get_db_connection()
    try:
        yield conn          # ← the route runs here
    finally:
        conn.close()        # ← runs after the response, even if the route raised an error
```

```python
# a route
def get_hospital(ccn: str, conn=Depends(get_db)):
```

Why do it this way instead of calling `get_db_connection()` inside each route?

- **Guaranteed cleanup.** No route can forget to close its connection.
- **Easy to test.** In tests you can write `app.dependency_overrides[get_db] = fake_db` to swap in a test database without touching any route.
- **One place to change.** Adding a connection pool, or auth (`Depends(get_current_user)`), means writing one function, not editing every route.

### `def` vs `async def`

All the routes here are plain `def`. FastAPI runs those in a **thread pool**, so a slow database call blocks one worker thread rather than the whole server. That is the right choice because `psycopg2` is a blocking driver. Using `async def` with `psycopg2` would be a bug: a blocking call inside the event loop stalls every request. Switching to `async def` would only make sense with an async driver such as `asyncpg` or `psycopg` 3.

### FastAPI compared with the alternatives

| | FastAPI | Flask | Django REST Framework |
|---|---|---|---|
| Validation | Automatic, from type hints | By hand or with extensions | Serializer classes |
| API docs | Built in (OpenAPI) | Needs extensions | Needs extensions |
| Async | Built in (ASGI) | Limited | Partial |
| ORM and admin | None (you choose) | None | Included |
| Best for | JSON APIs, typed contracts | Small apps, flexibility | Full apps with an admin and ORM |

This project is a read-only JSON API over a database filled by a separate ETL job. It needs no admin, no ORM and no templates, which is the use case FastAPI is designed for.

---

## 5. The endpoints

| Method and path | What it returns | Key parameters |
|---|---|---|
| `GET /health` | `{"status": "ok"}`. Doesn't check the database | – |
| `GET /hospitals` | Hospitals in a state, optionally within a radius, nearest first | `state`=CA, `lat`, `lng`, `radius_miles`=25, `limit`, `offset` |
| `GET /hospitals/{ccn}` | One hospital and its CMS quality metrics, or 404 | – |
| `GET /hospitals/{ccn}/providers` | Doctors at the hospital, busiest first | `limit`, `offset` |
| `GET /providers` | Doctors, filterable by specialty, city or radius | `specialty`, `state`=CA, `city`, `lat`, `lng`, `radius_miles`=25 |
| `GET /providers/{npi}` | One doctor with metrics and hospital affiliations, or 404 | – |
| `GET /prices` | Prices for a CPT code, cheapest first, with hospital quality | `cpt` (required), `payer`, `lat`, `lng`, `radius_miles`=50 |
| `GET /prices/compare` | One CPT code's prices at chosen hospitals, side by side | `cpt`, `ccns` (comma-separated) |
| `GET /devices` | Device catalog, with typo-tolerant brand search | `product_code`, `manufacturer`, `medical_specialty`, `q` |
| `GET /devices/by-procedure/{cpt}` | Devices used in a procedure | – |
| `GET /devices/{device_id}` | One device, its last 10 recalls and an adverse-event count by type, or 404 | – |
| `GET /devices/{device_id}/recalls` | The device's full recall history | `limit`, `offset` |
| `GET /devices/{device_id}/adverse-events` | FDA MAUDE reports for the device | `event_type`, `limit`, `offset` |
| `GET /search` | Typo-tolerant search across procedures, providers, hospitals and devices | `q` (at least 2 characters), `limit` (at most 50) |

**Database features the API relies on:**
- **PostGIS.** `spatial_where()` uses `ST_DWithin` for "within N miles", and `distance_select()` uses `ST_Distance` to compute miles.
- **pg_trgm.** The `%` operator and `similarity()` power the typo-tolerant search in `/search` and `/devices?q=`.

---

## 6. REST evaluation

### What it does well

- **Resources are nouns.** Paths are plural nouns (`/hospitals`, `/devices`), with no verbs like `/getHospital`.
- **Collections and items follow the usual pattern:** `/hospitals` for the list and `/hospitals/{ccn}` for one.
- **Related data is nested:** `/hospitals/{ccn}/providers` and `/devices/{id}/recalls` show the relationships clearly.
- **Natural keys are used as IDs.** CCN, NPI and CPT are industry-standard identifiers, so URLs are stable and meaningful.
- **Filtering uses query parameters, and identity uses the path.** That is the correct split.
- **Paging is consistent.** `limit` and `offset` work the same way on every list, with a hard maximum of 200.
- **Status codes are correct.** Missing items return 404 and invalid input returns 422 automatically.
- **The method matches the behavior.** Everything is `GET`, which is safe and idempotent, and CORS only allows `GET`.
- **SQL injection is prevented.** User values always go through `%s` parameters. The f-strings only insert fragments the code itself controls, such as `where_sql` and `order`.

### Gaps and how to fix them

Listed roughly from most to least important.

#### 1. No response models, so the response format is undocumented
Routes return raw database rows. As a result, `/docs` shows *every* response as an untyped `"string"`, the frontend can't generate types from the spec, and renaming a database column silently changes the API.

**Fix:** define Pydantic models and declare them on the routes:
```python
class HospitalSummary(BaseModel):
    ccn: str
    name: str
    city: str | None
    distance_miles: float | None

@router.get("", response_model=list[HospitalSummary])
```
This is the FastAPI feature with the biggest payoff that the project doesn't use yet.

#### 2. A new database connection for every request
`get_db()` opens a fresh SSL connection to RDS on every request and closes it afterward. Opening the connection probably takes longer than most of these queries, and heavy traffic could use up Postgres's connection limit.

**Fix:** create a `psycopg2.pool.ThreadedConnectionPool` once, at startup (with FastAPI's `lifespan`). Then have `get_db` borrow a connection from the pool and return it, instead of connecting and closing.

#### 3. Some input isn't validated
- `offset=-1` → Postgres rejects it → **500** instead of 422. Add `ge=0`.
- `limit=0` or negative values are accepted. Add `ge=1`.
- `lat` and `lng` have no range. Add `ge=-90, le=90` and `ge=-180, le=180`.
- `radius_miles` has no maximum, so a huge radius scans the whole table.
- `lat` without `lng` (or the reverse) is silently ignored instead of rejected.
- `/prices/compare?ccns=...` accepts any number of IDs.

#### 4. The state filter and the radius filter fight each other
`/hospitals`, `/providers` and `/prices` always filter by `state` (default `CA`), *and also* by radius. A search near Lake Tahoe never shows Nevada hospitals. If a location is given, the state filter should probably be dropped unless `state` was passed explicitly.

#### 5. List responses have no paging information
Lists return a bare JSON array, so the client can't tell how many results exist in total or whether there's another page. A common pattern is:
```json
{ "items": [...], "total": 1234, "limit": 50, "offset": 0 }
```
At minimum, add a `total` count or a `next` link. Bare arrays also make it hard to add metadata later without breaking the frontend.

#### 6. Response shapes are inconsistent
Most endpoints return an array, but `/search` returns an object with four groups, and the detail endpoints mix item fields with nested lists (`affiliations`, `recent_recalls`). Each choice makes sense alone. Writing them down as Pydantic models (gap 1) makes the shapes explicit.

#### 7. Database errors become plain 500s
A database outage or SQL error returns a generic `Internal Server Error`. Adding an exception handler for `psycopg2.Error` would give a structured 503 or 500 response and log the details. Also, `/health` should run `SELECT 1`, so that load balancers and Docker can tell when the database is down.

#### 8. No versioning
Without a version prefix like `/v1`, any breaking change hits every client at once. The fix is one line: `include_router(..., prefix="/v1/hospitals")`.

#### 9. Procedures aren't a resource of their own
CPT codes appear in `/prices?cpt=`, `/devices/by-procedure/{cpt}` and `/search`, but there is no `/procedures` endpoint. A more consistent structure would be:
```
GET /procedures                  # list or search CPT codes
GET /procedures/{cpt}            # description, category, is_surgical
GET /procedures/{cpt}/prices     # replaces /prices?cpt=
GET /procedures/{cpt}/devices    # replaces /devices/by-procedure/{cpt}
```

#### 10. Smaller items
- `/prices/compare` takes a comma-separated string. FastAPI supports repeated parameters directly: `ccns: list[str] = Query()` handles `?ccns=A&ccns=B` and documents it as an array.
- The CORS origin is hard-coded to `localhost:5173`. Read it from an environment variable so production can work.
- There is no rate limiting or auth. That's acceptable for public data, but `/search` runs four similarity queries per call, which makes it the endpoint to protect first.
- There are no tests. FastAPI's `TestClient` together with `dependency_overrides[get_db]` makes route tests short to write.

### Scorecard

| Area | Rating | Notes |
|---|---|---|
| Resource naming and URLs | Good | Plural nouns, sensible nesting, natural keys |
| Use of HTTP methods and status codes | Good | GET-only, correct 404 and 422 |
| Input validation | Fair | Main limits are in place, but offset, coordinates and radius are unchecked |
| Response contract | Weak | No response models, so the docs show no response shapes |
| Paging | Fair | Consistent, but no total count or next link |
| Performance | Fair | Indexed PostGIS and trigram queries, but no connection pooling |
| Production readiness | Weak | No versioning, health check ignores the database, hard-coded CORS, no tests |

---

## 7. Exploring the API yourself

```bash
make install-api
make run-api                         # needs api/.env with DB_* variables
```

`make run-api` runs `.venv/bin/uvicorn`. If you type plain `uvicorn` without activating the venv (`source .venv/bin/activate`), your shell may run a global or conda copy with mismatched packages.

Then open:
- **http://localhost:8000/docs** to try each endpoint in the browser (Swagger UI)
- **http://localhost:8000/redoc** for read-only reference docs
- **http://localhost:8000/openapi.json** for the raw spec, which can generate client code

Things to try, in order:
1. Call `/prices` **without** `cpt` and look at the 422 response FastAPI writes for you.
2. Call `/hospitals?limit=500` to see the `le=200` limit enforced.
3. Call `/hospitals?offset=-1` to see gap 3, a 500 where a 422 should be.
4. Look at the response section of any endpoint in `/docs` and notice there's no response schema (gap 1). Then add a `response_model` to one route and reload.
5. Add `ge=0` to an `offset` parameter, and watch both the docs and the error change with no other code.
