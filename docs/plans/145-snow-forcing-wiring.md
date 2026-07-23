---
status: DRAFT
created: 2026-07-23
plan: 145
title: Snow (JSNOW) forcing wiring — fetch → store → broadcast, + canonical snow aggregation
scope: Make JSNOW snow products (swe / snow_depth=hs / snowmelt=rof) usable as forcing for any model. Wire the snow FORECAST fetch into the forecast cycle + WeatherForecastStore (today `fetch_snow_forecast` has zero callers, so the deterministic-snow broadcast path is a permanent no-op), wire snow REANALYSIS for the antecedent/past channel, and fix the aggregation fallback so `swe`/`snow_depth` aggregate as states (MEAN/LAST) and `snowmelt`/`rof` as a flux (SUM). Deterministic single stream → broadcast across ensemble members. Carved out of Plan 139 W7; unblocks 139 and 144. Forcing ingest.
depends_on: [082]
blocks: [144, 139]
supersedes: []
---

# Plan 145 — Snow (JSNOW) forcing wiring

## Status
**DRAFT — carved out of Plan 139 W7 (owner 2026-07-23) as a standalone, buildable-now plan.** Generic (any
JSNOW-fed model), not tied to the 12300 target/onboarding that blocks the rest of 139. Needs `/plan` before
READY. Grounded in [[reference_recap_gateway_12300_products]] (JSNOW is a **single deterministic stream** — no
ensemble members; hourly; swe subscribed for 12300, hs/rof newly subscribed with forecast still materializing).

## Problem — snow forcing never reaches a model
**This is a SAP3 plumbing gap, not a gateway/data gap.** The gateway *does* deliver snow forecasts — a live probe
returned `snow.forecast(swe)` = 241 hourly rows for 12300 (2026-07-23). The data and the client methods both
exist; SAP3 simply never fetches them. Three verified gaps:
1. **SAP3 never fetches future snow.** The production forecast fetch `fetch_forecasts` (`run_forecast_cycle.py:886`)
   iterates **`_ifs_variables()` only** (swe/hs/rof have a `snow_name` but no `ifs_name`, so they are excluded);
   the dedicated `RecapGatewayForecastAdapter.fetch_snow_forecast` (`recap_gateway.py:830`, uses
   `_snow_variables()` at `:859`) has **zero production callers**. So nothing writes deterministic snow rows into
   the `WeatherForecastStore`, and the
   Plan 082 Task 2H-snow broadcast (`operational_inputs._broadcast_deterministic_features_to_members`, the
   "broadcast deterministic snow across every real ensemble member" step) is a **permanent no-op**. The
   `future_known nwp/swe` (and hs/rof) channel is never fed at inference.
2. **Antecedent (past) snow is not wired** either — no production caller fetches snow **reanalysis** for the
   lookback/antecedent-state channel a snow-fed model needs.
3. **Snow aggregation is wrong/missing.** `_V0_AGGREGATION_FALLBACK` (`training_data.py:29-40`) carries only the
   legacy `snow_water_equivalent: MEAN` key — not the canonical Recap names. A `swe` column falls through to the
   unknown-parameter MEAN fallback (coincidentally OK for a state, but silent), and **`snowmelt`/`rof` — a
   flux — would wrongly MEAN instead of SUM**. SAP3 does **not** read the FI-declared `aggregation` field
   (confirmed), so this fallback table is the real control.

## What already exists — 145 fills the wiring, doesn't rebuild
- **Variable mapping** — `RECAP_VARIABLES` (`recap_gateway.py`): `snow_depth`→`hs`, `snowmelt`→`rof`, `swe`→`swe`
  (via `snow_name`), with `convert=None` as the deliberate "snow units unresolved" sentinel.
- **The broadcast path** — `operational_inputs._broadcast_deterministic_features_to_members` (Plan 082 2H-snow)
  already broadcasts member_id=None snow across real IFS members; it just has no data to broadcast.
- **The store + assembly** — `WeatherForecastStore` + `assemble_station_operational_inputs`; snow mirrors the
  existing IFS forecast fetch→store→assemble path (`run_forecast_cycle.py fetch_forecasts` at `:704`).
- **The client** — `recap_client.snow.{forecast,reanalysis,operational,gap_fill}` (pin 9340e40, PR #127).

## Design decisions
- **D1 — JSNOW is deterministic (member_id=None); broadcast, don't fan out.** Snow has one forecast stream (no
  perturbed members). Snow rows are written as deterministic (member_id=None) and **broadcast across the IFS
  ensemble members** by the existing 2H-snow path — snow forcing is identical for all 51 members. (Aligns with
  Plan 144: snow is the same across the ensemble.)
- **D2 — wire BOTH channels.** Future snow via `fetch_snow_forecast` → `WeatherForecastStore` (feeds
  `future_known`); antecedent snow via snow **reanalysis** → the past/observed channel (feeds `past_known` /
  lookback). Mirror the IFS forecast/reanalysis wiring already in the cycle.
- **D3 — fix the aggregation fallback (before any snow model ships).** Add to `_V0_AGGREGATION_FALLBACK`:
  `swe` + `snow_depth` → **MEAN/LAST** (states); `snowmelt` + `rof` → **SUM** (flux). This governs both training
  and the operational time_step aggregation.
- **D4 — resolve snow units (retire the `convert=None` sentinel).** Settle hs/swe (metres) and rof (flux)
  magnitudes + the canonical SAP3 unit, so the recap adapter emits canonical snow like it does for precip/temp
  (m→mm, K→C) and the FI adapter can label/validate them. Snow-unit magnitudes are explicitly unresolved today.
- **D5 — subscription/availability tolerance.** Only-subscribed variables return; unsubscribed raise
  `subscription_not_found` (e.g. hs/rof before subscription). A missing snow forecast must degrade cleanly
  (deterministic-snow absent → broadcast no-op → model runs without the snow channel iff not required), not abort
  the cycle. (hs/rof forecast for 12300 was still materializing at draft time.)

## Non-goals
- The ensemble fan-out / two-track orchestration (Plan 144). The snow *model* (aquacast / 139). Rating/obs (DHM).
- Any new gateway endpoint (uses the existing `snow.*` client methods).

## Phases (sketch — harden in `/plan`)
1. **Aggregation fallback fix** (D3) — smallest, unblocks correct snow aggregation everywhere; red-first test that
   `rof` SUMs and `swe`/`snow_depth` MEAN/LAST.
2. **Future-snow wiring** (D2) — call `fetch_snow_forecast` in the forecast cycle, write deterministic snow rows
   to `WeatherForecastStore`; integration test: snow fetch → store → broadcast → member-suffixed inputs present.
3. **Antecedent-snow wiring** (D2) — snow reanalysis into the past/observed channel.
4. **Snow units** (D4) — canonical conversion, retire `convert=None`; adapter emits canonical, FI labels.
5. **Degradation** (D5) — missing/unsubscribed snow forecast is a clean no-op, not a cycle abort.

## Dependencies
- **082** (gateway operational + the 2H-snow broadcast path + polygon bindings). Consumed by **144** (any
  JSNOW-fed track) and **139** (the 12300 SWE model). Client pin ≥ 9340e40 (#127).

## Open items / to confirm
- **hs/rof forecast availability** — subscription added for 12300; forecast still materializing (probed
  `source_data_missing` 2026-07-23). Re-verify before the future-snow integration test.
- **Snow unit magnitudes** (D4) — confirm hs/swe (metres) + rof flux units + canonical SAP3 target.
- **Past-snow channel shape** — whether antecedent snow rides the weather-history path or a dedicated fetch.
- **Relationship to Plan 139 W7** — 145 absorbs W7's two gaps; update 139 to defer to 145 (mark W7 → Plan 145).
