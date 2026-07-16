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
