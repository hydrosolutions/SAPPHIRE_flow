# Plan 034 — Add `forecast_horizon_steps` to Model Interface

**Status**: DONE  
**Phase**: 7 + 8 (Model Framework + Forecast Cycle)  
**Unblocks**: Plan 032 (hindcast predictions — currently 100% failure rate due to horizon mismatch)  
**Note**: Execute this plan BEFORE Plan 032. Plan 032 Task 1 becomes a no-op after this plan's Task 4.

## Context

`ModelDataRequirements` declares what a model needs (features, lookback, time steps, spatial type) but not what it produces. The forecast horizon is currently a magic constant (`120`) injected top-down by the orchestrator at every call site:

| Caller | Source | Value |
|---|---|---|
| `run_forecast_cycle.py:390` | `getattr(config, "forecast_horizon_steps", 120)` — `DeploymentConfig` has no such field, so `getattr` always falls back | **120** |
| `run_station_hindcast()` (hindcast.py:212) | Function parameter default | **120** |
| `run_group_hindcast()` (hindcast.py:313) | Function parameter default | **120** |
| `_make_hindcast_fn()` (onboarding.py:102) | `model.data_requirements.lookback_steps` — wrong field | **7** for LR daily (bug — reads lookback, not horizon) |
| `smoke_test_model()` (model_onboarding.py:294,323) | `min(req.lookback_steps, len(data.future_dynamic))` — proxy | **7** (for LR daily) |

This causes two distinct failure modes depending on the caller:

**Standalone hindcast flow (`run_hindcast_flow`) — 100% failure rate:**

1. `assemble_station_training_data()` (training_data.py:149) produces `future_dynamic = forcing_df.clear()` — always 0 rows (correct by design: training data is all historical reanalysis, no NWP forecasts exist at training time).
2. `LinearRegressionDaily.train()` (linear_regression_daily.py:83–86) sees 0 rows → falls back to `n_steps = _LOOKBACK = 7`.
3. At hindcast predict time, `forecast_horizon_steps=120` is passed in `StationModelInputs` (the function default — no caller overrides it).
4. `predict()` (linear_regression_daily.py:174) checks `if horizon > art.n_steps` → `120 > 7` → `ValueError`.
5. Every hindcast step fails. **100% failure rate, zero successful predictions.**

**Onboarding/smoke-test paths — silent wrong-value bug:**

`_make_hindcast_fn()` (onboarding.py:102) and `smoke_test_model()` (model_onboarding.py:294,323) pass `forecast_horizon_steps=model.data_requirements.lookback_steps` = 7. The guard `7 > 7` is False — no `ValueError`. Predictions **succeed**, but produce **7-step forecasts** instead of the correct 5-step operational horizon. This is a silent correctness bug, not an outright failure.

The root cause: the model has no way to declare its operational forecast horizon, so the caller guesses (wrongly).

### Design rationale

`ModelDataRequirements` is already bidirectional — it carries both input requirements and output declarations:

- `target_parameters` — what the model forecasts (output) AND what observations it needs (input)
- `supported_time_steps` — what temporal resolution the model works at (both)
- `lookback_steps` — how far back the model looks (input)

`forecast_horizon_steps` completes the temporal pair: how far forward the model projects. It is both an input requirement (how many future forcing steps to provide) and an output declaration (how many forecast steps to expect). This is consistent with the ForecastInterface contract philosophy (memory: project_forecast_interface_contract.md): the model declares its requirements, the orchestrator delivers data matching those requirements.

### v0 horizon default: 5 days

All v0 daily models declare `forecast_horizon_steps = 5`. Rationale: ICON-CH2-EPS — the v0 NWP forcing product — provides 120 hourly timesteps = **5 days** of lead time. A daily model resampling this forcing gets at most 5 daily future timesteps. Declaring `_HORIZON = 5` matches the operational NWP constraint: the model cannot produce forecasts beyond the available forcing window without a gap-filling strategy (persistence extension, climatology fill), which is out of scope for v0.

`LinearRegressionDaily` decouples `_LOOKBACK = 7` (past window) from `_HORIZON = 5` (forecast window) — the model uses 7 days of past data to produce 5-day forecasts. When v1 introduces longer-range NWP products (ECMWF IFS, 15-day lead time), models targeting those products can declare a larger horizon independently.

---

## Tasks

### Task 1: Add `forecast_horizon_steps` to `ModelDataRequirements`

**Changes in `src/sapphire_flow/types/model.py`**:

Add the field after `lookback_steps` (line 266):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelDataRequirements:
    target_parameters: frozenset[str]
    past_dynamic_features: frozenset[str]
    future_dynamic_features: frozenset[str]
    static_features: frozenset[str]
    supported_time_steps: frozenset[timedelta]
    lookback_steps: int
    forecast_horizon_steps: int          # NEW — how many steps forward the model produces
    spatial_input_type: SpatialRepresentation

    def __post_init__(self) -> None:
        if self.forecast_horizon_steps < 1:
            raise ValueError(
                f"forecast_horizon_steps must be ≥ 1, got {self.forecast_horizon_steps}"
            )
```

**Note**: `ModelDataRequirements` already has no `__post_init__`; this adds one. The positivity guard is consistent with the codebase's frozen-dataclass validation philosophy (`GeoCoord`, `TimeRange`, etc.) and prevents nonsensical zero/negative horizons from propagating silently to downstream arithmetic. If `lookback_steps` also lacks a guard, add one in the same `__post_init__` for consistency.

**Changes in `docs/spec/types-and-protocols.md`**:

Update the `ModelDataRequirements` definition (lines 1195–1204) to include the new field with a description matching the code.

**Changes in `docs/architecture-context.md`**:

Update the `ModelDataRequirements` definition (lines 1491–1502) to include the new field. Add an inline comment: `# number of future time steps the model produces`.

Update the capacity planning formula (line 2852) — the current formula uses `120 timesteps` as the `forecast_values` row count per station per cycle. This was written assuming hourly models matching the NWP resolution. With v0 daily models declaring `forecast_horizon_steps = 5`, the correct formula for daily models is `stations × 21 members × 4 cycles/day × 5 timesteps × ~60 bytes/row ≈ 4.3 MB/day` (vs the current ~103 MB/day for hourly). Add a note that hourly models (v0b+) would use the larger 120-timestep horizon.

Update the minimum disk / headroom figures (line 2853) — the current `1 TB fills in ~415–830 days` assumes 1000-station hourly models. Add a qualifier: `(hourly v0b+ models) or 10,000+ days (v0a daily models)`.

Update the Single VM viability paragraph (line 2855) — the current `~0.2–0.4 GB/day` storage growth assumes 170-station hourly models. Add qualifiers: `(120-step hourly models; ~9–17 MB/day for v0a daily models)` and update the 1000-station `~1–2 years` estimate to `(~1–2 years for hourly v0b+ models; >100 years for v0a daily models)`.

**Changes in `docs/v0-scope.md`** (line 50, §A1 "No table partitioning"):

This paragraph contains `~3.7B forecast_values rows/year` and `~0.6 GB/day raw (~1.2–2.4 GB/day with overhead)` at the 1000-station ceiling — both derived from 120-step hourly models. Add a parenthetical qualifier after each: `(assuming 120-step hourly models; v0a daily models: ~153M rows/year)` and `(120-step hourly models; v0a daily: ~25 MB/day raw, ~50–100 MB/day with overhead)`.

**Changes in `docs/v0-scope.md`** (line 307):

The storage benchmark `170 × 21 members × 120 timesteps = ~429K rows/cycle` assumes hourly forecast output. Update both figures to reflect v0a daily models:
- `170 × 21 × 5 = ~17,850 rows/cycle` (daily, vs ~429K hourly)
- `1000 × 21 × 5 = ~105K rows/cycle` (daily at 1000-station ceiling, vs ~2.52M hourly)

Add a note that the 120-timestep figures apply to future hourly models (v0b+).

**Changes in `docs/v0-scope.md`** (line 345, performance table):

The "Store results" row states `2.52M rows/cycle` — derived from 120-step hourly models. Add a qualifier: `(120-step hourly; v0a daily: ~105K rows/cycle)`.

**Changes in `docs/v0-scope.md`** (line 546):

The "not risks" table states `~3.7B forecast_values rows/year` at the 1000-station ceiling, derived from 120-step hourly models. With v0a daily models (5 steps), the actual figure is ~153M rows/year (24× lower). The 500M cumulative-row partitioning threshold is not approached until v0b hourly models are introduced. Add a parenthetical qualifier after `~3.7B forecast_values rows/year`: `(assuming 120-step hourly models; v0a daily models produce ~153M rows/year — the 500M threshold is not approached until v0b hourly models are introduced)`.

**Files**: `src/sapphire_flow/types/model.py`, `docs/spec/types-and-protocols.md`, `docs/architecture-context.md`, `docs/v0-scope.md`

**Verify**: `uv run ruff check src/sapphire_flow/types/model.py`

### Task 2: Update all model implementations

Each model declares its `forecast_horizon_steps` in its `data_requirements`:

**`src/sapphire_flow/models/linear_regression_daily.py`**:

Add a `_HORIZON` constant at line 30, alongside `_LOOKBACK`:

```python
_LOOKBACK = 7
_HORIZON = 5
```

Update the `data_requirements` construction (line 63):

```python
data_requirements: ModelDataRequirements = ModelDataRequirements(
    ...
    lookback_steps=_LOOKBACK,
    forecast_horizon_steps=_HORIZON,  # 5-day lead-time forecast (matches ICON-CH2-EPS)
    spatial_input_type=SpatialRepresentation.POINT,
)
```

Update the training fallback (lines 83–86) — when `future_dynamic` is empty, fall back to `_HORIZON` instead of `_LOOKBACK`:

```python
n_steps = future_ts["timestamp"].n_unique()
if n_steps == 0:
    # Training data has no future split — default to declared horizon
    n_steps = _HORIZON
```

`_LOOKBACK` and `_HORIZON` are now independent: `_LOOKBACK = 7` controls the past feature window, `_HORIZON = 5` controls the forecast output length.

**`src/sapphire_flow/models/persistence_fallback.py`** (line 44):

`PersistenceFallbackModel` is horizon-agnostic (it generates however many steps are requested). But it must declare a concrete value. Add `forecast_horizon_steps=5` directly to the `ModelDataRequirements` construction:

```python
self.data_requirements = ModelDataRequirements(
    ...
    lookback_steps=1,
    forecast_horizon_steps=5,
    spatial_input_type=SpatialRepresentation.POINT,
)
```

**Note**: In v0, `discover_models()` calls `cls()` with no arguments, so constructor parameters for horizon are unreachable. Hardcoding `5` is correct — all v0 models share the same horizon. If v1 needs per-deployment fallback horizons, the model discovery mechanism must be extended first (e.g., per-model constructor params in `config.toml`).

**`src/sapphire_flow/models/climatology_fallback.py`** (line 45):

Same pattern — hardcode `forecast_horizon_steps=5`:

```python
self.data_requirements = ModelDataRequirements(
    ...
    lookback_steps=1,
    forecast_horizon_steps=5,
    spatial_input_type=SpatialRepresentation.POINT,
)
```

**Files**: `src/sapphire_flow/models/linear_regression_daily.py`, `src/sapphire_flow/models/persistence_fallback.py`, `src/sapphire_flow/models/climatology_fallback.py`

**Verify**: `uv run ruff check src/sapphire_flow/models/`

### Task 3: Update fake models and test construction sites

All fake models in `tests/fakes/fake_models.py` must include the new field in their `ModelDataRequirements`. Use `forecast_horizon_steps=5` (matching the test fixtures that use 5 as the standard test horizon).

Construction sites to update:
- `FakeStationForecastModel` (line 25) — direct `ModelDataRequirements(...)` call
- `FakeGroupForecastModel` (line 154) — direct `ModelDataRequirements(...)` call

Also update the ad-hoc `ModelDataRequirements` construction in:
- `tests/unit/services/test_model_onboarding.py` (line 93) — direct `ModelDataRequirements(...)` call
- `tests/unit/services/test_operational_inputs.py` (line 114) — **uses `.__class__(...)` idiom**, not `ModelDataRequirements(...)` directly; still needs the field
- `tests/unit/services/test_operational_inputs.py` (line 365) — direct `ModelDataRequirements(...)` call inside `_NoPastDynamicModel`
- `tests/unit/flows/test_run_forecast_cycle.py` (line 194) — **uses `.__class__(...)` idiom**; still needs the field

**Note**: Two sites use `FakeStationForecastModel.data_requirements.__class__(...)` instead of `ModelDataRequirements(...)`. These resolve to the same class but will not appear in a `ModelDataRequirements(` grep. Both must add the new field.

**Files**: `tests/fakes/fake_models.py`, `tests/unit/services/test_model_onboarding.py`, `tests/unit/services/test_operational_inputs.py`, `tests/unit/flows/test_run_forecast_cycle.py`

**Verify**: `uv run ruff check tests/`

### Task 4: Thread model-declared horizon through hindcast callers

**Changes in `src/sapphire_flow/services/hindcast.py`**:

`run_station_hindcast()` (def at line 195, `forecast_horizon_steps` parameter at line 212) and `run_group_hindcast()` (def at line 296, `forecast_horizon_steps` parameter at line 313) both have `forecast_horizon_steps: int = 120`. Change the default to `None` and resolve from the model:

```python
def run_station_hindcast(
    ...
    forecast_horizon_steps: int | None = None,
    ...
) -> list[HindcastStepResult]:
    ...
    if forecast_horizon_steps is None:
        forecast_horizon_steps = model.data_requirements.forecast_horizon_steps
    log.debug(
        "hindcast.horizon_resolved",
        forecast_horizon_steps=forecast_horizon_steps,
        model_id=str(model_id),
        station_id=str(station_id),
    )
```

Same pattern for `run_group_hindcast()`.

**Changes in `src/sapphire_flow/services/onboarding.py`** (line 102):

Remove the explicit `forecast_horizon_steps=model.data_requirements.lookback_steps` kwarg from `_make_hindcast_fn()`. With the `None` default in `run_station_hindcast()`, the model's declared `forecast_horizon_steps` resolves automatically — no explicit argument needed at the call site.

```python
# Before (line 101-102):
hindcast_run_id=uuid4(),
forecast_horizon_steps=model.data_requirements.lookback_steps,

# After (line 101):
hindcast_run_id=uuid4(),
```

This also fixes the bug that Plan 032 Task 1 targets — if both plans are merged, Plan 032 Task 1 becomes a no-op (the line is already gone).

**Implicit callers (no change needed)**: `_run_station_hindcast_task()` (`run_hindcast.py:25`) and `_run_group_hindcast_task()` (`run_hindcast.py:64`) are Prefect task wrappers that call `run_station_hindcast()` / `run_group_hindcast()` without passing `forecast_horizon_steps`. After this change, they implicitly use the `None` default → model resolution. No code change required — the new default handles them correctly.

**Files**: `src/sapphire_flow/services/hindcast.py`, `src/sapphire_flow/services/onboarding.py`

**Verify**: `uv run ruff check src/sapphire_flow/services/hindcast.py src/sapphire_flow/services/onboarding.py`

### Task 5: Thread model-declared horizon through the forecast cycle

**Changes in `src/sapphire_flow/flows/run_forecast_cycle.py`** (line 390):

Replace the magic constant with the model's declared value. The model lookup (`models.get(sorted_assignments[0].model_id)`) already happens at line 401; this change moves the horizon derivation earlier. If the model is missing, log an ERROR, skip the station, and continue — following the established skip-and-continue pattern (`run_station_forecast.py:102`) but at ERROR severity (an upgrade from the WARNING used in `run_station_forecast.py:104` for the same condition) because a configured-but-missing model is a deployment-level defect that requires human attention.

A model present in `model_assignments` but absent from the runtime registry is not a configuration-file problem (which would be caught at startup) — it is a runtime state inconsistency (e.g., model entry point uninstalled, registry built before package installed). Per `conventions.md`, `ConfigurationError` is reserved for "fail fast at startup" and "flow invocation time." This condition is discovered mid-cycle, so the handler logs ERROR, increments `stations_failed`, and `continue`s — matching all other early-exit paths in this loop. The station receives no forecast for this cycle (identical to current behavior where `None` is passed to input assembly and the `except` catches), but the failure is now explicit and diagnosable.

```python
# Before (line 390):
forecast_horizon_steps: int = getattr(config, "forecast_horizon_steps", 120)

# After (replace line 390, BEFORE the try block):
first_model = models.get(sorted_assignments[0].model_id)
if first_model is None:
    log.error(
        "forecast_cycle.station_skipped_model_not_loaded",
        model_id=str(sorted_assignments[0].model_id),
    )
    errors.append(
        f"Configured model {sorted_assignments[0].model_id} missing for {sid}"
    )
    stations_failed += 1
    structlog.contextvars.unbind_contextvars("station_id")
    continue
forecast_horizon_steps: int = first_model.data_requirements.forecast_horizon_steps
```

Also update line 401 inside the `try` block — the existing `models.get()` call is now redundant (the guard above already resolved and validated the model):

```python
# Before (line 401):
model=models.get(sorted_assignments[0].model_id),  # type: ignore[arg-type]

# After:
model=first_model,
```

The `# type: ignore[arg-type]` can be dropped — `first_model` is narrowed to non-`None` by the guard.

**Why this pattern (not a raise)**:

1. `log.error` at ERROR level — `logging.md` line 285 lists "Model artifact not found or corrupted" as an ERROR example. The `forecast_cycle.station_skipped_model_not_loaded` event name pairs with the existing `forecast_cycle.station_skipped_no_nwp` (line 424) — both are skip-station events with the same outcome (station receives no forecast). **Severity differs deliberately**: `station_skipped_no_nwp` is `log.info` (missing NWP is a normal operational condition — data arrives late), while `station_skipped_model_not_loaded` is `log.error` (a configured-but-missing model is a deployment defect requiring human attention). This is also an upgrade from `run_station_forecast.py:104`, which logs the same condition at WARNING — the earlier detection point in the forecast cycle justifies ERROR because no fallback is attempted. `model_id` is passed as a kwarg, not bound via `bind_contextvars` — this is intentional: the guard fires before any per-model iteration begins, and no other early-exit in this loop binds anything beyond `station_id` (the only context variable in the station loop). Adding a context bind solely for this error event would be inconsistent with lines 383, 417, 424, 453, 579.
2. `errors.append(...)` — all 5 station-level failure paths in the loop append to `errors` (lines 418, 466, 521, 559, 580). Consistency is required.
3. `stations_failed += 1` — matches lines 419, 454, 507, 581. The counter feeds into `ForecastCycleResult` and is logged in the `forecast_cycle.complete` summary event.
4. `unbind_contextvars("station_id")` — **mandatory** before every `continue` in this loop. Every early exit (lines 384, 420, 425, 455, 508, 582) unbinds. Omitting it would leave a stale `station_id` bound, corrupting structured log context for subsequent iterations.
5. `continue` — the station receives **no forecast** for this cycle (same as the current behavior, where `models.get()` returns `None` → input assembly fails → `except` at line 416 catches → station skipped). The improvement is diagnostic: a clear ERROR event with the model ID instead of a cryptic `NoneType` exception logged as `input_assembly_failed`. The fallback chain (persistence/climatology tried in priority order) lives inside `run_station_forecast()` at line 433+, which is downstream of all `continue` paths and unreachable for skipped stations.

**Monitoring**: Flow 4 (pipeline watchdog) does not pattern-match on log events — it queries DB freshness and Prefect run state. The `stations_failed` counter surfaces in `ForecastCycleResult`, which Prefect captures. `log.error` is the primary signal for human incident response.

**Note**: `ForecastEnsemble.forecast_horizon_steps` is derived automatically in `from_members()` / `from_quantiles()` via `values["valid_time"].n_unique()` — no caller change needed downstream. The model-declared value flows into `StationModelInputs`, the model produces output sized to that horizon, and `ForecastEnsemble` captures it from the data.

**Files**: `src/sapphire_flow/flows/run_forecast_cycle.py`

No new imports required — `log`, `structlog`, `errors`, and `stations_failed` are already in scope at the change site.

**Verify**: `uv run ruff check src/sapphire_flow/flows/run_forecast_cycle.py`

### Task 6: Fix `smoke_test_model()` to use declared horizon

**Changes in `src/sapphire_flow/services/model_onboarding.py`** (lines 269–271, 294, 323):

Replace the `lookback_steps` proxy with the declared field:

```python
# Before (line 269):
smoke_horizon = max(req.lookback_steps, 10)

# After:
smoke_horizon = max(req.forecast_horizon_steps, 10)
```

And at lines 294 and 323:

```python
# Before:
forecast_horizon_steps=min(req.lookback_steps, len(data.future_dynamic)),

# After:
forecast_horizon_steps=min(req.forecast_horizon_steps, len(data.future_dynamic)),
```

**Note**: The `n_future = smoke_horizon` sizing (line 271) ensures synthetic training data produces enough rows for a training artifact with `n_steps >= req.forecast_horizon_steps`. The `max(..., 10)` guard ensures a minimum of 10 rows for models with very small horizons. For `LinearRegressionDaily` with `forecast_horizon_steps=5`, the guard resolves to `max(5, 10) = 10` — producing 10 synthetic future rows, more than the 5 needed, which is safe.

**Files**: `src/sapphire_flow/services/model_onboarding.py`

**Verify**: `uv run ruff check src/sapphire_flow/services/model_onboarding.py`

### Task 7: Add training-time validation to `LinearRegressionDaily`

After `train()` produces the artifact, validate that `art.n_steps` matches the declared `forecast_horizon_steps`. This catches cases where training data shape doesn't support the model's declared horizon.

**Changes in `src/sapphire_flow/models/linear_regression_daily.py`**, at the end of `train()` (before the return at line 155):

```python
declared = self.data_requirements.forecast_horizon_steps
if art.n_steps < declared:
    raise ValueError(
        f"Trained artifact n_steps={art.n_steps} < declared "
        f"forecast_horizon_steps={declared}. Training data may have "
        f"insufficient future_dynamic rows."
    )
```

**Note**: This uses `<` not `!=`, allowing the artifact to support MORE steps than declared (the model can be trained on richer data and still work at the declared horizon). The `predict()` guard (`horizon > art.n_steps` at line 174) remains as a separate runtime check.

In the normal production path: `assemble_station_training_data()` produces empty `future_dynamic` → training falls back to `n_steps = _HORIZON = 5` → `art.n_steps = 5` → validation `5 < 5` is False → passes. The guard fires only if a future change produces `future_dynamic` with fewer than 5 unique timestamps.

**Files**: `src/sapphire_flow/models/linear_regression_daily.py`

**Verify**: `uv run ruff check src/sapphire_flow/models/linear_regression_daily.py`

### Task 8: Update tests

1. **`tests/unit/models/test_linear_regression_daily.py`**: Verify that `data_requirements.forecast_horizon_steps == 5`. Three test changes required:

   **a) Restructure `test_horizon_guard` (line 145)**: The existing test trains with `_make_training_data(horizon_steps=3)`. After Task 7, this raises `ValueError` at **train time** (`3 < 5`), before `predict()` is ever called — breaking the test. Fix: change to `_make_training_data(horizon_steps=5)` (valid artifact with `n_steps=5`), then predict with `horizon=10` (exceeds `art.n_steps=5`). This isolates the **predict-time** guard test.

   **b) Fix `test_ensemble_members_count` (line 155)**: Also trains with `_make_training_data(horizon_steps=3)` — same breakage. Change to `horizon_steps=5`. Keep `_make_predict_inputs(horizon=3)` — predicting 3 steps when artifact supports 5 is valid (`3 ≤ 5`).

   **c) Add `test_train_rejects_insufficient_future_dynamic`** (new test): Train with `_make_training_data(horizon_steps=3)` so `future_dynamic` has only 3 unique timestamps, which is less than the declared `forecast_horizon_steps=5`. Assert `train()` raises `ValueError` with match `"forecast_horizon_steps"`. **Important**: the test must pass **non-empty** `future_dynamic` — empty `future_dynamic` triggers the fallback to `n_steps = _HORIZON = 5`, which passes the guard trivially (`5 < 5` is False).

2. **`tests/unit/models/test_persistence_fallback.py`**: Verify `data_requirements.forecast_horizon_steps == 5`.

3. **`tests/unit/models/test_climatology_fallback.py`**: Same as Persistence — verify `forecast_horizon_steps == 5`.

4. **`tests/unit/services/test_hindcast.py`**: Two new tests for the `None`-default resolution:

   **a) `test_horizon_resolved_from_model_when_omitted`**: Call `run_station_hindcast()` without passing `forecast_horizon_steps` (relies on `None` default). Assert that the resulting `HindcastStepResult` contains a forecast ensemble with `forecast_horizon_steps` matching `model.data_requirements.forecast_horizon_steps` (= 5). This verifies the resolution is threaded all the way through to the stored result, not just that no exception is raised.

   **b) `test_explicit_horizon_overrides_model_declaration`**: Call `run_station_hindcast(forecast_horizon_steps=3)` with a model declaring `forecast_horizon_steps=5`. Assert the result uses `3`, not `5`. **The explicit value must differ from the model's declared value** to make the test non-vacuous.

5. **`tests/unit/flows/test_run_forecast_cycle.py`**: Add a test for the Task 5 guard:

   **`test_station_skipped_when_model_not_loaded`**: Configure a station with a model ID that is absent from the `models` dict. Run the forecast cycle. Assert:
   - The station is skipped (no forecast produced for that station).
   - `stations_failed` is incremented (visible in `ForecastCycleResult`).
   - No unhandled exception is raised (the cycle continues to other stations).

6. **`tests/unit/services/test_model_onboarding.py`**: Add a test for the Task 6 fix:

   **`test_smoke_test_uses_forecast_horizon_not_lookback`**: Create a model with `lookback_steps=720` and `forecast_horizon_steps=5`. Run `smoke_test_model()`. Assert it succeeds (the smoke horizon resolves to `max(5, 10) = 10`, not `max(720, 10) = 720`). This verifies the sizing fix is active — with the old `lookback_steps`-based code, `smoke_horizon=720` would create unnecessarily large synthetic data; with the fix, `smoke_horizon=10`.

7. Existing tests: update any `ModelDataRequirements(...)` constructions to include the new field.

**Files**: `tests/unit/models/test_linear_regression_daily.py`, `tests/unit/models/test_persistence_fallback.py`, `tests/unit/models/test_climatology_fallback.py`, `tests/unit/services/test_hindcast.py`, `tests/unit/flows/test_run_forecast_cycle.py`, `tests/unit/services/test_model_onboarding.py`

**Verify**: `uv run pytest --tb=short -q`

### Task 9: Full verification

```bash
uv run pytest --tb=short -q
uv run ruff check src/ tests/
uv run pyright --strict src/
uv run bump-my-version bump patch
```

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "tasks": ["1"],
      "note": "Type change — blocks all subsequent tasks"
    },
    {
      "id": "phase-1",
      "tasks": ["2", "3"],
      "parallel": true,
      "depends_on": ["phase-0"],
      "note": "All ModelDataRequirements construction sites must add the field"
    },
    {
      "id": "phase-2",
      "tasks": ["4", "5", "6", "7"],
      "parallel": true,
      "depends_on": ["phase-1"],
      "note": "Thread the declared value through callers"
    },
    {
      "id": "phase-3",
      "tasks": ["8", "9"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Risk Assessment

### 1. Fallback model horizon must match primary model

`PersistenceFallbackModel` and `ClimatologyFallbackModel` hardcode `forecast_horizon_steps=5`. In v0, all models share this horizon so no mismatch is possible. In v1, if the primary model declares a different horizon, the fallback models must be updated to match. This requires extending the model discovery mechanism (`discover_models()` currently calls `cls()` with no arguments) to support per-deployment constructor parameters — e.g., via `config.toml`. The combination framework (Plan 025/026) handles multi-model output alignment, but the declared horizon should match for consistency.

### 2. Training validation and empty `future_dynamic`

For `LinearRegressionDaily`, `assemble_station_training_data()` produces empty `future_dynamic` (correct by design — training uses sliding windows over historical data). The model's `train()` falls back to `n_steps = _HORIZON = 5`, which equals `forecast_horizon_steps = 5`. The Task 7 validation (`art.n_steps < declared`) passes trivially. If a future change to training data assembly populates `future_dynamic` with a different number of rows, the validation catches the mismatch.

### 3. Forecast cycle multi-model horizon

Task 5 reads `forecast_horizon_steps` from the first priority-sorted model and logs ERROR + skips if the model is missing. In v0, all models declare the same horizon (5 daily). When v0b introduces the FI-wrapped ML model (`GroupForecastModel`), or v1 introduces conceptual models, they may declare different horizons. The forecast cycle will then need per-model horizon handling — the current single-horizon approach must be replaced with per-`(model, station)` horizon threading in the fan-out loop. This is out of scope for this plan but is a **v0b prerequisite** if the ML model uses a different horizon.

### 3b. `forecast_horizon_steps` ambiguity with multi-timestep models

`forecast_horizon_steps` is an integer step count whose temporal meaning depends on `supported_time_steps`. For a model with `supported_time_steps = {timedelta(hours=1), timedelta(days=1)}` and `forecast_horizon_steps = 5`, it is ambiguous whether that means 5 hours or 5 days. In v0 this is a non-issue: all models are daily-only. When v1 introduces multi-timestep models, `forecast_horizon_steps` must be interpreted relative to the active `time_step` from `model_assignments`. Consider whether the field should carry units or remain a step count (resolved per-assignment by the orchestrator). **No action in this plan.**

### 4. Backward compatibility

Adding a required field to `ModelDataRequirements` is a breaking change for any code that constructs it without the new field. Task 2/3 updates all 9 construction sites (3 model implementations, 2 fake models, 4 test ad-hoc constructions — including 2 that use the `.__class__(...)` idiom). No external consumers exist (the type is internal to SAPPHIRE Flow). `ModelDataRequirements` is never persisted to the DB (`ModelRegistryEntry` is runtime-only, `PgModelStore` stores only `ModelRecord` metadata) — no DB migration is needed.

### 5. ForecastInterface alignment [v1 — deferred]

The external `ForecastInterface` package already has horizon concepts:
- **Output side**: `VariableMetadata.forecast_horizon: int` — per-variable, returned after each prediction. The SAPPHIRE adapter (`adapters/forecast_interface/_adapter.py`) currently ignores this field and derives horizon from DataFrame row count.
- **Input side**: `FutureKnownVariable.future_steps: int` — per-variable/product, declares how many future steps the model needs.

Neither is a model-level declaration equivalent to `ModelDataRequirements.forecast_horizon_steps`. For v0, no FI changes are needed — the SAPPHIRE wrapper around FI models can set `forecast_horizon_steps` on its own `ModelDataRequirements`. For v1, consider adding `forecast_horizon_steps: int` to `InputRequirement` (FI's input specification type) and using `VariableMetadata.forecast_horizon` in the adapter for post-prediction validation. These are tracked in FI's `docs/open_design_questions.md` (question 3). **No action in this plan.**

### 6. Combination framework horizon alignment [v0b/v1 — deferred]

The combination layer (`forecast_combination.py`) concatenates `ForecastEnsemble` DataFrames without checking that all models produced the same set of `valid_time` values. In v0 this is safe because all models declare the same horizon (5 daily). When v0b or v1 introduces models with different horizons, the pooled/BMA result will have misaligned timestep coverage — some members covering more steps than others. Adding a horizon consistency check to the combination layer is deferred out of scope.

## Verification

```bash
uv run pytest --tb=short -q
uv run ruff check src/ tests/
uv run pyright --strict src/
uv run bump-my-version bump patch
```
