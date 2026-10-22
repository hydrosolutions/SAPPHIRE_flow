# M1 — direct-transfer release delivery and isolated test runtime

status: PROPOSAL — owner scope updated 2026-10-21; independent planning review complete; implementation approval pending

Related Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354
Authority: [confirmed vision](../visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md).
Status/checkpoints: [milestone map](milestones.md). M0 accepted; M1 active and
unaccepted. This plan update grants no implementation or deployment authority.

## Owner scope change and outcome

On 2026-10-21 the owner **deferred backup commissioning and recovery testing**,
including plaintext recovery tests. The owner also confirmed that the existing
hosting assumption covers **all 21 delivered stations, including historical
water-level records**. This is not DHM consent or scientific qualification; the
[hosting decision](../../docs/decisions/2026-09-30-dhm-data-hosting-assumption.md)
retains its restrictions and objection/changed-conditions process.

Active M1 delivers exact tested default and Aquacast linux/amd64 application images
privately after owner release publication, then commissions the existing
Compose/API/Prefect runtime only on a separate explicit deployment request. Keep
the intended architecture, FI/services/stores/normal authenticated API and existing
dashboard. No registry, paid fallback, server rebuild, model execution, station
activation or scientific-input changes. Do not import the whole demonstration
snapshot or replace the dashboard.

Preserve the original isolated archive, UUIDs and RAW provenance, original dirty
checkout on the local Mac, local dashboards and scientific evidence. Only
separately authorised **additive work in new isolated target storage** is in scope.
Do not overwrite an existing target to make setup succeed. Destructive/in-place
source changes remain held for separate protection and approval. Existing
protected-data, access and preservation/publication guards are not disabled.

There is **no verified disaster-recovery claim**. Without commissioned recovery,
loss may require lengthy reconstruction or leave state unrecoverable. Image
rollback, dump exit zero and container health are not database recovery proof.
Record actual independent delivery/evidence custody and unknowns; do not invent
copies, their locations or permission to delete them.

## Deferred, not complete and not on the critical path

The earlier full-recovery proposal required a complete verified off-host set before
target changes, independent key/capsule custody, Cloudflare free-allowance/overlap
checks, safe rotating-set replacement, complete daily/prechange backups, a fresh
verified prechange point and timed 24-hour loss/four-working-hour restore proofs.
Those **active M1/M2 gates are superseded** by the 2026-10-21 owner decision.
Backup commissioning, plaintext/timed recovery tests and their RPO/RTO acceptance
are deferred, not accepted or complete, and are not forecast/dashboard milestone
prerequisites. Cloudflare access, allowance evidence and a recovery machine are
not blockers now. M1 does not activate a backup schedule under an exception.

The historical one-private-encrypted-set/successor-before-predecessor policy stays
recorded in the vision for separately reopened work. No extra replica, paid
storage fallback or new plaintext/decrypted-secret hosting authority follows from
this deferral. Existing local backup code/retention and protected evidence remain
unchanged; do not delete or disable an existing backup as a side effect. Resuming
recovery needs separate planning/review and explicit activation, not an automatic
new dependency on M2 or a claim that recovery passed.

## Source baseline and exact extension points

Revalidate main before implementation and the host only with approved access.
Source inspection at `c7ad8cd1e0a3b65821a261a001b8e167e69ca371` found:

- `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`: two variants and
  build-secret/private dependency paths. Current base is **Python 3.14.8-slim**,
  not the earlier 3.14.6 proposal baseline; Caddy is 2.11.7. Actual amd64
  imports/entrypoint/venv isolation and scans have CI evidence, not scientific
  operation or combined cold-build/export capacity proof.
- `tools/release_identity.py::build_receipt` builds both images itself. Extend its
  builder/probe boundary; never rebuild after a separate proof to generate a
  receipt. Retain owner-only `publish`, remote tag/state validation and legacy
  receipt reading. `tools/release_target.py` retains the current per-PR policy.
- `tools/image_identity_probe.py` probes captured immutable IDs but records Docker
  `Id` as an opaque SHA256. `ops/protected_evidence_backup.py::verify_image_archive`
  already supports direct config hashes and OCI descriptor-chain reachability.
  Reuse this validator without changing protected-publication manifest semantics.
  `tools/compose_release_identity.py` currently checks an environment identity
  triple, not exact running image consumption.
- `docker-compose.yml`, `docker-compose.nepal-cloud.yml`, Nepal config overlay,
  `docker/entrypoint.sh`, `docker/init-db.sh`, `docker/bootstrap-roles.*` and
  `docs/deployment/nepal-cloud-host.md`: actual topology, secrets, role/init order.
  Normal init migrates before role bootstrap/registration; it must not operate on
  the original archive. Both target migration chain and dated archive label end
  at **0071**; no older schema or obligatory migration is established. Actual
  schema/ACL/role/trigger equivalence still needs checking.
- `src/sapphire_flow/cli/register_deployments.py`: real default/ingest/backup pools,
  cron overrides and skip filtering. Skipping a name does not pause an old
  deployment or its queued/manual/retry runs. Re-registration can affect schedules.
- Existing local `flows/backup.py`, protected evidence backup and watchdog paths
  are not complete off-host DR. They are not expanded or commissioned by active M1.

M0's 21 ONBOARDING stations, 18 RAW archives, 1,233,045 observations and absent
normal stack/basin/model/forcing/hindcast/skill/forecast integration are dated
facts, not a current-host assertion. A prior dump exit zero proves neither DR nor
current content/identity. Any authorised additive transfer must validate its own
source provenance/content and target isolation without claiming a recovery drill.

## Active feasibility checkpoint

**Read-only feasibility baseline — 2026-10-21:** main was unprotected and the
repository ruleset inventory was empty. Basic required controls are available on
the current repository tier but are not installed. The successful
repository-effective organisation Actions-secret inventory returned zero; broader
organisation-wide visibility is not a prerequisite or a reason to expand access.
These are dated observations, not enduring guarantees: revalidate effective
settings/secret scope before setup or credential admission. Exact A1 admission
and combined cold-build/export capacity remain unresolved and unapproved.

Before full implementation investment, obtain a small owner-bounded packet with
evidence date/custodian, available capability, unknowns and a reduced-scope
estimate. Existing authorised evidence first; private settings/host reads or
prototypes need their own scope. Required active inputs are:

- Provider-enforced release-tag/ref/reviewer controls and scoped build/delivery
  credentials, including retirement of equivalent ungated repository/org secrets.
  The dated baseline below must be revalidated before changes; YAML is not the
  credential boundary.
- Named existing free native-amd64 CI capacity, cold download/build/scan/export
  disk/time limits, receiver/target capacity and tested Docker image-store modes.
- An owner-approved SSH route from the selected runner, pinned host identity and
  restricted receiver custody. No assumed broad ingress or tunnel/paid fallback.
- Existing runtime/source access needed for preservation checks and additive target
  setup, without modifying original roles/guards. All-21 hosting scope is already
  owner-confirmed, not a question to reopen; identity/scientific admissibility is
  still station-specific in later milestones.
- Accepted release/config identity custody and actual original delivery/evidence
  preservation account. CI uses synthetic fixtures and private dependency code,
  never protected plaintext observations, runtime-secret capsules or raw protected
  diagnostics. No recovery-host authorisation is inferred.

Cloudflare budgets, backup keys, complete-set capacity and drill machines are
explicitly excluded from this active checkpoint. Missing active access/security/
free delivery capacity is a named hold. No automatic at-risk offline spend or
commitment to an old estimate is granted by this plan.

## A. Reviewed CI trust transition and exact private delivery

### A1/A2 — install trusted verification, then contract legacy PR credentials

Recommend the smaller expand/contract sequence: A1 installs trusted verification
and private-output/cache hardening; A2 makes PR checks secret-free and migrates
required-check settings. Initial owner trust is still required. Before choosing
this route, verify that provider controls can bound the exact owner-reviewed
repository/PR/base/head/merge/workflow/run tuple. If unavailable or declined,
HOLD; a separately selected alternative must have named authorised free resources.
Do not implement two bootstrap routes or switch automatically.

External controls must restrict canonical release-tag create/update/delete to
owner-authorised actors; release environment to approved tags; steady-state
verification environment to protected main and authorised reviewers. Inventory
actual ref semantics, self-review/admin bypass authority and available features.
Scope secrets there and retire equivalent ungated repository/org credentials
**before** A1's private run. Workflow validators remain defence-in-depth and result
binding, not authority over replaceable workflow code.

A1 has only scoped private-source build credentials, never delivery/R2/runtime or
publication credentials. Owner approves its exact final candidate before any
credential admission; changed base/head/workflow invalidates approval/proof.
Preserve substantive current gates/check names but harden their outputs/caches.
Run the full final suite, lint/format/typing and both actual amd64 image/import/
resource/entrypoint/isolation/scan gates; no skipped/degraded private coverage.
After owner merge, prove installed main verification on an approved current/no-op
candidate before A2, then expire temporary A1 grants. Failure holds, not a broad
secret reopening. Keep other credential-dependent PRs/Dependabot updates held
through this bounded A1-to-A2 transition; owner must accept the temporary disruption.

A2 uses the installed main verifier. Secret-free public static/target checks keep
exact event merge-parent/release-target validation. Trusted verification records
repository/PR/base/head/merge parents, trusted workflow commit and run/attempt;
revalidate before credentials and success. Owner checks the fresh matching FULL
private result before **every** merge. This is an owner-procedural gate, not a
main-SHA workflow check impersonating provider-enforced private PR success. Make
that responsibility explicit to the owner. Revalidate the dated absence of branch
protection/rulesets and inventory any later required names before migration;
preserve unaffected checks and all substantive full tests/image gates. Do not
claim a currently enforced check was removed, use an admin merge bypass or
green-by-skip. Record before/after settings and proof.

Current CI publishes full SBOM/SARIF and dependency tables, enables dependency/
scanner caches, and references private secrets without an in-file environment.
These are source-visible exposure paths, not a claim of disclosure or fork-secret
access. Keep mandatory proof/scans but retain raw reports, arbitrary labels,
private source/dependency evidence and subprocess errors privately. Audit public
logs/artifacts/annotations/summaries/JUnit/coverage/post-job and failure paths with
synthetic canaries. If private diagnostic custody is unavailable, fail safely;
never upload private evidence as fallback.

No configured cache publication by candidate-executing jobs, including implicit
post-job saves. Cold release build/scan/export consumes **no shared cross-run
cache**, even for public tools/advisories. A YAML ban does not prove candidate code
cannot publish cache; the release consumer must reject candidate-writable state.
Fetch verified pinned tools and authenticated fresh advisories; retain gate failures.
Trusted within-job common-layer reuse is allowed. Measure cold-run cost.

### A3 — build once, export, receive without activation

Extend existing identity tools and a narrow release-transfer tool; no new release
authority or generic deployment framework. Owner publishes through the existing
publisher only after route/settings/capacity prerequisites are verified. A merged
PR alone triggers neither release delivery nor activation.

A canonical owner-published tag starts credential-free remote tag/state/source/
target validation, followed by one externally gated cold amd64 release runner.
Build both variants once, capture exact IDs, run all actual-image and scan proofs
on those IDs, then export exact tested bytes. Inject source credentials only for
build and SSH only for final fixed transfer after fresh identity/gate validation.
Premerge cannot reach delivery credentials. This is deliberate trust in reviewed
release code, not process isolation from it. No cross-run public image artifact
bridge, second build, contents-write or publication permission.

Versioned envelope binds existing receipt hash/source/tree/tag/state, run/attempt,
complete gate results, default/Aquacast identities, archive SHA256/length and
same-source allowlisted runtime Compose/Caddy/bootstrap/config bundle digest.
Preserve raw receipt IDs; add normalized config digest/platform, reported ID kind/
store mode and verified OCI descriptor relationship using the existing validator.
Never silently redefine an old ID. Refuse unsupported runner/host mappings. Full
private dependency inventories/labels stay private; runtime secrets/data/models
are excluded. Legacy receipt reading remains, but old receipt alone cannot
satisfy new transfer proof.

Start with a compressed archive of both exact IDs to share layers. Measure peak
size/disk/time. If it cannot fit, HOLD for a bounded reviewed free-resource change
such as streaming or sequential variant export, preserving exact bytes, complete
envelope and local archive availability. No automatic paid/public cache, registry,
rebuild or protected/rollback pruning.

Use separate upload and committing OS principals. Upload is forced-command SSH,
key-only, no shell/forwarding/PTY, writes only bounded incoming staging. Committer
has no login/Docker/DB/runtime-secret/activation rights and owns accepted storage
and append-only ledger. A root-owned fixed handoff may use one exact non-root sudo
rule, never arbitrary commands/paths/environment or general root sudo. Server-owned
key/account mapping and trusted invoking identity bind authenticated arrival;
client-supplied identity or a checksum alone does not.

Committer copies bounded regular files into its **own inodes**, rejects unsafe
paths/links/conflicts, verifies envelope/hash/length/identity, fsyncs and atomically
seals; only then appends/fsyncs ledger and acknowledges. Never rename incoming-owned
bytes into trusted custody. Serialize per identity. Interrupted transfers cannot
activate; identical retries are idempotent; changed bytes cannot replace an accepted
mapping. Test crash before/after seal and ledger; no live adversarial testing.

Owner retains a small accepted release/config identity record outside receiver/
backup-writer control through existing evidence custody. This is metadata, not a
new recovery replica. Activation checks sealed provenance and raw/config/platform
relationships before load, preserves prior local release when present, rejects
tag conflicts, and assigns only receipt-authorised tags after validation
(save-by-ID may omit tags). Verify loaded AND running identities. Effective Compose
uses no app build or implicit pull; upstream infrastructure remains pinned.

## B. Additive target commissioning with durable holds

This is not backup commissioning or a plaintext disaster-recovery rehearsal.
Only after explicit deployment/target-operation authority:

1. Identify original and new target resources separately; fail if paths/DB/volumes
   alias protected originals or the target is not safely new. Leave the archive,
   source roles/guards, dirty checkout and local evidence untouched. Any permitted
   capture/transfer must retain exact byte/content identity and station UUID/RAW
   provenance; validate the copied content and schema/ACL/role/guard semantics in
   the additive target. Do not substitute a migration number or dump exit status.
   No verified off-host set, 24-hour prechange snapshot gate or recovery drill is
   required by this reduced-scope plan. That explicit deferral does not authorise
   destructive/in-place source changes or a protected-guard bypass.
2. Validate exact overlays, mounts, runtime-only secrets, least-privilege roles,
   resource limits, intended tenant scope and no office-network exposure. The
   Nepal overlay's disabled weather forecast adapter does not prove current Nepal
   forcing; qualification stays in the later scientific milestones.
3. Load only accepted exact images; run target-only setup/init with workers stopped.
   Registration must durably hold schedules/pools and reconcile ALL existing,
   skipped, unknown, queued/manual/retry work before worker admission. A typed
   normal/held policy in protected host configuration fails closed when missing on
   Nepal while preserving existing non-Nepal defaults. Use current registrar and
   pools, not new supervision. Keep skip controls for inapplicable Swiss jobs, not
   as a substitute for pausing old registrations. Unknown/in-flight work needs an
   explicit disposition, not silent cancellation.
4. Preserve held state across repeated init and restart. Backup, ingestion,
   forecast, training and onboarding schedules are not enabled in M1. Do not alter
   an already running unrelated service/backup under this plan. After reconciliation,
   held workers may provide heartbeat/read-only runtime evidence without consuming
   scientific work. Cron changes alone are not a hold; `SCHEDULE_*`/skip compatibility
   remains tested. Do not introduce a recovery-only activation mode for deferred work.
5. Prove normal API/auth/TLS, regional isolation and private DB/Prefect access before
   exposing an edge. Use read-only application checks, never a model/onboarding
   write as smoke test. Record exact identities, original preservation, target
   validation, queue evidence, local rollback limitations and unverified DR risk.
   Stop for M1 owner review before M2.

## Verification, dependencies and acceptance

Implement A1 -> installed-main proof -> A2 -> A3 -> B, with independent review,
owner merges and exact final-code tests per outcome. Deployment follows owner
publication/private acceptance and a separate activation request. No recovery
work node is an active prerequisite. Naming expresses stable release/registration/
runtime responsibilities, never ticket-shaped production APIs/modules.

- CI/identity: exact tuple freshness, external secret/ref/reviewer settings and no
  equivalent ungated secrets; mandatory full private coverage, both actual amd64
  image probes/scans, cold-cache policy and public-output canaries on failure paths.
- Transfer: immutable envelope/content/config binding, descriptor/store mapping,
  unsafe/truncated/conflicting bytes, two-principal mutation/crash/idempotence,
  tag assignment, loaded/running mismatch and preservation of prior local release.
- Runtime: rendered base/Mac-mini/Nepal Compose compatibility, role/secret consumers,
  source/target separation and copied UUID/RAW/content/guard checks; real ephemeral
  Prefect re-registration/restart with skipped/old/manual/retry/unknown work held.
  Local synthetic fixtures only in CI; no protected plaintext or runtime capsules.
- Required integration jobs provision Docker/Prefect and fail if designated gates
  are skipped/missing. Native focused tests, lint/format/changed-module typing and
  full final suite remain mandatory before merge. Source inspection is not actual
  image, privilege or host evidence; no live exploitation/vulnerability reproduction.
- Update canonical security/cicd/orchestration/logging/deployment/touchpoint docs with
  code changes. Preserve FI, scientific behavior, normal API/store/data-use contracts,
  legacy receipt reading, existing local backup retention and non-Nepal behavior.
  No repository-local agent workflow/skill copies or parallel release authority.

M1 acceptance requires measured private delivery/runtime evidence and owner review,
not full DR completion. Later FI/input admissibility, station onboarding/evaluation,
normal forecast/dashboard integration and unattended activation gates are unchanged.
Do not claim delivery of the Effort from M1 health, a merge or a working subset.

## Estimates and remaining decisions

The old 3–5-day estimate was already superseded by conditional 12–23 active days
for the former full delivery/recovery scope. That latter range is historical, not
a current reduced-scope promise. Deferral does not justify reverting to 3–5 days
or a ten-day milestone promise. **Active reduced-scope re-estimation is pending**
the bounded delivery/runtime feasibility packet and selected CI trust transition.
Separate implementation, reviews/merges, access waits and later observation time.

Owner decisions still needed before execution: bounded feasibility/investment,
exact initial trusted CI admission and the manual private-result/transition-hold
responsibility, permitted private transfer/access, and later release/deployment
activation. Backup commissioning/recovery-host choices and all-21 source coverage
are not questions to reopen now. The confirmed backup deferral records a scope
choice and operational loss/reconstruction risk, not verified DR or code approval.


## 2026-10-22 bounded Actions-to-staging route exception

The owner selected Actions builds and private delivery of the same tested images,
with existing package keys/locations unchanged and no inferred pause of other builds.
The owner explicitly permits the existing staging ADMIN SSH key to be copied only
into the approval-protected staging release environment. This is not a restricted
upload principal or custody inaccessible to CI. The earlier A1/A2 credential
migration and A3 two-principal isolation criteria are NOT satisfied by this route.
They are not prerequisites silently claimed complete. Historical requirements above
remain evidence of the prior route, not instructions to migrate/delete keys.

The bounded replacement is [Actions staging release delivery](../../docs/deployment/actions-staging-release.md):
one manual canonical owner-published tag, all substantive gates, the existing release
builder as sole producer, both actual amd64 proofs/scans, exact-ID private export,
bounded no-clobber acceptance and a digest-only owner receipt retained outside server
admin control. Deliver-only is the default; image load needs a separately bound
receiver approval. Merge, release publication, receiver setup and runtime activation
remain distinct owner-controlled/approved operations. This local implementation is
not live qualification, accepted M1 or backup commissioning.

The trust set includes all job-executed actions, dependencies and base images,
Docker/build services and privileged hosted-runner processes. Late SSH injection
is exposure reduction, not hostile-code isolation. Existing source-key consumers
and their output/cache exposure remain unchanged. The owner-directed exception
must not be reported as old isolation guarantees or independent server custody.
