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

- The Mac mini's `nwp_grids` volume holds **421 GB** in `icon_ch2_eps` (measured 2026-09-30): 259 versioned
  directories (~1.6 GB each, so roughly one or two per cycle, not always two) and 249 `.zarr` symlinks, back to
  **2026-07-03** (~88 days), with `nwp_grid_retention_days` at its default of **3**. Re-measure before quoting
  the per-cycle figure.
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
| D1 | **Prune by cycle, not by name pattern.** For each source directory, group every entry belonging to a cycle — `<cycle>.zarr` (symlink or directory), `<cycle>_vN`, `<cycle>_vN_tmp`, `<cycle>_tmp_symlink`, `<cycle>.zarr.old` — and remove the whole group when the cycle is older than the cutoff. **Iterate directory entries by name, not by `is_dir()`** (a dangling symlink is not a directory), parsing the cycle stem from every member form. | The layout has several members per cycle; matching one of them leaves the rest. |
| D2 | **Remove a symlink with `unlink`, a directory with `rmtree`, and report failures.** Log `nwp.old_cycle_pruned` only for what was actually removed; log a warning with the path when a removal fails; end each run with one summary event (cycles removed, bytes freed, failures). | The false "pruned" message hid the fault for 88 days. |
| D3 | **Order: remove the symlink first, then the versioned directories; tolerate a member vanishing (`FileNotFoundError`); skip any cycle group with a member modified within the last hour** (the writer's own `_tmp` window) and prune only stale `_tmp` members. **A freshness check is not exclusion**: a writer can start after the check. So `archive()` and the prune share a **per-source advisory lock** (a lock file in the source directory, held by the writer around the swap and by the prune per cycle group) **unless T3 proves every writer is already serialised with the prune** (for example, the forecast cycle is the only writer and runs the prune in the same flow under a concurrency limit of one). | A reader never follows a symlink into a half-deleted directory, and a writer archiving a historical or re-fetched cycle (`archive()` accepts any cycle time and writes `_vN_tmp` before renaming and swapping the symlink) is never deleted under. |
| D4 | **The retention rule is unchanged** (age-only, `nwp_grid_retention_days`, its existing floor of `ceil(nwp_max_fallback_age_hours / 24) + 1`). | The fault is that the rule is never applied, not that it is wrong. |
| D5 | **One-time reclaim on the mini is the fix's first run**, done after deployment by the orchestrator, with before/after `du`. No separate cleanup script. | The corrected prune removes everything older than the window; a second mechanism would be a second thing to get wrong. |

## Tasks

### T1 — Failing test on the real layout
**Outcome**: a test that reproduces the fault.
- Build a fixture through the real writer where practical (`ZarrNwpGridStore.archive` twice for one cycle to
  get `_v1` and `_v2` plus the symlink), else construct the same shape by hand: `<cycle>_v1`, `<cycle>_v2`, symlink
  `<cycle>.zarr -> <cycle>_v2`, for one old cycle and one recent cycle.
- Assert after `prune_old_cycles`: nothing of the old cycle remains (**these old-cycle assertions are the
  discriminating ones**; the recent cycle being intact is already true on `main`), the recent cycle's symlink
  still resolves.
**In / Out**: `tests/unit/store/test_zarr_nwp_grid_store.py`. Out: any source change.
**Verification**: `uv run pytest tests/unit/store/test_zarr_nwp_grid_store.py -k prune` — fails on `main`.
**Pre-change**: RED, and mutation-checked: reverting only the symlink handling makes it fail again.

### T2 — Fix the prune
**Outcome**: old cycles are removed completely; recent ones are untouched.
- Implement D1–D4 in `store/zarr_nwp_grid_store.py`. Keep the function signature. Cover: a dangling symlink,
  a stale `_tmp` directory, a fresh `_tmp` (left alone), a name that matches no pattern (left alone), a missing
  base path (no-op), a member removed by a concurrent run (`FileNotFoundError` tolerated), and a deterministic
  interleaving test **that starts the writer after the prune's freshness check** and shows the writer's cycle
  survives (through the lock).
- Update the existing plain-directory test so it states which layout it covers.
**In / Out**: `store/zarr_nwp_grid_store.py` (the prune, and the shared lock in `archive()` unless T3 proves it
unnecessary), tests, and the docs that describe the prune (`docs/architecture-context.md` Flow 1.2 and the
`config.toml` comment); a patch version bump. Out: the call site, retention defaults.
**Verification**: T1 passes; the existing prune tests pass; `uv run pytest tests/unit/store -q`,
`uv run pyright`, `uv run ruff check`.
**Pre-change**: T1's RED, plus RED tests for the dangling-symlink and stale-`_tmp` cases.

### T3 — Nothing else needs the old cycles, and who writes
**Outcome**: confirmed, not assumed, that no reader depends on grids older than the window and that every writer
is covered by D3.
- The prune's own comment says the NWP adapter re-fetches from STAC and never reads the archive. **Readers**:
  the callers of `ZarrNwpGridStore.load` — `adapters/replay/nwp.py:46` — and any
  other reader of `nwp_grid_archive_base_path` (`adapters/era5_land_reanalysis.py` keeps its own
  `{store_root}/{var}.zarr` and is not a reader of this base path); state each one's window.
- **Writers**: every caller of `archive()` — the forecast cycle (`flows/run_forecast_cycle.py:2708`), recovery or
  backfill flows, and `tools/record_fixtures.py:307` (a writer, to its own output directory, not the operational
  archive) — and whether each can write a cycle older than the cutoff or run concurrently with the prune. The
  answer decides whether the lock in D3 is needed.
**In / Out**: this plan's "Findings" section only. Out: code changes.
**Verification**: inspection — each reader and writer named with the file:line that shows its window.
**Pre-change**: N/A (read-only check).

### T4 — Reclaim on the mini, and watch it hold
**Outcome**: the mini's `nwp_grids` volume drops to roughly the retention window and stays there.
- After merge and deployment (owner/orchestrator action; the mini is the staging host), run one forecast
  cycle; record the size before and after (run `du -sh /data/nwp_grids` inside the `prefect-worker` container, or against the volume) and the summary log event.
- Check again after three further days that the volume is stable, not growing.
**In / Out**: a dated note in this plan. Out: changing the disk guards (Plan 105).
**Verification**: before/after `du`, the summary event with non-zero bytes freed, and the day-3 size.
**Pre-change**: baseline 421 GB / 259 versioned directories (2026-09-30).

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
| Deleting the symlink of a cycle a reader still uses, or a live writer's files | D3 order, freshness skip and `FileNotFoundError` tolerance; T3 reader and writer audit; the retention floor above the NWP fallback age. |
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
