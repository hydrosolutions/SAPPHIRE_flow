---
status: DRAFT
created: 2026-07-23
plan: 146
title: Antecedent (past) snow reanalysis channel — provenance + owning ingest flow + read-side routing
scope: Make historical/antecedent JSNOW (swe/snow_depth/snowmelt) reach a model's `past_dynamic` frame end to end. Add a supported `ForcingSource` provenance for the recap snow-reanalysis literal, a DEDICATED recap-reanalysis ingest flow/schedule that fetches + persists snow reanalysis to `HistoricalForcingStore` (the blocker: today no production caller runs it), and read-side hybrid routing so a stored snow series is selectable by the training / hindcast / live-inference read path. Split from Plan 145 (which owns the future channel). Forcing ingest.
depends_on: [082, 145]
blocks: [139, 144]
supersedes: []
---

# Plan 146 — Antecedent (past) snow reanalysis channel

## Status
**DRAFT — split from Plan 145 (owner 2026-07-23).** This is the load-bearing half of the original snow-forcing
plan: the antecedent channel needs a new provenance source, a read-side snow tier, and — the blocker — an
**owning ingest flow/schedule** (today the snow-reanalysis adapter has zero production callers). **D2 DECIDED
(owner 2026-07-24): a SCHEDULED daily watermark-bounded ingest deployment** (not backfill-first — the operational
antecedent reads from the persisted store, and backfill-first was fiddly in the prior project). Depends on
Plan 145 for the canonical snow aggregation fix (the training/read path uses it). Needs a confirming `/plan`
before READY. Grounded in [[reference_recap_gateway_12300_products]].

## Problem — antecedent snow is not fetched, not provenance-supported, and not read-routed
A model needing snow **lookback** (antecedent SWE/depth/melt in its `past_dynamic_features`,
`operational_inputs.py:410-431`) gets nothing today. Three coupled gaps:
1. **No production caller fetches snow reanalysis.** `RecapGatewayReanalysisAdapter.fetch_reanalysis`
   (`recap_gateway.py:934-963`, `_rows_for_variable` at `:987-998`) *can* fetch `snow.reanalysis`, but the
   production weather-history ingest (`ingest_weather_history.py:402-417`) builds **only** the MeteoSwiss adapter
   (`build_production_reanalysis_adapter`, `:192`) — nothing runs the recap reanalysis adapter. **This is the
   blocker:** a standalone task with no owning flow/schedule leaves the gap intact.
2. **No supported provenance.** The persisted literal `recap_snow_reanalysis` (`recap_gateway.py:273`) is **not a
   `ForcingSource` member** and has **no `SOURCE_ATTRIBUTIONS` entry** (`forcing_sources.py:18-47` — only
   MeteoSwiss/CAMELS/NWP_ARCHIVE members), so a persisted snow row has no supported provenance/attribution.
3. **No read-side routing.** The hybrid read chain wires **MeteoSwiss-only** per-parameter priority chains
   (`hybrid_reanalysis_factories.py:37-46,60-75`, Plan 115b4 §5B) — no snow tier — so even a stored snow series is
   never selected and never reaches `past_dynamic` for training, hindcast, or live inference.

## What already exists — 146 fills the wiring, doesn't rebuild
- **The reanalysis adapter** — `RecapGatewayReanalysisAdapter.fetch_reanalysis` already routes `snow.reanalysis`
  → `RawHistoricalForcing` (`recap_gateway.py:934-963`); it lacks a production caller + provenance + read-routing.
- **The stores + readers** — `HistoricalForcingStore`, `PerSourceStoreReader`, the hybrid factory
  (`hybrid_reanalysis_factories.py`). 146 adds a snow tier, does not rebuild the read stack.
- **The client** — `recap_client.snow.reanalysis` (pin ≥ 9340e40, #127).
- **Plan 145** — the canonical snow aggregation fix (`swe`/`snow_depth` MEAN, `snowmelt` SUM) used by the
  training/read resample.

## Design decisions
- **D1 — a DEDICATED recap-reanalysis ingest path, NOT the MeteoSwiss flow.** The `ingest_weather_history` flow
  types its adapter as `_ReanalysisAdapter` requiring `fetch_products(...)` + `discover_rhiresd_boundary()` and
  unconditionally does MeteoSwiss RhiresD boundary discovery (`:83-102,457-521`). `RecapGatewayReanalysisAdapter`
  exposes only `fetch_reanalysis(station_configs, start, end, parameters)` — it satisfies neither method — so it
  cannot drop into that flow. 146 adds a **standalone recap-reanalysis ingest flow**.
- **D2 — a SCHEDULED recap-reanalysis ingest deployment (DECIDED, owner 2026-07-24).** The ingest is a real
  Prefect **deployment on a daily cron** (`SCHEDULE_INGEST_SNOW_REANALYSIS`, mirroring
  `SCHEDULE_INGEST_WEATHER_HISTORY = "0 6 * * *"`, `register_deployments.py:40`), **watermark-bounded** (fetch only
  the newly-landed reanalysis beyond the last-ingested timestamp, respecting the ~7-day JSNOW reanalysis lag).
  **NOT a one-shot backfill** — the operational antecedent is **read from the persisted store**
  (`operational_inputs.py:410-421` reads `past_dynamic` from the historical forcing store), so a backfill-only
  ingest would freeze the antecedent at its run date and every later cycle would read staler lookback. The
  **initial history backfill is the *same flow* run with an explicit wide window** (a `start`/`end` param), not a
  separate mechanism — mirroring `ingest_weather_history`. Rationale (owner): backfill-first was fiddly in the
  prior SAPPHIRE project; go straight to the scheduled pathway. Acceptance test invokes the **actual entry point**
  (the deployed flow), not the task in isolation, proves persistence, and asserts the watermark advances so a
  second run is incremental (no re-fetch of already-ingested days).
- **D3 — supported provenance for `recap_snow_reanalysis`.** Add one `ForcingSource` member for the persisted
  literal (`recap_gateway.py:273`) + its `SOURCE_ATTRIBUTIONS` entry (`forcing_sources.py:18-47`); round-trips
  through the provenance layer. (The sibling `recap_era5_land_reanalysis` literal is out of scope — no live
  consumer; belongs to whichever plan wires ERA5-land read-routing.)
- **D4 — read-side snow tier reaches all three consumers.** Add per-parameter read routing for
  `swe`/`snow_depth`/`snowmelt` (extend the hybrid factory / `PerSourceStoreReader`,
  `hybrid_reanalysis_factories.py:37-46,60-75`) so a stored snow series is selectable — and prove it reaches
  `past_dynamic` in **each** consumer separately: **training** (`training_data.py:178`), **hindcast**
  (`hindcast.py:287`), and **live input assembly** (`operational_inputs.py:410`). Adapter/factory tests alone are
  insufficient — the claim is that the stored series appears in each consumer's `past_dynamic`.
- **D5 — snow-reanalysis degradation (mirror Plan 145's forecast containment).** `fetch_reanalysis` loops
  requested variables and aborts on the first raised error (`:954,987`) — the same partial-loss problem 145 fixes
  for forecasts. Contain per-`(hru, variable)`, preserve accumulated rows, and define whether a fully-unavailable
  historical window is a **successful no-op**, a **degraded** task result, or a **failure**. Reuse the snow error
  boundary from Plan 145 (do not remap `_map_recap_error` globally).
- **D6 — snow units stay unresolved (shared with 145).** `convert=None` retained; the antecedent series flows
  through with correct shape/provenance, not canonical magnitudes. The unit-resolution follow-on gates onboarding
  a snow-fed FI model.

## Non-goals (owned elsewhere)
- The FUTURE snow-forecast channel + the aggregation fix (**Plan 145**). Snow unit resolution (further follow-on).
- ERA5-land recap read-routing (a parallel gap; the shared adapter can fetch it, but 146 neither ingests nor
  routes it). The MeteoSwiss weather-history flow / `_ReanalysisAdapter` protocol (unchanged). The snow model.

## Phases (red-first; each task lists In/Out + Verify)
### Phase 1 — Provenance + attribution (D3)
- Add the `recap_snow_reanalysis` `ForcingSource` member + `SOURCE_ATTRIBUTIONS` entry; provenance round-trip test.
  **Verify:** `uv run pytest tests/unit/types/test_forcing_provenance.py tests/unit/types/test_forcing_schema.py`.

### Phase 2 — Owning ingest flow + persistence (D1/D2/D5) — the blocker
- **2a — dedicated recap-reanalysis ingest flow** (D2 = scheduled): constructs `RecapGatewayReanalysisAdapter`
  (recap client + the Plan 082 polygon resolver), selects the snow canonical scope (swe/snow_depth/snowmelt),
  computes the **watermark window** (last-ingested timestamp → now − ~7-day lag; a wide `start`/`end` override for
  the initial backfill), fetches over snow-bound stations, persists `RawHistoricalForcing` → `HistoricalForcingStore`
  under the Phase-1 provenance, and **advances the watermark**; snow-scoped degradation (D5). Acceptance test
  invokes the **real flow entry point** and asserts a second run is incremental (watermark held).
- **2b — register the scheduled deployment.** Add `SCHEDULE_INGEST_SNOW_REANALYSIS` (default a daily cron, e.g.
  `"0 5 * * *"`) to `register_deployments.py` + `docker-compose.yml`, mirroring `SCHEDULE_INGEST_WEATHER_HISTORY`;
  the initial history backfill is the same flow run with an explicit wide window (not a separate command).
  **Verify:** `uv run pytest tests/unit/flows/test_ingest_recap_reanalysis.py tests/unit/cli/test_register_deployments.py` + the registration test.

### Phase 3 — Read-side routing to all consumers (D4) — depends Phases 1,2
- Add the snow read tier; prove the **same stored snow series** reaches `past_dynamic` via **training**,
  **hindcast**, and **live** read paths (three separate consumer tests over a fake/test store).
  **Verify:** `uv run pytest tests/unit/adapters/test_hybrid_reanalysis_factories.py tests/unit/adapters/test_hybrid_reanalysis.py tests/unit/services/test_training_data.py tests/unit/services/test_hindcast.py tests/unit/services/test_operational_inputs.py`.

### Phase 4 — Docs
- `docs/standards/orchestration.md` (new ingest flow/schedule), `docs/v0-scope.md`, `docs/standards/logging.md`
  (ingest outcome/event names), the relevant touchpoint map.

## Dependencies
- **082** (gateway reanalysis adapter + polygon bindings) · **145** (canonical snow aggregation). Client pin ≥
  9340e40 (#127). Blocks **139** (antecedent SWE for the 12300 model) and **144** (any snow-lookback model).

## Open items / to confirm
- *(Resolved — owner 2026-07-24: **D2 = scheduled ingest deployment** with a watermark; the initial backfill is
  the same flow run with a wide window. See D2.)*
- **Watermark storage** — where the last-ingested-per-`(station, variable)` timestamp lives (reuse the
  MeteoSwiss history flow's boundary-discovery mechanism, or a dedicated marker). `/plan` to pin.
- **Snow unit magnitudes** — shared follow-on with 145; gates onboarding a snow-fed FI model.
- **ERA5-land recap read-routing** — noted parallel gap, out of scope here.
