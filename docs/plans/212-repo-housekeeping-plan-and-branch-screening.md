---
status: DRAFT
created: 2026-08-28
plan: 212
title: Screen the undecided plans and the dormant branches against the code, once, with evidence
scope: Two screenings the 2026-08-28 stale-status audit could not finish safely. (A) Nine plan docs whose status cannot be settled without reading the code they describe. (B) ~65 local branches with no remote, where "has a diff against main" is NOT evidence of unmerged work. Produces status decisions and a delete list. NO product code changes.
depends_on: []
blocks: []
source: the 2026-08-28 stale-status audit (main b1e1b516); the audit's own limits
---

# Plan 212 — screen the undecided plans and dormant branches

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

This is a **bookkeeping** plan. It reads code and reports; it changes plan statuses and deletes
branches. Reviewers: "no findings" is a complete review. Do **not** propose new tooling, a status
linter, CI automation, a plan-status schema, or a rewrite of `docs/plans/README.md`. **Adding
length is a cost.** If a screening item turns out to need real code work, the answer is *file a
plan for it*, not to grow this one.

## What the audit already settled (do not redo)

Committed on `main` as `b1e1b516` (090, 140, the 064 correction, the 201/206 flips, the
README convention) and `3c4736ff` (082, 117, 129, 130, 145, 161):

- Eight plans archived: 082, 090, 117, 129, 130, 140, 145, 161. All inbound links repointed.
- 064 corrected `READY` → `PARTIAL` (B0/B3/D3 shipped; the e2e tier was never built).
- 201, 206 marked COMPLETE.
- The status convention is now written in `docs/plans/README.md`.

**The audit's own first finding, which sets this plan's method:** a status scan cannot be trusted
here. Statuses live in three forms — frontmatter `status:`, a legacy `**Status**:` line (27 plans),
and 11 plans with no marker at all. A scan keyed on one form silently skips the rest. **Every task
below must therefore enumerate plans from the filesystem, not from a status query.**

## A — nine plans whose status needs a code check

Each row is a *question*, not a verdict. The evidence column is what the audit could see cheaply;
it is deliberately weak, which is why these were held back.

| plan | audit evidence | the question to answer |
|---|---|---|
| 138 BAFU precip+temp+runoff regression | body says "**T1 is PARTIAL, not done**" | is T1 still outstanding, or did it land without the doc being updated? |
| 035 rating-curve provenance | header says implementation waits for v1, but `tests/unit/services/test_rating_conversion.py` and a `0035` migration-downgrade test exist | did part of this ship early, or do those artifacts belong to something else? |
| 162 robust database backup | 6 plan-named test files, 4 source; `scripts/restore-rehearsal.sh` exists | complete? If so it archives **with** a fix to the stale docstring path at `tests/unit/ops/test_restore_rehearsal.py:10` |
| 069 pyright backlog cleanup | 0 plan-named tests/source. Ratchet exists and passes (404 vs baseline 432) | the *ratchet* shipped via Plan 070; did the *backlog cleanup* this plan is about ever start? |
| 075 mac-mini stream-C bootstrap | 0 plan-named refs, but `scripts/bootstrap-mac-mini.sh` exists | shipped untagged, or partially? |
| 084 dev-deployment validation (2-station runoff) | 0 plan-named refs | superseded by the mini being live, or still a real gate? |
| 102 dashboard multi-parameter observations | 0 plan-named refs | outstanding, or overtaken by later dashboard work? |
| 104 dashboard hardening (links, chart defaults) | 0 plan-named refs | same question |
| 122 package operational scripts | 0 tests, 1 source ref; names `scripts/onboard.py`, `services/reanalysis_backfill.py` | do those entrypoints exist in the shape the plan specifies? |

Two outcomes are equally good: **archive** (with the evidence recorded) or **stays open** (with a
one-line note saying what is actually left). A third is allowed and expected: **outdated —
supersede or delete**, when the plan describes a world that no longer exists.

### A1 — answer the nine questions

*In:* the nine plan docs; read-only elsewhere.

For each row: read the plan's own exit criteria, then check them against `src/`, `tests/`,
`scripts/` and `alembic/`. Record the finding **in the plan doc** as a dated note, set the status,
and archive if COMPLETE.

**Grep for inbound references before moving any file.** Archiving Plan 174 once broke a test that
read the doc from disk, and this audit found two more live cases (`integration-nightly.yml:144`
for 201, `test_restore_rehearsal.py:10` for 162). A reference from `src/` or `tests/` means the
move needs a **code PR**, not a plan-doc commit to `main`.

**Exit:** each of the nine has a status backed by a named file or symbol, not by a commit count.

## B — screen the dormant branches

`git branch` lists ~65 local branches with no remote counterpart. The audit's classification was
**not sound** and must not be reused: `git diff origin/main...<branch>` reports a diff for any
branch whose merge base is old, whether or not its content already landed via squash-merge. Only
one bucket is trustworthy:

- **11 branches show no diff at all** against `origin/main` — squash-merge residue, safe to delete.
- The rest need a per-branch answer to: *is this content already on `main`?*

Two known exceptions that must survive any pruning:

- **`feat/plan-151-t8b`** — 2 commits, ~1,950 lines of forecast-cycle code and tests, **never
  pushed**, no PR. Real unmerged work.
- **`backup/*`** (158, 170, 174 pre-rebase) — deliberate rescue points. Delete only if the owner
  says so.

### B1 — produce a delete list, delete nothing yet

*In:* a report. No branch is deleted in this task.

For each remote-less branch, classify as **merged** (content on `main`), **unmerged** (real work),
or **backup**.

**Do NOT use `git cherry`.** It was in this plan's first draft and an independent review killed it,
correctly. `git cherry` matches by patch-id, and a squash-merge rewrites the branch's commits into
one, so no patch-id matches. Verified on `feat/plan-204-forecast-lab-v2`, squash-merged to `main` as
`b3473348` (#223): `git cherry origin/main` reports **all four commits as `+`** — "unmerged" — when
the work is fully landed. In a squash-merge repo `git cherry` marks everything unmerged and is worse
than useless here, because it would argue against deleting branches that are genuinely done.

**Use a content comparison instead.** For each branch: take the files it touches, then ask whether
those files still differ from `main`.

```bash
b=<branch>
files=$(git diff --name-only "$(git merge-base origin/main $b)" "$b" \
        | grep -vE '^(uv\.lock|pyproject\.toml|src/sapphire_flow/__init__\.py)$')
git diff --name-only origin/main "$b" -- ${=files}   # zsh: ${=files} splits; bash: $files
```

Empty output ⇒ every file the branch touched is byte-identical to `main` ⇒ **merged/superseded**.
Non-empty ⇒ **needs a look** — not automatically unmerged, since the branch may simply be old.

**Two traps, both hit while writing this plan — do not re-introduce them:**

1. **zsh does not word-split unquoted variables.** Passing `-- $files` sends the whole newline-joined
   list as ONE pathspec, which matches nothing, so `git diff` comes back empty and **every branch
   reports as merged.** In a plan that ends in `git branch -D`, that mistake deletes unmerged work.
   It reported `feat/plan-151-t8b` — 1,946 unpushed insertions — as "content on main". Use `${=files}`.
2. The version-churn files (`uv.lock`, `pyproject.toml`, `__init__.py`) differ on every branch and
   drown the signal; the filter above drops them.

**A clean comparison is still not proof.** It says the content matches `main` *today*; it cannot tell
a merge from a revert-then-rewrite. Anything the owner is unsure about stays.

**Exit:** every remote-less branch is in exactly one bucket, with a one-line reason and the command
output that put it there.

### B2 — owner confirms, then delete

Deletion is destructive and the owner has previously required an independent cross-check before
deleting superseded branches. Present B1's list; delete only the confirmed set; leave `backup/*`
and any unmerged branch alone unless explicitly named.

**Exit:** `git branch` lists only `main`, live work, and whatever the owner chose to keep.

## C — worktrees

`git worktree list` shows **13** (not 10 — the first draft undercounted). The main checkout is shared
by several sessions, and that is an active hazard: during this audit another session's pre-commit
stash cycle made in-progress edits transiently vanish from the shared tree.

**Removing a live worktree and pruning stale registrations are different operations, and the first
draft conflated them.** `git worktree prune` only clears administrative records for directories that
are already gone — a review confirmed `--dry-run --verbose` currently prints nothing, because all 13
registrations are live. It will therefore never remove a worktree, and running it is not the task.

Removing a live worktree is destructive and needs, in order: **owner confirmation**, a check that the
worktree is clean (`git -C <path> status --porcelain` empty — uncommitted work in another session's
tree is exactly what this repo has lost before), and `git worktree remove` **without** `--force`. Let
it refuse; a refusal means someone is working there.

**A branch checked out in a worktree cannot be deleted until that worktree is removed.** Sequence the
two: worktree first, branch second.

**Exit:** `git worktree prune` run (a no-op is a valid result); every surviving worktree maps to a
live branch; no worktree removed without confirmation and a clean tree.

## D — nine plan paths already dangle

Measured 2026-08-28 with the gate below. **Nine, not the six this plan first claimed** — an
independent review found the first gate silently skipped `.sh`, `.yaml` and `.py` files and could not
match the repo's `115b1`–`115b5` plan ids. All nine predate this audit — every path the audit itself
moved was repointed — and they are the residue of earlier archiving rounds that skipped the
reference check:

| cited path | actually lives at |
|---|---|
| `docs/plans/065-config-overlay-environment-variants.md` | `archive/` — cited from `docker-compose.staging.yml:4` |
| `docs/plans/070-precommit-and-gate-parity.md` | `archive/` — cited from **`CLAUDE.md:176`** and **`AGENTS.md:175`** |
| `docs/plans/120-basin-static-importer.md` | `archive/` |
| `docs/plans/176-lindas-archive-completeness.md` | `archive/` — cited from `docker-compose.yml:402` |
| `docs/plans/189-audit-window-edge-and-poll-bound.md` | `archive/` — cited from `docker-compose.yml:404`, `tests/unit/cli/test_register_deployments.py:220` |
| `docs/plans/198-forecast-lab-snapshot-export.md` | `archive/` |
| `docs/plans/070-precommit-and-gate-parity.md` | also cited from `.pre-commit-config.yaml:17` |
| `docs/plans/115b4-reader-flip-cutover.md` | `archive/` — cited from `scripts/audit_distribution_shift.py:16` |
| `docs/plans/132-recap-probe-deployment-reconciliation.md` | `archive/` — cited from `scripts/launchd/run-recap-probe.sh:20` |
| `docs/plans/199-salvage-plan-158.md` | `archive/` — cited from `scripts/launchd/docker-endpoint.sh:35` |

### D1 — repoint all nine

*In:* the citing files only. Add `archive/` to the path; change nothing else.

Several citations live in `CLAUDE.md`, `AGENTS.md`, `docker-compose*.yml`, `.pre-commit-config.yaml`,
two `scripts/launchd/*.sh` wrappers and a test — code-tree files, so this task lands as a **PR**, not
a plan-doc commit to `main`.

**One known blind spot, accepted:** `docker-compose.yml:404` wraps a plan path across two lines, so no
single-line grep can see it. Fix it by hand while doing the rest; the gate will not flag it.

**Exit:** the gate below prints nothing.

## Non-goals

Product code changes · a status linter or CI check · restructuring `docs/plans/README.md` ·
re-verifying the eight plans the audit already archived · touching plans in flight in other
sessions (151, 155, 182, 188, 202, 204) · deleting anything under `backup/` without the owner
naming it.

## Exit gates

```bash
uv run pre-commit run --all-files

# Every docs/plans/... path cited in a TRACKED file must resolve to a real file.
# git grep covers every tracked file — the first version used `grep -r --include=...`
# and silently skipped .sh/.yaml/.py, missing 3 of the 9. The [0-9]{3}[a-z]?[0-9]?
# id pattern matches 115b4 and 111b as well as plain 3-digit ids.
# Verified 2026-08-28: prints the 9 known dangling paths now, nothing after D1,
# and proven to flag an injected fake path.
git grep -hoE "docs/plans/(archive/)?[0-9]{3}[a-z]?[0-9]?-[a-z0-9-]+\.md" -- . \
  | sort -u | while read -r pth; do [ -f "$pth" ] || echo "DANGLING: $pth"; done
```
