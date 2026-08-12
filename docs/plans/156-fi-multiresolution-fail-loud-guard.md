---
status: DRAFT
created: 2026-08-12
plan: 156
title: FI multi-resolution requirement — fail loudly instead of silently flattening
scope: Reject a ForecastInterface model whose InputRequirement declares MORE THAN ONE time_step, instead of silently flattening its branches into one feature set with max-collapsed lookback/horizon and an arbitrarily-chosen resolution. Split out of Plan 152 (its T0b) because it is a standalone safety fix with no dependency on the aquacast integration — it protects every FI model, ships immediately, and is the only thing standing between a multi-resolution artifact and plausible wrong numbers while real multi-resolution support (Plan 153) stays deferred.
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

**The failure is silent.** A multi-resolution artifact onboarded today would have its features
merged, its lookback/horizon **max-collapsed as bare step counts** (e.g. 210 daily vs 168 hourly →
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

**Red-first (all three must fail against current code, for the stated reason):**
1. An FI model whose `InputRequirement.dynamic` carries **more than one `time_step` key** is
   **rejected with a clear error naming the resolutions** — today it is flattened and accepted.
2. **With one multi-resolution model installed alongside good ones, `discover_models()` still
   returns the good ones** — today a `ConfigurationError` from the projection aborts the whole loop
   (`services/model_registry.py:93-95`).
3. No remaining site chooses a resolution via `next(iter(...))` over a multi-element set.

**Exit gate:** `uv run pytest -q` + `uv run pyright` + `uv run ruff check`.

## Non-goals

- **Multi-resolution SUPPORT** — that is Plan 153, deferred (Plan 152 D7). This plan only refuses.
- Any aquacast-specific work.

## Coordination

**`adapters/forecast_interface.py` is also edited by Plan 151 (its T2, per-`time_step` accessors)**
and by Plan 157 (the aquacast unit boundary). Sequence against 151 or accept a rebase; when 151's
per-`time_step` accessors land, they are the natural foundation for Plan 153 and this guard should
compose with them rather than duplicate them.

## References
- `docs/plans/152-aquacast-pooled-model-integration.md` — parent; § G5 for the full gap analysis.
- Plan 153 (not yet drafted) — multi-resolution support; turns this plan's red test green.
- `CLAUDE.md` § ForecastInterface Adherence.
