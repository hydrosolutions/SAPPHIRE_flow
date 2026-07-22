---
status: DRAFT
created: 2026-07-22
plan: 139
title: Nepal basin 12300 — operational enablement for an SWE+precip+temp discharge regression
scope: The multi-workstream path to run a regression model consuming past+future SWE + past+future precipitation + past+future temperature for gateway-fed Nepal test basin 12300 on the mac-mini. An EPIC/roadmap that sequences new work against existing Plans 081/082/115/117/121, and resolves the discharge-target blocker.
depends_on: [81, 82, 115, 117, 121]
blocks: []
supersedes: []
---

# Plan 139 — Nepal basin 12300: SWE+precip+temp regression, operational enablement

## Status

**DRAFT — EPIC/roadmap, not a single implementation plan.** Grounded in a 3-agent investigation
(2026-07-22) + a live recap-gateway snow probe. Owner chose "full Nepal operational enablement" over an
offline forcing-only test. Because 12300 is *entirely absent* from the deployment and several enabling
subsystems don't exist, this is a **program of workstreams** — most map onto existing Nepal-v1 plans
(081/082/115/117/121); some are new; one (the discharge target) is a hard external blocker. Each workstream
below is a candidate to split into its own implementation plan once this roadmap is agreed. For a `/plan`
adversarial round before READY.

## Context — 12300 is a bare test gauge; "run it on the mac-mini" is a program

Verified live on the mac-mini (2026-07-22): basin **12300 has NO `stations` row, NO `basins` row, NO
`recap_gateway_polygon_bindings` row, NO group** — nothing about it exists anywhere in the DB. The mac-mini
runs the **Swiss v0** config (`data_source="camels-ch"`, `[adapters.weather_forecast] type="meteoswiss_nwp"`),
and the recap gateway is **inactive** there. To forecast discharge for 12300 with an SWE+precip+temp
regression, ALL of the following must be true, and today none are:

1. 12300 exists as a station+basin with a gateway geometry binding (`hru_code=12300`, `g_123`).
2. There is a **discharge target** to train the regression against (12300 has none; see § The target problem).
3. Historical **forcing** (SWE/precip/temp) is loadable into the training path for 12300.
4. The model **declares SWE** and SWE is delivered past AND future (future-SWE is currently dark).
5. **Operational forcing** for 12300 comes from the recap gateway — but forcing dispatch is a *global* mode,
   incompatible with the co-hosted Swiss basins on the same stack.

The recap **snow API works** (probed 2026-07-22): `client.snow.reanalysis(hru_code="12300", variable="swe")`
returns hourly SWE 2025-10→2026-06 (source `jsnow_reanalysis`); the four channels
`reanalysis/operational/forecast/gap_fill` mirror the precip/ecmwf structure. So the *data source* exists;
the *pipeline into training + forecasting* does not.

## The target problem (the gating decision)

A discharge regression predicts discharge — it needs an **observed discharge target** to fit. 12300 has
none: it is a fake test gauge, and real DHM discharge is **blocked on the DHM data-format questionnaire**
(see memory `project_dhm_data_interface`). Three ways to unblock, in priority order:

- **W1a — JSNOW `rof` (modeled runoff) as the interim/test target (RECOMMENDED to unblock now).** The JSNOW
  snow model exposes `rof` (runoff); `client.snow.reanalysis(hru_code="12300", variable="rof")` *may* provide
  a modeled runoff series to train + validate against **as a stand-in for discharge**, letting us prove the
  whole SWE→discharge pipeline end-to-end before DHM data arrives. **MUST verify 12300 is subscribed to
  `rof`** (the probe found `hs`/`snow_depth` returns "not subscribed to JSNOW parameter" for 12300, so
  subscription is per-parameter and not guaranteed). This makes 12300 a *pipeline* test, honestly reported as
  forecasting modeled runoff, not observed discharge.
- **W1b — real DHM discharge.** Blocked on the questionnaire + the DHM-obs adapter + rating ingestion (a
  separate track). The true target, but not available on this timeline.
- **W1c — synthetic target.** A deterministic synthetic discharge series purely to exercise the plumbing.
  Weakest (validates mechanics only), fallback if `rof` is not subscribed.

**This decision gates W4/W8 (training) and must be resolved first.**

## Objective

Basin 12300 forecasting discharge (or its agreed proxy target) from an SWE+precip+temp regression, running
on the mac-mini's operational forecast cycle alongside the Swiss basins — with the enabling Nepal subsystems
(gateway-fed onboarding, gateway-forcing-into-training, per-station forcing dispatch) built or reused.

## Non-goals

- **Not** the real DHM discharge integration (separate DHM track; this plan uses the W1 proxy).
- **Not** a general multi-tenant Nepal deployment — scoped to the single test basin 12300 as the
  end-to-end proof.
- **Not** re-designing subsystems already owned by Plans 081/082/115/117/121 — this roadmap sequences and
  integrates them, and only *adds* the genuinely-missing pieces (SWE model, future-SWE wiring, snow
  aggregation, the 12300-specific onboarding/target).

## Workstreams

Each lists: the work, the **owning/related existing plan**, and the **status** (BUILT reusable / PARTIAL /
NEW). "NEW" = no existing plan covers it and this epic must add it (or spawn a sub-plan).

### W1 — Resolve the discharge target (gating) — NEW (external blocker)
Decide W1a/W1b/W1c (§ The target problem). If W1a: verify `rof` subscription for 12300 via
`client.snow.reanalysis(variable="rof")`; define how the modeled runoff becomes the `observations`/target
series 12300's training reads. Blocks W4, W8.

### W2 — Basin geometry import for 12300 — related Plan 117 (basin/static artifact boundary)
Import the 12300 HRU geometry + static attributes into `basins` (Plan 117 established the `g_<station_code>`
naming, single-kind GeoPackage, `basins.attributes` JSONB, ERA5-Land indices). Plan 117 is READY + the
extractor brief is out, but the **SAP3 importer is a "separate importer plan, not yet built"** — W2 is that
importer, scoped to 12300. Status: **PARTIAL** (design done, importer NEW).

### W3 — Gateway-fed station onboarding (non-CAMELS) — NEW
`onboard_from_camelsch` (`services/onboarding.py:974`) is CAMELS-CH-only; the `data_source` field exists in
`config/onboarding.py:96` but only `"camels-ch"` is implemented. W3 adds a **gateway-fed onboarding path**
that creates the 12300 `stations` row + a `recap_gateway_polygon_bindings` row (tenant-bound `hru_ref`, per
memory `project_recap_dg_client_geometry_lifecycle`) + default model assignment, without CAMELS. Status:
**NEW** (related to Plan 082 gateway bindings, but no station-onboarding path exists).

### W4 — Gateway forcing → training path — related Plans 081 (offline forcing spine) / 121 (Flow-6 adapter fork)
Training reads `historical_forcing`; the recap gateway is **not wired into** `training_data.py`,
`reanalysis_backfill.py`, or the hybrid-reanalysis factories. W4 wires **gateway reanalysis (era5_land) +
snow reanalysis (jsnow `swe`, and `rof` if it's the target)** into the training-data assembly for 12300,
respecting the leakage guard (observed `era5_land`/`jsnow_reanalysis` only; forecast-fill `ifs`/`jsnow_forecast`
excluded — `recap_gateway.py:282-293`). Status: **PARTIAL** (Plans 081/121 own the offline/operational forcing
adapters; the training-ingestion wiring for 12300 is the gap).

### W5 — Per-station operational forcing dispatch — owning Plan 115 (WeatherSourceRole identity)
Operational forcing is a **global** `[adapters.weather_forecast] type` switch (`run_forecast_cycle.py:334`),
so 12300 (recap gateway) cannot co-host with the Swiss basins (`meteoswiss_nwp`) on one stack. W5 makes
forcing **per-station/per-source** (station_weather_sources drives which adapter serves each station) — this
is exactly Plan 115's identity model. Status: **PARTIAL/owned by Plan 115** (must land before 12300 forecasts
operationally next to Swiss).

### W6 — The SWE+precip+temp regression model — NEW
A new FI `StationForecastModel` (clone the `SeasonalPrecipRunoffRegression` pattern, `nwp_regression.py:628`)
that declares:
- `future_known nwp/{precipitation,temperature}` (base) + **`future_known nwp/swe`** (the future SWE channel,
  `ensemble_mode` broadcast-from-deterministic per Plan 082 2H-snow) — `future_steps` = horizon.
- `past_known reanalysis/{precipitation,temperature,swe}` (past forcing windows) — SWE as a **STATE**
  (aggregation MEAN/LAST, not SUM).
- `past_known obs/<target>` lookback (autoregressive lags of the W1 target).
Register via `pyproject.toml` entry-point + `types/ids.py` (`MODEL_TIERS` + `ALERT_ELIGIBILITIES` — mandatory,
`model_registry.py:37,52`). Constraint (Agent 1): a model MUST declare ≥1 `future_known` var (horizon derives
from it) — satisfied. Status: **NEW**.

### W7 — Future-SWE wiring + snow aggregation fix — NEW (concrete SAP3 gaps)
Two verified gaps that block SWE reaching a model:
- **Future-SWE is dark end-to-end.** `RecapGatewayForecastAdapter.fetch_snow_forecast`
  (`recap_gateway.py:830`) has **zero callers**; nothing writes deterministic snow rows into the
  `WeatherForecastStore`, so the Plan 082 broadcast path (`operational_inputs.py:112-150,456+`) is a permanent
  no-op. W7 wires `fetch_snow_forecast` into `run_forecast_cycle.py` and stores the snow forecast so the
  `future_known nwp/swe` channel is fed at inference.
- **Snow aggregation is wrong/missing.** `_V0_AGGREGATION_FALLBACK` (`training_data.py:29-40`) has only the
  legacy `snow_water_equivalent: MEAN` key — not the canonical Recap names `swe`/`snow_depth`/`snowmelt`; a
  `swe` column hits the unknown-parameter MEAN fallback (coincidentally OK for a state, but noisy), and
  **`snowmelt`/`rof` (a flux) would wrongly MEAN instead of SUM**. W7 adds `swe`/`snow_depth` (MEAN/LAST) and
  `snowmelt`/`rof` (SUM) rows before any snow model ships. (SAP3 does not read the FI-declared `aggregation`
  field — confirmed — so the fallback table is the real control.) Status: **NEW**.

### W8 — Onboard + train + schedule 12300 on the mac-mini — integrates W1-W7
Once W1-W7 land: onboard 12300 (W3) with its geometry (W2) and gateway binding; assemble training data
(W4) with the W1 target; onboard-model the W6 SWE model for 12300 (`flows/onboard_model.py` — compat, smoke,
train, hindcast, skill gate, promote, assign); the forecast cycle (per-station dispatch, W5) then runs it.
Deploy with the correct macmini overlay stack and verify a 12300 forecast is produced. Status: **NEW**
(integration).

## Dependency graph

```json
{
  "phases": [
    { "id": "target", "workstreams": ["W1"], "depends_on": [],
      "note": "Gating: verify rof subscription / choose the target. Blocks training." },
    { "id": "geometry-onboard", "workstreams": ["W2", "W3"], "depends_on": [],
      "note": "W2 geometry import then W3 gateway-fed station onboarding for 12300." },
    { "id": "forcing", "workstreams": ["W4", "W5"], "depends_on": ["geometry-onboard"],
      "note": "W4 gateway-forcing-into-training; W5 per-station dispatch (Plan 115). W5 can proceed in parallel." },
    { "id": "model", "workstreams": ["W6", "W7"], "depends_on": [],
      "note": "The SWE model + future-SWE wiring + aggregation fix are independent SAP3 code; can start immediately." },
    { "id": "integrate", "workstreams": ["W8"], "depends_on": ["target", "forcing", "model"],
      "note": "Onboard+train+schedule 12300; needs the target, the forcing path, and the model." }
  ]
}
```

## Risks / open decisions for the grill-me

- **Target (W1) is the make-or-break.** If `rof` is not subscribed for 12300 and DHM is unavailable, the epic
  cannot train a discharge model — it collapses to a synthetic-target mechanics test (Plan A's rejected
  "offline validation" option, reached the long way). Verify `rof` subscription **first**.
- **W5 (per-station dispatch) is arguably its own hard plan (Plan 115).** Co-hosting Nepal + Swiss on one
  stack is a real architectural change; if not ready, an alternative is a **separate Nepal-only stack**
  (second compose overlay/host) rather than per-station dispatch — cheaper to stand up, avoids the global-mode
  conflict, but diverges from the multi-tenant target.
- **Scope/size.** This is 6-8 workstreams; realistically 3-5 implementation plans. The `/plan` round should
  decide which workstreams this epic *drives directly* (likely W1, W6, W7 — the genuinely-new, 12300-specific
  code) vs *depends on* (W2/W4/W5 owned by 117/081/121/115), and whether to split.
- **v1 critical-path alignment.** Confirm this sequencing against Plan 106 (v1 critical-path roadmap) so the
  12300 test doesn't reorder the locked wave sequence.

## Exit gates (per code workstream)

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- Live mac-mini (2026-07-22): 12300 absent from `stations`/`basins`/`recap_gateway_polygon_bindings`;
  Swiss `meteoswiss_nwp` global forcing; recap gateway inactive on the stack.
- recap snow API: `.venv/.../recap_client/snow.py` (`reanalysis/operational/forecast/gap_fill`); probe
  confirmed `variable="swe"` hourly 2025-10→2026-06 for 12300; `hs` not subscribed.
- `adapters/recap_gateway.py` (`fetch_snow_forecast` `:830` zero callers; `RecapVariable` swe/snow_depth/
  snowmelt `:85-100`; leakage guard `_OBSERVED_SOURCES` `:282-293`; `client.snow.reanalysis` `:991`).
- `services/operational_inputs.py` (Plan 082 2H-snow broadcast `:112-150,456+`, a current no-op).
- `services/training_data.py` (`_V0_AGGREGATION_FALLBACK` `:29-40`; no gateway wiring).
- `flows/run_forecast_cycle.py` (`:334` global forcing dispatch; `_build_recap_forecast_adapter` `:402`).
- `services/onboarding.py:974` (CAMELS-only); `config/onboarding.py:96` (`data_source`).
- `src/sapphire_flow/models/nwp_regression.py:628` (`SeasonalPrecipRunoffRegression` template).
- Existing plans: 081 (Nepal forcing spine), 082 (operational/coverage + 2H-snow), 115 (WeatherSourceRole /
  per-station identity), 117 (basin/static artifact + geometry importer), 121 (Flow-6 adapter fork / ERA5
  operational bridge), 106 (v1 critical-path roadmap).
- memory: `project_recap_ifs_fc_hres_member0`, `project_recap_dg_client_geometry_lifecycle`,
  `project_dhm_data_interface`, `project_basin_static_artifact_plan117`, `project_v1_critical_path_roadmap`.
