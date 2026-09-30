---
status: DRAFT
---

# Plan 515 — The NWP raw-grid prune removes nothing on the real layout

**Date**: 2026-09-30
**Related**: Plan 095 (introduced the prune; archived — `docs/plans/archive/095-nwp-grid-archive-retention.md`),
Plan 511 (Nepal cloud host: D8 sizes its disk from the database until this is fixed), Plan 105 (disk guards)
**Scope**: make `prune_old_cycles` actually delete old raw-grid cycles, prove it with a test that uses the
layout the writer really produces, and reclaim the space already wasted on the Mac mini. The permanent record
(extracted values in `weather_forecasts`) is untouched.

---

## What is wrong (measured 2026-09-30)

- The Mac mini's `nwp_grids` volume holds **421 GB** in `icon_ch2_eps`: ~250 cycles back to **2026-07-03**
  (~88 days), each cycle ~1.7–1.8 GB, with `nwp_grid_retention_days` at its default of **3**.
- **Writer layout** (`store/zarr_nwp_grid_store.py`, `archive`): each cycle is a **versioned directory**
  `<cycle>_vN` (two are kept, `_cleanup_stale_artifacts` removes versions older than `current-1`) plus a
  **symlink** `<cycle>.zarr -> <cycle>_vN`, swapped atomically.
- **Prune** (`prune_old_cycles`) matches only names of the form `^\d{8}T\d{2}\.zarr$` — the symlink — and calls
  `shutil.rmtree(..., ignore_errors=True)` on it. `rmtree` refuses a symlink, the error is swallowed, nothing
  is removed, and the code still logs `nwp.old_cycle_pruned`. The real `_vN` directories never match the
  pattern at all.
- **Reproduced** in a scratch directory: layout `20260703T06_v1` + symlink `20260703T06.zarr`, prune with a
  3-day window — the log says "pruned"; the data and the symlink are both still there.
- The existing unit test (`tests/unit/store/test_zarr_nwp_grid_store.py`, `TestPruneOldCycles`) builds a plain
  `X.zarr` **directory**, so it never exercised the layout the writer creates. That is why it stayed green.
- The prune call site (`flows/run_forecast_cycle.py`, after a successful NWP fetch) wraps the call in
  `except Exception`, so even a raised failure would only log a warning.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Prune by cycle, not by name pattern.** For each source directory, group every entry belonging to a cycle — `<cycle>.zarr` (symlink or directory), `<cycle>_vN`, `<cycle>_vN_tmp`, `<cycle>_tmp_symlink`, `<cycle>.zarr.old` — and remove the whole group when the cycle is older than the cutoff. | The layout has several members per cycle; matching one of them leaves the rest. |
| D2 | **Remove a symlink with `unlink`, a directory with `rmtree`, and report failures.** Log `nwp.old_cycle_pruned` only for what was actually removed; log a warning with the path when a removal fails; end each run with one summary event (cycles removed, bytes freed, failures). | The false "pruned" message hid the fault for 88 days. |
| D3 | **Order: remove the symlink first, then the versioned directories.** | A reader never follows a symlink into a directory that is half deleted. |
| D4 | **The retention rule is unchanged** (age-only, `nwp_grid_retention_days`, its existing floor of `ceil(nwp_max_fallback_age_hours / 24) + 1`). | The fault is that the rule is never applied, not that it is wrong. |
| D5 | **One-time reclaim on the mini is the fix's first run**, done after deployment by the orchestrator, with before/after `du`. No separate cleanup script. | The corrected prune removes everything older than the window; a second mechanism would be a second thing to get wrong. |

## Tasks

### T1 — Failing test on the real layout
**Outcome**: a test that reproduces the fault.
- Build a fixture through the real writer where practical (`ZarrNwpGridStore.archive` twice for one cycle to
  get `_v1` and `_v2` plus the symlink), else construct the same shape by hand: `<cycle>_v1`, `<cycle>_v2`, symlink
  `<cycle>.zarr -> <cycle>_v2`, for one old cycle and one recent cycle.
- Assert after `prune_old_cycles`: nothing of the old cycle remains, the recent cycle is intact and its
  symlink still resolves.
**In / Out**: `tests/unit/store/test_zarr_nwp_grid_store.py`. Out: any source change.
**Verification**: `uv run pytest tests/unit/store/test_zarr_nwp_grid_store.py -k prune` — fails on `main`.
**Pre-change**: RED, and mutation-checked: reverting only the symlink handling makes it fail again.

### T2 — Fix the prune
**Outcome**: old cycles are removed completely; recent ones are untouched.
- Implement D1–D4 in `store/zarr_nwp_grid_store.py`. Keep the function signature. Cover: a dangling symlink,
  a stale `_tmp` directory, a name that matches no pattern (left alone), a missing base path (no-op).
- Update the existing plain-directory test so it states which layout it covers.
**In / Out**: `store/zarr_nwp_grid_store.py`, tests. Out: the writer, the call site, retention defaults.
**Verification**: T1 passes; the existing prune tests pass; `uv run pytest tests/unit/store -q`,
`uv run pyright`, `uv run ruff check`.
**Pre-change**: T1's RED, plus RED tests for the dangling-symlink and stale-`_tmp` cases.

### T3 — Nothing else needs the old cycles
**Outcome**: confirmed, not assumed, that no reader depends on grids older than the window.
- The prune's own comment says the NWP adapter re-fetches from STAC and never reads the archive. Grep every
  reader of the archive path (`adapters/era5_land_reanalysis.py`, `flows/run_forecast_cycle.py`, any CLI or
  script) and state each one's window.
**In / Out**: this plan's "Findings" section only. Out: code changes.
**Verification**: inspection — each reader named with the file:line that shows its window.
**Pre-change**: N/A (read-only check).

### T4 — Reclaim on the mini, and watch it hold
**Outcome**: the mini's `nwp_grids` volume drops to roughly the retention window and stays there.
- After merge and deployment (owner/orchestrator action; the mini is the staging host), run one forecast
  cycle; record `du -sh /data/nwp_grids` before and after and the summary log event.
- Check again after three further days that the volume is stable, not growing.
**In / Out**: a dated note in this plan. Out: changing the disk guards (Plan 105).
**Verification**: before/after `du`, the summary event with non-zero bytes freed, and the day-3 size.
**Pre-change**: baseline 421 GB / ~250 cycles (2026-09-30).

## Exit gates

1. T1 fails on `main` and passes after T2; the dangling-symlink and stale-`_tmp` tests pass.
2. T3 records every reader of the archive and its window; none needs cycles older than the retention window.
3. After deployment the mini's grid volume is within the retention window plus one cycle of headroom, and is
   flat three days later.
4. The new summary log event appears on every forecast cycle that reaches the prune.

## Not in this plan

Changing `nwp_grid_retention_days`; disk-guard thresholds; a health alert on grid-volume growth (worth
considering separately — the disk guards did not catch 421 GB); the Nepal host's disk sizing (Plan 511).

## Risks

| Risk | Mitigation |
|---|---|
| Deleting the symlink of a cycle a reader still uses | D3 order, T3 reader audit, the retention floor above the NWP fallback age. |
| First run removes ~400 GB in one cycle and slows it | The prune runs after the NWP fetch and never aborts the cycle; T4 records the duration. |
| A future writer layout change breaks the prune again | T1 builds the fixture through the real writer where practical, so the test follows the writer. |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T3"], "parallel": true },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] }
  ]
}
```
