---
status: DRAFT
created: 2026-08-27
plan: 206
title: Make docs/standards/cicd.md match the workflows it documents
scope: Correct six places where docs/standards/cicd.md describes CI behaviour that no longer matches .github/workflows/ci.yml. Docs only — NO workflow change, NO new job, NO new tooling, NO CI-behaviour change of any kind. The `unit` row is deliberately OUT of scope; Plan 201 T3 owns it.
depends_on: []
blocks: []
source: drift audit 2026-08-27, prompted by the Plan 201 investigation
---

# Plan 206 — make `cicd.md` match the workflows

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is a set of corrections to one Markdown file.** It changes **no** CI behaviour. Reviewers: "no
findings" is a complete and welcome review; a finding must name a **factually wrong statement in the
doc**, not a missing feature or a nicer structure. Do NOT propose workflow changes, new jobs, a doc
reorganisation, automation to prevent future drift, or extra scope. **Adding length is a cost.**

## Sequencing against Plan 201 (read first)

Plan 201 is a sibling DRAFT of the same date, and it already claims the `unit` row:
`docs/plans/201-unit-suite-isolation-defect.md:185-186` — *"`docs/standards/cicd.md:503` **already
documents the unit job WITHOUT `-n auto`** … so T3 must correct that row whichever option is chosen"*.
Worse, Plan 201's ratified T3 (`docs/plans/201-unit-suite-isolation-defect.md:160-165`) **adds a new
`run:` step to the `unit` job** (a ~8 s sequential-reproducer check) plus a new full-sequential step in
`.github/workflows/integration-nightly.yml`. An audit frozen today cannot see either.

**Resolution: Plan 206 does not touch the `unit` job at all.** The earlier draft's item 1 (rewrite the
unit row to include `-n auto`) is **dropped**. That leaves the two plans' edits disjoint, so no
`depends_on` and no ordering constraint is needed — they can land in either order.

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
| 2 | `integration` and `build-image-and-scan` "Depends on: `unit`" | **no `needs:` key exists anywhere in `ci.yml`** — all five jobs start in parallel | `cicd.md:507,508,510` + the tier-4 rows; grepping `ci.yml` for `needs:` returns only the *comment* at `ci.yml:440` |
| 3 | "`e2e` gates on this job succeeding (`needs: [unit, integration, build-image-and-scan]`)" — present tense | there is **no `e2e` job**; `ci.yml` ends at a dangling comment, `ci.yml:577-579` | `cicd.md:628`; the table row `cicd.md:517` additionally cites "line 206 of ci.yml", a line that no longer exists |
| 4 | — | `timeout-minutes` on every job (`ci.yml:30,92,309,347,443`; also step-level at `121,385`) | absent from `cicd.md` |
| 5 | — | a workflow-level `concurrency` group, `cancel-in-progress` on PRs only | `ci.yml:23-25`, absent from `cicd.md` |
| 6 | "the full per-step breakdown … across all **three** workflow files"; four rows titled "Configure git auth for the private **recap-dg-client** clone" | the table covers **five** workflow files; the step configures recap **and**, conditionally, aquacast | `cicd.md:475`; `cicd.md:488,497,504,508` vs `ci.yml:38-50` |

Items 4-5 have **zero** mentions in `cicd.md`. Its five `concurrency` hits are **four** Prefect
work-pool mentions (`cicd.md:78,80,86,93`) plus **one** CI mention — `cicd.md:347`, which explains why
`tag-main.yml` deliberately runs *without* a concurrency group. None of the five describes `ci.yml`'s
group. (The earlier draft said all five were Prefect; that was wrong.)

Item 3 is an internal contradiction as well as a drift: `cicd.md:456` already prints
`(Tier 5 e2e) → not yet implemented`, correctly.

**The implementation is right; the doc is stale.** PR #185 was measured and well-reasoned — *"of the
~16 min unit job, setup is ~70 s … the test run is the lever"*. Reverting workflows to match the doc
would cost real CI time on every PR to fix a documentation error. So this plan changes the doc.

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

2. **Dependency graph.** Replace `unit` with `—` in the "Depends on" cell of every `integration` and
   `build-image-and-scan` row (`cicd.md:507,508,510` and the tier-4 rows). Keep the *intra-job* step
   ordering already recorded in those cells ("Step 1 guard", "Trivy image scan (report)", "Convert report
   to SARIF") — that ordering is real; the cross-job `needs:` is not. Add one sentence to the prose above
   the table: `ci.yml` declares **no** `needs:` between jobs, so `lint`, `unit`, `wheel-only-guard`,
   `integration` and `build-image-and-scan` all start in parallel.

3. **`e2e`.** Rewrite `cicd.md:628` from present tense to stated intent: when an `e2e` job is added it is
   *intended* to gate on `[unit, integration, build-image-and-scan]` per Plan 064 B0/B3, and no such job
   exists today. In the table row `cicd.md:517`, **delete** the line-number citation ("line 206 of
   ci.yml") rather than repointing it — it will rot again; "a dangling comment at the end of `ci.yml`"
   suffices. Leave `cicd.md:456` alone; it is already correct, and after this edit the two agree.

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

5. **"three workflow files"** (`cicd.md:475`). The table actually names five (`ci.yml`,
   `dependency-safety.yml`, `integration-nightly.yml`, `live-lindas-weekly.yml`,
   `live-lindas-weekly-autoretry.yml`). Weaken the sentence to "the workflow files listed in it" rather
   than asserting a count — completing the per-step inventory is a bigger job than this plan carries, and
   the sixth workflow, `tag-main.yml`, is deliberately documented in prose at `cicd.md:340-352` with no
   table rows. Weakening is the cheap correct fix; recounting "three"→"five" would still leave the
   "every `run:` command" claim overstated.

6. **Auth-step row titles** (`cicd.md:488,497,504,508`). Retitle from "Configure git auth for the private
   recap-dg-client clone" to "Configure git auth for the private clones", matching `ci.yml:38`. That step
   writes the recap-dg-client rewrite unconditionally and an aquacast rewrite only when `AQUACAST_TOKEN`
   is set (`ci.yml:44-49`). Extend each row's trailing "requires `RECAP_DG_CLIENT_TOKEN`" note to name the
   conditional aquacast credential.

## Deferred, tracked (NOT silently dropped)

Two known-stale duplicates of the corrected text are **out of scope because they live in code, not
docs**, and any code commit in this repo mandates a `bump-my-version` patch bump plus a PR — which would
destroy this plan's doc-only property and its exit gate:

- `tools/gate_parity_check.py:42-45` repeats the stale integration command
  (*"run locally as 'uv run pytest tests/integration/ -v -m not slow'"* — missing
  `--ignore=tests/integration/live`, and rendering `-m not slow` unquoted).
- `.github/workflows/ci.yml:20-22` comments that cancelling on `main` "would leave gaps in the record",
  which overstates the guarantee (see T1 item 4).

**Owner action required before 206 is marked READY:** file these two as one small follow-on *code* plan
under a new plan number — **not** Plan 201, whose scope is the unit-suite state leak, and which would
have to grow a foreign concern to carry them. If the owner declines to file it, record that decision in
this section. Either way they are not left silently stale.

## Non-goals

Changing any workflow · adding or removing a CI job · drift-detection automation · restructuring
`cicd.md` (no new table columns, no new per-job rows) · completing the per-step `run:` inventory ·
the `unit` job's row, its command, `-n auto`, the sequential-leak narrative, or Plan 201's new
sequential check · editing `tools/gate_parity_check.py` · anything else in Plan 201.

## Exit gates

```bash
uv run pre-commit run --all-files
```

Plus a read-back: for each of the six items, the doc's text matches `.github/workflows/ci.yml` verbatim
wherever it quotes a command, and `git diff --stat` shows **exactly one file changed**,
`docs/standards/cicd.md`. No version bump — doc-only, direct to `main` per `CLAUDE.md`
§ Version Bumping.
