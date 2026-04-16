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

### A1. Record real BAFU fixture data

The recording tool (`tools/record_fixtures.py`) works. The current Parquet
is a synthetic placeholder. Record real data:

```bash
uv run python -m sapphire_flow.tools.record_fixtures \
    --source bafu \
    --start 2024-01-01T00:00:00+00:00 \
    --end 2025-12-31T23:59:59+00:00 \
    --output tests/fixtures/reference/
```

This produces `bafu_observations.parquet` with ~2 years of hourly discharge
for 7 BAFU stations. ~120K rows, <5MB. Committed to the repo alongside the
existing `stations.toml`.

**Why 2 years**: The LinearRegressionDaily model needs a training period
(default 1 year) plus a hindcast evaluation period (≥6 months for seasonal
coverage). 2 years gives 1 year training + 1 year evaluation.

### A2. E2e test: full pipeline chain

Single integration test file: `tests/integration/test_e2e_pipeline.py`

**Setup** (per-test, using testcontainers PostgreSQL):
- Run Alembic migrations
- Load `stations.toml` → store 7 stations via `PgStationStore`
- Load `bafu_observations.parquet` via `ReplayStationAdapter`

**Pipeline steps** (sequential, all on real DB):

1. **Onboard stations**: Ingest historical observations for 7 stations.
   Run QC. Compute climatological baselines. Assign LinearRegressionDaily
   model.
   - Assert: 7 stations in DB with `status=OPERATIONAL`
   - Assert: observations stored with `qc_status` in {QC_PASSED, QC_SUSPECT}
   - Assert: baselines computed (365 day-of-year entries per station)

2. **Train model**: Train LinearRegressionDaily on the first year of data.
   - Assert: model artifact stored with `status=ACTIVE`
   - Assert: artifact SHA-256 hash matches stored bytes

3. **Run hindcast**: Hindcast over evaluation period (second year) for each
   station.
   - Assert: hindcast forecasts stored (one per evaluation day per station)
   - Assert: ensemble has expected member count
   - Assert: all forecast values ≥ 0 (discharge is non-negative)

4. **Compute skills**: Compute CRPS, NSE, KGE, MAE for each station.
   - Assert: skill scores stored per lead time
   - Assert: CRPS > 0 (not perfect — real model on real data)
   - Assert: NSE > -1.0 (model is at least somewhat informative)
   - Assert: sample_size matches expected hindcast count
   - Assert: freshness == CURRENT

5. **Run forecast cycle**: Run operational forecast for the most recent date.
   - Assert: forecast stored with `status=RAW`
   - Assert: ensemble representation matches model output
   - Assert: forecast values are physically plausible (0 < Q < 10000 m³/s)

6. **Query API**: Use TestClient to hit the API endpoints.
   - `GET /api/v1/stations` → 7 stations
   - `GET /api/v1/stations/{id}` → station detail with thresholds
   - `GET /api/v1/stations/{id}/observations?...` → real observation values
   - `GET /api/v1/stations/{id}/forecasts` → forecast summaries
   - `GET /api/v1/forecasts/{id}` → forecast detail with ensemble
   - `GET /api/v1/health` → status=ok

**Key principle**: No mocks. Real DB, real observations, real model training,
real skill computation. Assertions on actual values, not counts.

### A3. Performance baseline

Record per-step wall-clock times and log them. Not a hard assertion (CI
hardware varies), but a structlog event with `step_name` and `duration_ms`
for each pipeline stage. Establishes a baseline for regression detection
(v0-scope.md §E7).

---

## Part B: Test Quality Hardening

### B1. Skill service: assert computed values, not just presence

**File**: `tests/unit/services/skill/test_service.py`

**Current** (line 219): `assert len(scores) > 0` after creating hindcasts
and observations with identical values (perfect forecast: all members = 10,
all observations = 10).

**Fix**: Assert actual metric values for the perfect-forecast case:
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
```

**Add a second test** with imperfect forecasts (members have spread around
the observation): assert CRPS > 0, NSE < 1, and verify CRPS improves when
forecast is closer to observation.

### B2. Skill service: add "worse than climatology" test

Create hindcasts with random values far from observations. Assert NSE < 0
(worse than climatology baseline). This catches bugs where the metric
formula is inverted or the denominator is wrong.

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
- Combined ensemble mean ≈ weighted average of input means
- Combined ensemble variance ≥ min(input variances) (pooling can't reduce spread)
- Quantiles of combined ensemble bracket the input quantiles

After BMA combination, assert:
- BMA mean ≈ weighted sum of model means
- Higher-weight model contributes more members (verify sampling proportionality)

### B5. Alert checker: assert exceedance probability values

**File**: `tests/unit/services/test_alert_checker.py`

Current tests check `len(active) == 1`. Add assertions on the alert's
`trigger_probability` field — the exceedance probability should match
the fraction of ensemble members exceeding the threshold.

### B6. Model tests: validate forecast plausibility

**File**: `tests/unit/models/test_linear_regression_daily.py`

The current test trains on synthetic Gaussian data. Add a test that:
- Trains on a simple known relationship (Q = a*P + b with noise)
- Predicts with known forcing
- Asserts forecast mean is within expected range
- Asserts all members are non-negative

---

## Implementation steps

### Layer 0 — Fixture recording (manual, one-time)

**Step 0.1**: Record real BAFU data via the recording tool. Run locally.
Commit the updated `bafu_observations.parquet` to the repo.

### Layer 1 — E2e test (depends on 0.1)

**Step 1.1**: Write `tests/integration/test_e2e_pipeline.py` — the full
chain test with real data and value assertions.

### Layer 2 — Test quality hardening (parallel with Layer 1)

**Step 2.1**: Harden `test_service.py` — assert metric values for perfect
and imperfect forecasts, add "worse than climatology" test.

**Step 2.2**: Harden `test_qc.py` — realistic BAFU value ranges, parametrized
tests.

**Step 2.3**: Harden `test_forecast_combination.py` — ensemble statistical
property assertions.

**Step 2.4**: Harden `test_alert_checker.py` — exceedance probability
assertions.

**Step 2.5**: Harden `test_linear_regression_daily.py` — forecast
plausibility with known relationship.

---

## Files to create

| File | Purpose |
|---|---|
| `tests/integration/test_e2e_pipeline.py` | Full pipeline chain test |

## Files to modify

| File | Change |
|---|---|
| `tests/fixtures/reference/bafu_observations.parquet` | Replace synthetic with real BAFU data |
| `tests/unit/services/skill/test_service.py` | Assert metric values, add imperfect/bad tests |
| `tests/unit/services/test_qc.py` | Realistic BAFU ranges, parametrized scenarios |
| `tests/unit/services/test_forecast_combination.py` | Ensemble property assertions |
| `tests/unit/services/test_alert_checker.py` | Exceedance probability assertions |
| `tests/unit/models/test_linear_regression_daily.py` | Forecast plausibility assertions |

## Deferred

- NWP fixture recording (`--source nwp`) — v0b dependency, gridded path not wired
- Full 9-scenario matrix from §E3 — add incrementally after the happy-path e2e works
- Golden answer files — generated after the e2e test proves the pipeline produces stable outputs

## Verification

```bash
# Record real data (manual, run once)
uv run python -m sapphire_flow.tools.record_fixtures --source bafu \
    --start 2024-01-01T00:00:00+00:00 --end 2025-12-31T23:59:59+00:00

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
