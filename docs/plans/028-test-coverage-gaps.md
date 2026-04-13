# Plan 028 — Test Coverage Gap Remediation

**Status**: READY  
**Phase**: Cross-cutting (test infrastructure)

## Problem

A systematic audit of the test suite (893 tests) identified critical coverage gaps. The store layer and type validators are well-tested, but three operational flows have near-zero test coverage, key service code paths (flow regime stratification, threshold-based skill metrics, quantile exceedance) are untested, and the API layer (Phase 9, ~30% implemented) has no tests at all.

This plan addresses the gaps ranked by risk of silent regression. The API layer is excluded — it belongs in the Phase 9 plan since the routes are still under construction.

## Scope

**In scope**: Flow-level tests, service gap tests, type validator tests, Protocol conformance tests, forecast cycle combination mode test.

**Out of scope**: API route tests (Phase 9), new Pg store implementations for deferred Protocols (RatingCurveStore, ForecastAdjustmentStore, ForeignForecastStore), performance/load tests, e2e CI pipeline.

---

## Tasks

### Task 1: Flow tests — `run_hindcast.py`

**Scope**: Create `tests/unit/flows/test_run_hindcast.py` with tests covering the `run_hindcast_flow` orchestrator and its two task wrappers. Use fake stores and fake models.

Tests to write:
- Station-scoped hindcast: happy path with a fake model, verify hindcasts and skill scores stored
- Group-scoped hindcast: happy path via `group_id`, verify per-station hindcasts produced
- Neither `station_id` nor `group_id` provided raises `ValueError`
- `model=None` for station path raises `ValueError("model must be provided for station hindcast")`
- `artifact=None` for station path raises `ValueError("artifact must be provided for station hindcast")`
- `model=None` for group path raises `ValueError("model must be provided for group hindcast")`
- `artifact=None` for group path raises `ValueError("artifact must be provided for group hindcast")`
- Group not found (fake store returns `None` for `group_id`) raises `ValueError(f"Group {group_id} not found")`

**Out of scope**: Real database. Real Prefect server.  
**Files**: `tests/unit/flows/test_run_hindcast.py`  
**Verification**: `uv run pytest tests/unit/flows/test_run_hindcast.py -x -q`

### Task 2: Flow tests — `train_models.py`

**Scope**: Extend the existing tests in `tests/unit/flows/test_train_models.py` (currently 2 import checks + 1 `compute_skills_task` test) with meaningful `train_models_flow` tests. Use fake stores and fake models.

Tests to write:
- Happy path: train a station-scoped model, verify artifact stored, hindcasts run, skill computed
- Model not found in registry: verify returned `TrainingResult` has non-None `error` field
- Group model training path (via `group_id`)
- SHA-256 artifact integrity round-trip (store → fetch → verify hash)
- Tampered artifact — two independent paths, both must be tested:
  - (a) **Store-layer integrity**: Corrupt bytes in `FakeModelArtifactStore._bytes` after `store_artifact()`, then call `fetch_artifact()` → verify `ArtifactIntegrityError`.
  - (b) **Flow-layer defense-in-depth**: The flow's own SHA-256 re-check (lines 268–278 of `train_models.py`) is a second line of defense. To test it independently of (a), create a thin spy store that wraps `FakeModelArtifactStore` and returns silently corrupted bytes from `fetch_artifact()` *without* raising `ArtifactIntegrityError` itself (simulating a store implementation that lacks integrity checks). Verify the flow raises `ValueError("SHA-256 mismatch...")`.

**Out of scope**: Real database. Full fan-out with `task.map`.  
**Files**: `tests/unit/flows/test_train_models.py`  
**Verification**: `uv run pytest tests/unit/flows/test_train_models.py -x -q`

### Task 3: Flow tests — `onboard_model.py`

**Scope**: Create meaningful tests in `tests/unit/flows/test_onboard_model_flow.py` (replace the current 3 cosmetic tests). Use fake stores, fake models, and factory helpers from `conftest.py`. All tests must:
- Patch `sapphire_flow.flows.onboard_model.concurrency` (top-level import — patch where the name is imported) to avoid Prefect API calls.
- Patch `sapphire_flow.services.model_registry.discover_models` to inject fake models. Note: `discover_models` is imported **inside the flow function body** (line 323), not at module scope, so the patch target is the canonical definition site, not `sapphire_flow.flows.onboard_model.discover_models`.
- Patch `prefect.runtime.flow_run` (or set up a mock context) — the flow accesses `prefect.runtime.flow_run.id` at line 508 for run tracking. Without this, tests calling `.fn()` directly will get `None` or raise.

Tests to write:
- Happy path: model discovered, compatibility check passes, smoke test passes, skill gate passes, artifact promoted, assignment created
- Compatibility check fails (model requires features the station doesn't have) — model skipped for that station
- Skill gate rejects model (below threshold) — artifact not promoted
- Model already assigned — idempotent (no duplicate assignment)
- Structured log assertion: use `structlog.testing.capture_logs()` to verify `model.skill_gate_completed` is emitted at WARNING level when `passed=False`

**Out of scope**: Real database. Download/adapter logic.  
**Files**: `tests/unit/flows/test_onboard_model_flow.py`  
**Verification**: `uv run pytest tests/unit/flows/test_onboard_model_flow.py -x -q`

### Task 4: Skill service — flow regime stratification + threshold metrics

**Scope**: Add tests to `tests/unit/services/skill/test_service.py` for the two largest untested code paths in `compute_skill_for_station()`. Use realistic hydrological magnitudes (see Design Decision §5): observations and hindcasts should reflect plausible Swiss discharge ranges, and danger level thresholds should be consistent with real BAFU alert levels. Load station metadata from `tests/fixtures/reference/stations.toml` where applicable. Note: `compute_skill_for_station` requires a `seasons: list[SeasonDefinition]` parameter — pass `[]` for non-season tests.

Tests to write:
- **Flow regime**: Provide a `FlowRegimeConfig` with p50/p90 thresholds based on realistic discharge quantiles (e.g., Aare at Bern: p50 ≈ 150 m³/s, p90 ≈ 350 m³/s). Verify scores are produced with `flow_regime = FlowRegime.LOW`, `FlowRegime.HIGH`, `FlowRegime.FLOOD` keys. Verify `flow_regime_config_id` is stamped on scores. Verify CRPS scores are produced and stratified by flow regime.
- **Threshold metrics**: Provide `thresholds` (at least 2 danger levels) with realistic exceedance values. Verify BSS, POD, FAR, CSI metrics appear in the returned scores — note metric names embed the danger level string (e.g., `bss_danger_moderate`, `pod_danger_high`). Verify reliability and ROC diagrams are produced.
- **Rank histogram + sharpness**: Verify rank histogram diagram is produced for MEMBERS representation. Verify sharpness metrics (P10–P90 width, P25–P75 width) appear in returned scores.
- **QUANTILES hindcasts**: Run `compute_skill_for_station` with quantile-representation hindcasts instead of members.
- **`artifact_id=None`**: Verify combined-model path works (scores have `model_artifact_id=None`).

**Out of scope**: BMA cross-validation (already tested in `test_combined_skill.py`).  
**Files**: `tests/unit/services/skill/test_service.py`  
**Verification**: `uv run pytest tests/unit/services/skill/test_service.py -x -q`

### Task 5: Alert strategy — QUANTILES exceedance + pooled normal path

**Scope**: Add tests to `tests/unit/services/test_alert_strategy.py` and `tests/unit/services/test_alert_checker.py` for untested code paths. Use realistic discharge magnitudes and danger level thresholds consistent with Swiss BAFU alert levels (see Design Decision §5).

Tests to write:
- **QUANTILES exceedance** in `_compute_exceedance()`: Create a quantile-representation ensemble with realistic discharge quantiles, compute exceedance against a danger level threshold. Verify CDF interpolation returns a plausible probability (e.g., threshold at median → ~0.5).
- **Normal pooled path**: `_resolve_strategy_and_filter()` with `POOLED` strategy, 2+ models, homogeneous MEMBERS representation. Verify all models' ensembles are included (not fallen back to PRIMARY).
- **Multi-station dispatch** in `check_station_alerts()`: Pass `all_ensembles` with 2+ stations (use real station IDs from `stations.toml` via `parse_stations_toml()`). Provide a `DeploymentConfig` with `enable_forecast_alerts=True` and `threshold_check_mode="raw"` — without these the function silently returns without checking anything. Verify alerts are checked for each station independently.
- **Unknown strategy** in `_resolve_strategy_and_filter()`: Verify raises `ValueError`.

**Out of scope**: BMA alert strategy (not yet implemented).  
**Files**: `tests/unit/services/test_alert_strategy.py`, `tests/unit/services/test_alert_checker.py`  
**Verification**: `uv run pytest tests/unit/services/test_alert_strategy.py tests/unit/services/test_alert_checker.py -x -q`

### Task 6: Forecast QC overrides + `_qc_helpers.merge_thresholds`

**Scope**: Add tests for the forecast QC override merging path.

Tests to write:
- **`merge_thresholds` for forecast QC**: Create a `ForecastQcRuleSet` with default thresholds, plus a `StationForecastQcOverride` that overrides one rule's threshold for a specific station/parameter. Call `ForecastOutputQualityChecker.check()` with the override. Verify the overridden threshold takes effect (e.g., a value that passes with default but fails with override, or vice versa).
- **No matching override**: Override for a different station. Verify default threshold applies.

**Out of scope**: Observation QC overrides (already tested in `test_qc.py`).  
**Files**: `tests/unit/services/test_forecast_qc.py`  
**Verification**: `uv run pytest tests/unit/services/test_forecast_qc.py -x -q`

### Task 7: Forecast cycle combination mode + remaining service gaps

**Scope**: Add a test to `tests/unit/flows/test_run_forecast_cycle.py` for the non-PRIMARY combination path. Plus small targeted tests for remaining gaps.

Tests to write:
- **Forecast cycle POOLED mode**: Set `forecast_combination_strategy = ModelCombinationStrategy.POOLED` on the config. Provide 2 models per station. Verify individual forecasts stored for both models, plus a combined forecast with `combination_strategy="pooled"`.
- **`compute_combined_skills_task` BMA branch**: In `tests/unit/flows/test_compute_skills.py`, add a test with `strategy=ModelCombinationStrategy.BMA` and 2 models with member-representation hindcasts.

**Out of scope**: Full end-to-end pipeline test.  
**Files**: `tests/unit/flows/test_run_forecast_cycle.py`, `tests/unit/flows/test_compute_skills.py`  
**Verification**: `uv run pytest tests/unit/flows/ -x -q`

### Task 8: Type validators + Protocol conformance

**Scope**: Fill remaining type-layer and Protocol conformance gaps.

Tests to write:
- **`types/model_onboarding.py`**: `CompatibilityReport.__post_init__` (both-set, neither-set invalid), `SkillGateResult.__post_init__` (two separate duplicate checks: duplicate metric name in `metric_scores`, duplicate metric name in `thresholds`), `ModelOnboardingResult` count methods (`promoted_count`, `failed_count`, `skipped_count`, `gate_rejected_count`).
- **`types/training.py`**: Station-scoped with mismatched `station_ids`, group-scoped with empty `station_ids`.
- **Protocol conformance**: Add `isinstance` checks for `NotificationAdapter` (create a minimal `FakeNotificationAdapter` in `fake_adapters.py`), `ModelAlertStrategy` (check concrete `PrimaryModelStrategy` and `PooledEnsembleStrategy`), `QualityChecker` (check `Stage1QualityChecker`), and `ForecastQualityChecker` (check `ForecastOutputQualityChecker`). Add conformance `isinstance` checks for existing `FakeMultiTargetStationForecastModel` and `FakeMultiTargetGroupForecastModel` (already in `fake_models.py`).

**Out of scope**: Tests for plain dataclasses with no validators.  
**Files**: `tests/unit/types/test_model_onboarding.py`, `tests/unit/types/test_training.py`, `tests/fakes/test_fakes.py`, `tests/fakes/fake_adapters.py`  
**Verification**: `uv run pytest tests/unit/types/test_model_onboarding.py tests/unit/types/test_training.py tests/fakes/test_fakes.py -x -q`

### Task 9: `config/deployment.py` — `load_config` + derived methods

**Scope**: Add tests to `tests/unit/config/test_deployment.py` for untested config parsing and derived methods.

Tests to write:
- **`load_config()`**: Provide a minimal TOML string, verify `DeploymentConfig` fields populated correctly. Test `paths` section parsing.
- **`_resolve_env_vars()`**: `${VAR}` substitution with set env var, unset env var raises `ValueError`.
- **`get_danger_level_definitions()`**: Config with 3 danger levels, verify list of `DangerLevelDefinition`.
- **`get_season_definitions()`**: Config with 2 seasons, verify list of `SeasonDefinition`.
- **`_validate_retention`**: `max_retention_days <= forecast_hot_days` raises `pydantic.ValidationError`.

**Out of scope**: Full production TOML parsing with all sections.  
**Files**: `tests/unit/config/test_deployment.py`  
**Verification**: `uv run pytest tests/unit/config/test_deployment.py -x -q`

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "label": "Critical flow gaps",
      "tasks": ["1", "2", "3"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "label": "Service code path gaps",
      "tasks": ["4", "5", "6"],
      "parallel": true,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "label": "Remaining gaps",
      "tasks": ["7", "8", "9"],
      "parallel": true,
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Key Design Decisions

1. **API tests excluded**: The API layer (Phase 9) is ~30% implemented. Testing incomplete routes would create brittle tests that break as routes are finished. API tests belong in the Phase 9 plan.

2. **Flow tests use fakes, not Prefect server**: Call `.fn()` or invoke the flow directly with fake stores/models. This keeps tests fast (~0.1s each) and avoids Prefect infrastructure.

3. **No coverage target**: The goal is to cover high-risk code paths identified by the audit, not to chase a percentage. Each test should fail for one reason and test behavior, not implementation.

4. **Phases ordered by risk**: Phase 1 (critical flows) has the highest blast radius. Phase 2 (service gaps) covers paths that could silently produce wrong results. Phase 3 (types + config + conformance) is lower risk but still worth addressing.

5. **Prefer real public data over synthetic fixtures**: Use real station metadata from `tests/fixtures/reference/stations.toml` (7 BAFU gauges with real coordinates). For Tasks 4–5 (skill computation, alert strategies), construct test observations and hindcasts with **realistic hydrological magnitudes** drawn from known Swiss station characteristics (e.g., Aare at Bern ~100–400 m³/s, Rhine at Basel ~500–2500 m³/s) rather than uniform random `[1, 50]`. Use danger level thresholds consistent with real BAFU alert levels. When the Tier-2 reference dataset (`tests/fixtures/reference/bafu_observations.parquet`) is populated with real data via `record_fixtures`, tests should migrate to use it. Flow-level tests (Tasks 1–3, 7) may continue using synthetic fakes since they test orchestration logic, not data processing.

6. **`NotificationAdapter` conformance (Task 8) is test infrastructure**: `v0-scope.md §G` excludes `NotificationAdapter` from v0 runtime scope, but `types-and-protocols.md` specifies `FakeNotificationAdapter` as standard fake infrastructure. The conformance test validates the Protocol definition, not a v0 runtime path.

## Not In Scope

- API route tests (Phase 9 plan)
- Pg implementations for deferred stores (RatingCurveStore, ForecastAdjustmentStore, ForeignForecastStore)
- e2e CI pipeline tests (Phase 11)
- Performance/load tests
- `@pytest.mark.parametrize` refactoring of existing tests (good idea but separate effort)
- `FakeStationDataSource` / `FakeWeatherForecastSource` realism improvements
