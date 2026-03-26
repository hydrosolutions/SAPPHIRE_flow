---
status: DRAFT
created: 2026-03-26
scope: design doc + spec + architecture-context consistency fixes
depends_on: [001, 002]
---

# 006 — Onboarding type fixes from critical review

Consolidates fixes for issues 1, 3–10 from the critical review of plan 001.
Issue 2 (multi-target predict chain) is covered by plan 003.

## Decisions

### D1. Drop `OnboardingUnit` — reuse `TrainingUnit` (Issue 3)

`OnboardingUnit` is structurally identical to `TrainingUnit` (field name difference
only). Drop it. `OnboardingUnitResult.unit` references `TrainingUnit` directly.

Files: design doc §3f, §1 (mermaid diagram label "Per OnboardingUnit"), §6d (`onboard_model` signature), §8 (five `@task` signatures using `unit: OnboardingUnit`), §11 (P4 phase description); spec (types/model_onboarding.py section); architecture-context.md

### D2. `CompatibilityReport.compatible` → method `is_compatible()` (Issue 4)

Remove `compatible: bool` stored field. Add:

```python
def is_compatible(self) -> bool:
    return (
        self.protocol_conforms
        and not self.missing_target_parameters
        and not self.missing_past_dynamic
        and not self.missing_future_dynamic
        and not self.missing_static_features
        and self.time_step_compatible
    )
```

Project-wide convention: derived values should be methods, not stored fields.

Files: design doc §3f, spec, architecture-context.md (if referenced)

### D3. Simplify `SkillGateResult` (Issues 5, 8)

Remove `model_id`, `station_id`, `group_id` — context comes from parent `TrainingUnit`.
Add `artifact_id: ArtifactId` for audit trail.

```python
class SkillGateResult:
    artifact_id: ArtifactId
    passed: bool
    metric_scores: dict[str, float]
    thresholds: dict[str, float]
    failing_metrics: frozenset[str]
```

Files: design doc §3f, spec

### D4. `OnboardingOutcome` enum + drop stored counters (Issues 6, 7)

Add enum:

```python
class OnboardingOutcome(Enum):
    PROMOTED = "promoted"
    GATE_REJECTED = "gate_rejected"
    SKIPPED_COMPAT = "skipped_compat"
    SKIPPED_NO_DATA = "skipped_no_data"
    FAILED_TRAINING = "failed_training"
    FAILED_HINDCAST = "failed_hindcast"
    FAILED_SKILL = "failed_skill"
```

Update `OnboardingUnitResult`:

```python
class OnboardingUnitResult:
    unit: TrainingUnit              # was OnboardingUnit (D1)
    outcome: OnboardingOutcome
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    hindcast_steps: list[HindcastStepResult]
    skill_gate: SkillGateResult | None
    error: str | None = None
```

Update `ModelOnboardingResult` — drop stored counters, add methods:

```python
class ModelOnboardingResult:
    model_id: ModelId
    units: tuple[OnboardingUnitResult, ...]

    def total(self) -> int:
        return len(self.units)

    def promoted_count(self) -> int:
        return sum(1 for u in self.units if u.outcome == OnboardingOutcome.PROMOTED)

    _FAILED = frozenset({OnboardingOutcome.FAILED_TRAINING, OnboardingOutcome.FAILED_HINDCAST, OnboardingOutcome.FAILED_SKILL})
    _SKIPPED = frozenset({OnboardingOutcome.SKIPPED_COMPAT, OnboardingOutcome.SKIPPED_NO_DATA})

    def failed_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in self._FAILED)

    def skipped_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in self._SKIPPED)

    def gate_rejected_count(self) -> int:
        return sum(1 for u in self.units
                   if u.outcome == OnboardingOutcome.GATE_REJECTED)
```

Files: design doc §3f, spec, types/enums.py (new enum)

### D5. `fetch_groups_for_model()` queries `group_model_assignments` (Issue 1)

Redefine `StationGroupStore.fetch_groups_for_model(model_id)` to query
`group_model_assignments` table instead of cross-joining per-station
`model_assignments`.

Files: architecture-context.md (store description), spec (StationGroupStore Protocol),
design doc §10 (consistency checks)

Note: this changes the semantics from "groups with an active _artifact_" to "groups with
an active _assignment_." These differ when a model has an artifact but the skill gate
rejected it. Callers should be aware that post-D5, `fetch_groups_for_model` returns groups
where the model is actively assigned, not merely where an artifact exists.

### D6. `skill_gate_thresholds` in `DeploymentConfig` (Issue 9)

Add to spec's `DeploymentConfig`:

```python
skill_gate_thresholds: dict[str, float]  # metric_name → minimum value
# Default: {"crpss": 0.0, "bss_danger_1": 0.0}
# Evaluated as min-across-strata (Issue 10)
```

Files: spec (DeploymentConfig section), design doc §6b, config/deployment.py

### D7. Skill gate aggregation: min across strata (Issue 10)

Document in design doc §6b and spec:

> The skill gate evaluates `min(metric_value across all strata)` against the
> threshold. A model must meet the threshold in every stratum (lead time × season
> × flow regime) to pass. This prevents deploying models with hidden regime-specific
> weaknesses.
>
> In v0, the gate logs results but does not block promotion (auto-promote).

**Note:** This is a deliberate change from the existing design doc §6b wording, which says
"compute mean score across valid strata." The min-across-strata rule is stricter — a model
must meet the threshold in _every_ stratum, not just on average.

Files: design doc §6b, spec (if gate logic is described there)

### D8. `HindcastStepResult` cross-module import (Issue from review)

Document in spec's module map that `types/model_onboarding.py` imports
`TrainingUnit` and `HindcastStepResult` from `types/training.py`. Also add
`OnboardingOutcome` to the module listing for `model_onboarding.py`.
Clarify filename: `model_onboarding.py` (not `onboarding.py`, which is Flow 5's
station onboarding).

Files: spec (module map)

---

## File-level change summary

| File | Changes |
|------|---------|
| `docs/design/v0-flow13-model-onboarding.md` | D1–D7: update §3f types, §1 diagram, §6b service, §6d `onboard_model` signature, §8 task signatures, §9 consistency, §11 phase table |
| `docs/spec/types-and-protocols.md` | D1–D8: update onboarding types, SkillGateResult, DeploymentConfig, module map |
| `docs/architecture-context.md` | D5: update fetch_groups_for_model description; D1: remove OnboardingUnit references |

## Verification

- Grep for `OnboardingUnit` in spec — should find zero matches (only `OnboardingUnitResult`)
- Grep for `compatible:` as a field in spec — should find zero matches on CompatibilityReport
- Grep for `station_id` in SkillGateResult definition — should find zero matches
- Grep for `total:` / `promoted:` / `skipped:` / `failed:` as fields on ModelOnboardingResult — zero
- Grep for `skill_gate_thresholds` in spec — should match DeploymentConfig section
- Grep for `OnboardingOutcome` in spec — should match enums section
