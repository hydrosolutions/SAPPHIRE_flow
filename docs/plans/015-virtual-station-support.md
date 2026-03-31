---
status: DRAFT
created: 2026-03-30
scope: design — virtual station types, calculated station formulas, onboarding flow impact
depends_on: []  # target: v1
---

# 015 — Virtual Station Support

## Problem

SAPPHIRE Flow currently defers virtual stations to v2.0 (`architecture-context.md`).
Virtual station support is a core modelling capability needed for v1 — one of our
modellers specialises in ungauged catchment prediction and calculated station derivation.
This plan brings the design forward to v1.

## Two Kinds of Virtual Stations

1. **Ungauged sites** — No observations exist. A location on a river where forecasts
   are desired but no gauge is installed. The model runs on NWP forcing and basin
   characteristics alone (regionalized parameters or ML transfer learning).

2. **Calculated stations** — Derived from gauged tributaries. Typical example: reservoir
   inflow = weighted sum of upstream gauged tributaries. Common in Central Asia.
   Formula: `Q_virtual = Σ(wᵢ × Qᵢ)` where `Qᵢ` are observed/forecast flows.

## Design Questions

1. **Station type enum** — Extend `StationKind` (or create new enum) to distinguish
   `GAUGED`, `UNGAUGED`, `CALCULATED`. Affects which flows apply (e.g. calculated
   stations skip model prediction, ungauged stations skip observation QC).

2. **Calculated station formula** — How to represent the aggregation formula.
   Options: (a) config-driven weighted sum, (b) expression DSL, (c) Python callable
   registered per station. Weighted sum covers 90%+ of cases.

3. **Observation handling** — Ungauged stations have no observations → no observation
   QC, no skill scores against observations, no rating curves. Calculated stations
   have "observations" derived from component stations → need propagated QC flags.

4. **Model assignment** — Ungauged stations still need forecast models (regionalized).
   Calculated stations may not need a forecast model if they're purely derived from
   component forecasts.

5. **Basin delineation** — Virtual stations need basin outlines for NWP extraction.
   Options:
   - **HydroSHEDS API** (paid) — pre-computed basin outlines worldwide. Could serve as
     quality-check for user-uploaded outlines.
   - **User upload** — allow uploading custom basin outlines (GeoJSON/Shapefile).
   - Both paths should be supported. HydroSHEDS integration could be a paid add-on.

6. **Onboarding flow impact** — Flow 5 (station onboarding) needs virtual station
   branches. Flow 0 (deployment onboarding) / organization onboarding could integrate
   HydroSHEDS for basin delineation.

## Urgency

v1 target. Core modelling capability — one team member specialises in this area.
Design should be completed before v1 station onboarding is finalised.

## Origin

Extracted from plan 011 §B. Promoted from v2.0 to v1.
