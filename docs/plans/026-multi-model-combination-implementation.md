# Plan 026 — Multi-Model Combination: Implementation

**Status**: READY  
**Phase**: Cross-cutting (extends Flows 1, 8/10; touches services, stores, types, DB schema)  
**Depends on**: Plan 025 (fallback models + config scaffolding)

## Context

Plan 025 established the design for multi-model combination forecasting (decisions D1–D5) and implemented the v0 slice (fallback models, `forecast_combination_strategy` config field, sentinel `ModelId` constants). This plan implements the combination machinery itself:

- **v0b**: pooled combination (step 1.8b), combined skill computation (step S.4b), schema migrations
- **v0c**: BMA combination with skill-weighted model averaging

### Current State (after Plan 025)

- All assigned models run per station every cycle (Flow 1 Phase B)
- `run_station_forecast()` uses **fallback mode**: iterates models by priority, stops at first success, returns one `StationForecastResult`
- `all_ensembles` accumulator in the flow stores **one model per station**: `all_ensembles[sid] = {fc_result.model_id: dict(fc_result.ensembles)}`
- The alert checker (`check_station_alerts`) already accepts `dict[StationId, dict[ModelId, ...]]` and dispatches on `AlertModelStrategy` — but currently only receives one model per station
- `forecast_store` writes `model_id` and `model_artifact_id` as NOT NULL, no combination columns
- `skill_scores.model_artifact_id` is NOT NULL
- `PgHindcastStore.fetch_hindcasts()` always filters by a single `model_id`
- `compute_skill_for_station()` takes exactly one `(model_id, artifact_id)` pair

### What Needs to Change

1. **Run all models per station** — not just the first success. Individual results stored separately. The fallback chain still determines which model is the "primary" (for `primary` strategy and for cases where combination fails), but all models run regardless.

2. **Combine ensembles** — new service that takes multiple models' ensembles and produces a combined `ForecastEnsemble` using the configured strategy (pooled, then BMA).

3. **Store combined forecasts** — new DB columns + updated store. Combined forecast stored alongside individual model forecasts.

4. **Compute combined skill** — retrieve multi-model hindcasts, construct combined hindcast ensemble, run verification metrics.

---

## Tasks

### Task 1: Alembic migration — combination columns + nullable artifact_id

**Scope**: Create an Alembic migration that:

1. Adds `VIRTUAL = "virtual"` to `ArtifactScope` enum in `src/sapphire_flow/types/enums.py`. Update the CHECK constraint on `models.artifact_scope` to include `'virtual'`.
2. Adds to `forecasts` table:
   - `combination_strategy TEXT NULL` — `NULL` for individual model forecasts
   - `source_model_ids JSONB NULL` — `NULL` for individual; `["model_a", "model_b"]` for combined
3. Makes `forecasts.model_artifact_id` nullable (`UUID NULL` instead of `NOT NULL`) — combined forecasts have no single artifact. Update FK constraint to allow NULL.
4. Makes `skill_scores.model_artifact_id` nullable (`UUID NULL` instead of `NOT NULL`)
5. Makes `skill_diagrams.model_artifact_id` nullable (same change, keep consistent)
6. Inserts sentinel model entries into `models` table (columns: `id`, `display_name`, `artifact_scope`, `description`):
   - `id = '_pooled'`, `display_name = 'Pooled Ensemble'`, `description = 'Grand ensemble from all models'`, `artifact_scope = 'virtual'`
   - `id = '_bma'`, `display_name = 'BMA Ensemble'`, `description = 'Bayesian Model Averaging weighted ensemble'`, `artifact_scope = 'virtual'`
   - `id = '_consensus'`, `display_name = 'Consensus Forecast'`, `description = 'Consensus across models'`, `artifact_scope = 'virtual'`

**Out of scope**: No Python type changes beyond `ArtifactScope` enum (remaining type changes are Task 2). No combination logic.  
**Files**: `src/sapphire_flow/types/enums.py` (ArtifactScope), `alembic/versions/XXXX_add_combination_columns.py`, `src/sapphire_flow/db/metadata.py` (update table definitions)  
**Verification**: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` (round-trip)

### Task 2: Type and store updates for combination forecasts

**Scope**: Update Python types, service signatures, and stores to support combination forecasts:

1. `SkillScore.model_artifact_id: ArtifactId | None` (was `ArtifactId`) in `src/sapphire_flow/types/skill.py`
2. `SkillDiagram.model_artifact_id: ArtifactId | None` (same change)
3. `OperationalForecast.model_artifact_id: ArtifactId | None` (was `ArtifactId`) in `src/sapphire_flow/types/forecast.py` — combined forecasts have no artifact
4. Add to `OperationalForecast` in `src/sapphire_flow/types/forecast.py`:
   - `combination_strategy: str | None = None`
   - `source_model_ids: list[ModelId] | None = None`
5. Update `PgForecastStore.store_forecast()` to write the new columns. Update `_rows_to_domain()` (or equivalent fetch path) to handle `model_artifact_id = NULL` without crashing — currently hardcodes `ArtifactId(header["model_artifact_id"])`.
6. Update `PgSkillScoreStore` (and `PgSkillDiagramStore` if separate) to handle nullable `model_artifact_id` in both write and read paths.
7. Update `compute_skill_for_station()` in `src/sapphire_flow/services/skill/service.py` to accept `artifact_id: ArtifactId | None` (was `ArtifactId`) — needed for Task 7 (combined skill passes `None`). Also update private helpers `_compute_scores()` and `_compute_diagrams()` which receive and pass through `artifact_id` — they must also accept `ArtifactId | None` for pyright to pass.

**Out of scope**: No new service logic beyond signature changes. No flow changes.  
**Files**: `src/sapphire_flow/types/skill.py`, `src/sapphire_flow/types/forecast.py`, `src/sapphire_flow/store/forecast_store.py`, `src/sapphire_flow/store/skill_store.py` (if exists), `src/sapphire_flow/services/skill/service.py`, corresponding test files  
**Verification**: `uv run pyright --strict src/sapphire_flow/types/skill.py src/sapphire_flow/types/forecast.py && uv run pytest tests/ -x -q`

### Task 3: Service — run all station models (refactor `run_station_forecast`)

**Scope**: Refactor `src/sapphire_flow/services/run_station_forecast.py` to support two execution modes:

1. **Extract** the per-model predict-and-QC logic into a shared helper (e.g., `_run_single_model()`) that takes one model assignment and returns a result or error.
2. **Add** `run_all_station_forecasts()` that runs ALL assigned models for a station (not just the first success). Returns a new dataclass:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class MultiModelForecastResult:
    station_id: StationId
    results: dict[ModelId, StationForecastResult]  # all successful models
    primary_model_id: ModelId | None               # highest-priority success (for fallback)
    failed_models: dict[ModelId, str]              # model_id → error message
```

3. **Keep** `run_station_forecast()` as a thin wrapper that calls `run_all_station_forecasts()` and returns only the primary model's result (backward compatible — the flow uses this in `primary` mode).

**Key behaviour**: Every model runs regardless of earlier successes/failures. A model that fails prediction or QC is recorded in `failed_models` but does not stop the loop. The `primary_model_id` is the highest-priority model that succeeded (same as current fallback logic).

**Out of scope**: No combination logic. No flow changes.  
**Files**: `src/sapphire_flow/services/run_station_forecast.py`, `tests/unit/services/test_run_station_forecast.py`  
**Verification**: `uv run pytest tests/unit/services/test_run_station_forecast.py -x -q`

### Task 4: Service — pooled ensemble combination

**Scope**: Create `src/sapphire_flow/services/forecast_combination.py` with:

1. `combine_ensembles_pooled(ensembles: dict[ModelId, dict[str, ForecastEnsemble]]) -> dict[str, ForecastEnsemble]`
   - For each parameter: merge all models' member ensembles into a single grand ensemble.
   - All input ensembles must have `representation = MEMBERS` (pooling quantile ensembles is not meaningful). If any ensemble is quantile-only, skip that model for pooling and log a warning.
   - The resulting ensemble has `model_id = POOLED_MODEL_ID` (sentinel from `types/ids.py`), `representation = MEMBERS`, and member count = sum of all models' members.
   - Member IDs are remapped to avoid collision: `model_a_member_0`, `model_a_member_1`, ... or simply sequential integers.

2. `build_combined_forecasts(station_id, multi_result: MultiModelForecastResult, strategy: AlertModelStrategy, nwp_metadata, clock, uuid_factory) -> list[OperationalForecast]`
   - Dispatches on strategy (v0b: only `POOLED` implemented; others raise `NotImplementedError`)
   - Constructs `OperationalForecast` objects with `combination_strategy` and `source_model_ids` set
   - Uses sentinel `model_id` and `model_artifact_id = None`
   - Sets `input_quality = InputQualityLevel.FULL` and `input_quality_flags = ()` (combined forecast quality is derived from individual models, not reassessed)

**Out of scope**: BMA and consensus strategies (v0c/v1). No flow changes.  
**Files**: `src/sapphire_flow/services/forecast_combination.py`, `tests/unit/services/test_forecast_combination.py`  
**Verification**: `uv run pytest tests/unit/services/test_forecast_combination.py -x -q`

### Task 5: Wire combination into forecast cycle flow

**Scope**: Update `src/sapphire_flow/flows/run_forecast_cycle.py` Phase B to support combination mode:

1. **Dispatch on `config.forecast_combination_strategy`**:
   - `PRIMARY`: call `run_station_forecast()` (existing behavior — single model, fallback)
   - `POOLED` / `BMA` / `CONSENSUS`: call `run_all_station_forecasts()`, store ALL individual model forecasts, then call `build_combined_forecasts()` and store the combined forecast
2. **Populate `all_ensembles` with ALL models' ensembles** (not just the primary) when in combination mode. This enables the alert checker to use multi-model alerting strategies.
3. **Step 1.8b**: After storing individual forecasts and before Phase C, construct and store the combined forecast.

**Fallback**: If combination fails (e.g., only one model succeeded, incompatible representations), fall back to `primary` — store only the primary model's forecast, log a warning.

**Input assembly for multiple models**: The current flow calls `assemble_station_operational_inputs()` using the highest-priority model's `data_requirements`. In combination mode, each model may have different requirements (lookback, features). Strategy: assemble inputs using the **most demanding** model's requirements (max lookback, union of features). Each model's `predict()` receives the same `StationModelInputs` — models that need fewer features simply ignore extras. This matches the current single-model behavior (the Protocol does not enforce that models consume all supplied features). If a fallback model (climatology/persistence) is included, its minimal requirements are a subset and naturally satisfied.

**Out of scope**: No alert checker changes (it already supports multi-model). No new Prefect tasks (combination runs inline in Phase B per-station loop).  
**Files**: `src/sapphire_flow/flows/run_forecast_cycle.py`, `tests/unit/flows/test_run_forecast_cycle.py`  
**Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -x -q`

### Task 6: Extend hindcast store for multi-model retrieval

**Scope**: Add `fetch_hindcasts_by_station(station_id, parameter, period_start, period_end) -> dict[ModelId, list[HindcastForecast]]` to `PgHindcastStore`. This retrieves hindcasts from ALL models for a given station and period, grouped by model. Needed by the combined skill computation (Task 7) to construct combined hindcast ensembles.

The existing `fetch_hindcasts(station_id, model_id, ...)` stays unchanged.

**Out of scope**: No skill computation logic.  
**Files**: `src/sapphire_flow/store/hindcast_store.py`, `tests/unit/store/test_hindcast_store.py` (or integration tests)  
**Verification**: `uv run pytest tests/ -k hindcast -x -q`

### Task 7: Service — combined skill computation (step S.4b)

**Scope**: Create `src/sapphire_flow/services/skill/combined_skill.py` with:

`compute_combined_skill(station_id, parameter, strategy: AlertModelStrategy, hindcasts_by_model: dict[ModelId, list[HindcastForecast]], observations, thresholds, flow_regime_config, seasons, skill_source, clock, uuid_factory) -> tuple[list[SkillScore], list[SkillDiagram]]`

1. For each hindcast time step, construct the combined ensemble from all models' hindcast ensembles at that step (using the same combination logic from Task 4).
2. Pass the combined hindcast series to the existing `compute_skill_for_station()` — but with `model_id = POOLED_MODEL_ID` (sentinel) and `artifact_id = None`.
3. Return `SkillScore` and `SkillDiagram` records with sentinel model_id.

**Alignment**: Must filter to time steps where ALL models have hindcast results (intersection, not union). Log and report coverage: "combined skill computed on N of M total hindcast steps."

**Out of scope**: BMA weight training (v0c). No flow wiring (Task 8).  
**Files**: `src/sapphire_flow/services/skill/combined_skill.py`, `tests/unit/services/skill/test_combined_skill.py`  
**Verification**: `uv run pytest tests/unit/services/skill/test_combined_skill.py -x -q`

### Task 8: Wire combined skill into skill computation flow

**Scope**: Update the skill computation flow (Flows 8/10) to add step S.4b:

1. After per-model skill computation completes, check `config.forecast_combination_strategy`.
2. If not `PRIMARY`: for each station, call `fetch_hindcasts_by_station()` (Task 6) to get all models' hindcasts, then call `compute_combined_skill()` (Task 7).
3. Store the resulting `SkillScore` and `SkillDiagram` records.

**Out of scope**: BMA cross-validation (v0c).  
**Files**: Skill computation flow file (find in `src/sapphire_flow/flows/`), corresponding test file  
**Verification**: `uv run pytest tests/ -x -q`

### Task 9: Service — BMA weight computation (v0c)

**Scope**: Create `src/sapphire_flow/services/skill/bma_weights.py` with:

`compute_bma_weights(station_id, parameter, skill_scores_by_model: dict[ModelId, list[SkillScore]], metric: str = "crps", lead_time_hours: int | None = None) -> dict[ModelId, float]`

1. For each model, retrieve the most recent `CURRENT`-freshness skill score for the specified metric (default CRPS) and optionally a specific lead time.
2. Convert scores to weights using inverse-CRPS normalisation (lower CRPS = higher weight). Models with no skill score receive zero weight.
3. Weights sum to 1.0. If only one model has scores, it gets weight 1.0 (equivalent to `primary`).
4. Return `dict[ModelId, float]`.

**Weight storage**: BMA weights are ephemeral — computed on the fly from `skill_scores`. No new table needed. If weight computation becomes expensive, caching can be added later.

**Out of scope**: Cross-validated weight training (see D4 note in Plan 025 — deferred design). No flow wiring.  
**Files**: `src/sapphire_flow/services/skill/bma_weights.py`, `tests/unit/services/skill/test_bma_weights.py`  
**Verification**: `uv run pytest tests/unit/services/skill/test_bma_weights.py -x -q`

### Task 10: Service — BMA ensemble combination

**Scope**: Add `combine_ensembles_bma()` to `src/sapphire_flow/services/forecast_combination.py`:

`combine_ensembles_bma(ensembles: dict[ModelId, dict[str, ForecastEnsemble]], weights: dict[ModelId, float]) -> dict[str, ForecastEnsemble]`

1. For each parameter and each forecast step: weight each model's ensemble members by the model's BMA weight. Practically: subsample or replicate members proportional to weight (e.g., if model A has weight 0.7 and model B 0.3, and both have 50 members, the combined ensemble has 70 members from A and 30 from B — total 100).
2. The resulting ensemble has `model_id = BMA_MODEL_ID`, `representation = MEMBERS`.
3. Models with zero weight are excluded.

Update `build_combined_forecasts()` to dispatch to `combine_ensembles_bma()` when `strategy = BMA`.

**Out of scope**: Consensus strategy (v1).  
**Files**: `src/sapphire_flow/services/forecast_combination.py`, `tests/unit/services/test_forecast_combination.py`  
**Verification**: `uv run pytest tests/unit/services/test_forecast_combination.py -x -q`

### Task 11: Combined skill for BMA + cross-validation (v0c)

**Scope**: Extend `compute_combined_skill()` (Task 7) to support BMA:

1. Accept an optional `weights: dict[ModelId, float] | None` parameter. When provided, use BMA combination instead of pooled.
2. Implement two-fold temporal cross-validation for BMA skill evaluation:
   - Split the hindcast period into two halves
   - Fold 1: compute weights from half-1 skill scores, evaluate on half-2
   - Fold 2: compute weights from half-2 skill scores, evaluate on half-1
   - Average the two skill estimates
3. Store with `model_id = BMA_MODEL_ID`.

Update the skill computation flow (Task 8 wiring) to pass BMA weights when `strategy = BMA`.

**Out of scope**: Consensus strategy.  
**Files**: `src/sapphire_flow/services/skill/combined_skill.py`, skill flow file, corresponding test files  
**Verification**: `uv run pytest tests/ -x -q`

### Task 12: Documentation updates

**Scope**: Update docs to reflect the implemented combination machinery:

- `docs/architecture-context.md`: Update Flow 1 to document step 1.8b implementation, multi-model execution mode, and the `MultiModelForecastResult` data flow. Update Flows 8/10 to document step S.4b.
- `docs/handover/data-flows.md`: Update Flow 1 steps table to include 1.8b. Update skill computation steps to include S.4b.
- `docs/v0-scope.md`: Update §A8e to reflect v0b/v0c implementation status. Update deferred table.
- `docs/spec/database-schema.md`: Update `forecasts` table (new columns `combination_strategy`, `source_model_ids`; nullable `model_artifact_id`). Update `skill_scores` and `skill_diagrams` tables (nullable `model_artifact_id`). Add sentinel model entries to `models` table diagram. Update ER relationships.
- `docs/spec/types-and-protocols.md`: Update `ArtifactScope` enum (add `VIRTUAL`). Update `OperationalForecast` (new fields + nullable `model_artifact_id`). Update `SkillScore` and `SkillDiagram` (nullable `model_artifact_id`). Update `compute_skill_for_station()` signature.

**Out of scope**: No code changes.  
**Files**: `docs/architecture-context.md`, `docs/handover/data-flows.md`, `docs/v0-scope.md`, `docs/spec/database-schema.md`, `docs/spec/types-and-protocols.md`  
**Verification**: `uv run pytest tests/ -x -q` (confirms no code breakage from doc-only changes)

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "v0b-foundation",
      "label": "v0b — Schema + types",
      "tasks": ["1", "2"],
      "parallel": true
    },
    {
      "id": "v0b-services-a",
      "label": "v0b — Multi-model execution + hindcast store",
      "tasks": ["3", "6"],
      "parallel": true,
      "depends_on": ["v0b-foundation"]
    },
    {
      "id": "v0b-services-b",
      "label": "v0b — Pooled combination service",
      "tasks": ["4"],
      "parallel": false,
      "depends_on": ["v0b-services-a"],
      "note": "Task 4 uses MultiModelForecastResult from Task 3"
    },
    {
      "id": "v0b-wiring",
      "label": "v0b — Flow integration",
      "tasks": ["5", "7"],
      "parallel": true,
      "depends_on": ["v0b-services-b"]
    },
    {
      "id": "v0b-skill-flow",
      "label": "v0b — Skill flow wiring",
      "tasks": ["8"],
      "parallel": false,
      "depends_on": ["v0b-wiring"]
    },
    {
      "id": "v0c-bma",
      "label": "v0c — BMA combination",
      "tasks": ["9", "10"],
      "parallel": true,
      "depends_on": ["v0b-skill-flow"]
    },
    {
      "id": "v0c-bma-skill",
      "label": "v0c — BMA cross-validated skill",
      "tasks": ["11"],
      "parallel": false,
      "depends_on": ["v0c-bma"]
    },
    {
      "id": "docs",
      "label": "Documentation",
      "tasks": ["12"],
      "parallel": false,
      "depends_on": ["v0c-bma-skill"]
    }
  ]
}
```

## Key Design Decisions

1. **Run all models, not just first success**: `run_all_station_forecasts()` runs every assigned model regardless of prior successes. This is needed for both combination forecasting AND multi-model alerting (the alert checker already expects multi-model input). The existing `run_station_forecast()` becomes a thin wrapper for `primary` mode backward compatibility.

2. **Pooled combination requires MEMBERS representation**: Merging quantile ensembles is statistically unsound — you cannot pool quantiles from different distributions. Models producing quantile-only output are excluded from pooling with a warning. BMA has the same constraint. This is an important operational consideration: models should produce member ensembles, not just quantiles, if they want to participate in combination.

3. **BMA weights are ephemeral, not stored**: Computed on the fly from `skill_scores` using inverse-CRPS normalisation. No new table. If this becomes a performance concern, add a lightweight cache later.

4. **Combined skill uses intersection of hindcast steps**: Only time steps where ALL models have hindcast results contribute to combined skill. This avoids bias from models that cover different periods. Coverage statistics are logged.

5. **Sentinel models have `artifact_scope = 'virtual'`**: New `VIRTUAL` value added to `ArtifactScope` enum. Virtual model entries have no training, no artifacts, no entry point. The CHECK constraint on `models.artifact_scope` is updated to include `'virtual'`.

6. **Combined forecast stored alongside individual forecasts**: One combined `OperationalForecast` per (station, parameter, cycle), plus individual forecasts from each model. The API can filter by `combination_strategy IS NOT NULL` to return only combined forecasts, or `IS NULL` for individual models.

## Not In Scope (v1 follow-up)

- Consensus combination strategy
- Flow 3 integration — combined product in forecast review UI
- API filtering by combination_strategy
- BMA weight caching / materialised table
- Cross-model ensemble calibration (reliability correction of combined ensemble)
