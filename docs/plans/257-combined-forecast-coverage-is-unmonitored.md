---
status: DRAFT
created: 2026-09-08
plan: 257
title: A whole forecast product went dark for four days under a green light
scope: ONE pipeline_health check type for combined-forecast coverage, emitted once per forecast cycle at every site that already emits the freshness heartbeat, measured against a DECLARED station list (not the per-cycle contributor count) and counting only API-servable non-QC-failed discharge rows. Records evidence and a ready-made alarm condition; the watchdog probe is a named follow-up, not part of this plan. Explicitly NOT the combiner's behaviour (Plan 222 — correct as-is), NOT the anchoring that would refill the product (Plan 226), NOT a general per-product coverage ledger for every model, NOT new alert transport.
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

- **D1 — DECIDED (owner, 2026-09-08): measure against a DECLARED station list, not against what
  happened to run.** ⭐ This supersedes two earlier recommendations, both wrong.

  The expectation is a **fixed, configured list of stations expected to carry a combined discharge
  forecast**, held in deployment config. Status is derived from that list. The `n_models` histogram
  is retained as **diagnostic detail only** and never drives status.

  🔴 **Why the per-cycle count fails — this is the blocker that forced the change** (independent
  review, 2026-09-08). `combinable_results` holds only *successful* non-fallback results, so the
  denominator evaporates exactly when things break: if every three-model station falls to one model,
  `expected == written == 0` and the check reports **`ok`** — reproducing the precise false-green
  this plan exists to prevent. Worse, a station skipped before a `MultiModelForecastResult` exists
  (no assignments, or input assembly failed — `flows/run_forecast_cycle.py:2791`, `:3090`) cannot
  appear in any bucket at all, so the entire product can vanish upstream while the check stays green.
  A declared list cannot evaporate.

  Two rejected alternatives, and why:
  - **`len(combinable_results) >= 2`** — evaporates, as above. It is also not real per-parameter
    eligibility: the combiner separately drops missing and non-`MEMBERS` ensembles and applies the
    floor *per parameter* (`services/forecast_combination.py:96`, `:106`), so two successful models
    with disjoint parameters would be counted eligible while correctly producing no pool — a false
    critical.
  - **Counting `pooled_empty_intersection` events** — reports the 34 and misses the 101 entirely.

  ⚠️ Cost to accept openly: a configured list must be maintained, and a stale list is its own silent
  failure. T1 mitigates by recording the list's size in `detail` every cycle, so drift is visible in
  the same row rather than hidden in config.
- **D2 — DECIDED: the numerator is what a user can actually be served, not what was written.**
  🔴 Second blocker (independent review, 2026-09-08), and it is **new behaviour that landed after
  this plan was drafted**: under Plan 253's OD-1 a QC-failed combination is deliberately **stored**
  (`services/forecast_combination.py:511`), while the Forecast Lab explicitly **excludes** those rows
  and reports `no_combined_forecast` (`services/forecast_lab/db_sources.py:206`). Counting stored
  rows would therefore report full coverage while the API reports unavailable — again the exact
  blind spot this plan claims to close.

  So a station counts as covered only when it has a combined row that is **persisted, not
  `QC_FAILED`, and for `discharge`** — the parameter the API actually fetches, where
  `build_combined_forecasts` may return rows for several (`services/forecast_combination.py:434`).
  Status: `ok` when covered == declared; `warning` when partially covered; `critical` when
  declared > 0 and covered == 0.

  **Tests must cover the QC-failed and failed-store cases explicitly** — a test that only exercises
  the happy path would not have caught either blocker.
- **D3 — DECIDED: record now, page later, with a trigger that does not depend on a halted plan.**
  Write to `pipeline_health` and add **no** watchdog probe initially. The known-dark state means the
  check is red from day one, and a permanently-firing alert on the shared Slack channel would
  degrade every other alert on it.

  **Trigger to revisit: the first time this check reports `ok` for a full day, add the watchdog
  probe.** Deliberately *not* "when Plan 226 lands" — 226 is itself halted pending the timezone
  consolidation, so tying the trigger to it would leave the deferral open-ended. This trigger fires
  on observed reality instead.

  ⚠️ State the outcome honestly, and do not overclaim: `/api/v1/health` does not inspect
  `pipeline_health` rows and will keep returning `ok` (`api/routes/health.py:27`). Until the probe is
  added, this plan delivers **durable evidence and a ready-made alarm condition — not an alert.**
  The gap named in the title is only fully closed when the probe lands, and that follow-up is owned
  here rather than left implicit.
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

## Phase graph

Single phase, single task. T2 is explicitly not scheduled (owner decision), so there is nothing to
sequence against it.

```json
{"phases": [{"id": "P1", "tasks": ["T1"], "depends_on": []}]}
```

## Tasks

### T1 — the check (the whole of the required work)

**Outcome:** every scheduled forecast cycle writes one durable `pipeline_health` row stating how
many stations were declared to need a combined discharge forecast and how many actually have a
servable one — so the 2026-09-04 stoppage would have been visible in the database on the day it
happened, not found by hand four days later.

**Pre-change evidence (the failure this same evidence exposes):** on the current staging state,
`SELECT check_type, status, max(checked_at) FROM pipeline_health GROUP BY 1,2` returns
`forecast_freshness | ok` while zero `_pooled` rows have been written since 2026-09-04 06:26Z. After
T1, the same query returns a `forecast_combination_coverage` row reporting `stations_covered: 0`.

**In:** `PipelineCheckType`; one emitter beside `_emit_forecast_freshness_record` and its five call
sites; the deployment-config declared-station list; the `detail` payload; the status precedence;
tests; the check-type documentation in `docs/architecture-context.md`.
**Out:** `combine_ensembles_pooled` / `build_combined_forecasts` behaviour of any kind; the watchdog
probe (D3's follow-up); `drop_reasons` (T2, unscheduled); any other `model_id`'s coverage.

Add `FORECAST_COMBINATION_COVERAGE = "forecast_combination_coverage"` to `PipelineCheckType`
(`types/enums.py:193`) and a sibling emitter to `_emit_forecast_freshness_record`
(`flows/run_forecast_cycle.py:720-787`; its five call sites at `:1764, 2387, 2677, 3525, 3625`),
following that function's existing conventions exactly:

- **Skip records for explicit-`cycle_time` runs** (backfill/replay) for the reason already documented
  there: `fetch_recent` orders by `checked_at`, so a backfill written "now" would become the latest
  record and could mask or fake a live state.
- `subject="forecast_combination"`, `cycle_time=resolved_cycle_time`.
- `detail`: `{"strategy": <configured strategy value>, "cycle_completed": bool,
  "stations_declared": int, "stations_covered": int,
  "stations_by_n_models": {"0": int, "1": int, "2": int, "3+": int}}`.
  `stations_declared` is the size of D1's configured list (recorded every cycle so config drift is
  visible in the row, not hidden); `stations_covered` counts stations from that list holding a
  persisted, non-`QC_FAILED`, `discharge` combined row for this cycle (D2). The histogram is
  **diagnostic detail only and never drives status** — it is what keeps the 101 single-model
  stations visible without this plan needing to know why they have one model.
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

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -k coverage` plus the
full unit suite. Each test must distinguish its case from the adjacent one — a test that only
asserts "a row was written" locks nothing. Required cases:

- status mapping at each boundary: declared=0; declared>0 and covered=0; partial; full.
- `PRIMARY` → `ok` even with `cycle_completed=False` (the precedence, which was ambiguous before).
- `cycle_completed=False` with declared>0 → does **not** reach `critical`.
- **a `QC_FAILED` combined row does NOT count as covered** (D2 — the blocker; store it, assert
  `stations_covered` does not increment).
- **a failed store does NOT count as covered** (D2).
- **a cycle in which every station falls to one model reports `critical`, not `ok`** (D1 — proves
  status follows the declared list and not the evaporating per-cycle count).
- the record is **not** written for an explicit-`cycle_time` run.
- every `_emit_forecast_freshness_record` call site has a paired coverage emit, so a sixth site
  added later cannot silently skip coverage.

### T2 — NOT SCHEDULED: revisit "why did it fail" after T1 ships (owner decision, 2026-09-08)

T1 says a declared station is uncovered. It does not name which gate dropped it, and the gates are
not distinguishable from the record alone: the intersection gate (`:148`), the contributor floor
(`:106`), and three persistence-boundary rejections
(`pooled_single_timestamp_not_persisted` `:445`, `pooled_non_uniform_spacing_not_persisted` `:461`,
`no_qc_rules_for_step_not_persisted` `:493` — the last added by PR #264).

⛔ **An earlier revision claimed this was cheap — "count the warnings already emitted". That was
wrong**, and the independent review was right to reject it. The combination service writes to a
module logger and returns only forecasts (`services/forecast_combination.py:57`); the flow receives
no event or diagnostic object (`flows/run_forecast_cycle.py:2932`), and there is no cycle-local log
collector. Aggregating those counts requires a signature or callback change on the shared
combination path, or global logging instrumentation — not a counter threaded through the cycle.

**Owner decision: ship T1 first, then judge from real use whether the missing reasons actually slow
an investigation down.** Do not schedule the instrumentation now. If it is taken up later it needs
its own plan, its own review, and an explicit authorisation to change the combination path's
signature — which this plan's Proportionality section otherwise forbids.

⚠️ Note for whoever picks it up: even a complete `drop_reasons` tally would **not** account for the
101 single-model stations, because that path emits no service-level event at all (`:397` returns
before the combiner is entered). The histogram covers them; the two are complementary and neither
is sufficient alone.

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
  investigation on 2026-09-08: `stations_covered: 0` against a non-zero `stations_declared`, with
  the histogram showing `{"0": 5, "1": 101, "3+": 34}` — both the 34 and the 101 visible in one row.
- A test proves a QC-failed combined row does **not** count as covered, and a test proves a failed
  store does not either (D2). Without these two, neither blocker the review found is locked out.
- A test proves the declared list, not the per-cycle contributor count, drives status — i.e. a cycle
  in which every station falls to one model reports `critical`, not `ok` (D1's blocker).
- Full test suite passes after the final code change.
