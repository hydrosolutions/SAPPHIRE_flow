# Plan 090 — NWP incomplete-cycle selection + horizon-coverage validation

**Status**: READY (grill-me held 2026-07-02 — D1–D5 resolved below; phases
concrete). Implement Phase 1 (age-delay guard + post-download validation) before
the Mac-mini data-collection deployment.
**Priority**: **elevated / near-term** — this silently truncated a live NWP
forecast horizon (5 days → 1 step) with no error. Not a "parked" nice-to-have.
**Phase**: v0b operational hardening (NWP adapter robustness + forecast validation)
**Parent**: epic 088 (NWP-on forecasting) — surfaced during the 2026-07-02 live
onboarding of stations 2009/2091
**Related**: `adapters/meteoswiss_nwp.py` (`resolve_cycle` / `_cycle_is_published`
/ `CycleResolution` / `max_fallback_steps`), `services/operational_inputs.py`
(daily aggregation), `models/nwp_regression.py` (`horizon = len(future_times)`),
M4 provenance (`nwp_cycle_source`), `docs/research/063-meteoswiss-stac-probe.md`,
Plan 089 (recorded this as a sibling follow-up)
**Created**: 2026-07-02 · **Revised**: 2026-07-02 (post design-review)

---

## Problem — TWO defects, not one (observed live 2026-07-02)

MeteoSwiss publishes an ICON-CH2-EPS cycle **incrementally**: early lead-times
appear on OGD first, later lead-times fill in over ~an hour (note at
`adapters/meteoswiss_nwp.py:76`).

**Defect A — the adapter selects an incompletely-published cycle.**
`resolve_cycle()` stops at the first `_cycle_is_published(candidate)` success, and
`_cycle_is_published()` returns true if **any** feature has a matching
`forecast:reference_datetime` — there is **no lead-time / coverage check**
(`meteoswiss_nwp.py:350, :381, :404`). The fetch path is likewise coverage-blind:
it lists `cycle_time → cycle_time+120h`, client-filters by
`forecast:reference_datetime`, and downloads matching allowlisted assets
(`:467, :523, :535`). So it selected the freshly-published **06Z** cycle (~30 of
~120 hourly steps uploaded at fetch) over the **complete 00Z** cycle (121 steps).

**Defect B — partial NWP is not validated; the forecast is silently truncated.**
The daily aggregation + `_filter_and_cap_daily_records` are **correct** for the
observed case (`operational_inputs.py:71, :111, :124`): for an ~08:00 issue time
with 06Z data only through next-day 14:00, the daily buckets are issue-day 00:00
(backdated, dropped) and next-day 00:00 → exactly **1** future bucket survives.
The bug is that **nothing rejects or flags an under-covered future frame**:
`NwpRegression.predict` forecasts `horizon = len(future_times)`
(`models/nwp_regression.py:214, :240`), so a 1-row future frame becomes a 1-step
forecast **with no error**. Even a perfect cycle-selection fix should be paired
with a coverage guard so a short/partial NWP frame fails loudly (and falls back)
rather than producing a truncated forecast.

Evidence (stored `weather_forecasts` at fetch time):

| cycle | hourly steps | valid range |
|---|---|---|
| 00Z (complete) | 121 | 07-02 00:00 → 07-07 00:00 (5 days) |
| 06Z (selected, incomplete) | 30 | 07-02 06:00 → 07-03 14:00 (~1.3 days) |

## Goal

The pipeline never emits a silently-truncated NWP forecast: (1) the adapter
prefers a cycle with **adequate coverage for the deployment's horizon** over a
newer-but-incomplete one, and (2) an under-covered future frame is **validated
and rejected** (fall back / fail loudly), not consumed. Provenance and logs make
the reason explicit.

## Resolved decisions (grill-me 2026-07-02)

- **D1** — coverage = **N future daily buckets strictly after the nominal
  issue_time, for every required variable AND every required member/type**, where
  N = the model's `forecast_horizon_steps` in `time_step` units (not raw hours).
- **D2** — implement **(c) age-delay guard (selection gate) + (d) mandatory
  post-download coverage validation** first. Defer (a) terminal-valid-time probe
  and (b) full-listing count as later precision refinements.
- **D3** — add `fallback_reason` (`incomplete_coverage` / `not_published` /
  `late`). On fallback-budget exhaustion (no adequate cycle within
  `max_fallback_steps`): **fall to runoff-only for NWP-consuming models** (they
  produce nothing this cycle; fallback models still forecast via the priority
  chain) — do NOT fail the whole cycle.
- **D4** — prefer the **complete older cycle** for daily models (accept ~6 h less
  freshness for a full horizon). Sub-daily/nowcasting out of scope.
- **D5** — the coverage requirement is **derived** from `forecast_horizon_steps`
  + `time_step` (not operator-set); the **delivery-delay** is an operator-tunable
  `DeploymentConfig` value (default ≈ 90–120 min, ICON publish latency).

## Phases

- **P1 — cheap mitigation (ship before mini deploy):**
  - Config: add a delivery-delay to `DeploymentConfig` + `config.toml` (default
    ≈ 90–120 min).
  - Adapter selection gate: `resolve_cycle` skips a snapped cycle whose age
    `(now - cycle_time) < delivery_delay` and walks back per `max_fallback_steps`
    (age-delay guard, D2c); this legitimately sets `fallback_used=True`.
  - Post-download validation (D2d): after the future frame is assembled
    (`operational_inputs` / a shared guard), require ≥ N daily buckets per
    variable/member (D1); on shortfall, treat NWP as unavailable for that station
    → **runoff-only** path (M4 `RUNOFF_ONLY` provenance) rather than a truncated
    forecast.
  - `fallback_reason` logging (D3); runoff-only-on-exhaustion (D3).
  - Tests (RED-confirmed): (i) partial newest + complete older cycle → complete
    chosen, `fallback_used=True`, `fallback_reason=incomplete_coverage`; (ii) an
    under-covered assembled frame → runoff-only, NOT a 1-step forecast.
- **P2 — precision refinement (later, optional):** (a) terminal-valid-time STAC
  probe per variable/member for exact pre-download coverage, reducing wasted
  fetches of doomed partial cycles. Own follow-up once P1 is in.

## Design questions (resolved above — retained for rationale)

### D1 — Coverage criterion (define in DAILY BUCKETS, not raw hours)
"≥ `forecast_horizon_steps` of lead-time" as written is under-specified:
`forecast_horizon_steps` is **model steps**, not hours, and the check must
account for `time_step`, UTC-midnight bucket semantics, and the fact that Phase B
keeps the **nominal/clock** issue time even when NWP falls back
(`run_forecast_cycle.py:862`). Practical criterion for daily models: **"the cycle
can yield N future daily buckets strictly after the nominal issue_time, for every
required NWP variable AND every required ensemble member/type"** — not just a max
hourly step. Precip is the sharp case: intermediate hourly-accumulation gaps must
not pass a naive max-step check.

### D2 — Detection mechanism (feasibility refined)
Pre-download detection **is** feasible — live STAC exposes `forecast:horizon` per
item (probed: `.../items?datetime=2026-07-02T06:00:00Z&limit=1` →
`forecast:horizon = P0DT18H00M00S`). But **server-side filtering by
`forecast:reference_datetime` is NOT available** — only `datetime` (valid-time)
filters server-side (`docs/research/063-meteoswiss-stac-probe.md:13, :59`). So
"enumerate the whole cycle and count distinct lead-times" is more expensive than
needed. Candidate mechanisms to weigh:
- **(a) Terminal-valid-time probe (cheapest):** query
  `datetime=<required terminal valid-time(s)>` and client-filter by
  `forecast:reference_datetime` + variable + perturb/control to confirm the
  needed far lead-times exist for the required variables/members.
- **(b) Full-listing coverage count:** list the cycle window once, count distinct
  lead-times per variable/member. Simpler logic, more items fetched.
- **(c) Age-delay guard (cheapest, least exact):** ignore cycles younger than a
  configured delivery-delay (existing age/fallback config:
  `config/deployment.py:77`, `config.toml:12`). A good **first operational
  mitigation** even before full coverage probing.
- **(d) Post-download / parsed-dataset guard (defence in depth — likely always
  needed):** after fetch+parse, validate the assembled future frame meets D1 per
  variable/member; reject if short. Catches the cases (a)-(c) miss: a listing that
  looks complete but an asset download fails or a variable/member is absent. NOTE
  today an adapter fetch failure **aborts** NWP fetch rather than walking back to
  an older cycle (`run_forecast_cycle.py:327`) — the walk-back-on-insufficient
  behaviour must be designed in.

Likely answer: **(c) or (a) as the selection-time gate + (d) as the mandatory
post-assembly validation.**

### D3 — Fallback semantics, provenance, and budget
An incomplete newest cycle should consume a `max_fallback_steps` step and walk
back to the last adequate cycle. `fallback_used=True` is semantically fine
(current provenance maps any adapter walk-back to `NwpCycleSource.FALLBACK` —
`run_forecast_cycle.py:106, :844`), BUT add a **diagnostic `fallback_reason`**
(e.g. `incomplete_coverage` vs `not_published` vs `late`) so "fallback" doesn't
hide *why*. Decide: what if the fallback budget is exhausted by successive
incomplete cycles (fail the cycle? emit runoff-only? widen the budget)?

### D4 — Staleness vs completeness trade-off
Falling back to 00Z when 06Z is partial trades ~6 h freshness for a full horizon —
almost certainly right for daily models; confirm, and note it may differ for a
future sub-daily / nowcasting use case (out of scope here).

### D5 — Config surface
Whether the coverage threshold / delivery-delay is a `DeploymentConfig` value
(operator-tunable) or derived from the forecast horizon + `time_step`.

## Non-goals

- Changing the daily aggregation / `_filter_and_cap_daily_records` (correct).
- Sub-daily / nowcasting cycle handling (future).

## Affected surfaces (preliminary)

- `adapters/meteoswiss_nwp.py` — `resolve_cycle` / `_cycle_is_published` / STAC
  item enumeration / `CycleResolution`: coverage-aware acceptance + walk-back.
- `services/operational_inputs.py` or `run_station_forecast` — **post-assembly
  coverage validation** of the future frame (per variable/member) with loud
  reject + fallback.
- `flows/run_forecast_cycle.py` — walk-back-on-insufficient (not just on
  not-published); `fallback_reason` logging; provenance already threads
  `fallback_used`.
- `config/deployment.py` + `config.toml` — coverage / delivery-delay threshold.
- tests — fake STAC listing with a partial newest + complete older cycle
  (assert the complete one is chosen, `fallback_used=True`,
  `fallback_reason=incomplete_coverage`); a parsed under-covered frame is
  rejected / falls back rather than producing a 1-step forecast.

## Process

READY (grill-me done). **P1** is the next implementation (hold-at-PR) and gates
the Mac-mini data-collection deployment's forecast quality. **P2** is a deferred
precision refinement. Re-scope P2 into its own plan when P1 lands.
