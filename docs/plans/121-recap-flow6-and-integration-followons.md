---
id: 121
title: Recap Gateway — Flow-6 reanalysis + deferred integration follow-ons
status: DRAFT (stub)
depends_on: [082]
owner: unassigned
created: 2026-07-16
---

# Plan 121 — Recap Gateway: Flow-6 reanalysis + deferred integration follow-ons

> **Stub.** Carved out of Plan 082 by an explicit owner decision (2026-07-16):
> 082 ships the Flow-1 operational forecast core; the three integration items
> below were built-but-not-wired (or blocked) and are deferred here. This plan
> must go through the `plan` workflow (adversarial Codex loop) before READY.

## Why this exists

Plan 082 delivered the Recap operational forecast path (Flow-1 dispatch, cycle
resolution, watchdog, coverage manifest, §5a polygon store/resolver, secret
plumbing) — all committed and gates-green. Three pieces were deliberately left
out of that PR:

1. **Task 2E — Flow-6 Recap reanalysis wiring (BLOCKED, needs a design decision).**
   Plan **115b1** (merged the same day 082 went READY) rewrote Flow-6's
   `_ReanalysisAdapter` Protocol in `src/sapphire_flow/flows/ingest_weather_history.py`
   to require `fetch_products(products, station_configs, start, end, parameters)`
   + `discover_rhiresd_boundary()` — a MeteoSwiss RhiresD/RprelimD product-scoped
   model. `RecapGatewayReanalysisAdapter` (merged under Plan 081) only implements
   `fetch_reanalysis(station_configs, start, end, parameters)`. Constructing it as
   the Flow-6 adapter would `AttributeError` at the first
   `discover_rhiresd_boundary()` call.
   **Consequence:** Flow-6 (rolling reanalysis back-extraction — the Nepal v1
   historical forcing spine) cannot fetch Recap reanalysis in the current
   codebase state.

2. **Task 3B (items 1/2) — coverage training-gate wiring.** The pure functions
   `coverage_spans_window` / `assert_returned_span_covers_request` in
   `src/sapphire_flow/services/gateway_coverage.py` are built and unit-tested but
   are **not referenced by any flow** — they enforce nothing automatically. Needs
   wiring into `train_models_flow` / model-lifecycle orchestration, plus
   `tests/unit/flows/test_train_models.py::TestGatewayCoverageTrainingGate`.

3. **Task 2H-snow (part 2) — snow-forecast Flow-1 wiring.**
   `fetch_snow_forecast` on the Recap forecast adapter is built and unit-tested
   but **not wired into the Flow-1 `_fetch_nwp_task` / storage path** — snow
   forecast is never fetched or persisted operationally yet.

## Open design fork (2E) — pressure-test in the plan workflow

How should the Recap reanalysis adapter meet Flow-6's 115b1 Protocol?

- **(A) Capability-branch Flow-6.** Branch the flow's reanalysis fetch on adapter
  capability (Swiss product-model path via `fetch_products`/`discover_rhiresd_boundary`
  vs Recap path via `fetch_reanalysis`), instead of forcing a MeteoSwiss-shaped
  contract onto the Gateway adapter. *Leaning — keeps the two source families'
  contracts honest.*
- **(B) Give the adapter the product model.** Implement
  `fetch_products`/`discover_rhiresd_boundary` on `RecapGatewayReanalysisAdapter`
  so it satisfies the merged Protocol directly. Simplest wiring; bolts a
  MeteoSwiss-shaped contract onto a Gateway adapter that has no `RhiresD` notion.
- **(C) Unify the Protocol.** Revisit 115b1's `_ReanalysisAdapter` so both source
  families share a product-agnostic reanalysis contract. Largest blast radius;
  touches merged Swiss behavior.

Decide (A/B/C) in the review loop; do not pre-commit.

## Operational follow-ons (IT-specialist)

- **`RECAP_DG_CLIENT_TOKEN` GitHub Actions secret** must be created so CI can
  `uv sync` the private `recap-dg-client` git-pin. Until then every `uv sync`
  CI job and the Docker build fail closed. (Wiring is in 082's diff; the secret
  itself cannot be created from a sandbox.)
- ~~Swiss deployments require a placeholder `./secrets/sapphire_dg_api_key`
  file.~~ **RESOLVED in Plan 082 (PR #91):** the Recap secret moved out of base
  `docker-compose.yml` into a Nepal-only `docker-compose.recap.yml` overlay
  (verified additive-merge — workers keep `db_password` + `sapphire_dg_api_key`).
  Swiss deploys omit the overlay and need no placeholder file.

## Live-smoke test-fixture gap (from 082 Task 1B)

The live-smoke test (`tests/integration/live/test_recap_gateway_live.py`) targets
a synthetic fixture HRU (`test_hru_01`, features `g_test01` / bands) that is **not
registered on the live Gateway** — so it is a contract-shaped scaffold that
cannot pass live until a matching test HRU is registered Gateway-side with the
required subscriptions (IFS `fc`/`pf`, and JSNOW `hs`/`rof`/`swe` for the snow
leg). Probed 2026-07-16: the key authenticates and ERA5-Land returns data, but
`snow.reanalysis` for an unsubscribed HRU returns
`ApiValidationError: Shapefile '...' is not subscribed to JSNOW parameter 'hs'` —
confirming per-HRU × per-parameter entitlement. Register a subscribed test HRU
or mark the snow leg xfail-on-entitlement.

## Live probe — ERA5-Land latency / resolution / the past→future bridge (2026-07-20)

Live-probed the Gateway (`https://recap.ieasyhydro.org/sdk`, owner-supplied key, **test HRU
`12300`** — caveat below) to verify a claim that "10 days of ERA5-Land returns sub-daily data up
to today, recent tail backfilled from old IFS forecasts." **Partly confirmed, with two material
corrections and one design gap.**

**Confirmed**
- **Observed ERA5-Land is hourly.** A clean past window (2024-06-01..10) returned 240 rows, every
  step 1.0 h, `source=era5_land`. Matches the "ERA5-Land: hourly" assumption in 081/082.
- **IFS forecast native cadence is 3-hourly → 6-hourly.** `ifs_forecast(fc, run=2026-07-19)`
  returned 84 steps: 48 × 3 h then 35 × 6 h out to +15 d — exactly the 081/082 "3-hourly→144 h,
  then 6-hourly" shape. **This is the "3-hourly" the tail claim refers to** — it is the IFS
  *forecast* cadence (and thus the gap-fill source), not an ERA5-Land property.

**Corrections**
- **Lag is ~8 days and ragged, not 5–6.** Newest served ERA5 date was 2026-07-12 (today−8), with
  scattered holes near the edge (errors named 07-13/15/16/17). The window boundary floats; do not
  hardcode a day count (our adapter correctly keys on the row `source`, not a fixed lag).
- **`subdaily_resolution=3` is not rejected at validation** (the client's `# 6/12/24` inline
  comment understates the accepted set) — but no data returned, so 3 h fill output is unconfirmed.
  `operational`/`ifs_gap_fill` *resample* to `subdaily_resolution`; only raw `ifs_forecast` is
  native 3 h/6 h.

**The design gap (the real finding)**
- **Our `RecapGatewayReanalysisAdapter` calls the pure `era5_land_reanalysis` endpoint**
  (`adapters/recap_gateway.py::era5_land_reanalysis` → `_drop_forecast_fill_rows`), which **hard-errors**
  (`ApiDataUnavailableError` → our `RecapDataUnavailableError`) the moment the requested window
  crosses the latency edge — it neither truncates nor gap-fills. So "ask for 10 days → get up to
  today" is **false via our path**: it raises and degrades to runoff-only. Confirmed live (every
  end-date past today−8 errored).
- The gap-fill tail the seam-closing idea depends on comes from the **separate** `operational` /
  `ifs_gap_fill` endpoints, which **we do not call**. Right now they also could not stitch for HRU
  12300 (gap-fill reached for an aged-out IFS run 2026-07-04; `operational` hit a missing ERA5 date
  07-03) — so the bridge could not be observed end-to-end live.
- This is the **Nepal analogue of the Swiss RprelimD→NWP seam** (see 115b1 § Access-model
  correction and the earlier seam-3 discussion). Two channels are needed off the same Gateway
  response, and only the first exists today: **(1) leakage-free training** — observed `era5_land`
  only, fill stripped (built) — and **(2) an operational past→future bridge** — the `ifs` gap-fill
  tail, used *only* at inference to close the ERA5→forecast lag, **never** admitted to training
  (not built; nothing calls `operational`/`ifs_gap_fill`).

**Follow-on actions (for the plan workflow, not pre-committed)**
1. Decide whether the operational bridge (channel 2) is in Nepal v1 scope and where it lives — it
   interacts with 2E's A/B/C fork, since Flow-6 is the reanalysis path.
2. **Re-probe a real registered operational HRU** before drawing coverage conclusions — 12300 is a
   test HRU with ragged, possibly incomplete archives; the missing-date holes may be HRU-specific,
   not a Gateway-wide outage.
3. Record in 081/082 that "ERA5-Land: hourly" is correct for the observed body **but** the endpoint
   hard-errors past a ~1-week ragged latency edge, and that the fill/stitch lives on endpoints we do
   not currently call.

*(Probe scripts were run against the live Gateway with the key passed via environment only; the key
is not stored in the repo, this plan, or any script.)*

## Onboarding invariant (capture, don't lose)

Forcing is model-driven: a basin's required Gateway subscription set = the union
of variables required by the models assigned to it (via each model's
ForecastInterface requirement). A snow-consuming model assigned to a basin means
that basin must be subscribed to the JSNOW parameters it needs, or every cycle
hard-aborts. Add "subscribe the basin to every parameter its assigned models
require" to the DHM/station onboarding checklist (Flow 5 / Plan 120 territory).

## Non-goals

- Does not re-open anything 082 shipped (Flow-1 forecast path stays as merged).
- Does not integrate the extraction tool (adjacent boundary — see 04/117).
