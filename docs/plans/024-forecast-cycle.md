# Plan 024 — Flow 1: Forecast Cycle

**Status**: READY  
**Phase**: 8 (Forecast cycle)

## Context

All building blocks for the operational forecast cycle exist (adapters, stores, services, model protocols, QC, alert checking) but no flow wires them together. The file `src/sapphire_flow/flows/run_forecast_cycle.py` does not exist. This plan creates it along with the two missing service functions it needs.

## What Flow 1 Does

Every ~6 hours: fetch NWP weather → prepare model inputs → run models → QC → store → check alerts.

**Three-phase sequencing** (from `docs/handover/data-flows.md`):
- **Phase A** (NWP): 1.1 fetch NWP → (1.2 archive grid → 1.3 extract → 1.4 archive extractions — v0b only) → 1.5 post-process (pass-through)
- **Step 1.6** (observations): runs in parallel with Phase A
- **Phase B** (per station): 1.7 prepare inputs → 1.8 predict → 1.9 post-process (pass-through) → 1.10 QC → 1.11 store
- **Phase C** (alerts): gated by `enable_forecast_alerts`. Steps 1.12–1.13 (threshold check). Step 1.14 (notify) is no-op in v0 per §A8.

**v0a vs v0b**: Adapter returns `dict[StationId, WeatherForecastResult]` (v0a, pre-extracted) or `GriddedForecast` (v0b, gridded). Flow branches on isinstance check. v0a skips steps 1.2–1.4.

## Existing Building Blocks

| Component | Location | Used For |
|-----------|----------|----------|
| `WeatherForecastSource` Protocol | `protocols/adapters.py` | Step 1.1 |
| `MeteoSwissNwpAdapter` | `adapters/meteoswiss_nwp.py` | Production NWP fetch |
| `ExactExtractGridExtractor` | `preprocessing/exact_extract_grid_extractor.py` | Step 1.3 (v0b) |
| `ZarrNwpGridStore` | `store/zarr_nwp_grid_store.py` | Step 1.2 (v0b) |
| `basin_avg_to_records()` | `preprocessing/converters.py` | NWP → WeatherForecastRecord |
| `PgWeatherForecastStore` | `store/weather_forecast_store.py` | Store/fetch NWP per station |
| `PgObservationStore` | `store/observation_store.py` | Step 1.6 (river observations) |
| `StoreBackedReanalysisSource` | `adapters/store_backed_reanalysis.py` | past_dynamic via `WeatherReanalysisSource` Protocol |
| `PgForecastStore` | `store/forecast_store.py` | Step 1.11 |
| `PgModelArtifactStore` | `store/model_artifact_store.py` | Load model artifacts |
| `PgModelStateStore` | `store/model_state_store.py` | Warm-up state read/write |
| `PgModelStore` | `store/model_store.py` | Model metadata |
| `discover_models()` | `services/model_registry.py` | Load model classes via entry points (standard pattern, used by all flows) |
| `ForecastOutputQualityChecker` | `services/forecast_qc.py` | Step 1.10 (class with `.check()`) |
| `check_station_alerts()` | `services/alert_checker.py` | Phase C |
| `assess_input_quality()` | `services/input_quality.py` | Input quality flags |
| `StationForecastModel.predict()` | `protocols/forecast_model.py` | Step 1.8 |
| `GroupForecastModel.predict_batch()` | `protocols/forecast_model.py` | Step 1.8 (group, deferred) |
| `make_pg_stores()` | `flows/_db.py` | Store factory (needs additions) |
| All Fake stores/models | `tests/fakes/` | Unit testing |

**Model loading**: `discover_models()` is the standard approach — used by `train_models_flow` and `onboard_model_flow`. Call once at flow start (or accept pre-injected `models` dict for testability). `ModelRecord` does not carry entry point strings, so entry-point scanning is the only resolution path.

## What's Missing

1. **`make_pg_stores()`** lacks `weather_forecast_store`, `forecast_store`, `model_state_store`
2. **No operational input assembly** — `training_data.py` uses `WeatherReanalysisSource` for both past and future forcing (teacher forcing in hindcast). Operational path needs `WeatherForecastStore` for `future_dynamic` (real NWP) while keeping `WeatherReanalysisSource` for `past_dynamic` (consistency with training)
3. **No station forecast runner** — service function: load artifact → predict → QC → construct `OperationalForecast` with multi-model fallback
4. **No `point_forecast_to_records()`** — converter for v0a path (analogous to `basin_avg_to_records`)
5. **No Prefect flow** that orchestrates all phases

---

## Tasks

### Task 1: Add missing stores to `make_pg_stores` + point forecast converter

**Scope**: Add `weather_forecast_store`, `forecast_store`, `model_state_store` to `make_pg_stores()`. Add `point_forecast_to_records()` converter to `preprocessing/converters.py` (mirrors `basin_avg_to_records` but for `PointForecast`).  
**Out of scope**: No new store implementations, no schema changes.  
**Note**: `PointForecast.values` DataFrame schema is undocumented in the type definition. Implementer must check `MeteoSwissNwpAdapter` output (or `BasinAverageForecast` tests) to confirm columns are `[valid_time, parameter, member_id, value]` before writing the converter.  
**Files**: `flows/_db.py`, `preprocessing/converters.py`, `tests/unit/preprocessing/test_converters.py`  
**Verification**: `uv run pytest tests/unit/preprocessing/test_converters.py -x -q`

### Task 2: Service — assemble operational model inputs

**Scope**: Create `services/operational_inputs.py` with `assemble_station_operational_inputs()`. Assembles `StationModelInputs` for the operational forecast path:
- `past_targets`: from `obs_store` (river observations — discharge/water_level for the model's lookback window)
- `past_dynamic`: from injectable `forcing_source: WeatherReanalysisSource` — **same adapter used in training and hindcast** (per §I2). In v0, the concrete impl is `StoreBackedReanalysisSource` (reads from `historical_forcing` table). Calls `forcing_source.fetch_reanalysis(start=issue_time - lookback, end=issue_time, ...)`. This ensures training/inference consistency.
- `future_dynamic`: from `weather_forecast_store` (NWP forecasts for the current cycle, pivoted into wide DataFrame matching model's `future_dynamic_features`). This is the key difference vs hindcast — hindcast uses reanalysis for both past and future (teacher forcing), while operational uses real NWP for future.
- `static`: from `basin_store` (same pattern as `training_data.py`)
- Warm-up state: from `model_state_store.fetch_latest_state()`. Compute `WarmUpSource` (FRESH/SNAPSHOT/COLD_START) and `warm_up_state_age_hours`.
- Compute `observation_staleness_hours` (hours since latest observation).
- Returns `tuple[StationModelInputs, OperationalInputMetadata] | None` (None if future_dynamic unavailable).
- Define `OperationalInputMetadata` frozen dataclass in this module: `warm_up_source`, `warm_up_state_age_hours`, `observation_staleness_hours`, `prior_state: bytes | None`, `nwp_age_hours: float`.
- Skip `past_dynamic` fetch entirely if model's `data_requirements.past_dynamic_features` is empty.

**Out of scope**: No Prefect decorators. No group input stacking (deferred with group models).  
**Files**: `services/operational_inputs.py`, `tests/unit/services/test_operational_inputs.py`  
**Key pattern**: Follow `_assemble_hindcast_inputs()` in `hindcast.py` for the past_dynamic fetch pattern via `WeatherReanalysisSource`. Follow `training_data.py` for pivot logic. `future_dynamic` comes from `WeatherForecastStore` (WeatherForecastRecord rows → wide DataFrame).  
**Verification**: `uv run pytest tests/unit/services/test_operational_inputs.py -x -q`

### Task 3: Service — run single station forecast with multi-model fallback

**Scope**: Create `services/run_station_forecast.py` with `run_station_forecast()`. Iterates model assignments by priority. For each model: load artifact → predict → QC. On failure (exception or QC_FAILED), try next model. Constructs `OperationalForecast` for each parameter ensemble, populating:
- `nwp_cycle_reference_time` and `nwp_cycle_source` (passed in from Phase A)
- `warm_up_source`, `warm_up_state_age_hours` (from Task 2's metadata)
- `observation_staleness_hours` (from Task 2's metadata)
- `input_quality` and `input_quality_flags` (from `assess_input_quality()`)
- `qc_status` and `qc_flags` (from `ForecastOutputQualityChecker.check()`)

Calls `assess_input_quality()` — note this function requires caller-supplied threshold params (`obs_partial_hours`, `warmup_partial_hours`, `warmup_degraded_hours`) in addition to `InputQualityConfig`. These come from `DeploymentConfig.input_quality`.

Returns result dataclass with: `station_id`, `model_id`, `artifact_id`, `forecasts: list[OperationalForecast]`, `new_state: bytes | None`, `ensembles: dict[str, ForecastEnsemble]` (for alert checking). Include unit tests using fakes.  
**Out of scope**: No storing (caller stores), no state persistence (caller does), no alert checking (caller does), no Prefect decorators.  
**Files**: `services/run_station_forecast.py`, `tests/unit/services/test_run_station_forecast.py`  
**Verification**: `uv run pytest tests/unit/services/test_run_station_forecast.py -x -q`

### Task 4: Prefect flow — `run_forecast_cycle_flow`

**Scope**: Create `flows/run_forecast_cycle.py`. Three-phase Prefect flow.

**Prefect configuration** (per `orchestration.md`):
- `@flow(name="forecast-cycle", log_prints=False)` with concurrency limit of 1
- Tasks use `persist_result=False` when passing large objects (NWP DataFrames, ensembles) — per §D4
- Tasks use `log_prints=False` — per logging standard
- Step 1.11 bulk writes use `concurrency("db_bulk_write", occupy=1)` guard — per orchestration.md

**Flow start** (batch pre-fetch, per architecture):
- Load `DeploymentConfig` and `ForecastQcRuleSet` (via `load_forecast_qc_rules()` — requires `SAPPHIRE_CONFIG` env var or explicit path)
- Batch pre-fetch `ClimBaseline` records for all operational stations
- Batch pre-fetch `ForecastQcOverride` records for all operational stations
- Batch pre-fetch `ModelAssignment` and `StationThreshold` per station
- Load model classes: call `discover_models()` once at flow start (or accept pre-injected `models` dict for testability — same pattern as `train_models_flow`)
- Instantiate `StoreBackedReanalysisSource` as the `forcing_source: WeatherReanalysisSource` for past_dynamic (per §I2)
- Inject `clock: Callable[[], UtcDatetime]` — never use `datetime.now()` directly

**Phase A** (`@task(name="fetch-nwp-forcing", persist_result=False)`):
- Call `adapter.fetch_forecasts()`. Branch on isinstance:
  - `dict[StationId, WeatherForecastResult]` (v0a): convert via `point_forecast_to_records()`, store in `weather_forecast_store`
  - `GriddedForecast` (v0b): raise `NotImplementedError("v0b grid path not yet wired")`
- On fetch failure: log error + skip cycle (no fallback — NWP lateness fallback deferred to v0b per scope §deferred table). Return `None` to signal abort.

**Step 1.6** (`@task(name="fetch-observations")`):
- Runs in parallel with Phase A
- Fetch latest QC-passed observations per station from `obs_store`
- Fetch latest timestamp per station for staleness computation

**Phase B** (per station, sequential loop in v0):
- Bind `station_id` via `structlog.contextvars.bind_contextvars(station_id=str(station_id))` — per logging standard
- Call `assemble_station_operational_inputs()` (Task 2)
- Call `run_station_forecast()` (Task 3) — receives `nwp_cycle_reference_time` and `nwp_cycle_source` from Phase A
- On success: store each `OperationalForecast` via `forecast_store.store_forecast()`, persist warm-up state via `model_state_store.store_state()` (architecture mandate)
- Step 1.11 bulk writes guarded by `concurrency("db_bulk_write", occupy=1)`
- Per-step timing: `time.perf_counter()` + structlog `duration_ms` on all `*.completed` events — per logging standard §D6

**Phase C** (`@task(name="check-alerts")`):
- Gated by `config.enable_forecast_alerts`
- Calls `check_station_alerts()` with accumulated ensembles, thresholds, danger levels, priorities
- Step 1.14 (notify): no-op in v0 per §A8

**Result**: `ForecastCycleResult` dataclass with summary counts.

**Logging** (per `logging.md` canonical events):
- `forecast.input_quality_assessed` at INFO (partial) or WARNING (degraded), per logging standard
- `station_id` and `model_id` bound via `bind_contextvars` in per-station loop

**Out of scope**: v0b grid extraction path, `task.map()` parallelisation (conscious deviation from §D3 — justified: 170 × ~200ms ≈ 34s station loop + ~20-30s fixed costs ≈ 54-64s total, at the edge of the 60s budget; accepted risk, rework to task.map if benchmarks show overrun or station count exceeds ~300), step 1.9 forecast post-processing (pass-through).  
**Files**: `flows/run_forecast_cycle.py`  
**Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -x -q`

### Task 5: Flow-level unit test

**Scope**: Create `tests/unit/flows/test_run_forecast_cycle.py`. End-to-end test of the flow using all fakes (`FakeWeatherForecastSource`, `FakeStationForecastModel`, `FakeWeatherForecastStore`, `FakeForecastStore`, `FakeObservationStore`, `FakeModelArtifactStore`, `FakeModelStateStore`, etc.). Use a fixed clock for determinism.

Test cases:
- Happy path: 2 stations, 1 model each — forecasts stored, state persisted
- Multi-model fallback: first model fails QC, second model succeeds
- Alert checking enabled: verify `check_station_alerts()` called with correct ensembles
- NWP fetch failure: cycle skips gracefully, no forecasts stored
- Empty station list: returns zero-count result

**Out of scope**: Integration tests, performance benchmarks.  
**Files**: `tests/unit/flows/test_run_forecast_cycle.py`  
**Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -x -q`

### Task 6: Documentation updates

**Scope**: Update `docs/handover/data-flows.md` Flow 1 section to note implementation status. Light updates to `docs/v0-scope.md` Phase 8 row if needed.  
**Out of scope**: No architecture doc changes, no API docs.  
**Verification**: `uv run pytest tests/ -x -q`

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1", "2", "3"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["4"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["5"],
      "parallel": false,
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "tasks": ["6"],
      "parallel": false,
      "depends_on": ["phase-3"]
    }
  ]
}
```

## Key Design Decisions

1. **Sequential station loop in v0** (conscious deviation from §D3): No `task.map()` fan-out. Each station prediction is ~100-300ms; 170 stations ≈ 34s. With fixed costs (~20-30s for NWP + obs + store), total ≈ 54-64s — at the edge of the 60s budget. Accepted risk: if benchmarks show overrun, rework to `task.map()`. Simpler to debug and test in the initial implementation. Revisit when station count exceeds ~300.

2. **v0b grid path stubbed**: The isinstance branch for `GriddedForecast` will exist but raise `NotImplementedError`. All components exist — wiring is a follow-up task.

3. **Service functions are pure, flow is thin**: `assemble_station_operational_inputs()` and `run_station_forecast()` contain all logic. The Prefect flow is a thin orchestration wrapper.

4. **Multi-model fallback at service level**: The `run_station_forecast()` service iterates model assignments by priority. Testable without Prefect.

5. **Group models deferred**: v0 only has `LinearRegressionDaily` (a `StationForecastModel`). Group model support (`predict_batch`) added when the first `GroupForecastModel` is onboarded. Note: for group models, QC-failed individual station results within a batch are stored with `QC_FAILED` status (no per-station fallback within a batch) — this asymmetry must be addressed in the group model wiring task. `stack_model_inputs()` in `types/model.py` uses the legacy `ModelInputs` type (with `xr.Dataset`) and is incompatible with `StationModelInputs` — a new stacking utility will be needed for group model support.

6. **NWP lateness fallback deferred**: Per v0-scope.md deferred table, the three-stage fallback strategy (wait → fallback cycle → skip) is v0b scope. v0a simply logs and skips the cycle on NWP fetch failure.

7. **`past_dynamic` via `WeatherReanalysisSource`, not `WeatherForecastStore`**: Per §I2, the forcing source must be injectable. `past_dynamic` comes from the same `WeatherReanalysisSource` adapter used in training and hindcast (v0 concrete: `StoreBackedReanalysisSource` → `historical_forcing` table with SMN data per §A12). `future_dynamic` comes from `WeatherForecastStore` (real NWP). This is the key operational vs hindcast split: hindcast uses reanalysis for both past and future (teacher forcing); operational uses reanalysis for past + real NWP for future.

8. **Warm-up state persistence is the flow's responsibility**: After successful `predict()`, the flow layer saves the new state snapshot via `ModelStateStore.store_state()`. Without this, no snapshot would exist to fall back to in subsequent cycles.

## Not In Scope (deferred items)

- Season-aware threshold resolution (Plan 023 follow-up)
- NWP lateness fallback (v0b)
- Forecast post-processing / bias correction (v0b+ when sufficient archive exists)
- Notification dispatch (v1)
- Group model fan-out and `predict_batch` (when first group model is onboarded)
- `task.map()` parallelisation (when station count exceeds ~300 or benchmarks warrant)
