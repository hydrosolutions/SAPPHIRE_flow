---
status: DRAFT
created: 2026-03-26
scope: services + store + flows
depends_on: [003]
---

# 005 — Multi-target support in operational forecast service (Flow 1)

## Problem

The operational forecast service (`services/forecast.py`, not yet implemented) will
have the same single-ensemble consumption pattern as the hindcast service. When
`predict()` returns `dict[str, ForecastEnsemble]`, the forecast service must store
one forecast per parameter and route each to parameter-specific alert thresholds.

**Timing:** This is Phase 8 work (`v0-scope.md` §H). The forecast service doesn't
exist yet. This plan documents the pattern to follow when it is implemented, ensuring
consistency with the hindcast approach from plan 003.

---

## Changes

### Phase 1 — Forecast Service: Multi-Parameter Storage Loop

#### 1A. `src/sapphire_flow/services/forecast.py`

When `model.predict()` returns `dict[str, ForecastEnsemble]`, iterate and store
one `OperationalForecast` per parameter:

```python
ensembles, state = model.predict(...)
for param_name, ensemble in ensembles.items():
    forecast = OperationalForecast(
        id=ForecastId(uuid_factory()),
        station_id=station_id,
        model_id=model_id,
        ...
        ensemble=ensemble,
        created_at=clock(),
    )
    forecast_store.store_forecast(forecast)
```

Follow the same pattern established in plan 003's hindcast service (Phase 2A).

**Dependencies:** Plan 003 complete. Forecast service skeleton exists (Phase 8).

### Phase 2 — Alert Threshold Routing

#### 2A. `src/sapphire_flow/services/forecast.py`

Route each ensemble to its parameter-specific alert thresholds:

```python
if enable_forecast_alerts:  # v0-scope.md: default False
    for param_name, ensemble in ensembles.items():
        # Filter thresholds to this parameter
        param_thresholds = [t for t in thresholds if t.parameter == param_name]
        for threshold in param_thresholds:
            check_threshold(ensemble, threshold)  # v0: ABOVE direction only (§A8a)
```

**Notes:**
- `enable_forecast_alerts` flag (`v0-scope.md` §A8) governs whether alert checking runs
- v0 uses `ABOVE` direction only (`v0-scope.md` §A8a)
- If station has single-parameter forecasts, the loop degenerates to one iteration — correct

**Dependencies:** 1A.

### Phase 3 — Store Protocol

#### 3A. `src/sapphire_flow/protocols/stores.py` — `ForecastStore`

Add optional `parameter: str | None = None` to `fetch_latest_forecast()`:

```python
def fetch_latest_forecast(
    self,
    station_id: StationId,
    model_id: ModelId | None = None,
    parameter: str | None = None,       # NEW
) -> OperationalForecast | None:
    ...
```

**Return semantics for multi-parameter case:**
- `parameter="discharge"` → latest forecast for that parameter (unambiguous)
- `parameter=None` → latest forecast by `issued_at` regardless of parameter. With
  multi-parameter models, this returns whichever parameter was stored last in the same
  cycle. Callers that need a specific parameter MUST pass `parameter=`.

This matches the pattern from plan 003's `HindcastStore.fetch_hindcasts(parameter=)`.

**Dependencies:** None.

#### 3B. `src/sapphire_flow/store/forecast_store.py` — `PgForecastStore`

Add `parameter` filter. When `parameter is not None`, join to the ensemble data
and filter on `ensemble.parameter`.

**Dependencies:** 3A.

#### 3C. `tests/fakes/fake_stores.py` — `FakeForecastStore`

Add `parameter: str | None = None` to `fetch_latest_forecast()`. Filter:

```python
and (parameter is None or f.ensemble.parameter == parameter)
```

**Dependencies:** 3A.

### Phase 4 — ForecastOutputQualityChecker

No changes needed — already operates per-ensemble. Confirmed in plan 003 review.

---

## Phase 5 — Test Plan

#### 5A. Tests that break without changes

None — the forecast service doesn't exist yet. All tests are new.

#### 5B. New tests needed

**`tests/unit/services/test_forecast.py`**

1. `TestMultiParameterForecast` — `test_two_parameters_stored`:
   - Use a fake model returning `dict` with `"discharge"` and `"water_level"`.
   - Assert `FakeForecastStore` contains 2 forecasts per cycle.
   - Assert filtering by `parameter="discharge"` returns only discharge.

2. `TestMultiParameterForecast` — `test_single_parameter_backward_compat`:
   - Use a model returning a single-key dict.
   - Assert 1 forecast stored per cycle.

3. `TestAlertRouting` — `test_alerts_routed_per_parameter`:
   - Two parameters, only one exceeds threshold.
   - Assert alert raised only for the exceeding parameter.

4. `TestAlertRouting` — `test_alerts_skipped_when_disabled`:
   - `enable_forecast_alerts=False`.
   - Assert no alert checking occurs.

**`tests/unit/store/test_forecast_store.py`**

5. `TestParameterFilter` — `test_fetch_latest_with_parameter_filter`:
   - Store forecasts for two parameters.
   - Fetch with `parameter="discharge"` → correct result.
   - Fetch with `parameter=None` → returns latest regardless.

**`tests/fakes/`**

6. `FakeForecastStore` parameter filtering verification.

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/services/forecast.py` | Multi-parameter storage loop + alert routing | 1A, 2A |
| `src/sapphire_flow/protocols/stores.py` | Add `parameter` filter to `ForecastStore.fetch_latest_forecast` | 3A |
| `src/sapphire_flow/store/forecast_store.py` | Add `parameter` filter to `PgForecastStore` | 3B |
| `tests/fakes/fake_stores.py` | Add `parameter` filter to `FakeForecastStore` | 3C |
| `tests/unit/services/test_forecast.py` | New: multi-parameter + alert routing tests | 5B |
| `tests/unit/store/test_forecast_store.py` | New: parameter filter tests | 5B |

---

## Dependency Graph

```
1A (forecast service storage loop)
  └─ 2A (alert routing)

3A (ForecastStore Protocol)
  └─ 3B (PgForecastStore)
  └─ 3C (FakeForecastStore)

5B (all tests — depends on 1A, 2A, 3A, 3B, 3C)
```

Phases 1+2 and 3 can proceed in parallel. Phase 5 depends on both.

---

## Guardrails

- Follow plan 003's hindcast pattern exactly for storage loop
- Run `uv run pytest` after each phase
- After Phase 3: verify `isinstance(FakeForecastStore(), ForecastStore)` passes
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)

---

## Open Items

1. **`fetch_latest_forecast` with `parameter=None`** — returns latest by `issued_at` regardless
   of parameter. This is potentially confusing for multi-parameter models. Consider whether
   the API should require `parameter` (no default) or return `list[OperationalForecast]` for
   all parameters. Defer to Phase 8 implementation.

2. **No DB migration needed** — `parameter` is already inside `ForecastEnsemble` (stored in
   JSONB or separate `forecast_values` rows). The filter in `PgForecastStore` queries the
   existing data structure.
