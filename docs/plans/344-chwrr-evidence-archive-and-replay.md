---
status: DRAFT
created: 2026-09-24
plan: 344
title: Six-year CHWRR evidence archive and diagnostic replay
depends_on: [340, 341, 342, 343]
blocks: []
related: [035]
reviews: []
open_decisions:
  - CHWRR sets `evidence_retention_days` to at least six years after forecast valid time and approves archive capacity, backup target and restore time before cleanup is enabled.
source: 2026-09-24 owner — longer-term archive and replay may follow initial capture, but observations can take five years to become final.
---

# Plan 344 — six-year CHWRR evidence archive and diagnostic replay

## Status and scope

**DRAFT — not implementable.** This is the deferred cold-tier and diagnostic replay slice separated from the first-run capture in Plan 340 and from the operational scorecards in Plan 343. Plans 340, 342 and 343 keep evidence in protected storage with backup, restore checks and cleanup disabled until this plan passes. A CHWRR test publication may begin under those interim gates; destructive evidence cleanup may not. Storage migration and data-loss risk make this high-risk under `docs/workflow.md`.

## Retention contract

- CHWRR configures `evidence_retention_days` with Plan 340's minimum of 2,192 days after forecast valid time. Apply the same six-calendar-year floor to forecast inputs/outputs, model artifacts and runtime images by digest, as-used thresholds and evaluation manifests/results, forecast/warning publications and decisions, observation truth revisions, report generations, cases, adjudications and corrective actions. For an event spanning multiple forecasts, retain each linked chain until every dependent event/case retention deadline has passed. Do not create a second independent verification retention setting.
- Coordinate existing `max_retention_days`, hot windows and cold cleanup so one linked record cannot outlive the evidence needed to interpret it. Archive immutable payloads with content hashes and stable IDs; store an index and restore map for the full chain. Preserve preliminary generations after finalization. A current projection may be rebuilt, but historical records cannot be silently rewritten or deleted.
- Cold archive and backup copies must have access control, integrity checking, capacity monitoring and a tested restore path. A backup of only the current database without pinned model/artifact/image bytes is insufficient for diagnostic replay. Do not assume a new object-storage service; choose the existing protected storage or justify an addition with measured six-station volume and restore time.
- Replay is diagnostic: restore one representative cold snapshot, pinned artifact and runtime; compare outputs within a documented numeric tolerance, and report nondeterministic differences. Do not promise bit-for-bit equality from nondeterministic models. A missing piece remains `evidence_incomplete` with cause.

## Tasks

### T1 — Archive and restore the forecast and warning chain

**Outcome.** A six-year-old forecast input/output, threshold evaluation and human publication/decision chain is linked, intact and restorable after leaving the hot tier.

**In / Out.** In: CHWRR-set retention configuration and minimum validation, existing hot/cold and `max_retention_days` policy, immutable snapshot/artifact/runtime archive by digest, evaluation/publication/decision history, hashes, backup/restore, access control and six-year capacity estimate. Keep deletion disabled until the entire linked chain restores. Out: a new object-storage service without measured need, deletion of earlier publication states or rewriting past decisions.

**Verification.** `uv run pytest tests/integration/store/test_forecast_evidence_archive.py`; below-floor setting rejected, archive and restore a synthetic six-year-old linked chain, hash and actor history preserved, references resolve after hot cleanup, and representative Nepal-sized capacity/restore-time measurements are recorded.

**Pre-change.** RED: the interim no-cleanup rule preserves hot records but provides no tested cold restore after the hot-store window.

### T2 — Archive and restore truth and post-event review history

**Outcome.** A six-year-old observation revision, preliminary/final score generation, case, adjudication and corrective action remains linked and restorable.

**In / Out.** In: Plan 343 truth/revision/report/case/action records, shared retention setting and archive keys, cold integrity and backup/restore. Coordinate deletion so a case cannot outlive its truth or decision history. Out: a second retention setting or removal of preliminary generations on finalization.

**Verification.** `uv run pytest tests/integration/store/test_verification_archive.py`; restore a synthetic six-year-old chain with source revision, hashes, previous classifications and actor history intact; measure six-station size and restore cost.

**Pre-change.** RED: Plan 343 protects these rows from cleanup but does not supply a proven cold archive.

### T3 — Diagnostic replay and cleanup activation runbook

**Outcome.** Operators can diagnose a past forecast from archived evidence and safely enable age-based cleanup only after linked restore succeeds.

**In / Out.** In: one restored model artifact and runtime image, replay tolerance and differences, archive monitoring, backup/restore rehearsal, `docs/architecture-context.md`, `docs/standards/cicd.md`, `docs/standards/logging.md`, `docs/touchpoint-maps.md` and handover instructions. Out: automatic retraining or model activation, claims of exact replay for nondeterministic models.

**Verification.** Restore and replay a representative cold forecast; inject a missing artifact/image and confirm visible `evidence_incomplete`; prove cleanup stays disabled on failed integrity, restore or capacity check and can run only after CHWRR approves the measured policy.

**Pre-change.** RED: no cold evidence replay or linked-record cleanup gate exists.

## Exit gates

T1–T3 checks, real restore and replay, capacity and backup measurements, changed-module Ruff/pyright gates and the full suite after the final code change pass before merge. CHWRR approves the configured retention and capacity before evidence cleanup is enabled. A failed archive or restore leaves the interim no-cleanup protection in force.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"]}
  ]
}
```
