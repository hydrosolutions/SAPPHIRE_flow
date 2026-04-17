---
status: DONE
created: 2026-04-17
scope: Phase 3/8 v0b — Wire gridded NWP path (Flow 1 steps 1.1–1.4) into forecast cycle
depends_on: ["021"]
---

# Plan 045 — Wire Gridded NWP Path into Forecast Cycle (v0b)

## Context

v0a is complete. All Plan 021 components (MeteoSwissNwpAdapter, ExactExtractGridExtractor,
ZarrNwpGridStore, basin_avg_to_records converter, ReplayNwpAdapter) are built and
unit-tested in isolation, but **not connected** into the forecast cycle. The forecast
cycle currently raises `NotImplementedError("v0b grid path not yet wired")` at
`run_forecast_cycle.py:107` when the adapter returns `GriddedForecast`. A dead helper
`_station_weather_sources()` at line 142 returns `[]`, meaning no `StationWeatherSource`
objects reach the adapter.

This plan wires the gridded NWP path (Flow 1 steps 1.1–1.4) into the forecast cycle
and completes the remaining Plan 021 deliverables (recording tool stub, 6 unapplied doc
updates).

## Scope

Six steps: config plumbing, test fakes, forecast cycle wiring, gridded-path tests,
recording tool completion, remaining doc updates.

**Not in scope**: task.map parallelisation, GroupForecastModel support, pooled forecast
combination (§A8e), live NWP fixture recording (interactive/manual task).

**Breaking change**: Step 5 replaces `--cycles` (int) with `--cycle-time` (repeatable
ISO 8601). Any scripts referencing `--source nwp --cycles N` must be updated.

## Step dependency graph

```
Step 1 (config) ──┐
                   ├──> Step 3 (wire flow) ──> Step 4 (tests) ──> Step 6 (docs)
Step 2 (fakes) ───┘                       └──> Step 5 (recording tool)
```

Steps 1 + 2 are independent (parallel). Step 3 depends on both. Steps 4, 5 are
independent of each other (parallel). Step 6 is last.

---

## Step 1: Add `nwp_grid_archive_base_path` to DeploymentConfig

**Problem**: `_fetch_nwp_task` needs to know where to archive Zarr grids. `config.toml`
has `adapters.weather_forecast.archive_base_path = "/data/nwp_grids"` (line 361), but
`load_config()` strips the `adapters` section (line 263 of `config/deployment.py`). The
path is unreachable from the flow.

**Change**: Extract `archive_base_path` from `data["adapters"]["weather_forecast"]`
*before* popping `adapters` in `load_config()`. Store it as
`nwp_grid_archive_base_path: str | None = None` on `DeploymentConfig`.

**Files**:
- `src/sapphire_flow/config/deployment.py` — add field + extract in `load_config()`

**Exit gate**: `load_config("config.toml").nwp_grid_archive_base_path == "/data/nwp_grids"`

---

## Step 2: Create FakeNwpGridStore and FakeGridExtractor

**Problem**: Testing the gridded path in the forecast cycle requires in-memory fakes.
None exist.

**FakeNwpGridStore** (in `tests/fakes/fake_stores.py`):
- `_archives: dict[tuple[str, UtcDatetime], GriddedForecast]`
- `archive_count: int = 0` for test assertions
- `exception: Exception | None = None` (for error injection — `archive()` raises if set)
- `archive()` stores in dict, increments `archive_count`, returns a real `Path` instance
  (e.g. `Path(f"/fake/{forecast.nwp_source}/{forecast.cycle_time}")`)
- `load()` retrieves from dict or raises `StoreError`

**FakeGridExtractor** (in `tests/fakes/fake_adapters.py`):
- Constructor accepts `result: dict[StationId, BasinAverageForecast | ElevationBandForecast] | None`
  and `exception: Exception | None = None` (for error injection in tests)
- `extract()`: if `exception` is set, raises it; otherwise returns the canned result.
  Tracks `call_count` and captures `last_configs: list[StationWeatherSource]` for
  test assertions

**Conformance tests** in `tests/fakes/test_fakes.py`:
- `isinstance(FakeNwpGridStore(), NwpGridStore)` passes
- `isinstance(FakeGridExtractor(), GridExtractor)` passes

**Files**:
- `tests/fakes/fake_stores.py`
- `tests/fakes/fake_adapters.py`
- `tests/fakes/test_fakes.py`

---

## Step 3: Wire the gridded path into the forecast cycle

This is the core step. Four sub-changes in `run_forecast_cycle.py`:

### 3a. Delete `_station_weather_sources` helper + simplify `fetch_forecasts` call

Delete `_station_weather_sources` (line 142-143) — it returns `[]`, a dead placeholder.
Replace with batch-prefetch in the flow function (Step 3b).

Also update the `fetch_forecasts()` call (lines 99-101) — the list comprehension through
`_station_weather_sources` is no longer needed. After the `station_configs` type change
(Step 3c), the call simplifies to:
```python
result = adapter.fetch_forecasts(station_configs, cycle_time)
```

### 3b. Batch-prefetch weather sources + basins

Add to the batch-prefetch section (after line 301):
```python
all_weather_sources: dict[StationId, list[StationWeatherSource]] = {
    s.id: station_store.fetch_weather_sources(s.id)
    for s in operational
}
flat_weather_configs = [ws for sources in all_weather_sources.values() for ws in sources]

# Build station→basin map for GridExtractor
station_basins: dict[StationId, Basin] = {}
for s in operational:
    if s.basin_id is not None:
        basin = basin_store.fetch_basin(s.basin_id)
        if basin is not None:
            station_basins[s.id] = basin
        else:
            log.warning("nwp.basin_not_found", station_id=s.id, basin_id=s.basin_id)
```

**Note**: `flat_weather_configs` contains configs for ALL NWP sources. The gridded path
in Step 3c filters by `result.nwp_source` before passing to `GridExtractor.extract()`
— `ExactExtractGridExtractor` does not filter internally.

**Note**: Phase B (line 409) currently calls `station_store.fetch_weather_sources(sid)`
per-station independently. After this change, that data is already available in
`all_weather_sources`. Replace the Phase B per-station call with a lookup into
`all_weather_sources[sid]` to eliminate N redundant DB queries per cycle.

### 3c. Expand `_fetch_nwp_task` signature + implement gridded path

**Type change on existing parameter**: Change `station_configs: list[StationConfig]` to
`station_configs: list[StationWeatherSource]`. The point-based path does not reference
`station_configs` after `fetch_forecasts()`, so this is safe for existing callers. The
`fetch_forecasts()` call (Step 3a) simplifies to a direct pass-through.

New parameters (all optional):
- `grid_store: NwpGridStore | None = None` (use Protocol type, import under `TYPE_CHECKING`)
- `grid_extractor: GridExtractor | None = None` (use Protocol type, import under `TYPE_CHECKING`)
- `station_basins: dict[StationId, Basin] | None = None` (pre-resolved)
- `grid_archive_base_path: str | None = None`

Replace the `NotImplementedError` block (line 107-108) with:
```python
if isinstance(result, GriddedForecast):
    # Step 1.2: Archive raw grid to Zarr (non-fatal — archiving is auxiliary)
    if grid_store is not None and grid_archive_base_path is not None:
        archive_t0 = time.perf_counter()
        try:
            grid_store.archive(result, Path(grid_archive_base_path))
        except Exception as exc:
            log.warning("nwp.archive_failed", nwp_source=result.nwp_source,
                        cycle_time=str(cycle_time), error=str(exc))
        else:
            log.info("nwp.archive_completed", nwp_source=result.nwp_source,
                     duration_ms=round((time.perf_counter() - archive_t0) * 1000, 1))

    # Step 1.3: Extract basin averages
    if grid_extractor is None:
        log.warning("nwp.extraction_skipped", reason="grid_extractor_not_configured")
        return None

    # Filter configs to only those matching this grid's NWP source
    configs_for_source = [
        ws for ws in station_configs if ws.nwp_source == result.nwp_source
    ]
    if not configs_for_source:
        log.warning("nwp.extraction_skipped", reason="no_matching_sources",
                    nwp_source=result.nwp_source)
        return None

    extract_t0 = time.perf_counter()
    try:
        extracted = grid_extractor.extract(
            grid=result.values,
            configs=configs_for_source,
            basins=station_basins or {},
            cycle_time=cycle_time,
            nwp_source=result.nwp_source,
        )
    except Exception as exc:
        log.error("extraction.failed", nwp_source=result.nwp_source,
                  cycle_time=str(cycle_time), error=str(exc))
        return None

    # Step 1.4: Convert to records and store
    all_records = []
    for station_id, forecast in extracted.items():
        if isinstance(forecast, BasinAverageForecast):
            all_records.extend(basin_avg_to_records(station_id, forecast, clock, uuid4))
        else:
            # ElevationBandForecast — deferred to v1 (Nepal)
            log.warning("nwp.unknown_forecast_type",
                        station_id=str(station_id),
                        type=type(forecast).__name__)

    if all_records:
        weather_forecast_store.store_weather_forecasts(all_records)

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log.info(
        "nwp.fetch_completed",
        records_stored=len(all_records),
        stations=len(extracted),
        extraction_duration_ms=round((time.perf_counter() - extract_t0) * 1000, 1),
        duration_ms=duration_ms,
    )
    return cycle_time
```

**Note**: The existing `try/except Exception` around `adapter.fetch_forecasts()` (lines
98–105) must be preserved — only the `NotImplementedError` block inside the success path
is replaced.

### 3d. Update flow function: new parameters + task submission

Add to `run_forecast_cycle_flow` signature (use `object` for Prefect serialization,
consistent with existing store parameters):
- `grid_store: object | None = None`
- `grid_extractor: object | None = None`

Production setup block: instantiate defaults if None:
```python
if grid_store is None:
    from sapphire_flow.store.zarr_nwp_grid_store import ZarrNwpGridStore
    grid_store = ZarrNwpGridStore()
if grid_extractor is None:
    from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
        ExactExtractGridExtractor,
    )
    grid_extractor = ExactExtractGridExtractor()
```

Update `_fetch_nwp_task.submit()` call (line 322-328):
```python
nwp_future = _fetch_nwp_task.submit(
    adapter=adapter,
    station_configs=flat_weather_configs,  # was: operational
    cycle_time=resolved_cycle_time,
    weather_forecast_store=weather_forecast_store,
    clock=clock,
    grid_store=grid_store,
    grid_extractor=grid_extractor,
    station_basins=station_basins,
    grid_archive_base_path=config.nwp_grid_archive_base_path,
)
```

**Files**:
- `src/sapphire_flow/flows/run_forecast_cycle.py`

**Exit gate**: `NotImplementedError` removed. When adapter returns `GriddedForecast`,
the task archives (non-fatal on failure — catches `Exception as exc`, not just
`StoreError`; logs `error=str(exc)`), extracts (guarded by empty-`configs_for_source`
check; catches `Exception as exc`, not just `ExtractionError`; logs `error=str(exc)`),
converts (with `isinstance` dispatch), and stores.
Point-based path unchanged. Logging events follow entity conventions: archive/fetch
events and task-level extraction guards under `nwp.*` (avoids duplicate
`extraction.completed` — the extractor already emits that event internally);
extraction internals under `extraction.*` (per canonical table in `logging.md`).
`nwp.unknown_forecast_type` reuses the existing event name (line 123) with
matching kwargs (`type=`, not `forecast_type=`) for the `ElevationBandForecast` skip
path. Per-step D6 timing: `nwp.archive_completed` (step 1.2),
`nwp.fetch_completed` with `extraction_duration_ms` (step 1.3 timing folded in),
`nwp.fetch_completed` `duration_ms` (overall task).

---

## Step 4: Add gridded-path tests

Nine new test methods in `tests/unit/flows/test_run_forecast_cycle.py`:

1. **`test_gridded_nwp_happy_path`**: `FakeWeatherForecastSource` returns a
   `GriddedForecast` (small synthetic xarray Dataset with dims
   `member × valid_time × latitude × longitude`). `FakeGridExtractor` returns
   pre-canned `BasinAverageForecast` per station. `FakeNwpGridStore` records archive
   call. Config must set `nwp_grid_archive_base_path` to a non-None value. Verify:
   - `grid_store.archive()` was called (archive count = 1)
   - `grid_extractor.extract()` was called (call_count = 1)
   - `grid_extractor.last_configs` contains only sources matching the grid's
     `nwp_source` (verifies NWP source filter)
   - Weather forecast records stored in `FakeWeatherForecastStore`
   - Cycle proceeds to Phase B, `result.stations_succeeded >= 1`

2. **`test_gridded_nwp_no_grid_extractor`**: Adapter returns `GriddedForecast` but
   `grid_extractor=None`. Verify task returns None → cycle aborts with "NWP fetch
   failed" error.

3. **`test_gridded_nwp_extraction_error`**: `FakeGridExtractor` raises
   `ExtractionError`. Verify the error is caught, logged, and task returns None →
   cycle aborts with failure.

4. **`test_gridded_nwp_archive_failure_non_fatal`**: `FakeNwpGridStore.archive()`
   raises `StoreError`. Verify extraction still proceeds (records stored successfully)
   — archiving failure does not abort the cycle.

5. **`test_gridded_nwp_point_path_unchanged`**: Adapter returns `dict[StationId,
   WeatherForecastResult]` (point-based path). Verify existing behavior is preserved
   after `station_configs` type change to `list[StationWeatherSource]` — regression
   guard.

6. **`test_gridded_nwp_elevation_band_skipped`**: `FakeGridExtractor` returns only
   `ElevationBandForecast` objects (no `BasinAverageForecast`). Verify:
   - `nwp.unknown_forecast_type` warning logged per station
   - Zero weather forecast records stored
   - Task still returns `cycle_time` (not `None` — deferred types are skipped, not
     errors)

7. **`test_gridded_nwp_source_filtering`**: Seed two `StationWeatherSource` entries
   with different `nwp_source` values (e.g., `"icon_ch2_eps"` and `"other_source"`).
   `FakeWeatherForecastSource` returns a `GriddedForecast` with
   `nwp_source="icon_ch2_eps"`. Verify `FakeGridExtractor.last_configs` contains only
   the matching source.

8. **`test_gridded_nwp_archive_skipped_when_no_path`**: `grid_store` provided but
   `config.nwp_grid_archive_base_path = None`. Verify `grid_store.archive()` is NOT called
   but extraction proceeds and records are stored.

9. **`test_gridded_nwp_no_matching_sources`**: Seed one `StationWeatherSource` with
   `nwp_source="other_source"`. `FakeWeatherForecastSource` returns a `GriddedForecast`
   with `nwp_source="icon_ch2_eps"`. Verify `configs_for_source` is empty →
   `nwp.extraction_skipped` logged with `reason="no_matching_sources"` → task returns
   `None` → cycle aborts.

**Test helpers needed**:
- `_make_gridded_forecast(cycle_time)` → builds minimal `xr.Dataset` with expected
  dimensions + wraps in `GriddedForecast` (reference:
  `tests/unit/store/test_zarr_nwp_grid_store.py::_make_forecast()`)
- Extend `_build_station_and_stores()` with two new keyword-only parameters:
  - `extraction_type: SpatialRepresentation = SpatialRepresentation.POINT` (preserves
    existing test behavior — all 5+ existing call sites are unchanged)
  - `basin_store: FakeBasinStore | None = None` (required when `extraction_type` is
    `BASIN_AVERAGE`; `None` default preserves existing call sites)
  When `BASIN_AVERAGE` is requested, the helper must also:
  - Set `basin_id` on the `StationConfig`
  - Seed a matching `Basin` in `basin_store` (raise `ValueError` if `basin_store is None`)
  - Seed the `StationWeatherSource` with `extraction_type=BASIN_AVERAGE`
  - Pass `seed_nwp=False` — the gridded path in Phase A provides NWP records; pre-seeding
    point-based records creates noise in the store
- `FakeGridExtractor` must capture its `configs` argument as `last_configs` for
  assertion (in addition to existing `call_count`)
- Build `FakeGridExtractor` result with matching station IDs and a valid
  `BasinAverageForecast` (Polars DataFrame with `valid_time, parameter, member_id,
  value` columns)

**Files**:
- `tests/unit/flows/test_run_forecast_cycle.py`

**Exit gate**: All 9 new tests pass. All existing tests still pass (no regressions
from `_build_station_and_stores()` extension — default `extraction_type` and
`basin_store=None` preserve existing call sites).

---

## Step 5: Complete `record_fixtures.py --source nwp` stub

**Problem**: Lines 293-297 log a warning and exit. The adapter and store are
constructed but never used.

**Change**: Replace the stub with a working recording loop:
1. Replace `--cycles` (int) with `--cycle-time` (repeatable ISO 8601 datetime strings)
2. `--cycle-time` is required (no auto-discovery — `MeteoSwissNwpAdapter` has no
   list-cycles API; STAC queries require a known timestamp)
3. For each cycle: `adapter.fetch_forecasts([], cycle_time)` →
   `store.archive(forecast, output_dir)`.
   **Note**: passing `[]` as `station_configs` works because `MeteoSwissNwpAdapter`
   returns the full grid regardless of configs — add a code comment documenting this
   as a MeteoSwiss-specific convention (point-based adapters would silently no-op)
4. Log archived path and cycle metadata

**Files**:
- `src/sapphire_flow/tools/record_fixtures.py`

**Exit gate**: `--source nwp --cycle-time 2026-04-17T00:00:00Z` attempts a real fetch
(requires network). Code compiles and is not a warning-only stub.

---

## Step 6: Apply remaining Plan 021 doc updates

Six updates that were not applied:

| # | File | Section | Change |
|---|------|---------|--------|
| 1 | `docs/v0-scope.md` | §A11 (line 189-197) | Remove v0a pre-extracted simplification. v0 uses gridded NWP. Steps 1.2–1.4 active. Preserve Compatibility sentence. |
| 2 | `docs/v0-scope.md` | §A12 (line 199-210) | Replace "SMN station observations" with "CAMELS-CH basin-averaged gridded data". Add plan 013 supersession note. Preserve v1 ERA5-Land sentence. |
| 3 | `docs/v0-scope.md` | Flow 1 table (line 25) | Replace "v0a: point weather forecast data" with gridded NWP active language. Preserve steps 1.5/1.9/1.10 text. |
| 4 | `docs/spec/types-and-protocols.md` | GriddedForecast.values (line 2275) | Fix dimension comment: `time × parameter × y × x` → `member × valid_time × latitude × longitude; weather parameters are data variables, not a dimension coordinate` |
| 5 | `docs/v0-scope.md` | §I1, §I2, §A3, §H, Deferred table | Collapse v0a/v0b language for NWP. Update NWP lateness fallback from "v0b or v1" to "v1" with urgency note. |
| 6 | `docs/spec/types-and-protocols.md` | Store Protocols section | Add `NwpGridStore` Protocol definition (exists in code at `protocols/stores.py:725` but was never added to the spec — unapplied Plan 021 deliverable) |

**Files**:
- `docs/v0-scope.md`
- `docs/spec/types-and-protocols.md`

**Exit gate**: No remaining references to "v0a" as current state in NWP contexts.

---

## Critical files (implementation)

| File | Steps |
|------|-------|
| `src/sapphire_flow/config/deployment.py` | 1 |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | 3 |
| `src/sapphire_flow/tools/record_fixtures.py` | 5 |
| `tests/fakes/fake_stores.py` | 2 |
| `tests/fakes/fake_adapters.py` | 2 |
| `tests/fakes/test_fakes.py` | 2 |
| `tests/unit/flows/test_run_forecast_cycle.py` | 4 |
| `docs/v0-scope.md` | 6 |
| `docs/spec/types-and-protocols.md` | 6 |

## Existing code to reuse (not create)

| Component | File | Used in |
|-----------|------|---------|
| `ExactExtractGridExtractor` | `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py` | Step 3 (production default) |
| `ZarrNwpGridStore` | `src/sapphire_flow/store/zarr_nwp_grid_store.py` | Step 3 (production default), Step 5 |
| `MeteoSwissNwpAdapter` | `src/sapphire_flow/adapters/meteoswiss_nwp.py` | Step 5 |
| `basin_avg_to_records` | `src/sapphire_flow/preprocessing/converters.py` | Step 3 (already imported) |
| `point_forecast_to_records` | `src/sapphire_flow/preprocessing/converters.py` | Step 3 (already imported) |
| `FakeWeatherForecastSource` | `tests/fakes/fake_adapters.py` | Step 4 (already supports GriddedForecast) |
| `FakeBasinStore` | `tests/fakes/fake_stores.py` | Step 4 |
| `FakeStationStore.store_weather_source()` | `tests/fakes/fake_stores.py` | Step 4 |

## Exit gates

**Code**:
- [ ] `DeploymentConfig.nwp_grid_archive_base_path` populated from `config.toml`
- [ ] `FakeNwpGridStore` satisfies `NwpGridStore` Protocol
- [ ] `FakeGridExtractor` satisfies `GridExtractor` Protocol; captures `last_configs`
- [ ] `NotImplementedError("v0b grid path not yet wired")` removed
- [ ] `_station_weather_sources` dead helper deleted
- [ ] Weather sources batch-prefetched via `station_store.fetch_weather_sources()`
- [ ] Phase B per-station `fetch_weather_sources()` replaced with `all_weather_sources` lookup (no duplicate DB queries)
- [ ] Station→basin map built for `GridExtractor.extract()`
- [ ] GriddedForecast path: archive → extract → convert → store
- [ ] NWP source filter applied before extraction (`configs_for_source`)
- [ ] Empty `configs_for_source` guarded explicitly (log + return `None`)
- [ ] Extraction exception catch is `except Exception` (not just `ExtractionError`)
- [ ] Archive exception catch is `except Exception` (not just `StoreError`)
- [ ] Existing `try/except Exception` around `adapter.fetch_forecasts()` preserved
- [ ] `ElevationBandForecast` results logged via `nwp.unknown_forecast_type` (reuses existing event) and skipped (v1 deferred)
- [ ] `station_configs` retyped to `list[StationWeatherSource]` in task signature
- [ ] `fetch_forecasts()` call simplified (no list comprehension shim)
- [ ] Logging: archive/fetch/extraction-guard events under `nwp.*`, extraction internals under `extraction.*`
- [ ] Logging: `grid_extractor is None` logged at WARNING via `nwp.extraction_skipped` (not ERROR)
- [ ] Logging: no duplicate `extraction.completed` — extractor emits it; task folds timing into `nwp.fetch_completed`
- [ ] Logging: `except Exception as exc` (not bare) with `error=str(exc)` in both catch blocks
- [ ] D6 per-step timing: `nwp.archive_completed` (1.2), `nwp.fetch_completed` with `extraction_duration_ms` (1.3), `nwp.fetch_completed` `duration_ms` (overall)
- [ ] Point-based path still works (existing tests pass)
- [ ] 9 new gridded-path tests pass (happy path, no extractor, extraction error, archive failure non-fatal, point-path regression, elevation band skipped, source filtering, archive skipped when no path, no matching sources)
- [ ] `_build_station_and_stores()` extended with backward-compatible `extraction_type` and `basin_store` parameters
- [ ] Recording tool `--source nwp` is functional (not a warning-only stub)
- [ ] Recording tool `--cycles` → `--cycle-time` breaking change documented
- [ ] Lint clean (`ruff check`, `ruff format`)

**Documentation**:
- [ ] v0-scope.md §A11 updated (v0a pre-extracted simplification removed)
- [ ] v0-scope.md §A12 updated (SMN → CAMELS-CH, plan 013 superseded)
- [ ] v0-scope.md Flow 1 table updated (gridded NWP active)
- [ ] types-and-protocols.md GriddedForecast.values dimension comment fixed
- [ ] v0-scope.md §I1, §I2, §A3, §H, Deferred table: v0a/v0b language collapsed for NWP
- [ ] types-and-protocols.md: `NwpGridStore` Protocol added (unapplied Plan 021 deliverable)

## Verification

1. `uv run pytest tests/fakes/test_fakes.py -v` — conformance tests pass
2. `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -v` — all tests pass (existing + 9 new)
3. `uv run pytest tests/unit/config/ -v` — config loading tests pass
4. `uv run ruff check && uv run ruff format --check` — lint clean
5. Full suite: `uv run pytest tests/ -x` — no regressions
