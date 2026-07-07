# Plan 095 — NWP grid-archive retention (prune old cycle zarrs)

**Status**: READY (plan-review workflow 2026-07-03 resolved every design
question against the code — retention floor/validator, flow-body trigger,
source-discovery; no residual human-decision forks. Note: the run's automated
verdict read "exhausted/not-converged", but that was a false negative — a
rate-limited `revise-r2` let round-3 re-raise the already-resolved flow-body
point; the plan on disk is internally consistent and implementation-ready.)
**Priority**: medium — real disk-fill risk for long-running deployments
(unbounded ~7 GB/day accumulation); surfaced during the 2026-07-03 Mac-mini
data-collection deployment.
**Phase**: v0b — operational hardening (NWP grid lifecycle)
**Related**: `store/zarr_nwp_grid_store.py` (`_cleanup_stale_artifacts`,
`archive`), the `nwp_grids` named volume, the `weather_forecasts` table (the
permanent extracted-value archive), Plan 046 (disk sizing),
`docs/architecture-context.md` §"Data retention and cold storage"
(architecture-context.md:2715-2732, the tiered-retention policy this plan must
reconcile against)

**Created**: 2026-07-03

---

## Problem

Each `forecast-cycle` archives the **full ICON grid cube (~1.7 GB zarr)** per
distinct cycle to `/data/nwp_grids`. `_cleanup_stale_artifacts`
(`store/zarr_nwp_grid_store.py:51`) prunes **only stale *versions* of the
current `cycle_stem`** (plus `*_tmp` / `.zarr.old`) — it does **not** prune old
cycles (a different `cycle_stem`, e.g. yesterday's `20260702T00.zarr`). So
distinct cycle archives accumulate **unbounded**: ~4 cycles/day × ~1.7 GB ≈
**~7 GB/day → ~50 GB/week**. Plan 046 sized the Docker VM at ≥100 GB → **~1-2
weeks** of headroom before disk pressure → `nwp.archive` / forecast failures.

The **permanent** NWP archive is the **extracted basin-average values** (the
`weather_forecasts` DB table; architecture: "extracted values, not raw GRIB2,
permanent retention") — **not** the full grid cube. So old grid zarrs are safe
to prune once no runtime path reads the zarr back (see the lifecycle facts
below).

### Zarr lifecycle facts (established by review — grounds the safety argument)

These are confirmed against the code; the retention design rests on them.

1. **The zarr is write-once-per-cycle in Phase A, and read back by no
   operational path.** In `run_forecast_cycle.py` the Phase-A NWP steps run in
   strict order: Step 1.2 `grid_store.archive()` (line 357) → Step 1.3
   `grid_extractor.extract()` (line 409) → Step 1.4
   `weather_forecast_store.store_weather_forecasts(all_records)` (line 441).
   After extraction+persist, nothing in the operational flow re-opens the zarr.
2. **Phase-B readback reads the DB, not the zarr.** The Phase-B readback
   (`run_forecast_cycle.py:897-906`, `nwp_readback_cycle_time`) flows into
   `assemble_station_operational_inputs` →
   `weather_forecast_store.fetch_weather_forecasts()`
   (`services/operational_inputs.py:323-326`) — a `weather_forecasts` DB query.
   It never calls `ZarrNwpGridStore.load()`.
3. **The only `ZarrNwpGridStore.load()` caller is `ReplayNwpAdapter`**
   (`adapters/replay/nwp.py:46`), used exclusively for developer-side fixture
   replay — never against the operational `nwp_grids` volume.
4. **The adapter fallback budget selects *cycles to fetch*, not zarrs to
   read.** `nwp_max_fallback_age_hours = 12` (`config/deployment.py:79`) →
   `max_fallback_steps = ceil(12 / 6) = 2` steps (`run_forecast_cycle.py`
   ~670-671). This governs which upstream STAC cycle the adapter *downloads*;
   it does not read the archived zarr. The only interaction with retention is
   that a cycle within the fallback window may be *re-fetched and re-archived*,
   so the retention window should comfortably exceed the fallback budget to
   avoid churn. **Note:** this churn is an *efficiency* concern, not a *safety*
   one — the fallback adapter (`MeteoSwissNwpAdapter`) re-fetches from STAC, it
   never reads the archived zarr (fact 3), so pruning a zarr inside the fallback
   window causes at worst a fresh STAC download + re-archive, never a missing
   read.

**Conclusion (safety criterion, resolved):** because no operational path reads
an archived operational zarr back after Phase A, the sole safety constraint on
the retention window is **age**: keep a cycle at least as long as the fallback
budget (+ margin) so a cycle that might be re-fetched is not needlessly
re-archived, and long enough to cover any operator-desired reprocessing window.
A DB-persistence check is **not** required for safety and is deliberately **not**
used (see Goal / open question 3 for why age, not DB-presence, is the criterion).

## Goal

Bound the `nwp_grids` disk footprint: prune grid-cube zarrs whose `cycle_time`
is older than a configured retention window, keeping only recent cycles. The
permanent extracted-value archive (DB) is never touched. Prune **by age alone**
(cycle_time older than the window) — NOT by "are the values persisted?". Age is
the reliable proxy: a cycle older than the window has either been extracted or
is unrecoverable, and an age-only rule also reclaims **orphaned zarrs** where
`archive()` succeeded but extraction failed (`run_forecast_cycle.py:416-423`
returns without storing) — exactly the leak a DB-presence gate would leave
behind forever.

### Reconciliation with the existing tiered-retention policy (blocker resolved)

`docs/architecture-context.md:2725` already lists **Raw gridded NWP** with a
hot window of `weather_hot_days` (180) → cold (`cold/nwp_grids/`) → delete at
`max_retention_days`. That tiered lifecycle is **not implemented** for the Zarr
path today (no cold-move step exists; `_cleanup_stale_artifacts` only versions
the current stem), and 180 hot days of ~7 GB/day = ~1.3 TB, far beyond Plan 046
disk sizing. This plan **supersedes the `weather_hot_days` hot-tier for the raw
grid Zarr path only**: the raw grid cube is disposable auxiliary data (the
permanent archive is the extracted values in `weather_forecasts`), so its hot
window is capped independently at `nwp_grid_retention_days` (default 3 days),
NOT at `weather_hot_days`. The cold-move-to-`cold/nwp_grids/` step for raw grids
is dropped as a non-goal (raw cubes are re-derivable by re-fetch from STAC;
cold-archiving 1.3 TB of re-derivable cubes is not worth the disk).

**This plan's implementation MUST update `architecture-context.md`**: change the
"Raw gridded NWP" row (line 2725) so its hot window reads `nwp_grid_retention_days`
(3, not `weather_hot_days`) with no cold tier, and add a note that the extracted
values (`weather_forecasts`) remain the permanent archive per the row above.
Trade-off noted: this removes the (never-implemented) ability to cold-archive
raw cubes; reprocessing with new station geometry beyond 3 days requires
re-fetching from STAC rather than re-reading a cold cube. Operators for whom
long-window reprocessing matters more than disk may raise
`nwp_grid_retention_days`.

## Design decisions (resolved by the plan-review loop, 2026-07-03)

1. **Retention window.** `nwp_grid_retention_days` (default 3 days). Must be
   **>= `ceil(nwp_max_fallback_age_hours / 24) + 1`** (the fallback budget in
   days, plus a one-day margin), so a cycle still within the fallback window is
   not pruned and then immediately re-downloaded + re-archived if the adapter
   selects that same cycle again. This is an **efficiency guard, not a safety
   guard** — the adapter re-fetches from STAC, never from the zarr (facts 3-4),
   so a too-tight window costs a redundant download, not a missing read. The
   `+ 1` margin ensures the most recent fallback-eligible cycle is never pruned
   mid-window even at the supported extreme `nwp_max_fallback_age_hours = 24.0`
   (without the margin, `ceil(24/24) = 1` would let `retention_days = 2` prune a
   day-1 cycle on day 2 while it is still fallback-eligible). With the default
   `nwp_max_fallback_age_hours = 12.0`, `ceil(12/24) + 1 = 2`, so the default
   `3` is safely above the floor. Enforce with a `@model_validator` on
   `DeploymentConfig` (mirrors the existing `max_retention_days >
   forecast_hot_days` constraint at `architecture-context.md:2732`). To avoid a
   new `import math` in `deployment.py`, write the check as pure arithmetic:
   `if self.nwp_grid_retention_days * 24 < self.nwp_max_fallback_age_hours + 24:
   raise ValueError(...)` — the `+ 24` term encodes the one-day margin, so this
   is exactly `nwp_grid_retention_days >= ceil(nwp_max_fallback_age_hours/24)+1`
   for the integer-day field without needing `math.ceil`.
2. **Trigger site (RESOLVED — flow body, not task body).** Two triggers, both
   driven by an age-only criterion:
   - **(a) Inline in the flow BODY, after the Phase-A result is collected.** A new
     `prune_old_cycles(...)` call placed in the **flow function**, immediately
     after `nwp_outcome = nwp_future.result()` returns non-`None`
     (`run_forecast_cycle.py:846`) — **NOT** inside the `_fetch_nwp_task` `@task`
     body (line 441) and **NOT** inside `archive()`. **This placement is the
     resolution of the earlier "after line 441" ambiguity.** Line 441 sits
     inside `_fetch_nwp_task` (signature at `run_forecast_cycle.py:303-313`),
     whose parameters are `adapter, station_configs, cycle_time,
     weather_forecast_store, clock, grid_store, grid_extractor, station_basins,
     grid_archive_base_path` — it receives **neither `DeploymentConfig` nor any
     `retention_days`**, so a prune there could not read the retention window
     without widening the task signature and the `.submit(...)` call at line 826.
     Placing the prune in the flow body instead keeps `_fetch_nwp_task` a pure
     NWP-fetch unit (no new parameter, no extra Prefect serialisation) and puts
     the call where `config`, `clock`, and the base path are already in scope.
     At the flow-body call site the arguments are exactly the question-2b
     signature: `config.nwp_grid_archive_base_path` (skip the prune when `None` —
     same guard as the archive step at line 354), `config.nwp_grid_retention_days`,
     and `clock`. **No `nwp_source` is passed** — the function self-discovers
     every source subdirectory under `base_path` (question 2b), so the flow body
     does not enumerate sources from `flat_weather_configs`.
     **`archive()` is unchanged** (at line 357 extraction/persist
     have not happened yet, and `archive()` is also called by
     `record_fixtures.py` — see question 5 — so it must stay a pure write).
     **Coverage caveat (major finding, acknowledged):** the inline prune fires
     only on the **extraction-success path**. Two valid non-failure early returns
     inside `_fetch_nwp_task` write a zarr (line 357) but return *before*
     extraction/persist — `grid_extractor is None` (returns at line 381) and
     `not configs_for_source` (returns at line 396). Both leave an **orphaned
     zarr** that the inline prune never sees. The extraction-failure early return
     (line 416-423) is a third such path. Because `nwp_outcome` is still non-
     `None` on the two early-return paths, a flow-body prune at line 846 *does*
     still fire for those cycles (it is gated on `nwp_outcome is not None`, not on
     records being stored) — but the flow-body prune sweeps the source dir by
     age, so it reclaims those orphans on the *next* successful cycle regardless.
     Net: the cron backstop (b) is **not merely a fallback** — it is the
     guaranteed reclaimer for any cycle whose forecast run aborts before the
     flow-body prune (e.g. `nwp_outcome is None` at line 847, or a crash between
     archive and the prune call).
   - **(b) A cron backstop** on a dedicated maintenance flow (see the flow-number
     note below) that prunes any cycle older than the window, covering cycles the
     inline trigger never reached (a forecast run that aborted at
     `nwp_outcome is None`, line 847, or crashed mid-flow). It sweeps **all** NWP
     source subdirectories by age (see the source-discovery decision in question
     2b). Both triggers use the same age-only rule; there is no DB join.
   Preferred: (a) + (b). **Flow-number note:** Flow 11 is already
   **"NWP gap recovery"** in the authoritative architecture
   (`architecture-context.md:1171`, triggered by Flow 4 step 4.1, v1-deferred).
   The cron backstop MUST NOT reuse "Flow 11". Assign it a fresh operational
   flow slot (grill-me to pick the number, e.g. a `grid-retention` maintenance
   deployment) or fold it into an existing maintenance flow as an explicit
   sub-step. The plan must not shadow the real Flow 11.
   **2b. NWP-source discovery (RESOLVED — signature carries no source).** The
   on-disk layout is `{base_path}/{safe_source}/{cycle_stem}.zarr`
   (`zarr_nwp_grid_store.py:31-32`), so each NWP source occupies its own
   subdirectory. The cron backstop does not (and should not) know the source
   list a priori. **Decision:** the prune function takes **no `nwp_source`
   argument** — its signature is
   `prune_old_cycles(base_path, retention_days, clock)`, and it **discovers
   sources by iterating the immediate subdirectories of `base_path`**
   (`base_path.iterdir()`, dirs only), then prunes age-old `*.zarr` cycle dirs
   within each. This makes the cron backstop implementable with zero source
   knowledge and automatically covers any source added later (no hard-coded
   per-source loop that would silently skip a new source). The inline flow-body
   trigger (2a) calls the **same** signature — it too sweeps every source
   subdirectory, which is harmless (each source dir is checked by age) and keeps
   one code path. `safe_source` sanitisation (`Path(nwp_source).name`,
   `zarr_nwp_grid_store.py:31`) already guarantees each subdir is a single path
   component, so `iterdir()` stays within `base_path`. Each cycle dir's age is
   read from its `cycle_stem` (`%Y%m%dT%H`, `zarr_nwp_grid_store.py:32`), not
   from filesystem mtime, so re-archiving does not reset the clock.
3. **Safety criterion (resolved — age-only).** A cycle is prunable iff
   `cycle_time < clock() - nwp_grid_retention_days`. It is NOT gated on
   "values persisted": (i) no operational path reads an archived zarr after
   Phase A (see Zarr lifecycle facts 1-3 — Phase-B reads the DB, and the only
   `load()` caller is `ReplayNwpAdapter` on fixture dirs, never the operational
   volume); (ii) a DB-presence gate would leak orphaned zarrs from
   extraction-failure runs forever (`run_forecast_cycle.py:416-423`). Age alone
   is both sufficient and complete. **Resolved-no-action** re. "confirm the
   readback path can never request a pruned cycle": confirmed — Phase-B readback
   is a `weather_forecast_store` DB query (`operational_inputs.py:323-326`), so
   pruning operational grid zarrs cannot affect it.
4. **Config surface.** Add `nwp_grid_retention_days: int = 3` to
   **`DeploymentConfig` near `src/sapphire_flow/config/deployment.py:73`**
   (a top-level field alongside `weather_hot_days` at line 73 — note line 68 is
   the `class DeploymentConfig(BaseModel):` header, not a field), with the
   `@model_validator` from question 1. Because `load_config()` pops the
   `[adapters.*]` sections (`deployment.py:319`), the field must be a
   **top-level TOML key** (like `weather_hot_days`), NOT under
   `[adapters.weather_forecast]` — otherwise it is stripped before
   `model_validate`. Document it in `docs/spec/config-reference.toml`.
5. **`record_fixtures.py` must be unaffected.** The recording tool calls
   `ZarrNwpGridStore().archive()` directly (`tools/record_fixtures.py:294,330`)
   against an operator-supplied fixture directory, which must remain a stable
   reference archive, NOT a rolling store. Because the prune is a **separate
   method / flow-level call** (question 2a), and `archive()` stays a pure write,
   the recording tool is automatically unaffected — it never invokes the prune.
   No `enable_retention_prune` flag on `archive()` is needed. Additionally, the
   prune is triggered from the **flow body** (question 2a), never from
   `_fetch_nwp_task`, so no task-signature or `.submit(...)` change touches the
   fetch path the recording tool shares conceptually.

## Protocol / typing decision (major resolved)

`NwpGridStore` (`protocols/stores.py:725-732`) currently exposes only `archive`
and `load`. Decision: **do NOT add prune to the Protocol.** The prune is
implemented as a **module-level function**
`prune_old_cycles(base_path, retention_days, clock)` (or a concrete-class method
on `ZarrNwpGridStore`) that the flow body calls directly against the operational
`nwp_grids` base path — it is a filesystem-lifecycle operation, not part of the
store's read/write contract, and it must never be reachable via `ReplayNwpAdapter`
fakes (which operate on fixture dirs). This keeps the Protocol and all existing
fakes untouched (no silent fake gaps, no conformance-test churn at
`test_zarr_nwp_grid_store.py:134`), at the cost that the prune is not
polymorphic over `NwpGridStore` — acceptable, since only the operational Zarr
path is ever pruned. Trade-off noted here rather than silently widening the
Protocol.

## Non-goals

- Pruning the extracted basin-average values (permanent, in the DB
  `weather_forecasts` table).
- Cold-archiving raw grid cubes to `cold/nwp_grids/` (dropped — see
  "Reconciliation with the existing tiered-retention policy"; raw cubes are
  re-derivable by STAC re-fetch).
- Zarr-cycle reuse / a fetched-cycle cache. **Note (major finding — corrected):**
  an earlier draft claimed Plan 090 **P2** introduces "archive/reuse a fetched
  cycle" and that retention must not prune a cycle P2 would reuse. That is
  **false**. Plan 090 P2 is a *STAC-probe precision refinement*
  ("terminal-valid-time STAC probe per variable/member for exact pre-download
  coverage", `docs/plans/090-nwp-incomplete-cycle-selection.md:99-101`) — it
  introduces **no new zarr-load path** and no cycle cache. No
  retention/reuse-window reconciliation is required, and the earlier "open
  question 5" about reconciling those windows was based on a phantom coupling and
  has been removed. If a genuine fetch-cache / cycle-reuse plan is ever written,
  it will define its own retention interaction at that time.

## Interim operational mitigation (until this ships)

On a long-running deployment, **monitor disk** (`df -h`, or the Docker VM disk
usage) and, if it pressures, manually remove old cube archives —
`rm -rf /data/nwp_grids/icon_ch2_eps/<old YYYYMMDDTHH>.zarr` for cycles older
than a couple of days (the extracted values are already in `weather_forecasts`).
Do **not** touch the DB or the `weather_forecasts` rows (the permanent archive).

## Process

DRAFT until a grill-me resolves the retention window default + the cron
backstop's flow number, then phases + JSON graph → READY. Implementation
touches:

1. **`config/deployment.py`** — add top-level `nwp_grid_retention_days: int = 3`
   near line 73 (alongside `weather_hot_days`; line 68 is the class header) +
   a `@model_validator(mode="after")` mirroring `_validate_retention`
   (`deployment.py:164-171`). Use the pure-arithmetic form
   `if self.nwp_grid_retention_days * 24 < self.nwp_max_fallback_age_hours + 24:
   raise ValueError(...)` so **no `import math` is added** to `deployment.py`
   (current imports are `os`, `re`, `datetime.timedelta`, pydantic validators —
   `deployment.py:1-8`). Wire through `load_config()` as a top-level key
   (survives the `data.pop('adapters')` sweep at `deployment.py:319`).
2. **`store/zarr_nwp_grid_store.py`** — add the age-only, **source-discovering**
   `prune_old_cycles(base_path, retention_days, clock)` function/method (NO
   `nwp_source` argument — it iterates `base_path.iterdir()` subdirs and derives
   each cycle's age from its `cycle_stem`, per question 2b). Injected `clock`
   per the DI rule — no `datetime.now()` in logic. `archive()` is left unchanged
   (pure write).
3. **`flows/run_forecast_cycle.py`** — call `prune_old_cycles(...)` in the
   **flow body**, immediately after `nwp_outcome = nwp_future.result()` returns
   non-`None` (line 846), passing `config.nwp_grid_archive_base_path` (guard:
   skip when `None`), `config.nwp_grid_retention_days`, and `clock`. **Do NOT**
   place it inside `_fetch_nwp_task` (line 441): that task's signature
   (`run_forecast_cycle.py:303-313`) receives no `DeploymentConfig` and no
   retention value, so a prune there would require widening the task signature
   and the `.submit(...)` call at line 826. Flow-body placement avoids both
   (question 2a). No change to `_fetch_nwp_task` or its `.submit(...)` call.
4. **New cron maintenance flow** (fresh flow number, NOT Flow 11) — age-only
   backstop prune calling the same `prune_old_cycles(base_path, retention_days,
   clock)`, which self-discovers all NWP source subdirs; register a scheduled
   deployment. This flow is the guaranteed reclaimer for extraction-skipped and
   aborted-run orphans the inline trigger misses (question 2a coverage caveat).
5. **Docs** — update `docs/architecture-context.md:2725` (raw-grid row →
   `nwp_grid_retention_days`, no cold tier), update `config-reference.toml:24`
   to remove "raw grids" from the `weather_hot_days` comment and cross-reference
   `nwp_grid_retention_days` for the raw-grid-zarr hot window, and add the new
   `nwp_grid_retention_days` field to `docs/spec/config-reference.toml`.

RED-confirmed tests: (i) a cycle older than the window is pruned while recent
cycles are kept; (ii) an **orphaned** zarr (archived, no DB record) older than
the window is also pruned (age-only, no DB gate); (iii) the recording tool's
`archive()` path leaves fixture directories untouched (no prune fires);
(iv) config validation rejects `nwp_grid_retention_days` below the fallback
budget + margin (e.g. `nwp_max_fallback_age_hours=24.0` rejects
`nwp_grid_retention_days=2`, accepts `3`); (v) `prune_old_cycles` **discovers
multiple NWP source subdirectories** under `base_path` and prunes age-old cycles
in each without being told the source names (covers a source added after
deployment); (vi) an orphaned zarr from an **extraction-skipped early return**
(`grid_extractor is None` / `not configs_for_source`,
`run_forecast_cycle.py:381,396`) is reclaimed by the cron backstop even though
the inline flow-body prune never fired for that failed run.
