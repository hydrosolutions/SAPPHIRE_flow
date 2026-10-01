---
status: DRAFT
created: 2026-10-01
plan: 519
title: Nepal current forecast data with isolated expired-rating pathway tests
risk: high
related: [143, 268, 300, 511, 514, 516]
---

# Nepal current forecast data

Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354
Vision: ../../planning/visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md

## Authority and scope

This plan implements the approved vision including the test-mode amendment merged
in PR #359. Its recorded status is DRAFT; execution follows the global skill and
current user instructions under `AGENTS.md`, not a separate READY gate.
Owner approval controls every merge and exceptional
operational action. The scientific-validity, database-identity, API/security and FI
boundaries make this high-risk work, requiring the ordinary Claude/Codex pair and
one additional owner-commissioned relevant review at the prescribed gates.

The outcome is backend readiness and authenticated latest-per-model data for DHM
447, 450, 604.5, 647, 670 and 684. All available models are exposed; CMAL is the
target, existing regressions may establish an initial pathway. No dashboard code
is included: coordinate contracts and live acceptance with SAPPHIRE-flow-map agents.
No new public/download/derived-metric permission, fabricated inputs, model-contract
bypass, independent publishing batch or duplicate cloud host is authorized.

## Recovery and measured starting point

- Vision PRs #358/#359 are merged and exact content verified at main `c93e6a92`.
  No implementation PR or authoritative delivery record for this Effort exists.
- Merged reusable work: restricted delivery import (#332), discharge readiness
  (#344/#347), Nepal cloud deployment support (#351), reader/authentication and
  rejected forecasts (#320/#328/#330), existing DHM adapter (archived Plan 300),
  CMAL artifact import, weather assembly and forecast-cycle services.
- Existing remote branches for that work are historical evidence, not grounds to
  redo merged changes. No abandoned implementation for this Effort was identified.
  The surviving local checkout is `.worktrees/effort-354-nepal6-backend`; this DRAFT
  is remaining planning work, not evidence of implementation or activation.
- Read-only Mac-mini checks on 2026-10-01 found API/Prefect healthy and worker package
  `0.1.1039`. All six `chwrr` DHM rows are onboarding, without basins, targets,
  active station/group model assignments or forecasts. Historical QC-passed Q
  ends in 2019 at five stations and 2023 at 450. None has a currently valid curve;
  level units/reference metadata are unset. These are shared Mac-mini findings,
  not proof of deployment on the intended Infomaniak Nepal staging host.
- BIPAD metadata was reachable. Candidate API IDs are 137, 25, 37, 61, 89 and 63
  respectively for the six codes above. They are candidates, not approved bindings:
  place-name matching is not authoritative gauge identity or datum confirmation.
- Existing DHM adapter/config/ingest unit tests: 63 passed, 1 warning. This is
  baseline evidence, not a test of the new conversion or invalid-forecast path.
- No cloud HTTPS connection succeeded from this environment. Do not infer that
  the VM is absent or provision a replacement.

The initial source findings are specific: `ingest_observations.py` wires the level
adapter but does not call `RatingConverter`; `OperationalForecast` has lifecycle,
QC and input-quality axes but no scientific test-use classification; forecast retry
identity is station/model/issue/parameter and can supersede another result before
any API filtering. Existing station lists are history, not latest per model, and
station detail omits group assignments. Diagnostic detail can contain restricted
observation-derived statistics. DRAFT Plans 514/516 provide investigation only;
their entire scopes are not dependencies and their conflicting decisions do not
supersede this plan's approved vision.

## Non-negotiable design boundaries

1. **Expired-curve use is not validity.** Preserve curve validity dates and use
   only the newest confirmed same-gauge curve. Keep original levels, derived Q,
   exact parent observation/curve/version and transformation provenance separate.
   Unknown reference compatibility holds conversion and is escalated to the owner.
   Units, station identity, domain/range checks and finite-value requirements remain.
2. **Classification is independent of QC and lifecycle.** Numerically passing
   output may still be invalid. Do not repurpose `QC_FAILED`, `DEGRADED`, a fake
   model ID, delivery ID or `ForecastStatus` to stand for expired-rating test use.
   Existing data must not receive a fabricated claim of scientific validity.
3. **Isolation precedes writing test data.** Valid and invalid natural keys, retries,
   supersession, current markers and last-good selection must be disjoint. An
   identical test recomputation cannot alias an ordinary forecast ID. Passing new
   QC, combining forecasts or a later issue time cannot erase invalidity.
4. **Training and normal inference do not opt in.** Provisional Q and invalid output
   must never enter training, retraining, calibration, hindcast targets or skill
   evaluation. Only the explicitly selected test inference path may consume them.
   Legitimate historical training data and existing compatible artifacts are reused.
5. **Lineage covers actual inputs.** Track all consumed lookback records, resampling
   contributors, group inputs and combined forecast IDs, not just the latest input
   or `OperationalForecast.rating_curve_id` (which currently means active at issue).
   Existing source/evidence structures are reusable, but optional/incomplete evidence
   blobs alone cannot enforce runtime safety. Unknown dependency lineage fails safe.
6. **No side doors.** Ordinary list/detail/history/legacy exports, human publication,
   rejected forecasts, pooled/BMA contributors and in-memory alerts must enforce
   the same class boundary. Test display never creates a normal publication decision
   or disables the publication gate. Restricted/provisional observations and sensitive
   diagnostics stay withheld from consumer/reviewer access.
7. **Current is separate from valid.** Preserve actual issue/valid times, resolutions,
   uncertainty representations and model identities. Fresh invalid results remain
   invalid. Old last-good results remain stale. Slow or failing combinations must
   not hold back already stored healthy results. No synthetic uncertainty or data.
8. **Reuse service boundaries.** Use typed domain values and existing store/service
   interfaces, ForecastInterface, injected clocks and established vocabulary. No
   ticket-shaped runtime architecture or independent clone of the forecast engine.

## Chosen design for review

### Separate provisional inputs; partitioned forecast outputs

Keep genuine BIPAD measured levels in ordinary observation storage. Store provisional
derived discharge in a dedicated, immutable, tenant/station-bound relation, never in
ordinary observations and never under an invented delivery ID/source flag. Existing
observation natural keys (station/time/parameter/source) cannot distinguish provisional
from legitimate rating-derived Q, and QC-passed readers are source-unrestricted.
Physical input separation prevents ordinary training/baselines/component arithmetic
from discovering provisional Q even if its numerical QC passes. A narrow operational
reader composes approved ordinary inputs with the explicitly selected provisional Q
stream for test inference only; it is never bound into a training/evaluation factory.
The existing injected ObservationStore read seam and DeliveryObservationReader precedent
support this without another forecasting engine. Keep deterministic source selection
before pivot/resampling; reject ambiguous same-time candidates instead of last-row wins.

Keep forecast values/evidence in the existing persistence graph, with an immutable typed
use classification `STANDARD` versus `EXPIRED_RATING_TEST`, independent of QC/lifecycle.
STANDARD preserves the ordinary processing contract, not a new certification of legacy
scientific validity. Only explicitly authorized test composition writes test-use outputs,
which are always exposed as INVALID with the safe expired-rating reason. Purpose-bound
forecast stores reject writes of the other class. Partition current-generation keys,
retry lookup/equality and supersession by class, so test and ordinary outputs coexist.
Ordinary store methods and direct SQL readers default to STANDARD, including by-ID/history
paths that intentionally include superseded records. No implicit widening of readers.

Dedicated forecast tables would duplicate the existing values/evidence/preservation/
publication identity graph; the explicit output partition reuses it safely only with a
complete audited reader/writer inventory: stores, direct SQL, summaries/fakes, latest/cycle
markers, history/detail/legacy exports, rejected records, publication, evaluation,
combination and in-memory alert inputs. A single `_is_current` predicate is insufficient
because explicit-status reads bypass it. Provisional inputs remain physically separate;
normal observation schema/source/QC semantics do not change to accommodate them.

Persist immutable conversion lineage keyed to each derived input version: originating
level observation identity AND a protected immutable value/timestamp/source snapshot
(or an equally immutable existing version identity), same-station curve ID/version,
original validity interval, reference-proof identity and conversion version. Observation
IDs alone are insufficient because current ingestion upserts restated values. Corrections
append new provenance versions; never change provenance supporting a stored forecast.
Keep this evidence internal and subject to restricted-data backup/retention controls.
Use the existing same-station/tenant integrity constraints and add those needed for
new lineage joins. Do not reinterpret the active-at-issue `forecast.rating_curve_id`.

Carry typed consumed-input lineage alongside numeric frames in both assemblers and
through actual group/combination dependencies. Extend the existing source-evidence seam,
but persist a mandatory lightweight class/lineage projection atomically with test output;
optional compressed evidence is not the validity gate. Missing mandatory provenance
refuses test persistence. New passing QC cannot erase class. Retry equality within the
test class must compare input/curve/proof lineage as well as existing numeric/artifact/QC
identity, so equal values from a changed expired curve do not silently alias old lineage.
Preserve the ordinary retry decision table and audit historical class semantics.

### Retry table amendment and conversion ordering

The forecast partial unique key becomes `(station_id, model_id, issued_at, parameter,
data_use)` with the existing `status <> 'superseded'` predicate. Retry lookup and
supersession use exactly that key. Amend Plan 327's documented table and classifier
contract explicitly: STANDARD rows keep rows 1-4 unchanged. For EXPIRED_RATING_TEST,
existing rows 1 (values differ) and 2 (artifact differs) supersede within that class;
row 3 (same values/artifact but different QC verdict) still refuses. Before row 4,
a changed canonical consumed-input/curve/reference/transform lineage fingerprint
supersedes within the test class, preserving the previous immutable evidence. Otherwise
return the identical ID. For equal values and artifact, a simultaneous lineage AND
QC-verdict change deliberately remains row-3 REFUSED, not superseded: the historical
QC-policy conflict contract still requires operator review. Test that exact joint-change
case; do not move the lineage check ahead of QC refusal. The refusal message/runbook
must tell the operator to retain the existing result, record the conflict and investigate
changed QC/curve provenance. A later genuine scheduled issue may proceed; do not retimestamp
a failed run, overwrite the held row or claim it succeeded. Same-key replacement under a
changed QC policy needs separate explicit approval, not an undocumented bypass.
Fingerprints exclude run IDs
and wall-clock capture times and include immutable input versions; numerical equality
alone cannot alias new provenance.
Cross-class records never enter the comparison at all.

Define newest same-gauge curve by the store's existing chronology: highest `valid_from`,
then highest `version`, not highest version globally. Conflicting provenance or an
ambiguous same chronology/version holds the gauge. Preserve `valid_to` even when expired;
do not change existing active/valid-at APIs. Conversion accepts only measured levels with
persisted `QC_PASSED` under the configured applicable level rules. RAW, QC_UNCHECKED,
SUSPECT, FAILED and MISSING are held. The immutable level snapshot includes QC status,
rule version and safe rule identifiers at conversion time, as well as value/time/source.
An unknown reference can still be ingested as a measurement but never converted.
An explicit bounded `[since, until]` backfill invocation overrides the normal latest-level
cursor; it uses the adapter's bounded windows/page budget, idempotent storage and historical
QC context. Normal polling retains the existing watermark contract. No hidden full-history
request or backfill truncation is accepted as success.

### No shared model state in the initial test composition

FI/CMAL and the intended FI regression path are stateless at the SAP3 warm-state
boundary. For this delivery, the explicit test composition never reads or writes
`model_states`: inject a no-state reader and a write-refusing state store, pass no
ordinary warm-up bytes and disable all shared state-save branches. Inspect every
station/per-track/fan-out/group result before persisting or adding it to combination;
if a model returns non-null state, report `stateful_test_model_unsupported` for that
combination and do not persist its forecast or state. List that model as unavailable,
not silently omitted. Test-mode support for stateful models requires a separately
reviewed partition/lineage design; do not improvise one or change FI in this plan.
A hard state-store guard is a backstop, not the normal refusal mechanism. Test a
state-producing fake followed by an ordinary run: ordinary state and its ID/time
must remain unchanged, and test code must never load it. Ordinary state behaviour
is untouched. All outputs of the explicit test composition are test-use INVALID,
even when a particular model consumes no provisional Q; any actual test contributor
also makes a downstream product test-use. No test/standard composition mixing is
permitted through an ordinary-store injection.

### Safe migration and rollout

Add the provisional-input/immutable-lineage relations and forecast/rejected-result class
columns with STANDARD defaults for existing data/writers, typed constraints and class-aware
indexes, followed by application/read-path updates. Prevent mutation of an existing
forecast's class with a database guard. Reject test-use publication in the locked store
transaction and a database guard, not only in HTTP presentation. Tests
must verify PostgreSQL identity/foreign-key/index behaviour, not only Python fakes.
No test records may be written until every reader and writer deployed on that database
supports isolation. Ordinary defaults do not protect old binaries that query all rows.
Enforce a per-tenant DB-recorded activation gate, default disabled, writable only by
the owner-approved deployment/operator role, not the runtime API/worker roles. Database
guards reject all test-class forecast/rejection/attempt, provisional-input and protected
conversion-lineage writes until the gate is active. Reference-proof metadata is exempt:
the authorized operator must be able to record verified units/reference evidence before
activation; it contains no provisional values and cannot itself enable conversion or
prediction. Runtime roles cannot approve or mutate those proofs. Grant runtime roles
SELECT only on the activation columns required by the invoker-rights triggers, not
INSERT/UPDATE/DELETE; no SECURITY DEFINER escalation is needed. Test both the legitimate
guard read and denied mutations using the actual runtime/operator roles. The activation
CLI requires an operator-recorded inventory of the API,
every worker and registered deployment image (including queued jobs), operator containers,
scheduled jobs and supported rollback release, with immutable image IDs/digests and source
SHAs demonstrating isolation support. Stop/drain old queued/running jobs first. Record the
inventory digest, permission reference and operator/date; missing or mixed-generation
inventory refuses activation. Verify the guard through direct SQL and the operator CLI.
Keep the gate off throughout rolling upgrades; this is an explicit authorized operator
act, not a migration side effect. After any test records exist, rollback to a pre-isolation
application is prohibited on that mixed database even if the activation gate is disabled. Use the
reviewed compatible rollback release, or owner-authorized isolated restore; never delete
provenance or silently downgrade. Do not enable test mode merely because migrations ran.

### Preparation and prediction without false activation

An explicit CHWRR/six-station preparation invocation may ingest genuine water levels
while the stations remain ONBOARDING. Reuse adapter/binding/window/QC functions and an
explicit station list; do not change ordinary operational/gauged eligibility or admit
foreign/weather rows. The same explicit observation eligibility must reach QC rule
applicability; permitting fetch while leaving the operational-only QC gate unchanged
would silently ingest unjudged levels. Scheduled continuation requires reviewed explicit
Nepal configuration, provider limits and deployment authority. Level ingestion can succeed with an unknown
reference as measured data, but provisional conversion remains blocked until a persisted
verified same-gauge feed/curve reference record exists. It includes author/source/date,
units and the explicit transformation (if any); an absent station datum is not proof.

An explicitly selected, default-off expired-rating inference mode composes existing
station/group forecast services with class-aware input and result stores. It may run
approved prepared Nepal stations without changing their station lifecycle to OPERATIONAL.
It still requires compatible artifact/group scope, static and weather requirements,
QC/input policy, complete trustworthy lineage, and sufficient actual history. Do not
loosen the ordinary scheduler or model declarations. Normal training paths never accept
test-use selection. Missing past forcing continues through the declared-schema/NaN
contract where required by Plan 514's owner decision; future shortfalls and anticipated
member errors are reported individually. Database/store corruption/errors remain fatal.
Assemble incompatible SINGLE/ENSEMBLE requirements separately using existing per-track
seams rather than feeding a mixed superset to all models.

### Current reads and bounded attempt state

Propose `GET /api/v1/stations/{station_id}/current-forecasts` with a typed `data_use`
selector (`standard` default or `expired_rating_test`), plus the same explicit selector
on forecast detail/history as needed for class-disjoint access. These are additive
backend contract changes to agree with the map agents before T4. Key current results
by model/parameter and requested class, union effective station/group assignments with
eligible stored model products, and retain missing assigned combinations as explicit
unavailable entries. Use existing details for values/shape and minimal station-bound
basin metadata where the consumer needs it. Preserve historical-list contracts except
mandatory safe-class filtering and restricted-data redaction. Ordinary by-ID access to
an invalid-class ID returns the same non-disclosing not-found as an absent ID; explicit
test detail always includes INVALID and its safe reason. No query option widens scope.

An explicit invalid request also requires the default-off Nepal test-mode deployment
policy and the recorded owner permission reference. Normal principal station/tenant
scope still applies. Initially, test reads for a publication-gated tenant fail closed;
they do not disable the gate or mint a normal publication decision. The current Nepal
headless configuration has no activated publication gate; changing that is a separate
policy decision, not an implicit exception in this plan. Direct publication-store
calls must reject test-use forecasts even if numerical QC passes.

Use a small latest-attempt projection keyed by station/model/parameter/use-class, with
attempt/cycle identity, ordered timestamps and a bounded enumerated safe outcome/reason.
Keep its state independent of last-good forecast identity and reject older concurrent
attempt updates overwriting newer state. No history analytics or generalized coverage
ledger is added. Missing evidence is UNKNOWN, not inferred failure. A crash may leave
an old/unfinished attempt; report that honestly. Freshness is computed with injected
clock and actual scheduled cycle/valid-time metadata, never from input-age alone.
Sensitive internal errors remain internal. Persist successful outcome/result linkage
consistently; a failed write cannot emit a success projection.

Coordinate the exact additive OpenAPI route/parameter/schema contract with map agents
before T4 implementation; they own proxy/client changes and invalid display. If no map
agent is available, document the proposed backend contract and the coordination hold,
not a fictitious agreement. No plan readiness assertion establishes consumer agreement.

## Ownership of the group-member repair

This plan owns only the group repair required for the approved six-station outcome:
project declared statics, preserve declared-schema/NaN handling for missing past forcing,
and isolate anticipated per-member future/input shortfalls without shrinking reported
membership. The recorded 2026-09-30 owner decision is carried here explicitly: missing
past forcing is represented as declared nulls plus quality flags; FI max_nan decides
whether prediction proceeds. Do not replace it with blanket refusal. Unexpected schema
errors and fatal storage failures remain visible failures.

Coordination recorded 2026-10-01: T3b is the sole implementation owner of this
bounded seam. Plan 514 marks its overlapping static/conformance tasks as delegated,
not delivered; its independent telemetry and unresolved decisions remain there.

Plan 514 stays DRAFT and is not executed as a prerequisite. Before editing the shared
code, the implementing agents must record this limited scope ownership in 514 and
reduce its remaining scope accordingly, or assign the repair there and remove it here. No concurrent
implementation of the same group seam is authorized. Other 514 work is not imported, and
this proposal does not itself change its YAML status.

## Tasks

### T0 — Confirm bounded activation inputs and data authority

**Outcome:** one operator-reviewed six-gauge input/model matrix and explicit holds.

**In:** preserve existing delivery acceptance; verify station/API identities, immutable
basin/static package joins, level units/reference compatibility, newest same-gauge
curve identity/dates and supported bounds, BIPAD history coverage and polling limits,
Recap P/T polygon/source bindings, existing artifacts and station/group assignments.
Check model declarations against FI and real input support: CMAL small requires recent
Q/P/T and declared Caravan statics; the nominally weather-only regression still declares
Q for training labels. Provisional real conversion may satisfy an input only through
the approved test path; do not remove declarations or insert dummy Q.

**Out:** repeat restricted delivery replacement, accepting candidate IDs by title alone,
blind activation, external spending or loading restricted data before backup prerequisites.
Unresolved datum, package review, execution role, cloud access or provider authority
is a named per-station/deployment hold, not a default or silent skipped check.

**Verification:** retained aggregate read-only evidence identifies source host/release,
input dates/availability and artifact contracts without restricted values or secrets.
Review the operator execution role and transaction permissions before writes; earlier
one-off owner credentials are not reusable authority. Re-measure on the actual target.

**Pre-change:** N/A, operational prerequisites and measurement; no mutation authorized.

### T1a — Provisional discharge and reference evidence storage

**Outcome:** provisional Q is physically absent from ordinary observations, with immutable
parent-value/QC snapshots and verified feed/curve reference evidence.

**In:** `types/provisional_discharge.py`, `types/rating_reference.py`, corresponding
`store/provisional_discharge_store.py` and `store/rating_reference_store.py` (new),
`protocols/stores.py`, `db/metadata.py`, one additive Alembic revision, scoped grants in
`docker/bootstrap-roles.sh` / `docker/bootstrap-roles.sql`, and
`docs/spec/types-and-protocols.md`. Use existing same-station/tenant FK patterns. Owns
reference-proof persistence, protected snapshot/version identity, append-only conversion
lineage and its unique identity; no mutable observation-ID-only lineage. Normal observation
stores do not join or union this relation. Runtime readers cannot approve reference proofs.

**Out:** converter invocation, forecast class changes, actual measurement writes.

**Verification:** add `tests/integration/store/test_provisional_discharge_store.py` and
`test_rating_reference_store.py`, and `tests/integration/db/test_migration_provisional_discharge.py`.
Run `uv run pytest tests/integration/store/test_provisional_discharge_store.py tests/integration/store/test_rating_reference_store.py tests/integration/db/test_migration_provisional_discharge.py tests/integration/store/test_observation_store_upsert.py -q`.
Use existing PostGIS/testcontainers fixtures. Assert same-gauge/tenant joins, immutable
restatement/QC snapshots, no overwrite, defaults/grants/migration round trip, and direct
ordinary observation reads cannot see numerically QC-passed provisional canaries.

**Pre-change:** write the store/migration contract tests first; missing relation and
unavailable isolated write/read API must fail before migration/implementation. Keep
ordinary observation-upsert regressions green as the discriminating non-regression gate.

### T1b — Forecast identity, retries and immutable test classification

**Outcome:** standard and test outputs coexist without aliasing or cross-class replacement.

**In:** `types/enums.py`, `types/forecast.py`, `types/forecast_summary.py`,
`services/forecast_retry.py`, `store/forecast_store.py`, `db/metadata.py`, one additive
Alembic revision, `protocols/stores.py`, relevant `tests/fakes/` forecast store and
`docs/plans/327-a-forecast-cycle-cannot-be-re-run.md` table amendment plus type specs.
Implement the explicit index/retry table above, class-immutable DB guard, purpose-bound
store write checks, normal history/detail/range/latest/cycle-marker defaults, and atomic
class/lineage/evidence/value persistence. Preserve ordinary retry and superseded history.

**Out:** API formatting, model execution and enabling any test writer.

**Verification:** run `uv run pytest tests/unit/services/test_forecast_retry.py tests/integration/store/test_forecast_supersession.py tests/integration/store/test_forecast_evidence_store.py tests/integration/db/test_migration_forecast_data_use.py -q`.
The last file is new. Test identical and differing values across classes, equal values
with changed test lineage, QC-only refusal, immutable class via SQL, class-local history,
old-row defaults and migration downgrade safety. Explicit-status queries must not bypass
class filtering. Keep normal selected-but-superseded access semantics intact.

**Pre-change:** add asymmetric same-natural-key test/standard regressions that demonstrate
current ID reuse/supersession, plus a same-output/changed-lineage test that currently
returns IDENTICAL. New class construction/migration tests fail until implemented.

#### T1b storage implementation boundary

The storage slice uses migration `0069` and an **unconditional** PostgreSQL refusal
of `EXPIRED_RATING_TEST` forecast INSERTs, including the owner and COPY. It adds no
activation flag, session override or runtime/operator write grants. The existing
provisional-input permission is not output-deployment authority. T1c must replace
this refusal through separately reviewed inventory-backed activation; until then
there is no production test-output writer.

Typed canonical consumed lineage retains dynamic source snapshots, consumed station
static attributes, existing immutable provisional fingerprints and actual contributor
IDs with explicit transformation versions. It is mandatory for TEST, absent for
STANDARD and persisted with values/evidence. The database derives the immutable
source-station/tenant join, including legitimate same-tenant cross-station inputs.
Completeness remains the future T3 assembler contract, not a claim inferred from
nonempty caller metadata. No assembler, model/FI, publication or direct-reader sweep
is delivered by this slice. No raw lineage is added to public responses.

Structural tests remove only the dormant refusal trigger inside disposable PostGIS
transactions, restoring it by rollback (the nonempty downgrade fixture restores it
before commit). Those tests are distinct from unmodified owner/runtime-role guard
tests and do not provide a production activation capability. Existing STANDARD retry
rows 1–4 and superseded historical access stay intact. See Plan 327's amendment for
the post-QC/pre-IDENTICAL test-lineage supersession rule and conflict handling.

### T1c — Rejection, publication and deployment activation guards

**Outcome:** no test write before authorized compatible deployment, and no test result can
become a normal publication through HTTP, a direct store call or direct runtime-role SQL.

**In:** `types/rejected_forecast.py`, `store/rejected_forecast_store.py`,
`store/forecast_publication_store.py`, `api/routes/forecast_publication.py`,
`db/metadata.py`, additive migration/role grants, new narrowly scoped
`cli/forecast_test_activation.py` and activation store/domain support, corresponding
fakes, `docs/standards/security.md` and `docs/standards/cicd.md`. This task owns the
DB gate schema/operator command/inventory record and default-denied write triggers.
Rejected numerical failures retain test classification; they are not storage for all
numerically passing test forecasts. Runtime API/workers cannot activate their own gate.

**Out:** turning the gate on in staging or weakening valid publication decisions.

**Verification:** new `tests/integration/db/test_forecast_test_activation.py` and
`tests/unit/cli/test_forecast_test_activation.py`; run
`uv run pytest tests/integration/db/test_forecast_test_activation.py tests/unit/cli/test_forecast_test_activation.py tests/integration/store/test_rejected_forecast_store.py tests/integration/store/test_forecast_publication_store.py tests/integration/store/test_forecast_publication_concurrency.py tests/unit/api/test_forecast_publication_api.py -q`.
Assert default-denied writes, missing/mixed inventory denial, role restrictions,
immutable class, QC-passed test publication refusal and valid publication races/selected
superseded behaviour unchanged. SQL trigger assertions run on PostgreSQL, not fakes.

**Pre-change:** tests create a numerically passed invalid candidate and demonstrate that
current publication rejects none of its scientific use facts; guarded test insertion
must fail until an authorized activation record exists. Do not activate live systems.

### T1d — Close ordinary-reader and evaluation escape paths

**Outcome:** every ordinary read path excludes test output and cannot reveal provisional
input or restricted evidence; no indirect baseline, component or skill consumer leaks it.

**In:** bounded inventory and edits in `api/routes/forecasts.py`, `api/routes/tables.py`,
legacy export routes, `services/training_data.py`, `services/hindcast.py`,
`flows/compute_skills.py`, `services/onboarding.py`,
`services/component_derivation.py`, `services/observation_alert_checker.py`,
`services/forecast_preservation.py`, `ops/publication_backup_health.py`,
`services/forecast_lab/db_sources.py`, `api/routes/dashboard.py`,
`api/routes/stations.py`, `services/nepal_demo.py`, `cli/export_nepal_demo.py`,
`ops/evidence_backup_worker.py`, `ops/protected_evidence_backup.py`, backup/restore
queries and `scripts/restore-rehearsal.sh`, plus tests and `docs/touchpoint-maps.md`.
Inspect direct SQL against forecasts/observations/rejections/model_states in these
surfaces before edits. Backup preserves BOTH classes and protected lineage; valid
publication proof/health counts only legitimate publication selections. Admin audit
views may inspect clearly labelled invalid records, not present them as ordinary data.
Record closure in this task's execution notes, not a second permanent ledger/schema.

**Out:** unrelated API redesign, new monitoring platform or raw evidence publication.

**Verification:** add synthetic canary cases to existing training/hindcast/component/
observation-alert tests and `tests/integration/services/test_forecast_data_use_isolation.py`
(new). Run `uv run pytest tests/unit/services/test_training_data.py tests/unit/services/test_hindcast.py tests/unit/services/test_component_derivation.py tests/unit/services/test_observation_alert_checker.py tests/integration/services/test_forecast_data_use_isolation.py -q`.
Bounded source inspection must account for every direct forecast-table reference in
`src/`, `scripts/` and backup/restore tooling, with file/line and safe default recorded
in the PR; tests exercise bypasses rather than merely count references. The listed
files are starting points, not a closed inventory: include `ops/watchdog.py`,
`ops/evidence_backup_host.py`, `store/forecast_preservation_store.py`,
`flows/collect_bafu_forecasts.py`, `cli/register_deployments.py` and `api/__init__.py`
where the bounded sweep finds a relevant reader. Standard watchdog freshness must not
be refreshed by test-only production; test availability is reported separately. Include
old by-ID/historical/explicit-status paths, ordinary state read and backup round trip.

**Pre-change:** failing canary tests demonstrate existing unfiltered forecast reader
paths; physical provisional-input exclusion is a new-store regression from T1a.

### T2a — Explicit observation preparation and bounded backfill

**Outcome:** selected bound DHM onboarding gauges can ingest and QC real levels without
becoming operational or affecting normal Swiss eligibility.

**In:** `config/dhm.py`, `flows/ingest_observations.py`,
`services/station_qc_overrides.py`, an explicit preparation subcommand in
`cli/onboard_nepal.py`, associated config/type seams and
`docs/operations/nepal-station-onboarding-runbook.md`. Reuse adapter fetch, water-level
cursor, bounded window/page policy, idempotent storage and historical QC context. Add
explicit `since`/`until` backfill bounds independent of the normal latest cursor. The
eligibility selection must also reach QC applicability, not only fetch. Missing bindings,
unapproved network/tenant/station status and incompatible units are station holds.

**Out:** changing ordinary scheduled eligibility, adopting guessed candidate IDs or
writing provisional Q. Unknown-reference measured levels may be stored unchanged;
this grants no conversion readiness. Live invokes require T0 authority.

**Verification:** run `uv run pytest tests/unit/adapters/test_dhm.py tests/unit/config/test_dhm.py tests/unit/flows/test_ingest_observations_dhm.py tests/unit/services/test_station_qc_overrides.py tests/unit/cli/test_onboard_nepal.py -q` (add the CLI module tests if absent).
Add onboarding-selected-with-QC, unchanged-status, unselected/foreign rejection, bounded
backfill behind a newer cursor, repeated poll and recovered-history tests. Default
operational eligibility and Swiss tests remain unchanged.

**Pre-change:** an explicitly selected ONBOARDING gauge currently fails the operational
eligibility filter; new test must fail on missing level/QC output before the change.

### T2b — Verified-reference expired-curve conversion

**Outcome:** only QC-passed, reference-compatible, in-domain genuine levels produce
isolated provisional Q with unchanged expiry and immutable lineage.

**In:** new `services/provisional_discharge.py`, station-scoped newest-curve store method
in `store/rating_curve_store.py`, preparation CLI wiring, T1a stores and runbook.
Reuse `RatingConverter` without changing ordinary conversion semantics. Use explicit
chronology/version ordering, fail ambiguous candidates, and refuse BELOW/ABOVE range
flags rather than accepting clamped endpoints. Snapshot QC and transformation facts.

**Out:** ordinary discharge writes, curve-date edits, guessed datum, private data exports.

**Verification:** add `tests/unit/services/test_provisional_discharge.py` and run
`uv run pytest tests/unit/services/test_provisional_discharge.py tests/unit/services/test_rating_conversion.py tests/integration/store/test_rating_curve_store.py tests/integration/store/test_provisional_discharge_store.py -q`.
Cover newest chronology versus version, same-gauge constraint, unchanged validity,
unknown/mismatched reference, QC_UNCHECKED/RAW rejection, out-of-range refusal, restated
measurement new immutable version, idempotent identical conversion and no normal-Q write.

**Pre-change:** contract tests must fail because no provisional conversion service/write
path exists; preserve converter's existing endpoint-clamping unit tests unchanged.

### T3a — Purpose-scoped model inputs and no-state composition

**Outcome:** test inference reuses real assemblers/model declarations with isolated
provisional Q and full-lookback provenance; it cannot touch ordinary model state.

**In:** `services/operational_inputs.py`, `services/track_assembly.py`,
`types/forecast_evidence.py`, `services/forecast_evidence.py`, a narrow provisional
operational reader, `services/run_station_forecast.py` and scoped composition in
`flows/run_forecast_cycle.py`. Name the existing flow dependency-injection parameters
`obs_store`, `forecast_store`, and the model-state store as the entry seams. Explicit
purpose/authorized station selection is separate from ordinary OPERATIONAL filtering;
prepare tenant-safe assignments through existing assignment/artifact APIs after T0
qualification, never by spoofing status. Default invocation remains unchanged.
Use existing per-track assembly for incompatible forcing modes, preserving declarations.

**Out:** new FI state architecture, model retraining on provisional data, changing
CMAL configs/declarations or silently allowing stateful test models.

**Verification:** run `uv run pytest tests/unit/services/test_operational_inputs.py tests/unit/services/test_track_assembly.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_station_forecast_per_track.py tests/unit/services/test_run_station_forecast_fanout.py tests/unit/flows/test_forecast_evidence.py tests/integration/store/test_model_state_store.py -q`.
Add older-tainted/newest-clean, resampled lineage, same-time source conflict, missing
mandatory lineage, state-producing fake refusal BEFORE result persistence, write-guard
backstop, subsequent ordinary state unchanged and default-call equivalence tests.

**Pre-change:** old taint is lost by numeric pivot/resampling and shared state would be
read/written; new asymmetric tests fail on those behaviours before scoped composition.

### T3b — Independent group-member outcomes

**Outcome:** anticipated per-member input/static/future shortfalls do not erase healthy
members or silently shrink expected coverage.

**In:** `services/run_group_forecast.py`, its narrow flow catch boundary in
`flows/run_forecast_cycle.py`, relevant input metadata types, tests and Plan 514 overlap
note agreed by the agents owning the overlapping work. Preserve the owner decision restated above: declared
past gaps reach FI max_nan with quality flags; undeclared statics do not poison stacking.
No model-contract, global QC or database-error suppression changes.

**Out:** unrelated Plan 514 telemetry scope or independent parallel implementation of 514.

**Verification:** run `uv run pytest tests/unit/services/test_run_group_forecast.py tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py -q`.
Add asymmetric good/bad members for missing/short future forcing, missing declared
static, undeclared null static and anticipated assembly failures. Preserve expected
roster and reasons; maintain per-station metadata, no sibling taint for independent
predictions, fatal persistence errors still raise. Upstream FI contract checks apply.

**Pre-change:** one short-future member currently returns an empty whole group; replace
that expectation with a failing asymmetric regression, not only both-short tests.

#### T3b local implementation evidence (2026-10-01; not deployment acceptance)

- Based on `origin/main` `eb806843`. The ten asymmetric RED cases failed before
  production edits (short future, undeclared mixed/null statics, missing declared
  statics, empty past forcing and anticipated assembly shortfall).
- Focused group service + FI-resolver flow gate: 67 passed, including real FI
  `max_nan` refusal/tolerance, pre-fill quality preservation, expected-roster
  retention, healthy-member persistence, and fatal DB/persistence controls.
- The installed locked FI is 0.1.20; aquacast 0.1.356 at `5460f898` declares
  CMAL-small past Q/P/T lookback 30 with `max_nan=0`; future P/T `max_nan=0`,
  AT_MOST horizon 10 with minimum 1. No declarations were changed.
- The time grid is materialized only for absent declared past-forcing columns or
  empty past-forcing frames. Required past-target history is identified only by
  explicit past-known declarations, not output names; absent required target
  rows/columns exclude that member, never manufacture a null target history.
  The FI boundary projects `required_past_targets` from actual past-known target
  names; `None` preserves native unspecified behavior and an explicit empty set
  means weather-only inputs. Forcing-only `declared_lookbacks` stays unchanged. Nonempty complete-schema histories retain original rows and missing-row
  quality evidence; length/shape shortfalls remain model-owned. A real-FI regression
  delivers sparse rows unchanged, then a fake model returns an explicit per-entry
  failure while its healthy sibling succeeds. No universal gap-padding claim.
- Only `InsufficientDataError` is recovered as a defensive anticipated-assembly
  contract; no current operational assembler raiser is claimed. Real exclusions
  are static/future/binding/cadence/missing-required-target checks.
  Configuration/schema/programming faults remain visible group failures, not
  silently relabelled shortages. `StoreError` and shared-policy connection-fatal
  errors (including raw driver failures) remain fatal; other database errors
  remain visible group failures under the existing policy.
- Unresolved model-contract limitation: the in-repo aquacast shim's `predict`
  converts all stations before invoking the inner model and converts a single
  bad-area error to whole-run `ModelFailure`. A synthetic healthy-area/zero-area
  pair reproduces this; healthy alone succeeds. FI requires per-entry failure
  when another entry can run. A separate model-compliance repair owns this defect;
  T3b does not hide it with SAP3 gating or claim universal healthy-member isolation.
- No live access, restricted-data reads, state activation, deployment or consumer
  acceptance was performed. Whole-group telemetry and latest-attempt persistence
  remain separate work. Plan 514's delegated tasks are not independently delivered.

### T3c — Output propagation, rejected results, combination and alerts

**Outcome:** every test-produced or test-dependent output remains invalid across all
runners, actual contributing combinations and in-memory alert paths.

**In:** `services/run_station_forecast.py`, `services/run_group_forecast.py`,
`services/forecast_combination.py`, `flows/run_forecast_cycle.py`, forecast/rejected
result types and tests, and corresponding type/touchpoint documentation. Persist through
purpose-bound stores. Check model-returned state before output persistence. Union actual
consumed contributor lineage (exclude zero-weight or parameter-absent noncontributors),
retain exact IDs, and partition alert inputs before pooled/BMA reconstruction.

**Out:** changing numerical model/combination algorithms or manufacturing uncertainty.

**Verification:** run `uv run pytest tests/unit/services/test_forecast_combination.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_group_forecast.py tests/unit/flows/test_run_forecast_cycle.py tests/unit/flows/test_run_forecast_cycle_rejected_capture.py tests/unit/flows/test_run_forecast_cycle_resume.py tests/unit/services/test_alert_checker.py -q`.
Assert QC_PASS/new issue cannot cleanse class; rejected path retains class; mixed
actual contributors yield invalid; unused contributors do not affect lineage; invalid
never reaches PRIMARY/POOLED/BMA alerts; completed stored results remain readable despite
sibling failure. Keep standard output and alert contracts unchanged.

**Pre-change:** existing combination rebuilds RAW/QC state and alert preparation reads
ensembles without scientific class; new test-mode propagation/alert canaries fail there.

### T3d — Bounded latest-attempt state

**Outcome:** report safe latest attempt outcome separately from last-good forecast,
without mistaking absence, an old cycle-health record or a crash for success/failure.

**In:** new `types/forecast_attempt.py`, `store/forecast_attempt_store.py`, metadata and
additive migration, cycle write seam and focused tests. Owns the latest-only projection
schema and conditional writer keyed by station/model/parameter/use-class. This task
also attaches T1c's shared activation-guard function to the new attempt relation and
owns its PostgreSQL disabled-gate/runtime-role test; T1c cannot attach a trigger to a
relation that does not yet exist. Existing cycle health says only any-forecast-produced and cannot answer this contract; reuse its
safe-reason vocabulary, not its aggregate as a fabricated per-model verdict. No history
analytics. Writes ordered by cycle/attempt identity; older concurrent runs cannot replace
newer outcomes. Success references a committed same-class forecast; failed persistence
cannot mark success. A crash leaves unfinished/unknown/stale state honestly.

**Out:** generalized monitoring platform, raw exception text or output values in status.

**Verification:** add `tests/integration/store/test_forecast_attempt_store.py` and
`tests/unit/flows/test_forecast_attempt_reporting.py`; run
`uv run pytest tests/integration/store/test_forecast_attempt_store.py tests/unit/flows/test_forecast_attempt_reporting.py tests/unit/flows/test_run_forecast_cycle_resume.py -q`.
Cover out-of-order/concurrent attempts, both use classes, success/result mismatch,
rollback, no-prior-data and crash-in-progress plus a healthy sibling. Direct test-attempt writes
while activation is disabled must be rejected by the new relation's DB trigger.

**Pre-change:** new contracts fail against absent projection; show existing aggregate
cycle success cannot distinguish failed and successful model siblings.

### T4a — Scoped current/test reads and restricted-safe diagnostics

**Outcome:** all effective models have class-disjoint current/last-good/status entries,
and no consumer/reviewer read path leaks provisional inputs or restricted diagnostics.

**In:** `api/routes/api_stations.py`, `api/routes/api_forecasts.py`,
`api/routes/api_rejected_forecasts.py`, `api/schemas.py`, a shared safe projection at the
API boundary, store latest-query seam and tests. Union station/group assignment visibility
without foreign member leakage; reuse real representations and needed station-bound basin
metadata. Preserve selected superseded valid generations. Invalid by-ID access requires
explicit permitted class selection; test reads fail closed for publication-gated tenants.
Never use defaults that lose sparse >7-day last-good or models behind history pagination.

**Out:** Swiss Forecast Lab widening, new browser secrets, UI implementation or raw
lineage snapshots/levels/curve points/coefficients/baseline statistics in responses.

**Verification:** run `uv run pytest tests/unit/api/test_api_stations.py tests/unit/api/test_api_forecasts.py tests/unit/api/test_api_rejected_forecasts.py tests/unit/api/test_security.py tests/integration/api/test_access_token_auth.py -q`.
Add current-route tests, all-model station/group inventory, missing assignment/result,
older sparse last-good with >200 other-model rows, both representations/cadences,
freshness clock, class-specific next-read visibility, revoked/foreign/empty scope, gated
tenant denial and unchanged standard publication. Use synthetic canary diagnostics to
prove all old/new paths redact restricted/provisional detail, including role variants.

**Pre-change:** new route and class selectors fail against current surface; existing
unredacted forecast/rejection details expose canary observation-derived statistics.

### T4b — Versioned consumer contract and map-agent handoff

**Outcome:** exact additive API semantics are documented/versioned and agreed with map
agents; their UI/proxy implementation is neither performed nor claimed here.

**In:** `docs/spec/api-v1-map.openapi.json`, its generator/version gate, API specification,
`docs/operations/nepal-flow-map-demo.md`, `docs/touchpoint-maps.md` and contract tests.
Record route/query authorization, data-use/invalid reasons, IDs/shape/cadence/freshness,
unavailable/attempt semantics and withheld fields. Coordinate existing map proxy allowlist
and existing selector/view; no copied selector implementation or new plotting features.

**Out:** edits to the separate map repository, false consumer approval or deploying keys.

**Verification:** `uv run pytest tests/unit/api/test_map_contract.py tests/unit/tools/test_check_map_contract_version.py -q`; use the documented native generator and drift command
from those tests: `uv run python tools/generate_map_contract.py` after the additive
contract-version change, and `uv run python tools/check_map_contract_version.py --base-ref origin/main`.
The equality test above is the drift gate. Bounded handoff receipt identifies the map
agent/PR that accepts the contract and their remaining proxy/browser tests. Without that
receipt, record a coordination hold, not completed end-to-end delivery.

**Pre-change:** new route/query/schema makes the existing committed contract drift; the
updated contract must close that failing deterministic gate without weakening it.

### T5 — Authorized deployment and separate valid/test pathway acceptance

**Outcome:** target-host operation and consumer integration are demonstrated, with actual
valid and invalid coverage counted separately and the service configured beyond October 7.

**In:** owner-authorized staging deployment and bounded onboarding using
existing reviewed paths, backup prerequisites before restricted transfers, scoped secrets
and no inbound office exposure. Preserve Swiss operations and restrict Nepal snow/forcing
schedules as required by existing onboarding. The cloud overlay's disabled forecast feed
must be replaced by verified Nepal configuration, not inherited Swiss defaults. Reuse
legitimate artifacts/training history and confirm actual group bindings and requirements.
Coordinate map agents' explicit invalid display and authenticated reloading, not UI coding.

**Out:** production deployment, new spending, destructive replacement, premature operational
claims, changes to curve validity or accepting unresolved gauge references.

**Verification:** record at least two real cycles and next-read visibility with traceable
stored IDs/times/class; six-station/model matrix including failures; authorized/unauthorized
browser checks with map agents; failed refresh/production retains correctly labelled
last-good results. Zero valid coverage remains zero. Invalid tests establish pathway
behaviour only. Verify exclusion from alerts/training/evaluation and continued schedules,
monitoring/recovery/retirement handoff. Backend and browser evidence remain distinct.
No delivery/landing claim from merged code, API health or model onboarding alone.

**Pre-change:** N/A for authorized operator steps; use deterministic regression tests for
configuration/wiring changes before deployment. At this task's start, inspect and
record exact API/worker/deployment/operator/scheduled-job image identities and rollback
support, verify the T1c default-denied DB guard, then activate only through the authorized
inventory-recording command after old jobs are drained. This is the concrete rollout
exit gate, not a statement that every service looked healthy. Never activate while T0
has an unresolved safety/authority hold. T2/T3 development tests can proceed without
live T0 inputs; their operator invocations cannot.

The final code gate is `uv sync --frozen --extra aquacast`, followed by
`uv run pytest tests/ --ignore=tests/integration/live -o addopts="" -m "not deployment and not deployment_destructive and not live and not live_lindas and not live_stac and not live_recap" -q`
(all hermetic tests, including slow tests; explicit override of the repository's default
slow-test deselection), `uv run pre-commit run --all-files`, and all required GitHub checks after
the final change. Live tests require separately authorized services/credentials and the
bounded T5 acceptance evidence. Every new integration test uses disposable testcontainers,
never a staging database. Per-task commands above are focused gates, not substitutes
for this final full-suite requirement.

## Delivery and review gates

One agent owns each reviewed implementation pass. Slice PRs along the phases once the
design checkpoint is resolved, preserving safe default-off operation until prerequisites
are met. Every code PR updates affected docs and includes the currently mandated patch
bump. Every implementation PR carries the canonical Effort URL as a neutral reference,
never an auto-closing instruction. Required independent reviews and owner merge apply.
Keep detailed review reports local, concise outcomes/unresolved findings on PRs.

The root updates the sole authoritative delivery comment only after merged target effects,
current tests and end-to-end evidence match the approved vision; leave the Effort open for
`land-ticket`. Holds and partial coverage are recorded honestly, not marked delivered.

```json
{
  "phases": [
    {
      "id": "prerequisites",
      "tasks": [
        "T0"
      ],
      "parallel": false
    },
    {
      "id": "isolation",
      "tasks": [
        "T1a",
        "T1b",
        "T1c",
        "T1d"
      ],
      "parallel": false
    },
    {
      "id": "conversion",
      "tasks": [
        "T2a",
        "T2b"
      ],
      "depends_on": [
        "isolation"
      ],
      "parallel": false
    },
    {
      "id": "forecasting",
      "tasks": [
        "T3a",
        "T3b",
        "T3c",
        "T3d"
      ],
      "depends_on": [
        "conversion"
      ],
      "parallel": false
    },
    {
      "id": "reads",
      "tasks": [
        "T4a",
        "T4b"
      ],
      "depends_on": [
        "isolation",
        "forecasting"
      ],
      "parallel": false
    },
    {
      "id": "acceptance",
      "tasks": [
        "T5"
      ],
      "depends_on": [
        "prerequisites",
        "forecasting",
        "reads"
      ],
      "parallel": false
    }
  ]
}
```


### Partial storage/conversion implementation boundary

The protected-storage slice covers part of T1a, prerequisite fail-closed T1c guards
and pure/transactional T2b conversion only. It does not activate or complete those
tasks. It adds the narrow `MeasurementFeedEvidence` prerequisite: historical
`ObservationSource.MEASURED` and current adapter bindings cannot establish a row's
feed identity. Authorized T2a work must later attest exact measured-value snapshots
to a verified endpoint/API station identity. A restatement needs new feed evidence;
QC-only reprocessing snapshots its new QC generation without re-attesting the feed.
Proof/association recording, configured-rule verification, full activation inventory
and all CLI/flow wiring remain held. No runtime or delivery-operator grant is added.
