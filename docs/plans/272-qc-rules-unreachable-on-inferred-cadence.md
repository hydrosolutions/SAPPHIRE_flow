---
status: DRAFT
created: 2026-09-11
plan: 272
title: Configured QC rules are unreachable when the inferred cadence matches nothing
scope: Diagnose and fix the observation-QC rule-selection path so that a configured rule cannot be silently unreachable because the cadence inferred from a short observation window fails to match its declared time step. NOT new rule kinds, NOT threshold values, NOT per-station overrides (Plan 269), NOT the network dimension (Plan 264) — though this plan and 264 T3 must land in the right order, see § Cross-plan.
blocks: [264, 269]
related: [264, 268, 269]
open_decisions: [D1, D2, D3]
source: 2026-09-11 — found by the round-6 independent reviews of Plan 269 (both gates, independently) and verified directly against the repository. Plan 268 knew the single-row mechanism locally; nobody owned the systemic consequence.
---

# Plan 272 — configured QC rules are unreachable when the inferred cadence matches nothing

## Status

**DRAFT — NOT READY. No independent review has run.** This is a diagnosis plus a proposed fix to
a live operational path, so it needs the ordinary independent Claude and Codex plan reviews
before the owner may set it READY.

Opened at the owner's direction on 2026-09-11, after the round-6 reviews of Plan 269 surfaced it.
**Plan 269 is paused (`BLOCKED`) pending this plan**, because the fix determines whether the path
269 wires can apply a daily ceiling at all.

## Problem

Observation QC selects rules by a cadence **inferred from the observations in front of it**, and
matches that cadence for **exact equality** against each rule's declared time step. On the
scheduled ingest path the observation window is fixed and short. The three facts together make
configured rules unreachable, silently.

Measured at `3889c816`:

- **The window is three hours and is never widened.** `_run_qc_task` fetches
  `now - context_window_hours … now + 1h` (`flows/ingest_observations.py:277-280`) with
  `context_window_hours: float = 2.0` at both the task (`:277`) and the flow (`:528`); the Prefect
  deployment passes no parameters (`cli/register_deployments.py:92-99`), and no caller anywhere in
  `src/` or `scripts/` overrides it.
- **Fewer than two rows infers one hour.** `_infer_time_step` returns `timedelta(hours=1)` for a
  group of fewer than two observations, and otherwise the **median** inter-row gap
  (`services/qc.py:40-47`).
- **Selection is exact.** `QcRuleSet.rules_for` filters `r.time_step == time_step`
  (`types/domain.py:160-167`); `services/qc.py:252-253` calls it with the inferred value.
- **The deployed rule set declares only two cadences.** `config.toml` carries 26 rules:
  **14 at 600 s and 12 at 86400 s**. There is no 3600 s rule.
- **A miss is silent and reads as success.** No matching rule ⇒ no flags ⇒
  `_aggregate_qc_status([])` returns `QC_PASSED` (`flows/ingest_observations.py:130-132`).

### What that adds up to

1. **All 12 daily rules are unreachable on the scheduled ingest flow.** A daily series puts at
   most one row in a three-hour window, so the inferred step is 1 h, which matches neither 600 s
   nor 86400 s. The affected rules are `range_check`, `rate_of_change`, `spike` and
   `gross_outlier` for `discharge` and `water_level`, and `range_check` + `gross_outlier` for
   `precipitation` and `temperature`.
2. **`precipitation` and `temperature` have *only* daily rules.** For those two parameters there
   is no sub-daily fallback at all, so on this path they can match nothing at any cadence.
3. **The failure presents as a clean pass**, which is why it has not been noticed: the flow
   reports observations as QC-passed, having run zero rules.

Daily QC does still happen at **onboarding**, whose window is historical and wide enough for the
median to land on 86400 s (`services/onboarding.py` Step 5). So the defect is specific to the
scheduled path — which is the only one that runs after deployment.

### What is NOT yet measured

**How many production stations are affected today.** That needs the live database: the counts of
`(station, parameter)` groups whose real cadence is daily, and how many observations each puts in
a three-hour window. T1 owns that measurement. Nothing in this plan's fix should be chosen before
it, because the right fix depends on whether the real problem is "daily stations" or "every
station with a gap".

## 🔴 Cross-plan: this is a blocker for Plan 264 T3

**Plan 264 T3 makes "a non-empty rule set that resolves nothing for a series" an error.** That is
the correct policy, and it was accepted precisely to stop fail-open QC. But if it lands while
this defect stands, **every daily `(station, parameter)` group starts raising on every scheduled
ingest run** — which is the fleet-wide halt that Plan 269 D3 and `docs/workflow.md` § Preserve
Existing Logic both argue against.

264 T3's own verification ("a non-empty rule set that resolves nothing for a series raises") would
pass its unit test and take the live path down. **264 T3 must not land before this plan**, or it
must land together with the fix. Recorded in 264 as a required sequencing note.

The same mechanism also explains a limitation in **Plan 269**: the ingest path it wires cannot
apply a daily per-station ceiling, because no daily rule is ever selected there for an override to
merge onto. That is why 269 is paused rather than folded again.

## Owner decisions

**D1 — Which end to fix: the window, or the selection?** Three options, and the measurement in T1
should decide between them rather than taste:

- **(a) Select on the declared cadence, not an inferred one.** A station's expected cadence is
  metadata, not something to guess from a three-hour sample. This is the most correct answer and
  the largest change: nothing on `StationConfig` currently declares an observation cadence, so it
  would have to be added and populated.
- **(b) Widen the QC window per parameter** so a daily series has enough rows for the median to
  land. Smallest change, and it keeps the inference. But it enlarges every QC fetch, and a
  station with gaps still mis-infers.
- **(c) Try each configured cadence rather than one inferred value** — for a group, select rules
  whose declared step is consistent with the observed spacing within a tolerance, instead of
  demanding exact equality on a median.
*Recommendation: decide after T1.* If the affected set is "daily stations", (b) is enough and
cheap; if it is "any station with a gap", the exact-equality match in `rules_for` is the real
defect and (c) is the fix; (a) is the v1 answer either way and should be recorded as such.

**D2 — Does a resolve-nothing outcome become an error here, or stay with Plan 264 T3?**
264 T3 already owns the policy. *Recommendation: leave the policy there and fix reachability
here*, then let 264 T3 land afterwards — at which point it protects rather than halts. This plan
should not introduce a second, competing fail-closed gate.

**D3 — Is the unreachability corrected for history as well as for future runs?**
Observations already marked `QC_PASSED` having run zero rules are indistinguishable, in the
stored row, from observations that genuinely passed. *Recommendation: do not backfill; record the
limitation.* A re-QC over history is a much larger operation, and `docs/standards/wmo.md` treats a
QC verdict as a record of what was known at judgement time. But the owner should decide whether
any published figure rests on those verdicts.

## Tasks

### T1 — Measure the live blast radius

**Outcome**: a measured count of how many `(station, parameter)` groups currently resolve zero
rules on the scheduled path, and why.
**In**: a read-only query against the staging database, run and its output recorded in this plan;
no code change.
**Verification**: for each `(station, parameter)` with observations in the last 30 days, report
the median inter-row gap over a three-hour window, the cadence that would be inferred, whether
any configured rule matches it, and the count of observations marked `QC_PASSED` with an empty
`qc_flags`. **The headline number this plan needs is the proportion of QC-passed observations
that had zero rules run.**
**Pre-change**: N/A — measurement.

### T2 — The fix

**Outcome**: a configured rule is selected for the series it was written for.
**In**: decided by D1 — `services/qc.py` (`_infer_time_step` and/or the `rules_for` call),
`types/domain.py` (`rules_for`), and/or `flows/ingest_observations.py` (the window).
**Out**: no new rule kinds; no threshold changes; no change to what a *selected* rule does; no
per-station override behaviour (Plan 269).
**Verification**: a daily series presented through the scheduled path's window selects the daily
rules and flags a value that violates them — the discriminating case, since the current code
marks it passed with zero rules. A 10-minute series continues to select the 600 s rules, asserted
against the same fixture so the fix cannot silently broaden selection.
**Pre-change**: a RED test proving that today a daily series in a three-hour window selects **no**
rules and is reported `QC_PASSED` — failing on the flag/status outcome, not on a missing symbol.

### T3 — Make a zero-rule outcome observable

**Outcome**: a group for which no rule resolved is visible without reading the code.
**In**: `flows/ingest_observations.py` — a counter on `IngestResult` and a
`PipelineHealthRecord` at `WARNING` (`types/enums.py:187-190` has only `OK`/`WARNING`/`CRITICAL`).
**Out**: **not** a raise — that is Plan 264 T3's policy and must not be duplicated here (D2).
**Verification**: a run containing one zero-rule group reports it in the flow result and in a
health record, while a run where every group resolves rules reports neither.
**Pre-change**: a RED test proving that today such a run is indistinguishable from a clean one.

### T4 — Documentation

**Outcome**: the selection contract is written down, including the failure mode that produced this
plan.
**In**: `docs/spec/types-and-protocols.md` (the `rules_for` contract and the cadence source);
`docs/standards/wmo.md` if it states the QC selection contract; `docs/touchpoint-maps.md`.
**Out**: no code change.
**Verification**: bounded inspection — the cadence source is named, exact-equality matching is
stated, and the historical limitation from D3 is recorded.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"] }
  ]
}
```

T1 runs first and gates D1: the fix should be chosen from the measurement, not before it.

## Explicitly out of scope

- **The fail-closed policy for a resolve-nothing outcome** — Plan 264 T3 owns it (D2).
- **Per-station threshold overrides** — Plan 269, which is paused pending this plan.
- **Re-QC over historical observations** (D3).
- **New rule kinds or threshold values.**
- **A declared-cadence field on `StationConfig`** unless D1 selects option (a).
