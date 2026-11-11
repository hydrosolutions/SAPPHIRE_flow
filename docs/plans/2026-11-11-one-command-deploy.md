---
status: DRAFT
created: 2026-11-11
title: One-command, deployment-generic update with test and hotfix tracks
---

# One-command, deployment-generic update

DRAFT. Not executable until independently reviewed and set READY by the orchestrator.
CI test-run duration is out of scope here: see [faster-ci-feedback](faster-ci-feedback.md).

## Outcome

One command updates a deployment to a given commit or release, verifies it, and rolls back
automatically when that is safe. The first version ships the mac-mini profile only (minimal
schema, no speculative generality). Test deployments on the mini update as soon as that commit's
PR checks are green. A hotfix track exists for the owner. **No existing gate is weakened**:
release identity, receiver approval, admission data and the no-schema-downgrade contract stay
as they are, and the command never writes or bypasses them.

## Measured baseline (2026-11-11)

- **mac-mini (host build):** build + up 2-2.5 min with dependency layers cached (slowest steps
  ~50 s and ~26 s, probably image export). Around 10 manual steps: rollback image, stash local
  edits, pull, three identity values in `.env`, build with two build secrets, ~8 acceptance
  checks.
- **Two near-misses the same day:** a stale `SAPPHIRE_RELEASE_VERSION` in `.env` produced images
  tagged 0.1.1125 that contained 0.1.1115; a memory limit set only with `docker update` was
  reverted by the container recreate. `git pull` also refuses while tracked files carry host-local
  edits (the mini has three).
- **Nepal/Infomaniak (`private-verification.yml`, manual dispatch of a published tag):** latest
  run 33 min (verify + build + export 22 min, SSH transfer 10 min); runs a week earlier took up
  to 2 h. It stops before activation (`deliver-only`); starting the stack is a further manual
  step.
- **Test run:** about 22 min on the PR and again on main.
- Host disk is not a constraint (91 GB images, 81 GB reclaimable, 21 GB build cache).

## Owner decisions (2026-11-11)

1. Build a one-command deploy that is generic across deployments (design for it; ship the mini).
2. Test/MVP deployments (the mini) may update from a PR branch once that PR's checks are green;
   production requires a merged, tagged release.
3. Emergency gate: targeted tests + image build + health check. The full suite starts in parallel
   with the fast deploy; if it fails, debugging and fixing continue.
4. The existing Nepal CI route (admission-bound, digest-attested) is improved, not bypassed.
5. Trigger: the owner, and later a production administrator. No other triggers.
6. Activation: the command also starts the stack and verifies it, subject to invariant I1 below.
7. Hotfix track: the owner only, per use (sole developer for now; revisit when that changes).
8. Host-local config follows design item 5.
9. This is an **MVP**: hardening and refactoring are deferred (see MVP scope).

## MVP scope

Ship the smallest thing that removes the manual ritual and its two observed failure classes.
**Deferred, not forgotten:** dedicated deploy keys with forced commands, resume-after-interrupt
(MVP: abort and print manual recovery), a production profile, Nepal-route speedups, moving the
mini's local edits into committed overlays, a generic multi-host profile schema. Invariants I1-I5
stay in scope because they protect existing gates, not because they are hardening.

## Invariants (each must have a test)

- **I1 No approval, no activation.** On a profile with a receiver approval or admission data, the
  command only orchestrates the existing phases: dispatch, wait, then stop and print the exact
  approval request if the approval is absent. It never writes the receiver config, the admin key
  or any approval. Activation (init, up, verify) runs only after the recorded approved load. The
  owner's single command means dispatch + wait + hand-off, not approval bypass. (No-op on the
  mini, which has no approval step; the Nepal profile is outside the first version.)
- **I2 Test identity is never release identity.** The test track is allowed only on profiles with
  no admission data (host-build, i.e. the mini); the profile schema rejects it elsewhere. A test
  build has `SAPPHIRE_RELEASE_VERSION=0.0.0+test.<12-hex-sha>`, the full 40-hex SHA in
  `SAPPHIRE_SOURCE_REVISION`, `VERSION=0.0.0-test.<12-hex-sha>` (Docker-safe tag) and receipt kind
  `test`. A T1 test must show this string fails the canonical release regex yet is accepted by the
  snapshot validator in `tools/release_env_identity.py`; the release-identity tooling is not
  modified to accept it.
- **I3 Migrations are never rolled back by image swap.** Preflight records the migration head.
  Verify failure with an unchanged head: automatic image rollback. Head advanced: no image
  rollback; leave the stack as is, print the backup-restore instructions, never auto-restore. The
  previous image is only guaranteed to run against the new schema inside the one-version additive
  window (`docs/standards/cicd.md`); the printed instructions say so.
- **I4 Provisional never promotes.** A provisional (hotfix) receipt is not a basis for the release
  track and not a rollback target.
- **I5 One deploy at a time per host.** No secret in argv, logs, env dumps or receipts.

## Design

1. **Profile.** `deploy/profiles/<name>.toml`, minimal schema: host, compose file list, build mode
   (`host-build` only in the MVP), health URL, profile-specific checks (e.g. overlay path
   resolved, backup-worker memory), required PR checks, allowed tracks.
2. **Command.** `tools/deploy.py <profile> --ref <sha|tag> --track test|release|hotfix`. Phases,
   each timed: lock -> preflight -> anchor -> update -> build -> up -> verify -> receipt. An
   interrupted run aborts and prints the manual recovery steps (resume is deferred).
   - **Lock:** host-side `flock` on a lock file; it releases when the process dies, so a crashed
     controller leaves no stale lock.
   - **Preflight:** host reachable (pinned known_hosts, never a key scan), engine up, disk
     headroom, clean tree (design item 5), migration head recorded, identity values derived from
     the ref and never typed. *Migrating update* = the alembic head differs between the running
     and the target ref. A migrating update additionally requires a **fresh verified backup**. On the mini the
     only backups are the local nightly dumps on the mini itself (`backup-database`, volume
     `/Volumes/sapphire-backup/pg_dumps`); there is no external or off-server copy anywhere (known
     limitation, owner-confirmed 2026-11-11). The check is therefore: the newest published dump
     passes `pg_restore --list` validation and is no older than 26 h (one nightly cycle plus
     slack). Profiles with a protected-evidence target use `latest_backup_health` /
     `verify_protected_backup` (`ops/protected_evidence_backup.py`) instead. A profile with neither
     refuses migrating updates. The check proves a dump exists on the same disk as the database,
     not that it survives losing the host; the preflight output says so.
   - **Anchor:** a unique tag per deploy, `rollback-<previous-version>`, protected from pruning
     like the existing `rollback-*` tags (cicd.md). Provisional receipts are never chosen as an
     anchor (I4). Rollback must leave no deployment on a workerless pool (both worker pools
     recreated together).
   - **Verify:** version read inside the container equals the ref's identity, init exit 0,
     migration head, health, profile checks, zero tracebacks for 5 minutes from container logs.
   - **Receipt:** credential-free, per-phase timings, append-only (no overwrite) in a directory
     outside the checkout on the host.
   - **Access (MVP):** the owner's existing SSH access with pinned host keys; the two build
     secrets are read on the host from `./secrets/` as today and never passed on the command line.
     Deploy keys with forced commands are deferred (production hardening).
3. **Tracks.**
   - `test`: mini only (I2). "PR checks green" = every required check in the profile passed on a
     head SHA equal to the SHA being built, re-queried at deploy time so a force-push invalidates
     it.
   - `release`: the existing release-identity and receipt checks, unchanged.
   - `hotfix`: owner only, recorded reason and full SHA, on a commit merged to main (this is
     stricter than the test track on purpose). Targeted tests come from a conservative allowlist
     mapping changed files to tests, falling back to the full suite; then build + health. The full
     suite starts in parallel. The receipt is provisional (I4) and writes a blocking obligation
     into the same append-only receipt directory: the next deploy, **including via the manual
     runbook, which gains a one-line check**, is refused until CI shows the full suite green for
     that SHA (queried, not self-asserted) or the owner records a waiver as a separate receipt
     naming the SHA and the reason.
4. **Nepal route: deferred.** Not in the MVP. After T0 measurements a separate plan decides
   speedups; each makes attestation-equivalence claims that need their own security review. The
   command may later wrap the existing dispatch/approval flow under I1.
5. **Host-local config (recommended practice; migration deferred).** The deployed checkout is
   clean and `deploy` refuses a dirty tree; until the mini's three local edits move into committed
   overlays (follow-up), the mini profile lists them as the only permitted modifications and the
   command preserves them across the pull. `.env` is host-local and untracked, outside the
   clean-tree check, and its identity values are written only through the existing atomic helper
   (`tools/release_env_identity.py`). Secrets (`./secrets/`) and runtime-generated values (e.g.
   Prefect deployment IDs) never go to git; the follow-up overlay PR starts with a secrets audit.
6. **Testability.** The host executor is an injected Protocol; tests use a fake that records
   commands. Dry-run prints a diffable plan. Every phase failure branch has a fake-host test
   before any real-host use.

## Tasks (order matters)

- **T0** Read-only: per-phase timing of the mini deploy (and the Nepal route for the later plan);
  list every manual step; secrets audit of the mini's three local edits; **settle the documented
  state of the tagging workflow** (cicd.md says retired, AGENTS.md says active). T0's tagging
  answer gates T1 because identity derivation depends on it.
- **T1** Profile schema, mini profile, `tools/deploy.py` dry-run, fake-host executor, tests for
  I1-I5 including the I2 identity proof.
- **T2** Execute path: lock, anchor, verify, rollback, receipt, abort; failure-branch tests.
- **T3** Hotfix track and obligation mechanism, including the runbook check (before any real-host
  use).
- **T4** Move the mini onto the command; keep the manual runbook as fallback.
- **T5** Runbook and `docs/standards/cicd.md` updates.
- **Follow-ups (separate plans/PRs):** overlays for the mini's local edits, Nepal speedups,
  production profile and hardening, resume-after-interrupt.

## Open questions for the reviewer

None carried over. Not verified by review: the required-check names, and whether the receipt directory can reuse `write_json_once`.

## Risk and review

High risk: credentials, remote execution, production deployment. Two independent reviews
(2026-11-11): the first returned NEEDS CHANGES on three blockers (activation vs receiver approval,
test track vs release gate, migration rollback), addressed as I1-I3; the second returned PASS
WITH CHANGES (identity string, verified-backup definition, obligation home, anchor tags, lock,
access model, inconsistencies), folded into this revision, with hardening deferred under the MVP
scope. No deployment, secret change or workflow-permission change happens under this plan without
a separate owner approval.
