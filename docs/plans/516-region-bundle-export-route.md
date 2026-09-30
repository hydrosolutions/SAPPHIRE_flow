---
status: DRAFT
---

# Plan 516 — Region bundle export for the flow-map (real Nepal forecasts, honestly labelled)

**Date**: 2026-09-30
**Related**: Plan 049 (Cloudflare edge for the dashboards; T3 names this plan), Plan 401 (reviewer/consumer tokens),
Plan 402 (map read routes; committed contract and its version gate, D14), Plan 404 (rejected forecasts),
Plan 268 (restricted DHM delivery; D5/D14 publication limits), Plan 341/342 (publication gate), Plan 511 (Nepal
API host), Plan 273 (synthetic demo bundle v2, COMPLETE), Plan 204 (forecast-lab snapshot), the map project's
`docs/SAPPHIRE_FLOW_REGION_BUNDLE_EXPORT_PROMPT.md` and `schemas/flow-map-region-bundle-v3.schema.json`
**Scope**: add the backend export the map asked for on 2026-09-18: `flow-map-region-bundle/v3`, for a **named
station set** (not `network=bafu`), so the Nepal dashboard can show **real** forecasts where they exist and label
everything else truthfully. It is **one self-contained plan**: it does not change station onboarding, QC rules,
the cadence seam, or anything Plans 143/264/268/269/272 own. **If serving the six real stations would require
that, this plan stops and the dashboard stays on fixtures** (the map's own condition).

---

## Context (verified 2026-09-30)

- The map's ask: a route emitting the v3 bundle — four documents in its schema (`manifest`, `stations`, `basins`,
  per-station `series`) — with per-station `sourceMode` (`illustrative | modelled | operational`), per-dataset
  publication flags **defaulting to withheld**, the observed series with explicit nulls, per-run `forcingKind`
  (`nwp_forecast | reforecast | reanalysis | blended | unknown`), the model calibration window, and
  ensemble **members** where cheap. The schema and a validator (`scripts/validate_region_bundle.py`) live in the
  map repository and refuse unknown keywords.
- The backend stores forecasts as `forecast_values` rows with either a `member_id` **or** a `quantile`
  (`db/metadata.py`), so members can be exported where stored, and quantiles otherwise.
- **Restricted data.** Plan 268's delivery-tagged DHM discharge rows are withheld from consumer and reviewer
  tokens (`security.md` § Restricted DHM history). The export must not become a side door: it serves the same
  rows the token could read through `/observations`, and the delivery-tagged ones never appear in it. For the six
  DHM gauges the observed series is therefore `null` ("no verification possible", a legal state in the schema)
  until the owner and DHM agree otherwise and the standard is changed by its own plan.
- **The publication gate** (Plans 341/342) is not active on any tenant today, but every new forecast reader must
  be inventoried in Plan 341's route list and read through the same gate once it is.
- **The contract discipline** (Plan 402 D14): `docs/spec/api-v1-map.openapi.json` is committed with a version
  and a CI check; an additive route is a minor bump, a breaking change needs the documented process.
- **Dashboard side**: today the Nepal dashboard runs on synthetic fixtures (Plan 273's bundle is synthetic). The map
  pulls the bundle every 6 hours and keeps the last good copy on failure.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Two read routes under `/api/v1/regions/{region}/`**: `bundle` (manifest, stations, basins) and `stations/{station_key}/series` (one station's document), GET-only. | The schema's series documents are large and the browser must never load all of them; two routes keep each response bounded. |
| D2 | **Auth = the dashboard's token** (reviewer, station-scoped or tenant-bound per Plan 401), never admin. The publisher that builds the bundles uses the same kind of token. | The map's prompt says "consumer scope"; Plan 401 D-decisions send dashboards to `reviewer`. Restriction rules are identical for both roles. |
| D3 | **A region is a named station set in deployment config**, not a network filter: `[region_bundle.<region>]` lists station keys and, per station and dataset (`observations`, `forecasts`, `derived_metrics`), whether it is published. **Default for every dataset: withheld.** | The map's contract requires default-withheld flags; config keeps the decision out of code and reviewable. |
| D4 | **`sourceMode` and `forcingKind` are derived from stored provenance, never configured.** Where the provenance cannot decide `forcingKind`, emit `unknown` (renders as "not verifiable"). `illustrative` is never emitted by the backend — invented values exist only in map fixtures. | A label that can be configured can be configured wrong; "not verifiable" must be machine-decidable. |
| D5 | **Ship quantiles first, members where stored.** Members are optional in the schema; when a forecast has `member_id` rows they are exported, else the stored quantile levels. No fitting of distributions to manufacture tail probabilities. | Matches the map's priority list and its refusal to fit through three quantiles. |
| D6 | **The observed series honours Plan 268's restriction** (see Context) and the D3 flag; skill and verification are computed on the map side from what is published, not here. | The backend supplies inputs; a metric can expose withheld values (bias exposes the mean). |
| D7 | **The bundle schema is copied into the repository with its SHA-256 recorded**, and every export is validated against it in tests. | A cross-repository contract must fail loudly when either side changes. |
| D8 | **Question 0 is answered first, separately, in this plan** (T0): are ECMWF IFS reforecasts, or any archived forecast forcing overlapping the pre-2019 DHM record, reachable through the recap gateway or another adapter? | It decides whether any skill number the dashboard shows is forecast skill or simulation skill. |

## Tasks

### T0 — Question 0: is archived forecast forcing available for the DHM record?
**Outcome**: a one-paragraph answer with evidence, sent to the map session before implementation.
- Check the recap-gateway products and adapters (`docs/` reference notes, `adapters/`) for reforecast/hindcast
  coverage overlapping the six basins' pre-2019 record; state what exists, its dates, and what does not.
**In / Out**: a "Findings" section in this plan. Out: implementation.
**Verification**: the paragraph names the sources examined with file:line or product ids; a reviewer can
re-check each.
**Pre-change**: N/A (investigation).

### T1 — Schema copy, hash, and contract test
**Outcome**: the v3 schema is committed here and a test validates exported documents against it.
- Copy `flow-map-region-bundle-v3.schema.json` to `docs/spec/`, record its SHA-256 in the test and in the spec
  page; add a test that validates a generated six-station bundle from a fixture database.
**In / Out**: `docs/spec/`, one test module. Out: the routes.
**Verification**: `uv run pytest` on the new module; changing one byte of the schema fails the hash test.
**Pre-change**: RED — the test imports a builder that does not exist yet (written first, fails for that reason).

### T2 — Region station sets and publication flags (config)
**Outcome**: `[region_bundle.<region>]` config parses at the boundary into frozen types; every dataset defaults to
withheld; an unknown station key or region fails at load.
**In / Out**: `config/deployment.py`, the config reference, tests. Out: enabling any region in a shipped overlay.
**Verification**: unit tests for the default-withheld rule, unknown key, duplicate key, and a config that names a
station of another tenant (rejected).
**Pre-change**: RED — parsing tests written first.

### T3 — Bundle route (manifest, stations, basins)
**Outcome**: `GET /api/v1/regions/{region}/bundle` returns the manifest, stations and basins for the region's
station set, scoped to the caller's token; a station outside scope or a withheld dataset is absent, not blank.
- `sourceMode` per station and `forcingKind` per run derived per D4; banner text per the schema.
- Inventoried in the route-auth matrix (Plan 401's exhaustive matrix) and in Plan 341's forecast-reader list.
**In / Out**: the route, the builder, the matrix and inventory entries. Out: series.
**Verification**: route tests through the real ASGI app with reviewer, consumer, admin and no token; an
out-of-scope station is absent; the schema validation from T1 passes.
**Pre-change**: RED — the route returns 404 today.

### T4 — Series route (one station)
**Outcome**: `GET /api/v1/regions/{region}/stations/{station_key}/series` returns forecast issues (quantiles, and
members where stored), the observed series with explicit nulls and declared gaps, per-run provenance, and the
model calibration window.
- Calibration window from the model artifact's recorded training window; when absent, the schema's "unknown".
- Observations pass through the same restriction as `/observations`: delivery-tagged discharge rows are never
  emitted; the whole series is `null` when withheld.
- Forecasts read through the publication gate reader when a tenant's gate is active.
**In / Out**: the route and builders. Out: skill computation (map side).
**Verification**: tests seeding delivery-tagged rows prove they never appear for reviewer/consumer tokens; a
forecast with members exports members; one with quantiles only exports quantiles; a run with undecidable forcing
emits `unknown`.
**Pre-change**: RED — 404 today; the restriction test fails against a naive implementation that reads rows directly.

### T5 — Contract, docs, and the map's validator
**Outcome**: the committed OpenAPI (`docs/spec/api-v1-map.openapi.json`) contains both routes with the version
bumped per Plan 402 D14; `docs/spec/types-and-protocols.md` or the spec page documents the bundle; the map's
`scripts/validate_region_bundle.py` passes on the output of a seeded run.
**In / Out**: contract file, drift test, docs. Out: the map repository.
**Verification**: the drift test; the D14 CI check; running the map's validator against generated output
(recorded with the command).
**Pre-change**: RED — the drift test fails until the contract is regenerated.

### T6 — Acceptance on a real database
**Outcome**: the six-station bundle is generated from a real staging database and validated; each station's
`sourceMode` and observed-series state are recorded honestly (expected today: no `operational` DHM station,
observed series `null`).
**In / Out**: an acceptance record. Out: making stations operational.
**Verification**: the record with commands and the validator output; the map session can fetch the bundle with
its own token.
**Pre-change**: N/A (operator run after merge; staging only).

## Exit gates

1. T0's paragraph is delivered and its evidence re-checkable.
2. Both routes exist, are in the committed contract, and every export validates against the pinned schema.
3. A delivery-tagged DHM row can be shown by a test never to appear for a reviewer or consumer token.
4. Every dataset is withheld unless config allows it; the default is proved by a test.
5. The map's validator accepts a seeded run; the acceptance record is filed.

## Not in this plan

Station onboarding, QC rules, the cadence seam and anything Plans 143/264/268/269/272 own; a live DHM adapter;
level-to-discharge conversion; model training; the Swiss deployment and the forecast-lab snapshot route; skill or
threshold computation; flipping any DHM observation to publishable; the map repository.

## Risks

| Risk | Mitigation |
|---|---|
| The export becomes a side door for restricted DHM data | D6, T4's tests, T3/T4 inventory in Plans 341 and the auth matrix; observed series `null` by default for the six gauges. |
| Real Nepal forecasts do not exist yet for the six stations | T6 records honestly; the bundle carries `modelled`/`unknown` labels and the dashboard states them; fixtures remain the fallback. |
| Reanalysis-forced runs are shown as forecast skill | D4: `forcingKind` is derived, `unknown` renders as not verifiable; T0 settles what exists. |
| Cross-repository schema drift | D7: pinned hash and validation test. |
| Scope creep into onboarding plans | The stop condition in Scope; "Not in this plan". |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0", "T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-4"] }
  ]
}
```
