---
status: READY
created: 2026-09-02
plan: 232
title: Replace iterative plan-review loops with one review and one confirmation
scope: Make plan review read-only, owner-controlled, and bounded. Use plan tasks as the only completion contract; run one ordinary Claude+Codex review and at most one confirmation review; retire the duplicate Sonnet-only workflow. Preserve policy-required high-risk review outside this ordinary pair. Do not change implementation orchestration.
depends_on: [231]
blocks: [233]
source: 2026-09-02 workflow audit, repeated review escalation on small plans, and selected design principles from hydrosolutions/lean-environmental-modeling-orchestration at commit 3ad109f2619d71aef2f94c3da28d2401a1720b44
---

# Plan 232 — one plan review, one owner decision, one confirmation

## Problem

The current `plan` workflow combines useful independent review with mechanics that reward scope
growth:

- `.claude/workflows/plan.js:85-97,267-310` runs three Claude lenses plus Codex every round.
- Findings have no stable identity or violated contract (`plan.js:49-73`), are flattened
  (`:325-329`), and are all sent to a planner (`:352-361`). Reviewer preferences therefore enter the
  plan without an owner decision.
- Convergence uses raw blocker/major counts (`:344-350`) and the full audit can repeat five times
  (`:41-44,257-364`), inviting new findings rather than checking agreed corrections.
- `.claude/workflows/plan-review.js` duplicates the loop without Codex and cannot satisfy the required
  independent-Codex floor.
- Plans already have phase tasks and verification commands, but those are not consistently written as
  observable contracts. Adding a second acceptance artifact would create another source of truth.

The external `lean-environmental-modeling-orchestration` repository supports one accountable
orchestrator, explicit completion evidence, failure bundling, and delta review. It remains design
input only. This plan does not adopt its assurance vocabulary, specialist panel, `.run-status`,
external skill, or orchestration framework.

## Decisions

### D1 — tasks are the only completion ledger

Every non-trivial task in a plan must contain:

- **Outcome:** one observable behavior or artifact;
- **In / Out:** bounded files and exclusions;
- **Verification:** an exact test node, command, or bounded `file:property` inspection; and
- **Pre-change:** for changed behavior, how the same evidence fails before implementation and for what
  reason. Use `N/A` only for documentation, mechanical, or integration/gate tasks and state why.

Dependencies stay in the existing task headings and JSON graph. Do not add an acceptance-map table,
separate ledger, or persistent run file. Task IDs are the stable keys used by planning and later by
implementation. Every task must be accounted for; no agent selects “key” tasks.

### D2 — one ordinary reviewer pair, with existing high-risk escalation preserved

The ordinary `plan` invocation runs two complementary perspectives in parallel:

1. Claude: design soundness, proportionality, owner decisions, and the smallest correct scope;
2. real Codex: repository grounding, callers/contracts, feasibility, and whether every task's stated
   verification discriminates against the current repository.

Both must return usable verdicts. A missing/failed reviewer makes the result `REVIEW_INCOMPLETE`, not
clean. The orchestrator supplies both with a compact factual packet: plan path/status, scope/non-goals,
task contracts, reviewed HEAD, and named authoritative docs. Remove the separate prose-grounding
agent.

This pair is the default floor, not an absolute maximum. The already-enumerated high-risk cases in
`docs/workflow.md` and anything the owner flags still receive the policy-required additional
independent panel as a separate orchestrator-owned step. Ordinary work does not gain reviewers
autonomously. For high-risk work, the workflow's final recommendation remains incomplete until that
panel's result is supplied; the ordinary pair cannot waive it.

### D3 — findings identify contract defects, not preferences

Each reviewer returns a separate report. Every finding contains:

- stable per-report ID (`CLAUDE-001`, `CODEX-001`);
- severity;
- exact plan section or repository `file:line` evidence;
- the violated decision, task, repository rule, or verified regression; and
- the smallest sufficient correction.

A blocker/major is admissible only when the plan is unsafe, contradictory, unexecutable, incomplete
against stated scope, or protected by non-discriminating evidence. Alternative architecture,
hardening, abstraction, and desirable follow-ons are not blocking unless the plan requires them.
Minors remain visible but are never auto-applied. Do not flatten, deduplicate, upgrade, or silently
discard the two reports through another model.

### D4 — review is read-only, owner-disposed, and bounded

`plan` has two modes and never edits the plan:

**Review** runs the ordinary pair once and returns the two reports plus a compact plan fingerprint and
task/gate identity manifest. The packet does not repeat the plan text or add repository sidecars.

The owner then assigns every blocker/major one disposition:

- `fix` — amend the plan;
- `reject` — reviewer claim is wrong, with rationale;
- `follow-up` — genuine but outside the stated scope, with rationale; or
- `accept-risk` — explicitly accept a known in-scope concern, with rationale.

Only the owner may choose `accept-risk`; a model never infers it. Accepted risks remain visible in the
terminal output.

**Confirm** receives the prior reports, fingerprint, and owner dispositions. Since the compact packet
contains no prior plan text, the same perspectives review the complete current plan once while
rechecking prior findings and dispositions. Retain compact fail-closed start and terminal plan-staleness
checks against authoritative upstream movement.

Return `READY`, `NOT_READY`, or `REVIEW_INCOMPLETE` as advice plus any `acceptedRisks`. The owner alone
sets YAML READY. For high-risk work, `READY` additionally requires the externally commissioned panel
to have returned without an unresolved or owner-unaccepted concern. Remove `while`, `maxRounds`,
raw-count convergence, and the plan-writing agent.

### D5 — one executable plan-review path

Delete `.claude/workflows/plan-review.js` and its tooling references. If Codex is unavailable, a
Claude-only diagnostic cannot be represented as the required review or recommend READY.

### D6 — structural guards stay honest and small

Use focused Python source-regression tests only for dangerous anchors: no review loop; no plan writer;
complete Claude+Codex slots; no mutation before owner action; confirm rechecks the current plan once; reviewer
failure cannot yield READY. These tests do not prove workflow runtime behavior. `node --check` proves
syntax only. Do not build a JavaScript workflow harness.

As the manual behavioral check, invoke the rewritten workflow once in review mode against Plan 233
and record in the PR that both reviewers returned, the plan was not mutated, and task-by-task findings
were produced. Plan 233's later confirmation will complete the dogfood path after owner disposition.

## Tasks

### T1 — define the lean task and review contract

**Outcome:** policy defines tasks as the single completion ledger, the ordinary reviewer pair, the
high-risk exception, admissible findings, owner dispositions, and the one-confirmation limit.

**In:** `docs/workflow.md`, `AGENTS.md`, `CLAUDE.md`, and new-plan guidance in
`docs/plans/README.md`.

**Out:** existing plan retrofits/statuses, implementation orchestration, external package adoption.

**Pre-change:**
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py::test_policy_uses_tasks_as_the_only_completion_ledger -q`
fails because current policy lacks the task outcome/pre-change contract and still documents loops.

**Verification:**
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py::test_policy_uses_tasks_as_the_only_completion_ledger -q`
passes.

### T2 — rewrite `plan` and remove `plan-review`

**Depends on:** T1.

**Outcome:** `plan` implements D2-D5 without an autonomous revision/convergence loop, and the
Sonnet-only duplicate no longer exists.

**In:** `.claude/workflows/plan.js` and deletion of `.claude/workflows/plan-review.js`.

**Out:** implementation workflow, generic reviewer framework, persistent state, extra standard
reviewer roles.

**Pre-change:** source-regression tests fail on the current three Claude lenses, `while`, planner
writer, flattened findings, and legacy workflow file.

**Verification:** `node --check .claude/workflows/plan.js` and
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py -q` pass.

### T3 — verify the bounded source contract and dogfood review mode

**Depends on:** T2.

**Outcome:** dangerous source regressions are locked without claiming behavioral simulation, and one
real review-mode invocation demonstrates the bounded path on Plan 233.

**In:** `tests/unit/tooling/test_plan_workflow_contract.py`, version files, and one recorded manual
workflow invocation.

**Out:** JavaScript test framework, generalized linter, extra review round, automatic Plan 233 edits.

**Pre-change:** N/A — gate/dogfood task; T1/T2 contain the source falsifiers.

**Verification:** `uv run pytest tests/unit/tooling/test_plan_workflow_contract.py -q` passes, and
`Workflow({ name: "plan", args: { mode: "review", planPath: "docs/plans/233-evidence-led-implementation-workflow.md", repo: "." } })`
returns a complete pair without changing the plan file.

## Scope discipline

A finding is in scope only if D1-D6 would leave ordinary plan review unsafe, non-independent,
owner-bypassing, or unable to account for every task. Extra reviewers beyond existing high-risk
policy, new modes, hashes, persistent state, acceptance ledgers, generic schemas, and active-plan
migrations are follow-ups, not blockers.

## Exit gates

```bash
node --check .claude/workflows/plan.js
uv run ruff check tests/unit/tooling/test_plan_workflow_contract.py
uv run ruff format --check tests/unit/tooling/test_plan_workflow_contract.py
uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/tooling/test_plan_workflow_contract.py -q
uv run pytest -q
uv run pre-commit run --all-files
```

Implementation removes `.claude/workflows/plan-review.js`, updates affected docs, runs
`uv run bump-my-version bump patch`, commits conventionally on a feature branch, and never tags.
Hold at PR.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Rollout

Implement Plan 231 first. After Plan 232 lands, the owner disposes the dogfood findings for Plan 233
and runs its single confirm invocation before deciding whether Plan 233 becomes READY.
