---
id: 123
title: Model-driven forcing membership — CONTROL_ONLY + NONE (fc-first)
status: DRAFT
depends_on: [082]
owner: unassigned
created: 2026-07-17
---

# Plan 123 — Model-driven forcing membership (CONTROL_ONLY + NONE)

> **Re-scoped 2026-07-18 (owner) to the `fc`-first deployment-critical slice.** The earlier
> full 3-membership plan escalated (2 blockers + 4 majors) because it tangled with the
> ENSEMBLE / mixed-run path — which is **deferred and non-critical** (no ensemble models in the
> live Nepal deployment; Sandro's models are control-only). This plan does **only** `NONE` +
> `CONTROL_ONLY`, and leaves the existing ENSEMBLE fetch (1 `fc` + 50 `pf`) **unchanged**. The
> ENSEMBLE-specific improvements are deferred to [[Plan 126]]. Prior full-scope history is in git.

## Problem (fc-first)

`RecapGatewayForecastAdapter.fetch_forecasts` (`src/sapphire_flow/adapters/recap_gateway.py:704`)
**hardcodes a 51-member fetch** — `fc` (member 0, `:744`) + `for member in range(1, 51)` over all
50 `pf` (`:747`) — regardless of what the assigned models need. Consequences for the live Nepal
`fc`-only deployment (confirmed live, HRU `12300`, 2026-07-17):

1. **Hard abort on missing `pf`.** ECMWF disseminates the control (`fc`/HRES) **before** the
   perturbed members (`pf`) — a normal per-cycle window, preserved by IFS Cycle 50r1. Cycle
   resolution locks onto the newest `fc` cycle, then the unconditional `pf` loop demands 50
   members that aren't published yet → the whole NWP fetch aborts. A control-only model needs
   **none** of those `pf` members.
2. **Control-only forcing is unreadable by FI `SINGLE` models.** `_pivot_nwp_records`
   (`src/sapphire_flow/services/operational_inputs.py:181`) emits member-**suffixed** columns
   whenever any record has a `member_id`; since `fc`=`member_id=0`, a control-only fetch yields
   `precipitation_0`, but FI `SINGLE`'s `_frame_with_column`
   (`src/sapphire_flow/adapters/forecast_interface.py:987`) requires the **bare** `precipitation`
   → `ConfigurationError`. So even if the fetch succeeded, the model couldn't consume it.

## What already exists (survey — do not re-derive; no FI gap)

The single-vs-ensemble distinction is **already modelled and wired**; reuse it (no FI-repo issue):

- FI expresses it: `FutureKnownVariable.ensemble_mode: EnsembleMode` (`SINGLE`/`ENSEMBLE`)
  (`forecast_interface/input/variable.py:40`).
- FI adapter projects it: `_project_requirements` sets `any_ensemble_future`
  (`adapters/forecast_interface.py:474`) and stamps `ModelDataRequirements.ensemble_mode`
  (`:525`). Domain type carries it, default `SINGLE` (`types/model.py:271`).
- Fallback models declare `future_dynamic_features=frozenset()` (need no NWP): e.g.
  `models/linear_regression_daily.py`, `climatology_fallback.py`, `persistence_fallback.py`.
- Assignment status is now **active-filtered** for operational consumers — the active-only
  view `active_model_assignments` is already built in the flow
  (`run_forecast_cycle.py:1468`, `_active_only(...)`) and consumed by forecasting, input
  assembly, and alert priority (Plan 124; its plan doc is still `status: DRAFT`, but the
  filter is in the code). Membership aggregation **must use that same ACTIVE set**.
- **Storage has no run-level membership boundary.** `weather_forecasts` is keyed by
  `(station_id, nwp_source, cycle_time, valid_time, parameter, spatial_type, band_id, member_id)`
  (`db/metadata.py:400-410`, `member_id` in the unique key), and inserts are
  `on_conflict_do_nothing` (`store/weather_forecast_store.py:46`). Read-back reads **all**
  rows for `(station, source, cycle)` (`weather_forecast_store.py:56-62`,
  `operational_inputs.py:389`). So prior `pf` rows from an earlier ENSEMBLE fetch of the
  **same** cycle can coexist with a later control-only `fc` fetch — you **cannot** infer
  "this was a control-only run" from the rows that come back. This drives D8 below.

## Goal (this plan)

Decide a **run-level forcing membership** from the ACTIVE assigned models' FI signals, and act on
two of the three states; leave ENSEMBLE as today:

- **`NONE`** — no ACTIVE assigned model declares any `future_dynamic_features` → **skip the NWP
  fetch** (reuse the flow's existing `skip_nwp_fetch` runoff-only path; `skip_nwp_fetch` is
  defined at `run_forecast_cycle.py:1568` — `runoff_only_mode or not flat_weather_configs`).
  **The staleness-health gate must also change.** The NWP-staleness computation
  (`_check_nwp_grid_staleness`) is at `run_forecast_cycle.py:1657-1667` and is gated on
  `if nwp_enabled:` (`:1658`) — **not** on `skip_nwp_fetch`. `nwp_enabled` is a static
  deployment flag (`:1299`/`:1312`: `adapter is not None` / `weather_forecast_config.enabled`);
  `skip_nwp_fetch` is a per-cycle flag. Today they only coincide because the *sole* existing
  reason for `skip_nwp_fetch=True` is `runoff_only_mode = not nwp_enabled` (`:1384`), so
  "NWP disabled" makes both true together. **Model-driven `NONE` breaks that coincidence**: a
  deployment can have `nwp_enabled=True` (adapter configured — e.g. the live Recap deployment)
  while, on a given cycle, no ACTIVE assigned model needs NWP. Merely setting
  `skip_nwp_fetch=True` does **nothing** to `nwp_grid_stale`, which is still computed because
  `nwp_enabled` is still True; as no-NWP cycles accumulate, `fetch_latest_cycle_time` stops
  advancing, `age_hours` grows past `max_age_hours`, `_check_nwp_grid_staleness` fires a
  CRITICAL `nwp_grid` health record and flips the cycle to DEGRADED — **exactly the false-degrade
  this plan exists to prevent**. **Required code change:** thread the model-driven membership
  decision into the staleness gate itself, e.g. `if nwp_enabled and forcing_membership is not
  NONE:` (equivalently `and not model_driven_none`), so the staleness check is suppressed on a
  legitimately-no-NWP cycle — not just the fetch. **Preserve** the `nwp_unavailable_runtime`
  staleness signal for the *other* skip case: when `skip_nwp_fetch` is True only because
  `not flat_weather_configs` (a real binding failure, NOT a `NONE` decision), NWP is still
  needed, so staleness must still fire. In other words, gate on the `NONE` *decision*, **not**
  on `skip_nwp_fetch` and **not** on `not effective_runoff_only` — the `NoCycleAvailableError`
  branch writes no health record, so the staleness check is the only detector of a real
  "NWP needed but unavailable" failure. *(Folded in from ex-124 #1.)*
- **`CONTROL_ONLY`** — ACTIVE models need NWP but **none is `ENSEMBLE`** → fetch **`fc` only**
  (member 0), **skip the `pf` loop entirely**, never abort on missing `pf`. Normalize the fetched
  control forcing to **bare** columns (D8, below) so FI `SINGLE` models consume it.
  **Scope limit — IFS precip/temp only.** "Fetch `fc` only" targets `fetch_forecasts`'
  IFS path, which loops `_ifs_variables()` and dispatches `ecmwf.ifs_forecast`
  (`recap_gateway.py:731-759`). Recap **snow** future-forcing is a *separate* variable class
  (`snow_name` set, `ifs_name` is `None`, so it is skipped by the `_ifs_variables()` loop's
  `if ifs_name is None: continue` at `:733`) delivered by a *different* method,
  `fetch_snow_forecast` (`recap_gateway.py:813`), which Phase A **does not call**
  (`run_forecast_cycle.py:1582` submits only `_fetch_nwp_task` → `fetch_forecasts`). Plan 123 is
  therefore **explicitly restricted to IFS precipitation/temperature** future-dynamic
  requirements. A control-only model that declares a snow future-dynamic feature is **out of
  scope**: this slice does not aggregate snow requirements or dispatch the snow endpoint from the
  membership decision. Snow-forecast integration into model-driven membership is a **documented
  known gap deferred to [[Plan 126]]**. *(Sandro's live control-only models consume IFS
  precip/temp only — see [[project_nwp_v0_variable_allowlist]] `tp` + `t_2m`.)*
- **`ENSEMBLE`** — any ACTIVE model is `ENSEMBLE` → **unchanged**: the current 1×`fc` + 50×`pf`
  fetch and member-suffixed columns. No new behavior here (see Non-goals / Plan 126).

### Station scoping — membership also decides *which* stations enter Phase A

Membership is not only "which members to fetch"; it also scopes **which stations** the fetch
runs for. Today the flow resolves a FORECAST binding for **every** operational station
(`run_forecast_cycle.py:1512-1525`) and passes **all** valid bindings (`flat_weather_configs`)
to the shared NWP prefetch (`:1582`). Two consequences this plan must address:

- A station with **no** ACTIVE NWP-needing model (a `NONE` station) can still enter binding
  resolution and, on a bad/missing FORECAST binding, raise `ConfigurationError` — poisoning the
  shared control-only fetch's accounting even though that station never needed NWP.
- The run-level `NONE` decision cannot rely on the existing `not flat_weather_configs` skip path
  alone (that fires only when **every** station fails binding resolution).

**Decision for this slice:** derive `nwp_required_station_ids` from the **ACTIVE** station
assignments (`active_model_assignments`, already built at `:1468`) whose
`ModelDataRequirements.future_dynamic_features` is non-empty, and resolve/pass FORECAST bindings
**only** for those stations into `flat_weather_configs`. Stations that need no NWP are simply not
in the Phase A input, so their binding config cannot affect the fetch. This **narrows** the set
fed to the adapter; it does **not** change the existing per-station binding-validation contract
(`ConfigurationError` on 0 or ≥2 FORECAST bindings) for stations that *do* need NWP — those are
still resolved and still recorded-once-failed exactly as at `:1512-1523`. **Note (trade-off):**
this changes the population that gets a binding-config error logged per cycle (a `NONE` station
with a broken FORECAST binding no longer surfaces here); that is intended — a station that needs
no forecast forcing should not be failed for a forecast-binding problem. A regression must lock
the choice: a `NONE` station with a deliberately-broken FORECAST binding must **not** fail the
control-only fetch for the other stations.

### D8 — control-only forcing-column normalization

When the run is **control-only**, `_pivot_nwp_records` (`operational_inputs.py:170`) must emit
**bare** feature columns (`precipitation`, not `precipitation_0`) so FI `SINGLE` models can
consume them.

**Do NOT infer control-only from the read-back rows.** The obvious-looking test
("record `member_id`s ⊆ `{None, 0}`") is **unsound** given the storage model surveyed above:
`_pivot_nwp_records` currently branches on `members = {r.member_id for r in records if
member_id is not None}` (`:179-181`) and read-back returns *every* row for
`(station, source, cycle)` (`weather_forecast_store.py:56-62`). Because `member_id` is in the
unique key and inserts are `on_conflict_do_nothing` (`metadata.py:409`,
`weather_forecast_store.py:46`), stale `pf` rows from an **earlier** ENSEMBLE fetch of the same
cycle survive; a later control-only `fc` fetch then reads `{0, 1, …, 50}` and the pivot mis-classifies
the run as ENSEMBLE — emitting `precipitation_0` and re-triggering the exact `ConfigurationError`
this plan removes. Membership is a **run-level** property; the rows cannot carry it.

**Required mechanism — thread an explicit column mode from the flow.** The run-level membership
decision (computed once, per §Design forks 1) must be plumbed into input assembly and down into
`_pivot_nwp_records` as an explicit argument (a `ForcingMembership` / column-mode enum), rather
than sniffed from data:

- **`CONTROL_ONLY`**: read-back **filters to `member_id ∈ {None, 0}`** (so stale `pf` rows are
  excluded) and `_pivot_nwp_records` emits **bare** columns. Where the filter lives — narrow the
  `fetch_weather_forecasts` query, or filter in the pivot — is Design fork 3.
- **`ENSEMBLE`**: unchanged — suffixed columns, all members, exactly as today.

Rationale for not aliasing "member 0 present" → bare: in a full ENSEMBLE fetch, member 0 is a
normal ensemble member with no control status (see [[project_recap_ifs_fc_hres_member0]]), so
aliasing it would silently change `SINGLE`-model input semantics on a mixed cycle.

**Regression (mandatory):** with **stale `pf` rows already present** for the same
`(station, source, cycle)`, a `CONTROL_ONLY` run must still yield **bare** columns and a
consumable `SINGLE` frame — proving the classification comes from the threaded mode, not the rows.

## Non-goals (deferred to [[Plan 126]])

- **ENSEMBLE membership improvements:** requirement-aware complete-ensemble cycle resolution (the
  `fc`-before-`pf` lag as it affects ENSEMBLE runs), and any change to the ENSEMBLE fetch/columns.
  ENSEMBLE stays exactly as it is today.
- **Mixed runs** (an ENSEMBLE model and a `SINGLE`-with-NWP model on the same station/run): the
  bare-and-suffixed column coexistence. Not needed for the control-only Nepal deployment.
- **Group-model NWP needs driving fetch-time membership.** The membership decision must happen
  **before** Phase A submission (`run_forecast_cycle.py:1582`). Station-level ACTIVE assignments
  are already resolved early (`active_model_assignments` at `:1468`), so station aggregation is a
  pure wiring choice. **Group** assignments, however, are only discovered deep in Phase B2 —
  `discover_group_runs(models, group_store)` + `group_store.fetch_group_model_assignments(...)`
  at `:2054-2063` — which runs **after** Phase A has completed and forecasts have started. Making
  group needs reach the fetch-time decision would require **hoisting** group-run/assignment
  discovery ahead of Phase A (a real phase-ordering change: either a second, earlier
  `discover_group_runs` call — extra DB round-trips and a drift risk between the early and the
  Phase-B2 lookups — or refactoring Phase B2 to consume a pre-computed result). **Decision:** for
  this `fc`-first slice, **only station-level ACTIVE assignments drive the membership decision.**
  A group model that needs NWP is a **documented known gap deferred to [[Plan 126]]** (which also
  owns the ENSEMBLE/mixed path where group aggregation actually matters). This is a genuine
  deferral, not an unresolved fork: see Design fork 1 for the explicit mechanism.

## Design forks for the `plan` workflow (grill-me)

1. **Where the membership decision lives** and how it is plumbed into `fetch_forecasts` (which
   currently takes only `(station_configs, cycle_time)`): a `ForcingMembership` arg, a per-source
   field, or a flow-level pre-fetch computation. **Aggregation source (resolved, not open):** the
   decision aggregates over **station-level ACTIVE assignments only** (`active_model_assignments`,
   `run_forecast_cycle.py:1468`), computed before Phase A submission (`:1582`). **Group** ACTIVE
   assignments are *not* available at fetch time (they are discovered only in Phase B2 at
   `:2054-2063`) and are **out of scope for this slice** — deferred to [[Plan 126]] per Non-goals.
   The open fork is purely *representation* (arg vs field vs pre-fetch computation) and the same
   mode value must also feed the staleness gate (`:1658`, per the `NONE` bullet) and
   `_pivot_nwp_records` (per D8) — i.e. it is threaded, not recomputed independently in three
   places.
2. **`CONTROL_ONLY` fetch shape** in the adapter: parameterize the member set (skip the `pf` loop)
   without disturbing the ENSEMBLE path.
3. **D8 plumbing** — *not* keyed on read-back rows (that test is unsound; see D8). The open
   choice is **where the CONTROL_ONLY `{None,0}` filter lives**: narrow the
   `fetch_weather_forecasts` query (`weather_forecast_store.py:56-64`, add a member filter) vs.
   filter inside `_pivot_nwp_records` — and how the threaded `ForcingMembership` mode reaches the
   pivot (input-assembly signature change).
4. **Reconcile with "ensemble-first"** (`docs/architecture-context.md`). Be precise about which
   `member_id` this plan touches: **control-only *forcing* rows use `weather_forecasts.member_id=0`**
   (the `fc`/HRES member — `recap_gateway.py:744`, `_FC_MEMBER_ID`). This plan does **not**
   prescribe any *model-output* member id. Do **not** describe a control-only forecast as
   "a 1-member forecast at `member_id=0`": FI deterministic model output is converted to
   `member_id=1`, not 0 (`_members_from_deterministic`, `forecast_interface.py:323`), and a
   1-member MEMBERS output would sit **below** the default operational floor
   (`min_operational_ensemble_size = 20`, `config/deployment.py:123`). Those output-side
   implications (ensemble-size floor, alert eligibility — `services/alert_checker.py`,
   `services/model_onboarding.py`) are a **separate** concern from control-only *forcing* and are
   **out of scope** here — control-only forcing feeds `SINGLE` FI models whose output shape is
   unchanged by this plan. Confirm only that the store / `NwpCycleSource` / skill paths handle
   `member_id=0` *forcing* rows without special-casing.

## References

- ECMWF timing (control disseminated ahead of perturbed members; Cycle 50r1 preserves it):
  https://www.ecmwf.int/en/about/media-centre/focus/2024/plans-high-resolution-forecast-hres-and-ensemble-forecast-ens
- Merged adapter: `src/sapphire_flow/adapters/recap_gateway.py`; FI contract:
  `interface/protocol.py`, `input/requirement.py`.

## Escalation — `plan` workflow (2026-07-18), and a proposed simpler path

Even the `fc`-first slice ESCALATED (R1 2 blocker/4 major → R2 3 blocker/4 major, stalled). Two
strands: (i) the planner re-introduced an over-scoped "station-scoping" mechanism (narrowing
FORECAST-binding resolution) that broke Phase B + group forecasts — **drop it**; the reviewer's
simpler approach is "keep resolving bindings for all stations, only filter the Phase-A fetch input
set." (ii) GENUINE design forks in the full model-driven approach:
- **NONE reuses runoff-only → forecasts marked DEGRADED.** `skip_nwp_fetch` →
  `effective_runoff_only` → forecasts stamped `RUNOFF_ONLY`, and `assess_input_quality`
  (`input_quality.py:70`) always flags `RUNOFF_ONLY` NWP as degraded. Distinguishing "NWP not
  required" from "NWP unavailable" is real new provenance/quality work.
- **Protocol blast radius.** Threading a `ForcingMembership` arg into `fetch_forecasts` touches the
  shared `WeatherForecastSource` protocol (`protocols/adapters.py:18`) + MeteoSwiss + replay +
  fakes + fixture recording.
- **D8 layering.** The control-only `{None,0}` filter must run at readback/store-query, before
  aggregation/broadcast/capping — not inside `_pivot_nwp_records`.

**Proposed reframe (decide before proceeding — the grill-me):** the model-driven-membership
machinery (flow aggregates membership → adapter fetches `fc`-only → NONE skip) is the *efficient*
long-term design, but it is genuinely multi-part. A **minimal unblock** likely makes Sandro's
control-only models forecast NOW with far less risk, adapter-local:
1. Make the `pf` fetch **tolerant** — a missing `pf` member does not abort the fetch; keep the `fc`
   (+ any members that returned).
2. **D8** — emit BARE forcing columns when the returned records are control-only (member ids ⊆
   `{None,0}`), at readback.
No membership decision, no protocol change, no NONE provenance work. Downside: still *attempts* the
50 `pf` calls per cycle (wasteful at scale) — the efficiency (skip `pf` for control-only) + `NONE`
skip + the ENSEMBLE work then become follow-ups. **This plan (full membership) becomes that
follow-up if the minimal unblock is chosen.** Owner to choose minimal-unblock vs full-membership.
