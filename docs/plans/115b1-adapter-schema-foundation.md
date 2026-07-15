---
status: DRAFT
created: 2026-07-15
plan: 115b1
parent: 115b
title: Forcing foundation — RhiresD + SrelD products, archive asset family, writer-side fetch, past/future availability split
scope: The additive adapter+schema layer for the self-derived MeteoSwiss forcing series. No behaviour change.
depends_on: [115a]
blocks: [115b2]
---

# Plan 115b1 — Forcing foundation (adapter + schema)

> **Design source of truth: [Plan 115b](115b-weather-flow6-reachability.md)** — read §0 (the self-derived
> series decision), §0a (two-product precip disambiguation), §1 (adapter/archive) and the SrelD contract.
> This child carries **phase 1 only**, extracted so it can be built and reviewed on its own.
> Umbrella context/decisions D1–D3: [Plan 115](115-weather-source-identity-model.md).

## Status

**DRAFT.** First of four chunks 115b was split into (115b1 foundation → 115b2 bindings+backfill → 115b3
validation gate → 115b4 reader-flip). Iteratively reviewed by an independent Codex agent before READY.

## Why this is the safe first slice

Like 115a, this is **additive and behaviour-neutral**: it adds products, a canonical parameter, a
writer-side fetch path, and a schema split — but changes **no default**, flips **no flow**, and writes
**no production data** (the backfill is 115b2). It can land and be verified in isolation.

## Scope — phase 1 (see 115b §0a/§1 and the SrelD contract for the detailed spec + citations)

- **1A — products `RhiresD` + `SrelD`.** Add `ForcingSource.METEOSWISS_RHIRESD` (`raw_var="RhiresD"`,
  token `rhiresd`) and `METEOSWISS_SRELD` (`meteoswiss_sreld`) with `SOURCE_ATTRIBUTIONS`; add both to the
  adapter `_PRODUCT_REGISTRY`. `RprelimD` **stays** (live-tail product). Add the canonical parameter
  `relative_sunshine_duration` (unit `%`) to `CANONICAL_FORCING_SCHEMA` (`types/forcing_schema.py:37-46`);
  add its single-source hybrid chain `relative_sunshine_duration: (METEOSWISS_SRELD,)` and default
  `parameters_in_scope` (`hybrid_reanalysis_factories.py:26-38`); add the DB parameter **seed migration**
  (`0001_v0_schema.py:770` seeds `parameters` — required, not "if"). Move the existing exactly-four-parameter
  test pins to five (`test_forcing_schema.py:24-29`, `test_ingest_weather_history.py:79-84`,
  `test_hybrid_reanalysis_factories.py:17-28,48`). **SrelD is REANALYSIS/PAST-only** (see 1E).
- **1B — archive asset selection.** Support the `archive` asset family: per-year NetCDFs
  (`…-archive.rhiresd_ch01h.swiss.lv95_YYYY0101…_YYYY1231….nc`). The current adapter queries per-day items
  only (`meteoswiss_open_data_reanalysis.py:174`) and `_asset_href` returns the first product match with no
  year selection (`:231`) — it cannot address the right archive file. Grid families:
  `RhiresD`/`RprelimD` = `ch01h`; `TabsD`/`TminD`/`TmaxD`/**`SrelD`** = `ch01r`. The CRS/extraction path
  must cover both families.
- **1C — real LV95 archive fixture.** Existing tests use synthetic lat/lon NetCDFs
  (`test_meteoswiss_open_data_reanalysis.py:89`) that prove nothing about the real files. Require a real
  (or faithfully-shaped) LV95 archive fixture — or a live-gated smoke — proving asset selection by year,
  variable names, dims, CRS normalisation, and `exactextract` compatibility.
- **1D — dynamic `R` boundary.** `RhiresD` publishes **monthly** (~25th of the following month); the
  preliminary boundary `R` = the latest published `RhiresD` date, **discovered from STAC**, not a fixed
  offset. Provide the helper the backfill (115b2) and Flow 6 (115b4) both consume.
- **1E — past-vs-future availability split (round-4/5 finding — a REQUIRED code change).**
  `DeploymentConfig.available_nwp_parameters` is today the **only** set (`config/deployment.py:124-127`),
  used for **both** past and future compatibility — `onboard_model` passes it as `available_features`
  (`onboard_model.py:252-258`) and **both** compat paths subtract it (`model_onboarding.py:126` **and**
  `:213`). Introduce a **past-availability** set distinct from the forecast/future set (or a per-parameter
  past/future flag); thread it through both compat paths + `onboard_model` + service onboarding callers;
  add `relative_sunshine_duration` to **past-available only**. There is no forecast sunshine product (ICON
  fetches only precip/temp, `meteoswiss_nwp.py:56-62`), so advertising it as future-available would let a
  model declare an undeliverable feature.
- **1F — writer-side product-scoped fetch.** Add
  `fetch_products(products: list[ForcingSource], station_configs: list[StationWeatherSource], start, end,
  parameters) -> list[RawHistoricalForcing]` on the concrete `MeteoSwissOpenDataReanalysisAdapter`
  (`station_configs` **required** — matches `fetch_reanalysis`; the adapter filters + extracts it,
  `meteoswiss_open_data_reanalysis.py:138,159,229` → `exact_extract_grid_extractor.py:47`). The old
  parameter-keyed `fetch_reanalysis(..., ["precipitation"])` must **fail closed** (raise `ConfigurationError`)
  once two precipitation products exist — precip is served ONLY via `fetch_products`; the other four
  parameters (1 product each) still resolve on the parameter path unchanged. **The read-side
  `WeatherReanalysisSource` protocol, its fakes, `PerSourceStoreReader`/`HybridForcingSource` are
  UNCHANGED.**

## Tests

- **SrelD priority resolution:** `relative_sunshine_duration` resolves via the single-source `SRELD` chain;
  the hybrid reader (keyed on exact `row.parameter`, `hybrid_reanalysis.py:77-84`) returns the
  `meteoswiss_sreld` row.
- **SrelD past-vs-future (1E):** a model declaring `relative_sunshine_duration` as `past_dynamic` onboards;
  declaring it as `future_dynamic` is **rejected** by model-compat. *Soundness: fails against today's single
  conflated `available_nwp_parameters`.*
- **Writer-side product-scoped fetch (1F):** a `RHIRESD`-scoped `fetch_products` call returns ONLY RhiresD
  rows, never RprelimD (and vice versa). *Soundness: fails against the parameter-only path.*
- **Fail-closed precip (1F):** `fetch_reanalysis(..., ["precipitation"])` **raises** once both precip
  products are registered.
- **Archive asset selection (1B/1C):** the real/faithful LV95 fixture extracts correct basin-average values
  through `exactextract` for a `ch01h` (RhiresD) and a `ch01r` (SrelD) file.
- **Four→five parameter pins migrated, not broken** (the three test files above).

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Adapter + schema foundation (additive; no behaviour change)",
      "tasks": ["1A-products-rhiresd-sreld", "1B-archive-asset-selection", "1C-real-lv95-fixture", "1D-dynamic-rhiresd-boundary", "1E-past-vs-future-availability-split", "1F-writer-side-product-scoped-fetch"],
      "parallel": false,
      "depends_on": ["plan-115a"]
    }
  ]
}
```
*(1A before 1E/1F; 1B before 1C; 1D standalone. Sequential is fine — small phase.)*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/           # must not exceed the main baseline
uv run pytest                 # green, incl. the new tests
```

**Doc sync:** `docs/conventions.md` (`ForcingSource` values incl. `METEOSWISS_RHIRESD` + `METEOSWISS_SRELD`),
`docs/spec/types-and-protocols.md` (the new canonical parameter + `fetch_products` signature).

## Provenance

Extracted from Plan 115b (phase 1) as the first of four buildable chunks (owner, 2026-07-15) after 115b —
though design-complete over 6 review rounds — was judged too large to land safely as one unit.
