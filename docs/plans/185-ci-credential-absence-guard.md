---
status: DRAFT
created: 2026-08-18
plan: 185
title: CI credential-absence guard — a withheld secret must degrade loudly, not fail red
scope: Make the `unit` job's private-extra install tolerate a STRUCTURALLY absent credential (Dependabot runs, fork PRs) with a loud, visible loss-of-coverage warning, while still failing hard when the credential is absent on a run that should have had it. Explicitly NOT adding fork-PR support, NOT changing the aquacast pin, NOT the wheel migration (080).
depends_on: []
blocks: []
---

# Plan 185 — CI credential-absence guard

## Status
**DRAFT.** Not for implementation until the owner confirms.

## Why this exists

On 2026-08-18, PR #174 merged at 09:57 and made the `unit` job install a private
extra: `uv sync --frozen --extra aquacast` (`ci.yml`). Within ~10 minutes all four
open Dependabot PRs went red. The failure reads as a dependency error —
`fatal: could not read Username for 'https://github.com'` — but the cause is that
**GitHub structurally withholds Actions secrets from Dependabot-triggered runs**;
they see only the Dependabot secret store (`Secret source: Dependabot` in the job log).

The immediate outage is fixed: `AQUACAST_TOKEN` was mirrored into the Dependabot
store (11:34), matching `RECAP_DG_CLIENT_TOKEN`, which was mirrored in July for the
same reason. **This plan is about durability, not that outage.** The same shape
recurs for fork PRs, for a rotated-and-not-yet-mirrored token, and for any future
private dependency — and each time it presents as a broken build rather than as a
missing credential.

## The design fork this must resolve

The naive fix — skip the extra whenever the token is missing — **re-opens the exact
hole #174 closed**. The shim's tests are guarded by `importorskip`, so without the
extra they skip silently and the aquacast boundary is "exercised on nobody's machine
but the author's" (#174's own rationale). A guard that trades a red build for silent
zero coverage is a worse position, not a better one.

## Decisions

- **D1 — Four-way behaviour, not two** (extended by the OD-2 ruling below).
  1. Credential present → today's behaviour, unchanged: install the extra.
  2. Absent + the PR **touches `uv.lock`** → **fail**, with a message saying a lock
     change cannot be validated without the extra and naming the secret + store to fix
     it. A lock change is exactly where the extra's resolution breaks (this is how the
     `rich>=15` / prefect conflict surfaced), so skipping there would validate nothing.
  3. Absent + no lock change + a run GitHub *cannot* give secrets to (Dependabot,
     fork PR) → **install without the extra, emit a `::warning::` naming the lost
     coverage**.
  4. Absent + no lock change + a run that *should* have had the secret → **fail**,
     naming the secret and its store, not a git auth error.
  Rationale: "GitHub withheld it", "somebody deleted the secret", and "this change is
  the one that most needs the check" are three different faults and must not share an
  outcome.
- **D2 — Detection mechanism must be pinned at implementation.** Candidates:
  `github.actor == 'dependabot[bot]'`, `github.event.pull_request.user.login`, and
  `github.event.pull_request.head.repo.fork`. Pick one per condition and state why;
  do not infer "credential-less" from the empty secret itself, which would make every
  cause look structural.
- **D3 — Coverage loss must be visible in the checks UI, not only in a log line.**
  A warning nobody reads is the failure mode this whole plan exists to prevent
  (cf. the 0-byte backups that cleared their own alarm for four nights).
- **D4 — The positive case needs an assertion.** When the extra IS installed, the run
  must prove the shim tests actually executed rather than skipped — otherwise the
  guard silently degrades into permanent no-coverage and nothing notices. Mechanism to
  pin: a non-zero collected-count check on the aquacast tests, or `-p no:randomly`
  plus an explicit `--strict-markers`-style gate. This is the load-bearing task.
- **D6 — "Touches `uv.lock`" must be detected deliberately; the naive form does not
  work.** Every `actions/checkout` in `ci.yml` runs at the **default depth of 1**
  (`ci.yml:22,74,137,188,236` — none sets `fetch-depth`), so `git diff
  origin/main...HEAD` has no base commit to diff against and will not detect anything.
  Three candidates, pin one at implementation: (a) `fetch-depth: 0` on the `unit` job —
  simplest, costs a full-history fetch on every run; (b) fetch just the base ref
  (`git fetch --depth=1 origin $GITHUB_BASE_REF`) then diff — cheaper, more moving
  parts; (c) `gh pr view --json files` via the job's `GITHUB_TOKEN` — no git surgery,
  but adds an API dependency and returns nothing on `push`. **Recommended default:
  (b).** Whichever is chosen, define the `push`-to-`main` case explicitly: main has the
  secret, so it always installs the extra and never consults the lock test.
- **D5 — Scope is `AQUACAST_TOKEN` only.** `RECAP_DG_CLIENT_TOKEN` gates a
  *non-optional* dependency: without it no job can sync at all, so there is nothing to
  degrade to. Fork-PR support for it is a separate decision (§Non-goals).

## Tasks

- **T1** — Replace the bare `uv sync --frozen --extra aquacast` step with the D1
  four-way guard, including the D6 lock-change test; message text names the secret, the
  store, and what coverage is lost (or why the run refuses to proceed without it).
- **T2** — Add the D4 positive assertion so an installed extra provably runs the shim
  tests.
- **T3** — Document the Dependabot-secret mirroring requirement in
  `docs/standards/cicd.md` next to the existing private-clone notes, so the next
  private dependency does not rediscover this by outage.

## Exit

1. A Dependabot PR that does **not** touch `uv.lock` is **green**, with a visible
   warning that aquacast coverage was skipped.
1b. A PR that **does** touch `uv.lock` runs the extra. With the secret reachable it
   passes; with the secret unreachable it fails with the D1-case-2 message rather than
   skipping. (Today this passes for Dependabot because `AQUACAST_TOKEN` was mirrored
   into the Dependabot secret store on 2026-08-18; the test must not silently depend on
   that remaining true.)
2. A normal PR still installs the extra and **provably runs** the shim tests (T2
   assertion fails if they skip).
3. With the secret removed on a normal PR, the job fails with the named message —
   not with `could not read Username`.
4. `docs/standards/cicd.md` states the mirroring requirement.

## Non-goals

- Fork-PR CI support in general (`RECAP_DG_CLIENT_TOKEN` makes it a larger question).
- Changing the aquacast git pin, the `rich` override, or the extra's contents.
- Migrating aquacast/FI to a private index (Plan 080).
- Any change to `lint`, `integration`, `wheel-only-guard`, or the image scan.

## Open items

- **OD-1** — Should the T1 warning also post as a PR comment, or is a check-run
  annotation sufficient? Annotation preferred (no comment spam on every Dependabot PR);
  owner to confirm.
- ~~**OD-2**~~ — **RESOLVED (owner, 2026-08-18): PRs that touch `uv.lock` must run the
  aquacast install.** Folded into D1 case 2 and exit gate 1b. Marked **"for now"** by
  the owner — this is a reversible policy, not a principle. Two things would justify
  revisiting it: (i) the aquacast extra pulls **torch**, so requiring it makes every
  weekly `uv`-group Dependabot PR pay the heaviest install in CI; (ii) it depends on
  `AQUACAST_TOKEN` remaining reachable from bot-authored branches — if that ever becomes
  unacceptable, the requirement becomes unsatisfiable rather than merely expensive.
  Record the revisit rather than re-deriving it.
- **OD-3** — A **fork** PR that touches `uv.lock` is structurally unsatisfiable: forks
  never receive the secret, so D1 case 2 makes it a hard fail. That is arguably correct
  (you cannot validate a lock change without the private dependency, so do not pretend
  you did) but it means fork contributions could not change dependencies at all.
  Acceptable while there are no external contributors — confirm that assumption holds
  before this is treated as settled.
