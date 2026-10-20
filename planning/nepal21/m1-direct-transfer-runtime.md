# M1 — direct-transfer release delivery and recoverable test runtime

status: DRAFT

Related Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

Authority: [confirmed vision](../visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md).
Status/checkpoints: [milestone map](milestones.md). M1 preparation is authorised,
but this implementation plan needs independent review and owner merge. No new
application code or server change is made by publishing these documents.

## Outcome and scope

Two accepted, tested application images run on Infomaniak through the intended
Compose stack. The API and Prefect workers operate against the intended test DB.
CI transfers accepted releases privately without a registry or server rebuild;
activation requires an explicit request. One verified private Cloudflare recovery
set and the previous local application release provide measured recovery/rollback.
No station activation, scientific reclassification, model execution or unintended
scheduled work belongs in M1. Preserve existing archive DB and local dashboards.

The owner retains release publication and PR merge authority. Automatic transfer
is downstream of that accepted-release event, not every merge or PR build. Keep
model correctness/data-input changes in their appropriate onboarding work unless
a concrete runtime compatibility prerequisite requires a separately reviewed fix.
Do not import the whole demonstrated source snapshot into this runtime change.

## Baseline and source touchpoints

Read these before implementation; recheck current main and actual host state:

- `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`: two image variants,
  optional Aquacast dependency, build secrets, tests/scans/SBOMs and public artifacts.
  The inspected base is Python 3.14.6; the local model demonstration was Python 3.12.10.
  Actual image imports/runtime need verification, not presumed compatibility.
- `tools/release_identity.py`, `tools/image_identity_probe.py`,
  `tools/compose_release_identity.py`, `tools/release_target.py`: owner-issued release
  identity, local image receipts, source labels and exact Compose consumption.
  Existing local image IDs are not registry digests. Extend these contracts rather
  than introducing a second release authority or relying on mutable tags alone.
- `docker-compose.yml`, `docker-compose.nepal-cloud.yml`,
  `config/overlays/nepal-cloud.toml`, `docs/deployment/nepal-cloud-host.md`,
  `docker/entrypoint.sh`, `docker/init-db.sh`, `docker/bootstrap-roles.sh` and SQL:
  intended topology, persistent storage, role separation, migrations and init order.
- `src/sapphire_flow/cli/register_deployments.py`: actual deployments, cron overrides,
  skip controls and work pools. Init registers deployments; workers must not consume
  queued default/Swiss work before commissioning checks.
- `src/sapphire_flow/flows/backup.py`, `src/sapphire_flow/ops/protected_evidence_backup.py`,
  `src/sapphire_flow/ops/evidence_backup_worker.py`, `src/sapphire_flow/ops/watchdog.py`;
  `docs/decisions/2026-09-30-dhm-data-hosting-assumption.md`, `docs/standards/security.md`,
  `docs/standards/cicd.md`, `docs/standards/orchestration.md` and the deployment
  touchpoint map. Reconcile stale examples with source; do not invent new pools or
  framework layers because a standards table describes future capabilities.
- Existing protected-preservation and data-use guards: ordinary backup behaviour
  may not preserve the protected archive. Explicitly reconcile permitted private
  recovery with those guards; do not broaden ordinary worker/API access to copy it.

M0 found only the isolated archive DB, with migration label 0071; both schema and
role/trigger compatibility remain to be proven. Fresh pg_dump completed during
preparation, but integrity/restore/off-box verification is outstanding. Equal
migration labels and exit code zero are not recovery acceptance.

## A. Release/export/transfer design and focused changes

1. Inspect existing CI/public logs/artifacts and private dependency acquisition.
   Keep private code/images/dependency inventories out of public outputs; do not
   widen credentials or use a personal login as a routine build/deploy mechanism.
2. Use one explicitly authorised protected release job to build both linux/amd64
   variants on standard free runners. Run relevant image/import/security checks
   on the exact immutable IDs exported; no second build after successful proof.
   Normal PR checks remain, but do not promote unrelated PR-built images.
3. Export a compressed image archive with a manifest bound to accepted source,
   release receipt, both local image IDs, source labels, archive size and SHA256.
   Combining both variants may reduce duplicated layers; choose after measuring
   peak build/export disk usage. Resume only identical accepted bytes; reject
   conflicting attempts to overwrite a release mapping.
4. Transfer privately with pinned host identity and scoped release-only credentials.
   Prefer a restricted incoming-file account, without Docker/database privileges.
   Keep activation separate. Write under temporary names, verify size/hash against
   the authenticated receipt, and mark complete atomically. Incomplete/conflicting
   transfers cannot be activated; a checksum alone is not source authentication.
5. Keep archives and full private release evidence off public Actions artifacts and
   releases. Audit metadata/log exposure as well as secret strings. No untrusted
   PR receives source-dependency or transfer secrets.
6. Measure free-runner capacity and network/host footprint. If it does not fit,
   stop and return an existing-resource alternative; no paid runners, registry,
   cache expansion, public upload or change to scientific dependencies as a bypass.

Evidence: exact tested/exported IDs and manifest; private transfer receipt;
interrupted/corrupt/conflicting replay rejection; safe identical retry; credential
and public-output review; measured disk/archive/transfer usage. Use local/fake tests
for failure cases, not destructive experiments against the live archive.

## B. Recovery integration

1. Inspect existing Cloudflare account usage and permissions. Use a dedicated private
   recovery location, not the dashboard's public asset path. R2 Standard is the
   intended existing-service fit; validate account access and current free terms.
2. One recovery set contains the DB, current application images, required model
   artifacts, protected configuration and consistent manifests. During M1 some
   model stores may legitimately be empty; inventory them explicitly. Preserve
   delivered source datasets separately; one rolling backup is not permission to
   delete custody/evidence archives. Resolve restore-time access to secrets and
   encryption keys without storing plaintext secrets in public receipts or making
   recovery depend on the lost server alone. Encrypt restricted objects before upload.
   Record the execution boundary and least-privilege credentials for encryption,
   upload, integrity verification and replacement before implementation. Cloudflare
   permissions must be limited to the private recovery location, not dashboard
   publishing or unrelated buckets. Do not silently give the existing backup worker
   extra image/artifact access or Docker privileges; changes require high-risk review.
   Cloudflare is the planned private off-box replica governed by the DHM hosting
   assumption: an objection/changed conditions stops new replication and invokes
   its hosting-removal/change process. Encryption does not waive those conditions.
   Inventory existing off-host custody of original deliveries and archive evidence
   separately; they are not implicitly covered by the rolling test-system set.
   Report missing preservation evidence; do not invent a second automatic replica.
3. Check available free storage/operation allowances, existing account consumption,
   complete-set size and peak old/new overlap before upload. Include concurrent
   usage/headroom; no assumption that a new bucket gets its own free quota or that
   a billing alert enforces a hard spending cap. If safe free capacity cannot be
   established, retain the good set and notify the owner without automatic spend.
4. Create a consistent snapshot, upload a candidate set, verify complete manifests,
   content integrity and restoreability, then switch the latest-set reference.
   Delete older recovery objects only after successful verification. Failed or
   interrupted replacements leave the last usable set intact. Clean failed partial
   uploads safely without deleting the good set. No historical backup retention
   after a successful replacement in Cloudflare; protected evidence has separate
   custody rules. Existing local dump retention is separate (the inspected flow
   defaults to seven artifacts and requires keep_count >= 2). Leave it unchanged
   unless measured disk limits require a reviewed adjustment; do not set keep_count=1
   to implement the off-host policy. Cleanup must preserve objects referenced by
   the newly retained set if immutable objects are shared.
5. Daily backup and pre-deployment/pre-database-change backup are the intended policy.
   Record the fresh verified recovery point before mutating a deployment/database.
   Do not start a destructive change when no verified recovery point exists.
   Monitor the age of the latest verified off-host recovery point, not just local
   dump mtime. Notify the operator through existing monitoring on failed/missed
   backups or an approaching 24-hour age; report the target as missed if exceeded.
   A daily cron declaration alone does not prove a 24-hour recovery point.
6. Rehearse isolated restore, including runtime identity, roles/guards, station UUIDs,
   RAW custody, artifacts/config and API/worker health. Target four working hours
   with at most 24 hours of newly collected data loss during initial testing.
   At least one timed rehearsal uses only the Cloudflare set and off-host secret
   channel in a clean environment denied access to the original host local state.
   Measure from operator acknowledgement during working hours to restored API/worker
   readiness; record incident detection separately, without promising 24/7 response.
   Record elapsed time, what was restored and any unmet target. Checksums/pg_dump
   success alone are insufficient. Image rollback does not undo a migration.

Evidence: private account/capacity inventory, retained-set identity, replacement
failure behaviour, isolated restoration and timed operator recovery instructions.
Public reports contain sanitized outcomes, not protected backup contents.

## C. Server commissioning and controlled activation

1. Preserve original archive DB and dirty checkout. Verify the existing fresh dump,
   perform isolated restore, and compare schema/role/guard semantics before seeding
   separate intended Compose-managed test storage. Preserve station UUIDs and RAW
   provenance; do not reacquire or reimport 1.2 million observations unnecessarily.
2. Validate effective Compose overlays, volumes, mounts, non-root service privileges,
   runtime-only secrets, resource limits and access boundaries. The inspected Nepal
   overlay disables weather forecasting; do not treat it as complete Nepal forcing.
3. On explicit activation request, verify archive/receipt identity before load and
   compare both loaded image IDs/labels with CI proof. Loading can overwrite tags:
   detect conflicts and preserve rollback anchors first. Run existing Compose
   preflight with no build or implicit pull for application images. Keep the previous
   application release locally after a successful update.
4. Start infrastructure/init with workers held. Inspect registered schedules and
   queued runs before enabling workers; pause production/ingestion/training schedules
   for commissioning and avoid Swiss/default work. Use SAPPHIRE_SKIP_DEPLOYMENTS
   to omit non-applicable jobs on this host; it does not pause/delete pre-existing
   registrations or queued runs. Register intended commissioning deployments with
   workers held, pause their schedules through Prefect, and reconcile queued runs.
   Verify held state across init re-registration and restart, not only first startup.
   If registration reactivates a schedule, use a durable reviewed hold (including
   omitting future re-registration where appropriate), keep workers held, and pause/
   reconcile existing schedules and queued runs after every init. Skipping a name
   alone never proves an already registered job is held.
   Cron overrides are the source-defined SCHEDULE_* variables, including
   SCHEDULE_BACKUP_DATABASE; cron changes alone are not a safe pause mechanism.
   Verify normal API/auth, expected
   pools and a controlled non-mutating application flow, not just container health.
5. Separate the backup schedule from held forecast/ingestion work. Enable only the
   reviewed daily schedule that creates AND verifies the complete Cloudflare
   recovery set after recovery validation and explicit activation. Enabling the
   existing local-only backup-database dump is not sufficient;
   other Nepal schedules await their milestone. Do not leave backups disabled under
   a blanket commissioning pause, or enable every deployment just to start backup.
6. Verify TLS/access before exposing a public edge. Restrict operational Prefect/DB
   access; no office-network exposure. No model/forecast/onboarding write is part of
   these runtime checks. Stop for M1 owner review before M2 data work.

Evidence: exact running release identity, effective configuration (sanitized),
API/auth checks, controlled flow/worker evidence, empty/held unintended queues,
validated backup scheduling, original archive preservation and local rollback plan.

## Work dependencies and reviews

A and B planning/offline tests can proceed independently. C.1 seeding a separate
test database requires the verified original archive dump plus isolated restore,
not a circular requirement for an already-seeded full runtime backup. Preserve the
original DB unchanged and inspect every actual role/trigger/protected-data guard;
do not clear guards/markers or invent a bypass to make backup succeed. Establish
the first complete Cloudflare recovery set with tested candidate images before
application activation and later destructive changes. Its isolated runtime restore
does not enable forecasts. Final integrated recovery verifies the accepted C runtime.
Thus seed proof and full-runtime recovery proof are distinct, ordered requirements.

Use one coherent implementation plan and narrowly necessary PRs, not serial
cosmetic review loops. Independent Claude and Codex reviews are required; security,
privileges, live DB/recovery and scheduling changes additionally need the applicable
owner-commissioned high-risk review before execution. No model reviews its own work.
Keep full reports local, concise outcomes in PRs, and owner merge authority intact.

## Verification and acceptance

- Unit/contract tests: manifest/receipt/source binding, immutable conflict handling,
  transfer interruption/retry, pre-load tag conflicts, post-load identity mismatch,
  rollback preservation, free-capacity refusal and replacement integrity.
- Backup tests: inconsistent/incomplete sets refused, previous good set survives
  upload/verification/delete failures, only verified successor can retire it,
  role/data-use custody maintained and secrets recoverable only through authorised
  channels. Each set must contain the artifacts referenced by its DB snapshot;
  mismatched artifact/config snapshots cannot be called a complete recovery set.
  Test missed-backup/stale-off-host notifications and the initial set as well as
  successor replacement; no predecessor exists for the first successful upload.
- CI/deployment tests: protected release trigger, no PR credential access, private
  outputs, no implicit server rebuild/pull, paused unwanted schedules/queued work,
  daily complete off-host backup independently enabled, held state across repeated
  init/restarts and queued-run reconciliation, and application API/worker smoke.
- Run native focused tests, lint/format/type checks on changed modules. Full suite
  after final code change before merge, locally or in CI; source-only checks cannot
  prove host/image compatibility. Restore/transfer tests need actual target evidence.
- Final operator report: measured free-tier footprint and build capacity; exact
  images; verified fresh dump/restore; one Cloudflare recovery set; prior local
  release; API/worker/schedule evidence; limitations and next-milestone estimate.

A missing input, secret/access capability, safe capacity margin, compatible runtime,
review or recovery proof is a concrete hold. Do not report M1 passed until all
applicable evidence exists and the owner accepts it. Estimate 3–5 active working
days, excluding owner review/merge and external access waits; refine from measurements.
