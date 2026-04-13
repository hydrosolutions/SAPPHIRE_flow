---
status: DONE
created: 2026-04-13
completed: 2026-04-13
scope: implementation — observation-based alerting (Phase 6 completion)
depends_on: []
---

# 027 — Observation Alerting (Phase 6 Completion)

## Context

Phase 6 (observation ingest) is ~90% complete. The ingest flow fetches observations from BAFU LINDAS, stores them, and runs Stage 1 QC — all working with 618 unit + 189 integration tests passing.

The single remaining gap is **observation-based alerting** (Flow 2 steps 2.8–2.10). The stub at `ingest_observations.py:322-326` logs `"not_implemented"` when `enable_observation_alerts` is true. All infrastructure exists: `AlertSource.OBSERVATION` enum, `AlertStore` (Pg + Fake), `Alert` dataclass, `StationThreshold` type, `enable_observation_alerts` config flag. The flag defaults to `false`, so this is opt-in.

Per v0-scope.md §A8c: observation alerts are "simple value-vs-threshold" — no ensembles, no probabilities, no strategies. Much simpler than the existing forecast alerting.

## Files to modify

| File | Change |
|------|--------|
| `src/sapphire_flow/services/observation_alert_checker.py` | **New** — service function |
| `src/sapphire_flow/flows/ingest_observations.py` | Wire alert_store + replace stub |
| `tests/unit/services/test_observation_alert_checker.py` | **New** — service unit tests |
| `tests/unit/flows/test_ingest_observations.py` | Add flow-level alert tests |

## Implementation

### Task 1: Create `observation_alert_checker.py` service

**File**: `src/sapphire_flow/services/observation_alert_checker.py`

Single public function:

```python
_OBS_LOOKBACK = timedelta(hours=24)

def check_observation_alerts(
    station_params: set[tuple[StationId, str]],
    obs_store: ObservationStore,
    station_store: StationStore,
    alert_store: AlertStore,
    now: UtcDatetime,
) -> None
```

**Important**: Processing must be per-station, not per-`(station_id, parameter)`.
`Alert` has no `parameter` field — the upsert de-duplicates on `(station_id,
alert_level, source)`. If we processed per-parameter, evaluating discharge could
resolve a "yellow" alert that water_level just raised (or vice versa). This
mirrors the same design in forecast alerting (`_process_results()` in
`alert_checker.py:269-342`).

**Structure** — group by station, then evaluate all parameters:

1. Group `station_params` by `station_id` → `dict[StationId, set[str]]`
2. For each station:
   a. `thresholds = station_store.fetch_thresholds(station_id)`
   b. If no thresholds, skip station
   c. Build `level_parameters: dict[str, set[str]]` — maps danger_level → set of configured parameters (from thresholds)
   d. For each parameter in the station's set:
      - Get latest QC-passed value: `obs_store.fetch_observations(station_id, parameter, start=now - _OBS_LOOKBACK, end=now, qc_status=QcStatus.QC_PASSED)`, take max-timestamp. `_OBS_LOOKBACK = timedelta(hours=24)` as a module-level constant.
      - If no QC-passed observation, skip parameter (can't evaluate)
      - Track as evaluated parameter
      - For each threshold matching this parameter: if `value > threshold.value` (ABOVE direction, per §A8a), add danger_level to exceeded set. Store the trigger_value for that level.
   e. **Raise**: For each exceeded level, `alert_store.upsert_alert(Alert(...))` with `source=AlertSource.OBSERVATION`, `trigger_probability=None`, `trigger_value=<one of the exceeding values>`, `model_ids=()`, `alert_model_strategy=None`
   f. **Resolve**: `alert_store.fetch_active_alerts(station_id, source=AlertSource.OBSERVATION)` — for each active alert: resolve only if (1) `alert_level` NOT in exceeded set, AND (2) all configured parameters for that level (from `level_parameters`) were evaluated. If not all configured params were evaluated, defer resolution (log and skip). This prevents premature resolution when only some parameters had data.

**Reuse from existing codebase**:
- Follow the raise/resolve pattern from `alert_checker.py:_process_results()` (lines 269-342)
- Use same `Alert` construction pattern (lines 302-322)
- `FakeAlertStore.upsert_alert()` already de-duplicates on `(station_id, alert_level, source)` for non-resolved alerts

**Design decisions**:
- No `DangerLevelDefinition` dependency — observation alerting doesn't need `trigger_probability`, `min_trigger_duration`, or `min_resolve_duration` (those are for ensemble/forecast hysteresis). Just compare value vs threshold.
- Processing is per-station (not per-parameter) — mirrors forecast alerting. Resolution requires all configured parameters for a danger level to be evaluated before resolving, preventing premature resolution when one parameter exceeds but another wasn't available.
- `trigger_value` stores one of the exceeding values when multiple parameters exceed the same level — the last-processed parameter's value. Acceptable for v0; the alert level is the actionable information.
- `_OBS_LOOKBACK` is a module-level constant (24h) — generous for BAFU automatic stations (10-min intervals). Sufficient for v0; can be made configurable if manual stations are added in v1.
- The `alert_level` field on `StationThreshold` matches the `alert_level` field on `Alert` — this is the join key.

### Task 2: Wire into `ingest_observations_flow`

**File**: `src/sapphire_flow/flows/ingest_observations.py`

Three changes:

1. **Add `alert_store` parameter** to flow signature (line 179):
   ```python
   alert_store: object = None,
   ```

2. **Add production setup** (after line 200, inside the `if station_store is None:` block):
   ```python
   alert_store = stores["alert_store"]
   ```

3. **Replace stub** (lines 322-326):
   ```python
   if deployment_config is not None and deployment_config.enable_observation_alerts:
       from sapphire_flow.services.observation_alert_checker import check_observation_alerts
       assert alert_store is not None
       check_observation_alerts(
           station_params=station_params,
           obs_store=obs_store,
           station_store=station_store,
           alert_store=alert_store,
           now=now,
       )
   else:
       log.debug("ingest.observation_alerts_disabled")
   ```

### Task 3: Unit tests for the service

**File**: `tests/unit/services/test_observation_alert_checker.py`

Using `FakeStationStore`, `FakeObservationStore`, `FakeAlertStore` from `tests/fakes/fake_stores.py`. Use `make_station_config` from `tests/conftest.py`.

Tests:
1. **`test_alert_raised_when_threshold_exceeded`** — store a threshold (e.g. danger_level="yellow", value=100.0), store a QC-passed observation with value=150.0, call checker, assert one RAISED alert with `source=OBSERVATION`, `trigger_value=150.0`, `alert_level="yellow"`
2. **`test_no_alert_when_below_threshold`** — value=50.0 < threshold=100.0, assert no alerts
3. **`test_alert_resolved_when_value_drops`** — pre-seed an active OBSERVATION alert at "yellow" level, store a QC-passed observation below threshold, call checker, assert alert is RESOLVED
4. **`test_no_thresholds_no_alerts`** — station with no thresholds, assert no crash, no alerts
5. **`test_no_qc_passed_observations_skips`** — only RAW observations exist, no QC_PASSED, assert no alerts
6. **`test_multiple_danger_levels`** — two thresholds (yellow=100, red=200), value=250 exceeds both, assert two RAISED alerts
7. **`test_multi_parameter_no_premature_resolution`** — station has "yellow" thresholds for both discharge AND water_level. Discharge exceeds, water_level does not. Assert "yellow" alert is RAISED (not resolved) because discharge still exceeds. This is the critical test for per-station (not per-parameter) processing.
8. **`test_resolution_deferred_when_parameter_not_evaluated`** — station has "yellow" threshold for both discharge and water_level. Only discharge is in `station_params` (water_level had no data). Discharge is below threshold. Pre-seed an active "yellow" alert. Assert alert is NOT resolved (resolution deferred because water_level was not evaluated).

### Task 4: Flow-level alert tests

**File**: `tests/unit/flows/test_ingest_observations.py`

Add 2 tests to `TestIngestObservationsFlow`:

1. **`test_observation_alert_raised_when_enabled`** — pass `deployment_config` with `enable_observation_alerts=True`, store a threshold, ingest an observation above it, pass `alert_store=FakeAlertStore()`, assert alert_store has a RAISED OBSERVATION alert
2. **`test_observation_alerts_disabled_by_default`** — pass `deployment_config=None` (default), ingest above-threshold obs, pass `alert_store=FakeAlertStore()`, assert no alerts raised

Need to construct a minimal `DeploymentConfig` for tests. Required fields: `max_retention_days: int` (use 600). The `enable_observation_alerts=True` overrides the default.

## Verification

```bash
# Run the new service tests
uv run pytest tests/unit/services/test_observation_alert_checker.py -v

# Run the updated flow tests
uv run pytest tests/unit/flows/test_ingest_observations.py -v

# Run full unit + integration suite for regressions
uv run pytest tests/unit/ -q
uv run pytest tests/integration/ -q

# Lint
uv run ruff check src/sapphire_flow/services/observation_alert_checker.py tests/unit/services/test_observation_alert_checker.py tests/unit/flows/test_ingest_observations.py
uv run ruff format src/sapphire_flow/services/observation_alert_checker.py tests/unit/services/test_observation_alert_checker.py tests/unit/flows/test_ingest_observations.py
```
