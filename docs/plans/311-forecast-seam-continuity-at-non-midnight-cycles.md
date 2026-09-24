---
status: DRAFT
created: 2026-09-21
revised: 2026-09-24
plan: 311
title: Past and future inputs are contiguous only at the midnight cycle — 3 of 4 scheduled cycles leave a one-day hole
scope: Decide whether the issue day must appear in a daily model's inputs at a non-midnight cycle, and if so, close the gap between `past_targets`/`past_dynamic` (which end before the issue day) and `future_dynamic` (which begins after it). Explicitly NOT changing either window's own rule — both are individually correct — NOT the aggregation fix (that is the Plan 262 T3b follow-up), NOT the forecast schedule itself, NOT sub-daily models, NOT the NWP ingest or archive.
depends_on: []
blocks: [262]
open_decisions: [D1, D2]  # a closure is drafted at the foot of this plan; reviewed 2026-09-24 (4 majors, folded) and awaits the owner. D1 now has an ENFORCEMENT half the first draft omitted.
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

---

## ⚖️ Decision closure — DRAFTED 2026-09-24, **awaiting the owner's confirmation**

⛔ **Not yet the owner's words.** This is the closure T1's measurement earns, written out so the
owner can confirm or correct it in one reading. Until they do, `open_decisions` stays as it is and
`blocks: [262]` stands. ⚠️ *The orchestrator may set a plan READY; it may not close an owner
decision. This section is a proposal, not an authority.*

### D1 — proposed: **(a) now, and (d) is what T1 actually established.**

T1 answered the question the options were written before: the model reconciles past and future on a
**date key** and takes its output timestamps from the real date index, so the D+1→D shift this plan
was opened to rule out does not occur. That is option (d)'s precondition — *"only valid if T1 shows
the model is timestamp-aware"* — and it holds.

⇒ **The labels are safe at every cycle.** But (d)'s own wording, *"merely a shorter horizon"*, is
**withdrawn by T1's second finding**: `cmal_small` is `daily_only`, `_regrid_basin` never runs, so
the daily tensor slice is positional and a missing date leaves the neighbouring rows physically
adjacent. The recurrence integrates a sequence with one day elided while the dates beside it stay
true. Nothing is mislabelled; the dynamics are compressed at the seam.

⇒ **Therefore (a) for the pilot — not as a dodge, as the only option with zero exposure.** The
00:00Z cycle is already seam-continuous, so the first real forecast meets none of this, and the
residual can then be judged against real output rather than against source.

**What this obliges, concretely:**
- **Plan 262 T5 must name the 00:00Z cycle** and say why. Its In today says only *"the first cycle
  following T3b's gated live activation"*, which at three cycles in four is the exposed case. ⇒ a
  one-line amendment to 262, carried with this closure.
- ⚠️ **The restriction must be OPERATIONAL, not merely written.** ⛔ *An instruction to observe the
  midnight cycle does not stop the other three running.* If the pilot's assignment is live, the
  other cycles will fire. Whether that is acceptable — they are label-safe, only dynamics-compressed
  — or whether the assignment must be gated to one cycle is **the part of D1 the owner must settle**,
  because it is the difference between a note and a code change.

### D2 — proposed: **scoped to the Swiss pilot; Nepal does not inherit it.**

The elision arises from a **daily** model issued off midnight. Nepal's forcing is hourly, which is
precisely what lets any issue phase be honoured there (recorded under the modeller's confirmed
conventions: daily = local per basin, sub-daily = UTC). A seam fix shaped for a Swiss daily model
would therefore be designed against a constraint Nepal does not have.

⇒ **Do not anticipate Nepal here.** ⚠️ **But do not read that as "Nepal is fine" either** — nobody
has measured the Nepal seam, and Plans 252/254/258 still hold the target cadence open. The honest
statement is *unexamined*, not *safe*, and this closure records it as such rather than implying
clearance.

### The residual, with a named carrier

*How much does a one-day elision at the seam degrade a 30-day-lookback CMAL?* — a modelling
question, to be answered **with evidence from real pilot output**, not from source.

⚖️ **Proposed carrier: the modeller (Sandro), after 262 T5 produces output.** ⛔ *Named rather than
left to "the modeller" in the abstract, because an unowned residual is how this reappears as a
surprise.* The owner should confirm the carrier — routing work to a collaborator is theirs, not the
orchestrator's.

### What closing this unblocks

`blocks: [262]` exists because *"the pilot should not go live on 3-of-4 cycles while T1 is
unanswered"*. T1 is answered. ⇒ **On the owner's confirmation, `blocks: [262]` lifts** and Plan 262
proceeds: re-register the import flow schema → T4 import → T3b gate and assign → T5 on the 00:00Z
cycle.

⛔ **This plan's T2 does not run.** T2 was conditional — *"only runs if T1 says it matters"* — and
T1 says the correctness question it was written for does not exist. Closing D1 on (a) leaves T2
unexecuted by design, not skipped.

---

## 🔴 Independent review of the closure, 2026-09-24 — **PROBLEMS: 4 major.** Folded below.

The closure above was reviewed before being put to the owner. It came back with four majors, all
verified against source before folding. ⭐ **Two of them change the substance, not the wording** —
the closure was written from a reassuring half of T1 and from a stale picture of Plan 262.

### M1 — the model REFUSES; it does not silently compress. **The closure's characterisation was wrong.**

The closure said the seam leaves output *"label-safe, only dynamics-compressed"*. That overlooks an
earlier gate. Verified at the pinned revision:

- `operational/datasource.py::_block_problems` requires **every** consecutive gap to equal the
  declared step — `delivered_diffs != [declared]` is a problem (`:538-545`). A seam gap delivers
  `{1 day, 2 days}` and fails it.
- `_cadence_problem` runs at `datasource.py:100` — **before any flattening or tensor construction**.
- The result is `ModelFailure(cause=INPUT_DATA)` (`operational/model.py:637-643`).

⇒ **The positional compression T1 identified describes what an admitted gappy tensor WOULD do. It is
not reachable for this feed**, because the gappy tensor is never built. ⭐ This is *better* news than
the closure claimed — a loud typed refusal, not a quiet degradation — but it is a different
operational fact: **a non-midnight cycle produces NOTHING, not a worse forecast.** ⛔ *Do not carry
the phrase "merely dynamics-compressed" forward; it names a behaviour the gate prevents.*

### M2 — option (a) requires an ENFORCED restriction, and T2 is deferred, not dismissed.

The closure correctly noted that observing the midnight cycle does not stop the other three firing,
then left enforcement undecided **while lifting the blocker anyway**. That is the waved-through
half. Owner risk-acceptance is legitimate; it does not substitute for the technical judgement:

⇒ **(a) is only (a) if the restriction is enforced.** An unenforced (a) is "run on all four cycles
and look at one", which under M1 means three cycles a day returning `INPUT_DATA` failures.

⇒ **T2 is DEFERRED under that restriction — ⛔ not "does not run by design".** The closure's
reasoning (correct labels ⟹ the seam does not matter) does not survive M1. ⚠️ And note the trap:
**midnight-only output can never measure the effect of a seam it never encounters**, so the residual
question cannot be answered from pilot output alone while (a) holds. The closure implied it could.

### M3 — "3 of 4 cycles" is NOMINAL. Even the midnight cycle is not automatically continuous.

`_resolve_cycle_time` (`flows/run_forecast_cycle.py:698-704`) returns **`clock()`** when no explicit
cycle time is supplied. A cron-scheduled midnight run therefore issues at, say, `00:00:37Z` — and
under the future window's `valid_time >= issue_time` rule the `00:00Z` bucket is **dropped**.

⇒ **Option (a) requires an explicitly PINNED issue time, not a schedule.** ⭐ Plan 262's own
execution record already learned this class of error on 2026-09-22 — *"a live-input check MUST
resolve the real cycle via `fetch_latest_cycle_time`, never wall clock"* — and its T5 run did pin
`cycle_time` to `00:00Z` for exactly this reason. The closure did not carry that forward.

### M4 — the "remaining path" in the closure is STALE. Most of it is already done.

The closure proposed *re-register schema → T4 import → T3b gate and assign → T5*. Plan 262's
execution record says otherwise, and the code confirms it:

| step | actual state |
|---|---|
| T4 import | **DONE** — the artifact is an ACTIVE row |
| T3b live-input gate | **RERUN 2026-09-22 and PASSES** — the ~142× inflation is gone; `discharge → mean` verified inside the running worker |
| T3b assignment | **DONE 2026-09-22** — `create_group_assignment`, audited, `priority=50` |
| T5 | **RAN and FAILED** at the model: `station_code_resolver required for GROUP input conversion` |
| the resolver | **FIXED AND DEPLOYED** — Plan 312 (#296) at v0.1.957; staging runs v0.1.965. Present at `flows/run_forecast_cycle.py:2119` |

⇒ **The real remaining path is three steps, not five:**
1. **Close D1 including its enforcement half** (M2) — the only genuinely open question.
2. **Re-run 262 T5** on an **explicitly pinned** `00:00Z` issue time (M3), with the resolver fix
   confirmed live on the host rather than inferred from the version number.
3. **262 T6** — record what the pilot proved and what it did not.

⚠️ **Plan 312 still reads `status: READY` with `blocks: [262]` although it is merged and deployed.**
That is a stale machine field on a live gate — it should be reconciled with 262's, not left to be
rediscovered.

### What this does to the proposed closure

- **D1's recommendation stands — (a) — but for a corrected reason** (M1: the alternative is three
  daily typed refusals, not degraded forecasts) and **with an added obligation**: the restriction
  must be enforced and the issue time pinned (M2, M3).
- **D2 is unchanged.** The review called its "Nepal unexamined" caveat appropriately cautious.
- **The residual's carrier stands**, but ⚠️ **it cannot be answered from pilot output while (a)
  holds** (M2) — whoever carries it needs a different evidence route, and that should be said when
  it is handed over.
- ⛔ **`blocks: [262]` does NOT lift on this text alone.** It lifts when D1 is closed *including*
  enforcement. The closure's claim that confirmation alone unblocks 262 was premature.
