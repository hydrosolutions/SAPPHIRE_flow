---
name: plan
description: Create an implementation plan from a design doc, or break a phase into session-sized tasks
---

# Implementation Plan Skill

## Purpose

Create a detailed, reviewable implementation plan from a design doc or break a phase into session-sized tasks. Plans are created as DRAFT and must survive the full review maturity gate before implementation begins.

## Trigger

When the user says `/plan` followed by a scope description (e.g., `/plan phase 0a`, `/plan weather adapter`).

## Inputs

The user provides one of:
- A phase identifier (e.g., "phase 0a") — plan all tasks for that phase
- A component or feature name — plan implementation of that specific piece
- A design doc path — derive implementation tasks from it

## Steps

### 1. Gather context

Read these files to understand the full picture:
- `docs/design/00-overview.md` — system scope and phases
- The relevant design doc(s) for the scope requested
- `docs/spec/types-and-protocols.md` — authoritative type definitions
- `docs/conventions.md` — naming and structural patterns
- `CLAUDE.md` — coding conventions, type-driven development rules
- `docs/workflow.md` — status lifecycle and review process
- `docs/spec/implementation-plan.md` — existing tasks (if it exists)
- `docs/templates/plan-template.md` — mandatory plan structure

### 2. Draft the plan

Use the template from `docs/templates/plan-template.md`. The plan MUST include:

**Frontmatter:**
```yaml
---
status: DRAFT
scope: <what this plan covers>
created: <date>
depends-on: <list of design docs that must be READY>
---
```

**DRAFT banner** (immediately after frontmatter):
```markdown
> **DRAFT** — This plan has not been reviewed. Do not implement until `status: READY`.
```

**For every task**, satisfy ALL items from the **Junior Dev Readiness Checklist** in CLAUDE.md (section "Per task (plans)"). The template fields map 1:1 to the 8 checklist items. Do not re-interpret or abbreviate the checklist — follow it exactly as written in CLAUDE.md.

**Dependency DAG**: Show which tasks block which. Verify it's acyclic.

**Review History section** (empty, at the bottom):
```markdown
## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
```

### 3. Output the plan

Write the plan to the appropriate location (e.g., `docs/spec/plans/<scope>.md`).

### 4. Mandatory post-draft action

After writing the plan, ALWAYS:

1. State: "Draft complete. This needs at least 2 review-fix cycles before it can be considered ready."
2. Ask the user if they want to run `/review` now.
3. **NEVER** suggest the plan is ready, looks good, or can be implemented.

## Hard rules

- **ALWAYS start with `status: DRAFT`** — no exceptions.
- **ALWAYS include the DRAFT banner** — it is removed only when the user confirms readiness.
- **ALWAYS include an empty Review History table** — this is what the maturity gate counts.
- **NEVER say** "this plan is ready", "we can start implementing", or "this looks solid" after drafting.
- **NEVER skip the Junior Dev Readiness Checklist** — every task must pass all 8 items.
- **If a referenced design doc has `status: DRAFT`**, note this as a dependency that must be resolved before the plan can be marked READY.
