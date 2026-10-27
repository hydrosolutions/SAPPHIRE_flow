# Daily evaluation forecasts

Scheduled execution, evaluation reads and local disposable one-shot execution are
implemented behind default-off configuration. The configured full suite passed
(10139 tests); independent implementation reviews closed the repaired defects within
their stated scopes. Operational policy decisions and owner approval remain open.
Authenticated recovery is unavailable pending the maintenance design decision.
No deployment, live invocation or unattended acceptance is claimed.

## ForecastInterface group boundary

The FI adapter intentionally leaves ensemble model identity unset. The SAP3 group
result builder binds a missing identity from the authoritative assignment before
QC and before accepted or rejected payload construction. A present different
identity is an output error; daily integrity checks remain strict.

`GroupInputGate` is an optional SAP3 capability, not an FI protocol extension.
Its pure `assess_group_inputs` returns typed member input issues before prediction.
The FI adapter reports `nan_tolerance_exceeded` using the unchanged declared
`max_nan` gate. The runner excludes these members before invoking the model and
retains the issues even if prediction fails for the remaining members. An entirely
excluded batch never invokes prediction. Daily completion records these members
as `INPUT_EXCLUDED`, not `MODEL_FAILED`. Returned FI `ModelFailure` remains a
model failure for the serviceable members. Existing native group models need not
implement this capability; the `GroupForecastModel.predict_batch` API is unchanged.

## Logical issue time

`types.daily_forecast.ScheduledDailyForecast` carries the authoritative scheduled
UTC tick, not execution time. Its issue time is midnight UTC of that tick's date.
A delayed 18:00 tick still belongs to that date when execution starts after midnight.
The boundary must normalize an aware timestamp to UTC; naive timestamps are invalid.
Constructing this domain value does not itself verify Prefect scheduling provenance.
That verification belongs to the flow boundary before a scheduled claim is allowed.

`OneShotDailyForecast` distinguishes manual and replay requests. It requires an
explicit target calendar date and cannot carry a scheduled tick. These requests
must not consume scheduled attempt ordinals or count toward unattended acceptance.
Internal store support uses `OneShotDailyKey`, `OneShotDailyExecution` and
`OneShotDailySnapshot`, separate from scheduled claim types. `start_one_shot` admits
an exact UUID once; `read_one_shot` requires that UUID and full trigger/scope/date.
Common completion and failure check the exact active owner, discriminator and frozen
identity. Finalization still verifies committed output evidence in the same database.
Uncertain writes or a refused summary commit leave the execution active. There is no
automatic retry or one-shot recovery. All scheduled discovery, admission, authoritative
reads and recovery queries exclude every one-shot state.

**Runtime gate:** an internal [local explicit-date group runner](local-one-shot-runner.md)
now requires an open owned disposable output bundle. It accepts no live target or
independent writers. Different UUIDs, deployments or dates still do not isolate
ordinary natural-key writes. Real invocation, input/artifact access and retention/
export require separate authorization. No operational CLI is exposed.

## Scheduled attempt eligibility

`services.daily_forecast_dispatch.next_daily_attempt` evaluates the most recent
scheduled attempt for the same daily key. It permits the first attempt, or one
retry on a strictly later scheduled tick after an incomplete, failed or abandoned
first attempt. A completed day stays closed; a second attempt exhausts the budget.
An active owner blocks another claim regardless of elapsed time. Abandonment must
be an explicit operator-confirmed store transition, not inferred from age.

The policy alone is not a concurrency lock. `PgDailyForecastStore.claim` now
serializes the tenant/deployment/group/model/data-use/day key in PostgreSQL, reads
the latest attempt, evaluates the policy and inserts the claim in one transaction.
Migration 0072 adds unique scheduled ordinals and a partial unique active claim.
Retry claims retain the first attempt's roster, artifact and configuration hash;
callers must compare this frozen identity with current configuration before running.
Claims return only after commit. Caught failure releases the exact active owner
only after its synchronous writes have stopped. Unknown/crashed ownership stays
active. Rows cannot be deleted or altered after failure.

`finalize` locks the exact active owner, checks the stored frozen identity and
complete member/parameter accounting, then verifies exact output IDs and their
committed values, QC, artifact and scope. Ordinary rows must still be current;
identical-result reused IDs are accepted. Rejected IDs must belong to this attempt.
Produced outputs must pass the frozen output contract and evaluation integrity
checks. Only then does one transaction store the immutable complete/incomplete
summary and release the claim. A failed verification leaves the claim active.
There is no per-member merging across attempts. Reads must use one finalized
summary, and must recheck ordinary supersession before selecting its references.

Summaries carry typed input/weather provenance with explicit unknown fields.
NWP age is the captured assembly age, not recomputed from logical issue time.
Finalization checks parameter evidence against its member's provenance, and checks
ordinary metadata against the stored forecast. Rejected records retain this bounded
summary metadata without inventing STANDARD input lineage. Detailed quality flags
receive the same publication/access withholding as values in the evaluation API.

`abandon` requires an operator confirmation identifying the exact attempt and owner,
a terminal run state and stopped writers. This typed attestation is NOT an automatic
process probe or an authorization check: the future operator boundary must verify
both facts and authenticate its operator before constructing it. Abandonment is
transactional and audited, cannot rewrite finalized rows, and prevents the old owner
from finalizing. `recovery_status` records intervention across the whole scoped day;
`operator_recovered` must exclude that day from unattended acceptance even if retry
succeeds. Elapsed time alone never releases ownership.

`read_day` returns the latest attempt separately from the authoritative outcome
summary: the newest finalized complete/incomplete summary. An active or shared-failed
retry does not replace the previous summary. A finalized retry replaces it as a whole,
including explicit failures or exclusions; members are never mixed across attempts.
Both reads use one scoped database query, and JSON is parsed back into validated domain
values. References still require access, supersession and usability checks at API
selection time; an authoritative reference is not blanket permission to serve values.

`pending_keys` returns same-day active or retry-eligible scheduled records independently
of current assignments. Preflight unions these with active configured assignments
before station filtering or shared-NWP early exits. It does not backfill
older days, reopen complete days or reset an exhausted retry budget.

Runtime composition supplies bounded transaction factories, role grants, provenance
capture and scheduled dispatch. Manual/replay records are executable only through
owned local disposable output tooling. Operator recovery authentication and real
termination verification remain unavailable. No deployment configuration is activated.

## Output integrity before finalization

`assess_daily_output` checks a result against an explicit output contract: identity,
units, representation, cadence, first lead, horizon and complete member/quantile
grid. It rejects missing/duplicate cells, nonfinite values and crossing quantiles,
including crossings that have no QC flag. Unknown failed QC rules fail closed;
only `negative_value` and `range_check` failures can receive the distinct
`plausibility_flagged` evaluation classification. Missing or inconsistent QC
execution evidence is not usable. This does not change ordinary QC or publication.
The caller must independently enforce the deployment's evaluation model allowlist.

Structural `PRODUCED` and a `COMPLETE` ledger day do not mean selected, publishable,
scientifically valid, or a passing unattended acceptance day. For example, a sole
member's finite, complete discharge output can fail only `negative_value` QC. The
completion service preserves its rejected reference and can close the day COMPLETE.
If CMAL is absent from the evaluation read allowlist, the selector cannot select
that reference: it returns an ordinary fallback or explicit absence. QC is not
rewritten. Before activation, verify the intended deployment's CMAL read allowlist.
The vision's authenticated API delivery check remains a separate acceptance
requirement, alongside unattended execution and all other acceptance conditions.

The contract must come from the model's declared/configured requirements, not be
inferred from the candidate being checked. Contracts are frozen with the first
claim and retained on retry. The finalizer applies these checks after verifying
exact committed payload correspondence; the standalone classifier does not itself
certify persistence or release a claim.

## Member outcome accounting

`types.daily_forecast_outcome` represents each frozen-roster member as pending,
produced, input/configuration excluded, model failed or integrity rejected.
Exclusions and failures require bounded reason codes. Produced mappings must cover
every required parameter exactly once with distinct stored references from one
station/model/issue context. Integrity rejection requires rejected evidence;
pending members cannot carry produced references. The outcome classifier closes a
day only when at least one member produced and all others produced or were genuinely
excluded. It never treats an all-excluded group as success.

These domain checks validate accounting shape, not database existence or scientific
usability. The store finalizer checks the frozen roster, scope, exact committed IDs
and payload integrity. Input/weather provenance is recorded explicitly and checked
against ordinary stored metadata. Shared scheduled and one-shot execution capture
assembly provenance before committed completion.

## Exact rejected-output reads

`RejectedForecastStore.fetch_rejected_forecast` retrieves one exact evidence ID,
including older references outside the list page. Missing IDs and IDs of another
construction-time data-use purpose return `None`. STANDARD reads do not select
protected input lineage; test-purpose database errors retain the protected envelope.
This capability adds no API route or test-use serving permission. Callers must still
check station/tenant access, publication withholding, attempt identity and payload
integrity before rendering or selecting evidence.

## Committed rejected-output references

`PgRejectedForecastStore.write_committed` synchronously stores one rejected
assignment and returns `ForecastOutputKey` → `RejectedForecastId` for every
parameter, only after its dedicated transaction commits. Empty and duplicate
parameter sets are rejected. Connection setup uses the existing bounded factory;
lock and statement limits apply inside the transaction. No daemon or timed join
is used. Any exception propagates; a caller must not infer definite rollback from
an uncertain commit or release an owned daily claim without resolving it.

This is an optional writer capability, separate from ordinary `write_batch`.
It preserves purpose/lineage guards and does not validate outputs as usable for
evaluation. Daily finalization must verify payload integrity, tenant/attempt
scope and all required parameters before counting production. The existing
best-effort capture path and ordinary readers are unchanged.

Neither component changes ordinary model issue times or activates scheduling.


## Scheduled configuration and dispatch boundary

`DeploymentConfig.daily_evaluation` is absent/`None` by default. An explicit block
names the tenant, ledger deployment, exact Prefect deployment UUID and nonempty unique
groups. Only `cmal_small` and STANDARD data are supported. Output contracts must be
explicit declarations verified against the deployed model/configuration, not inferred
from a candidate output. Contracts declare parameter, units, representation, whole-day
cadence/first lead, horizon and exact series keys. No production block has been added.

The scheduled boundary accepts only an automatically scheduled, top-level Prefect run
of the configured deployment with an expected start time. It derives the logical day
from that UTC tick, even when execution starts after midnight. Actual start time, the
wall clock, manually supplied cycle times, manual deployment runs and subflows cannot
establish scheduled identity. The Prefect run ID becomes the daily owner identity.
Manual invocations cannot claim scheduled work. Explicit-date local one-shot
execution uses its separate identity and owned output database.


The runtime store bundle now constructs `PgDailyForecastStore` with its own
transaction factory. It uses a five-second connection timeout and `NullPool`, not
the flow's shared autocommit connection. Each operation commits or rolls back its
own transaction and closes the connection. Construction alone performs no queries
or daily dispatch. Runtime grants and flow wiring preserve this transaction boundary.


Daily output contracts preserve FI horizon semantics. `minimum_horizon_steps=None`
means an exact horizon; an explicit minimum allows a complete horizon between that
minimum and the declared maximum. Dispatch must match these bounds to the model's FI
declaration, not introduce a consumer override. Aquacast declares `AT_MOST` with a
one-step floor for relaxable configurations. The checker still requires every declared
series at every time step of the returned horizon; missing cells, holes, malformed
metadata, nonfinite values and quantile crossings remain hard integrity failures.


The installed `CmalSmall` FI adapter was inspected without a trained artifact or
prediction: it declares discharge, maximum horizon 10, `at_most`, minimum 1.
`validate_daily_model_contracts` rejects configuration that changes the model's
required targets, cadence, maximum or floor. Exact/undeclared horizons stay exact.
Candidate preparation invokes this check before scheduled or one-shot admission.


`claim_daily_candidates` coordinates already-prepared full-roster candidates with
same-day pending keys. Candidate preparation must run before station filtering and
must validate tenant/model/FI declarations and fingerprint assignment configuration.
The coordinator rejects mixed scope before writes. A new retry retains the original
roster; disappeared candidates or changed artifact/configuration/output contracts
produce an explicit configuration failure. Roster changes alone do not shrink the
retry roster: execution must account for missing original members as configuration
exclusions and must not add new members. Active ownership is never reclaimed.
The caller must supply proven scheduled identity and only execute returned claims.
The configured forecast flow invokes discovery, preparation and owned execution.


Daily attempt artifact identity may be explicitly absent when candidate preparation
finds no active artifact. The full roster can then be recorded as a failed
`configuration_unavailable` attempt, not silently skipped or run with a substitute.
No such claim is executable or eligible for output finalization. Both the store and
`ck_daily_artifact` forbid complete/incomplete summaries without an artifact. The
missing identity stays frozen on retry; later activation is configuration drift,
not permission to substitute an artifact within the same daily identity.


`prepare_daily_candidates` validates the model declaration and configured tenant,
selects active CMAL assignments, and freezes every group member before operational
station filtering. Overlapping station/model outputs across configured groups fail
configuration validation. Artifact discovery reads IDs only, not trained bytes.
Absent/ambiguous artifacts or an unsupported assignment cadence produce an explicit
unavailable candidate for failure accounting, not executable work. Inactive assignments
are omitted from new work; the coordinator still discovers their prior pending keys.

The per-assignment fingerprint includes the caller's execution/scientific configuration projection,
assignment identity/priority/cadence and model data requirements. Set-valued declarations
are sorted for deterministic hashing. The roster is kept separately so membership
changes do not silently alter the frozen retry roster. Scheduled and one-shot
preparation share the same assignment-freezing logic.


Group execution accepts an optional `frozen_artifact_id`. When supplied, it verifies
that the exact artifact belongs to the requested group/model and is still active,
then fetches that artifact by ID. Missing, foreign or superseded artifacts produce
no model execution; there is no fallback to another active artifact. Returned identity
mismatch is a store error. The normal path without a frozen ID retains existing active
artifact selection. Daily execution passes the claim's nonempty artifact ID.


Daily execution must request `GroupStatePolicy.STATELESS_ONLY`. This checks the
entire predicted batch before output QC or accepted/rejected record construction.
Any non-`None` post-state, including empty bytes, fails the whole batch with
`ModelOutputError`. This also covers members whose outputs would fail QC; their
state cannot disappear inside a rejection payload. Normal group execution defaults
to `PRESERVE` and retains its existing state behavior. Both scheduled and local
one-shot daily execution activate the stateless guard.


Daily persistence helpers validate accepted output identity, all required parameters,
frozen contracts and captured provenance before any ordinary write. Outputs that fail
these checks cannot be written as ordinary daily forecasts. The dedicated
`PgCommittedForecastWriter` commits all accepted parameters in one transaction and
returns the store's exact IDs, including IDs reused by ordinary retry policy.
`commit_daily_rejection` uses the claim ID for synchronous rejected evidence and never
also buffers a best-effort write. Both helpers return evidence only after their writer
returns, check the returned mapping, and propagate errors without releasing a claim.
Finalization independently verifies committed records. Runtime composition and
both daily execution paths use these committed helpers.


The production store bundle now includes `daily_forecast_writer`, backed by a
separate bounded transactional engine rather than the flow's shared AUTOCOMMIT
connection. Construction performs no output writes. Ordinary `forecast_store`
composition is unchanged; daily dispatch uses the dedicated writer.


`complete_daily_group` connects group results to committed evidence and whole-attempt
finalization. It rejects conflicting member accounting and post-state, preserves explicit
input/configuration exclusions, and records absent model outputs as model failures.
Outputs that pass ordinary QC but violate the frozen daily contract go to rejected
evidence rather than ordinary storage. Structurally valid plausibility rejections remain
evaluation-only. Storage/finalization exceptions propagate without releasing ownership.
Both daily execution paths call this completion service.


`capture_daily_group_provenance` preserves the assembly-captured weather age and
observation staleness for accepted, rejected and missing-output members. Accepted
outputs retain their recorded input-quality assessment and flags. Rejected/missing
outputs have unknown quality when the group runner did not assess it; this is never
reported as full quality. Runoff-only inputs have no weather reference or age.
Completion retains available provenance for assembly exclusions and model failures.


Scheduled dispatch uses the existing forecast cycle. With daily configuration present, only proven scheduled runs claim work;
configured CMAL groups never fall through to ordinary dispatch on unowned/manual ticks.
Preflight runs before station filtering, freezes full membership, and records known
shared early exits. Owned groups assemble at their logical midnight, use the frozen
artifact and stateless guard, and synchronously finalize their whole summary. Their
outputs do not enter the ordinary combination/alert buffer or best-effort rejection
buffer. Other models retain their original execution path. No live configuration is
enabled by this change. Local manual/replay execution is separate and cannot repair
scheduled acceptance.

### Read selector component

`deployment.evaluation_forecasts` is independently default-off (`None`). Its
explicit tenant/deployment/groups and STANDARD-only policy never enable the
`daily_evaluation` schedule. Only configured `cmal_small` may allow the existing
`negative_value`/`range_check` plausibility failures; QC is retained unchanged.

`services.evaluation_forecasts.read_evaluation_forecasts` takes an authorized
station, injected aware clock, optional aware valid time, and active assignments
with declared output contracts. `resolve_evaluation_assignments` unions active
station assignments and all in-tenant group assignments for the station at minimum
model priority. Daily ledger scope remains limited to configured daily groups. The caller supplies model-declared contracts, never forecast-derived
shapes. Assignments missing contracts remain visible but cannot supply fallback. The selector
checks tenant/group scope before reads, uses exact finalized references, rejects
superseded outputs, and keeps latest attempt separate from whole-summary authority.
Multiple configured groups use the lowest group ID containing an authoritative
member, without mixing parameter references across groups. Each required parameter
is selected separately. Fallbacks exclude the daily model and require current-day
ordinary usable outputs ranked by assignment priority/model ID. Coverage is half-open.

Attempt diagnostics cover this UTC issue day and the preceding day. Per-model
latest-result diagnostics also use bounded latest stored accepted/rejected reads,
then exact issue-day summary lookup. Older finalized references remain stale
metadata/values, never current selections. Unverified legacy rows expose only
stored identity and issue age; no contract or scheduled acceptance is inferred.
No matching latest attempt means not attempted; an ACTIVE latest attempt means
unfinished. Shared failure reasons and recovery status remain explicit.

The request clock is a bounded read cutoff, not a reconstructed ledger snapshot.
If either returned latest/authoritative attempt started, finished, or was recovered
after `as_of`, that whole day is unavailable for this request. Its diagnostic has
`availability: post_as_of`, `latest_state: unavailable`, no attempt IDs, member,
references or recovery status. No earlier state or no-attempt claim is fabricated.
The same rule applies to exact historical summary lookups. Other valid ordinary
fallbacks remain selectable; scope violations still fail the request closed.
Future-issued outputs remain ineligible. There is no transition-history scan.

The internal typed result is rendered only through the authenticated API serializer.
API composition authorizes before calling and applies publication withholding equally
to all records and member provenance, with no-store and scoped CORS. Ordinary readers,
publication rules and scheduling activation remain unchanged.

## Authenticated evaluation API

`GET /api/v1/stations/{station_id}/evaluation-forecasts?valid_at=<aware ISO time>`
uses the current request clock for its UTC issue day. Omitted valid_at uses that
clock; naive times return 400. Future valid_at is allowed without shifting the day.
The optional `evaluation_forecasts` deployment block enables reads independently
of scheduling; absent configuration returns 404. Only STANDARD stores/results
are accepted. Scope and station permission checks precede ledger/value reads.

The response contains per-parameter selection, exact source/ID, age, inline values,
QC and input quality, active assignments, and current/prior-day diagnostics for
this member only. Latest attempt and authoritative member summary remain separate.
References are metadata, never alternate URLs. Each exact finalized reference has
bounded `usability`: `usable`, `superseded`, or `unavailable`; legacy stored-only
identities are `unverified`. Usable means eligible under the read policy, not
coverage at the requested valid time or publication permission. A superseded
reference stays visible as diagnostic history with its old ID; it supplies no
values and is never replaced by an unfinished retry's newer output. Publication
withholding sets usability to null, like other detailed output diagnostics.
`diagnostic_window_start` bounds
attempt status only, not the separate latest-result diagnostic. Per-parameter
`daily_absence` preserves the daily-model no-output/no-coverage reason even when
an ordinary fallback wins; `absence` still describes the selected result.

Reviewer tokens in publication-gated tenants receive null values, flag details and
provenance for both accepted and rejected sources. Admin and station-review-granted
humans retain review access. All responses, including errors and CORS preflight,
are `Cache-Control: no-store`. Exact-route credentialed CORS permits GET only from
the configured human dashboard origin. Ordinary forecast behavior is unchanged.

The API ledger capability exposes only `read_day` and borrows the existing request
transaction. It grants no write rights. `get_evaluation_contracts` reads explicit
validated fallback declarations from deployment configuration. Missing declarations
keep assignments visible but unusable for fallback. Shapes are never inferred from
stored output values. Actual deployment bindings still require verification.

Map contract 2.2 adds explicit evidence authority, legacy stored identity and
independent daily absence. Example absent parameter entry:
```json
{"parameter":"discharge","selection":"absent","absence":"no_usable_output","daily_absence":"no_usable_output","selected":null}
```
A successful top-level response also carries `station_id`, `as_of`, `valid_at`,
`issue_time`, `diagnostic_window_start`, `data_use: "standard"`, `daily` and
`assignments`; see the generated OpenAPI schemas for the complete shape.


Finalization locks ordinary forecast references against concurrent supersession.
Rejected evidence is append-only and is read without a row lock: PostgreSQL row
locks require UPDATE privileges, which the worker must not receive on rejected
records. Actual-role tests verify this distinction without broadening permissions.

## Per-model latest diagnostics

`models` contains every active station/group assigned model, even without a declared
fallback contract and even when another model wins selection. It does not change
`parameters[].selection`. `freshness` is temporal, not QC success or usability:

- Ordinary `current` / `stale`: latest non-superseded, uncombined STANDARD row
  issued at or before `as_of`, on today's UTC issue day / before that day.
  One exact latest-as-of query returns at most one row per assigned model,
  independently of requested valid time and fallback selection. Equal issue times
  use highest version then ID. No history payload scan or contract inference occurs.
- Ordinary `absent`: no matching row as of this clock. `latest_result_at` is the
  row's issue time; `age_seconds` is the time since issue. This is a latest stored
  result, not a claim of scheduled success or complete multi-parameter history.
- Daily recent-attempt diagnostics retain their two-day status view when no older
  result supersedes that view. Finalized references use `evidence_authority:
  finalized_summary`; no summary means `unknown` authority.
- Daily history reads one latest accepted row and one latest rejection, scoped by
  station/model/purpose/clock (rejections also by configured groups or unknown
  legacy group). Newer rejected evidence cannot silently disappear behind an older
  accepted row; a rejection wins an equal-issue tie. Exact scheduled summary reads
  for the chosen issue prefer finalized whole-summary references. Excluded members
  cannot revive prior retry output. Historical values retain frozen-contract, QC,
  exact-ID, scope and supersession checks and never enter current selection.
- `stored_only_unverified` identifies rows without matching finalized authority,
  including pre-feature data. `stored_result` gives source, exact ID, parameter and
  issue. `latest_result_at` and `age_seconds` use that issue. `freshness: unknown`
  means scheduled eligibility is unknown, even for today's stored row. Records and
  values are empty: no shape, contract, provenance or acceptance is fabricated.
  A stored identity is not a scheduled success or unattended acceptance result.
  A rejected row can retain its group, latest attempt ID and failed state when
  its exact issue, attempt, artifact and frozen station membership match the
  bounded day read. This verifies attribution only, not output eligibility.
  An unrelated group's finalized summary never supplies authority. If the link
  cannot be verified, attempt metadata remains absent; values remain empty.

History queries return a bounded row count, not all historical payloads; there is
no Python history scan. `diagnostic_window_start: null` marks this latest-result
view as independent of the two-day attempt window. Diagnostic values use the same
publication withholding as selections, including QC/input-quality details and
provenance. Diagnostics grant no publication, alert or scientific-purpose
eligibility. Existing ordinary `fetch_latest_forecast` semantics are unchanged.


### Explicit fallback declarations

Evaluation read configuration accepts `fallback_contracts`, keyed by assigned model
ID. Each value declares the parameter, units, representation, daily cadence, first
lead, horizon bounds and series keys. The API dependency parses these declarations
without discovering plugins, loading artifacts or inspecting candidate values.
Undeclared models remain visible but are not selectable fallbacks. `cmal_small`
cannot appear in this mapping: its frozen attempt contract remains authoritative.

Source-backed native example: `linear_regression_daily` emits discharge in `m³/s`,
MEMBERS with keys 0..49, a 24-hour first lead and intended five daily steps. Native
persistence/climatology use seven quantiles (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
and intended five daily steps starting one day after issue. Actual assignment/input
horizon and artifact compatibility must be verified before activating these bindings.
No deployment overlay is enabled here.

NWP-fan-out regressions do not have a universal fixed 21-member shape or fixed first
lead: forcing member keys are preserved, and calendar-aligned future buckets give
issue-dependent leads. They remain visible but unselectable without an authoritative
compatible declaration. Do not infer keys or leads from their candidate outputs or
copy CMAL's issue-relative zero-lead contract. Use the explicit calendar anchor
below only after verifying the model's assembly and forcing binding.


The explicit `grid_anchor` declaration now supports `next_utc_midnight` for verified
calendar-aligned daily fallback models. The anchor is the first UTC midnight at or
after the trusted issue timestamp; `first_lead_hours` is then added. For zero lead,
issues at 00/06/12/18 UTC therefore expect relative leads 0/18/12/6 hours, respectively.
This is a declaration, not timestamp inference from the candidate. The default
`issue_time` preserves existing frozen contracts and native issue-relative models.
Hourly cadence cannot use the daily calendar anchor. Series keys/horizon bounds still
require explicit verified bindings; the anchor does not assume NWP ensemble size.

PostgreSQL accepted-output hydration preserves the stored model identity inside
the ensemble for both member and quantile representations. Evaluation integrity
checks use that identity; losing it incorrectly makes valid persisted output unavailable.

### Assembled API database-role verification

`tests/integration/db/test_evaluation_api_roles.py` exercises the evaluation API
with real PostgreSQL role connections, persisted daily summaries, and exact output
reads. It covers accepted/rejected results, publication withholding, retry authority
and supersession, configuration-backed fallback, and purpose isolation. Authorization
principals are injected in this proof; it is not live bearer-authentication validation.
Final results are recorded separately from full-suite and independent-review gates.


### Local disposable output runtime

The zero-argument `create_local_one_shot_outputs()` factory owns a fresh local
PostGIS container and composes every output writer and verification reader on
that database. It has no live-target mode or execution endpoint. See
[local lifecycle and limits](local-one-shot-outputs.md), including explicit cleanup,
process-lifetime evidence retention and separately authorized staging. This is
local development tooling used by the explicit-date runner, not a production
launcher or an operational CLI.

Committed accepted-output batches set transaction-local 5-second lock and
30-second statement limits before writes, matching bounded rejection/ledger operations.
A timeout rolls back the batch; it does not release ownership or authorize a retry.

Completion reads the injected finish clock after all output commits, immediately
before summary finalization. The recorded finish does not precede accepted/rejected
output persistence. Uncertain summary commits still retain active ownership.


### Scheduled cached covering weather

Scheduled daily execution first checks the selected weather cycle with strict raw
daily coverage. If it cannot serve any frozen eligible member, it may select a
previously stored ICON cycle. The query is bounded by the unchanged
`nwp_max_fallback_age_hours` (default 12 hours), measured from the actual injected
clock, not the logical issue day. Candidates follow ICON's 00/06/12/18 UTC cadence,
are never future-dated, and must cover day zero through the existing strict
assembly and FI horizon gates. A delayed run does not extend the age limit.

The newest candidate that serves members wins as one whole assembly. Missing
member inputs remain explicit exclusions; records from different cycles are never
combined. The selected older cycle is `FALLBACK`, with its actual cycle reference
and age retained. Unchanged selections retain their original classification.
Cache errors fail the attempt closed. No live weather fetch is added. Unknown
source contracts (including IFS) remain unavailable under strict daily coverage.

This resolver is scheduled-only. Explicit one-shot weather choices, ordinary
forecast ticks, input-quality thresholds, and runoff-only behavior are unchanged.

The shared executor branches on the real execution type: only a scheduled claim
uses current-clock bounded cached-cycle selection. Explicit manual/replay execution
uses its supplied cycle/source/reference directly with the same strict raw coverage
gate. Historical replay does not acquire scheduled age-selection or retry semantics.

Schema regression guards pin migration head 0072 and include the daily attempt
table in Nepal readiness before/after and rollback inventories. The immutable
scientific oracle and its protected-input qualification expectations are unchanged.

## Integration test isolation

Daily ledger and committed-output tests use `isolated_committed_db_engine`, a
session-scoped disposable PostGIS container separate from the rollback suite.
Committed rows remain available to independent readers without contaminating
existing store tests. Unique tenant and model identifiers isolate cases within
that container. Role tests retain their own bootstrapped role database. No
protected ledger records are deleted for test cleanup.
The synthetic FI boundary test commits a mixed frozen roster (healthy output,
SAP3 NaN exclusion, and FI per-entry failure), checks exact persisted references,
and compares healthy output with healthy-only execution for 1- and 10-day horizons.


### Incident isolation and persistence certainty

Daily preflight and per-claim failures do not abort the ordinary forecast tick.
The tick still runs ordinary group models, combinations, alert checks and its
freshness heartbeat. Daily failures are reported as bounded error types and
make cycle health degraded. Configuration reserves daily CMAL groups even when
the plugin or preflight is unavailable. An unconfigured CMAL group that overlaps
a reserved roster is also withheld as a whole; unrelated models are unaffected.

Each acknowledged claim is tracked immediately. A later admission failure does not
close healthy unstarted claims. An ambiguous claim
acknowledgement does not establish ownership and is never reclaimed. Completion
validates all output plans before any output write. Pure prewrite failures can
close the attempt and make the existing later-tick, maximum-two-attempt policy
available; there is no immediate retry.

The accepted-output and synchronous rejected-output writers can report a proven
rollback only after a transaction body failure and an explicit
rollback succeeds and its transaction context exits cleanly. That proof releases
a claim only if no earlier output committed. Commit acknowledgement loss, rollback
failure, context-exit failure, failures after an earlier committed output, and
uncertain rejection/finalization writes retain ACTIVE ownership. Error classes
alone, including database timeouts, are not rollback proof. One-shot UUID and
weather selection rules remain unchanged.


### Daily failure ownership and diagnostics

Preparation and admission faults are isolated per configured group. Healthy admitted
claims still execute; another group's invalid tenant, artifact or roster cannot use
up their retry budget. No invalid ledger identity is invented. Before admission,
`daily_forecast.failure` records bounded group/phase/error-type diagnostics with no
attempt ownership claim. A submitted UUID after lost claim acknowledgement is only
an investigation identifier, never permission to execute or close the claim.

An unacknowledged failure transition is not retried blindly. The controller retains
the attempt as unresolved and reports its UUID. Unknown output, diagnostic-capture,
or finalization acknowledgements likewise retain ACTIVE ownership. Ordinary work,
alerts and freshness checks continue with degraded cycle health.

Shared store failures, configuration failures, output-integrity failures and
unexpected internal failures have distinct typed attempt reasons. Carried rejected
payloads from a failed group build are validated and synchronously captured with
the owned attempt UUID. They are diagnostic evidence only: no partial accepted
outputs are saved, no member is marked produced, and failed-group members remain
pending/unknown. Unknown capture or failure acknowledgement needs operator recovery.

The retry fingerprint uses an explicit execution/scientific config projection:
daily execution settings, input quality/staleness, weather availability/age/source,
ensemble-size requirements, warm-up bounds, climatology and model priorities.
Model requirements, assignments (including priority), artifacts and declared output
contracts remain frozen separately. Read-only evaluation authorization/selection,
retention and presentation settings are excluded. Enabling evaluation reads does
not invalidate a retry; changing actual execution/input contracts does. Priority,
overlapping-group withholding and daily alert/combination policy are unchanged and
remain subject to the outstanding owner decisions.


Non-configuration preparation failures leave a group unavailable for that tick, rather than
claiming its retry as configuration drift. Confirmed removal or changed scientific
configuration still fails closed. Other healthy groups can proceed. Unavailable does
not imply temporary: a persistent non-configuration fault needs operator diagnosis.
A frozen artifact that disappears, changes ownership or becomes inactive after the
controller recheck remains a configuration failure. Missing frozen artifact bytes
remain an integrity failure. Neither case runs inference or selects a replacement.

The fingerprint excludes the deployment-wide group roster, Prefect registration
UUID and assignment creation timestamp. Per-assignment priority remains frozen.

One-shot execution returns normally after an acknowledged FAILED transition,
including prewrite evidence validation and a proven first-write rollback. Missing
artifact failure acknowledgement, output/capture acknowledgement and finalization
uncertainty raise without repeating the transition or granting retry authority.

Regression checks observe the real ordinary forecast combination call and its
result, alongside alerts and freshness records, rather than depending on Prefect
log propagation. Synthetic database write faults are scoped with unconditional
listener teardown, including when a downstream test assertion fails. Ordered
flow and persistence tests share the disposable database to detect fault leakage.
When FI returns only failed variables, the adapter retains the typed anticipated
`ModelPredictionFailure` and the legacy `no usable output` message for callers.
