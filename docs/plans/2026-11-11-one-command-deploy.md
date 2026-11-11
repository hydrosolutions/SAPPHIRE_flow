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
2. Test/MVP deployments may update from a PR branch once that PR's checks are green; production
   requires a merged, tagged release.
3. Emergency gate: targeted tests + image build + health check. The full suite starts in parallel
   with the fast deploy; if it fails, debugging and fixing continue.
4. The existing Nepal CI route (admission-bound, digest-attested) is improved, not bypassed.
5. Trigger: the owner, and later a production administrator. No other triggers.
6. Activation: the command also starts the stack and verifies it, subject to invariant I1 below.
7. Hotfix track: the owner only, per use (sole developer for now; revisit when that changes).
8. Host-local config follows item 6 of the design.

## Invariants (each must have a test)

- **I1 No approval, no activation.** On a profile with a receiver approval or admission data, the
  command only orchestrates the existing phases: dispatch, wait, then stop and print the exact
  approval request if the approval is absent. It never writes the receiver config, the admin key
  or any approval. Activation (init, up, verify) is a separate phase that runs only after the
  recorded approved load. The owner's single command means dispatch + wait + hand-off, not
  approval bypass.
- **I2 Test identity is never release identity.** The test track is allowed only on profiles with
  no admission data (host-build, i.e. the mini); the profile schema rejects it elsewhere. Test
  builds carry a sha-based, clearly non-release identity and a receipt kind `test`. The release
  identity tooling is not modified to accept it.
- **I3 Migrations are never rolled back by image swap.** Preflight records the migration head.
  Verify failure with an unchanged head: automatic image rollback. Head advanced: no image
  rollback; leave the stack as is, print the backup-restore instructions, never auto-restore.
- **I4 Provisional never promotes.** A provisional (hotfix) receipt is not a basis for the release
  track and not a rollback target.
- **I5 One deploy at a time per host** (lock), and no secret in argv, logs, env dumps or receipts.

## Design

1. **Profile.** `deploy/profiles/<name>.toml` with a minimal schema: host, compose file list,
   build mode (`host-build` now; `prebuilt-archive` reserved), required secrets, health URL,
   profile-specific checks (e.g. overlay path resolved, backup-worker memory), allowed tracks.
2. **Command.** `tools/deploy.py <profile> --ref <sha|tag> --track test|release|hotfix`. Phases,
   each timed, with persisted host-side state so an interrupted run can resume or abort (not
   held only in the controlling process): lock -> preflight -> anchor -> update -> build or load
   -> up -> verify -> receipt.
   - Preflight: host reachable (pinned known_hosts, never a key scan), engine up, disk headroom,
     clean tree (I-config below), migration head recorded, and **a fresh verified backup
     required before any update that carries migrations**. Identity values are derived from the
     ref and checked against the existing release-identity rules; never typed.
   - Anchor: reuse the existing no-overwrite rollback-image evidence and prune protection of the
     `rollback-*` tags (cicd.md); image rollback must keep both worker pools consistent so no
     deployment is left on a workerless pool.
   - Verify: version read inside the container equals the ref, init exit 0, migration head,
     health, profile checks, and zero tracebacks for a fixed window (set in T1, from container
     logs).
   - Receipt: credential-free, per-phase timings, stored immutably (no overwrite) outside the
     checkout.
3. **Tracks.**
   - `test`: mini only (I2). "PR checks green" means: every required check listed in the profile
     passed, on a head SHA equal to the SHA being built, re-queried at deploy time so a
     force-push invalidates it.
   - `release`: the existing release-identity and receipt checks, unchanged.
   - `hotfix`: owner only, a recorded reason and the full SHA, on a commit merged to main. Targeted
     tests come from a conservative allowlist mapping changed files to tests, falling back to the
     full suite; then build + health. The full suite starts in parallel. The receipt is
     provisional (I4) and opens a blocking obligation: the next deploy is refused until the full
     suite on that SHA is green or the owner records a waiver.
4. **Nepal route: deferred.** Not part of the first version. After T0 measurements, a separate
   plan decides speedups (re-use of same-SHA results, layer-delta transfer), each of which makes
   attestation-equivalence claims that need their own security review. Until then the command
   may only wrap the existing dispatch/approval flow under I1.
5. **Host-local config (recommended practice).** The deployed checkout is read-only and clean;
   `deploy` refuses a dirty tree. `.env` is host-local and untracked: it is outside the clean-tree
   check and its identity values are written only through the existing atomic dotenv helper
   (`tools/release_env_identity.py`). Non-secret per-deployment settings (the mini's tenant and
   group IDs, roster hash, image digest pins) move to committed per-deployment overlays, so they
   are reviewed, reproducible and survive a host rebuild; an overlay change is an ordinary PR with
   the release-target rule. A secrets audit of every value **precedes** its commit (T0), not after:
   secrets (`./secrets/`) and runtime-generated values (e.g. Prefect deployment IDs) stay off git.
6. **Testability.** The host executor is an injected Protocol; tests use a fake that records
   commands. Dry-run prints a diffable plan. Every phase failure branch is covered by a fake-host
   test before any real-host use.

## Tasks (order matters)

- **T0** Read-only: per-phase timing of the Nepal route and the mini deploy; list every manual
  step; secrets audit of the mini's three uncommitted edits; settle the documented-vs-actual state
  of the tagging workflow (cicd.md says retired, AGENTS.md says active) because identity depends
  on it.
- **T1** Profile schema, mini profile, `tools/deploy.py` dry-run, fake-host executor and tests for
  I1-I5.
- **T2** Execute path: lock, anchor, verify, rollback, receipt, resume/abort; failure-branch
  tests.
- **T3** Hotfix track and its obligation mechanism (before any real-host use, per the reviewer).
- **T4** Move the mini onto it (overlays, clean tree); keep the manual runbook as fallback.
- **T5** Runbook and `docs/standards/cicd.md` updates.

## Open questions for the reviewer

- Does the I1 hand-off design leave the Nepal receiver approval and admission data fully intact?
- Is the I3 rule sufficient given the one-version additive migration window?

## Risk and review

High risk: credentials, remote execution, production deployment. One independent reviewer with
security and deployment expertise per `docs/workflow.md`. A first review (2026-11-11) returned
NEEDS CHANGES on three blockers (activation vs receiver approval, test track vs release gate,
migration rollback); this revision addresses them as I1-I3. No deployment, secret change or
workflow-permission change happens under this plan without a separate owner approval.
