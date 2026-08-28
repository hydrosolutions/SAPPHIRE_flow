---
status: COMPLETE
created: 2026-08-27
plan: 206
title: Correct six statements in docs/standards/cicd.md that no longer match the workflows
scope: Correct six places where docs/standards/cicd.md describes CI behaviour that no longer matches .github/workflows/ci.yml. Docs only — NO workflow change, NO new job, NO new tooling, NO CI-behaviour change. Two things are deliberately excluded and owned elsewhere - the unit-suite command row (Plan 201 T3) and cicd.md:627 (Plan 207).
depends_on: []
blocks: []
source: drift audit 2026-08-27, prompted by the Plan 201 investigation
---

# Plan 206 — correct six statements in `cicd.md`

## Status

**IMPLEMENTED — held at PR #222 (2026-08-28).** Branch `docs/plan-206-cicd-drift`, v0.1.825. All six
items corrected; exit criterion met mechanically (every command the doc quotes in the `run:` step
column appears verbatim in the workflow it names — 0 drift). Independent Codex review of the diff:
BLOCKER none, MAJOR none, MINOR none, APPROVE.

One change outside the audited six, recorded rather than folded in silently: the nightly
sequential-unit row was MOVED to sit after the slow and live rows. That row's position stopped
matching the file when Plan 201's fixer round moved the step to the end of
`integration-nightly.yml`. Same row, same content.

`cicd.md:503`'s unit-suite row — excluded here and owned by Plan 201 T3 — was corrected in PR #220,
so this plan did not land alongside a knowingly stale row.


**DRAFT.** Not for implementation until the owner confirms.

**Independent Codex review 2026-08-27 after reconstruction: BLOCKER clean, MAJOR clean, MINOR clean —
"Ready to implement: yes."** It verified the six drift items, the Plan 064 analysis (including the
17-of-17 SHA-pinning claim, which it re-counted), and that T1 would not write anything false into the
standard.

**Reconstructed by hand 2026-08-27.** A `/plan` round grew this from 85 to 247 lines and escalated. Its
substantive findings are kept; its narration of its own drafting history is not. Same treatment as
Plans 184 and 188 — re-running the loop on an over-expanded plan does not shrink it.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**Six corrections to one Markdown file. No CI behaviour changes.** Reviewers: "no findings" is a
complete and welcome review; a finding must name a **factually wrong statement in the doc**, not a
missing feature or a nicer structure. Do NOT propose workflow changes, new jobs, doc reorganisation,
drift-detection automation, or extra scope. **Adding length is a cost.**

## The drift, audited

Every workflow file is named in `cicd.md` and every `ci.yml` job appears. The drift is in **what the doc
says they do**:

| # | Documented | Actual | Where |
|---|---|---|---|
| 1 | `pytest tests/integration/ -v -m "not slow"` | `… --ignore=tests/integration/live …` | `cicd.md:510` vs `ci.yml:428` — wrong since **2026-04-21** (`d39aa8a`) |
| 2 | `integration` / `build-image-and-scan` "Depends on: `unit`" | **no `needs:` key exists in `ci.yml`** — no cross-job dependency | `cicd.md:507-510` and the tier-4 rows |
| 3 | "`e2e` gates on this job succeeding (`needs: [unit, integration, build-image-and-scan]`)", present tense | **there is no `e2e` job** | `cicd.md:628`; `cicd.md:517` also cites "line 206 of ci.yml", which no longer exists |
| 4 | — | `timeout-minutes` on every job (`ci.yml:30,92,309,347,443`) | absent from `cicd.md` |
| 5 | — | workflow-level `concurrency`, `cancel-in-progress` on PRs only (`ci.yml:23-25`) | absent from `cicd.md` |
| 6 | "…across all **three** workflow files"; four rows titled "…private **recap-dg-client** clone" | the table covers **five**; the step is "…private **clones**", configuring recap unconditionally **and** aquacast conditionally | `cicd.md:475`; `cicd.md:488,497,504,508` vs `ci.yml:40-52` |

Items 4-5 have **zero** mentions: `cicd.md`'s five `concurrency` hits are four Prefect work-pool
references (`:78,80,86,93`) plus `:347`, which is about `tag-main.yml` deliberately having no group.

Item 3 also contradicts `cicd.md:456`, which already prints `(Tier 5 e2e) → not yet implemented`.

**For these six the doc is stale and the workflow is right.** PR #185 was measured — *"of the ~16 min
unit job, setup is ~70 s … the test run is the lever"* — so reverting workflows to match the doc would
cost real CI time on every PR to fix a documentation error.

## Plan 064 is not outdated — one item of it was never built

Item 3's fix depends on what `e2e` was *supposed* to be, so `docs/plans/064-supply-chain-hardening.md`
(READY, 2026-04-20) was audited. It is **~90 % implemented**, not stale:

| 064 deliverable | State |
|---|---|
| B0 `build-image` job | **done** — `build-image-and-scan` exists |
| B3 `trivy image` scan | **done** |
| D3 SHA-pinned actions | **done** — 17 of 17 |
| **the e2e tier** | **never built** — 0 jobs |

**064 says the e2e tier gates on "the image build/scan path succeeding" (`:245`) and "on Trivy passing"
(`:286`) — only that.** It never establishes a dependency on `unit` or `integration`, and
`ci.yml:577-579`'s surviving comment agrees: *"Gated on build-image-and-scan per Plan 064 B0 step 5 and
B3 step 3."*

**So item 3's fix is: document current behaviour** — there is no `e2e` job, and full-pipeline coverage
today is the `@pytest.mark.slow` `test_full_pipeline` (`tests/integration/test_e2e_pipeline.py:156`) run
nightly by `integration-nightly.yml:97`. If a future-intent sentence is kept it may name **only** the
build/scan dependency. **Do not write the three-job list** — nothing supports it.

*(Worth someone's attention, NOT this plan's job: 064 reads as fully outstanding while being ~90 %
shipped — the stale-status pattern that had 21 plans archived on 2026-08-26.)*

## Excluded, and owned elsewhere

- **The unit-suite command row (`cicd.md:503`) — Plan 201 T3.** Its ratified T3 also *adds* a `run:` step
  to the `unit` job, so an audit frozen today cannot describe that row correctly. If Plan 201 is dropped,
  this row must be re-filed. Accepted, so two plans do not rewrite one row.
- **`cicd.md:627` (SBOM "on every run") — Plan 207.** That line is **correct**; the *workflow* is wrong.
  **Do not edit it** — matching the doc to the defect would silently weaken the security standard.

## Task

### T1 — correct the six statements

*In:* `docs/standards/cicd.md` only.

Fix items 1-6 above. For item 2, correct **every** `integration` and `build-image-and-scan` row carrying
a `unit` dependency, not only the cited lines. For item 6, the lint row's local-equivalent cell must
describe **both** rewrites (unconditional recap, conditional aquacast at `ci.yml:47`); the three "Same as
lint" rows then inherit it.

For item 6's sentence at `cicd.md:475`, replace it wholesale rather than patching the number — "the full
per-step breakdown of every `run:` command" stays overstated even after "three" becomes "five".

**Exit:** for each item, the doc's quoted command matches the workflow file verbatim.

## Non-goals

Changing any workflow · adding or removing a job · drift-detection automation · restructuring `cicd.md` ·
the unit-suite row · `cicd.md:627` · Plan 064's status · anything in Plans 201 or 207.

## Exit gates

```bash
uv run pre-commit run --all-files
```
