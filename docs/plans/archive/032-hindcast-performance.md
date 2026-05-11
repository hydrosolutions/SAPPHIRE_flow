# Plan 032 — Hindcast Performance: Configurable Period, In-Memory Pre-fetch, Connection Resilience

**Status**: READY
**Phase**: Bug fix & performance (against Phase 5/7 code)
**Depends on**: Plan 034 (implemented — commit 31432b3). Plan 034 added `forecast_horizon_steps` to `ModelDataRequirements` and removed the mis-wired override in `_make_hindcast_fn()`. The original Task 1 of this plan is therefore already resolved.

## Context

Station onboarding runs hindcasts over the full CAMELS-CH import window (1980–2030 = 18,263 daily steps per station) because `_run_onboarding()` passes `start_utc`/`end_utc` as both the data import window AND the training/hindcast period via `determine_onboarding_scope()` (onboarding.py:527–537).

Additionally, per-issue-time DB queries in `_assemble_hindcast_inputs()` (hindcast.py:83) make each hindcast step trigger two DB round-trips (`fetch_reanalysis` at line 100, `fetch_observations` at line 108). With 720-day lookback windows that overlap almost completely between consecutive days, >99% of data is re-fetched redundantly.

A production onboarding run (2026-04-14) also revealed a third problem: when a DB connection dies mid-hindcast (e.g., `psycopg.errors.AdminShutdown` from Postgres OOM or container restart), the `except Exception` at hindcast.py:287 catches the error and continues the loop — but the shared `sa.Connection` is permanently poisoned. All subsequent steps silently fail with `PendingRollbackError("Can't reconnect until invalid transaction is rolled back")`, churning through thousands of doomed iterations.

Three independent problems, all fixable without architecture changes:

1. **No configurable hindcast period**: The onboarding path always hindcasts the full import window. For testing the dataflow, shorter windows are needed. The hindcast period should be configurable, defaulting to the full data range. This is a developer convenience feature (not mandated by v0-scope.md).

2. **Per-step DB queries**: Each issue_time calls `forcing_source.fetch_reanalysis()` and `obs_store.fetch_observations()` — one DB round-trip each. The architecture (Flow 7, architecture-context.md:1010–1034) defines H.2/H.3 as **pre-loop** bulk reads, followed by per-step assembly (H.4). The sequencing note (architecture-context.md:1073) is explicit: *"H.2 and H.3 run in parallel (both are bulk store reads scoped by H.1). They join at the per-step loop."* The current code does H.2/H.3 inside the loop — fixing this restores architectural intent.

3. **Poisoned connection on DB errors**: All stores share one `sa.Connection` (created in `make_pg_stores()`, `src/sapphire_flow/flows/_db.py:23–59`). `store_hindcast()` (src/sapphire_flow/store/hindcast_store.py) has zero error handling — no try/except, no rollback, no savepoint. When the connection dies, the service-layer `except Exception` catches the error but never resets the connection, dooming all subsequent steps. The pre-fetch optimization (problem 2) eliminates per-step reads, but per-step writes to `hindcast_store.store_hindcast()` remain and need connection resilience.

### Architecture alignment

- Flow 7's Mermaid diagram (architecture-context.md:1010–1034) shows H.2 ("Gather historical forcing") and H.3 ("Gather historical observations") **outside and upstream of** the per-step subgraph. The sequencing note (line 1073) is explicit: *"H.2 and H.3 run in parallel (both are bulk store reads scoped by H.1). They join at the per-step loop."*
- Note: architecture-context.md:1058 says *"This is per-step: each hindcast step's lookback window shifts with the simulated issue time."* The approach here (pre-fetch a super-window, then filter in-memory per step) is consistent — H.2/H.3 are still bulk reads, and H.4 still assembles per-step windows from the pre-fetched data.
- The `WeatherReanalysisSource` protocol (protocols/adapters.py:37–45) is an injection point — `FakeWeatherReanalysisSource` (tests/fakes/fake_adapters.py:101–119) already demonstrates the in-memory filter pattern.

### Connection architecture (relevant to Task 4)

- All stores are created via `make_pg_stores(conn)` (`src/sapphire_flow/flows/_db.py:23–59`), sharing a single `sa.Connection`
- Connection opened with `isolation_level="AUTOCOMMIT"` via `execution_options()` in `setup_production_stores()` (`flows/_db.py:68`, called from `flows/onboard.py:86`) and directly in `scripts/onboard.py:230`
- `PgHindcastStore.__init__(self, conn: sa.Connection)` stores the connection as `self._conn`
- `store_hindcast()` executes two independent auto-committed writes: INSERT into `hindcast_forecasts` (line 29) and conditional INSERT into `hindcast_values` (line 76–77, inside `if rows:` guard)
- `pool_pre_ping` is not enabled on any engine
- No `rollback()` or `invalidate()` calls exist in production code (`src/`). `tests/integration/conftest.py` uses `trans.rollback()` for test isolation — intentionally excluded.
- **Known limitation (architectural debt)**: With AUTOCOMMIT, a connection failure between the two writes in `store_hindcast()` leaves an orphaned `hindcast_forecasts` header row with no corresponding `hindcast_values`. Downstream, `_reconstruct_ensemble()` (hindcast_store.py:214, raises at line 224–227) raises `ValueError("No hindcast_values rows for hindcast_forecast_id=...")` when reading the orphan. The abort-on-fatal fix (Task 4) prevents cascading damage but cannot prevent the single orphan from the failing step. Wrapping both writes in an explicit transaction is a prerequisite for v1: AUTOCOMMIT is architecturally incompatible with PgBouncer transaction-pooling mode (conventions.md §Database connection patterns), which relies on transaction boundaries to return connections to the pool.

---

## Tasks

### Task 1: Add configurable hindcast period to onboarding

Add an optional `hindcast_days: int | None` parameter to control the hindcast evaluation window. Default `None` = use the full data period (current behavior). This is a developer convenience feature for faster dataflow testing, not mandated by any specification.

**Changes in `src/sapphire_flow/services/onboarding.py`**:

1. Add `hindcast_days: int | None = None` parameter to `_run_onboarding()` (line 182) and `onboard_from_camelsch()` (line 636). Thread it through the `_run_onboarding()` call at line 694.

2. At the `determine_onboarding_scope()` call site (line 527), compute the hindcast window:
   ```python
   if hindcast_days is not None:
       from datetime import timedelta
       hindcast_start = ensure_utc(max(start_utc, end_utc - timedelta(days=hindcast_days)))
   else:
       hindcast_start = start_utc
   ```

3. Pass `hindcast_start` as `training_period_start` and `end_utc` as `training_period_end` to the existing `determine_onboarding_scope()` signature (model_onboarding.py:475). **No signature change to `determine_onboarding_scope()` is needed** — it already has `training_period_start`/`training_period_end` parameters.

4. **CRITICAL**: `start_utc`/`end_utc` must remain unchanged for all other steps:
   - Step 5 (QC, loop at line 351)
   - Step 5b (baselines, loop at line 384)
   - Step 5c (flow regimes, loop at line 413)

   Only the `determine_onboarding_scope()` call (Step 7, line 528) uses the narrowed window. The `TrainingUnit.training_period_start` propagates through `_make_hindcast_fn()` (line 60–105) to `run_station_hindcast(period_start=...)`, so the hindcast window narrows correctly without touching any other step.

5. Validate `hindcast_days` and log the effective hindcast window. Use WARNING when actually narrowed (model trains on shortened data — skill scores not comparable to full-period training, and evaluation is in-sample):
   ```python
   if hindcast_days is not None and hindcast_days < 1:
       raise ValueError(f"hindcast_days must be >= 1, got {hindcast_days}")

   narrowed = hindcast_start > start_utc
   if narrowed:
       log.warning(
           "hindcast.period_narrowed",
           hindcast_start=str(hindcast_start),
           hindcast_end=str(end_utc),
           hindcast_days=(end_utc - hindcast_start).days,
           note="model trains AND evaluates on narrowed window — "
                "skill scores not comparable to full-period training",
       )
   elif hindcast_days is not None:
       # hindcast_days was set but exceeds available data — silent no-op.
       # Log so the operator knows the parameter had no effect.
       log.info(
           "hindcast.period_unchanged",
           hindcast_start=str(hindcast_start),
           hindcast_end=str(end_utc),
           hindcast_days_requested=hindcast_days,
           actual_days=(end_utc - start_utc).days,
           note="hindcast_days exceeds available data range — using full period",
       )
   else:
       log.info(
           "hindcast.period_resolved",
           hindcast_start=str(hindcast_start),
           hindcast_end=str(end_utc),
           hindcast_days=(end_utc - hindcast_start).days,
       )
   ```

**Changes in `src/sapphire_flow/flows/onboard.py`**:

6. Add `hindcast_days: int | None = None` parameter to `onboard_stations_flow()` (line 51). Thread it through the `onboard_from_camelsch()` call at line 137.

**Changes in `scripts/onboard.py`**:

7. Add `--hindcast-days` CLI argument to `_build_parser()` (after line 140, before the `return parser`). Thread it through the `onboard_from_camelsch()` call at line 265.

**Files**: `src/sapphire_flow/services/onboarding.py`, `src/sapphire_flow/flows/onboard.py`, `scripts/onboard.py`

**Verify**: `uv run pytest tests/unit/services/test_model_onboarding.py -x -q`

**Impact**: When `hindcast_days=730`, reduces hindcast steps from ~18,263 to ~730 per station (25x). Default behavior unchanged.

### Task 2: Pre-fetch forcing and observations in `run_station_hindcast()`

Refactor `run_station_hindcast()` (hindcast.py:195) to fetch all forcing and observations once before the issue-time loop, then pass pre-fetched data to `_assemble_hindcast_inputs()`.

**Step A — Add missing imports** to the `TYPE_CHECKING` block (hindcast.py:21–38):

```python
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.observation import Observation
```

**Step B — Bulk pre-fetch before the loop** (insert before line 235, i.e. before the `for issue_time in _issue_times(...)` loop):

```python
# Pre-fetch all data for the full period (H.2 + H.3 from architecture).
# Must extend start by lookback to cover the first issue_time's window.
full_start = ensure_utc(period_start - lookback_steps * time_step)
# +1 so the last issue_time's full forecast horizon
# (issue_time + horizon_steps * time_step) falls inside
# the half-open pre-fetch range [full_start, full_end).
full_end = ensure_utc(period_end + (forecast_horizon_steps + 1) * time_step)

t0 = time.perf_counter()
all_forcing = forcing_source.fetch_reanalysis(
    station_configs=weather_sources, start=full_start, end=full_end,
    parameters=required_features,
)
all_observations = obs_store.fetch_observations(
    station_id=station_id, parameter=parameter,
    start=full_start, end=period_end, qc_status=QcStatus.QC_PASSED,
)
log.info(
    "hindcast.prefetch_completed",
    station_id=str(station_id),
    forcing_records=len(all_forcing),
    observation_records=len(all_observations),
    duration_ms=round((time.perf_counter() - t0) * 1000, 1),
)
```

The pre-fetch range is `[period_start - lookback_steps * time_step, period_end + (forecast_horizon_steps + 1) * time_step)`. Post-Plan 034, `forecast_horizon_steps` is read from `model.data_requirements.forecast_horizon_steps` (e.g., 7 for `LinearRegressionDaily`). With `lookback_steps=720` at daily steps, this extends ~720 days before `period_start` and ~8 days after `period_end`. For a 730-day hindcast window, total pre-fetch covers ~1,458 days of forcing data (~190KB) and ~1,450 days of observations (~50KB). Negligible memory cost.

**Step C — Change `_assemble_hindcast_inputs()` signature** (hindcast.py:83):

Replace `forcing_source: WeatherReanalysisSource` and `obs_store: ObservationStore` with pre-fetched data:

```python
def _assemble_hindcast_inputs(
    station_id: StationId,
    issue_time: UtcDatetime,
    lookback_steps: int,
    time_step: timedelta,
    forecast_horizon_steps: int,
    required_features: list[str],
    all_forcing: list[RawHistoricalForcing],    # <-- was forcing_source
    all_observations: list[Observation],         # <-- was obs_store
    weather_sources: list[StationWeatherSource],
    static_attributes: pl.DataFrame | None,
    parameter: str = "discharge",
) -> StationModelInputs | None:
```

Replace the `fetch_reanalysis()` call (line 100) with in-memory filter (same pattern as `FakeWeatherReanalysisSource`):

```python
lookback_start = ensure_utc(issue_time - lookback_steps * time_step)
horizon_end = ensure_utc(issue_time + (forecast_horizon_steps + 1) * time_step)

station_ids = {cfg.station_id for cfg in weather_sources}
raw_forcing = [
    r for r in all_forcing
    if r.station_id in station_ids
    and lookback_start <= r.valid_time < horizon_end
    and r.parameter in required_features
]
```

Replace the `fetch_observations()` call (line 108) with in-memory filter:

```python
# NO-FUTURE-LEAKAGE: end=issue_time (unchanged from original).
# Defensive filters on station_id, parameter, qc_status mirror the
# pre-fetch scope. These are cheap (in-memory list) and guard against
# future changes that might widen the pre-fetch.
observations = [
    o for o in all_observations
    if o.station_id == station_id
    and o.parameter == parameter
    and o.qc_status == QcStatus.QC_PASSED
    and lookback_start <= o.timestamp < issue_time
]
```

**Step D — Update the call site** in `run_station_hindcast()` (line 237):

Pass `all_forcing` and `all_observations` instead of `forcing_source` and `obs_store`.

**Step E — Fix logger** (hindcast.py:40):

Change `log = structlog.get_logger()` to `log = structlog.get_logger(__name__)` per logging.md.

**Files**: `src/sapphire_flow/services/hindcast.py`

**Verify**: `uv run pytest tests/unit/services/test_hindcast.py -x -q`

**Impact**: Reduces DB queries from 2×N (N=issue_time count) to 2 total per station. For 730 issue times: 1,460 queries → 2.

### Task 3: Apply same pre-fetch pattern to `run_group_hindcast()`

The group hindcast function (hindcast.py:305–485) has the same per-step fetch pattern via `_assemble_hindcast_inputs()` called inside `for sid in group.station_ids` inside the issue-time loop (line 370).

**Step A — Bulk pre-fetch per station** before the issue-time loop (after line 350):

Pre-fetch forcing and observations for each station in the group. `fetch_reanalysis()` accepts `station_configs: list[StationWeatherSource]`, so forcing can be fetched in a single batch call for all stations. Observations still need per-station calls because stations may have different parameters (`parameter_map`) and `fetch_observations_batch()` (protocols/stores.py:118) takes a single `parameter: str`.

```python
# Pre-fetch all forcing and observations (H.2 + H.3).
# Skip stations not found in the station store (mirrors the per-step
# guard at line 365 that checks station_configs[sid] is not None).
full_start = ensure_utc(period_start - lookback_steps * time_step)
full_end = ensure_utc(period_end + (forecast_horizon_steps + 1) * time_step)

fetchable_sids = [sid for sid in group.station_ids if station_configs[sid] is not None]

# Single batch call for forcing — station_configs accepts all sources.
all_weather_sources = [
    ws for sid in fetchable_sids for ws in weather_sources_map[sid]
]
all_forcing_flat = forcing_source.fetch_reanalysis(
    station_configs=all_weather_sources,
    start=full_start, end=full_end, parameters=required_features,
)
# Index by station_id for per-step lookup.
all_forcing_map: dict[StationId, list[RawHistoricalForcing]] = {
    sid: [] for sid in fetchable_sids
}
for r in all_forcing_flat:
    if r.station_id in all_forcing_map:
        all_forcing_map[r.station_id].append(r)

# Per-station calls for observations (different parameters per station).
all_obs_map: dict[StationId, list[Observation]] = {}
for sid in fetchable_sids:
    all_obs_map[sid] = obs_store.fetch_observations(
        station_id=sid, parameter=parameter_map.get(sid, "discharge"),
        start=full_start, end=period_end, qc_status=QcStatus.QC_PASSED,
    )
```

**Contract assumption**: The indexing `all_forcing_map[r.station_id]` relies on `RawHistoricalForcing.station_id` being the **hydrological** station ID (matching `StationWeatherSource.station_id`), not a weather-source or grid-cell ID. This is guaranteed by the current `WeatherReanalysisSource` contract (`FakeWeatherReanalysisSource` is the reference implementation). If a future adapter (e.g., `GridExtractor` for NWP basin-average extraction) uses a different ID scheme, the bucketing would silently produce empty lists for every station — each step would log `hindcast.skip.no_forcing` with no error. The `GridExtractor` design (Phase 3 v0b) must preserve this contract.

**Step B — Update `_assemble_hindcast_inputs()` call** inside the double loop (line 370):

Pass `all_forcing_map[sid]` and `all_obs_map[sid]` instead of `forcing_source` and `obs_store`.

**Latent optimisation (out of scope)**: For groups where all stations share the same parameter (the common v0 case — all discharge), a single `fetch_observations_batch()` call (protocols/stores.py:118) could replace N per-station `fetch_observations()` calls. The per-station loop is correct and safe; the batch call is a future performance refinement.

**Files**: `src/sapphire_flow/services/hindcast.py`

**Verify**: `uv run pytest tests/unit/services/test_hindcast.py -x -q`

### Task 4: Add connection-error resilience to hindcast loops

After the pre-fetch refactor (Tasks 2–3), the only per-step DB operation remaining in the hindcast loop is `hindcast_store.store_hindcast()`. If this write fails due to a connection-level error (AdminShutdown, PendingRollbackError), the shared `sa.Connection` is permanently poisoned — every subsequent step will fail identically.

**Approach**: Detect unrecoverable connection errors and abort early with a clear error, rather than churning through thousands of doomed iterations.

**Architectural deviation (H.6 — store write failures)**: architecture-context.md line 1061 defines the continue-on-failure contract for H.5: *"if a time step fails (model error, NaN output, numerical divergence), the step is logged with the error and skipped — the hindcast run continues."* Note that H.5 explicitly enumerates **model errors** — it says nothing about infrastructure failures. H.6 (store writes) has no stated failure policy in the architecture. This task fills that gap: when `store_hindcast()` fails due to a dead DB connection, continuing is provably futile — every subsequent step will fail identically. The continue-on-failure contract (H.5) remains in force for model/data errors (ValueError, NaN, insufficient data). This distinction is encoded in `_is_connection_fatal()`: only exceptions matching known connection-death patterns trigger the abort.

**Step A — Define connection-fatal error detection** (hindcast.py, module level):

```python
import psycopg
import psycopg.errors

from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    InternalError,
    OperationalError,
    PendingRollbackError,
)

from sapphire_flow.exceptions import StoreError

_CONNECTION_FATAL_KEYWORDS = frozenset({
    "adminshutdown",
    "server closed",
    "connection reset",
    "connection refused",
    "terminating connection",
    "could not connect",
})

def _is_connection_fatal(exc: Exception) -> bool:
    """Return True if the exception indicates the DB connection is dead."""
    # PendingRollbackError is the specific subclass for "Can't reconnect
    # until invalid transaction is rolled back". Do NOT catch the parent
    # InvalidRequestError — it includes NoResultFound, NoSuchColumnError,
    # ResourceClosedError, etc., which are data/programming errors that
    # should be logged-and-continued, not treated as connection death.
    # InterfaceError covers "connection is closed" when SQLAlchemy
    # detects a dead connection at the DBAPI level.
    if isinstance(exc, (DisconnectionError, InterfaceError, PendingRollbackError)):
        return True
    # OperationalError and InternalError wrap diverse psycopg errors —
    # only treat as fatal when the message indicates connection death.
    # This avoids false-positives on transient errors like deadlock
    # detection or lock-wait timeout.
    if isinstance(exc, (OperationalError, InternalError)):
        msg = str(exc).lower()
        return any(kw in msg for kw in _CONNECTION_FATAL_KEYWORDS)
    # Safety net for unwrapped psycopg errors that bypass SQLAlchemy
    # wrapping. Check specific subclasses first (locale-independent,
    # works even when str(exc) is empty), then fall back to keyword
    # matching for unexpected error types.
    if isinstance(exc, psycopg.Error):
        # Direct subclass checks — sqlstate-based, no locale dependency.
        _fatal_psycopg = (
            psycopg.errors.AdminShutdown,
            psycopg.errors.CrashShutdown,
            psycopg.errors.ConnectionFailure,
            psycopg.errors.ConnectionDoesNotExist,
            psycopg.errors.CannotConnectNow,
        )
        if isinstance(exc, _fatal_psycopg):
            return True
        msg = str(exc).lower()
        return any(kw in msg for kw in _CONNECTION_FATAL_KEYWORDS)
    return False
```

**Step B — Update `run_station_hindcast()` except block** (hindcast.py:287):

Replace the current catch-all with connection-fatal detection:

```python
except Exception as exc:
    if _is_connection_fatal(exc):
        log.error(
            "hindcast.connection_failed",
            station_id=str(station_id),
            issue_time=str(issue_time),
            error_type=type(exc).__qualname__,
            successful_steps=sum(1 for r in results if r.success),
            remaining_steps=len(
                _issue_times(ensure_utc(issue_time + time_step), period_end, time_step)
            ),
            exc_info=True,
        )
        raise StoreError(
            f"Connection-fatal error during hindcast store write: "
            f"{type(exc).__qualname__}"
        ) from exc  # Abort — wraps in domain exception per conventions.md
    # Guard against DSN leakage: OperationalError/InternalError/InterfaceError/
    # psycopg.Error that did NOT match _CONNECTION_FATAL_KEYWORDS may still
    # contain connection strings in str(exc). Use error_type for those;
    # str(exc) is safe for model/data errors (ValueError, NaN, etc.).
    if isinstance(exc, (OperationalError, InterfaceError, InternalError, psycopg.Error)):
        error_fields: dict[str, str] = {"error_type": type(exc).__qualname__}
    else:
        error_fields = {"error": str(exc)}
    log.warning(
        "hindcast.step_failed",
        station_id=str(station_id),
        issue_time=str(issue_time),
        **error_fields,
    )
    results.append(
        HindcastStepResult(
            issue_time=issue_time,
            success=False,
            error=type(exc).__qualname__ if isinstance(
                exc, (OperationalError, InterfaceError, InternalError, psycopg.Error)
            ) else str(exc),
        )
    )
```

Note: `error_type` logs the exception class name without the full `str(exc)` to avoid leaking connection DSN fragments (logging.md §Security: "Never log database connection strings"). `exc_info=True` passes the traceback to structlog's `format_exc_info` processor (already configured in the shared processor chain) for operator diagnosis. The same DSN-safe pattern is applied to the non-fatal branch: any `OperationalError`, `InterfaceError`, `InternalError`, or `psycopg.Error` that falls through `_is_connection_fatal()` (e.g., non-English locale messages, unexpected phrasings) is logged with `error_type` instead of `error=str(exc)` to prevent credential leakage.

**Step C — Update `run_group_hindcast()` except blocks**:

Apply the same `_is_connection_fatal()` check to all three except blocks in the group hindcast loop. Re-raise as `StoreError` on connection-fatal errors (matching Step B) rather than recording a failure and continuing.

The three except blocks are:
1. Per-station `_assemble_hindcast_inputs()` errors — after the pre-fetch refactor, this is in-memory assembly only, so connection errors cannot originate here. Guard defensively anyway.
2. `model.predict_batch()` failures — no DB involved, but guard for completeness.
3. Per-station `hindcast_store.store_hindcast()` failures — the primary risk point.

Also fix any `exc_info=exc` usage: replace with `error_type=type(exc).__qualname__, exc_info=True` for connection-fatal blocks (avoids DSN leakage while preserving traceback). For non-fatal blocks, apply the same DSN-safe pattern as Step B: use `error_type=type(exc).__qualname__` when the exception is `isinstance(exc, (OperationalError, InterfaceError, InternalError, psycopg.Error))`, and `error=str(exc)` otherwise.

**Step D — Enable `pool_pre_ping`** on all engine creation sites:

There are **three** `sa.create_engine` call sites in production code, none using `pool_pre_ping`:

| File | Line | Current | Covers |
|------|------|---------|--------|
| `src/sapphire_flow/db/engine.py` | 10 | `sa.create_engine(url)` | API path (via `create_engine_from_env()` called from `api/deps.py:15`) |
| `src/sapphire_flow/flows/_db.py` | 65 | `sa.create_engine(database_url)` | All Prefect flows (`onboard.py`, `ingest_observations.py`, `run_forecast_cycle.py`, `run_hindcast.py`) via `setup_production_stores()` |
| `scripts/onboard.py` | 208 | `sa.create_engine(database_url)` | Standalone CLI onboarding script |

Add `pool_pre_ping=True` to all three `sa.create_engine` call sites. The three Prefect flow files (`flows/onboard.py:86`, `flows/ingest_observations.py:186`, `flows/run_forecast_cycle.py:207`) all call `setup_production_stores()` from `_db.py` — they are **transitively covered** by site #2.

This makes SQLAlchemy issue a `SELECT 1` before each connection checkout, detecting stale connections at the pool level. **Important**: `pool_pre_ping` operates at pool checkout time. It protects short-lived connection patterns (forecast cycle, ingest flows) but does **not** protect the hindcast path, which holds a single long-lived connection checked out once. Mid-run connection failures in the hindcast loop are handled by the abort-on-fatal logic (Steps A–C), not by `pool_pre_ping`.

Note: `tests/integration/conftest.py:30` and `notebooks/01_data_inspection.ipynb` also have `sa.create_engine` calls without `pool_pre_ping`. These are intentionally excluded — test/notebook sites are not production paths.

**Step E — Prefect retry guidance** (documentation of intent — no code change needed):

The `sa.Connection` is created in the flow scope (via `setup_production_stores()` in `_db.py:62–70`, called from `flows/onboard.py:86`), not inside the hindcast task. If Prefect retries the task within the same flow execution, it reuses the same poisoned connection — automatic recovery via retry is not possible. The standalone hindcast task in `flows/run_hindcast.py:26` already has no explicit `retries` setting (Prefect default = 0). The onboarding path calls `run_station_hindcast()` directly (not through a `@task`), so Prefect retries do not apply there. No code change is needed — this step documents the constraint for future refactors. If a future plan adds `@task` wrapping or the conventions.md standard `@task(retries=3, retry_delay_seconds=[60, 300, 900])` pattern to the hindcast path, a `# retries=0: connection created in flow scope; retried task reuses poisoned connection` comment must accompany `retries=0`.

**Files**: `src/sapphire_flow/services/hindcast.py`, `src/sapphire_flow/db/engine.py`, `src/sapphire_flow/flows/_db.py`, `scripts/onboard.py`

**Verify**: `uv run pytest tests/unit/services/test_hindcast.py -x -q`

### Task 5a: Add call-count instrumentation to fakes (run alongside Tasks 2–3)

Add call-count instrumentation to `FakeWeatherReanalysisSource` and `FakeObservationStore` (e.g., increment a counter in `fetch_reanalysis()` / `fetch_observations()`). This must be done **before or alongside Tasks 2–3**, not after, because the Task 2/3 verification step (`pytest test_hindcast.py`) cannot otherwise detect a partial implementation that still queries per-step — the fakes produce correct results regardless of call pattern.

**Files**: `tests/fakes/fake_adapters.py` (add counter), `tests/fakes/fake_stores.py` (add counter)

### Task 5b: Update hindcast tests

Update `tests/unit/services/test_hindcast.py` to verify:

1. **Pre-fetch call count**: Assert that `fetch_reanalysis()` / `fetch_observations()` are called exactly once per station (not per issue-time), using the counters added in Task 5a.

2. **No-future-leakage guarantee still holds**: `TestNoFutureLeakage` (line ~239) depends on the `+1` in `horizon_end` calculation. Verify this test still passes with the in-memory filter. The test computes `horizon_end = issue_time + forecast_horizon_steps * _STEP` then adds `extended_end = horizon_end + _STEP` — this fragile coupling with the `+1` must be preserved. Additionally, extend the test's seeded forcing data to cover `full_end` (`period_end + (forecast_horizon_steps + 1) * time_step`) so the upper-bound filter assertion is exercised non-vacuously.

3. **Connection-fatal abort**: Add a test that simulates a connection-fatal error from `hindcast_store.store_hindcast()` (e.g., raise `OperationalError` with a connection-death message like "server closed the connection unexpectedly") and verifies that `run_station_hindcast()` raises `StoreError` instead of continuing the loop. Verify the same for `run_group_hindcast()`. Also add a test that a transient `OperationalError` (e.g., "deadlock detected") does NOT abort — it should be logged and skipped.

4. **Step failures still handled correctly**: `TestStepFailureContinues` (line ~352) should still work for non-connection errors — the per-step try/except is unchanged for those.

5. **Existing tests pass without modification**: Since `FakeWeatherReanalysisSource` already uses the in-memory filter pattern (stores all records in `self._records`, filters by window), the refactored production code now matches the fake's internal behavior. All existing tests should pass as-is — the fakes' `fetch_*` methods are called once during the pre-fetch and produce the same in-memory list that per-step calls would have produced. Only the new call-count, connection-fatal, and seeded-data assertions are new.

6. **`hindcast_days` parameter test** (in `tests/unit/services/test_model_onboarding.py`): Add a test that verifies:
   - When `hindcast_days` is set, `determine_onboarding_scope()` receives a `training_period_start` that is `end_utc - timedelta(days=hindcast_days)` (not `start_utc`).
   - When `hindcast_days` exceeds the data range, `training_period_start` falls back to `start_utc` (no narrowing).
   - When `hindcast_days=None` (default), `training_period_start == start_utc` (unchanged behavior).
   - The `hindcast.period_narrowed` WARNING log is emitted when `hindcast_start > start_utc`.
   - `hindcast_days < 1` raises `ValueError`.

**Files**: `tests/unit/services/test_hindcast.py`, `tests/unit/services/test_model_onboarding.py`

**Verify**: `uv run pytest tests/unit/services/test_hindcast.py tests/unit/services/test_model_onboarding.py -x -q`

### Task 6: Verify full test suite

```bash
uv run pytest tests/unit/services/test_hindcast.py -x -q
uv run pytest tests/unit/services/test_model_onboarding.py -x -q
uv run ruff check src/sapphire_flow/services/hindcast.py src/sapphire_flow/services/onboarding.py src/sapphire_flow/flows/onboard.py scripts/onboard.py
uv run pytest --tb=short -q
```

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "tasks": ["1", "5a"],
      "parallel": true,
      "note": "Configurable hindcast period and call-count instrumentation are independent prerequisites"
    },
    {
      "id": "phase-1",
      "tasks": ["2", "3"],
      "parallel": false,
      "depends_on": ["phase-0"],
      "note": "Task 2 changes _assemble_hindcast_inputs() signature; Task 3 depends on it. Call-count fakes from 5a allow verification."
    },
    {
      "id": "phase-1b",
      "tasks": ["4"],
      "parallel": true,
      "depends_on": ["phase-1"],
      "note": "Connection resilience modifies the same except blocks that phase-1 changes"
    },
    {
      "id": "phase-2",
      "tasks": ["5b", "6"],
      "parallel": false,
      "depends_on": ["phase-0", "phase-1", "phase-1b"]
    }
  ]
}
```

Phase 0: configurable hindcast period (Task 1) and call-count instrumentation (Task 5a) are independent prerequisites — run in parallel. Phase 1: pre-fetch refactor (Tasks 2–3) depends on phase 0 for call-count fakes. Phase 1b: connection resilience (Task 4) follows phase 1. Phase 2: tests + full verification depends on all prior phases.

## Risk Assessment

### 1. Hindcast period vs training period

**IMPORTANT**: When `hindcast_days` is set, `training_period_start=hindcast_start` propagates through `TrainingUnit` into both the training data assembly (called at model_onboarding.py:694; defined in training_data.py:58) and the hindcast evaluation window (`_make_hindcast_fn()` at onboarding.py:91). The artifact metadata (call at model_onboarding.py:785) and skill computation (`_make_skill_fn()` at onboarding.py:134) also receive the narrowed period. This applies to all flows that use the shared hindcast service layer — onboarding (Flow 5), model onboarding (Flow 13), and standalone hindcast (Flow 7). This means:
1. The model **trains on `[hindcast_start, end_utc)`** — not the full data range — producing a qualitatively different model.
2. The model is **evaluated in-sample** on that same shortened window.
3. With short `hindcast_days` and configured `skill_gate_thresholds`, most strata fall below `min_skill_samples`, producing `SKIPPED_INSUFFICIENT_EVAL` — the artifact stays in `training` (promotion blocked). With the v0 default (`skill_gate_thresholds = {}`), the gate always passes regardless of `hindcast_days` and the artifact auto-promotes — but this is by design, not a `hindcast_days`-specific risk (see Risk 13).

Skill scores therefore do not represent a model trained on the full data range, and the evaluation is in-sample. The `hindcast.period_narrowed` WARNING log explicitly flags effects (1) and (2). Acceptable for dataflow smoke-testing, but results must not be compared to full-period runs. Decoupling training period from hindcast period would require adding a separate field to `TrainingUnit` — out of scope for this developer convenience feature.

When `hindcast_days=None` (default), the full period is used — no narrowing, no in-sample concern.

### 2. Pre-fetch memory usage

For one station with the default full period (18,263 days + 720-day lookback + 8-day horizon): ~18,991 days × 2 parameters = ~37,982 forcing records (~1.3MB). Observations: ~18,263 records (~630KB). Under 2MB per station — negligible. For `run_group_hindcast()`, all stations in the group are pre-fetched concurrently; a 20-station group at ~2MB/station ≈ 40MB, still well within typical worker memory.

For the shortened case (730 days + 720-day lookback + 8-day horizon): ~1,458 days × 2 parameters = ~2,916 records (~100KB). Negligible.

### 3. No-future-leakage guarantee

The `_assemble_hindcast_inputs()` function enforces `end=issue_time` for observations (the `# NO-FUTURE-LEAKAGE` comment at line 107). This logic is preserved identically in the in-memory filter — only the data source changes (pre-fetched list vs DB), not the filtering predicate.

### 4. Observation filter — defensive guards

The in-memory observation filter includes explicit defensive guards on `station_id`, `parameter`, and `qc_status` in addition to the time-range filter. These mirror the pre-fetch scope and are cheap (in-memory list). They guard against future changes that might widen the pre-fetch (e.g., batching across stations or parameters), closing what would otherwise be an implicit contract between the pre-fetch and per-step filter.

### 5. Existing callers and data flows unaffected

`_assemble_hindcast_inputs()` is called from exactly two places:
- `run_station_hindcast()` (line 237) — updated in Task 2
- `run_group_hindcast()` (line 370) — updated in Task 3

Both callers are updated. No other callers exist. The `_make_hindcast_fn()` callback (onboarding.py:60–105) passes `period_start=unit.training_period_start` to `run_station_hindcast()`, so the configurable window propagates correctly.

### 6. Half-open interval convention

The `+1` in `full_end = period_end + (forecast_horizon_steps + 1) * time_step` ensures the last issue time's full forecast horizon falls inside the half-open pre-fetch range `[full_start, full_end)` (conventions.md:194–207). Without it, forcing records at exactly `issue_time + horizon_steps * time_step` would be excluded by the `<` upper bound. This must not be removed.

### 7. Stratified skill evaluation with shortened windows

When using a shortened window (e.g., `hindcast_days=730`), the 730 daily samples divided across 84 strata (7 lead times × 4 seasons × 3 flow regimes) yields ~8.7 samples per stratum — likely below `min_skill_samples` (currently configured as 100; see `DeploymentConfig` in config/deployment.py:101). With configured `skill_gate_thresholds`, most strata will produce `SKIPPED_INSUFFICIENT_EVAL` and the artifact stays in `training` (promotion blocked). With the v0 default (`skill_gate_thresholds = {}`), the gate always passes regardless of sample count. In either case, fine-grained skill evaluation (rare flow regimes, seasonal breakdown) is not available with shortened windows. Default full-period hindcasts provide comprehensive stratification. See Risk 13 for the interaction between `hindcast_days` and promotion behaviour.

### 8. Connection-fatal abort vs graceful degradation

The abort-on-fatal approach (Task 4) re-raises the exception instead of continuing. H.5's continue-on-failure contract (architecture-context.md:1061) explicitly covers model errors (ValueError, NaN, numerical divergence). H.6 (store writes) has no stated failure policy. Task 4 fills this H.6 gap for infrastructure errors where the DB connection is provably dead — see Task 4's "Architectural deviation (H.6)" note. The operator sees one clear error instead of thousands of identical warnings, and the Prefect task fails cleanly.

**Partial summary on abort**: When the loop aborts on a connection-fatal error, remaining steps are not summarized in the hindcast result. The `hindcast.connection_failed` log event includes `successful_steps` and `remaining_steps` for operator diagnosis. The `results` list accumulated up to the abort point is not returned (the exception propagates). This is a known limitation — a future improvement could emit the partial `results` list before re-raising.

**Retry mitigation**: The `sa.Connection` is created in the flow scope (via `setup_production_stores()` in `_db.py:62–70`, called from `flows/onboard.py:86`), not inside the hindcast task. If Prefect retries the task within the same flow execution, it reuses the same poisoned connection — automatic recovery via retry is not possible. The standalone hindcast task (`flows/run_hindcast.py:26`) already has no explicit `retries` (Prefect default = 0); the onboarding path calls `run_station_hindcast()` directly without a `@task` wrapper. No code change is needed — see Task 4 Step E. Inline reconnection (passing `engine` instead of stores, allowing per-retry reconnection) is a larger refactor better suited to a future plan.

**Orchestration standards note**: The flow-scoped connection prevents effective retry isolation — a retried task reuses the same poisoned `sa.Connection`, making the retry futile. This is distinct from (but related to) orchestration.md's idempotency principle ("Tasks should be idempotent where possible"). Both issues are tracked as technical debt for when connection management is refactored to pass `engine` instead of pre-built stores.

### 9. Partial writes on connection-fatal (architectural debt)

With `isolation_level="AUTOCOMMIT"`, `store_hindcast()` writes the `hindcast_forecasts` header and `hindcast_values` data as two independent auto-committed statements. A connection failure between them orphans the header row. The abort-on-fatal fix prevents cascading damage (all subsequent steps) but cannot prevent the single orphan. Downstream, `_reconstruct_ensemble()` raises `ValueError` when reading orphaned headers. Fix: wrap both writes in an explicit transaction — this is a **prerequisite for v1**, since AUTOCOMMIT is architecturally incompatible with PgBouncer transaction-pooling mode (conventions.md §Database connection patterns), which relies on transaction boundaries to return connections to the pool.

### 10. `pool_pre_ping` performance impact

`pool_pre_ping=True` adds one `SELECT 1` per connection checkout. This is negligible overhead. Note that `pool_pre_ping` protects short-lived connection patterns (forecast cycle, ingest) at checkout time — it does **not** protect the hindcast path's long-lived connection (see Task 4 Step D). The hindcast path's mid-run failures are handled by the abort-on-fatal logic (Steps A–C). When v1 adds PgBouncer in transaction-pooling mode (conventions.md §Database connection patterns), the engine may need `NullPool` or `pool_size=1` alongside `pool_pre_ping` to avoid pool/PgBouncer interaction issues — out of scope here.

### 11. `forecast_horizon_steps` resolved (Plan 034)

Plan 034 (commit 31432b3) added `forecast_horizon_steps` to `ModelDataRequirements` and updated all callers. `run_station_hindcast()` now reads the value from `model.data_requirements.forecast_horizon_steps` (e.g., 7 for `LinearRegressionDaily`). The original Task 1 of this plan (fix mis-wired override) is no longer needed — the override was removed as part of Plan 034.

### 12. Constant `lookback_steps` assumption in pre-fetch

The pre-fetch window correctness (`full_start = period_start - lookback_steps * time_step`) depends on `lookback_steps` being constant across all hindcast steps. This is true for v0 (all steps share the same `ModelDataRequirements.lookback_steps`). If future models introduce variable lookback per step, the pre-fetch window calculation would need revision.

### 13. Skill gate behaviour with short `hindcast_days`

The interaction between `hindcast_days` and promotion depends on the `skill_gate_thresholds` configuration:

- **v0 default (`skill_gate_thresholds = {}`)**: The gate always evaluates `passed=True` because no thresholds are configured. `SKIPPED_INSUFFICIENT_EVAL` is never reached. Models auto-promote regardless of `hindcast_days`. This is by design (v0-scope.md §A7: the skill gate evaluation step "in v0 does not block promotion by default").

- **Configured thresholds + short `hindcast_days`**: All strata fall below `min_skill_samples` → `metric_scores` empty + `passed=False` → `SKIPPED_INSUFFICIENT_EVAL` → the `model_onboarding.py` promotion logic executes `continue`, **skipping promotion**. The artifact stays in `training`. This is the opposite of auto-promotion — short `hindcast_days` with active thresholds **blocks** promotion.

In neither case does `hindcast_days` create a silent auto-promotion bypass. The real concern is that shortened training data produces a qualitatively different model (Risk 1 items 1–2) with in-sample evaluation, making any skill scores that are computed meaningless for comparison.

**Mitigations in this plan**:
- The `hindcast.period_narrowed` WARNING log explicitly flags that skill scores are not comparable to full-period training.
- The `--hindcast-days` CLI argument is clearly a developer convenience, not a production default.

### 14. Time-step parallelism (out of scope)

architecture-context.md line 1073 states: *"Steps H.4–H.6 are parallelizable across time steps — each step assembles its own inputs, runs the model, and stores results independently."* This plan restores the architectural intent for the pre-loop bulk reads (H.2/H.3) and adds connection resilience (H.6), but does not implement time-step parallelism. The sequential loop is preserved. Parallelism via `task.map()` fan-out is a separate concern better addressed when the connection architecture supports per-task reconnection.

### 15. Multi-target parameter scope in pre-fetch

The observation pre-fetch is scoped to a single `parameter` (e.g., `"discharge"`). This is correct for v0 where each model targets one parameter. v0-scope.md §A13 exercises the multi-parameter pipeline with discharge (river stations) and water_level (lake stations) — different station types forecasting different parameters, not single models with multiple targets. Each station's model targets one parameter, so the single-parameter pre-fetch is correct. If future models target multiple parameters per station, pre-fetching would need to widen. The in-memory filter's defensive `o.parameter == parameter` guard ensures correctness but not completeness. Flagged as latent debt — no action needed for v0.

## Verification

```bash
uv run pytest tests/unit/services/test_hindcast.py -x -q
uv run pytest tests/unit/services/test_model_onboarding.py -x -q
uv run ruff check src/sapphire_flow/services/hindcast.py src/sapphire_flow/services/onboarding.py src/sapphire_flow/flows/onboard.py scripts/onboard.py
uv run pytest --tb=short -q
```
