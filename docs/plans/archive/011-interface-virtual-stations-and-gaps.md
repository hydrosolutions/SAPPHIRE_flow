---
status: ARCHIVED
created: 2026-03-27
scope: backlog — remaining items after splitting into plans 012–015
depends_on: []
---

# 011 — Backlog: Remote Training, Selective Hindcast, Dataclass Cleanup

## Context

Original plan 011 ("ForecastInterface Alignment, Virtual Stations, and Architectural
Gaps") was a collection of loosely related investigation items. It has been split into
focused plans:

- **Plan 012** — Forecast QC integration in Flow 1 (was §C)
- **Plan 013** — v0 scale re-evaluation (was §G)
- **Plan 014** — ForecastInterface adapter design + weather source mapping (was §A + §D)
- **Plan 015** — Virtual station support (was §B, promoted from v2.0 to v1)

The remaining items (§E, §F, §H) are retained here as backlog.

---

## E — Remote Training for Large ML Models

**What:** Large models (LSTM, transformer-based) may require GPU resources not
available on the default Docker Compose worker. The design for remote/GPU training
exists in `docs/design/v0-flow13-model-onboarding.md` §9 but is barely surfaced in
`architecture-context.md`.

**Existing design (v0-flow13 §9):**
- v1: `ModelDataRequirements` gains `compute_backend: Literal["cpu", "gpu"]`. Prefect
  routes to a `gpu_training` work pool via `with_options(work_pool_name=...)`.
- v0: all training on CPU `default` pool; GPU routing annotated but not active.
- No Protocol changes — routing stays in the flow layer.

**Tasks:**

1. Decide whether `architecture-context.md` should promote remote training to a
   first-class architectural concern (dedicated subsection) or if the flow-13 design
   doc is sufficient.
2. If promoted: document the work pool topology, GPU pool provisioning (cloud provider
   agnostic), artifact transfer between pools, and failure/retry semantics.
3. Consider whether remote training should be offered as an optional paid service —
   this affects whether the feature is behind a deployment-level toggle.

**Target:** v1.

---

## F — Selective Hindcast Recomputation

**What:** When individual hindcast steps fail (recorded as `HindcastStepResult(success=False)`),
the only recovery path is re-running the entire Flow 13 onboarding for that model/station
unit — which re-runs the full hindcast. At ~1000 stations with sub-daily data,
full-period re-runs for individual step failures are prohibitively expensive.

**Tasks:**

1. **Flow 7 selective replay** — Extend `run_{station|group}_hindcast()` to accept an
   optional set of timestamps to replay, instead of always running the full period.
   Only re-run steps where `success=False`.
2. **Flow 10 gap-fill mode** — Flow 10 (skill recomputation, scoped for v1) could
   include a "fill gaps" mode: identify failed hindcast steps, re-run them selectively,
   then recompute skill scores from the updated result set.
3. **Incremental skill update** — When individual steps are re-run, skill scores should
   be updated incrementally rather than recomputed from scratch. Design the merge
   semantics (replace failed step results, recompute affected strata scores).
4. **API/CLI trigger** — Provide a way to trigger selective hindcast re-runs for
   specific (model, station, timestamp) combinations — either via API endpoint or
   CLI command.

**Target:** v1 — needed before large-scale experiments hit failure-recovery limits.

---

## H — Frozen Dataclass Immutable-Container Consistency

**What:** Three QC frozen dataclasses in the spec (`QcRuleParams`, `StationQcOverride`,
`ForecastQcRuleParams`) use `dict[str, float]` fields, which makes instances non-hashable
and allows mutation despite `frozen=True`. Plan 006 (D3) fixes this for `SkillGateResult`
by using `tuple[tuple[str, float], ...]`, but the QC types remain inconsistent.

**Tasks:**

1. Audit all frozen dataclasses in `types-and-protocols.md` for mutable container fields
   (`list`, `dict`, `set`). Replace with immutable equivalents (`tuple`, `tuple[tuple[...], ...]`,
   `frozenset`) where the type is used as a result or value object.
2. For config-like types that are never hashed or collected in sets, decide whether the
   ergonomic cost of `tuple[tuple[...], ...]` outweighs the purity benefit — document
   the convention either way.
3. Also fix `TrainingResult.hindcast_steps: list[HindcastStepResult]` →
   `tuple[HindcastStepResult, ...]` for consistency with `OnboardingUnitResult.hindcast_steps`
   (plan 006 D4 noted this but deferred it).
4. Codify the resulting convention in `conventions.md`.

**Target:** Low priority cleanup.
