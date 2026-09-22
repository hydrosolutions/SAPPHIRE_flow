---
status: DRAFT
created: 2026-09-21
plan: 311
title: Past and future inputs are contiguous only at the midnight cycle — 3 of 4 scheduled cycles leave a one-day hole
scope: Decide whether the issue day must appear in a daily model's inputs at a non-midnight cycle, and if so, close the gap between `past_targets`/`past_dynamic` (which end before the issue day) and `future_dynamic` (which begins after it). Explicitly NOT changing either window's own rule — both are individually correct — NOT the aggregation fix (that is the Plan 262 T3b follow-up), NOT the forecast schedule itself, NOT sub-daily models, NOT the NWP ingest or archive.
depends_on: []
blocks: [262]
open_decisions: [D1, D2]  # D1 narrowed by T1, see the T1 record
source: 2026-09-21 — observed in the Plan 262 T3b live-input gate on the mac-mini at v0.1.944, then reproduced locally by computing the seam with the real functions (`aligned_lookback_bounds` and the future path's `valid_time >= issue_time` rule) at each scheduled cycle. Line anchors verified against `main` at `62ac7b9c`.
---

# Plan 311 — the seam nobody owns

## Status

**DRAFT, filed 2026-09-21 at the owner's request. Not reviewed.** Filed so the finding does not
survive only as a paragraph inside Plan 262's gate record.

⚖️ **Plan number 311 is claimed, not granted** — unused in `docs/plans/` and `docs/plans/archive/`,
and nothing in `docs/` refers to a "Plan 311".

## What was measured

The Plan 262 T3b gate assembled real operational inputs for the Swiss pilot and found
**`2026-09-21` present in neither array**: `past_targets`/`past_dynamic` ended `09-20`,
`future_dynamic` began `09-22`.

Reproduced locally with the production functions, at each cycle the default schedule fires:

| cycle (UTC) | last PAST day | first FUTURE day | seam |
|---|---|---|---|
| **00:00** | 2026-09-20 | 2026-09-21 | ✅ **continuous** |
| 06:00 | 2026-09-20 | 2026-09-22 | 🔴 **1-day hole** |
| 12:00 | 2026-09-20 | 2026-09-22 | 🔴 **1-day hole** |
| 18:00 | 2026-09-20 | 2026-09-22 | 🔴 **1-day hole** |
| *17:13 (the gate's own ad-hoc run)* | 2026-09-20 | 2026-09-22 | reproduces the observed frames |

`SCHEDULE_FORECAST_CYCLE` defaults to `0 */6 * * *` (`docker-compose.yml:417`), so **three of every
four cycles carry the hole**, and `_resolve_cycle_time` uses wall-clock `clock()` when no explicit
cycle time is passed (`flows/run_forecast_cycle.py:698-704`) — so this is production behaviour, not
an artifact of the gate's harness.

## Why it happens — both halves are correct in isolation

| | |
|---|---|
| **past** | `aligned_lookback_bounds` ends the window at `floor_to_time_step(issue_time)`, **exclusive** (`services/training_data.py:281-283`). The issue day is incomplete, so excluding it is right. |
| **future** | `_filter_and_cap_daily_records` keeps buckets with `valid_time >= issue_time` and therefore drops the issue-day bucket at a non-midnight cycle, because it "mixes already-elapsed hours with future ones" (`services/operational_inputs.py:232-245`). Also right, on its own terms. |
| **the seam** | that same docstring protects continuity **only** for the midnight-exact case, citing *"Plan 129's 'no gap' seam-continuity claim"*, and says nothing about the other three. |

🔑 **Nothing is broken; nothing owns the join.** Plan 134 discusses the backdating discipline for
each window separately (`134:149-162`) and likewise does not address what lies between them.

## 🔴 What is NOT established

⛔ **Whether this harms the forecast.** It depends on whether the model consumes the timestamps or
assumes `past_dynamic` and `future_dynamic` are contiguous. If it assumes contiguity, a forecast
labelled D+1…D+5 would be read as D…D+4 — a one-day shift in every prediction. The `aquacast`
package is behind an optional extra and is not installed in a plain dev environment, so this was
**not** verified.

⚠️ **Quote the two halves together.** "Three of four cycles are wrong" is the memorable half and
the impact is unverified; carrying the first without the second would turn a measured gap into an
unmeasured incident.

## Tasks

### T1 — establish whether the gap matters, before designing anything

**Outcome.** A recorded answer to: does a daily aquacast model align `past_dynamic` and
`future_dynamic` by **timestamp**, or by **position**?

**In.** The model's own input handling, read in an environment with the `aquacast` extra installed,
or a written answer from the modeller. Whichever is cheaper.

**Out.** ⛔ **No code change.** ⛔ Do not design a fix before this answers — a positional model and
a timestamp-aware model need different fixes, and one of them needs none.

**Verification.** The answer cites the model's own code or the modeller, not inference from
behaviour. ⚠️ A forecast that "looks plausible" does not distinguish the two cases.

### T2 — close the seam, if T1 says it matters

**Outcome.** The issue day appears in exactly one of the two arrays, on every cycle.

**Gated on D1.** The shape is D1's question, not this task's.

**Out.** ⛔ Neither window's own rule may be loosened to achieve this: the past window must not
include an incomplete day, and the future window must not include a bucket contaminated with
elapsed hours. A fix that relaxes either has traded a visible gap for an invisible wrong value.
⛔ No change to sub-daily models, where the issue "day" is not a step.

**Verification.** The seam table above, recomputed, shows **continuous** at all four cycles — and a
non-midnight regression test asserts it, since the midnight case already passes today and would
hide a fix that only works there.

## Owner decisions

### D1 — where does the issue day belong at a non-midnight cycle?

| option | shape | cost |
|---|---|---|
| **(a)** restrict the pilot to the 00:00Z cycle | no code at all; the midnight cycle is already continuous | forecasts once a day instead of four times; sidesteps rather than answers |
| **(b)** let the future array start at the issue day, using NWP for the whole day | contiguous, and the NWP covers the elapsed hours too | the elapsed part of the day is a forecast where an observation exists — the contamination the current rule exists to avoid |
| **(c)** let the past array include the issue day, filled from observations so far | contiguous, and uses real data | a partial day aggregated as if whole: a daily mean of 6 hours is not a daily mean, and precipitation summed over 6 hours understates |
| **(d)** accept the gap and make the model's timestamps authoritative | no data change | only valid if T1 shows the model is timestamp-aware; then the "gap" is merely a shorter horizon |

⚠️ **(b) and (c) both reintroduce, on the other side, exactly the contamination each window's rule
was written to prevent.** That is why this is a decision and not an implementation detail.

Recommendation: **(a) now, (d) if T1 permits.** (a) is free and unblocks the Swiss pilot today;
(d) is the honest end state if the model reads timestamps.

### D2 — is this a v0 Swiss question or a v1 Nepal question?

Nepal's forcing is hourly and the target cadence is under active decision (Plans 252/254/258), so a
daily-seam fix designed for Switzerland may not transfer. ⚠️ Decide whether this plan is scoped to
the Swiss pilot or must anticipate Nepal **before** T2 designs anything.

## Watch items

- 🪤 **Both halves are individually correct and individually defended in comments.** A reviewer
  reading either one alone will conclude there is no bug. The defect is only visible at the join.
- 🪤 **The midnight cycle already passes**, so any regression test written only against 00:00Z is
  green before and after a fix. Non-midnight is the case that must be asserted.
- **This blocks nothing structurally** — Plan 262's pilot can proceed on option (a) — but it is
  recorded as `blocks: [262]` because the pilot should not go live on 3-of-4 cycles while T1 is
  unanswered.

## Exit gates

- T1's answer is recorded with its source (model code or modeller), not inferred from behaviour.
- D1 and D2 are closed or explicitly carried, with the carrier named.
- If T2 runs: the seam is continuous at all four cycles, proven by the recomputed table **and** a
  non-midnight regression test, with neither window's own rule loosened.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"], "produces_decision": "D1",
      "note": "establish whether the gap matters; a positional and a timestamp-aware model need different answers, and one needs none" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "decision": "D1 and D2 CLOSED",
      "note": "only runs if T1 says it matters" }
  ]
}
```

## Changelog

**2026-09-21 — created** at the owner's request, from the Plan 262 T3b gate record. Not reviewed.


## T1 — ANSWERED 2026-09-22: **timestamp-aligned.** The blocker as posed does not exist.

Answered inside the staging worker, where the `aquacast` extra is installed. Two load-bearing
claims independently re-verified against the container's own source rather than taken on report:

| claim | evidence |
|---|---|
| past and future are reconciled on a **date key**, not by position | `operational/datasource.py::_merge_dynamic_inputs` — `pl.concat(frames, how="vertical").unique(subset="date", keep="first", maintain_order=True)`, then variables joined `on="date"` |
| output `valid_time`s come from the real date index, never synthesised as `issue + t·step` | `inference/records.py:89-97` `target_dts.append(dates[target_idx])`, over a `DateIndex` built from the actual `date` column |
| the shim does not drop timestamps | `_shim.py:277` renames/scales only `c != "datetime"`; `_TEMPORAL_COLUMNS` is excluded outbound too |

⟹ **a missing 2026-09-22 yields no row for that date. It is never back-filled by the next future
value, and it cannot relabel a step.** The D+1→D shift this plan was opened to rule out **does not
happen**, and ⛔ **no downstream re-attachment is needed** — correctly, none is done.

There is also a fail-closed backstop: `operational/model.py::_filter_issues` matches
`forecast_issue_dt` by calendar date, so a mis-seated window returns
`ModelFailure(cause=INPUT_DATA)` rather than a quiet shift.

### 🔴 But it is not "merely a shorter horizon" either — a narrower defect, newly identified

`cmal_small` is daily-only, so `mode = "daily_only"`, and **`_regrid_basin` runs ONLY under
`synchronized_multi_res`** (verified: both call sites, `container.py:865` and `:978`, sit under
`if mode == "synchronized_multi_res"`). That function is what turns a date gap into an all-null row
— its own docstring says it is *"what makes the container's positional pairing true rather than
lucky"*. It does not run here.

⟹ the daily tensor slice **is** positional (`data/dataset.py:90`), so the rows for `09-21` and
`09-23` end up **physically adjacent in the recurrence**: the CMAL sees a forcing sequence with one
day **elided**, while the dates carried alongside stay true. The labels are right; the dynamics the
recurrence integrates are compressed by one day at the seam. ⚠️ `_feed_horizon`
(`model.py:325`) counts `(daily_end - issue).days + 1` from calendar dates, so the relaxed horizon
counts the missing day while no row exists to fill it.

### What this does to D1

- ⛔ **The stated blocker is withdrawn**: no forecast is mislabelled, so the pilot is not unsafe in
  the way this plan feared.
- ✅ **Option (a) — run the pilot on the 00:00Z cycle — is now clearly the right FIRST move**, not a
  dodge: that cycle is already seam-continuous, so the first real forecast carries **zero** exposure
  to the elision, and the remaining question can be answered against real output instead of source.
- The residual question is narrower and no longer blocking: *how much does a one-day elision at the
  seam degrade a 30-day-lookback CMAL?* That is a modelling question for the modeller, with
  evidence, not a correctness question about SAP3.
