---
status: DRAFT
created: 2026-08-27
plan: 206
title: Correct six statements in docs/standards/cicd.md that no longer match the workflows
scope: Correct six selected places where docs/standards/cicd.md describes CI behaviour that no longer matches .github/workflows/ci.yml. Docs only — NO workflow change, NO new job, NO new tooling, NO CI-behaviour change of any kind. This plan does NOT claim the doc is the only stale thing in this area — the same audit found a real code defect on the SBOM failure path (see "A code defect this audit found"), which is out of scope here and must be filed as its own code plan. The unit-suite command row (cicd.md:503) is deliberately OUT of scope; Plan 201 T3 owns it.
depends_on: []
blocks: []
source: drift audit 2026-08-27, prompted by the Plan 201 investigation
---

# Plan 206 — correct six statements in `cicd.md`

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is a set of corrections to one Markdown file.** It changes **no** CI behaviour. Reviewers: "no
findings" is a complete and welcome review; a finding must name a **factually wrong statement in the
doc**, not a missing feature or a nicer structure. Do NOT propose workflow changes, new jobs, a doc
reorganisation, automation to prevent future drift, or extra scope. **Adding length is a cost.**

The single exception, already taken below: where the audit found the *code* wrong rather than the doc,
this plan says so and refuses to write a doc sentence that would legitimise the defect.

## Sequencing against Plan 201 (read first)

Plan 201 is a sibling DRAFT of the same date, and it already claims the unit-suite command row:
`docs/plans/201-unit-suite-isolation-defect.md:185-186` — *"`docs/standards/cicd.md:503` **already
documents the unit job WITHOUT `-n auto`** … so T3 must correct that row whichever option is chosen"*.
Plan 201's ratified T3 (`docs/plans/201-unit-suite-isolation-defect.md:160-165`) also **adds a new `run:`
step to the `unit` job** (a ~8 s sequential-reproducer check) plus a new full-sequential step in
`.github/workflows/integration-nightly.yml`. An audit frozen today cannot see either.

**Resolution: Plan 206 does not touch the unit-suite command row at `cicd.md:503`, nor any row describing
a `unit`-job `run:` step that Plan 201 adds or rewrites.** It *does* touch `cicd.md:497`, but only that
row's step **title** and credential note — an edit shared with three sibling rows (T1 item 6) and
disjoint from everything Plan 201 T3 changes. So no `depends_on` and no ordering constraint: the two
plans can land in either order.

**Consequences, named rather than hidden:**

- The `-n auto` drift (`ci.yml:277` vs `cicd.md:503`) is **not** fixed by this plan. If Plan 201 is
  deferred or dropped, that row stays stale and someone must re-file it. That is the accepted trade-off
  for not having two plans rewrite one table row — and for not shipping a "current" doc that is already
  missing Plan 201's brand-new `unit` step, which is precisely the failure mode 206 exists to fix.
- The `-n auto` **masking** note belongs to Plan 201 too. Recorded here only so 201 does not inherit an
  overstated claim: the accurate wording is *"the current `-n auto` partition happens to separate the
  polluter from its victims"*, **not** that parallel execution categorically masks ordering leaks — Plan
  201 makes exactly that correction itself at `docs/plans/201-unit-suite-isolation-defect.md:38-42`. Any
  failure count must be written time-bound ("13 as of 2026-08-26"), since 201 T2 exists to remove it.

## The drift, audited

All six workflow files are named somewhere in `cicd.md`, and every `ci.yml` job appears. The drift is in
**what the doc says they do**. Every row below was re-checked against `ci.yml` at its current 579 lines
(2026-08-27).

| # | Documented | Actual | Where |
|---|---|---|---|
| 1 | `pytest tests/integration/ -v -m "not slow"` | `… --ignore=tests/integration/live -v -m "not slow"` | `cicd.md:510` vs `ci.yml:428` — wrong since **2026-04-21** (`d39aa8a`) |
| 2 | `integration` and `build-image-and-scan` "Depends on: `unit`" | **no `needs:` key exists anywhere in `ci.yml`** — the jobs carry no cross-job dependency | `cicd.md:507,508,509,510` + the tier-4 rows; grepping `ci.yml` for `needs:` returns only the *comment* at `ci.yml:440` |
| 3 | "`e2e` gates on this job succeeding (`needs: [unit, integration, build-image-and-scan]`)" — present tense | there is **no `e2e` job**; `ci.yml` ends at a dangling comment, `ci.yml:577-579`. Full-pipeline coverage today is the `@pytest.mark.slow` `test_full_pipeline` at `tests/integration/test_e2e_pipeline.py:156-157`, run nightly by `.github/workflows/integration-nightly.yml:97` | `cicd.md:628`; the table row `cicd.md:517` additionally cites "line 206 of ci.yml" (a line that no longer exists) and asserts a three-job dependency nothing supports |
| 4 | — | `timeout-minutes` on every job (`ci.yml:30,92,309,347,443`; also step-level at `121,385`) | absent from `cicd.md` |
| 5 | — | a workflow-level `concurrency` group, `cancel-in-progress` on PRs only | `ci.yml:23-25`, absent from `cicd.md` |
| 6 | "the full per-step breakdown … of every `run:` command … across all **three** workflow files"; four rows titled "Configure git auth for the private **recap-dg-client** clone" | the table covers **five** workflow files; the step is titled "…for the private **clones**" and configures recap unconditionally **and** aquacast conditionally | `cicd.md:475`; `cicd.md:488,497,504,508` vs `ci.yml:40-52` |

Items 4-5 have **zero** mentions in `cicd.md`. Its five `concurrency` hits are four Prefect work-pool
mentions (`cicd.md:78,80,86,93`) plus one CI mention — `cicd.md:347`, which explains why `tag-main.yml`
deliberately runs *without* a concurrency group. None of the five describes `ci.yml`'s group.

Item 3 is an internal contradiction as well as a drift: `cicd.md:456` already prints
`(Tier 5 e2e) → not yet implemented`, correctly.

**For these six items the doc is stale and the workflow is right.** PR #185 was measured and
well-reasoned — *"of the ~16 min unit job, setup is ~70 s … the test run is the lever"*. Reverting
workflows to match the doc would cost real CI time on every PR to fix a documentation error. So this plan
changes the doc. That conclusion is **scoped to these six items only** — the next section records one
place where the code, not the doc, is what is wrong.

## A code defect this audit found — now Plan 207

Auditing `cicd.md:627` (*"uploaded … on every run"*) showed the **reverse** of the six items below: the
doc states the intended contract and the workflow does not honour it. The two SBOM steps carry no `if:`,
so GitHub's implicit `success()` skips them once the vulnerability gate fails — the SBOM is missing
exactly when a vulnerability was found.

**Filed as `docs/plans/207-sbom-skipped-when-the-vulnerability-gate-fails.md`.** It is a code fix and
does not belong in a docs-correction plan. **Consequence for 206: `cicd.md:627` must be left ALONE** —
it is correct, and "fixing" it to match the defect would silently weaken the security standard.

## Why it drifted (recorded, not fixed here)

**PR #185 was CI work done without a plan document.** `CLAUDE.md` requires that every code change update
affected docs, and the mechanism that normally enforces it is a plan's **Doc sync** section. No plan, no
doc sync. Item 1 drifted the same way, four months earlier.

**Deliberately NOT in scope:** any automation to detect future doc/workflow drift. That is a separate
idea with its own cost, and bolting it onto a small correction is exactly the over-engineering this plan
forbids.

## Task

### T1 — correct the six statements

*In:* `docs/standards/cicd.md` only. No other file in the repo is edited by this plan.

1. **Integration command** (`cicd.md:510`). The `run:` cell becomes verbatim
   `uv run pytest tests/integration/ --ignore=tests/integration/live -v -m "not slow"`.
   **Mandatory, not "also check":** the same row's *local equivalent* cell currently reads
   `uv run pytest tests/integration/ -v -m "not slow"` and must become
   `uv run pytest tests/integration/ --ignore=tests/integration/live -v -m "not slow"` (requires postgres
   service + system deps).

2. **Dependency graph.** Operative rule: replace `unit` with `—` in the "Depends on" cell of **every**
   `integration` and `build-image-and-scan` row. As of today those are `cicd.md:507,508,509,510` plus the
   tier-4 rows — but the rule, not the enumeration, is authoritative if a row moves or one was missed.
   Keep the *intra-job* step ordering already recorded in those cells ("Step 1 guard", "Trivy image scan
   (report)", "Convert report to SARIF") — that ordering is real; the cross-job `needs:` is not. Add one
   sentence to the prose above the table: `ci.yml` declares **no** `needs:` between jobs, so `lint`,
   `unit`, `wheel-only-guard`, `integration` and `build-image-and-scan` have no cross-job dependency and
   are eligible to run in parallel (actual start times still depend on runner availability).

3. **`e2e` — document what exists, not an unratified future contract.**
   - `cicd.md:628`: drop the present-tense `needs: [unit, integration, build-image-and-scan]` claim.
     There is **no `e2e` job**. Full-pipeline coverage today is the `@pytest.mark.slow` `test_full_pipeline`
     (`tests/integration/test_e2e_pipeline.py:156-157`), run nightly by
     `.github/workflows/integration-nightly.yml:97`. The only future dependency the sources support is on
     the image build/scan path — `docs/plans/064-supply-chain-hardening.md:245` (B0 step 5, *"Gate the
     e2e tier on the image build/scan path succeeding"*) and `:286` (B3 step 3, *"Gate the e2e tier on
     Trivy passing"*) — which is also what the surviving comment at `ci.yml:577-579` says (*"Gated on
     build-image-and-scan"*). **Do not document a `unit` or `integration` dependency:** nothing in Plan
     064 or `ci.yml` establishes one, and the owner would have to ratify that list separately.
   - `cicd.md:517` (the tier-5 table row): **delete** the line-number citation ("line 206 of ci.yml")
     rather than repointing it — it will rot again; "a dangling comment at the end of `ci.yml`" suffices.
     Its "Depends on" cell must stop asserting `unit`, `integration`, `build-image-and-scan`; it becomes
     "n/a — job does not exist (intended: `build-image-and-scan`, Plan 064 B0 step 5)".
   - Leave `cicd.md:456` alone; it is already correct, and after this edit the two agree.
   - Leave `cicd.md:627` alone — see § A code defect this audit found.

4. **`timeout-minutes`, `cancel-in-progress`, the `concurrency` group.** These are recorded as **two or
   three sentences of prose in the existing paragraph above the table** (`cicd.md:479`, the section
   intro under "## CI workflow tiers"). **Not** as new table columns and **not** as per-job rows: the
   per-job values genuinely differ (10 / 25 / 15 / 25 / 30 minutes at `ci.yml:30,92,309,347,443`, plus
   two step-level 8-minute timeouts at `ci.yml:121,385`) and `concurrency` is workflow-level rather than
   job-level, so table-ising either would require exactly the restructuring this plan's Non-goals forbid.

   The concurrency sentence must be accurate about `main`, which is subtler than "main is never
   cancelled". `ci.yml:23-25` sets
   `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`, so **in-progress cancellation is
   enabled for PRs only**. On `main` the group serialises runs and the *running* run is preserved — but a
   newer queued `main` run can still supersede an older **pending** one, so this does **not** guarantee
   that every `main` commit receives its own verdict. Document that mechanism; do **not** restate the
   unqualified claim in the `ci.yml:20-22` comment or in
   `docs/plans/archive/197-main-tags-itself.md:112-116`. **No workflow change is required or permitted by
   this item.**

5. **"three workflow files" / "every `run:` command"** (`cicd.md:475`). Both halves of that sentence are
   wrong: the table names five workflow files (`ci.yml`, `dependency-safety.yml`,
   `integration-nightly.yml`, `live-lindas-weekly.yml`, `live-lindas-weekly-autoretry.yml`), and it does
   not carry *every* `run:` command. Replace the whole sentence with, verbatim:

   > See the CI workflow tiers table below for a per-step breakdown of `run:` commands for the workflow
   > files named in its section rows, including local equivalents and CI-only reasons.

   "full", "every" and the count all go in that one edit. Do **not** merely swap "three"→"five", which
   would leave the "every `run:` command" claim standing: completing the per-step inventory is a bigger
   job than this plan carries, and the sixth workflow, `tag-main.yml`, is deliberately documented in
   prose at `cicd.md:340-352` with no table rows.

6. **Auth-step row titles** (`cicd.md:488,497,504,508`). Retitle from "Configure git auth for the private
   recap-dg-client clone" to "Configure git auth for the private clones", matching the actual step title
   at `ci.yml:40` (repeated verbatim at `ci.yml:150,326,414`). That step writes the recap-dg-client
   rewrite unconditionally (`ci.yml:42`) and an aquacast rewrite only when `AQUACAST_TOKEN` is non-empty
   (`ci.yml:47-49`). Two consequences:
   - The `lint` row's *local equivalent* cell (`cicd.md:488`) currently shows only the recap `insteadOf`
     rewrite. It must describe **both**: the unconditional recap-dg-client rewrite and the aquacast
     `insteadOf` rewrite applied only when an `AQUACAST_TOKEN` is available. The three "Same as `lint` row
     above" cells (`cicd.md:497,504,508`) then inherit an accurate equivalent and need no further edit to
     that column.
   - Extend each of the four rows' trailing "requires `RECAP_DG_CLIENT_TOKEN`" note to name
     `AQUACAST_TOKEN` as the optional, conditional credential.

## Known stale duplicates elsewhere — out of scope, not silently dropped

Two known-stale duplicates of the corrected text live in **code**, not docs, so editing them would
trigger a `bump-my-version` patch bump plus a PR and destroy this plan's doc-only property and exit gate:

- `tools/gate_parity_check.py:42-45` repeats the stale integration command
  (*"run locally as 'uv run pytest tests/integration/ -v -m not slow'"* — missing
  `--ignore=tests/integration/live`, and rendering `-m not slow` unquoted).
- `.github/workflows/ci.yml:20-22` comments that cancelling on `main` "would leave gaps in the record",
  which overstates the guarantee (see T1 item 4).

Named so a later reader finds them. Neither gates this plan's readiness — unlike the SBOM failure-path
defect above, which does, because that one breaks a security contract rather than a comment.

## Non-goals

Changing any workflow · adding or removing a CI job · fixing the SBOM failure-path defect (its own code
plan) · editing `cicd.md:627` · drift-detection automation · restructuring `cicd.md` (no new table
columns, no new per-job rows) · completing the per-step `run:` inventory · the unit-suite command row at
`cicd.md:503`, `-n auto`, the sequential-leak narrative, or Plan 201's new sequential check · editing
`tools/gate_parity_check.py` · anything else in Plan 201.

## Exit gates

```bash
uv run pre-commit run --all-files
```

Plus a read-back: for each of the six items, the doc's text matches `.github/workflows/ci.yml` verbatim
wherever it quotes a command or a step title; `cicd.md:627` is **unchanged**; and `git diff --stat` shows
**exactly one file changed**, `docs/standards/cicd.md`. No version bump — doc-only, direct to `main` per
`CLAUDE.md` § Version Bumping.
