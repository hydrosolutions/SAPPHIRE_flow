# Plan 073 — Fix concrete pyright violations outside `flows/`

**Status**: READY
**Date**: 2026-04-22 (DRAFT) → 2026-05-11 (READY, post six review rounds)
**Depends on**: none (independent of Plan 069 but intentionally lands alongside — Plan 069 drains the ~445 "Unknown-cluster" errors that propagate through services under the ratchet, while this plan fixes the concrete violations pyright flagged which are not masked by the `flows/` carve-out).
**Scope**: Fix the 65+ concrete type-violations pyright reports outside
`src/sapphire_flow/flows/` after the flows/ carve-out experiment landed
in `pyrightconfig.json`. These are errors the Unknown-cluster silencing
does NOT hide — `reportArgumentType`, `reportAttributeAccessIssue`,
`reportOperatorIssue`, `reportMatchNotExhaustive`,
`reportPrivateUsage`, `reportGeneralTypeIssues`, `reportUnusedImport`,
`reportUnusedFunction`, `reportUnnecessaryIsInstance`. Each one is a
potential runtime bug, a domain-type gap, or a latent design question.
No intended runtime behaviour change except where a fix surfaces a
real bug whose behaviour must change to be correct.

---

## Cross-plan coordination

This plan is one of three DRAFTs addressing pyright / type-checking
hygiene after Plan 064. The three are:

- **Plan 070** (pre-commit hooks + gate parity) — prevents new
  lint/format/secret regressions during the drain.
- **Plan 073** (this plan — concrete type-violations cleanup outside
  flows/) — fixes 65+ real-bug-rule violations before the ratchet
  captures a baseline.
- **Plan 069** (pyright backlog ratchet + drain) — freezes the
  post-073 baseline and drains remaining errors under the ratchet.

**Merge order (mandatory):** 070 → 073 → 069 Phase 1 → 069 Phase 2+.
Plan 073 must land before Plan 069 Phase 2 begins. After Plan 073
lands, Plan 069 Phase 2 (flows/ concrete violations) excludes the
files Plan 073 touches:
`services/model_onboarding.py`, `services/forecast_qc.py`,
`services/hindcast.py`, `services/alert_checker.py`,
`store/observation_store.py`, `store/forecast_store.py`,
`tools/record_fixtures.py`, `tools/observation_coverage_summary.py`,
`api/routes/tables.py`, `api/routes/dashboard.py`,
`api/routes/forecasts.py`, `api/routes/models.py`,
`api/routes/stations.py`, `api/routes/api_stations.py`,
`services/baselines.py`, `services/qc.py`,
`services/training_data.py`, `api/__init__.py`,
`config/forecast_qc_rules.py`, `config/qc_rules.py`,
`adapters/meteoswiss_nwp.py`, `types/domain.py`.

**Baseline numbers:**
- 1078 = pre-experiment (no carve-out). Historical reference only.
- 676 = live baseline at 2026-05-11, pre-Plan-073 (flows/ carve-out active; includes `meteoswiss_nwp.py:180`).
- ≤609 = post-Plan-073 target (Plan 069's ratchet floor).

**Config location:** `pyrightconfig.json` at repo root is
authoritative. `[tool.pyright]` in `pyproject.toml` is NOT used.

---

## Context

### Why now

Plan 064 left pyright disabled in CI. Plans 069 (ratchet) and 070
(pre-commit parity) were DRAFTed to bring pyright back online. Before
running Plan 069, we ran a configuration experiment: silence the
Unknown-cluster rules inside `src/sapphire_flow/flows/` (where
Prefect `@flow`/`@task` decorator type erasure produces ~400 errors
that do not indicate real bugs) while keeping every real-bug rule
active everywhere else. The experiment landed in `pyrightconfig.json`.
Results:

- Total errors: **1078 → 675** (−403, −37%).
- `flows/`: 569 → 166 (the 166 remaining inside `flows/` are
  concrete violations the carve-out intentionally preserves).
- Non-flows: 509 → 509 (zero spillover).
- Real-bug rules still fire at full count globally: 124
  `reportArgumentType`, 76 `reportAttributeAccessIssue`, 6
  `reportCallIssue`, 7 `reportOperatorIssue`, 1 `reportReturnType`,
  etc.

Of the 509 non-flows errors, **64 are concrete violations** under the
rules listed in §Scope above. Investigation of each site confirms
that several are latent runtime bugs (None-check gaps, union dispatch
without narrowing, polars wide-type leakage), several are domain-type
round-trip issues (NewType wrapper bypass, invariant-dict variance),
and the rest are cleanup / design-call items. This plan fixes them
before Plan 069 starts draining the Unknown cluster, so Plan 069's
ratchet is starting from a clean concrete-violation slate.

### Principle

**Fix the bugs pyright is pointing at, even when the code "works" in
practice.** If a union-narrowing gap means the type system cannot
guarantee a call is safe, we encode the invariant (via `isinstance`,
`assert`, or a type refactor) rather than silencing the warning.
Where a site is a legitimate false positive (e.g. polars' `.min()`
returning a PythonLiteral union when the column is demonstrably
float), we fix with a typed boundary cast, not an ignore.

One ignore-as-first-line-tool deviation per Plan 069 D6: **no
`# pyright: ignore` without a dated, rule-scoped comment.**

### Tier summary (from investigation 2026-04-22)

**Tier 1 — latent crashes** (~13 enumerated sites; the pyright diagnostic count is higher because some sites produce multiple errors per attribute access — e.g. T4 sites each emit ~10 reportAttributeAccessIssue errors). The 'tier' bucketing is by call-site / fix-pattern, not by raw diagnostic count. These are None-check gaps and union dispatch without narrowing that can AttributeError / TypeError at runtime under realistic inputs.

| Site | Nature |
|---|---|
| `services/alert_checker.py:308` | `max(current, result.exceedance_probability)` where `exceedance_probability: float \| None`. Tightening `ExceedanceResult.__post_init__` makes this sound; see T1. |
| `services/model_onboarding.py:707, 709, 1021` | `unit.group_id: StationGroupId \| None` passed to callers requiring non-None. `TrainingUnit.__post_init__` (at `types/training.py:24`) already enforces the exactly-one-of invariant; the gap is pyright narrowing only — see T2. |
| `services/model_onboarding.py:758, 760` | `ForecastModel` (union) dispatched on `artifact_scope` enum without `isinstance` narrowing — trainer will break on mismatched branch. |
| `services/forecast_qc.py:89, 139` | `.timetuple()` on polars `PythonLiteral` union from a **datetime** column (valid_time). Fix: `assert isinstance(first_vt, datetime)`. |
| `services/forecast_qc.py:31, 70, 115, 117, 151` | Operator errors (`<`, `/`, `-`) on polars `PythonLiteral` union from **numeric** columns. Fix: `float(...)` inside the None-check. |

**Tier 2 — domain-type safety gaps** (40 errors): NewType wrappers
bypassed at smoke-test/synthetic sites, invariant-dict variance at
validator boundaries, Protocol/implementation divergence.

| Site | Nature |
|---|---|
| `services/model_onboarding.py:219, 245, 316` | `StationId(f"synthetic_{i}")`, `StationGroupId("synthetic_group")`, `StationId("smoke_test_station")` — NewType over UUID constructed from str literal. `ModelId = NewType("ModelId", str)` is string-based by design and is NOT affected; see T6 clarification. |
| `services/model_onboarding.py:302, 330` | `dict[str, ForecastEnsemble]` passed to `_validate_ensemble_dict(ensembles: dict[str, object], ...)`. Invariant-dict variance. Fix: signature → `Mapping[str, object]`. |
| `services/model_onboarding.py:356, 359` | `hasattr(ensemble, "parameter")` does not narrow `object` to "something with .parameter"; pyright lacks native `TypeGuard` inference. Introduce a Protocol. |
| `services/hindcast.py:215, 217, 589` | `.basin_id` on `object`; `dict[StationId, object]` → `dict[StationId, ModelInputs]`. Same variance + narrowing pattern. |
| `services/observation_store.py:86` | Mixed-domain union appended to `list[ObservationId]`. |
| `tools/observation_coverage_summary.py:189` | `PgObservationStore` does not satisfy `ObservationStore` protocol. Divergence unknown until investigated; see T11. |
| `tools/record_fixtures.py:319, 323` | `GriddedForecast \| dict[...]` union, not narrowed before member access / `archive()` call. |
| `store/forecast_store.py:99` | `Sequence[RowMapping]` → `list[Unknown]` — annotation gap in private helper `_rows_to_domain`. |

**Tier 3 — cleanup + design calls** (13 errors):

| Site | Nature |
|---|---|
| `services/model_onboarding.py:74, 121` | `isinstance(x, StationForecastModel \| GroupForecastModel)` on a `ForecastModel = StationForecastModel \| GroupForecastModel`. Always true. |
| `api/routes/{dashboard,forecasts,models,stations}.py` | All 4 import `_get_reflected` (leading underscore) from the API package. Design call: promote or accept. |
| `api/__init__.py:22` | `str` passed where a type/class is expected. Likely a Rich config miswire. |
| `api/routes/api_stations.py:124` | "type is not iterable" — a class iterated as if it were a sequence. |
| `services/forecast_qc.py:242`, `services/qc.py:238` | `match` not exhaustive on `str`. Design call: Literal or `case _:`; see T13.5. |
| `services/baselines.py:16` | Dead fn `_doy_distance`. |
| `api/routes/tables.py:30` | Unused `geoalchemy2` import. |
| `services/training_data.py:71` | Decimal vs float operator (polars wide-type family, but isolated). |

### Non-goals

- **Not a refactor of `forecast_qc.py` or `model_onboarding.py`.**
  Scope is type-safety fixes at the sites pyright flags. If a real
  bug is found whose fix requires broader surgery (e.g. tightening
  `OnboardingUnit` to encode "exactly one of station_id / group_id
  is set"), it becomes a separate plan with its own spec review.
- **Not Plan 069.** Plan 069 handles the 445 non-flows Unknown-cluster
  errors under a ratchet. This plan fixes the 65+ concrete errors. The
  two plans share no file-level ordering constraint but serialize on
  the same `pyrightconfig.json` if we decide to also trim the
  Unknown-cluster carve-out in services/ later.
- **Not a broader polars-typing crusade.** Sites flagged in Tier 1.4
  and Tier 2 that stem from polars `.min()`/`.std()`/`.median()`
  getting the wide `PythonLiteral` union are fixed in-place with
  explicit `float()` casts (numeric) or `assert isinstance(x, datetime)`
  (datetime). We do not introduce a new "typed polars wrapper" module.
- **Not a pyright version bump.** Locked at `1.1.408`; severity of
  these rules is stable at this version.

### Inputs

- `pyrightconfig.json` — current config (strict + flows/ carve-out +
  pythonVersion 3.12), landed by the 2026-04-22 experiment.
- These were the 2026-04-22 baseline snapshots (`/tmp/pyright_final.json`, `/tmp/pyright_rewrite.json`); treat as historical context only. Implementers should generate a fresh `uv run pyright --outputjson src/` capture at execution time, not read from /tmp.
- `src/sapphire_flow/services/{forecast_qc,model_onboarding,alert_checker,
  hindcast,observation_store,qc,baselines,training_data}.py`
- `src/sapphire_flow/api/{__init__,routes/*}.py`
- `src/sapphire_flow/tools/{record_fixtures,observation_coverage_summary}.py`
- `src/sapphire_flow/store/{forecast_store,observation_store}.py`
- `src/sapphire_flow/types/domain.py` — `ExceedanceResult.exceedance_probability: float | None` invariant lives here.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Fix Tier 1 union narrowing at the call site, not by widening callee signatures.** When `ForecastModel` (union) is dispatched on `artifact_scope`, wrap the dispatch in `isinstance` assertions that pyright can read. Do NOT weaken `train_station_model`'s signature from `StationForecastModel` to `ForecastModel`. | The concrete trainer signature is the point — it is the invariant "station models train on station data." Weakening it to the union defeats the type system's purpose. |
| D2 | **`TrainingUnit.__post_init__` already enforces exactly-one-of at construction time. The T2 fix is narrowing-only.** `TrainingUnit.__post_init__` at `types/training.py:24` raises `ValueError` if `(station_id is None) == (group_id is None)`. Every `TrainingUnit` that reaches model-onboarding code has already passed this check. The pyright gap is that pyright cannot propagate the invariant from `__post_init__` into downstream branches — it sees `group_id: StationGroupId | None` and cannot prove the None case is excluded after the `station_id is None` branch. The fix (T2) is `assert unit.group_id is not None, "..."` — a pyright hint that exploits what the invariant guarantees, not a new invariant. Tagged-union refactor is out of scope and unnecessary; the invariant already exists. | Asserting what `__post_init__` already guarantees is the minimal correct fix. If the invariant were ever wrong, the `ValueError` on construction would catch it before the assertion. |
| D3 | **Introduce a `_HasParameter` Protocol module-locally in `services/model_onboarding.py` for `_validate_ensemble_dict`.** `hasattr(x, "parameter")` does not narrow; pyright wants either `TypeGuard` or a Protocol. Protocol is structural and cheap. Placement is **module-local** (underscore-prefixed, defined at module level inside `model_onboarding.py` itself), NOT in `types/domain.py` — the project separates domain value types (frozen dataclasses in `types/`) from structural interfaces (Protocols in `protocols/`), and a service-private Protocol used only by `_validate_ensemble_dict` belongs co-located with its only caller. | Minimal surface: one Protocol, one validator, no other callers. Module-local placement preserves the `types/` vs `protocols/` separation and avoids polluting `types/domain.py` with non-dataclass content. Alternative of `cast(ForecastEnsemble, ensemble)` after `hasattr` is uglier and loses the "duck-typed" meaning. |
| D4 | **Polars wide-type leakage → fix depends on column dtype.** Two patterns: (a) **Numeric columns** (`.value`, `.std()`, `.iqr` results, `.median()` results): wrap with `float(...)` INSIDE the existing `if ... is not None` block — `float(None)` raises `TypeError`, so the cast must follow the None-check. Document with a one-line comment naming the polars API shape. (b) **Datetime columns** (`.valid_time`, used in `forecast_qc.py:89, 139`): narrow with `assert isinstance(x, datetime)` INSIDE the None-check — do NOT use `float(...)` on a datetime. The `float(...)` cast inside business logic technically violates parse-don't-validate (the typed-polars-wrapper alternative would be the principled fix), but a typed-polars-wrapper module is scope-excluded for this plan; escalate if the pattern expands. | The two column types require different fixes. A uniform `float()` wrapper is wrong for datetime columns and would raise at runtime. |
| D5 | **Tier 2.1 smoke-test NewType sites → construct from `uuid.uuid4()`.** Applies only to `StationId` and `StationGroupId` (UUID-based NewTypes). `ModelId = NewType("ModelId", str)` is string-based by design (see `POOLED_MODEL_ID = ModelId("_pooled")`, `BMA_MODEL_ID = ModelId("_bma")`, `CONSENSUS_MODEL_ID = ModelId("_consensus")` in `types/ids.py`); no change needed for `ModelId` constructors. | Soundness: the NewType promise holds for UUID-based IDs. `ModelId` string literals are intentional by design and must not be replaced. |
| D6 | **Tier 2 variance fixes: `dict[K, T]` parameters → `Mapping[K, T]` where T is treated read-only.** Follows PEP 484 variance rules. | One-line signature change; no runtime impact; unlocks call-site type safety without caller changes. |
| D7 | **Tier 3.2: `_get_reflected` → public `get_reflected`** if it is actually a public utility. Four route modules import it; that is API-shape, not internal. Alternatively, scope the usage to one internal adapter. | A private name imported by N > 1 external modules is a design lie. Pick one answer: rename (public) or relocate (truly internal). Reviewing the function will make that call; not pre-judged here. |
| D8 | **Phase 3 (Tier 3) tasks land as one consolidated cleanup commit, not file-by-file.** | Too small to be worth separate reviews. |
| D9 | **Update the ratchet baseline after each phase.** If Plan 069's baseline file exists by the time we start, update it. If not, record the post-plan pyright count in the implementation commit message body (e.g. 'Live pyright count post-Plan-073: <N>'). Plan 069 Phase 1's T2 will read the latest such message when establishing the ratchet baseline. | Keeps the two plans coherent when they land in either order. |
| D10 | **Protocols dispatched via `isinstance` must carry `@runtime_checkable`.** Before T3's isinstance narrowing lands, verify `StationForecastModel` and `GroupForecastModel` carry the `@runtime_checkable` decorator. Confirmed: both are decorated in `protocols/forecast_model.py` (lines 22 and 48). If any future Protocol is added to the dispatch, the precondition check must be repeated — a missing `@runtime_checkable` produces a `TypeError` at runtime, not a type error. | `isinstance` on a non-`@runtime_checkable` Protocol raises `TypeError`. The decorator must be present before T3 is implemented. |

---

## Task list

### Phase 1 — Tier 1 (latent crashes)

#### T1 — `alert_checker.py:308` — None-safe max

**File**: `src/sapphire_flow/services/alert_checker.py`

1. Read the loop at lines 295–309. Both callsites in
   `alert_strategy.py:182` (ExceedanceResult constructor start) / line 187
   (the `exceedance_probability=` keyword argument) AND
   `alert_strategy.py:232` (constructor start) / line 237 (kwarg) always
   pass a computed `float` (returned by `_compute_exceedance`) — there is
   no path today where `exceeded=True` and `exceedance_probability=None`
   simultaneously. **Fallback decision rule**: if grep finds any callsite
   passing `exceeded=True, exceedance_probability=None`, **stop and
   escalate to orchestrator** before landing T1. Do not attempt to fix a
   surprise callsite as part of T1.
2. Tighten `ExceedanceResult` (in `types/domain.py`): when
   `exceeded=True`, `exceedance_probability` must be non-None. Enforce
   via `__post_init__`. This is a **behaviour change** — construction of
   an `ExceedanceResult(exceeded=True, exceedance_probability=None, ...)`
   will now raise `ValueError` rather than silently succeeding. No
   runtime regressions are expected because both callsites already pass
   a float, but verify by grepping all `ExceedanceResult(...)` calls
   before landing.
3. After the `__post_init__` tightening, line 308 is type-safe: pyright
   can see `exceedance_probability` is `float` when `exceeded=True`.

**Test location**: `tests/unit/types/test_domain.py` (verify the file
exists; if not, create alongside other `test_*.py` files in
`tests/unit/types/`).
```python
class TestExceedanceResult:
    def test_post_init_rejects_exceeded_true_with_none_probability(self) -> None:
        with pytest.raises(ValueError, match="exceedance_probability"):
            ExceedanceResult(exceeded=True, exceedance_probability=None, ...)
            # fill in required fields by reading the dataclass definition
```

**Adjacent residual gap**: `alert_checker.py:319`
(`trigger_probability=level_max_prob.get(level)` returning `float | None`
for `Alert.trigger_probability`) is a separate narrowing gap. Out of scope
for T1 but flag for Plan 069 Phase 3 (or a tightening of `Alert` dataclass
invariants in a follow-on plan).

**Exit**: zero pyright errors at this site; the `ExceedanceResult.__post_init__`
test covering the invalid-state rejection passes; existing callsites verified
not to pass `None` when `exceeded=True`.

#### T2 — `model_onboarding.py:707, 709, 1021` — `group_id` None-narrowing

**File**: `src/sapphire_flow/services/model_onboarding.py`

1. **Confirm** (do not add) that `TrainingUnit.__post_init__` at
   `types/training.py:24` enforces exactly-one-of: the check
   `if (self.station_id is None) == (self.group_id is None): raise ValueError(...)`
   is already present. This is NOT a new invariant to add — it exists.
2. At line 707 (inside `else:` of line 694's
   `if unit.station_id is not None`), add:
   `assert unit.group_id is not None, "TrainingUnit invariant: group_id must be set when station_id is None (enforced by __post_init__)"`.
   This is a **pyright narrowing hint**, not a new invariant — it tells
   pyright what `__post_init__` already guarantees.
3. Apply the same narrowing assert in the else-branch of any other
   `if unit.station_id is not None:` check in this file; grep for that
   pattern and add the same `assert unit.group_id is not None` narrowing
   assert at each site (including the site at approximately line 1021).
4. **Also handle line 709 (a related but distinct error)**: line 709
   passes `group` (the return value of `fetch_group(unit.group_id)`,
   typed `StationGroup | None`) to `assemble_group_training_data` which
   requires non-None `StationGroup`. The step 2 assert on
   `unit.group_id` narrows the argument to `fetch_group` but does NOT
   narrow the *return*. Per `protocols/stores.py` `fetch_group(...)
   -> StationGroup | None`, None IS a legitimate return (missing
   group). Missing-group-at-training-time is a data-integrity fault,
   not a transient error — handle with explicit raise (NOT assert).
   Immediately after the `group = fetch_group(...)` call (one line
   before 709), add:

   ```python
   if group is None:
       raise StoreError(
           f"fetch_group({unit.group_id}) returned None — "
           "group was deleted or never persisted; training unit "
           "references a stale group_id"
       )
   ```

   Import `StoreError` from `src/sapphire_flow/exceptions.py` if not
   already imported in `model_onboarding.py` (verify imports at edit
   time). This matches the project exception convention in
   `docs/conventions.md` §Exception table.
5. Add a one-line inline `#` comment above each assertion explaining what
   `__post_init__` guarantees (e.g.
   `# TrainingUnit.__post_init__ guarantees exactly-one-of station_id / group_id`).

**Do NOT** propose a tagged-union refactor — the invariant already
exists and a refactor is out of scope.

**Exit**: zero pyright errors at these sites; the assertion's failure
message is informative; existing model-onboarding tests pass.

#### T3 — `model_onboarding.py:758, 760` — ForecastModel union dispatch

**File**: `src/sapphire_flow/services/model_onboarding.py`

**Note**: `smoke_test_model` at `model_onboarding.py:304` already uses
the prescribed `isinstance(model, StationForecastModel)` dispatch pattern
correctly. Do NOT modify `smoke_test_model`. T3's scope is the onboarding
training loop's dispatch at lines 757–760 AND the extension to line 710
in the same else-branch (the `model=` argument — see scope-addition #3
below).

0. **Precondition**: Confirm `StationForecastModel` and
   `GroupForecastModel` in `protocols/forecast_model.py` carry
   `@runtime_checkable`. Confirmed present at lines 22 and 48 — safe to
   proceed with `isinstance` narrowing. If a new Protocol is added to
   the `ForecastModel` union in future, recheck this precondition.
1. At the dispatch block (lines 753–760), replace the
   `if model.artifact_scope == ArtifactScope.STATION` check with
   `isinstance(model, StationForecastModel)` and the `else` branch
   with `isinstance(model, GroupForecastModel)` (or a `match`
   statement). Pyright will narrow the subsequent `train_*` calls.
2. Check the `artifact_scope` enum is still consistent with the
   concrete subclass — if there's ever a station model with
   `artifact_scope=GROUP` by configuration error, the isinstance
   dispatch will crash loudly, which is correct.
3. Similarly for the `training_data` argument: if
   `StationTrainingData | GroupTrainingData` can be distinguished by
   isinstance at this site, narrow it.
4. **Extension (scope-addition #3)**: also narrow the `model=` argument at
   line 710 (in the same else-branch as step 2's dispatch). Add an
   `isinstance(model, GroupForecastModel)` narrowing assert immediately
   before line 710's invocation. This confirms the precondition that the
   model is a `GroupForecastModel` before it is passed as such.

**Exit**: zero pyright errors at these sites; existing model-onboarding
tests still pass.

#### T4 — `forecast_qc.py:89, 139` — polars `.min()` on datetime column → isinstance

**File**: `src/sapphire_flow/services/forecast_qc.py`

These two sites access `.timetuple()` on `first_vt`, which comes from
`ensemble.values["valid_time"].min()`. The `valid_time` column is a
**datetime column**, not a numeric column. The polars `.min()` on a
datetime column returns a Python `datetime` object (or `None`), but
pyright sees the wide `PythonLiteral` union.

1. At lines 86–89 and 136–139, inside the existing
   `if first_vt is None: return None` guard, add immediately after:
   `assert isinstance(first_vt, datetime)`.
   This narrows `first_vt` from `PythonLiteral` to `datetime` before
   the `.timetuple()` call.
2. `datetime` is currently NOT imported as a runtime import in
   `forecast_qc.py` — it appears only under `TYPE_CHECKING`. Adding the
   `assert isinstance(first_vt, datetime)` check requires moving
   `datetime` to a runtime import. Add `from datetime import datetime`
   (not under `TYPE_CHECKING`). If ruff flags the new runtime import with
   TCH002/TCH003 (suggesting it belongs under `TYPE_CHECKING`), suppress
   with `# noqa: TCH003` because the import is used for runtime
   `isinstance` narrowing, not just typing.
3. Do **NOT** use `float(first_vt)` — these are datetime values, not
   numeric. `float(datetime(...))` raises `TypeError` at runtime.

**Exit**: zero `reportAttributeAccessIssue` errors at lines 89 and 139;
QC tests pass.

#### T5 — `forecast_qc.py:31, 70, 115, 117, 151` + `training_data.py:71` — polars numeric aggregation → float

**File**: `src/sapphire_flow/services/forecast_qc.py` and
`src/sapphire_flow/services/training_data.py`

These sites perform arithmetic operators (`<`, `/`, `-`, comparisons)
on results from polars `.min()`, `.std()`, `.median()`, `.mean()` on
**numeric columns**. Pyright sees the wide `PythonLiteral` union.

1. Wrap each aggregation result in `float(...)` INSIDE the existing
   `if ... is not None` block — the cast must come after the None-check
   because `float(None)` raises `TypeError`. Pattern:
   ```python
   # Before:
   std = ensemble.values["value"].std()
   if std is not None and std < tolerance:
   # After:
   std = ensemble.values["value"].std()
   if std is not None and float(std) < tolerance:
   ```
   Or assign to a typed variable inside the None-check block:
   ```python
   if std is not None:
       std_f = float(std)  # polars .std() returns PythonLiteral; column is numeric
       if std_f < tolerance:
   ```
2. Add a one-line comment at each site naming the polars API shape
   (e.g. `# polars .std() returns PythonLiteral; column is float64`).
3. Apply the same pattern to `training_data.py:71` in the same commit.
4. Cross-reference D4 for the datetime-vs-numeric distinction.

**Exit**: zero `reportOperatorIssue` errors at these six sites; QC and
training-data tests pass.

### Phase 2 — Tier 2 (type-safety gaps)

#### T6 — NewType soundness at smoke-test / synthetic sites

**File**: `src/sapphire_flow/services/model_onboarding.py:219, 245, 316`

The three flagged lines construct NewType values from string literals:
- Line 219: `StationId(f"synthetic_{i}")` — `StationId = NewType("StationId", UUID)`, unsound.
- Line 245: `StationGroupId("synthetic_group")` — `StationGroupId = NewType("StationGroupId", UUID)`, unsound.
- Line 316: `StationId("smoke_test_station")` — same `StationId`, unsound.

**`ModelId` is out of scope.** `ModelId = NewType("ModelId", str)` is
intentionally string-based (see `POOLED_MODEL_ID = ModelId("_pooled")`,
`BMA_MODEL_ID = ModelId("_bma")`, `CONSENSUS_MODEL_ID = ModelId("_consensus")`
in `types/ids.py`). Any `ModelId("some_string")` call is sound by design.
If line 245 or any nearby line constructs a `ModelId`, leave it untouched.

1. Replace `StationId(f"synthetic_{i}")` → `StationId(uuid4())` (import
   `from uuid import uuid4` if not present).
2. Replace `StationGroupId("synthetic_group")` → `StationGroupId(uuid4())`.
3. Replace `StationId("smoke_test_station")` → `StationId(uuid4())`.
4. **DataFrame column impact**: the UUID swap must NOT silently break the
   `"station_id"` column values in DataFrame rows at lines 228 and 238
   (and any similar synthetic-data construction). Decouple: use `uuid4()`
   for the NewType wrapping but keep the `synthetic_*` string as a
   separate variable for display/column purposes. Pattern:
   ```python
   sid = StationId(uuid4())
   sid_label = f"synthetic_{i}"
   # ... later in the DataFrame row:
   "station_id": sid_label,  # keeps human-readable column value
   ```
   **Also replace the existing `str(sid)` calls at lines 228 and 238
   (which currently produce `"synthetic_0"` etc.) with `sid_label` so
   the column-value text stays human-readable** — `str(StationId(uuid4()))`
   would silently bleed UUID strings into the synthetic dataset's
   station_id column and break any downstream pattern-matching on the
   `synthetic_` prefix. Verify with `grep -n "str(sid)"` after editing;
   any remaining `str(sid)` in this function is a missed site.

   If any string identifier is used for logging/display, keep it as a
   dedicated `label` variable — do not lose the debugging value.

**Out-of-scope note**: `tests/unit/models/test_linear_regression_daily.py:23`
(`_STATION_ID = StationId("smoke_test_station")`) and
`tests/unit/types/test_model.py:21-24` (`StationId("station-a")` etc.)
construct `StationId` values from string literals independently of
`model_onboarding.py`. These are test-only constructions that currently
do not trigger pyright because `pyrightconfig.json` includes only `src/`.
**Do not modify these test files as part of T6** — scope is limited to
`services/model_onboarding.py`. If a future plan adds `tests/` to the
pyright include path, the same UUID substitution will need to happen
there; tracked as a latent follow-on, not a blocker.

**Exit**: zero pyright errors at these three sites in `model_onboarding.py`;
smoke-test invocations pass; test files listed above are unchanged.

#### T6b — `model_onboarding.py:240` — `rng.random()` float in `dict[str, str]`

**File**: `src/sapphire_flow/services/model_onboarding.py:240`

`rng.random()` returns `float` but is assigned into a `dict[str, str]`
row value. Verify the actual line content at implementation time.

1. Fix by wrapping the float value with `str(...)` or using
   `f"{rng.random():.6f}"` for the dict value. Choose the format that
   best matches the surrounding row construction style.

**Exit**: zero pyright errors at this site; the dict value is a `str`.

#### T7 — `_validate_ensemble_dict` Protocol + preserve domain check

**File**: `src/sapphire_flow/services/model_onboarding.py:302, 330, 346–359`

1. Define `class _HasParameter(Protocol): parameter: str` **module-locally**
   in `src/sapphire_flow/services/model_onboarding.py` (at module level,
   not inside `types/domain.py` — per D3, the Protocol is service-private
   and belongs co-located with its only caller `_validate_ensemble_dict`).
   Insertion point: at module top-level near `_validate_ensemble_dict`
   in `model_onboarding.py` (search for `def _validate_ensemble_dict` to
   anchor; place the Protocol immediately above the function definition,
   below imports). The type of `parameter` is `str` — confirmed from
   `ForecastEnsemble.parameter: str` in `src/sapphire_flow/types/ensemble.py`.

   **Import**: `model_onboarding.py` currently has only
   `from typing import TYPE_CHECKING`; it does NOT import `Protocol`.
   Add `from typing import Protocol` as a runtime import (NOT under
   `TYPE_CHECKING`, since the Protocol is used at module level for
   parameter type annotation that's evaluated at definition time
   when used with `Mapping[str, _HasParameter]`). If both `Protocol`
   and `TYPE_CHECKING` are needed, combine: `from typing import Protocol, TYPE_CHECKING`.
2. Change `_validate_ensemble_dict` signature: parameter type
   `ensembles: Mapping[str, _HasParameter]` (replacing the current
   `dict[str, object]` or `Mapping[str, object]`). This makes `.parameter`
   accessible without `hasattr`.
3. **Replace** `if hasattr(ensemble, 'parameter') and ensemble.parameter != key:`
   with `if ensemble.parameter != key:`.
   The `hasattr` wrapper is removed because the Protocol makes
   `.parameter` accessible on every value in the dict. The
   `ensemble.parameter != key` check is **domain validation** (mismatch
   detection between dict key and ensemble's internal parameter name)
   and must be preserved. Under no circumstances remove the
   `ensemble.parameter != key` check — it is not a narrowing guard.

**Exit**: zero pyright errors at these sites; the domain check fires
correctly when key ≠ ensemble.parameter; smoke-test passes.

#### T8 — `hindcast.py:215, 217, 589` — same variance/narrowing pattern

**File**: `src/sapphire_flow/services/hindcast.py`

1. Inspect each site. `.basin_id` on `object` suggests a
   `dict[StationId, object]` or similar. Change the type to
   `dict[StationId, <concrete>]` at the declaration; if the
   upstream factory is Unknown, add a boundary cast.
2. **Root-cause fix at the shim, not the consumer**: line 589's
   `stack_model_inputs(inputs=legacy_batch)` fails because
   `legacy_batch` (built at lines 582–584) holds the return values of
   `_to_legacy_model_inputs(...)` — and that helper at
   `hindcast.py:229` is annotated `def _to_legacy_model_inputs(inputs: StationModelInputs) -> object:`.
   The body actually returns a `ModelInputs` instance (verify at
   line 238 or wherever the `return` lives); the `-> object` annotation
   is the bug. Fix: change the return annotation to `-> ModelInputs`
   and import `ModelInputs` from `src/sapphire_flow/types/model.py`
   if not already imported. With the shim return type tightened,
   `legacy_batch` inferred type becomes `dict[StationId, ModelInputs]`,
   which is directly assignable to `stack_model_inputs`'s existing
   `dict[StationId, ModelInputs]` parameter — NO widening of
   `stack_model_inputs`'s signature is needed. (Earlier draft of this
   plan prescribed widening `stack_model_inputs` to
   `Mapping[StationId, ModelInputs]`; that is unnecessary once the
   shim's return type is correct, and `Mapping` widening would also
   be a public-API signature change. Keep `stack_model_inputs` as-is.)

**Exit**: zero pyright errors at lines 215, 217, and 589; hindcast
tests pass; `stack_model_inputs`'s public signature in
`types/model.py` is unchanged.

#### T9 — `observation_store.py:86` — append union narrowing

**File**: `src/sapphire_flow/store/observation_store.py:86`

1. Read the surrounding context (~20 lines). Identify where the union
   is introduced. Add a narrowing check or change the variable's
   annotation so only `ObservationId` is appended.

**Exit**: zero pyright error at this site.

#### T10 — `record_fixtures.py:319, 323` — gridded-vs-point forecast narrowing

**File**: `src/sapphire_flow/tools/record_fixtures.py:319, 323`

1. Before `.archive(...)` and `.nwp_source` access, narrow the union
   `GriddedForecast | dict[...]` with `isinstance(forecast, GriddedForecast)`
   or a dedicated branch.

**Exit**: zero pyright errors at these sites.

#### T10b — `adapters/meteoswiss_nwp.py:180` — `dict_keys` passed to `sorted()`

**File**: `src/sapphire_flow/adapters/meteoswiss_nwp.py:180`

`dict_keys[...]` is passed where `sorted()` expects an iterable. Pyright
flags this as a type mismatch. Verify the actual call at implementation
time.

1. Fix by wrapping with `list(...)` or `tuple(...)` before passing to
   `sorted()`. Pattern: `sorted(list(my_dict.keys()))` or equivalently
   `sorted(my_dict)` (dict iteration yields keys directly and satisfies
   pyright).

**Exit**: zero pyright errors at this site; the sort produces the same
result as before.

#### T11 — `observation_coverage_summary.py:189` — Protocol mismatch

**File**: `src/sapphire_flow/tools/observation_coverage_summary.py:189`
+ `src/sapphire_flow/protocols/<observation_store>.py`

0. **Investigate first**: run
   `uv run pyright src/sapphire_flow/tools/observation_coverage_summary.py`
   and read the error message at line 189. The plan prescribes a fix
   direction only after the actual divergence is known — it is likely a
   parameter type divergence (not a missing method), but confirm before
   acting.
1. After the investigation, identify which method signature diverges
   between `PgObservationStore` and `ObservationStore`.
2. Either fix `PgObservationStore` to satisfy the protocol, or fix
   the protocol to reflect reality. **Default** (if the divergence is
   not a genuine protocol oversight): bring the implementation into
   protocol compliance — the protocol is the contract.
3. If the fix is non-trivial, escalate to a separate plan and use a
   dated ignore per Plan 069 D6 format:
   `# pyright: ignore[reportArgumentType]  # <reason>; re-review YYYY-MM-DD`
   (default re-review date: 6 months from implementation date).

**Note on spec staleness**: `docs/spec/types-and-protocols.md` shows
`update_qc(self, observation_id, qc_status, qc_flags)` without the
`qc_rule_version` parameter that already exists in both
`protocols/stores.py:100` and `PgObservationStore`. T11's investigation
will discover the protocol and implementation already match; the spec
divergence is a separate doc-sync task — flag for resolution in a
follow-on plan, do NOT fix the spec as part of T11.

**Exit**: zero pyright errors at this site OR an explicit
follow-up-plan link + dated ignore per D6 format.

#### T12 — `store/forecast_store.py:99` — `_rows_to_domain` annotation

**File**: `src/sapphire_flow/store/forecast_store.py:99`

1. Fix the `_rows_to_domain(rows: list[Unknown])` signature: replace
   with `_rows_to_domain(rows: Sequence[RowMapping])` (matching the
   callsite) OR `Iterable[RowMapping]`. Read the body to pick.

**Exit**: zero pyright error at this site.

### Phase 3 — Tier 3 (cleanup + design calls)

Single commit:

#### T13 — Cleanup batch

1. `model_onboarding.py:74, 121` — the always-true `isinstance` on
   `ForecastModel`: either remove (if the check is redundant) or
   replace with a meaningful concrete check (if the real intent was
   to distinguish Station vs Group).
2. `api/routes/{dashboard,forecasts,models,stations}.py` — all 4 modules
   import `_get_reflected` from `src/sapphire_flow/api/routes/tables.py`
   AND call it multiple times each. Per decision D7 (promote to public):
   rename `_get_reflected` → `get_reflected`. **Full rename scope**:
   verify by `grep -rn "_get_reflected" src/sapphire_flow/` before
   editing. As of 2026-05-11 the count is:
   - `tables.py`: 1 definition + 3 internal call sites (≈ lines 76, 107, 149) = **4 tokens**.
   - `api/routes/dashboard.py`: 1 import + 1 call = **2 tokens**.
   - `api/routes/forecasts.py`: 1 import + 3 calls = **4 tokens**.
   - `api/routes/models.py`: 1 import + 3 calls = **4 tokens**.
   - `api/routes/stations.py`: 1 import + 7 calls = **8 tokens**.

   Total: **22 token replacements** across 5 files. A naive "update
   the 4 imports" instruction misses 18 call-site usages and would
   leave NameError at runtime on every route handler that uses the
   function. Use `sed`/`ruff format`-safe replacement (or per-file
   Edit with `replace_all=true` on the bare identifier). Re-grep
   after editing to confirm zero `_get_reflected` remain
   (`grep -rn "_get_reflected" src/sapphire_flow/` must return empty).
   Add a brief docstring at the public function explaining the
   reflection cache pattern (one sentence is sufficient — e.g.
   "Lazily reflects the live database schema; cached per engine
   after first call.").
3. `api/__init__.py:22` — investigate the rich-config str-where-type-expected
   error; the pyright error message will identify the misuse; fix in-place.
   NOT related to the geoalchemy2 import or the `get_reflected` rename.
4. `api/routes/api_stations.py:124` — read and fix the "type not
   iterable" error.
5. `services/baselines.py:16` — delete dead `_doy_distance` fn.
6. `api/routes/tables.py` (geoalchemy2 lazy import inside `_get_reflected`):
   the `import geoalchemy2` inside `_get_reflected` is a side-effect import
   that registers the PostGIS geometry type with SQLAlchemy for
   `MetaData.reflect()`. Do NOT remove it. Instead, add a pyright suppress
   comment (Plan 069 D6 dated-ignore precedent):
   ```python
   import geoalchemy2  # pyright: ignore[reportUnusedImport]  # 2026-05-11: side-effect registers PostGIS geometry type with SQLAlchemy for MetaData.reflect() in this module. Re-review 2026-11-11.
   ```
   The existing `# noqa: F401` ruff suppress may remain alongside the
   pyright suppress if ruff still fires.

**Exit**: zero pyright errors for all Tier-3 sites (excluding T13.5);
all affected tests pass; no runtime behaviour change.

#### T13.5a — `forecast_qc.py:242` — match exhaustion on `ForecastQcRuleParams.rule_id`

**Files**: `services/forecast_qc.py`, `config/forecast_qc_rules.py`

Change `ForecastQcRuleParams.rule_id: str` →
`Literal["negative_value", "range_check", "flat_ensemble",
"ensemble_spread", "climatology_outlier", "temporal_consistency",
"quantile_crossing"]` (the 7 rules visible at `forecast_qc.py:243–261`).
This makes invalid rule IDs unrepresentable, aligns with
parse-don't-validate, and makes the `match` exhaustive.

Also extend `src/sapphire_flow/config/forecast_qc_rules.py:_parse_rule()`
to validate `raw["rule_id"]` against the literal set at the TOML boundary
before constructing `ForecastQcRuleParams`. Per CLAUDE.md §Pydantic for
boundary validation: if `config/forecast_qc_rules.py` already uses
Pydantic, extend the Pydantic model; if not, add a small inline validation
step that raises `ValueError` with a clear message (e.g.
`f"Unknown forecast QC rule_id {raw['rule_id']!r}; valid: {VALID_RULE_IDS}"`).

**Exit**: zero `reportMatchNotExhaustive` errors at `forecast_qc.py:242`;
`ForecastQcRuleParams.rule_id` is typed as `Literal[...]`; boundary
parsing rejects unknown rule IDs with a clear error.

#### T13.5b — `qc.py:238` — match exhaustion on `QcRuleParams.rule_id`

**Files**: `services/qc.py`, `config/qc_rules.py`

Same treatment as T13.5a for `QcRuleParams.rule_id`. The full enumerable
set is **5 items, not 4**: `range_check`, `rate_of_change`, `spike`,
`gross_outlier`, **and `frozen_sensor`**. The `match` statement at
`qc.py:238` only covers the first four because `frozen_sensor` is
short-circuited by an `if rule.rule_id == "frozen_sensor":` guard
earlier in the function (see `qc.py:227–228`). The `Literal[...]`
annotation on `QcRuleParams.rule_id` MUST include `frozen_sensor` —
omitting it makes `frozen_sensor`-typed rule rows from
`config/qc_rules.py` (lines 45, 81, 117, 153, 189) unrepresentable
and the default rule set unloadable. Verify the count against
`services/qc.py:238` and `config/qc_rules.py` at implementation time;
escalate if the set has changed.

Also extend `src/sapphire_flow/config/qc_rules.py:_parse_rule()` to
validate `raw["rule_id"]` against the literal set at the TOML boundary,
following the same pattern as T13.5a. Verified 2026-05-11:
`config/qc_rules.py` does NOT currently use Pydantic — use the inline
`VALID_RULE_IDS` frozenset + `ValueError` raise pattern from T13.5a;
do NOT add Pydantic as a new dependency at this boundary.

**Exit**: zero `reportMatchNotExhaustive` errors at `qc.py:238`;
`QcRuleParams.rule_id` is typed as `Literal[...]`; boundary parsing
rejects unknown rule IDs with a clear error.

### Phase 4 — Verify + close out

#### T14 — Verify global baseline

1. `uv run pyright --outputjson src/` → total error count.
2. Live baseline at 2026-05-11: 676 concrete pyright errors (including
   `meteoswiss_nwp.py:180`). Expected after Plan 073: 676 − 67 = 609
   maximum, but materially lower in practice because T4 alone yields
   ~20 individual diagnostics across 2 sites (counted as 1 'site' in the
   tier table). Exit gate: **pyright total ≤ 609** AND no new rule
   classes introduced. Don't assert a floor — Plan 073 fixes may cascade
   into other sites' diagnostics resolving automatically.
3. Record the new count in the implementation commit message body
   (e.g. 'Live pyright count post-Plan-073: <N>'). Plan 069 Phase 1's
   T2 will read the latest such message when establishing the ratchet
   baseline.

**Exit**: pyright total ≤ 609, no new rule classes introduced, all
unit + integration tests pass.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | T1, T2, T3 (Tier 1.1–1.3) | High | Low-medium | Latent None/union-dispatch crashes. Highest per-error ROI. |
| 2 | T4, T5 (Tier 1.4, polars leakage) | High | Low | Type leakage from polars into QC logic. Same root; batch. |
| 3 | T6, T6b, T7, T8 (Tier 2.1–2.3 + scope add) | Medium | Low-medium | Domain-type soundness. No crashes today, but types lie. |
| 4 | T9, T10, T10b, T11, T12 (Tier 2.4–2.7 + scope add) | Medium | Medium (T11 may escalate) | Long tail of narrowing/protocol fixes. |
| 5 | T13, T13.5a, T13.5b (Tier 3 cleanup) | Low | Low | Stylistic, design, dead code. One commit. |
| 6 | T14 (verify) | Low | Trivial | Close-out. |

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-tier1",
      "tasks": ["T1", "T2", "T3", "T4", "T5"],
      "parallel": true,
      "depends_on": []
    },
    {
      "id": "phase-2-tier2",
      "tasks": ["T6", "T6b", "T7", "T8", "T9", "T10", "T10b", "T11", "T12"],
      "parallel": true,
      "depends_on": ["phase-1-tier1"]
    },
    {
      "id": "phase-3-tier3",
      "tasks": ["T13", "T13.5a", "T13.5b"],
      "parallel": false,
      "depends_on": ["phase-2-tier2"]
    },
    {
      "id": "phase-4-verify",
      "tasks": ["T14"],
      "parallel": false,
      "depends_on": ["phase-3-tier3"]
    }
  ]
}
```

Phase-1 tasks are fully independent (different files, different rule
classes) and can run in parallel branches. Phase-2 tasks are also
independent. Phase-3 is a single commit. Phase-4 is a verification
step.

---

## Open questions for user review

1. **T1 — ExceedanceResult `__post_init__` tightening.** The
   principled fix (tighten `__post_init__`) is recommended — both
   callsites at `alert_strategy.py:182/187` and `232/237` already pass
   a computed `float`. The tightening is a behaviour change:
   `ExceedanceResult` construction with `exceeded=True, exceedance_probability=None`
   now raises. Confirm this is acceptable before landing. If grep finds a
   surprise callsite passing `exceeded=True, exceedance_probability=None`,
   stop and escalate — do not fix it inline.
2. **T3 — use `isinstance` or `match`?** Python's `match` with class
   patterns is cleaner but has a learning cost. Recommendation:
   `isinstance` if there are only two branches; `match` if a third
   ever appears.
3. **T6 — smoke-test NewType round-trip.** Should the smoke-test
   data fixtures move to a dedicated module (e.g.
   `services/_smoke_fixtures.py`) rather than being inline in
   `model_onboarding.py`? Not required for the fix; flag as hygiene.
4. **T11 — protocol vs impl divergence: which direction to fix?**
   Needs a read before the call. Default per D-architecture rule is
   "protocol is the contract", but if the implementation captures a
   real capability the protocol forgot, flip it. Log finding in the
   commit message.
5. **T13.2 — `_get_reflected` design call.** Resolved: promote to
   `get_reflected` (public) with a brief docstring, since 4 routes
   already depend on it. See T13 item 2 above.
6. **T13.5a/T13.5b — Literal design call.** Resolved: use option (b)
   (Literal) because the rule ID sets are small and enumerable. T13.5a
   uses 7 items from `forecast_qc.py`; T13.5b uses **5 items** from
   `qc.py` (range_check, rate_of_change, spike, gross_outlier, and
   frozen_sensor — the last is handled outside the `match` block via
   an early-return guard at `qc.py:227–228` but is still a valid
   `rule_id` value at the type level). Both also extend config
   boundary validation to reject unknown rule IDs before constructing
   the params types. Verified 2026-05-11: neither
   `config/forecast_qc_rules.py` nor `config/qc_rules.py` currently
   uses Pydantic; both T13.5a/b use the inline `VALID_RULE_IDS`
   frozenset + `ValueError` pattern (no new Pydantic dep at this
   boundary).

## Changelog

- **2026-04-22** — Initial DRAFT. Motivated by the 2026-04-22
  `pyrightconfig.json` carve-out experiment (which cut total errors
  1078 → 675) and the subsequent investigation of the 509 non-flows
  errors that found 64 concrete violations across 3 tiers: latent
  crashes (11), type-safety gaps (40), cleanup + design (13). Four
  phases: Tier 1 → Tier 2 → Tier 3 cleanup → verify. Designed to
  land alongside Plan 069 (ratchet) and to clear real-bug signal
  before Plan 069's Unknown-cluster drain begins.

- **2026-04-22 (rewrite)** — Addressed three factual errors from
  critical review: (T2) clarified that `TrainingUnit.__post_init__`
  at `types/training.py:24` already enforces exactly-one-of; the fix
  is narrowing-only (`assert unit.group_id is not None`), not a new
  invariant; (T6) corrected that `ModelId = NewType("ModelId", str)`
  is intentionally string-based and out of scope — only `StationId`
  and `StationGroupId` constructors (UUID-based) need uuid4() fixes;
  (T7) preserved the `ensemble.parameter != key` domain check — only
  the `hasattr` wrapper is removed; the mismatch-detection logic must
  stay. Added preconditions: T3 documents that `@runtime_checkable` is
  already present on both Protocols (confirmed in
  `protocols/forecast_model.py:22, 48`) and requires re-checking if
  new Protocols join the union; T4 clarified as datetime-specific
  (use `assert isinstance(first_vt, datetime)`, NOT `float(...)`); T5
  clarified as numeric-specific (`float(...)` inside the None-check).
  T1 annotated as a behaviour change requiring callsite verification.
  D4 updated to document the datetime/numeric split. Escalated T13.5
  match-exhaustion to a proper design call (Literal vs `case _:`) with
  recommendation for option (b). Added T11 investigation step (run
  pyright, read the error) before prescription. Added D10 for
  `@runtime_checkable` precondition. Added Cross-plan coordination
  section with merge order (070 → 073 → 069 Phase 1 → 069 Phase 2+),
  file-exclusion list for Plan 069 Phase 2, and baseline numbers.
  Resolved Open Q6 (Plan 069 Phase 2 overlap) as binding coordination;
  replaced with T13.5 Literal-vs-pass design call.

- **2026-05-11 (corrections pass)** — Applied 25 orchestrator corrections
  from three-reviewer pass. Key changes: (1) Tier-1 site count updated
  to "~13 enumerated sites" with note that raw diagnostic count is higher
  (T4 alone ~20 diagnostics). (2) Stale live baseline corrected from 64
  errors / 675 total to 65+ errors / 676 total (includes `meteoswiss_nwp.py:180`).
  (3) T14 exit gate updated to ≤609, with "don't assert a floor" caveat.
  (4) T13 item 6 rewritten from "remove geoalchemy2 import" to
  "add dated pyright:ignore[reportUnusedImport] with PostGIS side-effect
  explanation; re-review 2026-11-11" — P1 operational-risk fix. (5) T13.5
  split into T13.5a (ForecastQcRuleParams, 7 rules, + config boundary
  validation) and T13.5b (QcRuleParams, 5 rules including frozen_sensor,
  + config boundary validation; non-Pydantic inline VALID_RULE_IDS path
  per 2026-05-11 verification). (6) Three scope additions: T3 extended
  to line 710 model=
  argument in else-branch; T6b added (model_onboarding.py:240 float-in-str-dict);
  T10b added (meteoswiss_nwp.py:180 dict_keys sort). (7) T6 step 4 updated
  to decouple UUID wrapping from DataFrame column string values. (8) T4
  updated with datetime import note and ruff TCH003/noqa guidance. (9) T1
  updated with callsite escalation rule, line-number clarification
  (182/187 and 232/237), test location (tests/unit/types/test_domain.py),
  and alert_checker.py:319 out-of-scope note. (10) T2 step 3 changed from
  "apply at line 1021" to structural grep anchor; step 4 changed from
  docstring to inline # comment per CLAUDE.md §Documentation Standards.
  (11) T3 smoke_test_model clarifying note added. (12) T7 Protocol type
  confirmed as str; placement anchored to after ExceedanceResult in
  types/domain.py. (13) T11 ignore format updated to D6 with 6-month
  re-review date; spec staleness note added. (14) T13.2 resolved: promote
  to get_reflected (public) with brief docstring. (15) D9 updated to
  record count in commit message body rather than /tmp cache. (16) §Inputs
  /tmp references replaced with "generate fresh at execution time" note.
  (17) §Cross-plan coordination file list expanded to include all Plan 073
  files (config/, adapters/, types/). (18) Open Q5/Q6 updated to reflect
  resolved design calls.

- **2026-05-11 (DRAFT — final-review touch-ups)** — A fourth Sonnet
  4.6 reviewer ran a post-corrections sweep and found one P1 defect
  introduced by the corrections pass: T13.5b's prescribed Literal set
  for `QcRuleParams.rule_id` was stated as 4 items (range_check,
  rate_of_change, spike, gross_outlier) but the actual valid set is
  **5 items** — `frozen_sensor` is also a valid rule_id, handled
  outside the `match` block via an early-return guard at
  `qc.py:227–228`. Omitting it would make the default QC rule set
  (which uses `frozen_sensor` in `config/qc_rules.py` lines 45, 81,
  117, 153, 189) unrepresentable. Fixed in T13.5b body, §Open Questions
  Q6, and this Changelog. Also clarified that neither
  `config/forecast_qc_rules.py` nor `config/qc_rules.py` currently uses
  Pydantic (verified 2026-05-11), so both T13.5a and T13.5b use the
  inline VALID_RULE_IDS frozenset + ValueError pattern — no new
  Pydantic dep at this boundary.

  Ready for orchestrator promotion to READY.

- **2026-05-11 (DRAFT — cold-read sanity check touch-ups)** — A fifth
  Sonnet 4.6 reviewer ran an independent cold-read pass (no prior
  finding anchor) and surfaced four issues that the previous rounds
  missed: (1) **P1 T4 noqa is wrong** — the plan said `# noqa: TC003`
  but the project uses the `TCH` ruleset prefix
  (`pyproject.toml` line 84: `select = ["TCH"]`, line 87:
  per-file-ignores reference `"TCH002"`/`"TCH003"`). Fixed to
  `# noqa: TCH003`. (2) **P1 T2 line 709 is a separate error from
  lines 707/1021** — the plan's `assert unit.group_id is not None`
  fix narrows the argument to `fetch_group` but not the return value
  (`group: StationGroup | None`). Added step 4 prescribing
  `assert group is not None` (or explicit `StoreError` raise) after
  the fetch_group call. (3) **P1 T13 item 2 rename scope** — the
  plan said "update all 4 route imports" but `tables.py` has 3
  internal call sites of `_get_reflected` (lines 76, 107, 149) that
  the literal-rename would leave broken with `NameError`. Updated to
  explicitly enumerate all 7+ call sites (1 definition + 3 internal +
  4 external). (4) **P2 T7 Protocol placement breaks `types/` vs
  `protocols/` separation** — placing `_HasParameter` in
  `types/domain.py` violates the project convention that `types/` is
  for frozen dataclasses and `protocols/` for structural interfaces.
  Since `_HasParameter` is service-private (used only by
  `_validate_ensemble_dict` in `model_onboarding.py`), the correct
  placement is **module-local in `model_onboarding.py` itself**. D3
  and T7 step 1 updated accordingly.

  Plan ready for orchestrator promotion to READY (after this
  touch-up).

- **2026-05-11 (DRAFT — round 4 cold-read touch-ups)** — A sixth
  Sonnet 4.6 reviewer ran another independent cold-read pass and
  surfaced three more issues that round 3 missed: (1) **P1 T2 step 4
  assert-vs-StoreError branch was unresolved** — fetch_group's
  protocol contract at `protocols/stores.py` returns
  `StationGroup | None` (verified 2026-05-11). Missing-group-at-
  training-time is a data-integrity fault per `docs/conventions.md`
  §Exception table, so the correct fix is `raise StoreError(...)`,
  not `assert`. T2 step 4 now prescribes the StoreError path
  unconditionally with import guidance. (2) **P2 T13 item 2 rename
  scope undercounted external usages** — the plan said "4 external
  imports" but actual count outside `tables.py` is **22 token
  replacements** across 5 files (1 import + N calls per file:
  dashboard 2, forecasts 4, models 4, stations 8). Updated to a full
  per-file enumeration with a grep-verify-empty exit check. (3) **P2
  T7 missing Protocol import** — `model_onboarding.py` does not
  currently import `Protocol` from typing; the prescribed
  `class _HasParameter(Protocol)` definition would `NameError` at
  module import without it. T7 step 1 now prescribes adding
  `from typing import Protocol` as a runtime import.

  Plan ready for orchestrator promotion to READY (after this
  fourth round of touch-ups).

- **2026-05-11 (DRAFT — round 5 touch-up)** — A seventh Sonnet 4.6
  reviewer ran round 5 and surfaced one P1 defect the prior four
  rounds all missed: T8's prescribed fix (widen `stack_model_inputs`'s
  parameter to `Mapping[StationId, ModelInputs]`) does NOT resolve
  the pyright error at hindcast.py:589 because the call site passes
  `legacy_batch`, a `dict[StationId, object]` whose value type comes
  from `_to_legacy_model_inputs(...) -> object` at hindcast.py:229.
  The root cause is the shim's `-> object` return annotation, not
  the consumer's parameter type. T8 step 2 rewritten to fix the
  shim's return annotation to `-> ModelInputs` and keep
  `stack_model_inputs`'s public signature unchanged (the earlier
  proposal would have been a needless API widening). Find-rate
  across 5 rounds: 8 → 3 → 4 → 3 → 1 — meaningfully diminishing.

  Plan ready for orchestrator promotion to READY.

- **2026-05-11 (DRAFT — round 6 touch-up)** — A sixth-round reviewer
  ran a focused scan of areas prior rounds touched least and found
  one P3 nit: T6 step 4's DataFrame decoupling pattern told the
  implementer to use `sid_label` for *new* row construction but did
  NOT explicitly say to also update the *existing* `str(sid)` calls
  at lines 228 and 238. Without the explicit instruction those calls
  would silently emit UUID strings instead of `"synthetic_0"` etc.,
  bleeding into column values and breaking any downstream pattern-
  matching on the `synthetic_` prefix. Added one-sentence
  clarification + `grep -n "str(sid)"` verification step to T6 step 4.

  Find-rate across 6 rounds: 8 → 3 → 4 → 3 → 1 → 1 (P3-only). Round 6
  found no P1/P2 issues. Round 6 verdict: no blocking issues.

  Plan ready for orchestrator promotion to READY.

- **2026-05-11 (READY — promoted)** — Status flipped DRAFT → READY
  after six review rounds with a clearly-diminished find-rate (round
  6 found zero P1/P2 issues, only a P3 clarification). Approximately
  22 surgical edits applied across the day's reviews. The plan is
  implementation-ready. Plan 069's T15 reciprocal exclusion list was
  updated in lockstep; both plans coordinated. Implementation gated
  on a separate orchestrator go-ahead. Sprint 2 merge order:
  070 (DONE) → 073 (this plan, READY) → 069 Phase 1 → 069 Phase 2+.
