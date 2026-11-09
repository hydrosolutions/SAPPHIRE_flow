# Bound local backup storage and duration

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/481
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/482

## Outcome

Keep local backup storage and elapsed time under control during development,
without losing scientific history or disrupting forecasts. Deliver the smallest
adequate improvement to the existing backup paths, not a backup platform. This is
the first of two contained Efforts; exact replay retention belongs to Effort 483.

Each deployment needs explicit, configurable limits for retained backup copies
and temporary storage, and a measured, practical backup operating envelope.
Investigate the actual causes of repeated bytes and slow runs before changing
scope or defaults. Prefer existing configuration and mechanisms over new code.
Do not assume that a selective dump is faster or that seven copies are the cause.

## Current repository starting point

These are source facts, not a claim about any running host:

- `src/sapphire_flow/flows/backup.py` makes a full custom-format `pg_dump`.
  It already has copy-count retention (default seven; the current flow requires
  at least two), a filesystem lock, stale owned-temp cleanup, free-space
  headroom checks, a two-hour dump timeout, validation and atomic publication.
  Its published-file validation is not proof of a successful restore.
- `src/sapphire_flow/cli/register_deployments.py` registers `backup-database`
  on the dedicated backup pool. Its default schedule is nightly at 02:00,
  configurable through `SCHEDULE_BACKUP_DATABASE`. `docker-compose.yml` gives
  only the dedicated backup worker and bootstrap component the backup secret.
- `src/sapphire_flow/ops/evidence_backup_host.py`,
  `evidence_backup_worker.py` and `protected_evidence_backup.py` provide a
  separate protected-evidence path: full dumps, verified runtime image archives,
  disposable restores, manifests and forecast/publication attestations. It
  deliberately does not prune completed bundles. Pending manifests can support
  reconciliation; they are not automatically disposable temporary files.
- `docs/standards/cicd.md`, under “Protected forecast-evidence backup”, documents
  the separate-device and preservation safeguards. Existing proof references
  may depend on older dumps, manifests or image archives. A newer full dump
  alone does not establish that they can be removed. Keep these safeguards;
  this Effort does not commission the archival/supersession work described there.
- Reuse `scripts/restore-rehearsal.sh`, `tests/unit/flows/test_backup.py` and the
  protected/mixed restore tests under `tests/integration/ops/`. The mixed tests
  cover preserved content and restricted principal boundaries. Existing checks
  are a starting point, not measurements of deployment-sized recovery.

Inspect the deployed configuration and actual paths under separately approved
read-only access before drawing host conclusions. The ordinary backup volume,
protected bundles, runtime images, temporary files and active weather cache are
not interchangeable. Historical Plans 162, 194, 208 and 048 are background,
not authority to revive USB, AWS, off-server or other old deployment assumptions.

## Contained delivery and evidence

Use one agreed representative deployment for a bounded before/after comparison.
Identify the backup paths actually in use and the main contributors to retained
bytes, temporary peak space and elapsed time. Include relevant database and
non-database dependencies; a database dump must not silently stand in for missing
model artifacts, configuration or schema/reference material.

Based on that baseline, propose concrete safe deployment defaults for the
smallest useful combination of copy count, age/space limits, temporary-file
cleanup, backup scope and execution cost controls. Reuse existing controls where
they suffice. There are no invented byte, speed or percentage targets: state the
chosen limits, why they fit the measured deployment, and any limits that cannot
be met safely. Do not turn this into open-ended research or implement every
possible control merely because it appears in this list.

Acceptance evidence consists of:

1. A bounded comparison of retained and peak temporary bytes, backup duration,
   and resource/forecast impact under comparable conditions. Record the data
   scope, schedule and selected configuration. Show the useful improvement and
   that repeated successful runs remain within the chosen copy/storage policy;
   explain any irreducible growth or safety-held evidence separately. Forecast
   availability and accepted operational behavior must remain intact.
2. One bounded restore check of representative critical records and their
   references, using existing tools in an isolated disposable environment.
   Cover observations and QC, forecasts and skill metrics, model/artifact
   history, and the schema/configuration/reference dependencies needed to use
   them. Check content and links, not merely command exit status. Preserve
   tenant isolation and existing restricted-data boundaries. Report the scope
   and elapsed time honestly; this check is not proof of full service recovery
   or deterministic model replay.
3. Concise operator documentation of the resulting limits, measured benefit,
   safe failure behavior, recovery check and remaining risks. Show how a failed
   or interrupted backup avoids replacing good evidence with partial output,
   deleting in-use files or consuming unbounded temporary space. Reuse focused
   regression checks rather than building a new test framework.

## Preservation and replay boundary

Preserve ingested observations, QC evidence, forecasts, skill metrics, model and
artifact history, necessary configuration/schema/references, and their lineage.
Rotating redundant backup copies does not authorize deleting the underlying
scientific record. Do not silently exclude tables, break referential integrity,
weaken permissions or expose sensitive exports, credentials or raw diagnostics.

Preserve the last verified recoverable evidence until its replacement is
verified. For protected bundles, also preserve every still-required proof
reference and runtime image; do not equate “newest dump” with supersession.
If those dependencies prevent a safe storage bound, report their measured cost
and the concrete gap. Do not delete them or build a separate archive to force
this Effort to appear complete. Retain locking, atomic publication, validation,
secret isolation and fail-closed preservation checks.

Copy limits and safe cleanup of abandoned backup temporary files can proceed
without waiting for Effort 483. Removing or excluding weather needed for replay
requires verified replacement evidence first. Reducing weather duplication in
backups is distinct from changing active weather-cache retention. Do not change
that cache policy or design replay artifacts/runtimes in this Effort.

The separate replay Effort targets six months for Nepal. Swiss weather grids
are limited to at most seven days; it will assess economical six-month retention
of compact exact inputs, otherwise state a seven-day replay limit explicitly.
These policies are deployment-specific, not permission here to delete weather
or assume upstream archives preserve the exact version used. Preserve already
accepted scientific and publication safeguards while that work proceeds.

## Recovery limits and stop rule

One-hour data loss and one-working-day service restoration are provisional
development targets. A few-hour restoration is a future aspiration. Report
feasibility and measured gaps for hydromet-partner discussion; nightly dumps
alone do not demonstrate a one-hour loss window. Do not treat these targets as
production guarantees or a mandate for point-in-time recovery infrastructure.
No off-server backup location exists and none is commissioned here. Local copies
do not protect against losing the server/storage device; make this risk explicit.

Stop after the minimum adequate improvement and its bounded checks. A new
framework, provider abstraction, separate archival platform, off-server service,
production hardening or broader redesign requires a separate owner decision.
Do not make all legacy full-backup work mandatory for every release. Keep the
other deployment and station-onboarding Programs separate.

This vision authorizes no host access, live changes, destructive cleanup or
production deployment. Those require separate approval. Follow current repository
rules and installed skills for implementation and review; the owner retains PR
approval and merge authority.
