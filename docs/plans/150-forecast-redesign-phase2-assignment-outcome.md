---
status: DRAFT
created: 2026-08-10
plan: 150
title: Forecast-cycle redesign Phase 2 — per-assignment outcome result + structured fallback
scope: The second, behaviour-preserving slice of the forecast-cycle redesign (docs/design/forecast-cycle-redesign.md). Migrate the station-cycle runner (_run_single_model / run_all_station_forecasts / MultiModelForecastResult) to return a structured, discriminated per-assignment outcome (AssignmentSuccess | AssignmentFailure, with a typed AssignmentFailureCause) in place of today's `StationForecastResult | str` return and `failed_models: dict[ModelId, str]` field. The fallback chain and its semantics are UNCHANGED — an assignment failure is still assignment-local, still advances the loop to the next-priority assignment, and the station still succeeds iff any assignment succeeds; this phase only replaces the untyped `str` failure channel with a structured one so a later phase (track resolution) can add a new failure cause without reworking the runner. Still one cycle: no ForcingTrackKey, no per-track resolution, no per-assignment input assembly, no superset removal. Forecast cycle.
depends_on: [148]
blocks: []
supersedes: []
---

# Plan 150 — Forecast-cycle redesign Phase 2: per-assignment outcome result

## Status
**DRAFT — Phase 2 of the forecast-cycle redesign** (`docs/design/forecast-cycle-redesign.md`, "Build sequence"
item 2: *"Migrate the station runner to consume per-assignment `ModelRunContext`, returning a per-assignment
success/failure result (fallback chain intact; a missing context ≠ a dead station). Still one cycle."*).

**This plan STACKS ON PLAN 148** (branch `feat/plan-148-modelruncontext`, PR #139 — merged into this branch, but
**not yet on `main`**). Every `file:line` citation in this document is against the **post-148 code in this
worktree** (`/Users/bea/Documents/GitHub/sapphire-phase2`, commit `d11e20e` at draft time), which already carries
`ModelRunContext` / `WarmUpState` / `load_warm_up_state` (`services/operational_inputs.py`) and the per-assignment
warm-up read inside `_run_single_model` (`services/run_station_forecast.py`). **Both `/plan`-finalizing this
document and `/implement`-ing it are GATED on PR #139 merging to `main`** — do not run either workflow, and do not
open an implementation PR for this plan, until #139 is on `main`. If #139's line numbers shift before it merges,
re-verify this plan's citations against the merged `main` state before implementation starts.

## Problem
Today `_run_single_model` (`src/sapphire_flow/services/run_station_forecast.py:98-118`) returns
`StationForecastResult | str` — a bare string standing in for "this assignment failed, here's why"
(`:126,155,166,185,221,258,275,310`). `run_all_station_forecasts` dispatches on `isinstance(outcome,
StationForecastResult)` (`:419-424`) and stores the string verbatim in
`MultiModelForecastResult.failed_models: dict[ModelId, str]` (`:76`). This has two problems the redesign's
Phase 3 depends on fixing first:

1. **No structure to extend.** Phase 3 needs to add a new, *distinct* assignment-local failure — "this
   assignment's track/cycle is unavailable" (the design's `AssignmentFailureCause.MISSING_CONTEXT`,
   `forecast-cycle-redesign.md:221`) — that must be **just as assignment-local** as today's eight failure paths
   and must **not** abort a station whose higher-priority assignment already succeeded ("a missing context ≠ a
   dead station", `forecast-cycle-redesign.md:272-273`). A bare `str` return gives Phase 3 nowhere principled to
   plug that in except string-formatting another sentence — callers (and tests) that need to distinguish failure
   *kinds* (e.g. to decide whether a failure is walk-back-eligible vs. flow-fatal, per the design's candidate-fetch
   taxonomy, `forecast-cycle-redesign.md:198-204`) can only pattern-match on message text.
2. **Untyped failure channel violates the repo's type-driven convention.** `CLAUDE.md` "Type Driven Development":
   *"Never use `bool`/raw `str` to represent a domain state with two [or more] named possibilities. Use
   `enum.Enum`"* and *"Parse, don't validate ... internal functions never accept raw primitives when a domain type
   exists."* Seven distinct, already-enumerable failure kinds (`model_not_found`, `insufficient NWP coverage`,
   `no_active_artifact`, `warm-up state load failed`, `unsupported stateful ensemble` ×2 call sites,
   `predict failed`, `QC failed for parameter X`) are currently indistinguishable except by parsing the message
   string — exactly the invalid-state-representable problem the convention exists to prevent.

## What Phase 2 delivers (and deliberately does NOT)
**Delivers:**
- A discriminated per-assignment outcome type, `AssignmentOutcome = AssignmentSuccess | AssignmentFailure`, and a
  typed `AssignmentFailureCause` enum covering today's seven concrete failure kinds.
- `_run_single_model` returns `AssignmentOutcome` instead of `StationForecastResult | str`.
- `MultiModelForecastResult.failed_models: dict[ModelId, AssignmentFailure]` instead of `dict[ModelId, str]`.
- Every existing test that reads `.failed_models` or asserts on a failure-reason substring, migrated to assert on
  `.cause` (and `.detail` where the message content itself matters).
- New red-first tests proving the three failure kinds that today are **only** exercised via the `run_station_forecast`
  wrapper's `None` return (`MODEL_NOT_FOUND`, `NO_ARTIFACT`) or via bare membership checks (`QC_FAILED`,
  `UNSUPPORTED_STATEFUL_ENSEMBLE`) now round-trip a correctly-typed `AssignmentFailure` into `failed_models`.
- A spec update at **both** places `failed_models`/`AssignmentFailureCause` appear in
  `docs/spec/types-and-protocols.md`: the forward-looking three-bucket `AssignmentFailureCause` sketch (`:3012-3016`,
  part of the design doc's "FI failure semantics" section, not yet landed in code) **and** the authoritative
  `MultiModelForecastResult.failed_models: dict[ModelId, str]` field in the "Multi-model combination types and
  services" section (`:3609`) — the latter's value type must change to match the landed `AssignmentFailure` (see
  D5).

**Deliberately does NOT:**
- Touch track resolution, `ForcingTrackKey`, per-track cycle resolution, or per-station availability
  (`StationTrackOutcome`/`TrackFetchResult`) — that is Phase 3 (`forecast-cycle-redesign.md` build sequence item 3).
- Touch per-assignment input assembly or drop the station superset (also Phase 3/4).
- Add a `MISSING_CONTEXT`/track-unavailable failure cause — there is no track concept yet for a context to be
  missing *from*. Phase 3 adds that variant onto the enum this phase creates (see D5 forward note).
- Change what a *successful* forecast produces, its QC, its provenance, or its persistence. Every currently-green
  code path that reaches `AssignmentSuccess` carries the exact same `StationForecastResult` it carries today.
- Change any log event name or add a new one — the eight `log.warning(...)` call sites keep their existing event
  names (`run_station_forecast.model_not_found`, `nwp.insufficient_coverage`, `run_station_forecast.no_active_artifact`,
  `run_station_forecast.warm_up_load_failed`, `run_station_forecast.unsupported_stateful_ensemble` ×2,
  `run_station_forecast.predict_failed`, `run_station_forecast.qc_failed`); only the **return value** built after
  each log call changes shape.
- Change `run_forecast_cycle.py`'s control flow. Grepped and confirmed (`flows/run_forecast_cycle.py:2173,2194,2215,
  2230,2269,2276,2282`): the flow reads `multi_result.primary_model_id`, `.results`, and `.combinable_results` — it
  **never reads `.failed_models`'s contents**, only ever `.results`/`.primary_model_id`. So this phase's type change
  to `failed_models`'s values needs **zero** production call-site changes in the flow layer; only the runner module
  itself and its direct unit tests move.

## Design

- **D1 — new discriminated outcome type, service-local in `services/run_station_forecast.py`.** Placed next to
  `StationForecastResult`/`MultiModelForecastResult` (`run_station_forecast.py:60-82`) — its own module, not
  `services/operational_inputs.py`: unlike `ModelRunContext` (a Plan-148 input-side construct consumed across
  the assembler and the runner), this is the runner's own **output** type, has exactly one producer
  (`_run_single_model`) and one consumer (`run_all_station_forecasts`), and no other module needs it. Frozen,
  kw-only, slots, per the repo's default value-type convention:
  ```python
  class AssignmentFailureCause(Enum):
      MODEL_NOT_FOUND = auto()
      INSUFFICIENT_COVERAGE = auto()
      NO_ARTIFACT = auto()
      WARM_UP_LOAD_FAILED = auto()
      UNSUPPORTED_STATEFUL_ENSEMBLE = auto()
      PREDICT_FAILED = auto()
      QC_FAILED = auto()

  @dataclass(frozen=True, kw_only=True, slots=True)
  class AssignmentSuccess:
      result: StationForecastResult

  @dataclass(frozen=True, kw_only=True, slots=True)
  class AssignmentFailure:
      cause: AssignmentFailureCause
      detail: str

  AssignmentOutcome = AssignmentSuccess | AssignmentFailure
  ```
  `detail` carries exactly the free-text half of today's return string (e.g. today's
  `f"insufficient NWP coverage: {coverage.detail}"` at `:155` becomes
  `AssignmentFailure(cause=AssignmentFailureCause.INSUFFICIENT_COVERAGE, detail=f"insufficient NWP coverage: {coverage.detail}")`
  — the message text is preserved verbatim in `detail` so no log line or human-readable content is lost; `cause`
  is the new machine-checkable discriminant). A success wraps the existing, unchanged `StationForecastResult` — no
  field on `StationForecastResult` itself changes.
- **D2 — `_run_single_model` returns `AssignmentOutcome`, not `StationForecastResult | str`.** Every one of the
  eight `return f"..."` sites becomes `return AssignmentFailure(cause=..., detail=f"...")`, and the terminal
  `return StationForecastResult(...)` (`:354-361`) becomes `return AssignmentSuccess(result=StationForecastResult(...))`
  — nine return sites total (eight failures + one success). The two `unsupported_stateful_ensemble` sites
  (`:212-221` input-side, `:265-275` output-side) are **distinct return sites that share one cause** — see the
  table below and T2's red-first requirement that both are independently proven.
  Exact mapping (verified against this worktree's current line numbers):
  | Site | Today | New cause |
  |---|---|---|
  | `:119-126` | `model_not_found` | `MODEL_NOT_FOUND` |
  | `:146-155` | `nwp.insufficient_coverage` | `INSUFFICIENT_COVERAGE` |
  | `:160-166` | `run_station_forecast.no_active_artifact` | `NO_ARTIFACT` |
  | `:174-185` | `run_station_forecast.warm_up_load_failed` | `WARM_UP_LOAD_FAILED` |
  | `:212-221` | `run_station_forecast.unsupported_stateful_ensemble` (input-side, `reject_prior_state_for_fanout`) | `UNSUPPORTED_STATEFUL_ENSEMBLE` |
  | `:251-258` | `run_station_forecast.predict_failed` | `PREDICT_FAILED` |
  | `:265-275` | `run_station_forecast.unsupported_stateful_ensemble` (output-side, `reject_stateful_ensemble_states`) | `UNSUPPORTED_STATEFUL_ENSEMBLE` |
  | `:303-310` | `run_station_forecast.qc_failed` | `QC_FAILED` |
  Both `unsupported_stateful_ensemble` call sites map to the same cause (as today, both share one log event name
  and one message prefix) — the *log* event stays the discriminant for "which guard fired"; `AssignmentFailureCause`
  only needs to distinguish failure *kinds* the fallback/caller logic cares about, and input-side vs. output-side
  stateful-ensemble rejection is not a distinction any current or Phase-3 consumer needs to make.
- **D3 — `run_all_station_forecasts` dispatches on the outcome via `match`, not `isinstance`.** Replaces
  `:419-424`'s `if isinstance(outcome, StationForecastResult): ... else: failed_models[...] = outcome` with:
  ```python
  match outcome:
      case AssignmentSuccess(result=result):
          results[assignment.model_id] = result
          if primary_model_id is None:
              primary_model_id = assignment.model_id
      case AssignmentFailure() as failure:
          failed_models[assignment.model_id] = failure
  ```
  Pattern matching per `CLAUDE.md`'s "Example Pattern Matching (Python 3.10+)" convention; behaviourally identical
  to today's `isinstance` branch — the loop body, priority bookkeeping, and `primary_model_id`
  first-success-wins logic (`:396-424`) are **untouched**.
- **D4 — `MultiModelForecastResult.failed_models: dict[ModelId, AssignmentFailure]`.** Field type change only, at
  `:76`. `combinable_results` (`:78-82`) is unaffected — it filters `self.results`, never `self.failed_models`.
  `run_station_forecast`'s wrapper (`:435-480`) is unaffected in signature and behaviour — it still returns
  `StationForecastResult | None` (unpacking `multi.results[multi.primary_model_id]` at `:480`, which is now a plain
  `StationForecastResult` since `AssignmentSuccess.result` is unwrapped inside `run_all_station_forecasts`'s `match`
  before it ever reaches `results[...]`).
- **D5 — reconcile with the spec's existing placeholder `AssignmentFailureCause` (3 variants): land the 7-variant
  orchestration taxonomy now, but do NOT erase the spec's forward FI distinction.**
  `docs/spec/types-and-protocols.md:3012-3016` already sketches an `AssignmentFailureCause` with three coarse
  buckets (`MISSING_CONTEXT`, `MODEL_FAILURE`, `UNEXPECTED_EXCEPTION`) as part of the design doc's forward-looking
  "FI failure semantics" section (`forecast-cycle-redesign.md:220-226`) — that section is explicitly about the
  **FI-boundary** distinction (track/context missing vs. a returned `ModelFailure` vs. an unexpected exception),
  co-designed with the FI-adherence mandate (`CLAUDE.md` "ForecastInterface Adherence (MANDATORY)": a model
  RETURNS `ModelFailure`, never raises; `ModelFailure.cause` is preserved structurally, not flattened to text).
  That distinction is **not observable in code today, independent of this phase**: the FI adapter's
  `_output_from_result` already flattens a returned `ModelFailure` into a raised `ModelOutputError` string
  (`adapters/forecast_interface.py:369`, `f"ForecastInterface model failure: {result.cause.name}: {result.message}"`),
  and `_run_single_model`'s `except Exception` around `predict` (`:251`) cannot tell that flattened-`ModelFailure`
  string apart from a genuinely unexpected exception. So the MODEL_FAILURE-vs-UNEXPECTED_EXCEPTION split is already
  unrealized *in code* pre-Phase-2 — Phase 2 does not newly erase it there. The risk this D5 addresses is erasing it
  from the **spec**, where it is still the documented forward target.
  - **What T3 does to the spec (both locations, see Delivers/T3):** at `:3012-3016`, T3 does **not** delete or
    overwrite the 3-variant `MISSING_CONTEXT`/`MODEL_FAILURE`/`UNEXPECTED_EXCEPTION` sketch — it keeps that sketch,
    labelled explicitly as the **forward FI-boundary target taxonomy** (unrealized until an FI-adapter follow-on
    lands, see below), and adds the landed 7-variant `AssignmentFailureCause` (D1) alongside it, labelled as
    **Phase 2's current orchestration-level realization**. At `:3609`,
    `MultiModelForecastResult.failed_models`'s value type is updated from `str` to `AssignmentFailure` to match the
    landed code.
  - **Reconciliation path, stated plainly (not a clean monotonic-addition story):** every one of this phase's seven
    variants is, at the FI-boundary level of abstraction, either "model not called" (`MODEL_NOT_FOUND`,
    `INSUFFICIENT_COVERAGE`, `NO_ARTIFACT`, `WARM_UP_LOAD_FAILED`, `UNSUPPORTED_STATEFUL_ENSEMBLE` — all
    orchestration rejects before or around `predict`) or the SAP3 backstop / a graceful post-`predict` reject
    (`PREDICT_FAILED`, `QC_FAILED`). `TRACK_UNAVAILABLE` (Phase 3's realization of `MISSING_CONTEXT`) is a clean
    **addition** to the enum. `PREDICT_FAILED` is **not** — it currently conflates the spec's `MODEL_FAILURE` and
    `UNEXPECTED_EXCEPTION` buckets, and reconciling it requires a **later split** (`PREDICT_FAILED` →
    `MODEL_FAILURE` | `UNEXPECTED_EXCEPTION`), which is an enum **change**, not a pure addition, and depends on a
    separate FI-adapter-boundary follow-on emitting a typed signal instead of flattening `ModelFailure` to text.
    This phase does not attempt that follow-on (Non-goals) — see **Open items** for the resulting scope question,
    which is an owner decision, not settled by this D5.
- **D6 — fallback invariant, explicitly unchanged and now type-visible.** The loop in `run_all_station_forecasts`
  (`:396-424`) is untouched structurally: it iterates `sorted_assignments` (priority order, `:384`) exactly once,
  records every failure in `failed_models` keyed by `model_id`, and sets `primary_model_id` to the **first**
  assignment whose outcome is `AssignmentSuccess` — never re-evaluated, never reset by a later failure. A station
  "fails" only when `primary_model_id is None` after the loop (`run_station_forecast.py:475-479`;
  `run_forecast_cycle.py:2194,2196` PRIMARY-mode dark-station recording; combination-mode is symmetric). This is the
  literal "fallback chain intact; a missing context ≠ a dead station" invariant the parent design names for this
  phase (`forecast-cycle-redesign.md:272-273`) — Phase 2 does not change **when** the chain advances, only **what
  shape** a per-assignment failure is recorded in. This is also why the change is a pure refactor of the failure
  *channel*, not a behaviour change: every `AssignmentFailure` this phase produces is produced at exactly the same
  point in exactly the same control flow as today's `str`.

## Phases

- **T1 — new types + runner wiring (types + `_run_single_model` + `run_all_station_forecasts` +
  `MultiModelForecastResult`, landed together).**
  - **In scope:** add `AssignmentFailureCause`, `AssignmentSuccess`, `AssignmentFailure`, `AssignmentOutcome` to
    `services/run_station_forecast.py` (D1); change `_run_single_model`'s return type and all nine return sites
    (eight failures + one success, D2); change `run_all_station_forecasts`'s dispatch to `match` (D3); change
    `MultiModelForecastResult.failed_models`'s type (D4). Types + wiring land in one task — unlike Plan 148's
    `prior_state` removal (which was deliberately sequenced across two tasks to avoid an intentionally-broken
    intermediate for a field with existing readers), there is no such hazard here: the outcome type has no consumer
    until the runner is wired, so there is nothing to keep green mid-way, and splitting them would only add an
    artificial gate with an unusable intermediate state (a `match` statement against a type nothing produces yet
    does not typecheck).
  - **Out of scope:** any test-file changes (T2); docs (T3); anything under `flows/` (confirmed no call-site change
    needed, Non-goals above).
  - **Red-first:** the tests below are written FIRST — against `_run_single_model -> StationForecastResult | str`
    they fail (attribute access `.cause` on a `str` raises `AttributeError`; `isinstance(x, AssignmentFailure)` is
    `False` for a `str`) — then T1's implementation makes them pass. New tests, migrating existing string-substring
    assertions is T2's job (below), but T1 must supply at minimum:
    1. A single new unit test asserting `_run_single_model` returns `AssignmentFailure(cause=AssignmentFailureCause.NO_ARTIFACT, ...)` for an unseeded artifact store (today only exercised indirectly via the `None`-returning wrapper, `test_returns_none_when_no_artifact:579`) — proves the type lands correctly for at least one previously wrapper-only path, catching a mis-wired `cause` before T2's broader migration.
    2. A single new unit test asserting a successful assignment's outcome is `AssignmentSuccess(result=<the StationForecastResult>)` via `isinstance`/`match` — proves the success path is wrapped, not just the failure path.
  - **Gate:** `uv run pytest tests/unit/services/test_run_station_forecast.py -k "no_artifact or single_model_returns_result" -q` (the two new tests above; the rest of that file still targets the old `str` shape until T2 — this is the one intentionally-red-then-green slice) **and** `uv run pyright` (the type definitions and signature changes typecheck standalone).

- **T2 — migrate every consumer of the old `str`/`isinstance` shape + red-first taxonomy-completeness tests.**
  - **In scope:** migrate the 13 grepped assertion sites in `tests/unit/services/test_run_station_forecast.py`
    (`:684,736,772,773,949,950,1100,1101,1473,1515,1516,1550,1551`) from `"<substring>" in result.failed_models[mid]`
    / `mid in result.failed_models` to `result.failed_models[mid].cause is AssignmentFailureCause.<X>` (adding
    `.detail` substring assertions only where the exact message content is itself the thing under test, e.g.
    `:950,1101` which assert on `coverage.detail`'s content, not just that coverage was insufficient). Add red-first
    tests proving the three causes today's suite exercises **only** via wrapper-`None`/bare-membership, now
    round-trip a correctly-typed `AssignmentFailure`:
    - `MODEL_NOT_FOUND` via `run_all_station_forecasts` directly (today only `test_returns_none_when_model_not_in_registry:604` exercises this, through the `None`-returning wrapper — add a direct `run_all_station_forecasts` call asserting `result.failed_models[mid] == AssignmentFailure(cause=AssignmentFailureCause.MODEL_NOT_FOUND, detail=...)`-shaped, via `.cause`).
    - `QC_FAILED` via `run_all_station_forecasts` directly, asserting `.cause is AssignmentFailureCause.QC_FAILED` for the failing-QC assignment in `test_qc_failed_ensemble_falls_through_to_next_model:444` (today that test only asserts the *fallback* succeeded, not the failed assignment's recorded cause).
    - **Both `UNSUPPORTED_STATEFUL_ENSEMBLE` return sites, proven independently** (this is the pair the D2 table
      keeps as two distinct sites sharing one cause — a test suite that only ever exercises one guard would not
      catch a mis-wired `cause` on the other): (a) the **output-side** guard (`:265-275`,
      `reject_stateful_ensemble_states`) — `test_ensemble_output_side_reject_guard_is_assignment_local:1435` already
      calls `run_all_station_forecasts` and asserts `_MODEL_ID_B in result.failed_models` (`:1473`, in T2's grepped
      migration list) — migrate that assertion to `result.failed_models[_MODEL_ID_B].cause is
      AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE`. (b) the **input-side** guard (`:212-221`,
      `reject_prior_state_for_fanout`) — `test_ensemble_input_side...` (`~:1400`) today only calls the
      `run_station_forecast` **wrapper** and asserts `result is not None` plus the log event — it never reaches
      `.failed_models`, so it is **not** in T2's grepped migration list and would not otherwise be touched. Add a
      new/adapted test that calls `run_all_station_forecasts` **directly** for this same input-side scenario and
      asserts `result.failed_models[<the rejected model>].cause is
      AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE` — the same "wrapper-only today" pattern T1 already
      calls out for `NO_ARTIFACT`.
    - **Fallback-invariant regression, explicit:** a station whose primary assignment fails with **each** of the
      seven causes in turn (parametrized) still produces a successful forecast from the next-priority assignment,
      and `failed_models[primary_id].cause` is exactly the expected variant — this is the acceptance criterion
      the parent design names ("a missing context ≠ a dead station" — Phase 2's analogue, since there is no track
      yet, is "a structured per-assignment failure ≠ a dead station", proven per-cause). This parametrized test
      exercises one guard per `UNSUPPORTED_STATEFUL_ENSEMBLE` case; the two dedicated tests above are what prove
      **both** of that cause's return sites independently — between the two, all eight failure return sites (and
      the one success site, T1) have a direct per-site assertion.
  - **Out of scope:** any further production-code change (T1 already lands the types/wiring); docs (T3).
  - **Gate:** `uv run pytest tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_station_forecast_fanout.py tests/unit/flows/test_run_forecast_cycle.py tests/integration/test_e2e_pipeline.py -q` **and** `uv run pyright` (ratchet — catches any un-migrated call site the grep above missed, same backstop pattern as Plan 148 T2 item 7) **and** `uv run ruff check`.

- **T3 — docs + final full-suite gate.**
  - **In scope (docs only), both `docs/spec/types-and-protocols.md` locations grepped and confirmed
    (`grep -n "failed_models\|AssignmentFailureCause" docs/spec/types-and-protocols.md`):**
    mark Phase 2 done in `docs/design/forecast-cycle-redesign.md`'s "Build sequence" item 2;
    1. **`:3012-3016`** (forward-looking "FI failure semantics" sketch) — per D5, do **not** delete the existing
       3-variant `MISSING_CONTEXT`/`MODEL_FAILURE`/`UNEXPECTED_EXCEPTION` sketch; relabel it explicitly as the
       **forward FI-boundary target taxonomy** (not yet realized — depends on a future FI-adapter follow-on), and
       add the landed 7-variant `AssignmentFailureCause` + `AssignmentSuccess`/`AssignmentFailure`/`AssignmentOutcome`
       shapes (D1, service-local in `services/run_station_forecast.py`) alongside it as **Phase 2's current
       orchestration-level realization**, with the explicit forward note that Phase 3 adds a `TRACK_UNAVAILABLE`
       variant to this same enum, and that `PREDICT_FAILED` will later **split** into `MODEL_FAILURE` |
       `UNEXPECTED_EXCEPTION` when that FI-adapter follow-on lands (not a pure addition — state this plainly, per
       D5).
    2. **`:3609`** (authoritative `MultiModelForecastResult` shape, "Multi-model combination types and services"
       section) — change `failed_models: dict[ModelId, str]  # model_id → error message` to
       `failed_models: dict[ModelId, AssignmentFailure]  # model_id → structured failure (cause + detail)`,
       matching D4's landed field type. This is the section a reader of `MultiModelForecastResult`'s spec entry
       actually lands on; leaving it saying `str` after T1/T2 land would make the spec self-contradictory.
    Also check whether `docs/touchpoint-maps.md`'s "Forecast cycle / assignment selection" map (`:195-207`) needs a
    one-line pointer update to the new outcome type (only if an existing bullet names the old `str`/`isinstance`
    shape — if not, no edit needed, per the touchpoint map's fitness test in `docs/workflow.md` § Right-sizing: only
    add what a task-context-packet reader needs to go read). No `docs/standards/logging.md` change — no log event
    name changes (Non-goals).
  - **Out of scope:** any code change.
  - **Final gate (whole repo):** `uv run pytest -q` (full suite) **and** `uv run ruff check` **and** `uv run pyright`
    (ratchet).

## Phase dependency graph
```json
{
  "phases": [
    {
      "id": "T1",
      "tasks": ["T1"],
      "parallel": false
    },
    {
      "id": "T2",
      "tasks": ["T2"],
      "parallel": false,
      "depends_on": ["T1"]
    },
    {
      "id": "T3",
      "tasks": ["T3"],
      "parallel": false,
      "depends_on": ["T2"]
    }
  ]
}
```
Strictly sequential: T1 → T2 → T3.

## Dependencies
- **Plan 148** (`feat/plan-148-modelruncontext`, PR #139) — this plan's runner (`_run_single_model`,
  `run_all_station_forecasts`) is the post-148 code (`ModelRunContext`-consuming, per-assignment warm-up read
  already wired). **Not yet on `main`** — see Status.
- `docs/design/forecast-cycle-redesign.md` — the parent architecture; Build sequence item 2 is this plan's exact
  scope statement.

## Non-goals
- `ForcingTrackKey`, per-track resolution, `CandidateFetchResult`/`TrackFetchResult`/`StationTrackOutcome`, exact-51
  completeness, per-assignment input assembly, superset removal — all Phase 3/4.
- A `TRACK_UNAVAILABLE`/`MISSING_CONTEXT` failure cause — there is no track concept yet; Phase 3 adds it to the
  enum this phase lands (D5).
- **Phase 2 is a pure refactor of the failure channel's shape — it does NOT touch the FI adapter
  (`adapters/forecast_interface.py`) and does NOT attempt to preserve the spec's `MODEL_FAILURE` vs.
  `UNEXPECTED_EXCEPTION` distinction in code.** Distinguishing a returned FI `ModelFailure` from an unexpected
  exception inside `PREDICT_FAILED`'s bare `except Exception` (`:251-258`) requires the FI adapter to stop
  flattening a returned `ModelFailure` into a raised `ModelOutputError` string (`_output_from_result`,
  `adapters/forecast_interface.py:369`) and instead surface it as a typed signal `_run_single_model` can catch
  separately — that is FI-adapter-boundary work, out of scope here (see D5 and Open items). It is not required for
  Phase 3 to proceed (Phase 3's `MISSING_CONTEXT`/`TRACK_UNAVAILABLE` case is orchestrator-local, resolved *before*
  `predict` is ever called — it does not need `PREDICT_FAILED` to be split first).
- Any GROUP-path change — `run_group_forecast.py` does not call `_run_single_model`/`run_all_station_forecasts` and
  is untouched.
- Any write-side / state-persistence change (still Plan 148's named deferred follow-on, unrelated to this phase).

## Open items
- **OWNER DECISION — blocks READY. Failure-taxonomy shape:** should Phase 2 (a) land the 7 concrete gate-causes
  now with `PREDICT_FAILED` conflating FI-`ModelFailure` and unexpected-exception, splitting later when an
  FI-adapter follow-on emits a typed signal (this plan's current direction — pure refactor, minimal scope, but a
  later enum SPLIT); or (b) pull the FI-adapter typed-`ModelFailure` signal into Phase 2 so `MODEL_FAILURE` vs.
  `UNEXPECTED_EXCEPTION` are distinct from the start (larger scope, touches the MANDATORY FI-adherence boundary, no
  later split); or (c) keep only the spec's 3 coarse variants and NOT introduce the 7 concrete ones. Recommendation:
  (a) — keeps Phase 2 a behaviour-preserving refactor and defers FI-boundary work to a focused follow-on; the later
  split is a small, well-contained enum change. Owner to ratify before READY.
- D5's reconciliation of the spec's 3-variant sketch with this phase's 7-variant landed enum (kept side-by-side,
  not one replacing the other, per the owner-decision item above) is otherwise settled for direction (a); flagged
  here rather than left as a silent TBD.
- **Gate contingent on #139.** If Plan 148's PR #139 changes line numbers before merging to `main` (e.g. a review
  round shifts `_run_single_model`'s body), re-verify this plan's D2 table and D6 line citations against the
  merged `main` state before `/implement` runs — the citations here are pinned to worktree commit `d11e20e`.
