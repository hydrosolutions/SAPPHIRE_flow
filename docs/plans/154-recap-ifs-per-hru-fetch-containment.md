---
status: DRAFT
created: 2026-08-11
plan: 154
title: Recap IFS fetch containment — one station's missing data must not degrade the whole cycle
scope: Contain RecapDataUnavailableError inside RecapGatewayForecastAdapter.fetch_forecasts so a data gap affecting one HRU no longer discards every other station's already-accumulated rows and no longer escalates a station-scoped gap into the flow's cycle-wide runoff-only degradation. Containment and commit are both per HRU (the Gateway call unit): an HRU is committed only with its complete variable set, and station-level partiality within an HRU is unrepresentable at this boundary (one call per HRU/variable; every polygon column required). Partial loss is surfaced as a queryable pipeline_health record and a DEGRADED cycle health, per the logging standard. Preserves today's behaviour when no HRU commits. No adapter return-type change, no Protocol change. The single-HRU cycle PROBE is explicitly out of scope.
depends_on: []
blocks: []
supersedes: []
---

# Plan 154 — Recap IFS fetch containment

## Status
**DRAFT.** A standalone operational-reliability fix (category **A**), independent of the forecast-cycle redesign.
Numbered 154 because 152 is taken (aquacast) and 153 is informally reserved for multi-resolution domain-type support.

**Provenance.** Two manual Codex passes plus a `/plan` review shaped this document; the `/plan` run escalated by
over-expansion (179 → 418 lines) rather than converging, so this is a **reconstruction** carrying only the
independently re-verified findings. The expanded version is preserved outside the repo for audit. The reviewers
**disagreed with each other** on commit granularity (HRU vs station); that was settled by reading the adapter boundary
directly rather than by deferring to either — see D2, which records the reasoning and the condition under which the
answer flips.

## Problem
`RecapGatewayForecastAdapter.fetch_forecasts` loops over HRUs and, for each variable, fetches the deterministic
control member **outside any containment block**:

- The HRU loop is `for hru_name, refs_by_polygon in by_hru.items()`
  (`src/sapphire_flow/adapters/recap_gateway.py:825`).
- The **control (`fc`)** call — `_accumulate_member(..., ifs_type="fc", ...)` — sits directly in that loop body with
  **no `try`** (`:830-841`).
- Only the **perturbed (`pf`)** loop is contained: `except RecapDataUnavailableError:` → log
  `recap.pf_unavailable_control_only` → `break` (`:842-871`). Added deliberately (Plan 127 Fix 1) because ECMWF
  disseminates `fc` before `pf`, so during that window every `pf` member is absent.
- `_accumulate_member` reaches the Gateway through `_guarded_fetch(self._client.ecmwf.ifs_forecast, ...)`
  (`:908-911`), which raises `RecapDataUnavailableError` when the Gateway reports `source_data_missing`.

**Consequence.** A control-fetch gap for one HRU propagates out of the *entire* `for hru_name` loop. Rows already
accumulated for HRUs processed earlier are discarded with the stack frame, and later HRUs are never attempted.

**Blast radius is the whole cycle.** At flow level `_fetch_nwp_task` catches `RecapDataUnavailableError`
(`src/sapphire_flow/flows/run_forecast_cycle.py:1101-1116`) and returns
`_NwpFetchOutcome(..., nwp_unavailable=True)` with a WARNING pipeline-health record naming `source_data_missing`. That
handler is **correct** for a genuinely cycle-wide gap — it degrades to runoff-only rather than aborting — but here it
fires on a **station-scoped** cause, so **every station in the deployment loses NWP forcing for that cycle**,
including stations whose own data was complete.

**Root cause:** the adapter **loses the scope of the failure**, raising the same exception for "this HRU's data is
missing" and "this cycle is not published at all". The flow can only see the exception type, so it correctly applies
the cycle-wide remedy to both.

**Reachability.** Latent on the current single-HRU deployment; live for multi-HRU Nepal.

## Design

- **D1 — Contain the exception per HRU (the fetch unit).** Wrap each HRU's fetch in
  `except RecapDataUnavailableError`, record that HRU as **discarded**, and continue to the next HRU. A discarded HRU
  contributes no rows for any of its stations — the Gateway call is per `(HRU, variable, member)`, so a raised call
  yields nothing for that HRU regardless of which station needed it. Only `RecapDataUnavailableError` is contained.
- **D2 — Stage per HRU and commit all-or-nothing per HRU — and this IS the correct granularity.**
  *(Two reviewers disagreed here; resolved by reading the boundary directly.)* One reviewer argued commit must be
  per **station**, because an HRU groups several stations (`_group_by_hru` →
  `dict[GatewayHruName, dict[GatewayPolygonName, GatewayPolygonRef]]`, `:651-653`) and discarding the HRU would drop a
  healthy sibling. That is true of HRU *grouping* but **not producible at this boundary**:
  - A control fetch is issued **once per `(HRU, variable, member)`** (`:826-841`), so a raised call yields rows for
    **no** station in that HRU — never a subset.
  - `_iter_long_rows` receives the HRU's **entire** `refs_by_polygon` and raises `AdapterError` if **any** resolved
    polygon lacks a column (`:511-525`), so a successful call yields rows for **every** station in the HRU.
  - Therefore "station A complete, station B incomplete, same HRU, same variable" is **unrepresentable**. Per-station
    coverage tracking would be additional state guarding an unreachable case, and HRU-local staging discards a failed
    call unit without needing any station→HRU reverse lookup.
  - **Invariant (state it in the code):** *within one HRU, all stations share the same fetch outcome per variable.*
  - **Forward constraint — this changes under Plan 151.** Plan 151 D7 deliberately reclassifies a missing polygon
    column from a batch-wide `AdapterError` into **per-station** unavailability on its new per-track path. Once that
    lands, station-level partiality **becomes** representable and commit granularity must move to per-station **there**.
    This plan's HRU granularity is correct for the legacy path it fixes, and must not be copied into the per-track
    path unexamined.
  - **Invariant: a committed HRU has its complete variable set.** IFS fetches **two** variables per HRU
    (`RECAP_VARIABLES`, `:75-90`) and `_accumulate_member` writes rows straight into the shared accumulator as each
    call succeeds (`:912-921`), so precipitation-succeeds-then-temperature-raises must discard the HRU's staged
    precipitation rows rather than ship **incomplete forcing** — strictly worse than today's cycle-wide degradation.
  - **Provenance commits on the same boundary.** `cycle_source_run` is shared mutable state threaded through
    `_accumulate_member`'s `prior` argument (`:823`) and set by the first successful call, so a discarded HRU must not
    determine the returned results' `cycle_time`.
- **D3 — Re-raise only on TOTAL loss; distinguish DISCARDED from legitimately EMPTY.** *(Reviewer blocker-fix.)*
  Three outcomes, not two:
  - **At least one HRU committed → return its stations.** Stations absent from the mapping are already how this
    return shape says "no data for this station" (`:874-878`); the downstream per-station path degrades them
    individually.
  - **No HRU committed AND at least one HRU discarded → re-raise `RecapDataUnavailableError`,** so the flow's
    existing cycle-wide handler fires unchanged. This preserves today's behaviour for the total-failure case.
  - **No HRU committed and NO HRU discarded** (every response well-formed but empty) → return `{}`, exactly as
    today. Without this split, an empty-but-successful HRU could mask a real total failure: the flow would treat `{}`
    as success and never set `nwp_unavailable`.
- **D4 — Partial loss must be operator-visible, not log-only.** *(Reviewer blocker-fix; this overrides the draft's
  "adapter logs only" recommendation.)* `docs/standards/logging.md:428` requires that forecast-feed resilience events
  affecting operator trust — explicitly including **dark station forecasts** — carry a queryable `pipeline_health`
  record in addition to any structlog event. A partial fetch darkens specific stations, so log-only would violate that
  standard, and the cycle could report `HEALTHY` while an NWP-fed model was silently suppressed.
  - **No adapter contract change is needed.** The flow already knows which stations it requested, so `_fetch_nwp_task`
    reconciles requested station ids against the returned mapping's keys, appends an `NWP_DELIVERY` / WARNING
    `pipeline_health` record naming the missing stations, and threads an internal partial flag through
    `_NwpFetchOutcome` into `_forecast_cycle_health` so the cycle reports **DEGRADED**.
  - **Reconciliation fires ONLY on a non-empty PROPER SUBSET** (reviewer blocker-fix). An empty mapping is *not*
    partial loss: `{}` is today's legitimate no-op-NWP success (D3), and reconciling it would mark every station
    missing and degrade a cycle that behaves exactly as it does on `main`. Partial-loss handling requires
    `0 < len(returned) < len(requested)`; `len(returned) == 0` keeps its existing semantics untouched.
  - The adapter additionally logs a WARNING per contained HRU naming the HRU, variable and `ifs_type`, mirroring the
    existing `recap.pf_unavailable_control_only` precedent (`:862-868`). Event: `recap.hru_unavailable_contained`.
- **D5 — Auth and configuration errors stay fatal and uncontained.** `RecapAuthError` and `RecapConfigurationError`
  keep propagating; the flow hard-aborts on auth (`run_forecast_cycle.py:1086-1100`) because an expired or
  misconfigured key needs operator action, not a per-station skip. Containment applies **only** to the temporal
  `source_data_missing` signal.
- **D6 — The missing-polygon-column raise is NOT touched.** `_iter_long_rows` raises a batch-wide `AdapterError` when a
  resolved polygon lacks a response column (`:511-525`), deliberately, so a corrupt response cannot **silently** drop a
  station. Different cause (malformed response, not absent data). Re-classifying it belongs to Plan 151, and only on
  its new per-track path.
- **D7 — Relationship to the snow channel: analogous, deliberately stricter.** *(Reviewer minor-fix — the draft
  overstated this as "making IFS consistent with snow".)* `fetch_snow_forecast` also contains per `(HRU, variable)`
  and records gaps per HRU (`:953`, `:972-1009`), but it **ships whatever rows accumulated** and reports the gaps in a
  returned `unavailable` map. IFS deliberately does **not** ship partial-variable forcing (D2), because snow is an
  optional enrichment channel whereas precipitation and temperature are the model's required forcing. Snow is a
  **precedent for containment**, not for partial shipping.
- **D8 — Return type, Protocol, and flow contract are otherwise unchanged.** No new types, no `WeatherForecastSource`
  change, no per-station outcome model — those belong to Plan 151 Phase 3. This is the smallest change that stops one
  station's gap darkening the deployment.
- **D9 — The single-HRU cycle PROBE is OUT OF SCOPE, and named as a separate defect.**
  `_resolve_effective_cycle` probes **one** HRU — `resolved[0].hru_name` (`:784`) — and raises
  `RecapDataUnavailableError` before the accumulation loop exists (`:791-796`) when no candidate falls within
  `max_cycle_age_hours`. If the *probe* HRU is the unavailable one, the cycle still degrades wholesale and this plan's
  containment never runs. That is a **second, distinct** manifestation of the same root cause, gated on the factual
  question in D10. **T1's tests must stub the probe explicitly** so they exercise the accumulation loop rather than
  passing accidentally. Scoping this out keeps the fix small and honest; it does not claim to close every path.

## Phases

- **T1 — Per-HRU containment with all-or-nothing HRU commit (D1–D6) + docs.**
  - **In scope:** `src/sapphire_flow/adapters/recap_gateway.py` — per-HRU exception containment, an HRU-local staging
    buffer merged into the shared accumulator only when that HRU has its complete variable set, provenance committed
    on the same boundary, the three-way committed/discarded/empty outcome, and the contained-gap WARNING.
    `src/sapphire_flow/flows/run_forecast_cycle.py` — requested-vs-returned station reconciliation **restricted to a
    non-empty proper subset**, the `NWP_DELIVERY` WARNING `pipeline_health` record, and the partial flag into
    `_forecast_cycle_health` (D4). Add the event to `docs/standards/logging.md` and the entry to `docs/plans/README.md`.
  - **Red-first acceptance tests** (extend `tests/unit/adapters/test_recap_gateway.py`; each stubs the cycle probe so
    D9 cannot mask the behaviour under test):
    1. **Two HRUs, A discarded, B complete** → returns B's stations, not A's; no raise. **Fails today.**
    2. **Order independence** — same scenario with A and B swapped. **Fails today in both orders**; this variant
       specifically locks preservation when the failure lands *after* successful accumulation.
    3. **The multi-station-per-HRU invariant holds** — for two stations sharing one HRU, a discarded HRU yields
       **neither**, and a successful HRU yields **both** (never a subset), locking D2's "all stations in an HRU share
       the fetch outcome" reasoning. Paired with an unchanged-behaviour assertion that a **missing polygon column
       still raises batch-wide `AdapterError`** naming the affected station (D6), which is what makes station-level
       partiality unrepresentable here — the existing `TestMissingPolygonColumnBatch` contract must not regress.
    4. **Partial forcing is never returned** — an HRU whose precipitation succeeds but whose temperature control
       fails contributes nothing; with that HRU alone the call re-raises.
    5. **Total unavailability unchanged** — every HRU discarded → `RecapDataUnavailableError` still propagates, so
       `_fetch_nwp_task` still returns `nwp_unavailable=True` and the cycle still degrades to runoff-only.
    6. **Well-formed empty is not a failure** — all HRUs return empty, none discarded → returns `{}` with no raise,
       exactly as today; distinct from case 5.
    7. **Single-HRU deployment unchanged** — today's production shape yields the same returned result / raised
       exception and the same flow outcome. (Not byte-identical *logging*: D4 adds new observable output by design.)
    8. **Auth is not contained** — `RecapAuthError` propagates immediately; no partial result.
    9. **`pf` containment unchanged** — the "fc present, pf absent" window still yields control + accumulated pf
       members; no regression to Plan 127 Fix 1.
    10. **Provenance** — a discarded HRU cannot determine the returned results' `cycle_time`.
    11. **Flow-level partial** (`tests/unit/flows/test_run_forecast_cycle.py`): with one station returned and one
        missing, the complete station is persisted, the missing station falls back locally, a queryable
        `NWP_DELIVERY` WARNING `pipeline_health` record names it, and cycle health is **DEGRADED** (D4). **Plus the
        boundary case:** an **empty** mapping records **no** partial-loss health record and does **not** degrade the
        cycle — proving reconciliation is restricted to a non-empty proper subset.
    12. **A contained gap logs** `recap.hru_unavailable_contained` with HRU name + variable.
  - **Test soundness:** tests 1–2 must be shown failing against unmodified `fetch_forecasts`; test 4 against a naïve "any rows → return" implementation. State which baseline
    each was proven against.
  - **Exit gate:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/flows/test_run_forecast_cycle.py -q`
    + full `uv run pytest -q` + `uv run pyright` + `uv run ruff check`.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false }
  ]
}
```

## Non-goals
- **The single-HRU cycle probe** (D9) — named, not fixed; gated on D10.
- **Any per-station outcome modelling** (`StationTrackOutcome`, `CandidateFetchResult`) — Plan 151.
- **The missing-polygon-column batch-wide raise** (D6) — unchanged.
- **The snow channel** — already contained (D7).
- **MeteoSwiss** — different adapter, different failure model.

## Open items
- **D10-probe-representativeness — FACTUAL QUESTION for the owner / hydrosolutions; gates D9.**
  `_resolve_effective_cycle` documents "IFS publication cadence is global across HRUs, so ONE already-resolved in-scope
  HRU is used as the probe" (`src/sapphire_flow/adapters/recap_gateway.py:773-783`). Global cadence is **not
  sufficient** to make that safe: the probe issues a real **HRU-specific** `ifs_forecast` request (`:403`), so an
  HRU-specific data or subscription gap can make the *probe* HRU fail even when the cycle is published everywhere
  else. **The question is therefore broader than cadence:** does the probe HRU's availability for the probe variable
  reliably represent cycle availability across **all** in-scope HRUs? **If yes**, the probe is sound and this
  containment is the whole fix. **If no**, the probe is a second instance of this bug and needs its own fix (probe
  fallback across HRUs, or per-HRU resolution). Worth confirming before Nepal onboarding, since the entire
  single-probe design rests on it.
- **Relationship to Plan 151.** Plan 151 D7 requires exactly this containment for its per-track path and currently
  carries it inside its own T4. Landing 154 first shrinks 151's T4 and de-risks it; landing 151 first would leave 154
  still needed on the legacy path, which persists until Phase 4. They do not conflict. Recommended order: **154
  first** — small, independently valuable, testable today.
