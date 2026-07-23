---
status: DRAFT
created: 2026-07-23
plan: 143
title: DHM/v1 basin + gauge onboarding (GeoPackage → N gauges → forecast-ready)
scope: End-to-end onboarding of a DHM/Nepal basin delivery — a GeoPackage carrying N gauges — into a forecast-ready state: geometry import (Plan 120), gauge/station records + rating tables, recap gateway polygon bindings, and dataset subscriptions. Turns "we received a GeoPackage" into "these gauges forecast through the pipeline." Flow 0/5 (onboarding).
depends_on: [117, 120, 082]
blocks: [144]
supersedes: []
---

# Plan 143 — DHM/v1 basin + gauge onboarding

## Status
**DRAFT — aligned with owner 2026-07-23; needs `/plan` before READY.** 12300 (gauge `123` → geometry `g_123`)
is the worked example; the general case is a GeoPackage with **many gauges**, not one. Grounded in
[[reference_recap_gateway_12300_products]], [[project_basin_static_artifact_plan117]], and the recap
polygon-binding store.

## Problem
DHM/Nepal basins arrive as **GeoPackages, each carrying N gauges** (single-gauge is the rare edge). To make
those gauges forecast-ready, onboarding must, **per gauge in the package**, establish: basin geometry, the
gauge/station record (+ rating table for level→discharge), the recap gateway binding, and the dataset
subscriptions. Today only the geometry-import half exists (Plan 120); the gauge/binding/subscription steps are
undrafted, and 12300 was subscribed by hand.

## What already exists (do not rebuild)
- **Geometry import — Plan 120 importer.** Already **dissolves a multi-gauge package into N basins** with
  per-basin versioning + provenance + `g_<gauge_id>` naming. So the geometry side of a multi-gauge package is
  built; Plan 143 consumes it, not reimplements it.
- **Gateway polygon-binding store** (`recap_gateway_polygon_bindings` + `adapters/recap_gateway.py`) — reads the
  `g_<gauge>` value column correctly; the binding rows are what map a gauge to its gateway geometry.
- **Static-artifact boundary** (Plan 117) — per-basin static features.

## Design decisions (owner-aligned 2026-07-23)
- **D1 — the unit of onboarding is a GeoPackage → N gauges.** One delivery fans out to N basins/gauges; every
  onboarding step is per-gauge. Do not assume one gauge per package.
- **D2 — per-gauge onboarding steps** (the new work beyond Plan 120 geometry):
  1. **Gauge/station record** — create the station for each gauge (id `123`, the DHM gauge identity), linked to
     its imported basin.
  2. **Rating table** — level→discharge conversion per the DHM-obs contract (versioned From/To rating windows;
     the DHM-questionnaire track). Gauges deliver **level**; we store discharge via the rating.
  3. **Gateway binding** — register `gauge_id ↔ g_<gauge_id> ↔ hru_code` in `recap_gateway_polygon_bindings`
     (who assigns the `hru_code`, e.g. 12300, is an open item — gateway-side onboarding).
  4. **Dataset subscriptions** — which products the gauge receives (era5, ifs fc/pf, jsnow swe/hs/rof). 12300
     was subscribed by hand; onboarding should drive this (see open item on the subscription API).
- **D3 — onboarding is an orchestrated flow** (Flow 0/5): import geometry (Plan 120) → create stations →
  register bindings → subscribe → verify forecast-readiness (a gauge can be resolved end-to-end: binding
  present, a probe fetch returns data). Idempotent + re-runnable (a re-delivered package updates, not duplicates).
- **D4 — tenant/identity boundary.** DHM/Nepal is multi-tenant (east/west). Onboarding must tag each gauge's
  records with the owning tenant; this ties into the (undrafted) auth/RBAC/tenant-write-isolation work
  (Plan 106 D5-3).

## Non-goals
- The forecast pipeline itself (Plan 144). Rating-table *format* design (DHM-obs track / questionnaire). Auth/RBAC
  build (its own plan). Re-implementing Plan 120 geometry import.

## Phases (sketch — harden in `/plan`)
1. **Per-gauge station + basin link** — create stations from a package's gauge set, linked to Plan-120 basins.
2. **Gateway binding registration** — `gauge_id ↔ g_<id> ↔ hru_code` rows, idempotent.
3. **Subscription step** — drive dataset subscriptions (pending the gateway subscription API; manual fallback).
4. **Rating-table ingest** — level→discharge (depends on the DHM-obs contract).
5. **Onboarding flow + readiness gate** — orchestrate 1–4; verify each gauge resolves end-to-end.

## Dependencies
- **120** (basin-static importer — geometry, multi-basin dissolve) · **117** (static-artifact boundary) ·
  **082** (gateway operational + binding store). DHM-obs / rating-table track (questionnaire). Feeds **144**
  (forecasting needs onboarded, bound, subscribed gauges).

## Open items / to confirm
- **Subscription API:** is there a gateway client method to subscribe a geometry to datasets, or is it
  gateway-side/manual? (12300 was done by hand.) Determines whether D2.4 is automatable now.
- **`hru_code` assignment:** who mints the gateway `hru_code` (12300) for a new gauge — DHM, the gateway dev, or
  us? Governs binding registration order.
- **Tenant identity:** how east/west tenancy is expressed on gauge records (ties to auth/RBAC, Plan 106 D5-3).
- **Rating-table delivery:** format + cadence (DHM questionnaire still out) — blocks D2.2/Phase 4 values, but the
  rest is buildable against fakes.
