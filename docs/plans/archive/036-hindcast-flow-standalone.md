# Plan 036 — Make `run_hindcast_flow` Standalone-Triggerable

**Status**: DONE
**Phase**: 8 (Forecast Cycle)
**Depends on**: Plan 034 (forecast_horizon_steps — merged)

## Context

`run_hindcast_flow` (Flow 7 in architecture-context.md) is declared in `orchestration.md` as both "subflow" and "on-demand" triggerable. The subflow path works — `train_models_flow` and `onboard_model_flow` pass pre-wired stores, model, artifact, and forcing source. The on-demand path does not — the flow has no self-resolution block and cannot bootstrap its own dependencies from `DATABASE_URL`.

This means a model admin cannot trigger a hindcast from the Prefect UI or API, which `orchestration.md` (line 165) explicitly requires. The flow is dead code for standalone use.

### Current state

`run_hindcast_flow` (`flows/run_hindcast.py:102`) takes 17 parameters. Of these, 7 are opaque `object = None` store/model/artifact/forcing parameters that must be injected by the caller:

| Parameter | Type | Self-resolved? |
|---|---|---|
| `model_id` | `ModelId` | n/a (user-provided) |
| `artifact_id` | `ArtifactId` | n/a (user-provided) |
| `station_id` / `group_id` | `StationId` / `StationGroupId` | n/a (user-provided) |
| `period_start` / `period_end` | `UtcDatetime \| None` | Yes (defaults to 2020-01-01 → now) |
| `time_step` | `timedelta` | Yes (default `1 day`) |
| `model` | `object` | **No** — caller must pass |
| `artifact` | `object` | **No** — caller must pass |
| `forcing_source` | `object` | **No** — caller must pass |
| `obs_store` | `object` | **No** — caller must pass |
| `hindcast_store` | `object` | **No** — caller must pass |
| `station_store` | `object` | **No** — caller must pass |
| `basin_store` | `object` | **No** — caller must pass |
| `clock` / `rng` / `hindcast_run_id` | various | Yes |

**Missing parameter**: The flow has no `group_store` parameter despite calling `station_store.fetch_group(group_id)` at line 166. `fetch_group` is on the `StationGroupStore` protocol, not `StationStore`. `PgStationStore` does not have this method. Tests pass only because `_CombinedStationGroupStore` (a test-only dual-inheritance fake) combines both protocols. Any production standalone group hindcast would `AttributeError`. This plan fixes the latent bug alongside the self-resolution work.

### Template: `run_forecast_cycle_flow`

`run_forecast_cycle_flow` (`flows/run_forecast_cycle.py:186`) demonstrates the correct pattern:

1. All store parameters default to `None`.
2. `if station_store is None:` → read `DATABASE_URL` → `_setup_production_stores()` → unpack all stores.
3. `if models is None:` → `discover_models()`.
4. `forcing_source = StoreBackedReanalysisSource(forcing_store)` — constructed in-flow.
5. `assert` all stores are non-None before proceeding.

### What this plan does

1. Extract the duplicated `_setup_production_stores` helper to `flows/_db.py` (currently duplicated in 3 flows).
2. Add a `group_store` parameter to `run_hindcast_flow` and fix the latent `station_store.fetch_group()` bug (uses wrong protocol — see above).
3. Add the self-resolution pattern to `run_hindcast_flow` so it can be triggered standalone with only `(model_id, artifact_id, station_id/group_id)` — the rest is resolved from the environment.
4. Update all affected callers, tests, and docs to match the new semantics.

### What this plan does NOT do

- **Prefect deployment registration** (`prefect.yaml`, `init` service): Out of scope — the `init` service does not exist yet and is a cross-cutting concern for all flows. Tracked in `orchestration.md` line 190 but not implemented for any flow.
- **`train_models_flow` / `compute_skills_flow` self-resolution**: Same gap, same fix pattern, but different flows. Can be addressed in a follow-up plan using the same template.

---

## Tasks

### Task 0: Extract `_setup_production_stores` to `flows/_db.py`

`_setup_production_stores` is a thin 6-line wrapper duplicated in 3 flows (`run_forecast_cycle.py:52`, `ingest_observations.py:74`, `onboard.py:50`). All three already import `make_pg_stores` and `run_migrations` from `_db.py` — the duplication is the wrapper shell (create engine, call `run_migrations`, open AUTOCOMMIT connection, call `make_pg_stores`), not the business logic. The `onboard.py` variant has two extra `log.info` calls around migrations; these are dropped in the extraction (Alembic's own logging is sufficient, though the structlog events carried Prefect context fields like `flow_run_id` — an acceptable debuggability tradeoff since neither call includes `duration_ms` or other timing data).

**Changes in `src/sapphire_flow/flows/_db.py`**:

Add `setup_production_stores` (public — no underscore, since it's now an exported API) after `make_pg_stores`:

```python
def setup_production_stores(database_url: str) -> tuple[sa.Connection, dict[str, object]]:
    engine = sa.create_engine(database_url)
    run_migrations(engine)
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    stores = make_pg_stores(conn)
    return conn, stores
```

Note: return type is `tuple[sa.Connection, dict[str, object]]` (not `tuple[object, ...]`) since the module already imports `sqlalchemy`.

**Changes in `src/sapphire_flow/flows/run_forecast_cycle.py`**:

Remove the local `_setup_production_stores` function (lines 52–61). Update the call site (line 217) to import from `_db`:

```python
from sapphire_flow.flows._db import setup_production_stores
...
_conn, stores = setup_production_stores(database_url)
```

**Changes in `src/sapphire_flow/flows/ingest_observations.py`**:

Remove the local `_setup_production_stores` function (lines 74–85). Update the call site to use the imported version.

**Changes in `src/sapphire_flow/flows/onboard.py`**:

Remove the local `_setup_production_stores` function (lines 50–63, including the `log.info` wrappers). Update the call site to use the imported version.

**Files**: `src/sapphire_flow/flows/_db.py`, `src/sapphire_flow/flows/run_forecast_cycle.py`, `src/sapphire_flow/flows/ingest_observations.py`, `src/sapphire_flow/flows/onboard.py`

**Verify**: `uv run ruff check src/sapphire_flow/flows/ && uv run pytest tests/unit/flows/ --tb=short -q`

### Task 1: Add `group_store` parameter and self-resolution block to `run_hindcast_flow`

**Changes in `src/sapphire_flow/flows/run_hindcast.py`**:

Add `import os` to the top-level imports.

**1a. Add `group_store` parameter** — add `group_store: object = None` to the flow signature (after `station_store`):

```python
def run_hindcast_flow(
    ...
    station_store: object = None,
    group_store: object = None,       # NEW — was missing; fetch_group is on StationGroupStore, not StationStore
    basin_store: object = None,
    ...
```

**1b. Fix `station_store.fetch_group()` bug** — change line 166 from:

```python
        group = station_store.fetch_group(group_id) if station_store else None
```

to:

```python
        group = group_store.fetch_group(group_id) if group_store else None
```

`fetch_group` is defined on the `StationGroupStore` protocol (`protocols/stores.py:509`), not on `StationStore`. `PgStationStore` has no `fetch_group`. Tests only passed because `_CombinedStationGroupStore` (a test-only dual-inheritance fake) combines both protocols.

**1c. Early validation** — immediately after the `clock`/`rng`/`hindcast_run_id`/`period` defaults (line 136), add the routing parameter check:

```python
    if station_id is None and group_id is None:
        raise ValueError("Either station_id or group_id must be provided")
```

This moves the validation from the `else` branch at line 188 to *before* the resolution block. Without this, a call with no station_id/group_id and no stores would attempt store resolution (reading `DATABASE_URL`) before discovering the call is invalid — producing a confusing `KeyError` instead of the correct `ValueError`.

Remove the existing `else: raise ValueError(...)` at line 187–188 (now dead code — the check fires earlier).

**1d. Store resolution** — after the early validation:

```python
    # --- Production setup ---
    _conn: object = None
    if station_store is None:
        database_url = os.environ["DATABASE_URL"]

        from sapphire_flow.flows._db import setup_production_stores

        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        group_store = stores["group_store"]
        obs_store = stores["obs_store"]
        hindcast_store = stores["hindcast_store"]
        basin_store = stores["basin_store"]
        artifact_store = stores["artifact_store"]
        forcing_store = stores["forcing_store"]
    else:
        artifact_store = None  # not available when stores are caller-provided
        forcing_store = None
```

**1e. `forcing_source` resolution** — after the store resolution block (outside both branches), using the `onboard.py` pattern:

```python
    if forcing_source is None and forcing_store is not None:
        from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource

        forcing_source = StoreBackedReanalysisSource(forcing_store)
```

This is placed *after* the `if station_store is None:` / `else:` block, not inside it. Rationale:
- Respects caller-provided `forcing_source` (no silent overwrite).
- Works for both resolution paths: self-resolved `forcing_store` (from `make_pg_stores`) or a `forcing_store` that a future caller might pass directly.
- Matches `onboard.py:113–119` which guards on both `forcing_source is None` and `forcing_store is not None`.
- Differs from `run_forecast_cycle.py:308–312` which constructs unconditionally — that flow has no `forcing_source` parameter, so there is no caller value to respect.

**1f. Model resolution** — after forcing resolution:

```python
    if model is None:
        from sapphire_flow.services.model_registry import discover_models

        all_models = discover_models()
        model = all_models.get(model_id)
        if model is None:
            raise ValueError(
                f"Model {model_id} not found in registry. "
                f"Available: {list(all_models.keys())}"
            )
```

**1g. Artifact resolution** — after model resolution:

```python
    if artifact is None:
        if artifact_store is None:
            raise ValueError(
                "Cannot resolve artifact: artifact_store is only available "
                "when stores self-resolve from DATABASE_URL. Either pass "
                "artifact explicitly or omit station_store to trigger "
                "full self-resolution."
            )
        result = artifact_store.fetch_artifact(artifact_id)
        if result is None:
            raise ValueError(f"Artifact {artifact_id} not found in store")
        _, artifact_bytes = result
        artifact = model.deserialize_artifact(artifact_bytes)
```

**1h. Remove old guards** — remove the `if model is None: raise ValueError(...)` / `if artifact is None: raise ValueError(...)` guards inside both the `station_id` and `group_id` branches (lines 139–142, 162–165). The resolution blocks above guarantee both are non-None by this point.

**1i. Store validation** — before the dispatch branches. Use `ValueError` with messages (not bare `assert`, which is disabled by `python -O` and produces no diagnostic):

```python
    _required = {
        "station_store": station_store,
        "obs_store": obs_store,
        "hindcast_store": hindcast_store,
        "basin_store": basin_store,
        "forcing_source": forcing_source,
    }
    if group_id is not None:
        _required["group_store"] = group_store
    _missing = [k for k, v in _required.items() if v is None]
    if _missing:
        raise ValueError(
            f"Required dependencies are None: {_missing}. "
            "Either pass all stores explicitly or omit station_store to "
            "trigger full self-resolution from DATABASE_URL."
        )
```

**Note on `artifact_store` scope**: `artifact_store` is only available during self-resolution (when `station_store is None` triggers full store bootstrapping from `DATABASE_URL`). It is not a flow parameter and is not passed to `run_station_hindcast` or `run_group_hindcast`. When callers provide stores individually (subflow path), `artifact_store` is set to `None` in the `else` branch — if they also omit `artifact`, the error message explains the constraint.

**Implicit callers (no change needed)**: `_run_station_hindcast_task()` and `_run_group_hindcast_task()` are internal Prefect task wrappers that forward parameters from `run_hindcast_flow`. They receive already-resolved values. No code change required.

**1j. Update production callers** — both callers must now pass the new `group_store` parameter:

In `src/sapphire_flow/flows/train_models.py` (line 285 call site), add `group_store=group_store` to the `run_hindcast_flow(...)` call. `train_models_flow` already receives `group_store` as a separate parameter (line 145).

In `src/sapphire_flow/flows/onboard_model.py` (line 513 call site), add `group_store=group_store` to the `run_hindcast_flow(...)` call. `onboard_model_flow` already receives `group_store` as a separate parameter.

**Files**: `src/sapphire_flow/flows/run_hindcast.py`, `src/sapphire_flow/flows/train_models.py`, `src/sapphire_flow/flows/onboard_model.py`

**Verify**: `uv run ruff check src/sapphire_flow/flows/run_hindcast.py src/sapphire_flow/flows/train_models.py src/sapphire_flow/flows/onboard_model.py`

### Task 2: Update tests

**Changes in `tests/unit/flows/test_run_hindcast.py`**:

**Imports**: Add `FakeModelArtifactStore` to the import block (from `tests.fakes.fake_stores`). It is not currently imported in this file but is needed by tests 2h, 2i, 2k, 2o, 2q. Note: `FakeModelArtifactStore.store_artifact()` auto-generates the `ArtifactId` on each call — tests that pre-load artifacts must capture the returned ID and pass it as `artifact_id` to the flow (see 2h, 2k, 2o for details).

The self-resolution blocks change the semantics of passing `model=None`, `artifact=None`, or omitting stores. Four existing tests assert on the old error messages and must be removed (2a–2c). Two group-path happy-path tests must be updated to pass the new `group_store` parameter (2e). The `_CombinedStore` workaround in `test_train_models.py` must be removed (2s). Fourteen new tests cover the resolution paths (2f–2r-bis), plus one test update in `test_train_models.py` (2s).

#### Existing tests to update (group-path tests)

**2a. Replace `test_station_hindcast_no_model_raises` and `test_station_hindcast_no_artifact_raises`**:

These tests pass `model=None` / `artifact=None` with all stores at their None defaults. After the change, the `station_store is None` resolution block fires first, reading `os.environ["DATABASE_URL"]` — producing a `KeyError`, not the old `ValueError`. The old error semantics ("you must provide X") are replaced by resolution semantics ("I'll try to resolve X").

Remove both tests. They are replaced by tests 2f–2i below which cover model/artifact resolution.

**2b. Replace `test_group_hindcast_no_model_raises`**:

This test passes `model=None` with `station_store=combined_store`. Store resolution does not fire (station_store is not None). Model resolution fires — calls `discover_models()`, which is not mocked. The old `ValueError("model must be provided for group hindcast")` is replaced by `ValueError("Model test_model not found in registry")`.

Remove this test. Replaced by test 2f below.

**2c. Replace `test_group_hindcast_no_artifact_raises`**:

This test passes `artifact=None` with `station_store=combined_store`. Store resolution does not fire. Artifact resolution fires — checks `artifact_store is None` → raises `ValueError("Cannot resolve artifact: artifact_store is only available...")`.

Remove this test. Replaced by test 2h below.

**2d. `test_neither_station_nor_group_raises` — no change needed**:

After Task 1's early validation fix, this test works as before: the station_id/group_id check fires before any resolution. The test's `ValueError("Either station_id or group_id must be provided")` assertion remains correct. This test serves as a **regression guard** for Task 1c's validation placement — it proves the check fires before `DATABASE_URL` is read.

**2e. Existing group-path tests — update for `group_store` parameter**:

`test_station_hindcast_stores_results` passes all stores explicitly — resolution blocks do not fire. No change needed.

`test_group_hindcast_produces_per_station_results` and `test_group_not_found_raises` currently pass `station_store=combined_store` (the dual-inheritance fake). After Task 1a–1b, they must pass `group_store=combined_store` in addition to `station_store=combined_store` (or split the fake). The `_CombinedStationGroupStore` continues to satisfy both protocols, so both parameters can point to the same instance.

#### New tests — self-resolution path

**Monkeypatch note**: Tests 2f–2q use `monkeypatch.setattr` to replace `discover_models` and `setup_production_stores`. These are internal functions, not external boundaries, so this is a pragmatic deviation from the "fakes over mocks" convention in CLAUDE.md. The alternative — making these injectable parameters — would add 2 more parameters to an already-18-parameter flow. The lazy-import pattern (inside conditional blocks) also makes constructor-injection awkward. This pragmatic choice is accepted; the functions being patched are simple callables with no complex interaction to verify.

For `setup_production_stores` (lazily imported inside the flow body), the correct patch target is the source module: `sapphire_flow.flows._db.setup_production_stores`. For `discover_models`, the target is `sapphire_flow.services.model_registry.discover_models`. Both are lazily imported via `from ... import ...` inside conditional blocks — patching the source module ensures the patched version is picked up at import time.

**2f. `test_model_resolved_from_registry`** (station path):

Call `run_hindcast_flow.fn()` with `model=None`, all stores passed explicitly (subflow-like, but model omitted). Monkeypatch `sapphire_flow.services.model_registry.discover_models` to return `{_MODEL_ID: FakeStationForecastModel()}`. Pass all other parameters as in the happy-path test.

Assert: flow completes successfully, hindcast results returned.

**2g. `test_model_not_in_registry_raises`** (station path):

Same as 2f, but `discover_models` returns `{}` (empty dict — model not registered). Assert: `ValueError` matching `"not found in registry"`.

**2h. `test_artifact_resolved_from_store`** (station path):

Call `run_hindcast_flow.fn()` with `artifact=None` and `station_store=None`. Use `monkeypatch.setenv("DATABASE_URL", "sqlite://")`. Monkeypatch `sapphire_flow.flows._db.setup_production_stores` to return fake stores (use `_build_station_stores` to create the standard fakes, plus a `FakeModelArtifactStore`). **Important**: `FakeModelArtifactStore.store_artifact()` auto-generates the `ArtifactId` — the test must call `store_artifact()` first, capture the returned `ArtifactId`, and pass *that* as `artifact_id` to the flow. Monkeypatch `discover_models` to return the model.

Assert: flow completes successfully, the artifact was fetched from the fake artifact store and deserialized.

**2i. `test_artifact_not_found_in_store_raises`** (station path):

Same setup as 2h, but the `FakeModelArtifactStore` is empty (no artifact stored). Assert: `ValueError` matching `"not found in store"`.

**2j. `test_artifact_resolution_without_store_resolution_raises`**:

Pass `station_store=<fake>` (skips store resolution), `artifact=None` (triggers artifact resolution). No `artifact_store` is available (it's only created during self-resolution). Assert: `ValueError` matching `"Cannot resolve artifact"`.

**2k. `test_full_self_resolution_from_env`**:

Pass only `(model_id, artifact_id, station_id)` — everything else at defaults. Monkeypatch `DATABASE_URL`, monkeypatch `sapphire_flow.flows._db.setup_production_stores` to return fake stores (including artifact store and forcing store), monkeypatch `discover_models`. **Important**: same `ArtifactId` constraint as 2h — call `store_artifact()` on the `FakeModelArtifactStore`, capture the returned `ArtifactId`, and pass it as `artifact_id`. Assert: flow completes, hindcast results returned. This is the primary test for the standalone use case.

**2l. `test_self_resolution_skipped_when_stores_provided`**:

Pass all stores explicitly (the subflow path), including `forcing_source`. Use `monkeypatch.delenv("DATABASE_URL", raising=False)` to guarantee the environment is clean — without this, a `DATABASE_URL` set in CI would cause the test to silently exercise the wrong path. Assert: flow completes successfully — proves the resolution block is skipped entirely. This is a regression guard for the subflow path.

**2m. `test_missing_database_url_raises`**:

Pass `station_store=None`, ensure `DATABASE_URL` is NOT in env (`monkeypatch.delenv("DATABASE_URL", raising=False)`). Assert: `KeyError` — clear error for misconfigured standalone invocation.

**2n. `test_explicit_model_not_overridden_by_discovery`**:

Pass `model=FakeStationForecastModel()` explicitly, all stores provided. Monkeypatch `discover_models` to raise `RuntimeError("should not be called")`. Assert: flow completes — `discover_models` is never invoked when model is provided.

**2o. `test_group_path_self_resolution`**:

Same as 2k but for group hindcast: pass `(model_id, artifact_id, group_id)`. Monkeypatch stores + models. Same `ArtifactId` constraint as 2h/2k. The `setup_production_stores` fake returns a stores dict including `"group_store"` as a `FakeStationGroupStore` that has had `store_group(group)` called on it, where `group.station_ids` matches the station IDs populated in the fake `station_store`. Assert: flow completes with per-station results dict.

**2p. `test_group_model_not_in_registry_raises`**:

Same as 2g but for group path: pass `group_id` (not `station_id`), all stores explicitly including `group_store`, `model=None`. Monkeypatch `discover_models` to return `{}`. Assert: `ValueError` matching `"not found in registry"`. This is the group-path equivalent of 2g — ensures model resolution errors surface correctly on the group path.

**2q. `test_group_artifact_resolution_without_store_resolution_raises`**:

Same as 2j but for group path: pass `station_store=<fake>`, `group_store=<fake>`, `artifact=None`. Assert: `ValueError` matching `"Cannot resolve artifact"`. Group-path equivalent of 2j.

**2r. `test_partial_stores_raises`** (station path):

Pass `station_store=<fake>` but omit `obs_store` (defaults to `None`), with `model`, `artifact`, `forcing_source` all provided. Assert: `ValueError` matching `"Required dependencies are None"` with `obs_store` in the missing list. This tests the store validation guard (Task 1i) for partial store provision.

**2r-bis. `test_group_partial_stores_missing_group_store_raises`** (group path):

Pass `station_store=<fake>`, `group_id=<valid>`, but omit `group_store` (defaults to `None`), with `model`, `artifact`, `forcing_source`, `obs_store`, `hindcast_store`, `basin_store` all provided. Assert: `ValueError` matching `"Required dependencies are None"` with `group_store` in the missing list. This is the group-path equivalent of 2r — ensures the conditional `group_store` validation in Task 1i fires.

#### Changes in `tests/unit/flows/test_train_models.py`

**2s. Update `_CombinedStore` workaround**:

`test_train_models.py` has a `_CombinedStore` class (line 439–447) with `__getattr__` that proxies `fetch_group` calls to `group_store`. This was a workaround for the `station_store.fetch_group()` bug in `run_hindcast_flow`. After Task 1b (which changes the call to `group_store.fetch_group()`), this workaround is no longer needed — `train_models_flow` now forwards `group_store` as a separate parameter (Task 1j).

Remove the `_CombinedStore` class and its comment referencing the bug. In the test setup, pass `station_store=station_store` (the plain `FakeStationStore`) instead of `station_store=combined_store`. The test already passes `group_store=group_store` separately to `train_models_flow` (which now forwards it to `run_hindcast_flow`).

**Files**: `tests/unit/flows/test_run_hindcast.py`, `tests/unit/flows/test_train_models.py`

**Verify**: `uv run pytest tests/unit/flows/test_run_hindcast.py tests/unit/flows/test_train_models.py --tb=short -q`

### Task 3: Update documentation

**`docs/architecture-context.md`** — no change needed. The Flow 7 trigger line already reads: *"On-demand (from Flows 6/9, or standalone by model admin)"*. The document says nothing about store injection; it operates at a higher level of abstraction.

**`docs/v0-scope.md`** — no change needed. §H exists as the implementation phases heading. Flow 7 (hindcast) was implemented as part of Phase 7 (Model framework + training) and appears in the priority table as "Flow 6 → 7 → 8 — Train → hindcast → skill". No subflow-only language exists — nothing to update.

**`docs/standards/orchestration.md`** — no change needed. It already correctly describes `run-hindcast` as both subflow and on-demand (line 165–169).

**Files**: none (documentation already consistent with this plan's changes)

### Task 4: Full verification

```bash
uv run pytest --tb=short -q
uv run ruff check src/ tests/
uv run pyright src/sapphire_flow/flows/run_hindcast.py src/sapphire_flow/flows/_db.py
uv run bump-my-version bump patch
```

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "tasks": ["0"],
      "note": "Extract shared helper — blocks Task 1 which imports it"
    },
    {
      "id": "phase-1",
      "tasks": ["1"],
      "depends_on": ["phase-0"],
      "note": "Core change — add group_store, fix fetch_group bug, add self-resolution block, update callers"
    },
    {
      "id": "phase-2",
      "tasks": ["2", "3"],
      "parallel": true,
      "depends_on": ["phase-1"],
      "note": "Tests and docs can proceed in parallel"
    },
    {
      "id": "phase-3",
      "tasks": ["4"],
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Risk Assessment

### 1. Subflow callers must not break

When `train_models_flow` or `onboard_model_flow` calls `run_hindcast_flow` with pre-wired stores, the self-resolution block does NOT fire (`station_store is not None`). The `model`/`artifact` resolution blocks also do not fire (callers pass both). The only change for existing callers is the new `group_store` parameter (Task 1j) — both callers already have `group_store` available and must now forward it. Verified: both call sites (`train_models.py:285`, `onboard_model.py:513`) currently pass 6 of the 7 opaque parameters (`model`, `artifact`, `forcing_source`, `obs_store`, `hindcast_store`, `station_store`, `basin_store`) but do not forward `group_store`; adding `group_store=group_store` is a one-line addition at each site.

### 2. Migration on every standalone invocation

`setup_production_stores` runs `run_migrations(engine)` on every standalone call. This is consistent with all other self-resolving flows (`run_forecast_cycle`, `ingest_observations`, `onboard`). Alembic `upgrade head` is a no-op when already at head — the cost is one `SELECT` against `alembic_version`. Acceptable for on-demand invocations.

**Note**: `cicd.md` §First-boot sequence specifies that migrations should use `DATABASE_URL_DIRECT` (bypassing PgBouncer). In v0 there is no PgBouncer, so `DATABASE_URL` and `DATABASE_URL_DIRECT` are identical. In v1, when PgBouncer is introduced, all self-resolving flows (`run_forecast_cycle`, `ingest_observations`, `onboard`, and now `run_hindcast`) will need to switch their migration call to use `DATABASE_URL_DIRECT`. This is a pre-existing codebase-wide gap, not introduced by this plan. Tracked as a v1 concern.

**Concurrent migrations**: If two standalone hindcast flows start simultaneously on an un-migrated database, both call `run_migrations(engine)`. Alembic does not use advisory locks — concurrent DDL could fail. At head (the common case), both processes read "at head" and no-op safely. Pre-existing risk shared with all self-resolving flows.

### 3. AUTOCOMMIT connection lifecycle

The self-resolution block opens a connection with `isolation_level="AUTOCOMMIT"`. The connection's lifetime is the flow function scope — no `try/finally` or context manager wraps it. If the flow raises an exception after `_conn` is assigned, the connection leaks until the process exits or the DB idle timeout fires. This matches `run_forecast_cycle`'s behavior exactly (same pattern, same gap). Acceptable for v0 on-demand invocations; connection lifecycle cleanup is a cross-cutting improvement for all self-resolving flows.

### 4. `discover_models()` loads all models

When `model is None`, the resolution calls `discover_models()` which loads all registered model classes. For v0 this is 3 models (LR daily, persistence, climatology). The overhead is negligible. For v1 with many models, consider caching or targeted loading — deferred.

### 5. Artifact deserialization in the flow function

The resolved artifact is deserialized in the flow function body (not in a task). This means the full artifact bytes pass through the flow's memory. For v0 LR daily artifacts (~1 KB), this is negligible. For v1 ML models with large artifacts (100s MB), consider moving deserialization into the task or using artifact references. Deferred.

### 6. Extraction of `_setup_production_stores`

Task 0 extracts the helper from 3 flows into `_db.py`. The `onboard.py` variant has two `log.info` calls around `run_migrations` that are dropped — Alembic's own logging covers migration progress. Verified: neither call includes `duration_ms` or other timing data, so no D6 per-step instrumentation (per `logging.md` §Timing) is lost. All 3 flows are updated to import the shared version. If any flow previously relied on the log events `migrations_running` / `migrations_complete` for monitoring, they would need to be restored at the call site. Current monitoring (Prefect run state + DB freshness checks in Flow 4) does not pattern-match on these log events.

### 7. Artifact resolution asymmetry

`artifact_store` is only available when the full store resolution block fires (`station_store is None`). If a caller provides stores individually (subflow path) but omits `artifact`, the artifact resolution block cannot fetch the artifact — it raises `ValueError` with an actionable message explaining the constraint. This is a new failure mode, but it replaces a less informative `ValueError("artifact must be provided")`. The two production callers (`train_models_flow`, `onboard_model_flow`) always pass both `model` and `artifact`, so this path is never hit in practice.

### 8. Related flows without self-resolution

`train_models_flow` and `compute_skills_flow` have the same gap. They are not addressed in this plan because:
- `train_models_flow` is always called from `onboard_model_flow` which wires stores. Its on-demand use case (retrain a specific model) is rarer than hindcast re-execution.
- `compute_skills_flow` is always called as a subflow from `train_models_flow`.
- Both can be fixed with the same pattern in a follow-up plan. After Task 0, they can import `setup_production_stores` from `_db.py` directly.

### 9. Existing test breakage — 7 test changes

Four existing tests assert on the old "must be provided" error messages and are removed: two in 2a (`test_station_hindcast_no_model_raises`, `test_station_hindcast_no_artifact_raises`), one in 2b (`test_group_hindcast_no_model_raises`), one in 2c (`test_group_hindcast_no_artifact_raises`). Two group-path tests (one happy-path, one error-path) are updated for the new `group_store` parameter (2e). `test_neither_station_nor_group_raises` and `test_station_hindcast_stores_results` are unaffected. `test_train_models.py` must also be updated: the `_CombinedStore` workaround (line 439–447) that proxied `fetch_group` to `station_store` is now stale and should be simplified — see Task 2s. Fourteen new tests plus one test update cover the resolution semantics (2f–2s). See Task 2 for the detailed mapping.

### 10. `os.environ["DATABASE_URL"]` vs security.md

`security.md` §Secrets-management states: "Application code reads secrets from file paths, never from environment variables in production." The OWASP A02 table entry reinforces: "Secrets in Docker secrets, not env vars." The plan uses `os.environ["DATABASE_URL"]`, which contradicts both rules as written. However, all three existing self-resolving flows (`run_forecast_cycle`, `ingest_observations`, `onboard`) already use the same pattern. The plan is consistent with the codebase but inconsistent with the standard's letter. `DATABASE_URL` is treated as a derived configuration value (connection string containing host, port, dbname, and credentials), not a raw secret file — but the mechanism by which it is set (Docker Compose `environment:`, entrypoint assembly, or other) is not documented in `security.md` or `cicd.md`. If `DATABASE_URL` contains the password inline (as is typical), it is effectively a secret in an environment variable, and the "file paths only" rule applies. This is a pre-existing codebase-wide deviation shared by all self-resolving flows, not introduced by this plan. A codebase-wide resolution (e.g., reading `db_password` from file and constructing the URL in application code, or documenting the entrypoint assembly mechanism) is deferred as a cross-cutting concern.

### 11. `group_store` parameter — fixing a latent pre-existing bug

`run_hindcast_flow` calls `station_store.fetch_group(group_id)` at line 166. `fetch_group` is defined on `StationGroupStore` (`protocols/stores.py:509`), not `StationStore`. `PgStationStore` has no `fetch_group` method. Tests only pass because `_CombinedStationGroupStore` (a test-only dual-inheritance fake) combines both protocols. Any production standalone group hindcast would `AttributeError`. This plan fixes the bug by adding `group_store` as a separate parameter (Task 1a–1b) and updating both callers (Task 1j). The two production callers (`train_models_flow`, `onboard_model_flow`) already have `group_store` available as a separate parameter.

### 12. `forcing_source` construction placement

The plan constructs `forcing_source` using the `onboard.py` pattern (`if forcing_source is None and forcing_store is not None:`), placed after the store resolution block. This:
- Respects caller-provided `forcing_source` (no silent overwrite)
- Works for both resolution paths (self-resolved or caller-provided stores)
- Differs from `run_forecast_cycle.py` which constructs unconditionally — but that flow has no `forcing_source` parameter, so there is no caller value to respect

In the subflow path (caller provides stores), `forcing_store` is set to `None` in the `else` branch, so this conditional does not fire and the caller must provide `forcing_source` directly. The two production callers both pass `forcing_source` explicitly. The store validation guard (Task 1i) catches any case where `forcing_source` is still `None` at dispatch time.

### 13. Prefect serialization — standalone path requires `ThreadPoolTaskRunner`

Self-resolved stores include SQLAlchemy connections, which are not pickle-serializable. The existing subflow path handles this by running in-process with `ThreadPoolTaskRunner` (noted in `train_models.py:313–315`). The standalone path creates the same non-serializable objects inside the flow function body — it also requires an in-process task runner. If a future Prefect deployment registers `run-hindcast` with a subprocess or distributed worker, task parameter serialization will fail. This constraint is shared with all self-resolving flows (`run_forecast_cycle`, `ingest_observations`, `onboard`) and is not introduced by this plan. When the `init` service and `prefect.yaml` deployment registration are implemented (out of scope — see §What this plan does NOT do), the `run-hindcast` deployment must use `ThreadPoolTaskRunner` or equivalent in-process execution.

## Verification

```bash
uv run pytest --tb=short -q
uv run ruff check src/ tests/
uv run pyright src/sapphire_flow/flows/run_hindcast.py src/sapphire_flow/flows/_db.py
uv run bump-my-version bump patch
```
