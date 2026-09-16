# Nepal illustrative bundle v1

SAPPHIRE Flow produces four offline files. SAPPHIRE-flow-map imports and renders
these files. This specification follows the consumer implementation inspected on
2026-09-16; its old geometry constants require the update described below.

The machine-readable schema is generated from the export boundary models into
`nepal-demo-bundle-v1.schema.json`. It validates an object whose keys are
`manifest`, `series`, `station`, `basin`, mapped respectively to `region.json`,
`series.json`, `station.geojson`, `basin.geojson`. Every document declares
`region: nepal`; only the manifest declares `schema_version: flow-map-region-bundle/v1`.
Closed boundary models reject extra keys. No checksums are used.

## Manifest

Preserve the existing consumer names: `generated_at`, `generator_seed: 20260916`,
`source_mode: illustrative`, `banner_text`, `provenance`, `uncertainty_meaning`,
`spread_label`, `station`, `units`, `timezone`, `forecast`, `thresholds`,
`threshold_basis`, `comparator`, `date_basis`, `date_label`, `verification_label`,
`verification_note`, `supersession`.

Banner: **Illustrative scenario — synthetic data, not an operational forecast**.
Spread: **Illustrative spread — not calibrated uncertainty**.
`uncertainty_meaning` is `illustrative_spread`. `date_basis` is `demonstration_date`.
History and verification are invented, never real gauge measurements. Verification
agreement/disagreement is arbitrary, never evidence of skill. No scores are supplied.

Station: network `demo`, code `DEMO-NP-001`, display name `Illustrative demo gauge`,
backend UUID null, longitude **86.668726**, latitude **27.269326**. This fictional
point uses the Rabuwa GIS outlet as geographical context; it does not assert the
precise location of the real DHM instrument. Discharge unit `m3/s`; display timezone
`Asia/Kathmandu`. Stored instants are UTC RFC3339 at second precision with Z.

`provenance` has basin/basemap/station/observations/forecast entries, each with
`kind` and plain `attribution`. Basin kind is `real_geometry`, basemap is
`third_party_raster`, and the remaining entries are `synthetic`. The basemap note
refers to attribution from frontend configuration; no tiles are provided here.

`forecast` contains `forecast_id`, `issued_at`, `representation: quantiles`,
`quantile_levels: [0.25, 0.5, 0.75]`, `cadence_seconds: 3600`, `horizon_start`,
`horizon_end`, `valid_times`, `qc_status: synthetic_eligible`, `eligibility_note`.
That QC string means eligible for illustrative playback only, not operational QC.
Thresholds/comparator are null; threshold basis is `none_available`.

## Series

`series.json` has `region`, `observations`, `forecast`, `verification`, `superseded`.
The concurrently updated consumer requires supersession metadata. This one-issue
scenario supplies `superseded: []` and manifest `supersession` with
`cycle_hours: 6` (consumer playback configuration only),
`label: Single-issue illustrative scenario`, and
`note: No earlier forecast cycles are supplied in this demonstration.`
No prior forecast cycles or operational schedule are implied.
All three blocks have `source_mode: illustrative`, `unit: m3/s`, ordered
`valid_times` and `gaps` (objects with `start`/`end`). History and verification
carry `values`; forecast carries `series` with exactly keys `0.25`, `0.5`, `0.75`.

History additionally declares `window_start`, `window_end`, `cadence_seconds`.
Forecast declares its matching `forecast_id`. Verification declares
`kind: verification_outturn`, `starts_at_issue_time: false`; its times match the
forecast. All windows and gap intervals are **half-open [start,end)**.
`horizon_start` equals the first valid time, issue +1 hour.
`horizon_end` is one cadence after the last valid time (issue +73 hours).

Default issue/generation time: **2025-08-12T00:00:00Z**, deliberately a past
illustrative date, not a record of actual conditions. An explicit issue override
shifts all times. No wall clock, network or database affects generation.

- History: 168 hourly samples at issue -168 through -1 hours; six null samples
  at -48 through -43 hours (gap [-48,-42)).
- Forecast: 72 hourly samples at +1 through +72 hours, no missing values,
  nonnegative ordered p25/median/p75, invented rise/peak/recession.
- Verification outturn: same 72 times, a delayed lower peak, null at +41/+42
  hours (gap [+41,+43)); never used to construct the forecast.

The validator requires a value to be null if and only if its valid time falls
in a declared gap interval; any disagreement is rejected.
All values are finite/nonnegative or explicit nulls matching gap intervals;
zero is a real value and must not be substituted for missing data. The validator
checks lengths, cadence, identities, times, ordering and gaps across files.

## Geography

`station.geojson` is a FeatureCollection with one Point. Feature properties
exactly equal manifest.station, including longitude/latitude.
`basin.geojson` is a FeatureCollection with `region`, plain `attribution`, one
MultiPolygon feature with `id: NP_1_00092` and these properties:

```json
{
  "basin_id": "NP_1_00092",
  "name": "Dudh Koshi at Rabuwa",
  "source": "BARHKH NepalCaravanified/nepal_watersheds_merged_dedup.gpkg; basin outlines by Nicolas"
}
```

Preserve the source geometry from `docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson`
in EPSG:4326, longitude first. Reject empty/invalid geometry, nonfinite or out-of-range
coordinates, incorrect basin identity and an outline not covering the display point.
Source station `nepal_20010` is identified by Sandro's fine-tune configuration;
only basin geometry is reused. See the asset README for the source mapping.

## Consumer integration

The inspected frontend uses the same field layout but still pins the unrelated
old station at (82.92333, 28.24417), a 99.73 km² Polygon, and different basin
properties. Update those schema constants/types to the Rabuwa Point/MultiPolygon
and properties above. Import these four documents instead of regenerating its
old scenario. The importer currently reads a combined object; assemble it using
the key-to-filename mapping above. Pin one shared schema revision. The generated Pydantic schema uses `$ref`,
`$defs`, `anyOf` and array constraints: the current consumer custom validators
do not support all of these. Use a complete JSON Schema validator (or extend the
consumer validator) and test rejection of malformed nullable values and geometry;
copying the schema alone is insufficient. The Python
export boundary additionally performs semantic checks beyond JSON Schema.

Frontend import/animation completion is a separate verification step. Backend
checks alone do not establish frontend compatibility. Swiss paths and operational
APIs remain outside this export.
