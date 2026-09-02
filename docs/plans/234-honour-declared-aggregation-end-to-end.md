---
status: DRAFT
created: 2026-09-02
plan: 234
title: SAP3 does not honour the aggregation and lookback the ForecastInterface says it owes
scope: Thread each channel's declared aggregation method through every assembly path, and deliver exactly the declared lookback at the model boundary. Split out of Plan 228's final review round. No FI change — the contract already specifies this.
depends_on: [228]
blocks: []
source: 2026-09-02 — Plan 228's implement review, findings deferred by owner decision to keep 228 shippable
---

# Plan 234 — honouring what the contract already says

## Status

**DRAFT — not reviewed.** Split out of Plan 228 on 2026-09-02 so the skill-score corruption fix
could ship. The findings below are from an independent review of Plan 228's diff and were verified
before being carried across; they are not speculative.

## ⛔ This is OUR defect, not an FI gap. Do not file an FI issue.

Checked against the pinned `forecastinterface` v0.1.19 docs, which are explicit
(`docs/input_requirement.md:105,164,170`):

> *"When a model declares a variable at a resolution coarser than the delivered data, **SAP3
> aggregates** with `SUM`, `MEAN` or `MAX`… Default follows the per-parameter convention
> (precipitation / reference_et = `SUM`; temperature, discharge, SWE and other state variables =
> `MEAN`); override via the optional `aggregation` property only for a non-default rule."*

and describes the parameter/unit/aggregation triple as *"a coordination contract with SAP3"*.

The contract defines the method, the default, and whose job it is. Nothing here is inexpressible,
so `CLAUDE.md` § ForecastInterface Adherence puts this on path 1 — **our code violates the FI, so
our code is fixed.** Filing an FI issue would be reporting our own omission as their bug.

## What is wrong

### A1 — declared aggregation is bypassed or flattened on every path

Skill scoring, operational NWP resampling, hindcast forcing, per-channel model declarations, and
superset conflict detection all discard or flatten the declared method
(`services/skill/service.py:524`, `services/operational_inputs.py:165`, `services/hindcast.py:273`,
`types/model.py:285`, `services/operational_inputs.py:452`).

A model declaring `MAX` on a peak channel — the FI docs' own example, "a window-max discharge a
flood threshold is set on" — silently receives a mean. For a flood threshold that is the difference
between crossing and not crossing.

Aggregation must be retained keyed by channel (time_step, temporality, product, variable) and
threaded through training, hindcast, both operational assemblers and scoring. When one assembly
serves several models, **effective** methods must be compared — including defaults, so an explicit
declaration and an identical default do not read as a conflict, and two genuinely different methods
are not silently merged.

### A2 — the declared lookback is validated but not delivered

Plan 228 D4 requires exactly N complete buckets at the model boundary. The hindcast path validates
a trimmed window and then delivers the whole frame (`services/hindcast.py:248,278`:
`validation_window = obs_df.tail(N)` is used only for the cadence check, while
`past_targets=obs_df`). The FI adapter's past-series conversion never slices by
`PastKnownVariable.lookback`. Short-lookback cases only warn.

Today's models self-slice (`tail(7)`), so Plan 228's reported defect is genuinely fixed — but the
invariant is not enforced where the contract places it, and a model that consumes its whole frame
would get whatever it was handed.

### A3 — hindcasts are attributed to the wrong artifact when the run id is omitted

`flows/compute_skills.py:104,150` and `services/onboarding.py:109,156` fetch unfiltered histories,
so old-artifact predictions can be scored and published under a new artifact, and rerun duplicates
double-weighted. `HindcastStore` has no `model_artifact_id` filter
(`store/hindcast_store.py:138`).

## Non-goals

- Any change to the ForecastInterface package. See the section above.
- Re-opening Plan 228's D1-D4 or its shipped fix.
- The skill natural-key and phase-validation defects — those stayed in Plan 228 because that branch
  introduced them.

## Exit gates

To be written when this plan is reviewed. Two are fixed regardless:

- A locking test per finding, each demonstrated failing before the fix. A1's must use `MAX` against
  data where mean and max differ, and must cover an explicit-vs-default co-assignment.
- A2's test must assert the **total delivered length**, not `.tail(N)` of it — the distinction that
  let this pass unnoticed in Plan 228.
