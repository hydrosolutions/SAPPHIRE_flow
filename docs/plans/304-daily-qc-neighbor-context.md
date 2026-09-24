---
status: DRAFT
created: 2026-09-24
plan: 304
title: Supply neighboring observations to daily QC rules
scope: Make daily discharge and water-level `rate_of_change` and `spike` checks see the required adjacent readings without enlarging the set of rows being judged. NOT new rule kinds or thresholds, widening the checked window, forecast QC, or changing Plan 272's cadence-resolution policy.
depends_on: [264, 272, 316, 317, 318]
blocks: [315]
related: [268, 272, 315, 316, 317, 318]
---

# Plan 304 — daily QC neighbor context

## Status

**DRAFT.** Plan 272 D6 assigns this follow-on: the daily `rate_of_change` and
`spike` rules are selected for `discharge` and `water_level`, but they cannot
fire when the checker sees only the short scheduled-ingest window. Plan 318 is
implemented on `origin/main`; do not implement until Plans 316/317 and the
remaining Plan 272 closure are complete and the Plan 264 network-aware checker
contract has landed.

## Problem and measured boundary

The observation checker evaluates a group's `rate_of_change` against its
previous row and `spike` against both its previous and next row
(`services/qc.py::_apply_rate_of_change`, `_apply_spike`). The scheduled ingest
QC window is only three hours (two hours back, one hour forward), so a daily
series supplies at most one row. The rules are selected but return no flag
because their neighbor inputs are absent. Widening the set of rows being judged
would alter which observations receive verdicts; Plan 272 D6 explicitly assigns
neighbor context instead.

## Decisions

### D1 — Separate rows being judged from rows used as context

The checker receives a target set (the observations whose QC status may change)
and a context set (read-only adjacent values used by neighbor-dependent rules).
Only target observation IDs are present in the check result. Context observations
must never receive flags or have their stored QC status rewritten by this call.

Use Plan 272's cadence resolution; do not infer cadence again from the context
set or substitute the nearest configured cadence. For a target in a daily group,
only use a same-station, same-parameter observation exactly one resolved daily
step before/after as the corresponding neighbor. A missing day, explicit gap, or
non-adjacent timestamp breaks the neighborhood; do not bridge a gap.

### D2 — Preserve all existing rule behavior and thresholds

Daily range and gross-outlier checks are unchanged. Only the missing neighbor
inputs for daily discharge/water-level `rate_of_change` and `spike` are restored.
Do not change rule thresholds, daily cadence, QC status aggregation, Swiss
10-minute behavior, or the forecast QC checker. Do not widen the checked window
to include the context rows.

### D3 — Keep reads bounded and status-safe

Production callers fetch only the required adjacent values for a resolved daily
group, through a bounded station/parameter/time query. Existing `QC_PASSED`,
`QC_FAILED`, or `QC_SUSPECT` neighbors may be read as context but must not be
re-judged. The target batch remains the existing set of eligible RAW rows. A
missing neighbor at a true record boundary produces no neighbor-dependent flag;
it does not make the other per-observation checks fail.

### D4 — Coordinate with the onboarding QC follow-on

Plan 315 changes the same `Stage1QualityChecker.check` onboarding caller. Before
Plan 315 becomes READY, its task and dependency set must include this context
contract; otherwise its implementation could land against the pre-304 checker
interface. Plan 268's full-history import QC already has adjacent rows and does
not require this bounded scheduled-ingest context.

## Tasks

### T1 — Define and type the target/context checker contract

**Outcome:** the observation checker distinguishes target observations from
read-only context and consumes the cadence decision supplied by the completed
Plan 272 resolution path.

**In:** `services/qc.py`, the checker Protocol, and the focused QC contract
tests. Coordinate signatures with Plan 264's network mapping; there is no
observation-QC fake today.
**Out:** forecast QC interfaces, rule-selection policy, storage status changes.

**Verification:** a typed/signature test proves the target/context inputs are
explicit and the returned mapping contains target IDs only. Tests show that
context rows are never present in output, even when they would themselves meet
a rule threshold. Run `uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_qc_selection_resolution.py`.

**Pre-change:** the checker accepts one undifferentiated observation list, so a
bounded one-row daily target batch cannot access its neighbors without treating
them as targets.

### T2 — Fetch and thread bounded daily neighbors

**Outcome:** scheduled observation QC supplies one exact predecessor and/or
successor for daily groups without changing the checked batch.

**In:** the observation-store Protocol/implementation, scheduled ingest QC
caller, and onboarding caller after Plan 315's dependency is corrected. Reuse
Plan 272's cadence resolution and station/network mapping. Plan 268's batch QC
already checks its full historical series and does not need bounded context.
**Out:** larger history scans, changing the three-hour target window, writes or
re-QC of context rows, offline precipitation-mask rules.

**Verification:** store tests assert exact station, parameter, status, and time
bounds; caller tests prove that only target rows are passed for status updates.
An absent neighbor is handled as an ordinary boundary/gap, while store or
configuration errors retain their existing fail behavior. Run
`uv run pytest tests/integration/store/test_observation_store.py tests/unit/flows/test_ingest_observations.py tests/unit/services/test_onboarding.py`.

**Pre-change:** the current scheduled window cannot contain both neighbors of a
daily target; a synthetic daily spike in a one-row target batch currently
produces no `spike` check.

### T3 — Prove daily temporal rules without changing the checked population

**Outcome:** daily discharge/water-level `rate_of_change` and `spike` can judge
eligible target rows using adjacent daily readings, and no flag crosses a gap.

**In:** unit tests for the checker, scheduled-ingest QC tests, onboarding QC
tests after Plan 315 is reconciled, and Plan 268's daily-QC regression.
**Out:** threshold calibration, broadening QC history, changing Plan 264
selection, forecast QC.

**Verification:** tests include a middle-day spike with context immediately on
both sides, a rate change using the predecessor, a first/last record boundary,
a missing daily neighbor, a multi-day gap, and a target window whose context row
would itself be flagged if judged. Assert exact target IDs, statuses, and rule
IDs. The Plan 264 Swiss 10-minute equivalence test remains unchanged. Run
`uv run pytest tests/unit/services/test_qc.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_ingest_observations_dhm.py tests/unit/services/test_onboarding.py`.

**Pre-change:** a one-row daily target has no neighbor input and cannot trigger
these rules; the tests must reproduce that no-op before the implementation.

## Exit condition

Plan 304 completes when the same daily neighbor-dependent rules can inspect
adjacent context while verdict writes remain limited to the original target
rows, with no gap bridging and no Swiss or forecast-QC behavior change.

```json
{
  "phases": [
    {"id": "contract", "tasks": ["T1"], "parallel": false},
    {"id": "context-fetch", "tasks": ["T2"], "depends_on": ["contract"]},
    {"id": "behavior", "tasks": ["T3"], "depends_on": ["context-fetch"]}
  ]
}
```
