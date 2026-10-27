---
status: READY
created: 2026-10-26
revised: 2026-10-26
title: Shared BAFU and Nepal21 daily CMAL evaluation — Mac-mini acceptance
risk: high
related: [262, 326, 514, 519]
---

# Shared BAFU and Nepal21 daily CMAL evaluation — Mac-mini acceptance

Vision: [confirmed outcome](../../planning/visions/2026-10-26-reliable-daily-bafu-cmal-small-evaluation-forecasts-through-the-api.md)

## Scope and authority

This replaces this file's narrower pre-interview QC-isolation draft. The owner
confirmed one daily forecast, current BAFU assignments, API-only delivery,
`cmal_small` preference with identified fallbacks, reviewable plausibility-flagged
outputs, unchanged input QC/FI requirements and seven unattended acceptance days.
Notifications, sub-daily support, retraining and membership expansion are deferred.

Owner-approved Nepal6 amendment: design group-member failure isolation, daily CMAL
scheduling, explicit evaluation API selection/fallbacks and plausibility-flagged
visibility for BOTH BAFU and Nepal21. Configure evaluation behavior per deployment;
do not introduce a BAFU-only exception. Disabled/unconfigured deployments retain
existing behavior. Installing shared code is not approval to activate a schedule.
The Mac mini remains the first live seven-day acceptance target; Nepal21 rollout
and schedule activation remain separately approved and owned by Nepal6.

READY: the owner authorized readiness and implementation on 2026-10-26 after
independent reviews and incorporation of the accepted corrections. At the owner's
request, no further plan-review round was started; no post-correction review is
claimed. The vision governs outcomes; this plan describes reversible mechanisms.
Live triggers, data/configuration changes and deployments remain separately gated.
Implementation validation and patch review remain distinct from plan readiness.

## Baseline and coordination

- Clone: `/Users/bea/Documents/GitHub/SAPPHIRE_flow-group-qc-isolation`.
- Branch: `feat/daily-evaluation-forecasts`.
- Implementation base: `9980747284b71c89effbf737794dbbe6efb8a4c2` (PR #413);
  original investigation baseline was merged vision PR #412.
- Focused baseline: 162 tests passed, 2 warnings; Prefect logged a shutdown error
  after completion, with command exit status 0. This validates the existing group
  isolation/FI tests, not the remaining daily-flow or evaluation API work.
- Mac mini: 0.1.1039; 138 assigned members; two stored CMAL forecasts from
  September 25. The October 26 read-only diagnosis reproduced the empty-target
  stacking error; 2206/2414 have suspect-only discharge in the checked window.
- Current main ALREADY contains Plan 519 T3b member isolation and the subsequent
  Aquacast area-failure repair. Plan 514's delegated repair is not a new queue.
  Verify and reuse it. Its broader telemetry remains separately owned.
- Nepal6 uses a separate clone, the same source SHA and published v0.1.1076;
  delivery failed before release-image activation. It owns delivery diagnosis,
  replay and release adoption. Never modify that clone/environment/deployment.
- Preserve the original shared checkout and its unexplained bare setting.
- Shared files requiring coordination: group service, operational inputs,
  input-quality policy, FI adapter, cycle flow, their tests and contract docs.
  API/selection/scheduling/configuration work is newly in scope from the confirmed
  vision; coordinate with Nepal6 before implementation touches shared surfaces
  there. Its current delivery attempt stays pinned. The shared design does not
  authorize modifying its checkout or deployment.
- No silent dependency repin or schema/API change. A necessary additive change
  must be described, tested for compatibility and reviewed before implementation.
  Ordinary/provisional classification, evidence, publication, tenant and restricted
  data-use boundaries remain intact. Evaluation is not permission to bypass them.

## Source findings driving the plan

`assemble_group_operational_inputs_outcome` already excludes absent required
past targets per member and retains expected IDs/reasons. Required inputs come
from FI declarations, not output target names. Existing tests cover target-free
models, sparse model-owned histories, real FI NaN gating, static/future isolation
and fatal-error handling. `_shim.py` has per-member area conversion failures.
These facts replace the older assumption that a new core isolation repair is needed.
The implementation regression seeds real suspect-only or failed-only discharge
history through the observation-store interface, runs the real group assembly and
FI cycle, and checks healthy-sibling persistence plus an explicit
`missing_required_target` exclusion with the rejected member first and last.

The group result builder still sends QC_FAILED outputs to rejected storage.
The combined-cycle reader in `forecast_lab/db_sources.py` excludes QC_FAILED
results, but its per-model latest reader has no equivalent QC filter. `snapshot.py`
selects the first renderable model in assignment-priority order. Today group QC
failures stay out because the result builder captures them in rejected storage,
not because all readers protect against them. Existing rejected APIs
have separate authorization and evidence semantics. Thus an assignment-number
change alone cannot deliver preferred plausibility-flagged evaluation results.

The old mini resolves default issue time from wall-clock start; daily filtering
can discard the issue-day weather bucket. Reconcile current main, Plan 326 and
actual schedule configuration before implementing a logical-issue-time solution.
Incomplete NWP fetches can prevent the whole cycle before group isolation runs.

## Rapid-evaluation design proposal — 2026-10-26

Owner direction: prioritize getting the evaluation workflow running, preserve ALL
existing regression/other models, and avoid an isolation platform for deployments
that run on separate machines. CMAL preference is selection, not exclusive execution.
The proposals below close the three review gaps with small shared components. They
are accepted for implementation under the readiness decision above, not activation
approval. No additional post-correction plan review is claimed.

### D1 — Existing Prefect flow, one daily CMAL identity, one retry maximum

Keep the existing `forecast-cycle` deployment and six-hour cadence. No second flow
or scheduler. Add explicit deployment configuration for daily CMAL dispatch,
disabled by default and activated only with approval. All unconfigured group,
regression and other station models retain their current selection and timing.
CMAL preference is not exclusive model execution.

The configured CMAL assignment is REPLACED in the ordinary group loop by the daily
eligibility check: it is never run both as an ordinary six-hour assignment and as
a daily assignment. Resolve due status and claim in a narrow CMAL preflight inside
the regular flow, BEFORE its no-operational-stations return and BEFORE common
Phase-A NWP work. The later group loop executes only the claimed CMAL work, never
claims it again. Enumerate active configured daily assignments UNION unfinished or
retry-eligible scheduled daily records for the tick's logical day, so a vanished
assignment does not strand its earlier attempt. Revalidate frozen assignment,
artifact and configuration identity; record configuration failure on drift.

Freeze the full assignment roster at this preflight before station filtering. On
no-operational-station return or fatal NWP outcome, finalize the claimed attempt as
an explicit prerequisite/shared failure, retain unknown pending member outcomes,
and preserve the existing return/raise and non-CMAL behavior. Never fabricate input
exclusions for an NWP failure. A later same-day tick sees ordinal 1 and may claim
only ordinal 2; failure to persist the summary leaves ownership unresolved, not a
fresh allowance. Test these actual early-exit branches, not only group-loop fakes.
If not due, skip only that assignment, not the rest of the flow. MVP supports
`cmal_small` on its existing GROUP/FI route; reject unsupported daily overrides at
configuration validation rather than silently changing station-model dispatch.

Use typed trigger context: SCHEDULED carries Prefect's authoritative scheduled tick,
resolved once at the flow boundary and explicitly passed into daily dispatch;
MANUAL/REPLAY require an explicit target date and are authorized one-shot runs:
no scheduled claim, no scheduled ordinal, no automatic retry, and no update to the
scheduled authoritative-attempt/acceptance marker. Give them trigger-qualified
identity distinct from scheduled ordinals 1/2. Do not let manual writes supersede
an active scheduled day's referenced outputs: use disposable replay stores or
explicitly approved non-overlapping identities; no hidden natural-key collision.
Actual wall-clock start is not a fallback for daily identity. Scheduled daily issue
is 00:00 UTC of the scheduled tick's date. An 18:00 tick delayed past midnight still
belongs to its scheduled day; this is a delayed scheduled run, not invented catch-up.
If scheduled context cannot be verified, leave CMAL unattempted with a clear reason
and continue unaffected models. Preserve the existing cycle time for other models.

CMAL's assembly, issue/lead times and evidence use its daily logical issue time;
weather reference and actual execution remain distinct. Use available compatible
weather under existing age, feature, timing and coverage contracts. No input
fabrication, historical loop or scheduled backfill. Use the same artifact identity
for the day's first attempt and its retry; an artifact/configuration change does
not silently turn a retry into a different experiment.

Allow at most TWO scheduled executions per logical-day/group/model/data-use key:
the initial attempt and ONE retry on a later regular scheduled tick for that date.
Count a claimed daily attempt even if a prerequisite such as NWP later fails; claim
and record failure before any early-abort path can lose the day's accounting.
Once every frozen-roster member is either structurally produced or genuinely
input/configuration-excluded, and at least one CMAL result is produced, close daily
dispatch. Input exclusions remain visible; later data recovery does not reopen a
closed day. If model/integrity/shared failures remain or no output was produced,
allow the single retry. After the second attempt, stop regardless of outcome and
report incomplete/failed honestly. Do not add further retry machinery.

The retry recomputes the eligible group once under the SAME logical issue identity,
using existing identical-result/supersession rules and exact returned IDs. Its
outcomes form a complete new attempt, not a union of each station's best attempt.
The containing regular tick still runs regression and all other models exactly
once under existing behavior. The CMAL retry adds no extra invocation and suppresses
no ordinary model run; it gates/retries only CMAL. The scheduler and API use D3's same authoritative
attempt key and frozen roster. Manual retries are separately authorized and cannot
repair unattended acceptance after the fact.

MVP state boundary: the current FI CMAL path returns no model post-state. Keep that
path stateless; assert this in real-adapter and retry tests, never publish a new
state from either attempt. An unexpected post-state is an explicit unsupported
contract outcome, not silently discarded or written. Do not enable daily overrides
for stateful/native models in this delivery. Existing other-model state handling is
unchanged. If a future daily model requires state, it needs a separately reviewed
pre-day state snapshot and idempotent finalization design; no generic state ledger
is built now. Verify same prior inputs/state provenance on a controlled partial-
then-retry fixture, and no same-day state advancement.

Preserve existing cross-model combination issue/source compatibility; never coerce
other model issue times to the CMAL day. Test two ordinary ticks on the same date
keep regression outputs at both ticks while closed CMAL is skipped; failed CMAL
gets at most one retry, with no third on later ticks. Cover delayed-across-midnight,
manual/replay, policy disabled, all excluded, mixed outcomes and overlapping claims.

### D2 — Reuse forecast and rejected storage; evaluation is a read policy

Do not make plausibility-failed outputs ordinary successful forecasts. Keep normal
QC computation and `forecasts` versus `rejected_forecasts` persistence unchanged.
Do not rewrite flags or soften the model's output contract. Existing rejected
capture stays out of in-memory successful ensembles, combinations, alerts and
publication. This is the main isolation mechanism, not a new output data silo.

Add one explicit deployment-level evaluation configuration with mode OFF by default,
model preference order (CMAL first, then existing assignment priorities), and the
model allowlist eligible for plausibility-only rejected selection (initially CMAL).
It is usable on either machine; it contains no BAFU-only station/network heuristic.
Other assigned models remain visible and can be preferred fallbacks. Enabling reads
does not activate the daily schedule, and schedule activation does not enable reads.
Keep existing tenant/data-use restrictions independently enforced on both stores.

Proposed additive endpoint:
`GET /api/v1/stations/{station_id}/evaluation-forecasts`.
Use the existing reviewer-or-authorized-human review access pattern and station
scope checks; mode OFF does not expose this route's evaluation data. Do not widen
an ordinary consumer token or treat separate hosting as authentication. Return all
active station AND group model assignments, not just CMAL, with latest result/age,
current daily attempt status, QC/input quality, and an explicit preferred/fallback
selection. Include values or a scope-checked detail reference ONLY when the caller
may read them. Preserve existing publication-gate withholding of values and flag
details for reviewer service tokens; apply the same policy to accepted and rejected
candidates. Withheld metadata may identify a selection without exposing values.
No alternate detail URL may bypass withholding. Rejected references use authorized
review access, never ordinary forecast-by-ID. Test reviewer versus granted human,
active/inactive publication gate and cross-tenant access; coordinate the additive
response with the map team. Explicitly classify this route and its detail responses
as `Cache-Control: no-store`, rather than relying on substring route matching. Add
it to the existing dashboard-origin credentialed human CORS policy for GET reads
(and required OPTIONS preflight); do not allow mutation methods or widen origins.
CORS is not authorization. Test allowed/disallowed Origin, browser preflight and
cache headers with publication gating both active and inactive. No frontend
implementation is included here.

Build the response by composing existing purpose-bound forecast/rejected reads and
D3's small outcome record. Preserve source kind (`forecast` versus `rejected`), IDs,
original QC flags and scientific-use classification. An evaluation preference is
NOT validity. Unsupported/unauthorized data-use classes are withheld, not silently
coerced to standard. MVP shared-code/API acceptance covers STANDARD-class data for
BAFU and Nepal21, using synthetic Nepal fixtures where needed. Nepal's
`expired_rating_test` insert guard and STANDARD-only read boundary remain unchanged;
there is currently NO available test-use activation/read path to reuse. Real Nepal
test-use serving is a separately reviewed and authorized rollout, not this MVP.
Document this limit in the Nepal6 handoff; never bypass guards to unify the endpoint.

Normative selection contract for this MVP:
- Request accepts an optional timezone-aware `valid_at`; missing means the injected
  request clock `as_of`. Reject naive/invalid timestamps. `as_of` always determines
  the current logical UTC issue day; requesting a future valid time does not move
  the issue day or pretend tomorrow's run exists. Return both timestamps.
- Current daily CMAL candidates come ONLY from the chosen finalized authoritative
  SCHEDULED attempt's exact per-parameter produced references for that issue day,
  subject to D2 integrity/data-use/access checks. Never select by latest row alone.
  Failed/unfinished-attempt rows are separately labelled diagnostic/last-good
  evidence, not current/preferred CMAL or successful acceptance evidence.
- Check coverage without interpolation or extrapolation: a valid-time step `t`
  covers `t <= valid_at < t + time_step` for selection purposes. Require the selected
  parameter/representation to contain a complete structurally valid step there
  (including all required quantiles/members); do not relabel timestamps or invent
  values. Document this half-open bucket selection convention in the API schema.
- A stale CMAL row is one from an earlier logical issue day. Future horizon coverage
  alone never makes it current. A current-day result with no requested-time coverage
  remains visible but is not selected, with `no_valid_time_coverage` reason.
- If current usable CMAL exists, select it (`selection=current`, model CMAL), even
  with the allowed plausibility flags. Otherwise select a usable assigned fallback
  issued on the current UTC day, no later than `as_of`, with the same coverage check;
  rank by existing assignment priority then model ID, latest issue per model.
  Label `selection=fallback`, model/source identity and bounded CMAL absence reason.
- With no such candidate return `selection=absent`, no preferred ID/values; report
  stale last-good results/ages separately. Report latest attempt state independently
  (`unfinished`, `failed`, completed member outcome or `not_attempted`), never infer
  success/failure from a missing row. One slow/failed retry cannot combine members
  from different attempts or promote its intermediate durable rows.
- A prior finalized attempt may remain the authoritative completed attempt while
  a newer retry is unfinished, but only its exact references may be selected. If
  a retry has superseded a referenced ordinary row, show that reference as diagnostic
  history until a coherent final attempt is available; do not call it current.

Bind injected-clock tests to this table: midnight boundary, valid-time bucket edge,
future requested time, stale CMAL/current fallback, no coverage, all absent,
crash-after-commit, partial/failed/unfinished retry, exact idempotently reused IDs,
and both accepted and authorized rejected sources. API display never counts a
fallback or a manual/replay result as scheduled CMAL production.

Initial rule taxonomy for the evaluation selector: `negative_value` and `range_check`
failures may be reviewable/preferred for allowlisted models, with all flags intact.
Existing suspect flags (`flat_ensemble`, `ensemble_spread`, `climatology_outlier`,
`temporal_consistency`) remain visible. `quantile_crossing`, non-finite values,
identity/timing/unit/parameter/horizon/representation violations are hard selection
exclusions. Unknown failed rules fail closed; missing QC execution is not presumed
a plausibility-only result. Validate persisted rejected payload structure before
selection because rejected evidence intentionally permits recording invalid values.

Tests demonstrate that evaluation mode changes only this explicit selection/read
path. Ordinary readers, CLI exports, combinations, alerts and publication still
cannot consume the newly selected rejected result. Use BAFU and Nepal21 fixtures,
mode ON/OFF, all-model visibility, fallback/staleness and authorization controls.
Do not undertake broad API cleanup or a new publication/evaluation storage system.

### D3 — Minimal attempt summary and exact output references

Use one bounded attempt-summary table, not event sourcing or a monitoring platform.
One row per daily group/model execution stores tenant/deployment/data-use identity,
logical issue time, attempt UUID and scheduled ordinal (1 or 2; NULL for one-shot
MANUAL/REPLAY with distinct trigger-qualified identity), trigger kind and scheduled
tick, start/finish/state, frozen roster, artifact/configuration identity and validated
per-member outcomes. Use typed enums/domain values and boundary JSON validation.
A partial unique active-claim constraint plus a transaction serializing the daily key
protects claim and the two-attempt limit; Prefect concurrency alone is insufficient.
No history backfill invents outcomes for pre-feature forecasts.

Freeze the active group-assignment member roster BEFORE operational-station filtering.
Use that same roster on the retry. Every removed/non-operational/unrunnable member
gets `configuration_excluded` with a bounded reason, rather than disappearing.
If the active assignment itself changes/disappears after the freeze, record an
explicit configuration failure; do not execute it or substitute a smaller roster.
Define ordinary assignment/status eligibility separately from usable-input checks.
An all-unrunnable group is recorded, not just logged and skipped.

Member states: pending, produced, input_excluded, configuration_excluded,
model_failed, integrity_rejected. Produced includes ordinary accepted output and
structurally usable plausibility-only evaluation output, distinguished by source
kind and unchanged QC flags. Track required parameters within each member; one
parameter cannot hide another's absence. Shared store/config/programming failures
remain failed/unfinished attempts with pending members unknown, never fabricated
input exclusions. Produced counters are based on durable references, not promises.

For each required output parameter, retain a typed reference keyed by
`(station_id, model_id, issued_at, parameter)`, scoped by tenant/deployment/data-use
and owning attempt. Each value contains source kind and exact committed ordinary
or rejected ID. Ordinary and rejected write results use this same mapping shape.
One member is `produced` only when EVERY declared required parameter has exactly
one verified structurally usable committed reference. Rejected storage creates one
row per parameter; one ID for a multi-parameter payload is insufficient. Reject
missing/extra/mismatched mappings as integrity or persistence failure, never success.
Verify reference identity, representation, cadence, units and required value shape
against the payload written; do not query arbitrary latest rows. Test multi-parameter
rejection, mixed parameter verdicts, partial writes and timeouts.

Also capture bounded per-member input/weather provenance at assembly: NWP source,
reference time and age, observation staleness, input-quality verdict and flags,
with unknowns explicit. Store it in the daily outcome/reference payload, not as
fake STANDARD input lineage. Rejected records currently lack these fields; do not
invent them at API read time. Preserve original meanings/timestamps and apply the
same publication/access withholding to detailed quality/provenance fields as to
values/flag details. No raw observations, restricted payloads or secret exceptions
are stored. For ordinary references cross-check against existing stored provenance.

Narrow persistence adaptation for daily-summary-enabled CMAL ONLY:
- Retain each authoritative `ForecastId` RETURNED by `store_forecast`; identical
  retries may return an earlier stored ID. Verify identity/value correspondence;
  do not insist every reused forecast row was physically inserted by this attempt.
- Add a dedicated SYNCHRONOUS rejected-write operation on the daily flow path,
  returning the committed typed per-parameter ID mapping and explicit success/failure.
  Preassign IDs or return inserted IDs. Use DB-native connection/lock/statement
  time bounds; no detached/daemon writer or timed thread join on this path. Return
  after confirmed commit or definite failure; a genuinely unknown commit leaves
  the attempt unfinished with its claim held for operator recovery. Do not reuse
  the existing best-effort daemon wrapper or declare success after timeout.
- Perform daily rejected capture at this explicit attempt boundary, not only in
  the final best-effort whole-cycle flush. Exclude already-handled payloads from
  that flush. This intentionally changes the last-write invariant ONLY for this
  daily path; other models keep their existing best-effort capture behavior.
- Store normal and rejected outputs through their existing bounded transactions,
  then finalize the summary/current marker and release the claim in one transaction
  only after all required references are verified committed and identity-matched.
  This is NOT a claim that separate output stores commit atomically together.
  Intermediate durable rows may exist after a crash, but never imply a completed
  attempt. An evidence-write failure leaves the attempt failed/unfinished and cannot
  yield a produced or integrity-rejected outcome claiming missing evidence.

Finalized attempts are immutable. A retry gets its own UUID/ordinal and parent link;
atomically choose one complete authoritative attempt for the logical day, never a
per-member mix. A newer unfinished attempt is shown separately with the last
completed result. Old valid output may remain accessible as last-good evidence but
must not masquerade as the new attempt's success. Reuse exact existing forecast
supersession behavior and test actual returned IDs with a real PostgreSQL store.

MVP crash recovery is conservative: a normally caught failure can mark the attempt
failed and release its claim only after its owned writes have stopped. Unknown or
crashed ownership stays unfinished and blocks automatic retry. An authorized
operator must verify the owning Prefect run AND worker/write activity have stopped,
then transactionally abandon the exact claim before any retry. No timeout-based
reclamation, heartbeat service or automatic process-death inference. Manual recovery
makes that day non-counting for unattended acceptance; tomorrow is a new daily key.
Test a slow live worker separately from a confirmed terminated attempt; an old
abandoned worker cannot finalize because finalization checks claim identity/state.

Initially retain summaries without automatic deletion and at least as long as the
referenced evidence. Store no raw observations or restricted diagnostic payloads.
Use an additive migration and scoped grants matching tenant/data-use guards, with
old-reader compatibility tested before upgrade. Stateless CMAL writes no state;
no model-state schema change is part of this MVP.

Tests: no runnable members, partial success plus exactly one retry, all failures,
plausibility versus integrity rejection, output/rejected-write failure or timeout,
crash between commit and finalization, authoritative reused forecast IDs, concurrent
claims, third-attempt refusal, non-operational roster members and no stale-row
substitution. Acceptance reads this summary plus referenced values and API results,
not a second acceptance database. Notifications/analytics remain deferred.

### Design gate and smallest delivery sequence

D1–D3 were considered together in the independent design reviews; the owner
accepted the folded corrections and authorized implementation without another
plan-review round. D2's read/storage/auth contract precedes any change that could
affect output persistence.
Use T1 first to verify merged isolation; then implement the small attempt summary
and dispatch integration needed for daily operation, and add the evaluation API
composition. Existing rejected-forecast review remains usable during development;
do not block basic forecast recovery on building a new isolation architecture.
Deliver coherent reviewed changes, retaining all models. Full vision acceptance
still includes the explicit API preference/outcomes and seven-day run; a daily-run
checkpoint alone is not completion. No activation is authorized by this design.

## Proposed execution phases

### T1 — Verify the exact QC-filtered path on current main

**Outcome:** measured distinction between already merged repair and residual bug.

**In:** source/pinned FI contract inspection and command A below. Add only missing
coverage through real observation filtering, group assembly and the real FI adapter,
using synthetic fakes at store/model boundaries rather than stubbing the entire
station assembler. Recover merge provenance; plan labels are not delivery evidence.

**Verification:** healthy + suspect/failed-only required-target member, both orders;
partly rejected but sufficient history; allowed unchecked provenance; target-free
model; all-unusable case; no sibling/tenant/use-class leakage; healthy output
persistence and excluded reason; fatal storage controls. With a deterministic
independent model, healthy-alone and mixed-group numerical outputs must match.

**Pre-change:** add the exact regression first. If it passes, report added coverage,
not a repaired bug. Only demonstrated failures authorize a proposed behavioral diff.

**Out:** changing observation QC, null-padding target history, generic 30-day gates,
singleton-prediction workarounds or removing members from the live group.

### T2 — Establish daily issue identity and compatible weather delivery

**Outcome:** a shared, deployment-configurable daily CMAL schedule with one logical
forecast per day and truthful issue/execution/weather/valid times, without changing
other models' cadence. Schedule activation requires explicit deployment approval.

**In:** cycle issue-time resolution, deployment registration and narrowly scoped
assignment/schedule configuration as needed. Read orchestration and deployment
standards. Choose logical daily identity, idempotent retry behavior, overlap and
late-start handling before editing. Reuse existing retry/supersession policy.
Inspect NWP fetch incompleteness and existing supported cycle fallback behavior;
repair a reproduced retrieval bug if necessary, without weakening input contracts.
Supported older compatible cycles may be used only within configured fallback-age
limits and with every required valid-time/feature/unit covered, with true source,
reference time, age and quality metadata. Arbitrarily old structurally complete
cycles do not qualify. Verify the pinned model's actual FI requirements. Use the
same mechanism for BAFU and Nepal21 with deployment-appropriate weather sources;
source availability must not be inferred from the BAFU-specific incident. Keep
schedule activation explicit and separate from release installation/evaluation
read configuration; no default config migration may start a Nepal schedule.

**Verification:** delayed midnight and daytime execution for the same logical day;
continuous daily input seam; retries/overlaps cannot duplicate or relabel issue
identity; no off-hour CMAL issue accidentally created; other model schedules stay
unchanged; incomplete weather is rejected or an already supported compatible
fallback is selected; failure remains visible when no compatible input exists.
Run BAFU-shaped and Nepal21-shaped schedule cases and opt-in/default controls;
evaluation enablement or release installation alone must not activate a schedule.

**Pre-change:** discriminating tests for wall-clock/daily-bucket mismatch and any
confirmed NWP defect. A real upstream outage is not a reason to invent a code fix.

**Out:** perfect training-weather matching, retraining, fabricated weather, bypassing
coverage/units/timing or a new general scheduling framework.

### T3 — Configure shared CMAL output-plausibility evaluation per deployment

**Outcome:** structurally usable but physically questionable CMAL forecasts remain
reviewable and eligible for preferred evaluation selection with truthful QC flags.

**In:** classify existing output checks into integrity vs plausibility from their
actual behavior; keep hard output contract checks. Propose an explicit scoped
read policy usable by BOTH BAFU and Nepal21, default preserving existing behavior
when not explicitly enabled. Follow D2: retain existing forecast/rejected storage
and unchanged QC flags, and expose rejected plausibility-only results through the
explicit evaluation reader. Do not globally serve QC_FAILED rows or rewrite them
as QC_PASSED. No new ordinary-success persistence is required for these results.

Before RED tests, record a named-rule taxonomy: determine which checks (including
`negative_value` and `range_check`) are plausibility-only for this evaluation policy;
quantile crossing and other violated output contracts remain hard failures. Check
the actual rule behavior, not just its present QC_FAILED label. No unsupported
classification is implied by listing a rule here.

**Verification:** implausible finite forecast visible with original check identity
and evaluation warning; malformed/nonfinite/wrong-identity/bad-time outputs cannot
be selected; other models/deployments unchanged; input QC unchanged; flagged
evaluation results cannot gain alert/publication eligibility or cleanse data-use
classification. Name the selected evaluation endpoint and prove the same flagged
row is not made usable through ordinary forecast readers, combinations, alerting,
publication or ordinary caches/exports in EITHER deployment. Explicitly enabled
STANDARD-class Nepal21 evaluation reads may show/prefer the same flagged results,
while preserving access restrictions and scientific-use classification. Guarded
Nepal test-use insertion/serving remains a separate blocked rollout (D2). Test BAFU and Nepal21
enabled, disabled and independently configured cases: enabling one must not enable
the other. Existing authorized review diagnostics may show flagged results without
treating them as ordinary valid output. Include negative
controls for unknown/wrong station, missing station output, issue-time mismatch,
malformed horizons/quantiles, unit/parameter mismatch and total FI failure. Existing
rejected diagnostic evidence is not deleted.

**Pre-change:** current suppression of a structurally valid plausibility-only result
fails the scoped evaluation acceptance test, while integrity-negative controls pass.

**Out:** threshold tuning to make tests green, weakening observation QC or disabling
all forecast QC. The exact policy representation is not predetermined by this draft.

### T4 — Deliver API preference, fallbacks and per-member outcome visibility

**Outcome:** an authorized API consumer can identify the current preferred CMAL
forecast, an explicit fallback, latest CMAL result/age and each assigned member's
outcome without conflating success, stale history, absence or diagnostic rejection.

**In:** API routes/schemas, forecast-lab selection and bounded existing outcome/store
surfaces. Confirm which existing authenticated endpoint(s) the map team consumes;
provide a backend contract/example response without doing frontend work. Set CMAL
assignment preference only through an authorized configuration path. Specify current
vs stale using logical daily cycle identity and valid coverage, tested with an
injected clock. A stale CMAL row must not defeat a current fallback just because its
priority is better. Select fallback under existing model ranking and label it.
Use the same explicit evaluation selection contract for both deployments. Test
authorized STANDARD-class Nepal21 evaluation access separately from ordinary and
publication readers; guarded provisional/test-use reads remain refused. Ensure
disabled evaluation retains existing selection and publication withholding remains.

Map expected members to produced, excluded, model-failed, integrity-rejected or
unfinished/unknown outcomes. Structured logs may support diagnostics, but where the
API needs latest-attempt status use D3's bounded summary and exact output references.
Confirm no equivalent projection landed since this baseline before adding its
migration. Absence must not be invented as success. Coordinate Plan 519 T3d ownership
instead of implementing a duplicate; do not import Plan 514's monitoring framework.
Before coding, choose the authoritative logical-day/execution-attempt identity and
transaction/finalization rule. Retries and resumes must converge without mixing
one attempt's success with another's exclusions or stale history. Successful status
references committed output; unfinished/crashed stays unknown, and shared faults
remain cycle failures rather than synthetic per-member data exclusions. Include
schema/versioning and backward-compatibility behavior in the map API handoff.

**Verification:** mixed current/stale/absent/flagged CMAL and current fallback matrix;
per-model QC/age/source fields; expected-roster completeness; all-unusable group;
crash/unfinished and older concurrent attempt do not overwrite newer truth; correct
auth, tenant scope and ordinary/provisional/publication isolation on every changed
reader. Test actual values, not only model metadata or HTTP 200.

**Pre-change:** explicit selection/visibility regressions fail on current readers.
Preserve backward compatibility or document a separately reviewed additive contract.

**Out:** map/dashboard code, unauthenticated access, general history analytics or
notifications. API-required outcome visibility is not deferred with notifications.

### T5 — Integrated verification, review and coordinated release handoff

**Outcome:** reviewed owner-mergeable changes and exact evidence for both adopters.

**In:** bounded commits per coherent task, affected docs, focused tests, lint/format/
changed-module type checks and full suite after the final code change. Use this
clone's current version/contribution policy. Require independent Claude and Codex
reviews plus the owner-commissioned relevant high-risk review. Keep full reports
local; record concise outcomes in PRs. Owner alone merges.

**Verification:** commands A/B plus new task-specific schedule/API policy tests,
`uv run pytest` full suite locally or in CI; assembled fixture with healthy,
input-excluded and plausibility-flagged stations through persistence to API. Include
Nepal21-shaped multi-member regression without importing restricted data, plus the
evaluation enabled/default/disabled matrix and no-implicit-schedule-activation tests.

Provide Nepal6 branch/base, changed surfaces, review/PR references and compatibility
implications before adoption. After review and owner merge give exact merge SHA,
release identity, tests/results, configuration requirements and defaults, API
contract examples and schedule-activation approval prerequisites. Nepal6 assesses
and schedules its own update; its current attempt stays pinned. No deployment
action here, and no Nepal live acceptance claim from Mac-mini results.

**Pre-change:** N/A for review/docs. Each behavioral change has earlier RED evidence.

### T6 — Authorize upgrade, then demonstrate seven unattended daily cycles

**Outcome:** actual Mac-mini daily API service, not a one-off successful replay.

**In:** review full upgrade drift from 0.1.1039, not just our patch. Inventory DB
migrations/roles, configuration/secrets, image identity and rollback compatibility.
Require the exact owner-merged SHA, immutable release/image identity, verified
migration state, backup and tested rollback path before any Mac-mini write. No
branch/local image can start the acceptance clock. Read deployment/security
standards; obtain explicit owner authorization before any
live write, forecast trigger, assignment priority change, schedule activation or
update. Evaluation configuration and schedule activation each need explicit scope
in the approval; approving one does not implicitly authorize the other. First use an
authorized isolated Swiss replay/disposable stores where practical; no restricted
Nepal data, no modification of Nepal6's running attempt.

After authorized upgrade, freeze each day's expected active-assignment roster and
configuration before reading its failures. Remeasure input readiness under declared
requirements; never define eligibility by whether prediction succeeded. For seven
consecutive logical UTC issue days, record scheduled/execution/logical times,
weather provenance, one authoritative outcome per expected member, persisted CMAL
IDs/values/QC and authorized API selection/fallback behavior.

A day passes only if EVERY usable-input member produces a structurally valid CMAL
forecast, every other member has an explicit exclusion reason, and at least one
CMAL forecast is produced. Report daily production/exclusion/failure counts; no
percentage minimum is imposed. A usable-input model/integrity failure is explained
but still fails acceptance. Restart after any non-counting day; manual/replay/
backfilled attempts cannot repair a day after the fact. Roster/configuration changes
restart the window unless pre-authorized and evaluated. Keep reports secret-free.

**Verification:** actual scheduled run evidence + read-only DB/API checks for each
day; all seven satisfy the vision, not just Prefect completion. API handoff lets the
map team integrate independently. No claim of model skill or operational adoption.

**Pre-change:** N/A for approved rollout; code/config changes were tested earlier.

**Out:** automatic merge/deploy authority, Nepal update, alerts/publication enablement,
retraining, stricter plausibility thresholds, station expansion or sub-daily support.

## Focused commands and test discovery

A — existing isolation/FI/shim controls:

```bash
uv run pytest tests/unit/services/test_run_group_forecast.py tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py tests/unit/adapters/test_forecast_interface_adapter_nan_gate.py tests/unit/models/test_aquacast_shim_translation.py -q
```

B — existing compatibility controls:

```bash
uv run pytest tests/unit/services/test_operational_inputs.py tests/unit/flows/test_run_forecast_cycle.py tests/unit/flows/test_run_forecast_cycle_rejected_capture.py tests/unit/flows/test_run_forecast_cycle_resume.py tests/unit/adapters/test_forecast_interface_adapter_predict.py tests/unit/adapters/test_forecast_interface_adapter_outputs.py -q
```

T1: `tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py` passed all
18 tests after adding four QC-only-history cases (2 warnings; shutdown logging
error after successful exit). This is regression coverage, not a behavioral fix.

T2 begins with `tests/unit/types/test_daily_forecast.py`: collection first failed
because the daily context module did not exist; all 12 tests passed after adding
UTC scheduled-day and separate explicit-date manual/replay domain contexts.
These are prerequisites only: no flow dispatch or durable claim limit exists yet.
The expanded isolation/identity suite passed 178 tests (2 warnings), and the
existing compatibility command B passed 278 tests (45 warnings).

`tests/unit/services/test_daily_forecast_dispatch.py` defines the pure retry
eligibility policy; it and the identity tests passed 26 tests. The policy does
not replace transaction serialization or prove durable concurrency enforcement.

`tests/integration/store/test_rejected_forecast_committed_write.py` covers the
new synchronous write capability and exact per-parameter IDs after commit, plus
commit refusal, duplicate/empty parameters, read-only construction and purpose
mismatch. With existing rejected-store and data-use tests, 45 PostgreSQL tests
passed. This writer is not yet connected to daily dispatch. No new schema or
schedule has been activated. Changed production modules passed focused pyright.
See [daily evaluation contract](../daily-evaluation-forecasts.md).

Before further T2–T4 edits, record exact new test paths for their RED cases and run
them in the project environment. Do not invent passing evidence for unimplemented
contracts. Private dependency access failures are blockers,
not permission to copy another clone's environment or alter its lockfile.

## Readiness notes

The owner authorized implementation of the corrected D1–D3 design; no new
post-correction review is claimed. T1 establishes repair coverage. T2–T4 implement
the bounded schedule, existing-storage evaluation reader and durable summary.
No new user outcome decision is needed merely to choose those mechanisms. Escalate any discovered contradiction
with the confirmed vision, FI or an existing approved scientific-use boundary.

```json
{
  "phases": [
    {"id": "baseline", "tasks": ["T1"], "parallel": false},
    {"id": "design-gate", "tasks": ["D1", "D2", "D3"], "parallel": false, "depends_on": ["baseline"], "gate": "owner accepts findings from independent complete-plan review before behavioral edits"},
    {"id": "daily-production", "tasks": ["T2"], "depends_on": ["design-gate"]},
    {"id": "evaluation-and-api", "tasks": ["T3", "T4"], "parallel": false, "depends_on": ["daily-production"]},
    {"id": "review-release", "tasks": ["T5"], "depends_on": ["evaluation-and-api"]},
    {"id": "authorized-acceptance", "tasks": ["T6"], "depends_on": ["review-release"]}
  ]
}
```


## Implementation validation and draft handoff

Default-off scheduled execution, exact committed output accounting, authenticated
evaluation reads and owned local manual/replay tooling are implemented. See
[the implementation contract](../daily-evaluation-forecasts.md) for current
behavior and limitations. Migration 0072 and runtime role changes are not deployed.

Final configured suite after the last code change: **10139 passed, 50 skipped,
15 deselected, 58 warnings**, exit 0. Scoped Ruff and format checks passed
(1015 files); production Pyright ratchet passed (348 errors within the 400-error
baseline, with no per-file regression). This is not a zero-error test-file typing
claim. Independent Claude, Codex and focused high-risk reviews closed the repaired
implementation defects within their stated scopes. Full review reports remain
local, not in this plan or the commit.

Release target is 0.1.1079 against base
`9980747284b71c89effbf737794dbbe6efb8a4c2`; no tag or release is published.
Local editable package identity was refreshed against that exact SHA before the
passing run. Earlier failed runs are not passing evidence.

### Remaining owner decisions and operational gates

- Overlapping groups and same-day roster moves: current conservative withholding
  can affect unconfigured CMAL members; incomplete preparation cannot establish
  every overlap. Decide activation-time validation and permitted roster changes.
- Daily CMAL is excluded from ordinary alert/combination inputs. Other models
  continue. Confirm this evaluation-only policy before activation.
- Assignment and deployment priority settings remain frozen for retries; decide
  whether unrelated preference changes should invalidate them.
- Uncertain claims remain ACTIVE without timeout takeover. Authenticated recovery
  and persistent operational signalling need an approved maintenance design.
- Invalid or unavailable pre-admission groups have bounded operational logs, not
  fabricated ledger rows. A full day of unavailable preparation is not acceptance.
- Strict daily weather currently declares ICON hourly intervals only. Undeclared
  IFS/recap intervals fail closed; Nepal availability is not established.
- Owner PR approval/merge, release identity, migration/rollback verification,
  deployment and separate schedule/read activation are pending. No live model
  run, bearer-authenticated live API proof, or seven-day acceptance is claimed.

The draft PR is implementation evidence, not whole-vision completion or permission
to deploy. Nepal6 adoption remains separately owned and pinned until approved.
