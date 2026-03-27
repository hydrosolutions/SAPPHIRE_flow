---
status: ARCHIVED
created: 2026-03-26
implemented: 2026-03-27
scope: db schema + store + docs (Phase 0/3 immediate) | services (Phase 1, deferred to Phase 8)
depends_on: [003, 004]  # both ARCHIVED (implemented); no outstanding blockers
---

# 005 — Multi-target support in operational forecast service (Flow 1)

## Problem

The operational forecast service (`services/forecast.py`, not yet implemented) will
have the same single-ensemble consumption pattern as the hindcast service. When
`predict()` returns `dict[str, ForecastEnsemble]`, the forecast service must store
one forecast per parameter and route each to parameter-specific alert thresholds.

Additionally, the `forecasts` table unique constraint `uq_forecasts_station_model_issued`
is `(station_id, model_id, issued_at)` — it does **not** include `parameter`. Inserting
two forecasts for the same cycle (one per parameter) will raise a uniqueness violation.
This must be fixed before multi-parameter storage can work.

**Timing:** This is Phase 8 work (`v0-scope.md` §H). The forecast service doesn't
exist yet. This plan documents the pattern to follow when it is implemented, ensuring
consistency with the hindcast approach from plan 003. Phase 0 (unique constraint fix)
can be done immediately.

---

## Changes

### Phase 0 — Fix forecasts unique constraint

#### 0A. `src/sapphire_flow/db/metadata.py`

Widen the partial unique index to include `parameter`:

```python
# BEFORE (db/metadata.py lines 657-664):
sa.Index(
    "uq_forecasts_station_model_issued",
    forecasts.c.station_id,
    forecasts.c.model_id,
    forecasts.c.issued_at,
    unique=True,
    postgresql_where=forecasts.c.status != "superseded",
)

# AFTER:
sa.Index(
    "uq_forecasts_station_model_issued_param",
    forecasts.c.station_id,
    forecasts.c.model_id,
    forecasts.c.issued_at,
    forecasts.c.parameter,
    unique=True,
    postgresql_where=forecasts.c.status != "superseded",
)
```

Create an Alembic migration that drops the old index and creates the new one.
Migration `0008_add_constraints_indexes_columns.py` (lines 98–104) created the original
index by name — the new migration must reference it explicitly:

```python
# upgrade()
op.drop_index("uq_forecasts_station_model_issued", table_name="forecasts")
op.create_index(
    "uq_forecasts_station_model_issued_param",
    "forecasts",
    ["station_id", "model_id", "issued_at", "parameter"],
    unique=True,
    postgresql_where=text("status != 'superseded'"),
)

# downgrade()
op.drop_index("uq_forecasts_station_model_issued_param", table_name="forecasts")
op.create_index(
    "uq_forecasts_station_model_issued",
    "forecasts",
    ["station_id", "model_id", "issued_at"],
    unique=True,
    postgresql_where=text("status != 'superseded'"),
)
```

**Scope:** Rename and widen the unique index only. No column additions. Includes Alembic
migration (drop old index, create new one with `parameter`).
**Verification:** `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
then `uv run pytest tests/ -x -q`

#### 0B. `docs/architecture-context.md` — forecasts table schema

Update the `forecasts` table schema block (~line 1665) to:
1. Add `parameter: TEXT NOT NULL` and `units: TEXT NOT NULL` columns
2. Update the partial unique constraint to `(station_id, model_id, issued_at, parameter)`
3. Add `qc_status` and `qc_flags` columns that exist in `metadata.py`
   but are missing from the doc (pre-existing drift)
4. Fix `nwp_cycle_is_fallback: BOOL DEFAULT FALSE` → `nwp_cycle_source: TEXT CHECK
   ('primary'|'fallback') DEFAULT 'primary'` to match the actual column in `metadata.py`
   (pre-existing drift — the column was renamed and changed type)
5. Add `WHERE status != 'superseded'` predicate to the partial unique constraint
   description (pre-existing drift — the doc omits the partial predicate)

**Scope:** Doc-only. Reconcile architecture-context.md with metadata.py for the
`forecasts` table.
**Verification:** `uv run python -c "print('Phase 0B: manual review — doc matches metadata.py lines 557–664')"` then manual diff review.

**Dependencies:** None.

### Phase 1 — Forecast Service: Multi-Parameter Storage Loop

#### 1A. `src/sapphire_flow/services/forecast.py`

When `model.predict()` returns `dict[str, ForecastEnsemble]`, iterate and store
one `OperationalForecast` per parameter. Follow the hindcast pattern
(`services/hindcast.py` lines 215–233), including the key/value consistency check.
Note: the hindcast service uses `uuid4()` directly, but the forecast service should
use the injectable `uuid_factory: Callable[[], UUID]` pattern (established in
`services/skill/service.py` and `services/flow_regime.py`) for testability:

```python
ensembles, state = model.predict(...)
for param_name, ensemble in ensembles.items():
    if ensemble.parameter != param_name:
        raise ValueError(
            f"Dict key '{param_name}' != ensemble.parameter "
            f"'{ensemble.parameter}'"
        )
    forecast = OperationalForecast(
        id=ForecastId(uuid_factory()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        issued_at=issued_at,
        nwp_cycle_reference_time=nwp_cycle_reference_time,
        nwp_cycle_source=nwp_cycle_source,
        representation=ensemble.representation,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=warm_up_state_age_hours,
        observation_staleness_hours=observation_staleness_hours,
        ensemble=ensemble,
        created_at=clock(),
        updated_at=clock(),
    )
    t0_store = time.perf_counter()
    forecast_store.store_forecast(forecast)
    t_store = time.perf_counter()
    log.info("forecast.stored", station_id=str(station_id),
             parameter=param_name, duration_ms=round((t_store - t0_store) * 1000, 1))
```

**Prefect topology:** This loop runs inside the per-station `forecast_station` task
(see `orchestration.md` fan-out sketch). Each `forecast_station` task is mapped via
`task.map()` over stations; shared dependencies (stores, config) must be passed via
`unmapped()` per `orchestration.md` § Fan-out patterns. The store call is a DB write
but happens inside the already-bounded `forecast_station` task — no inner `@task`
needed (per `orchestration.md`: "inner `@task` decorators on DB-boundary helpers may
be removed to avoid Prefect UI saturation" at high fan-out).

**Logging:** Bind `station_id` via `structlog.contextvars.bind_contextvars()` (without
context manager) at the `forecast_station` task entry — per `logging.md` § Context
binding, Prefect task bodies are the scope, so `bound_contextvars()` context manager
is unnecessary. Emit `forecast.stored` with `duration_ms` per `logging.md` § Per-step
timing instrumentation (`duration_ms` is mandatory on `*.completed` events per
`logging.md` line 320; applied here by convention on `*.stored` for consistency). Use `log_prints=False` on the
task decorator per `logging.md` § Prefect-specific settings.

**Scope:** Multi-parameter storage loop with key/value check, logging, timing. Out of
scope: alert routing (Phase 2), Prefect flow wiring (Phase 8 proper).
**Verification:** `uv run pytest tests/unit/services/test_forecast.py -x -q`

**Dependencies:** Phase 0A complete. Plan 003 complete. Forecast service skeleton
exists (Phase 8).

### Phase 2 — Alert Threshold Routing (moved to plan 010)

**Scope:** Moved entirely to plan 010. No work in this plan.
**Verification:** N/A — no changes.

Alert routing (Flow 1 steps 1.11–1.13) is defined in **plan 010** (multi-model alert
strategy). Plan 010 introduces configurable multi-model alert strategies (primary,
pooled, BMA, consensus) with a Phase C convergence service that collects all models'
ensembles per station and dispatches to the configured strategy.

This plan's Phase 1A (multi-parameter storage loop) provides the per-parameter ensembles
that plan 010's convergence service consumes. The boundary is: this plan stores forecasts
(Phase 1A), plan 010 checks thresholds and raises alerts (Phase C).

### Phase 3 — Store Protocol

#### 3A. `src/sapphire_flow/protocols/stores.py` — `ForecastStore`

Add optional `parameter: str | None = None` to all three fetch methods, matching
the `HindcastStore.fetch_hindcasts(parameter=)` precedent from plan 003:

```python
def fetch_latest_forecast(
    self,
    station_id: StationId,
    model_id: ModelId | None = None,
    parameter: str | None = None,       # NEW
) -> OperationalForecast | None:
    ...

def fetch_forecasts_for_cycle(
    self,
    issued_at: UtcDatetime,
    station_id: StationId | None = None,
    parameter: str | None = None,       # NEW
) -> list[OperationalForecast]:
    ...

def fetch_forecasts_in_range(
    self,
    station_id: StationId,
    start: UtcDatetime,
    end: UtcDatetime,
    model_id: ModelId | None = None,
    status: ForecastStatus | None = None,
    parameter: str | None = None,       # NEW
) -> list[OperationalForecast]:
    ...
```

Also update `docs/spec/types-and-protocols.md` `ForecastStore` definition to match.

**Return semantics for multi-parameter case:**
- `parameter="discharge"` → forecasts for that parameter only (unambiguous)
- `parameter=None` → all parameters returned. For `fetch_latest_forecast`, this returns
  whichever parameter was stored last in the same cycle. Callers that need a specific
  parameter MUST pass `parameter=`.

This matches the pattern from plan 003's `HindcastStore.fetch_hindcasts(parameter=)`.

**Scope:** Protocol signature change + spec doc update. Out of scope: implementations.
**Verification:** `uv run pyright --strict src/sapphire_flow/protocols/stores.py`
(Full Protocol conformance check including implementations deferred to after 3B/3C:
`uv run pyright --strict src/sapphire_flow/protocols/stores.py src/sapphire_flow/store/forecast_store.py tests/fakes/fake_stores.py`)

**Dependencies:** None.

#### 3B. `src/sapphire_flow/store/forecast_store.py` — `PgForecastStore`

Add `parameter` filter to `fetch_latest_forecast`, `fetch_forecasts_for_cycle`, and
`fetch_forecasts_in_range`. Filter directly on `forecasts.c.parameter` — the column
already exists on the `forecasts` table (`metadata.py` line 609). No join needed.
Same pattern in each method:

```python
if parameter is not None:
    query = query.where(forecasts.c.parameter == parameter)
```

**Scope:** SQL WHERE clause additions to three methods.
**Verification:** `uv run pytest tests/integration/store/test_forecast_store.py -x -q`

**Dependencies:** 3A.

#### 3C. `tests/fakes/fake_stores.py` — `FakeForecastStore`

Add `parameter: str | None = None` to `fetch_latest_forecast()`,
`fetch_forecasts_for_cycle()`, and `fetch_forecasts_in_range()`. Filter:

```python
and (parameter is None or f.ensemble.parameter == parameter)
```

**Scope:** Fake implementation update for three methods.
**Verification:** `uv run pytest tests/unit/ -x -q`

**Dependencies:** 3A.

### Phase 4 — ForecastOutputQualityChecker

No changes needed — already operates per-ensemble. Confirmed in plan 003 review.

**Scope:** No changes. Documenting that this component is already multi-parameter safe.
**Verification:** `uv run pytest tests/unit/services/test_forecast_qc.py -x -q`

---

## Phase 5 — Test Plan

#### 5A. Tests that break without changes

None. The forecast service doesn't exist yet (Phase 1 tests are all new). Phase 3
adds `parameter: str | None = None` with a default, so existing `ForecastStore` callers
remain backward-compatible — no existing test breaks. Phase 0A renames the unique index
but no test references `uq_forecasts_station_model_issued` by string name.

#### 5B. New tests needed

**`tests/unit/services/test_forecast.py`**

1. `TestMultiParameterForecast` — `test_two_parameters_stored`:
   - Use `FakeMultiTargetStationForecastModel` returning `dict` with `"discharge"`
     and `"water_level"`.
   - Assert `FakeForecastStore` contains 2 forecasts per cycle.
   - Assert filtering by `parameter="discharge"` returns only discharge.

2. `TestMultiParameterForecast` — `test_single_parameter_backward_compat`:
   - Use a model returning a single-key dict.
   - Assert 1 forecast stored per cycle.

3. `TestMultiParameterForecast` — `test_key_ensemble_parameter_mismatch_raises`:
   - Construct a dict where key differs from `ensemble.parameter`.
   - Assert `ValueError` raised with descriptive message.

Alert routing tests (previously items 4–6) are now in plan 010
(`tests/unit/services/test_alert_strategy.py` and `test_alert_checker.py`).

**`tests/integration/store/test_forecast_store.py`**

4. `TestParameterFilter` — `test_fetch_latest_with_parameter_filter`:
   - Store forecasts for two parameters in same cycle.
   - Fetch with `parameter="discharge"` → correct result.
   - Fetch with `parameter=None` → returns latest regardless.

5. `TestParameterFilter` — `test_unique_constraint_allows_same_cycle_different_params`:
   - Store two forecasts for same `(station_id, model_id, issued_at)` but different
     `parameter` values.
   - Assert both succeed (validates Phase 0A fix).

6. `TestParameterFilter` — `test_unique_constraint_rejects_duplicate_param`:
   - Store two forecasts for same `(station_id, model_id, issued_at, parameter)`.
   - Assert `IntegrityError` raised.

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/db/metadata.py` | Widen unique index to include `parameter` | 0A |
| `docs/architecture-context.md` | Reconcile `forecasts` schema (add `parameter`, `units`, `qc_status`, `qc_flags` columns; update unique constraint) | 0B |
| `src/sapphire_flow/services/forecast.py` | Multi-parameter storage loop | 1A |
| `alembic/versions/0017_widen_forecast_unique_index.py` | Migration: drop old index, create new with `parameter` | 0A |
| `src/sapphire_flow/protocols/stores.py` | Add `parameter` filter to `ForecastStore` fetch methods (`fetch_latest_forecast`, `fetch_forecasts_for_cycle`, `fetch_forecasts_in_range`) | 3A |
| `docs/spec/types-and-protocols.md` | Update `ForecastStore` fetch method signatures | 3A |
| `src/sapphire_flow/store/forecast_store.py` | Add `parameter` WHERE clause to `PgForecastStore` fetch methods | 3B |
| `tests/fakes/fake_stores.py` | Add `parameter` filter to `FakeForecastStore` fetch methods | 3C |
| `tests/unit/services/test_forecast.py` | New: multi-parameter storage tests | 5B |
| `tests/integration/store/test_forecast_store.py` | New: parameter filter + unique constraint validation tests | 5B |

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "tasks": ["0a", "0b"],
      "parallel": true
    },
    {
      "id": "phase-3",
      "tasks": ["3a"]
    },
    {
      "id": "phase-3-impl",
      "tasks": ["3b", "3c"],
      "parallel": true,
      "depends_on": ["phase-3"]
    },
    {
      "id": "phase-5-store",
      "tasks": ["5b-store"],
      "note": "Store tests (items 4-6): parameter filter + unique constraint",
      "depends_on": ["phase-0", "phase-3-impl"]
    },
    {
      "id": "phase-1",
      "tasks": ["1a"],
      "note": "Deferred to Phase 8 — forecast service does not exist yet",
      "depends_on": ["phase-0"]
    },
    {
      "id": "phase-5-service",
      "tasks": ["5b-service"],
      "note": "Service tests (items 1-3): multi-parameter storage loop",
      "depends_on": ["phase-1", "phase-3-impl"]
    }
  ]
}
```

**Immediate work:** Phase 0 and Phase 3 proceed in parallel. Phase 5-store (items 4–6:
parameter filter + unique constraint tests) follows immediately after both complete.

**Deferred to Phase 8:** Phase 1 (forecast service storage loop) and Phase 5-service
(items 1–3: multi-parameter service tests). These require the forecast service skeleton.

**No-op phases:** Phase 2 (moved to plan 010) and Phase 4 (no changes needed) are omitted
from the dependency graph — they have no tasks to execute.

---

## Guardrails

- Follow plan 003's hindcast pattern for storage loop (key/value check, per-parameter store call), but use `uuid_factory` injection (not `uuid4()` directly) per the newer service pattern
- Run `uv run pytest` after each phase
- After Phase 0A: `alembic upgrade head` succeeds; run integration tests to verify two-parameter inserts succeed
- **Downgrade caveat:** The Phase 0A migration downgrade recreates the 3-column unique index. If multi-parameter forecasts have been stored (same station+model+issued_at, different parameter), the downgrade will fail with a uniqueness violation. Only downgrade on an empty or single-parameter `forecasts` table.
- After Phase 3: verify `isinstance(FakeForecastStore(), ForecastStore)` passes
- Emit `forecast.stored` with `duration_ms` by project convention (mandatory on `*.completed` per `logging.md`; extended to `*.stored` for consistency)
- Bind `station_id` via `bind_contextvars()` (not `bound_contextvars()`) per `logging.md` § Context binding — Prefect task bodies are the scope
- Use `log_prints=False` on all Flow 1 tasks per `logging.md` § Prefect-specific settings
- Pass stores and shared config to `task.map()` via `unmapped()` per `orchestration.md` § Fan-out patterns
- Alert routing is out of scope — see plan 010
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)

---

## Resolved Items

1. **DB schema change IS needed** — The unique index `uq_forecasts_station_model_issued`
   must be widened to include `parameter` (Phase 0A). The `parameter` column itself
   already exists on `forecasts` (`metadata.py` line 609) and the store already reads/writes
   it — only the uniqueness constraint needs updating.

## Open Items

1. **`fetch_latest_forecast` with `parameter=None`** — returns latest by `issued_at` regardless
   of parameter. In a multi-parameter world, if discharge and water_level are stored in the
   same cycle, the result is non-deterministic (depends on insertion order). Callers that need
   a specific parameter MUST pass `parameter=`. Consider whether the API layer (Phase 9)
   should require `parameter` (no default) or return `list[OperationalForecast]` for all
   parameters. Defer to Phase 9 (API) implementation.

2. **Alert routing depends on plan 010** — Phase 2 (alert threshold routing) has been moved
   to plan 010 (multi-model alert strategy). Plan 010 defines the Phase C convergence
   structure, multi-model strategy dispatch, and `ExceedanceResult`/`Alert` model traceability.
   This plan's Phase 1A provides the per-parameter ensembles that plan 010 consumes.

3. **`ForecastStatus` has no `SUPERSEDED` value** — The partial unique index filters
   `WHERE status != 'superseded'`, but `ForecastStatus` only has `RAW`, `REVIEWED`,
   `PUBLISHED`. The filter is always TRUE (no row can match `superseded`), making the
   partial index effectively a full unique index. Pre-existing issue — not introduced
   by this plan. If forecast replacement/versioning is intended, `SUPERSEDED` should be
   added to `ForecastStatus` in a separate plan. This plan carries forward the existing
   WHERE clause unchanged.
