# Forecast-cycle redesign — per-requirement, multi-track, probabilistic

**Status:** DRAFT design doc — created 2026-07-23. **Supersedes the incremental patching of Plans 126 + 144**
(they are folded into this redesign). Decisions locked in `docs/design/v1-forecasting-decisions.md`.
**Next:** review this architecture, then build in the phased sequence below (control-only path preserved
throughout).

## Why this doc exists
Six consecutive `/plan` runs on the ensemble-forecasting cluster (142 ×2, 144, 145, 126) stalled — and the
final 126 pass proved *why*: the failures are not plan-doc quality, they are **three load-bearing assumptions in
the v0 forecast cycle that a probabilistic, multi-track pipeline cannot satisfy.** They are architectural, so
they need an architecture change, not another doc edit. This doc specifies that change.

## The three structural blockers (verified in code)
1. **One cycle per batch.** The flow reduces the whole fetch to a single cycle —
   `resolved_cycle = next(iter(result_object.values())).cycle_time` (`run_forecast_cycle.py:1155-1157`) — and
   every station's Phase-B readback + provenance use that one `nwp_cycle_reference_time`
   (`:1727-1745`, `_NwpFetchOutcome.cycle_time` `:175-191`). A CONTROL model that should ride the freshest cycle
   and an ENSEMBLE model that must ride the latest *complete* 00Z cycle **cannot both be served** — only the
   first-seen cycle is read back; the other's records are stored under a different cycle and go invisible.
2. **Superset requirements collapse per-feature horizon.** `build_superset_requirements`
   (`operational_inputs.py:266-323`) unions all features and takes the **max** horizon, and the FI boundary
   already `max`-collapses per-variable `future_steps` (`forecast_interface.py:471-476`). "precip for 2 steps +
   temp for 10 steps" becomes "both for 10" — an invalid feature × max-horizon cross-product that can reject a
   cycle the actual FI contract accepts.
3. **One assembly, one mode, per station.** `assemble_station_operational_inputs` runs **once per station** with
   a single `time_step` and a superset (`operational_inputs.py:342-353`, `run_forecast_cycle.py:1787-1866`). A
   station carrying a daily SINGLE model *and* a 3-hourly ENSEMBLE model cannot be expressed as one partition.

## What already exists (reuse — the redesign wires these together, it does not build them)
- **`services/ensemble_fanout.py`** — per-member deterministic fan-out, **stateless-models-only**. The "ensemble
  wrapper."
- **`types/ensemble.py` `ForecastEnsemble`** + **`services/forecast_qc.py`** (spread/QC).
- **Plan 134** — control operational-forcing bridge + the **6h** gap-fill floor (control/daily path).
- **Plans 145 / 146** — future / past **snow** forcing wiring (separate forcing-ingest track; unchanged by this).
- **`ModelDataRequirements`** (`types/model.py:262-271`), `assess_future_coverage` (`nwp_coverage.py:65-141`),
  `_resolve_effective_cycle` / `resolve_latest_cycle` (`recap_gateway.py:349-384,673-702`).

## Target architecture

The organizing idea: **the unit of resolution + assembly is a *requirement*, not a station.** A requirement is
one model assignment's forcing need; a station carries one or more requirements; the flow resolves, fetches,
assembles, and reads back **per requirement**, keyed by a per-requirement resolved cycle.

1. **Per-requirement projection (fixes blocker 2).** Each active assignment → one immutable `FetchRequirement`
   carrying `{features, per-feature horizon (not a single max), selected time_step, ensemble_mode}`. Derived at
   the FI boundary so per-variable `future_steps` are preserved, not `max`-collapsed. `build_superset_requirements`
   may stay for *within-requirement* assembly, but it does **not** supply cross-requirement fetch acceptance.
2. **Requirement-aware cycle resolution + a per-requirement resolved-cycle map (fixes blocker 1).** For each
   requirement, resolve the latest cycle satisfying its **completeness** (D1: exact-51 for ENSEMBLE features; `fc`
   for CONTROL) **and per-feature horizon**, walking back within `max_cycle_age_hours` (D4 walk-back-only). Because
   `pf` is 00Z-only today (D3), ENSEMBLE requirements resolve to the latest 00Z (once/day) while CONTROL
   requirements keep the freshest cycle — **so the flow must carry a `map[requirement → resolved_cycle]`**, not a
   single batch cycle. Phase-B readback + provenance key off the requirement's own resolved cycle.
3. **Candidate-local accumulation (fixes the walk-back-contamination blocker).** Fetch + validate each candidate
   cycle into a **fresh** accumulator; commit only on full completeness + horizon pass; discard on failure so no
   partial/rejected rows or provenance leak.
4. **Multi-track assembly (fixes blocker 3).** Assemble **once per requirement** (its features, its time_step, its
   horizon, under its resolved cycle) — not once per station. A station's N requirements produce N assembled
   frames; each model consumes its own.
5. **Ensemble fan-out (reuse).** For an ENSEMBLE requirement, run the model through `ensemble_fanout` (one
   deterministic call per member, `ensemble_mode=SINGLE`, stateless) and aggregate via `ForecastEnsemble` +
   `forecast_qc`. CONTROL requirements run once (member 0). `min_operational_ensemble_size` gates **output**
   eligibility only.
6. **Forcing providers behind a seam.** Control/daily forcing = Plan 134's 6h bridge; ensemble sub-daily forcing
   = the client-side per-member 3h stitch now / a 3h gateway ensemble-operational later (the decisions-note ask);
   snow = 145/146. Units are canonical from the recap adapter; the FI adapter labels.

## Locked decisions (see `docs/design/v1-forecasting-decisions.md`)
D1 exact-51 + walk-back to latest complete cycle · D3 `pf` 00Z-only (ensemble once/day now, 4×/day later) ·
D4 walk-back-only · units canonical from the recap adapter · reuse `ensemble_fanout` (stateless).

## What this supersedes / relates to
- **Supersedes** the incremental designs in **Plan 126** (cycle resolution — now redesign component 2/3) and
  **Plan 144** (multi-track orchestration — now components 1/4/5). Mark both `SUPERSEDED by this redesign`.
- **Keeps separate:** Plan 134 (control bridge), Plans 145/146 (snow forcing ingest), Plan 143 (onboarding),
  `ensemble_fanout`/`ForecastEnsemble`/`forecast_qc` (reused).

## Build sequence (each phase keeps the control-only path green)
1. **Per-requirement projection + per-feature horizon** — introduce `FetchRequirement`; derive at the FI boundary;
   control-only path becomes the single-requirement case.
2. **Per-requirement resolved-cycle map** — replace the single `resolved_cycle` with a `map`; thread through
   readback + provenance; control-only = a one-entry map (no behaviour change).
3. **Requirement-aware cycle resolution + candidate-local accumulation** — extend `_resolve_effective_cycle` to
   completeness+horizon per requirement; fresh-accumulator fetch.
4. **Multi-track assembly** — assemble per requirement; a station with one requirement is unchanged.
5. **Ensemble fan-out wiring** — route ENSEMBLE requirements through `ensemble_fanout` + `ForecastEnsemble` +
   `forecast_qc`.
6. **Forcing providers** — the `EnsembleForcingProvider` seam (client-stitch 3h now); snow via 145/146.

**Backward-compat is structural:** a control-only station is exactly the degenerate case — one requirement, one
cycle, one assembly, one member — so each phase can land without breaking the live control-only forecasting.

## Risks
- The forecast cycle is load-bearing and live; this touches fetch, resolution, readback, provenance, assembly.
  Mitigation: the phased sequence above, control-only-green at every step, red-first tests per phase, and the
  existing full-suite + Codex gate.
- Completeness verification today relies on `pf` all-or-nothing per cycle (cheap `fc`+first-`pf` probe + bounded
  full-member validation); the durable fix is the gateway completeness manifest (decisions-note D2).

## Open items
- **Gateway asks** (decisions note): 3h gap-fill / ensemble-operational, a completeness manifest, `pf` at all
  cycles. None block the build (client-side + walk-back cover the interim).
- **Review** this doc (design review / `/plan` over the redesign as a whole, not the superseded fragments), then
  slice the build sequence into implementation plans.
