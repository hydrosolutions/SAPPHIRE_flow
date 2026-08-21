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

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is one workflow file of roughly 30 lines.** It needs no new service, no changelog, no release
automation, no version-scheme change, and no action from the marketplace. Reviewers: "no findings" is a
complete review; a finding must name a concrete failure (a tag that would be wrong, missing, or
duplicated), not a missing feature. Adding scope is a cost.

## The problem, measured 2026-08-21

Since `v0.1.773`, main has carried **7 distinct versions**; **5 have no tag** — `0.1.774`, `0.1.775`,
`0.1.776`, `0.1.779`, and `0.1.787` (current main). Only `0.1.773` and `0.1.786` are tagged.

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

- **D1 — Trigger on CI success, not on push.** Use `workflow_run` against the `CI` workflow, filtered to
  `conclusion == 'success'` and to main. A tag is a claim that a state is good; tagging on push would
  stamp commits whose CI later fails, and main has been red before (Plan 190). The cost is that the tag
  arrives minutes after the merge rather than instantly, which nothing depends on.
- **D2 — Idempotent by construction: skip if the tag exists.** Never overwrite, never force. A docs-only
  push carries an unchanged version, finds its tag present, and no-ops. This is what makes the workflow
  safe to run on every CI success rather than only on version changes.
- **D3 — Gaps are accepted, not repaired.** If two PRs both land carrying `0.1.790`, the first tags it
  and the second finds it present and skips. The second commit ends up untagged. That is strictly better
  than the alternatives (force-moving a tag, or inventing a number nothing was built at), and it matches
  the existing gappy-tags convention.
- **D4 — Annotated tags.** The repo has both kinds (`v0.1.786` is annotated, `v0.1.773` is lightweight).
  Standardise on annotated going forward: it records who/when, and `git describe` prefers them.
- **D5 — Least privilege, job-scoped.** `permissions: { contents: write }` on the job only, matching the
  file's existing convention of scoping permissions per job (`ci.yml:87`, `:445`). A tag pushed with
  `GITHUB_TOKEN` does not trigger further workflows, so there is no recursion risk.
- **D6 — Serialise with `concurrency`.** Two merges landing close together could otherwise race to
  create the same tag. A workflow-level concurrency group makes the second wait and then observe the
  tag already exists (D2). Do **not** set `cancel-in-progress` — cancelling would drop a tag.

## Task

### T1 — `.github/workflows/tag-main.yml`

One job. Read the version from `pyproject.toml`, exit cleanly if `v$VERSION` already exists, otherwise
create an annotated tag at the commit CI validated and push it.

Two details that decide whether this works at all:

- **Tag the SHA `workflow_run` reports, not `main`'s current head.** They differ whenever another merge
  lands during the CI run, and tagging the head would stamp a commit CI never validated. Check out
  `github.event.workflow_run.head_sha` explicitly.
- **Read the version from that same SHA**, for the same reason — not from a later checkout.

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

## Open question for the owner

**What should happen when CI is red on main?** D1 means no tag is created, which is intended — but the
version then stays untagged even after a later fix, because the fix carries a *new* version. That is
just another accepted gap (D3), and the alternative is tagging known-bad states. Confirm that is the
behaviour you want, or say tags should follow pushes regardless of CI.
