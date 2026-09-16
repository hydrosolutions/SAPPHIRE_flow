---
status: READY
created: 2026-09-16
revised: 2026-09-16
plan: 273
title: Nepal illustrative demo — backend data producer and offline export
related: [139, 143, 192, 198, 204, 219, 268]
---

# Plan 273 — Nepal illustrative demo data

## Outcome and ownership

SAPPHIRE Flow supplies a deterministic, validated offline data bundle.
SAPPHIRE-flow-map consumes it and owns startup region selection, rendering,
playback, basemap configuration and recording. We can implement and test the
producer without waiting for the frontend UI.

The owner selected clearly labelled illustrative forecasts on 2026-09-16 and
then clarified that this repo supplies the backend. This supersedes the earlier
recommendation that the map generate the synthetic data itself. No operational
forecasting changes are needed; the backend deliverable is a data producer/export.
The owner subsequently instructed “proceed. switch to automode”. This delegates
implementation and routine review corrections for this bounded offline producer;
PR creation, push, merge and deployment remain excluded.

**Geometry decision, 2026-09-16:** owner confirmed using the Dudh Koshi/Rabuwa
data associated with Sandro's `nepal_20010` fine-tune and requested removal of
checksum requirements. A matching basin outline has been extracted to
`docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson`. Keep only its basin
ID, name and plain source note; forecasts remain synthetic.

In: one fictional station, simulated discharge history, one fixed synthetic
forecast with illustrative spread, existing basin geometry, versioned files,
tests and export instructions.
Out: operational onboarding, trained models, FI adapters, DB writes, restricted
DHM data, live weather requests, API routes, Prefect, Docker, deployment,
real warning thresholds, skill claims or frontend edits. This is a fixture
producer, not a forecast model registered with the operational pipeline.

## Repository evidence

- Freshly fetched main: `f2dc569c`, 2026-09-16 inspection. Plan 273 is available.
  Isolated worktree `/private/tmp/sapphire-nepal-demo`, branch
  `feat/nepal-demo-export`, created from that main.
- The concurrently implemented frontend now has `schemas/flow-map-region-bundle-v1.schema.json`
  and `scripts/build_nepal_bundle.py`. Adopt its field layout, explicit gap lists,
  half-open windows and `synthetic_eligible` playback state. Its hard-coded old
  Nepal location and Polygon metadata require a small consumer update for Rabuwa.
  Record that pending integration explicitly, without waiting on the UI or editing it.
  A later consumer addition requires supersession fields: provide its metadata
  with an explicitly empty older-cycle list, preserving the approved single-issue scope.
- Forecast Lab v2 is explicitly BAFU-only and remains unchanged. Generic APIs
  can support later real data; this export requires no server or token.
- Aquacast `configs/basins/dudh_koshi.txt` names `nepal_20010`; the original
  station metadata identifies it as Dudh Kosi at Rabuwa. The BARHKH GIS package
  identifies the corresponding Rabuwabazar basin as `NP_1_00092`. The extracted
  GeoJSON preserves that MultiPolygon in EPSG:4326 (about 3,720 km²).
  This replaces the unrelated small test-basin fixture. See the adjacent asset
  README for source paths and the GIS outlet used for the fictional display point.
- The seeded Nepal station has placeholder geometry and is not used.
  Geopandas, shapely and Pydantic already exist; no new dependency is needed.
  Existing CLI conventions use module invocation and structlog.
  `cli/export_forecast_lab.py` demonstrates validation-before-write.

## Shared contract

Use the frontend's proposed four-file layout and identifier. Pin exact fields
in T1 and maintain one authoritative schema/example here; the consumer pins
that version. Frontend completion is not required. Align to the inspected consumer and document
the remaining geometry/schema import change as a separate integration step.

| File | Backend-supplied content |
|---|---|
| `region.json` | Version, region, source mode, deterministic timestamps, label, units, timezone, provenance, fictional station identity, forecast representation/cadence, null comparator/thresholds. |
| `series.json` | Simulated history and one forecast: ordered UTC times, values, illustrative quantile levels and aligned arrays. Exact nested names are fixed in T1. |
| `station.geojson` | One fictional point, code `DEMO-NP-001`, network `demo`, null backend UUID and source note. Use the basin's GIS outlet: longitude 86.668726, latitude 27.269326. |
| `basin.geojson` | The extracted Dudh Koshi/Rabuwa MultiPolygon with feature properties `basin_id`, `name`, and `source`. |

Every document declares `region: nepal`. The backend does not download basemap tiles. Its required provenance note
delegates actual basemap attribution to the map configuration.
Distinguish real basin geometry from synthetic station/history/forecast.

Required label:
**Illustrative scenario — synthetic data, not an operational forecast**.
History is explicitly simulated. Spread carries
`uncertainty_meaning: illustrative_spread`; no calibrated coverage claim.
Use the consumer’s `synthetic_eligible` playback state, never operational
`qc_passed`. No operational QC enum changes.
Thresholds and comparator are null; no Swiss danger levels or skill scores.

Declare discharge wire unit `m3/s` (rendered m³/s) and display timezone
`Asia/Kathmandu`. All stored timestamps are UTC RFC3339 with Z.
Issue time and forecast valid times remain distinct.

Proposed scenario: seven days of hourly synthetic history strictly before issue,
a labelled six-hour missing interval, and 72 hourly forecast steps at issue +1
through +72 hours. Invent a rise, peak and recession with nonnegative ordered
p25/median/p75. This is presentation data, not a trained/physical runoff model.
No rainfall causality or predictive accuracy is implied. Pin explicit null/omitted
gap representation in T1; zero remains a valid discharge, never a missing marker.

Default issue and generation times are fixed. Use the consumer’s past demonstration issue date, 2025-08-12T00:00:00Z.
Include its synthetic verification-outturn series for playback, with a delayed,
lower peak and explicit missing interval; label it as invented and never compute
skill scores. Verification is at issue +1 through +72, not at issue itself.
An optional CLI issue-time argument
is parsed at the boundary. The generator takes typed UTC input; no wall clock,
global randomness, live data or host-specific paths enter output. Use a locally seeded RNG (20260916, recorded by the consumer schema), passed
explicitly to the pure builder, alongside analytic rise/peak/recession curves.

## Implementation shape

Keep the pure builder in `services/nepal_demo.py`, small immutable domain
values in `types/nepal_demo.py`, and boundary models/validation in `cli/nepal_demo_schemas.py`, with CLI in
`cli/export_nepal_demo.py`. Pydantic is restricted to input/output boundaries.
A committed generated JSON Schema and a synthetic example are the shared
references. No production forecast schema or FI change is involved.

Export command:

```bash
uv run python -m sapphire_flow.cli.export_nepal_demo \
  --basin-file docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson \
  --output-dir /tmp/nepal-demo
```

Require a fresh output directory and refuse an existing destination. Validate
the whole bundle, stage beside its destination, then publish the completed
directory by atomic no-replace rename (Linux/macOS; fail clearly elsewhere). On failure, clean up only exporter-owned temporary files;
never expose a partial bundle or overwrite an existing output.
Do not write automatically into the other repo or Swiss asset directories.
The consumer explicitly stages the exported directory into its Nepal namespace.

The command must work without DATABASE_URL, API credentials, deployment config,
network access or containers. Runtime diagnostics use structlog and do not enter
the deterministic artifacts.
The explicit `--basin-file` avoids depending on a developer's Dropbox or locating
test fixtures at runtime. Validate the GeoJSON shape and coordinates; no hashes
or source-manifest machinery are required.

## Tasks

### T1 — Agree the exact file contract

**Outcome:** exact contract in `docs/spec/nepal-demo-bundle.md`, aligned to the
current frontend field layout. Document the required Rabuwa schema update.
**In / Out:** specification, this plan and handoff prompt; no production schema
or frontend edits.
**Pre-change:** N/A — contract/planning. Frontend section 8 leaves series
structure incomplete and assigns generation to its own N5.
**Verification:** account for each consumer field, identity, synthetic QC,
null/gap semantics, basemap provenance and file layout. Record the inspected consumer revision and residual integration changes; do not
wait for UI implementation. No claim of consumer acceptance until verified.

### T2 — Generate and validate the deterministic scenario

**Outcome:** pure builder, domain values, boundary/schema and small synthetic
example; identical explicit inputs produce identical serialized bytes.
**In / Out:** proposed modules, `docs/spec/nepal-demo-bundle-v1.schema.json`,
`tests/unit/services/test_nepal_demo.py`,
`tests/fixtures/nepal_demo/`; no model registration, production QC or DB.
**Pre-change:** new behavior tests fail before the generator/contract exists.
**Verification:** `uv run pytest tests/unit/services/test_nepal_demo.py -q`.
Check history/horizon, monotonic UTC times, cadence, explicit missing interval,
ordered nonnegative quantiles, aligned lengths, illustrative provenance and
absence of operational-QC claims. Reject wrong region/source mode, naive time,
non-finite values, unordered quantiles and length mismatches. Validate the example
against the generated schema; repeated generation is byte-identical.

### T3 — Export the complete bundle without infrastructure

**Outcome:** CLI exports the four files, including the supplied Rabuwa geometry.
**In / Out:** CLI and `tests/unit/cli/test_export_nepal_demo.py`; no downloads,
other-repo writes, stores, API, config, deployment or Swiss artifact changes.
**Pre-change:** CLI behavior tests fail before the exporter exists.
**Verification:** `uv run pytest tests/unit/cli/test_export_nepal_demo.py -q`.
Run with DB/auth unset and network calls blocked. Check cross-file identity,
versions, times, units, geometry and station/source-outlet agreement. Compare
bytes from two exports to fresh destinations. Inject write/conversion failure:
no partial published directory. Existing output must be refused and unchanged, including dangling symlinks and
a destination created after staging. Use ctypes with Linux `renameat2(RENAME_NOREPLACE)` and macOS
`renamex_np(RENAME_EXCL)`; unsupported platforms fail without publishing.

### T4 — Document and deliver

**Outcome:** `docs/operations/nepal-flow-map-demo.md` documents the command,
scenario, labels, geometry provenance, contract and explicit import handoff.
Provide generated artifacts at an agreed local path.
**In / Out:** runbook, plan/index, exporter output outside source; no frontend
rendering, recording, hosting or deployment.
**Pre-change:** N/A — documentation/handoff.
**Verification:** run the documented CLI into a fresh temporary directory and
validate all files. Producer completion is independently testable. Record consumer
import compatibility separately when its importer is available; do not claim the
animation is complete from producer tests alone.

## Reviews and implementation gates

The first independent Codex pass found one P2: unspecified basin metadata.
Resolved by explicitly defining basin feature ID/properties in the contract.
The current consumer-aligned plan receives independent Claude and Codex passes
plus a focused contract review before implementation. Both independent Codex passes completed: corrected the obsolete handoff link,
documented consumer schema vocabulary support, and required atomic no-replace
publication (including a destination race regression). Claude Sonnet completed the text-only design pass. Its corrections are
recorded: name the platform-specific no-replace primitives, make gap/null
equivalence explicit, and define horizon_start as the first valid time. These
clarify the existing design. The earlier interrupted Claude attempts are not
counted as completed reviews. READY records the owner’s “proceed; switch to
automode” authorization, not a reviewer granting approval. Owner automode authorization permits routine corrections and implementation;
no agent report grants authority to push, open a PR, merge or deploy.

Implementation starts on the isolated clean named branch at freshly fetched main.
Run focused tests, lint/format/type checks and update affected docs. Code commits
include the patch version bump. The full suite is required before merge.

Implementation evidence (2026-09-16):
- T1: contract aligned to inspected frontend field layout; geometry and schema-validator
  changes remain explicitly assigned to frontend integration.
- T2/T3 RED: newly added behavior tests failed collection for the absent producer/exporter.
  GREEN: 33 producer/export/schema tests pass; fixture passes the generated schema.
  Tests cover deterministic bytes, preserved geometry, invalid contracts, zeros/nulls,
  CLI time shifting, write failures, existing destinations and the publication race.
- T4: documented CLI exported four files to `/private/tmp/nepal-demo-rabuwa-v1`;
  committed example is `tests/fixtures/nepal_demo/`.
- Existing station/forecast API regression checks: 28 pass (61 combined).
  Repository-wide ruff lint/format pass; changed-module pyright: zero errors.
- Independent Codex repository review and focused contract/safety review completed
  with no findings, including the final empty superseded-cycle fields. Claude
  Sonnet reviewed the complete final base-branch diff (schema, fixtures, geometry,
  version files and docs included) with no correctness findings. Its earlier
  TypeAdapter style suggestion was declined: explicit `[str]` is needed to avoid
  unknown-type inference, as verified by pyright. A final type-only annotation
  made the empty list's boundary dictionary type explicit; pyright remains clean.
- Fresh isolated uv environment also passes the 61 checks and the module CLI.
  Publication primitives were exercised natively on macOS; Linux awaits CI.
- Full-suite/CI and frontend rendering remain unverified; they are required before
  merge / animation acceptance respectively. No operational endpoint was added.
  Backend implementation is complete on the feature branch; READY is retained
  until owner integration/merge disposition.

## Later real forecasts

Real station/data/model readiness and DHM publication restrictions remain separate
work under Plans 143/268 and related QC plans. The Nepal weather feed is not a
discharge forecast. None of those operational dependencies blocks this producer.

```json
{
  "phases": [
    {"id": "contract", "tasks": ["T1"], "parallel": false},
    {"id": "producer", "tasks": ["T2"], "depends_on": ["contract"]},
    {"id": "export", "tasks": ["T3"], "depends_on": ["producer"]},
    {"id": "handoff", "tasks": ["T4"], "depends_on": ["export"]}
  ]
}
```
