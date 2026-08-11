---
status: DRAFT
created: 2026-08-11
plan: 154
title: Recap IFS per-HRU fetch containment — one HRU's missing data must not degrade the whole cycle
scope: Contain RecapDataUnavailableError per HRU inside RecapGatewayForecastAdapter.fetch_forecasts using per-HRU staging with all-or-nothing commit, so a data gap for one HRU no longer discards every sibling HRU's already-accumulated rows and no longer escalates a station-scoped gap into the flow's cycle-wide runoff-only degradation. A station is returned only with COMPLETE forcing (every IFS variable), never a partial set. Preserves today's behaviour exactly when no HRU completes. Adapter-internal; no return-type change, no Protocol change, no flow-contract change. The single-HRU cycle PROBE is explicitly out of scope and raised as an open question.
depends_on: []
blocks: []
supersedes: []
---

# Plan 154 — Recap IFS per-HRU fetch containment

## Status
**DRAFT.** A standalone operational-reliability fix (category **A**), independent of the forecast-cycle redesign.
Numbered 154 because 152 is taken (aquacast) and 153 is informally reserved for multi-resolution domain-type support.
An independent Codex review of the first draft found two real defects in it (a partial-forcing hazard and an
out-of-scope failure path); both are folded in below.

## Problem
`RecapGatewayForecastAdapter.fetch_forecasts` loops over HRUs and, for each variable, fetches the deterministic
control member **outside any containment block**:

- The HRU loop is `for hru_name, refs_by_polygon in by_hru.items()`
  (`src/sapphire_flow/adapters/recap_gateway.py:825`).
- The **control (`fc`)** call — `_accumulate_member(..., ifs_type="fc", member=None, member_id=_FC_MEMBER_ID)` — sits
  directly in that loop body with **no `try`** (`:830-841`).
- Only the **perturbed (`pf`)** member loop is contained: `except RecapDataUnavailableError:` → log
  `recap.pf_unavailable_control_only` → `break` (`:842-871`). That containment was added deliberately (Plan 127 Fix 1)
  because ECMWF disseminates `fc` before `pf`, so during that window every `pf` member is absent.
- `_accumulate_member` reaches the Gateway through `_guarded_fetch(self._client.ecmwf.ifs_forecast, ...)`
  (`:908-911`), which raises `RecapDataUnavailableError` when the Gateway reports `source_data_missing`.

**Consequence.** A control-fetch gap for **one** HRU propagates out of the *entire* `for hru_name` loop. Every row
already accumulated for HRUs processed earlier is discarded with the stack frame, and HRUs later in iteration order are
never attempted.

**Blast radius is the whole cycle, not just the sibling HRUs.** At flow level, `_fetch_nwp_task` catches
`RecapDataUnavailableError` (`src/sapphire_flow/flows/run_forecast_cycle.py:1101-1116`) and returns
`_NwpFetchOutcome(cycle_time=..., fallback_used=False, nwp_unavailable=True)` with a WARNING pipeline-health record
naming `source_data_missing`. That handler is **correct** for a genuinely cycle-wide gap — it degrades the cycle to
runoff-only rather than aborting. But here it fires on a **station-scoped** cause, so **every station in the
deployment loses NWP forcing for that cycle**, including stations whose own HRU had complete data.

**Root cause:** the adapter **loses the scope of the failure**. It raises the same exception for "this one HRU's data
is missing" and "this cycle is not published at all", so the flow — which can only see the exception type — cannot
tell a station-scoped gap from a cycle-wide one, and correctly applies the cycle-wide remedy to both.

**The snow channel already does this correctly** — this plan makes the IFS channel consistent with it.
`fetch_snow_forecast`'s contract states that a `(hru, variable)` fetch raising `RecapSnowUnavailableError` "is
CONTAINED — rows already accumulated for other variables in the SAME hru are preserved, and the gap is recorded in the
returned result's `unavailable` map, keyed per HRU (never a global/cross-HRU set)"
(`src/sapphire_flow/adapters/recap_gateway.py:953`; Plan 145 D3.2). The IFS path is the outlier.

**Reachability.** Latent on the current single-HRU deployment; live with multi-HRU Nepal. Note the adapter's own
documented assumption that *publication cadence* is global across HRUs (`:773-783`) — this fix targets per-HRU **data**
gaps (a specific HRU's rows missing at an otherwise-published cycle), which that assumption does not cover. Whether
cadence itself can vary per HRU is a separate factual question, raised in Open items (D8).

## Design

- **D1 — Contain per HRU, with ALL-OR-NOTHING commit via per-HRU staging.** Wrap each HRU's fetch in
  `except RecapDataUnavailableError`, accumulating that HRU's rows into a **staging buffer**, and merge the buffer into
  the shared accumulator **only if that HRU completed every required variable**. On a gap, discard that HRU's staged
  rows and continue to the next HRU.
  - **Why staging is mandatory (reviewer blocker-fix).** IFS fetches **two** variables per HRU — precipitation then
    temperature (`RECAP_VARIABLES`, `:75-90`) — and `_accumulate_member` writes rows straight into the shared `acc` as
    each member succeeds (`:912-921`). A naïve "if any rows accumulated, return them" rule would therefore return a
    station whose precipitation succeeded but whose **temperature control fetch failed** — shipping *incomplete
    forcing* as success, which is strictly worse than today's cycle-wide degradation. **Invariant: a station appears in
    the returned mapping only with its complete variable set.**
  - Partiality is therefore modelled at **HRU** granularity, never variable granularity. Variable-level partial forcing
    is never returned.
  - **Stage the provenance, not just the rows** (reviewer fix). `cycle_source_run` is shared mutable state threaded
    through `_accumulate_member`'s `prior` argument (`src/sapphire_flow/adapters/recap_gateway.py:823`), and it is set
    by the *first* successful call — so a discarded HRU's successful precipitation fetch could otherwise determine the
    returned results' `cycle_time`. Provenance is committed on the **same all-or-nothing boundary** as the rows.
- **D2 — Distinguish PARTIAL from TOTAL by COMPLETED HRUs.** After the loop:
  - **At least one HRU completed → return the merged rows** for those HRUs. Stations in unavailable HRUs are simply
    absent from the returned `dict[StationId, WeatherForecastResult]`, which is already the return shape's way of
    saying "no data for this station" — `fetch_forecasts` returns only stations that accumulated rows (`:874-878`), and
    the downstream per-station path already degrades such a station on its own.
  - **No HRU completed → re-raise `RecapDataUnavailableError`**, so the flow's existing cycle-wide handler fires
    unchanged. On a single-HRU deployment "no HRU completed" is the *only* reachable failure outcome, so today's
    behaviour is preserved exactly — including the single-HRU precip-ok/temp-missing case, which re-raises rather than
    returning partial forcing.
- **D3 — A contained gap is logged, never silent.** Each contained HRU emits a WARNING naming the HRU, the variable
  that failed, and `ifs_type`, mirroring the existing `recap.pf_unavailable_control_only` precedent (`:862-868`). Event
  name: `recap.hru_unavailable_contained` (`{entity}.{action}`, `docs/standards/logging.md`). This matters because the
  partial path returns *successfully* and would otherwise leave no trace.
- **D4 — Auth and configuration errors stay fatal and uncontained.** `RecapAuthError` and `RecapConfigurationError`
  keep propagating immediately; the flow hard-aborts on auth (`src/sapphire_flow/flows/run_forecast_cycle.py:1086-1100`) because an
  expired/misconfigured key needs operator action, not a per-station skip. Containment applies **only** to the
  temporal `source_data_missing` signal.
- **D5 — The missing-polygon-column raise is NOT touched.** `_iter_long_rows` raises a batch-wide `AdapterError` when a
  resolved polygon lacks a response column (`:511-525`), deliberately, so a corrupt response cannot **silently** drop a
  station. Different cause (malformed response, not absent data); unchanged here. Re-classifying it is Plan 151's
  business, and only on its new per-track path.
- **D6 — Return type, Protocol, and flow contract are unchanged.** No new types, no `WeatherForecastSource` change, no
  per-station outcome model. Those belong to Plan 151 Phase 3. This is deliberately the smallest change that stops one
  HRU darkening the deployment.
- **D7 — The single-HRU cycle PROBE is OUT OF SCOPE, and named as a separate defect** (reviewer major-fix).
  `_resolve_effective_cycle` probes **one** HRU — `resolved[0].hru_name` (`:784`) — and raises
  `RecapDataUnavailableError` before the accumulation loop exists (`:791-796`) when no candidate falls within
  `max_cycle_age_hours`. So if the *first* HRU is the unavailable one, the cycle still degrades wholesale and this
  plan's containment never runs. That is a **second, distinct** manifestation of the same root cause, and fixing it
  requires deciding whether cadence can vary per HRU (D8) — a factual question about the Gateway, not a code change.
  **T1's tests must therefore model or stub the probe explicitly** so they exercise the accumulation loop rather than
  passing accidentally. Scoping this out keeps the fix small and honest; it does **not** claim to close every path.

## Phases

- **T1 — Per-HRU containment with all-or-nothing staging (D1–D5) + docs.**
  - **In scope:** `src/sapphire_flow/adapters/recap_gateway.py` — per-HRU staging buffer, all-or-nothing merge,
    re-raise on total unavailability, WARNING log event. Add the event to `docs/standards/logging.md` and the plan
    entry to `docs/plans/README.md`.
  - **Red-first acceptance tests** (extend `tests/unit/adapters/test_recap_gateway.py`; each stubs
    `_resolve_effective_cycle`/`resolve_latest_cycle` so the probe (D7) cannot mask the behaviour under test):
    1. **Two HRUs, A's control fetch unavailable, B complete** → returns B's stations, not A's; no raise.
       **Fails today** (the error escapes the whole loop).
    2. **Iteration-order independence** — same scenario with A and B swapped: B's stations are returned either way,
       proving staged rows are not discarded when the failure lands *after* them. **Fails today** in the A-first order.
    3. **Partial forcing is NEVER returned** — one HRU whose **precipitation succeeds but temperature control fails**
       contributes **nothing**; with that HRU alone the call **re-raises**. Locks D1's staging invariant, and would
       fail against a naïve "any rows → return" implementation.
    4. **Total unavailability unchanged** — every HRU unavailable → `RecapDataUnavailableError` still propagates, so
       `_fetch_nwp_task` still returns `nwp_unavailable=True` and the cycle still degrades to runoff-only.
    5. **Single-HRU deployment unchanged** — today's production shape yields the **same returned result / raised
       exception and the same flow outcome** on both the success and the unavailable path. (Not byte-identical
       *logging*: D3 adds a contained-gap WARNING, which is new observable output by design.)
    6. **Auth is not contained** — `RecapAuthError` from one HRU propagates immediately; no partial result returned.
    7. **`pf` containment unchanged** — the "fc present, pf absent" window still yields control + accumulated pf
       members (`recap.pf_unavailable_control_only`); no regression to the Plan 127 Fix 1 contract.
    8. **A contained gap logs** `recap.hru_unavailable_contained` with HRU name + variable.
  - **Test soundness:** tests 1–2 must be shown **failing against unmodified `fetch_forecasts`**. Test 3 is a
    different kind of lock — it *passes* against current code (which raises) and is there to fail against a **naïve
    non-staging fix**, so it must be demonstrated red against that intermediate implementation, not against `main`.
    State which baseline each test was proven against.
  - **Exit gate:** `uv run pytest tests/unit/adapters/test_recap_gateway.py -q` + full `uv run pytest -q` +
    `uv run pyright` + `uv run ruff check`.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false }
  ]
}
```

## Non-goals
- **The single-HRU cycle probe** (D7) — named, not fixed; gated on D8.
- **Any return-type / per-station-outcome modelling** (`StationTrackOutcome`, `CandidateFetchResult`) — Plan 151.
- **The missing-polygon-column batch-wide raise** (D5) — unchanged.
- **The snow channel** — already contained (Plan 145 D3.2).
- **MeteoSwiss** — different adapter, different failure model.

## Open items
- **D8-per-hru-cadence — FACTUAL QUESTION for the owner / hydrosolutions; gates D7.** `_resolve_effective_cycle`
  documents "IFS publication cadence is global across HRUs, so ONE already-resolved in-scope HRU is used as the probe"
  (`src/sapphire_flow/adapters/recap_gateway.py:773-783`). But global cadence is **not sufficient** to make the probe safe: the probe
  issues a real **HRU-specific** `ifs_forecast` request (`:403`), so an HRU-specific data or subscription gap can make
  the *probe* HRU fail even when the cycle is published everywhere else. **The question to answer is therefore broader
  than cadence:** does the probe HRU's availability for the probe variable reliably represent cycle availability across
  **all** in-scope HRUs? **If yes**, the probe is sound and this containment is the whole fix. **If no** — whether
  because cadence varies or because the probe HRU can individually lack data — the probe is a second instance of this
  same bug and needs its own fix (probe fallback across HRUs, or per-HRU resolution). Worth confirming against the
  Gateway's actual behaviour before Nepal onboarding, since the whole single-probe design rests on it.
- **D9-partial-health-record — RECOMMENDATION, owner to confirm.** On the partial path the fetch now *succeeds*, so
  the flow records **no** pipeline-health warning and the cycle looks healthy even though some stations lost NWP
  forcing. D3's WARNING log makes it visible in logs but not in the health store.
  **Options:** **(A) RECOMMENDED — adapter-side log only here**, raising health fidelity as a follow-on, keeping this
  fix minimal and behaviour-preserving; **(B)** thread a partiality signal out of `fetch_forecasts` so `_fetch_nwp_task`
  can append a WARNING `NWP_DELIVERY` record naming the affected HRUs — better observability, but it changes the
  adapter's return contract, which D6 avoids and which Plan 151 restructures anyway.
- **Relationship to Plan 151.** Plan 151 D7 *requires* exactly this containment for its per-track path and currently
  carries it inside its own T4. Landing 154 first shrinks 151's T4 and de-risks it; landing 151 first would make 154
  redundant on the migrated path but **not** on the legacy path, which persists until Phase 4. They do not conflict.
  Recommended order: **154 first** — small, independently valuable, testable today.
