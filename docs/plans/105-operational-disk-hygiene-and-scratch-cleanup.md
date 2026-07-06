# Plan 105 — operational disk hygiene & NWP scratch cleanup (stop a full disk silently killing the feed)

**Status**: DRAFT — **grill-me COMPLETE (2026-07-06)**: D1 finally-cleanup +
prune-all-stale (safe, `forecast-cycle` is `concurrency_limit=1`); D2 **tiered**
(soft → warn+degrade to fallback, hard → fail-closed red run) on **absolute free-GB**
thresholds; D3 **weekly HOST-level launchd cron** for `docker image/builder prune`
(NOT a Prefect flow — no Docker socket in the worker); D4 keep 4 GiB scratch, no
`max_files` cap. See DECIDED DESIGN. Next: `plan-review` (WF1) → READY → implement.
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

## DECIDED DESIGN (grill-me 2026-07-06)

- **D1 — scratch self-cleanup on failure + prune ALL stale cycle dirs (the bug fix).**
  - Wrap the fetch body in `meteoswiss_nwp.py` `fetch_forecasts` so the current
    cycle's `scratch_dir` is removed in a **`finally`** — a failed fetch never leaves
    partials.
  - At fetch start, prune **every** stale cycle dir under `scratch_path` (any dir
    that is not the active `cycle_time`), draining an already-clogged scratch without
    a worker recreate. **Safe: no concurrent-fetch race** — `forecast-cycle` is
    `concurrency_limit=1` (`register_deployments.py:57`), so only one fetch touches
    the scratch at a time (record this invariant in a comment; if v0b ever
    parallelises forecast-cycle, revisit).
- **D2 — pre-fetch disk tripwire: TIERED, ABSOLUTE free-GB, first real use of
  `DISK_USAGE`.** Before starting the ~2.8 GB download, check **absolute free GB** on
  both the **scratch mount** (`/tmp/sapphire_nwp`) and the **`/data/nwp_grids`
  persistent volume**. `DISK_USAGE` (`types/enums.py:139`) is currently **defined but
  never emitted** — this wires it up. Two tiers (starter values; plan-review/impl to
  tune):
  - **Soft (e.g. < ~8 GB free on the persistent disk / < ~1.5 GB on the 4 GiB
    scratch) → WARN + DEGRADE:** emit a loud `DISK_USAGE` event and **skip NWP for
    this cycle**; the forecast-cycle continues runoff-only / on the Plan-100 fallback
    floor. Feed stays alive, issue surfaced.
  - **Hard/critical (e.g. < ~3 GB free on the persistent disk / < ~0.5 GB scratch) →
    FAIL-CLOSED:** raise → the forecast-cycle run goes **RED** in Prefect. Maximum
    visibility when the disk is critically full.
  - Absolute GB (not %) — predictable against the fixed ~2.8 GB working set.
    Thresholds live in config so they are tunable per deployment.
- **D3 — weekly HOST-level launchd cron for image/build-cache prune (NOT a Prefect
  flow).** A Docker prune needs **host Docker-daemon access**; running it from a
  Prefect flow would require mounting the Docker socket into the worker — a
  **security no-go** (container escape surface; violates the least-privilege model in
  `docs/standards/security.md`). So the weekly `docker image prune -f` +
  `docker builder prune -f` runs as a **host launchd periodic job on the mac-mini**
  (alongside `start-sapphire.sh`), documented in the mini runbook. **Not** on every
  boot, **not** in the upgrade runbook (owner chose weekly-cron only). GRILL-ME
  residual for plan-review: exact cron cadence + a size-guard so it only prunes when
  reclaimable space is meaningful.
- **D4 — keep the 4 GiB scratch, no `max_files` mini cap.** The live incident showed
  a *clean* fetch stays well under 4 GiB (~400 MB and climbing when healthy) — the
  tmpfs is not too small; the clog was leftovers (fixed by D1). Do **not** add a
  `max_files` cap prematurely; only revisit if a clean fetch is later shown to exceed
  4 GiB.

### Implementation vision (feeds WF1 plan-review → WF2)

- **D1 (code):** in `adapters/meteoswiss_nwp.py` `fetch_forecasts` (`:459-503`),
  move the per-cycle `rmtree` into a `try/finally` around the download/convert body,
  and add a start-of-fetch sweep that removes every child of `scratch_path` except
  the active cycle dir. Unit-test: seed a stale `scratch/<oldcycle>/` + a partial in
  the active dir, run a fetch (fake HTTP), assert both are gone afterward and a
  raised fetch still cleans its own dir.
- **D2 (code + config):** add a `disk_free_gb(path)` helper (`shutil.disk_usage`) and
  a pre-fetch check in `flows/run_forecast_cycle.py` (before the fetch, near
  `:339-348`); emit `DISK_USAGE`; branch on soft/hard thresholds (config keys, e.g.
  `disk_guard_soft_gb` / `disk_guard_hard_gb`). Soft path reuses the existing
  runoff-only degrade branch (`:679-682, 819`); hard path raises. Inject the clock/
  path so it's testable. Tests: soft → degrade + event; hard → raise; healthy →
  no-op.
- **D3 (ops):** a `scripts/launchd/prune-docker.sh` (+ a launchd `.plist`) that runs
  `docker image prune -f` + `docker builder prune -f` weekly; documented in the mini
  runbook / `docs/standards/cicd.md`. No app-code change; no Docker socket in any
  container.

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
- **D3:** run the weekly host launchd prune job and confirm stale images/build cache
  are reclaimed with no impact on running services.

## Process

Grill-me **COMPLETE** (2026-07-06): D1 finally-cleanup + prune-all-stale (safe via
`concurrency_limit=1`); D2 tiered soft-degrade / hard-fail-closed on absolute
free-GB; D3 weekly host launchd prune (not a Prefect flow — no Docker socket in the
worker); D4 keep 4 GiB, no `max_files`. Residuals for **plan-review to sharpen**:
exact GB thresholds per mount, the cron cadence + reclaimable-size guard. Next: run
`plan-review` (WF1) → READY → implement. Implementation is a code + config + ops
change (`adapters/meteoswiss_nwp.py`, a pre-fetch check in
`flows/run_forecast_cycle.py`, config threshold keys, a `scripts/launchd/`
prune job + `.plist`, docs) → **hold-at-PR** with a version bump.
