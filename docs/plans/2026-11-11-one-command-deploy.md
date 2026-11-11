---
status: DRAFT
created: 2026-11-11
title: One-command, deployment-generic update with test and hotfix tracks
---

# One-command, deployment-generic update

DRAFT. Not executable until independently reviewed and set READY by the orchestrator.
CI test-run duration is out of scope here: see [faster-ci-feedback](faster-ci-feedback.md).

## Outcome

One command updates any deployment profile (mac-mini, Nepal/Infomaniak, later production) to a
given commit or release, verifies it, and rolls back automatically if verification fails. Test
deployments update as soon as that commit's PR checks are green. Production gets a fast hotfix
track. Nothing about existing gates is weakened without an owner decision recorded here.

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

1. Build a one-command deploy that is generic across deployments.
2. Test/MVP deployments may update from a PR branch once that PR's checks are green; production
   requires a merged, tagged release.
3. Emergency gate: targeted tests + image build + health check. The full suite starts in parallel
   with the fast deploy; if it fails, debugging and fixing continue.
4. The existing Nepal CI route (admission-bound, digest-attested) is improved, not bypassed.

## Design

1. **Profiles.** `deploy/profiles/<name>.toml` declares: host, compose file list, build mode
   (`host-build` or `prebuilt-archive`), required secrets, health URL, profile-specific checks
   (e.g. overlay path resolved, backup-worker memory) and which tracks are allowed.
2. **Command.** `tools/deploy.py <profile> --ref <sha|tag> --track test|release|hotfix`.
   Phases, each timed: preflight (host reachable, engine up, disk headroom, identity values
   *derived* from the ref, never typed) -> anchor (tag current images, save `.env`) -> update ->
   build or load -> up -> verify (version read inside the container equals the ref, init exit 0,
   migration head, health, profile checks, no tracebacks for N minutes) -> receipt with per-phase
   timings. Failed verify triggers automatic rollback; if migrations ran, stop and require an
   explicit owner flag instead of rolling back blindly.
3. **Tracks.** `test`: queries that the PR checks at that SHA are green. `release`: existing
   release-identity and receipt checks, unchanged. `hotfix`: targeted tests mapped from changed
   files + build + health; records an obligation that the full suite runs in parallel, marks the
   receipt provisional until it passes, and surfaces a failure loudly.
4. **Nepal route.** Measure per phase first. Candidate speedups that keep its attestations: reuse
   results already produced for the same SHA instead of re-running retained checks (only if
   provably equivalent), layer caching, transferring changed layers rather than the whole archive,
   and one `deploy` entry point that also performs activation (currently held by a separate
   receiver approval: owner decision).
5. **Host-local config.** Replace uncommitted edits to tracked files with committed per-deployment
   overlays or untracked override files so pulls never need a stash.

## Tasks

- **T0** Read-only: per-phase timing of the Nepal route and of the mini deploy; list every manual
  step of both.
- **T1** Profile schema, mini profile, `tools/deploy.py` plan/dry-run mode, tests with a fake host.
- **T2** Execute path: anchor, verify, rollback, receipt; tests for each failure branch.
- **T3** Move the mini onto it; delete the manual ritual from the runbook (keep as fallback).
- **T4** Nepal profile and the agreed speedups.
- **T5** Hotfix track, runbook and `docs/standards/cicd.md` updates.

## Open questions for the owner

- Who may trigger: owner only, or the orchestrator for staging?
- May the Nepal command also activate (start the stack), or must activation stay a separate
  approval?
- Hotfix sign-off: per use by the owner, or pre-authorised for named failure classes?
- Where host-local config should live (item 5).

## Risk and review

High risk: credentials, remote execution, production deployment. One independent reviewer with
security and deployment expertise per `docs/workflow.md`. No deployment, secret change or
workflow-permission change happens under this plan without a separate owner approval.
