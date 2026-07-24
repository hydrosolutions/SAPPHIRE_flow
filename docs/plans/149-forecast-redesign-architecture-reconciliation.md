---
status: DRAFT
created: 2026-07-24
plan: 149
title: Reconcile the forecast-cycle redesign with the repo architecture + standards
scope: An independent architecture/standards-alignment review of docs/design/forecast-cycle-redesign.md (verdict NEEDS_RECONCILIATION) found the design conceptually aligned but under-formalized against the locked layering rule, the type-driven-development standard, the authoritative Protocol/type spec, the FI failure contract, and the documented Flow 1 phase structure. This plan reconciles it in two moves: (A) formalize the contracts INSIDE the redesign doc, and (B) make the deliberate updates the redesign necessitates in the AUTHORITATIVE docs (architecture-context.md Flow 1 + types-and-protocols.md). Docs-only; no code. Precedes slicing the redesign into build plans.
depends_on: []
blocks: []
supersedes: []
---

# Plan 149 — Reconcile the forecast-cycle redesign with the repo architecture + standards

## Status
**DRAFT — docs-only reconciliation.** Driven by an independent architecture/standards-alignment Codex review
(2026-07-24) of `docs/design/forecast-cycle-redesign.md`: **VERDICT NEEDS_RECONCILIATION**, with the design
**conceptually aligned** (assignment=run-unit, artifact scope, ensemble-first, fallback semantics, per-forecast
provenance, WMO, clock-DI all confirmed) but **under-formalized** against the repo's locked contracts. This plan
makes the redesign conform + updates the authoritative docs it changes, so it is safe to slice into build plans.
Needs `/plan` before READY. Preserve the confirmed-aligned properties — do not regress them.

## Problem — the redesign diverges from formal repo contracts (not concepts)
The review found (all grounded in `docs/design/forecast-cycle-redesign.md` × the cited authority):
- **Layering unassigned (blocker).** The redesign lists projection / walk-back / completeness / candidate fetch /
  immutable accumulation / assembly in one phase but never assigns them to layers, violating the locked rule
  (`architecture-context.md:3101-3120`: adapters = I/O only; services = business logic that **cannot call
  adapters**; flows orchestrate without business logic).
- **Types don't meet the type-driven standard (blocker).** The three horizons are `Mapping[..,int]` + bare `int`s
  (`redesign:79-92`); `StationTrackOutcome` is prose "available/unavailable"; several result/context types have no
  explicit frozen/kw-only/slotted definition (`redesign:66-75,93-100`). `CLAUDE.md:279-312,323-377,396-414`
  requires NewType for semantic primitives, enums/discriminated types for named states, and frozen kw-only slotted
  dataclasses as the default value type.
- **UTC contract unstated (major).** `resolved_cycle`/`nominal_cycle` are untyped (`redesign:61-69,93-99`); the
  locked rule is `UtcDatetime` everywhere + `ensure_utc()` at boundaries (`architecture-context.md:3187-3190`,
  `types-and-protocols.md:53-68`).
- **Authoritative Protocol bypassed (major).** `fetch_requirement(...)` is added "alongside" `fetch_forecasts`
  (`redesign:93-95`), but `WeatherForecastSource` in the spec exposes only `fetch_forecasts`
  (`types-and-protocols.md:2793-2813`); Protocols are locked into `protocols/` + the spec
  (`architecture-context.md:3181-3185`).
- **FI failure semantics conflated (major).** "A member failure fails the assignment" (`redesign:116-123`) must
  distinguish a **returned `ModelFailure`** (anticipated) from an **unexpected exception** (Prefect backstop) per
  the FI mandate (`CLAUDE.md:50-68`; SAP3 boundary maps total FI failure to `ModelOutputError`,
  `types-and-protocols.md:1730-1743`).
- **Flow 1 phase boundary changes (major).** Flow 1 today = Phase A fetch **per NWP source**, Phase B run **per
  model-unit** (`architecture-context.md:184-199`); the redesign inspects assignments **before** fetch and fetches
  **per track** — a real architecture change (`redesign:54-60,176-181`).
- **Cross-cycle combination disabled = capability drift (major).** The redesign disables it (`redesign:182-189`);
  the architecture says combination consumes all combinable models with no same-cycle restriction
  (`architecture-context.md:118-130`; WMO favours combining after per-model processing, `wmo.md:65-72`).
- **Minors:** Prefect topology (task-per-track/assignment + gather barriers) unspecified vs `orchestration.md:35-59`;
  no logging event contract vs `logging.md:168-305`; no resource/concurrency acceptance gate vs
  `cicd.md:74-93` / `orchestration.md:54-59`.

## Design decisions (what to reconcile, and how)
- **This plan is docs-only.** It edits the redesign doc + two authoritative docs; it writes **no code**. The
  build plans sliced afterward carry the code.
- **(B) is a *deliberate, documented* architecture change, not drift.** The redesign genuinely changes Flow 1's
  phase order and the source contract; per the repo rule "every change updates affected docs", we update
  `architecture-context.md` + `types-and-protocols.md` on purpose (with the redesign as the rationale), rather
  than let the code silently diverge from the spec.
- **Preserve the confirmed-aligned properties** (assignment run-unit, artifact scope, ensemble-first, fallback,
  provenance, WMO, clock-DI). No task may regress them.

## Tasks
### (A) Formalize contracts INSIDE `docs/design/forecast-cycle-redesign.md`
- **A1 — assign every component to a layer.** Add an explicit layer map: domain values/results → `types/`; the
  widened source contract → `protocols/adapters.py`; external candidate fetch → `adapters/`; projection,
  completeness, candidate selection, assembly → pure `services/` (no I/O); the retry/fetch/validate loop + fan-out
  → `flows/`. State that walk-back **policy** lives in `services/` (pure), not inside the adapter's
  `fetch_requirement()`.
- **A2 — define the new types per the type-driven standard.** Replace the prose types with frozen, kw-only,
  slotted definitions + semantic wrappers: `FutureSteps = NewType(...)` (or a validated positive wrapper);
  distinct `FeatureFetchHorizons` / `InputFrameHorizon` / `OutputHorizon`; `ForcingTrackKey`,
  `CandidateFetchResult`, `TrackFetchResult`, `StationTrackOutcome` (an **available/unavailable discriminated
  frozen variant**, not a nullable payload or bool); reuse existing enums/NewTypes for ensemble mode, spatial
  representation, and station/model/group ids.
- **A3 — type every timestamp `UtcDatetime`** in the doc's signatures (cycle, nominal, issue, provenance,
  candidate), with `ensure_utc()` only at external/config/API boundaries.
- **A4 — clarify FI failure semantics.** State the three cases explicitly: (i) missing track/context =
  orchestrator-local **expected** failure → do not call the model; (ii) model-declared insufficient/degraded
  inputs = **returned FI `ModelFailure`** → the adapter maps it to the existing assignment fallback signal; (iii)
  unexpected exceptions propagate to Prefect (never silently relabelled as ordinary fallback). Exact-51 is
  **orchestration** acceptance policy; per-variable shape/length shortfalls remain the **model's** FI responsibility.
- **A5 — specify Prefect topology, logging events, and a resource gate.** `task.map` fetch over tracks + forecast
  over assignments, per-member calls **inside** the assignment task, explicit gather barriers before
  persistence/combination/Phase C (`orchestration.md`). Canonical `{entity}.{action}` events —
  `nwp.candidate_rejected`, `nwp.track_resolved`, `forecast.assignment_failed`, `forecast.fallback_advanced` —
  with cycle/source/track/station|group/model/cause/duration fields, WARNING for fallback, member-level ≤ DEBUG
  (`logging.md`). A concurrency/batching/memory/log-volume acceptance gate for the high-fan-out phases
  (`cicd.md`/`orchestration.md`).

### (B) Deliberate updates to the AUTHORITATIVE docs
- **B1 — `architecture-context.md`: update Flow 1.** Amend the Flow 1 sequence + diagram to the redesign's phase
  order: scope/assignment discovery → track projection → **Phase A per-track fetch** → observations → **Phase B
  per-assignment prepare/run** → combination/persist. Record the rationale (the redesign) inline so it reads as a
  deliberate evolution, not drift.
- **B2 — `types-and-protocols.md`: the widened source contract + result types.** Add the `fetch_requirement(...)`
  signature to the `WeatherForecastSource` Protocol section (keeping `fetch_forecasts` as the compatibility
  contract during migration), and add the authoritative definitions of `ForcingTrackKey`, `CandidateFetchResult`,
  `TrackFetchResult`, `StationTrackOutcome`, `ModelRunContext`, and the horizon types (A2). This is the spec of
  record the build plans implement against.
- **B3 — combination eligibility.** Document the **same-NWP-cycle** combination-eligibility rule in
  `architecture-context.md` (and/or the spec): combination requires combinable models sharing one NWP cycle in
  v1; heterogeneous-cycle combination is deferred pending richer per-source provenance. Reconciles the disabled
  cross-cycle combination with the documented capability.

### Verification
- **V1 — re-run the architecture/standards-alignment review** (independent Codex pass) over the reconciled
  redesign + updated authoritative docs; target **VERDICT: ALIGNED**. The confirmed-aligned properties remain.
- **V2 — cross-doc consistency:** the redesign, `architecture-context.md` Flow 1, and `types-and-protocols.md`
  agree on the phase order, the Protocol, and the type definitions (no contradictions).

## Non-goals
- **Any code.** No implementation, no new modules — docs-only. Build plans (sliced from the reconciled redesign)
  carry code, each updating its own affected docs. The individual redesign build phases (`ModelRunContext` =
  Plan 148, etc.) are separate.

## Dependencies
- `docs/design/forecast-cycle-redesign.md` (the subject) + the alignment-review findings. No code/plan deps.
  Should land before the redesign is sliced into build plans, so those plans implement against a reconciled spec.

## Open items / to confirm
- **`FutureSteps` — NewType vs validated wrapper** (A2): a `__post_init__`-validated positive-int wrapper if we
  want construction-time range enforcement, else `NewType` for a zero-cost semantic tag. `/plan` to pick.
- **Combination eligibility (B3)** — confirm v1 = same-cycle-only is acceptable (vs investing in richer provenance
  now). Owner call.
- **Where `ModelRunContext` lands** — Plan 148 keeps it service-local; if B2 promotes its contract to the spec,
  confirm the type stays service-defined while its shape is spec-documented (no `types/` move that would re-create
  the layering inversion).
