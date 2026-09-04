---
status: SUPERSEDED
created: 2026-09-02
plan: 231
title: Make plan status, readiness, and tagging policy agree
scope: Reconcile the durable plan-lifecycle, status, readiness, and tagging rules in policy documents and readiness utilities. Make YAML READY the sole executable readiness signal and keep historical body-status parsing confined to archived-plan diagnostics. Do not edit workflow JavaScript that Plans 232 and 233 replace.
depends_on: []
blocks: [232]
source: 2026-09-02 workflow review — status and tagging instructions conflict, while the unused readiness checker requires review metadata that repository plans do not persist
---

# Plan 231 — one durable policy and one readiness check

Superseded by Plan 242, which preserves the durable readiness/status rules but
replaces confirmation-mode workflow policy with owner-run prompt passes.

## Problem

The durable policy and utilities disagree before either orchestration workflow starts:

- `docs/workflow.md:64-94` and `docs/plans/README.md:6-19` define different active-plan status
  vocabularies and disagree about whether a DRAFT may be reviewed.
- `docs/plans/README.md:8-19` makes YAML frontmatter the only status source, while
  `scripts/check_readiness.py:29-58` accepts a legacy body `**Status**:` marker.
- `scripts/check_readiness.py:61-214` additionally requires a `Review History` table, two rounds,
  zero blocking findings, and `user-confirmed`. The planning workflows do not persist that table and
  the script has no production caller, so this is an unused readiness contract rather than a working
  safety gate.
- `tools/standing_snapshot.py:131-157` scans both status forms without distinguishing active plans
  from historical archived plans.
- `.github/workflows/tag-main.yml:1-61` automatically tags `main`, but the agent guides and
  `docs/workflow.md` still contain manual-tag wording.

The JavaScript workflows also contain stale status, tagging, and red-first prose. Editing and testing
those lines here would be throwaway work: Plan 232 replaces `plan.js` and deletes `plan-review.js`,
while Plan 233 replaces `implement.js`. Those executable prompt corrections belong in the rewrites.

## Decisions

### D1 — DRAFT may be planned and reviewed; only READY may be implemented

The canonical lifecycle wording is:

1. the author creates a DRAFT;
2. planning and independent-review agents may read it, but implementation agents may not execute it;
3. the owner decides review findings and edits are folded into the DRAFT;
4. the required confirming review runs; and
5. only the owner may set YAML `status: READY`.

A reviewer recommendation is advisory and never mutates status. Apply this policy to
`docs/workflow.md`, `AGENTS.md`, and `CLAUDE.md`. Plan 232 owns the executable review mechanics.

### D2 — one canonical active-plan status vocabulary

YAML `status:` frontmatter is the only machine-readable status source for active plans:

| Status | Meaning | Implementable? |
|---|---|---|
| `DRAFT` | Proposed or under review; not owner-confirmed | No |
| `READY` | Owner-confirmed executable plan | Yes |
| `BLOCKED` | Cannot proceed until a named blocker changes | No |
| `DEFERRED` | Valid scope intentionally postponed | No |
| `PARTIAL` | Some work landed; remaining work needs renewed owner approval | No |
| `SUPERSEDED` | Replaced by another named authority | No |
| `COMPLETE` | Fully implemented; archive when references allow | No |

Do not use `IN_PROGRESS` or `DONE` as active-plan statuses. Execution progress belongs to the branch,
run, or PR; `ARCHIVED` is a location, not an active status. Historical files already under
`docs/plans/archive/` retain their legacy labels. Missing active YAML status is reported as `NONE`,
never inferred from body prose. Do not normalize the existing inventory in this plan.

### D3 — readiness means exactly YAML READY

Simplify `scripts/check_readiness.py` to one contract and one CLI form:

```bash
uv run python scripts/check_readiness.py <plan-path>
```

It succeeds only when the file exists, has YAML frontmatter, and its status is exactly `READY`.
Remove the body-status fallback, review-history parser, round count, blocking count, and
`user-confirmed` checks. Multi-model review and owner confirmation remain mandatory policy and
workflow obligations; they are not represented by fictitious metadata that active plans do not
write. Do not add `--status-only` or retain a second mode.

Plan 233 will make `implement.js` call this checker and validate its numeric exit code and raw output.
Until then, Plan 231 changes the deterministic utility only; it does not temporarily patch workflow
code that Plan 233 replaces.

### D4 — active and archived status diagnostics have different provenance

`tools/standing_snapshot.py` reads YAML only under active `docs/plans/`. A missing active status is
`NONE`. It may retain legacy body-marker fallback only for files already under `docs/plans/archive/`,
where D2 preserves historical labels. Add focused behavior tests for both cases.

### D5 — feature branches never tag; main tags automatically

Use one durable policy sentence in `AGENTS.md`, `CLAUDE.md`, and `docs/workflow.md`:

> Every code commit includes a patch version bump. Never create a tag on a feature branch.
> On pushes to `main`, `.github/workflows/tag-main.yml` creates the version tag when absent.

Do not change the GitHub workflow, version frequency, or release behavior. Plan 233 owns removal of
contradictory wording from the executable implementation prompt.

### D6 — test durable behavior, not the soon-to-be-replaced workflow source

Behavioral tests cover `check_readiness.py` and `standing_snapshot.py`. One narrow source-regression
test may compare canonical status/tagging anchors across the policy documents. Do not assert on
`plan.js`, `plan-review.js`, `implement.js`, red-first behavior, reviewer counts, or loop structure in
this plan.

## Tasks

### T1 — reconcile durable policy

**Outcome:** `AGENTS.md`, `CLAUDE.md`, `docs/workflow.md`, and the status convention in
`docs/plans/README.md` agree on D1, D2, and D5 and delegate orchestration mechanics to Plans 232/233.

**In:** only those four policy/index files.

**Out:** workflow JavaScript, plan inventory normalization, status changes, tagging behavior.

**Pre-change:**
`uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py -q` fails on the current
DRAFT rule, status vocabulary, and manual-tag wording.

**Verification:** `uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py -q` passes.

### T2 — simplify readiness and status diagnostics

**Outcome:** executable readiness is exact YAML READY, and legacy body status is diagnostic only for
archived plans.

**In:** `scripts/check_readiness.py`, `tests/unit/scripts/test_check_readiness.py`,
`tools/standing_snapshot.py`, and `tests/unit/tools/test_standing_snapshot.py`.

**Out:** review-history persistence, workflow integration, active-plan migration.

**Pre-change:** focused tests for body-only rejection, YAML-only success without review history, and
active/archive diagnostic provenance fail against the current utilities.

**Verification:**
`uv run pytest tests/unit/scripts/test_check_readiness.py tests/unit/tools/test_standing_snapshot.py -q`
passes.

### T3 — run the durable regression gates

**Depends on:** T1 and T2.

**Outcome:** only durable policy and utility behavior is locked; no soon-to-be-replaced workflow
implementation is tested.

**In:** the three focused test files named by T1/T2 and patch-version files required for a code commit.

**Out:** JavaScript workflow tests, a general documentation linter, new status abstractions.

**Pre-change:** N/A — integration/gate task; T1 and T2 carry the behavioral falsifiers.

**Verification:** every command under Exit gates passes.

## Scope discipline

A finding is in scope only if D1-D6 would leave durable policy contradictory, YAML readiness
ambiguous, archived diagnostics broken, or tagging ownership unclear. Review-loop design, acceptance
evidence, reviewer disposition, and implementation orchestration belong to Plans 232/233.

Prefer deletion and direct edits. Do not add a status framework, review-history format, generalized
policy linter, workflow engine, or active-plan migration.

## Exit gates

```bash
uv run ruff check scripts/check_readiness.py tools/standing_snapshot.py tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/scripts/test_check_readiness.py tests/unit/tools/test_standing_snapshot.py
uv run ruff format --check scripts/check_readiness.py tools/standing_snapshot.py tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/scripts/test_check_readiness.py tests/unit/tools/test_standing_snapshot.py
uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/scripts/test_check_readiness.py tests/unit/tools/test_standing_snapshot.py -q
uv run pytest -q
uv run pre-commit run --all-files
```

Implementation is a code change: run `uv run bump-my-version bump patch`, stage the version files
with the patch, commit conventionally on a feature branch, and do not tag. Hold at PR.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```
