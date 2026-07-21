---
status: DRAFT
created: 2026-07-21
updated: 2026-07-21
# STUB split out of Plan 015 (which shipped CALCULATED support, #109/#112/#113). This plan
# owns UNGAUGED station support — the half of the original "Virtual Station Support".
# 2026-07-21 REFRAME (owner discussion + hydrologeez model eval): the two things earlier
# framed as co-equal hard blockers are NOT co-equal. The go-live dependency is one
# FI-compliant OPERATIONAL model for the basin (the modelling team's deliverable) + basin
# geometry (Plans 117/120, already in motion). The separate "floor/baseline" model is
# DEFERRABLE and, moreover, DOWNSTREAM of the model-paradigm choice (a conceptual model is
# its own floor). Crucially, a meaningful slice of SAP3 scaffolding is BUILDABLE NOW against
# the shipped FI interface — see §"SAP3 scaffolding (unblocked)". So this is no longer a
# fully-blocked stub: part is buildable, the rest waits on the model + geometry.
# A 2026-07-21 `/plan` run (Codex-backed) ESCALATED but surfaced two durable design gaps
# (§"Open design problems": P1 Branch-A has no skill gate; P2 the Step-8 climatology floor
# is unsatisfiable for zero-obs stations). Design content originated in the
# `⚠ REFERENCE-ONLY (ungauged plan)` sections of docs/plans/015-virtual-station-support.md.
category: B  # v1 Nepal feature
scope: design — UNGAUGED station support (no observations; models on NWP forcing + basin characteristics)
depends_on: [015]  # CALCULATED support (done). Live go-live also needs: an FI model (modelling team) + basin geometry (117/120).
---

# 016 — Ungauged Station Support

## Status: DRAFT — partly buildable now; live forecasting waits on the model + geometry

This plan was **split out of Plan 015** when that plan was narrowed to CALCULATED
(component-derived) stations and shipped (#109 storage + trigger, #112 Flow 2 step-2.5
derivation, #113 TOML onboarding).

**2026-07-21 reframe.** The plan originally treated a "baseline/floor model" and
"basin-outline upload" as two co-equal hard blockers. That was wrong on both counts (see
§Dependencies): the **floor is deferrable and downstream of the model-paradigm choice**,
and basin geometry arrives via the **117/120 import** path (not user upload). What a *live*
ungauged station actually needs is one **FI-compliant operational forecast model** for the
basin (the modelling team's deliverable) plus **basin geometry** (117/120). Meanwhile a
real slice of **SAP3 scaffolding is buildable now** against the shipped FI interface
(§"SAP3 scaffolding (unblocked)"), getting the system *model-ready* so the model plugs in.

The detailed design originated in the `⚠ REFERENCE-ONLY (ungauged plan)` sections of
`docs/plans/015-virtual-station-support.md` — consult those for the original framing.

## Problem

An **ungauged site** is a location on a river where forecasts are desired but **no gauge
is installed** — no observations exist, ever. The forecast model runs on **NWP forcing +
basin characteristics alone** (regionalized parameters or ML transfer learning). This is
distinct from a *calculated* station (Plan 015), which derives observations from gauged
tributaries via a weighted sum. The two share only the `GaugingStatus` axis; their flow
behaviour is almost entirely different.

The `GaugingStatus` enum (with `UNGAUGED`), the `StationConfig.gauging_status` field, and
the `stations.gauging_status` column already shipped in v0 (`1a88f92`). This plan is the
**flow + model + onboarding + basin-delineation** work to make an `UNGAUGED` station
forecastable — none of which is built.

## Dependencies & sequencing (reframed 2026-07-21)

### Two different "models" — don't conflate them
- **The operational forecast model** — the mountain model (conceptual and/or ML) that
  produces the ungauged forecast from NWP forcing + basin characteristics. **The modelling
  team's deliverable.** It's mountain hydrology (snow + glacier + elevation bands for
  Nepal) — a genuine R&D effort, not a library pick (see §Model paradigm).
- **The floor / baseline** — a *simpler, always-available* fallback (catches cycles where
  the operational model errors) that doubles as a skill-comparison reference. This is what
  the old "baseline-model" blocker (D5a) was really about.

### What a LIVE ungauged station actually needs
1. **One FI-compliant operational model for the basin** — modelling team. Until *some*
   model exists, an ungauged station literally cannot forecast, so it cannot go live. This
   is the real hard dependency for *live forecasting* (not for the SAP3 scaffolding below).
2. **Basin geometry** for NWP extraction — arrives via the **Plan 117 / 120 import** path
   (adjacent extractor → gateway → importer), already in motion. The earlier "basin-outline
   **user upload** + `security.md` file-upload gate" is a *separate, optional* path — needed
   only if operators must hand-supply custom outlines — and is **deferred** (likely not
   needed for Nepal v1, where basins come through the import path). It is **not** a blocker.

### The floor is deferrable — and downstream of the model-paradigm choice
Deferring the floor is *correct*, not a compromise: you cannot design a good floor until you
know what it is a floor *for*.
- If the modelling team picks a **conceptual** mountain model, it is essentially **its own
  floor** — a water-balance model always produces a hydrograph from valid forcing; it does
  not "fail" on out-of-distribution inputs the way an ML model can. A separate floor may
  then be **unnecessary**.
- If they pick an **LSTM/ML** operational model, a simpler conceptual floor beneath it is
  worth having — but that's a decision made *after* the paradigm is chosen.

Honest cost of deferral: an ungauged station whose operational model errors has no fallback
that cycle — a documented, Flow-4-monitored gap, and one that is largely moot if the
operational model is conceptual.

### Model paradigm — the modelling team's call (context, not a decision this plan makes)
Mountainous ungauged basins (e.g. the Nepal targets, and the HRU 12300 test HRU) need
**snow + glacier + elevation bands**. The `hydrologeez` package (differentiable PyTorch
GR6J + single-zone HBV, MIT, v0.1.0) was evaluated and is **out for production**: lumped
single-zone, **no snow, no glacier, no elevation bands** — inadequate for a Himalayan
regime — though its *differentiable-regionalization* idea (learn attributes → parameters
end-to-end; cf. δHBV) is worth keeping. The real fork is **conceptual mountain HBV
(snow+glacier+bands, regionalized)** vs a **regional LSTM** (SOTA for PUB but data-hungry,
less interpretable; better as a later Branch-A upgrade). Per the FI split this is
hydrosolutions'/Sandro's layer; the owner is discussing it with the modelling team. An
interpretable conceptual model as a comparison/floor is wanted but **not the current
priority** — hence the decoupling above.

## SAP3 scaffolding (unblocked — buildable now)

These depend only on the **shipped FI interface**, not on any specific model or on basin
geometry, so they can be built now as a self-contained slice that makes the system
*model-ready*. (Hold-at-PR, standard red-first + review — this is code, so schedule it when
the ungauged track is prioritized; the plan records it, it is not "do it now".)

1. **Step 8 go-live gate refactor (closes P2).** Replace the hardcoded per-station
   `CLIMATOLOGY_FALLBACK_MODEL_ID` requirement (`services/onboarding.py:924-946`) with a
   general "≥1 active artifact, **station- or group-scoped**" gate. Lets whatever model the
   team delivers satisfy go-live; also benefits group-scoped models generally.
2. **Zero-row `past_targets` plumbing — verify + harden.** Confirm a zero-row (correct
   schema) `past_targets` flows through Flow 1 input-prep, training, and hindcast without
   code assuming non-empty (the `/plan` review flagged this as *unverified*). Pure de-risk;
   needs no model. If a site assumes `height > 0`, fix it here.
3. **`gauging_status` branching.** `services/onboarding.py:_run_onboarding()` runs
   QC/baselines/flow-regimes unconditionally — branch so `UNGAUGED` skips steps 5.4–5.9;
   `services/scope.py:determine_training_scope()` filters by `station_status` only — also
   consider `gauging_status`.
4. **Donor leave-one-basin-out skill-gate framework (closes P1).** Build the mechanism for
   the Branch-A transfer-skill check (regionalize on N−1 gauged donors, score the held-out
   donor *as if ungauged*). The framework is buildable now; it needs a donor dataset
   (CAMELS-CH/GB dev, DHM Nepal) to actually *run*.

What is **NOT** in this slice (waits on the model + geometry): the full UNGAUGED onboarding
branch's basin-geometry + weather-source wiring (needs 117/120), and any *live-forecasting*
ungauged station (needs the operational model).

## Open design problems to resolve before build (surfaced by the 2026-07-21 `/plan` review)

These are **not** external-dependency blockers — they are internal design gaps a future
implementer must close. Recorded here so they aren't rediscovered late. (The `/plan` run
escalated trying to force this blocked stub to READY; these two findings are the durable
value from it.)

- **P1 — Branch A transfer-learning has no quality gate (safety).** D3 lists transfer
  learning (Flow 5.11 Branch A: apply an existing `GroupForecastModel` to the new station)
  as the *first* deployment path. But the empirical skill gate (LOBO CV / signed-off
  accepted risk) lives only in the single-/group-model promotion pipeline
  (`services/model_onboarding.py` `evaluate_skill_gate` + `flows/onboard_model.py`). Branch
  A never routes through it: its recipe is `add_station_to_group()`
  (`store/station_group_store.py`, a bare `INSERT ... ON CONFLICT DO NOTHING`) + re-using an
  already-active `GroupModelAssignment` — **zero** skill re-evaluation for the new basin.
  `discover_group_runs()` then picks the station up on the next forecast cycle and can issue
  live flood alerts from a model with no evidence it generalizes to that catchment. **This
  plan must define a Branch-A gate:** either a LOBO-style transfer-skill check *before*
  `add_station_to_group`, or a per-station signed-off accepted-risk record — and Flow 5.11's
  checklist must carry a quality-gate line item for Branch A (today it has none).

- **P2 — the Step 8 go-live floor is not satisfiable for a zero-observation station.**
  Onboarding Step 8 promotes a non-weather station only when a **per-station**
  `CLIMATOLOGY_FALLBACK_MODEL_ID` artifact is active
  (`services/onboarding.py:924-946`). An ungauged station cannot train that climatology
  floor (no observations), and group-scoped artifacts are **not** checked there. So the
  standard floor gate is unsatisfiable for ungauged stations. **This plan must replace the
  hardcoded per-station climatology gate with a general station-or-group active-artifact
  gate.** This is the buildable-now item #1 in §"SAP3 scaffolding (unblocked)" — it does
  **not** wait on the floor model (the operational model's own artifact, station- or
  group-scoped, satisfies it).

## Design (carried over from Plan 015 `⚠ REFERENCE-ONLY` sections)

### D1. Classification
`GaugingStatus.UNGAUGED` (already shipped). Orthogonal to `StationKind` — an ungauged
river site is still `StationKind.RIVER`. No enum/column/migration work here.

### D3. Model assignment — required
Ungauged stations **must** have forecast models. Deployed via **transfer learning**
(Flow 5.11 Branch A: an existing `GroupForecastModel` applied to the new station) or
**trained with regionalized parameters** (a `StationForecastModel` implementation detail,
not a new model type). Both rely on NWP forcing + basin characteristics. The standard
go-live precondition (`≥1 active model artifact`) applies; plus the baseline-model floor
from D5a.

### D4. Observation handling — none
No observations → no observation QC, no rating curves, no skill scores against
observations. Flow 2 **pre-filters** ungauged stations out (the existing `GAUGED`+operational
guard at `ingest_observations.py` already excludes them — confirm this still holds).

### D5. `past_targets` — zero-row DataFrame
`StationInputData.past_targets` stays `pl.DataFrame` (non-optional); for ungauged stations
it is a **zero-row DataFrame with the correct column schema** (timestamp + target columns).
Models must not assume `height > 0`. Per the ForecastInterface contract the orchestrator
does **no** input-sufficiency check — it delivers what's available (zero-row past_targets),
the model validates its own inputs and either forecasts or returns a structured
`ModelFailure`; on failure the orchestrator falls back to the baseline model (D5a). The
"return `ModelFailure`, don't raise" vs native-model behaviour is **already reconciled** by
the shipped boundary: `ForecastInterfaceAdapter._output_from_result()`
(`src/sapphire_flow/adapters/forecast_interface.py:369-373`) converts a returned
`ModelFailure` into a raised `ModelOutputError`, which SAP3's except-and-fallback backstop
catches — so no new FI-contract work is needed *for this question*. The 4-slot
input-contract wording in `architecture-context.md` ("Always present for stateful models")
must become: "Always non-None. May be zero-row for ungauged stations
(`GaugingStatus.UNGAUGED`)." See [[project_forecast_interface_contract]] /
[[feedback_forecastinterface_adherence_mandatory]] — any *genuine* contract gap → an
FI-repo issue, never a SAP3 workaround.

### D7. Basin delineation + file-upload security
See §Dependencies (basin geometry via 117/120 import; user upload optional/deferred).
HydroSHEDS vs HydroATLAS/MERIT DEM reconciliation deferred
(architecture-context.md currently references HydroATLAS+MERIT for Flow 0/5 basin
attributes; HydroSHEDS outlines are a related-but-distinct product). **Note:** the Plan 117
static-artifact import supplies basin **geometry + static attributes** — usable as the
extraction footprint for an ungauged basin — but it is **not** a source of historical
*forcing rows*. An ungauged station still needs a reanalysis extraction/backfill (over the
imported geometry) to obtain training/hindcast forcing; do not treat Plan 117 as a forcing
backfill source.

### D8 / WMO-WIGOS identity
`GaugingStatus` has no direct WMO-49 Vol III / WMO-168 enum precedent. WIGOS ID policy for
virtual/ungauged stations: `wigos_id = NULL` acceptable; **excluded from WIGOS exchange**.
`network` value follows the deployment convention (e.g. `"dhm"`) — identifies the
organizational owner, not a data source.

## Flow Impact (ungauged column, from Plan 015)

| Flow | Ungauged behaviour |
|------|--------------------|
| **Flow 0** (deployment onboarding) | Basin delineation for ungauged sites (HydroSHEDS / user upload) |
| **Flow 1** (forecast cycle) | Model runs with zero-row `past_targets`; input-prep passes a zero-row DataFrame with correct schema |
| **Flow 2** (obs ingest) | **Skipped** — pre-filter excludes ungauged stations |
| **Flow 3** (review) | No observation overlay |
| **Flow 4** (monitoring) | **Exclude** from observation-staleness checks — no expected observations |
| **Flow 5** (onboarding) | Modified UNGAUGED branch — see below. **v1 impl note:** `services/onboarding.py:_run_onboarding()` runs QC/baselines/flow-regimes for all stations unconditionally today — add `gauging_status` branching so ungauged skips steps 5.4–5.9 |
| **Flow 6/9** (training/retraining) | `past_targets` may be zero-row; model handles regionalized params. **v1 impl note:** `services/scope.py:determine_training_scope()` filters by `station_status` only — must also consider `gauging_status` |
| **Flow 7** (hindcast) | Zero-row `past_targets`; model must handle |
| **Flow 8/10** (skill) | **No skill scores** — no observations to verify against; cross-validation on regionalized params is future work |
| **Flow 11** (NWP archive) | No change — ungauged stations still consume NWP |
| **Flow 12** (reprocessing) | No impact — no observations to reprocess |

## Onboarding Flow (Flow 5) — UNGAUGED branch

- 5.1 Register station metadata (`gauging_status = 'ungauged'`)
- 5.2 Fetch catchment attributes (basin geometry from HydroSHEDS / user upload)
- 5.3 Configure weather-source mappings (NWP extraction — **critical**, models depend
  entirely on NWP forcing)
- **Skip 5.4–5.9** (no historical obs, no QC, no rating curves, no baselines, no flow
  regimes)
- 5.10 Assign model (required)
- 5.11 Model readiness — Branch A: transfer learning with an existing group artifact; or
  Branch B/C: train a new station/group model with regionalized parameters
- 5.12 Go-live — **not** the hardcoded per-station climatology floor (unsatisfiable here,
  §P2); use the D5a ungauged floor gate — an active group-scoped artifact + active group
  assignment, or the baseline-model floor. Branch A additionally needs its own quality
  gate (§P1) before this point.

Checklist: ✅ metadata · ✅ catchment attributes · ✅ weather source mapped ·
⬜ historical observations (N/A) · ⬜ baselines/flow regimes (N/A) · ✅ ungauged floor gate
(§P2, D5a) · ⬜ Branch-A transfer-skill gate (§P1, if Branch A) · ⬜ alert thresholds
(optional).

## Standards updates required (when built)

- **`wmo.md`** — virtual/ungauged-station identity: no WMO-49/WMO-168 enum precedent;
  `wigos_id = NULL` policy; WIGOS-exchange exclusion.
- **`architecture-context.md`** — the 4-slot input-contract wording (D5); HydroSHEDS vs
  HydroATLAS/MERIT reconciliation (D7).
- **`security.md`** — File Upload section (MIME / size / geometry-complexity) +
  authorization-matrix entry — **only if** the optional basin-outline *user-upload* path is
  pursued (deferred; the 117/120 import path needs no upload). Not required for the import
  path or for the SAP3 scaffolding slice.

## Non-goals

- **Calculated (component-derived) stations** — DONE in Plan 015 (#109/#112/#113).
- **The operational mountain forecast model** — the modelling team's / hydrosolutions'
  layer (snow+glacier+bands, regionalized). This plan delivers the SAP3 inputs + gates; it
  does not author the model.
- **The floor/baseline model** — deferrable and downstream of the model-paradigm choice
  (§Dependencies); not designed here.
- **Basin-outline user upload + its security gate** — optional/deferred; basin geometry
  comes via the 117/120 import path.

## Related plans

- **015** — Calculated Station Support (the sibling half, complete).
- **117 / 120** — basin/static artifact architecture + importer (the basin-geometry path;
  117 READY, 120 DRAFT, in motion). Supplies the extraction geometry — **not** forcing rows.
- **Operational mountain model** — modelling team / hydrosolutions; paradigm under
  discussion (conceptual HBV-glacier-bands vs regional LSTM). Hard dependency for *live*
  ungauged forecasting, not for the SAP3 scaffolding slice.
- **Floor/baseline model** — deferrable; decide *after* the operational-model paradigm.
- **Basin-outline user-upload + security.md File Upload gate** — optional/deferred.
