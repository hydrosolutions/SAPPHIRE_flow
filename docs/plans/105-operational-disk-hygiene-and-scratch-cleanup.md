# Plan 105 — operational disk hygiene & NWP scratch cleanup (stop a full disk silently killing the feed)

**Status**: DRAFT
**Priority**: high — on 2026-07-06 a full disk **silently stopped the operational
forecast feed** on the mac-mini: `nwp.fetch_failed: no space left on device`, the
forecast-cycle completed **green** (runoff-only fallback catch), and no forecast
was written — the same silent-failure class as the NWP-off blackout (Plan 100). Two
root causes: Docker image/build-cache accumulation from our own rebuilds (~15 GB
reclaimable) **and** a scratch-tmpfs clog from un-cleaned failed-fetch leftovers.
**Phase**: v0b — operational reliability / pipeline monitoring
**Parent**: the mac-mini operational test (Plan 091); companion to Plan 100
(forecast-feed resilience) and its Flow-4 monitoring
**Related**:
- `src/sapphire_flow/adapters/meteoswiss_nwp.py:501-503` (scratch cleanup —
  `rmtree(scratch_dir)` only for the **current** `cycle_time` dir at fetch start;
  `:459-497` `fetch_forecasts` try/except → `:496-497` logs `nwp.fetch_failed` on any
  exception; `cleanup_scratch_on_fetch=True` `:324,350`)
- `src/sapphire_flow/flows/run_forecast_cycle.py:347-348` (`nwp.fetch_failed` caught
  → **runoff-only for the cycle, flow stays green**); `:358-360` `nwp.archive_failed`
- `docker-compose.yml:75,110-114` (`/tmp/sapphire_nwp` tmpfs, **4 GiB**),
  `config/overlays/mac-mini.toml` (`[adapters.weather_forecast].max_files` cap,
  currently unset — the config comments anticipate a mini cap)
- `src/sapphire_flow/types/enums.py:139` (`DISK_USAGE = "disk_usage"` — a monitoring
  metric already scaffolded → hook the tripwire here)
- `scripts/launchd/start-sapphire.sh`, `scripts/bootstrap-mac-mini.sh`,
  `docs/standards/cicd.md` (upgrade runbook — where a deploy-time prune belongs)
- Plan 100 M4 (NWP-staleness tripwire — the *symptom*; this plan attacks the *cause*)
- Plan 095 (`nwp_grid_retention_days=3` — bounds the *archive*, not the scratch or images)
**Created**: 2026-07-06

---

## Problem (observed live on the mac-mini, 2026-07-06)

After the NWP overlay was restored (Plan 100 incident #1), the fetch still failed:
`nwp.fetch_failed: no space left on device`. The disk was full from two sources,
**neither operational data** (a few days of two stations is megabytes):

1. **Docker image / build-cache accumulation.** Every version bump builds a fresh
   ~1.9 GB `sapphire-flow` image; dozens accumulated (~15 GB reclaimable). On a
   Docker-Desktop-for-Mac VM disk this fills fast. Nothing prunes them.
2. **NWP scratch-tmpfs clog (a real bug).** `meteoswiss_nwp.py:501-503` cleans only
   **the current cycle's** scratch dir (`scratch_path / cycle_time`), and only at
   the *start* of a fetch. When a fetch **fails mid-download** (`:496-497`), its
   partial files are left in `scratch/<that-cycle>/`. The next fetch is usually a
   **different** cycle, so it creates a new dir and **never cleans the failed one**.
   Failed-cycle leftovers therefore **accumulate until the 4 GiB tmpfs is full**,
   after which *every* fetch fails instantly on "no space" — a self-perpetuating
   clog. (Cleared on the mini only by recreating the worker, which resets the
   ephemeral tmpfs.)

Both are **silent**: the fetch failure is caught (`run_forecast_cycle.py:347`), the
cycle falls to runoff-only, the flow reports **green**, and no forecast is written —
no alert, no signal, exactly like the NWP-off blackout.

## Goal

- A full or filling disk **cannot silently kill the feed** — it is detected and
  surfaced loudly *before* (or at) the point it would cause a failed fetch.
- The NWP scratch **cannot clog** — failed-fetch leftovers are always cleaned, and
  stale cycle dirs are pruned.
- Deploys **don't accumulate** unbounded Docker images/build cache on the host.

## Design (proposed; grill-me before READY)

- **D1 — scratch self-cleanup on failure + prune ALL stale cycle dirs (the bug fix).**
  - Wrap the fetch body so the current cycle's `scratch_dir` is removed in a
    **`finally`** (or on the exception path) — a failed fetch never leaves partials.
  - At fetch start, prune **every** stale cycle dir under `scratch_path` (not just
    the current `cycle_time`), e.g. any dir older than the current cycle / not the
    active one. This drains an already-clogged scratch without a worker recreate.
  - GRILL-ME: prune-all-but-current vs age-based; confirm no concurrent fetch races
    on the shared scratch (v0 runs one forecast-cycle at a time — confirm).
- **D2 — pre-fetch disk-space tripwire (fail-loud, reuse `DISK_USAGE`).** Before
  starting a ~2.8 GB download, check free space on the **scratch mount** and the
  **`/data/nwp_grids` volume**; if below a threshold, emit a **loud, monitorable
  event** (wire to the existing `DISK_USAGE` metric / Flow-4 monitoring) and decide
  fail-closed vs warn-and-skip-NWP. Catches the cause proactively instead of only
  the stale-grid symptom (Plan 100 M4). GRILL-ME: thresholds (absolute GB vs %),
  fail-closed vs warn, and which mounts to check.
- **D3 — deploy-time image/build-cache pruning.** Add `docker image prune -f` +
  `docker builder prune -f` to the deploy/upgrade path so old images don't
  accumulate. GRILL-ME: where — `start-sapphire.sh` (every boot, risky if it prunes
  something in use), the `cicd.md` upgrade runbook (manual, safe), or a low-frequency
  maintenance cron. Recommend the upgrade runbook + an optional weekly prune cron;
  **not** on every boot.
- **D4 — mini scratch sizing (contingent).** The live incident showed a *clean*
  fetch stays well under 4 GiB (~400 MB and climbing when healthy), so the tmpfs is
  **not** currently too small — the clog was leftovers, fixed by D1. Keep 4 GiB;
  only revisit a `max_files` mini cap (`config/overlays/mac-mini.toml`) if a clean
  fetch is later shown to exceed it. Documented so we don't cap prematurely.

## Non-goals

- The NWP-off overlay persistence + fallback floor — Plan 100.
- The water_level QC datum bug — Plan 101.
- The full Flow-4 pipeline-monitoring watchdog — this plan adds one disk tripwire on
  the existing `DISK_USAGE` metric; the broader watchdog stays in the Flow-4 plan.
- Postgres/backup-volume retention tuning (separate; `/data/raw` CAMELS-CH at 92% is
  a large reference dataset on its own disk, noted but out of scope).

## Verification (local dev stack is up)

- **D1:** simulate a failed fetch (leave a dummy `scratch/<oldcycle>/` dir), trigger
  a fetch for a new cycle, confirm the old dir is pruned and a failed fetch cleans
  its own dir (scratch returns to ~empty).
- **D2:** constrain free space (or lower the threshold) and confirm a loud
  `DISK_USAGE` event fires and the chosen fail-closed/warn behaviour holds *before*
  a doomed download.
- **D3:** run the deploy/upgrade step and confirm stale images are reclaimed with no
  impact on running services.

## Process

DRAFT until a grill-me settles D1 (prune strategy), D2 (thresholds + fail-closed vs
warn + mounts), D3 (where the prune runs). Then plan-review (WF1) → implement.
Implementation is a code + config change (`meteoswiss_nwp.py`, the pre-fetch check
in `run_forecast_cycle.py`, a deploy-script/runbook edit, docs) → **hold-at-PR**
with a version bump.
