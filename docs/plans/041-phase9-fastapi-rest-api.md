# Plan 041 — Phase 9: FastAPI REST API

**Status**: DONE
**Phase**: 9 (API)
**Depends on**: Phases 1-8 (all complete for v0a)

## Context

### Why now

The pipeline works end-to-end — stations onboard, models train, hindcasts run,
skills compute, and forecasts execute. But there is no structured way to consume
the results. The existing API layer has HTML dashboard views and a handful of
ad-hoc `.json` endpoints, but the REST API specified in v0-scope.md §J is
largely unimplemented. Without it, the system is a batch pipeline with no
consumer interface.

### What exists

The API layer (`src/sapphire_flow/api/`) has:

- **App setup**: `__init__.py` creates `FastAPI`, registers 6 routers. No CORS
  middleware. Jinja2 templates for HTML views.
- **Deps**: `deps.py` provides `lifespan()` (SA engine), `get_connection()`
  (yields `sa.Connection`), `get_stores()` (dict of 10 Pg stores — each opens
  its own connection, separate from `get_connection()`).
- **HTML routes**: Dashboard, station list/detail, forecast list/detail, model
  list/detail, observation coverage, table browser.
- **JSON endpoints** (ad-hoc, no Pydantic schemas, raw dicts via `JSONResponse`):
  - `GET /api/v1/health` — DB ping, returns `{"status": "ok"}`
  - `GET /api/v1/stations/{id}/observations.json` — timeseries
  - `GET /api/v1/stations/{id}/forcing.json` — forcing timeseries
  - `GET /api/v1/stations/{id}/baselines.json` — climatological baselines
  - `GET /api/v1/stations/{id}/hindcasts.json` — hindcast data
  - `GET /api/v1/forecasts/{id}/data.json` — forecast ensemble data
  - `GET /api/v1/models/{id}/skill-chart.json` — skill chart series

All existing JSON endpoints query reflected metadata tables directly (not via
stores) and return raw dicts. No collision with new routes — existing endpoints
use `.json` suffix, new ones don't.

### What's missing (v0-scope.md §J)

**Core (this plan):**
```
GET    /api/v1/stations                    # paginated station list
GET    /api/v1/stations/{id}               # station detail (JSON)
GET    /api/v1/stations/{id}/observations  # observations for station
GET    /api/v1/stations/{id}/forecasts     # forecasts for station
GET    /api/v1/forecasts/{id}              # forecast detail + ensemble
GET    /api/v1/alerts                      # alerts (filterable)
POST   /api/v1/alerts/{id}/acknowledge     # acknowledge alert
GET    /api/v1/health                      # exists, enhance with Prefect ping
```

**Deferred (see §Deferred below):**
```
POST   /api/v1/flows/{flow}/trigger        # → v0b (needs auth; users have Prefect CLI)
GET    /api/v1/health/detail               # → v0b (pipeline_health table empty until Flow 4)
```

Plus: Pydantic response schemas, offset-based pagination, CORS middleware,
structured error responses, `orjson` serialization.

---

## Architecture decisions

### D1. New route files, don't refactor existing

Existing `.json` endpoints serve the HTML dashboard (HTMX chart loading). Leave
them as-is. New REST endpoints go in separate route files under `/api/v1/`
prefix. No naming collision (existing use `.json` suffix).

### D2. Use stores, not reflected tables

New endpoints use the typed store layer via `get_stores()`. Stores return frozen
dataclasses, route handlers convert to Pydantic response models. This follows
the "Pydantic at boundaries only" rule.

### D3. Offset-based pagination

v0 has ~170 stations. Forecasts are the only entity that might paginate
meaningfully (~4,760 summaries for 7 days × 4 cycles × 170 stations). At this
scale, offset-based pagination is simpler, debuggable ("page 3 of 12"), and
sufficient. Cursor-based pagination adds complexity (base64 encoding, keyset
queries, opaque tokens) without measurable benefit at v0 volumes.

Response envelope:
```json
{"items": [...], "total": 95, "limit": 50, "offset": 0}
```

Default `limit=50`, max `limit=200`. `total` included for client convenience
(enables page count display). Migration to cursor-based at v1 is a
backend-only change — the envelope shape stays the same (swap `offset`/`total`
for `next_cursor`/`has_more`).

### D4. Polars DataFrame serialization

`ForecastEnsemble.values` is a Polars DataFrame. The route handler pivots it
before constructing the Pydantic response — DataFrame is never exposed to
Pydantic. Members representation: `{valid_times: [...], members: {id: [values]}}`.
Quantiles: `{valid_times: [...], quantiles: {level: [values]}}`.

### D5. Lightweight forecast list (metadata only)

`GET /api/v1/stations/{id}/forecasts` needs forecast summaries without ensemble
data. The existing `PgForecastStore.fetch_forecasts_in_range()` loads full
ensemble data (expensive). Add a thin `fetch_forecast_summaries()` method to
`PgForecastStore` that queries the `forecasts` table only (no join to
`forecast_values`), returning a lightweight summary type.

### D6. Write dependency for alert acknowledgement

`get_stores()` never commits. The acknowledge endpoint is the only write
endpoint. Use `engine.begin()` for explicit transaction semantics — commits on
clean exit, rolls back on exception (HTTPException, etc.):

```python
def get_connection_rw(request: Request) -> Generator[sa.Connection, None, None]:
    engine: sa.Engine = request.app.state.engine
    with engine.begin() as conn:
        yield conn
```

### D7. Single connection per request (fix existing `deps.py`)

Currently `get_connection()` and `get_stores()` each open **separate**
connections via `engine.connect()`. This wastes pool connections. Refactor
`get_stores()` to accept the connection from `get_connection()` via FastAPI's
dependency caching:

```python
def get_stores(conn: sa.Connection = Depends(get_connection)) -> dict[str, Any]:
    return {
        "station_store": PgStationStore(conn),
        "obs_store": PgObservationStore(conn),
        ...
    }
```

FastAPI caches dependencies per-request, so `get_connection()` yields the same
connection to both `get_stores()` and any other dependency that uses it. One
connection per request instead of two.

### D8. `orjson` for serialization

Already a dependency. Use `ORJSONResponse` as default response class on the
API router. 3-10x faster than stdlib json for large forecast payloads.

### D9. CORS with explicit origin list

`docs/standards/security.md` says "explicit list, never `*`". Read origins from
`SAPPHIRE_CORS_ORIGINS` env var (comma-separated). If unset, CORS middleware is
**not added** (deny all cross-origin). This aligns with security.md and the
principle of least privilege.

---

## Store protocol extensions

Three gaps in the existing store protocols must be filled:

### S1. `AlertStore.fetch_alert()` — fetch single alert by ID

The existing protocol has no way to fetch a single alert by ID. Both the
acknowledge endpoint (needs 404/409 checks) and future auth (needs station_id
access check) require this.

Add to `AlertStore` protocol and `PgAlertStore`:
```python
def fetch_alert(self, alert_id: AlertId) -> Alert | None: ...
```

### S2. `AlertStore.fetch_alerts()` — paginated alert list with filters

The existing `fetch_active_alerts()` only filters by station_id + source and
hardcodes `WHERE status != 'resolved'`. The `fetch_alert_history()` requires
station_id as mandatory. Neither supports `level` filtering or offset
pagination.

Add to `AlertStore` protocol and `PgAlertStore`:
```python
def fetch_alerts(
    self,
    *,
    station_id: StationId | None = None,
    source: AlertSource | None = None,
    status: AlertStatus | None = None,
    level: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Alert], int]:  # (items, total_count)
    ...
```

### S3. `ForecastStore.fetch_forecast_summaries()` — metadata-only query

Queries `forecasts` table only (no join to `forecast_values`). Returns a
lightweight `ForecastSummaryRow` frozen dataclass (header fields, no ensemble).
Supports offset pagination:
```python
def fetch_forecast_summaries(
    self,
    station_id: StationId,
    start: UtcDatetime,
    end: UtcDatetime,
    *,
    model_id: ModelId | None = None,
    parameter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ForecastSummaryRow], int]:  # (items, total_count)
    ...
```

`ForecastSummaryRow` fields: `id`, `station_id`, `model_id`, `issued_at`,
`parameter`, `representation`, `status`, `qc_status`, `nwp_cycle_source`,
`created_at`. Note: `input_quality` is **excluded** — it is not persisted in
the `forecasts` table (defaults to `FULL` on the domain type). Add it to the DB
and summary when it becomes operationally needed (v0b NWP degradation tracking).

---

## Implementation steps

### Layer 0 — Infrastructure (no dependencies between steps)

**Step 0.1: Pydantic response schemas** `api/schemas.py` -- DONE

16 Pydantic models. Key schemas:
- `GeoCoordResponse` — lon, lat, altitude_masl
- `StationSummary` — id, code, name, location, kind, status, network, ownership, measured_parameters
- `StationDetail` — extends summary with thresholds, model_assignments, weather_sources, basin_id, forecast_targets, timestamps
- `ThresholdResponse`, `ModelAssignmentResponse`, `WeatherSourceResponse`
- `ObservationResponse` — id, station_id, timestamp, parameter, value, source, qc_status, qc_flags (row-based, one object per observation — matches domain model, not the columnar format of the legacy `.json` endpoint)
- `ForecastSummary` — id, station_id, model_id, issued_at, parameter, representation, status, qc_status, nwp_cycle_source, created_at
- `ForecastDetail` — extends summary with ensemble, nwp_cycle, warm_up, combination fields
- `EnsembleResponse` — representation, parameter, units, horizon, time_step_seconds, member_count, valid_times, series (dict)
- `AlertResponse` — all Alert fields as serializable types
- `AcknowledgeRequest` — acknowledged_by (UUID string)
- `AcknowledgeResponse` — id, status, acknowledged_at (confirms the state change)
- `HealthResponse` — status, prefect_status, checked_at
- `PaginatedResponse[T]` — generic wrapper: items, total, limit, offset
- `ErrorResponse` — error, detail

**Step 0.2: Error handlers** `api/errors.py` -- DONE

Structured error response: `{"error": "not_found", "detail": "..."}`

### Layer 1 — Dependency + middleware wiring (depends on 0.x)

**Step 1.1: Refactor `deps.py`** -- DONE

Single connection per request. PgAlertStore added. `get_connection_rw()` with `engine.begin()`.

**Step 1.2: Update `__init__.py`** -- DONE

CORS middleware (deny-all default), error handlers, new routers registered.
ORJSONResponse dropped (deprecated in current FastAPI — native Pydantic serialization).

### Layer 2 — Route implementations -- ALL DONE

**Step 2.1: Station endpoints** `api/routes/api_stations.py` -- DONE

| Endpoint | Store method | Notes |
|---|---|---|
| `GET /api/v1/stations` | `StationStore.fetch_all_stations()` | Offset pagination in Python (small set). Optional `?kind=` and `?status=` filters. |
| `GET /api/v1/stations/{id}` | `StationStore.fetch_station()` + `fetch_thresholds()` + `fetch_model_assignments()` + `fetch_weather_sources()` | Accepts UUID. 404 if not found. |
| `GET /api/v1/stations/{id}/observations` | `ObservationStore.fetch_observations()` | Required: `parameter`, `start`, `end`. Optional: `qc_status`. Returns `list[ObservationResponse]` (not paginated — bounded by time range). |
| `GET /api/v1/stations/{id}/forecasts` | new `ForecastStore.fetch_forecast_summaries()` | Metadata-only query. Offset pagination. Optional `?model_id=`, `?parameter=`. Default last 7 days. |

**Step 2.2: Forecast detail endpoint** `api/routes/api_forecasts.py` -- DONE

| Endpoint | Store method | Notes |
|---|---|---|
| `GET /api/v1/forecasts/{id}` | `ForecastStore.fetch_forecast()` | Pivots `ForecastEnsemble.values` (Polars DF) to JSON. Handles sentinel model_ids (`_pooled`, `_bma`, `_consensus`). 404 if not found. |

**Step 2.3: Alert endpoints** `api/routes/api_alerts.py` -- DONE

| Endpoint | Store method | Notes |
|---|---|---|
| `GET /api/v1/alerts` | new `AlertStore.fetch_alerts()` | Filters: `?status=`, `?source=`, `?station_id=`, `?level=`. Offset pagination. |
| `POST /api/v1/alerts/{id}/acknowledge` | `AlertStore.fetch_alert()` then `AlertStore.acknowledge_alert()` | Uses `get_connection_rw()`. Fetch first to check existence (404) and status (409 if resolved). Request body: `AcknowledgeRequest`. Response: `AcknowledgeResponse`. |

**Step 2.4: Enhance health endpoint** in existing `api/routes/health.py` -- DONE

Enhance the existing `GET /api/v1/health` to include a Prefect heartbeat
check (HTTP ping to `PREFECT_API_URL/health` via `httpx.Client`). Response
becomes `HealthResponse` with `status`, `prefect_status`, `checked_at`. If
Prefect is unreachable, `prefect_status = "unreachable"` but overall status
remains based on DB connectivity only (Prefect being down is informational,
not a 503).

**Step 2.5: Store protocol extensions** -- DONE

Implemented S1, S2, S3. All 1024 tests pass, zero regressions.
- `fetch_alert()` and `fetch_alerts()` added to `AlertStore` protocol + `PgAlertStore` + `FakeAlertStore`
- `fetch_forecast_summaries()` added to `ForecastStore` protocol + `PgForecastStore` + `FakeForecastStore`
- `ForecastSummaryRow` frozen dataclass created in `types/forecast_summary.py`
- 24 new integration tests with full value assertions (enum identity, JSONB round-trip, UTC preservation, half-open intervals, deterministic ordering, pagination item identity)

### Layer 3 — Router registration -- DONE (included in Layer 1.2)

### Layer 4 — Tests -- ALL DONE (31 unit tests + 24 integration tests)

**Step 4.1: Test fixtures** `tests/unit/api/conftest.py`

- `TestClient` fixture with dependency overrides
- Override `get_stores()` with a dict of fake stores (from `tests/fakes/fake_stores.py`)
- Override `get_connection_rw()` with a no-op connection
- Factory helpers: reuse existing `make_station_config()`, `make_alert()`, etc.

**Step 4.2: Schema tests** `tests/unit/api/test_schemas.py`

- Round-trip: domain object → Pydantic model → JSON → deserialize back
- Edge cases: None fields, empty lists, enum serialization

**Step 4.3-4.6: Route tests** `tests/unit/api/test_api_*.py`

Per route module. Each tests:
1. Happy path (200, correct response shape)
2. 404 for missing resources
3. 400 for invalid query params
4. Pagination (`total`, `limit`, `offset`, empty pages)
5. Filter parameters

**Step 4.7: Store extension integration tests**

- `fetch_alert()`: round-trip store → fetch by ID
- `fetch_alerts()`: filter by status, source, level; offset pagination; total count
- `fetch_forecast_summaries()`: metadata-only query, pagination, filters

---

## Dependency graph

```
Step 0.1 (schemas)  ─┐
Step 0.2 (errors)   ─┘ Layer 0 (parallel)
          │
Step 1.1 (deps.py)  ─┐ Layer 1 (parallel)
Step 1.2 (__init__)  ─┘
          │
Step 2.1 (stations)  ─┐
Step 2.2 (forecasts)  │
Step 2.3 (alerts)     ├─ Layer 2 (parallel)
Step 2.4 (health)     │
Step 2.5 (store exts) ┘
          │
Step 3.1 (register)
          │
Step 4.1 (conftest)    ─┐
Step 4.2 (schemas)      │
Step 4.3-4.6 (routes)   ├─ Layer 4 (parallel)
Step 4.7 (store tests)  ┘
```

## Files to create

| File | Purpose |
|---|---|
| `src/sapphire_flow/api/schemas.py` | Pydantic response/request models |
| `src/sapphire_flow/api/errors.py` | Structured error handlers |
| `src/sapphire_flow/api/routes/api_stations.py` | Station REST endpoints |
| `src/sapphire_flow/api/routes/api_forecasts.py` | Forecast detail REST endpoint |
| `src/sapphire_flow/api/routes/api_alerts.py` | Alert REST endpoints |
| `src/sapphire_flow/types/forecast_summary.py` | `ForecastSummaryRow` frozen dataclass |
| `tests/unit/api/__init__.py` | Package marker |
| `tests/unit/api/conftest.py` | TestClient + fake store fixtures |
| `tests/unit/api/test_schemas.py` | Schema round-trip tests |
| `tests/unit/api/test_api_stations.py` | Station endpoint tests |
| `tests/unit/api/test_api_forecasts.py` | Forecast endpoint tests |
| `tests/unit/api/test_api_alerts.py` | Alert endpoint tests |
| `tests/unit/api/test_api_health.py` | Health endpoint tests |

## Files to modify

| File | Change |
|---|---|
| `src/sapphire_flow/api/__init__.py` | CORS, error handlers, ORJSONResponse, new router imports |
| `src/sapphire_flow/api/deps.py` | Fix connection lifecycle (D7), add `PgAlertStore`, add `get_connection_rw()` |
| `src/sapphire_flow/api/routes/health.py` | Add Prefect heartbeat to existing health endpoint |
| `src/sapphire_flow/protocols/stores.py` | Add `fetch_alert()`, `fetch_alerts()`, `fetch_forecast_summaries()` |
| `src/sapphire_flow/store/alert_store.py` | Implement `fetch_alert()` and `fetch_alerts()` |
| `src/sapphire_flow/store/forecast_store.py` | Implement `fetch_forecast_summaries()` |
| `tests/fakes/fake_stores.py` | Add new methods to `FakeAlertStore`, `FakeForecastStore` |

## What NOT to do (v0 scope limits)

- No auth (deferred to Plan 042, post-v0 deployment — see Deferred section)
- No client SDK (deferred — premature before API stabilizes)
- No CSV export (no identified consumer; JSON-to-CSV trivial downstream)
- No flow trigger endpoint (users have Prefect CLI/UI; needs auth first)
- No `/health/detail` (pipeline_health table empty until Flow 4)
- No cursor-based pagination (offset sufficient at v0 volumes)
- No rate limiting (Caddy handles this; no auth = no per-key throttling)
- No response caching headers (Caddy can add these)
- No OpenAPI schema customization beyond FastAPI defaults
- Do not refactor existing `.json` endpoints — they serve the dashboard

## Deferred items (tracked for future plans)

| Item | Target | Rationale |
|---|---|---|
| API key auth + per-station access | Plan 042 (v0b) | v0 is 1-2 team members, SSH-only VM, Swiss public data. Auth adds 15+ files for zero v0 consumers. Ship the API first, add auth when external consumers appear. |
| Client SDK | Plan 042 (v0b) | Pre-1.0 API will change. SDK is throwaway before API stabilizes. Integration tests via httpx give same coverage. |
| CSV export | v0c | No identified consumer. Nested structure flattening (ensembles, QC flags) is underspecified. Downstream users can transform JSON trivially. |
| Flow trigger endpoint | v0b (with auth) | Unprotected write endpoint is a security risk. Users have Prefect CLI/UI. Add with auth in Plan 042. |
| `/health/detail` | v0c (with Flow 4) | `pipeline_health` table is empty (Flow 4 deferred). Full component status is monitoring-dashboard scope. |
| Cursor-based pagination | v1 | Offset sufficient at v0 volumes (~170 stations, ~5K forecasts/week). Backend-only migration when needed. |
| `StoreBundle` typed dict | v0b | Replace `dict[str, Any]` from `get_stores()` with a typed dataclass. Low-risk cleanup, catches key typos at type-check time. |

## Verification

1. `uv run ruff check --fix && uv run ruff format`
2. `uv run pyright src/sapphire_flow/api/`
3. `uv run pytest tests/unit/api/ -v` — all new tests pass
4. `uv run pytest tests/ -x` — no regressions
5. Manual smoke test with `docker compose up`:
   - `curl localhost:8000/api/v1/stations | jq .` — paginated station list
   - `curl localhost:8000/api/v1/stations/{id} | jq .` — station detail
   - `curl localhost:8000/api/v1/stations/{id}/observations?parameter=discharge&start=...&end=... | jq .`
   - `curl localhost:8000/api/v1/alerts | jq .` — alert list
   - `curl localhost:8000/api/v1/health | jq .` — health with Prefect status
   - Existing HTML dashboard still works at `/`

## Doc updates

- Update `docs/v0-scope.md` §J to mark implemented endpoints and deferred endpoints
- Update Phase 9 status in `docs/v0-scope.md` §H
