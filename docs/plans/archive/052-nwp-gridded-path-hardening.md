# Plan 052 — Flow 1 gridded-NWP path hardening

**Status**: DONE (archived 2026-04-20, commit ff18118, tag v0.1.357)
**Date**: 2026-04-18
**Depends on**: Plan 045 DONE (gridded NWP integration landed 2026-04-17).
**Prerequisite**: Plan 063 (MeteoSwiss NWP adapter tz-aware emission contract) must
land before or alongside T4 — Plan 052 only hardens the extractor boundary; Plan 063
owns the upstream adapter.
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
   `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`:
   `exactextract` returns `NaN` for a polygon that does not intersect the
   raster extent. The code casts via `float(...)` and writes the NaN into
   `WeatherForecastRecord.value` with no guard. The existing `ExtractionError`
   only fires when the *basin dict* is empty — a spatially out-of-bounds polygon
   passes validation and yields NaN silently. Downstream `_pivot_nwp_records` in
   `services/operational_inputs.py` builds a Polars DataFrame that carries NaN
   into the model. No test covers this case.
2. **HIGH — `ExactExtractGridExtractor` constructed unconditionally at startup**
   `src/sapphire_flow/flows/run_forecast_cycle.py`:
   production startup always instantiates `ZarrNwpGridStore` and
   `ExactExtractGridExtractor` even when `nwp_grid_archive_base_path` is `None`.
   The heavy optional deps (`exactextract`, `rioxarray`, `geopandas`) become
   *mandatory* — a minimal deployment without them crashes at construction time
   rather than at extraction time.
3. **MED — Zarr atomic-swap not crash-safe**
   `src/sapphire_flow/store/zarr_nwp_grid_store.py`:
   the rename sequence `zarr → old`, `tmp → zarr`, `rmtree old` is not atomic
   across a process crash between steps 2 and 3. On next run `.zarr.old`
   lingers and the swap logic wraps it again, meaning stale backups persist
   indefinitely. Test `test_overwrite_existing_archive` only covers a clean
   re-run. Additionally, `os.replace` on a non-empty directory fails with
   `ENOTEMPTY` — the directory-rename approach must use a symlink-pointer swap.
4. **MED — naive-datetime silent UTC coercion**
   `preprocessing/exact_extract_grid_extractor.py`:
   if `xarray` returns a naive `valid_time` coordinate (e.g. from a Zarr
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
- MeteoSwiss adapter tz-aware emission contract (Plan 063).

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Two-branch NaN discriminator in GridExtractor**: all-members-NaN for a polygon → raise `ExtractionError`; some-but-not-all-members-NaN → skip NaN members, emit `extraction.member_skipped` per skipped member (INFO), continue with remaining members. Collect all all-NaN polygons and raise once after the full batch. | `WeatherForecastRecord.value: float` has no NaN-handling contract; silently feeding NaN into Polars means the model receives garbage. Partial-member NaN can occur legitimately at grid edges; raising immediately would abort valid partial-coverage cycles. Collecting all offenders before raising gives operators a complete diagnostic. |
| D2 | **Gate GridExtractor instantiation on `config.nwp_grid_archive_base_path is not None`**: construct `ZarrNwpGridStore` and `ExactExtractGridExtractor` only when `nwp_grid_archive_base_path` is set. `DeploymentConfig` has no `weather_forecast_sources` field — do not add a "at least one gridded source active" check here; that check already happens at runtime inside the flow. | Removes the hard dep on `exactextract`/`rioxarray`/`geopandas` at startup. A deployment running only legacy BAFU point-forecast adapters no longer needs the gridded-NWP stack installed. |
| D3 | **Atomic Zarr swap via per-cycle symlink-pointer swap**: `_safe_zarr_path(base, nwp_source, cycle_time)` returns the symlink path `{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr` — no signature change. The symlink points at a versioned directory `{cycle_time:%Y%m%dT%H}_v{N}/` on the same filesystem. Write path: `{cycle_time:%Y%m%dT%H}_v{N}_tmp/` → rename to `{cycle_time:%Y%m%dT%H}_v{N}/` → create `{cycle_time:%Y%m%dT%H}_tmp_symlink` → `os.replace(tmp_symlink, {cycle_time:%Y%m%dT%H}.zarr)` (POSIX-atomic symlink→symlink). `load()` opens the symlink transparently; no code change needed in `load()`. The atomicity guarantee applies PER CYCLE — cross-cycle consistency is not affected because each cycle has its own archive directory. All operations must stay on the SAME filesystem as `nwp_grid_archive_base_path` (hard constraint). | `os.replace` on a non-empty directory fails with `ENOTEMPTY`; the symlink-pointer swap is the only POSIX-atomic approach for directory-shaped Zarr stores. xarray/zarr-python 3 follows symlinks transparently on macOS and Linux. Using the cycle timestamp as the symlink name matches the existing `_safe_zarr_path` return value — `load()` needs no change. |
| D4 | **Retention policy — per cycle, keep exactly one previous version**: after a successful swap for cycle `{cycle_time}`, retain `{cycle_time:%Y%m%dT%H}_v{N-1}/`; delete any older versions (`_v{N-2}`, `_v{N-3}`, …) for that cycle during the cleanup sweep. Each cycle's versioned directories are independent — cleanup for one cycle does not touch another cycle's archives. | Bounded growth per cycle (at most 2× the most recent archive size for that cycle). Protects in-flight readers holding a handle to the previous version of the same cycle. |
| D5 | **Require tz-aware datetimes in GridExtractor**: raise `ValueError` if `valid_time` comes in as naive. Do not silently coerce. Plan 063 owns the upstream adapter's emission contract; Plan 052 asserts only at the extractor boundary. | Matches the project convention (`UtcDatetime` NewType, `ensure_utc()` at boundaries). Silent coerce violates "parse, don't validate". |
| D6 | **Cover three new failure modes with unit tests**: polygon-outside-extent (raises), partial-member-NaN (skip+log), re-run idempotency (second call overwrites first; no duplicates). | Maps 1:1 to the three audit gaps. Each test is small and isolated; no fixture regeneration required. |

---

## Structlog event inventory

Per `docs/standards/logging.md`, the following canonical event names are introduced by this plan:

| Event | Level | Fields |
|---|---|---|
| `nwp.archive_swapped` | INFO | `zarr_path`, `version` |
| `nwp.swap_failed_cleanup` | WARNING | — |
| `nwp.stale_tmp_removed` | WARNING | `path` |
| `nwp.old_version_removed` | INFO | `path`, `version` |
| `extraction.member_skipped` | INFO | `polygon_id`, `member_id`, `cycle_time` |
| `extraction.polygon_outside_extent` | ERROR | (emitted immediately before raising) |

---

## Task list

### T1 — Fail-fast on NaN from exactextract (two-branch discriminator)

**File**: `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`

1. After each `float(...)` cast, check `math.isnan(value)`.
   - If the NaN comes from a member where **all** polygons/members at that
     polygon are NaN (polygon outside grid extent): collect the offending
     `(polygon_id, raster_extent, polygon_bbox)` tuple — do NOT raise yet.
   - If the NaN is for a specific member at an otherwise-valid polygon
     (some-but-not-all-members-NaN): skip that member, emit
     `extraction.member_skipped` (INFO) with fields
     `{polygon_id, member_id, cycle_time}`, and continue with remaining members.
2. After the full batch completes, if any all-NaN polygons were collected: emit
   `extraction.polygon_outside_extent` (ERROR) then raise
   `ExtractionError` including all offending polygon IDs, raster extent, and
   polygon bounding boxes. Do not raise on the first offender — operators
   should see all out-of-extent basins at once.
3. Update the `ExtractionError` class comment in `exceptions.py` to document
   both trigger cases.

**Exit**: a basin polygon placed entirely outside the ICON-CH2-EPS grid raises
`ExtractionError` with all offending polygon IDs; no NaN reaches
`WeatherForecastRecord`; partial-member NaN is handled gracefully.

### T2 — Gate GridExtractor instantiation on `nwp_grid_archive_base_path`

**File**: `src/sapphire_flow/flows/run_forecast_cycle.py`

1. Locate the unconditional `ZarrNwpGridStore(...)` and
   `ExactExtractGridExtractor()` construction. The imports at lines ~360 and
   ~364–366 are already deferred inside `if ... is None` guards — the module
   import itself is not the problem. The residual issue is unconditional
   construction when the caller passes `config.nwp_grid_archive_base_path = None`.
2. Gate both instantiations on `config.nwp_grid_archive_base_path is not None`
   (single condition — do NOT add a "gridded sources active" check; that is
   handled at runtime in the flow body). When the path is `None`, assign
   `grid_store = None` and `grid_extractor = None`.
3. Convert the `# TODO: use merge_data_requirements()` comment (line ~640) to
   `# HACK(v0b): merge_data_requirements() not yet implemented — tracked in v0b
   remainder`. No behaviour change.
4. Callers already tolerate `None` for both from Plan 045 — no further changes
   required there.

**Exit**: a unit test asserts that calling `run_forecast_cycle` (or its setup
helper) with `config.nwp_grid_archive_base_path = None` leaves `grid_store` and
`grid_extractor` as `None` and does NOT construct either class. This replaces the
vacuous `python -c "import ..."` check from the previous draft.

### T3 — Atomic Zarr swap via symlink-pointer + cleanup sweep

**File**: `src/sapphire_flow/store/zarr_nwp_grid_store.py`

1. Replace the three-step rename (current lines 59–63) with a per-cycle
   symlink-pointer atomic swap. `_safe_zarr_path` returns the symlink path
   `{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr` — its signature is unchanged.
   The atomicity guarantee applies PER CYCLE; cross-cycle consistency is not
   affected because each cycle has its own symlink and versioned directory.
   - Derive the versioned directory name as `{cycle_time:%Y%m%dT%H}_v{N}/`
     where `N` is a monotonically increasing integer stored in an adjacent
     `.version` file (one per cycle; default 0).
   - Write the new Zarr data to `{cycle_time:%Y%m%dT%H}_v{N}_tmp/` (safe for
     Prefect retry resumption — a retried task starts the write from scratch).
   - Rename `{cycle_time:%Y%m%dT%H}_v{N}_tmp/` → `{cycle_time:%Y%m%dT%H}_v{N}/`
     (non-atomic but safe: both paths are within `nwp_grid_archive_base_path`).
   - Create a new temporary symlink `{cycle_time:%Y%m%dT%H}_tmp_symlink →
     {cycle_time:%Y%m%dT%H}_v{N}/` on the same filesystem.
   - Call `os.replace({cycle_time:%Y%m%dT%H}_tmp_symlink,
     {cycle_time:%Y%m%dT%H}.zarr)`. This is POSIX-atomic on symlink→symlink.
     `_safe_zarr_path` returns `{cycle_time:%Y%m%dT%H}.zarr` unchanged —
     `load()` opens it transparently without modification.
   - Hard constraint: all paths must reside on the SAME filesystem as
     `nwp_grid_archive_base_path`. Document this as a `# CONSTRAINT:` comment.
     Typical Docker deployment: all paths on the `nwp_grids` named volume —
     not `/tmp`.
   - Emit `nwp.archive_swapped` (INFO) with `{zarr_path, version}`.
   - On any exception during or after the rename, catch and emit
     `nwp.swap_failed_cleanup` (WARNING) before re-raising.
2. Apply retention policy (D4): after a successful swap, call
   `_cleanup_stale_artifacts(base_path, cycle_time)` which:
   - For the given `cycle_time`, removes `{cycle_time:%Y%m%dT%H}_v{N-2}/`,
     `{cycle_time:%Y%m%dT%H}_v{N-3}/`, … (all versions older than `v{N-1}`).
     Log `nwp.old_version_removed` (INFO, `{path, version}`) per deletion.
     Cleanup for one cycle does not touch other cycles' archives.
   - Removes `*_tmp/` and `*_tmp_symlink` artifacts older than 1 hour (crash
     recovery for partial writes). Log `nwp.stale_tmp_removed` (WARNING,
     `{path}`).
   - Removes any legacy `*.zarr.old/` directories (migration path for pre-T3
     code).
   - (Renamed from `_cleanup_stale_backups` in the previous draft.)
3. Add test `test_atomic_swap_leaves_no_stale_artifacts`: run two back-to-back
   writes for the SAME cycle, assert no `*_tmp/` or `*.zarr.old/` artifacts
   exist after the second completes, and only `{cycle_time:%Y%m%dT%H}_v{N-1}/`
   is retained alongside the current `{cycle_time:%Y%m%dT%H}.zarr` symlink.

**Hard constraint note**: callers must not place the symlink target and source
on different filesystems (e.g. `/tmp` vs the Docker named volume `nwp_grids`).
Document as a `# CONSTRAINT:` comment in the implementation.

**Exit**: no `*_tmp/` or `*.zarr.old/` sibling directories after any write;
per-cycle versioned archives beyond `v{N-1}` are cleaned; `nwp.archive_swapped`
emitted on every successful swap.

### T4 — Reject naive `valid_time` in GridExtractor

**File**: `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`

1. At the `_to_utc_datetime` helper, replace the "if naive, assume UTC" branch:
   ```python
   if ts.tzinfo is None:
       raise ValueError(
           f"valid_time {dt!r} is naive; extractor requires tz-aware datetimes"
       )
   ```
2. Add a `# NOTE:` comment: "Plan 063 owns the MeteoSwiss adapter's tz-aware
   emission contract; this guard asserts only at the extractor boundary."
3. Do NOT update NWP adapter callers or `fetch_forecasts` — that is Plan 063's
   scope.

**Exit**: passing a naive datetime to `_to_utc_datetime` raises `ValueError`;
no silent UTC coercion; no adapter-side changes in this plan.

### T5 — New failure-mode tests

**File**: `tests/unit/preprocessing/test_exact_extract_grid_extractor.py`

1. `test_polygon_outside_grid_extent_raises`: construct a basin polygon at
   `lon=30.0, lat=60.0` (unambiguously outside the Swiss ICON grid) and a
   Swiss ICON raster fixture. Call `extract`. Assert `ExtractionError` is
   raised and the message contains all offending polygon IDs.
   Fixture: a synthetic xarray Dataset with `longitude` ∈ [5.9, 10.5]
   (5 points), `latitude` ∈ [45.7, 47.9] (5 points), `member` dim of size 3,
   `valid_time` dim of size 1, single variable `tp` filled with 1.0. A basin
   polygon at `lon=30.0, lat=60.0` (a single-vertex-buffer Polygon) is clearly
   outside this extent. Implementer can inline this as a pytest fixture without
   further guidance.
2. `test_partial_member_nan_skipped_with_log`: construct a raster where only
   10 of 21 members contain valid data at an otherwise-valid polygon (others
   NaN-filled at the source). Assert the extractor returns 10 member results
   and emits `extraction.member_skipped` once per skipped member
   (11 times total), with correct `{polygon_id, member_id, cycle_time}` fields.

**File**: `tests/unit/store/test_zarr_nwp_grid_store.py`

3. `test_rerun_idempotency`: call `store.archive(...)` with the same
   valid-time and init-time twice in sequence. Assert the second call
   overwrites the first; assert only one current symlink and one versioned
   archive directory exist on disk.

**Exit**: three new tests, all passing.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T1", "T2", "T3", "T4"],
      "parallel": true,
      "note": "all four are independent files and responsibilities; Plan 063 is a prerequisite for T4 to land in production but does not block T4 implementation"
    },
    {
      "id": "phase-2",
      "tasks": ["T5"],
      "parallel": false,
      "depends_on": ["phase-1"],
      "note": "T5 tests must be written after T1 and T3 interfaces are finalised"
    }
  ],
  "external_prerequisites": [
    {
      "plan": "Plan 063",
      "reason": "MeteoSwiss adapter tz-aware emission contract; T4 asserts at the extractor boundary only"
    }
  ]
}
```

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py` | T1, T4 | Two-branch NaN discriminator; reject naive datetimes |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | T2 | Gate instantiation on `nwp_grid_archive_base_path`; TODO → HACK |
| `src/sapphire_flow/store/zarr_nwp_grid_store.py` | T3 | Symlink-pointer atomic swap; `_cleanup_stale_artifacts`; retention policy |
| `src/sapphire_flow/exceptions.py` | T1 | `ExtractionError` comment update |
| `tests/unit/preprocessing/test_exact_extract_grid_extractor.py` | T5 | +2 tests |
| `tests/unit/store/test_zarr_nwp_grid_store.py` | T5 | +1 test |

---

## Exit gates

1. A basin polygon at `lon=30.0, lat=60.0` with a Swiss ICON raster raises
   `ExtractionError` listing all out-of-extent polygon IDs — verified by T5 test 1.
2. A raster with 10 of 21 members NaN-filled at a valid polygon returns 10
   member results and emits `extraction.member_skipped` 11 times —
   verified by T5 test 2.
3. A unit test asserts that `run_forecast_cycle` setup with
   `config.nwp_grid_archive_base_path = None` leaves both `grid_store` and
   `grid_extractor` as `None` — verified by T2 test.
4. After two back-to-back writes to the same key, no `*.zarr.tmp/` or
   `*.zarr.old/` directories exist; only `archive_v{N-1}` is retained alongside
   the current symlink — verified by T3 test and T5 test 3.
5. Passing a naive `datetime` to `_to_utc_datetime` raises `ValueError` —
   verified by T4 test (add one if missing).
6. `uv run pytest tests/unit/preprocessing/ tests/unit/store/ -q` passes.
7. Full `uv run pytest` suite remains green (1170 tests → 1174+ after T5).
8. Version bump applied per CLAUDE.md.

---

## Risks

| Risk | Mitigation |
|---|---|
| Production cycle fails loudly on a basin that was previously silently NaN | Intended behaviour. Operators see the error, fix the basin polygon (or grid domain), and retry. Document in runbook. |
| Lazy instantiation hides an `exactextract` install-time error until the first gridded cycle runs | Acceptable: the error message at cycle-time will point at the import. Eager import would defeat the purpose. |
| Stale-artifact cleanup at startup removes something a human was mid-inspection of | Low probability; `nwp.stale_tmp_removed` and `nwp.old_version_removed` log what was removed. |
| Rejecting naive datetimes breaks a test fixture or adapter that currently emits naive tz | T4 asserts only at the extractor boundary. Plan 063 ensures the MeteoSwiss adapter emits tz-aware datetimes. Any fixture emitting naive datetimes should be fixed — they are a project-wide anti-pattern. |
| Reader with open `xr.open_zarr(zarr_path)` handle during a symlink swap sees a reshape error on lazy chunk access against the new version's metadata | Applies only to readers opening the SAME cycle's archive during a retry of that cycle. Loud failure, acceptable for v0 single-worker. Known limitation. Re-open the dataset after a swap. Cross-cycle consistency is not affected: each cycle has its own per-cycle symlink and versioned directory. |
| Symlink target and source on different filesystems (e.g. `/tmp` vs `nwp_grids` Docker volume) | `os.replace` on cross-filesystem symlinks raises `OSError`. Documented as a hard constraint in code. Typical Docker deployment uses a single `nwp_grids` named volume — not `/tmp`. |

---

## Open questions

Not blocking DRAFT → READY:

1. Should `_cleanup_stale_artifacts` run on every `archive()` call or once per
   `__init__`? (Recommendation: every `archive()` call after a successful swap —
   cheap, and the log event provides visibility. Running at `__init__` risks
   cleaning during a parallel write in future multi-worker deployments.)
2. Should the `.version` file tracking `N` be a plain integer text file or a
   JSON envelope? (Recommendation: plain integer — simplest, no parsing overhead.)
