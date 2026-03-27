---
status: READY
created: 2026-03-26
scope: types + DB schema + store + service + tests
depends_on: []  # 007 (ARCHIVED, already merged). Independent of 003; plan 003 Phase 2 depends on THIS plan's Phase 1
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
    id: UUID
    station_id: StationId
    model_id: ModelId
    parameter: str            # NEW
    model_artifact_id: ArtifactId
    ...
```

Same for `SkillDiagram`:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillDiagram:
    id: UUID
    station_id: StationId
    model_id: ModelId
    parameter: str            # NEW
    model_artifact_id: ArtifactId
    ...
```

**Spec update:** Also add `parameter: str` to `SkillScore` and `SkillDiagram` definitions in
`docs/spec/types-and-protocols.md` (authoritative for type definitions). While updating the spec,
also add `eval_period_start` and `eval_period_end` fields to both types — they are present in
the implementation but currently missing from the spec (pre-existing drift).

**Architecture doc update:** Deferred to Phase 4B, which handles all `architecture-context.md`
edits for both tables (adding `parameter`, fixing `eval_period` drift, documenting
`uq_skill_diagrams_natural_key`).

**Dependencies:** None.

### Phase 2 — Service Layer

#### 2A. `src/sapphire_flow/services/skill/service.py`

`compute_skill_for_station()` already has a required `parameter: str` keyword argument
(from plan 003) but does not thread it to the private helpers.

Three changes required:

1. Add `parameter: str` to the signature of `_compute_scores()` (~line 138).
2. Add `parameter: str` to the signature of `_compute_diagrams()` (~line 232).
3. In both functions' `_add()` closures, pass `parameter=parameter` to the `SkillScore(...)` and
   `SkillDiagram(...)` construction sites (~lines 166 and 268).
4. In `compute_skill_for_station()`, pass `parameter=parameter` to both `_compute_scores()` and
   `_compute_diagrams()` calls (~lines 358 and 378).

**Dependencies:** Phase 1A (types must have the field before construction sites can pass it).

### Phase 3 — Store Protocol and Implementations

#### 3A. `src/sapphire_flow/protocols/stores.py` — `SkillStore`

Add optional `parameter: str | None = None` to all three fetch methods **and** to
`mark_stale`. Existing required arguments are unchanged:

```python
def fetch_latest_scores(
    self,
    station_id: StationId,
    model_id: ModelId,                        # unchanged (required)
    skill_source: SkillSource | None = None,  # unchanged
    parameter: str | None = None,             # NEW
) -> list[SkillScore]:
    ...

def fetch_latest_diagrams(
    self,
    station_id: StationId,
    model_id: ModelId,                        # unchanged (required)
    diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,  # unchanged
    parameter: str | None = None,             # NEW
) -> list[SkillDiagram]:
    ...

def fetch_scores_by_regime(
    self,
    station_id: StationId,
    model_id: ModelId,                        # unchanged (required)
    flow_regime: FlowRegime,                  # unchanged (required)
    parameter: str | None = None,             # NEW
) -> list[SkillScore]:
    ...

def mark_stale(
    self,
    station_id: StationId,
    start: UtcDatetime,
    end: UtcDatetime,
    parameter: str | None = None,             # NEW
) -> int:
    ...
```

Default `None` means "all parameters" — backward-compatible with existing callers.

**Why `mark_stale` needs `parameter`:** Without it, recomputing skill scores for
`water_level` would also stale out valid `discharge` scores at the same station. With
multi-parameter models, a training run that regenerates hindcasts for one parameter
should only invalidate that parameter's scores — not silently corrupt the other
parameter's skill records.

**`store_skill_scores` / `store_skill_diagrams` do NOT need `parameter`:** These write
methods receive typed `SkillScore`/`SkillDiagram` objects that already carry `parameter`
as a field after Phase 1A. The stores just persist what they receive.

**Spec update:** Also update `SkillStore` method signatures in
`docs/spec/types-and-protocols.md` to include the `parameter` filter on all four methods.

**Dependencies:** 1A.

#### 3B. `src/sapphire_flow/store/skill_store.py` — `PgSkillStore`

Five changes:

1. **Fetch methods** (`fetch_latest_scores`, `fetch_latest_diagrams`, `fetch_scores_by_regime`):
   add `parameter: str | None = None` and filter clause:

   ```python
   if parameter is not None:
       q = q.where(skill_scores.c.parameter == parameter)
   ```

2. **`mark_stale()`**: add `parameter: str | None = None` and filter clause (same pattern).

3. **`_score_to_row()`** (~line 116): add `"parameter": s.parameter` to the returned dict.

4. **`_diagram_to_row()`** (~line 140): add `"parameter": d.parameter` to the returned dict.

5. **`_row_to_score()`** (~line 161) and **`_row_to_diagram()`** (~line 189): add
   `parameter=row["parameter"]` to the `SkillScore(...)` and `SkillDiagram(...)` construction.

**Dependencies:** 3A.

#### 3C. `tests/fakes/fake_stores.py` — `FakeSkillStore`

Add `parameter: str | None = None` to all three fetch methods and `mark_stale`. Add filter
predicate:

```python
and (parameter is None or s.parameter == parameter)
```

**Dependencies:** 3A.

### Phase 4 — DB Schema Migration

#### 4A. Alembic migration `0016_skill_parameter_column.py`

Add `parameter TEXT NOT NULL` column to `skill_scores` and `skill_diagrams` tables, update
the `uq_skill_scores_natural_key` unique index to include `parameter`, and **create**
`uq_skill_diagrams_natural_key` — a new unique index on `skill_diagrams` that includes
`parameter` from the start.

**Why `skill_diagrams` needs a unique index now:** `PgSkillStore.store_skill_diagrams()` uses
`on_conflict_do_nothing()`, but without a unique constraint this is a no-op — every re-run
silently accumulates duplicate rows. This is a pre-existing bug (not introduced by this plan),
but adding `parameter` without fixing it would make the duplication worse (more variants ×
more duplicates). Since we are already writing a migration that touches both tables, fixing
the diagrams gap here is low incremental cost and eliminates a data integrity issue.

**Safe NOT NULL addition.** The migration uses the `server_default` / drop-default pattern
established by migration 0008: add the column as `NOT NULL` with `server_default="discharge"`,
then immediately drop the default. This is safe even if a developer has existing skill rows in
their local DB — the default populates existing rows before the NOT NULL constraint is enforced.
The default is removed immediately so new inserts must provide an explicit value.

Developers who want a clean slate can run `alembic downgrade 0015 && alembic upgrade head`
(targets only the 0016 migration — does not destroy other tables). All migrations will be
squashed into a single `0001` before v0 ships (see Guardrails).

**Migration boilerplate:** The implementer must add the standard Alembic header
(`revision = "0016"`, `down_revision = "0015"`, `branch_labels = None`,
`depends_on = None`) and a module-level docstring following the convention in
existing migrations (e.g. `0015_hindcast_parameter_index.py`). Omitted here for brevity.

```python
def upgrade() -> None:
    # Add parameter column using server_default/drop pattern (matches migration 0008).
    # Safe even if skill rows already exist in a developer's local DB.
    op.add_column("skill_scores", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.add_column("skill_diagrams", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.alter_column("skill_scores", "parameter", server_default=None)
    op.alter_column("skill_diagrams", "parameter", server_default=None)

    # Update unique index to include parameter as discriminator
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",                           # NEW
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )

    # NEW — skill_diagrams had no unique constraint at all (pre-existing gap)
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",                           # NEW with this plan
            "lead_time_hours",
            "diagram_type",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )
    op.drop_column("skill_diagrams", "parameter")
    op.drop_column("skill_scores", "parameter")
```

**Why the scores index matters:** Without `parameter` in `uq_skill_scores_natural_key`, inserting
a `water_level` score after a `discharge` score for the same artifact/metric/lead-time
combination will produce a false unique-constraint violation.

**Diagrams natural key derivation:** Analogous to `uq_skill_scores_natural_key`, replacing
`metric` with `diagram_type`, `forcing_type` with `threshold_level` (diagrams are
forcing-agnostic — the column does not exist on `skill_diagrams`). `threshold_level` is NULL
for `rank_histogram` diagrams and a danger level name for `reliability`/`roc` — hence the
`COALESCE` wrapper. `computation_version` is intentionally excluded (same as scores) — the
unique constraint ensures idempotent re-insertion via `on_conflict_do_nothing()`.

#### 4B. `src/sapphire_flow/db/metadata.py`

Three changes:

1. Add `sa.Column("parameter", sa.Text, nullable=False)` to both `skill_scores` (after
   `model_artifact_id`, ~line 833) and `skill_diagrams` (after `model_artifact_id`, ~line 880)
   table definitions.

2. Update the `uq_skill_scores_natural_key` index definition (~lines 911–922) to include
   `skill_scores.c.parameter` as a key component.

3. **Add `uq_skill_diagrams_natural_key`** — a new `sa.Index(...)` after the `skill_diagrams`
   table definition, following the same pattern as `uq_skill_scores_natural_key`:

   ```python
   sa.Index(
       "uq_skill_diagrams_natural_key",
       skill_diagrams.c.station_id,
       skill_diagrams.c.model_artifact_id,
       skill_diagrams.c.skill_source,
       skill_diagrams.c.parameter,
       skill_diagrams.c.lead_time_hours,
       skill_diagrams.c.diagram_type,
       sa.text("COALESCE(season, '')"),
       sa.text("COALESCE(flow_regime, '')"),
       sa.text("COALESCE(threshold_level, '')"),
       unique=True,
   )
   ```

**Architecture doc update:** Update the `skill_scores` and `skill_diagrams` table schemas in
`docs/architecture-context.md` (~lines 1806–1827 and 1836–1851):
- Add `parameter TEXT NOT NULL` to both tables. The architecture doc describes parameter-scoped
  skill computation in prose (Flow 7 multi-target note, Flow 8 parameter scoping) but the table
  schemas currently omit the column — this closes that gap. This also resolves the
  forward-reference at ~line 2413 which says `SkillScore.parameter: str` as if the field
  already exists.
- Add `eval_period_start TIMESTAMPTZ NOT NULL` and `eval_period_end TIMESTAMPTZ NOT NULL` to
  both tables. These exist in `metadata.py` (added in migration 0008) and in `types/skill.py`
  but were never added to the architecture doc — pre-existing drift, fixed here since we are
  already editing these table definitions.
- **Replace `is_stale: BOOLEAN DEFAULT FALSE` with `freshness: TEXT NOT NULL DEFAULT 'current'
  CHECK (freshness IN ('current', 'stale'))`** on `skill_scores` (~line 1828). Migration 0009
  replaced the boolean `is_stale` with a text enum `freshness` column. The architecture doc was
  never updated — this is the largest undocumented schema divergence in the skill tables. Also
  update the 5 prose references to `is_stale` in the architecture doc (~lines 1089, 1141, 1151,
  1245, 1264) to use `freshness = 'stale'` / `freshness = 'current'` language instead.
- Document `uq_skill_scores_natural_key`, `uq_skill_diagrams_natural_key`, and
  `ix_skill_scores_station_freshness` (partial index on `(station_id, freshness,
  eval_period_start, eval_period_end) WHERE freshness = 'current'`) in the index
  documentation section (~line 1832). The current doc only has a non-unique read index; the
  unique constraint and partial freshness index were added in migrations 0008/0010 but never
  documented.

**Note on `SkillDiagram.computed_at`:** `SkillScore` has a `computed_at: UtcDatetime` field
(spec line 1344, code line 27) but `SkillDiagram` does not — neither in the spec nor in the
implementation. This asymmetry is pre-existing and intentional (diagrams are stored alongside
scores in the same computation; the score's `computed_at` serves as the timestamp for both).
Confirm this is still the intended design before making the spec edit in Phase 1A — if
`computed_at` should be added to `SkillDiagram`, it should happen in the same commit.

**Dependencies:** None (migration is independent).

---

### Phase 5 — Test Changes

#### 5A. Tests that break without changes

| Test file | Why it breaks | Fix |
|---|---|---|
| `tests/unit/services/skill/test_service.py` | `SkillScore` and `SkillDiagram` are constructed inside `service.py` which these tests call; returned objects will now carry `parameter` | No construction-site fixes needed here — Phase 2A fixes the source. Tests may need updated assertions if they compare against expected objects. |
| `tests/integration/store/test_skill_store.py` — `_make_score()` (~line 82) and `_make_diagram()` (~line 121) | Helpers construct `SkillScore(...)` and `SkillDiagram(...)` directly — missing required `parameter` field | Add `parameter="discharge"` (or accept as argument with default `"discharge"`) |
| `tests/fakes/fake_stores.py` — `FakeSkillStore` | Protocol gains `parameter` on fetch methods | Update in Phase 3C |
| Any other test constructing `SkillScore` or `SkillDiagram` directly | Missing required field | Add `parameter="discharge"` |

#### 5B. New tests needed

**`tests/unit/services/skill/test_service.py`**

1. `TestParameterStamping` — `test_parameter_stamped_on_scores`:
   - Call `compute_skill_for_station` with `parameter="water_level"`.
   - Assert all returned `SkillScore` objects have `parameter == "water_level"`.

2. `TestParameterStamping` — `test_parameter_stamped_on_diagrams`:
   - Same as above for `SkillDiagram`.

**`tests/integration/store/test_skill_store.py`** (existing file — no unit store test file exists)

3. `TestParameterFilter` — `test_fetch_filters_by_parameter`:
   - Store scores for both `"discharge"` and `"water_level"`.
   - Fetch with `parameter="discharge"` — assert only discharge scores returned.
   - Fetch with `parameter=None` — assert all scores returned.

4. `TestParameterFilter` — `test_fetch_diagrams_by_parameter`:
   - Same pattern for diagrams.

5. `TestParameterFilter` — `test_fetch_scores_by_regime_with_parameter`:
   - Store regime-stratified scores for two parameters.
   - Fetch with parameter filter — assert correct subset.

6. `TestParameterFilter` — `test_mark_stale_filters_by_parameter`:
   - Store CURRENT scores for both `"discharge"` and `"water_level"`.
   - Call `mark_stale(station_id, start, end, parameter="discharge")`.
   - Assert only discharge scores are STALE; water_level scores remain CURRENT.
   - Call `mark_stale(station_id, start, end, parameter=None)` — assert all remaining
     CURRENT scores become STALE.

**`tests/fakes/test_fakes.py`**

7. Verify `FakeSkillStore` parameter filtering works:
   - Store mixed-parameter scores, fetch with filter, assert correct results.
   - Also verify `mark_stale` with parameter filter on the fake.

**`tests/integration/store/test_skill_store.py`** (additional — diagram idempotency)

8. `TestDiagramIdempotency` — `test_store_diagrams_idempotent`:
   - Store a set of diagrams, then store the same set again.
   - Assert the total count in the DB is unchanged (no duplicates).
   - This validates that the new `uq_skill_diagrams_natural_key` makes `on_conflict_do_nothing()`
     effective. (Without the unique constraint, this test would fail — the second insert would
     double the row count.)

### Phase 6 — Training Orchestration + Guard Removal

This phase completes the multi-parameter skill computation story. It is the reason plan 003's
`NotImplementedError` guard existed — without `SkillScore.parameter` (Phases 1–4), running
skill computation for non-discharge parameters would produce indistinguishable records.

#### 6A. Refactor `compute_skills.py`: dual-interface pattern + remove guard

Two changes in a single phase (both touch the same file and are co-dependent):

**1. Remove `NotImplementedError` guard** (lines 81–85):

```python
# DELETE THIS BLOCK (added in plan 003 Phase 2B as temporary safeguard)
if parameter != "discharge":
    raise NotImplementedError(
        "Non-discharge skill computation requires SkillScore.parameter "
        "field (plan 004) to comply with WMO verification standards"
    )
```

With Phases 1–4 landed, `SkillScore` carries `parameter` and the guard is no longer needed.

**2. Introduce dual-interface pattern** — split into `compute_skills_task` (a `@task` for
`task.map()` fan-out) and `compute_skills_flow` (a thin `@flow` wrapper for standalone
deployment).

**Why dual-interface:** Two requirements conflict:
- **Parallelism** requires `task.map()`, which only works on `@task` functions. v0 experiments
  with multi-parameter models on datasets up to ~1000 stations. Sequential execution
  (1000 stations × 2 parameters × ~1s each ≈ 33 min) is too slow for developer iteration.
- **Standalone deployability** requires `@flow`. The orchestration standard
  (`docs/standards/orchestration.md` §Flow-to-Prefect mapping) lists `compute_skills` as a
  registered deployment (`compute-skills`) with trigger "Subflow or on-demand" — model admins
  can invoke it standalone to recompute skill scores for a specific station, and Flow 10 (v1
  broad recomputation) needs an independently triggerable entry point.

The dual-interface pattern satisfies both:

```python
# In compute_skills.py

@task(name="compute-skills-task", log_prints=False)
def compute_skills_task(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str,
    ...
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    structlog.contextvars.bind_contextvars(
        station_id=station_id, parameter=parameter,
    )
    ...  # full computation body (see below)


@flow(name="compute-skills", log_prints=False)
def compute_skills_flow(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str,
    ...
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    """Thin wrapper — preserves standalone deployment registration."""
    return compute_skills_task(
        station_id=station_id,
        model_id=model_id,
        artifact_id=artifact_id,
        parameter=parameter,
        ...
    )
```

**Internal helpers become plain functions.** The three existing `@task`-decorated helpers
(`_fetch_hindcasts`, `_fetch_observations`, `_store_skill_results`) lose their `@task`
decorators and become plain functions called sequentially inside `compute_skills_task`. This
is the correct trade-off:
- When invoked via `task.map()` (1000+ stations), per-helper task tracking would generate
  3000+ task nodes in the Prefect UI — noise with no operational value.
- When invoked standalone via `compute_skills_flow`, the wrapper `@flow` provides the
  run-level observability that matters (run ID, state, logs). Per-step tracking within a
  single station's skill computation adds no value — the entire operation takes ~1s.
- The helpers' only purpose was retry and observability. At the per-station granularity of
  `compute_skills_task`, a task-level retry on the outer task is more useful than retrying
  individual DB reads within it.

**Logging context:** Each `compute_skills_task` invocation binds its own structlog context at
the start of the task body via `structlog.contextvars.bind_contextvars(station_id=...,
parameter=...)`. This follows the logging standard's per-task binding pattern
(`docs/standards/logging.md` §Context binding protocol, item 3). Context is task-local in
Prefect's `ThreadPoolTaskRunner` — no cross-task leakage.

**Dependencies:** Phases 1A, 2A, 3A–3C, 4A–4B, 5A, 5B all complete (full test suite must be
green before refactoring the flow).

#### 6B. Multi-parameter fan-out in `flows/train_models.py`

Update the skill computation call site (~lines 295–317) to use `task.map()` fan-out over
`(station, parameter)` pairs instead of a sequential loop with hardcoded `"discharge"`.

```python
# BEFORE (lines 301–317 in train_models.py)
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

# AFTER — model_instance already in scope (line 212, None-guarded at line 223)
assert isinstance(model_instance, (StationForecastModel, GroupForecastModel))
target_parameters = model_instance.data_requirements.target_parameters
skill_pairs = [
    (sid, param)
    for sid in station_ids_for_skill
    for param in sorted(target_parameters)
]
futures = compute_skills_task.map(
    station_id=[sid for sid, _ in skill_pairs],
    model_id=unmapped(unit.model_id),
    artifact_id=unmapped(artifact_id),
    parameter=[param for _, param in skill_pairs],
    hindcast_run_id=unmapped(hindcast_run_id),
    hindcast_store=unmapped(hindcast_store),
    obs_store=unmapped(obs_store),
    skill_store=unmapped(skill_store),
    station_store=unmapped(station_store),
    flow_regime_store=unmapped(flow_regime_store),
    deployment_config=unmapped(deployment_config),
    clock=unmapped(clock),
)
skill_results = [f.result() for f in futures]
skill_computed = any(scores for scores, _ in skill_results)
```

`sorted()` ensures deterministic parameter ordering for reproducibility.
`unmapped()` shares constant arguments across all mapped tasks without duplication.
Prefect schedules mapped tasks concurrently up to the work pool's concurrency limit.

**Note on `target_parameters` access:** `model_instance` is already in scope at the skill
computation call site — assigned at line 212 (`models.get(unit.model_id)`) and None-guarded
by `continue` at line 223. Use the existing variable directly. However, `models` is annotated
as bare `dict | None` (unparameterized), so pyright sees `model_instance` as `Any`. Add
`assert isinstance(model_instance, (StationForecastModel, GroupForecastModel))` before
accessing `model_instance.data_requirements.target_parameters: frozenset[str]` to satisfy
pyright strict mode. `target_parameters` is `frozenset({"discharge"})` for single-target,
`frozenset({"discharge", "water_level"})` for multi-target.

**Import changes:** `train_models.py` must:
- Import `compute_skills_task` (not `compute_skills_flow`) from `sapphire_flow.flows.compute_skills`.
- Add `from prefect.utilities.annotations import unmapped`.
- **Remove** the now-unused `compute_skills_flow` import (ruff will flag it otherwise).

**Runner constraint:** `compute_skills_task.map()` with `unmapped(stores)` only works with
in-process task runners (`ThreadPoolTaskRunner`, `ConcurrentTaskRunner`). Stores hold
SQLAlchemy connections that are not serializable — distributed/subprocess runners would fail.
This is not a new constraint (the current sequential call also passes stores directly), but
`task.map()` makes it load-bearing. v0 uses a single work pool with in-process execution, so
this is safe. Document in a code comment at the `task.map()` call site.

**Dependencies:** 6A (task must exist before `task.map()` can reference it).

#### 6C. Test updates for `compute_skills.py` refactor

The existing test in `tests/unit/flows/test_compute_skills.py::TestNonDischargeGuard` asserts
`NotImplementedError` for non-discharge parameters. After Phase 6A removes the guard, this test
must be updated:

- **Update import:** change `from sapphire_flow.flows.compute_skills import compute_skills_flow`
  to also import `compute_skills_task` (the direct entry point for unit tests).
- **Delete** `test_non_discharge_raises_not_implemented` (it tests removed behavior).
- **Add** `test_water_level_parameter_computes_skill` — call `compute_skills_task` with
  `parameter="water_level"` and verify it succeeds (returns scores with
  `parameter == "water_level"`).
- **Add** `test_flow_wrapper_delegates_to_task` — call `compute_skills_flow` with
  `parameter="discharge"` and verify it returns the same result as a direct `compute_skills_task`
  call. This validates the thin wrapper pattern works correctly.

**Implementation note:** The existing guard test works because it fires before any store access.
The replacement tests must provide populated fakes (`FakeHindcastStore` with water_level
hindcasts, `FakeObservationStore` with matching observations, `FakeSkillStore`,
`FakeStationStore` with thresholds). Use the same fixture pattern as existing tests in that
file (e.g. `TestComputeSkillsFlow`).

#### 6D. Test for multi-parameter training loop

**`tests/unit/flows/test_train_models.py`** (new file — does not yet exist):

`TestMultiParameterSkillComputation` — `test_computes_skills_for_all_target_parameters`:
- Create a fake model whose `data_requirements.target_parameters = frozenset({"discharge", "water_level"})`.
  **Note:** `FakeMultiTargetStationForecastModel.data_requirements` is aliased from
  `FakeStationForecastModel.data_requirements`, which has `target_parameters=frozenset({"discharge"})`.
  Create an inline test model with the correct `frozenset({"discharge", "water_level"})` value,
  or assign a new `ModelDataRequirements` instance on the fake (the original is a frozen
  dataclass — it cannot be mutated in place).
- Run training flow for 2 stations.
- Assert `FakeSkillStore` contains scores for both parameters (inspect `_scores` for
  `parameter == "discharge"` and `parameter == "water_level"`).
- Assert scores exist for all 4 combinations (2 stations × 2 parameters).

**Dependencies:** 6A, 6B.

#### 6E. Standards and convention updates

The dual-interface pattern introduces several new patterns not yet covered by the standards.
All updates below land in the same commit as 6A–6D (or immediately after — must not be
deferred beyond the Phase 6 commit).

**1. `docs/standards/orchestration.md` — dual-interface composition pattern**

Document in §Flow composition as a fourth pattern. Also update the introductory count sentence
(~line 102, "Three composition patterns are used") to "Four":

**4. Dual-interface (task + flow wrapper)** — a `@task` contains the computation logic and is
used with `task.map()` for fan-out. A thin `@flow` wrapper calls the task and preserves
standalone deployment registration. Used when the same computation needs both concurrent
fan-out (inside a parent flow) and independent invocability (admin on-demand, Flow 10).

Update the composition graph to show both entry paths as siblings (not parent-child):

```
Flow 5 (onboard_station)
  └→ Flows 6/9 (train_models) [training pool]
       ├→ Flow 7 (run_hindcast) [hindcast pool]
       ├→ compute_skills_task.map() [in-process fan-out inside train_models]
       └→ compute_skills_flow [registered deployment, standalone/on-demand]
```

**2. `docs/standards/orchestration.md` — flow-to-Prefect mapping table**

Update the Flow 8/10 row (~line 23) to reflect the dual-interface:
- Change Prefect flow function from `compute_skills` to `compute_skills_flow` (registered
  deployment entry point) / `compute_skills_task` (fan-out entry point).
- Note that the embedded `task.map()` path runs in-process inside the parent flow's work pool,
  while standalone `compute_skills_flow` runs on its own pool assignment.

**2b. `docs/standards/orchestration.md` — training fan-out pseudocode**

Update the illustrative code block in §Training fan-out (~line 97) which currently calls
`compute_skills(station, model, artifact)` — rename to `compute_skills_task(...)` to match
the Phase 6A rename. (The block is marked "Illustrative only" but should still reflect the
actual function names to avoid confusion.)

**3. `docs/standards/orchestration.md` — inner task suppression at fan-out scale**

Add to §Task granularity: "When a `@task` is itself invoked via `task.map()` at high fan-out
(hundreds+ concurrent invocations), inner `@task` decorators on DB-boundary helpers may be
removed to avoid Prefect UI saturation. Retry responsibility moves to the outer task. This
is an exception to the 'use `@task` at system boundaries' rule — document the trade-off in
a code comment when stripping inner decorators."

**4. `docs/conventions.md` — Prefect naming convention for dual-interface**

Update §Prefect flows and tasks (~lines 122–126) to:
- Add the `_task`/`_flow` suffix exception: "When the dual-interface pattern is used (see
  `orchestration.md` §Flow composition, pattern 4), append `_task` and `_flow` suffixes to the
  `verb_noun` base name (e.g., `compute_skills_task`, `compute_skills_flow`). The Prefect
  deployment `name=` in the `@flow` decorator retains the unsuffixed kebab-case form
  (`compute-skills`). The base `verb_noun` form remains the default for all other flows and
  tasks."
- Update the existing `compute_skills` example on line 124 to `compute_skills_flow` /
  `compute_skills_task` to reflect the rename.

(Note: orchestration.md has no §Naming section — all Prefect naming conventions live in
conventions.md. The dual-interface composition pattern itself is documented in orchestration.md
§Flow composition as item 1 above.)

**5. `docs/standards/orchestration.md` — runner constraint for non-serializable `task.map()` args**

Add to §Fan-out and convergence: "`task.map()` with `unmapped()` store/connection arguments
requires an in-process task runner (`ThreadPoolTaskRunner` or `ConcurrentTaskRunner`). Stores
hold SQLAlchemy connections that are not pickle-serializable — distributed or subprocess runners
would fail. v0 uses a single work pool with in-process execution. Document this constraint in
a code comment at any `task.map()` call site that passes store objects."

**6. `docs/standards/logging.md` — generalize `log_prints=False`**

The current rule (~line 320) restricts `log_prints=False` to `@task` decorators in Flows 1
and 2. Generalize to: "Use `log_prints=False` on any `@task` or `@flow` used in high-fan-out
`task.map()` patterns, and on all tasks in Flows 1 and 2."

**7. `docs/standards/logging.md` — skill-computation context fields**

Add a "Recommended context fields" subsection below the mandatory fields table (~line 165).
Add `parameter` as the first entry, scoped to skill-computation tasks:
"`parameter` — bound via `bind_contextvars(parameter=...)` in `compute_skills_task`. Not
mandatory globally because most flows operate on a single implicit parameter."

**8. `docs/architecture-context.md` — Flows 8/10 section**

Update the Flows 8/10 section (~line 1011) to describe the dual-interface pattern:
- The Prefect flow is now `compute_skills_flow` (thin wrapper for standalone deployment).
- The computation body is `compute_skills_task` (used with `task.map()` for fan-out from
  `train_models`).
- Internal helpers (`_fetch_hindcasts`, `_fetch_observations`, `_store_skill_results`) are
  plain functions, not Prefect tasks.

**Dependencies:** None (doc-only changes, can be done in parallel with implementation).

---

## Dependency Graph

```
1A (SkillScore/SkillDiagram types)
  └─ 2A (service stamps parameter)
  └─ 3A (SkillStore Protocol)
       └─ 3B (PgSkillStore)
       └─ 3C (FakeSkillStore)

4A/4B (DB migration + metadata + uq_skill_diagrams_natural_key — independent of 1–3)

5A (fix broken tests — depends on 1A, 2A, 3B, 4A, 4B)
5B (new tests — depends on 2A, 3A, 3B, 3C; item 8 also depends on 4A)

6A (dual-interface refactor + remove guard — depends on 1A–5B; test suite must be green first)
  └─ 6B (task.map() fan-out in training loop — depends on 6A)
       └─ 6C, 6D (tests — depends on 6A, 6B)
6E (doc updates — independent, can parallel with 6A–6D)
```

Phases 1+2, 3, and 4 can proceed in parallel. Phase 5 depends on 1–4. Phase 6 depends on
all of 1–5 (except 6E which is doc-only).

**Commit boundaries:**
- **Minimum atomic unit for Phases 1–5:** Phase 1A adds `parameter` as a required field on
  frozen dataclasses (`kw_only=True`), so every existing construction site will raise
  `TypeError` until updated. The minimum green-test commit is **1A + 2A + 5A** (types +
  service threading + test fixes for existing construction sites). Phases 3A–3C and 4A–4B
  can land in the same commit or a subsequent one, but 5A's integration test fixes also
  depend on 3B and 4A/4B, and 5B item 8 (`TestDiagramIdempotency`) depends on 4A's new
  `uq_skill_diagrams_natural_key` — so in practice **1A + 2A + 3A–3C + 4A–4B + 5A + 5B**
  is the smallest commit that keeps the full test suite green.
- **Phase 6A+6B+6C+6D must be a single commit** — removing the guard (6A) without updating
  the training loop (6B) leaves the system in a state where non-discharge skill computation
  is silently enabled but never invoked. Combining them ensures the guard removal and the
  loop that exercises it land atomically.
- **Phase 6E** (orchestration.md update) can be in the same commit or a separate doc commit.

---

## File-Level Change Summary (updated)

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/skill.py` | Add `parameter: str` to `SkillScore` and `SkillDiagram` | 1A |
| `docs/spec/types-and-protocols.md` | Add `parameter: str` to `SkillScore`, `SkillDiagram`; add `eval_period_start`/`end` (drift fix); add `parameter` filter to `SkillStore` fetch methods and `mark_stale` | 1A, 3A |
| `src/sapphire_flow/services/skill/service.py` | Add `parameter: str` to `_compute_scores()` and `_compute_diagrams()` signatures; pass `parameter=` in construction sites and call sites | 2A |
| `src/sapphire_flow/protocols/stores.py` | Add `parameter` filter to `SkillStore` fetch methods and `mark_stale` | 3A |
| `src/sapphire_flow/store/skill_store.py` | Add `parameter` filter to fetch methods and `mark_stale`; add `parameter` to `_score_to_row()`, `_diagram_to_row()`, `_row_to_score()`, `_row_to_diagram()` | 3B |
| `tests/fakes/fake_stores.py` | Add `parameter` filter to `FakeSkillStore` fetch methods and `mark_stale` | 3C |
| `src/sapphire_flow/db/metadata.py` | Add `parameter` column to skill table definitions; add `parameter` to `uq_skill_scores_natural_key` index; **create** `uq_skill_diagrams_natural_key` index | 4B |
| `alembic/versions/0016_skill_parameter_column.py` | New migration: add `parameter TEXT NOT NULL`; drop + recreate `uq_skill_scores_natural_key` with `parameter`; **create** `uq_skill_diagrams_natural_key` | 4A |
| `tests/integration/store/test_skill_store.py` | Update `_make_score()` and `_make_diagram()` helpers; add `TestParameterFilter`; add `TestDiagramIdempotency` | 5A, 5B |
| `tests/fakes/test_fakes.py` | Add `FakeSkillStore` parameter filtering test | 5B |
| `tests/unit/services/skill/test_service.py` | Update assertions if needed; add `TestParameterStamping` | 5A, 5B |
| `src/sapphire_flow/flows/compute_skills.py` | Remove `NotImplementedError` guard; introduce dual-interface (`compute_skills_task` + thin `compute_skills_flow` wrapper); strip `@task` from internal helpers | 6A |
| `docs/architecture-context.md` | Add `parameter TEXT NOT NULL` + `eval_period_start`/`eval_period_end TIMESTAMPTZ NOT NULL` to `skill_scores` and `skill_diagrams` DB schema definitions (parameter is new; eval_period is drift fix); replace `is_stale BOOLEAN` with `freshness TEXT` on `skill_scores` + update 5 prose references (migration 0009 drift fix); document `uq_skill_scores_natural_key` and `uq_skill_diagrams_natural_key`; resolve forward-reference at ~line 2413; update Flows 8/10 section to reflect dual-interface pattern (Phase 6A) | 4B, 6E |
| `src/sapphire_flow/flows/train_models.py` | Replace sequential loop with `compute_skills_task.map()` fan-out over `(station, parameter)` pairs; use existing `model_instance` to access `target_parameters`; import `compute_skills_task` + `unmapped`; remove unused `compute_skills_flow` import | 6B |
| `tests/unit/flows/test_compute_skills.py` | Update import; replace guard test with water_level success test + wrapper delegation test | 6C |
| `tests/unit/flows/test_train_models.py` | **New file.** Add `TestMultiParameterSkillComputation` | 6D |
| `docs/standards/orchestration.md` | Document dual-interface pattern as 4th composition pattern; update flow-to-Prefect mapping table row for Flow 8/10; update composition graph; add inner-task suppression guidance; add runner constraint note for non-serializable `task.map()` args | 6E |
| `docs/standards/logging.md` | Generalize `log_prints=False` rule beyond Flows 1/2; add "Recommended context fields" subsection with `parameter` for skill computation | 6E |
| `docs/conventions.md` | Add `_task`/`_flow` suffix exception + naming convention for dual-interface to §Prefect flows and tasks; update `compute_skills` example to reflect rename | 6E |

---

## Guardrails

- Run `uv run pytest` before starting and after each commit-boundary phase group (not after
  individual sub-phases within Phase 6 — the suite is expected to be transiently broken
  between 6A and 6C)
- Migration: uses `server_default="discharge"` / drop-default pattern (matches migration 0008). Safe with existing rows. Developers wanting a clean slate: `alembic downgrade 0015 && alembic upgrade head`
- After Phase 3: verify `isinstance(FakeSkillStore(), SkillStore)` passes
- After Phase 4: run `alembic upgrade head` against test DB
- After Phase 6 (all sub-phases complete): run full `uv run pytest` — suite must be green
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)

---

## Open Items

1. **Safe NOT NULL via `server_default` pattern** — The migration uses `server_default="discharge"`
   then drops the default (matching migration 0008). This is safe even if a developer has existing
   skill rows — the default populates them before the NOT NULL constraint is enforced. This
   follows cicd.md's spirit of additive-only migrations while being technically NOT NULL. Note:
   cicd.md line 119 says "new columns nullable" for production backward-compatibility; this
   pre-release migration uses NOT NULL with a temporary default instead because there is no
   production data and all migrations will be squashed into a single `0001` before v0 ships.
   Developers wanting a clean slate: `alembic downgrade 0015 && alembic upgrade head` (targets
   only migration 0016 — does not destroy other tables).

2. **Dual-interface pattern for `compute_skills`** — Phase 6A introduces `compute_skills_task`
   (a `@task` for `task.map()` fan-out) alongside `compute_skills_flow` (a thin `@flow` wrapper
   for standalone deployment). This is necessary because Prefect 3 has no `flow.map()` — only
   `task.map()`. The wrapper preserves the `compute-skills` deployment registration required by
   the orchestration standard ("Subflow or on-demand") and Flow 10 (v1 broad recomputation).
   The internal helpers (`_fetch_hindcasts`, `_fetch_observations`, `_store_skill_results`)
   become plain functions — their `@task` decorators are stripped because per-helper task
   tracking at 2000+ concurrent invocations generates UI noise with no operational value.

3. **`parameter: str` vs enum, `NewType`, or FK constraint** — The codebase uses bare `str`
   for parameter names across all 34+ occurrences in `src/` (`Observation`, `ForecastEnsemble`,
   `FlowRegimeConfig`, `ModelDataRequirements`, etc.). An enum was considered but rejected:
   the canonical parameter set (10 names in `conventions.md`) is DB-driven — the `parameters`
   table is the authoritative registry, seeded per deployment. Nepal v1 will add different
   parameters. An enum would require code changes for every new deployment, contradicting the
   adapter-agnostic architecture. `StationThreshold` and `ExceedanceResult` use
   `Literal["discharge", "water_level"]` — see Open Item 5 for analysis of whether to
   generalize. A codebase-wide `ParameterName = NewType("ParameterName", str)` remains a
   legitimate improvement (catches argument-swap bugs at static analysis time without
   constraining the value set), but introducing it in only this one type would create
   inconsistency. If adopted, it should be a separate plan touching all parameter-carrying types.

   **FK constraint to `parameters.name`** was considered but deferred. A FK would enforce
   referential integrity at the DB level (no typos like `"dishcarge"`), but it couples skill
   score insertion to the `parameters` table's seeding order — test setup would require
   inserting parameter seed data before any skill score can be stored, adding friction to
   integration tests. The `parameters` table is seeded via Alembic migration, so production
   writes are safe (only seeded names exist). The trade-off is acceptable for v0; a FK can
   be added later without data migration if desired.

4. **`skill_diagrams` unique index — resolved by this plan.** Unlike `skill_scores` (which has
   `uq_skill_scores_natural_key`), `skill_diagrams` had no uniqueness constraint at all. This
   was a pre-existing gap: `PgSkillStore.store_skill_diagrams()` uses `on_conflict_do_nothing()`
   which was a no-op without a unique constraint, silently accumulating duplicate rows on every
   re-run. Phase 4A now creates `uq_skill_diagrams_natural_key` with `parameter` included from
   the start, and Phase 4B adds the corresponding index definition in `metadata.py`.

5. **`StationThreshold.parameter: Literal["discharge", "water_level"]` — generalize to `str`** —
   `StationThreshold` and `ExceedanceResult` are the only types that use `Literal` instead of
   `str` for the parameter field. The `Literal` was intended to encode that only river parameters
   get thresholds (`architecture-context.md`: "hydromet agencies handle meteorological warnings
   in their own systems"). However, v0 will experiment with water quality parameters
   (`water_temperature`, possibly `dissolved_oxygen`, `turbidity`) measured at river stations —
   these are river-domain parameters that would legitimately carry thresholds (e.g. "alert if
   water_temperature > 25°C"). This means the `Literal` is **not stable** and would need editing
   for every new water quality parameter.

   **Recommendation:** generalize `StationThreshold.parameter` and `ExceedanceResult.parameter`
   from `Literal["discharge", "water_level"]` to `str`, consistent with all other 34+
   parameter-carrying types and with `architecture-context.md`'s own prose schema. The
   architectural constraint (no *meteorological* thresholds — weather stations don't get
   threshold alerts) is a domain rule enforced by which stations get thresholds assigned, not
   by the type system. This change is outside plan 004's scope but should happen before water
   quality experimentation begins. Enablement path for new parameters:
   1. Add canonical names to `conventions.md` and seed `parameters` table (river domain).
   2. Widen `StationThreshold.parameter` and `ExceedanceResult.parameter` to `str`.
   3. No other type/store/service changes needed — everything else already uses `str`.

6. **Parameter onboarding — towards a data-driven parameter registry** — The current design
   treats parameters as a small, static set: 10 canonical names hardcoded in `conventions.md`,
   seeded into the `parameters` table via Alembic migration, classified by a `ParameterDomain`
   enum (`RIVER`, `WEATHER`). This works for v0's river discharge/water_level + weather forcing,
   but becomes a friction point as the system expands to new monitoring domains:
   - **Water quality** at river stations: `water_temperature`, `dissolved_oxygen`, `turbidity`
   - **Groundwater**: `groundwater_level` in boreholes (new station kind, not river or weather)
   - **Soil moisture**: point measurements at lysimeter or sensor sites
   - **Snow**: `snow_water_equivalent`, `snow_depth` at dedicated snow monitoring stations

   Each new parameter currently requires: (a) update `conventions.md`, (b) write an Alembic
   migration to seed `parameters`, (c) potentially extend the `ParameterDomain` enum. This is
   manageable for infrequent additions but does not scale to research experimentation where
   users want to quickly try forecasting a new variable.

   **Future direction (post-v0):** A parameter onboarding flow where new parameters are
   registered via config or API, with domain classification as a field on the parameter record
   rather than an enum gate. The `parameters` table already has the right shape (`name`, `unit`,
   `aggregation_method`, `parameter_domain`); the missing piece is a registration pathway that
   doesn't require code changes. This would also need to address: which parameters get
   thresholds (alerting), which get skill computation, and which station kinds can observe them.

   **v0 implication:** plan 004's choice of `parameter: str` (not enum) on `SkillScore` is
   forward-compatible with this direction — skill computation already works for any parameter
   name without code changes. The `Literal` on `StationThreshold` (Open Item 5) is the main
   type-system bottleneck that needs widening before new threshold-bearing parameters can be
   added.
