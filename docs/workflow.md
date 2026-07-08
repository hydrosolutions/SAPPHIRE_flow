# SAPPHIRE Flow — Workflow Conventions

## Orchestration Protocol

**The orchestrator (Opus) NEVER writes code directly.**

1. **Explore** the codebase before each phase to gather context for agent prompts
2. **Delegate** all implementation work to Sonnet 4.6 general-purpose agents
3. **Coordinate** parallel vs sequential execution based on the plan's dependency graph
4. **Review** all changes via `git diff` after agents complete
5. **Iterate** by delegating fixes to subagents if issues found
6. **Commit** only when all tests pass

## Plan Structure

Plans are organized as **phases** containing **tasks**. Each task is a unit of
work delegatable to a single subagent.

Each task specifies:

1. **Scope** — what is in / explicitly out of scope (one sentence each)
2. **Verification** — exact `uv run` command that must pass

Interface details (types, Protocols, signatures) belong in implementation-level
plans only, not high-level plans. The subagent reads the codebase and docs.

Plans end with a JSON dependency graph:

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1a", "1b", "1c"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["2a", "2b"],
      "parallel": true,
      "depends_on": ["phase-1"]
    }
  ]
}
```

Tasks within a phase run in parallel unless marked otherwise. Phases run
sequentially based on `depends_on`.

## Preserve Existing Logic

**Do not break pre-existing data flows, code logic, or documented workflows without
extremely good reason.** The architecture and flow designs represent deliberate decisions.

- Before changing any existing behavior, verify it is genuinely wrong — not just
  unfamiliar or different from what you would have chosen.
- If you believe existing logic or a documented workflow must change, **stop and discuss
  with the user first.** Present the evidence for why the change is necessary.
- Refactors that preserve behavior are fine. Changes that alter behavior require explicit
  approval.

## Plan Readiness

- Plans start as `status: DRAFT`. No subagent runs from a DRAFT plan.
- Opus self-reviews the plan before presenting it to the user.
- User confirms the plan. Opus sets `status: READY`.
- A second review round is required only if the user requests changes.
- Do not present a plan as ready without user confirmation.

### Plan status vocabulary

Active plans in `docs/plans/` use one of the following statuses in their
frontmatter:

- **DRAFT** — plan is being written or has not yet been confirmed by the user.
  Not ready for implementation. No subagent runs from a DRAFT plan.
- **READY** — plan is confirmed and ready to execute. Subagents may be
  dispatched.
- **IN_PROGRESS** — plan is actively being implemented. Used while a session
  is mid-execution.
- **DEFERRED** — scope-validated, intentionally postponed to a future version
  (v0b, v1, etc.). Distinct from `DRAFT` (unplanned / not ready) and from
  `ARCHIVED` (closed historical record). Deferred plans stay in `docs/plans/`
  (not `archive/`) until they are re-promoted (flipped back to `DRAFT` or
  `READY`) or archived.
- **DONE** — plan is complete. Typically archived promptly; see below.

Plans that have been moved to `docs/plans/archive/` are collectively referred
to as **ARCHIVED**. Archive is the terminal state: closed historical records
that are no longer part of the active registry.

Note: this codification does **not** backfill legacy archive-only labels such
as `COMPLETE`, `RESOLVED`, or archived `READY`. Historical plan records in
`docs/plans/archive/` keep whatever status they were archived with.

## Multi-Model Review

Multi-model review is **mandatory for all non-trivial work** — plans and
patches/code changes alike. The goal is convergent, independently-checked
output before any human approval gate.

**Trivial exemption** (single-perspective self-check is enough) applies *only* to:

- typos
- comments / docstrings
- single-line log text
- mechanical, no-behavior-change edits

**When in doubt, treat the work as non-trivial.**

### Context packet

Before any non-trivial plan, review, or implementation pass, the orchestrator
builds a concise **context packet** and hands it to every model on the task.
The packet tells each model what to read, what repo rules govern the task, what
is in and out of scope, and how success is verified. It **points to canonical
sources — it does not duplicate them**.

Minimum fields:

- **User request / task objective**
- **Current plan path**, if any
- **Repo workflow sources to read** — `CLAUDE.md`, `AGENTS.md`, `docs/workflow.md`
- **Task-specific context files**
- **Relevant source / test paths**
- **Constraints and non-goals**
- **Required verification gates**
- **Known owner decisions**
- **Open questions**
- **Forbidden files / actions**
- **Expected output format**

Any reviewer may request missing context. **Missing or contradictory required
context is an escalation trigger** (see Escalation).

### Touchpoint maps

Reusable per-subsystem **routing checklists** — the concrete touchpoints, contracts,
and verification a context packet points into when a task touches a given subsystem —
live in **`docs/touchpoint-maps.md`**. Consult the relevant map when building the
context packet. Current maps: **ForecastInterface / model execution**; **Forecast
cycle / assignment selection**; **Persistence / API write path**; **Prefect / Docker /
deployment**; **Training / hindcast / skill**; **Alerting / alert-state**. Their
governance — the right-sizing fitness test — is below (see
Right-sizing).

### Required perspectives

Non-trivial work requires at least:

- **Claude / orchestrator design perspective** — requirements, architecture,
  contracts, user-visible behavior.
- **Codex repo-grounded perspective** — must cite `file:line` evidence for its
  claims.

**High-risk work adds an independent reviewer panel** on top of the two required
perspectives. High-risk work includes security/auth surface, container/privilege
or secrets handling, data-loss or migration risk, external-facing contract or API
change, live-DB impact, Prefect scheduling, Docker entrypoint, FI contract
boundary, user-visible behavior, or anything the owner flags as high-risk.

Rules that always hold:

- **A model may not approve its own output.**
- **The revision author may not approve their own revision.**

### Review redundancy principle

- **Two independent perspectives are the minimum floor, not the maximum.**
- When risk or uncertainty is non-trivial, **prefer one additional independent
  review over one fewer.**
- Extra review cost is acceptable when it reduces implementation, safety, data,
  API, or workflow risk.
- If reviewers disagree, or a reviewer returns "uncertain", **add another
  independent review or escalate to the human owner.**

### Right-sizing (guard against over-engineering)

Our review loops are **monotonically additive**: the completeness lens is rewarded
for finding what's *missing*, and "progress" is measured as *fewer open findings* —
so the loop's natural endpoint is "nothing left to add," which is the
over-engineering attractor. Left unchecked, plans over-scope and detail-bearing docs
accrete reference detail that rots. Counter it two ways:

- **In-loop:** `plan-review` runs a standing **proportionality lens** that argues for
  cuts each round (over-scope, gold-plating, speculative generality, and reference
  detail that belongs in code/docstrings).
- **Before READY** — for **detail-bearing artifacts** (docs, checklists, schemas; not
  code): run one **subtractive right-sizing pass** that judges the artifact against
  its *fitness test*, not against "is anything missing?".

**Fitness test — state what the artifact is FOR, then keep only what serves it.** You
cannot judge "too much detail" without it. Example (routing / touchpoint map): every
bullet names a symbol/subsystem to go read; no bullet teaches how the code works; a
"must not change silently" contract covers only a **surprising, high-consequence,
cross-cutting** invariant — a localized fact the named symbol already reveals is not a
contract.

This guard is itself subject to the trivial-exemption rule: do not add process weight
that exceeds the risk it removes.

### Verdicts and blockers

Each reviewer returns exactly one verdict: **APPROVE | NEEDS_CHANGES | ESCALATE**.

- **Blockers are tracked by decision area, not exact wording.**
- A narrower repo-fact restatement in the same decision area counts as the
  **same blocker recurring**, not a new one.

### Iteration budget

- **Target: 3 review/revise iterations.**
- **Hard maximum: 5 iterations.**
- **One iteration** = one review round returning one or more `NEEDS_CHANGES`
  verdicts, followed by exactly one revised plan or patch.

### Escalation

Escalate to the human owner when any of the following occur:

- the same blocker recurs twice
- reviewers disagree on user-visible behavior
- repo facts invalidate the plan or patch
- scope grows materially beyond the approved plan
- a boundary touch lacks human acknowledgement or is not named in the approved plan
- required context is missing or contradictory
- the hard maximum of 5 iterations is reached

**Escalation packet** contents:

- status
- unresolved blocker
- reviewer disagreement
- options
- recommended next action

### Post-ratification confirming pass

After owner-ratified design decisions are folded into a plan, run **one
confirming multi-model review round before human READY approval**. This round
checks that:

- ratified decisions are reflected consistently
- repo facts still match
- acceptance criteria are complete
- the plan has an executable phase / task breakdown

**The plan may not move to READY** until this confirming round returns APPROVE,
or the remaining concerns are explicitly accepted by the human owner.

### Post-implementation review gate

**An implementation agent's "done" or "complete" claim is evidence, not
approval.** After implementation, the patch must pass independent review before
PR approval.

The **implementer report** must include:

- changed files
- tests / commands run
- deviations from the READY plan
- residual risks

The **Claude / design reviewer** checks:

- the patch matches the approved plan
- requirements and non-goals are respected
- behavior / user-visible implications are correct
- no unresolved design decision was made silently

The **Codex repo-grounded reviewer** checks:

- diff correctness
- tests are meaningful
- verification commands actually ran / passed
- no unintended files changed
- repo patterns / contracts are followed

- **Any `NEEDS_CHANGES` verdict returns the patch to implementation.**
- The target-3 / hard-max-5 review-fix iteration budget applies.
- **The patch may not go to human PR approval** until independent reviewers
  APPROVE, or the remaining concerns are explicitly accepted by the human owner.

### Authority gates

- The **human owner is the terminal authority.**
- The **human approves READY** before implementation.
- The **human approves the PR** before merge.
- **Codex writes code only from a human-approved READY plan.**
- **No actor except the human merges.**

### Context maintenance

Context surfaced mid-task must be **applied, deferred with a reason, or tracked**
— never silently dropped.

### Tooling

This section is the **policy**; it stands on its own and holds even when run by
hand. The repo also ships machinery that executes parts of it. Each stage maps to
a tool as follows:

| Policy stage | Tool | Where it lives |
|---|---|---|
| Plan-doc review loop (pre-READY / confirming round) | `plan-review` skill | `.claude/skills/…` + `.claude/workflows/plan-review.js` |
| Interactive plan stress-test / surface design forks | `grill-me` skill | `.claude/skills/grill-me/` |
| Vision → ordered, human-approved milestone list (WF1) | `vision-decompose` skill | skill |
| Milestone implementation + post-implementation gate (WF2) | `vision-build` skill | skill, driven by `.claude/workflow-capabilities.json` |
| Task Exit Gate / acceptance gates for WF2 | gate manifest | `.claude/workflow-capabilities.json` (mirrors `.github/workflows/ci.yml`) |

Notes:

- **The policy is not auto-enforced.** No hook blocks a commit or PR for skipping
  multi-model review — the tools above run it, but the orchestrator is
  responsible for invoking them.
- **WF2 (`vision-build`) has not yet been run against this repo.** Confirm the
  manifest's gate commands locally before the first launch (see the manifest's
  own `_comment`). Adoption stance is manual-deploy-first, then WF2 fix-mode on
  confirmed bugs, **hold-at-PR — never auto-merge**.

## Task Exit Gate

After each subagent completes, the orchestrator verifies:

1. Task's verification command passes
2. `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean
3. `uv run pyright src/` — no type errors in changed modules
4. `uv run pytest` — all tests pass
5. Affected docs updated in the same change

## Documentation Hygiene

1. **Every code change updates affected docs.** No stale docs.
2. **Single source of truth.** Each concept defined in one place, others reference it.
3. **No TODO/FIXME without a corresponding open question.**

## Commit Conventions

[Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
**Scope**: module name — `feat(types): add domain enums`, `test(qc): add range check tests`

Every commit includes a patch version bump (see CLAUDE.md). Tag after committing.
