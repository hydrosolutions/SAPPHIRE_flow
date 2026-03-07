---
description: Create an implementation plan from a design doc, or break a phase into session-sized tasks
argument-hint: [design-doc-path-or-phase-id]
allowed-tools: Agent, Read, Glob, Grep, Write
model: opus
---

You are a planning coordinator for SAPPHIRE Flow. Take a design doc or phase and produce a phased implementation plan with session-sized tasks.

## Input

The user provides: $ARGUMENTS

Either:
- A **design doc path** (e.g., `docs/design/03-adapters.md`) — produce a full phased plan
- A **phase ID** (e.g., `0a`) — break that phase into detailed session tasks

## Read first

1. `docs/spec/types-and-protocols.md` — the type contract (skip if not yet created)
2. `docs/spec/implementation-plan.md` — existing phases and tasks (skip if not yet created)
3. `docs/conventions.md` — naming and structural conventions (skip if not yet created)
4. `docs/workflow.md` — status lifecycle and exit checklist
5. The design doc(s) relevant to the input
6. For phases 0f-0h, also read `docs/design/07-deployment.md`

If any spec files do not exist yet, note their absence in Open Questions.

## Process

**If the input is a design doc path**, run steps 1-3 then 4. **If the input is a phase ID**, read that phase from `docs/spec/implementation-plan.md` and skip directly to step 4.

### 1. Extract implementable units (design doc input only)

From the design doc, identify every concrete artifact:
- Types and enums to define
- Protocol implementations (stores, adapters)
- Service functions (pure business logic)
- Prefect flow definitions
- API endpoints
- Configuration and infrastructure

### 2. Map dependencies (design doc input only)

For each unit:
- Which types must exist first?
- Which Protocols must be defined before implementations?
- Which fakes are needed before services can be tested?
- Which services must exist before flows can wire them?

### 3. Sequence into phases (design doc input only)

Group units into phases. Each phase:
- Has clear inputs and outputs
- Can be verified independently
- Fits into the existing phase structure from `docs/spec/implementation-plan.md`

### 4. Break into session-sized tasks

A session-sized task is:
- One module + its tests (~100-300 lines production code + tests)
- Or one Protocol fake + verification
- Or one service function + tests against fakes

Exceptions:
- **Adapter tasks** often exceed 300 lines — split into "adapter + fake" and "adapter integration test" sessions.
- **Migration tasks**: one Alembic migration creating up to 5 related tables, or one partitioning setup.
- **Config/ops tasks**: one deployment configuration + verification.

For each task, produce a self-contained instruction block that satisfies **all 8 items** of the Junior Dev Readiness Checklist (CLAUDE.md). A task missing any section is a blocking finding during review.

```
### Task [ID]: [name]

**Goal**: [one sentence]

**Read first**:
- `docs/spec/types-and-protocols.md` section [X]
- [other relevant design docs]

**Inputs**:
- [exact type] from [source module/function/store/API] via [how obtained: function call, query, etc.]
- ...

**Outputs**:
- [exact return type] consumed by [who uses it and how: stored, passed to function, returned via API]
- ...

**Data flow**:
```
[Source] → (parse: RawType → ParsedType) → (validate/transform: ParsedType → DomainType) → (store/return: DomainType → destination)
```
Spell out the concrete type at each arrow. No gaps, no "data is processed."

**Error cases**:
| Error condition | Behavior | Details |
|----------------|----------|---------|
| [what can go wrong] | raise / log-and-skip / retry | [if retry: count, backoff, exhaustion behavior] |

**Create/modify**:
- `src/sapphire_flow/[path]` — [what to implement]
- `tests/test_[module].py` — [what to test]

**Import dependencies**:
- `from sapphire_flow.[module] import [Protocol, type, function]`
- ...

**Depends on**: [task IDs]

**Design decisions**:
- [If two reasonable implementations exist, state which was chosen and why. If none, write "No ambiguous choices."]

**Verify**:
1. `uv run ruff check src/ tests/`
2. `uv run ruff format --check src/ tests/`
3. `uv run pyright --strict src/sapphire_flow/[path]`
4. `uv run pytest tests/test_[module].py -v`

**Exit criteria**: [specific conditions]
```

### 5. Identify parallel tracks

Mark which tasks can run concurrently vs sequentially.

## Output

Every plan starts with a DRAFT marker that is only removed after user confirmation following the Readiness Declaration Protocol (CLAUDE.md).

```
---
status: DRAFT
---

# Implementation Plan: [topic]

> **DRAFT** — This plan has not passed the maturity gate. Do NOT implement until
> status is changed to `READY` after the Readiness Declaration Protocol is satisfied
> (minimum 2 review cycles with zero blocking findings + user confirmation).

## Dependencies on existing phases
- Requires: [existing tasks that must be done first]

## Phase [X]: [name]

### Inputs
- [what must exist]

### Task graph
[ASCII dependency diagram]

### Tasks
[session instruction blocks — using the full task template above]

### Acceptance criteria
- [how to know this phase is done]

## Open questions
- [anything needing resolution before implementation]

## Estimated sessions: [count]
## Parallel tracks: [count]
```

Cross-check all types against `docs/spec/types-and-protocols.md`.

### 6. Write output to file

Write the plan to `docs/spec/plans/<topic-slug>.md` using the Write tool. Print the file path at the end.

**After writing, state explicitly:**

> "Plan draft written. This needs at least 2 review-fix cycles before it can be considered ready. Running `/review` now."

Then immediately run `/review docs/spec/plans/<topic-slug>.md`. Do NOT suggest readiness or that the plan "looks good."
