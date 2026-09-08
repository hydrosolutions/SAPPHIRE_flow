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

The cause is Plan 222's union→intersection change reaching the host on 2026-09-04 (it shipped in
`v0.1.869`; `git merge-base --is-ancestor 928b3093 <0.1.869 bump>` → true); the three-way
`valid_time` intersection is empty because `linear_regression_daily` sits on issue-time phase and
the NWP pair sit on midnight. **That behaviour is correct and stays** (Plan 222 §D7: "Absence is the
accepted, honest outcome"). Plan 226 is the fix that refills the product. This plan only makes the
absence *visible*.

### ⭐ Confirmed from live logs — and there are THREE gates, not one

`docker logs sapphire_flow-prefect-worker-1`, cycle 07:49Z 2026-09-08, current code:

```
forecast_cycle.combined_forecast_skipped:      forecast_combination.pooled_empty_intersection:
    5  n_models=0                                 34  (contributor_count=3, parameter=discharge)
  101  n_models=1
   34  n_models=3
```

| gate | stations | where it is visible |
|---|---|---|
| empty `valid_time` intersection | **34** | `pooled_empty_intersection`, **service** level |
| `<2` contributors | **101** | `combined_forecast_skipped n_models=1`, **flow** level only |
| no contributors | **5** | flow level only |

The 34 matches the database measurement exactly. The 101 arise because
`linear_regression_daily`/`nwp_regression` fail an off-by-one antecedent check
(`Insufficient lookback: need 7 rows, got 6`).

⚠️ **The 101 are not a third reason the writes stopped.** Those stations were never pooling — only
2-3 stations pooled at all before 09-04. **Lookback explains the denominator; the guard explains the
stoppage.** Different defects, different fixes, and this plan monitors rather than fixes either.

🪤 **`pooled_insufficient_contributors` never fires for the 101**, because
`build_combined_forecasts` returns at `:397` before `combine_ensembles_pooled` is entered. They
are **flow-visible, service-silent**. That event is not dead code, though:
`services/skill/combined_skill.py:111` calls the combiner without the outer gate, and the
per-parameter `eligible` filter can drop below the floor even when three contributors arrive.

⛔ **This kills a dead end that has now been re-derived twice**: "no `forecast_combination.*` events
appear, so combination is not running" is FALSE — they fire **34× per cycle**. Both sessions that
concluded otherwise were reading logs a redeploy had already destroyed.

### The real argument for this plan

Not "a product went dark" — that is the symptom. It is that **every piece of evidence above lives in
container logs that a redeploy destroys, while the database retains only the *absence* of rows,
which is identical across all three gates.** That is precisely why the diagnosis was re-derived
twice, and why a durable per-cycle record is worth more than the outage itself.

⚠️ Consequence to face squarely: **the check this plan adds will be non-OK from the moment it ships,
and will stay non-OK until Plan 226 lands.** That is the correct reading of reality. D2 decides what
status that is and whether it pages, because a check that is born red and stays red for weeks trains
operators to ignore it — which is the same failure this plan exists to fix, one level up.

## Decisions

- **D1 — record the `n_models` DISTRIBUTION, not an eligible/written pair.** ⭐ This supersedes the
  original recommendation, which was wrong. Count stations by
  `len(multi_result.combinable_results)` — the same property `build_combined_forecasts` gates on
  (`services/forecast_combination.py:397`), read off `MultiModelForecastResult.combinable_results`
  (`services/run_station_forecast.py:127-131`) — and record the histogram alongside how many got a
  `_pooled` row.

  **Both simpler designs have a blind spot, in opposite directions**, which is why the histogram is
  the only correct shape:
  - Defining eligible as `>= 2` and reporting `eligible / written` makes the **101 invisible** —
    they are simply "not eligible" and never appear.
  - Counting `pooled_empty_intersection` events reports **34 and misses the 101** entirely.

  A distribution keeps both populations visible and distinguishable, costs one field, needs no new
  plumbing, and — the property that matters most — **it does not require this plan to know WHY a
  station has one model.** That keeps it a coverage plan and stops it drifting into model health,
  which the Proportionality section forbids. Rejected alternative: counting from `model_assignments`,
  which would call 148 stations eligible and never agree with what actually ran.
- **D2 — what status, while Plan 226 is open?** Two candidates, and the plan must pick one before
  T1 is written:
  - **(a) Honest and loud.** `critical` when eligible > 0 and written == 0; `warning` when
    0 < written < eligible; `ok` when written == eligible. Born red. Recommended for the *record*;
    see D3 for whether it pages.

    🔴 **`written` is NOT `servable`, and after Plan 253 the difference is real.** 253 shipped
    2026-09-08: a combined forecast that FAILS QC is deliberately **stored** marked `QC_FAILED`
    (OD-1) and **excluded from the Forecast Lab** (OD-1a, `services/forecast_lab/db_sources.py`).
    So every eligible station could have a written row, this check could report `ok`, and the
    product could still be unavailable — which is the exact failure this plan exists to catch.
    Either count only rows that are servable, or state plainly that this check covers persistence
    only and name what covers availability. Do not let "written == eligible" stand unqualified.

    ⚠️ **T2's drop-reason list is also missing one that 253 introduced:**
    `forecast_combination.no_qc_rules_for_step_not_persisted` — a combination for which no QC rule
    exists is now deliberately not persisted at all. That is a third way to be absent, distinct from
    the two this plan enumerates.
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
  (`flows/run_forecast_cycle.py:1764, 2387, 2677, 3525, 3625`): normal completion, no-operational-
  stations early return, NWP-fetch abort, the group-store fatal, and the fatal-exit helper — by
  design, so "a dark cycle never silences its own heartbeat" (`emit_freshness_on_fatal_exit`,
  `:1743`). A coverage check that emits only at `:3625` goes silent on exactly the cycles that most
  need explaining.

  ⚠️ **Not all four non-completion sites are alike, and one blanket rule is wrong** (independent
  review, 2026-09-08):
  - **No operational stations** (`:2387`) is NOT an aborted cycle — it returns normally with
    `ForecastCycleHealth.HEALTHY`. Marking it `cycle_completed=false` would be simply false; it is a
    completed cycle with nothing to do, and belongs with the `PRIMARY` case below.
  - **The group-store fatal** (`:3525`) occurs AFTER the whole station-combination loop, so its
    counts are real and measured. Capping it at `warning` would downgrade a genuine zero-of-34
    outage. It should carry its counts and its true status.
  - Only the **NWP-fetch abort** (`:2677`) and the **fatal-exit helper** (`:1764`) are true
    "no station work ran" cases where the counts are meaningless.
  The `warning` floor therefore applies to those last two only. But an aborted cycle has **no meaningful eligibility count**
  — no station work ran — and reporting an all-zero histogram would read as "no pool was
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
(`flows/run_forecast_cycle.py:720-787`; its five call sites at `:1764, 2387, 2677, 3525, 3625`),
following that function's existing conventions exactly:

- **Skip records for explicit-`cycle_time` runs** (backfill/replay) for the reason already documented
  there: `fetch_recent` orders by `checked_at`, so a backfill written "now" would become the latest
  record and could mask or fake a live state.
- `subject="forecast_combination"`, `cycle_time=resolved_cycle_time`.
- `detail`: `{"strategy": <configured strategy value>, "cycle_completed": bool,
  "stations_by_n_models": {"0": int, "1": int, "2": int, "3+": int}, "stations_written": int}`
  (D1). `stations_eligible` is derivable as the sum of the `>= 2` buckets and is NOT stored
  separately — one number that can disagree with the histogram is worse than none.
- Called from all five sites that already emit `FORECAST_FRESHNESS`, with `cycle_completed=False`
  at the **two** true no-work sites only (`:2677`, `:1764`) — see D5 for why the other two are not
  aborts.
- **Status precedence, evaluated in this order** — the rules are not independent and an earlier
  revision left `PRIMARY` + `cycle_completed=false` ambiguous (independent review, 2026-09-08):
  1. `PRIMARY` configured → **`ok`**, all-zero histogram, whatever else is true. No combined
     product is expected, so no other rule can make it red. Do not skip the record entirely; a
     missing row is indistinguishable from a dead flow.
  2. `cycle_completed=false` → floor at **`warning`** (never `critical`): the counts are meaningless
     because no station work ran, and the cycle's own abort is already `critical` on
     `forecast_freshness`.
  3. Otherwise → status per D2 on the measured counts.
- 🪤 Do NOT "fix" the `emit_freshness_on_fatal_exit` docstring's "all four" (`:1743`). An earlier
  revision of this plan called it stale at five; **it is correct** — that helper is itself the fifth
  site, and "all four" names the four that predate it.

**Verification:** unit tests over the status mapping at each boundary (eligible=0; eligible>0 and
written=0; partial; full; PRIMARY; and `cycle_completed=False` with eligible>0 asserting it does
**not** reach `critical`), plus one test asserting the record is **not** written for an
explicit-`cycle_time` run, and one asserting every `_emit_forecast_freshness_record` call site has a
paired coverage emit — so a sixth freshness site added later cannot silently skip coverage. A test
that only asserts "a row was written" locks nothing; each test must distinguish its case from the
adjacent one.

### T2 — carry the reason (SECOND HALF, cut this first if the plan must shrink)

T1's histogram says a station had 3 contributors and no row. It does not name the gate that dropped
it — and the persistence-boundary gates (`pooled_single_timestamp_not_persisted` `:445`, `pooled_non_uniform_spacing_not_persisted` `:461`,
and `no_qc_rules_for_step_not_persisted` `:493` — the last added by PR #264) are
indistinguishable from the intersection gate in the histogram alone.

⭐ **This is much smaller than first scoped.** The original T2 proposed changing
`build_combined_forecasts`' return type to report the gate. That is unnecessary: **the events
already exist and already carry what is needed** — `pooled_empty_intersection` fires per
station/parameter at `:148`, its siblings at `:96`/`:106` and `:445`/`:461`/`:493`, and the flow's own
`combined_forecast_skipped` carries `n_models`. Nothing aggregates them into a record that survives
a redeploy; that is the entire gap.

So T2 is: **count the warnings the combination path already emits within a cycle, and put the tally
in T1's `detail`** as `{"drop_reasons": {"<reason>": <count>}}` — a counter threaded through the
cycle, not a signature change. No return type moves, and **no change whatsoever to which rows are
written** (Plan 222's contract, out of scope).

⚠️ Note the asymmetry T1's histogram already exposes and T2 must not paper over: the `<2` path
emits **no service-level event at all** (`:397` returns before the combiner is entered), so
`drop_reasons` will legitimately not account for the 101. The histogram is what covers them; the two
fields are complementary and neither is sufficient alone.

**Verification:** a test asserting that a station whose contributors have an empty `valid_time`
intersection is tallied under `pooled_empty_intersection` and not merely as "not written"; a test
asserting a `<2`-contributor station appears in the histogram but contributes **no** `drop_reasons`
entry; and the existing Plan 222 combination tests passing unchanged, proving write behaviour did
not move.

## Non-goals

- Fixing the dark product. That is **Plan 254 T8**, which absorbed Plan 226's anchoring scope
  intact on 2026-09-08 (owner decision); Plan 226 is `SUPERSEDED` and is to be read for its
  evidence, not its instructions. This plan reports the outage; it does not repair it.
- Making the combiner write more rows, or restoring the pre-Plan-222 union.
- A per-product coverage ledger for every `model_id`.
- New alert transport. Alerts remain webhook-only.

## Exit gates

- A `forecast_combination_coverage` row is written by every scheduled forecast cycle, and by no
  backfill/replay run.
- On the current staging state the check reproduces, automatically, the finding that took a manual
  investigation on 2026-09-08: `stations_written: 0` against a histogram of
  `{"0": 5, "1": 101, "3+": 34}` — both the 34 and the 101 visible and distinguishable in one row.
- D3's paging decision is recorded in the plan with its revisit trigger, whichever way it goes.
- Full test suite passes after the final code change.
