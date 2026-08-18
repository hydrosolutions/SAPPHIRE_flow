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

- **D1 — Three-way behaviour, not two.** Absent credential + a run GitHub *cannot*
  give secrets to (Dependabot, fork PR) → **install without the extra, emit a
  `::warning::` naming the lost coverage**. Absent credential on a run that *should*
  have had it → **fail with a message naming the secret and the store it belongs
  in**, not a git auth error. Present credential → today's behaviour, unchanged.
  Rationale: "GitHub withheld it" and "somebody deleted the secret" are different
  faults and must not share an outcome.
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
- **D5 — Scope is `AQUACAST_TOKEN` only.** `RECAP_DG_CLIENT_TOKEN` gates a
  *non-optional* dependency: without it no job can sync at all, so there is nothing to
  degrade to. Fork-PR support for it is a separate decision (§Non-goals).

## Tasks

- **T1** — Replace the bare `uv sync --frozen --extra aquacast` step with the D1
  three-way guard; message text names the secret, the store, and what coverage is lost.
- **T2** — Add the D4 positive assertion so an installed extra provably runs the shim
  tests.
- **T3** — Document the Dependabot-secret mirroring requirement in
  `docs/standards/cicd.md` next to the existing private-clone notes, so the next
  private dependency does not rediscover this by outage.

## Exit

1. A Dependabot PR is **green**, with a visible warning that aquacast coverage was
   skipped.
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
- **OD-2** — Is permanently-skipped aquacast coverage on Dependabot PRs acceptable, or
  should dependency PRs that touch `uv.lock` be required to run it? A lock change is
  exactly the case where the extra's resolution could break.
