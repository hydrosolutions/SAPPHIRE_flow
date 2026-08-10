---
status: DRAFT
created: 2026-08-10
plan: 150
title: Forecast-cycle redesign Phase 2 — per-assignment outcome SHAPE (structured fallback channel)
scope: The outcome-SHAPE sub-slice of the forecast-cycle redesign (docs/design/forecast-cycle-redesign.md). Migrate the station-cycle runner (_run_single_model / run_all_station_forecasts / MultiModelForecastResult) from today's `StationForecastResult | str` return + `failed_models: dict[ModelId, str]` to an ASSIGNMENT-LEVEL discriminated outcome (AssignmentSuccess | AssignmentFailure, with an assignment-level AssignmentFailureCause enum holding today's concrete causes and extensible by Phase 3), AND close one latent fallback-invariant gap with a loop-level backstop so an unexpected exception in a lower-priority assignment can no longer darken a station whose higher-priority assignment already succeeded. This is a pure failure-channel refactor (behaviour-preserving except the one named backstop delta). It does NOT complete redesign build-sequence item 2 on its own — the FI-typed `ModelFailure`-signal preservation and the runner's `ModelRunContext`-consumption seam are explicit named follow-ons (option A). Still one cycle: no ForcingTrackKey, no per-track resolution, no per-assignment input assembly, no superset removal.
depends_on: [148]
blocks: []
supersedes: []
---

# Plan 150 — Forecast-cycle redesign Phase 2: per-assignment outcome SHAPE

## Owner scope decision (LOCKED 2026-08-10) — option A

**Phase 2 is the outcome-SHAPE sub-slice only.** After an independent plan review (Claude + Codex) escalated with
blockers/majors, the owner locked **option A**: keep Phase 2 a **pure failure-channel refactor** — replace the
untyped `str` failure channel with a structured, discriminated, **assignment-level** outcome, and repair one latent
fallback-invariant gap (the loop-level backstop). Do **not** expand it to preserve the FI typed `ModelFailure`
signal, and do **not** migrate the runner's `ModelRunContext`-**consumption** seam. Those are **explicit, named
follow-ons** (both below):

- **Phase 2-FI follow-on** — stop the FI adapter flattening a returned `ModelFailure` into a raised
  `ModelOutputError`, so the runner can split `PREDICT_FAILED` into `MODEL_FAILURE | UNEXPECTED_EXCEPTION` at the FI
  boundary. Required before the redesign's failure contract is *fully* satisfied; **out of scope here** (Non-goals).
- **Context-consumption seam** — make `_run_single_model` **receive** a per-assignment `ModelRunContext` (and, for
  Phase 3, have missing context determined *before* model execution) instead of building the context internally
  from raw shared inputs. Belongs to **Phase 3** (or a dedicated seam follow-on), **not Phase 2**.

**Consequence for the build sequence:** redesign build-sequence item 2 is delivered by **Phase 2 (outcome shape) +
the Phase 2-FI follow-on + the context-consumption seam (Phase 3)** — **not by Phase 2 alone.** Phase 2 is
behaviour-preserving on every currently-green path, plus the one intended backstop delta (D6). This keeps Phase 2
consistent with the ratified taxonomy decision (a) in Open items.

## Status
**DRAFT — outcome-SHAPE sub-slice of the forecast-cycle redesign** (`docs/design/forecast-cycle-redesign.md`,
"Build sequence" item 2, `:272`: *"Migrate the station runner to consume per-assignment `ModelRunContext`,
returning a per-assignment success/failure result (fallback chain intact; a missing context ≠ a dead station). Still
one cycle."* — Phase 2 delivers the **success/failure result shape** half of that item; see the Owner scope
decision above and the design-doc reconciliation note the plan adds at `:272`).

**Plan 148 (Phase 1, `ModelRunContext`) is complete and on `main`** (`fa14b9a`, PR #139 —
`feat(forecast-cycle): Plan 148 Phase 1 — ModelRunContext + per-assignment warm-up state (READ side)`). The post-148
code — `ModelRunContext` / `WarmUpState` / `load_warm_up_state` (`services/operational_inputs.py`) and the
per-assignment warm-up read inside `_run_single_model` (`services/run_station_forecast.py`) — is on `main`. Every
`file:line` citation in this document is grounded against **current `main`** (`bb7e3a9` at revision time). Source
citations from 148 are stable (148's files squash-merged unchanged, e.g. `_run_single_model ->
StationForecastResult | str` at `run_station_forecast.py:118`); only spec line numbers drifted (Plan 147 shifted
them: `MultiModelForecastResult.failed_models` is now at `types-and-protocols.md:3639`, the forward
`AssignmentFailureCause` FI-boundary sketch at `:3042`). All spec citations below use the post-drift `main` numbers;
`/plan` re-verifies before READY.

## Problem
Today `_run_single_model` (`src/sapphire_flow/services/run_station_forecast.py:98,118`) returns
`StationForecastResult | str` — a bare string standing in for "this assignment failed, here's why"
(`:126,155,166,185,221,258,275,310`). `run_all_station_forecasts` dispatches on `isinstance(outcome,
StationForecastResult)` (`:419-424`) and stores the string verbatim in
`MultiModelForecastResult.failed_models: dict[ModelId, str]` (`:76`). This has three problems the redesign's
Phase 3 depends on fixing first:

1. **No structure to extend.** Phase 3 needs to add new, *distinct* assignment-local failures — "this assignment's
   track/context is unavailable" (the design's `MISSING_CONTEXT` / `TRACK_UNAVAILABLE`,
   `forecast-cycle-redesign.md:220`) — that must be **just as assignment-local** as today's failure paths and must
   **not** darken a station whose higher-priority assignment already succeeded ("a missing context ≠ a dead
   station", `forecast-cycle-redesign.md:273`). A bare `str` return gives Phase 3 nowhere principled to plug those
   in except string-formatting another sentence — callers and tests that need to distinguish failure *kinds* can
   only pattern-match on message text.
2. **Untyped failure channel violates the repo's type-driven convention.** The domain has several already-distinct,
   already-enumerable failure kinds represented as an unstructured `str` — exactly the invalid-states-representable
   problem the repo's Type-Driven-Development rules exist to prevent: *"Invalid states should be unrepresentable"*
   and *"Parse, don't validate ... internal functions never accept raw primitives when a domain type exists"*
   (`CLAUDE.md` § Type Driven Development). Concretely, seven distinct anticipated failure kinds
   (`model_not_found`, `insufficient NWP coverage`, `no_active_artifact`, `warm-up state load failed`, `unsupported
   stateful ensemble` ×2 call sites, `predict failed`, `QC failed for parameter X`) are currently indistinguishable
   except by parsing the message string.
3. **A latent fallback-invariant gap the `str` shape hides.** The loop in `run_all_station_forecasts`
   (`:396-424`) calls `_run_single_model` with **no backstop try/except** (`:398`). `_run_single_model` returns a
   `str` for its eight *anticipated* failure return sites, but an **unanticipated** exception raised outside its two
   narrow guarded regions (the warm-up `try` at `:174-185` and the deserialize/predict `try` ending at `:258`) —
   e.g. from `model.data_requirements`/`assess_future_coverage` (`:137-146`),
   `artifact_store.fetch_active_artifact_for_station` (`:157`), a non-`ModelOutputError` raise from the reject
   guards, QC (`:280-310`), or `OperationalForecast`/`StationForecastResult` construction (`:330-361`) — propagates
   straight out of `run_all_station_forecasts`. It is **not flow-fatal**: the flow's per-station outer handler
   (`flows/run_forecast_cycle.py:2285`) catches it, logs `forecast_cycle.station_forecast_failed`, appends an
   error, increments `stations_failed`, and `continue`s. But the containment is **coarse — it darkens the WHOLE
   station** and **discards any already-recorded higher-priority success** (that success lives only in the runner's
   local `results` dict and is never persisted, because persistence runs *after* the runner returns, `:2214-2283` /
   `:2142-2166`). So an unexpected crash in a *lower-priority* assignment needlessly darkens a station whose
   *higher-priority* assignment already succeeded — silently violating the invariant the parent design names for
   this phase ("a missing context ≠ a dead station"). Phase 2 closes this with a loop-level backstop (D3/D6): a
   small, **intended** behaviour change, not pure byte-identical preservation.

## What Phase 2 delivers (and deliberately does NOT)
**Delivers:**
- A discriminated per-assignment outcome type, `AssignmentOutcome = AssignmentSuccess | AssignmentFailure`, and a
  single **ASSIGNMENT-LEVEL** `AssignmentFailureCause` enum covering today's seven concrete anticipated failure
  kinds **plus** an eighth `UNEXPECTED_EXCEPTION` produced only by the new loop-level backstop (D3). The enum is
  **assignment-level by design** so Phase 3 can add `MISSING_CONTEXT`/`TRACK_UNAVAILABLE` as **new enum members**
  with **no rework of the runner or the outcome type** — just new members + new production sites (see D1/D5). It
  reconciles with the spec's reserved `AssignmentFailureCause` FI-boundary sketch per D5 (side-by-side: landed
  concrete enum + forward FI-boundary target grouping).
- `_run_single_model` returns `AssignmentOutcome` instead of `StationForecastResult | str` (its eight anticipated
  failure return sites become `AssignmentFailure`, its success becomes `AssignmentSuccess`).
- A **loop-level backstop** in `run_all_station_forecasts`: the `_run_single_model` call is wrapped so an
  unanticipated exception is caught, logged at **ERROR** (D3/D6, MAJOR-3), recorded as
  `AssignmentFailure(cause=UNEXPECTED_EXCEPTION, ...)`, and the chain advances — closing the fallback-invariant gap
  in Problem #3 (a lower-priority crash no longer darkens a station whose higher-priority assignment succeeded).
  This aligns with the FI-adherence mandate's "SAP3's except-and-return is only a backstop for *unanticipated*
  bugs" (`CLAUDE.md` § ForecastInterface Adherence).
- `MultiModelForecastResult.failed_models: dict[ModelId, AssignmentFailure]` instead of `dict[ModelId, str]`.
- Every existing test that reads `.failed_models` or asserts on a failure-reason substring, migrated to assert on
  `.cause` (and `.detail` where the message content itself matters).
- New red-first tests proving the failure kinds today exercised **only** via the `run_station_forecast` wrapper's
  `None` return (`MODEL_NOT_FOUND`, `NO_ARTIFACT`) or via bare membership checks (`QC_FAILED`,
  `UNSUPPORTED_STATEFUL_ENSEMBLE` — both return sites) now round-trip a correctly-typed `AssignmentFailure` into
  `failed_models`; **and** a regression proving the backstop invariant (runner-level and flow-level, see T1/T2).
- A spec update at **both** places `failed_models`/`AssignmentFailureCause` appear in
  `docs/spec/types-and-protocols.md`, per D5: the forward FI-boundary three-bucket `AssignmentFailureCause` sketch
  (`:3042`) is **kept and labelled as the forward target**, and the landed concrete assignment-level enum is placed
  **alongside** it; the authoritative `MultiModelForecastResult.failed_models` value type (`:3639`) changes from
  `str` to `AssignmentFailure`.
- Doc bookkeeping (T2): a Plan 150 entry in `docs/plans/README.md`; an **UNCONDITIONAL** `docs/standards/logging.md`
  entry for the one new backstop event (the canonical Flow-1 table at `:266` already enumerates runner failure
  events); and Plan 148's status reconciled (archive action specified — see T2).

**Deliberately does NOT (option A boundaries):**
- **Complete redesign build-item 2 on its own.** Phase 2 delivers the *outcome-shape* half only. Build-item 2 is
  delivered by **Phase 2 + the Phase 2-FI follow-on + the context-consumption seam (Phase 3)** — see the Owner
  scope decision and the design-doc reconciliation note (T2 edits `forecast-cycle-redesign.md:272`).
- **Migrate the `ModelRunContext`-consumption seam.** `_run_single_model` still **takes the raw shared inputs and
  builds `ModelRunContext` internally** (`run_station_forecast.py:187-196`); Phase 2 only changes its **return**
  shape/dispatch/`failed_models`, not how it *receives* context. Making the runner **receive** a per-assignment
  `ModelRunContext` (and, in Phase 3, determine missing context *before* execution) is Phase 3 / the seam follow-on
  (MAJOR-1).
- **Preserve the FI typed `ModelFailure` signal.** A returned FI `ModelFailure` is still flattened by the adapter
  into a raised `ModelOutputError` (`adapters/forecast_interface.py:369`) and caught by `_run_single_model`'s
  narrow `except Exception` around `predict` (`:224-258`) as `PREDICT_FAILED`. This flattening is **pre-existing on
  `main`** — Phase 2 neither introduces nor fixes it. `PREDICT_FAILED` therefore **conflates** a returned FI
  `ModelFailure` with an unexpected exception *inside* `predict` **for now** (ratified option a). Splitting it into
  `MODEL_FAILURE | UNEXPECTED_EXCEPTION` requires the **Phase 2-FI follow-on** (see Non-goals + the "known
  FI-adherence gap" note below). Out of scope here; Phase 2 changes **no** FI-adapter behaviour.
- Touch track resolution, `ForcingTrackKey`, per-track cycle resolution, per-station availability
  (`StationTrackOutcome`/`TrackFetchResult`), per-assignment input assembly, or drop the station superset — Phase
  3/4.
- Add a `MISSING_CONTEXT`/`TRACK_UNAVAILABLE` failure cause — there is no track concept yet for a context to be
  missing *from*. Phase 3 adds those enum members (D5 shows exactly how they slot in with no runner/type rework).
- Change what a *successful* forecast produces, its QC, its provenance, or its persistence. Every currently-green
  code path that reaches `AssignmentSuccess` carries the exact same `StationForecastResult` it carries today.
- Change any log event name or level for the eight anticipated paths — the eight `log.warning(...)` call sites keep
  their existing event names and WARNING level; only the **return value** built after each log call changes shape.
  The backstop (D3) adds **one** new event, `run_station_forecast.unexpected_exception`, at **ERROR** (the only new
  log event; ERROR because it is genuinely unanticipated — repo convention, and it feeds a flow that already logs
  unanticipated escapes at ERROR, e.g. `nwp.fetch_failed`, `nwp.unexpected_return_type`).
- Change `run_forecast_cycle.py`'s call shape. Grepped and confirmed
  (`flows/run_forecast_cycle.py:2173,2194,2215,2229,2280`): the combination branch reads
  `multi_result.primary_model_id`, `.results`, and `.combinable_results` — it **never reads `.failed_models`'s
  contents**. So this phase's value-type change to `failed_models` needs **zero** production call-site changes in
  the flow layer; only the runner module and its direct unit tests move for the shape change. (The backstop is
  *inside* `run_all_station_forecasts`; it does not change the flow's call shape — but it **does** change the flow's
  observable outcome on the crash path, which T2 covers with a flow-level regression test, MAJOR-2.)

### Known FI-adherence gap + scheduled follow-on (honest tracking, not a silent divergence)
The FI adapter's `_output_from_result` flattens a returned FI `ModelFailure` into a raised `ModelOutputError`
(`adapters/forecast_interface.py:369`); `_run_single_model`'s narrow `except Exception` around `predict`
(`:224-258`) then records it as `PREDICT_FAILED`, indistinguishable from a genuinely unexpected exception. **This
flattening exists on `main` today and Phase 2 does not touch it.** Per the FI-adherence mandate (`CLAUDE.md` §
ForecastInterface Adherence), an *anticipated* failure should surface as a **returned, structurally-typed**
`ModelFailure` (`FailureCause` preserved), not a flattened string. Closing that is the **Phase 2-FI follow-on** (a
focused FI-adapter change that stops the flattening and lets the runner split `PREDICT_FAILED` into FI
`MODEL_FAILURE | UNEXPECTED_EXCEPTION` at the boundary). It is **required before the redesign's failure contract is
fully satisfied**, but **out of scope here**. Recording it explicitly here is FI-adherence *tracking* — a named
plan to fix a pre-existing gap — not a new SAP3-side workaround.

## Design

- **D1 — ASSIGNMENT-LEVEL discriminated outcome type, service-local in `services/run_station_forecast.py`.** Placed
  next to `StationForecastResult`/`MultiModelForecastResult` (`run_station_forecast.py:60-82`) — its own module,
  not `services/operational_inputs.py`: unlike `ModelRunContext` (a Plan-148 input-side construct consumed across
  the assembler and the runner), this is the runner's own **output** type, with one producer boundary
  (`_run_single_model` plus the loop backstop) and one consumer (`run_all_station_forecasts`). Frozen, kw-only,
  slots, per the repo's default value-type convention:
  ```python
  class AssignmentFailureCause(Enum):
      # --- the seven concrete anticipated causes that exist TODAY ---
      MODEL_NOT_FOUND = auto()
      INSUFFICIENT_COVERAGE = auto()
      NO_ARTIFACT = auto()
      WARM_UP_LOAD_FAILED = auto()
      UNSUPPORTED_STATEFUL_ENSEMBLE = auto()   # shared by two return sites (D2)
      PREDICT_FAILED = auto()                  # conflates FI ModelFailure + unexpected-in-predict (option a; Phase 2-FI follow-on splits it)
      QC_FAILED = auto()
      # --- added by Phase 2's loop-level backstop (D3) only ---
      UNEXPECTED_EXCEPTION = auto()            # never returned by _run_single_model; produced by run_all_station_forecasts's backstop
      # --- Phase 3 will ADD (no runner/type rework — just new members + new production sites): ---
      #   MISSING_CONTEXT = auto()     # track/context absent -> model NOT called (assignment-local, expected)
      #   TRACK_UNAVAILABLE = auto()   # per-station availability at an accepted cycle (assignment-local)

  @dataclass(frozen=True, kw_only=True, slots=True)
  class AssignmentSuccess:
      result: StationForecastResult

  @dataclass(frozen=True, kw_only=True, slots=True)
  class AssignmentFailure:
      cause: AssignmentFailureCause
      detail: str

  AssignmentOutcome = AssignmentSuccess | AssignmentFailure
  ```
  **Why assignment-level, not gate-only (resolves the type-algebra blocker).** The enum is defined at the
  **assignment** level of abstraction — "why did *this assignment* fail" — precisely so Phase 3's assignment-level
  causes (`MISSING_CONTEXT`, `TRACK_UNAVAILABLE`) are **new members of the same enum**, entering
  `AssignmentFailure.cause` and `failed_models` with **zero** change to `AssignmentFailure`, `AssignmentOutcome`,
  `failed_models`'s type, or the `match` dispatch (D3). A *gate-only* enum (whose members were only the pre-`predict`
  gates) could not structurally hold `MISSING_CONTEXT`, forcing Phase 3 to rework the runner + types again —
  contradicting this phase's stated purpose. `detail` carries exactly the free-text half of today's return string
  (e.g. `f"insufficient NWP coverage: {coverage.detail}"` at `:155` becomes
  `AssignmentFailure(cause=AssignmentFailureCause.INSUFFICIENT_COVERAGE, detail=f"insufficient NWP coverage: {coverage.detail}")`
  — message text preserved verbatim in `detail`; `cause` is the new machine-checkable discriminant). A success
  wraps the existing, unchanged `StationForecastResult` — no field on `StationForecastResult` itself changes.
- **D2 — `_run_single_model` returns `AssignmentOutcome`, not `StationForecastResult | str`.** Every one of the
  eight `return f"..."` sites becomes `return AssignmentFailure(cause=..., detail=f"...")`, and the terminal
  `return StationForecastResult(...)` (`:354`) becomes `return AssignmentSuccess(result=StationForecastResult(...))`
  — nine return sites total (eight anticipated failures + one success). `_run_single_model` **never** returns
  `UNEXPECTED_EXCEPTION` — that cause is produced only by the loop backstop (D3). The two
  `unsupported_stateful_ensemble` sites (`:212-221` input-side, `:265-275` output-side) are **distinct return
  sites that share one cause** — see the table below and the red-first requirement that both are independently
  proven.
  Exact mapping (verified against current `main`):
  | Site | Today | New cause |
  |---|---|---|
  | `:119-126` | `model_not_found` | `MODEL_NOT_FOUND` |
  | `:146-155` | `nwp.insufficient_coverage` | `INSUFFICIENT_COVERAGE` |
  | `:160-166` | `run_station_forecast.no_active_artifact` | `NO_ARTIFACT` |
  | `:174-185` | `run_station_forecast.warm_up_load_failed` | `WARM_UP_LOAD_FAILED` |
  | `:212-221` | `run_station_forecast.unsupported_stateful_ensemble` (input-side, `reject_prior_state_for_fanout`) | `UNSUPPORTED_STATEFUL_ENSEMBLE` |
  | `:224-258` | `run_station_forecast.predict_failed` (narrow `except Exception` around deserialize/predict, return at `:258`) | `PREDICT_FAILED` |
  | `:265-275` | `run_station_forecast.unsupported_stateful_ensemble` (output-side, `reject_stateful_ensemble_states`) | `UNSUPPORTED_STATEFUL_ENSEMBLE` |
  | `:303-310` | `run_station_forecast.qc_failed` | `QC_FAILED` |
  Both `unsupported_stateful_ensemble` call sites map to the same cause (as today, both share one log event name and
  one message prefix) — the *log* event stays the discriminant for "which guard fired"; `AssignmentFailureCause`
  only needs to distinguish failure *kinds* the fallback/caller logic cares about, and input-side vs. output-side
  stateful-ensemble rejection is not a distinction any current or Phase-3 consumer needs.
- **D3 — `run_all_station_forecasts` dispatches via `match`, with a per-assignment backstop `try`.** Replaces
  `:419-424`'s `if isinstance(outcome, StationForecastResult): ... else: failed_models[...] = outcome`, and wraps
  the `_run_single_model` call (`:398-418`) so an unanticipated exception is caught **per assignment** rather than
  escaping the loop to the flow's coarse per-station handler:
  ```python
  try:
      outcome = _run_single_model(... )
  except Exception as exc:                       # backstop for UNANTICIPATED bugs only
      log.error(                                 # ERROR: genuinely unanticipated (MAJOR-3)
          "run_station_forecast.unexpected_exception",
          station_id=str(station_id),
          model_id=str(assignment.model_id),
          error=str(exc),
      )
      outcome = AssignmentFailure(
          cause=AssignmentFailureCause.UNEXPECTED_EXCEPTION,
          detail=f"unexpected error: {exc}",
      )
  match outcome:
      case AssignmentSuccess(result=result):
          results[assignment.model_id] = result
          if primary_model_id is None:
              primary_model_id = assignment.model_id
      case AssignmentFailure() as failure:
          failed_models[assignment.model_id] = failure
  ```
  Pattern matching per `CLAUDE.md`'s "Example Pattern Matching (Python 3.10+)" convention. The `match` arms are
  behaviourally identical to today's `isinstance` branch — the priority bookkeeping and `primary_model_id`
  first-success-wins logic (`:396-424`) are unchanged. The **new** part is the backstop `try`: it converts a
  previously station-darkening, success-discarding escape into an assignment-local `UNEXPECTED_EXCEPTION` failure
  that advances the chain. This is the one place Phase 2 changes runtime behaviour, and it does so **only** on a
  path that today escapes into the flow's coarse per-station containment (never on a currently-green path) — a
  targeted fix that makes the code match the invariant the design already names (see D6). It is the
  orchestration-level analogue of the FI mandate's "SAP3's except-and-return is only a backstop for unanticipated
  bugs".
- **D4 — `MultiModelForecastResult.failed_models: dict[ModelId, AssignmentFailure]`.** Field type change only, at
  `:76`. `combinable_results` (`:78-82`) is unaffected — it filters `self.results`, never `self.failed_models`.
  `run_station_forecast`'s wrapper (`:435-480`) is unaffected in signature and behaviour — it still returns
  `StationForecastResult | None` (unpacking `multi.results[multi.primary_model_id]` at `:480`, which is a plain
  `StationForecastResult` since `AssignmentSuccess.result` is unwrapped inside `run_all_station_forecasts`'s `match`
  before it ever reaches `results[...]`).
- **D5 — relationship to the spec's reserved `AssignmentFailureCause` FI-boundary sketch: one symbol, two views,
  both documented (side-by-side).** `docs/spec/types-and-protocols.md:3042` sketches an `AssignmentFailureCause`
  with three **coarse FI-boundary buckets** (`MISSING_CONTEXT`, `MODEL_FAILURE`, `UNEXPECTED_EXCEPTION`) — the
  design doc's forward "FI failure semantics" target (`forecast-cycle-redesign.md:220`). That coarse grouping is
  **not realized in code today** (independent of this phase): the FI adapter flattens `ModelFailure` into a raised
  `ModelOutputError` (`adapters/forecast_interface.py:369`), so `_run_single_model` cannot yet distinguish
  `MODEL_FAILURE` from an unexpected exception. Phase 2 lands the **concrete, assignment-level**
  `AssignmentFailureCause` (the 8 members in D1). Per the owner decision (option a), the spec keeps **both**,
  side-by-side, one **not** replacing the other:
  - the **landed concrete enum** (Plan 150, 8 members) — what is in code now; and
  - the **forward FI-boundary grouping** (the 3-bucket sketch) — the coarse target the concrete causes *roll up
    into* once the follow-ons land.
  The **mapping** between them (documented once, here): the concrete gate causes group at the FI boundary as
  "model not called before/around predict" (`MODEL_NOT_FOUND`, `INSUFFICIENT_COVERAGE`, `NO_ARTIFACT`,
  `WARM_UP_LOAD_FAILED`, `UNSUPPORTED_STATEFUL_ENSEMBLE`) or "SAP3 backstop / graceful post-`predict` reject"
  (`PREDICT_FAILED`, `QC_FAILED`, `UNEXPECTED_EXCEPTION`). Reaching the coarse 3-bucket view is purely **additive**:
  - **Phase 3** adds `MISSING_CONTEXT` and `TRACK_UNAVAILABLE` as **new members** of the *landed* enum. Concretely,
    a Phase-3 production site (context resolved *before* `predict`) does exactly:
    ```python
    # Phase 3 — a purely additive change to the SAME enum + a NEW production site:
    if track_context is None:                      # new, before the model is called
        return AssignmentFailure(
            cause=AssignmentFailureCause.MISSING_CONTEXT,   # new enum member
            detail=f"no track/context for {assignment.model_id} at cycle {cycle}",
        )
    ```
    No change to `AssignmentFailure`, `AssignmentOutcome`, `failed_models`'s type, or D3's `match` dispatch — the
    new value flows into `failed_models[model_id].cause` through the *unchanged* runner. **This is the concrete
    proof the type algebra is assignment-level, not gate-only.**
  - the **Phase 2-FI follow-on** later *splits* `PREDICT_FAILED` into FI `MODEL_FAILURE | UNEXPECTED_EXCEPTION` at
    the FI boundary (an FI-adapter change), and `AssignmentFailureCause.UNEXPECTED_EXCEPTION` (the loop backstop)
    maps 1:1 to the FI-side `UNEXPECTED_EXCEPTION` bucket.
- **D6 — fallback invariant: unchanged where it held today, and now *repaired* where it silently didn't.** The loop
  in `run_all_station_forecasts` (`:396-424`) still iterates `sorted_assignments` (priority order, `:384`) exactly
  once, records every failure in `failed_models` keyed by `model_id`, and sets `primary_model_id` to the **first**
  assignment whose outcome is `AssignmentSuccess` — never re-evaluated, never reset by a later failure. A station
  "fails" only when `primary_model_id is None` after the loop (`run_station_forecast.py:479`; the flow records it
  dark in **combination mode** at `run_forecast_cycle.py:2194,2196` — `if multi_result.primary_model_id is None`,
  the branch this plan's shape change touches — and symmetrically in **PRIMARY mode** at `:2122,2124` via the
  wrapper's `if fc_result is None`). This is the literal "fallback chain intact; a missing context ≠ a dead
  station" invariant the parent design names for this phase (`forecast-cycle-redesign.md:273`).
  - **Where it held today (untouched):** the eight anticipated `str` failures were already assignment-local; Phase 2
    changes only their *shape* (`str` → `AssignmentFailure`), not *when* the chain advances.
  - **Where it silently didn't (repaired — the one behaviour change, stated as a trade-off):** an *unanticipated*
    exception in a lower-priority assignment previously escaped `run_all_station_forecasts` to the flow's per-station
    outer handler (`run_forecast_cycle.py:2285`), which darkens the **whole station** (`stations_failed += 1`,
    `continue`) and discards the higher-priority success already in the runner's local `results` (never persisted,
    Problem #3). Phase 2's D3 backstop makes that path assignment-local: the chain advances, and the station
    **succeeds and persists** via the earlier higher-priority assignment. **Trade-off, stated plainly:** Phase 2 is
    behaviour-preserving on every currently-green path, but it is **not** a pure byte-identical refactor — it
    deliberately *changes* the observable outcome on the crash path (a previously-darkened station now succeeds).
    This is intentional and aligned with the redesign's "a failure ≠ a dead station / fallback intact"; it is the
    minimal change that lets the reviewer-requested regressions (runner-level in T1, flow-level in T2) pass, and it
    touches no currently-green path.

## Phases

- **T1 — types + runner wiring + backstop + ALL affected runner-unit tests, landed together (repo green at the
  gate).** This task lands the production change **and every test consumer of the old shape in the same task**, so
  no completed task leaves known failures (the per-task exit gate in `docs/workflow.md` requires full pytest +
  pyright after every subagent task; pyright's scope is `src` only — `pyrightconfig.json` — so it cannot backstop
  test-file consumers, which is exactly why the string-assertion migration must land in the same task as the type
  change, not a later one).
  - **In scope (production):** add `AssignmentFailureCause`, `AssignmentSuccess`, `AssignmentFailure`,
    `AssignmentOutcome` to `services/run_station_forecast.py` (D1); change `_run_single_model`'s return type and all
    nine return sites (eight anticipated failures + one success, D2); wrap the `_run_single_model` call in
    `run_all_station_forecasts` with the backstop `try` (log at **ERROR**) and switch dispatch to `match` (D3);
    change `MultiModelForecastResult.failed_models`'s type (D4).
  - **In scope (tests):** migrate the grepped assertion sites in `tests/unit/services/test_run_station_forecast.py`
    (`:736,772,773,949,950,1100,1101,1473,1515,1516,1550,1551`) from `"<substring>" in result.failed_models[mid]` /
    `mid in result.failed_models` to `result.failed_models[mid].cause is AssignmentFailureCause.<X>` (adding
    `.detail` substring assertions only where the exact message content is itself under test, e.g. the
    coverage-`detail` assertions). **Note `:684` is NOT in this list** — `test_all_models_succeed_returns_all_results`
    asserts `len(result.failed_models) == 0` (an *empty-map* assertion); it needs **no** per-entry `.cause` edit and
    stays as-is. All test changes land here so the **full suite is green at this task's gate** — there is no
    intermediate red state.
  - **Red-first (written before the production change; they fail against `-> StationForecastResult | str` because
    `.cause` on a `str` raises `AttributeError` / `isinstance(x, AssignmentFailure)` is `False`, then the impl makes
    them green):**
    1. `_run_single_model` returns `AssignmentFailure(cause=AssignmentFailureCause.NO_ARTIFACT, ...)` for an
       unseeded artifact store (today only exercised indirectly via the `None`-returning wrapper,
       `test_returns_none_when_no_artifact:579`).
    2. A successful assignment's outcome is `AssignmentSuccess(result=<the StationForecastResult>)` via
       `isinstance`/`match` — proves the success path is wrapped, not just failures.
    3. `MODEL_NOT_FOUND` via `run_all_station_forecasts` **directly** (today only
       `test_returns_none_when_model_not_in_registry:604` exercises it through the `None`-returning wrapper) —
       assert `result.failed_models[mid].cause is AssignmentFailureCause.MODEL_NOT_FOUND`.
    4. `QC_FAILED` via `run_all_station_forecasts` directly — assert `.cause is AssignmentFailureCause.QC_FAILED`
       for the failing-QC assignment in `test_qc_failed_ensemble_falls_through_to_next_model:444` (today that test
       only asserts the *fallback* succeeded, not the failed assignment's recorded cause).
    5. **Both `UNSUPPORTED_STATEFUL_ENSEMBLE` return sites, proven independently** (the D2 pair sharing one cause):
       (a) **output-side** (`:265-275`) — migrate
       `test_ensemble_output_side_reject_guard_is_assignment_local:1439`'s `_MODEL_ID_B in result.failed_models`
       (`:1473`) to `.cause is AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE`; (b) **input-side**
       (`:212-221`) — the current input-side test (`:1394`) calls only the wrapper and asserts `result is not None`
       + the log event (`:1435`), never reaching `.failed_models`; add a test that calls `run_all_station_forecasts`
       **directly** for the same input-side scenario and asserts
       `result.failed_models[<rejected model>].cause is AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE`.
    6. **Backstop / fallback-invariant regression, RUNNER level (the blocker-requested test):** a station whose
       higher-priority assignment **succeeds** and whose lower-priority assignment **raises an unexpected
       exception** (inject a store/model test double whose `_run_single_model`-reachable call raises **outside** the
       guarded regions — e.g. `artifact_store.fetch_active_artifact_for_station` raising for the lower-priority
       model) still returns the higher-priority success as `primary_model_id`, and records
       `failed_models[<lower model>].cause is AssignmentFailureCause.UNEXPECTED_EXCEPTION`. This test must **fail
       against the pre-backstop code** (the exception escapes `run_all_station_forecasts`) and pass after D3 —
       proving the backstop is load-bearing.
    7. **Per-cause fallback regression (parametrized):** a station whose primary assignment fails with **each** of
       the seven anticipated causes in turn still produces a successful forecast from the next-priority assignment,
       and `failed_models[primary_id].cause` is exactly the expected variant. (`UNSUPPORTED_STATEFUL_ENSEMBLE` uses
       one guard here; the two dedicated tests in (5) prove both of that cause's return sites; the backstop test in
       (6) proves `UNEXPECTED_EXCEPTION`. Between them, all eight anticipated return sites + the loop backstop + the
       one success site each have a direct per-site assertion.)
  - **Out of scope:** docs and the flow-level regression (T2); anything under `flows/` production code (confirmed no
    call-shape change needed, Non-goals).
  - **Gate (full repo — no partial/red slice):**
    `uv run pytest tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_station_forecast_fanout.py tests/unit/flows/test_run_forecast_cycle.py tests/integration/test_e2e_pipeline.py -q`
    **and** `uv run pytest -q` (full suite — nothing else may have gone red) **and** `uv run pyright` (ratchet —
    catches any un-migrated call site the grep missed) **and** `uv run ruff check`.

- **T2 — flow-level regression + docs + plan-index + final full-suite gate.**
  - **In scope (flow-level regression test, MAJOR-2):** add a test to `tests/unit/flows/test_run_forecast_cycle.py`
    proving the D3 backstop's **observable flow outcome**: for a station with two assignments where the
    higher-priority assignment **succeeds** and the lower-priority assignment **raises an unexpected exception**,
    the flow **persists the earlier success** (`forecast_store.store_forecast` called for the primary's forecasts)
    and does **NOT** take the station-failure path (`stations_failed` not incremented; the station counts as
    succeeded and is not recorded dark). Cover the **combination branch** explicitly (`:2173-2283`, the branch whose
    `failed_models` shape this plan changes) and/or the **PRIMARY branch** (`:2100-2166`, via the
    `run_station_forecast` wrapper). Against pre-D3 code the escape reaches the outer handler (`:2285`) and the
    station is darkened; after D3 the station succeeds and persists — so the test is load-bearing at the flow level
    too. (This test consumes only the new backstop behaviour; it lands in T2 because it is a flow-layer test, not a
    runner-unit test — no production flow code changes.)
  - **In scope (docs only), both `docs/spec/types-and-protocols.md` locations grepped and confirmed
    (`grep -n "failed_models\|AssignmentFailureCause" docs/spec/types-and-protocols.md` → `:3042`, `:3639`):**
    1. **`:3042`** (forward "FI failure semantics" sketch): **leave the 3-variant
       `MISSING_CONTEXT`/`MODEL_FAILURE`/`UNEXPECTED_EXCEPTION` `AssignmentFailureCause` sketch in place, labelled as
       the forward FI-boundary target grouping.** **Add**, alongside it (side-by-side, per D5), the landed concrete
       assignment-level `AssignmentFailureCause` (8 members) + `AssignmentSuccess`/`AssignmentFailure`/
       `AssignmentOutcome` shapes (D1, service-local in `services/run_station_forecast.py`), with a one-line pointer
       to D5's mapping (concrete gate-level realization now; the coarse 3-bucket view is the forward target; Phase 3
       adds `MISSING_CONTEXT`/`TRACK_UNAVAILABLE` as new members; the Phase 2-FI follow-on later splits
       `PREDICT_FAILED`). Apply the spec edit per D5 — do **not** re-derive the rationale in the spec.
    2. **`:3639`** (authoritative `MultiModelForecastResult` shape): change
       `failed_models: dict[ModelId, str]  # model_id → error message` to
       `failed_models: dict[ModelId, AssignmentFailure]  # model_id → structured failure (cause + detail)`.
    3. **`docs/design/forecast-cycle-redesign.md` build-sequence item 2 (`:272`):** add a minimal, clearly-marked
       reconciliation note (MAJOR-1) — build-item 2 is delivered by **Phase 2 (outcome shape) + the Phase 2-FI
       follow-on + the context-consumption seam (Phase 3)**, NOT by Phase 2 alone; the `ModelRunContext`-consumption
       seam (runner *receives* per-assignment context) is not in Phase 2's scope. Do not rewrite the item; append a
       short note.
    4. **`docs/standards/logging.md` (UNCONDITIONAL — the canonical Flow-1 table at `:266` already enumerates runner
       failure events):** add a row for the new backstop event `run_station_forecast.unexpected_exception` at
       **ERROR**, kwargs `station_id`, `model_id`, `error`; Notes: SAP3 loop-level backstop for an *unanticipated*
       exception escaping `_run_single_model` outside its guarded regions — assignment-local (recorded in
       `failed_models` as `UNEXPECTED_EXCEPTION`, advances the fallback chain, does NOT darken a station whose
       higher-priority assignment already succeeded). No change to the eight existing WARNING rows.
    5. **`docs/plans/README.md`:** **add a Plan 150 entry**, and **reconcile Plan 148's status** (currently listed
       `DRAFT` at `README.md:106`; #139 merged to `main` at `fa14b9a`). Plan 148's archival is handled as a
       **separate bookkeeping action** and is **specified**, not performed, here: move
       `docs/plans/148-forecast-redesign-phase1-modelruncontext.md` → `docs/plans/archive/`, set its frontmatter
       `status: COMPLETE`, and update the `README.md:106` line from `DRAFT` to `COMPLETE (archived, PR #139)`. (Do
       **not** move 148 as part of Plan 150's own commits unless the orchestrator folds it in; the point is to stop
       listing a merged plan as an active `DRAFT`/`MERGED` status. "MERGED" is a git state, not a plan status —
       use `COMPLETE`/archived.)
    6. Check whether `docs/touchpoint-maps.md`'s "Forecast cycle / assignment selection" map needs a one-line
       pointer to the new outcome type (only if an existing bullet names the old `str`/`isinstance` shape — if not,
       no edit, per the touchpoint map's fitness test in `docs/workflow.md` § Right-sizing).
  - **Out of scope:** any production code change (the flow-level test drives existing production behaviour + the D3
    backstop landed in T1).
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
    }
  ]
}
```
Strictly sequential: T1 (types + runner + backstop + runner-unit tests, full-suite green) → T2 (flow-level
regression + docs + plan-index, full-suite green). No task leaves a known failure at its gate.

## Dependencies
- **Plan 148** (PR #139, merged to `main` at `fa14b9a`) — this plan's runner (`_run_single_model`,
  `run_all_station_forecasts`) is the post-148 code (`ModelRunContext` built internally, per-assignment warm-up read
  already wired).
- `docs/design/forecast-cycle-redesign.md` — the parent architecture; Build sequence item 2 (`:272`) is the
  build-item this plan **partially** delivers (outcome-shape half; see Owner scope decision + the T2 reconciliation
  note).

## Non-goals
- `ForcingTrackKey`, per-track resolution, `CandidateFetchResult`/`TrackFetchResult`/`StationTrackOutcome`, exact-51
  completeness, per-assignment input assembly, superset removal — all Phase 3/4.
- A `TRACK_UNAVAILABLE`/`MISSING_CONTEXT` failure cause — there is no track concept yet; Phase 3 adds those as **new
  members of the landed `AssignmentFailureCause`** (D5 shows the exact additive change).
- **The `ModelRunContext`-consumption seam** (build-item-2's "runner *consumes* per-assignment `ModelRunContext`").
  `_run_single_model` still takes raw shared inputs and builds `ModelRunContext` internally
  (`run_station_forecast.py:187-196`); Phase 2 changes only its return shape/dispatch. Making the runner *receive*
  per-assignment context (and Phase 3 determining missing context before execution) is Phase 3 / a dedicated seam
  follow-on (MAJOR-1).
- **Preserving the FI-boundary `MODEL_FAILURE` vs. `UNEXPECTED_EXCEPTION` distinction inside `predict`** (the
  **Phase 2-FI follow-on**). Phase 2 does NOT touch the FI adapter (`adapters/forecast_interface.py`). A returned FI
  `ModelFailure` stays flattened into a raised `ModelOutputError` (`_output_from_result`,
  `adapters/forecast_interface.py:369`) and recorded as `PREDICT_FAILED`. Splitting it requires the FI adapter to
  stop flattening and surface a typed signal the runner can catch separately — FI-adapter-boundary work, required
  before the redesign's failure contract is fully satisfied, out of scope here (see the "known FI-adherence gap"
  note). (Note: this is distinct from the loop-level backstop `UNEXPECTED_EXCEPTION` in D3, which catches exceptions
  raised **outside** the guarded predict region and is IN scope.) Phase 3's `MISSING_CONTEXT`/`TRACK_UNAVAILABLE`
  case is orchestrator-local, resolved *before* `predict` is called, so it does not need the FI-boundary split first.
- Any GROUP-path change — `run_group_forecast.py` does not call `_run_single_model`/`run_all_station_forecasts` and
  is untouched.
- Any write-side / state-persistence change (still Plan 148's named deferred follow-on, unrelated to this phase).

## Open items
- **RESOLVED (owner ratified 2026-08-10): failure-taxonomy shape = option (a).** Land the concrete assignment-level
  causes now, as members of a single **assignment-level** `AssignmentFailureCause`; keep the reserved 3-bucket
  FI-boundary sketch in the spec, side-by-side, as the forward target (D5); a later focused **Phase 2-FI follow-on**
  splits `PREDICT_FAILED` into FI `MODEL_FAILURE`/`UNEXPECTED_EXCEPTION` at the FI boundary. (Rejected: (b) pull the
  FI-adapter typed signal in now — larger scope, touches the MANDATORY FI-adherence boundary; (c) keep only the 3
  coarse variants — loses today's concrete per-gate reasons.) Under option A (owner-locked), (a) is the *outcome-shape*
  half only; the FI-signal preservation and the context-consumption seam are explicit named follow-ons.
- **Scope trade-off acknowledged (not silently regressed):** Phase 2 adds one behaviour change (the D3 backstop) on
  top of the shape refactor, to honour the fallback invariant the design names. Flagged in D6 as an explicit
  trade-off rather than left implicit; covered by a runner-level regression (T1) **and** a flow-level regression (T2).
