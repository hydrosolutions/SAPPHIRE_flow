---
status: DRAFT
created: 2026-07-21
updated: 2026-07-21
# STUB split out of Plan 015 (which shipped CALCULATED support, #109/#112/#113). This plan
# owns UNGAUGED station support — the half of the original "Virtual Station Support" that is
# HARD-BLOCKED on two unbuilt plans (baseline-model design + basin-outline upload/security).
# The design content below was carried over from the `⚠ REFERENCE-ONLY (ungauged plan)`
# sections of docs/plans/015-virtual-station-support.md; consult those for the original
# framing. Not ready to build — see §Blockers. Do NOT implement until the blockers clear.
# A 2026-07-21 `/plan` run (Codex-backed) ESCALATED — a blocked stub can't be forced to
# READY — but surfaced two durable design gaps now recorded in §"Open design problems"
# (P1 Branch-A transfer-learning has no skill gate; P2 the Step-8 climatology floor is
# unsatisfiable for zero-obs stations) + a few fact corrections (FI adapter, Plan 117).
# The workflow's speculative full-design expansion was deliberately discarded; this stays a stub.
category: B  # v1 Nepal feature
scope: design — UNGAUGED station support (no observations; models on NWP forcing + basin characteristics)
depends_on: [015]  # CALCULATED support (done). Plus two UNBUILT plans — see §Blockers.
---

# 016 — Ungauged Station Support

## Status: STUB / DRAFT — hard-blocked, do not build

This plan was **split out of Plan 015** when that plan was narrowed to CALCULATED
(component-derived) stations and shipped (#109 storage + trigger, #112 Flow 2 step-2.5
derivation, #113 TOML onboarding). Ungauged support was carved off because it is
**hard-blocked on two unbuilt plans** (§Blockers) — unlike calculated stations, it is not
self-contained and cannot be built or meaningfully tested yet.

The detailed design carried over from the `⚠ REFERENCE-ONLY (ungauged plan)` sections of
`docs/plans/015-virtual-station-support.md` — read those alongside this stub. This
document consolidates them into the ungauged plan's home and states what must land first.

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

## Blockers (why this is deferred, not buildable now)

Ungauged support cannot ship until **both** land:

1. **Baseline-model design (D5a).** An ungauged station has no observations, so it needs a
   **baseline model** — a simple, always-available forecast (climate norm, linear
   regression) that is both the skill-comparison reference and the initial operational
   model until a trained model exists. The baseline-model plan (model types, assignment
   policy, Flow 1 execution pattern) is **unbuilt**. **Hard dependency:** it must complete
   before ungauged support ships. (A baseline/fallback model may be a desirable
   cross-cutting default for *all* station types — that is the baseline-model plan's
   concern, not this one.)

2. **Basin-outline upload + `security.md` file-upload gate (D7).** Ungauged stations need
   basin outlines for NWP extraction, via **HydroSHEDS** (our own pre-computed product) or
   **user upload** (GeoJSON/Shapefile). User upload requires a `security.md` **File Upload**
   section (MIME validation, size limits, geometry-complexity limits, authorization) —
   **unbuilt**. **Hard gate:** the security.md §File Upload section + the authorization-matrix
   entry must be merged **before** basin-outline upload implementation begins.

Until both exist, this plan stays DRAFT and no subagent builds from it.

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
  hardcoded per-station climatology gate with the D5a ungauged floor gate** (accept the
  group-scoped active artifact + active group assignment path, or the baseline-model floor).
  This couples directly to blocker B1.

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
See §Blockers item 2. HydroSHEDS vs HydroATLAS/MERIT DEM reconciliation deferred
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

- **`security.md`** — File Upload section (MIME / size / geometry-complexity) +
  authorization-matrix entry for basin-outline upload. **Hard gate** (see §Blockers).
- **`wmo.md`** — virtual/ungauged-station identity: no WMO-49/WMO-168 enum precedent;
  `wigos_id = NULL` policy; WIGOS-exchange exclusion.
- **`architecture-context.md`** — the 4-slot input-contract wording (D5); HydroSHEDS vs
  HydroATLAS/MERIT reconciliation (D7).

## Non-goals

- **Calculated (component-derived) stations** — DONE in Plan 015 (#109/#112/#113).
- The **baseline-model design** itself — a separate plan this one depends on (D5a).
- The **basin-outline upload feature + its security gate** — a separate plan/standard this
  one depends on (D7). This plan consumes them; it does not design them.

## Related plans

- **015** — Calculated Station Support (the sibling half, complete).
- **Baseline-model plan** — TBD/unbuilt (hard blocker, D5a).
- **Basin-outline upload + security.md File Upload gate** — TBD/unbuilt (hard blocker, D7).
