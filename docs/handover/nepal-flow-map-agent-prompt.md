# Nepal multi-cycle backend handoff (v2 preparation)

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
Export path and completed verification evidence will be recorded after implementation.
