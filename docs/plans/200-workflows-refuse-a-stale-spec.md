---
status: DRAFT
created: 2026-08-24
plan: 200
title: A workflow must refuse to build from a stale copy of its plan
scope: Add a preflight staleness gate to the plan/implement workflows so they escalate when the working branch lacks the newest plan-changing commit, and warn (not escalate) when the branch is merely behind origin/main, plus a commit-time nudge when main carries unpushed commits. NOT a new tool, NOT a change to what the workflows do once started, NOT a branch-protection or CI change.
depends_on: []
blocks: []
source: PR #201 post-mortem; worktree-hygiene incidents 2026-08-20/21/24
---

# Plan 200 — a workflow must refuse to build from a stale copy of its plan

## Status

**DRAFT.** Not for implementation until the owner confirms.

**Independent Codex review 2026-08-24 — 1 blocker, 2 majors, minors clean; all VERIFIED AND FOLDED.**
The blocker: D1's equality predicate would false-escalate on every `/plan` run, since `/plan` edits the
plan doc by design. The majors: D5's hard stop would not have prevented its own example and cannot be
narrowed in this repo; D4 fired at the wrong git stage and would have been silent in the very case that
motivated it. **The root-cause claim was separately verified**: the worktree's plan copy (`e815776`)
specifies the marker, and the review (`8302dd1`) says "No marker file" — the implementer followed its
plan faithfully; the plan was an hour stale.

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

- **D1 — CONTAINMENT, not equality, against BOTH refs.** *(Corrected after independent review — the
  draft used equality and that was a BLOCKER.)* Check both `origin/main` and local `main`: `origin`
  catches "someone else edited the plan since this worktree was cut"; local `main` catches "the edit is
  here but unpushed" — the Plan 194 case, where `origin` alone would have said nothing.

  **But plain equality false-escalates on every legitimate run.** `/plan` **edits the plan document in
  place by design**, so its branch differs from `main` the moment it does its job; so does a resumed
  implementation branch that updated the plan's status. Equality would refuse those.

  **The predicate is:** the working branch must **contain the latest plan-changing commit** from each
  ref. Escalate only when such a commit is **absent from the branch AND** the worktree copy differs
  from that ref. That catches a correction the branch never saw, while allowing edits the branch itself
  made on top of the latest plan.
- **D2 — Escalate, do not warn.** The workflow returns its standard escalation shape and builds nothing,
  exactly as the existing not-READY gate does (`implement.js:180-189`). A warning inside a long
  autonomous run is not read by anyone. **A stale spec is worse than no build**: it produces a confident,
  green, reviewed diff of the wrong thing, which is precisely what happened.
- **D3 — Fetch first, and treat a failed fetch as a stop.** The comparison is worthless against a stale
  `origin/main`. If the fetch fails (offline, auth), escalate rather than silently comparing to whatever
  was last seen — an unavailable check must not look like a passing one. *(This is the same lesson as
  the recap probe, which reported `ok=False` 2,448 times because its harness was broken: a check that
  cannot run must not resemble a check that ran.)*
- **D4 — The nudge WARNS, never blocks, and runs POST-commit.** *(Stage corrected after independent
  review — as drafted it would have stayed silent in the exact case that motivated it.)* At **pre**-commit
  the new commit does not exist yet, so on the FIRST unpushed commit `origin/main..main` is still empty
  and the warning never fires. That is precisely the Plan 194 sequence: `e815776` was pushed, and
  `8302dd1` — the review — was the *first* unpushed commit after it. **Verified:** `8302dd1` descends
  directly from the pushed `e815776`.

  So run it at the **post-commit** stage (`always_run: true`, `pass_filenames: false`), where it can
  count the commit that was actually created. Warns on `main` only, always exits 0. Blocking would
  punish an ordinary sequence of commits and be disabled within a day; stranded work is a slower harm
  than a wrong build.

## Tasks

### T1 — preflight staleness gate in `implement.js` and `plan.js`

*In:* `.claude/workflows/implement.js`, `.claude/workflows/plan.js`.

Beside the existing status preflight in `implement.js` (`:170-189`), add two checks: the plan file's
content in `${repo}` matches both refs (D1), and the working branch is not behind `origin/main` (D5) —
fetching first (D3). On mismatch, return the standard
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
Sonnet-only fallback) · preventing worktrees from going stale in general (D5 gates the BASE being behind origin/main; it does not police the worktree in any other way).

## Exit gates

```bash
uv run pre-commit run --all-files
node --check .claude/workflows/implement.js && node --check .claude/workflows/plan.js
```

Plus a live proof, since this gate exists to fire: run `/implement` against a plan whose worktree copy
has been deliberately edited, and confirm it escalates **without building**.

**Doc sync:** `docs/workflow.md` § Tooling (the new gate and what escalation means);
`CLAUDE.md` § Workflow if the one-line description of the workflows changes.

## D5 — the gate ALSO covers a stale BASE, not only a stale plan (owner, 2026-08-24)

**Owner widened the scope.** A worktree cut days ago builds against stale *code*, and the resulting diff
is reviewed — by Codex and by a human — against a tree that no longer matches `main`. Plan 199 hit
exactly this: its branch had to merge `main` before the gates were meaningful, and that merge produced a
`pyproject.toml`/`uv.lock` conflict that a reviewer reading the pre-merge diff would never have seen.

**The design problem this creates, stated plainly so the review can attack it:** "escalate when the
branch is behind `origin/main`" fires on essentially every branch, because `main` moves several times a
day. A gate that fires constantly is disabled or ignored — the same failure as a blocking commit hook
(D4). So the widening needs a shape that is *actionable and rare*, not merely correct.

**RESOLVED after independent review: a stale BASE WARNS and proceeds; only a stale PLAN escalates.**
The draft proposed a hard refusal. The review attacked it successfully on two grounds, both verified:

1. **It would not have prevented its own motivating example.** Plan 199's implementation commit
   `35e7bae` was based on then-current `main` (`ab2e500`); `main` advanced over the weekend and the
   conflict appeared only when it was merged three days later. **A start-only gate passes and then goes
   stale during the run** — it cannot guarantee freshness through a long build, so a hard stop buys
   noise without the safety.
2. **Neither narrowing survives contact with this repo.** Filtering to `src/`/`scripts/`/`tests/` fires
   on **every code commit**, because the mandatory version bump touches
   `src/sapphire_flow/__init__.py` every time (CLAUDE.md § Version Bumping) — **verified across the
   last three code merges, all of which touch it**. And a plan's `*In:*` lines are not a complete path
   manifest: Plan 199's named the watchdog files, while its actual diff also touched hooks, CI and
   wrappers — and the later conflict was **precisely in files absent from `*In:*`**.

So: warn with the behind-count and the one-command remedy (`git merge origin/main`), and proceed. Do
not path-filter it. **This is deliberately weaker than the owner's initial "refuse" instruction** — a
gate that fires on nearly every run is bypassed, which is worse than no gate at all.
