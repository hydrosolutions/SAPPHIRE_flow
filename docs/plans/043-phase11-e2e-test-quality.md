# Plan 043 — Phase 11: End-to-End Test + Test Quality

**Status**: DRAFT
**Phase**: 11 (capstone) + cross-cutting test quality
**Depends on**: Phase 9 (Plan 041, DONE), Phases 1-8 (all complete for v0a)

## Context

### Why now

The REST API is done (Plan 041). All individual layers work — stores,
services, models, flows, API routes. But nothing proves the full chain works
end-to-end: onboard → train → hindcast → skill → forecast → API. Phase 11
is the capstone that validates this before deployment.

Alongside the e2e test, the user identified a systemic weakness: existing
tests check structure and counts more than actual computed values. The
metric unit tests (`test_metrics.py`) are strong (analytical solutions),
but the service-level tests that call them create perfect-match data and
only assert `len(scores) > 0` instead of `CRPS ≈ 0, NSE ≈ 1`.

### Two goals

1. **E2e test**: Single test proving the full pipeline works with real BAFU
   data, asserting on real numbers at every stage
2. **Test quality hardening**: Add value assertions to existing tests where
   they currently only check structure/counts

---

## Part A: End-to-End Test

### A1. Build realistic fixture data

**Constraint**: BAFU LINDAS serves only real-time observations (latest value
per parameter). Historical time series are not available via the public API —
that's exactly why we need the operational deployment running and ingesting
data over time.

**Deviation from v0-scope §E1**: §E1 specifies Tier 2 fixtures using "Real
CAMELS-CH" data. This is not feasible because (a) CAMELS-CH provides daily
resolution only — our pipeline needs hourly observations, and (b) BAFU LINDAS
serves real-time data only — no historical archive. Plan 043 replaces the
CAMELS-CH approach with deterministic, hydrologically realistic synthetic
data. v0-scope §E1 should be updated to reflect this. When the operational
deployment has accumulated 6+ months of real observations, re-record the
fixture with actual data and tighten assertions accordingly.

**Approach**: Generate deterministic, hydrologically realistic synthetic data
for the 7 reference stations. This is NOT arbitrary random numbers — it uses
real BAFU station characteristics (from `stations.toml`) with realistic:
- **Seasonal discharge patterns**: sinusoidal base flow with snowmelt peak
  (May-June for Alpine stations like Andermatt, flatter for lowland Basel)
- **Diurnal variation**: ±5-10% of base flow
- **Realistic magnitudes**: Andermatt/Reuss ~5-30 m³/s, Basel/Rhein ~500-1500 m³/s
  (from published BAFU annual reports)
- **Noise**: log-normal residuals scaled to each station's typical variability
- **QC edge cases**: inject 2-3 known anomalies per station for QC validation:
  - **Spike** (sudden unrealistic jump): triggers `spike` and `rate_of_change` checks
  - **Frozen sensor** (repeated identical values): triggers `frozen_sensor` check
  - **Range violation** (negative value or extreme outlier): triggers `range_check`
    and `gross_outlier` checks
  - Note: data gaps (missing rows) do not produce QC flags in the current
    implementation — the QC service skips `None` values. Gaps are tested
    separately via staleness/completeness checks in the observation service.

**Parameters**: Generate discharge for all 7 stations. For the 5 stations
with `measured_parameters` including `water_level` (2004, 2009, 2091, 2159,
2085 — per `stations.toml`), also generate water_level. This validates the
multi-parameter pipeline (v0-scope §A13).

Create `tests/fixtures/reference/generate_fixtures.py` — a deterministic
generator (seeded RNG) that produces `bafu_observations.parquet` with ~2
years of hourly data for 7 stations. ~200K rows (discharge for 7 + water_level
for 5). Committed to repo. The script has no live-data fallback — it is
purely synthetic.

**Why this works for the e2e test**: The model trains on the first year,
hindcasts the second year. Since the data has a known seasonal pattern, we
can assert that:
- Skill scores are positive (model captures seasonality)
- Forecasts track the seasonal signal
- QC correctly flags the injected anomalies
- Baselines match the seasonal mean/std

### A2. E2e test: full pipeline chain

Single integration test file: `tests/integration/test_e2e_pipeline.py`

**Approach**: The e2e test calls **service-layer functions directly**, not
Prefect `@flow`-decorated wrappers. This avoids requiring a Prefect server
in CI and keeps the test focused on correctness, not orchestration wiring.
Prefect fan-out, retries, and concurrency patterns are covered by the
scenario-based integration tests (§E3).

**Setup** (per-test, using a **dedicated** testcontainers PostgreSQL instance):
- Spin up a function-scoped testcontainer (separate from the session-scoped
  container used by other integration tests — the e2e test commits data
  across steps, so it cannot use the standard `db_connection` rollback
  fixture)
- Run Alembic migrations (using the direct connection URL, not via PgBouncer)
- Call `discover_models()` → `register_models()` to populate the `models`
  table with all entry-point-registered model types (LinearRegressionDaily,
  ClimatologyFallbackModel, PersistenceFallbackModel). This is the real
  registration path — uses `PgModelStore` on the test connection.
- Load `stations.toml` → store 7 stations via `PgStationStore` (status
  defaults to `ONBOARDING` from DB server_default)
- Load `bafu_observations.parquet` via `ReplayStationAdapter`
- Provide a `FakeWeatherReanalysisSource([])` for dependency injection into
  service functions. After the Step 0.25b/c fixes, this fake is never
  called when `past_dynamic_features` is empty — but the parameter must
  still be passed.
- Set `forecast_targets` explicitly when constructing `StationConfig` objects
  from `stations.toml` (e.g., `forecast_targets=frozenset(measured_parameters)`).
  **⚠ Silent failure**: `parse_stations_toml()` leaves `forecast_targets=None`.
  When `None`, `_run_onboarding()` silently skips baselines, flow regimes, and
  model assignments (steps 5–8). The e2e test must set this field or all
  downstream steps fail with confusing errors.

**Isolation**: The entire pipeline chain (steps 1-7) runs as a single test
function. The e2e test uses its own function-scoped testcontainer — NOT the
session-scoped `db_engine` from `tests/integration/conftest.py`. This avoids
two problems: (a) the session-scoped container's `db_connection` fixture
rolls back after each test, which would destroy data needed between steps,
and (b) if the e2e test commits to a shared container, its committed rows
become visible to subsequent tests through their transaction snapshot. A
dedicated container costs ~3s extra startup and guarantees zero interaction
with other tests. In CI, the e2e job runs separately anyway.

**CI placement**: This test runs in the **e2e** CI job (v0-scope §E5, < 5 min).
Requires a Docker-capable runner for testcontainers.

**Pipeline steps** (sequential, all on real DB):

1. **Onboard stations**: Ingest historical observations for 7 stations.
   Run QC. Compute climatological baselines. Create model assignments for
   LinearRegressionDaily (model was registered in setup).
   - Assert: 7 stations in DB with `status=ONBOARDING` (the e2e test does
     not call `update_station_status()` until Step 3 — the ONBOARDING →
     OPERATIONAL gate is enforced by the onboarding service's sequencing,
     not by the store which accepts any status unconditionally)
   - Assert: observations stored with `qc_status` in {QC_PASSED, QC_SUSPECT}
   - Assert: baselines computed (up to 366 day-of-year entries per station —
     366 when the synthetic period spans a leap year)
   - **⚠ Single-parameter limit**: `_run_onboarding()` currently uses
     `next(iter(forecast_targets))`, computing baselines for only one target
     parameter per station. Multi-parameter baselines (discharge + water_level)
     require iterating all `forecast_targets` — this is an implementation gap
     deferred beyond Plan 043.

2. **Aggregate observations to model time step**: Resample hourly
   observations to daily resolution before training. This step is a
   **prerequisite** — `LinearRegressionDaily` requires daily data
   (`supported_time_steps = {timedelta(hours=24)}`), but the fixture
   contains hourly observations.
   - Assert: daily DataFrame has ~365 rows per station-year (not ~8,760)
   - Assert: aggregation preserves the seasonal signal (daily mean tracks
     the hourly seasonal envelope)

   **⚠ Implementation gap**: No aggregation function exists in the codebase
   today. `assemble_station_training_data()` (`services/training_data.py`)
   passes raw observations through without resampling, and
   `assemble_station_operational_inputs()` (`services/operational_inputs.py`)
   does the same. **This must be built before the e2e test can work.** See
   new Layer 0.5 in the implementation steps below.

3. **Train model**: Train LinearRegressionDaily on the first year of data.
   Promote artifact to ACTIVE (auto-promote, per v0-scope §A7). Then
   transition all 7 stations from ONBOARDING → OPERATIONAL via
   `station_store.update_station_status()` — this mirrors
   `_run_onboarding()` step 8 which gates on ≥1 active artifact.
   - Assert: model artifact stored with `status=ACTIVE`
   - Assert: artifact integrity verified via `fetch_artifact()` (exercises
     the `_read_and_verify()` SHA-256 check — must not raise
     `ArtifactIntegrityError`)
   - Assert: all 7 stations now have `status=OPERATIONAL`

4. **Run hindcast**: Hindcast over evaluation period (second year) for each
   station.
   - Assert: hindcast forecasts stored (one per evaluation day per station)
   - Assert: ensemble has expected member count
   - Assert: all forecast values ≥ 0 (discharge is non-negative)

5. **Compute skills**: Compute CRPS, NSE, KGE, PBIAS, MAE (and all other
   metrics in the suite) for each station.
   - Assert: skill scores stored per lead time
   - Assert: CRPS > 0 (not perfect — real model on real data)
   - Assert: NSE > -1.0 (model is at least somewhat informative)
   - Assert: PBIAS is finite (not NaN — observations vary by construction)
   - Assert: all unconditional metric names present in stored scores (at
     minimum: `crps`, `nse`, `kge`, `pbias`, `mae`, `sharpness_p10_p90`,
     `sharpness_p25_p75`, `ensemble_range`). Threshold-conditional metrics
     (`bss_danger_*`, `pod_danger_*`, `far_danger_*`, `csi_danger_*`) are
     absent because onboarding does not insert thresholds.
     `peak_timing_error` is conditional on ≥1 observation exceeding p90.
   - Assert: sample_size matches expected hindcast count
   - Assert: freshness == CURRENT (i.e. `SkillFreshness.CURRENT`)
   - **⚠ §A5 deviation**: `compute_crpss()` exists in `metrics.py` but is not
     wired into `_compute_scores()`. CRPSss (v0-scope §A5) requires a
     reference CRPS from the climatology baseline. Wiring this is deferred to
     a follow-on plan. `crpss` is excluded from the e2e assertion set.

6. **Run forecast cycle**: Run operational forecast for the most recent date.
   After Layer 0.25 simplification, `LinearRegressionDaily` is purely
   autoregressive (lagged discharge only): `past_dynamic_features = ∅`,
   `future_dynamic_features = ∅`. The model needs only recent observations
   (past_targets) — no NWP forcing, no weather reanalysis. This means
   steps 1.1–1.5 are entirely irrelevant for v0a. The service layer
   (after Layer 0.25 fixes) correctly skips forcing fetch when
   `data_requirements.*_dynamic_features` is empty.
   - Assert: forecast stored with `status=RAW`
   - Assert: ensemble has `_N_MEMBERS` (50) members with
     `representation=MEMBERS`
   - Assert: forecast values are physically plausible (0 < Q < 10000 m³/s)

7. **Query API**: Use TestClient to hit the API endpoints.
   - `GET /api/v1/stations` → 7 stations
   - `GET /api/v1/stations/{id}` → station detail with thresholds
   - `GET /api/v1/stations/{id}/observations?...` → real observation values
   - `GET /api/v1/stations/{id}/forecasts` → forecast summaries
   - `GET /api/v1/forecasts/{id}` → forecast detail with ensemble
   - `GET /api/v1/health` → status=ok

**Key principles**:
- No mocks. Real DB, real observations, real model training, real skill
  computation. Assertions on actual values, not counts.
- **Fail loud, not silent.** If a component is missing (model not registered,
  forcing unavailable for a model that needs it, aggregation method unknown
  for a parameter), the test must raise an error — never silently skip or
  return empty results. We want to discover implementation gaps now, not
  have them masked by silent fallbacks.

### A3. Performance baseline

Record per-step wall-clock times using `time.perf_counter()` and emit
structlog events following the `{entity}.{action}` naming convention (e.g.,
`e2e.step_completed` with `step_name` and `duration_ms` keyword fields) per
logging.md. The e2e test conftest (or a session-scoped autouse fixture) must
call `configure_test_logging()` so structlog is initialized and events are
emitted to test output.

**Baseline workflow**:
1. First local run: if `performance_baseline.json` does not exist, write it
   and log a warning ("baseline created — commit to repo before CI can
   compare").
2. Developer commits the file to `tests/fixtures/reference/`.
3. CI reads the committed file. If absent (e.g., first CI run on a branch
   before the baseline is committed), log a warning and **skip** the
   comparison — do NOT fail.
4. On subsequent CI runs, compare against baseline and **warn** (not fail) on
   >50% regression per step (v0-scope §E7). This is not a hard assertion —
   CI hardware varies — but the comparison mechanism must exist for
   regression detection to be meaningful.
5. The baseline file is updated manually when legitimate performance changes
   occur (e.g., after the Layer 0.25 model rewrite).

**⚠ §E7 deviation**: v0-scope §E7 specifies instrumentation of **Flow 1**
steps specifically. This baseline measures service-layer step times for the
e2e chain (onboard → train → hindcast → skill → forecast), not the Prefect
Flow 1 orchestration overhead. Real Flow 1 instrumentation (including Prefect
task scheduling latency) requires a Prefect-enabled integration test, which
is deferred to the scenario-based tests (§E3 follow-on).

---

## Part B: Test Quality Hardening

### B1. Skill service: assert computed values, not just presence

**File**: `tests/unit/services/skill/test_service.py`

**Current** (line 219): `assert len(scores) > 0` after creating hindcasts
and observations with identical values (perfect forecast: all members = 10,
all observations = 10).

**⚠ Constant-observation trap**: When all observations are the same value
(e.g., 10.0), `compute_nse` returns `nan` (denominator `SS_tot = 0`) and
`compute_kge` returns `nan` (`std_obs = 0`). Asserting `nse ≈ 1.0` or
`kge ≈ 1.0` on constant data would fail. CRPS and MAE are safe (CRPS = 0,
MAE = 0 for perfect forecasts regardless of observation variance).

**Fix**: Change the existing perfect-forecast test to use **varying**
observations (e.g., `value = 8 + i` for `i in range(5)`) with matching
ensemble members (all members equal the observation for each timestep).
Then assert:
```python
crps_scores = [s for s in scores if s.metric == "crps"]
for s in crps_scores:
    assert s.score == pytest.approx(0.0, abs=0.01)

nse_scores = [s for s in scores if s.metric == "nse"]
for s in nse_scores:
    assert s.score == pytest.approx(1.0, abs=0.01)

kge_scores = [s for s in scores if s.metric == "kge"]
for s in kge_scores:
    assert s.score == pytest.approx(1.0, abs=0.01)

mae_scores = [s for s in scores if s.metric == "mae"]
for s in mae_scores:
    assert s.score == pytest.approx(0.0, abs=0.01)

pbias_scores = [s for s in scores if s.metric == "pbias"]
for s in pbias_scores:
    assert s.score == pytest.approx(0.0, abs=0.01)
```

**Add a second test** with imperfect forecasts (members have spread around
the observation, observations must vary): assert CRPS > 0, NSE < 1, and
verify CRPS improves when forecast is closer to observation.

### B2. Skill service: add "worse than climatology" test

Create hindcasts with random values far from observations. **Observations
must vary** (e.g., a sinusoidal pattern) — constant observations produce
`NSE = nan`, not a negative number. Assert NSE < 0 (worse than climatology
baseline). This catches bugs where the metric formula is inverted or the
denominator is wrong.

### B3. QC tests: use realistic BAFU value ranges

**File**: `tests/unit/services/test_qc.py`

Replace arbitrary ranges (`0-100`) with realistic BAFU ranges:
- Discharge for small Alpine river: 0.1 - 50 m³/s (e.g., Andermatt/Reuss)
- Discharge for large river: 100 - 2000 m³/s (e.g., Basel/Rhein)
- Rate of change: ≤ 20% of current value per hour (typical)

Add parametrized tests across multiple realistic scenarios:
```python
@pytest.mark.parametrize("value,expected_pass", [
    (15.0, True),     # normal flow
    (0.05, True),     # very low flow (valid)
    (-0.1, False),    # negative (instrument error)
    (5000.0, False),  # extreme outlier for small basin
])
def test_range_check_realistic(value, expected_pass): ...
```

### B4. Forecast combination: assert ensemble statistical properties

**File**: `tests/unit/services/test_forecast_combination.py`

After pooling two ensembles, assert:
- Combined member count == sum of input member counts
- Combined ensemble mean ≈ weighted average of input means (weighted by
  member count)
- Combined ensemble range (min, max) spans both input ranges

~~Combined ensemble variance ≥ min(input variances)~~ — **removed**: this is
not mathematically guaranteed. Pooling two identical distributions doubles the
sample but preserves variance, so the bound is not strict. Pooling
distributions with different locations increases variance via between-group
spread, but this is data-dependent.

After BMA combination, assert:
- BMA mean ≈ weighted sum of model means
- Higher-weight model contributes more members (verify sampling proportionality)
- BMA member count equals `_BMA_TARGET_MEMBERS` (100)

### B5. Alert checker: assert exceedance probability values

**File**: `tests/unit/services/test_alert_checker.py`

Current tests check `len(active) == 1`. Add assertions on the alert's
`trigger_probability` field — the exceedance probability should match
the fraction of ensemble members exceeding the threshold.

**⚠ Production code change required**: `alert_checker.py` `_process_results()`
currently hardcodes `trigger_probability=None` on all `Alert` objects. The
exceedance probability IS computed on `ExceedanceResult.exceedance_probability`
(via `_compute_exceedance()` in `alert_strategy.py`) but never copied to the
`Alert`. This fix must populate `trigger_probability` from the
`ExceedanceResult` before the test can assert on it. Add
`services/alert_checker.py` to scope.

### B6. Model tests: validate forecast plausibility

**File**: `tests/unit/models/test_linear_regression_daily.py`

After the Layer 0.25 rewrite to autoregressive, add a test that:
- Trains on a known autoregressive signal (e.g., slowly rising discharge
  ramp or sinusoidal seasonal pattern)
- Predicts the next 5 days from a known lookback window
- Asserts forecast mean is within the expected range of recent observations
- Asserts all 50 ensemble members are non-negative
- Asserts ensemble spread (std across members) is > 0 (not degenerate)

---

## Implementation steps

### Layer 0 — Fixture generation (deterministic, one-time)

**Step 0.1**: Create `tests/fixtures/reference/generate_fixtures.py` — a
deterministic generator with realistic seasonal/diurnal discharge patterns
for 7 BAFU stations. Generates `bafu_observations.parquet` (~200K rows,
2 years hourly, discharge + water_level). Commit the Parquet and the
generator script.

### Layer 0.25 — Simplify LinearRegressionDaily + fix service layer guards

The current `LinearRegressionDaily` requires `precipitation` and
`temperature` features in both `past_dynamic` and `future_dynamic`. This
is gratuitous for a v0a sample model whose purpose is to prove the pipeline
end-to-end. Making it purely autoregressive (lagged discharge only)
eliminates the need for weather forcing data in the e2e test fixture.

Additionally, two service functions unconditionally require weather data
even for models that don't need it — this is a real bug that affects any
future model with empty `*_dynamic_features`.

**Step 0.25a**: Rewrite `LinearRegressionDaily` to be autoregressive.
- `past_dynamic_features` → `frozenset()` (empty)
- `future_dynamic_features` → `frozenset()` (empty)
- `_build_feature_vector()`: use lagged discharge from `past_targets`
  (tail `_LOOKBACK` rows) instead of precipitation/temperature
- `train()`: build sliding-window features from discharge time series only
- `predict()`: use lagged discharge from `inputs.data.past_targets`.
  **⚠ valid_times**: The current code derives valid times from
  `future_dyn["timestamp"]` (line 213), which will be empty after this
  change. Replace with generated valid times:
  `[issue_time + (i + 1) * time_step for i in range(horizon)]`
  using `inputs.issue_time` and `inputs.time_step`.
- Ensemble generation (residual bootstrap, 50 members, non-negative clip)
  stays unchanged

**Step 0.25b**: Fix `assemble_station_training_data()` in
`services/training_data.py`. The unconditional guard at lines 94–97
(`if not weather_sources: return None`) must become conditional:
```python
required_features = list(model.data_requirements.past_dynamic_features)
if required_features:
    weather_sources = station_store.fetch_weather_sources(station_id)
    if not weather_sources:
        log.warning("training_data.no_weather_sources", ...)
        return None
    # ... existing forcing fetch logic (lines 99–120) stays inside this branch ...
else:
    forcing_df = pl.DataFrame(schema={"timestamp": pl.Datetime("us", "UTC")})
```
**⚠ Ordering note**: `past_targets_df` is created at line 146, *after* the
forcing section. The `else` branch cannot reference it — use a schema-only
empty DataFrame instead. The downstream code sets
`future_dynamic_df = forcing_df.clear()` (line 149), which works correctly
on an empty DataFrame — the result is a zero-row, timestamp-only DataFrame.

**⚠ Signature note**: The function signature keeps `forcing_source:
WeatherReanalysisSource` as a **mandatory** parameter. Do NOT make it
optional — all 5 callers (`assemble_group_training_data`, model onboarding
service, `train_models` flow, `onboard_model` flow, unit tests) pass it
today. Changing the signature would ripple across the codebase. Callers
that deal with autoregressive models pass a fake/stub that is never
invoked (the guard prevents the call). This matches `operational_inputs.py`
which also keeps `forcing_source` mandatory.

**⚠ §I2 coverage note**: After this fix, the non-empty `past_dynamic_features`
branch (forcing injection path) is still exercised by the existing 5 unit
tests in `test_training_data.py` — they all use fakes with precipitation and
temperature. Add one new unit test (in Step 0.25b scope) that calls the
function with a model having `past_dynamic_features = frozenset()` and
asserts it returns `StationTrainingData` with empty `past_dynamic` DataFrame
(not `None`). This ensures both branches have explicit test coverage.

Pattern: match `operational_inputs.py` (lines 162–182) which already guards
on `if past_dynamic_features:`.

**Step 0.25c**: Apply conditional guards in `services/hindcast.py` — **three
functions** need changes:

1. **`run_station_hindcast()`** (lines 287–292): Guard
   `forcing_source.fetch_reanalysis()` with `if required_features:`. In the
   `else` branch, set `all_forcing = []`. Currently, when `required_features`
   is empty, the fetch returns `[]`, then `_raw_forcing_to_dataframe([], ...)`
   returns `None`, and every hindcast step is silently skipped — this must
   fail loudly, not silently.

2. **`run_group_hindcast()`** (lines 475–480): **Same bug, same fix.** Guard
   `forcing_source.fetch_reanalysis()` with `if required_features:`. In the
   `else` branch, set `all_forcing_flat = []`. v0a only uses station models,
   but leaving this broken means any future group model with empty
   `past_dynamic_features` silently produces zero hindcast results.

3. **`_assemble_hindcast_inputs()`** (lines 172–179): The `forcing_df is None`
   guard currently returns `None` (skipping the step) even when the model
   needs no forcing. Fix: condition the guard on `required_features` being
   non-empty. When `required_features` is empty, set
   `forcing_df = pl.DataFrame(schema={"timestamp": pl.Datetime("us", "UTC")})`
   directly — the existing split logic at lines 185–186 (`past_dynamic =
   forcing_df.filter(...)`, `future_dynamic = forcing_df.filter(...)`) then
   produces two zero-row DataFrames, which is correct for an autoregressive
   model. `_assemble_hindcast_inputs` is called **only** from the two
   functions above — no other callers.

**⚠ Fail-loud principle**: After this fix, the hindcast still fails loudly
when a model that *needs* forcing data cannot get it (the existing guard
fires when `required_features` is non-empty but no forcing is found). The
fix only removes the false positive where models with no forcing needs
were silently skipped.

**Step 0.25d**: Rewrite `tests/unit/models/test_linear_regression_daily.py`.
All 8 existing tests break because they construct data with `precipitation`
and `temperature` columns. Replace with discharge-only fixtures. Test:
- Training on known linear signal → verify coefficients are sensible
- Prediction produces 50 non-negative members
- Insufficient lookback data → ValueError
- Round-trip serialize/deserialize unchanged

**Step 0.25e**: Verify model onboarding smoke test
(`services/model_onboarding.py`) works — `_make_synthetic_station_training_data()`
generates columns from `data_requirements.*_dynamic_features`, which will now
be empty. The model's `train()` no longer reads those columns, so the smoke
test should pass. Run existing integration test to confirm.

### Layer 0.5 — Observation time-step aggregation (NEW — prerequisite for e2e)

**Step 0.5a**: Implement `resample_to_time_step()` in
`src/sapphire_flow/services/training_data.py`. This function takes a Polars
DataFrame of observations at sub-daily resolution and resamples to a target
`time_step` (e.g., daily). Aggregation method is parameter-dependent and
must be looked up from the `parameters` table's `aggregation_method` column
(TEXT NOT NULL, CHECK IN `('sum', 'mean')`) — not hardcoded. The Alembic
seed data already defines the correct method for all 10 canonical parameters
(e.g., discharge → mean, precipitation → sum).

**Signature**: `resample_to_time_step(df, time_step, aggregation_methods)`
where `aggregation_methods: dict[str, AggregationMethod]` maps parameter
name → method. The caller fetches this mapping once (via `ParameterStore`
or a direct query) and passes it in. This avoids duplicating the canonical
mapping in application code and ensures correctness when new parameters are
added to the `parameters` table.

Insert the call after `_observations_to_dataframe()` returns and before the
data is packed into `StationTrainingData`. Guard: if the observation cadence
already matches `time_step`, skip resampling.

**Step 0.5b**: Apply the same resampling in
`assemble_station_operational_inputs()`
(`src/sapphire_flow/services/operational_inputs.py`) for inference-time
consistency. The model must receive the same temporal resolution at inference
as it saw during training.

**⚠ Signature ripple**: Adding `aggregation_methods` to
`assemble_station_training_data()` ripples to 5 production call sites
(`assemble_group_training_data` internal call, `model_onboarding.py`,
`train_models.py`, `onboard_model.py`, `run_forecast_cycle.py` for
operational_inputs) and 11 test call sites across `test_training_data.py`
and `test_operational_inputs.py`. The flow-layer callers also need
`ParameterStore` injected. **Recommended approach**: use an optional
parameter `aggregation_methods: dict[str, AggregationMethod] | None = None`
with a hardcoded v0 fallback dict (discharge→MEAN, water_level→MEAN,
precipitation→SUM, temperature→MEAN). This limits blast radius — existing
callers work unchanged, only the e2e test and new code pass the real dict.

**Step 0.5c**: Unit tests for the resampling function: hourly→daily with
known input/output, idempotent on already-daily data, correct aggregation
method per parameter.

### Layer 1 — E2e test (depends on 0.1, 0.25, 0.5)

**Step 1.1**: Write `tests/integration/test_e2e_pipeline.py` — the full
chain test with real data and value assertions.

### Layer 2 — Test quality hardening (parallel with Layer 1, except Step 2.5)

**Step 2.1**: Harden `test_service.py` — assert metric values for perfect
and imperfect forecasts, add "worse than climatology" test.

**Step 2.2**: Harden `test_qc.py` — realistic BAFU value ranges, parametrized
tests.

**Step 2.3**: Harden `test_forecast_combination.py` — ensemble statistical
property assertions.

**Step 2.4**: Harden `test_alert_checker.py` — exceedance probability
assertions.

**Step 2.5**: Harden `test_linear_regression_daily.py` — forecast
plausibility with known relationship. **Depends on Step 0.25d** (which
rewrites the same test file). This is the only Layer 2 step with a
sequential dependency on Layer 0.25 — all other Layer 2 steps (2.1–2.4)
can proceed in parallel with Layer 1 as described.

---

## Files to create

| File | Purpose |
|---|---|
| `tests/fixtures/reference/generate_fixtures.py` | Deterministic fixture generator with realistic seasonal patterns |
| `tests/integration/test_e2e_pipeline.py` | Full pipeline chain test |
| `tests/fixtures/reference/performance_baseline.json` | Per-step wall-clock baseline for §E7 regression detection |

## Files to modify

| File | Change |
|---|---|
| `tests/fixtures/reference/bafu_observations.parquet` | Replace empty placeholder with generated synthetic data |
| `src/sapphire_flow/models/linear_regression_daily.py` | Rewrite to autoregressive (lagged discharge only, no weather features) — Layer 0.25a |
| `src/sapphire_flow/services/training_data.py` | Guard forcing fetch on `past_dynamic_features` non-empty (Layer 0.25b); add `resample_to_time_step()` (Layer 0.5a) |
| `src/sapphire_flow/services/hindcast.py` | Guard forcing fetch on `past_dynamic_features` non-empty in `run_station_hindcast`, `run_group_hindcast`, and `_assemble_hindcast_inputs` (Layer 0.25c) |
| `src/sapphire_flow/services/operational_inputs.py` | Add matching resampling for inference-time consistency (Layer 0.5b) — NB: forcing guards already correct here |
| `tests/unit/models/test_linear_regression_daily.py` | Rewrite for autoregressive model (Layer 0.25d); add forecast plausibility test (Layer 2.5) |
| `tests/unit/services/test_training_data.py` | Add resampling unit tests (Layer 0.5c); add test for empty `past_dynamic_features` guard (Layer 0.25b) |
| `tests/unit/services/skill/test_service.py` | Vary observations (fix NaN trap), assert metric values, add imperfect/bad tests |
| `tests/unit/services/test_qc.py` | Realistic BAFU ranges, parametrized scenarios |
| `tests/unit/services/test_forecast_combination.py` | Ensemble property assertions (member count, mean, range) |
| `tests/unit/services/test_alert_checker.py` | Exceedance probability assertions |
| `src/sapphire_flow/services/alert_checker.py` | Populate `trigger_probability` from `ExceedanceResult.exceedance_probability` in `_process_results()` (prerequisite for B5 test hardening) |
| `pyproject.toml` | Add `pytest-timeout` to dev dependencies |
| `.github/workflows/ci.yml` | Replace e2e job stub with real `pytest tests/integration/test_e2e_pipeline.py` invocation |
| `docs/v0-scope.md` | Update §E1 Tier 2 description to reflect synthetic fixture approach |

## Deferred

- NWP fixture recording (`--source nwp`) — v0b dependency, gridded path not
  wired. **⚠ §E1 deviation**: v0-scope §E1 Tier 2 specifies "recorded
  ICON-CH2-EPS for 3-5 cycles" alongside CAMELS-CH. Plan 043 replaces BOTH
  components with synthetic discharge+water_level data. NWP fixtures are
  deferred to v0b alongside the gridded NWP pipeline (GridExtractor).
- Full 9-scenario matrix from §E3 — **v0 requirement**, not deferred
  indefinitely. Follow-on plan after Plan 043 lands the happy-path e2e. The
  happy-path e2e covers scenario 1 ("Normal cycle") and scenario 8 ("Full
  onboarding → forecast") from §E3. Remaining 7 scenarios require dedicated
  test setup. **Action required**: create a follow-on plan (or add Phase 11b
  to v0-scope §H) so these scenarios have a named home.
- Golden answer files — generated after the e2e test proves the pipeline
  produces stable outputs. **⚠ §E5 deviation**: v0-scope §E5 specifies
  "golden answer comparison" as part of the e2e CI job. Deferred until the
  pipeline produces stable, reproducible outputs across runs.
- §E6 adapter recording tool (`sapphire_flow.tools.record_fixtures`) — builds
  on top of the synthetic generator when real BAFU data becomes available

## Verification

```bash
# Generate fixture data (deterministic, run once)
uv run python tests/fixtures/reference/generate_fixtures.py

# Run autoregressive model tests (Layer 0.25)
uv run pytest tests/unit/models/test_linear_regression_daily.py -v
uv run pytest tests/integration/test_model_onboarding_integration.py -v

# Run aggregation unit tests (Layer 0.5)
uv run pytest tests/unit/services/test_training_data.py -v -k resample

# Run e2e test
uv run pytest tests/integration/test_e2e_pipeline.py -v --timeout=300

# Run hardened tests
uv run pytest tests/unit/services/skill/test_service.py -v
uv run pytest tests/unit/services/test_qc.py -v
uv run pytest tests/unit/services/test_forecast_combination.py -v
uv run pytest tests/unit/services/test_alert_checker.py -v
uv run pytest tests/unit/models/test_linear_regression_daily.py -v

# Full suite — no regressions
uv run pytest tests/ -x --timeout=300
```
