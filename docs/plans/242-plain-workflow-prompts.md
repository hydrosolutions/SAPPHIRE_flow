---
status: READY
created: 2026-09-04
plan: 242
title: Replace automated workflows with plain prompt skills
scope: Remove the plan and implement workflow engines and their relay machinery. Replace them with small, independent plan, implement, and review prompts. Preserve readiness, review, version, and pre-merge test policy in words plus the existing readiness check.
depends_on: []
supersedes: [231, 232, 233]
---

# Plan 242 — plain workflow prompts

## Why

Dogfood demonstrated that even the reduced workflow engines add launcher, timeout, relay,
and permission failure modes. The useful behavior is instruction and judgment, not
orchestration code.

## Tasks

### T1 — replace workflow engines with prompts

**Outcome:** `/plan`, `/implement`, and `/review` are short Markdown skills. Each
performs one requested pass and stops. They do not call each other, launch reviewers,
retry, reconcile findings, or manage state.

**In:** `.claude/skills/{plan,implement,review}/SKILL.md`; remove
`.claude/workflows/{plan,implement}.js`, `.claude/workflow-capabilities.json`,
`scripts/codex-review.sh`, its temporary-file permissions and ignore rules, and the
three machinery-specific workflow/relay tests.

**Out:** product code, scientific behavior, CI, deployment, and automatic reviewer
or fixer orchestration.

**Pre-change:** N/A — this is a mechanical replacement of development tooling;
the existing files provide the before state.

**Verification:** all three skills have valid `name` and `description` frontmatter;
the removed workflow and relay files are absent.

### T2 — document the manual iteration loop

**Outcome:** repository guidance says one agent owns each plan or implementation
pass. The owner deliberately starts independent Claude and Codex reviews, resolves
their findings, and requests any scoped correction. Focused checks run during
implementation; the full suite remains mandatory after the final code change and
before merge.

**In:** `docs/workflow.md`, `AGENTS.md`, `CLAUDE.md`, this plan, the plan index,
and the existing readiness checker and policy tests.

**Out:** automatic READY decisions, repeated review loops, output schemas, and new
enforcement code.

**Pre-change:** N/A — this task changes documentation and prompt policy only; the
existing guidance provides the before state.

**Verification:**
`uv run pytest tests/unit/scripts/test_check_readiness.py tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/deploy/test_dockerfile_operator_scripts.py -q`
passes.

## Exit gates

```bash
uv run pytest tests/unit/scripts/test_check_readiness.py tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/deploy/test_dockerfile_operator_scripts.py -q
uv run ruff check src/ tests/ scripts/check_readiness.py
uv run ruff format --check src/ tests/ scripts/check_readiness.py
```

## Pre-merge gate

After the final code change and before merge:

```bash
uv run pytest
```

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"]}
  ]
}
