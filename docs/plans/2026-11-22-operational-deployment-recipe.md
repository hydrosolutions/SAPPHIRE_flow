---
status: N1a COMPLETE; N1b–d execution remains DRAFT
created: 2026-11-22
owner: deployment orchestrator
baseline: 5dd6232ec
parent: 2026-11-22-fresh-deployment-comparison.md
---

# Reproducible operational deployment recipe

## Decision and scope

Deliver a supported, operator-assisted recipe for the first real-data comparison,
then use its evidence to decide which installation steps merit automation. Reuse
external profiles, ordered Compose files, the existing registrar and onboarding
commands. Do not extend the paused installer into an operational controller in
this slice. Its two-job checks and retained records stay unchanged.

The administrator selects one private profile and its linked runbook. That runbook
owns the complete invocation, explicit registration inputs and supplemental steps;
the administrator must not reconstruct an overlay history or remember interactive
exports. Public documentation describes the procedure; actual configurations,
credentials, schedules, station lists, model choices and evidence stay private.

This plan separates a small code repair from preparation of the concrete operational
recipe. The initial review admitted only the code repair, now complete.
Preparation of later packets is read-only/offline work; their execution still
requires review of the completed recipe, data handling and commissioning boundary.
No host creation, data transfer, registration or activation is authorized by this
document's preparation status.

## Evidence and existing capabilities

The private N0 inventory found more stored deployments than its captured Compose
invocation recreates, plus an omitted durable provider-path input. That invocation
may not be the operator's complete registration procedure. This is a reproducibility
gap, not authority to repair or re-register the existing server.

Two local, provider-free probes demonstrate a narrower public defect:

- `config/deployment_profile.py::_InstallationInput.permitted_environment` rejects
  both `SCHEDULE_REFRESH_OPERATIONAL_ERA5` and `SAPPHIRE_ERA5_WEATHER_SCOPE`.
  The schedule-key pattern excludes digits; the scope is absent from its allowlist.
- `tools/deployment_profile.py::_check_schedules` indexes the empty-environment
  defaults by every selected job name. A correctly supplied optional refresh job
  raises `KeyError`, because that job deliberately has no default registration.

The registrar already implements paired opt-in, explicit UTC, scope parameters,
pool routing and concurrency for recurring refresh. Do not duplicate that logic.
Its pure `build_deployment_specs(environment)` is the preview authority. The base
Compose init environment does not yet forward the paired refresh inputs.

The broader profile checks deliberately report operational provider/execution
coverage as pending. Fixing the above defects must not turn that pending recipe
into `PASS_OFFLINE`, or make it admissible to the paused installer/rehearsal.

## Dispatchable slices

| Slice | Deliverable / paths | Acceptance and evidence | Dependencies |
| --- | --- | --- | --- |
| N1a — optional weather preview | `src/sapphire_flow/config/deployment_profile.py`, `tools/deployment_profile.py`, `docker-compose.yml`; affected profile/reference documentation and boundary tests | Both absent preserves behavior; both present resolve the real registrar spec without a crash; incomplete pair, invalid cron and unforwarded scope/schedule refuse safely. No operational `PASS_OFFLINE` and no new runtime authority. | This plan's independent review; owner resolves any material findings |
| N1b — complete private recipe | Private profile, installation file, bounded Compose overlay and linked runbook outside Git; generic instructions in `docs/deployment/operational-comparison.md` | A clean-shell render and explicit registration inventory reproduce every required/manual job, including local forecast variants. Exact input references, mounts/readers and release identities accounted for; no secrets in preview. Synthetic Prefect proof covers first and repeated registration and a real child environment. | N0 inventory; N1a; reviewed concrete supplemental registration procedure |
| N1c — input reconstruction packet | Private bounded input manifest and dependency-ordered import procedure; link existing onboarding/model/provider runbooks | Pilot then full cohort, original provenance and time contracts, exact protected-data boundary, table/column closure where needed, target sizing and no copied execution/auth state. Dry-run or synthetic relational proof before real transfer. | N0; N1b configuration and model requirements; onboarding/weather owners' existing mechanisms |
| N1d — commissioning and cleanup packet | Private operations checklist; generic guide and parent plan status | Named operator, precise job enablement, due-run/coverage checks, finite provider budget, stop/recovery path, resource inventory and cleanup on failure. Resolve provider access and target size/cost before provisioning. | N1b/N1c completed and independently reviewed; N1a final source CI and patch review if used |

N1 is complete only when all four slices meet their gates. N1a alone is not an
operational installation qualification. N2 remains blocked on the complete N1
packet; do not silently substitute the synthetic installer qualification.

## N1a implementation contract

1. Add the two exact environment keys to the existing profile allowlist. Keep the
   schema version and all other restrictions; do not broaden the schedule regex
   or admit arbitrary environment names. No cron/scope default is invented.
2. Forward both optional values through normal Compose `init`, defaulting each to
   empty. With both absent, the registrar still creates no refresh deployment.
   A partially supplied pair must retain the registrar's fail-closed behavior.
3. Compare desired and rendered scope as well as schedule. A valid but different
   scope, or a scope/schedule dropped by a later overlay, must produce a value-free
   forwarding error. Keep the registrar as the source of parsing/selection truth.
4. Preview actual optional specs without assuming they occur in the default spec
   map. Mark an opt-in job and schedule as explicitly supplied; never describe it
   as a shared default. Include its selected/skipped disposition when opt-in inputs
   are supplied. Parameters, private scope names and user cron values stay redacted;
   show the logical setting's origin and pool, not arbitrary environment contents.
   A complete, valid, forwarded pair excluded by `ONLY`/`SKIP` is dormant: report
   it as skipped. If only the existing two supported jobs are selected, all existing
   gates may still pass; this does not qualify or enable the excluded refresh job.
   Half-pairs and forwarding errors still refuse even when refresh is filtered out.
5. Preserve unsupported-execution diagnostics, the installer's exact two-job
   inventory, all paused lifecycle guards, existing retained state and ordinary
   registrar behavior. No change to weather flow logic, first-run windows, model
   conventions, database schemas or provider access.

Tests belong in `tests/unit/config/test_deployment_profile.py`,
`tests/unit/tools/test_deployment_profile.py` and existing Compose/registrar tests
where the forwarding contract is owned. Verify actual render -> registrar spec,
not just a YAML substring. Cover neither/both/one opt-in, invalid cron, scope drift,
both `ONLY` and `SKIP` filtering (including the dormant-pair case), optional-job
provenance/redaction, unknown input rejection and
the unchanged two-job pass. An operational profile must still report uncovered
execution/provider checks and be refused by installation preparation. Run affected
lint/types/tests, then the full final code-change suite in CI. One independent
patch review precedes execution of the changed tool on a real deployment recipe.

## N1b: one durable operational entry point

Use the normal Compose route for this assisted comparison. It is separate from
`deployment_install.py`: do not manufacture a minimal profile, install it, then
mutate its frozen records/configuration into an unsupported operational stack.
Keep a fresh local database and fresh Prefect storage, local-only API access,
preloaded admitted images and existing service privilege/credential boundaries.

The private runbook must supply an exact ordered Compose list and environment
file, from a clean source matching the chosen image. Remove superseded overlays
only after comparing the effective service/configuration contracts with N0.
Before any execution, review those concrete bytes and a secret-safe semantic diff.
Do not copy the historical overlay chain and treat its length as completeness.

Record all seven baseline jobs with required/manual-only/excluded disposition.
The registrar's `build_deployment_specs` defines the six known jobs; the additional
local forecast variant uses the same flow and supported Prefect API. The private
bootstrap submits all seven with `paused=True` and inactive UTC schedules on the
first write. The ordinary registrar cannot express that initial paused contract,
so do not invoke its command and then race the scheduler to pause the result.
Keep the bounded bootstrap step private for
this comparison; do not add an agency catalogue, arbitrary-flow registry or second
public job schema. The runbook gives one maintained registration invocation that
includes both normal and supplemental steps; leaving a hidden post-install patch
is not acceptance.

Specify fresh-registration parameters, not merely preservation of parameters from
an old Prefect database. Preserve standard versus provisional data use, artifact
import/manual history behavior, concurrency and the held backup disposition. On a
repeated invocation, read back and refuse unexpected parameters/variables rather
than resetting unrelated settings. A partial registration failure leaves pools
paused and workers stopped; retry must check the existing target inventory first.

Create/verify paused pools and queues before registration, with workers stopped.
Verify all deployments and schedules remain paused/inactive after creation.
Registration itself is not activation. This initial bootstrap refuses any worker
history or flow runs, unknown/partial jobs and mismatched configuration. A matching
repeat is read-only; a pools-only state is admissible only when it matches the
paused contract. Do not use this command after commissioning. Pending-run recovery
or partial-state recreation requires a separate reviewed target-only step.

For each job compare desired -> rendered init -> registered definition -> pool,
queue and actual child process. Use the existing NHS forecast configuration checker
for its exact supported boundary; separately verify the local standard-data variant
and generic worker configuration. Stored environment alone is insufficient because
Prefect's worker environment can override job keys. Use synthetic files and real
Prefect for that proof, followed by UID/readability and parsed-config checks on the
target. No upstream forecast/configuration semantics change to make a recipe pass.

## N1c: reconstruct inputs without cloning a running service

Use existing station creation, group creation, basin import, observation loading,
trusted artifact import and assignment mechanisms where they preserve the needed
contract. Imports are not all lossless: the general observation loader creates RAW
observations and QC results; model import records an external import and promotes
the supplied artifact. Neither automatically reproduces source lineage/identity.
Record these differences before claiming equal inputs or parity; never relabel a
training/import provenance or promote a new scientific choice for this test.

The private manifest must specify each source, target, fixed station/time window,
hash/count, units/QC/data-use/provenance, required foreign keys and reader/write
authority. Sequence: fresh schema/roles and tenant -> stations and groups -> basin,
static and weather bindings -> required observations/ratings/weather -> trusted
artifact metadata/bytes and assignments -> read-only readiness/coverage checks.
Any operator role uses the existing bootstrap/grant path, never the API role or a
blanket grant. Build fresh tokens separately; keep publication and notifications off.

Where imports cannot preserve the comparison contract, prepare a bounded,
input-only export/restore specification against the exact schema before copying:
explicit tables/columns, predicates, lineage and FK closure, ID remapping,
sequence handling, triggers/role constraints, consistent snapshot/cutoff and byte
limits. Review a synthetic restore with that closure. A list of table names or a
full dump with a few deletions is insufficient. This plan does not approve any
such table copy yet. No source Prefect/auth/publication/claim/result state is part
of reconstruction; source forecast evidence remains separate comparison evidence.

Start with the deterministic climatology pilot; expand to all admitted baseline
stations and the full group-model cohort. That sequencing does not shrink the
final comparison objective. Freeze source evidence and comparison tolerances
before target results. If matching CMAL inputs cannot be reconstructed, report
observational results and the missing contract, with no numerical parity pass.

## N1d: operational responsibility and acceptance

The deployment orchestrator is the named temporary-target operator for job
enablement, monitoring, stopping and cleanup. The weather owner retains provider
policy/backfill scope; the source host remains authoritative and unchanged.
Resolve local provider login/project/region and server-creation permissions using
the owner's protected credential source. Availability of a login file is not a
successful authentication or an approved new provider destination.

Before provisioning, record the chosen Infomaniak host size, cost ceiling, timeout
and deletion inventory. Size from source disk/memory/model requirements plus image,
import, working-space and evidence headroom; no capacity claim from DB size alone.
Real data remains at the selected provider. Runtime DB/Prefect destinations must
be target-local; no source tokens, source database access or publication routes.
Use scoped provider access and a finite request budget that cannot starve source
ingestion. The owner-selected secret delivery interface stays file-based and
manager-neutral; no password-manager integration or SSO change is required.

After paused readback and input checks, enable only required jobs/schedules and
their pools/queues in a recorded order. The first rolling refresh window, provider
quota, existing source backfill coordination and weather completeness are explicit
preconditions. Observe actual due runs, including a full daily interval plus the
agreed execution allowance; no shortened cron or manual-run substitute. Confirm
stored coverage and authenticated API results, not merely successful Prefect runs.

Operational stop first pauses target schedules/pools and stops target workers;
verify active child processes have exited before stopping remaining services, DB
last. Preserve failed-attempt evidence. Recovery checks exact images/config/mounts,
DB health, unexpected pending runs and completeness before resuming; no automatic
init rerun or schedule reset. Never call the paused installer's verifier/start on
this independently commissioned route. On timeout/failure, perform the same
owned-resource cleanup as success and verify provider-side deletion of data disks,
server, temporary keys/rules and provider registrations; retain only agreed evidence.

## Documentation and review gates

N1a updates `docs/deployment/profiles.md` for the optional pair and safe preview
limits. N1b–d produce the concise administrator guide, linked from
`docs/deployment-quickstart.md`; refer to the maintained profile, secret bootstrap,
provider, station and artifact runbooks instead of duplicating them. The guide
separates supported commands from still-unqualified preparation. Implementation
history stays in plans, review reports outside Git, and private data outside public
examples. Do not edit the onboarding agent's walkthrough.

One independent Claude review completed on 22 November: no N1a blockers. Clarified
the dormant-pair outcome and explicit optional-job disposition under both filters.
A valid excluded pair does not expand execution coverage: the rendered two-job
inventory and all existing checks still govern acceptance. Keep new operational
drift checks and paused-pool creation in N1b, outside this repair. The review used
bounded code excerpts; the implementation must still verify the full parser,
filtering and real Compose boundary with the tests above. Full report stays local.

The initial review marked **N1a READY**. N1b–d execution remains DRAFT until concrete
packets are reviewed. Owner approval/merge and release admission remain separate
from orchestrator readiness. No deployment occurred in either pass.

### N1a implementation

The bounded repair now admits and forwards the two exact optional inputs, previews
optional jobs without a default-map lookup, checks scope forwarding and redacts
private schedules/parameters. A filtered-out pair is still validated and explicitly
shown as skipped. Selecting refresh remains pending and the installation preview
refuses it; neither supplied preserves the existing defaults and two-job behavior.

One independent Claude patch review found no blockers. Incorporated its advisory
cron-parity check for dormant optional jobs, dormant installation-preview coverage,
precise failure assertions and absent-pair selection regressions. Existing excluded
default-job behavior is unchanged. Final source CI passed on `bb15c9aea`, including
all unit/integration shards and installation/rehearsal gates; the owner merged
#607 as `af02ed67c`. N1a is complete. No registration, credential access or target
operation was part of this implementation. N1b–d remain DRAFT.

### N1b initial preparation (#608)

Read-only follow-up completed the launch-variable inventory. One local forecast
variant uses a command that reapplies environment overrides after the worker has
prepared the child environment; its stored environment alone is not the execution
contract. Manual-job working directory, observation concurrency and parameters
that need explicit fresh-registration values are now recorded privately. Different
workers also mount different input bytes at the same container pathname. Preserve
that service distinction; do not deduplicate by basename.

A protected candidate now contains the profile, installation inputs, consolidated
Compose recipe, per-service input references, seven-job contract and linked runbook.
It derives from the pinned source base and records intentional target differences.
The clean-environment, daemon-free render succeeds: eight services, the expected
six normal registrar jobs, no builds or external volumes, and loopback-only HTTP.
The additional local forecast was recorded as an explicit supplemental registration
requirement. The new [administrator guide](../deployment/operational-comparison.md)
describes the preparation boundary without publishing agency values.

This is not N1b completion or an offline pass. Application configuration bytes,
data-bearing rating inputs and credential deliveries have not been copied; target
image admission and host-path relocation remain unverified. At this stage the
private runbook specified the missing registration behavior without an executable
invocation. The next bounded step was its review and synthetic qualification.
Review the exact configuration semantic diff before approving target execution.
N1c/N1d gates still apply before N2; no server was created or source modified in
this initial preparation pass.

One independent Claude documentation review found no blockers. Clarified image
roles, preview outcomes, paused-pool ordering, repeat-registration acceptance and
inventory reconciliation. The named provider/model terms and inventory counts
were already in the merged parent/implementation plans; no private configuration
values or new agency data are added. Private bytes and runtime evidence were not
examined by this reviewer: the preparation results above are author-recorded
evidence, not independent execution qualification. N1b execution remains DRAFT.

### N1b private registration qualification

After #608 merged, one independent Claude plan review and one independent patch
review covered the private bootstrap and synthetic tests. Resolved the identified
scheduler race by submitting paused deployments and inactive schedules on the first
write, reusing the registrar's specifications and existing Prefect APIs. Kept the
helper, private inputs and full reports outside Git. The candidate's single init
invocation now includes this bootstrap and its evidence output; the revised Compose
recipe renders without launching services.

The orchestrator admitted only this bounded implementation and isolated local
qualification. All **18 private tests passed, zero skipped**, against Prefect
client 3.6.25 and server 3.6.29. Evidence covers first registration, unchanged repeat
identities/schedules, parameter and command drift refusal without writes, paused
partial failure, CLI subprocess outcomes and environment/working-directory settings
isolation. A live scheduler control generated work while all seven paused jobs
generated none. A real child without the launch wrapper read the worker's synthetic
overlay; with the wrapper it read the intended standard overlay through the
existing TOML reader. The registrar's **83 unit regressions** also passed; private
helper Ruff and Pyright checks passed.

Temporary test containers and networks were removed. The pre-existing local stack
was preserved. No cloud target was provisioned and the source service was untouched.
These tests substitute synthetic flows: they do not prove actual provider paths,
container UID/readability, agency configuration, image admission, station/model
state or forecasts. N1b target execution remains DRAFT. Next preparation is the
configuration semantic diff and bounded input-reconstruction manifest, followed by
container-path qualification and the commissioning/cost/cleanup packet. N2 still
requires the complete N1 gates; this is not an operational installation pass.

### N1c reconstruction inventory

Read-only source checks and local code tracing produced a private manifest covering
35 candidate input tables, their explicit columns, foreign keys, trigger inventory
and include/hold/exclude decisions. Column sets match the observed schema; nine
physical column orders differ. The initial default-only check missed an identity
column: `rating_derived_discharge.stored_order` uses an owned sequence. The C3a
amendment below supersedes the earlier blanket no-sequence finding; it does not
qualify a sequence reset or derived-data restore.
This is a selection specification, not an executable exporter or a completed restore.
No measurements, geometry, artifact bytes or credentials were copied.

The source release advanced after N0 while the schema stayed unchanged. The private
candidate still pins the earlier release; image admission and configuration rebasing
remain explicit gates. The originally admitted artifacts' hashes are unchanged in
database metadata; byte verification must repeat at transfer.
A newly present artifact does not expand the comparison: the original group model
is still the only active group assignment in the bounded readback.

The proposed reconstruction preserves domain identifiers and original training
provenance. It accounts for model-training basin versions separately from current
basins, and for artifacts that have no external-import provenance row. Tenant identity
must be seeded in a reviewed fresh-target bootstrap before ordinary provisioning;
existing delivery-operator privileges are not a general restore mechanism. Ordinary
imports that create new IDs, rerun QC or promote models cannot silently substitute
for this contract.

Protected observation/rating closure remains held: immutable captures can disagree
with corrected current observations, and complete batches can cross a selected time
window. No trigger bypass or hash rewriting is proposed. The operational weather
cache mixes inputs with execution attempts and is excluded; fresh target acquisition
does not establish equal model inputs. A zero recent-observation pilot inventory
is recorded as absence, not permission to fabricate history or weaken cycle gates.

Next bounded work is a private catalogue-only synthetic restore, then a normal
pilot-cycle check and full-group input-closure proof. The private packet specifies
tenant/FK/provenance, drift refusal, transaction rollback, runtime-reader and role
tests; protected evidence needs additional corrected-observation and complete-batch
cases. Fixed issue/input windows, finite input row/byte caps, trigger-compatible
closure, concrete role mapping and image/configuration qualification remain open.
N1c and all target execution remain DRAFT; N2 still requires the complete N1 packet.

One independent Claude review accepted these documentation changes as DRAFT after
qualifying the artifact-hash evidence. Its implementation blockers are recorded
privately: exact tenant identity checks beyond ordinary provisioning, a fixed
one-shot bootstrap role, seeded timestamp comparisons, complete constraint/index
inventory, measurable no-write retry and file cleanup. The revised private packet
addresses these and splits the next test into C1a (identity/artifact/rollback) and
C1b (remaining catalogue and runtime grants). Owner review of those dispositions
was pending at that point; no implementation or transfer had been marked READY.

### C1a synthetic catalogue qualification

The owner merged #612, settling the recorded plan-review dispositions. The
orchestrator admitted only C1a's private implementation and local synthetic test.
One independent Claude patch review identified harness issues in Docker connectivity,
full column-type comparison and failure cleanup. Resolved those before execution;
the review did not itself run tests. Code, fixtures and full review reports remain
private. No public importer or database migration was added.

All **20 checks passed, zero skipped**, using source revision
`097ccc303075599b20307442029380a637a6afd3`, its locked environment, schema `0090`
and the source's pinned PostGIS image. The small synthetic catalogue preserves
tenant/station/artifact identity, original training dates, separate training/current
basin references and absence of external-import provenance. Existing stores read
the restored records; placeholder artifact bytes are hashed, never loaded as a model.
An exact repeat preserves row values, transaction identities and audit counts.

Negative cases cover conflicting tenant identity, partial state, changed metadata
or artifact bytes, schema and geometry-reference drift, partial file writes, late
transaction failure and refused file cleanup. Commit-failure tests distinguish a
definite rejection from an uncertain acknowledgement: the latter retains bytes,
invalidates the connection and records a hold, tested both before and after a real
commit. It is not an automatic recovery mechanism. The final full private suite,
Ruff and strict Pyright checks pass; the two initial fault-injection failures and
their corrections remain in the protected evidence.

Both test attempts removed their owned database containers and networks; a separate
readback found no rehearsal resources. Pre-existing containers remained present.
Owned case artifact bytes were removed after retaining the test journals. The test
used a dedicated bridge with a loopback-only database port, not an egress-denied
network. It contacted no source deployment or provider and transferred no real data.

C1a is complete only for this synthetic subset. The C1b result below covers the
remaining catalogue and actual runtime-role permissions; owner-backed store reads
alone do not prove those grants. C2 must exercise the ordinary pilot forecast path,
and C3 must resolve the full input/trigger closure. Image/configuration/path
qualification and the commissioning packet also remain open. N1c and target
execution remain DRAFT.

### C1b catalogue dependencies and runtime-role qualification

After #615 merged, the orchestrator admitted this bounded private qualification
following one independent Claude plan review and one independent Claude patch
review. Resolved the review findings before execution: check excluded-table counts
around the restore, and exercise ambiguous weather binding under the owner role
without implying an unqualified worker write. Full reports and test code stay
private; execution evidence below is author-recorded, not reviewer-executed.

All **55 checks passed, zero skipped**, against the same source revision, locked
environment, schema and pinned PostGIS image as C1a (PostgreSQL 16.9). The fixture
covers all 20 catalogue-candidate tables, not the complete 35-table input manifest.
It adds group membership and assignments, weather/polygon bindings, thresholds,
QC baseline/regime metadata and external artifact provenance. Warm-start metadata
remains empty. Two artifact records share non-loadable placeholder bytes; this does
not qualify multi-file transfer or a working model. C1a's code and proof hashes
remain unchanged.

New connections authenticated as the actual worker and API roles read the restored
catalogue through existing stores or explicitly bounded SQL. Role attributes,
memberships and grants were unchanged by synthetic password setup. The checks prove
selected metadata writes, public-schema creation and owner-role escalation are
denied; the existing worker assignment write succeeds and rolls back. Both roles
are denied access to the existing Prefect database. Statement denials return
SQLSTATE 42501; the connection-startup driver omits that code, so evidence records
the explicit database-permission denial and successful application login separately.
These ordinary database reads do not prove HTTP authorization or tenant confidentiality.

Foreign-key and unique-constraint failures preserve catalogue state; a late invalid
group membership rolls back the combined catalogue transaction. Multiple forecast
weather sources are refused by the existing reader, not by a database uniqueness
constraint. Excluded table counts remain unchanged. Initial runs exposed fixture
issues with PostgreSQL's infinite role-expiry value and the one-station-per-basin
constraint; the corrected full suite, Ruff and strict Pyright pass without changing
production schema or grants.

Every attempt removed its owned container and network; independent readback found
none remaining. Pre-existing containers remained present, case journals were retained
and temporary artifact bytes were removed. No source deployment, provider, real data
or cloud resources were involved. C1b is complete only for this synthetic scope.
C2's ordinary pilot-cycle result follows below. C3 input closure, N1b
image/configuration/path qualification and N1d commissioning preparation remain open.
N1c and target execution remain DRAFT.

### C2 synthetic climatology pilot qualification

After #616 merged, one independent Claude plan review and one patch review covered
the bounded private test. Resolved findings about exact expected outcomes, persisted
health, failure journals, store wiring and owned-resource cleanup before execution.
The orchestrator admitted only this local synthetic qualification. Full reports and
test code remain private; runtime results are author-recorded, not reviewer-executed.

All **five integration cases passed, zero skipped**, on source revision
`1ae664d3c85c1869c52c9c338b490af01f1a6b3a`, its frozen offline environment and schema
`0090`. The same pinned PostGIS image ran PostgreSQL 16.9. The decorated forecast
cycle ran in-process with isolated Prefect and real PostgreSQL stores authenticated
as the worker role. Existing model, assembly, forecast QC and persistence behavior
were unchanged. This does not qualify a scheduled worker, release image or HTTP API.

The existing native climatology model used a small synthetic serialized artifact.
Empty history at midnight and 06:00 UTC, a 24-hour accepted-history control and the
same observations marked QC-failed each produced one forecast with five daily steps
and seven quantiles. All 35 values matched the saved artifact; the three midnight
outputs were identical. Persisted input snapshots contained one resampled day only
for the accepted-history control. Rejected observations stayed excluded; no history
was fabricated. Missing the active artifact produced no forecast and the expected
failed-cycle and persisted station-dark diagnostics.

Observed reads covered the preceding complete UTC day, plus a separate midnight-to-
06:00 freshness window for the 06:00 issue. Forecast valid times remained issue time
plus daily increments. These are the tested native model's conventions, not a new
hydrological-day or display-timezone policy. The four successful cases remained
**degraded**, with persisted fallback-only alert suppression and no alert/model-state
rows. Missing observations were logged; no distinct persisted missing-observation
flag was added. Existing runoff-only input-quality diagnostics remained visible.

The first attempt exposed an incorrect test expectation: source-checkout evidence is
marked incomplete because release-image identity is unavailable. The corrected test
requires that exact reason and verifies saved input/artifact hashes without inventing
an image digest. Complete runtime evidence remains an image-admission dependency.
The final five-case suite, **60 existing model/input-assembly regressions**, Ruff and
strict Pyright passed. Frozen C1a/C1b proof hashes remain unchanged.

Both attempts removed their owned containers, networks, processes, Prefect state and
artifact bytes; independent label readback found no remaining Docker resources.
Pre-existing containers were preserved. The Python socket tripwire was checked, but
does not cover libpq or subprocesses; this is not host-wide egress isolation. No
source deployment, provider, real data or cloud resources were involved.

C2 is complete only for this synthetic native path. Applying the result to the real
pilot still requires its admitted artifact, configuration and calendar to be checked.
C3a's bounded capture result follows below. Full C3 input/trigger closure,
image/configuration/path qualification and
commissioning preparation remain open. N1c and all target execution remain DRAFT.

```json
{"phases":[{"id":"N1a","status":"COMPLETE","depends_on":["N0"]},{"id":"N1b","status":"DRAFT","depends_on":["N1a"]},{"id":"N1c","status":"DRAFT","depends_on":["N1b"]},{"id":"N1d","status":"DRAFT","depends_on":["N1b","N1c"]}],"next_execution":"N2 only after N1a–d acceptance","preparation":"read-only/offline drafting allowed; no target mutations"}
```


### C3a capture constraints and inventory correction

After #619 merged, one independent Claude plan review and one patch review covered
this bounded local qualification. Resolved the findings on causal failure checks,
role preconditions, frozen test identities, failure evidence and owned-resource
cleanup. Full reports, fixtures and execution evidence remain private.

All **23 tests passed, zero skipped** at source `9cce7b50`, using the same pinned
PostGIS image, PostgreSQL 16.9 and schema 0090 as the earlier catalogue tests. This
includes ten new checks and thirteen unchanged repository regressions. Ruff and
strict Pyright passed. Frozen test identities matched both the execution ledger
and JUnit results; earlier C1a/C1b/C2 evidence hashes remained unchanged.

- A complete synthetic batch restored through the actual worker login with exact
  observation/capture/batch/sighting content, identifiers and hashes. Replay changed
  neither those rows' content/`xmin` nor table counts. The current-capture reader
  matched the source for both the selected window and the complete batch.
- Omitting the outside-window sighting, or that observation/capture/sighting together,
  failed at commit with the expected completeness error and rolled back. A batch's
  original provenance cannot be preserved by simply clipping its rows to a window.
- A current capture inserted successfully, but an older capture for the corrected
  observation failed its current-source guard. This rules out direct unchanged
  insertion for that case; ordered historical replay was not tested. The reused
  reader regression confirms that retained historical captures block legacy rating
  eligibility even after an uncaptured correction, so dropping them is not equivalent.
- Both cross-station sighting variants failed their composite foreign keys on insert,
  with rollback and valid controls. Actual API sessions could read ordinary
  observations but were denied all three raw capture tables. Reused regressions ran
  as the fixture owner; these role results do not establish HTTP or tenant isolation.

Schema-wide generated-column/default/sequence inspection found one such column among
all 35 candidate tables: `rating_derived_discharge.stored_order`, GENERATED ALWAYS
with an owned sequence. The recorded `station_rating_history` definition selects
highest `stored_order`; this was definition inspection, not a derived-reader parity
experiment. The private amendment references the unchanged original manifest hash.
Derived ordering, legacy ordering-basis rows and sequence restoration remain held.

Owned databases, container, network and child process group were removed; independent
readback found no labelled resources and retained the baseline resources. No source
contact, provider call, real-data transfer, cloud resource or application-code change.
This qualifies only the synthetic capture constraints, not a production exporter,
observation-version/audit reconstruction, complete protected-input closure or forecasts.

Next, C3b must resolve the full cohort's actual consumed historical/protected inputs
and ordering. C3c must establish artifact-derived FI/weather requirements, exact
windows and finite input/provider budgets. Check which dependencies are required
before designing further restoration machinery. N1b image/configuration/path checks,
N1d commissioning/cost/cleanup and N2 execution remain DRAFT.

### C3b/C3c read-only requirements investigation

After #620 merged, a bounded runtime declaration probe and aggregate-only source
queries narrowed the candidate dependencies. The probe instantiated the model
template without loading artifact bytes or contacting providers. Its configuration
hash matched the admitted group artifact's recorded import provenance; database
metadata still matched the original artifact allowlist. This establishes a declared
input contract, not artifact deserialization, byte verification or a successful run.
Exact requirements, release identities and diagnostic counts remain private.

The declared model tolerates missing target history but requires past weather and
static attributes. Its future-weather minimum and maximum differ. The selected
combined provider route supplies past weather directly for the inspected source
configuration and midnight-aligned bounds. This conclusion applies to the target
only if its release/configuration selects the same route and time contract;
copying the historical forcing table is not a demonstrated prerequisite for that
forecast path. Scheduled
historical refresh still needs its own due-run and stored-coverage acceptance.
Source and current code differ in selected future-weather admission, so the target
release and delivered horizon must be reviewed explicitly before comparison.

The diagnostic input window contains legacy derived-rating candidates with the
ordering basis that C3a identified as incompatible with unchanged reinsertion.
Candidate counts do not establish which records the runtime would consume. The
forecast reader matches recomputed fingerprints/content, whereas the history reader
uses stored ordering; one reader's success cannot qualify the other. Initial-window
captures matched current measurements, but complete-batch expansion adds captures
outside that window whose compatibility has not yet been checked. These separately
timed probes are not a consistent export or a frozen comparison window.

Next preparation must identify the consumed legacy subset and full expanded capture
closure. Legacy selection must satisfy the actual reader: approved fingerprints,
segment windows, current observation/selected-curve content equality and null
capture identity on the legacy derived row, plus applicable mode/history guards.
Then select and synthetically qualify a trigger-compatible reconstruction method.
Do not discard approved history, rewrite its ordering basis or substitute
fresh ingestion to avoid this gate. The explicitly approved bookkeeping renewal
below is a limited exception, with preserved scientific content and private
uncertainty evidence. In parallel, settle the chosen release's model
and weather contract, fixed issue windows, finite row/byte/request budgets and
member-by-member eligibility evidence. A declaration's missing-data allowance is
not permission to omit available source history. No broad importer is justified yet.

C3b/C3c are not complete; N1b–d execution and N2 remain DRAFT. No provider fetch,
artifact loading, measurement transfer, cloud provisioning or source mutation was
performed. The administrator guide now explains these dependency checks.

One independent Claude documentation review found no blockers. Clarified the
source-versus-target weather condition and the exact legacy selection predicate.
Runtime findings remain author-recorded; the reviewer checked supplied code and
documentation excerpts, not the live source. Full report remains private.

### C3b1: bounded derived-ordering rehearsal

The owner approved renewing database bookkeeping while preserving measurements,
QC, rating evidence and latest-record selection. For this temporary comparison
only, the original-order warning may remain in the private reconstruction receipt
rather than the new database. Model-performance evaluation is excluded until that
warning can be preserved durably. This does not authorize operational adoption or
claim the original chronology is known.

One independent Claude plan review and one patch review informed the private
synthetic helper and its tests; findings were addressed and reports remain local.
At source `c52c02886b570a0b59941746349339654d4fc817`, PostgreSQL 16.9/schema 0090,
all 26 frozen checks passed, with no skips, errors or failures. Ruff and strict
Pyright passed. The rehearsal used two synthetic observations and five derived
records, including migrated legacy and native records and a rejected observation.

The helper carries every scientific column unchanged and omits only generated
ordering and its basis from inserts. Single-row writes in observed source order
let ordinary database guards assign new bookkeeping. It changes no grants,
triggers, sequences or fingerprints. Tests cover the real legacy forecast-selection
helper separately from the order-sensitive history projection through an actual
API database connection, subsequent writes and worker privilege restrictions.
They do not run a forecast or establish HTTP/tenant isolation.

Receipt recovery is structurally read-only and checks committed rows, parent
content and API history before producing an atomic, non-overwriting receipt.
Tests cover late constraint failures and rollback, partial receipt writes,
unexpected existing rows, parent/projection drift and simulated committed versus
rolled-back acknowledgement loss. The latter is not a real network-fault test.
A post-commit mismatch holds the database for inspection; it cannot roll back an
already committed import. Runtime uncertainty disappears from renewed rows, as
the owner accepted; the receipt retains the original values and warning.

All owned local databases, container, network and process resources were removed;
independent readback confirmed no labelled containers/networks remained and the
driver recorded baseline resources preserved. No real-data transfer, provider
call, artifact loading, cloud provisioning or source mutation was part of this
rehearsal. Private helpers and full evidence are not public deployment commands.

This closes only the bounded ordering-method question. Full consumed-input and
recursive provenance closure, concrete snapshot/transfer qualification, chosen
release/weather requirements and N1b/N1d execution gates remain open. C3b/C3c are
not complete; N1b–d execution and N2 remain DRAFT.

### C3b2: full-window census and reconstruction specification

Prepare a bounded census before implementing a real exporter. Distinguish reader
roots, supporting evidence and actually consumed inputs. Derive aligned model
history, rejected-discharge lineage and trailing freshness windows from the chosen
release's readers and merged model requirements. Freeze issue/validation instants,
cohort and configuration together; the earlier diagnostic interval is not a trial
snapshot. Include competing derived records and actual newest-delivery selection.
Distinguish recomputed-but-unpersisted evidence, stale persisted evidence and the
final consumed days. Classify native-capture versus legacy-custody bindings and
preserve their different eligibility guards. Each table/read needs explicit covered,
checked-empty or unchecked status; totals with unchecked dependencies are lower
bounds, not complete transfer budgets.

The private companion specification defines capture/batch/sighting expansion,
commit-payload references, scope/measurement compatibility and finite refusal caps.
Count minimum complete-batch support separately from the full provenance closure;
neither authorizes discarding history. Resolve an excessive or incompatible closure
before choosing a smaller transfer contract. Original manifests and qualification
records remain immutable; use a linked amendment.

| Slice | Deliverable | Acceptance / dependency |
| --- | --- | --- |
| C3b2a — local census proof | Private fixed-schema diagnostic, synthetic graph fixtures and existing reader regressions | Multi-hop/shared-batch closure, JSON references, outside-window support, corrections, scope/cap refusal and actual reader controls. Review and admission precede implementation; no source access. |
| C3b2b — aggregate source census | Private count/size/held-reason report from one bounded read-only snapshot | C3b2a and patch review; actual selected job/worker configuration parses consistently; no substituted selector, lock-taking reader or detailed data export. Complete window and cohort, no sampling or truncated success. |
| C3b2c — composed reconstruction rehearsal | Private manifest amendment and integrated synthetic restore/receipt proof | Full table/column/trigger dependencies resolved, including remaining observation-version/provisional inputs; combine observations, batches and derived evidence with actual role reads and history/diagnostic parity. No real-data transfer authority. |

The runtime rating reader takes locks. A source read-only diagnostic must not call
it, strip its locks or reconcile source data. Qualify any narrower diagnostic
against the real reader locally and label its limits. Configuration refusal holds
the actual-route census; a constructed selector is diagnostic evidence only.
Weather/release admission, trial windows, provider budgets and commissioning still
belong to C3c/N1b/N1d. These slices do not make N2 READY.

One independent Claude plan review identified four technical gaps, now addressed:
selection categories, coverage claims, evidence-mode guards and exact mapping of
issue, validation and weather-cycle instants. The orchestrator admits **C3b2a local
qualification only**; C3b2b/C3b2c remain DRAFT. Full review and the concrete companion
specification stay private. No new implementation or runtime qualification is
claimed by this specification update.

### C3b2a: local census qualification

The private local diagnostic and synthetic suite are qualified at source
`8ba58ade3ddd1b33d3a78e4e2a7978fd2135949d`, PostgreSQL 16.9/schema 0090:
**43 checks passed**, no skips/errors/failures, after the final fixture changes.
Ruff and strict Pyright passed. One independent Claude patch review preceded
execution; findings were corrected or resolved against the actual reader code.
Full review, helpers and frozen evidence remain private.

The tested boundaries are:

- Minimum batch support versus multi-hop provenance closure, shared-capture
  deduplication, idempotence, exact/exceeded row caps and byte/round refusal.
  Derived references join capture roots. Historical measurement mismatches,
  missing parents and scope violations hold the diagnostic.
- Canonical batch evidence and commit-payload membership/counts, including exact
  batch/capture/sighting station and tenant agreement. A complete synthetic graph
  restores through ordinary database guards and worker inserts.
- A separately named read-only rating diagnostic agrees with the real reader for
  legacy, native, approved-overlap, missing-capture, historical-capture-block and
  unreconciled cases. It distinguishes stale persisted records from current
  selections and preserves held-record details. Identity/datum/custody/window,
  newest-delivery and raw six-hour occupancy barriers are exercised.
- Actual observation-store reads separate accepted history, rejected candidates
  and trailing freshness observations. Separate production controls cover measured
  precedence, conditional rejection lineage and issue/validation/weather clocks;
  these are not an end-to-end forecast or census-consumption parity result.

The occupancy test deliberately uses 1,001 synthetic observations; the other graph
fixtures stay small. A corrupt fingerprint response is injected at the read
boundary, not written past database constraints. Normal admission rejects tied
deliveries; a separate SQL-valid synthetic tie then tests the reader's defense.
Neither test disables guards or broadens grants. The diagnostic requires a
read-only repeatable-read connection, and a SQL tripwire refuses locking or writes.

All owned local resources were removed. Independent Docker readback found no owned
containers/networks; the driver recorded baseline resources unchanged and process
and temporary-state cleanup. No live source access, provider call, artifact load,
real-data transfer or cloud provisioning occurred in this qualification.

This closes the bounded local C3b2a step. It does not qualify a source runner, the
full cohort, a complete coverage manifest, observation-version/provisional closure,
target-history API parity or the composed restore/receipt procedure. C3b2b requires
actual job/worker configuration consistency and a separately admitted source-read
envelope; C3b2c and N1b–d/N2 execution remain DRAFT. Unchecked dependencies remain
explicit and their counts cannot establish a complete transfer budget.

### C3b2b: source-inventory runner

The deployment owner reports that the registered job, worker and actual forecast
child now load the intended configuration consistently. Treat this as a resolved
handoff prerequisite, then recheck exact runtime identities before any inventory.
Configuration success does not resolve the separately investigated station-input
exclusions. Keep every admitted member in the ledger; this slice does not observe
weather-dependent availability exclusions. Owner-reported exclusions remain an
external status note, not a computed census result.

The private runner specification separates three gates:

| Slice | Deliverable | Acceptance / dependency |
| --- | --- | --- |
| B0 — packet and local driver | Typed private execution packet and bounded runner around the qualified helper | First measure a bounded 365-day, multi-station feasibility case; then exact configuration/cohort/clock refusal, actual model requirement selection, aggregate-output protection, real worker-role read-only transaction and cleanup tests; independent plan and patch review |
| B1 — source preflight | Exact image/package/config/job/child and cohort readback | B0 qualification and patch review; selected model constructor inspected in the matching runtime without trained weights or provider calls; concrete packet and source envelope admitted before execution |
| B2 — aggregate census | One bounded snapshot report | B1; unchanged query/process/row/byte/output limits; full declared observation/rating/capture scope or HOLD; explicit unchecked categories and lower-bound totals |

Model history derives from the deployed declaration and selected route, not the
rating lookup allowance or recurring-ingestion window. Group and ordinary
multi-model paths retain their existing requirement-selection behavior. Inspect
only selected model constructors; broad plugin discovery can skip failures and
must not silently reduce the cohort. No trained weights, forecast run or weather
request is needed for this inventory slice.

The report separates raw roots, supporting evidence and reader results. Weather
content, artifact bytes, unresolved protected/history dependencies and final
consumed tensors remain unchecked. Configuration/job checks bracket the database
snapshot but do not make the filesystem and scheduler atomic with it. No complete
transfer budget, forecast readiness or reconstruction pass follows from this report.

One independent Claude plan review identified seven issues, resolved in the private
contract: process memory/CPU limits, non-vacuous empty-input coverage, exact
requirement/read mapping, reused-query bounds, feasibility measurement, narrower
exclusion claims and a closed output schema. Preserve the real outer rating-window
guard; splitting requests to evade it is not an accepted fix. Empty dependency-key
queries do not establish checked-empty table coverage. Failed timing or resource
qualification stops before source execution; limits are not automatically raised.

The orchestrator admits **B0 local implementation only**. B1/B2, C3b2c and
N1b–d/N2 execution remain DRAFT. Full review and concrete runtime details stay
private; no implementation or live-read result is claimed.

```json
{"phases":[{"id":"B0","status":"READY","depends_on":["C3b2a","independent_plan_review"]},{"id":"B1","status":"DRAFT","depends_on":["B0","independent_patch_review","execution_packet_admission"]},{"id":"B2","status":"DRAFT","depends_on":["B1"]}],"parent_execution":"DRAFT"}
```
