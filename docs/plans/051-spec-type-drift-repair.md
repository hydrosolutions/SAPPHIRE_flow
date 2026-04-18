# Plan 051 — Spec ↔ code drift repair (types-and-protocols.md)

**Status**: READY
**Date**: 2026-04-18
**Revision**: 3 — second pre-READY review pass (2026-04-18): tightened line references and removed redundant steps after verifying against the actual spec: (a) T3 step 1 now lists exact line numbers per enum (200, 204, 210, 214, 236) because the five deferred enums are non-contiguous — interleaved with `StationStatus`, `GaugingStatus`, `ObservationSource` at 220–234 which are NOT deferred; (b) T3 step 4 simplified: the only v0-facing cross-reference to a deferred enum outside the `types/auth.py` block is `ForecastAdjustment.adjustment_type` (spec line 866); `User.role` (983) and `AuditEntry.event_type` (999) live inside the auth.py section that T2 marks section-level; `Calendar` and `DlqResolution` have no field references in the spec; (c) T3 exit-gate grep pattern corrected from double to single backslashes for ripgrep; (d) T5 pinpointed to the right `fetch_forecasts` site (Protocol line 2266/2270) and warns against confusing it with `WeatherForecastStore.fetch_forecasts_for_cycle` (1835) or `fetch_forecasts_in_range` (1848) which share a verb but are different protocols; (e) T2 explicitly notes the transitive v1-deferred coverage of `UserRole`/`AuditEventType` field refs so T2 and T3 don't double-annotate.
**Revision**: 2 — pre-READY review (2026-04-18): (a) T3 originally proposed implementing `AdjustmentType` and `Calendar` in `enums.py`, but `docs/v0-scope.md:464` explicitly defers both to v1; Flow 3 (forecast adjustments) is also deferred (`conventions.md:373,409`). Proposed `Calendar` values (`NOLEAP`, `360_DAY`) would have contradicted the spec's existing Nepal-oriented `BIKRAM_SAMBAT` and created new drift. D3 revised: all five missing enums move to a v1-deferred section of the spec; no code changes, no new tests. (b) T1 exit gate was "`Grep AlertModelStrategy docs/` returns nothing" but `docs/design/v1-nepal-modelling.md:142` also carries the old name; scope widened accordingly. `docs/architecture-context.md:118` intentionally mentions the old name in a rename-history sentence — excluded.
**Depends on**: none — all changes are local to `docs/spec/types-and-protocols.md`
and `docs/design/v1-nepal-modelling.md`. No code changes, no runtime behaviour changes.
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
| D3 | **All 5 missing enums move to a `## v1 / deferred` section of the spec; none are implemented in v0.** Enums: `UserRole`, `AuditEventType`, `DlqResolution`, `AdjustmentType`, `Calendar`. | `docs/v0-scope.md:464` explicitly defers all four non-auth enums (UserRole, AuditEventType, AdjustmentType, Calendar); `DlqResolution` needs dead-letter infra that does not exist. Flow 3 (forecast adjustments) — the would-be consumer of `AdjustmentType` — is itself deferred (`conventions.md:373,409`). The only v0 use of a calendar concept is `config/deployment.py:105` which uses `Literal["gregorian", "bikram_sambat"]` (Nepal civil calendar), not CF-convention climate calendars; implementing CF values now would create new drift. Keeping the enums visible in the spec under a deferred-section preserves v1 design intent without tempting v0 imports. |
| D4 | **Align `FlowRunState` spec to `StrEnum`**. Code is correct (`StrEnum` is Python 3.11+ canonical). | The `(str, Enum)` pattern predates `StrEnum` (added in Python 3.11). The project's minimum Python is 3.11+ (confirmed via `pyproject.toml`). `StrEnum` is the modern equivalent and removes the subtle `isinstance` ambiguity. Spec should catch up. |
| D5 | **Document the `fetch_forecasts` union with a discrimination rule** in the Protocol docstring and spec. The rule: gridded sources return `GriddedForecast`; per-station sources return `dict[StationId, WeatherForecastResult]`. The caller discriminates by `isinstance`. | The union reflects two real implementation paths (ICON-CH2-EPS gridded vs. legacy BAFU forecast adapter). A discriminator field would require a wrapper type and a code change; documenting the `isinstance` contract is lower risk and matches existing caller code in `run_forecast_cycle.py`. |

---

## Task list (single stream)

### T1 — Rename `AlertModelStrategy` references

**Scope**: `docs/spec/types-and-protocols.md` and `docs/design/v1-nepal-modelling.md`.
Do **not** touch `docs/architecture-context.md:118` — that line intentionally
references the old name in a rename-history sentence. Do **not** touch
`docs/plans/archive/` — archived plans are frozen.

1. In `docs/spec/types-and-protocols.md`, replace every occurrence of
   `AlertModelStrategy` with `ModelCombinationStrategy`. Known sites:
   line 148 (enum definition), 678 (`ExceedanceResult.strategy`), 835
   (`Alert.alert_model_strategy`), 1570 (strategy registry paragraph), 2514
   (`DeploymentConfig.alert_model_strategy`).
2. In `docs/design/v1-nepal-modelling.md:142`, replace `AlertModelStrategy`
   with `ModelCombinationStrategy`.
3. Confirm the enum values (`PRIMARY / POOLED / BMA / CONSENSUS`) match
   `src/sapphire_flow/types/enums.py:90–94`. Update spec if values drift.
4. Note the **field name** `alert_model_strategy` (snake_case) stays — only
   the **type name** (PascalCase) changes. The Edit tool's exact-string match
   handles this naturally, but double-check after the edit.

**Exit**: `Grep AlertModelStrategy docs/spec/ docs/design/` returns nothing.
Remaining hits elsewhere must be (a) in `docs/architecture-context.md:118`
(rename-history sentence, intentional) or (b) under `docs/plans/archive/`
(frozen), or (c) in this plan file itself.

### T2 — Mark `types/auth.py` as v1 deferred in the spec

1. In `docs/spec/types-and-protocols.md`, locate `Module: types/auth.py` at
   line 1009. Directly below that marker, insert:
   `**Status**: v1 — deferred per Plan 042. Not implemented in v0. Types below
   are design intent only; do not import them.`
2. Keep the frozen-dataclass definitions as documentation for v1 design intent.
   These include `AccessTokenScope` (line 973), `User` (with `role: UserRole`
   at 983), `AccessToken` (with `scope: AccessTokenScope` at 991), and
   `AuditEntry` (with `event_type: AuditEventType` at 999). The field-level
   references to `UserRole` and `AuditEventType` (deferred enums handled by
   T3) are transitively v1 via this section-level marker — no per-field
   annotation required.
3. Add an inline note next to any Protocol method that references `User` or
   `AccessToken` clarifying the method is part of the v1 auth layer.

**Exit**: spec reader cannot mistake the `auth.py` section for current v0 code.

### T3 — Move all five missing enums to a v1-deferred section (spec only, no code)

**Rationale**: `docs/v0-scope.md:464` defers `UserRole`, `AuditEventType`,
`AdjustmentType`, `Calendar` to v1. `DlqResolution` has no infrastructure to
consume it. No v0 code path imports any of the five. Implementing them now
would either introduce dead code or, for `Calendar`, new drift (existing
`deployment.py:105` uses Nepal civil-calendar literals, incompatible with
Plan 051's original CF-convention proposal).

1. In `docs/spec/types-and-protocols.md`, the five enum definitions are
   **non-contiguous** (interleaved with `StationStatus`, `GaugingStatus`,
   `ObservationSource` at lines 220–234). Exact locations:
   - `DlqResolution` — line 200
   - `AdjustmentType` — line 204
   - `Calendar` — line 210 (keep `GREGORIAN`, `BIKRAM_SAMBAT` values verbatim)
   - `UserRole` — line 214
   - `AuditEventType` — line 236
2. Move all five verbatim (preserving member values, comments, and surrounding
   blank lines) into a new subsection at the bottom of the enums section titled:
   `## Enums — v1 / deferred (not implemented in v0)`. Do not reorder within
   the moved group — DlqResolution, AdjustmentType, Calendar, UserRole,
   AuditEventType (source order).
3. Add a preamble paragraph to that subsection: "These enums appear in v1
   design but are not implemented in `src/sapphire_flow/types/enums.py`.
   Deferred per `docs/v0-scope.md:464` (UserRole, AuditEventType,
   AdjustmentType, Calendar) and by lack of consumer infrastructure
   (DlqResolution). Implementers must not import them from
   `sapphire_flow.types.enums` — the symbols do not exist at runtime."
4. Annotate the single v0-facing cross-reference to a deferred enum:
   `ForecastAdjustment.adjustment_type` at `types-and-protocols.md:866`
   already uses `Literal["shift", "scale", "cap", "floor"]` with an inline
   comment. Extend that comment to read "… `AdjustmentType` enum is the v1
   canonical form (see *Enums — v1 / deferred*)". No other extra-auth
   cross-references need annotation: `UserRole` (line 983) and
   `AuditEventType` (line 999) live inside the `types/auth.py` module block
   which T2 already marks as v1-deferred at section level; `Calendar` and
   `DlqResolution` have no field references in the spec.
5. **No code changes.** Do not edit `src/sapphire_flow/types/enums.py`.
   Do not add tests.

**Exit**: (a) the five enums are grouped under the v1-deferred subsection;
(b) the `ForecastAdjustment.adjustment_type` Literal comment mentions
`AdjustmentType` as the v1 canonical form;
(c) `Grep "class (UserRole|AuditEventType|DlqResolution|AdjustmentType|Calendar)\(Enum\)" src/`
(single backslashes — ripgrep escapes the literal parens) returns nothing.

### T4 — Align `FlowRunState` spec

1. In `docs/spec/types-and-protocols.md`, change `class FlowRunState(str, Enum)`
   to `class FlowRunState(StrEnum)`.
2. Add a one-line note below: "Uses `StrEnum` (Python 3.11+). Members compare
   equal to their string value via `==` and `in`; `isinstance(v, str)` is
   `True`."
3. No code change required.

**Exit**: spec and code agree on `StrEnum` base.

### T5 — Document `fetch_forecasts` discrimination rule

1. In `src/sapphire_flow/protocols/adapters.py` (target: the
   `WeatherForecastSource.fetch_forecasts` method at lines 18–23), add a
   docstring stating:
   - Gridded-NWP implementations return `GriddedForecast`.
   - Per-station implementations return `dict[StationId, WeatherForecastResult]`.
   - Callers discriminate via `isinstance(result, GriddedForecast)`.
2. Mirror the rule in `docs/spec/types-and-protocols.md` at the
   `WeatherForecastSource` Protocol section (line 2266, signature at 2270).
   The fake implementation section at line 2605 already notes the union;
   the new text belongs next to the Protocol definition, not the fake.
   **Do not** touch the `WeatherForecastStore.fetch_forecasts_for_cycle`
   (line 1835) or `fetch_forecasts_in_range` (line 1848) — those are
   different protocols with a similar verb.
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
    "parallel": "all five tasks parallel (each edits a disjoint region of types-and-protocols.md)",
    "depends_on": []
  }
}
```

All five tasks are documentation-only after the D3 revision. They edit
different sections of `types-and-protocols.md`:
T1 → enum & field sites for `AlertModelStrategy`;
T2 → `auth.py` section (≈ line 1009);
T3 → enum definitions (≈ lines 199–252) + their field refs;
T4 → `FlowRunState` definition (≈ line 938);
T5 → `WeatherForecastSource` Protocol section.

If run by a single agent, sequential execution is simpler. If parallelised,
T1 and T3 must coordinate because both may touch enum-adjacent blocks in
the same region of the spec. T5 also makes the only out-of-spec edit (docstring
in `protocols/adapters.py`), but that file is disjoint from everything else.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docs/spec/types-and-protocols.md` | T1, T2, T3, T4, T5 | Rename `AlertModelStrategy`; add v1 markers to `auth.py` section; group `UserRole`/`AuditEventType`/`DlqResolution`/`AdjustmentType`/`Calendar` under `## Enums — v1 / deferred`; `str, Enum` → `StrEnum`; add `fetch_forecasts` discriminator note |
| `docs/design/v1-nepal-modelling.md` | T1 | Rename `AlertModelStrategy` → `ModelCombinationStrategy` at line 142 |
| `src/sapphire_flow/protocols/adapters.py` | T5 | Add return-type discriminator docstring on `WeatherForecastSource.fetch_forecasts` |

## Files to create

None. (Earlier revision considered a separate `docs/spec/v1-types.md`; D3
revision keeps the deferred enums inside `types-and-protocols.md` under a
labelled subsection so all type documentation remains in one file.)

---

## Exit gates

1. `Grep AlertModelStrategy docs/spec/ docs/design/` returns nothing. Any
   remaining hits in the repo are (a) `docs/architecture-context.md:118`
   rename-history sentence, (b) `docs/plans/archive/`, or (c) this plan file.
2. `docs/spec/types-and-protocols.md` renders cleanly; `types/auth.py` section
   is marked as v1-deferred; `FlowRunState` uses `StrEnum` base; the five
   missing enums appear under `## Enums — v1 / deferred`.
3. `uv run pytest -q` passes (no new tests added; regression check only).
4. `uv run ruff check` and `uv run ruff format --check` pass — expected to be
   no-ops since the only source-code edit is a docstring addition.
5. Version bump applied per CLAUDE.md Version Bumping mandate; commit tagged.

---

## Risks

| Risk | Mitigation |
|---|---|
| T1 accidentally renames `AlertModelStrategy` in an archived plan or in the intentional rename-history sentence at `docs/architecture-context.md:118` | T1 scope is restricted to `docs/spec/types-and-protocols.md` and `docs/design/v1-nepal-modelling.md`. The exit-gate `Grep` is scoped to those two paths. Archive is frozen. |
| A future v1 plan begins implementing `AdjustmentType` or `Calendar` and picks values that contradict the deferred-section definitions in the spec | The deferred subsection preserves `Calendar.BIKRAM_SAMBAT` (the existing spec value, aligned with `deployment.py:105`) so any v1 implementation starts from a consistent baseline. |
| Spec-only readers (agents in future sessions) have cached the old names in other context | Low impact; next read will pick up the updated spec. No cross-document migration needed. |
| Running in parallel with Plan 046 (Mac-mini staging): both plans bump the patch version on commit | File sets are disjoint. A trivial rebase resolves the version-file collision; no code-level conflict. |

---

## Open questions

Not blocking DRAFT → READY:

1. Should T6 (explicit `WeatherForecastResponse` wrapper) be promoted to a
   follow-up plan, or deferred indefinitely? (Recommendation: follow-up plan
   only if a third implementation path appears.)
