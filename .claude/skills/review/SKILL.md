---
name: review
description: Dispatch expert reviewers on any artifact (code, plan, or design doc)
---

# Review Skill

## Purpose

Dispatch specialist reviewer agents on an artifact, collect findings, update the Review History, and mechanically run the Readiness Declaration Protocol. This skill is the single gateway for all review activity — it controls the verdict.

## Trigger

When the user says `/review` followed by a path or description (e.g., `/review docs/spec/plans/phase-0a.md`, `/review weather adapter code`).

## Inputs

The user provides:
- A file path to review (plan, design doc, or code)
- Or a description of what to review (the skill identifies the relevant files)

## Steps

### 1. Identify the artifact type

Determine what is being reviewed:
- **Plan**: file in `docs/spec/plans/` or matches plan structure
- **Design doc**: file in `docs/design/`
- **Code**: files in `src/` or `tests/`
- **Mixed**: multiple artifact types (review each appropriately)

### 2. Select reviewers

Based on artifact type, dispatch relevant reviewers from `.claude/agents/`:

**Always selected:**
- `review-docs` — documentation hygiene (mandatory for all reviews)

**Plans:**
- `plan-reviewer` — adversarial: failure modes, consistency, implementability (mandatory)
- Plus relevant specialists based on plan content (e.g., `review-data-eng` for DB tasks, `review-security` for auth tasks)

**Design docs:**
- `design-reviewer` — data flow tracing, interface completeness, junior dev implementability (mandatory)
- Plus relevant specialists based on doc content

**Code:**
- `review-developer` — code quality, typing, architecture (mandatory)
- `review-testing` — test coverage, testability (mandatory if tests exist or should exist)
- `review-security` — mandatory for auth, API endpoints, deployment config, secrets, error handling, logging
- Plus other relevant specialists

### 3. Dispatch reviewers in parallel

Launch all selected reviewers as parallel subagents. Each reviewer:
- Reads the artifact and all referenced context
- Produces findings in the standard format (Blocking / Advisory / Verified)
- Returns its report

### 4. Consolidate findings

Collect all reviewer reports. Present a unified summary:

```markdown
## Review Summary — Round N

**Artifact**: <path>
**Reviewers**: <list>
**Date**: <date>

### Blocking findings: <count>
<consolidated blocking findings from all reviewers, with source>

### Advisory findings: <count>
<consolidated advisory findings from all reviewers, with source>

### Verified
<what was checked and looks good>
```

### 5. Update Review History (plans and design docs only)

If the artifact has a `## Review History` table, add a new row:

```markdown
| <next round> | <date> | <reviewer list> | <blocking count> | <advisory count> | fixes-needed |
```

**Rules:**
- Status is ALWAYS `fixes-needed` — only the user can change it to `user-confirmed`.
- Round number increments from the last row.
- **Never modify existing Review History rows — append only.** Each round adds a row; no row is ever edited or deleted. If round numbers are not sequential, flag as suspicious.
- If no Review History section exists, **add one** and note its absence as an additional blocking finding. Count = 0 rounds.

### 6. Run the Readiness Declaration Protocol (plans and design docs only)

After presenting findings, mechanically execute this sequence. **Every step must be shown to the user.** Skipping steps is a hard violation.

**Step 1 — Check DRAFT status:**
Read the document's frontmatter. Report: "Frontmatter status: <DRAFT|READY>"
- If `status: READY` already exists but findings were found, flag as suspicious.

**Step 2 — Count review rounds:**
Read the `## Review History` table. Count rows.
- If fewer than 2 rows: "Only N review cycle(s) completed. Minimum 2 required. **Not ready.**"
- If 2 or more: "N review cycles completed. Minimum met."

**Step 3 — Check latest round:**
Read the most recent row in Review History.
- If Blocking > 0: "N blocking finding(s) remain from round M. **Not ready.**"
- If Blocking == 0: "Zero blocking findings in round M."

**Step 4 — Run Junior Dev Readiness Checklist:**
For every task (plans) or component (design docs), verify each checklist item from CLAUDE.md.
- List any failures.
- If any item fails: "Junior Dev Readiness Checklist: N failure(s). **Not ready.**"
- If all pass: "Junior Dev Readiness Checklist: all items pass."

**Step 5 — Report status:**
Present the results of steps 1-4 in a clear summary table:

```markdown
### Readiness Gate Status

| Check | Result |
|-------|--------|
| Frontmatter | DRAFT |
| Review rounds | N (minimum 2) |
| Blocking findings (latest round) | N |
| Junior Dev Readiness Checklist | PASS / N failures |
| User confirmation | Pending |
```

**Step 6 — Verdict:**
- If ANY check fails: "**Document is not ready.** Fix the blocking findings and run `/review` again."
- If ALL mechanical checks pass: "All mechanical checks pass. Document is still DRAFT. **Awaiting your confirmation before marking as READY.**"
- **NEVER** say the document is ready without explicit user confirmation.
- **NEVER** change `status: DRAFT` to `status: READY` without explicit user confirmation.

### 7. Handle user confirmation (only when all checks pass)

If the user replies with exactly `confirm ready` (this exact phrase, case-insensitive, no substitutes):
1. Change frontmatter from `status: DRAFT` to `status: READY`
2. Remove the DRAFT banner
3. Update the latest Review History row status from `fixes-needed` to `user-confirmed`
4. State: "Document marked READY. Implementation may proceed."

If the user says ANYTHING else — including "confirmed ready", "confirm it's ready", "looks good", "let's move on", "ready", "yes" — do NOT treat it as confirmation. Instead ask: "To mark this document READY, please reply with exactly `confirm ready`."

## Hard rules

- **NEVER suggest readiness** — the protocol decides, not the reviewer.
- **NEVER skip the Readiness Declaration Protocol** for plans and design docs.
- **NEVER set status to `user-confirmed`** without the user replying `confirm ready`.
- **NEVER change frontmatter to `status: READY`** without the user replying `confirm ready`.
- **NEVER modify existing Review History rows** — append only.
- **ALWAYS show each protocol step** — transparency is non-negotiable.
- **ALWAYS add a Review History row** — even if the review is clean.
- **Review History status is always `fixes-needed`** until user confirms.
- **If Review History section is missing**, add it and count the absence as a blocking finding.
