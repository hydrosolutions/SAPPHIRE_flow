---
status: SUPERSEDED
created: 2026-09-02
plan: 233
title: Make implementation completion task-evidenced and bounded
scope: Rewrite the implement workflow so every READY-plan task is independently evidenced, findings require owner disposition, and repair is limited to one confirmed pass. Make both workflow entrypoints runtime-compatible and replace duplicated plan text with a compact deterministic contract manifest. Preserve the default Claude+Codex floor, policy-required high-risk review, readiness/staleness/fresh-commit safeguards, version policy, and hold-at-PR. Do not change product code or scientific models.
depends_on: [232]
blocks: []
source: 2026-09-02 workflow audit; selected design principles from hydrosolutions/lean-environmental-modeling-orchestration at commit 3ad109f2619d71aef2f94c3da28d2401a1720b44; 2026-09-03 dogfood run wf_30695be4-c91
---

# Plan 233 — “done” means every task has independent evidence

Superseded by Plan 242 after PR #247 dogfood showed that the executable mechanics
were over-engineered. Retained as design history; `docs/workflow.md` is current.

## Problem

The current `.claude/workflows/implement.js` has useful safety gates, but its completion and repair
mechanics still permit gaps and scope growth:

- the implementer selects “KEY” acceptance criteria (`implement.js:377-406`) instead of accounting
  for every planned task;
- the verifier checks commit/diff/worktree/gates (`:357-424`) but does not independently prove each
  planned outcome;
- red-first coverage is one global boolean (`:65-83,377-406`), so it cannot identify which task lacks
  discriminating evidence;
- review findings are flattened and immediately sent to a fixer (`:524-562`) without owner
  disposition; and
- full review/fix/verify can repeat five times (`:469-608`), repeatedly adding scope and commits.

The external lean-orchestration package contributes useful principles—one accountable implementer,
failure bundles, evidence before completion, delta review, and human adoption authority—but remains
design input only. This plan does not adopt its assurance modes, `.run-status`, specialist panel,
domain packs, or orchestration framework.

Commit `8afaffeb` implemented the original T1-T4 and passed source tests, independent review, the full
test suite, and pre-commit. The first real `/plan` dogfood run nevertheless stopped before either
reviewer spawned:

- the workflow runtime rejects `Date.now()` and `Math.random()` because they break resumability;
- the structured context duplicated the 13,747-byte plan plus parsed task contracts, producing an
  observed 19,185-byte call that truncated and failed twice; and
- after those schema failures, the context agent returned schema-shaped placeholders instead of an
  explicit failure.

The independent snapshot guard rejected the placeholders and returned `REVIEW_INCOMPLETE`. The same
guard already exists in `implement.js`, so this was fail-closed in both entrypoints; the defect is that
valid reviews cannot start. Returning only a Tasks excerpt is not sufficient: current READY plans have
Tasks sections over 33 KB and complete documents up to 83,300 bytes.

The next dogfood run, `wf_e96eb8dc-290`, proved the compact path works: both preflights returned a valid
655-byte manifest. It then exposed one remaining runtime mismatch before review: the workflow sandbox
does not provide `TextEncoder`, used by both scripts for UTF-8 fingerprints and byte limits.

After that fix, `wf_340221be-ec2` reached both reviewers but the real Codex relay could not start: its
absolute `/tmp` prompt path was outside the worktree-scoped sandbox. Claude completed cleanly and the
workflow correctly returned `REVIEW_INCOMPLETE` rather than accepting the missing Codex result.

The next run proved `.claude/` is also unsuitable because the harness treats it as sensitive and does
not let project settings override that protection. Its Claude half passed the T5 checks, while Codex
again failed closed before launch.

## Decisions

### D1 — READY-plan tasks are the implementation ledger

Read the task contracts introduced by Plan 232. The workflow rejects a READY plan before editing when
a task lacks an Outcome or exact Verification, or when a behavior-changing task lacks a plausible
Pre-change falsifier. Do not create or parse a second acceptance-map artifact.

The implementer reports one entry per task ID. Behavioral tasks keep separate `beforeEvidence` and
`afterEvidence`: the named verification must fail before the change for the expected reason and pass
afterward. Documentation/mechanical tasks use their declared bounded inspection rather than a
synthetic red test. A task may use `N/A` only when the READY plan already gives the reason.

If the evidence is already green for a behavior the plan claims is missing, or the planned check
cannot discriminate the change, return `PLAN_INCOMPLETE`. Do not invent a substitute requirement.

### D2 — one implementer and one root-cause bundle

One Sonnet implementer owns the complete patch and follows the dependency graph in order. Do not split
the patch across coding agents.

While editing, the implementer runs only task-targeted checks. When related checks fail, it inspects
the bounded group and reports one root-cause bundle: task IDs, observed evidence, common cause, and
smallest in-scope correction. It does not create sidecar state or patch symptoms one by one merely to
turn checks green.

At a stable candidate, the implementer performs the patch version bump and commits without tagging,
pushing, opening a PR, or merging. It does not run the complete repository gate set; the independent
verifier owns that single full run.

### D3 — independent verification proves every task once

After the implementation commit, a read-only verifier independently derives:

- HEAD differs from the recorded pre-build HEAD and the base diff is non-empty;
- the worktree is clean and the patch-version change is present;
- every READY-plan task ID has `PASS`, `FAIL`, or `UNVERIFIABLE` evidence from its exact Verification;
- all plan exit gates pass from the committed state; and
- deviations and changed files stay within scope.

This is the only complete exit-gate run for that committed candidate. Missing task entries, failed or
unverifiable evidence, a non-fresh/dirty/stale/empty diff, absent version bump, or failed gate yields
`IMPLEMENTATION_INCOMPLETE` and stops before patch review. Report the whole evidence bundle; an
implementer's “done” claim cannot set the verdict and does not trigger an automatic repair loop.

### D4 — one ordinary patch review, then owner disposition

After D3 passes, run the Plan 232 ordinary pair once:

1. Claude checks plan fit, design, proportionality, and owner decisions;
2. real Codex checks repository correctness, callers/contracts, meaningful tests, and the verifier's
   task evidence.

Both must return. Keep reports separate and apply Plan 232's finding/admissibility schema. A failed
reviewer makes the panel incomplete; minors are visible but never auto-fixed. Policy-required extra
review for the already-enumerated high-risk cases remains a separate orchestrator-owned gate. The
ordinary workflow does not add reviewers autonomously. Its result is included in `engineeringReview`;
high-risk work cannot reach `PR_READY` until that required result is supplied and has no unresolved
or owner-unaccepted concern.

If the ordinary pair has no blocker/major, it may recommend `PR_READY`. Otherwise return
`OWNER_DECISION_REQUIRED` without mutating the patch. The owner assigns `fix`, `reject`, `follow-up`,
or explicit `accept-risk` with rationale. Models never infer risk acceptance, and accepted risks stay
visible in terminal output.

### D5 — confirm permits one owner-scoped repair and delta review

`implement` accepts `mode: "build" | "confirm"`; there is no `while` or `maxRounds`.

Confirm requires the prior reviewer reports, dispositions, exact reviewed commit, and unchanged READY
plan. One fixer addresses only `fix` blocker/major findings, runs affected task-targeted checks, bumps
the patch version, and commits. A required repair must advance HEAD from the reviewed commit. If all
findings are rejected, followed up, or risk-accepted, do not make an empty commit.

The verifier reruns every task's evidence and the complete exit gates once on the resulting candidate.
The same ordinary pair then performs one delta-only confirmation of fixes, rationales, and regressions
introduced in the touched area. It does not start a general audit.

Any failed task/gate, invalid disposition, unresolved fix, introduced regression, reviewer failure,
staleness, or non-fresh required repair returns `NOT_READY` and stops. There is no second automated
fixer pass. A human may still explicitly accept a documented residual risk under repository policy.

Plan-review confirmation keeps the prior reviewed fingerprint as the old evidence anchor and validates
the current plan independently. It passes both fingerprints to the delta reviewers and must allow them
to differ when an owner-disposed fix changed the plan; a changed plan is the normal input to confirmation,
not a packet-mismatch failure.

### D6 — terminal output stays decision-useful

Return only three verdicts plus evidence:

| Field | Values | Meaning |
|---|---|---|
| `taskCompletion` | `COMPLETE`, `INCOMPLETE`, `UNVERIFIABLE` | Every READY-plan task has independent evidence |
| `engineeringReview` | `PASS`, `BLOCKED`, `INCOMPLETE` | Required engineering reviewers returned and blocking findings are resolved or owner-disposed |
| `recommendation` | `PR_READY`, `NOT_READY` | Whether the implementation may be handed to the human PR decision |

Also return one canonical per-task and per-gate evidence record, separate reviewer reports, deviations,
and `acceptedRisks` list. Command evidence uses the manifest fingerprint plus numeric exit and one
`outputExcerpt` of at most 1,000 characters; full raw command output is never copied into structured
agent responses. Reviewer prompts request findings only, without summaries or repeated plan/task text.
The terminal result excludes plan snapshots, task contracts, intermediate implementer/fixer/verifier
objects, and Codex `rawVerdict`; those may be checked internally but must not duplicate the canonical
evidence and parsed reviewer records returned to the owner. The public Codex record retains its exit
code and `rawVerdictPresent` boolean so the owner can see that the internal non-empty check passed.
Scientific or operational questions use ordinary plan tasks: completion means the declared question
was answered with its required evidence, regardless of which hypothesis outcome occurred.

`PR_READY` never authorizes merge, deployment, or scientific adoption; those remain human decisions.
No “pass with notes” may hide an incomplete task.

### D7 — preserve useful safety boundaries

Keep Plan 231's deterministic YAML READY checker, compact preflight/final staleness checks, dedicated
Codex relay, fresh-commit advancement, clean-worktree/base-diff guards, patch bumps, feature-branch
no-tag rule, and hold-at-PR. Represent the tag baseline in agent responses as a count plus one
deterministic tag-set fingerprint, never the complete tag-name list; preflight and final verification
compare both values. Before any edit, `implement.js` must invoke
`uv run python scripts/check_readiness.py <plan-path>` through its read-only preflight agent, require
the numeric exit code and a bounded output excerpt, and derive readiness only from a validated zero exit code—not
from the model's interpretation. Simplify prompt prose but not these predicates.

Use narrow Python source-regression tests for dangerous anchors and terminal predicates. They do not
simulate the workflow runtime; `node --check` proves syntax only. Do not introduce a JavaScript
workflow framework, database, status directory, generic orchestration layer, or extra model tier.

### D8 — prompt identity must be deterministic and content-bound

Workflow scripts must not call `Date`, `Date.now()`, or `Math.random()`. Define one small deterministic
fingerprint function and derive each temporary Codex prompt path from the final `codexPrompt` content,
after the prompt is assembled. Different review content therefore uses different paths. Concurrent
identical prompts may share a path because their content is identical. `scripts/codex-review.sh` must
read the prompt into memory exactly once before invoking Codex and reject empty/whitespace content or
the workflow's fixed cleared sentinel. A clearing race therefore either uses the already-captured
identical prompt or fails before Codex; it cannot review the sentinel or content from another run.
Do not add locks, persistent run state, or a caller-managed invocation token.

The workflow runtime also lacks `TextEncoder`, `Buffer`, `crypto`, `structuredClone`, `process`, and
`require`. Use one small local, surrogate-safe UTF-8 byte helper built from available string/array
primitives for both FNV-1a and byte ceilings. Source regressions forbid those unavailable globals in
both workflow scripts; `node --check` remains syntax evidence only.

Prompt files use one narrowly ignored `sapphire-flow-*-codex-review.md` pattern at the worktree root,
outside protected configuration directories. Shared permissions are limited to those files and the two
exact relay command prefixes. The relay still clears the prompt after its final attempt. Do not broaden
file-write or Bash permissions.

### D9 — pass a compact deterministic manifest, not the plan

Extend `scripts/check_readiness.py` with a machine-readable inspection mode that works for DRAFT plan
review as well as READY implementation. It validates the plan structure and emits one compact JSON
manifest containing:

- YAML status and a whole-document fingerprint;
- each task ID, a mechanically derived Pre-change mode (`N/A` versus executable), and deterministic
  fingerprints of its exact Verification and Pre-change;
- fingerprints of the declared Exit gates; and
- a validity flag and bounded diagnostics.

Inspection rejects duplicate required fields within a task and requires exactly one bash/sh command
fence in `## Exit gates`; it never silently overwrites or ignores ambiguous contract text.

The UTF-8 encoded manifest must not exceed 8,192 bytes. Exceeding that ceiling is an explicit invalid
inspection result, never truncation. Unit tests measure the largest current active source and the
greatest current parsed task count, then construct valid canonical plans at those two scales. Both must
produce valid serialized manifests below the ceiling; an invalid fallback manifest is not evidence of
capacity. This leaves measured headroom beneath the workflow runtime's observed successful 13,709-byte
structured-output probe.

The normal readiness command and exit semantics remain unchanged. The fingerprint algorithm is the
same small deterministic function used by the workflow scripts; it is an identity check, not a
cryptographic security boundary.

The context relay returns only the inspection command's numeric exit, unedited raw output, and compact
Git facts. Workflow code parses and validates that output. It never asks a model to return `planText`,
a Tasks excerpt, or restated contracts. Implementers, verifiers, and both reviewers read `planPath`
directly. Evidence records use manifest fingerprints rather than repeating declared commands; task/gate
ID sets must match exactly, and confirmation binds to the whole-document fingerprint instead of
embedding a prior plan snapshot. A failed command, invalid JSON, invalid or oversized manifest,
invalid fingerprint, missing task/gate, placeholder, or non-zero exit stops before review or editing.

`docs/workflow.md` must describe this same path/manifest/fingerprint contract instead of requiring
complete task contracts or verbatim plan snapshots in context and results. The policy-coherence test
guards that migration across the policy and both workflow entrypoints.

The inspection mode does not guess whether work is semantically behavioral. It derives only whether
the declared Pre-change is normalized `N/A`. Reviewers and the implementer still reject an `N/A` used
for behavior-changing work; do not add a second `taskKind` field to the plan format.

### D10 — real workflow execution is an exit gate

Source guards and `node --check` remain necessary but are not runtime proof. This DRAFT received a
manual independent review while `/plan` was under repair, its findings were folded in, and the owner
restored READY before T5 started.
After D8-D9 are implemented, run `/plan` on the still-READY plan and prove that both ordinary reviewers
return usable reports without changing the plan or Git state. Then run `/implement` against this
already-implemented plan and prove that it reaches task evidence and stops without a commit because
the behavioral Pre-change checks are already green. The later successful build of an unrelated
owner-selected READY plan remains the rollout check; do not create work merely to exercise the
harness.

## Implemented baseline — commit `8afaffeb`

The original T1-T4 below describe the committed first implementation. They are retained as history
but are not the outstanding task ledger parsed by the repaired workflows.

### T1 — document task-evidenced implementation

**Outcome:** repository policy defines per-task before/after evidence, the single full verifier run,
owner dispositions including accepted risk, the high-risk review exception, and human adoption
authority.

**In:** `docs/workflow.md`, `AGENTS.md`, `CLAUDE.md`, and
`tests/unit/tooling/test_implement_workflow_contract.py`.

**Out:** release authority, CI policy, status vocabulary, product test rules, ForecastInterface.

**Pre-change:**
`uv run pytest tests/unit/tooling/test_implement_workflow_contract.py::test_policy_requires_evidence_for_every_task -q`
fails on current key-criteria and review-loop policy.

**Verification:**
`uv run pytest tests/unit/tooling/test_implement_workflow_contract.py::test_policy_requires_evidence_for_every_task -q`
passes.

### T2 — replace the implementation loop

**Depends on:** T1.

**Outcome:** `implement.js` implements D1-D7 with one implementer, one independent full gate run per
candidate, one ordinary review, owner disposition, and at most one confirm repair/delta review. The
plan and implementation Codex relays use invocation-unique prompt files, and deterministic readiness
parsers reject nested or duplicate YAML `status` keys.

**In:** `.claude/workflows/implement.js`, `.claude/workflows/plan.js`,
`tests/unit/tooling/test_implement_workflow_contract.py`, and
`tests/unit/tooling/test_plan_workflow_contract.py`; `scripts/check_readiness.py`,
`tools/standing_snapshot.py`, `tests/unit/scripts/test_check_readiness.py`, and
`tests/unit/tools/test_standing_snapshot.py`.

**Out:** product code, JavaScript framework, persistent state, automatic extra reviewers, automatic
minor/follow-up work.

**Pre-change:** source-regression tests fail on model-interpreted readiness, key-criteria selection,
aggregate red-first state, `while`/`maxRounds`, flattened findings, immediate fixer invocation,
repeated full gates, and acceptance of nested or duplicate YAML `status` keys.

**Verification:** `node --check .claude/workflows/implement.js`,
`node --check .claude/workflows/plan.js`, and
`uv run pytest tests/unit/scripts/test_check_readiness.py tests/unit/tools/test_standing_snapshot.py tests/unit/tooling/test_plan_workflow_contract.py tests/unit/tooling/test_implement_workflow_contract.py -q`
pass.

### T3 — lock only dangerous source and terminal regressions

**Depends on:** T2.

**Outcome:** focused tests protect complete task accounting, verifier-owned full gates, owner-before-
fix ordering, one repair/delta limit, and fail-closed `PR_READY` predicates without claiming runtime
simulation.

**In:** `tests/unit/tooling/test_implement_workflow_contract.py` and version files.

**Out:** workflow runtime harness, generalized source linter, extra repair or reviewer pass.

**Pre-change:** N/A — regression/gate task; T1/T2 contain the falsifiers.

**Verification:** `uv run pytest tests/unit/tooling/test_implement_workflow_contract.py -q` passes.

### T4 — run final gates and record the first-use check

**Depends on:** T3.

**Outcome:** the rewritten source passes repository gates, and repository policy records the first-use
behavioral adoption check without pretending source tests simulate the workflow.

**In:** final verification and a short `docs/workflow.md` first-use note.

**Out:** executing an unrelated product plan merely to test this PR; a permanent run ledger.

**Pre-change:** N/A — final gate/adoption task.

**Verification:** every Exit gates command passes.

## Tasks

### T5 — make context and prompt identity runtime-safe

**Outcome:** both workflow entrypoints evaluate in the Claude workflow runtime without forbidden
entropy, and their preflight uses the compact deterministic manifest in D9 rather than returning plan
text through structured output. Exact task and gate evidence remains fingerprint-bound, agent command
output is capped, and terminal results do not duplicate evidence or internal reports. Plan confirmation
accepts a separately validated corrected fingerprint while preserving the prior fingerprint for delta
review. Git tag state is transported as a count and fingerprint. Ambiguous duplicate task fields or
multiple Exit-gate command fences fail inspection, and valid synthetic scale tests prove manifest
capacity against the current plan corpus. UTF-8 fingerprinting and byte ceilings use only globals
available in the workflow sandbox while preserving Python/JavaScript fingerprint parity.
The dedicated Codex prompt path is worktree-local, gitignored, outside protected configuration
directories, and covered by narrow shared permissions; it does not require an external writable directory.

**In:** `.claude/workflows/plan.js`, `.claude/workflows/implement.js`, `.claude/settings.json`, `.gitignore`,
`scripts/check_readiness.py`, `scripts/codex-review.sh`, `docs/workflow.md`,
`tests/unit/scripts/test_check_readiness.py`,
`tests/unit/tooling/test_codex_review_relay.py`,
`tests/unit/tooling/test_plan_workflow_contract.py`,
`tests/unit/tooling/test_implement_workflow_contract.py`,
`tests/unit/tooling/test_workflow_policy_coherence.py`, and version files.

**Out:** product code; a generic Markdown framework; a new helper executable; chunked plan transport;
locks or persistent workflow state; caller-managed invocation tokens; changes to review count,
dispositions, or repair limits.

**Pre-change:** run
`uv run pytest tests/unit/scripts/test_check_readiness.py::TestManifestJson tests/unit/tooling/test_codex_review_relay.py tests/unit/tooling/test_plan_workflow_contract.py::TestPlanWorkflowContract::test_context_uses_compact_manifest tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_context_uses_compact_manifest tests/unit/tooling/test_plan_workflow_contract.py::TestPlanWorkflowContract::test_prompt_identity_is_runtime_safe tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_prompt_identity_is_runtime_safe tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_agent_outputs_are_bounded_and_terminal_is_deduplicated tests/unit/tooling/test_workflow_policy_coherence.py::TestWorkflowPolicyCoherence::test_context_policy_uses_manifest_references -q`.
It fails because inspection JSON and the relay regression do not exist, both contexts require
`planText`, the scripts use runtime-forbidden time/random primitives, and the relay does not reject its
cleared sentinel before invoking Codex. Agent evidence is still unbounded and duplicated in the
terminal result, and policy still requires full task contracts and verbatim snapshots.

For the independent-review corrections, run
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py::TestPlanWorkflowContract::test_confirmation_accepts_changed_validated_plan tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_git_tag_state_uses_compact_identity tests/unit/scripts/test_check_readiness.py::TestManifestJson::test_rejects_duplicate_required_task_fields tests/unit/scripts/test_check_readiness.py::TestManifestJson::test_rejects_multiple_exit_gate_fences tests/unit/scripts/test_check_readiness.py::TestManifestJson::test_valid_synthetic_manifests_cover_current_plan_scale -q`.
Before the corrections it fails because the five named contracts are absent or violated.

For the second dogfood correction, run
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py::TestPlanWorkflowContract::test_runtime_helpers_avoid_unavailable_globals tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_runtime_helpers_avoid_unavailable_globals -q`.
It fails while either workflow uses `TextEncoder` or another measured unavailable runtime global.

For the worktree-sandbox correction, run
`uv run pytest tests/unit/tooling/test_plan_workflow_contract.py::TestPlanWorkflowContract::test_codex_prompt_path_is_workspace_local tests/unit/tooling/test_implement_workflow_contract.py::TestImplementWorkflowContract::test_codex_prompt_path_is_workspace_local -q`.
It fails while either workflow writes its prompt outside the worktree, under protected `.claude/`, or
lacks the narrow ignore/permission entries.

**Verification:** all four Pre-change commands pass, `node --check .claude/workflows/plan.js` passes, and
`node --check .claude/workflows/implement.js` passes.

### T6 — dogfood both entrypoints

**Depends on:** T5.

**Outcome:** real workflow runs get past preflight with bounded context: `/plan` returns usable Claude
and Codex reports without changing the plan or Git state, while `/implement` reaches task evidence and
correctly refuses to reimplement already-green Plan 233 behavior without changing Git state.

**In:** one clean dogfood worktree at the T5 commit and a concise result note in this plan.

**Out:** implementing an unrelated plan, a synthetic test-only plan, a permanent run ledger, or
weakening a guard to obtain a green run.

**Pre-change:** N/A — dogfood/adoption gate; historical runs `wf_30695be4-c91`,
`wf_e96eb8dc-290`, `wf_340221be-ec2`, and the protected-`.claude/` run at `aad3fe14` evidence the
pre-T5 failures but are not re-executable.

**Verification:** in a fresh Claude session, run
`/plan {"planPath":"docs/plans/233-evidence-led-implementation-workflow.md","mode":"review","riskClass":"ordinary"}`;
record a run ID where both reports have `reviewerFailed: false`, the Codex report has `exitCode: 0` and
`rawVerdictPresent: true`, the result is not `REVIEW_INCOMPLETE`, and the plan, worktree, and HEAD are
unchanged. The manual fallback review and owner confirmation must already have made this plan READY
before T5 began. Then run
`/implement {"planPath":"docs/plans/233-evidence-led-implementation-workflow.md","mode":"build","riskClass":"ordinary","baseBranch":"codex/lean-workflow-231-233"}`;
record a run ID that stops as `PLAN_INCOMPLETE`/`NOT_READY` on already-green Pre-change evidence, with
the worktree clean and HEAD unchanged.

## Scope discipline

A finding is in scope only if D1-D10 could permit unevidenced completion, bypass independent review or
owner authority, prevent a valid workflow from starting, or weaken an existing implementation safety
predicate. Acceptance maps, extra reviewers beyond existing high-risk policy, additional modes,
persistent state, generalized orchestrators, product changes, and unrelated test-policy reform are
out of scope.

## Exit gates

```bash
node --check .claude/workflows/plan.js
node --check .claude/workflows/implement.js
shellcheck scripts/codex-review.sh
uv run ruff check scripts/check_readiness.py tests/unit/scripts/test_check_readiness.py tests/unit/tooling/test_codex_review_relay.py tests/unit/tooling/test_plan_workflow_contract.py tests/unit/tooling/test_implement_workflow_contract.py tests/unit/tooling/test_workflow_policy_coherence.py
uv run ruff format --check scripts/check_readiness.py tests/unit/scripts/test_check_readiness.py tests/unit/tooling/test_codex_review_relay.py tests/unit/tooling/test_plan_workflow_contract.py tests/unit/tooling/test_implement_workflow_contract.py tests/unit/tooling/test_workflow_policy_coherence.py
uv run pytest tests/unit/scripts/test_check_readiness.py tests/unit/tooling/test_codex_review_relay.py tests/unit/tooling/test_plan_workflow_contract.py tests/unit/tooling/test_implement_workflow_contract.py -q
uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/tooling/test_plan_workflow_contract.py tests/unit/tooling/test_implement_workflow_contract.py -q
uv run pytest -q
uv run pre-commit run --all-files
```

Implementation runs `uv run bump-my-version bump patch`, stages version files with workflow, tests,
and docs, commits conventionally on a feature branch, and never tags. Hold at PR.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T5"], "parallel": false},
    {"id": "phase-2", "tasks": ["T6"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Rollout

Dogfood runs `wf_30695be4-c91`, `wf_e96eb8dc-290`, `wf_340221be-ec2`, and the protected-`.claude/`
run at `aad3fe14` failed before a complete review and reopened this plan. T6 is the bounded rerun for
both entrypoints. After T6 passes, use the next owner-selected READY implementation
as the successful-build adoption check and record in that PR that every task received evidence,
complete gates ran once per candidate, findings waited for owner disposition, and no second repair
loop occurred. Do not add a special test-only implementation mode.
