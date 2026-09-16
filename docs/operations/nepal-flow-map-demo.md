# Nepal illustrative export

Run from the backend repository with its usual uv environment:

```bash
uv run python -m sapphire_flow.cli.export_nepal_demo \
  --basin-file docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson \
  --output-dir /tmp/nepal-demo
```

Choose a fresh destination for each export. Existing directories/files/symlinks
are refused. The command validates the entire bundle, stages it beside the
output and publishes with an atomic no-replace rename on Linux/macOS. It does
not need a database, credentials, network, running API or model artifact.

The four files are region.json, series.json, station.geojson and basin.geojson.
For the flow-map importer assemble an object with keys manifest, series, station,
basin respectively. See [the contract](../spec/nepal-demo-bundle.md) and
[frontend handoff](../handover/nepal-flow-map-agent-prompt.md). The frontend’s old
location/schema constants and limited schema validator need the documented
updates before importing. Do not run its old synthetic generator afterwards.

The default demonstration issue time is 2025-08-12T00:00:00Z. Optional
`--issued-at 2025-08-13T00:00:00Z` shifts all times. The same input always produces
the same bytes. Seven days of hourly synthetic history precede issue; 72 hourly
forecast samples and a separately labelled synthetic outturn follow it. Nulls
represent explicit missing intervals. All values and spread are invented, never
operational forecasts, real measurements, or evidence of forecast skill.

Only the Dudh Koshi/Rabuwa basin outline is real, selected through Sandro’s
nepal_20010 fine-tune configuration. The displayed point is fictional at the GIS
outlet, not a verified location of the real gauge. See the [asset source notes](../handover/nepal-demo-assets/README.md).

Keep the illustrative banner, simulated-history label, spread disclaimer and
outturn disclaimer visible in the animation. Thresholds and comparator are null.
Swiss defaults, data and operational forecasting are unaffected by this command.

## Current handoff

Generated bundle: `/private/tmp/nepal-demo-rabuwa-v1/` (temporary local delivery).
A durable reproducible example is committed under `tests/fixtures/nepal_demo/`.
Backend branch: `feat/nepal-demo-export`; worktree: `/private/tmp/sapphire-nepal-demo`.
The original planning checkout has the current handoff link; implementation lives
in this isolated worktree.

The final bundle was checked against the current frontend JSON Schema. Its
series fields (history, forecast, verification and empty superseded cycles)
conform. Remaining failures are the old coordinate constants and basin
Polygon/properties schema; these are the consumer changes listed in the contract.
This check does not establish successful browser rendering.
