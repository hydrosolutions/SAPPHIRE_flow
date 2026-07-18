---
id: 123
title: Model-driven forcing membership (fetch what models require; run-level cycle resolution)
status: DRAFT (PAUSED)
depends_on: [082, 124]
owner: unassigned
created: 2026-07-17
---

# Plan 123 — Model-driven forcing membership

> **PAUSED 2026-07-17; re-scoped 2026-07-18 (owner decisions).** The `plan` workflow
> escalated (stalled at 2 blockers + 4 majors — see "Open blockers"). The 124 review
> then re-assessed the "pre-existing bugs": only the **active-assignment** inconsistency
> is a standalone live bug ([[Plan 124]], now narrowed to that one fix). The other two
> are **folded into THIS plan** — see "Folded-in from 124" below:
> - **Staleness gating** (ex-124 #1): only a bug once `NONE` membership skips the fetch by
>   model requirement — so it lives with 123's `NONE` handling, gated on `not skip_nwp_fetch`.
> - **Control-only forcing-column normalization** (ex-124 #3): this **is** D8; key it on a
>   control-only fetch (`member_id ∈ {None,0}` run-wide), not "member 0 present."
>
> **Critical path (owner):** deployment-readiness is **`fc`/control-only first**; `pf`/ENSEMBLE
> membership is **not currently critical** (Sandro's live models are control-only). Sequence:
> land 124 (active-assignment) → resume 123 scoped to `CONTROL_ONLY` + `NONE` (make the live
> `fc`-only path work: no hard-abort, correct staleness gating, bare control column for FI
> `SINGLE`) → defer full `ENSEMBLE`/mixed-run membership as a non-critical follow-on.
>
> **Pending decisions before 123 can be READY** (the grill-me): (1) the bare-vs-suffixed
> forcing-column contract (D8) — bare column only for a control-only fetch, suffixed retained
> for ensemble; (2) confirm the `fc`-first / `pf`-later scope split; (3) the residual blockers/
> majors below. Do NOT implement 123 until these are decided (124 may land first, independently).

## Folded-in from Plan 124 (2026-07-18)

Two forecast-flow issues moved here from the original 3-defect Plan 124, because both only become
real *as part of* 123's model-driven membership (verified during the 124 review):

- **NWP-staleness gating (ex-124 #1).** Today `_check_nwp_grid_staleness` runs whenever
  `nwp_enabled` (`run_forecast_cycle.py:1628`), but `skip_nwp_fetch` is NOT model-driven, so there
  is no observable false-positive today. Once 123's `NONE` membership skips the fetch because no
  assigned model needs NWP, staleness must be gated on **`not skip_nwp_fetch`** (NOT
  `not effective_runoff_only` — that would also suppress the `nwp_unavailable_runtime` case, whose
  `NoCycleAvailableError` handler writes NO health record, so the staleness check is the *only*
  detector of a genuine "NWP needed but unavailable" failure; deleting it = a false-negative on the
  "can't afford" axis). Acceptance: a `NONE` run emits no `nwp_grid_stale`; an NWP-needed run with
  an unavailable cycle STILL degrades.
- **Control-only forcing-column normalization = D8 (ex-124 #3).** `_pivot_nwp_records`
  (`operational_inputs.py:181`) emits suffixed columns whenever any record has a `member_id`; since
  `fc`=`member_id=0`, a control-only fetch yields `precipitation_0`, but FI `SINGLE`'s
  `_frame_with_column` (`forecast_interface.py:987`) needs bare `precipitation` → `ConfigurationError`
  — blocks Sandro's control-only models. Fix keys on **control-only fetch** (`{r.member_id} ⊆ {None,0}`
  run-wide), NOT "member 0 present": in a full 51-member Recap ENS, member 0 is a normal ensemble
  member with no control status, so aliasing it to a bare column would silently change `SINGLE`
  semantics. Acceptance: (a) control-only run → bare column present, FI `SINGLE` forecasts;
  (b) full-ensemble run incl. member 0 → NO bare column (suffixed only), fan-out intact.

> Surfaced by live-testing the merged Plan 082 adapter against the real Gateway
> HRU `12300` (2026-07-17). Touches merged adapter behaviour and the forecast
> flow's NWP fetch/cycle path, so plan-first with an independent review (the
> `plan` workflow). This revision folds review rounds 1–2 (Codex + design) into
> concrete decisions, a phase/task breakdown, exact gates, and a dependency
> graph. It **narrows scope to a single resolved cycle per run** (see D3) — the
> previous "per-requirement divergent cycles within one run" goal is moved to a
> Non-goal with an explicit re-open trigger.
>
> **Round-2 changes:** (a) added D8 — a downstream forcing-normalization
> contract so control-member forcing is consumable by FI `SINGLE` models (was a
> silent blocker); (b) `ForcingMembership` is now three-state (`NONE` /
> `CONTROL_ONLY` / `ENSEMBLE`) to model the "no NWP required" runs (fallback
> models); (c) D3's ENSEMBLE cycle resolution is unified to the existing `fc`
> probe + D7 reactive degrade (no unbuilt complete-ensemble probe); (d) D4's
> implementer/fake list now covers the in-test fakes plus an `rg` sweep step;
> (e) added a docs task; (f) reworded the `EnsembleMode` axis description.

## Problem

`RecapGatewayForecastAdapter.fetch_forecasts` (`src/sapphire_flow/adapters/recap_gateway.py:704`)
**hardcodes a 51-member fetch** — `fc` (member_id 0, `recap_gateway.py:744`) plus
`for member in range(_PF_MEMBER_MIN, _PF_MEMBER_MAX + 1)` over all 50 `pf`
members (`recap_gateway.py:747`). It never consults what the assigned models
actually require. Two consequences, both confirmed live:

1. **Wrong for the models the live Nepal deployment runs.** Sandro's operational
   Nepal models (external, owner-provided; not in this repo) currently consume
   the **`fc` control member only**, for efficiency. Fetching 50 `pf` members per
   cycle per variable per station is wasted work — and worse, it makes the fetch
   fail when `pf` is unavailable even though the assigned models need none of it.
2. **`fc`-before-`pf` is a normal per-cycle window, by ECMWF design — not a
   backfill artifact.** ECMWF disseminates the control (HRES / "ENS control")
   **earlier than the perturbed members**, and IFS **Cycle 50r1** preserves this
   (the renamed control "continues to be made available earlier than the ensemble
   perturbed members"). So every cycle has a window where `fc` is published and
   `pf` is not yet complete.

**Live evidence (HRU `12300`, 2026-07-17):** `_resolve_effective_cycle`
(`recap_gateway.py:673`) probes availability using `fc` only via
`resolve_latest_cycle`, locks onto the newest `fc` cycle (`2026-07-17 00Z`, which
had `fc` but no `pf` yet), then the unconditional `pf` loop demands the 50 `pf`
members at that cycle → the Gateway returns "No IFS dataset found". That surfaces
as a **generic `AdapterError`**, which the flow's `_fetch_nwp_task` catches at
`run_forecast_cycle.py:956` (`except Exception`) and turns into a **fatal `None`
return** (abort), *not* the graceful degrade-to-runoff-only that
`RecapDataUnavailableError` gets at `run_forecast_cycle.py:939`. So the current
failure mode is a hard abort of the whole NWP fetch, not a per-station skip.

## What already exists (survey — do not re-derive)

The single-vs-ensemble distinction is **already modelled and wired end-to-end**;
this plan reuses it rather than inventing a new signal or (as the stub feared)
escalating to an FI-repo issue:

- FI expresses it: `FutureKnownVariable.ensemble_mode: EnsembleMode` with values
  `SINGLE` / `ENSEMBLE`
  (`.venv/lib/python3.12/site-packages/forecast_interface/input/variable.py:40`).
- The FI adapter projects it into SAP3: `_project_requirements` sets
  `any_ensemble_future` when any future variable is `ENSEMBLE`
  (`src/sapphire_flow/adapters/forecast_interface.py:474`) and stamps
  `ModelDataRequirements.ensemble_mode`
  (`src/sapphire_flow/adapters/forecast_interface.py:525`).
- The domain type carries it, defaulting to `SINGLE`
  (`src/sapphire_flow/types/model.py:271`).
- **Provenance (corrected):** `ensemble_mode` is *set* by the FI adapter's
  `_project_requirements` (`src/sapphire_flow/adapters/forecast_interface.py:525`)
  from the model's declared FI variables — it is **not** computed or persisted by
  onboarding. `model_onboarding.py:525` only *reads* it to decide whether the
  smoke-test must fan a synthetic ensemble out for the operational floor (not a
  membership source of truth). `build_superset_requirements`
  (`src/sapphire_flow/services/operational_inputs.py:240`, field set at
  `:277`) is a per-model requirement **superset for input assembly** that copies
  `requirements[0].ensemble_mode` — it is neither persistence nor the run-level
  aggregation this plan introduces (D1). Do not mistake either for membership
  truth.
- The forecast services already branch on it: coverage
  (`src/sapphire_flow/services/nwp_coverage.py:70,90`), station fan-out
  (`src/sapphire_flow/services/run_station_forecast.py:139,166` →
  `src/sapphire_flow/services/ensemble_fanout.py`), group path
  (`src/sapphire_flow/services/run_group_forecast.py:387,394`).

The **only** thing FI's enum cannot express is an *exact ECMWF fc/pf member
subset* (e.g. "members 1–10 only"). This plan does not need that, so **no FI
gap and no FI-repo issue** — see D2.

The repo's own in-tree NWP model, `NwpRegression`, declares
`ensemble_mode=EnsembleMode.ENSEMBLE` for both future variables
(`src/sapphire_flow/models/nwp_regression.py:133,140`) with locked ensemble-mode
tests (`tests/unit/adapters/test_forecast_interface_ensemble_mode.py`). So the
in-repo world is **ensemble**; the *live Nepal operational* models are
control-only. Both must stay correct.

## Goal

Make **NWP forcing membership model-driven at the run level**, per the FI
principle (models declare requirements; SAP3 delivers exactly what is required),
using the existing `ModelDataRequirements.ensemble_mode` **and**
`future_dynamic_features` signals — not a hardcoded 51-member fetch. The run
resolves one of **three** forcing memberships (D1):

- **`NONE` — no assigned model declares any `future_dynamic_features` → fetch no
  NWP at all.** (All fallback models: `linear_regression_daily.py:57`,
  `climatology_fallback.py:48`, `persistence_fallback.py:48` all declare
  `future_dynamic_features=frozenset()`.) This reuses the flow's existing
  `skip_nwp_fetch` runoff-only path (`run_forecast_cycle.py:1538`) — see D1; it is
  not a new abort.
- **`CONTROL_ONLY` — assigned models need NWP but none is ensemble → fetch `fc`
  (member 0), skip `pf` entirely, never abort on missing `pf`.** (The live Nepal
  deployment.) The fetched control forcing is normalized so FI `SINGLE`
  consumers can read it (D8).
- **`ENSEMBLE` — any assigned model is ensemble → fetch the full 1×`fc` +
  50×`pf` set.** (The in-repo `NwpRegression`; required acceptance coverage.)
- **Run-level "strictest-wins" cycle resolution:** the run resolves **one**
  effective cycle via the existing `fc` probe (`resolve_latest_cycle`,
  `recap_gateway.py:349`) for **both** `CONTROL_ONLY` and `ENSEMBLE` (D3 —
  round-2 simplification: no separate complete-ensemble probe is built). A `pf`
  member missing at that cycle is handled reactively by D7 (map to
  `RecapDataUnavailableError` → graceful degrade), never a hard abort. This fixes
  the live control-only hard-abort and removes the fatal-abort mapping for
  ensemble runs — without touching the flow's single `nwp_cycle_reference_time` /
  readback thread (`run_forecast_cycle.py:1644,1662`) or the adapter's per-HRU
  batching loop (`recap_gateway.py:730`).

**Explicit trade-offs (noted, not regressed):**
1. **Shared cycle across mixed memberships.** A run mixing control-only and
   ensemble stations resolves to one shared `fc`-probed cycle; per-model cycle
   divergence within one run is deferred (see Non-goals).
2. **No proactive complete-ensemble walk-back (round-2).** By unifying cycle
   resolution to the `fc` probe for both memberships (D3), an ENSEMBLE run at a
   cycle whose `fc` is published but whose `pf` set is still incomplete degrades
   that cycle to runoff-only (D7) and retries at the next cycle 6h later, instead
   of automatically walking back to an older *complete* ensemble cycle. This is
   accepted because there is **no live operational ensemble consumer today** (only
   the in-repo `NwpRegression`; see "What already exists"), exactly mirroring the
   reasoning that defers per-model divergent cycles. **Re-open trigger:** a live
   ensemble consumer is onboarded and the 6h-retry latency is shown to matter —
   then build the `pf`-completeness probe (sizing its per-walk-back Gateway cost;
   `resolve_latest_cycle` today issues one `fc`-only `client.ecmwf.ifs_forecast`
   call per candidate cycle, `recap_gateway.py:349`, with no completeness/manifest
   primitive in `RecapClientLike`, `recap_gateway.py:221`).

## Design decisions (resolves stub Forks 1–4)

**D1 — Membership source = `future_dynamic_features` + `ensemble_mode`,
aggregated to a run-level three-state `ForcingMembership`.** (Resolves Fork 1;
round-2: adds the `NONE` state the two-state model missed.) The flow already
resolves each operational station→model assignment and each group→model run
(`run_forecast_cycle.py:2020`, `discover_group_runs`). Derive the run-level
membership from **both** requirement fields, strictest-wins:
  - `ENSEMBLE` if **any** assigned station-model **or** group-model has
    `ensemble_mode is EnsembleMode.ENSEMBLE`;
  - else `CONTROL_ONLY` if **any** assigned model declares a non-empty
    `future_dynamic_features` (`types/model.py:271`);
  - else `NONE` (no assigned model needs NWP — every fallback model declares
    `future_dynamic_features=frozenset()`:
    `linear_regression_daily.py:57`, `climatology_fallback.py:48`,
    `persistence_fallback.py:48`).

  Introduce enum `ForcingMembership { NONE, CONTROL_ONLY, ENSEMBLE }` (do not
  overload `EnsembleMode`, which describes *model input / ensemble fan-out*
  semantics; membership describes the *run-level Gateway fetch policy* — see D5).
  Pass it into the adapter (D4). Group-model assignments are included in the
  aggregation so the group path (`run_group_forecast.py:387`) is covered by the
  same single resolved cycle.

  **`NONE` reconciles with the existing skip path, not a new branch.** The flow
  already computes `skip_nwp_fetch = runoff_only_mode or not flat_weather_configs`
  (`run_forecast_cycle.py:1538`) and takes the runoff-only path when no weather
  configs exist. A `NONE` membership is the model-driven equivalent: T3.1 maps
  `NONE` onto that existing `skip_nwp_fetch` so no `fetch_forecasts` call is made
  and no abort accounting is triggered. `NONE` is therefore an aggregation output
  the flow already knows how to honour, not a fourth adapter code path.

**D2 — FI adherence: no gap, no FI issue.** (Resolves Fork 2.) The
single-vs-ensemble distinction is already expressible via
`FutureKnownVariable.ensemble_mode` and already projected to
`ModelDataRequirements.ensemble_mode`. SAP3 consumes an *existing* FI signal; it
does not work around the contract. An FI-repo issue is warranted **only** if a
future model needs an exact fc/pf member *subset* (beyond SINGLE/ENSEMBLE) — out
of scope here; recorded as a residual trigger below.

**D3 — One resolved cycle per run, via the existing `fc` probe for both
memberships.** (Resolves the Goal's former "per-requirement divergent cycles"
over-reach *and* round-2's "unbuilt complete-ensemble probe" finding.) The
adapter continues to resolve exactly one `effective_cycle_time` per
`fetch_forecasts` call (`recap_gateway.py:724`) and thread it through the whole
`by_hru` loop. **Cycle resolution is unchanged from today for both
memberships**: `_resolve_effective_cycle` (`recap_gateway.py:673`) keeps using
the `fc`-only `resolve_latest_cycle` probe (`recap_gateway.py:349` — its own
docstring notes "No Gateway health/latest-cycle endpoint exists", and there is no
completeness/manifest primitive on `RecapClientLike`, `recap_gateway.py:221`).

  **Why no separate `ENSEMBLE` complete-ensemble probe (round-2 decision):** a
  proactive "newest cycle whose complete 50-member ensemble is published" probe
  has no Gateway primitive today; building it would mean probing up to 50 `pf`
  members per candidate cycle (×`max_cycle_age_hours` walk-back steps) or assuming
  strict ascending `pf` dissemination order — a real, unsized new Gateway-load
  cost, for a scenario with **no live operational ensemble consumer** (see "What
  already exists"). Instead, an `ENSEMBLE` run that lands on a cycle with `fc` but
  an incomplete `pf` set degrades that cycle to runoff-only reactively via D7
  (`RecapDataUnavailableError`) and retries at the next cycle. The forfeited
  automatic walk-back is the round-2 trade-off noted in the Goal; its re-open
  trigger and the sizing the probe would need are recorded there and in
  Non-goals. This keeps the flow's single `nwp_cycle_reference_time` and single
  readback cycle (`run_forecast_cycle.py:1644,1662`) untouched. **Per-model
  divergent cycles within one run remain a Non-goal** (below).

**D4 — Protocol signature change, done deliberately.** (Addresses the
"protocol-wide, not just `fetch_forecasts`" finding.) Add a keyword-only
parameter `required_membership: ForcingMembership = ForcingMembership.ENSEMBLE`
to the `WeatherForecastSource.fetch_forecasts` Protocol
(`src/sapphire_flow/protocols/adapters.py:18`). The default **preserves today's
full 51-member behaviour**, so callers and adapters that ignore it are unchanged
in effect. Then update **every** implementer and fake deliberately, and the
conformance tests. The complete set (verified by
`rg -n "def fetch_forecasts" src tests`; **T1.2 must re-run this sweep and cover
every hit** so no explicit signature is missed):
  - **Production implementers:**
    - `src/sapphire_flow/adapters/recap_gateway.py:704` (honours it — the only
      member-aware adapter),
    - `src/sapphire_flow/adapters/meteoswiss_nwp.py:585` (gridded ICON; accepts +
      ignores — ICON member set is fixed by the grid, not per-request),
    - `src/sapphire_flow/adapters/replay/nwp.py:32` (accepts + ignores).
  - **Shared fake:** `tests/fakes/fake_adapters.py:35` (accepts; may assert it).
  - **In-test local fakes with explicit signatures (must be updated):**
    `tests/unit/flows/test_run_forecast_cycle.py:1605`, `:1707`, `:4717`;
    `tests/unit/flows/test_run_forecast_cycle_disk_guard.py:172`, `:189`.
  - **In-test local fakes already `*args, **kwargs` (absorb the new kwarg, no
    change needed but re-verify):** `test_run_forecast_cycle.py:986`, `:2311`,
    `:3047`.

  The flow passes the D1-aggregated value at the single call site — the
  `adapter.fetch_forecasts(station_configs, cycle_time)` call inside
  `_fetch_nwp_task` (`run_forecast_cycle.py:1542`), reached via
  `_fetch_nwp_task.submit` in Phase A.

**D5 — Three distinct axes; this plan touches only the fetch policy.** (Resolves
Fork 3/4, the "fc=0 vs member_id=1" finding, and round-2's EnsembleMode-wording
minor.) Keep these separate:
  - *`EnsembleMode` (FI model input / fan-out semantics)*: an FI variable's
    `EnsembleMode.SINGLE | ENSEMBLE`
    (`.venv/lib/python3.12/site-packages/forecast_interface/input/variable.py:35`)
    declares whether the **model's future-known input forcing** arrives as one
    trajectory or as member-suffixed columns fanned out per member
    (`services/ensemble_fanout.py`). It is *not* an output-numbering flag —
    correcting the round-1 wording that called it "model output" semantics.
  - *`ForcingMembership` (run-level Gateway fetch policy — new here)*: derived
    from `EnsembleMode` + `future_dynamic_features` across the run (D1); decides
    which Gateway input members are fetched (`NONE` / `fc` / `fc`+`pf`).
  - *Input NWP forcing member identity* (Gateway → weather-forecast store):
    `fc`=member_id 0, `pf`=1..50 (`recap_gateway.py:744,747`). This plan only
    decides **which of these input members are fetched**; it does **not** change
    their IDs (see [[project_recap_ifs_fc_hres_member0]]).
  - *Model output ensemble numbering* (FI adapter → forecast store): an FI
    deterministic model's single output member is numbered **member_id = 1**
    (`src/sapphire_flow/adapters/forecast_interface.py:333`), and ensemble output
    is numbered by the fan-out. **Untouched by this plan.** A control-only *input*
    model can still emit a multi-member *output* ensemble (its own
    perturbation/quantile method); input forcing membership does not constrain
    output membership. So the "ensemble-first / member_id=0" storage concern from
    the stub does not arise from this change.

**D6 — Operational floors are out of scope (output-side, pre-existing).**
(Addresses the min-ensemble-size finding.) The onboarding smoke-test floor
(`src/sapphire_flow/services/model_onboarding.py:717`,
`min_operational_ensemble_size` default 20 at
`src/sapphire_flow/config/deployment.py:119`) and the alerting skip
(`src/sapphire_flow/services/alert_checker.py:184`) both gate on **output**
member/quantile counts, which per D5 are independent of input forcing membership.
This plan changes only input fetch, so those floors are unaffected and unchanged.
**Residual policy question** (for the plan workflow / owner, not this plan): if a
Sandro control-*input* model also produces a single-member *output*, does the
deployment (a) lower the floor, (b) require quantile output, or (c) run it
intentionally non-alerting? Recorded below; not a blocker for the input-fetch fix.

**D7 — Error taxonomy for missing members.** (Resolves the minor.) With D3:
control-only runs never request `pf`, so the missing-`pf` abort disappears
entirely. For ensemble runs, a missing/incomplete ensemble at the newest cycle
must map to `RecapDataUnavailableError` (graceful degrade-to-runoff-only,
`run_forecast_cycle.py:939`), **never** the generic `except Exception` fatal path
(`run_forecast_cycle.py:956`). Acceptance tests assert both mappings (T7.1/T7.3).

**D8 — Control-member forcing must be normalized for FI `SINGLE` consumers
(round-2 blocker).** A `CONTROL_ONLY` fetch stores exactly the `fc` control
record, which carries **`member_id = 0`** (`recap_gateway.py:744`), *not* `None`.
`_pivot_nwp_records` (`src/sapphire_flow/services/operational_inputs.py:170`)
branches on `if members:` — where `members` = the non-`None` member ids
(`operational_inputs.py:179`) — so a lone `member_id=0` record takes the
**ensemble** branch and emits a member-suffixed column (`precipitation_0`), never
the bare `precipitation`. But an FI `SINGLE` model is fed via the direct
`model.predict(...)` path (`run_station_forecast.py:199`), and the FI adapter
resolves each future variable by its **bare** name through `_frame_with_column`
(`src/sapphire_flow/adapters/forecast_interface.py:986`, raising
`ConfigurationError` "missing ForecastInterface future_known input" when absent).
So without normalization a control-only fetch produces forcing that **no FI
`SINGLE` model can consume** — silently defeating the plan's own goal.

  **Contract:** when the fetched/aggregated forcing for a feature contains **only
  the control member** (member id set == `{0}`, i.e. a `CONTROL_ONLY` run with no
  `pf`), `_pivot_nwp_records` must emit the **bare** `feature` column (member
  suffix dropped) so the direct `SINGLE` path resolves it. To keep an
  (edge-case) ensemble consumer working, it MAY *additionally* emit `feature_0`;
  the ensemble fan-out treats bare-only forcing as a single-trajectory no-op
  anyway (`services/ensemble_fanout.py` `_member_index_sets` → "not member_sets"),
  so emitting bare alone is sufficient for the homogeneous control-only run this
  targets. This is the mirror of the existing deterministic (`member_id=None`)
  branch (`operational_inputs.py:196`), extended to recognise the control-only
  member set. `ENSEMBLE` runs (member ids `{0..50}`) are unchanged — suffixed
  columns, fanned out as today.

## Phases & tasks

### Phase 1 — Membership plumbing (types + protocol)
- **T1.1** Add `ForcingMembership { NONE, CONTROL_ONLY, ENSEMBLE }` enum
  (`src/sapphire_flow/types/enums.py`). Docstring: run-level Gateway *fetch
  policy* axis, distinct from `EnsembleMode` (FI model input / fan-out semantics
  — D5). No behaviour yet.
- **T1.2** Add keyword-only `required_membership: ForcingMembership =
  ForcingMembership.ENSEMBLE` to `WeatherForecastSource.fetch_forecasts`
  (`src/sapphire_flow/protocols/adapters.py:18`) and to **every** implementer +
  fake in the D4 list. **Start by re-running `rg -n "def fetch_forecasts" src
  tests`** and update every explicit-signature hit (the in-test fakes at
  `test_run_forecast_cycle.py:1605,1707,4717` and
  `test_run_forecast_cycle_disk_guard.py:172,189` included). Adapters other than
  recap accept-and-ignore. Conformance/fake tests updated to construct the new
  signature. (`NONE` never reaches an adapter — the flow skips the fetch, D1/T3.1
  — so no adapter needs a `NONE` code path.)

### Phase 2 — Adapter honours membership (recap; cycle resolution unchanged)
- **T2.1** Thread `required_membership` into `_resolve_effective_cycle`
  (`recap_gateway.py:673`) **but keep the existing `fc`-only probe
  (`resolve_latest_cycle`, `recap_gateway.py:349`) for both `CONTROL_ONLY` and
  `ENSEMBLE`** — no complete-ensemble probe is built (D3, round-2). The parameter
  is threaded only so a future probe has a hook; behaviourally cycle resolution is
  today's. (If accepted as-is, `_resolve_effective_cycle` may not even need the
  argument — note the option and let the implementer keep the signature minimal.)
- **T2.2** `fetch_forecasts` skips the `pf` loop entirely when
  `required_membership is CONTROL_ONLY` (guard the `range(_PF_MEMBER_MIN, …)`
  loop at `recap_gateway.py:747`); `ENSEMBLE` keeps the full 1×fc+50×pf fetch.
  `fc` is always fetched. A `pf` member found missing mid-fetch maps to
  `RecapDataUnavailableError` (D7), never the generic abort.

### Phase 3 — Downstream normalization (D8, blocker fix)
- **T3.1** Extend `_pivot_nwp_records`
  (`src/sapphire_flow/services/operational_inputs.py:170`) so a control-only
  member set (`{0}`) emits the **bare** `feature` column (not `feature_0`), per
  D8, so FI `SINGLE` models fed via `run_station_forecast.py:199` /
  `forecast_interface.py:986` can resolve their future variables. Mirror the
  existing deterministic (`member_id=None`) branch (`operational_inputs.py:196`).
  `ENSEMBLE` (`{0..50}`) unchanged. If `_broadcast_deterministic_features_to_members`
  (`operational_inputs.py:111`) is in the path, confirm it does not re-suffix the
  control-only case.

### Phase 4 — Flow aggregation + wiring (single call site)
- **T4.1** Compute run-level `ForcingMembership` from all operational
  station→model assignments **and** group→model runs, using D1's three-state
  derivation (`future_dynamic_features` + `ensemble_mode`;
  `run_forecast_cycle.py` Phase A prep; group discovery at line 2020). Map `NONE`
  onto the existing `skip_nwp_fetch` runoff-only path
  (`run_forecast_cycle.py:1538`); otherwise pass the value into
  `_fetch_nwp_task.submit` → `adapter.fetch_forecasts(..., required_membership=…)`
  (call site `run_forecast_cycle.py:1542`). Single resolved cycle preserved (D3);
  no change to `nwp_cycle_reference_time` / readback threading
  (`run_forecast_cycle.py:1644,1662`).

### Phase 5 — Group-path coverage
- **T5.1** Confirm the group path (`run_group_forecast.py:387`, flow group loop
  at `run_forecast_cycle.py:2020`) consumes the same single resolved cycle and
  that a group-model's `ensemble_mode` participates in the T4.1 aggregation. Add
  a group-path acceptance test (mixed station+group run resolves one cycle).

### Phase 6 — Docs
- **T6.1** Update `docs/spec/types-and-protocols.md`: (a) the
  `WeatherForecastSource.fetch_forecasts` Protocol signature block (line ~2590)
  to add `required_membership`; (b) add a `ForcingMembership` entry and clarify —
  next to the `ModelDataRequirements.ensemble_mode` note (line ~1249) — that
  `EnsembleMode` is the model input / fan-out axis while `ForcingMembership` is
  the run-level fetch-policy axis (D5); (c) the `RecapGatewayForecastAdapter`
  section (line ~1713) to describe membership-driven `fc`/`pf` fetch and the D8
  control-member normalization. Check `docs/v0-scope.md` for any "fetch full
  ensemble" wording that the model-driven membership contradicts and correct it.

### Phase 7 — Acceptance tests (red-first)
- **T7.1** Control-only run, `fc` present, `pf` absent at newest cycle → fetch
  succeeds on the newest `fc` cycle, **no** `pf` request, no abort. (Reproduces
  the live HRU-12300 incident; must fail against current `main`.)
- **T7.2** **D8 consumer test:** given control-member (`member_id=0`) NWP records
  and an FI model with a `FutureKnownVariable(ensemble_mode=SINGLE)` future
  variable, the assembled `StationModelInputs` exposes the **bare** `feature`
  column and `model.predict(...)` resolves it without `ConfigurationError`. (Must
  fail against current `main`, where `_pivot_nwp_records` emits only `feature_0`.)
- **T7.3** Ensemble run, newest cycle has `fc` but incomplete `pf` →
  `RecapDataUnavailableError` → runoff-only degrade (assert the
  `run_forecast_cycle.py:939` path, not `:956`). (D7; reactive degrade, no
  walk-back — D3 round-2.)
- **T7.4** `NONE` run (all fallback models, `future_dynamic_features=frozenset()`)
  → no `fetch_forecasts` call, runoff-only path, every station accounted for
  (assert the `skip_nwp_fetch` path at `run_forecast_cycle.py:1538`).
- **T7.5** Mixed run (some control-only, some ensemble stations/groups) →
  run-level membership is `ENSEMBLE`, one shared `fc`-probed cycle; assert the
  noted trade-offs are the observed behaviour.
- **T7.6** Existing `NwpRegression` ensemble locked tests
  (`tests/unit/adapters/test_forecast_interface_ensemble_mode.py`) and the
  member-id output tests
  (`tests/unit/adapters/test_forecast_interface_adapter_outputs.py:172`) still
  pass unchanged (proves D5 — no output-numbering regression).

## Verification (exact gates)

```bash
uv run ruff format --check
uv run ruff check
uv run pyright src/sapphire_flow/adapters/recap_gateway.py \
  src/sapphire_flow/protocols/adapters.py src/sapphire_flow/types/enums.py \
  src/sapphire_flow/services/operational_inputs.py \
  src/sapphire_flow/flows/run_forecast_cycle.py
uv run pytest tests/unit/adapters/test_recap_gateway.py \
  tests/unit/flows/test_run_forecast_cycle.py \
  tests/unit/flows/test_run_forecast_cycle_disk_guard.py \
  tests/unit/services/test_operational_inputs.py \
  tests/unit/adapters/test_forecast_interface_ensemble_mode.py \
  tests/unit/adapters/test_forecast_interface_adapter_outputs.py \
  tests/unit/services/test_gateway_coverage_gate.py -q
# Protocol/conformance + fakes still satisfy WeatherForecastSource:
uv run pytest tests/ -k "conformance or fetch_forecasts or fake_adapter" -q
```

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1.1", "desc": "ForcingMembership enum (NONE/CONTROL_ONLY/ENSEMBLE)", "deps": []},
    {"id": "T1.2", "desc": "protocol + all implementers/fakes signature (rg sweep)", "deps": ["T1.1"]},
    {"id": "T2.1", "desc": "recap thread membership; fc-only probe kept", "deps": ["T1.2"]},
    {"id": "T2.2", "desc": "recap skip pf when control-only; missing pf -> RecapDataUnavailableError", "deps": ["T1.2"]},
    {"id": "T3.1", "desc": "D8 pivot bare-column normalization for control member", "deps": ["T2.2"]},
    {"id": "T4.1", "desc": "flow three-state aggregation + NONE skip + wiring", "deps": ["T1.2"]},
    {"id": "T5.1", "desc": "group-path coverage", "deps": ["T4.1"]},
    {"id": "T6.1", "desc": "docs: types-and-protocols + v0-scope", "deps": ["T1.2", "T2.2", "T3.1"]},
    {"id": "T7.1", "desc": "acceptance: control-only no-pf", "deps": ["T2.1", "T2.2", "T4.1"]},
    {"id": "T7.2", "desc": "acceptance: D8 SINGLE consumer reads bare column", "deps": ["T3.1"]},
    {"id": "T7.3", "desc": "acceptance: ensemble incomplete-pf reactive degrade", "deps": ["T2.1", "T2.2", "T4.1"]},
    {"id": "T7.4", "desc": "acceptance: NONE run skips fetch", "deps": ["T4.1"]},
    {"id": "T7.5", "desc": "acceptance: mixed run strictest-wins", "deps": ["T4.1"]},
    {"id": "T7.6", "desc": "regression: FI ensemble + member-id unchanged", "deps": ["T2.2", "T3.1"]}
  ]
}
```

## Non-goals

- **Per-model divergent cycles within one run.** Two assigned models landing on
  *different* resolved cycles in the same run is explicitly out of scope: it would
  force per-station/per-group readback + provenance instead of the single
  `nwp_cycle_reference_time` (`run_forecast_cycle.py:1644,1662`) and multiply
  Gateway calls for mixed-membership HRUs (`recap_gateway.py:730`), for a scenario
  with **no current operational consumer**. **Re-open trigger:** when a genuinely
  ensemble operational model is onboarded *alongside* a control-only one **and**
  the one-cycle-older freshness of the control-only stations is shown to matter.
- **Proactive complete-ensemble cycle probe / walk-back (round-2).** No
  "newest cycle whose full 50-member `pf` set is published" probe is built; both
  memberships use the existing `fc`-only probe and an `ENSEMBLE` run reactively
  degrades an incomplete cycle (D3/D7). **Re-open trigger + sizing** are in the
  Goal's trade-off #2 (build the probe when a live ensemble consumer exists,
  sizing its per-walk-back-step Gateway cost — there is no completeness/manifest
  primitive on `RecapClientLike`, `recap_gateway.py:221`).
- **Exact fc/pf member subsetting** beyond SINGLE/ENSEMBLE. If a future model
  needs it, that is a real FI gap → file an FI-repo issue and co-design (D2); do
  not work around it SAP3-side.
- **Output-side operational floors / member numbering.** Onboarding floor,
  alerting skip, and FI output member_id are output-axis, pre-existing, and
  untouched (D5/D6).
- Does not change the `fc`=member_id 0 / `pf`=1..50 input identity
  ([[project_recap_ifs_fc_hres_member0]]) or remove full-ensemble support.
- Does not re-open anything else 082 shipped beyond the forecast-membership +
  cycle-resolution path.

## Residual questions for the plan workflow / owner

1. **D6 output-floor policy** for a control-*input* model that also produces a
   single *output* trajectory: lower the floor, require quantiles, or run
   intentionally non-alerting? (Only relevant if Sandro's models are single-output
   as well as single-input; confirm with Sandro.)
2. **D4 default direction:** confirm `required_membership` should default to
   `ENSEMBLE` (backward-compatible, safest for non-recap adapters) rather than
   `CONTROL_ONLY`.

## References

All in-repo `file:line` citations live inline at their point of use (Problem,
"What already exists", D1–D8, Phases) — they are not restated here, to avoid a
second copy that would rot independently as the code shifts. The only external
evidence, kept here because it is cited nowhere else:

- ECMWF timing (control disseminated ahead of perturbed members; Cycle 50r1
  renames HRES → "ENS control" but preserves the earlier control dissemination):
  - https://www.ecmwf.int/en/about/media-centre/focus/2024/plans-high-resolution-forecast-hres-and-ensemble-forecast-ens
  - https://www.ecmwf.int/en/forecasts/datasets/set-i
  - https://confluence.ecmwf.int/display/DAC/Dissemination+schedule

## Open blockers — `plan` workflow escalation (2026-07-17)

The `plan` workflow ran 3 rounds (Codex + design lenses) and **ESCALATED (stalled)**:
R1 = 2 blocker/9 major → R2 = 1 blocker/4 major → R3 = 2 blocker/4 major (re-introduced),
stopped to avoid thrash. **Not READY.** A human must resolve these before implementation.
Good news the review confirmed: the SINGLE-vs-ENSEMBLE signal already exists end-to-end
(`EnsembleMode` → `ModelDataRequirements.ensemble_mode` → coverage/fan-out), so **no FI gap /
no FI-repo issue** — see "What already exists". Residual:

- **BLOCKER — mixed-run column shape.** `_pivot_nwp_records` emits only member-suffixed columns
  when any member is present (`operational_inputs.py:181`), but FI `SINGLE` prediction needs the
  **bare** feature name (`forecast_interface.py:986`). A run mixing an ENSEMBLE model and a
  SINGLE model breaks the SINGLE consumer. Fix: project a bare control column whenever
  `member_id=0` is present (even in full ensemble runs); acceptance must assert both a SINGLE and
  an ENSEMBLE model succeed in one mixed run. (This is a **pre-existing** latent gap 123 surfaced.)
- **BLOCKER — missing-`pf` classifier not actually fixed.** The Goal promises "missing pf →
  `RecapDataUnavailableError` → graceful degrade," but `_map_recap_error` only maps the structured
  `source_data_missing` code (`recap_gateway.py:306`) and no task fixes/verifies the classifier
  against the REAL live "No IFS dataset found" response shape. Needs a task that pins the real
  response shape (red test from the actual body/message/params) and maps exactly that case —
  without broadening config/auth errors. (NB: my live probe showed the *no-data* case DID surface
  as `RecapDataUnavailableError`, while the *not-subscribed* case was generic `AdapterError` — so
  the two cases must be disambiguated against real payloads, not assumed.)
- **MAJOR — `NONE` runs still run NWP-staleness health** (`run_forecast_cycle.py:1628,691,2267`);
  gate staleness on "NWP required," not "adapter enabled."
- **MAJOR — active-assignment semantics undefined**: station path uses all-status assignments,
  group path filters active (`run_forecast_cycle.py:1440,2034`); membership aggregation must use
  one shared active set (pre-existing inconsistency 123 surfaced).
- **MAJOR — group-model membership discovered too late**: `discover_group_runs` runs in Phase B2
  (`:2020`), after Phase A already submits the NWP fetch (`:1552`); must query group runs early
  (mirroring `_check_fallback_priority_drift`'s existing early group query) so group-only ENSEMBLE
  models aren't silently excluded from strictest-wins aggregation.
- **MAJOR — D7 doesn't pin the live generic missing-`pf` error shape** (same root as blocker 2).

**Human decisions needed (grill-me):** (1) scope — fix `CONTROL_ONLY`+`NONE` now (the live
deployment's need) and carve the ENSEMBLE mixed-run column normalization to its own follow-up, vs
one plan covering all three memberships? (2) the mixed-run bare-vs-suffixed column contract (D8);
(3) whether the active-assignment + staleness-health fixes belong in 123 or a separate
forecast-flow-consistency plan (both are pre-existing).
