# Plan 050 — Prefect Run Naming Convention + Audit

**Status**: DONE
**Date**: 2026-04-17 (created), 2026-04-17 (completed)
**Phase**: Orchestration / Operability (cross-cutting)
**Depends on**: nothing blocking — touches decorators + one standards doc only.

## Context

Prefect assigns random slug names (`loyal-parakeet`) to every flow run and task
run. On the dashboard this makes it hard to tell which `forecast-cycle` run is
the 06Z vs 12Z cycle, or which of 1000 fanned-out station tasks failed. Prefect
lets us template `flow_run_name=` / `task_run_name=` per invocation.

Audit (2026-04-17) found:

- 9 `@flow` decorators, 18 `@task` decorators across 7 modules under
  `src/sapphire_flow/flows/`. **All 27 use `name=` only; none set run-name
  templates.**
- `docs/standards/orchestration.md` covers deployment names (kebab-case) but has
  **no run-naming guidance**.
- No tests pin `flow_run_name` / `task_run_name` / `with_options` — low
  breakage risk.

Decision: add a run-naming section to `orchestration.md` first (so the
convention is the source of truth), then apply templates across all decorator
sites to match.

## Scope

**In scope**

- New "Run naming" section in `docs/standards/orchestration.md` with rules,
  templating syntax (string vs callable), and a table of per-flow templates.
- `flow_run_name=` on all 9 `@flow` sites under `src/sapphire_flow/flows/`.
- `task_run_name=` on all 18 `@task` sites under `src/sapphire_flow/flows/`.
- Unit-test-level guard that every run-name template resolves against at least
  one real call-site parameter set (no runtime `KeyError` on dashboard).

**Not in scope**

- Changing deployment names (already kebab-case per orchestration.md).
- Changing the existing `name=` argument on any decorator (dashboard grouping
  must stay stable — any name change would break Prefect saved-filter URLs and
  existing dashboards).
- Touching decorators outside `src/sapphire_flow/flows/` (none found by the
  audit).
- Prefect version upgrades.
- Adding structured-log run-name fields to structlog (separate concern; log
  correlation is already handled by `flow_run_id`).

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Template shape**: `<flow-kebab-name>-<time-or-shard>-<secondary-shard>`. Lead with the flow name so dashboard text-filter still matches; then the time axis (ISO `%Y-%m-%dT%H`) for chronological sort; then any distinguishing shard key (`station_id`, `model_id`, `member_id`). Target ≤60 chars. | Scannable, sorts lexicographically = chronologically, survives dashboard truncation, keeps grouping-by-name intact. |
| D2 | **Callable run names for flows whose templating parameter is `Optional` or computed at flow entry** (e.g. `cycle_time=None` resolved from `clock()` inside `run_forecast_cycle_flow`). Use a closure that reads `get_run_context().flow_run.parameters` and falls back to `flow_run.expected_start_time` if the param is `None`. | String templates evaluate at submit time and would stringify `None` as `"None"`. Callables evaluate at run time and can look at resolved inputs. No behaviour change. |
| D3 | **String templates for tasks** — all task params are non-None by the time the task runs. Simpler and cheaper than callables. | Tasks receive fully-resolved args. No `Optional` surface. |
| D4 | **Keep `name=` unchanged everywhere.** Only add `flow_run_name=` / `task_run_name=` as new kwargs. | Zero impact on deployment registration, saved filters, or alerting rules keyed on flow name. |
| D5 | **Non-scalar params (stores, adapters, units, rngs) are never referenced in templates** — only scalar identifiers (`station_id`, `model_id`, `artifact_id`, `parameter`, `group_id`, `period_start`, `period_end`). | Complex objects have no stable `__format__`; rendering would produce addresses or fail. |
| D6 | **Template-resolution test**: add `tests/unit/flows/test_run_names.py` that, for every flow/task with a run-name template, feeds a synthetic parameter dict and confirms the template resolves to a non-empty ASCII-safe string without raising. | Run-name templates only fail at dashboard-render time in production. A unit test catches typos and missing params before deploy. |
| D7 | **Docs-first ordering.** Write the standard in Phase 1; all Phase 2 subagents then cite the standard's table for exact templates. | Prevents drift between doc and code — matches `docs/workflow.md` "single source of truth". |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Template references a param that doesn't exist → `KeyError` only at dashboard-render time. | D6 unit test + subagent must cross-check every template against the actual signature before saving. |
| `cycle_time=None` / `period_start=None` etc. rendered literally as `"None"` in production. | D2 callable pattern; test (D6) includes the `None` case. |
| Some param is a complex object whose `__repr__` leaks memory addresses or secrets. | D5: only scalars. Subagent must reject any template mentioning `store`, `adapter`, `unit`, `rng`, `clock`, `model`, `deployment_config`. |
| Renaming dashboards / breaking saved filters. | D4: `name=` is frozen for this plan. |
| Subagent edits break unrelated decorator kwargs (`log_prints=False`, `persist_result=False`). | Task scope restricts edits to "add new kwarg only". Verified by `git diff` inspection in Task Exit Gate. |
| Callable run names accidentally close over a mutable default and leak state across runs. | D2 callables are stateless — they only call `get_run_context()`. Reviewed in code review step. |
| Version bump noise (27 sites × patch bump per commit). | Group into a single commit per module (7 commits) rather than per decorator. |

---

## Tasks

### Task 1: Add "Run naming" section to `docs/standards/orchestration.md`

**Scope**: Add a new subsection (placed after the deployment-name paragraph
around line 199). Content:

1. Why templated run names matter on the dashboard.
2. The D1 template shape.
3. Syntax: string templates (`"ingest-obs-{window_end:%Y-%m-%dT%H}"`) vs callable
   templates (for `Optional` params); example of each.
4. Forbidden substitutions (D5 scalar-only list).
5. A **per-flow table** fixing the canonical template for every flow in
   `src/sapphire_flow/flows/` — Phase 2 subagents read their row from this table
   and apply it verbatim. Include the task templates in the same table or an
   adjacent one.
6. A one-line rule: "Unit test `tests/unit/flows/test_run_names.py` must cover
   every new template."

**Out of scope**: Any decorator changes. Changes to existing orchestration.md
sections (deployment names, concurrency, scheduling). New flows.

**Files**: `docs/standards/orchestration.md`.

**Verification**:
```bash
uv run python -c "import pathlib; t = pathlib.Path('docs/standards/orchestration.md').read_text(); assert 'Run naming' in t and 'flow_run_name' in t and 'task_run_name' in t"
```

### Task 2: Add the run-name resolution test

**Scope**: New file `tests/unit/flows/test_run_names.py`. For every decorator in
the codebase, the test:

1. Imports the decorated function.
2. Reads the `flow_run_name` / `task_run_name` attribute off the Prefect
   decorator (string or callable).
3. If string: resolves it against a synthetic parameter dict covering every
   referenced placeholder (both populated and, where relevant, `None`).
4. If callable: invokes it inside a `prefect.testing.utilities.prefect_test_harness`
   context, or monkeypatches `get_run_context` to return a minimal stub.
5. Asserts the rendered name is a non-empty string ≤60 chars, matches
   `^[a-z0-9A-Z:_.\-]+$`.

The test is parameterised over a list of `(import_path, template_params)` pairs
that lives in the same file for easy update.

**Out of scope**: Actually running any flow. Any production code changes. Any
fixture changes.

**Files**: `tests/unit/flows/test_run_names.py`.

**Verification**: The test file must import cleanly and, with Phase 2 complete,
must pass. In Phase 1 (before Phase 2 lands), the test should either **xfail**
with a clear marker keyed to Plan 050 Phase 2, or skip entirely — whichever the
subagent finds simpler. Command:
```bash
uv run pytest tests/unit/flows/test_run_names.py -q
```

### Task 3: Apply run-name templates to `flows/run_forecast_cycle.py`

**Scope**: Using the orchestration.md table:

- `run_forecast_cycle_flow` (line 258): callable `flow_run_name` that resolves
  `cycle_time` if set, else `flow_run.expected_start_time`, formatted
  `forecast-%Y-%m-%dT%H`.
- `_fetch_nwp_task` (line 83): `"fetch-nwp-{cycle_time:%Y-%m-%dT%H}"` (task
  receives resolved `cycle_time`).
- `_fetch_obs_timestamps_task` (line 234): `"fetch-obs-ts-{cycle_time:%Y-%m-%dT%H}"`
  if `cycle_time` is a task param; otherwise a short fixed string.

Keep all other decorator kwargs unchanged. No signature changes. No logic
changes.

**Out of scope**: Any non-decorator edit. Any change to the surrounding flow
logic. Any new imports beyond what's needed for the callable (e.g.
`get_run_context`).

**Files**: `src/sapphire_flow/flows/run_forecast_cycle.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/run_forecast_cycle.py
```

### Task 4: Apply templates to `flows/ingest_observations.py`

**Scope**: `ingest_observations_flow` (callable run-name using
`flow_run.expected_start_time`) and the three tasks — `_fetch_observations_task`,
`_store_raw_task`, `_run_qc_task`. Templates per orchestration.md; `_run_qc_task`
includes `{station_id}` and `{parameter}`.

**Out of scope**: Flow/task signature changes, QC logic, adapter behaviour.

**Files**: `src/sapphire_flow/flows/ingest_observations.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/ingest_observations.py
```

### Task 5: Apply templates to `flows/train_models.py`

**Scope**: `train_models_flow` (callable — period may be `None` at submit time)
plus the four tasks (`_determine_scope_task`, `_assemble_data_task`,
`_train_model_task`, `_store_artifact_task`). Tasks include the scope identifier
from `unit` (typically `unit.station_id` or `unit.group_id` — subagent inspects
`TrainingScope` / `TrainingUnit` in-repo to pick the correct attribute path; if
attribute access inside a string template isn't supported in this Prefect
version, use a callable).

**Out of scope**: Any change to training logic, model selection, or data
assembly. Any change to `TrainingUnit` shape.

**Files**: `src/sapphire_flow/flows/train_models.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_train_models.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/train_models.py
```

### Task 6: Apply templates to `flows/onboard_model.py`

**Scope**: `onboard_model_flow` + 9 tasks. Flow template:
`onboard-{model_id}-{period_start:%Y%m%d}` (callable, because `period_start` is
`Optional`). Task templates include `{model_id}` and, where applicable,
`unit.station_id` / `unit.group_id`.

**Out of scope**: Onboarding state machine changes, skill-gate logic.

**Files**: `src/sapphire_flow/flows/onboard_model.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_onboard_model_flow.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/onboard_model.py
```

### Task 7: Apply templates to `flows/run_hindcast.py`

**Scope**: `run_hindcast_flow` (callable) + 2 mapped tasks
(`_run_station_hindcast_task`, `_run_group_hindcast_task`). Flow template:
`hindcast-{model_id}-{period_start:%Y%m%d}-{period_end:%Y%m%d}`. Tasks include
`{station_id}` or `{group_id}` respectively.

**Out of scope**: Hindcast-store logic, map fan-out shape.

**Files**: `src/sapphire_flow/flows/run_hindcast.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_run_hindcast.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/run_hindcast.py
```

### Task 8: Apply templates to `flows/compute_skills.py`

**Scope**: `compute_skills_flow`, `compute_combined_skills_flow`, and the two
tasks. Templates include `{model_id}-{station_id}-{parameter}` for
`compute_skills_flow`; `{station_id}-{parameter}-{strategy}` for
`compute_combined_skills_flow`.

**Out of scope**: Skill-metric logic, combined-skill strategy selection.

**Files**: `src/sapphire_flow/flows/compute_skills.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_compute_skills.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/compute_skills.py
```

### Task 9: Apply templates to `flows/backup.py` and `flows/onboard.py`

**Scope**: `backup_database_flow` (callable — uses
`flow_run.scheduled_start_time`), `_dump_database_task`,
`_cleanup_old_backups_task`; `onboard_stations_flow` (callable),
`_download_task`. Simpler module pair; grouped because each is small.

**Out of scope**: Backup retention, onboarding data shape.

**Files**: `src/sapphire_flow/flows/backup.py`,
`src/sapphire_flow/flows/onboard.py`.

**Verification**:
```bash
uv run pytest tests/unit/flows/test_backup.py tests/unit/flows/test_onboard_flow.py tests/unit/flows/test_run_names.py -x -q
uv run pyright --strict src/sapphire_flow/flows/backup.py src/sapphire_flow/flows/onboard.py
```

### Task 10: Enable full test coverage

**Scope**: If Task 2's `test_run_names.py` was scaffolded with `xfail` / `skip`
pending Phase 2, flip it to active now that all templates exist. Confirm full
suite green.

**Out of scope**: Adding coverage for anything beyond run names.

**Files**: `tests/unit/flows/test_run_names.py`.

**Verification**:
```bash
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright --strict src/
```

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1-docs-and-test-scaffold",
      "tasks": ["1", "2"],
      "parallel": true
    },
    {
      "id": "phase-2-apply-templates",
      "tasks": ["3", "4", "5", "6", "7", "8", "9"],
      "parallel": true,
      "depends_on": ["phase-1-docs-and-test-scaffold"]
    },
    {
      "id": "phase-3-enable-coverage",
      "tasks": ["10"],
      "parallel": false,
      "depends_on": ["phase-2-apply-templates"]
    }
  ]
}
```

## Full verification

```bash
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright --strict src/
```

Then manually eyeball the Prefect UI on the Mac-mini staging deployment (Plan
046) after the next scheduled run to confirm names render as expected. This is
a human check, not a CI gate.

## Commit strategy

One commit per task (conventional commits; patch bump per CLAUDE.md):

- `docs(orchestration): add run-naming standard`
- `test(flows): add run-name template resolution test`
- `feat(flows): template run names in run_forecast_cycle`
- …one per module…
- `test(flows): enable run-name coverage`

10 commits total, 10 tags. Each commit self-contained and revertable — if a
module's templates turn out to break dashboard rendering in a way the unit test
missed, revert the module's commit alone.
