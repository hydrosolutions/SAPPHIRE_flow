---
status: DRAFT
scope: <what this design doc covers — e.g., "Weather data adapters">
created: <YYYY-MM-DD>
related-docs:
  - <list related design docs>
---

> **DRAFT** — This design doc has not been reviewed. Do not implement from it until `status: READY`.

# Design: <scope>

## Overview

<1-2 paragraphs: what this component does, where it sits in the architecture, what it produces.>

## Data Flow

### End-to-end trace

```
[External Source] → (fetch: HTTP → bytes)
  → (parse: bytes → RawResponseModel)
  → (validate: RawResponseModel → DomainType)
  → (store: DomainType → DB table)
  → (retrieve: SQL → DomainType)
  → (consume: ServiceX.method())
```

<For every data path through this component, show the full trace with concrete types at each arrow. No gaps.>

### Boundary behavior

| Boundary | Input | Output | On error |
|----------|-------|--------|----------|
| <e.g., HTTP fetch> | `<request type>` | `<response type>` | <retry 3x, exp backoff 1-8s, then raise AdapterError> |
| <e.g., parse> | `bytes` | `RawResponseModel` | <raise ParseError with raw payload snippet> |
| <e.g., validate> | `RawResponseModel` | `DomainType` | <log-and-skip invalid records, raise if all invalid> |

## Interfaces

### Protocols

```python
@runtime_checkable
class <ProtocolName>(Protocol):
    def method_name(self, param: ExactType) -> ReturnType:
        """<One line: what it does.>

        Raises:
            SpecificError: <when>
        """
        ...
```

<Every Protocol method has full signature: parameters with types, return type, exceptions.>

### Types

```python
class DomainType(NamedTuple):
    field: ExactType
    # invariant: <what __new__ enforces>
```

<Every type has all fields with types. Invariants documented. Cross-reference with `docs/spec/types-and-protocols.md`.>

## Configuration

| Setting | Type | Default | Source | Description |
|---------|------|---------|--------|-------------|
| <name> | `<type>` | `<default>` | env / TOML / DB | <what it controls> |

## Concurrency and Ordering

<State explicitly: Can operations run in parallel? Does order matter? What happens with concurrent access? If not applicable, state "Single-threaded, sequential execution — no concurrency concerns.">

## Error Handling Summary

| Error | Source | Handling | Escalation |
|-------|--------|----------|------------|
| <error type> | <where it occurs> | <immediate action> | <what happens if handling fails> |

## Design Decisions

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| <what was decided> | <chosen approach> | <rejected approach> | <why> |

## Open Questions

<!-- MANDATORY section. Absence is a blocking finding. Any unchecked item blocks READY status. -->

- [ ] <Any unresolved question — each unchecked item is a blocking finding for READY status>

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
