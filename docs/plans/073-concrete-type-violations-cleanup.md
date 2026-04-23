# Plan 073 — Fix concrete pyright violations outside `flows/`

**Status**: DRAFT
**Date**: 2026-04-22
**Depends on**: none (independent of Plan 069 but intentionally lands alongside — Plan 069 drains the ~445 "Unknown-cluster" errors that propagate through services under the ratchet, while this plan fixes the concrete violations pyright flagged which are not masked by the `flows/` carve-out).
**Scope**: Fix the 64 concrete type-violations pyright reports outside
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
  flows/) — fixes 64 real-bug-rule violations before the ratchet
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
`api/routes/*`, `services/baselines.py`, `services/qc.py`,
`services/training_data.py`, `api/__init__.py`.

**Baseline numbers:**
- 1078 = pre-experiment (no carve-out). Historical reference only.
- 675 = post-experiment, pre-Plan-073 (flows/ carve-out active).
- ~611 = post-Plan-073 (this plan's target end-state; Plan 069's
  ratchet floor).

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

**Tier 1 — latent crashes** (11 errors across 5 call sites): None-check
gaps and union dispatch without narrowing that can AttributeError /
TypeError at runtime under realistic inputs.

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
  errors under a ratchet. This plan fixes the 64 concrete errors. The
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
- `/tmp/pyright_final.json` — the 675-error baseline this plan reduces.
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
| D3 | **Introduce a `_HasParameter` Protocol in `types/domain.py` for `_validate_ensemble_dict`.** `hasattr(x, "parameter")` does not narrow; pyright wants either `TypeGuard` or a Protocol. Protocol is structural and cheap. | Minimal surface: one Protocol, one validator, no other callers. Alternative of `cast(ForecastEnsemble, ensemble)` after `hasattr` is uglier and loses the "duck-typed" meaning. |
| D4 | **Polars wide-type leakage → fix depends on column dtype.** Two patterns: (a) **Numeric columns** (`.value`, `.std()`, `.iqr` results, `.median()` results): wrap with `float(...)` INSIDE the existing `if ... is not None` block — `float(None)` raises `TypeError`, so the cast must follow the None-check. Document with a one-line comment naming the polars API shape. (b) **Datetime columns** (`.valid_time`, used in `forecast_qc.py:89, 139`): narrow with `assert isinstance(x, datetime)` INSIDE the None-check — do NOT use `float(...)` on a datetime. The `float(...)` cast inside business logic technically violates parse-don't-validate (the typed-polars-wrapper alternative would be the principled fix), but a typed-polars-wrapper module is scope-excluded for this plan; escalate if the pattern expands. | The two column types require different fixes. A uniform `float()` wrapper is wrong for datetime columns and would raise at runtime. |
| D5 | **Tier 2.1 smoke-test NewType sites → construct from `uuid.uuid4()`.** Applies only to `StationId` and `StationGroupId` (UUID-based NewTypes). `ModelId = NewType("ModelId", str)` is string-based by design (see `POOLED_MODEL_ID = ModelId("_pooled")`, `BMA_MODEL_ID = ModelId("_bma")`, `CONSENSUS_MODEL_ID = ModelId("_consensus")` in `types/ids.py`); no change needed for `ModelId` constructors. | Soundness: the NewType promise holds for UUID-based IDs. `ModelId` string literals are intentional by design and must not be replaced. |
| D6 | **Tier 2 variance fixes: `dict[K, T]` parameters → `Mapping[K, T]` where T is treated read-only.** Follows PEP 484 variance rules. | One-line signature change; no runtime impact; unlocks call-site type safety without caller changes. |
| D7 | **Tier 3.2: `_get_reflected` → public `get_reflected`** if it is actually a public utility. Four route modules import it; that is API-shape, not internal. Alternatively, scope the usage to one internal adapter. | A private name imported by N > 1 external modules is a design lie. Pick one answer: rename (public) or relocate (truly internal). Reviewing the function will make that call; not pre-judged here. |
| D8 | **Phase 3 (Tier 3) tasks land as one consolidated cleanup commit, not file-by-file.** | Too small to be worth separate reviews. |
| D9 | **Update the ratchet baseline after each phase.** If Plan 069's baseline file exists by the time we start, update it. If not, cache the post-plan pyright count for Plan 069 Phase 1 to absorb. | Keeps the two plans coherent when they land in either order. |
| D10 | **Protocols dispatched via `isinstance` must carry `@runtime_checkable`.** Before T3's isinstance narrowing lands, verify `StationForecastModel` and `GroupForecastModel` carry the `@runtime_checkable` decorator. Confirmed: both are decorated in `protocols/forecast_model.py` (lines 22 and 48). If any future Protocol is added to the dispatch, the precondition check must be repeated — a missing `@runtime_checkable` produces a `TypeError` at runtime, not a type error. | `isinstance` on a non-`@runtime_checkable` Protocol raises `TypeError`. The decorator must be present before T3 is implemented. |

---

## Task list

### Phase 1 — Tier 1 (latent crashes)

#### T1 — `alert_checker.py:308` — None-safe max

**File**: `src/sapphire_flow/services/alert_checker.py`

1. Read the loop at lines 295–309. Both callsites in
   `alert_strategy.py:187, 237` always pass a computed `float`
   (returned by `_compute_exceedance`) — there is no path today where
   `exceeded=True` and `exceedance_probability=None` simultaneously.
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

**Exit**: zero pyright errors at this site; a new
`ExceedanceResult.__post_init__` test covering the invalid-state
rejection passes; existing callsites verified not to pass `None` when
`exceeded=True`.

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
3. Apply the same narrowing assert at line 1021 (or wherever
   `unit.group_id` is passed to a function requiring non-None in the
   else-branch).
4. Add a docstring note on `TrainingUnit` that `__post_init__` enforces
   exactly-one-of station_id / group_id, so callers can rely on this
   after construction.

**Do NOT** propose a tagged-union refactor — the invariant already
exists and a refactor is out of scope.

**Exit**: zero pyright errors at these sites; the assertion's failure
message is informative; existing model-onboarding tests pass.

#### T3 — `model_onboarding.py:758, 760` — ForecastModel union dispatch

**File**: `src/sapphire_flow/services/model_onboarding.py`

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
2. Ensure `from datetime import datetime` is present at the top of the
   file (check the imports — add only if missing).
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
4. If any string identifier is used for logging/display, move it to a
   `name` or `label` field on the surrounding dataclass — do not lose
   the debugging value.

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

#### T7 — `_validate_ensemble_dict` Protocol + preserve domain check

**File**: `src/sapphire_flow/services/model_onboarding.py:302, 330, 346–359`

1. Define `class _HasParameter(Protocol): parameter: str` (or
   whatever `.parameter`'s actual type is — read the body).
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
2. `stack_model_inputs(inputs=dict[StationId, object])` at 589:
   change parameter to `Mapping[StationId, ModelInputs]`.

**Exit**: zero pyright errors at these sites; hindcast tests pass.

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
3. If the fix is non-trivial, escalate to a separate plan and
   `# pyright: ignore[reportArgumentType]` with reason + dated
   re-review.

**Exit**: zero pyright errors at this site OR an explicit
follow-up-plan link + dated ignore.

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
2. `api/routes/{dashboard,forecasts,models,stations}.py` +
   `api/__init__.py` — decide whether `_get_reflected` becomes public
   (`get_reflected`) or truly internal. Rename accordingly.
3. `api/__init__.py:22` — read and fix the `str`-where-type-expected
   misuse (Rich config).
4. `api/routes/api_stations.py:124` — read and fix the "type not
   iterable" error.
5. `services/baselines.py:16` — delete dead `_doy_distance` fn.
6. `api/routes/tables.py:30` — remove unused `geoalchemy2` import.

**Exit**: zero pyright errors for all Tier-3 sites (excluding T13.5);
all affected tests pass; no runtime behaviour change.

#### T13.5 — `forecast_qc.py:242`, `qc.py:238` — match exhaustion on `str`

**Files**: `services/forecast_qc.py:242`, `services/qc.py:238`

`ForecastQcRuleParams.rule_id: str` cannot be exhaustively matched —
pyright knows `str` has infinite values and reports
`reportMatchNotExhaustive`. This is a design question, not a simple fix.

Two options:

**(a) Tactical** — add `case _: pass` to silence the warning. Works,
but is a band-aid: the type system cannot help catch a misspelled or
new rule ID at development time.

**(b) Structural (CLAUDE.md-aligned)** — change `rule_id` to
`Literal["negative_value", "range_check", "flat_ensemble",
"ensemble_spread", "climatology_outlier", "temporal_consistency",
"quantile_crossing"]` (the exact set visible at `forecast_qc.py:243–261`).
This makes invalid rule IDs unrepresentable, aligns with parse-don't-validate,
and makes the `match` exhaustive. Apply the same treatment to `qc.py`
after reading its rule IDs.

Land option **(b)** if the literal set is small and enumerable (it is —
seven rule IDs visible today). Fall back to option **(a)** with a
re-review date comment if the rule set is open-ended. Escalate to Open
Questions if the design call is not straightforward.

**Exit**: zero `reportMatchNotExhaustive` errors at these sites; rule
IDs are typed as `Literal[...]` or guarded by `case _:`.

### Phase 4 — Verify + close out

#### T14 — Verify global baseline

1. `uv run pyright --outputjson src/` → total error count.
2. Expected: `675 − 64 = 611` (plus or minus any cascade fixes).
3. Record the new baseline into any file Plan 069 Phase 1 creates,
   or cache for that plan's ratchet.

**Exit**: pyright total ≤ 611, no new rule classes introduced, all
unit + integration tests pass.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | T1, T2, T3 (Tier 1.1–1.3) | High | Low-medium | Latent None/union-dispatch crashes. Highest per-error ROI. |
| 2 | T4, T5 (Tier 1.4, polars leakage) | High | Low | Type leakage from polars into QC logic. Same root; batch. |
| 3 | T6, T7, T8 (Tier 2.1–2.3) | Medium | Low-medium | Domain-type soundness. No crashes today, but types lie. |
| 4 | T9, T10, T11, T12 (Tier 2.4–2.7) | Medium | Medium (T11 may escalate) | Long tail of narrowing/protocol fixes. |
| 5 | T13, T13.5 (Tier 3 cleanup) | Low | Low | Stylistic, design, dead code. One commit. |
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
      "tasks": ["T6", "T7", "T8", "T9", "T10", "T11", "T12"],
      "parallel": true,
      "depends_on": ["phase-1-tier1"]
    },
    {
      "id": "phase-3-tier3",
      "tasks": ["T13", "T13.5"],
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
   callsites in `alert_strategy.py:187, 237` already pass a computed
   `float`. The tightening is a behaviour change: `ExceedanceResult`
   construction with `exceeded=True, exceedance_probability=None` now
   raises. Confirm this is acceptable before landing.
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
5. **T13.2 — `_get_reflected` design call.** If we promote it to
   `get_reflected` publicly, we commit to its signature stability.
   If we push the reflection logic inside each route and remove the
   shared helper, we lose DRY but tighten encapsulation.
   Recommendation: promote (with a brief docstring) since 4 routes
   already depend on it.
6. **T13.5 — Literal vs `case _:` design call.** Recommend option (b)
   (Literal) because the rule ID set is small and enumerable (7 items
   in `forecast_qc.py`). Confirm before the subagent changes the type
   of `rule_id` in `ForecastQcRuleParams` — this is a domain type
   change that affects any external code constructing `ForecastQcRuleParams`.

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
