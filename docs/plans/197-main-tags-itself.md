---
status: DRAFT
created: 2026-08-21
plan: 197
title: main tags itself — remove the manual step that keeps being skipped
scope: One CI workflow that creates the version tag on main when CI passes, if it does not already exist. NOT backfilling existing gaps (owner declined), NOT changing bump-my-version, NOT changing when or how versions are bumped, NOT a release-notes or changelog mechanism.
depends_on: []
blocks: []
source: tag audit 2026-08-21
---

# Plan 197 — main tags itself

## Status

**DRAFT.** Not for implementation until the owner confirms.

**D1 REVERSED by the owner 2026-08-21** — tag on push, not on CI success (see D1 for why; it also
deletes the `workflow_run` machinery). The draft's open question is resolved and removed.

**Independent Codex review round 1 — 1 blocker, 1 major, 1 minor, ALL VERIFIED AND FOLDED.** The
blocker (no git identity for `git tag -a`) would have failed the workflow on its first real run. The
major corrected D6 outright: my concurrency group would have *dropped* tags rather than protecting
them. The minor caught the version count going stale mid-draft. The reviewer separately confirmed the
`workflow_run` trigger, `workflow_run.head_sha`, `GITHUB_TOKEN` non-recursion and `contents: write`
claims are correct, and that the CLAUDE.md / `ci.yml` / cicd.md / `tag = false` citations hold.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is one workflow file of roughly 30 lines.** It needs no new service, no changelog, no release
automation, no version-scheme change, and no action from the marketplace. Reviewers: "no findings" is a
complete review; a finding must name a concrete failure (a tag that would be wrong, missing, or
duplicated), not a missing feature. Adding scope is a cost.

## The problem, measured 2026-08-21

Since `v0.1.773`, main has carried **8 distinct versions**; **6 have no tag** — `0.1.774`, `0.1.775`,
`0.1.776`, `0.1.779`, `0.1.787`, and `0.1.788` (current main). Only `0.1.773` and `0.1.786` are tagged.

*(The count moved while this plan was being written: PR #201 merged as `e55e62b` and took main from
0.1.787 to 0.1.788, which an independent review caught as a stale figure. That is the problem
restated — a number maintained by hand goes stale between writing it and reading it.)*

Two facts shape the design:

- **`0.1.777`–`0.1.785` never reached main at all.** They were consumed on branches. Each branch
  computes its next patch from its own `pyproject.toml` and cannot see what other branches claimed, so
  numbers are burned in parallel. Gaps on main are therefore **normal and expected**, not corruption —
  `docs/standards/cicd.md` already says tags are best-effort and gappy.
- **33 commits carry those 7 versions**, because plan-doc commits to main do not bump (CLAUDE.md
  § Version Bumping). So "tag every commit" is wrong by construction; the unit is the *version*.

**Why it keeps failing:** the current rule (CLAUDE.md:251, :257) is *"tag on main after the PR merges"* —
a manual step performed by a human after the interesting work is finished. It has been skipped 5 times
in 3 days. This is the same failure family as Plan 132's never-run post-merge cutover: a step that
nothing forces anyone to take.

**Not backfilled** (owner, 2026-08-21). This plan changes the future only.

## Decisions

- **D1 — Trigger on `push` to main. Do NOT gate on CI.** *(Owner decision 2026-08-21; this REVERSES the
  draft, which used `workflow_run` on CI success.)* **A tag here is an identifier, not a release gate** —
  measured, not assumed: no workflow in `.github/workflows/` triggers on a tag; the mac-mini deploy
  `git pull`s main and sets `.env VERSION` rather than checking out a tag (rollback uses a *Docker* image
  tag); and `docs/standards/cicd.md:336` already states tags are "a convenience, not an inventory".

  The general rule: gate on CI when tags **trigger releases**, tag on merge when tags **identify
  versions**. This repo is the second case, and gating would actively damage it:
  1. **It punches permanent holes in a lookup table.** "Which commit is 0.1.787?" has an answer whether
     or not CI passed. Withholding the tag loses that answer forever, because the eventual fix carries a
     *new* version — defeating the tag's only purpose.
  2. **It makes tags depend on CI's mood.** PR #195 existed solely because an apt mirror flaked, and two
     runs were auto-cancelled by the superseded-run rule. Infrastructure noise would silently decide
     which versions get tagged.
  3. **Main is already PR-gated.** A red main is exceptional (Plan 190's cross-PR interaction), and that
     commit still *is* version X. Quality already lives in the checks API; keep identity separate from it.

  This also makes the workflow markedly smaller: no `workflow_run`, no `head_sha`-versus-head subtlety,
  no event filtering — the exact places the first review found real defects. The tag also lands on the
  commit that **introduced** the version rather than a later docs commit.
- **D2 — Idempotent by construction: skip if the tag exists.** Never overwrite, never force. A docs-only
  push carries an unchanged version, finds its tag present, and no-ops. This is what makes the workflow
  safe to run on every push rather than only on version changes.
- **D3 — Gaps are accepted, not repaired.** If two PRs both land carrying `0.1.790`, the first tags it
  and the second finds it present and skips. The second commit ends up untagged. That is strictly better
  than the alternatives (force-moving a tag, or inventing a number nothing was built at), and it matches
  the existing gappy-tags convention.
- **D4 — Annotated tags.** The repo has both kinds (`v0.1.786` is annotated, `v0.1.773` is lightweight).
  Standardise on annotated going forward: it records who/when, and `git describe` prefers them.
- **D5 — Least privilege, job-scoped.** `permissions: { contents: write }` on the job only, matching the
  file's existing convention of scoping permissions per job (`ci.yml:87`, `:445`). A tag pushed with
  `GITHUB_TOKEN` does not trigger further workflows, so there is no recursion risk.
- **D6 — NO concurrency group; tolerate the race instead.** *(Corrected after independent review — the
  draft had this backwards.)* A concurrency group would **lose tags, not protect them**: with
  `cancel-in-progress: false` GitHub still allows only one *running* plus one *pending* run per group,
  and a third queued run **supersedes the pending one**, so three merges in quick succession can cancel
  the middle tagging run outright and leave that version permanently untagged. Serialisation is the
  wrong tool here.
  Instead, make the race harmless: two runs may both observe the tag absent, and one loses the push
  with `! [rejected] ... already exists`. Treat that rejection as **success** — re-check that the tag
  now exists and exit 0. Pure idempotency (D2) with no scheduling apparatus, and strictly fewer moving
  parts than the concurrency block it replaces.

## Task

### T1 — `.github/workflows/tag-main.yml`

`on: push: branches: [main]`. One job, `permissions: { contents: write }`. Read the version from
`pyproject.toml` at the checked-out commit, exit cleanly if `v$VERSION` already exists, otherwise create
an annotated tag at `github.sha` and push it.

Two details that decide whether this works at all:

- **Configure a git identity before `git tag -a`.** *(Independent review, BLOCKER — the draft omitted
  it.)* A clean GitHub runner has no `user.name`/`user.email`, and annotated tag creation fails with
  "Committer identity unknown" before anything is written. Set the `github-actions[bot]` identity in
  the job. Lightweight tags would sidestep this, but D4 chose annotated deliberately.
- **Fetch tags before checking existence.** `actions/checkout` does not fetch tags by default, so an
  existence check against a tagless local repo would report every tag missing and try to create one that
  already exists on the remote. Either fetch tags explicitly or query the remote
  (`git ls-remote --tags`). Without this, D2's idempotency is inert — the push rejection in D6 becomes
  the *only* thing preventing a duplicate, on every single run.

**Red-first:** a test asserting the workflow skips when the tag exists must fail against an
implementation that always creates. Verify the YAML with `actionlint` if the repo has it, otherwise a
parse check.

## Doc sync

- `CLAUDE.md` §Version Bumping (:251, :257) — the manual "tag on main after merge" step is replaced by
  this workflow. Keep the surrounding rules unchanged: **still no tagging on a feature branch**, still
  bump in the commit.
- `docs/standards/cicd.md` § Image tagging and versioning — record the workflow and the accepted gaps.

## Non-goals

Backfilling the 5 missing tags (owner declined) · changing `bump-my-version` (`tag = false` stays) ·
changing when versions bump · releases, changelogs, or GitHub Releases · signed tags · reconciling the
existing mix of annotated and lightweight tags.
