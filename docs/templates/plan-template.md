---
status: DRAFT
scope: <what this plan covers — e.g., "Phase 0a: types and store layer">
created: <YYYY-MM-DD>
depends-on:
  - <list design docs that must be READY before implementation>
---

> **DRAFT** — This plan has not been reviewed. Do not implement until `status: READY`.

# Implementation Plan: <scope>

## Overview

<1-2 paragraphs: what this plan covers, what it produces, and how it fits into the broader system.>

## Prerequisites

- [ ] Design docs listed in `depends-on` have `status: READY`
- [ ] `docs/spec/types-and-protocols.md` covers all types referenced below
- [ ] <any other prerequisites>

## Tasks

<!-- Each task must satisfy ALL 8 items from the Junior Dev Readiness Checklist in CLAUDE.md.
     The fields below map 1:1 to checklist items 1-8. -->

### Task N: <imperative title>

**Goal**: <one sentence — what exists after this task is done that didn't exist before>

**Files**:
- Create: `<full/path/from/project/root.py>`
- Modify: `<full/path/from/project/root.py>`

**Depends on**: Task M (for <specific output>)

**Inputs**:
| Input | Type | Source | How obtained |
|-------|------|--------|--------------|
| <name> | `<exact type>` | `<module.function>` or external | <function call / query / API request> |

**Outputs**:
| Output | Type | Consumer | How consumed |
|--------|------|----------|--------------|
| <name> | `<exact type>` | `<module.function>` or store | <return value / stored in X / passed to Y> |

**Data flow**:
```
[Source] → (parse: RawType → DomainType) → [Transform: DomainType → OutputType] → [Destination]
```
<Spell out each step with the concrete type at every arrow. No gaps.>

**Error handling**:
| Error condition | Behavior | Details |
|----------------|----------|---------|
| <what goes wrong> | raise / log-and-skip / retry | <exception type, retry count, backoff, exhaustion> |

**Import dependencies**:
```python
from sapphire_flow.<module> import <Type, Protocol, function>
```

**Verification**:
```bash
uv run pytest tests/<test_file>.py -v
uv run pyright --strict src/sapphire_flow/<module>.py
```

**Design decisions**:
- <If two approaches exist, state which was chosen and why.>

---

<Repeat for each task>

## Dependency DAG

```
Task 1 ──→ Task 3 ──→ Task 5
Task 2 ──→ Task 3
Task 4 (independent)
```

<Verify this is acyclic. Note which tasks can run in parallel.>

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
