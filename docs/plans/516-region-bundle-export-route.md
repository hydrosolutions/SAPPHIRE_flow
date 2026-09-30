---
status: DRAFT
---

# Plan 516 — Region bundle export for the flow-map (real Nepal forecasts, honestly labelled)

**Date**: 2026-09-30 (revised after the first round of Claude + Codex reviews, same day)
**Related**: Plan 049 (Cloudflare edge for the dashboards; T3 names this plan), Plan 401 (reviewer/consumer tokens,
route-auth matrix), Plan 402 (map read routes; committed contract and its version gate, D14; D6 flag-`detail`
carry-forward), Plan 404 (rejected forecasts), Plan 268 (restricted DHM delivery; **D5** publication limit and
**D9** trained-forecast restriction — not D14), Plan 341/342 (publication gate and its hard-listed route
inventory), Plan 511 (Nepal API host), Plan 273 (synthetic demo bundle v2, COMPLETE), the map project's
`docs/SAPPHIRE_FLOW_REGION_BUNDLE_EXPORT_PROMPT.md` and `schemas/flow-map-region-bundle-v3.schema.json`
**Scope**: add the backend export the map asked for on 2026-09-18: `flow-map-region-bundle/v3`, for a **named
station set** (not `network=bafu`), so the Nepal dashboard can show **real** forecasts where they exist and label
everything else truthfully. It is **one self-contained plan**: it does not change station onboarding, QC rules,
the cadence seam, or anything Plans 143/264/268/269/272 own. **If serving the six real stations would require
that, this plan stops** and the dashboard stays on fixtures (the map's own condition).

---

## Context (verified 2026-09-30)

- **The contract.** The map's v3 schema has four root documents — `manifest`, `stations`, `basins`, `series` —
  laid out on disk as `region.json`, `stations.geojson`, `basins.geojson` and `series/<station_key>.json` (the
  map's reference example and its validator, `scripts/validate_region_bundle.py`, use this layout and refuse
  unknown schema keywords). Wire field names are **snake_case** (`source_mode`, `forcing_kind`, `publication`,
  `basin_id`, `qc_status`, `ensemble_size`); publication values are `allowed | withheld`; `forcing_kind` is
  `nwp_forecast | reforecast | reanalysis | blended | unknown`; `source_mode` is
  `illustrative | modelled | operational`.
- **Every station entry** requires `key`, `identity`, `source_mode`, `publication` (all three dataset keys),
  `basin_id`, `calibration`, `verification`, `thresholds`, `threshold_basis`; a series document requires
  `region`, `station_key`, `observations` (nullable) and `forecasts` (an array); every forecast issue requires
  `forecast_id`, `issued_at`, `horizon`, `forcing_kind`, `qc_status`, `unit`, `valid_times`, `series`
  (non-empty, its keys equal to the declared quantile levels) and `gaps`. The manifest requires a `banner`,
  a `forecast_cycle` (cycle hours, cadence, horizon steps, representation `quantiles` or
  `quantiles_and_members`, quantile levels), `timezone`, `generator` and `provenance`. The validator also
  requires one issue per station per cycle slot, ordered and unique, all sharing the manifest's cycle,
  horizon and quantile levels; withheld observations `null`; `verification` `null` when observations are `null`.
- **What the backend stores.** `forecast_values` rows carry a `member_id` **or** a `quantile` (exclusive);
  `forecasts` has a `representation` (`members | quantiles`), a `version`, a `superseded` status, `model_id`,
  `nwp_cycle_source` (`primary | fallback | runoff_only`), `warm_up_source` and `qc_status` (default `raw`);
  `hindcast_forecasts` has `forcing_type` (`nwp_archive | reanalysis`). There is no forcing-kind column on live
  forecasts, and the store can hold several models, versions and superseded rows at one issue time.
- **Restricted data.** Plan 268's delivery-tagged DHM discharge rows are withheld from consumer and reviewer
  tokens (`security.md` § Restricted DHM history) — but the existing filter in `api/routes/api_stations.py`
  is written inline (`if not principal.is_admin: …`), so **admin tokens read them**. Separately, Plan 268 D9
  records that **forecasts trained on restricted measurements may themselves be restricted**, and the map's
  deployment plan requires DHM permission before publication.
- **Today** the Nepal dashboard runs on synthetic fixtures (Plan 273's bundle). The map pulls the bundle
  every 6 hours and keeps the last good copy on failure.
- **Dependencies not yet real:** Plan 341/342's publication gate is unimplemented and its activation setting
  rejects any non-empty value; the route inventory in Plan 341 is a hard-listed set.
- **Contract discipline** (Plan 402 D14): `docs/spec/api-v1-map.openapi.json` is committed at version 2.0 with a
  CI check; an additive route is a minor bump (to 2.1). The map keeps its own copy of the contract and checks
  its Worker allow-list against it.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Two read routes under `/api/v1/regions/{region}/`, GET-only, returning fragments.** `bundle` returns `{manifest, stations, basins}` (the publisher writes `region.json`, `stations.geojson`, `basins.geojson`); `stations/{station_key}/series` returns one series document (written to `series/<key>.json`). The publisher assembles the directory and runs the map's validator over the whole. | The root schema needs all four documents; two bounded routes keep responses small. Contracts are per fragment; whole-bundle validation happens on the assembled directory. |
| D2 | **Route class: a PRINCIPAL-class route** (Plan 401): station-scoped consumer and reviewer tokens read it; admin tokens also reach it but the **restriction filter applies to every role** (D6). No token → 401; an in-scope station → 200; an existing station out of scope or unknown → 404 with an identical body. The publisher uses a station-scoped token, never admin. | The map's prompt says consumer, Plan 401 sends dashboards to reviewer, and admin is unscoped: one class with a role-independent filter means a wrong token cannot leak restricted rows. |
| D3 | **A region is a named station set in config** — `[region_bundle.<region>]` — keyed by `(network, code)`, listing per station: `model` (the one product to export), and per dataset (`observations`, `forecasts`, `derived_metrics`) `withheld` (default) or `allowed`. **`allowed` for a station of a restricted network requires a `permission_ref`** (the identifier of a recorded owner + DHM decision); a config without it is rejected. Config alone is never permission. | Default-withheld per the map's contract; Plan 268 D9 means forecasts trained on restricted data need their own recorded decision, not just a flag. |
| D4 | **`forcing_kind` and `source_mode` come from stored evidence with conservative defaults.** `forcing_kind`: T1 writes the mapping table from `forecasts.nwp_cycle_source` / `warm_up_source` and `hindcast_forecasts.forcing_type`, defaulting to `unknown` (renders as not verifiable) wherever the evidence does not decide. `source_mode`: default `modelled`; `operational` only when live (non-hindcast) forecasts from an active artifact for that station exist within the last two cycles — **never from the station's status label** (which some stations got by direct DB write). `illustrative` is never emitted by the backend. | A label that can be configured or inferred from a loose flag can be wrong; "not verifiable" must be machine-decidable. |
| D5 | **The bundle always carries quantiles; members are added where stored.** A forecast stored as members yields **empirical quantiles of the members at the declared levels** (not distribution fitting) plus the members, with `ensemble_size` and manifest representation `quantiles_and_members`; a forecast stored as quantiles yields `quantiles`. | v3 requires a non-empty quantile `series` in every issue; the map refuses tail probabilities manufactured by fitting. |
| D6 | **One shared restriction predicate**, extracted from the `/observations` route and used by both routes and by `/observations`, applied to **every role**. `observations` is `null` — the whole series, not partial — when any delivery-tagged row is in the window or the D3 flag is withheld. In that case `publication.observations` is forced to `withheld`, `verification` is `null`, `thresholds` is `null` with `threshold_basis` `none_available` (so a percentile threshold cannot leak the observed distribution), and only `qc_status` is emitted — never QC flag `detail` or observation-derived free text (Plan 402 D6 carry-forward; `eligibility_note` carries no such text). | The filter must not depend on the caller; derived values can expose withheld series (bias exposes the mean). |
| D7 | **The bundle schema is copied into the repository with its SHA-256 recorded**; every export is validated against it in tests. | A cross-repository contract must fail loudly when either side changes. |
| D8 | **Question 0 is answered first, alone** (T0): are ECMWF IFS reforecasts, or any archived forecast forcing overlapping the pre-2019 DHM record, reachable through the recap gateway or another adapter? It also states whether the series' historical issues come from `hindcast_forecasts` (verification over the record) or live `forecasts` (recent). | It decides whether any skill number is forecast skill or simulation skill, and what feeds the series. |
| D9 | **Forecast selection: one issue per station per issue time, on the manifest's cycle.** Export the station's configured `model`, latest non-superseded version, ordered by issue time; a cycle slot with no eligible forecast is a declared `gap`; forecasts whose horizon, cadence or quantile levels differ from the manifest cycle are excluded and counted, not exported. The manifest cycle is defined per region in config. | The validator rejects duplicate issue times, missing slots and mixed shapes; the store holds several products per time. |
| D10 | **Series are bounded.** The series route takes `issued_from`, `issued_to` and a maximum count (defaulting to the recent window, rejected with 400 beyond a cap chosen in T4 against the schema's maximum of 20000 issues). | "Separately fetchable" is not a bound by itself. |
| D11 | **A region with no station the caller may read returns 404**, not an empty (schema-invalid) bundle. | v3 requires at least one station. |

## Field sourcing

| Required field | Source |
|---|---|
| `manifest.banner` (headline, spread_label, verification_note, date_label) | Fixed text, one per `source_mode`, kept in the spec page and reviewed by the owner — not configured per deployment |
| `manifest.forecast_cycle`, `timezone`, `generator`, `provenance` | Region config (cycle), deployment config (timezone), package version, and stored provenance |
| `stations[].identity` incl. `backend_uuid`, `key` (slug) | Station row; the key is the configured `(network, code)` mapped to a slug |
| `stations[].basin_id`, basin polygons | Stored basin geometry where present, else `null`/absent and stated |
| `stations[].calibration` | The model artifact's recorded training window, else `null` (there is no "unknown" value in the schema) |
| `stations[].thresholds`, `threshold_basis` | Stored thresholds (currently none: `null` / `none_available`) |
| `stations[].verification` | `null` — computed map-side from published data |
| issue `qc_status` | Mapped from stored `qc_status` (`raw` → `not_run`; unknown values → `unknown`) |
| issue `unit` | `m3/s` only; a station or forecast in another unit or parameter is excluded |

## Tasks

### T0 — Question 0: is archived forecast forcing available for the DHM record?
**Outcome**: a one-paragraph answer with evidence, sent to the map session **before any other task starts**.
- Check the recap-gateway products and adapters for reforecast/hindcast coverage overlapping the six basins'
  pre-2019 record; state what exists, its dates, and what does not; state the source of the series' historical
  issues (D8).
**In / Out**: a "Findings" section in this plan. Out: implementation.
**Verification**: the paragraph names each source examined with file:line or product id; a reviewer can re-check.
**Pre-change**: N/A (investigation).

### T1 — Schema copy, hash, and the provenance mapping
**Outcome**: the v3 schema is committed with its hash pinned, and the D4 mapping table is written and tested.
- Copy the schema to `docs/spec/`; record its SHA-256 in the test and the spec page; write the
  `forcing_kind` mapping table and the `source_mode` rule as pure functions with tests.
**In / Out**: `docs/spec/`, one small module, tests. Out: the routes.
**Verification**: a hash test that fails when one byte of the schema changes; table-driven tests for the mapping
including the `unknown` default.
**Pre-change**: RED — tests written first against the not-yet-existing functions.

### T2 — Region config, publication flags, and the shared restriction predicate
**Outcome**: `[region_bundle.<region>]` parses at the boundary into frozen types; the restriction predicate is
one shared function.
- Config load validates **shape only** (default withheld; a restricted-network station with `allowed` needs
  `permission_ref`; duplicate keys rejected). Existence and tenant checks need the database, so they run in a
  startup/CLI check command, and again per request (D2).
- Extract the delivery filter from `/observations` into a shared predicate used by that route, and add the
  admin case to its tests.
**In / Out**: `config/deployment.py`, the config reference, `api_stations.py`, tests. Out: enabling any region in a
shipped overlay.
**Verification**: unit tests for each rule; an `/observations` test showing an admin token is now also filtered
where a region flag withholds (state the behaviour change: admin still reads through the legacy admin surfaces).
**Pre-change**: RED — parsing and predicate tests first; the admin-filter test fails against the inline filter.

### T3 — Bundle route (manifest, stations, basins)
**Outcome**: `GET /api/v1/regions/{region}/bundle` returns the three fragments for the caller's readable stations.
- Expected statuses: in-scope consumer and reviewer 200; admin 200, filtered; no token 401; existing
  out-of-scope station absent; region with none readable 404 (D11); withheld datasets present with
  `publication` `withheld` (never omitted).
- Add the route to the Plan 401 route-auth matrix and to Plan 341's route inventory (or record the hand-off
  in that plan); note the dependency direction: **341/342 activation must cover this route**.
**In / Out**: the route, builders, matrix and inventory entries. Out: series.
**Verification**: route tests through the ASGI app for every role in the table above; each fragment validates
against the pinned schema; a foreign existing station and an unknown one are indistinguishable (identical 404).
**Pre-change**: RED — the route returns 404 today.

### T4 — Series route (one station)
**Outcome**: `GET /api/v1/regions/{region}/stations/{station_key}/series` returns the series document per D5, D6,
D9, D10.
- Forecasts are read through the publication-gate reader **when that reader exists**; until then the route reads
  raw forecasts only for tenants whose gate is inactive, and the tests inject an active predicate the way Plan
  341's own tests do (the setting rejects non-empty values today).
**In / Out**: the route and builders. Out: skill computation (map side).
**Verification** (each must fail against a naive implementation): delivery-tagged rows never appear for
consumer, reviewer **or admin** tokens; a station with any restricted row in the window gets `observations`
`null`, `verification` `null`, thresholds `null`; a direct request for an existing foreign station's series
is 404; a station whose `forecasts` flag is withheld returns an empty `forecasts` array and `withheld`; with an
injected active gate, unpublished and withdrawn values are absent and a selected publication survives
automatic supersession; a members-only forecast yields quantiles, members and `ensemble_size`; two products at
one issue time export one; a missing cycle slot is a declared gap; a horizon or quantile mismatch is excluded;
an undecidable forcing gives `unknown`; a request above the cap is 400.
**Pre-change**: RED — 404 today; the restriction and selection tests fail against a direct-read implementation.

### T5 — Contract, docs, the whole-bundle validator
**Outcome**: the committed contract has both routes at version **2.1** (D14, additive); the spec page documents
fragments, layout and field sourcing; the map's validator accepts an assembled bundle from a seeded run **and**
from real staging output (T6).
- Hand-offs to the map session, recorded here: the map's own copy of the contract
  (`schemas/sapphire-flow-api-v1-map.openapi.json`) and the Worker allow-list in `worker/apiProxy.ts` (checked
  against that copy) need entries for the region routes if the Worker will call them; the publisher writes the
  files. Also state the patch version bump.
**In / Out**: contract file, drift test, spec page, docs. Out: the map repository.
**Verification**: the drift test; the D14 CI check; the validator run over the assembled seeded bundle, recorded
with the command.
**Pre-change**: RED — the drift test fails until the contract is regenerated.

### T6 — Acceptance, bounded
**Outcome**: a recorded result for what can be served today, without onboarding or data movement.
- Preferred: an assembled six-station bundle from the staging database, validated. If the six cannot be served
  without excluded work, **one test basin** the map has said it will accept; if not even that, the recorded
  reason and the dashboard stays on fixtures. Expected today: no `operational` DHM station, observed series `null`.
**In / Out**: an acceptance record. Out: making stations operational, moving restricted data.
**Verification**: the record with commands and the validator output, or the recorded fallback reason.
**Pre-change**: N/A (operator run after merge; staging only).

## Exit gates

1. T0's paragraph is delivered before any other task began and its evidence is re-checkable.
2. Both routes exist, are in the committed contract at 2.1 and the route inventories, and every fragment
   validates against the pinned schema.
3. Tests prove a delivery-tagged row never appears for consumer, reviewer or admin, and a restricted-network
   station cannot publish `forecasts` or `derived_metrics` without a `permission_ref`.
4. Every dataset is withheld unless config allows it; the default is proved by a test.
5. The map's validator accepts an assembled seeded bundle, and T6 records real output or its bounded fallback.

## Not in this plan

Station onboarding, QC rules, the cadence seam and anything Plans 143/264/268/269/272 own; a live DHM adapter;
level-to-discharge conversion; model training; the Swiss deployment and the forecast-lab snapshot route; skill or
threshold computation; flipping any DHM observation or forecast to publishable; moving restricted data between
hosts; the map repository (only the hand-offs in T5).

## Risks

| Risk | Mitigation |
|---|---|
| The export becomes a side door for restricted DHM data | D2, D3, D6: role-independent filter, `permission_ref`, null observations/verification/thresholds, T4's discriminating tests. |
| Forecasts trained on restricted data are published without permission | D3: `allowed` needs a recorded decision reference (Plan 268 D9). |
| Real output fails the map's validator (mixed products, gaps, horizons) | D9 and T4's selection tests; T5/T6 validate real output, not only a seeded fixture. |
| Real Nepal forecasts do not exist yet for the six stations | T6's bounded fallback; labels stay `modelled`/`unknown`; fixtures remain the fallback. |
| Reanalysis-forced runs read as forecast skill | D4/D8: `forcing_kind` is derived, `unknown` renders as not verifiable; T0 settles what exists. |
| Cross-repository schema drift | D7: pinned hash and validation test. |
| Scope creep into onboarding plans | The stop condition in Scope; "Not in this plan". |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1", "T2"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T5"], "depends_on": ["phase-4"] },
    { "id": "phase-6", "tasks": ["T6"], "depends_on": ["phase-5"] }
  ]
}
```
