---
status: DRAFT
created: 2026-03-26
scope: types + DB schema + store + service + tests
depends_on: [007]  # Independent of 003; plan 003 Phase 2 depends on THIS plan's Phase 1
---

# 004 — Add `parameter` field to SkillScore and SkillDiagram

## Problem

`SkillScore` and `SkillDiagram` do not carry a `parameter` field. When multi-target
models produce hindcasts for both `discharge` and `water_level`, skill scores for
different parameters are distinguished only by caller context — there is no audit
trail in the stored records.

---

## Changes

### Phase 1 — Type Changes

#### 1A. `src/sapphire_flow/types/skill.py`

Add `parameter: str` to both `SkillScore` and `SkillDiagram` frozen dataclasses.
Place the field after `model_id` for logical grouping:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillScore:
    id: SkillScoreId
    station_id: StationId
    model_id: ModelId
    parameter: str            # NEW
    artifact_id: ArtifactId
    ...
```

Same for `SkillDiagram`.

**Spec update:** Also add `parameter: str` to `SkillScore` and `SkillDiagram` definitions in
`docs/spec/types-and-protocols.md` (authoritative for type definitions).

**Dependencies:** None.

### Phase 2 — Service Layer

#### 2A. `src/sapphire_flow/services/skill/service.py`

`compute_skill_for_station()` already has a required `parameter: str` argument (from plan 003).
Update all `SkillScore(...)` and `SkillDiagram(...)` construction sites to pass `parameter=parameter`.

**Dependencies:** Plan 003 complete.

### Phase 3 — Store Protocol and Implementations

#### 3A. `src/sapphire_flow/protocols/stores.py` — `SkillStore`

Add optional `parameter: str | None = None` to all three fetch methods:

```python
def fetch_latest_scores(
    self,
    station_id: StationId,
    model_id: ModelId | None = None,
    parameter: str | None = None,       # NEW
) -> list[SkillScore]:
    ...

def fetch_latest_diagrams(
    self,
    station_id: StationId,
    model_id: ModelId | None = None,
    diagram_type: str | None = None,
    parameter: str | None = None,       # NEW
) -> list[SkillDiagram]:
    ...

def fetch_scores_by_regime(
    self,
    station_id: StationId,
    model_id: ModelId,
    parameter: str | None = None,       # NEW
) -> list[SkillScore]:
    ...
```

Default `None` means "all parameters" — backward-compatible with existing callers.

**Spec update:** Also update `SkillStore` fetch method signatures in
`docs/spec/types-and-protocols.md` to include the `parameter` filter.

**Dependencies:** 1A.

#### 3B. `src/sapphire_flow/store/skill_store.py` — `PgSkillStore`

Add `parameter` filter to each fetch method:

```python
if parameter is not None:
    q = q.where(skill_scores.c.parameter == parameter)
```

Update `store_skill_score()` and `store_skill_diagram()` to write the `parameter` value.

**Dependencies:** 3A.

#### 3C. `tests/fakes/fake_stores.py` — `FakeSkillStore`

Add `parameter: str | None = None` to all three fetch methods. Add filter predicate:

```python
and (parameter is None or s.parameter == parameter)
```

**Dependencies:** 3A.

### Phase 4 — DB Schema Migration

#### 4A. Alembic migration `0016_skill_parameter_column.py`

Add `parameter TEXT` column to `skill_scores` and `skill_diagrams` tables.

**Migration ordering** (critical): add column as NULL first, backfill, then set NOT NULL:

```python
def upgrade() -> None:
    # Step 1: add nullable column
    op.add_column("skill_scores", sa.Column("parameter", sa.Text, nullable=True))
    op.add_column("skill_diagrams", sa.Column("parameter", sa.Text, nullable=True))

    # Step 2: backfill — all existing rows are discharge (v0 Swiss data)
    op.execute("UPDATE skill_scores SET parameter = 'discharge' WHERE parameter IS NULL")
    op.execute("UPDATE skill_diagrams SET parameter = 'discharge' WHERE parameter IS NULL")

    # Step 3: set NOT NULL
    op.alter_column("skill_scores", "parameter", nullable=False)
    op.alter_column("skill_diagrams", "parameter", nullable=False)
```

#### 4B. `src/sapphire_flow/db/metadata.py`

Add `parameter` column to both table definitions.

**Pre-existing note:** The `skill_scores` table currently uses `is_stale: bool` in the DB
schema (`0001_v0_schema.py` line 635) while the spec defines `freshness: SkillFreshness`
(enum). This plan does not resolve that mismatch — it is a separate concern — but implementers
should be aware.

**Dependencies:** None (migration is independent).

---

### Phase 5 — Test Changes

#### 5A. Tests that break without changes

| Test file | Why it breaks | Fix |
|---|---|---|
| `tests/unit/services/skill/test_service.py` — all `SkillScore(...)` assertions | `SkillScore` gains required `parameter` field | Add `parameter="discharge"` to all `SkillScore` and `SkillDiagram` construction |
| `tests/fakes/fake_stores.py` — `FakeSkillStore` | Stored `SkillScore` objects now require `parameter` | Update fake fixture data |
| Any test that constructs `SkillScore` or `SkillDiagram` directly | Missing required field | Add `parameter="discharge"` |

#### 5B. New tests needed

**`tests/unit/services/skill/test_service.py`**

1. `TestParameterStamping` — `test_parameter_stamped_on_scores`:
   - Call `compute_skill_for_station` with `parameter="water_level"`.
   - Assert all returned `SkillScore` objects have `parameter == "water_level"`.

2. `TestParameterStamping` — `test_parameter_stamped_on_diagrams`:
   - Same as above for `SkillDiagram`.

**`tests/unit/store/test_skill_store.py`** (or integration equivalent)

3. `TestParameterFilter` — `test_fetch_filters_by_parameter`:
   - Store scores for both `"discharge"` and `"water_level"`.
   - Fetch with `parameter="discharge"` — assert only discharge scores returned.
   - Fetch with `parameter=None` — assert all scores returned.

4. `TestParameterFilter` — `test_fetch_diagrams_by_parameter`:
   - Same pattern for diagrams.

5. `TestParameterFilter` — `test_fetch_scores_by_regime_with_parameter`:
   - Store regime-stratified scores for two parameters.
   - Fetch with parameter filter — assert correct subset.

**`tests/fakes/`**

6. Verify `FakeSkillStore` parameter filtering works:
   - Store mixed-parameter scores, fetch with filter, assert correct results.

### Phase 6 — Training Orchestration + Guard Removal

This phase completes the multi-parameter skill computation story. It is the reason plan 003's
`NotImplementedError` guard existed — without `SkillScore.parameter` (Phases 1–4), running
skill computation for non-discharge parameters would produce indistinguishable records.

#### 6A. Remove `NotImplementedError` guard in `flows/compute_skills.py`

Delete the temporary guard (lines 81–85):

```python
# DELETE THIS BLOCK (added in plan 003 Phase 2B as temporary safeguard)
if parameter != "discharge":
    raise NotImplementedError(
        "Non-discharge skill computation requires SkillScore.parameter "
        "field (plan 004) to comply with WMO verification standards"
    )
```

With Phases 1–4 landed, `SkillScore` carries `parameter` and the guard is no longer needed.

**Dependencies:** Phases 1A, 2A, 3A–3C, 4A–4B all complete.

#### 6B. Multi-parameter loop in `flows/train_models.py`

Update the skill computation call site (~lines 295–317) to loop over the model's
target parameters instead of hardcoding `"discharge"`:

```python
# BEFORE
for sid in station_ids_for_skill:
    scores, _ = compute_skills_flow(
        station_id=sid,
        model_id=unit.model_id,
        artifact_id=artifact_id,
        parameter="discharge",           # ← hardcoded
        ...
    )
    if scores:
        skill_computed = True

# AFTER
target_parameters = model_instance.data_requirements.target_parameters
for sid in station_ids_for_skill:
    for parameter in sorted(target_parameters):
        scores, _ = compute_skills_flow(
            station_id=sid,
            model_id=unit.model_id,
            artifact_id=artifact_id,
            parameter=parameter,          # ← from model requirements
            ...
        )
        if scores:
            skill_computed = True
```

`sorted()` ensures deterministic iteration order for reproducibility and logging.

**Note:** `model_instance` (the `ForecastModel`) is already in scope at this point in the
flow (loaded at ~line 212). `model_instance.data_requirements.target_parameters` is a
`frozenset[str]` — e.g. `frozenset({"discharge"})` for single-target, `frozenset({"discharge",
"water_level"})` for multi-target.

**Dependencies:** 6A (guard must be removed first, otherwise non-discharge calls raise).

#### 6C. Test for `TestNonDischargeGuard` removal

The existing test in `tests/unit/flows/test_compute_skills.py::TestNonDischargeGuard` asserts
`NotImplementedError` for non-discharge parameters. After Phase 6A removes the guard, this test
must be updated:

- **Delete** `test_non_discharge_raises_not_implemented` (it tests removed behavior).
- **Add** `test_water_level_parameter_computes_skill` — call `compute_skills_flow` with
  `parameter="water_level"` and verify it succeeds (returns scores with
  `parameter == "water_level"`).

#### 6D. Test for multi-parameter training loop

**`tests/unit/flows/test_train_models.py`** (or inline in existing test file):

`TestMultiParameterSkillComputation` — `test_computes_skills_for_all_target_parameters`:
- Use a fake model with `data_requirements.target_parameters = frozenset({"discharge", "water_level"})`.
- Run training flow for 2 stations.
- Assert `compute_skills_flow` is called 4 times total (2 stations × 2 parameters).
- Assert skill scores for both parameters are stored.

**Dependencies:** 6A, 6B.

---

## Dependency Graph

```
1A (SkillScore/SkillDiagram types)
  └─ 2A (service stamps parameter)
  └─ 3A (SkillStore Protocol)
       └─ 3B (PgSkillStore)
       └─ 3C (FakeSkillStore)

4A/4B (DB migration + metadata — independent of 1–3)

5A (fix broken tests — depends on 1A, 2A)
5B (new tests — depends on 2A, 3A, 3B, 3C)

6A (remove guard — depends on 1A, 2A, 3A–3C, 4A–4B)
  └─ 6B (training loop — depends on 6A)
       └─ 6C, 6D (tests — depends on 6A, 6B)
```

Phases 1+2, 3, and 4 can proceed in parallel. Phase 5 depends on 1–4. Phase 6 depends on all of 1–5.

**Commit boundaries:**
- Phases 1–5 can be split across commits as convenient.
- **Phase 6A+6B+6C+6D must be a single commit** — removing the guard (6A) without updating
  the training loop (6B) leaves the system in a state where non-discharge skill computation
  is silently enabled but never invoked. Combining them ensures the guard removal and the
  loop that exercises it land atomically.

---

## File-Level Change Summary (updated)

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/skill.py` | Add `parameter: str` to `SkillScore` and `SkillDiagram` | 1A |
| `docs/spec/types-and-protocols.md` | Add `parameter: str` to `SkillScore`, `SkillDiagram`; add `parameter` filter to `SkillStore` | 1A, 3A |
| `src/sapphire_flow/services/skill/service.py` | Pass `parameter=` to score/diagram construction | 2A |
| `src/sapphire_flow/protocols/stores.py` | Add `parameter` filter to `SkillStore` fetch methods | 3A |
| `src/sapphire_flow/store/skill_store.py` | Add `parameter` filter + write to `PgSkillStore` | 3B |
| `tests/fakes/fake_stores.py` | Add `parameter` filter to `FakeSkillStore` | 3C |
| `src/sapphire_flow/db/metadata.py` | Add `parameter` column to skill table definitions | 4B |
| `alembic/versions/0016_skill_parameter_column.py` | New migration: add + backfill parameter column | 4A |
| `tests/unit/services/skill/test_service.py` | Fix existing + add `TestParameterStamping` | 5A, 5B |
| `tests/unit/store/test_skill_store.py` | Add `TestParameterFilter` | 5B |
| `tests/integration/store/test_skill_store.py` | Update `_make_score()` and `_make_diagram()` helpers | 5A |
| `tests/fakes/fake_stores.py` | Update fixture data + add filter tests | 5A, 5B |
| `src/sapphire_flow/flows/compute_skills.py` | Remove `NotImplementedError` guard | 6A |
| `src/sapphire_flow/flows/train_models.py` | Add multi-parameter loop around `compute_skills_flow` call | 6B |
| `tests/unit/flows/test_compute_skills.py` | Replace guard test with water_level success test | 6C |
| `tests/unit/flows/test_train_models.py` | Add `TestMultiParameterSkillComputation` | 6D |

---

## Guardrails

- Run `uv run pytest` before starting and after each phase
- Migration: add column NULL → backfill → set NOT NULL (never add NOT NULL to non-empty table)
- After Phase 3: verify `isinstance(FakeSkillStore(), SkillStore)` passes
- After Phase 4: run `alembic upgrade head` against test DB
- After Phase 6A: existing `test_non_discharge_raises_not_implemented` WILL FAIL — this is expected; fix in 6C
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)

---

## Open Items

1. **`is_stale` vs `freshness: SkillFreshness`** — pre-existing mismatch between DB schema
   (Boolean `is_stale`) and spec (enum `SkillFreshness`). This migration is a natural
   opportunity to fix it, but doing so increases scope. Recommend a separate plan.

2. **Backfill assumption** — all existing rows are backfilled with `'discharge'`. This is
   correct for v0 Swiss data (BAFU stations). If non-discharge skill scores somehow exist
   before this migration, they will be incorrectly labeled. In practice this cannot happen
   in v0.

3. **Parallel vs sequential parameter loop (Phase 6B)** — The nested loop in `train_models_flow`
   runs `compute_skills_flow` sequentially per parameter. For v0 this is fine (single-target
   models). When multi-target models arrive, the caller could fan out as parallel Prefect task
   submissions for performance. This optimization is deferred — sequential is correct and simple.
