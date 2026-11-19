---
status: READY
created: 2026-11-19
---

# Reusable model onboarding with independent station outcomes

## Purpose

An operator can onboard a trainable model for an explicit station scope in any
deployment. Units returning `SKIPPED_COMPAT`, `FAILED_SMOKE_TEST`,
`SKIPPED_NO_DATA`, `FAILED_TRAINING`, `SKIPPED_INSUFFICIENT_EVAL` or
`GATE_REJECTED` do not prevent eligible units from completing. These are the
recoverable outcomes supported by this MVP, not a promise to recover from any
station exception. The operator can identify successful, held and failed units
and rerun only those requiring attention.

Reuse `onboard_model_flow`, its registered `onboard-model` Prefect deployment,
and `ModelOnboardingResult`. No Nepal IDs, paths, tenant, station count or time
zone enter shared code. The Nepal rehearsal is the first application.

The owner requested this reusable plan on 19 November. Exploratory code and tests
remain uncommitted; no expanded implementation was performed during planning.
This independently reviewed plan supersedes R1's implementation readiness in
[the Nepal execution plan](2026-11-18-nepal-model-onboarding.md); its isolated
rehearsal authorization remains unchanged. Plans 256 and 259 concern station
onboarding, a different flow: do not absorb their fleet reporting work here.

## Ownership and coordination

Owner direction on 19 November: proceed with P2; coordinate P1 with the
deployment-smoke owner and P3 with the BIPAD/station-onboarding owner.

- P1 is the existing **model-training smoke fixture**, not the new disposable
  deployment smoke (`tools/deployment_smoke.py`, `flows/deployment_smoke.py`).
  The deployment owner confirmed this session retains the model-training fixture
  fix and regression; that session retains deployment smoke and offline profile
  validation. No handoff or overlapping code edits are needed. This is ownership
  confirmation, not technical patch approval. Coordinate any shared-document
  edits and reconcile release metadata with latest main before PR.
- P2 remains owned by this model-onboarding session.
- P3 boundary agreed through the owner's relayed coordination message: the
  station-onboarding session owns `new-station-by-hand.md`, its station CLI
  commands and their touchpoint-map entries, and Plans 540/541. Its unmerged
  `docs/452-guide-gaps-walkthrough` branch is not a base for this work.
  This session owns the separate `docs/operations/model-onboarding.md` and may
  update `model-artifact-import-runbook.md` if necessary. Link to the existing
  group-assignment procedure instead of duplicating it. Send any proposed
  station-guide/index link wording to that owner; do not edit those shared files.
  Use current remote main and label unmerged/planned commands as unavailable.
  Implementation refreshed main to `81331369`, which includes the general
  station-readiness CLI. Its model-dependent discharge-lookback check describes
  operational inputs: operational lookback sufficiency is not proof of enough
  historical training/evaluation data. Station promotion is separate from model
  artifact promotion and is not implemented by this task.

### Readiness boundary — agreed with the station-onboarding owner

The owner relayed final agreement: Plan 540 M4b's historical training-window
check belongs to a **training-readiness view**, not the operational-readiness
verdict. The station-onboarding owner will record that distinction in Plan 540
and retain it when implementing M4b and station promotion in M7a.

This guide distinguishes three questions:

| Question | Evidence and owner |
|---|---|
| Can this station run an already trained model now? | Operational readiness: current required inputs and operational setup; station-onboarding owner |
| Is there enough suitable history to train this model? | Model-specific training window, variables, QC and coverage; future M4b training-readiness view, with existing training checks remaining authoritative meanwhile |
| Is there enough independent evidence to evaluate it? | Separate evaluation window, training overlap, observed reference coverage and actual metric sample counts; model-evaluation procedure in this guide |

Do not turn either of the first two answers into a scientific performance claim.
An imported model can be operationally usable without local training history;
local retraining must not become a prerequisite for using it. Conversely, a
successful training fit does not establish operational input availability or
independent skill. Respect existing gates without creating a new shared verdict.

M4b and M7a are not prerequisites for this patch or its isolated rehearsal.
Until their commands are merged and verified, document the manual checks and
existing flow refusals honestly. Do not add a second readiness checker here,
invent universal history-length thresholds, or change the other owner's plans.

## Evidence and existing behavior

- `flows/onboard_model.py::onboard_model_flow` accepts model, station/group scope,
  period, step, tenant and operator. It trains, stores, hindcasts, scores, checks
  gates, promotes and assigns per training unit.
- `types/model_onboarding.py::ModelOnboardingResult` already returns per-unit
  outcome, compatibility, artifact ID, hindcast steps, gate and error, with
  separate promoted, failed, skipped and gate-rejected counts.
- Common per-unit refusals already continue. Unexpected assembly/evaluation
  exceptions are not all isolated. Do not claim every exception is recoverable.
- Only promotion emits `model.onboarding_unit_completed`; other outcomes have
  different diagnostic events. Prefect completion alone does not establish that
  any unit was activated. Python result persistence/readability is not assumed.
- The synthetic smoke fixture supplies 21 daily rows to climatology, which needs
  at least 365. An isolated run with 1,096 real qualified training observations
  therefore failed before training. This fixture bug is independent of FI/time-grid.

## MVP behavior contract

| Situation | Existing result and continuation |
|---|---|
| Incompatible targets, features, cadence or other compatibility check | `SKIPPED_COMPAT`; no new artifact; continue |
| Smoke contract failure | `FAILED_SMOKE_TEST`; no new artifact; continue; a model-wide defect can affect every unit |
| Assembly returns no usable data | `SKIPPED_NO_DATA`; no new artifact; continue |
| Training fails inside the existing training guard | `FAILED_TRAINING`; no new artifact; continue |
| Missing/failed hindcast issue dates | Preserve existing per-step results; evaluate only actual evidence, never invent dates |
| Insufficient evaluation evidence or failed gate | `SKIPPED_INSUFFICIENT_EVAL` or `GATE_REJECTED`; artifact stays in training, no new assignment; continue |
| Checks pass | Preserve promotion/assignment behavior; read both back rather than infer both from a counter |
| Authorization, shared configuration, storage/integrity failure, or unexpected exception outside existing guards | Fail loudly; later units may be unattempted |

In the Prefect flow, uncaught assembly, lineage, storage/hash, deserialization,
hindcast-subflow, skill-task, promotion and assignment exceptions abort the run.
The service-level orchestration has different guards: its `FAILED_HINDCAST`,
`FAILED_SKILL` and `FAILED_ASSIGNMENT` outcomes are not produced by this flow.
Document the invoked entrypoint's actual behavior rather than the enum alone.
An input shortfall that raises during assembly instead of returning no data is
therefore not yet isolated. Do not advertise this patch as resolving that gap.

Preserve current exception boundaries, including hindcast's own handling; this
does not claim every store exception anywhere is already fatal. No blanket catch
or new generic recovery policy. For station models, one unit is one station.
For group models, one unit is the declared group: do not split it or silently
reduce membership to imitate independent station results.

Runs are not atomic. Earlier writes remain after a later failure. Promotion
precedes assignment, so an assignment error can leave an active artifact without
a new assignment. No rollback, atomic activation, idempotent retraining or resume
is promised. Selectively rerunning failed units can create new artifacts;
successful units should not be rerun casually. Protect inactive assignments.

## P1 — shared smoke-fixture repair

In `services/model_onboarding.py::_run_synthetic_train_predict`, use
`max(existing lookback + smoke horizon + margin, 366)` rows. The existing base
is 1 January 2000: daily data then covers every ordinal-day bin. Preserve larger
histories, cadence, seed, serialization and prediction checks. Do not lower real
training thresholds or skip smoke checks. No model or FI contract change.

Import-only artifacts acquire no synthetic-training requirement. This supports
existing climatology, not a claim that 366 samples train every future model.
Check enlarged-fixture cost, including determinism runs, with supported hourly,
daily, group and ensemble fixtures and existing FI conformance/floor coverage.

Exit: real climatology train/serialize/reload/predict smoke changes from the
reproduced failure to success; existing supported shapes and longer histories
retain their contracts. Prefer behavioral tests over private row-count checks.

## P2 — partial-success evidence and consistent completion logs

Keep existing control flow, gates and result types. Test two station units in
one run: the first model training call fails, the second completes. Exercise
real training, artifact storage, promotion and assignment bodies with fake
stores. Assert separate results, no new artifact/assignment for the failed
station and the healthy station's actual artifact/assignment. A mocked hindcast
does not prove persisted values; existing integration tests or the bounded
rehearsal must supply that evidence.

Cover missing data and evaluation refusal followed by success; all-refused and
empty/register-only runs must not look like successful activation. Retain
storage/integrity and tenant-refusal coverage and group-unit identity.

For normally returned runs, emit one compact new `model.onboarding_unit_result`
structured event per returned unit before the existing aggregate completion
event. Preserve `model.onboarding_unit_completed` and its existing emitters:
older consumers use it as promotion evidence, and the service emitter carries
timing. Include model, station/group, existing outcome and artifact ID in the
new event. Detailed existing events retain reasons. These are result summaries,
not progress events. A fatal run has no
complete final report: inspect earlier progress and persisted state; unreported
units are unknown/unattempted, never successful.

No new database report table, result schema, API, UI, reporting volume,
notification service, retry engine or deployment configuration. Update the
canonical logging event documentation for `model.onboarding_unit_result`.
Test mixed/all-refused totals and prove
fatal runs do not emit a successful final summary.

## P3 — one deployment-independent operator guide

Add `docs/operations/model-onboarding.md`; hand off link wording for the station
guide/index to their owner. Do not edit `new-station-by-hand.md` or
`onboarding-start-here.md` in this patch. Keep history here, and update affected
architecture prose without duplicating the guide. Document:

1. Existing Prefect entrypoint and checked parameter examples: model ID, explicit
   station UUIDs or group UUIDs matching scope, aware UTC bounds, step, tenant
   and operator. Resolve the actual registered deployment name; the display
   label is not necessarily its full CLI identifier. Verify installed CLI syntax
   in a test environment. Explain per-unit logs and totals, not just flow state.
2. Preflight: installed model capability, targets, qualified observations,
   required forcing/statics, cadence/scope, period, existing gates/priority,
   destination and write authority. Reuse deployment configuration selection.
   Keep the three readiness questions above separate. For training/evaluation,
   record the chosen historical windows, usable coverage and model-specific
   requirements; a passing operational-readiness report is not a substitute.
3. This flow auto-promotes/assigns passing units; it is **not a dry run**. Rehearsal
   requires a separately configured disposable database/artifact root and
   explicit destination checks. Do not invent a no-write/evaluation-only flag.
4. List the six recoverable outcomes from this plan's Purpose; explicitly state
   that assembly, hindcast-subflow and skill exceptions can abort the whole run,
   as can the other fatal boundaries listed above. Interpret outcomes and
   selectively rerun failed units after correction.
   Verify actual artifacts and assignments, including protected inactive ones.
   Explain fatal-run partial writes and limits of the summary.
5. Distinguish training from importing supplied checkpoints. CMAL/engression
   use the existing import route; do not force retraining. Import first on that
   route: the group-assignment command requires a registered model row but does
   not itself verify a scientifically qualified artifact. Native training's
   existing flow registers its model, so do not require an imported checkpoint
   before training. Link existing import, assignment, hindcast and skill
   references. Distinguish artifact activation from making a station operational;
   the planned station-promotion command is owned elsewhere and is not available
   merely because the model flow succeeded.
6. Built-in onboarding hindcasts use the training window: their scores are
   in-sample workflow evidence. Held-out evaluation uses existing flows and
   separate run identities; do not mix score generations. Pending time-grid
   qualification still governs scientific adoption. Preserve the authorized
   current-contract isolated rehearsal without changing timestamps.

Examples use synthetic IDs/placeholders; no secrets or restricted station/model
data enter this public repository. Retain operational evidence privately.

## Verification and delivery

Consult the Training / hindcast / skill and FI / model execution touchpoint maps,
Flow 13 design, orchestration and logging standards. No statistical method,
gate, calendar, FI model/adapter, API or live schedule change is planned.

Run affected onboarding/smoke/floor/conformance/group/ensemble tests, Ruff,
formatting and changed-module types. Classify environment failures separately:
the exploratory targeted test run has not been certified passing. Require an
independent patch review and full final suite locally or in CI before owner merge.
No server hotpatch or bypassed smoke gate.

After reviewed testing-image delivery, resume the isolated Nepal pilot; verify
persisted artifacts/hindcasts/skill and per-unit results, then eligible histories.
Keep live-isolation evidence. Live scientific adoption remains separate.

Deferred: generic minimum-training declaration, group redesign, recovery from
arbitrary exceptions, new onboarding UI/CLI, durable fleet reports, transactional
activation, parallel training, and time-grid implementation.

## Bounded implementation and handoff

One implementer owns P1–P3 in one small code/documentation PR. Keep the Nepal
execution plan as the deployment-specific record; the new operator guide remains
deployment-independent. Do not expand this PR to finish the entire model ladder.

| Step | Main touchpoints | Exit evidence |
|---|---|---|
| P1: fixture repair | `services/model_onboarding.py`, smoke tests, affected architecture description | Real climatology round trip succeeds; other supported fixture shapes and larger histories still work |
| P2: partial outcomes | `flows/onboard_model.py`, flow tests, logging standard | Failed/refused unit followed by a successful unit; accurate per-unit summary and totals; fatal errors stay visible |
| P3: operator procedure | New `docs/operations/model-onboarding.md`; import runbook only if a necessary correction is demonstrated | Checked entrypoint/parameters, clear training/import routes, separate readiness meanings and no unimplemented command presented as available |
| Review and delivery | Final diff, targeted checks, full suite/CI | One independent patch review; owner reviews/merges; testing image delivered through the existing release process |
| Nepal rehearsal | Existing isolated database/artifact-root procedure | Persisted pilot artifacts, hindcasts and skill checked; live deployment unchanged; skipped/failed units recorded |

Refresh remote main before implementation integration and again before PR;
preserve unrelated edits and do not merge the station agent's unmerged branch.
Resolve any actual overlap with that owner rather than copying their changes.

After the model-guide PR is merged, send the station-guide owner the final path,
section anchors and this proposed link wording for their PR:

> For training a new model, importing an existing model, and checking its
> evaluation and activation results, follow [Model onboarding](model-onboarding.md).
> Operational readiness and sufficient history for training/evaluation are
> separate checks.

Do not insert the link into their guide before the target is available. The
station agent retains group assignment, weather bindings and station-promotion
instructions. No extra review round is required solely for this ownership
clarification; material behavior changes still require review.

```json
{"review":{"status":"COMPLETE","depends_on":[]},"smoke_owner_coordination":{"status":"COMPLETE","depends_on":[]},"guide_owner_coordination":{"status":"COMPLETE","depends_on":[]},"P1":{"status":"READY","depends_on":["review","smoke_owner_coordination"]},"P2":{"status":"READY","depends_on":["review"]},"P3":{"status":"READY","depends_on":["P2","guide_owner_coordination"]},"patch_review_and_CI":{"depends_on":["P1","P2","P3"]},"isolated_rehearsal":{"depends_on":["patch_review_and_CI","owner_merge","testing_image_delivery"]}}
```

Independent Claude review on 19 November found no design blocker and requested
two plan corrections: preserve the existing promotion event with a separately
named result summary, and document the Prefect flow's exact recoverable/fatal
boundaries rather than conflate them with the service path. Both are incorporated.
The orchestrator marks this bounded plan READY. This is plan readiness, not patch
approval or passing-test evidence. Additional recovery is deferred until a
specific failure and safe boundary are demonstrated; no catch-all is authorized.
Full review reports remain outside Git.

Implementation evidence (19 November): shared fixture repair, final per-unit
result events and the separate operator guide are implemented. The affected
onboarding/conformance/floor tests plus climatology tests passed (93 tests).
All eight existing PostgreSQL onboarding integration tests also passed. Ruff and
formatting passed. Changed-module Pyright diagnostics match main exactly (38
pre-existing errors, zero added); this is no-regression evidence, not a claim
that those modules are type-clean.
The guide's CLI flags were checked against the installed Prefect CLI and its
onboarding JSON against the flow parameter schema, without launching a run.
Independent Claude patch review approved with no blockers. CLI syntax was also
checked as requested by that review. Final full-suite CI remains a delivery gate. The
station guide, its index, station commands and touchpoint-map entries were not
edited. No server image or live model selection changed.
