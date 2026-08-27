---
status: DRAFT
created: 2026-08-27
plan: 206
title: Make docs/standards/cicd.md match the workflows it documents
scope: Correct five places where docs/standards/cicd.md describes CI behaviour that no longer matches .github/workflows/. Docs only — NO workflow change, NO new job, NO new tooling, NO CI-behaviour change of any kind.
depends_on: []
blocks: []
source: drift audit 2026-08-27, prompted by the Plan 201 investigation
---

# Plan 206 — make `cicd.md` match the workflows

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is five corrections to one Markdown file.** It changes **no** CI behaviour. Reviewers: "no
findings" is a complete and welcome review; a finding must name a **factually wrong statement in the
doc**, not a missing feature or a nicer structure. Do NOT propose workflow changes, new jobs, a doc
reorganisation, automation to prevent future drift, or extra scope. **Adding length is a cost.**

## The drift, audited

All six workflows are named in `cicd.md`, and every `ci.yml` job appears. The drift is in **what the
doc says they do**:

| # | Documented | Actual | Drifting since |
|---|---|---|---|
| 1 | `pytest tests/unit/ --cov=… -v` | `pytest tests/unit/ **-n auto** --cov=…` (no `-v`) | 2026-08-18 (`fd96b56`, #185) |
| 2 | `pytest tests/integration/ -v -m "not slow"` | `… **--ignore=tests/integration/live** -v -m "not slow"` | **2026-04-21** (`d39aa8a`) |
| 3 | — | `timeout-minutes` on every job | 2026-08-18 (#185) |
| 4 | — | `cancel-in-progress` on PRs only | 2026-08-18 (#185) |
| 5 | — | a workflow-level CI `concurrency` group | 2026-08-18 (#185) |

Items 3-5 have **zero** mentions in `cicd.md` (its five `concurrency` hits are all Prefect work pools,
not CI). Item 2 has been wrong for **four months**.

**The implementation is right; the doc is stale.** PR #185 was measured and well-reasoned — *"of the
~16 min unit job, setup is ~70 s … the test run is the lever"* — and cut the job to 10-11 min. Reverting
to match the doc would cost ~4x on every PR to fix a documentation error. So this plan changes the doc.

## Why it drifted (recorded, not fixed here)

**PR #185 was CI work done without a plan document.** `CLAUDE.md` requires that every code change update
affected docs, and the mechanism that normally enforces it is a plan's **Doc sync** section. No plan, no
doc sync. Item 2 drifted the same way, four months earlier.

**Deliberately NOT in scope:** any automation to detect future doc/workflow drift. That is a separate
idea with its own cost, and bolting it onto a five-line correction is exactly the over-engineering this
plan forbids.

## Task

### T1 — correct the five statements

*In:* `docs/standards/cicd.md` only.

1. Unit row: the actual command, including `-n auto`.
2. Integration row: the actual command, including `--ignore=tests/integration/live`.
3-5. Record `timeout-minutes`, `cancel-in-progress` (PRs only, deliberately not `main`) and the CI
   `concurrency` group where the CI-tier section describes job structure.

**One substantive addition, because it is the reason the audit happened:** state that `-n auto`
**masks test-ordering leaks** — a full sequential run currently fails 13 tests that CI passes (Plan
201). A standard that documents the flag without its consequence would let the next person rediscover
that the expensive way. Keep it to a sentence and cross-reference Plan 201; do not re-explain the defect
here.

**Also check the table's "local equivalent" column** for the two corrected rows — if the CI command
changed, the suggested local command may no longer correspond.

## Non-goals

Changing any workflow · adding or removing a CI job · drift-detection automation · restructuring
`cicd.md` · documenting workflows other than the five statements above · anything in Plan 201.

## Exit gates

```bash
uv run pre-commit run --all-files
```

Plus a read-back: for each of the five items, the doc's text matches the workflow file verbatim where it
quotes a command.
