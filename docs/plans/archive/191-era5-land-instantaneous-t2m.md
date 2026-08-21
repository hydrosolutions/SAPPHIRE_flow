---
status: COMPLETE
created: 2026-08-20
plan: 191
title: ERA5-Land instantaneous variable path — t2m transform and point extraction
scope: Give the DHM-precipitation ERA5 pipeline an INSTANTANEOUS transform path (K->degC) and a narrow point extraction for it, producing a per-station hourly ERA5-Land 2m-temperature series at the grid cell. NOT the lapse correction, NOT the Pyramid check, NOT IMERG, NOT any generalisation of the accumulator path.
depends_on: [171, 174]
blocks: [184]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 191 — ERA5-Land instantaneous path (t2m)

## Status

**COMPLETE** — shipped on `main` as `06c2731` (feat(dhm-precip): Plan 191 — ERA5-Land instantaneous path (t2m transform + narrow extraction) (#199)).
*(Status reconciled 2026-08-21 in a housekeeping pass: the plan had shipped but was still marked READY, so it read as outstanding work.)*

**READY** — owner go, 2026-08-20, against the task breakdown in session. This is the last
prerequisite for Plan 184 D14, which needs a measured sub-freezing mass fraction and therefore needs
temperature.

**The data is already acquired and verified.** 74 raw monthly windows / 397 MB at
`data/dhm_precip/era5_land_t2m/era5_land/raw/era5_land_t2m_raw_{window}.nc`, its own data root, its
own manifest with `variable: "2m_temperature"` physically present on disk. `transformed_years` and
`accumulation_diagnostics` are both `{}` — nothing downstream of acquisition exists yet.

**Baseline before any change (2026-08-20):** the env-gated real-data suite is green —
`DHM_PRECIP_ERA5_ROOT=data/dhm_precip DHM_PRECIP_XLSX=…xlsx uv run pytest
tests/integration/test_dhm_precip_era5_extraction.py tests/integration/test_dhm_precip_reproduction.py`
→ **15 passed in 231 s**. 🔴 CI never sets those vars, so a green CI run proves nothing here. Every
task re-runs this suite locally.

## ⛔ Proportionality

**This is a units shift and a point lookup, not a second pipeline.** The instantaneous path is
genuinely simpler than the accumulator path — no deaccumulation, no boundary context, no conservation
post-conditions, no packing accounting, no orography run. A revision of this plan that generalises the
existing precipitation modules into a variable-polymorphic framework has made it worse. Adding length
is a cost. Reviewers: "no findings" is a complete review; do not propose registries, schemas or
abstraction layers.

## The framing

PR #194 fixed a filename that **lied** at the raw layer: `era5_land_tp_raw_{window}.nc` was keyed on
window alone, so a t2m fetch would have overwritten 868 MB of precipitation under a name still saying
`tp`. **The identical lie exists one layer down.** `product_artifact_path`
(`era5_manifest.py:55-56`) has no variable parameter at all, so a t2m product lands at
`era5_land_tp_mm_{year}.nc` — saying `tp`, saying `mm` — even under the separate t2m root. And
`era5_transform.py:211` calls `raw_artifact_path` **without** `variable_code`, so it silently resolves
`tp`: the variable-awareness that protects acquisition is exactly what leaves the transform blind.

## Decisions

- **D1 — A separate instantaneous path, not a generalisation of the accumulator path.** New module
  `scripts/dhm_precip/era5_instantaneous.py`. **`era5_deaccumulate.py` is not touched.** The two
  transforms share storage primitives (atomic publish, `.prev` rollback, manifest reconciliation) and
  nothing else. Merging them behind a strategy seam would put the 01 UTC reset one boolean away from
  an instantaneous field — the exact failure mode #194's exit-code-7 guard exists to prevent.

- **D2 — Product naming.** `<t2m_root>/era5_land/degc/era5_land_t2m_degc_{year}.nc`, mirroring
  `hourly_mm/era5_land_tp_mm_{year}.nc`. The directory is unit-named because the existing one is; the
  filename carries **both** the variable code and the unit, because that is precisely what the raw
  layer got wrong. Data variable `temperature`, `units` attr `degC`.

- **D3 — The identity hashes ONLY inputs that are actually READ (M-A5 rule P7a).**
  `transform_identity` (`era5_manifest.py:102`) hashes `accumulation_rule_id`,
  `packing_tolerance_mm`, `conservation_tolerance_m` and `units_factor`. **A K->degC transform reads
  none of them.** Reusing it verbatim would hash parameters the transform never consults — the exact
  rule this track wrote down after M-A5. The instantaneous path gets its own
  `instantaneous_identity` over: the twelve source sha256s it reads, `units_conversion`,
  `output_schema_version`, `transform_version`, output format/dtype/encoding.

- **D4 — No boundary context, and therefore none in the identity.** An instantaneous field needs no
  previous or next month: the 01 UTC reset that dominates `era5_deaccumulate.py` does not apply. Of
  the 74 acquired t2m windows, `2019-12-31` and `2026-01-01T00` exist only because deaccumulation
  needed them. The transform **must not read them** and **must not hash them** — a twelve-hash
  identity, never fourteen. `Era5MissingBoundaryContextError` is not on this path.

- **D5 — Extraction is narrow.** t2m needs one nearest series per station. It does **not** get the
  M-A5 bundle shape: no bilinear comparand, no `operator_sensitivity.csv`, no orography run. The
  extraction identity in `era5_extract_manifest.py` hashes `wet_threshold_mm_per_h`, `zero_policy`, an
  8-point quantile grid and `delta_statistics` — all precipitation semantics, all meaningless for
  temperature, and hashing them would violate D3 in the other direction. NEAREST remains THE operator
  (`era5_extract.py:258`); that is not re-litigated. **"Narrow" is about the PAYLOAD shape only, not
  about publication.** *(Corrected 2026-08-20, review round 2.)* An earlier revision of this task
  published t2m to a single fixed path (`era5_land/points/series_t2m_degc.nc`), swapped in place via a
  `.points.prev` backup — a crash between the two renames left the canonical path briefly ABSENT, and
  two concurrent publishers sharing one backup name could deadlock and strand both staging dirs. t2m
  now publishes through the SAME `allocate_published_dir`/`prepare_staging_dir` primitives the
  precipitation bundle uses (P1/P1a): a fresh, per-run-unique `<NNNN>-<identity>` directory under
  `era5_land/points/`, so `os.replace` never faces a non-empty target and there is no backup to race
  on. Discovery is therefore the SAME rule for both bundles — the highest `NNNN` whose manifest
  validates (P2/P6) — never a fixed path and never a glob on `*-<identity>`.

- **D6 — Elevations are reused, never re-derived.** Same 0.1° grid, same box ⇒ same nearest cell, so
  the published precipitation bundle's `station_grid_elevation.csv` already carries `station_elev_m`,
  `orography_elev_m` and `elev_mismatch_m` for all 26 stations (verified: Syangboche −747.38 m, Humde
  −1299.18 m, `datum_reconciled = UNRECONCILED` on every row). That is D14's lapse-correction input,
  already computed. The t2m extraction **references** that table and records the source bundle's
  identity; re-running orography would re-derive a validated input for no gain.

- **D7 — This plan stops at the CELL-LEVEL series.** The 6.5 °C/km lapse correction and the Pyramid
  `AT` independent check are **Plan 184 D14**, not this plan. Reason: "if the check fails, widen the
  reported uncertainty" is an analysis behaviour, so a pre-corrected product would have to carry the
  Pyramid check with it to be honest — dragging M-A6's analysis into its own prerequisite. Note
  `pyramid_loader.py` parses `RR` only (`AT` appears solely in an error message), so the AT loader is
  new work either way; it belongs beside the analysis that consumes it.

- **D8 — Seam tests, not decision tests.** #194's fifth bug: `variable` never crossed the pydantic
  boundary model, so it was written, dropped on serialisation, read back as the default, and the guard
  rejected its own data root — while 18 tests passed against an inert mechanism. Every artefact this
  plan introduces gets a test asserting the value is **physically present on disk**: the product
  filename actually contains `t2m_degc`, the identity payload actually lists twelve source hashes and
  not fourteen. Read-back alone passes on a default and proves nothing.

## Tasks

### T1 — make the product layer variable-aware
**Scope:** `product_artifact_path` takes a variable code and product directory; `era5_transform.py:211`
passes `variable_code` through to `raw_artifact_path`. Defaults preserved so every existing
precipitation path resolves byte-identically. **Out:** any change to transform behaviour or output.
**Verification:** `uv run pytest tests/unit/scripts/test_era5_manifest.py
tests/unit/scripts/test_era5_transform.py` plus a new test pinning the six existing
`era5_land_tp_mm_{2020..2025}.nc` paths as literals (D8).

### T2 — the instantaneous transform, pure
**Scope:** `era5_instantaneous.py` — K->degC conversion with a double-conversion guard mirroring
`convert_units`' "already converted, or wrong input" refusal; an output schema validator for
`temperature`/`degC` reusing the generic axis/grid/attr checks; `instantaneous_identity` per D3/D4.
**Out:** `era5_deaccumulate.py`, any driver wiring, any CDS access.
**Verification:** new `tests/unit/scripts/test_era5_instantaneous.py`, **red-first**: an identity built
over a boundary window must fail, and a Kelvin file converted twice must raise.

### T3 — wire the driver and lift the exit-7 guard
**Scope:** `transform_year` gains its instantaneous branch; `acquire_era5.py:252-262` permits
`--stage transform` when `accumulation_of(variable) is INSTANTANEOUS` and still refuses anything
unregistered. `--stage all` for `total_precipitation` is unchanged. **Out:** new CLI flags beyond the
existing `--variable`.
**Verification:** `uv run pytest tests/unit/scripts/test_dhm_precip_era5_variable.py
tests/unit/scripts/test_acquire_era5_cli.py`; then the **real run** producing six
`era5_land_t2m_degc_{year}.nc`, and the gated real-data suite re-run to prove precipitation is untouched.

### T4 — narrow t2m point extraction
**Scope:** parameterise the data-variable name in `extract_nearest_series` (`era5_extract.py:263` is
the only read site on the primary path), and publish a per-station hourly °C series with a small
manifest recording the operator, the source product hashes, the coordinate-table hash, and the
precipitation bundle identity whose elevation table it references (D6). Publication itself reuses
`era5_extract_manifest`'s numbered-directory discipline (D5, corrected 2026-08-20): stage via
`prepare_staging_dir`, publish via `allocate_published_dir` to a fresh `<NNNN>-<identity>` directory
under `era5_land/points/`, discover via the highest `NNNN` whose manifest validates
(`discover_t2m_bundle`) — never a fixed path, never a `.points.prev` backup. **Out:** bilinear,
sensitivity CSV, orography stage, any change to the published precipitation bundle.
**Verification:** new unit tests; a real run over the 26 stations asserting 52,608 hourly stamps and
zero non-finite values; gated real-data suite green.

### T5 — docs
**Scope:** update the milestone doc's t2m prerequisite note (`dhm-precipitation-milestones.md:389`)
from "fetch `2m_temperature`" to the delivered product, its path and its identity; note in Plan 184
D14 that the cell-level series now exists and where.
**Verification:** — (docs).

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T4"], "parallel": false, "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["T5"], "parallel": false, "depends_on": ["phase-3"]}
  ]
}
```

## Non-goals

The 6.5 °C/km lapse correction and the Pyramid `AT` check (Plan 184 D14) · the sub-freezing mass
fraction itself (Plan 184 D4) · IMERG (M-A5b) · any variable beyond `2m_temperature` · any
generalisation of the accumulator path · re-running orography · changing the published precipitation
product or bundle.
