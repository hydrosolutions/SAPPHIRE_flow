# Prepare a fresh operational comparison

Use this guide with deployment support to prepare a separate temporary system
from an existing deployment. Keep the existing system running unchanged. The
public procedure remains **preparation-only**: execution requires a reviewed
private runbook for the selected runtime. A disposable paused startup has now been
qualified for one recorded release; the
[implementation history](../plans/2026-11-22-operational-deployment-recipe.md#disposable-paused-startup-qualification)
records its evidence and limits. Input reconstruction and forecasting remain
separate qualifications.

The [persistent installer](profile-installation.md) supports a narrower paused,
two-job installation. It does not yet install or commission this broader
operational recipe. Do not alter its retained records or remove real requirements
to obtain an offline pass.

## Select one private recipe

Start with the [deployment quick-start](../deployment-quickstart.md). Maintain one
private profile, its installation file and a linked runbook outside the public
repository. The runbook must name the complete ordered Compose recipe and give
one maintained registration invocation, including any local forecast variants.
The administrator should not have to reconstruct shell history or patch jobs
interactively after installation.

Keep the application configuration, station/rating inputs and model artifacts
separate. Record each file's purpose, hash, reader and container path. A TOML
overlay can contain substantial data: classify its contents before treating it as
configuration. The same container path can resolve to different host files in
different services; preserve those distinctions.

Use explicit target paths and project identity. Pin the application source,
default and Aquacast application image identities, and required infrastructure
images. Consolidating an old
Compose chain is acceptable only after comparing the rendered service contracts;
keep the original inventory and a private record of intentional differences.
Preserve resource limits, commands, privileges, read-only mounts and credentials'
consumer boundaries. Re-render after relocating paths to the target host.

Credentials remain file deliveries through the existing
[manager-neutral interface](../secrets-bootstrap.md). Record logical requirements
and reader permissions, never values. Generate fresh local database credentials,
pepper and API identities; scope external provider access separately. A successful
render does not prove delivery availability or actual container readability.

## Account for every job

Build a private inventory from the required behavior and the existing registered
jobs, not only the captured Compose environment. For every job record:

- Required, manual-only or excluded disposition and its reason.
- Flow entrypoint, pool, queue, concurrency, parameters and working directory.
- UTC schedule and local display equivalent; initial deployment/schedule/pool pauses.
- Complete configuration chains, job variables, launch command and credential readers.
- Expected coverage, freshness, success evidence and recovery action.

Reconcile every source job against the desired target inventory, including jobs
excluded with a reason. A smaller target list must not hide an accidental omission.

Distinguish recurring weather refresh from fixed-window manual import and
forecast-weather acquisition. Optional refresh requires both an explicit schedule
and a weather scope; see the
[registrar reference](../standards/orchestration.md#nepal-operational-era5-refresh).
Neither an installed worker nor a successful manual import proves that recurring
ingestion is scheduled. Selecting fewer jobs also does not delete stored jobs.

Specify parameters for a **fresh** Prefect database. Preservation during
re-registration does not supply parameters that existed only on the old server.
Deployment support must include supplemental registration through the existing
Prefect API where the normal registrar does not cover a required local variant.
Do not add an agency catalogue or a second public job schema for this purpose.

## Check configuration and registration separately

Run the existing offline preview with the selected files:

```bash
uv run python tools/deployment_profile.py --profile-file /path/to/private/profile.toml check --installation /path/to/private/installation.toml --format json
```

Replace the placeholder paths with your private files. The
[profile reference](profiles.md) defines diagnostics, supported coverage and
provenance. Missing or invalid inputs produce `FAIL` (exit 1); otherwise uncovered
operational checks produce `PENDING` (exit 2). Installation preparation refuses
this broader recipe. Do not clear its real requirements to change that outcome.
Keep raw Compose output private. A syntax render and a projected job
list are useful preparation evidence, but do not establish `PASS_OFFLINE`,
successful registration, live credentials or scientific readiness.

Before approving execution, deployment support must test the complete registration
invocation against isolated real Prefect with synthetic files: first registration,
repeat registration, unexpected drift and partial failure. Check desired inputs
through rendered init settings, stored definitions and the actual worker child
process. Worker environment can override deployment variables; a custom launch
command can reapply values again. Test parsed configuration for each distinct job
path, not just stored environment strings. Reuse the
[forecast configuration checks](nhs-forecast-configuration.md) within their stated
boundary; a local forecast variant needs its own proof.

The reviewed execution runbook must create and verify paused pools **before**
registration and keep workers stopped throughout. The complete invocation,
including supplemental registration, must be repeatable without duplicates,
refuse unexpected drift, and return only after all jobs are verified paused.
Its logs must omit secrets, and its agency values must remain private.
The normal registrar may initially create active schedules; do not rely on it
to create paused jobs. The private comparison bootstrap instead reuses its job
specifications and Prefect's existing API to create paused deployments with
inactive schedules from the first write. This requires its own reviewed invocation
and synthetic qualification; it is not a new public registrar command.
Read back every deployment and schedule before workers start. Account for
target-created pending runs explicitly. On partial failure,
leave workers stopped and pools paused; inspect existing target state before retry.
Repeated registration must refuse unapproved drift rather than silently overwrite
parameters or reset schedules.

Treat initial bootstrap separately from operational updates. The private bootstrap
refuses worker/run history, partial or unknown jobs and configuration drift. An
exact repeat only checks the stored paused definitions. It cannot be used to
upgrade or repair a system after commissioning; follow its separate recovery plan.

## Verify the paused installation

The private runbook must pin the delivered image pair and matching source files,
verify their identities on the new host, and use fresh storage and local credentials.
Start the database and orchestration server, then run the reviewed initialization
and paused-registration sequence with workers stopped. Verify every required job,
queue and pool is paused, every schedule is inactive, and no flow runs exist before
starting workers. Preserve the completed init container when starting dependent
services; an explicit `--no-deps` operation requires checking those dependencies
first. Do not rerun initialization as an incidental part of starting the edge proxy.

Check configuration through the actual service user and each distinct forecast
selection. Confirm worker heartbeats, application health and API access from the
intended operator location. For server-only access, test the host loopback address:
an internal container health check alone does not prove the published port works.
Verify unauthenticated rejection, access with a fresh scoped token, and rejection
after revocation; keep token values out of logs and evidence. Recheck paused state
and zero runs after workers start.

In the qualified Docker setup, an internal-only edge network suppressed the host
port mapping. The private recipe therefore attached the edge proxy alone to an
additional IPv4 host-access bridge. Application and worker networks remained
internal, the sole published port bound to host loopback, and the provider firewall
allowed only operator SSH with no outbound access. If this topology is used,
verify the actual port and network assignments and firewall rules; a successful
Compose render is insufficient. Close temporary bootstrap egress before workers
start. Provider access for commissioning needs its own bounded authorization.

Record startup results separately from input and forecast results. An empty station
response proves authenticated API wiring, not station readiness. Successful ordinary
tenant provisioning also does not qualify the identity-preserving reconstruction
sequence below. Keep the source read-only, record concurrent source changes, and
verify deletion of every owned server, disk, address, network resource and temporary
key when the disposable test ends, including failed provisioning attempts.

## Reconstruct inputs, then commission

Prepare a bounded input manifest before transferring data. Use existing
[station onboarding](../operations/new-station-by-hand.md) and
[trusted artifact import](../operations/model-artifact-import-runbook.md) procedures
where they preserve the comparison contract. Record changes in QC, identifiers,
lineage or artifact provenance. Where ordinary import cannot preserve the required
inputs, review an exact input-only export/restore and a synthetic relational test
first. A full database clone is not this procedure.

Deployment support should give the administrator one private manifest with these
decisions settled before import:

| Input | Required decision |
| --- | --- |
| Tenant, stations, groups and bindings | Exact membership and identifiers; include referenced basin/static versions, not only current station metadata. |
| Observations and ratings | Fixed UTC windows, units, QC rules/results and complete referenced provenance; distinguish missing data from data deliberately excluded. |
| Trained models | Exact admitted artifact hashes, assignments and original training provenance; a newly added source artifact is not automatically included. |
| Weather | Required history, provider budget and acquisition method; do not copy a cache that also contains execution attempts. |
| Transfer | Named columns, dependency order, source snapshot, row/byte limits, target paths and receipt checks; refuse unresolved dependencies or exceeded limits. |

Derive that manifest from the admitted model's runtime input declaration and the
actual job's configuration. Check past targets, past weather, future weather,
static attributes, aggregation, missing-data tolerances and forecast horizon
separately. Some model routes deliberately omit stored observations whose time
periods have not been verified as compatible with the model. Record that omission explicitly; it is neither a
checked-empty input nor permission to relabel the observations. A model that tolerates missing discharge may still require complete
weather. Its minimum accepted forecast length is not a promise of the desired
operational horizon. Keep registered group membership, job eligibility and actual
forecast coverage distinct; explain every omitted member.

Confirm which weather route the forecast uses before selecting tables to copy.
Forecast-time provider acquisition and scheduled historical refresh can be separate
paths. Successful forecasts do not prove refresh is scheduled, and a populated
historical table does not prove the forecast used it. Record the selected issue
instant and input windows in UTC separately from display timezone and local-day
checks. Recheck these contracts when source and target releases differ.

Inventory observations used for diagnostics as well as model values. Rejected
discharge can contribute to rating warnings, and recent accepted observations
after the last complete model day can determine freshness without entering the
model's history. Deployment support must derive these separate windows from the
actual reader and compare its exclusions and warnings. Copying only accepted
model values can change the comparison even when the forecast numbers match.

A forecast's permitted lookup window and a scheduled ingestion window are separate
settings. Derive the inventory window from the deployed model's requirements;
neither setting proves that the required history exists. Keep unavailable members
in the report, and stop if the full declared scope exceeds the inventory limits.

Normal onboarding imports can create new identifiers, rerun QC or promote a model.
Do not describe those operations as preserving the original input history. Where
preserving identifiers is necessary, resolve tenant creation **before** ordinary
provisioning creates a different identity. This needs a reviewed bootstrap sequence;
it is not an instruction to edit existing tenant rows or widen database privileges.
Preserve model-training basin references and the absence of external-import
provenance where that is how the source artifact was created.

For inputs with capture provenance, deployment support must check complete batches,
not only timestamps. A batch can include a reading outside the forecast window;
removing its provenance can make the restore fail. An older capture may also conflict
with a corrected current observation. Do not drop that history to make import work:
it can change which readings the system accepts. Stop and resolve the reconstruction
method before transfer; fresh ingestion is a different comparison contract.

Check references inside batch records as well as database foreign keys. A capture
can occur in several batches, linking further readings and history outside the
initial window. Measure the complete linked set against the agreed station scope
and size limits. If it exceeds them, hold the transfer and resolve the scope;
do not clip the batch or silently switch to a smaller evidence set.

Require the inventory report to distinguish checked-empty inputs from inputs it
has not checked. Keep raw records, supporting evidence and selected model inputs
separate. Counts that omit unchecked dependencies are lower bounds, not a complete
transfer budget; a successful local rehearsal does not clear those omissions.

For several models, require a target-read disposition for every station/model pair:
read, deliberately omitted, excluded, or still pending. An omitted member stays
visible and must not appear as a successful empty query. Coverage of only the
queried members is a partial result, not weather availability or forecast readiness.
Deployment support must apply row and encoded-data limits to the union of all
selected observation windows before loading them, counting overlaps once. A scope
that exceeds these limits must stop rather than being split into individually
passing pieces. The integrated private runner shares one accounting ledger across
metadata, observation roots and linked evidence in its qualified local cases.
Every selected model must also load and declare its requirements within the reviewed
runtime limits. A failed constructor check leaves the inventory incomplete; it is
not evidence that observations are absent or permission to omit that model.
Deployment support must resolve that gate for the exact source runtime before
source execution. The maintained release 0.1.1197 passed its isolated constructor
and inventory-runner checks; this is not source-data acceptance. See the
[qualification record](../plans/2026-11-25-constructor-environment-guard.md#maintained-image-qualification--2026-11-26)
and [source-admission plan](../plans/2026-11-26-source-inventory-admission.md).

After an upgrade, ask deployment support to recheck which inputs the selected model
actually consumes. A newer route may use rating-derived runoff where an older one
deliberately omitted it. Verify registered settings against the actual worker and
forecast child; a run-level override or successful configuration parse alone does
not establish the executed selection. Reuse the maintained
[configuration reference](nhs-forecast-configuration.md#runtime-selection-and-consistent-configuration)
and [model-onboarding instructions](../operations/model-onboarding.md).

Deployment support has an isolated qualification of the private metadata checker.
Its result establishes configuration/process consistency only. An absent forecast
child remains unverified; do not start a live forecast merely to satisfy this check.
The source image, current input-read behavior and scientific readiness still need
their separate checks before an operational comparison.

For rating-derived inputs, request separate counts for candidates whose database
recording-time bound is at/before the forecast issue and those whose bound is later.
An older record may carry a database-upgrade time as its bound, so the latter does
not prove that the original measurement arrived late. Current revalidation also
does not reconstruct what a historical forecast consumed. Keep deliberately omitted
stored discharge visible even when the rating-input read succeeds.

The private reporting extension has passed a
[local synthetic test](../plans/2026-11-26-retained-input-inventory.md#s2-local-result--2026-11-26)
and still needs qualification against the admitted release. A completed report
describes a bounded, partial inventory; it does not establish weather
availability, whether the data can form model inputs, or restoration readiness.
The whole intended report must fit the existing
size limit; an output-limit stop is unresolved work, not permission to drop members.

Include database-generated ordering in the transfer inventory. An ordinary column-
default check can miss identity columns, and some history readers select the latest
record by that order. Preserve the required selection semantics and qualify subsequent
writes; do not assume insertion order or a sequence reset is safe. Deployment support
owns this check. The [reconstruction plan](../plans/2026-11-22-operational-deployment-recipe.md#c3a-capture-constraints-and-inventory-correction)
records the tested boundary and remaining work.

For an explicitly approved **temporary comparison**, new database bookkeeping
values may replace the originals only after a synthetic rehearsal proves that
measurements, QC, rating evidence and latest-record selection are preserved.
Keep the original ordering values, their uncertainty and the source-to-target
mapping in the required private reconstruction receipt. Renewed rows do not retain
the runtime warning that their original insertion order was unknown. Therefore
exclude model-performance evaluation until that warning can be preserved durably;
forecast operation and history selection still need their own acceptance checks.
A retained input's recording time can determine whether a forecast was allowed
to use it. Such availability evidence is outside this bookkeeping exception;
renewing it can change model inputs. Deployment support must settle and test its
preservation before admitting reconstruction on a release that uses it.

This limited exception is not a general backup/restore procedure or evidence of
the original chronology. Deployment support must verify the receipt against
committed data and actual service-role reads before admitting the target. If the
receipt is missing or a commit is uncertain, hold the target and inspect it before
retrying; do not overwrite evidence or automatically remove committed data.

Keep source and target release identities explicit. A source upgrade after inventory
requires a deliberate recipe/image review. The earlier inventory timestamp is not
a database snapshot: filtering by creation time cannot recover later-corrected data.
Use a bounded consistent export for actual transfer and separately verify file hashes.
Preserve missing inputs honestly; a successful deterministic pilot does not qualify
the full model cohort or prove that live provider inputs are identical.

Ask deployment support for the restore rehearsal's scope and failure results as
well as its success result. Identity checks must include tenant identifiers and
full column types, including geometry reference systems. An uncertain import
commit requires inspection before retry; it must not trigger automatic deletion
of an artifact that the database may already reference.

Check restored catalogue access using separate connections authenticated as the
actual worker and API database roles. An owner's successful import or read does
not prove those services can use it. Verify required reads and permitted writes,
refused privileged operations and rollback without broadening grants to make a
test pass. Database-role checks do not replace authenticated HTTP checks or prove
tenant-level confidentiality. Empty warm-start metadata and readable artifact
records also do not prove a model can run.

For the pilot, verify consumed inputs and diagnostics as well as stored forecast
values. A climatology model can produce forecasts from its saved calendar values
with no recent observations; that does not prove observation or weather ingestion
is working. Check missing-history diagnostics, QC exclusions and fallback alert
suppression separately. A source-checkout test without release-image identity also
leaves forecast evidence incomplete, even when its input snapshots are available.

Keep source authentication, Prefect state, execution claims and forecast results
out of the new system. Retain source results separately as comparison evidence.
Freeze input windows and comparison criteria before seeing target results. Begin
with a small deterministic pilot, then expand to the agreed full cohort. If inputs
cannot be matched, report an observational comparison, not a parity pass.

Before provisioning, the private runbook must settle permitted hosting, capacity,
cost/time limits, provider request budget, operator ownership and cleanup inventory.
After installation and input checks, commissioning is a separate recorded step:
enable only the agreed jobs and observe real scheduled runs, including a full daily
interval plus execution allowance where required. Check stored weather coverage,
forecast completeness and authenticated local API results. Do not shorten a cron
or substitute a manual run for schedule acceptance. Public access and cutover are
outside this comparison.

The stop procedure pauses target schedules/pools and stops workers, verifies child
process exit, then stops remaining services with the database last. Recovery checks
images, configuration, inputs and pending runs before resuming. Cleanup applies
after success or failure: remove owned temporary access, credentials, server,
disks and network resources with provider-side readback; retain only the agreed
protected evidence. Do not use the paused installer's lifecycle verifier after
independent commissioning.
