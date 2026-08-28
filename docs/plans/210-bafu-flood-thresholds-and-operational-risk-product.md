---
status: DRAFT
created: 2026-08-28
plan: 210
title: BAFU flood thresholds — parse what we already archive, and the operational risk product the Flow Map needs
scope: Extract the BAFU flood danger levels already present in the archived forecast plot payloads, persist them in the existing `station_thresholds` table, and decide what an operational threshold-risk export owes the SAPPHIRE-flow-map consumer. Does NOT change the forecast cycle, does NOT make forecasts sub-daily, does NOT lift the Plan 111 G1 licence gate.
depends_on: [111, 204]
blocks: []
source: Cross-repo seam review 2026-08-28 (Plan 204 § Seam review) + owner correction the same day that the threshold values are already on the mac mini
---

# Plan 210 — the thresholds are already in the archive; nothing parses them

## Status

**DRAFT — a stub for the owner to confirm or reshape.** Not for implementation. This exists so the
2026-08-28 findings are not lost between sessions; it is deliberately under-specified, because two
of its decisions are the owner's and one is BAFU's.

## Why this plan exists

The SAPPHIRE-flow-map project has shipped a feature-gated **forecast mode**
(`SAPPHIRE-flow-map@8f61d9d`, behind `VITE_ENABLE_FORECAST_MODE`) whose contract
(`schemas/forecast-snapshot-v2.schema.json`) makes three things **required** that SAPPHIRE Flow does
not currently emit: a station `threshold` (id/label/value_m3s/source), a `summary.risk_level`, and a
`summary.exceedance_probability`.

A cross-repo review first concluded we "cannot produce any of them". **That was wrong**, and the
correction is the whole reason this plan is small rather than large.

## What we already have (measured, not assumed)

**BAFU publishes the flood danger levels inside the forecast plot payloads we already archive** —
`plot.layout.shapes`, coloured `rect`s whose `y0` is that level's lower bound in m³/s.

Measured over the live mac-mini archive 2026-08-28 (12,655 raw files):

| fact | value |
|---|---|
| stations with `*_q_forecast_*.json` | **40** |
| stations carrying a complete 4-level threshold set | **40 of 40** |
| operational SAPPHIRE stations covered | both — `2009`, `2091` |
| colour → danger level | `#FFFF00`→2, `#FF9900`→3, `#F7001D`→4, `#800000`→5 |
| example `2009` (Porte du Scex) | 700 / 1000 / 1200 / 1400 m³/s |
| example `2091` (Rheinfelden) | 2500 / 3000 / 3600 / 4500 m³/s |
| code that parses any of it | **none** |

Level 1 is the unbanded region below the lowest bound. The **top** of the highest band is the plot's
y-axis ceiling, **not** a threshold — do not read it as one.

Storage already fits: `station_thresholds` (`db/metadata.py:306-334`) holds
`danger_level`/`parameter`/`value`/`source`, and `PgStationStore.store_thresholds()`
(`store/station_store.py:189`) exists. **It has zero production callers** — only three test modules
call it. The writer was built and never wired. Live DB: 0 threshold rows, 0 alerts.

An exceedance-probability algorithm also already exists (`services/alert_strategy.py:33-96`, member
fraction or quantile-CDF interpolation).

## The four things that make this real work, not a copy job

1. **This is scraped from a rendering, not a data API.** The values come from plot fill colours. A
   BAFU restyle changes them and the extraction breaks **silently**. Any parser here must *validate*
   and **fail loudly** — never fall back to a best-effort or partial read. A wrong flood threshold is
   worse than a missing one.
2. **The colour → danger-level mapping is INFERRED.** It has not been confirmed against BAFU's
   published 5-level scale. Confirm before it drives anything.
3. **Licence — and this gates the consumer regardless of engineering.** These come from the same
   research-only `/plots/*.json` whose `licence_status` is `unresolved`. A **public** operational map
   is *publication*, exactly what that marker excludes. **Plan 111's G1 letter is still unsent.**
   Parsing and storing for internal use is defensible; publishing is not, until G1 answers.
4. **Vocabulary mismatch.** Producer levels are `Low … Very High` (`config.toml:71-120`); the map's
   policy is `below_threshold|watch|likely|severe`. Mapping BAFU's 5 levels onto the map's 4 classes
   is a **hydrological decision**, not a rename.

## Accepted for now (owner, 2026-08-28)

**Forecasts stay daily.** The map's 48-hour horizon therefore holds **two points**, not a sub-daily
trajectory. The owner has accepted this for now; **sub-daily forecasting is upcoming work on the
producer side** and is explicitly *not* in this plan. Whatever this plan emits must not encode an
assumption that the step is daily — the consumer contract should carry the native step, so a later
sub-daily cycle needs no contract change.

## Open questions the owner must answer before this can be sliced

1. **Is the threshold parse a new adapter concern or a backfill tool?** Thresholds change rarely;
   the forecast payload arrives many times a day. Parse-on-every-ingest is wasteful, parse-once is
   stale-prone. (Suggested: parse on ingest, write only on change, record `observed_at`.)
2. **Does the operational product extend `forecast-lab-snapshot`, or is it a separate export?** The
   Forecast Lab contract is explicitly research-only with no alerting; overloading it would erase a
   deliberate boundary. (Suggested: separate export. Plan 204's review already established the two
   are different products.)
3. **The 5→4 level mapping**, per question 4 above. Hydrologist's call.
4. **Do we publish BAFU-derived thresholds at all, or only our own?** Bound to G1.

## Not in scope

Sub-daily forecasting. Changing the forecast cycle. Routing or reach-level colouring (the map's own
plan already forbids it without a validated routing product). Lifting the G1 gate. Anything in
Plan 204, which is orthogonal and proceeds independently.
