# Basin/static package fixtures

Real, contract-compliant basin/static packages produced by the **adjacent basin/static
extractor** (not SAP3 code — see `docs/requirements/04-basin-static-artifact-contract.md`
and Plan 117). Used to exercise the Plan 120 importer and `04`-contract validation.

## `nepal-dhm-basins/`

Provided by the extractor dev on 2026-07-17 (`extractor: static-attrs-nepal v0.1.2`).
One DHM test basin. Verified on receipt:

- `contract_version: basin-static-artifact/v1`; all 5 `manifest.json` sha256 checksums valid.
- Single-kind GeoPackage (one `polygons` layer, MULTIPOLYGON, EPSG:4326).
- Feature `name = g_123` (the `g_<station_code>` convention); `gateway_hru_name = nepal_dhm_v1`.
- Join key `gauge_id = nepal_123` is byte-identical across `basins.gpkg` and
  `static_attributes.parquet` (1 row × 93 cols: 92 float HydroATLAS/ERA5-Land attributes +
  `gauge_id`). `feature_catalog.json` documents all 92 features.
- Climatology window 1991-01-01 → 2020-12-31 (WMO 30-yr). No sensitive content.

**Known quirks (flagged upstream to the extractor, non-blocking for fixture use):**
- `polygons.delineation_method` carries a Rust `Debug`-format string
  (`Snap { strategy: WeightFirst, snap_id: SnapId(85891), distance_m: 32.69, ... }`) rather than a
  clean value; its embedded `distance_m` (32.69) also disagrees with the
  `outlet_snap_distance_m` column (37.57).
- `gauge_id` uses a `nepal_` prefix while `network = dhm` — confirm the intended id convention.
- `display_name = testin_123` looks like test-data placeholder text.
