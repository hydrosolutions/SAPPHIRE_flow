---
status: READY
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

**READY** (2026-07-15). Two independent Codex plan-review rounds → READY-TO-IMPLEMENT (round 1 caught a
real cross-chunk seam — the Flow-6 fail-closed break — fixed by owning the Flow-6 rewrite here as 1G;
round 2 confirmed the seam closed, no other caller missed, effect-neutral). First of four chunks
(**115b1** → 115b2 → 115b3 → 115b4). Implementation authorised; hold at PR.

## Why this is the safe first slice

Like 115a, this is **behaviour-EFFECT-neutral**: it adds products, a canonical parameter, a writer-side
fetch path, and a schema split — changes **no default**, writes **no production data** (backfill is 115b2),
and Flow 6 **stays dark in effect** (it matches zero stations until 115b2 creates the binding). It **does**
rewrite the Flow 6 *call shape* (1G) — this is REQUIRED for self-containment (see below), not a real
behaviour change, because Flow 6 produces nothing either way today.

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
  offset. Provide the helper that **Flow 6 consumes in THIS plan via 1G**, and the 115b2 backfill consumes.
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
- **1G — rewrite the Flow 6 caller to `fetch_products` (REQUIRED for self-containment — round-1 blocker).**
  Flow 6 today calls `fetch_reanalysis(_CANONICAL_PARAMETERS)` (`ingest_weather_history.py:318`), which
  includes `"precipitation"`. The moment 1A adds RhiresD (two precip products) and 1F makes the
  parameter-keyed precip path fail closed, that call would **raise**. So 115b1 MUST also rewrite the Flow 6
  ingest call to the two product-scoped calls over its rolling window (using 1D's `R` and 1F's
  `fetch_products`): `RHIRESD` over `[start, min(R+1d, end))`, `RPRELIMD` over `[max(start, R+1d), end)`;
  the other four parameters via one `fetch_products` (or the unchanged parameter path). **This does not
  change Flow 6's EFFECT** — it still matches zero stations and stores nothing until 115b2's binding — but
  it keeps the fail-closed guard and its only live caller in the SAME PR, so 115b1 never leaves a broken
  seam. *(115b2 then just adds the binding + backfill; it does not touch the Flow 6 call.)*

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
- **Four→five parameter pins migrated, not broken** — and this includes
  `tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py:72,82` (adapter product/parameter pins), not
  just the three unit files, plus the **parameter-store integration test**
  (`tests/integration/store/test_parameter_store.py:18` — updated seed count + `relative_sunshine_duration`,
  unit `%`, weather domain, mean aggregation).
- **Self-containment (1G — the round-1 blocker):** after 115b1 lands, Flow 6's ingest runs WITHOUT raising
  (it uses `fetch_products`, not the now-fail-closed parameter path for precip), and still stores nothing
  (zero-station, no binding yet). *Soundness: fails against a 115b1 that adds the guard but leaves the
  `fetch_reanalysis(_CANONICAL_PARAMETERS)` call in place.*
- **`R` discovery (1D):** the helper returns the latest published `RhiresD` date from STAC; handles an
  empty collection and pagination/latest-date selection. Signature pinned.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Adapter + schema foundation (additive; no behaviour change)",
      "tasks": ["1A-products-rhiresd-sreld", "1B-archive-asset-selection", "1C-real-lv95-fixture", "1D-dynamic-rhiresd-boundary", "1E-past-vs-future-availability-split", "1F-writer-side-product-scoped-fetch", "1G-flow6-fetch-products-rewrite"],
      "parallel": false,
      "depends_on": ["plan-115a"]
    }
  ]
}
```
*(1A before 1E/1F; 1B before 1C; **1D before 1F/1G** (the boundary helper); 1G last (needs 1F + 1D). Sequential is fine — small phase.)*

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
