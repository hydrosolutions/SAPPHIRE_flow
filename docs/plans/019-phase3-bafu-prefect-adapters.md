---
status: READY
created: 2026-04-08
scope: Phase 3a — BAFU river observation adapter + Prefect status adapter
depends_on: []
---

# 019 — BAFU + Prefect Production Adapters

## Problem

Phase 3 (adapters) is ~30% complete. Protocols, fakes, ForecastInterfaceAdapter,
StoreBackedReanalysisSource, and CAMELSCH utilities exist. No production adapters
for operational data ingest (BAFU river observations) or pipeline monitoring
(Prefect flow status) have been built yet.

## Scope

Build two production adapters that implement existing Protocols. Both are
independent and can be implemented in parallel.

**Why PrefectStatusAdapter now?** Flow 4 (pipeline monitoring) is deferred to
v0c+, but the `/api/v1/health` endpoint (Phase 9) requires a Prefect heartbeat
check (v0-scope.md line 34, line 551). This adapter satisfies that dependency.
It also serves `GET /api/v1/health/detail` (v0-scope.md line 550), which returns
detailed component status. **Note**: this adapter does **not** populate the
`pipeline_health` table — that table exists but remains empty until Flow 4 is
implemented (v0-scope.md line 34).

### Step 1: HydroScraperAdapter (BAFU LINDAS)

**Create**:
- `src/sapphire_flow/adapters/hydro_scraper.py`
- `tests/integration/adapters/__init__.py` (new directory)
- `tests/integration/adapters/test_hydro_scraper.py`

**Implements**: `StationDataSource` Protocol (`protocols/adapters.py:27-33`)

**Design**:
- Class `HydroScraperAdapter(endpoint: str, http_client: httpx.Client)`
- `fetch_observations(station_configs: list[StationConfig], since: dict[StationId, UtcDatetime]) -> list[RawObservation]`
  - Iterates `station_configs`, looks up `since[station_config.id]` per station
- `_build_sparql_query(site_code: str, since: UtcDatetime) -> str` — new SPARQL
  query with time-range filter (`FILTER(?measurementTime >= ?since)`). The
  reference scraper (`hydro_data_scraper/scrapers/lindas_sparql_scraper.py`) is
  snapshot-only (latest observation, no time filter) — the query here must be
  written from scratch. The reference is useful only for the LINDAS graph URI
  (`https://lindas.admin.ch/foen/hydro`) and predicate URI patterns.
- `_parse_bindings(bindings: list[dict], station_id: StationId) -> list[RawObservation]`
  — validates bindings via a thin Pydantic model (`SparqlBinding`) at the
  boundary, then converts to `RawObservation` frozen dataclasses. Strips base
  URI prefix from predicate URIs to extract parameter names.
- Uses `StationConfig.code` as the site code in the SPARQL subject URI
  (e.g., `.../river/observation/{code}`)
- **SPARQL injection guard**: Before interpolation, validate `code` against
  `r'^[A-Za-z0-9_\-\.]+$'`. Raise `ValueError` if it fails. This covers
  numeric BAFU codes and alphanumeric codes used by other agencies (e.g.,
  Afghanistan). Characters that could break SPARQL strings or URIs (`'`, `"`,
  `<`, `>`, `{`, `}`, `\`, whitespace) are rejected.
- Parameter mapping (after URI stripping): `discharge` -> `"discharge"`,
  `waterLevel` -> `"water_level"`, `waterTemperature` -> `"water_temperature"`
- Source: `ObservationSource.MEASURED`
- Timestamps: `ensure_utc()` on parsed ISO 8601
- Endpoint: `https://lindas.admin.ch/query` — sourced from deployment config
  (`config.toml` `[adapters.river_stations]` section), never from user input
  (OWASP A10 SSRF). Add `endpoint` key to the existing
  `[adapters.river_stations]` section in both `config.toml` and
  `docs/spec/config-reference.toml`.
- LINDAS is a public, unauthenticated endpoint — no API key required. If
  authentication is ever added, the secret must follow the Docker secrets
  pattern (`/run/secrets/`), not environment variables.
- Dedup at store level (upsert), not in adapter

**HTTP details** (raw `httpx`, no SPARQLWrapper):
- POST body: `query=<url-encoded SPARQL>` with
  `Content-Type: application/x-www-form-urlencoded`
- Header: `Accept: application/sparql-results+json`
- Set a default timeout on the `httpx.Client` (e.g., 30s connect, 60s read)

**Error handling**:
- Per-station: `except (httpx.HTTPError, ValueError, KeyError) as exc:` — log
  and continue. Let programming errors (`TypeError`, `AttributeError`) propagate.

**Logging** (structlog via `structlog.get_logger()`, per `docs/standards/logging.md`):
- `observation.fetch_started` — DEBUG, context: `station_id`, `since`
- `observation.http_response` — DEBUG, context: `station_id`, `url`,
  `status_code`, `response_bytes` (never log response body content)
- `observation.fetch_completed` — INFO, context: `station_id`, `duration_ms`,
  `record_count`. Use `time.perf_counter()` and `round(..., 1)` for `duration_ms`.
- `observation.fetch_failed` — WARNING, context: `station_id`, `error`
- `observation.parse_failed` — WARNING, context: `station_id`, `raw_timestamp`

**New dependency**: `uv add httpx`

**Tests** (integration — per `architecture-context.md` line 2924):
Fake HTTP transport returning canned SPARQL JSON responses.
- Happy path: multiple parameters returned for multiple stations
- Single station failure: others still succeed, warning logged
- Empty bindings: returns empty list
- Malformed timestamp: logged + skipped, other records returned
- Invalid station code (SPARQL injection chars): raises `ValueError`
- **Contract test**: A real LINDAS response for one station is recorded once and
  committed as a JSON fixture file (VCR-style, per `architecture-context.md`
  line 2924). The test asserts `_parse_bindings` round-trips it to valid
  `RawObservation` objects. Detects upstream schema changes. The recording
  script (not the test itself) is gated behind `@pytest.mark.network`.

**Key references**:
- SPARQL reference (snapshot-only): `lindas_sparql_scraper.py` in the sibling
  repo `hydro_data_scraper/` (not in this repository)
- Design doc: `docs/design/v0-flow2-observation-pipeline.md` lines 283-315
- Domain type: `src/sapphire_flow/types/observation.py` (RawObservation)

### Step 2: PrefectStatusAdapter

**Create**:
- `src/sapphire_flow/adapters/prefect_status.py`
- `tests/integration/adapters/test_prefect_status.py`

**Implements**: `PipelineStatusSource` Protocol (`protocols/adapters.py:59-65`)

**Design**:
- Class `PrefectStatusAdapter(client: SyncPrefectClient)` — must use
  `SyncPrefectClient` (not `PrefectClient`, which is async-only). Obtain via
  `get_client(sync_client=True)`. The Protocol's `fetch_recent_runs()` is
  synchronous — an async client would require `asyncio.run()` which breaks inside
  Prefect's existing event loop. The **caller** owns client lifecycle (use as
  context manager: `with get_client(sync_client=True) as client:`). The adapter
  receives an already-open client — it does not create or close it.
- `fetch_recent_runs(flow_names: list[str], since: UtcDatetime) -> list[FlowRunStatus]`
- Iterates `flow_names`, issuing one `client.read_flow_runs()` call per flow
  name with:
  - `flow_filter=FlowFilter(name=FlowFilterName(any_=[flow_name]))`
  - `flow_run_filter=FlowRunFilter(start_time=FlowRunFilterStartTime(after_=since))`
  This is necessary because `FlowRun` objects only carry `flow_id: UUID`, not
  the flow name. By querying per name, we know the flow name from the outer loop.
- Field mapping:
  - `flow_name` = from the outer iteration variable (NOT from the `FlowRun`
    object — `FlowRun` has no `flow_name` attribute, only `flow_id`)
  - `run_id` = `str(flow_run.id)` (UUID to str)
  - `state` = mapped from Prefect `StateType` (see below)
  - `started_at` = `ensure_utc(flow_run.start_time)` (may be `None`)
  - `duration_seconds` = `flow_run.total_run_time.total_seconds()` if available,
    else `None`
  - `error_message` = `flow_run.state.message` for failed/crashed, else `None`
- Wraps client errors in `AdapterError`

**State mapping** — Prefect 3 `StateType` has 9 values; `FlowRunState` has 7.
Unmapped states:
- `SCHEDULED` -> `FlowRunState.PENDING` (not yet running, same semantic bucket)
- `PAUSED` -> `FlowRunState.RUNNING` (mid-execution, temporarily halted)

| Prefect `StateType` | `FlowRunState` |
|---|---|
| `SCHEDULED` | `PENDING` |
| `PENDING` | `PENDING` |
| `RUNNING` | `RUNNING` |
| `PAUSED` | `RUNNING` |
| `COMPLETED` | `COMPLETED` |
| `FAILED` | `FAILED` |
| `CRASHED` | `CRASHED` |
| `CANCELLING` | `CANCELLING` |
| `CANCELLED` | `CANCELLED` |

If Prefect adds new states in the future, raise `AdapterError` with the unknown
state value rather than silently dropping the run.

**No new dependencies** — `prefect>=3.0` already present.

**Logging** (structlog via `structlog.get_logger()`, per `docs/standards/logging.md`):
- `pipeline.status_fetch_completed` — INFO, context: `flow_count`, `run_count`,
  `duration_ms`. Use `time.perf_counter()` and `round(..., 1)` for `duration_ms`.
- `pipeline.status_fetch_failed` — ERROR, context: `error`

**Tests**: Fake `SyncPrefectClient` with `read_flow_runs` returning canned data.
- State mapping for each Prefect `StateType` -> `FlowRunState` (all 9)
- Duration computation: present vs. missing `total_run_time`
- Error message extraction from failed/crashed runs
- `started_at` is `None` for pending/scheduled runs
- Client failure wraps in `AdapterError`
- Unknown state type raises `AdapterError`

**Key references**:
- Domain type: `src/sapphire_flow/types/pipeline.py:27-33` (FlowRunStatus)
- Enum: `FlowRunState` in `src/sapphire_flow/types/enums.py`

## Exit gates

- `isinstance(HydroScraperAdapter(...), StationDataSource)` passes
- `isinstance(PrefectStatusAdapter(...), PipelineStatusSource)` passes
- All integration tests pass: `uv run pytest tests/integration/adapters/`
- Type check: `uv run pyright --strict src/sapphire_flow/adapters/hydro_scraper.py src/sapphire_flow/adapters/prefect_status.py`
- Lint clean: `uv run ruff check && uv run ruff format --check`
- Version bump: `uv run bump-my-version bump patch` + tag
