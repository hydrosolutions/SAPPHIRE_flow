---
name: sync-types
description: This skill should be used when the user modifies a design doc, edits types-and-protocols.md, changes a Protocol or type definition in code, or when inconsistencies between spec and design docs are suspected. It cross-checks the types and protocols spec against design docs, conventions, and code for drift.
---

> **NOTE**: This skill needs refinement once implementation begins and real type
> definitions exist. The current checks are spec-vs-design-doc only. When code
> lands, extend to cover spec-vs-code drift and add automated verification.

# Type and Protocol Sync Check

## Purpose

Ensure `docs/spec/types-and-protocols.md` stays consistent with design docs, conventions, and implemented code. The spec is the single source of truth for all types and Protocols — drift causes implementation errors across sessions.

## When to activate

- After any edit to `docs/spec/types-and-protocols.md`
- After any edit to a design doc in `docs/design/`
- After implementing or modifying types in `src/sapphire_flow/types/` or `src/sapphire_flow/protocols/`
- When the user asks about type consistency or "does the spec match?"

## Checks to perform

### Types completeness
- Every type mentioned in design docs exists in the spec
- Every field matches (name, type, optionality)
- Every enum value in design docs exists in the spec

### Protocol consistency
- Every store/adapter method in design docs exists in the spec
- Method signatures match (parameter names, types, return types)
- Semantics align between design doc descriptions and spec

### Naming conventions
- Type names follow `docs/conventions.md`
- DB table/column names match the spec's mapping
- API route names follow conventions

### Code consistency (when code exists)
- Implemented types match the spec exactly
- No drift between spec and implementation
- Imports reference types from the canonical module

## Report format

```
# Type Sync: [scope]

## Status: [CONSISTENT | DRIFT FOUND]

## Inconsistencies ([count])
### [Type/Protocol name]
- **Spec**: [definition]
- **Other source**: [what differs, with file:line]
- **Fix**: [which to update]

## Missing from spec ([count])
- [referenced but absent]

## Verified ([count] checked)
- [summary of consistent items]
```

The spec is authoritative unless a design doc provides clear rationale for a different definition. Flag both sides and recommend which to update.
