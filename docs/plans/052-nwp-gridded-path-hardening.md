# Plan 052 — Flow 1 gridded-NWP path hardening

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: Plan 045 DONE (gridded NWP integration landed 2026-04-17).
**Scope**: Close the correctness and robustness gaps surfaced by the 2026-04-18
audit in the gridded-NWP path added by Plan 045. Covers GridExtractor
out-of-extent handling, naive-datetime safety, lazy instantiation, Zarr archive
atomic-swap crash safety, and missing failure-mode tests. No scope expansion —
does not add `task.map` parallelisation, `GroupForecastModel` support, or
pooled-forecast combination (those remain v0b remainders in `MEMORY.md`).

---

## Context

### Why now

Plan 045 wired gridded NWP (ICON-CH2-EPS) into Flow 1 steps 1.1–1.4. The
happy-path tests (9 new tests) green, but a targeted audit of the path found
five concrete risks that become active the moment real operational data flows
through the pipeline. Three are HIGH (silent data corruption, deployment-time
startup failure, crash-recovery footgun). Two are MED (subtle correctness,
noisy instantiation).

### Findings (from audit)

1. **HIGH — silent NaN propagation from non-intersecting basin polygons**
   `src/sapphire_flow/adapters/exact_extract_grid_extractor.py:126`:
   `exactextract` returns `NaN` for a polygon that does not intersect the
   raster extent. The code casts via `float(...)` and writes the NaN into
   `WeatherForecastRecord.value` with no guard. The existing `ExtractionError`
   at line 79 only fires when the *basin dict* is empty — a spatially
   out-of-bounds polygon passes validation and yields NaN silently. Downstream
   `_pivot_nwp_records` in `services/operational_inputs.py` builds a Polars
   DataFrame that carries NaN into the model. No test covers this case.
2. **HIGH — `ExactExtractGridExtractor` constructed unconditionally at startup**
   `src/sapphire_flow/flows/run_forecast_cycle.py:353–358`:
   production startup always instantiates `ZarrNwpGridStore` and
   `ExactExtractGridExtractor` even if `nwp_grid_archive_base_path` is `None`.
   The heavy optional deps (`exactextract`, `rioxarray`, `geopandas`) become
   *mandatory* — a minimal deployment without them crashes on import at
   startup rather than at extraction time.
3. **MED — Zarr atomic-swap not crash-safe**
   `src/sapphire_flow/stores/zarr_nwp_grid_store.py:51–55`:
   the rename sequence `zarr → old`, `tmp → zarr`, `rmtree old` is not atomic
   across a process crash between lines 53 and 55. On next run `.zarr.old`
   lingers and the swap logic wraps it again, meaning stale backups persist
   indefinitely. Test `test_overwrite_existing_archive` only covers a clean
   re-run.
4. **MED — naive-datetime silent UTC coercion**
   `exact_extract_grid_extractor.py:33–35`:
   if `xarray` returns a naive `valid_time` coordinate (e.g., from a Zarr
   archive written without timezone metadata, or a GRIB2 file that carries
   local time), the code silently localises as UTC. If the input was actually
   CET/CEST, forecast valid-times are 1–2 hours wrong. The existing test
   `test_valid_time_utc` only checks the output dtype.
5. **MED — test coverage is golden-path heavy**
   The 9 new tests cover happy-path, archive failure non-fatal, no-extractor,
   extraction error, elevation-band skip, source filtering, archive-skipped,
   point-path-unchanged, no-matching-sources. Missing: polygon outside grid
   extent (finding 1), partial-member grid (<21 members), and re-run
   idempotency (duplicate `store_weather_forecasts` calls).

### Non-goals

- `task.map` parallelisation (v0b, separate plan).
- `GroupForecastModel` support (v0b, separate plan).
- Pooled-forecast combination (v0b, separate plan).
- Bias correction / ensemble calibration (needs 6–12 months archive first).

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Fail-fast on NaN from GridExtractor**: raise `ExtractionError` with the offending basin and grid extent in the message when `exactextract` returns NaN for any member. Do not let NaN reach `WeatherForecastRecord`. | `WeatherForecastRecord.value: float` with no NaN-handling contract; silently feeding NaN into Polars means the model receives garbage, not a missing-data signal. A loud failure at extraction time is preferable — it identifies a config mistake (wrong basin polygon) immediately. |
| D2 | **Lazy GridExtractor instantiation**: construct `ZarrNwpGridStore` and `ExactExtractGridExtractor` only when `nwp_grid_archive_base_path` is set AND a gridded source is active in the current cycle. | Removes the hard dep on `exactextract`/`rioxarray`/`geopandas` at import time. A deployment running only legacy BAFU point-forecast adapters no longer needs the gridded-NWP stack installed. |
| D3 | **Atomic Zarr swap via `os.replace` + cleanup-on-next-run**: replace the three-step rename with `os.replace(tmp_path, zarr_path)` (atomic on same filesystem). Add a startup-time `_cleanup_stale_old_paths` sweep that removes `.zarr.old` siblings if they exist. | `os.replace` is POSIX-atomic; no mid-swap window. The cleanup sweep addresses any pre-existing `.zarr.old` from older code versions. |
| D4 | **Require tz-aware datetimes in GridExtractor**: raise `ValueError` if `valid_time` comes in as naive. Do not silently coerce. | Matches the project convention (`UtcDatetime` NewType, `ensure_utc()` at boundaries). A silent coerce at the boundary violates the "parse, don't validate" rule. |
| D5 | **Cover three new failure modes with unit tests**: polygon-outside-extent (raises), partial-member grid (behaviour: skip missing members with a structured log event, do not fail), re-run idempotency (second call overwrites first; no duplicates in the store). | Maps 1:1 to the three audit gaps. Each test is small and isolated; no fixture regeneration required. |

---

## Task list

### T1 — Fail-fast on NaN from exactextract

**File**: `src/sapphire_flow/adapters/exact_extract_grid_extractor.py`

1. After the `float(...)` cast at line 126, check `math.isnan(value)`. If true,
   collect the offending `(basin_id, member, valid_time)` tuple.
2. After the member loop, if any NaN was collected, raise `ExtractionError`
   with a message including the basin ID, the raster extent (`raster.bounds`),
   and the basin polygon bounding box. Suggest the likely cause (polygon does
   not intersect the raster).
3. Update the existing `ExtractionError` docstring / class comment in
   `exceptions.py` to reflect the new trigger.

**Exit**: a basin polygon placed entirely outside the ICON-CH2-EPS grid raises
`ExtractionError` with actionable context; no NaN reaches `WeatherForecastRecord`.

### T2 — Lazy GridExtractor instantiation

**File**: `src/sapphire_flow/flows/run_forecast_cycle.py`

1. Remove the unconditional `ZarrNwpGridStore(...)` and
   `ExactExtractGridExtractor()` construction at lines ~353–358.
2. Wrap the construction in a helper `_build_grid_components(config)` that
   returns `(store, extractor)` only when `config.nwp_grid_archive_base_path`
   is set AND at least one gridded source is configured for the cycle.
   Otherwise return `(None, None)`.
3. Move the import of `ExactExtractGridExtractor` and `ZarrNwpGridStore`
   inside the helper (deferred import) so modules that don't need them don't
   trigger `exactextract`/`rioxarray` imports at module load time.
4. Update callers to tolerate `None` for both — they already have branches
   for the no-extractor path from Plan 045.

**Exit**: `python -c "import sapphire_flow.flows.run_forecast_cycle"` succeeds
on a Python environment without `exactextract` installed, as long as no
gridded cycle is executed.

### T3 — Atomic Zarr swap + stale-old cleanup

**File**: `src/sapphire_flow/stores/zarr_nwp_grid_store.py`

1. Replace the three-step rename (lines 51–55) with a single
   `os.replace(tmp_path, zarr_path)`. This is POSIX-atomic on the same
   filesystem.
2. Add a module-level `_cleanup_stale_backups(base_path: Path)` that globs
   `*.zarr.old` under `base_path` and removes them. Call it at
   `ZarrNwpGridStore.__init__` time (once per process).
3. Add a warning-level structlog event if stale backups were found and cleaned
   (`nwp_grid_store.stale_backup_removed`).
4. Add a test `test_atomic_swap_leaves_no_stale_backup`: run two back-to-back
   writes, assert no `.zarr.old` directories exist after the second completes.

**Exit**: no `.zarr.old` sibling directories after any write; stale pre-existing
backups are cleaned at first instantiation with a log event.

### T4 — Reject naive `valid_time` in GridExtractor

**File**: `src/sapphire_flow/adapters/exact_extract_grid_extractor.py`

1. At the `_to_utc_datetime` helper (line 33), replace the "if naive, assume
   UTC" branch with `raise ValueError(f"valid_time {dt} is naive; adapters must
   provide tz-aware datetimes")`.
2. Document in the docstring: "naive datetimes are rejected — upstream
   adapters must call `ensure_utc()` before handing data to the extractor."
3. Update callers (NWP adapter `fetch_forecasts`) to verify they always produce
   tz-aware `valid_time` coordinates. Add an assertion at the boundary if not
   already present.

**Exit**: passing a naive datetime raises `ValueError`; no silent UTC coercion.

### T5 — New failure-mode tests

**File**: `tests/unit/adapters/test_exact_extract_grid_extractor.py`

1. `test_polygon_outside_grid_extent_raises`: construct a basin polygon at
   (10.0, 50.0) — well outside Switzerland — and a Swiss ICON raster. Call
   `extract_basin_averages`. Assert `ExtractionError` with "does not intersect"
   or equivalent in the message.
2. `test_partial_member_grid_skipped_with_log`: construct a raster where only
   10 of 21 members contain valid data (others NaN-filled at the source).
   Assert the extractor returns 10 member results and emits a log event
   `nwp_grid_extractor.members_skipped` with count=11.

**File**: `tests/unit/stores/test_zarr_nwp_grid_store.py`

3. `test_rerun_idempotency`: call `store.write_forecast(...)` with the same
   valid-time and init-time twice in sequence. Assert the second call
   overwrites the first; assert only one Zarr group exists on disk.

**Exit**: three new tests, all passing.

### T6 — Remove stale `TODO` marker

**File**: `src/sapphire_flow/flows/run_forecast_cycle.py`

1. Line 640: `# TODO: use merge_data_requirements()` — either implement
   `merge_data_requirements()` (not in scope for this plan) or convert the
   comment into a `# HACK:` with an explicit link to the follow-up plan
   tracking it. Recommendation: add a `HACK` referencing a new issue or
   v0b tracker. No behaviour change.

**Exit**: no dangling `TODO` without a tracking link in the live code path.

---

## Dependency graph

```json
{
  "stream-1": {
    "tasks": ["T1", "T2", "T3", "T4"],
    "parallel": "all four in parallel — independent files and responsibilities",
    "depends_on": []
  },
  "stream-2": {
    "tasks": ["T5"],
    "sequential": true,
    "depends_on": ["T1", "T3"]
  },
  "stream-3": {
    "tasks": ["T6"],
    "depends_on": []
  }
}
```

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `src/sapphire_flow/adapters/exact_extract_grid_extractor.py` | T1, T4 | Fail-fast on NaN; reject naive datetimes |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | T2, T6 | Lazy GridExtractor; clean up TODO |
| `src/sapphire_flow/stores/zarr_nwp_grid_store.py` | T3 | `os.replace`; stale-backup cleanup |
| `src/sapphire_flow/exceptions.py` | T1 | `ExtractionError` docstring update |
| `tests/unit/adapters/test_exact_extract_grid_extractor.py` | T5 | +2 tests |
| `tests/unit/stores/test_zarr_nwp_grid_store.py` | T5 | +1 test |

---

## Exit gates

1. A basin polygon placed at (10.0°E, 50.0°N) with a Swiss ICON raster raises
   `ExtractionError` with actionable context — verified by T5 test.
2. `python -c "import sapphire_flow.flows.run_forecast_cycle"` succeeds without
   `exactextract` installed (venv without optional deps) — manual check.
3. After two back-to-back writes to the same key, no `.zarr.old` directories
   exist under the archive base — verified by T3 test.
4. Passing a naive `datetime` to `_to_utc_datetime` raises `ValueError` —
   verified by T4 test (add one if missing).
5. `uv run pytest tests/unit/adapters/ tests/unit/stores/ -q` passes.
6. Full `uv run pytest` suite remains green (1170 tests → 1174+ after T5).
7. Version bump applied per CLAUDE.md.

---

## Risks

| Risk | Mitigation |
|---|---|
| Production cycle fails loudly on a basin that was previously silently NaN | This is the intended behaviour. Operators should see the error, fix the basin polygon (or the grid domain), and retry. Document in the runbook. |
| Lazy instantiation hides an `exactextract` install-time error until the first gridded cycle runs | Acceptable: the error message at cycle-time will point at the import. Alternative (eager import) defeats the purpose. |
| Stale-backup cleanup at startup removes something a human was mid-inspection of | Low probability; structlog event logs what was removed. Operators can recover from Zarr's consolidated metadata if needed. |
| Rejecting naive datetimes breaks a test fixture or adapter that currently emits naive tz | T4 step 3 explicitly verifies callers. If a fixture is found, it should be fixed — naive datetimes are a project-wide anti-pattern. |

---

## Open questions

Not blocking DRAFT → READY:

1. Should T1 collect **all** NaN offenders before raising, or fail on first?
   (Recommendation: collect all — better diagnostic in the error message.)
2. Should `_cleanup_stale_backups` run on every `__init__` or only on a flag?
   (Recommendation: every `__init__` — cheap, and the log event provides
   visibility.)
3. Should T6 `merge_data_requirements()` be promoted to its own plan now, or
   wait until a mixed-resolution model forces the issue?
   (Recommendation: wait — no current model needs it.)
