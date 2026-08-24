---
status: DRAFT
created: 2026-08-24
plan: 200
title: A workflow must refuse to build from a stale copy of its plan
scope: Add a preflight staleness gate to the plan/implement workflows so they escalate when the plan doc in the working repo differs from the newest committed version, plus a commit-time nudge when main carries unpushed commits. NOT a new tool, NOT a change to what the workflows do once started, NOT a branch-protection or CI change.
depends_on: []
blocks: []
source: PR #201 post-mortem; worktree-hygiene incidents 2026-08-20/21/24
---

# Plan 200 — a workflow must refuse to build from a stale copy of its plan

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**One preflight check and one commit-time warning.** No new tool, no new service, no CI change, no
branch protection. Reviewers: "no findings" is a complete review; a finding must name a concrete
failure, not a missing feature. Adding scope is a cost.

## The failure, measured

**Plan 194 shipped a file the owner's review had already rejected.**

| Time (2026-08-20) | Event |
|---|---|
| 17:11 | worktree `sapphire-p194` cut from `main` at `e815776` |
| 18:14 | owner's hand review committed to `main` as `8302dd1` — *"drop the marker"* |
| later | `/implement` built from the worktree's **17:11** copy and shipped `.backup-volume-unverified.json` |
| 2026-08-21 | PR #201 removed it: written, gitignored, documented, tested — and **read by nothing** |

`implement.js:175` reads the plan from `${repo}` — the worktree — and checks only that its status is
`READY`. Nothing checks whether that copy is still **current**. The review was also never pushed, which
removed the second chance to notice, but the root cause is narrower and fixable: **the workflow trusted
a snapshot of its own specification.**

`plan.js` has **no preflight at all** (`grep -c preflight` → 0).

**A second, related harm:** work stranded on one machine. Plan 158 — 16 commits, ~5,300 insertions,
including its own plan document — existed only in a local worktree until a housekeeping pass found it
on 2026-08-21. Unpushed commits on the shared `main` checkout recurred **three times in four days**
(2026-08-20, 08-21, 08-24).

**Why a rule in `CLAUDE.md` is not the fix:** the tag convention was exactly such a rule and was skipped
**five times in three days** until Plan 197 automated it. A step a human must remember, at the end of a
long task, is not a control.

## Decisions

- **D1 — Compare against the newest committed version, from BOTH refs.** The gate compares the plan
  file in `${repo}` against `origin/main` **and** local `main`. Either differing is a stop: `origin`
  catches "someone else edited the plan since this worktree was cut"; local `main` catches "the edit is
  here but unpushed" — the Plan 194 case, where `origin` alone would have said nothing.
- **D2 — Escalate, do not warn.** The workflow returns its standard escalation shape and builds nothing,
  exactly as the existing not-READY gate does (`implement.js:180-189`). A warning inside a long
  autonomous run is not read by anyone. **A stale spec is worse than no build**: it produces a confident,
  green, reviewed diff of the wrong thing, which is precisely what happened.
- **D3 — Fetch first, and treat a failed fetch as a stop.** The comparison is worthless against a stale
  `origin/main`. If the fetch fails (offline, auth), escalate rather than silently comparing to whatever
  was last seen — an unavailable check must not look like a passing one. *(This is the same lesson as
  the recap probe, which reported `ok=False` 2,448 times because its harness was broken: a check that
  cannot run must not resemble a check that ran.)*
- **D4 — The commit-time nudge WARNS and never blocks.** It fires only when `HEAD` is on `main` and
  `origin/main..main` is non-empty, printing the count. Blocking would punish an ordinary sequence of
  commits and would be disabled within a day. This half is a nudge, deliberately weaker than D2 —
  stranded work is a slower harm than a wrong build.

## Tasks

### T1 — preflight staleness gate in `implement.js` and `plan.js`

*In:* `.claude/workflows/implement.js`, `.claude/workflows/plan.js`.

Beside the existing status preflight in `implement.js` (`:170-189`), add a check that the plan file's
content in `${repo}` matches both refs (D1), fetching first (D3). On mismatch, return the standard
escalation shape with a reason naming **which** ref differs and the file. `plan.js` gets the same gate;
it has no preflight today, so this is its first.

**Red-first:** a test that fails when the workflow proceeds with a modified plan copy. If these scripts
have no test harness, state that plainly in the PR rather than inventing one — do not build a JS test
framework for this (proportionality).

### T2 — commit-time unpushed-commits nudge

*In:* `.pre-commit-config.yaml` (a `local` hook, matching the existing `shellcheck` / `plutil` entries).

On `main` only, when `origin/main..main` is non-empty, print the count and a one-line reminder. Always
exits 0 (D4). Must be silent on every other branch, so it does not become background noise that trains
people to ignore it.

## Non-goals

Branch protection or server-side hooks · changing what the workflows do after they start · auto-pushing
anything · a JS test framework for `.claude/workflows/` · touching `plan-review.js` (the deprecated
Sonnet-only fallback) · preventing worktrees from going stale in general — only the plan doc is gated.

## Exit gates

```bash
uv run pre-commit run --all-files
node --check .claude/workflows/implement.js && node --check .claude/workflows/plan.js
```

Plus a live proof, since this gate exists to fire: run `/implement` against a plan whose worktree copy
has been deliberately edited, and confirm it escalates **without building**.

**Doc sync:** `docs/workflow.md` § Tooling (the new gate and what escalation means);
`CLAUDE.md` § Workflow if the one-line description of the workflows changes.

## Open question for the owner

**Should the gate also cover the `repo` worktree being behind `main` generally**, not just in the plan
file? A worktree cut days ago builds against stale *code* too, which is a real hazard — but it is also
the normal condition of any branch, so gating on it would escalate constantly. This plan deliberately
gates **only the plan document**, on the grounds that the spec being wrong is categorically worse than
the base being old. Confirm that boundary, or widen it.
