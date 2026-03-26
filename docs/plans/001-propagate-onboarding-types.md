---
status: DONE
created: 2026-03-26
scope: docs + spec consistency fix
---

# 001 — Propagate design doc onboarding types to spec

## Problem

The design doc (`docs/design/v0-flow13-model-onboarding.md` §3f) defines a richer set of
model onboarding result types than what landed in `docs/spec/types-and-protocols.md`. The
spec has a simplified flat structure; the design doc has a proper multi-unit orchestration
model. The design doc is authoritative.

## Changes

### 1. `docs/spec/types-and-protocols.md` — replace onboarding types (~lines 1456–1479)

Replace the existing `CompatibilityReport`, `SkillGateResult`, `ModelOnboardingResult`
with the design doc versions, and add the missing `OnboardingUnit` and
`OnboardingUnitResult` types:

**CompatibilityReport** — replace:
```python
# OLD (spec)
class CompatibilityReport:
    model_id: ModelId
    protocol_satisfied: bool
    missing_features: dict[str, frozenset[str]]
    missing_static: frozenset[str]
    time_step_compatible: bool
    errors: list[str]

# NEW (from design doc)
class CompatibilityReport:
    model_id: ModelId
    protocol_conforms: bool
    missing_target_parameters: frozenset[str]
    missing_past_dynamic: frozenset[str]
    missing_future_dynamic: frozenset[str]
    missing_static_features: frozenset[str]
    time_step_compatible: bool
    compatible: bool   # True iff all checks pass
```

**SkillGateResult** — replace:
```python
# OLD (spec)
class SkillGateResult:
    passed: bool
    scores: dict[str, float]
    thresholds: dict[str, float]
    failures: list[str]

# NEW (from design doc)
class SkillGateResult:
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    passed: bool
    metric_scores: dict[str, float]
    thresholds: dict[str, float]
    failing_metrics: frozenset[str]
```

**Add** `OnboardingUnit` and `OnboardingUnitResult` (missing from spec entirely):
```python
class OnboardingUnit:
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    station_ids: frozenset[StationId]
    onboarding_period_start: UtcDatetime
    onboarding_period_end: UtcDatetime
    time_step: timedelta
    # XOR invariant: exactly one of station_id / group_id must be set

class OnboardingUnitResult:
    unit: OnboardingUnit
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    hindcast_steps: list[HindcastStepResult]
    skill_gate: SkillGateResult | None
    promoted: bool
    assigned: bool
    error: str | None = None
```

**ModelOnboardingResult** — replace:
```python
# OLD (spec)
class ModelOnboardingResult:
    model_id: ModelId
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    skill_gate: SkillGateResult | None
    stations_assigned: int
    errors: list[str]

# NEW (from design doc)
class ModelOnboardingResult:
    model_id: ModelId
    units: tuple[OnboardingUnitResult, ...]
    total: int
    promoted: int
    skipped: int        # compatibility failures
    failed: int         # training/hindcast errors
```

### 2. `docs/spec/types-and-protocols.md` — update module map (~line 2277)

Add `OnboardingUnit`, `OnboardingUnitResult` to the `types/model_onboarding.py` listing.

### 3. Fix stale references in `docs/architecture-context.md`

| Line | Fix |
|------|-----|
| ~105 (step 1.7) | Replace `required_features`, `required_static_attributes` → `data_requirements: ModelDataRequirements` |
| ~828 (step T.2) | Replace `required_static_attributes` → `data_requirements.static_features` |
| ~555 (step 5.6) | Replace `forecast_target` → `forecast_targets` |
| Lines 43+57 | Remove duplicate Flow 13 entry (keep the one in Initialization, remove Maintenance duplicate) |

### 4. Fix stale references in `docs/spec/types-and-protocols.md`

| Line | Fix |
|------|-----|
| ~2251 | Replace `required_static_attributes = frozenset()` → `data_requirements.static_features = frozenset()` |
| ~1143-1144 | Update `ModelRegistryEntry` prose to reference `data_requirements` |
| ~2294-2301 | Add `StationGroupStore` to module map |

## Verification

- Grep for `required_features` in architecture-context.md — should only appear in
  "replaces X" context, not as a current Protocol attribute
- Grep for `forecast_target[^s]` in architecture-context.md — should find zero matches
- Grep for `CompatibilityReport` in spec — fields should match design doc exactly
