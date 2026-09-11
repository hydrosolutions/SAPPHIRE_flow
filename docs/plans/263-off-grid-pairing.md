---
status: DRAFT
created: 2026-09-10
plan: 263
title: Two sources share a step and not a phase, and no operation consumes the second one
scope: Specify the ONE operation that lets a source on a different phase feed a model, when the source cannot be sub-divided. Settle the five semantics an independent review found missing: instantaneous values, ties, minimum overlap, the dual-time representation, and what a paired statistic may be called. Explicitly NOT the day boundary or which source wins (Plan 252 OD-15, decided), NOT the resampler's other rules (Plan 254 T3), NOT end-period stamping (Plan 267).
depends_on: [252]
blocks: [254]
source: 2026-09-10 — a Codex review of PR #269 found that Plan 252 OD-15 depends on an operation that does not exist, and that the sketch written to fill the gap was underspecified in five specific ways
---

# Plan 263 — off-grid pairing

## Status

**DRAFT — and deliberately demoted from a decision to a question.** A sketch of this operation was
written into Plan 252 as "OD-16" on 2026-09-10 and reviewed the same hour. The review found it
underspecified in five ways and recommended it *"remain a proposal, not underpin a settled decision or
a task, until those semantics are explicit."* That recommendation was accepted; the sketch is now
Plan 252 **OQ-7** and this plan is where it gets settled.

## The gap, and why it is real

⚖️ **Owner decision 2026-09-10 (Plan 252 OD-15): Switzerland adopts the MeteoSwiss precipitation day,
06:00Z.** That decision is sound and is not re-opened here. It has one consequence nobody costed at
the time:

| source | grid | after the cutover |
|---|---|---|
| `meteoswiss_rhiresd` / `rprelimd` (precipitation) | `(86400 s, 21600 s)` | native — exact |
| `meteoswiss_tabsd` / `tmind` / `tmaxd` (temperature) | `(86400 s, 0)` | **off-grid by 6 h** |

🔴 **Under the settled rules, that temperature series has no legal consumer.** Measured against the
plans as they stand:

- **Resampling it is forbidden.** Same step, so there is nothing to coarsen; re-cutting a daily value
  to a 6-hour-shifted day means splitting it, which Plan 252 **OD-13** forbids outright.
- **Shifting it is forbidden.** Plan 252 forbids moving a timestamp to make a series fit.
- **Refusing it discards temperature entirely** from every Swiss model.

So the operation must exist, or OD-15 cannot be implemented. ⛔ **Until this plan lands, the correct
behaviour is to REFUSE** — Plan 254 T3 states that, and Plan 254 T6 is blocked on this plan so that a
retrain never bakes in an unspecified treatment.

## The sketch, and the five things it does not say

> **Sketch.** Each source value is paired with the target bucket it **most overlaps**. The value is
> used **unchanged**, its timestamp is **unchanged**, and the pairing records the **displacement** and
> the **overlap fraction**. The series is flagged **off-grid** while the mismatch lasts.

For Switzerland that gives overlap 0.75 and displacement −6 h. The example is fine; the contract is
not. **These five are this plan's whole content.**

### D1 — what does "most overlaps" mean for an INSTANTANEOUS value?

A point has zero duration and therefore overlaps every candidate bucket by nothing. The sketch was
written for intervals and applied to both without noticing.

⚠️ **It also collides with an existing rule.** Plan 252 OD-14 already handles off-grid instantaneous
readings by moving the *bucket edge* to the nearest reading. Two mechanisms currently claim the same
case. **Recommend: instantaneous values are OD-14's, and pairing is restricted to INTERVAL-valued
series only.** That is the smaller rule and it removes the collision — but it must be decided, not
assumed.

### D2 — what is the MINIMUM acceptable overlap?

As sketched, 50.1% is as legal as 75%. **Recommend a declared floor, refusing below it** — with the
floor set per deployment rather than in code, and the Swiss case (0.75) comfortably above whatever is
chosen. An operation whose worst legal case is "just over half" is not one to leave open-ended.

### D3 — how are TIES broken?

A value can overlap two buckets equally, which is exactly what happens when phases differ by half a
step. **Recommend the EARLIER bucket, locked by a test**, matching the tie-break Plan 254 T3 already
uses for bucket edges — one rule, not two.

### D4 — the representation, which is self-contradictory as sketched (⭐ the substantive one)

The sketch requires the timestamp to be **unchanged** and the value to sit on the **target grid**. The
frame carries ONE timestamp column. Keep it and the series is not on the target grid; replace it and
the unchanged-timestamp rule is broken. **The sketch cannot be implemented as written.**

**Recommend two time fields**: the value's own support (unchanged, authoritative) and its target-bucket
association (derived, and never mistaken for a measurement). Everything downstream that joins on time
must be told which one it is joining on. ⛔ This is a type change, so it is the part of this plan with
real reach — Plan 254 T3's return type and every consumer of a paired series.

### D5 — what may a paired STATISTIC be called?

The sketch permitted pairing for interval statistics and forbade it for accumulations, on the ground
that a total misattributed across a boundary moves real quantities between days. **That line is
directionally right and insufficient.**

🔴 A mean, minimum or maximum is a property of *the interval it was measured over*. For a 75% overlap,
**an extreme may lie entirely in the 25% that does not overlap.** So a paired maximum is **not the
maximum of the target bucket** and must never be labelled as one.

**Recommend: a paired value is a declared PROXY FEATURE, never the target bucket's statistic**, and it
is named and typed so that no consumer can mistake it. This bears directly on OD-15: the daily **mean**
is the strong case, and `tmind` / `tmaxd` are materially weaker. **Whether the extremes may be paired
at all is an owner decision this plan must put.**

## Non-goals

- The day boundary, and which source wins — Plan 252 OD-15, decided.
- The resampler's other rules — Plan 254 T3.
- End-period stamping — Plan 267.
- Re-deriving the MeteoSwiss products from station data. It would remove the mismatch and is a far
  larger piece of work outside this system; recorded so the option is visible, not proposed.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198-210`).

### T1 — settle D1–D5

**Outcome:** five recorded decisions with rationale, including the owner call on whether daily extremes
may be paired at all. **In:** this document. **Out:** any code.
**Verification:** N/A — decision task. Each decision states the option rejected; D4 names the concrete
representation; D5 states what a paired value may and may not be called.

### T2 — specify the operation as a contract

**Outcome:** an implementable specification — eligibility, tie-break, overlap floor, representation,
naming — that Plan 254 T3 can build without making a design decision. Depends on T1.
**In:** this document, and `docs/conventions.md` alongside Plan 252 T1's grid conventions.
**Out:** implementing it (Plan 254 T3).
**Pre-change:** Plan 252 OQ-7 records a sketch with five undefined semantics; no task can build from it.
**Verification:** N/A — specification task. An implementer can derive the Swiss case (overlap 0.75,
displacement −6 h) from the text alone, and the text answers each of D1–D5 without deferring.

### T3 — hand the contract to its implementer

**Outcome:** Plan 254 T3 gains the pairing rules and its refusal test is replaced by the specified
behaviour; Plan 254 T4 declares which series are paired; Plan 254 T6's block on this plan is lifted.
Depends on T2. **Out:** writing the code.
**Verification:** N/A — handover task. Plan 254 carries the contract, and no plan still describes
pairing as unspecified.

## Exit gates

```bash
uv run python scripts/check_readiness.py docs/plans/263-off-grid-pairing.md
```

1. **Every one of D1–D5 is answered**, none deferred to implementation.
2. **A paired value cannot be mistaken for the target bucket's own statistic** — enforced by naming
   and type, not by documentation.
3. **Instantaneous values are assigned to exactly ONE mechanism**, this plan's or Plan 252 OD-14's.
4. ⛔ **Nothing here permits pairing an accumulation.**
5. **Plan 254 T6 stays blocked until T3 hands over.**

## Dependency graph

```json
{
  "plan": 263,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 2, "depends_on": ["T1"]},
    {"id": "T3", "phase": 3, "depends_on": ["T2"], "note": "unblocks Plan 254 T6"}
  ]
}
```
