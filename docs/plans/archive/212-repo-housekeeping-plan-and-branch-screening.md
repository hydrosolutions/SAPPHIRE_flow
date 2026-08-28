---
status: COMPLETE
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

**COMPLETE — archived 2026-08-28.** A1, B1, B2, C and D1 all done; D1 merged as PR #226 (v0.1.831).

- **A1 — done.** All nine statuses settled against named files/symbols. 075 and 084 archived COMPLETE;
  035, 069, 102, 104, 138 corrected to PARTIAL; 122 confirmed genuinely outstanding; 162 left
  UNDETERMINED on purpose. Two audit contradictions resolved: 035's "waits for v1" header was stale
  (`alembic/versions/0034_rating_curves_table.py` is *Plan 035 Task 1*), and 138's T2 *did* land.
- **D1 — MERGED (PR #226, v0.1.831).** All nine repointed, including the wrapped
  `docker-compose.yml:404` path this plan predicted no grep would see. **Gate verified clean on
  `main` after the merge: every cited plan path in the repo now resolves.**
- **B1 — reported below.** 13 retirable, 1 inspect, 53 keep.
- **B2 — owner set a cutoff: retire only branches last worked on 2026-08-25 or earlier; nothing from
  yesterday or today.** Applied 2026-08-28 → **exactly ONE of the 13 qualified**:
  `docs/plan-192-recap-second-stack` (last activity 2026-08-20). Retired recoverably —
  `pruned/docs-plan-192-recap-second-stack` → `19c3ab3f`, restore verified live. The other 12 are
  held: `docs/plan-205-ma8` is from yesterday and **eleven were touched TODAY** by other sessions
  (`docs/housekeeping` committed at 14:03, `docs/plan-ma9` at 10:57). The cutoff earned its keep.
- **C — worktree pass done; nothing removed.** Under the same cutoff no worktree qualifies: all nine
  non-main worktrees are clean, but every one either holds real work (`DIFFERS`) or was active today.
  `git worktree prune` was a no-op — no stale registrations. `sapphire-plan192`'s worktree WAS removed
  (unforced, clean) as the prerequisite for retiring its branch.

**Method note for anyone repeating this:** a `NO_UNIQUE_WORK` branch points at a commit already on
`main`, so `git log -1 <branch>` returns **main's** date, not when the branch was worked on. Use the
**reflog** (`git reflog show --date=short <branch>`) — it records when the ref itself moved. Using the
commit date would have declared all 13 retirable, including eleven under active edit.

**READY — owner flip 2026-08-28**, after three independent Codex passes. Passes 1-3 each found a
blocker in §B1's branch-classification method; all were folded and re-verified. The final change was
to stop depending on the classifier being right and make deletion recoverable (§B2).

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

Running the corrected classifier (below) over all remote-less branches on 2026-08-28 gives:

| verdict | count |
|---|---|
| `NO_UNIQUE_WORK` — adds nothing over its merge base | **14** |
| `VERSION_CHURN_ONLY` — differs only in version/lock files | **1** |
| `DIFFERS` — real content not on `main` | **52** |

The audit's crude first pass said "11 zero-diff"; the correct method says 14 deletable. **B1 must
re-run the classifier rather than reuse either number** — branches move.

That single `VERSION_CHURN_ONLY` result is why the guard exists: under the discarded filter it would
have classified as merged and been deleted unexamined. It is `test/live-recap-127-forecast-xfail`.

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

**Use a content comparison instead** — but not the naive one. A second review killed this plan's
*first* replacement too, for two reasons that both end in deleted work:

- **The empty-list bug.** When the filtered file list is empty, `-- ${=files}` expands to *no
  pathspec at all*, so `git diff` compares the **entire trees**. Measured: `docs/fix-mi3-cimo` touches
  zero files yet the command reported **48** differing files. Wrong direction here, but it makes the
  classifier meaningless.
- **The excluded-file hole, which IS destructive.** Filtering `pyproject.toml` / `uv.lock` /
  `__init__.py` out of the *comparison* hides a branch whose only unmerged work lives in one of them —
  a real dependency addition, say. It would report clean and be deleted. Proven with a probe branch
  carrying a version-only change: under the old filter it classified as "merged".

**Never exclude a file from the comparison. Exclude it only from the verdict.** And never build the
pathspec by word-splitting: this repo contains filenames with spaces (`docs/requirements/DFL_Dummy
Station A.txt` and two siblings), present on the `backup/*` branches among others, so `${=files}`
splits one path into two pathspecs that match nothing — and an unmerged new file then reads as
`CONTENT_ON_MAIN`. Compare one NUL-delimited path at a time:

```bash
classify() {                                  # POSIX-safe; no word-splitting anywhere
  local b=$1 base n del out sub
  base=$(git merge-base origin/main "$b" 2>/dev/null) \
    || { echo "NO_MERGE_BASE $b — unrelated history or deleted upstream; INSPECT"; return; }
  n=$(git diff --name-only -z "$base" "$b" | tr -cd '\0' | wc -c | tr -d ' ')
  [ "$n" -eq 0 ] && { echo "NO_UNIQUE_WORK $b"; return; }
  del=$(git diff --name-status -z "$base" "$b" | tr '\0' '\n' | grep -c '^[DR]')
  out=$(git diff --name-only -z "$base" "$b" | while IFS= read -r -d '' f; do
          git diff --quiet origin/main "$b" -- "$f" || printf '%s\n' "$f"
        done)
  if [ -z "$out" ]; then
    [ "$del" -gt 0 ] && { echo "INSPECT_DELETES_OR_RENAMES $b"; return; }
    echo "CONTENT_ON_MAIN $b"; return
  fi
  sub=$(printf '%s\n' "$out" | grep -vE '^(uv\.lock|pyproject\.toml|src/sapphire_flow/__init__\.py)$')
  [ -z "$sub" ] && { echo "VERSION_CHURN_ONLY $b"; return; }
  echo "DIFFERS($(printf '%s\n' "$sub" | grep -c .)) $b"
}
```

Verdicts, of which only two are deletable:

| verdict | meaning | action |
|---|---|---|
| `NO_UNIQUE_WORK` | adds nothing over its merge base | delete (recoverably — see B2) |
| `CONTENT_ON_MAIN` | every file it touched is byte-identical to `main`, and it deletes/renames nothing | delete (recoverably) |
| `VERSION_CHURN_ONLY` | differs *only* in version/lock files | **inspect** |
| `INSPECT_DELETES_OR_RENAMES` | looks clean, but the branch deletes or renames a path | **inspect** |
| `DIFFERS` / `NO_MERGE_BASE` | real work, or history that cannot be compared | keep |

`INSPECT_DELETES_OR_RENAMES` exists because a comparison keyed on paths cannot see a *deletion* that
`main` resolved by renaming instead: base has `old`, the branch deletes `old`, `main` moves `old` →
`new`, and both sides then lack `old`, so the naive answer is "identical". `docs/plans/` is renamed
constantly here — eight files in one commit during this very audit — so the case is live, not
theoretical.

Verified 2026-08-28 against known answers: `feat/plan-151-t8b` → DIFFERS (10 files, its 1,946 unpushed
insertions), `backup/plan-174-pre-rebase` (which carries the space-containing paths) → DIFFERS,
`docs/fix-mi3-cimo` → NO_UNIQUE_WORK, `test/live-recap-127-forecast-xfail` → VERSION_CHURN_ONLY. Full
sweep: **14 NO_UNIQUE_WORK, 1 VERSION_CHURN_ONLY, 52 DIFFERS.**

**Two traps, both hit while writing this plan — do not re-introduce them:**

1. **zsh does not word-split unquoted variables.** Passing `-- $files` sends the whole newline-joined
   list as ONE pathspec, which matches nothing, so `git diff` comes back empty and **every branch
   reports as merged.** In a plan that ends in `git branch -D`, that mistake deletes unmerged work.
   It reported `feat/plan-151-t8b` — 1,946 unpushed insertions — as "content on main". Use `${=files}`.
2. Do not "simplify" the classifier by filtering the file list before the diff, and do not collapse
   the per-path loop back into one pathspec. Those are exactly the holes that made drafts two and
   three of this section unsafe.

**A clean comparison is still not proof.** It says the content matches `main` *today*; it cannot tell
a merge from a revert-then-rewrite. Anything the owner is unsure about stays.

**Exit:** every remote-less branch is in exactly one bucket, with a one-line reason and the command
output that put it there.

**B1 result, 2026-08-28** (re-run before acting — branches move):

- **13 `NO_UNIQUE_WORK`**, i.e. retirable: `docs/fix-205-status`, `docs/fix-mi3-cimo`,
  `docs/fix-mi3-citations`, `docs/housekeeping`, `docs/plan-192-recap-second-stack`,
  `docs/plan-205-ma8`, `docs/plan-211-ready`, `docs/plan-ma5b`, `docs/plan-ma5b-r2`,
  `docs/plan-ma5b-r3`, `docs/plan-ma9`, `docs/track-i-plans`, `status-check`.
- **1 `VERSION_CHURN_ONLY`** — `test/live-recap-127-forecast-xfail`. Inspect; the discarded filter
  would have deleted this one unexamined.
- **53 `DIFFERS`** — keep, including `feat/plan-151-t8b` and all three `backup/*`.

### B2 — owner confirms, then retire recoverably

**Three independent reviews found three different ways for this classifier to call unmerged work
deletable** — patch-id vs squash-merge, an empty pathspec comparing whole trees, a filter hiding
version-only work, a split path matching nothing, a deletion masked by a rename on `main`. Each fix
was correct and each time a new edge appeared. The conclusion is not "write a fourth classifier".

**Stop relying on the classifier being right. Make being wrong cheap.**

Do not run `git branch -D` against anything. Retire a branch by first pinning its tip, then dropping
the branch:

```bash
git tag "pruned/$b" "$b"     # a real ref: keeps every object alive and reachable
git branch -D "$b"           # only after the tag exists
```

Restoring a mistake is then `git branch "$b" "pruned/$b"` — nothing is lost, ever. The `pruned/`
namespace is deliberate: it cannot collide with the `v*` tags that `tag-main.yml` creates on every
push to `main`. Keep the tags local unless the owner wants them pushed.

With deletion made reversible, the classifier's job drops from "must never be wrong" to "sorts the
list so a human reads a short one". That is a job it can actually do.

**Still required:** owner confirmation on the specific list; `backup/*` and anything `DIFFERS`,
`VERSION_CHURN_ONLY`, `INSPECT_DELETES_OR_RENAMES` or `NO_MERGE_BASE` untouched unless the owner
names it; and `feat/plan-151-t8b` retained until its work is pushed or deliberately abandoned.

**Exit:** every retired branch has a `pruned/<name>` tag pointing at its old tip, and
`git branch` lists only `main`, live work, and what the owner chose to keep.

## C — worktrees

`git worktree list` showed **13** during the 2026-08-28 review (not 10 — the first draft
undercounted), and 12 an hour later as other sessions finished. Count it again at implementation
time rather than trusting either number. The main checkout is shared
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

**Exit:** every surviving worktree maps to a live branch; no worktree removed without owner
confirmation and a clean tree. `git worktree prune` is optional bookkeeping, not a gate — with all
registrations live it is a no-op, and requiring it here contradicted the paragraph above.

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
# ':!' excludes THIS plan: its D table names the broken paths on purpose, so
# including it would keep the gate red forever. BOTH patterns are needed — the
# plan lives under archive/ now, and ':!docs/plans/212-*' does not match that.
git grep -hoE "docs/plans/(archive/)?[0-9]{3}[a-z]?[0-9]?-[a-z0-9-]+\.md" \
  -- . ':!docs/plans/**/212-*' ':!docs/plans/212-*' \
  | sort -u | while read -r pth; do [ -f "$pth" ] || echo "DANGLING: $pth"; done
```
