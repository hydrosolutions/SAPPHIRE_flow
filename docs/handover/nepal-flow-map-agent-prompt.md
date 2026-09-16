# Nepal multi-cycle backend handoff (v2)

Worktree `/private/tmp/sapphire-nepal-demo`, branch `feat/nepal-demo-export`.
This responds to `SAPPHIRE_FLOW_NEPAL_CYCLES_EXPORT_PROMPT.md`.

The v2 contract is `docs/spec/nepal-demo-bundle.md`. Expect eight issues six hours
apart, 24 three-hourly points through +72h each, every issue carrying p25/median/p75.
The first issue remains 2025-08-12T00:00:00Z. One hourly observation record has
211 values from -168h through the last issue (+42h), inclusive. Display only obs
<= the active issue and only that issue plus its predecessors. The full band is
available immediately on issue; no progressive forecast reveal.

`series.forecasts` replaces the single forecast; separate `verification` and
`superseded` arrays are removed. `manifest.forecast_cycle` declares schedule,
representation and counts. Retain the verification disclaimer in metadata.
Forecasts use independent parameter draws without conditioning on observed values
or sequence index; synthetic observations have a separate seeded random stream.

Cadence decision: 3h/72h/6h follows the owner's intended product and aquacast's
global subdaily config. The station-specific Dudh Koshi fine-tune config is hourly;
this is a demonstration of the intended product, not a claim about that trained model.

Dialect is JSON Schema Draft 2020-12, with explicit `$schema`; schema.json ships
alongside the four data documents. Use a complete validator and preserve semantic
schedule/identity/gap checks. Wire version is flow-map-region-bundle/v2; reject v1
rather than interpreting its one-issue shape as a cycle sequence.

A separately labelled render-only simplified basin copy is acceptable. Keep the
full authoritative basin.geojson unchanged alongside it, record source and method/
tolerance, check topology/coordinates/outlet coverage, and use no simplified
geometry for hydrological calculations. This optional derivative stays outside
the four-document schema. Backend does not generate that copy.

The real Dudh Koshi/Rabuwa outline, fictional GIS outlet point, synthetic labels,
null thresholds/comparator and Swiss isolation remain as agreed. No checksums.

## Import now

Copy all five files from `/private/tmp/nepal-demo-rabuwa-v2/` into a Nepal-only
asset location. The durable example is `tests/fixtures/nepal_demo_v2/` in the backend
worktree; its bytes match the delivery. `schema.json` validates the assembled object
`{manifest: region.json, series: series.json, station: station.geojson, basin: basin.geojson}`.
The exact schema vocabulary and semantic checks are listed in the contract above.

Replace the v1 single-forecast importer and local synthetic/ghost generation with
this sequence. Keep Swiss startup defaults and assets isolated. Verify all eight
steps: no future observations/issues; full bands for active and predecessor issues;
explicit null gaps; synthetic banner/provenance/disclaimers at every step. The
backend does not claim successful frontend import or browser rendering.

Backend checks: 48 Nepal service/CLI/contract tests plus 28 existing station/forecast
API tests pass (76 total), changed-module pyright passes, and repository src/tests
lint and formatting pass. The delivery passes Draft 2020-12 and semantic validation;
its basin is byte-identical to v1. Whole-tree lint/format additionally reports
pre-existing migration-file issues outside src/tests. The default full regression
suite completed on code commit f3c7596b: **6311 passed, 51 skipped, 15 deselected**,
no failures (54 warnings; 18m12s). Live/deployment/slow marker exclusions remain as
configured in pyproject.toml. This branch has not been pushed or merged.

Independent Claude and Codex patch reviews plus the focused contract review found
no defects. Backend version: 0.1.910.
