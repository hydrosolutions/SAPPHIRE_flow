# Prepare a fresh operational comparison

Use this guide with deployment support to prepare a separate temporary system
from an existing deployment. Keep the existing system running unchanged. The
procedure is currently **preparation-only**: a reviewed private execution runbook
and its runtime qualification are still required before installation.

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
to create paused jobs. Pause and read back every deployment and schedule before workers
start. Account for target-created pending runs explicitly. On partial failure,
leave workers stopped and pools paused; inspect existing target state before retry.
Repeated registration must refuse unapproved drift rather than silently overwrite
parameters or reset schedules.

## Reconstruct inputs, then commission

Prepare a bounded input manifest before transferring data. Use existing
[station onboarding](../operations/new-station-by-hand.md) and
[trusted artifact import](../operations/model-artifact-import-runbook.md) procedures
where they preserve the comparison contract. Record changes in QC, identifiers,
lineage or artifact provenance. Where ordinary import cannot preserve the required
inputs, review an exact input-only export/restore and a synthetic relational test
first. A full database clone is not this procedure.

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
