---
id: 124
title: Forecast-flow correctness fixes (pre-existing bugs surfaced by the Plan 123 review)
status: DRAFT (stub)
depends_on: []
owner: unassigned
created: 2026-07-17
---

# Plan 124 — Forecast-flow correctness fixes

> **Stub — near-term priority (owner, 2026-07-17).** The Plan 123 `plan`-workflow review
> surfaced several correctness issues that are **pre-existing in the live forecast flow**
> (`run_forecast_cycle.py` + the input-assembly/FI path), independent of 123's new feature.
> Per the owner: *"we cannot afford bugs"* and the critical path is `fc`-first — so these
> are fixed **before** [[Plan 123]] resumes. Each item below is **review-flagged and must be
> VERIFIED (reproduced red-first) before fixing** — Codex grounded them at `file:line`, but
> some are latent (trigger only under specific configs). Run the `plan` workflow to develop +
> pressure-test, then `implement` with a red-first test per bug.

## Candidate defects (verify → fix, most operationally-relevant first)

### 1. Runoff-only / no-NWP runs still run NWP-staleness health
The flow runs `_check_nwp_grid_staleness` whenever `nwp_enabled` is true
(`run_forecast_cycle.py:1628`); that check marks stale when **no latest cycle exists**
(`:691`) and degrades pipeline health via `nwp_grid_stale` (`:2267`). A station/run whose
assigned models need **no NWP** (all fallback/runoff-only) can therefore emit a **false
NWP-staleness health degradation**. **Likely affects the live Swiss deployment** (runoff-only
stations). Fix: gate NWP-staleness on *"NWP is genuinely not fetched this run"* — i.e.
`not skip_nwp_fetch` (equivalently `nwp_enabled and not runoff_only_mode and flat_weather_configs`,
per `run_forecast_cycle.py:1538`) — **not** `not effective_runoff_only`. **Do not conflate the
two "no-NWP" states** (see Verification #1 below): `skip_nwp_fetch` means NWP was never needed
(adapter off, or no station in the run needs weather config), whereas `nwp_unavailable_runtime`
means NWP *was* needed but the fetch found no adequate cycle — and that second case is the one
genuine NWP-delivery failure we still want the staleness check to surface. Regression (two cases):
(a) a runoff-only run (`skip_nwp_fetch` true) emits no NWP-delivery health record and no
`nwp_grid_stale` degradation; **and** (b) an NWP-needed run where the fetch is unavailable this
cycle (`nwp_unavailable_runtime` true, `skip_nwp_fetch` false) **still runs** the staleness
check and can still degrade — the delivery-failure signal is preserved.

### 2. Station-vs-group active-assignment semantics are inconsistent
`fetch_model_assignments` returns **all** statuses (`station_store.py:212`). The **station**
path uses every fetched assignment for superset assembly and forecasting
(`run_forecast_cycle.py:1440`, `run_station_forecast.py:327`), while the **group** path
filters to **active** assignments (`run_forecast_cycle.py:2034`). So an *inactive* station
assignment is still forecast on the station path — an inconsistency that can forecast with a
retired/disabled model. Fix: define and share **one active-assignment set** for both paths.
Regression: an inactive assignment is excluded from station forecasting + input assembly.

### 3. Forcing-column shape breaks FI `SINGLE` consumers in mixed runs
`_pivot_nwp_records` emits **only** member-suffixed (`feature_member`) columns when any member
is present (`operational_inputs.py:181`), but FI `SINGLE` prediction goes through direct
`model.predict` and `_frame_with_column` requires the **bare** feature name
(`forecast_interface.py:986`). A run that mixes an `ENSEMBLE` model and a `SINGLE`-with-NWP
model on the same station therefore breaks the `SINGLE` consumer. **Latent** — triggers only
under a mixed assignment; confirm whether any current/near config hits it. Fix: project a bare
column **only when the fetch is genuinely control-only** — i.e. the record set's member ids are a
subset of `{None, 0}` with no other members present — **not** merely "member 0 is present in the
fetch." **Do not** alias member 0 into a bare column in a full ensemble run: for Recap/ECMWF ENS
the `fc`=HRES trajectory is `member_id=0` alongside 50 `pf` members (project memory
*Recap IFS fc=HRES as ensemble member_id=0*) and has **no** special "control" status distinct from
any other member — projecting a bare `precipitation` there would silently feed a `SINGLE` model
only the single HRES trajectory, an un-reviewed semantic change for non-ICON sources. The
control-only case (ICON deterministic run, or a fetch that returned only `fc`) is the one where a
bare column is correct. Retain suffixed columns for fan-out in the ensemble case. Regression:
(a) a `SINGLE` and an `ENSEMBLE` model both forecast in one **control-only** mixed run (bare column
present); and (b) a full-ensemble run that includes member 0 does **not** emit a bare
`precipitation` column (only suffixed `precipitation_0..N`). (This is the D8 territory shared with
123 — decide ownership.)

## Verification (2026-07-17, orchestrator — read against the code, all CONFIRMED)

- **#1 CONFIRMED.** `run_forecast_cycle.py:1626` computes `effective_runoff_only = skip_nwp_fetch
  or nwp_unavailable_runtime`, but `:1628` gates `_check_nwp_grid_staleness` on `nwp_enabled`
  alone — so a no-NWP-needed run still runs the staleness check. **Fix must gate on
  `not skip_nwp_fetch`, NOT `not effective_runoff_only`** — the two are not interchangeable:
  - `skip_nwp_fetch` (`:1538 = runoff_only_mode or not flat_weather_configs`) = NWP genuinely
    not fetched this run (adapter disabled, or no station needs weather config). Suppressing
    the staleness check here is exactly the false-positive we are removing.
  - `nwp_unavailable_runtime` (`:1616`) = NWP *was* needed but the fetch found no adequate cycle
    (`NoCycleAvailableError`, `:887-894`). **Critically, that `NoCycleAvailableError` handler
    appends NO pipeline-health record** (unlike the sibling `RecapConfigurationError` /
    `GatewayResolutionError` branches at `:895-909` and `:910+`, which write NWP_DELIVERY
    records). So today `_check_nwp_grid_staleness` — via its stale `fetch_latest_cycle_time`
    lookup — is the **only** code path that surfaces a health record for the real
    "NWP needed but unavailable this cycle" failure. Gating on `not effective_runoff_only`
    would additionally suppress the check whenever `nwp_unavailable_runtime` is true, deleting
    that sole detection and trading the false-positive for a **false-negative regression** on
    the exact "we cannot afford" axis. Therefore: gate on `not skip_nwp_fetch` only, and add a
    regression asserting the staleness check STILL fires in the `nwp_unavailable_runtime` branch.
- **#2 CONFIRMED.** `station_store.py:212` `fetch_model_assignments` has NO status filter (returns
  all); station path consumes them unfiltered (`:1440-1443`; `run_station_forecast.py` has no
  assignment-status filter — grep clean); group path filters `status == ACTIVE` (`:2039`). Real
  asymmetry: station forecasting includes INACTIVE assignments.
- **#3 CONFIRMED — on the `fc`-first critical path, NOT merely latent.** `_pivot_nwp_records`
  (`operational_inputs.py:181`) enters the suffixed-column branch whenever ANY record has a
  `member_id`; since `fc` = `member_id=0`, even a **control-only** fetch emits `precipitation_0`,
  and FI `SINGLE`'s `_frame_with_column` (`forecast_interface.py:987`) requires the **bare**
  `precipitation` → `ConfigurationError`. So this **directly blocks Sandro's control-only
  models**. **Fix scoping (do NOT over-generalize):** the bare-column projection must key on
  "control-only fetch" (`{r.member_id for r in records}` ⊆ `{None, 0}`), not on "member 0 is
  present." In a full 51-member Recap ENS fetch (`fc`=member 0 + `pf` 1..50), member 0 is a normal
  ensemble member with no control status (project memory *Recap IFS fc=HRES as ensemble
  member_id=0*), so aliasing it into a bare `precipitation` would silently change `SINGLE`-model
  input semantics for that source. Verify this distinction explicitly during `implement`.
  **Ownership note:** this is the D8 normalization — decide whether it lands here (124)
  or as the first task of 123's control-only slice; either way it is `fc`-critical.

## Notes / non-goals

- Does **not** implement Plan 123's model-driven membership feature — only the pre-existing
  correctness fixes that must be clean first. (123's group-membership Phase-A/B2 timing concern
  stays with 123, since nothing aggregates run-level membership today.)
- Verify each defect against the code and (where feasible) a repro before committing to a fix —
  do not fix a "bug" that cannot be reproduced.
- Source: Plan 123 `plan`-workflow escalation, 2026-07-17 (see `docs/plans/123-...md`
  "Open blockers").

## Escalation + re-assessment (`plan` workflow, 2026-07-17)

The `plan` workflow ESCALATED (stalled, 2 rounds, 1 blocker + 3 majors; Codex hung in round 1,
so round 2 carried the real review). **NOT-READY.** Its deeper trace corrected my earlier
verification — the honest result reshapes this plan:

- **Defect #1 (staleness) — NOT a standalone live bug; RE-HOME to Plan 123.** My verification
  confirmed the *code structure* (staleness gated on `nwp_enabled`, not `effective_runoff_only`)
  but over-claimed the live impact. Deeper trace: `skip_nwp_fetch = runoff_only_mode or not
  flat_weather_configs` (`run_forecast_cycle.py:1538`) is driven by the deployment toggle +
  binding resolution, **not by model requirements**. A no-NWP-model station with a valid FORECAST
  binding keeps `skip_nwp_fetch == False`, so gating on `not skip_nwp_fetch` changes nothing
  there. The only path the fix affects (`flat_weather_configs` empty = *every* station fails
  binding) is already health=`FAILED` (`:769`, `stations_failed == stations_attempted`), which
  dominates the DEGRADED staleness signal — so no observable false-positive today. And
  `runoff_only_mode` is already excluded by the outer `if nwp_enabled` gate. **⇒ #1 becomes real
  only once Plan 123 introduces model-requirement-driven skip (`NONE` membership); it belongs
  with 123, not as standalone pre-work.**
- **Defect #2 (active-assignment) — CONFIRMED, the one clean standalone 124 fix.** Station path
  forecasts INACTIVE assignments; group path filters `ACTIVE`. Bounded (only ACTIVE/INACTIVE
  exist). Open Qs: single shared active-assignment helper vs two filters; and walk the downstream
  call-site impact (`run_forecast_cycle.py:1440`, `run_station_forecast.py:327`) so the fix
  doesn't change `forecasts_stored`/health for currently-passing runs.
- **Defect #3 (control-only column shape) — CONFIRMED + fc-critical, but Plan-123 territory
  (D8).** Real and on the `fc`-first path. Scope nuance the review pinned: the fix must key on a
  **control-only fetch** (`member_id ∈ {None, 0}` for the whole run), NOT merely "member 0
  present," or it would silently change semantics for full 51-member Recap ENS runs. This is the
  D8 normalization ⇒ **fold into Plan 123's control-only slice**, not standalone here.

**RECOMMENDED RE-SCOPE (decide on pickup):** narrow **124 to Defect #2 only** (a small, bounded
active-assignment fix — safe to `implement` on its own), and **move Defects #1 and #3 into Plan
123's control-only slice** (both only become real/needed as part of 123's model-driven membership).
That leaves a clean, tractable 124 and stops "fixing" #1 as a live bug it isn't.

**Residual questions (grill-me):** (a) confirm the re-scope above; (b) #2 fix shape (shared helper
vs two filters) + downstream call-site impact; (c) if 124 = only #2, do we still run it through
`plan`+`implement` (small enough to implement directly with a red-first test?).
