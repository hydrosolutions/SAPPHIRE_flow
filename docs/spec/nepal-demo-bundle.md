# Nepal illustrative bundle v2 — multi-cycle animation

Version: `flow-map-region-bundle/v2`. This replaces the single-issue v1 wire
shape; v1 is rejected by the new parser, never silently reinterpreted. The frozen
[v1 contract](nepal-demo-bundle-v1.md), v1 schema and example remain available.
Four data files retain their names: region.json -> manifest, series.json -> series,
station.geojson -> station, basin.geojson -> basin. The exporter also ships
**schema.json**, identical to `nepal-demo-bundle-v2.schema.json`, validating the
assembled four-key object. Every data document has `region: nepal`.

## Cycles and time convention

The first default issue is **2025-08-12T00:00:00Z**. There are **8 issues**, exactly
**6 hours apart**, ending 2025-08-13T18:00:00Z. Each has **24 three-hourly steps**
at issue +3, +6, ..., +72 hours. No forecast sample occurs at issue time.
This follows the owner's intended demo product and aquacast's
`configs/global/subdaily/cmal_global_subdaily.yaml` (72 hours, aggregation 3h,
issues 00/06/12/18 UTC). The station-specific fine-tune config
`configs/finetune_sweep/dudh_koshi/full_model.yaml` instead uses hourly targets
and currently lists three-hourly issue hours. This demo does not assert that an
operational Nepal model is configured or validated for the requested schedule.

`manifest.forecast_cycle` replaces `manifest.forecast` and contains:

```json
{"cycle_hours":6,"cadence_seconds":10800,"horizon_steps":24,"issue_count":8,
 "representation":"quantiles","quantile_levels":[0.25,0.5,0.75],
 "starts_at_issue_time":false}
```

`series.forecasts` replaces `series.forecast`: ordered oldest to newest, each with
`source_mode: illustrative`, `unit: m3/s`, unique `forecast_id`, `issued_at`,
`valid_times`, full `series` keys `0.25`, `0.5`, `0.75`, `gaps: []`,
`qc_status: synthetic_eligible`, `eligibility_note`, `horizon_start`, `horizon_end`.
All 24 values in each quantile are finite, nonnegative and ordered. Every issue
is complete when issued. IDs are deterministic from the demo station and issue.
Windows remain **[start,end)**: horizon_start is issue +3h, horizon_end is issue
+75h, one cadence beyond the last +72h sample. All times are UTC RFC3339 seconds Z.
The optional `--issued-at` shifts the FIRST issue and all derived timestamps.

## One observation record

`series.observations` retains source_mode/unit/valid_times/values/gaps,
window_start/window_end/cadence_seconds, at **hourly cadence (3600 seconds)**.
It contains **211 samples**, from first issue -168h through last issue (+42h)
inclusive. Its half-open window ends at first issue +43h. Explicit gaps are
[-48h,-42h) and [+17h,+19h), relative to first issue; null iff inside a gap.
Zero remains a valid discharge value. There is no separate verification series
and no superseded array. At animation step k, display observations at times
**<= issue[k]**, all of issue k, and only predecessor issues (indices <k).
Do not show later observations or later forecasts. Fading does not change data.

`manifest.verification_note` states: observations later than an issue are never
inputs to it; agreement or disagreement is arbitrary and illustrative, not
forecast skill; no skill score is computed. `verification_label` is removed.
`manifest.supersession` has cycle_hours 6, label `Eight illustrative forecast issues`,
and a note that predecessors retain complete bands and future issues are hidden.

## Independence and labels

Generate observations using a separately seeded random stream: an invented
positive Gaussian discharge hump with a small oscillation and bounded noise.
Its particular amplitude/shape is presentation data, not a physical calibration. Draw each issue's
absolute peak time (relative to the first issue), amplitude and width independently
from the SAME distributions, without conditioning on issue index or observed values.
The issue timestamp only selects the forecast sampling window. Never sort issues
by error or shrink their deviations toward observations. A fixed common event
context is allowed; no improving-skill narrative or validation metric is encoded.
Changing the observation stream must leave all forecast arrays unchanged.
`generator_seed` is the base seed: observations use it directly and forecasts use
base + 1; both stream seeds are recorded in per-asset provenance.
Reproducibility is guaranteed for the fixed generator seed and first issue time.

The schema_version value changes to flow-map-region-bundle/v2.
Retained manifest fields: region, generated_at (fixed to first
issue, not wall clock), generator_seed 20260916, source_mode illustrative,
banner_text, provenance, uncertainty_meaning illustrative_spread, spread_label,
station, units, timezone, thresholds null, threshold_basis none_available,
comparator null, date_basis demonstration_date, date_label and verification_note.
Banner: **Illustrative scenario — synthetic data, not an operational forecast**.
Spread: **Illustrative spread — not calibrated uncertainty**.
Station remains demo/DEMO-NP-001, Illustrative demo gauge, backend_uuid null,
longitude 86.668726 / latitude 27.269326. Display timezone Asia/Kathmandu.
Provenance separates real_geometry basin, third_party_raster basemap attribution
from frontend config, and synthetic station/observations/forecast.

## Geometry and schema

The authoritative NP_1_00092 Dudh Koshi at Rabuwa MultiPolygon is unchanged,
EPSG:4326 longitude first. station.geojson feature properties equal manifest.station.
Basin feature id/properties and attribution are unchanged from v1: basin_id,
name, plain source note. No checksum. See the asset README for fine-tune mapping.

A separately named and labelled **render copy** is acceptable for display only.
Keep the full outline alongside it; do not replace basin.geojson or use a simplified
outline for hydrological calculations. Record the simplification method/tolerance
and source filename in its provenance, validate topology/coordinates and retain
outlet coverage. That optional derivative is frontend-owned and outside the
four-document validation object; this exporter ships only the authoritative basin.

JSON Schema dialect: **Draft 2020-12**, explicitly declared by `$schema`.
Use a complete validator. The delivered schema uses `$schema`, `$defs`, `$ref`,
`anyOf`, `type`, `properties`, `required`, `additionalProperties`, `const`, `enum`,
`pattern`, `items`, `minItems`, `maxItems`, `minLength`, `minimum`, `maximum` and
`title`. No custom keywords. Pydantic's semantic
validation additionally enforces identities, exact schedules, half-open windows,
quantile order and gap masks; frontend must keep equivalent semantic checks.
Reject wrong versions, inconsistent counts/cadence, duplicate IDs, misordered issues,
incorrect QC/source labels, crossed quantiles, mismatched gaps or invalid geometry.
