# SAPPHIRE Flow — Workflow Conventions

## Task Status Lifecycle

Every work unit (from `docs/spec/implementation-plan.md`) moves through
exactly these states:

```
planned → in-progress → review → done → archived
  ↑  ↓        ↑  ↓        ↓
  blocked    blocked    planned  (design-level rework)
```

### State definitions

| State         | Meaning | Entry gate | Exit gate |
|---------------|---------|------------|-----------|
| `planned`     | Design complete, ready to implement | Spec exists in types-and-protocols or implementation plan | Implementation gate passes (see below) |
| `blocked`     | Waiting on an external dependency | Dependency not yet `done`, or discovered mid-work | Blocker resolved |
| `in-progress` | Actively being implemented | Implementation gate passed | Exit checklist passes (see below) |
| `review`      | Implementation complete, under expert review | Exit checklist passed, docs updated | `/review` passes with no blocking findings |
| `done`        | Reviewed and accepted | Review report clean or findings addressed | Sprint/phase closes |
| `archived`    | Historical record | Phase complete | — |

### Implementation gate (planned → in-progress)

Before starting implementation on ANY task, verify these conditions. This is a hard gate — skipping it is a blocking violation.

1. **Plan has `status: READY` frontmatter.** If the plan file says `status: DRAFT`, STOP. The plan has not been user-confirmed and must not be implemented. State: "Plan is still DRAFT. Run `/review` and complete the Readiness Declaration Protocol before implementing."
2. **Review History has ≥ 2 rounds.** Open the plan's `## Review History` table. If fewer than 2 rows, STOP.
3. **Latest round has 0 blocking findings.** If the most recent row shows Blocking > 0, STOP.
4. **Latest round status is `user-confirmed`.** If the status column says `fixes-needed`, the user has not confirmed readiness. STOP.
5. **Design docs referenced by the task also have `status: READY`.** If a task depends on a design doc that is still DRAFT, STOP — the design must be confirmed first.

If any check fails, do NOT begin implementation. Instead, report which check failed and what action is needed.

**Automated verification**: Run `uv run python scripts/check_readiness.py <path>` to mechanically verify readiness. This script checks frontmatter status, review round count, blocking findings, and user-confirmed status. Exit code 0 = ready, 1 = not ready. Agents SHOULD run this script before beginning implementation as defense-in-depth.

### In-progress exit checklist

Before transitioning from `in-progress` to `review`, all must pass:

1. `uv run ruff check src/ tests/` — no lint errors
2. `uv run ruff format --check src/ tests/` — formatting clean
3. `uv run pyright --strict src/` — no type errors in changed modules
4. `uv run pytest --cov=src/sapphire_flow --cov-report=term-missing tests/` — all
   tests pass, no new uncovered lines in changed modules
5. New/changed modules have corresponding test files with at least basic
   functionality and error handling tests
6. Affected spec and design docs updated in the same change (see Documentation
   Hygiene below)

### Transition rules

1. **planned → in-progress**: Run the implementation gate checklist (see above).
   Only if all 5 checks pass, mark the task status `in-progress` in the
   implementation plan.
2. **planned → blocked**: Dependency from the implementation plan is not
   yet `done`. Note the blocker.
3. **in-progress → blocked**: Discovered mid-work that a dependency or
   external resource is unavailable. Note the blocker.
4. **in-progress → planned**: Spec gaps discovered during implementation
   that require design-level rework before continuing.
5. **blocked → planned** or **blocked → in-progress**: Blocker resolved,
   resume from where you left off.
6. **in-progress → review**: Exit checklist passes. Run `/review` on the
   artifact. Mark task `review`.
7. **review → done**: All blocking review findings resolved. If fixes
   required code changes, re-run the exit checklist and `/review` before
   transitioning. Mark task `done`.
8. **review → planned**: Design-level blocking findings require rework.
   Mark task back to `planned` with a note referencing the review findings.
9. **done → archived**: At phase boundary, move completed items to an
   archive section at the bottom of the implementation plan.

## Documentation Hygiene

### Hard rules

1. **Every code change updates affected spec and design docs.** If you
   change a Protocol, update `docs/spec/types-and-protocols.md`. If you
   change a flow, update `docs/design/05-flows.md`. This applies to spec
   files and design docs, not inline docstrings (see CLAUDE.md prototyping
   policy). During early phases (0a-0c), design doc prose updates may be
   batched at phase boundaries; spec file updates must be immediate.

2. **Docs are checked before transitioning out of `in-progress`.** The
   `/review` command includes a documentation reviewer that verifies
   consistency, completeness, and conciseness.

3. **No stale docs.** If a design doc describes behavior that no longer
   matches the implementation, the doc is wrong and must be fixed in the
   same PR.

4. **Single source of truth.** Each concept is defined in exactly one place.
   Other docs reference it, never duplicate it:
   - Architecture → `docs/design/01-architecture.md`
   - Types and Protocols → `docs/spec/types-and-protocols.md`
   - Database schema → `docs/design/02-data-model.md`
   - API contracts → `docs/design/06-api.md`
   - Workflow/scheduling → `docs/design/05-flows.md`

### What to check

- [ ] All types referenced in code exist in the spec
- [ ] All Protocol method signatures match the spec
- [ ] Design doc sections affected by the change are updated
- [ ] No TODO/FIXME added without a corresponding open question
- [ ] Implementation plan status reflects current state

## Plan and Design Doc Review

Before any plan transitions from `planned` to `in-progress`, it must pass review.
Before any design doc is considered complete, it must pass review.

Both plans and design docs must meet the same maturity standard: a junior developer
who does not know the codebase should be able to work from the document without
asking questions.

### When to run `/review`

- After `/plan` produces an implementation plan
- After fixing findings from a previous review round
- After significant design doc changes (new component, changed data flow, new Protocol)

### How it works

`/review docs/spec/plans/<plan>.md` dispatches the `plan-reviewer` agent (adversarial
review covering failure modes, consistency, and implementability) alongside other
relevant reviewers. `/review docs/design/<doc>.md` dispatches the `design-reviewer`
agent (end-to-end data flow tracing, interface completeness, junior dev implementability)
alongside relevant specialist reviewers. Review history is tracked in the document.

### Maturity gate

A plan or design doc is **ready** only when it passes the full **Readiness Declaration
Protocol** defined in CLAUDE.md. The mechanical checks are:

1. Frontmatter has `status: DRAFT` (all documents start as DRAFT)
2. At least **2 review-fix cycles** completed (tracked in `## Review History`)
3. The latest round has **zero blocking findings**
4. The **Junior Dev Readiness Checklist** passes (see CLAUDE.md — 8 items for plans, 8 items for design docs)
5. The `## Open Questions` section has no unchecked items (design docs)
6. The user has replied `confirm ready` (no other phrase counts)

### Review History format (mandatory)

Every plan and design doc must have a `## Review History` section appended at the bottom.
Use this exact table format:

```markdown
## Review History

| Round | Date       | Reviewers                    | Blocking | Advisory | Status        |
|-------|------------|------------------------------|----------|----------|---------------|
| 1     | 2026-03-07 | plan-reviewer, review-docs   | 5        | 3        | fixes-needed  |
| 2     | 2026-03-08 | plan-reviewer, review-docs   | 1        | 2        | fixes-needed  |
| 3     | 2026-03-09 | plan-reviewer, review-docs   | 0        | 1        | user-confirmed|
```

**Status values**: `fixes-needed` | `user-confirmed`

Rules:
- A row is added after **every** `/review` round — never skip or backfill.
- **Never modify existing rows — append only.** Each round adds a row; no row is ever edited or deleted. If round numbers are not sequential, flag as suspicious.
- The Status column is `fixes-needed` until the user replies `confirm ready`.
- The table is the single source of truth for review cycle count. If the table has fewer than 2 rows, the document is not ready — regardless of what anyone claims.
- Round numbers are sequential and never reset.

### Junior Dev Readiness Checklist

This is a hard gate enforced by the `plan-reviewer` agent. Every task in a plan
and every component in a design doc must satisfy ALL items. See CLAUDE.md for the
full checklist. Key requirements:

**For plans** — each task must specify: exact input/output types with provenance,
end-to-end data flow trace with types at each step, enumerated error cases with
concrete handling, file paths, import dependencies, and a copy-pasteable verify
command.

**For design docs** — each component must specify: end-to-end data flow from
external source to final consumer, exact Protocol signatures, boundary behavior
(parsing, validation, errors, retries), configuration with defaults, and
concurrency/ordering constraints.

### Data flow trace requirement

Any document describing data movement must include an explicit trace showing:
```
[Source] → (parse: RawType → DomainType) → [Transform] → (store: DomainType → DB) → [Consumer]
```
with the concrete type at each arrow. The `plan-reviewer` agent traces these flows
end-to-end and flags any gap where a type or transformation is unspecified.

### Legacy document migration

Design docs created before the maturity gate system (the 9 docs in `docs/design/`)
have no `status:` frontmatter and no `## Review History` section. These are treated
as `status: DRAFT` for all gate purposes:

- They **cannot** be referenced as READY by any plan's `depends-on` field.
- Before a plan that references them can be marked READY, the referenced design docs
  must be retrofitted with frontmatter (`status: DRAFT`) and a `## Review History`
  section, then complete the standard maturity cycle (minimum 2 review rounds, zero
  blocking, user `confirm ready`).
- A prerequisite task to retrofit referenced design docs should be included in the
  first implementation plan.

### Post-READY amendments

Once a document has `status: READY`, any **substantive change** (new task, changed
type, changed error handling, changed file path, changed data flow) must:

1. Revert `status` to `DRAFT`
2. Restore the DRAFT banner
3. Append a note to Review History: `| N+1 | <date> | amendment | — | — | amended |`
4. Require at minimum **1 new review round** before re-marking READY

**Cosmetic edits** (typo fixes, formatting, clarifying wording without changing
semantics) do not trigger this. The agent must state which category the edit falls
into.

## Review Process

### When to review

Run `/review` before:
- Transitioning a task from `in-progress` to `review`
- Merging a phase
- Finalizing a design doc change
- After `/plan` produces an implementation plan (see Plan Review above)

### How it works

`/review [artifact]` dispatches specialist agents in parallel:

| Agent | Reviews for |
|-------|------------|
| `review-domain` | Domain correctness, operational workflow, forecaster UX, model interface, ensemble methods, skill scoring |
| `review-security` | Auth, data handling, secrets, OWASP top 10 |
| `review-developer` | Code quality, typing, architecture, CLAUDE.md compliance |
| `review-testing` | Test coverage, testability, fakes, edge cases |
| `review-data-eng` | Schema design, query performance, partitioning, data pipeline reliability |
| `review-ops` | Monitoring, failure recovery, observability, operational health |
| `review-cicd` | Deployment, Docker, Prefect flows, CI pipeline |
| `review-docs` | Documentation consistency, completeness, conciseness |
| `plan-reviewer` | Plans only: failure modes, consistency, implementability |
| `design-reviewer` | Design docs only: end-to-end data flow tracing, interface completeness, junior dev implementability |

Not every reviewer runs every time. The dispatch command selects relevant
reviewers based on what changed. Two reviewers are always-on:

- `review-docs` — always selected (documentation hygiene is mandatory)
- `review-security` — mandatory for changes touching auth, API endpoints,
  deployment config, secrets, error handling, or logging

### Review output format

Each reviewer produces:

```
## [Reviewer Name] — [PASS | FINDINGS]

### Blocking (must fix before done)
- Finding with file:line reference and recommendation

### Advisory (consider for improvement)
- Suggestion with rationale

### Verified
- What was checked and looks good
```

The dispatch command consolidates all reports into a single output.

### Handling findings

- **Blocking**: Must be resolved before transitioning to `done` (code) or before the next review round (plans).
- **Advisory**: Address or explicitly defer with rationale.
- **Disagreements**: The human decides. Agents advise, they don't gate.

## Task status markers

Use these text labels in the Status column of `docs/spec/implementation-plan.md`:

| Label | State |
|-------|-------|
| `planned` | Not started |
| `blocked` | Waiting on dependency (note the blocker) |
| `in-progress` | Actively being implemented |
| `review` | Pending `/review` |
| `done` | Reviewed and accepted |
| `archived` | Moved to archive section at phase boundary |

## Commit conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
**Scope**: module name — `feat(types): add domain enums`, `test(qc): add range check tests`

Every commit includes a patch version bump (see CLAUDE.md). Tag after committing.
