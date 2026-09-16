# Nepal illustrative export v2

Run from the backend repository with its usual uv environment:

```bash
uv run python -m sapphire_flow.cli.export_nepal_demo \
  --basin-file docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson \
  --output-dir /tmp/nepal-demo
```

Choose a fresh destination. Existing directories/files/symlinks are refused.
The command validates the whole bundle, stages it beside the output and publishes
with an atomic no-replace rename on Linux/macOS. No database, credentials, network,
running API or model artifact is required.

The five files are region.json, series.json, station.geojson, basin.geojson and
schema.json. Assemble the first four as manifest, series, station, basin and
validate against the included Draft 2020-12 schema, then apply semantic checks.
See [the v2 contract](../spec/nepal-demo-bundle.md) and
[frontend handoff](../handover/nepal-flow-map-agent-prompt.md). The consumer must
support the new version and a complete schema validator; do not run the old
frontend synthetic generator after import. V1 examples remain for reference.

The default first issue is 2025-08-12T00:00:00Z. Optional
`--issued-at 2025-08-13T00:00:00Z` shifts the whole run. Identical inputs produce
identical bytes. Eight issues are spaced six hours apart. Each supplies a full
p25/median/p75 band at 24 three-hourly times (+3..+72h). Hourly observations span
seven days before first issue through the final issue, inclusive (211 samples).
Two explicit missing intervals use nulls; zero is valid. There is no separate
verification record. No observations enter forecast generation and issue curves
are drawn independently, with no designed convergence or skill score.

Only the Dudh Koshi/Rabuwa outline is real, selected through Sandro's nepal_20010
fine-tune configuration. The displayed point is fictional at the GIS outlet,
not a verified real gauge location. See [asset source notes](../handover/nepal-demo-assets/README.md).
The requested 3h/72h/6h product differs from the hourly station fine-tune; this
export does not run that model or establish operational readiness.

Keep the illustrative banner, synthetic observation label and spread/verification
disclaimers visible. At each step show observations <= active issue, the complete
active band and complete predecessor bands. Hide later issues and observations.
Thresholds and comparator are null. Swiss defaults and operational forecasts are
unaffected. An optional labelled render-only basin derivative may coexist with
the unchanged authoritative outline; see the contract for provenance and checks.

## Current handoff

Generated bundle: `/private/tmp/nepal-demo-rabuwa-v2/` (temporary local delivery).
Durable reproducible example: `tests/fixtures/nepal_demo_v2/`.
Backend branch: `feat/nepal-demo-export`; worktree: `/private/tmp/sapphire-nepal-demo`.
The included schema describes this backend contract. Successful backend validation
does not establish frontend import or browser rendering; those checks belong to
the frontend agent.
