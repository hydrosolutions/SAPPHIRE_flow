---
status: READY
created: 2026-08-12
plan: 156
title: FI multi-FUTURE-FORCED-resolution requirement — fail loudly instead of silently flattening
scope: Reject a ForecastInterface model whose InputRequirement declares non-empty future_known in MORE THAN ONE time_step branch, instead of silently flattening its branches into one feature set with max-collapsed lookback/horizon and an arbitrarily-chosen resolution. The rule is deliberately narrower than "more than one time_step": a past-only second branch stays constructible, because Plan 151 T2 requires that shape and 8 existing tests share a two-branch fixture. Rejecting on multiple FUTURE-FORCED branches targets the actual flattening bug without contradicting 151. Split out of Plan 152 (its T0b) because it is a standalone safety fix with no dependency on the aquacast integration — it protects every FI model, ships immediately, and is the only thing standing between a multi-resolution artifact and plausible wrong numbers while real multi-resolution support (Plan 153) stays deferred.
depends_on: []
blocks: []
supersedes: []
---

# Plan 156 — fail loudly on a multi-resolution FI requirement

## Status
**DRAFT.** Split out of **Plan 152** (task T0b) on 2026-08-12. Grounded against `main`; every
citation below was verified during Plan 152's review rounds.

**Why it is separate:** it has **no dependency on aquacast, the Swiss data work, or the shim**. It
is a guard on our FI boundary that protects *every* model, and coupling it to a blocked integration
plan would delay a safety fix for no reason.

## The problem — a silent misread, not a gap

Our domain types are single-resolution by construction, and the FI projection **actively mis-reads**
a multi-resolution requirement rather than refusing it:

- `_iter_dynamic_specs` (`adapters/forecast_interface.py:827-831`) iterates `req.dynamic.values()`,
  **flattening every `time_step` branch into one stream**.
- `:519` sets `supported_time_steps=frozenset(req.dynamic)` — reinterpreting *"this model requires
  all of these resolutions simultaneously"* as *"this model can run at any one of them"*.
- Downstream acts on that second reading: onboarding checks mere membership
  (`services/model_onboarding.py:141`, `:231`), and five sites pick one **arbitrarily** with
  `next(iter(req.supported_time_steps))` (`services/model_onboarding.py:295`, `:368`, `:485`;
  `services/onboarding.py:806`, `:962`) — a non-deterministic choice over a `frozenset`.

**The failure is silent.** A multi-**future-forced**-resolution artifact onboarded today (an
MTS-LSTM config, say) would have its features merged, its lookback/horizon **max-collapsed as bare step counts** (e.g. 210 daily vs 168 hourly →
`210`, then applied at whichever resolution won the coin toss), and be handed a single-resolution
frame by a pipeline that believes it complied. **It would produce numbers, and they would be wrong.**

**Per `CLAUDE.md` § ForecastInterface Adherence this is path 1** — the FI expresses multi-resolution
correctly (`InputRequirement.dynamic` is *keyed by* `time_step`); **we** collapse it. The fix belongs
on the SAP3 side; no FI issue.

## Objective

Convert a silent misread into a **loud refusal**, so that deferring real multi-resolution support
(Plan 153) is safe. This plan does **not** add multi-resolution support — its red test is precisely
what Plan 153 will later turn green.

## The constraint that shapes the design

**`discover_models` re-raises `ConfigurationError`.** It catches and re-raises it
(`services/model_registry.py:93-95`), swallowing only generic `Exception`. So a `ConfigurationError`
raised anywhere in the try block — including from `_project_requirements` via `adapt_if_fi` —
**aborts discovery for EVERY model: a registry-wide blackout.**

**Invariant: one unsupported model must never darken the registry.** The guard therefore must not
simply raise `ConfigurationError` from inside the projection. Either it signals in a way discovery
skips per entry point, or `discover_models` is changed to skip-and-continue on a bad model.
**Which one is an implementation choice; the invariant is not.**

## Tasks

### T1 — Reject multi-resolution requirements, without blacking out the registry
**In scope:** `adapters/forecast_interface.py` (the projection) and whichever seam satisfies the
invariant above; plus the `next(iter(req.supported_time_steps))` sweep so no site can silently pick
a resolution.

**THE RULE IS NARROWER THAN "more than one `time_step`" (seam review, 2026-08-12 — this was a
blocking contradiction with Plan 151).** Rejecting every multi-`time_step` requirement would:
- break **8 existing tests** that share `_multi_product_requirement()`
  (`tests/unit/adapters/test_forecast_interface_adapter.py:152-168`), which legitimately declares a
  1 h and a 24 h branch; and
- **contradict Plan 151 T2**, whose canonical red test requires exactly that shape — a model with two
  time steps carrying different features, each branch exposed separately. Whoever landed second would
  turn the other's acceptance test red. **That is a contradictory contract, not a rebase.**

**Reject on the ACTUAL flattening bug instead: more than one branch declaring non-empty
`future_known`.** That is what the damage comes from — the cross-branch `max` collapse of
`future_steps` (`adapters/forecast_interface.py:472-481`) and
`supported_time_steps=frozenset(req.dynamic)` (`:519`). Plan 151's supported shape (one future-forced
branch plus a past-only branch) stays constructible; the genuinely unsupportable shape (two
future-forced resolutions, as in an MTS-LSTM config) fails loudly.

**THE RULE IS TWO-LEVEL (recorded post-implementation, 2026-08-12).** Implementation found that one
level is not sufficient, and the distinction matters to Plan 151:
- **At CONSTRUCTION / projection** — reject only **multiple FUTURE-FORCED branches**. One
  future-forced branch plus a past-only branch is **accepted**, so Plan 151 T2's per-branch accessors
  stay usable.
- **At DELIVERY** (`predict` / `predict_batch` / `train`) — reject **any** multi-branch requirement,
  via `_assert_single_deliverable_dynamic_branch` in `_station_inputs_from_frames`. Reason: SAP3
  builds only ONE `dynamic={time_step: …}` entry — the branch matching the caller's `time_step` —
  while fetch and the NaN gate flatten across **all** branches. A second branch's variables would
  therefore be fetched, NaN-checked, and then **silently omitted** from what the model receives:
  precisely the plausible-but-wrong outcome this plan exists to prevent. It lifts when Plan 153 lands
  real multi-resolution delivery.

**Red-first (all must fail against current code, for the stated reason):**
1. An FI model declaring **non-empty `future_known` in more than one `time_step` branch** is
   **rejected at construction** with a clear error naming the resolutions — today it is flattened and
   accepted. A requirement with one future-forced branch and one past-only branch **constructs
   successfully** (guards against over-rejection at that level).
1b. Any multi-branch requirement — **including** the one-future-forced-plus-past-only shape — raises
   `UnsupportedModelRequirementError` at **delivery**.
2. **With one multi-resolution model installed alongside good ones, `discover_models()` still
   returns the good ones** — today a `ConfigurationError` from the projection aborts the whole loop
   (`services/model_registry.py:93-95`).
3. No remaining site chooses a resolution via `next(iter(...))` over a multi-element set.

**Exit gate:** `uv run pytest -q` + `uv run pyright` + `uv run ruff check`.

## Non-goals

- **Multi-resolution SUPPORT** — that is Plan 153, deferred (Plan 152 D7). This plan only refuses.
- Any aquacast-specific work.

## Coordination — **NOT rebasable; requires ordering by the owner**

**This plan and Plan 151 hold rules for the same FI seam, and 151 is being implemented NOW.**
- **The contract conflict** is resolved above by narrowing the rule to *multiple future-forced
  branches*. **Both plans must agree on that boundary before either is built** — it is a shared
  contract, not a file-level rebase.
- **This plan must land BEFORE Plan 151 T2** for a second reason. 151's ratified global conformance
  sweep (its D21) raises `ConfigurationError` at adapter construction
  (`adapters/forecast_interface.py:449`), and `discover_models` **re-raises** it
  (`services/model_registry.py:93-95`). So one non-conformant entry point would darken discovery for
  the forecast cycle, training and onboarding. **This plan's T1 red test #2 is the only funded fix
  for that invariant**, so 151's sweep should signal through it rather than raise.
- **Shared fixture — CHANGED, and the plan's earlier premise about it was WRONG.** This plan claimed
  the fixture "legitimately declares a 1 h and a 24 h branch" and so needed no edit under the narrowed
  rule. **False:** on `main` *both* its branches carried non-empty `future_known`
  (`_daily_dynamic_spec` had `future_known={"nwp": {"temp_forecast": …}}`), making the fixture itself
  an instance of the rejected shape. Implementation resolved it by dropping that `future_known`, so
  the 24 h branch is past-only — which preserves the fixture's qualitative shape (two time steps,
  different features) **and** matches the shape Plan 151 T2 needs. Recorded here rather than left in a
  test comment, since this plan required fixture changes to be agreed.
- **⚠️ Plan 151 T6 is at risk from the DELIVERY-level guard — T2 is not.** 151 T2's red tests are
  *accessor* tests, and the 151 plan contains **zero** references to `predict(`, `predict_batch` or
  `.train(`, so construction-level acceptance is all it needs. But **151 T6** asserts a
  branch-specific `max_nan` is "honoured from the **selected branch** only" — implying a multi-branch
  fixture — and it exercises `InputSeries` construction, which is where
  `_assert_single_deliverable_dynamic_branch` sits. **If T6 drives a multi-branch requirement through
  the assembly path, this guard raises.** The 151 session must either use a single-branch fixture for
  that assertion or agree to lift the delivery guard for the assembly path.
- Plan 157 also touches `adapters/forecast_interface.py`'s neighbourhood; when 151's per-`time_step`
  accessors land they are the natural foundation for Plan 153, and this guard should compose with
  them rather than duplicate them.

## Post-implementation review (2026-08-13) — IMPLEMENTED, holding at PR

**Branch** `feat/plan-156-fi-multires-guard`, 4 commits, tagged **v0.1.699**.
**Gates:** 2523 unit tests passing, ruff check + format clean, pyright ratchet OK (428 ≤ 459).

`/implement` escalated with 2 majors and reported `redFirstMissed: false`. **Two independent reviews
of the committed diff were then run** — a repo-grounded Codex pass and a behaviour/consequences pass
— and they **disagreed on merge-readiness** (CHANGES REQUIRED vs SAFE TO MERGE). Both were verified
against the code before anything was folded. Four fixes followed:

1. **BLOCKER — guard ordering.** The delivery guard ran *after* the flattened NaN gate in `predict`
   and `predict_batch`, so a multi-branch requirement surfaced as a misleading `ModelOutputError`
   ("max_nan exceeded") or `ConfigurationError` ("missing `<past-only-branch variable>`") instead of
   `UnsupportedModelRequirementError`. It now runs **before any frame access, NaN gate or model
   call** in all three delivery entry points (the `artifact_scope` dispatch
   check legitimately precedes it — a programming error outranks a
   model-shape error). The old test was named `..._at_predict_time` but called only `train()` — the one
   entry point where the ordering happened to work. **Soundness proven** by disabling the guard and
   reproducing the exact wrong error.
2. **MAJOR — the registry invariant only half-held.** Multi-spatial and past-only/no-future
   requirements still raised `ConfigurationError`, which `discover_models` **re-raises**, so one
   legal-but-unrepresentable model still darkened discovery for **every** model. Both now raise
   `UnsupportedModelRequirementError`; genuine config faults (missing `ModelTier`) still hard-fail.
   **This is the invariant the "156 before 151 T2" ordering rests on.**
3. **MAJOR — shared fakes.** Hourly-only fakes made the (mostly daily) consumers domain-invalid and
   expanded auto-selection to hundreds of thousands of issue times: **`test_onboarding.py` went 1.7 s
   → 4 m 05 s**. Now daily: **11.9 s**, all 30 passing. *(The naive revert to `{1h, 24h}` runs in
   1.7 s but FAILS 3 tests — it is not the fix.)*
4. **REVERSAL of an implemented, tested decision.** `resolve_single_supported_time_step` raised
   whenever a model declared >1 supported time step — outlawing a **documented** shape, since
   `supported_time_steps` is the set of steps a model "can operate on" with `ModelAssignment.
   time_step` selecting per station. Plan 156's real complaint was the **non-determinism** of
   `next(iter(frozenset))`, not multi-valued sets. Now `resolve_synthetic_time_step`: deterministic
   smallest-step, logged, fatal only on empty. `architecture-context.md` reconciled. **Left as-is,
   Plan 153 would have had to delete a test asserting the wrong contract.**

**Blast radius verified empirically:** `discover_models()` returns all 6 entry points, and the three
FI-based production models (`nwp_regression`, `nwp_rainfall_runoff`,
`seasonal_precip_runoff_regression`) each declare **exactly one** `time_step` branch — the guard
cannot fire on anything deployed.

## Carried forward to the Plan 152 family — NOT fixed here

Both belong to the **group failure contract**, which is not 156's to change, and both matter to
Plan 152 because **aquacast is a GROUP model**:

- **The "loud" failure is silent at the health level.** `UnsupportedModelRequirementError` from
  `predict_batch` hits the broad `except Exception` in `services/run_group_forecast.py:453-466` →
  `log.warning` → `return {}`. The cycle then iterates an empty dict, appends **nothing** to
  `errors` and increments **nothing** in `stations_failed`, so `_forecast_cycle_health` reports
  **HEALTHY while an entire group produced no forecast**. Pre-existing, but this plan routes a new
  *permanent* failure class into it. *Smallest fix: re-raise alongside the `StoreError` passthrough
  at `:451` so it reaches the cycle's `except Exception`.*
- **Construction-accepts / delivery-rejects leaves a dead assignment row.** The
  one-future-forced + one-past-only shape constructs, so Flow-0 creates a station assignment with
  **no deliverability check** (`services/onboarding.py:808-860`). The clear diagnosis is emitted
  once, at onboarding; every later cycle reports `NO_ARTIFACT` rather than the multi-resolution
  cause. *Smallest fix: consult the branch-count check in `validate_compatibility` so the assignment
  is never created.*

## References
- `docs/plans/152-aquacast-pooled-model-integration.md` — parent; § G5 for the full gap analysis.
- Plan 153 (not yet drafted) — multi-resolution support; turns this plan's red test green.
- `CLAUDE.md` § ForecastInterface Adherence.
