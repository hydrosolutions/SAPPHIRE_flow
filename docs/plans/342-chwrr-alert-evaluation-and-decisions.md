---
status: DRAFT
created: 2026-09-24
plan: 342
title: Complete alert evaluations and CHWRR warning decisions
depends_on: [341, 340]
blocks: [343, 344, 110, 266]
related: [035, 147, 317]
reviews: []
open_decisions:
  - CHWRR will set warning validity and name a duty roster. This draft proposes an initial 24-hour validity, changeable by CHWRR with an audited configuration history.
source: 2026-09-24 owner — every warning candidate needs an attributed publish or dismissal decision before API release.
---

# Plan 342 — complete alert evaluations and CHWRR warning decisions

## Status and scope

**DRAFT — not implementable.** This plan takes the alert-evaluation and warning-decision work out of the former combined Plan 340. It depends on Plan 340's as-used forecast evidence and Plan 341's human principal, selected forecast publication and durable publication event. It does not send email/Slack, generate CAP, edit thresholds/models/QC rules, implement a bulletin, or build `sapphire-flow-map`. Operational warning behavior, storage and API authorization make it high-risk under `docs/workflow.md`.

The existing `alerts` table is a mutable active projection with `raised | acknowledged | resolved`; repeated triggers can replace detection or acknowledgement fields (`store/alert_store.py`). The existing `POST /api/v1/alerts/{id}/acknowledge` returns 501. Those states are not a hydrologist's publication judgment. New evaluation and decision history must be keyed to a specific cycle and kept separately.

## Locked behavior

1. **Account for every expected check.** Before checking, persist an immutable manifest of expected station/parameter/level/cycle/source/strategy keys. Reconcile one result per key: trigger, no trigger, or skipped/unavailable with reason. Missing checker invocation becomes `not_evaluated` plus a failure signal, never an absent row or a correct negative. Include source (`raw_forecast`, `published_forecast`, `observation`), stable cycle or observation identity, threshold direction/value and as-used alert strategy/probability configuration. The mutable `alerts` projection cannot rewrite evaluation history. Pipeline-health alerts remain internal.
2. **Check high and low flow.** Evaluate configured ABOVE and BELOW danger levels for discharge/water level using directional comparison. The current forecast checker skips BELOW and the observation checker compares only `>=`. Preserve source and level; a threshold crossing does not alone establish impact severity (WMO-1150 in `docs/standards/wmo.md`).
3. **Recheck the selected publication.** Consume Plan 341's idempotent publication and withdrawal events. Under `threshold_check_mode=published|both`, apply existing `AlertEligibility.SKILL_FORECAST`, minimum ensemble and forecast-output QC rules before checking stored selected-forecast values against as-used thresholds. An ineligible fallback or QC-rejected forecast yields `skipped_ineligible_model` or `skipped_qc_failed`, never a warning candidate. Persist source forecast ID and publication decision ID. Keep raw pooled/primary and selected-forecast results as distinct cohorts; show disagreement to the hydrologist. `raw` remains an internal early-warning mode, not authorization for external release.
4. **A human decides on each triggered candidate.** Publication states are `PENDING -> PUBLISHED` or `PENDING -> DISMISSED`; PUBLISHED may become `WITHDRAWN` or expire at the recorded `valid_through`. Each transition records verified actor, UTC time, controlled reason and optional note in append-only history, atomically with a mutable current-state projection. A dismissal affects only that evaluation: a later continuing or worsening cycle creates a new PENDING candidate; a higher level appears separately. Link cycles by station/parameter/direction incident and predecessor publication. Clear, lower-level or dismissed new candidates flag a still-current warning for duty-hydrologist review. Replacement atomically supersedes the previous current warning. Set `valid_through` using CHWRR-managed `warning_validity_hours` (proposed default 24), without rewriting earlier publications when the setting changes. A station-scoped duty hydrologist may replace or withdraw when the original publisher is off duty. No automatic release or escalation bypasses review.
5. **Keep reasons distinct from later outcomes.** Dismissal codes include `model_unlikely`, `data_quality`, `threshold_not_relevant`, `recipient_not_relevant`, `duplicate`, `superseded`, `other`; `other` and every withdrawal require a note. Plan 343 later classifies `correct_dismissal`, `wrong_dismissal`, `true_but_not_relevant` or `indeterminate` using observations and CHWRR relevance judgment. Preserve both records.
6. **Gate all warning outlets.** CHWRR consumer `GET /api/v1/alerts` and detail return only active, human-PUBLISHED warning evaluations in their validity window and station scope; never PENDING, DISMISSED, pipeline-health or raw candidates. Keep raw/pipeline inspection on an authorized internal route. Use stable evaluation/publication IDs instead of mutable alert UUIDs. New cycles do not auto-publish updates, and consumer payloads cannot expose unpublished forecast values. Plan 110 webhook and Plan 266 CAP must consume this human-publication ledger, including approved observation warnings and audited replacement/withdrawal, never the mutable `alerts` table. Both external hydrological outlets remain disabled for the CHWRR MVP; pipeline-health operations alerts remain separate.
7. **Keep linked publications consistent.** Forecast-backed warning publication requires a triggered `published_forecast` recheck linked to a still-active selected forecast publication. Reject `raw_forecast`, skipped, nontriggered or stale-source candidates inside the publish transaction. Observation-backed warnings have their separate human approval path. A forecast with a current linked warning cannot be withdrawn without a joint authorized transaction that withdraws both and writes both audit histories; otherwise reject the forecast withdrawal. A later outbox event cannot repair this invariant.
8. **Enforce append-only history in the database.** Expectation manifests, immutable evaluation results and warning decision transitions reject `UPDATE`, `DELETE` and `TRUNCATE` under role-independent migration guards; grant INSERT only to the appropriate service role. A mutable current-state/processing projection uses separately scoped privileges. Exercise guards as API, worker and privileged non-owner roles. Keep evidence, evaluation and decision rows outside cleanup until Plan 344 proves linked archival; use Plan 340's protected backup and six-year retention floor.

Dashboard contract: bounded, scoped, paginated `GET /api/v1/review/alert-candidates` and detail; `POST .../{id}/publish`, `/dismiss`, `/withdraw` with expected version and idempotency key; dismissal/withdrawal also require reason code/note. Consumer `GET /api/v1/alerts` and detail expose only published warnings. Replace or explicitly retire the acknowledgement stub; never repurpose it as dismissal. Define exact status codes and schemas in `docs/spec/types-and-protocols.md` during implementation. The separate MVP dashboard may demo candidate evidence, pending state, decision reasons and later outcomes; local mocks must be labelled illustrative until the backend exists.

## Tasks

### T1 — Complete per-cycle evaluation ledger and low-flow checks

**Outcome.** Every expected forecast or observation threshold check leaves an immutable trigger/no-trigger/skipped result with as-used threshold, direction, input references and strategy; BELOW checks produce correct candidates.

**In / Out.** In: forecast/observation checkers and flow call sites, Plan 341 publication-event consumer, typed expectation/evaluation schema/store, migration guards and grants. Derive the expected manifest before invocation and close it even when a checker fails. Process API-originated events through a durable idempotent outbox consumer on the `default` pool, with a proposed `2-59/5 * * * *` schedule, per-deployment concurrency 1, bounded batches and backlog health. Add `SCHEDULE_RECHECK_PUBLICATIONS` to `cli/register_deployments.py` and effective `docker-compose.yml` with matching defaults. Share a named DB bulk-write concurrency slot with the forecast cycle for conflicting writes; benchmark simultaneous six-station forecast/recheck CPU, memory, queue latency and DB load in the 8-GiB worker before activation. A missed or failed check remains visible, retried and caught up after restart. Out: threshold authoring, Plan 110 delivery/hysteresis. Keep raw, selected-published-forecast and observation results separate but incident-linked.

**Verification.** `uv run pytest tests/unit/services/test_alert_checker.py tests/unit/services/test_observation_alert_checker.py tests/integration/store/test_alert_evaluation_store.py`; high/low boundaries, expected-key closure after checker failure or no model, no-alert rows, threshold change, retry/concurrent cycle, raw/selected disagreement, eligibility/QC skips, worker failure and outbox catch-up, backlog health, effective CLI/Compose schedule, real role-independent append-only mutation denial, and representative overlapping forecast/recheck load and shared-write serialization.

**Pre-change.** RED: no-trigger/skipped checks lack durable rows; forecast BELOW levels are skipped and observation comparisons assume ABOVE.

### T2 — Human warning decision ledger and API

**Outcome.** Only an authorized CHWRR hydrologist can publish, dismiss or withdraw a specific warning candidate; service consumers see only released warnings.

**In / Out.** In: decision types/store, incident lineage, current-publication projection, API routes/schemas, audit events, DB/API grants, cache behavior and scope checks. The publish transaction rejects `raw_forecast` candidates and any `published_forecast` evaluation that is not triggered or whose selected source publication is inactive; check under the same transaction/lock as the decision. Observation candidates retain their own human approval path. Add CHWRR-managed, positive `warning_validity_hours` with audited changes; record fixed `valid_through`. Reject stale versions, invalid transitions, cross-station actions, and idempotency-key reuse with a different payload. Expiry, replacement and withdrawal retain history. Implement or reject linked forecast/warning withdrawal atomically. Do not enable CHWRR warning consumer mode before Plan 340's protected backup/restore and this plan's no-cleanup guards are proven. Out: automatic delivery, CAP, bulletin release, general acknowledgement/assignment workflow.

**Verification.** `uv run pytest tests/unit/api/test_alert_review_api.py tests/unit/api/test_api_alerts.py tests/integration/store/test_alert_decision_store.py`; retry/race, rollback, actor and reason, raw/selected nontrigger denial, stale source withdrawal denial, approved observation candidate, next-cycle re-raise, higher level, clear/dismissal against current warning, duty-cover action, validity change, linked withdrawal, expiry, consumer route disclosure, append-only mutation denial and backup/restore activation gate.

**Pre-change.** RED: acknowledgement returns 501, active alert rows are exposed directly, and no human publication/dismissal history exists.

### T3 — Document warning lifecycle and dashboard contract

**Outcome.** Operators and dashboard implementers have exact candidate, decision and consumer API behavior.

**In / Out.** In: `docs/architecture-context.md`, `docs/spec/types-and-protocols.md`, `docs/standards/security.md`, `docs/standards/logging.md`, `docs/standards/wmo.md`, `docs/standards/cicd.md`, `docs/standards/orchestration.md`, `docs/touchpoint-maps.md`, handover/API docs and DRAFT Plans 110/266. Make Plans 110/266 depend on this human-publication ledger for any later CHWRR hydrological dissemination. Out: separate dashboard repo.

**Verification.** Bounded OpenAPI/schema and deployment/backup gate inspection; focused T1/T2 checks.

**Pre-change.** N/A — documentation; T1 and T2 provide behavioral RED evidence.

## Exit gates

All task checks pass, including real PostgreSQL guards/grants, complete expected-key coverage and an atomic linked forecast/warning withdrawal test. Demonstrate effective schedules, concurrent forecast/recheck load and queue health. CHWRR warning publication remains off until the protected backup/restore and six-year no-cleanup policy from Plan 340 are proven for evidence, evaluations, publications and decisions. The changed-module Ruff and pyright gates pass, and the full suite passes after the final code change before merge. Webhook and CAP remain disabled for the CHWRR MVP.

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
