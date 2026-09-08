---
status: DRAFT
created: 2026-09-08
plan: 257
title: A whole forecast product went dark for four days under a green light
scope: ONE pipeline_health check type for combined-forecast coverage, emitted once per forecast cycle from the existing end-of-cycle accounting point, plus the decision of whether the watchdog pages on it. Explicitly NOT the combiner's behaviour (Plan 222 — correct as-is), NOT the anchoring that would refill the product (Plan 226), NOT a general per-product coverage ledger for every model, NOT new alert transport.
depends_on: []
blocks: []
source: 2026-09-08 — read-only diagnosis of why `_pooled` writes stopped on the mac-mini staging host. The stoppage was found by hand four days later; nothing in the system had reported it.
---

# Plan 257 — combined-forecast coverage is unmonitored

## Status

**DRAFT — not reviewed.**

## The problem in one row

`_pooled` forecasts stopped being written on the staging host at **2026-09-04 06:26Z**. Four days
later, on 2026-09-08, `pipeline_health` said this:

| check_type | status | latest |
|---|---|---|
| `forecast_freshness` | **ok** | 2026-09-08 06:27:02Z |

That is not a bug in `forecast_freshness`. It is the check working exactly as specified, and saying
something true and useless: *some* forecast was stored. About 1336 per day were, from the five other
`model_id`s. Meanwhile a product 34 stations depend on had produced **nothing for four days**, and
the API had been serving `available: false, reason: "no_combined_forecast"` for all of them.

## This was deferred deliberately, and the deferral is written down

`flows/run_forecast_cycle.py:730-741`, the `FORECAST_FRESHNESS` docstring:

> This answers a narrower, product-facing question instead: did the cycle persist ANY forecast at
> all? … **No partial-coverage state is tracked here (that is the explicitly out-of-scope per-product
> coverage ledger).**

Plan 116 named the gap and scoped it out. This plan closes the one instance of it that has now
demonstrably cost four days of blindness — it does **not** build the general ledger.

## ⛔ Proportionality — BINDING on this plan and on its review

This is a monitoring plan for a condition whose cause is already diagnosed and whose fix is already
owned elsewhere. It must stay small.

- **In:** one `PipelineCheckType`, one emitter, its `detail` payload, its status mapping, tests, and
  the watchdog-paging decision.
- **Out:** changing `combine_ensembles_pooled` or `build_combined_forecasts` in any way that alters
  which rows get written; anything that would make the check *pass* by lowering the bar; a coverage
  ledger for `nwp_rainfall_runoff`, the fallbacks, or any other `model_id`.
- A review finding that proposes generalising this to all products, or that re-litigates Plan 222's
  intersection, is **out of scope** — record it against Plan 226 or a new plan instead.

## What is measured (read-only, live mini, 2026-09-08)

Cycle `2026-09-08 06:00:05.032956Z`:

| | |
|---|---|
| stations with ≥2 combinable models (`nwp_regression` + `nwp_rainfall_runoff` + `linear_regression_daily`) | **34** |
| `_pooled` rows written | **0** |
| stations with exactly 1 combinable model (legitimately no pool) | 101 |
| `valid_time`s shared by all three contributors | **0** — max overlap is 2 models |

The cause is Plan 222's union→intersection change reaching the host on 2026-09-04; the three-way
`valid_time` intersection is empty because `linear_regression_daily` sits on issue-time phase and
the NWP pair sit on midnight. **That behaviour is correct and stays** (Plan 222 §D7: "Absence is the
accepted, honest outcome"). Plan 226 is the fix that refills the product. This plan only makes the
absence *visible*.

⚠️ Consequence to face squarely: **the check this plan adds will be non-OK from the moment it ships,
and will stay non-OK until Plan 226 lands.** That is the correct reading of reality. D2 decides what
status that is and whether it pages, because a check that is born red and stays red for weeks trains
operators to ignore it — which is the same failure this plan exists to fix, one level up.

## Decisions

- **D1 — what counts as "eligible"?** Recommend: a station is eligible for a combined forecast this
  cycle when `len(multi_result.combinable_results) >= 2` — the same predicate
  `build_combined_forecasts` already uses (`services/forecast_combination.py:308-310`), read off
  `MultiModelForecastResult.combinable_results`
  (`services/run_station_forecast.py:127-131`). This needs **no change to the combiner** and cannot
  drift from it, because it is the same property. Rejected alternative: counting from
  `model_assignments`, which would call 148 stations eligible and never agree with what actually ran.
- **D2 — what status, while Plan 226 is open?** Two candidates, and the plan must pick one before
  T1 is written:
  - **(a) Honest and loud.** `critical` when eligible > 0 and written == 0; `warning` when
    0 < written < eligible; `ok` when written == eligible. Born red. Recommended for the *record*;
    see D3 for whether it pages.
  - **(b) Declared-baseline.** Config carries the currently-accepted dark count; status is relative
    to it, so a *regression* is red and the known-dark state is `warning`. Honest but adds a config
    knob that must be un-set when Plan 226 lands, and a stale knob is its own silent failure.
  Recommendation: **(a)**, paired with D3's "record but do not page". It needs no new config and the
  red state is exactly the pressure that should exist while a product is dark.
- **D3 — does the watchdog page on it?** Recommend **no, not initially.** Record to
  `pipeline_health` (visible in `/health/detail?check_type=…`) but do not add a watchdog probe until
  Plan 226 has landed and the check has been observed green for at least one full day. Rationale: the
  watchdog's Slack path is the shared alert channel; introducing a permanently-firing alert there
  would degrade every other alert on it. The plan must record this as a **deliberate, time-boxed**
  deferral with the trigger for revisiting it, not leave it unstated — an unpaged check is still a
  check nobody reads.
- **D4 — is one row per cycle, or one per station, the right cardinality?** Recommend **one per
  cycle**, subject `forecast_combination`, with counts in `detail`. Per-station rows would add ~34
  rows/cycle to a table that already carries 646 `alert_suppressed_fallback` warnings since 09-04,
  and the question being asked ("is this product producing?") is cycle-level. Per-station attribution
  is already available from the `forecasts` table.
- **D5 — how does an aborted cycle report coverage?** ⚠️ This is the trap in T1.
  `_emit_forecast_freshness_record` has **five** call sites, not one
  (`flows/run_forecast_cycle.py:1764, 2387, 2677, 3515, 3615`): normal completion, no-operational-
  stations early return, NWP-fetch abort, the group-store fatal, and the fatal-exit helper — by
  design, so "a dark cycle never silences its own heartbeat" (`:1754-1761`; note that docstring says
  "all four", which is now stale). A coverage check that emits only at `:3615` goes silent on exactly
  the cycles that most need explaining. But an aborted cycle has **no meaningful eligibility count**
  — no station work ran — and reporting it as `stations_eligible: 0` would read as "no pool was
  possible", which is a different and misleading claim from "the cycle never got that far".
  Recommend: emit at all five sites, with `detail` carrying an explicit
  `"cycle_completed": true|false`, and status forced to **`warning`, never `critical`, when
  `cycle_completed` is false** — the cycle's own abort is already `critical` on
  `forecast_freshness`, and duplicating it here would double-page one fault while telling the next
  investigator to look at the combiner. The plan must not skip this; a coverage check that is
  ambiguous between "dark product" and "dead cycle" recreates the confusion this diagnosis cost four
  days to resolve.

## Tasks

### T1 — the check (the whole of the required work)

Add `FORECAST_COMBINATION_COVERAGE = "forecast_combination_coverage"` to `PipelineCheckType`
(`types/enums.py:193`) and a sibling emitter to `_emit_forecast_freshness_record`
(`flows/run_forecast_cycle.py:720-787`), following that function's existing conventions exactly:

- **Skip records for explicit-`cycle_time` runs** (backfill/replay) for the reason already documented
  there: `fetch_recent` orders by `checked_at`, so a backfill written "now" would become the latest
  record and could mask or fake a live state.
- `subject="forecast_combination"`, `cycle_time=resolved_cycle_time`.
- `detail`: `{"strategy": <configured strategy value>, "cycle_completed": bool,
  "stations_eligible": int, "stations_written": int}`.
- Called from all five sites that already emit `FORECAST_FRESHNESS` (D5), with
  `cycle_completed=False` at the four abort/fatal sites.
- Status per D2, floored at `warning` when `cycle_completed` is false (D5).
- When the configured strategy is `PRIMARY`, emit **`ok` with `stations_eligible: 0`** — under
  PRIMARY no combined product is expected and a red light would be a false alarm. Do not skip the
  record entirely; a missing row is indistinguishable from a dead flow.
- Fix the now-stale "all four" count in the `:1754-1761` docstring while touching these call sites.

**Verification:** unit tests over the status mapping at each boundary (eligible=0; eligible>0 and
written=0; partial; full; PRIMARY; and `cycle_completed=False` with eligible>0 asserting it does
**not** reach `critical`), plus one test asserting the record is **not** written for an
explicit-`cycle_time` run, and one asserting every `_emit_forecast_freshness_record` call site has a
paired coverage emit — so a sixth freshness site added later cannot silently skip coverage. A test
that only asserts "a row was written" locks nothing; each test must distinguish its case from the
adjacent one.

### T2 — carry the reason (SECOND HALF, cut this first if the plan must shrink)

T1 says *that* the product is dark. It does not say *why*, and the four `continue` branches in
`combine_ensembles_pooled` are distinguishable only from WARNING logs on stdout — which every
redeploy destroys, and which is precisely why this diagnosis took as long as it did.

Have `build_combined_forecasts` report, per station/parameter, which gate dropped it
(`pooled_insufficient_contributors`, `pooled_empty_intersection`,
`pooled_single_timestamp_not_persisted`, `pooled_non_uniform_spacing_not_persisted` —
`services/forecast_combination.py:86-134`, `:355-376`), and aggregate the counts into T1's `detail`
as `{"drop_reasons": {"<reason>": <count>}}`.

⚠️ This changes a return type on the combination path. It must not change **which rows are written**
— that is Plan 222's contract and out of scope here. If the review finds the return-type change
cannot be made without touching write behaviour, **drop T2** and record why; T1 alone closes the
blindness this plan is about.

**Verification:** a test asserting that a station whose contributors have an empty `valid_time`
intersection is reported under `pooled_empty_intersection` and not merely as "not written", plus the
existing Plan 222 combination tests still passing unchanged (proving write behaviour did not move).

## Non-goals

- Fixing the dark product. That is **Plan 226**, which is itself sequenced behind Plans 252/254.
- Making the combiner write more rows, or restoring the pre-Plan-222 union.
- A per-product coverage ledger for every `model_id`.
- New alert transport. Alerts remain webhook-only.

## Exit gates

- A `forecast_combination_coverage` row is written by every scheduled forecast cycle, and by no
  backfill/replay run.
- On the current staging state the check reports the dark product with `stations_eligible: 34` and
  `stations_written: 0` — i.e. it reproduces, automatically, the finding that took a manual
  investigation on 2026-09-08.
- D3's paging decision is recorded in the plan with its revisit trigger, whichever way it goes.
- Full test suite passes after the final code change.
