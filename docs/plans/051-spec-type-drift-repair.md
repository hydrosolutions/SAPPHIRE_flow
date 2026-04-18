# Plan 051 — Spec ↔ code drift repair (types-and-protocols.md)

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: none — all changes are local to `docs/spec/types-and-protocols.md`
and `src/sapphire_flow/types/`. No runtime behaviour changes.
**Scope**: Bring `docs/spec/types-and-protocols.md` and `src/sapphire_flow/types/`
back into sync. The spec is the authoritative reference for implementers and
currently contains several names, signatures, and module references that do not
exist in code — an implementer following the spec will write code that fails
static checks or raises `NameError` at runtime.

---

## Context

### Why now

The 2026-04-18 project audit surfaced six concrete drifts between
`docs/spec/types-and-protocols.md` and `src/sapphire_flow/types/`. Three of the
six will cause an implementer or subagent reading only the spec to emit broken
code. The other three are softer mismatches (base classes, module absence,
ambiguous returns) that still mislead.

The spec has not kept pace with incremental refactors landed between
Phase 7 (model framework) and Plan 045 (NWP integration). No single commit caused
this — it is accumulated drift over ~8 weeks.

### Inputs (findings from the audit)

1. **`AlertModelStrategy` renamed to `ModelCombinationStrategy`** in code
   (`src/sapphire_flow/types/enums.py:90`). Spec still references the old name at
   `ExceedanceResult.strategy` (≈ line 678) and `Alert.alert_model_strategy`
   (≈ line 835).
2. **`types/auth.py` declared in spec (≈ line 1009)** with frozen dataclasses
   `AccessTokenScope`, `User`, `AccessToken`, `AuditEntry` — module does not
   exist. `UserId`, `AccessTokenId`, `RefreshTokenId` exist in `ids.py`, but the
   entity types are absent. Plan 042 (DEFERRED to v0b) covers API auth; the spec
   should reflect that the types are v1 intent, not v0 reality.
3. **Five enums declared in spec missing from code**: `DlqResolution`,
   `AdjustmentType`, `Calendar`, `UserRole`, `AuditEventType` (spec ≈ lines
   199–252). None exist in `src/sapphire_flow/types/enums.py`.
4. **`FlowRunState` base class**: spec ≈ line 937 declares
   `class FlowRunState(str, Enum)`; code (`enums.py:193`) uses
   `class FlowRunState(StrEnum)`. Near-equivalent but not identical under `is`
   comparisons and `isinstance` semantics.
5. **`WeatherForecastSource.fetch_forecasts` return type**: code
   (`src/sapphire_flow/protocols/adapters.py:22`) returns
   `GriddedForecast | dict[StationId, WeatherForecastResult]` — an ambiguous
   union. Spec does not document the discrimination rule. An implementer cannot
   tell whether to return the dict or the object from the contract alone.

### Principle

Spec is authoritative. When spec and code disagree, either:

- the code was a deliberate change and the spec must be updated to match, OR
- the code is wrong and must be fixed to match the spec.

For each drift below, the plan picks one direction and explains why.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Update the spec to match the code** for the `AlertModelStrategy` → `ModelCombinationStrategy` rename. | The code is the product of multiple PRs and downstream usage (`ExceedanceResult.strategy` field uses `ModelCombinationStrategy.PRIMARY`). Renaming back would cascade through Plan 025/026 (multi-model combination) artefacts. The rename is the real decision; the spec lagged. |
| D2 | **Mark `types/auth.py` explicitly as v1 deferred** in the spec, with an inline note referencing Plan 042. Do not implement the module in v0. | Plan 042 already defers auth to v0b. v0 has no API consumers and no identity perimeter (see Plan 049 Cloudflare Access). Adding empty types now is premature. The spec entry should read "v1 / Plan 042 — not implemented in v0" so agents do not try to import it. |
| D3 | **Per-enum decision** for the 5 missing enums. Three are post-v0 (`UserRole`, `AuditEventType` — need auth; `DlqResolution` — needs dead-letter infra). Two should be implemented now (`AdjustmentType` — used by manual forecast adjustments, `Calendar` — used by date-range types). | Splitting by readiness avoids dumping unused enums into `enums.py`. The two now-enums are referenced in Phase 8/9 code paths that already exist; the other three are documentation for v1 planning. |
| D4 | **Align `FlowRunState` spec to `StrEnum`**. Code is correct (`StrEnum` is Python 3.11+ canonical). | The `(str, Enum)` pattern predates `StrEnum` (added in Python 3.11). The project's minimum Python is 3.11+ (confirmed via `pyproject.toml`). `StrEnum` is the modern equivalent and removes the subtle `isinstance` ambiguity. Spec should catch up. |
| D5 | **Document the `fetch_forecasts` union with a discrimination rule** in the Protocol docstring and spec. The rule: gridded sources return `GriddedForecast`; per-station sources return `dict[StationId, WeatherForecastResult]`. The caller discriminates by `isinstance`. | The union reflects two real implementation paths (ICON-CH2-EPS gridded vs. legacy BAFU forecast adapter). A discriminator field would require a wrapper type and a code change; documenting the `isinstance` contract is lower risk and matches existing caller code in `run_forecast_cycle.py`. |

---

## Task list (single stream)

### T1 — Rename `AlertModelStrategy` references in spec

1. In `docs/spec/types-and-protocols.md`, replace every occurrence of
   `AlertModelStrategy` with `ModelCombinationStrategy`. Verify with
   `Grep AlertModelStrategy` — count must reach 0 after edit.
2. Confirm the enum values (`PRIMARY / POOLED / BMA / CONSENSUS`) match
   `src/sapphire_flow/types/enums.py:90–99`. Update spec if values drift.
3. Cross-reference sites: `ExceedanceResult.strategy`, `Alert.alert_model_strategy`,
   any Protocol signature that takes the strategy. Update all.

**Exit**: `Grep AlertModelStrategy docs/` returns nothing.

### T2 — Mark `types/auth.py` as v1 deferred in the spec

1. In `docs/spec/types-and-protocols.md` § `types/auth.py` (≈ line 1009), insert
   a `**Status**: v1 — deferred per Plan 042. Not implemented in v0.` header.
2. Keep the frozen-dataclass definitions as documentation for v1 design intent.
3. Add an inline note next to any Protocol method that references `User` or
   `AccessToken` clarifying the method is part of the v1 auth layer.

**Exit**: spec reader cannot mistake the `auth.py` section for current v0 code.

### T3 — Enum decisions

1. **Remove from spec** (v1 concerns, not v0): `UserRole`, `AuditEventType`,
   `DlqResolution`. Move their definitions to a new `docs/spec/v1-types.md`
   file, or keep in spec under a clearly-marked `## v1 / deferred` section.
2. **Implement `AdjustmentType`** in `src/sapphire_flow/types/enums.py`. Values
   must match existing usage in forecast-adjustment code paths — search
   `src/sapphire_flow/` for `adjustment_type` to confirm values.
3. **Implement `Calendar`** in `src/sapphire_flow/types/enums.py`. Values:
   `GREGORIAN`, `NOLEAP`, `360_DAY` (match CF-convention calendar strings).
4. Run `uv run ruff format && uv run ruff check --fix` on modified files.
5. Add unit tests: `tests/unit/types/test_enums.py::TestAdjustmentType` and
   `::TestCalendar` — cover round-trip via string value and membership.

**Exit**: every enum in the v0 section of the spec has a corresponding entry
in `enums.py` with matching values.

### T4 — Align `FlowRunState` spec

1. In `docs/spec/types-and-protocols.md`, change `class FlowRunState(str, Enum)`
   to `class FlowRunState(StrEnum)`.
2. Add a one-line note below: "Uses `StrEnum` (Python 3.11+). Members compare
   equal to their string value via `==` and `in`; `isinstance(v, str)` is
   `True`."
3. No code change required.

**Exit**: spec and code agree on `StrEnum` base.

### T5 — Document `fetch_forecasts` discrimination rule

1. In `src/sapphire_flow/protocols/adapters.py`, add a docstring on
   `WeatherForecastSource.fetch_forecasts` stating:
   - Gridded-NWP implementations return `GriddedForecast`.
   - Per-station implementations return `dict[StationId, WeatherForecastResult]`.
   - Callers discriminate via `isinstance(result, GriddedForecast)`.
2. Mirror the rule in `docs/spec/types-and-protocols.md` at the
   `WeatherForecastSource` section.
3. Add an assertion comment at the one site in `run_forecast_cycle.py` that
   handles both branches — confirm the existing `isinstance` check is the
   canonical pattern.

**Exit**: an implementer reading the Protocol docstring alone can choose the
correct return shape.

### T6 — Optional hardening: make the union explicit

1. (Deferred to follow-up; not in this plan.) Consider replacing the union
   with a sealed `WeatherForecastResponse = GriddedResponse | PerStationResponse`
   frozen-dataclass wrapper so that discrimination is by tag field rather than
   `isinstance`. This would be a small breaking change for downstream callers.

---

## Dependency graph

```json
{
  "stream-1": {
    "tasks": ["T1", "T2", "T3", "T4", "T5"],
    "parallel": "T1, T2, T4, T5 in parallel; T3 sequential",
    "depends_on": []
  }
}
```

T1, T2, T4, T5 are documentation-only edits and can run in parallel.
T3 touches both the spec and `enums.py` with new tests, so it runs sequentially
to avoid merge conflicts with the other tasks.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docs/spec/types-and-protocols.md` | T1, T2, T3, T4, T5 | Rename `AlertModelStrategy`; add v1 markers to `auth.py` section; move or delete v1 enums; `str, Enum` → `StrEnum`; add `fetch_forecasts` discriminator note |
| `src/sapphire_flow/types/enums.py` | T3 | Add `AdjustmentType`, `Calendar` enums |
| `src/sapphire_flow/protocols/adapters.py` | T5 | Add return-type discriminator docstring on `WeatherForecastSource.fetch_forecasts` |
| `tests/unit/types/test_enums.py` | T3 | Add `TestAdjustmentType`, `TestCalendar` |

## Files to create

| Path | Task | Purpose |
|---|---|---|
| `docs/spec/v1-types.md` (optional) | T3 | Stash `UserRole`, `AuditEventType`, `DlqResolution` if they are moved out of main spec. If kept in main spec under a `## v1 / deferred` section, this file is not needed. |

---

## Exit gates

1. `Grep AlertModelStrategy` over the whole repo returns only matches in the
   archive (if any). Active docs and code use `ModelCombinationStrategy` only.
2. `docs/spec/types-and-protocols.md` renders cleanly; `types/auth.py` section
   is marked as v1-deferred; `FlowRunState` uses `StrEnum` base.
3. `uv run pytest tests/unit/types/ -q` passes with the new enum tests.
4. `uv run ruff check` and `uv run ruff format --check` pass.
5. Version bump applied per CLAUDE.md Version Bumping mandate; commit tagged.

---

## Risks

| Risk | Mitigation |
|---|---|
| Hidden reference to `AlertModelStrategy` outside `docs/spec/` (e.g. in an archived plan) gets accidentally renamed | `Grep` limited to `docs/spec/` and `src/` for the rename step; leave archive untouched. |
| `AdjustmentType` enum values chosen here conflict with existing usage sites not yet discovered | T3 step 2 explicitly searches `src/sapphire_flow/` for `adjustment_type` before picking values. |
| Spec-only readers (agents in future sessions) have cached the old names in other context | Low impact; next read will pick up the updated spec. No cross-document migration needed. |

---

## Open questions

Not blocking DRAFT → READY:

1. Keep `UserRole`/`AuditEventType`/`DlqResolution` inside `types-and-protocols.md`
   under a `## v1 / deferred` section, or move to a separate `v1-types.md`?
   (Recommendation: separate file — keeps v0 spec focused.)
2. Should T6 (explicit `WeatherForecastResponse` wrapper) be promoted to a
   follow-up plan, or deferred indefinitely? (Recommendation: follow-up plan
   only if a third implementation path appears.)
