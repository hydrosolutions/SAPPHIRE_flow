# Plan 025 — Multi-Model Combination Forecasting

**Status**: READY
**Phase**: Cross-cutting (extends Flows 1, 3, 7, 8/10; touches types, services, stores)

## Problem

The architecture runs all assigned models per station every cycle (Flow 1 Phase B) and has four multi-model strategies for **alerting** (`primary`, `pooled`, `bma`, `consensus` — see `AlertModelStrategy` enum). But three gaps remain:

1. **The published forecast is single-model.** Flow 3 step 3.3 says "forecaster picks the preferred model per station." There is no mechanism to publish a multi-model combination (skill-weighted average, pooled ensemble, consensus forecast) as the forecast product itself. The multi-model strategies apply only to threshold exceedance checks (steps 1.12–1.13), not to the hydrograph that forecasters review, publish, and external consumers receive via the API.

2. **No skill metrics for multi-model combinations.** Flows 8/10 compute verification metrics per individual model. There is no step that constructs the combined forecast from hindcast results and computes CRPS, BSS, NSE, etc. on it. Without this, we cannot objectively demonstrate that the multi-model combination outperforms the best single model — which is the primary justification for running multiple models.

3. **No guaranteed last-resort fallback model.** The current "multi-model fallback" (data-flows.md Flow 1) is priority-ordered error recovery: if model A fails, try model B. But if all models fail (e.g., NWP completely unavailable, observation gaps exceed all models' tolerances), the station produces no forecast. Climatology and persistence baselines exist (station onboarding step 5.8) but only as skill reference benchmarks — they are not registered as operational forecast models.

## Design Decisions

### D1. Climatology and persistence as registered fallback models

**Decision**: Implement `ClimatologyFallbackModel` and `PersistenceFallbackModel` as `StationForecastModel` implementations. These are real models in the model registry — they train, produce artifacts, and predict like any other model.

- **ClimatologyFallbackModel**: Trains from historical observations. Artifact contains per-day-of-year quantile distributions. Predicts by looking up the climatological distribution for each forecast step's day-of-year. Requires only historical observations (no NWP, no weather forcing). Always available if the station has been onboarded (step 5.8 guarantees historical baselines exist).
- **PersistenceFallbackModel**: Training produces a minimal marker artifact (no learned parameters). Predicts by repeating the most recent observation across the forecast horizon with uncertainty that widens with lead time. Requires only the latest observation — the absolute minimum data requirement.

Both models are onboarded via Flow 13 like any other model. They receive the lowest priority in model assignments (convention: priority 90 for climatology, 99 for persistence — well below the 0/1/2 range used for real models). The multi-model fallback chain in `run_station_forecast()` naturally reaches them after all real models have failed. The fallback priority tier (90–99) must be documented in `docs/conventions.md` alongside the existing 0/1/2 convention.

**Why not just extend `ClimBaseline`?** Baselines are statistical summaries used as skill reference denominators (CRPSss against climatology, BSS against persistence). Fallback models are operational forecast producers — they go through the same predict/QC/store pipeline. Keeping them separate avoids coupling the baseline computation to the model Protocol and makes their skill independently verifiable.

### D2. Forecast combination strategy — rename `AlertModelStrategy` → `ModelCombinationStrategy`

**Decision**: Rename the existing `AlertModelStrategy` enum to `ModelCombinationStrategy` (values: `PRIMARY`, `POOLED`, `BMA`, `CONSENSUS`). Both `alert_model_strategy` and the new `forecast_combination_strategy` config field on `DeploymentConfig` use this type. This controls how models' ensembles are combined — for alerting and for the **published forecast product** respectively.

**Why rename now?** The project's type-driven development principles (CLAUDE.md) require types to be self-documenting. An enum named `AlertModelStrategy` used for forecast combination violates this — it communicates "alerting" when the concept is "multi-model combination." The rename is a find-and-replace across the codebase; deferring it only increases the surface area.

The combination strategies applied to forecasts:

| Strategy | Forecast product | When to use |
|----------|-----------------|-------------|
| `primary` | Single best model's ensemble (highest priority that succeeded) | Default. v0. Single model or when forecaster always picks manually. |
| `pooled` | Grand ensemble: all models' members merged into one ensemble | Simple multi-model. Conservative — preserves full spread. |
| `bma` | Weighted ensemble: each model's members weighted by skill score (Bayesian Model Averaging) | Mature deployments with established skill scores. Recommended default for v1. |
| `consensus` | Per-lead-time median of each model's ensemble median, with uncertainty from inter-model spread | When models have incompatible ensemble sizes or representations. |

**Separation from alerting**: `alert_model_strategy` and `forecast_combination_strategy` are independent config fields. A deployment might use `bma` for alerts (exceedance probability weighted by skill) but `primary` for the published forecast (forecaster picks). Or `pooled` for both. Independence avoids forcing a single strategy across different concerns.

**Where combination happens**:
- **Flow 1 (automatic)**: After Phase B, if `forecast_combination_strategy != primary`, a new step **1.8b** constructs the combined forecast from all successful models' ensembles and stores it alongside individual model forecasts. Tagged with `combination_strategy` and `source_model_ids`.
- **Flow 3 (manual)**: The forecaster sees all individual model forecasts AND the combined product. At step 3.3, the forecaster can select an individual model OR the combined product for publication.

### D3. Combined forecast representation in the database

**Decision**: Combined forecasts are stored in the same `forecasts` + `forecast_values` tables as individual model forecasts, with two additional columns:

- `combination_strategy: TEXT NULL` — `NULL` for individual model forecasts; `'pooled'`/`'bma'`/`'consensus'` for combined products.
- `source_model_ids: JSONB NULL` — `NULL` for individual; `["model_a", "model_b"]` for combined.

The `model_id` column for combined forecasts uses a well-known sentinel: `ModelId("_pooled")`, `ModelId("_bma")`, or `ModelId("_consensus")`. These sentinel IDs are registered in the `models` table as virtual entries (no entry point, no model class — just metadata for referential integrity and API discoverability).

**Why not a separate table?** The combined forecast is a forecast — same schema, same API endpoints, same review/publish lifecycle. A separate table would duplicate the entire forecast query surface. Using the same table with a discriminator column (`combination_strategy`) is the standard pattern for type hierarchies in a single table.

**API note**: Combined forecasts will appear with sentinel `model_id` values (`_pooled`, `_bma`, `_consensus`). Phase 9 (FastAPI API) must be aware of this — API responses should distinguish combined from individual forecasts (e.g., via `combination_strategy` field in the response schema). This is not in scope for Plan 025/026 but must be addressed when Phase 9 resumes.

### D4. Skill metrics for combined forecasts

**Decision**: Extend Flows 8/10 with a new step **S.4b** that runs after per-model skill computation (S.4):

1. For each station and each configured `forecast_combination_strategy`:
   - Retrieve hindcast ensembles from all models for the same time steps
   - Construct the combined forecast using the configured strategy (pooled, BMA, consensus)
   - For BMA: use the per-model skill weights from the most recent skill computation
   - Compute the full verification metric suite on the combined forecast
2. Store results in `skill_scores` with `model_id` = sentinel (`_pooled`, `_bma`, `_consensus`).

This enables direct comparison: "Is the BMA combination better than the best individual model at this station?" — the answer is in the same table, queryable with the same API.

**Schema note**: `SkillScore.model_artifact_id` and `SkillDiagram.model_artifact_id` are currently non-nullable (`ArtifactId`, not `ArtifactId | None`). Combined forecasts have no single artifact. When S.4b is implemented (v0b+), both types must be relaxed to `ArtifactId | None` and the DB columns made nullable via Alembic migration. This is a v0b schema change — not needed for v0.

**BMA weight training**: Design of cross-validation strategy (temporal split, hold-out, etc.) is deferred to the v0c plan. Not specified here.

### D5. Phased rollout

| Phase | What's implemented | Default config |
|-------|-------------------|----------------|
| **v0** | Fallback models (climatology + persistence) registered and assigned. `forecast_combination_strategy` config field exists with default `PRIMARY`. Individual model forecasts stored; no combination step runs. | `forecast_combination_strategy = PRIMARY` |
| **v0b** | `pooled` combination implemented (step 1.8b). Combined forecast stored alongside individual forecasts. S.4b computes combined skill on hindcast. `SkillScore.model_artifact_id` made nullable. Sentinel model entries added to `models` table. DB migration for `combination_strategy` and `source_model_ids` columns on `forecasts`. | Switch to `pooled` when ≥2 models per station |
| **v0c** | `bma` combination implemented with weight training (linked to Flow 8/10 recomputation). Cross-validated skill evaluation for combined product. | Switch to `bma` once weights are trained and validated |
| **v1** | `consensus` if stakeholder demand. Flow 3 integration: forecaster can select combined product for publication. Full combined skill in dashboard. | `bma` recommended default |

This mirrors the `alert_model_strategy` rollout (v0-scope.md §A8d) and shares the same implementation timeline.

---

## Scope for This Plan (v0 only)

This plan implements the **v0 slice**: fallback models and config scaffolding. Combination strategies (pooled, BMA, consensus) and combined skill computation are design decisions documented here for architectural alignment but implemented in follow-up plans.

### v0 deliverables

1. `ClimatologyFallbackModel` — `StationForecastModel` implementation
2. `PersistenceFallbackModel` — `StationForecastModel` implementation
3. Rename `AlertModelStrategy` → `ModelCombinationStrategy` in `types/enums.py` + all references
4. `forecast_combination_strategy` config field on `DeploymentConfig` (using renamed `ModelCombinationStrategy`)
5. Sentinel `ModelId` constants in `types/ids.py`
6. Documentation updates (architecture-context.md, data-flows.md, v0-scope.md, conventions.md)

---

## Tasks

### Task 1: Fallback model — `ClimatologyFallbackModel`

**Scope**: Implement `ClimatologyFallbackModel` in `src/sapphire_flow/models/climatology_fallback.py` satisfying the `StationForecastModel` Protocol. Follow the existing `LinearRegressionDaily` implementation pattern (`src/sapphire_flow/models/linear_regression_daily.py`).

- `artifact_scope = ArtifactScope.STATION`
- `data_requirements`:
  - `target_parameters`: configurable at instantiation (e.g., `frozenset({"discharge"})`)
  - `past_dynamic_features`: empty frozenset (no weather forcing)
  - `future_dynamic_features`: empty frozenset
  - `static_features`: empty frozenset
  - `supported_time_steps`: `frozenset({timedelta(hours=24)})` (daily only for v0; extend later)
  - `lookback_steps`: 1 — the model does NOT use `past_targets` at predict time; all DOY quantile information lives in the trained artifact. `lookback_steps = 0` has not been exercised in the orchestrator's input assembly path and may cause edge cases; `1` is the safe choice.
  - `spatial_input_type`: `SpatialRepresentation.POINT`
- `train(self, data: StationTrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact`: Compute per-day-of-year quantile distribution from `data.past_targets` (quantile levels: 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95). Return a domain artifact dataclass (e.g., `ClimatologyArtifact`) containing the quantile DataFrame. Raise `ValueError` if fewer than 365 samples in the training data (training-time requirement, not a `lookback_steps` constraint).
- `predict(self, artifact: ModelArtifact, inputs: StationModelInputs, rng: random.Random, prior_state: bytes | None = None) -> tuple[dict[str, ForecastEnsemble], bytes | None]`: For each forecast step, compute the valid_time from `inputs.issue_time + step * inputs.time_step`, look up that day-of-year's quantile distribution from the artifact. Construct `ForecastEnsemble` via `from_quantiles(station_id=inputs.station_id, issued_at=inputs.issue_time, parameter=..., units=..., time_step=inputs.time_step, ...)` with values DataFrame schema `[valid_time, quantile, value]` (exactly these three columns — no `member_id`). Return `(ensembles_dict, None)` — no prior state, `rng` and `prior_state` unused, `past_targets` not read.
- `serialize_artifact()` / `deserialize_artifact()`: Use Polars IPC (`write_ipc` / `read_ipc`) for the quantile DataFrame. **No pickle** (project convention — see existing test `test_no_pickle_in_serialization`).

**Out of scope**: BMA weighting, NWP forcing, warm-up state, sub-daily time steps.
**Files**: `src/sapphire_flow/models/climatology_fallback.py`, `tests/unit/models/test_climatology_fallback.py`
**Entry point**: Register as `climatology_fallback` in `pyproject.toml` under `[project.entry-points."sapphire_flow.models"]`.
**Verification**: `uv run pytest tests/unit/models/test_climatology_fallback.py -x -q`

### Task 2: Fallback model — `PersistenceFallbackModel`

**Scope**: Implement `PersistenceFallbackModel` in `src/sapphire_flow/models/persistence_fallback.py` satisfying the `StationForecastModel` Protocol. Follow the existing `LinearRegressionDaily` implementation pattern.

- `artifact_scope = ArtifactScope.STATION`
- `data_requirements`:
  - `target_parameters`: configurable at instantiation (e.g., `frozenset({"discharge"})`)
  - `past_dynamic_features`: empty frozenset
  - `future_dynamic_features`: empty frozenset
  - `static_features`: empty frozenset
  - `supported_time_steps`: `frozenset({timedelta(hours=24)})`
  - `lookback_steps`: 1 (only the latest observation)
  - `spatial_input_type`: `SpatialRepresentation.POINT`
- `train(self, data: StationTrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact`: Return a minimal sentinel artifact dataclass (e.g., `PersistenceArtifact(parameter=...)`). No learned parameters — `data`, `params`, and `rng` are accepted but unused. The artifact exists so the model can be registered in the artifact store.
- `predict(self, artifact: ModelArtifact, inputs: StationModelInputs, rng: random.Random, prior_state: bytes | None = None) -> tuple[dict[str, ForecastEnsemble], bytes | None]`: Extract the most recent observation from `inputs.data.past_targets` — this is a wide-format Polars DataFrame with columns `[timestamp, <parameter_name>, ...]`. Select the target parameter column (e.g., `"discharge"`) from `data_requirements.target_parameters` and take the last row by timestamp. Repeat the value across all forecast steps. Generate quantile spread that widens linearly with lead time. The base spread percentage per step is a constructor parameter (e.g., `spread_pct_per_step: float = 0.05` meaning ±5% of the observed value per step). Construct `ForecastEnsemble` via `from_quantiles(station_id=inputs.station_id, issued_at=inputs.issue_time, parameter=..., units=..., time_step=inputs.time_step, ...)` with values DataFrame schema `[valid_time, quantile, value]` (exactly these three columns), at least 7 quantile levels (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95). Return `(ensembles_dict, None)` — `rng` and `prior_state` unused.
- `serialize_artifact()` / `deserialize_artifact()`: JSON (artifact is a small metadata dict). **No pickle.**

**Out of scope**: Anything beyond trivial persistence.
**Files**: `src/sapphire_flow/models/persistence_fallback.py`, `tests/unit/models/test_persistence_fallback.py`
**Entry point**: Register as `persistence_fallback` in `pyproject.toml` under `[project.entry-points."sapphire_flow.models"]`.
**Verification**: `uv run pytest tests/unit/models/test_persistence_fallback.py -x -q`

### Task 3: Rename enum + config scaffolding + sentinel constants

**Scope**: Three changes:

1. **Rename `AlertModelStrategy` → `ModelCombinationStrategy`** in `src/sapphire_flow/types/enums.py`. Update all imports and references across the codebase (`config/deployment.py`, `services/alert_checker.py`, `flows/run_forecast_cycle.py`, tests, etc.). The enum values (`PRIMARY`, `POOLED`, `BMA`, `CONSENSUS`) are unchanged.
2. **Add `forecast_combination_strategy: ModelCombinationStrategy`** field to `DeploymentConfig` (`src/sapphire_flow/config/deployment.py`) with default `ModelCombinationStrategy.PRIMARY`.
3. **Add sentinel `ModelId` constants** to `src/sapphire_flow/types/ids.py`: `POOLED_MODEL_ID = ModelId("_pooled")`, `BMA_MODEL_ID = ModelId("_bma")`, `CONSENSUS_MODEL_ID = ModelId("_consensus")`.

**Note**: The sentinel constants exist in Python from v0 onwards, but the corresponding rows in the `models` DB table are not inserted until v0b (Plan 026 Task 1). This is intentional — the constants are needed for type-safe references in code, but no combined forecast is stored in v0 so no FK lookup occurs.

**Out of scope**: No combination logic, no DB migration, no sentinel model table entries (v0b).
**Files**: `src/sapphire_flow/types/enums.py`, `src/sapphire_flow/config/deployment.py`, `src/sapphire_flow/types/ids.py`, all files importing `AlertModelStrategy`
**Verification**: `uv run pyright --strict src/sapphire_flow/ && uv run pytest tests/ -x -q`

### Task 4: Documentation updates

**Scope**: Update the following documents to reflect the multi-model combination design:

- `docs/architecture-context.md`: Add "Multi-model forecast combination" section near the existing "Multi-model alert strategy" content. Document D2 (forecast combination strategy), D3 (DB representation), D4 (combined skill). Update Flow 3 step 3.3 to note that the forecaster can select the combined product (v1).
- `docs/handover/data-flows.md`: Update Flow 1 to mention step 1.8b (combination, v0b+). Update the "Multi-model fallback" note to distinguish error fallback from combination. Add note about fallback models (climatology at priority 90, persistence at priority 99).
- `docs/v0-scope.md`: Add §A8e documenting the `forecast_combination_strategy` phased rollout (parallel to §A8d). Add fallback models to the v0 model list.
- `docs/conventions.md`: Add fallback priority tier (90–99) to the model assignment priority convention.

**Out of scope**: No code changes.
**Files**: `docs/architecture-context.md`, `docs/handover/data-flows.md`, `docs/v0-scope.md`, `docs/conventions.md`
**Verification**: `uv run pytest tests/ -x -q` (confirms no code breakage from doc-only changes)

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
    }
  ]
}
```

## Key Design Decisions Summary

1. **Fallback models are real models**: They go through the full model Protocol — train, artifact, predict, QC, skill — not a special-case hack in the forecast cycle. This means they're automatically covered by existing hindcast and skill computation flows.

2. **Rename `AlertModelStrategy` → `ModelCombinationStrategy`**: One enum for both concerns (alerting + forecast combination). Both config fields are independently settable. The rename aligns with type-driven development principles — the enum name describes its actual purpose.

3. **Combined forecasts in the same table**: No schema duplication. Discriminator column (`combination_strategy`) plus sentinel `model_id`. Same API, same review lifecycle.

4. **Combined skill uses existing schema**: Sentinel `model_id` in `skill_scores`. Requires `model_artifact_id` nullable (v0b schema change). Same metrics, same API. Direct comparison with individual models.

5. **Phased rollout mirrors alert strategy**: v0 = scaffolding + fallback models; v0b = pooled + schema migration; v0c = BMA; v1 = full Flow 3 integration. Each phase is self-contained and independently valuable.

## Not In Scope (follow-up plans)

- Pooled combination logic and step 1.8b (v0b)
- BMA weight training and cross-validated skill evaluation (v0c)
- Consensus combination logic (v1, if demand)
- Flow 3 integration — combined product in forecast review UI (v1)
- DB migration for `combination_strategy` and `source_model_ids` columns on `forecasts` (v0b)
- Sentinel model entries in `models` table (v0b migration)
- `SkillScore.model_artifact_id` nullable + Alembic migration (v0b)
