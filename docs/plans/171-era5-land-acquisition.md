---
status: DRAFT
created: 2026-08-13
plan: 171
title: M-A4 — ERA5-Land acquisition for the DHM precipitation comparison
scope: A committed, parameterised, resumable acquisition of hourly ERA5-Land total precipitation over a Nepal bounding box for 2020-2025 from the Copernicus CDS, deaccumulated and unit-converted, stored locally under data/dhm_precip/ with a provenance manifest. Explicitly NOT point extraction (M-A5), NOT the gauge comparison (M-A6), NOT IMERG acquisition (M-A5's plan, though the seam is designed for it).
depends_on: [170]
blocks: [M-A5, M-A6]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 171 — M-A4 ERA5-Land acquisition

## Status
**DRAFT.** Implements milestone **M-A4**. Unblocked: a bounding-box request needs no station
coordinates. Builds on Plan 170's `scripts/dhm_precip/` package.

## Problem

M-A6 — the point of Track A — compares DHM gauges against ERA5-Land. Nothing can start until the
ERA5-Land data is on disk in a form we trust. That is this plan, and it is mostly *not* an analysis
problem: it is a data-acquisition problem with two specific traps that silently corrupt everything
downstream if handled naively.

## Constraints

1. **No DHM or ERA5 data enters the public repository** (Plan 170 constraint 1). Everything lands in
   `data/dhm_precip/`, already gitignored (`.gitignore:21`).
2. **CDS access is credentialed and manual to set up.** An account plus a one-time dataset licence
   acceptance. Credentials come from the environment or `~/.cdsapirc`; **never committed, never
   logged**.
3. **CDS has changed its API and dataset identifiers before.** Confirm the current endpoint, client
   package and dataset name at implementation time rather than assuming.
4. **CDS requests are queued** and can take minutes to hours. The acquisition must be **resumable and
   idempotent**, not a single long call that loses everything on interruption.
5. **CI has no CDS credentials and must stay green.** All transformation logic is unit-tested on
   synthetic grids; the network call is the only untested I/O.
6. **Polars is the repo standard for tabular data**, but this is gridded — `xarray` + `cfgrib` /
   `h5netcdf`, already dependencies. Convert to Polars only at the point extraction (M-A5), not here.

## Design decisions

- **D1 — Extend `scripts/dhm_precip/`**, do not start a new package. Plan 170 established it; the
  acquisition module sits alongside the statistics modules and shares the parameter object pattern.
- **D2 — One frozen request-parameter object.** Bounding box, year range, variable, dataset id,
  product type, output format — all explicit fields with defaults matching this study
  (80–89 °E, 26–31 °N, 2020–2025, hourly total precipitation). No magic values inside call sites.
- **D3 — Chunk the request by year, one file per year.** Six requests of ~165 MB rather than one of
  ~1 GB. Gives resumability (D4), bounds the blast radius of a failed request, and keeps each request
  inside CDS's practical size limits.
- **D4 — Idempotent and resumable.** A year whose output file exists *and* matches its recorded
  sha256 is skipped. An interrupted run re-entered continues where it stopped. **Partial downloads are
  never treated as complete**: write to a temporary path and rename only on success.
- **D5 — Deaccumulation is the central correctness risk, and ERA5-Land differs from the existing
  precedent.** `_deaccumulate_precipitation` (`adapters/meteoswiss_nwp.py:157`) is a plain
  `pad().diff("valid_time")` — correct for a per-cycle accumulation, **wrong for ERA5-Land**, whose
  `total_precipitation` accumulates **from 00 UTC and resets each day**. A global diff produces a large
  **negative** spike at every day boundary and mishandles the 00 UTC step.
  The rule: **difference within each accumulation day, and treat the day's first step as itself.**
  Which UTC hour carries the daily total, and whether the 00 UTC stamp belongs to the previous day, is
  **verified against the downloaded data** in Task 2a, not assumed from documentation.
  **Post-conditions, asserted in code, not just tested:** no negative precipitation anywhere; the sum
  of deaccumulated hours within a day equals that day's accumulated total to floating-point tolerance.
- **D6 — Units: ERA5-Land `tp` is metres; convert to mm (×1000) once, at deaccumulation.** The repo
  already does m→mm for ERA5 via the recap adapter (`recap_gateway.py:75`) — same convention, so
  downstream units match what the rest of the system expects.
- **D7 — Period-ending stamps are recorded, not silently assumed.** ERA5-Land's convention (hour *t*
  covers *t−1 → t*) is *known*, unlike the gauge convention (M-D3). The manifest records it explicitly
  so M-A6's alignment is arithmetic once M-D3 answers — and so that, if M-D3 never answers, this is
  the fixed reference the ±1 h uncertainty is measured against.
- **D8 — A provenance manifest per acquisition.** Dataset identifier, full request parameters, client
  package version, per-year output sha256, download timestamps, the verified accumulation convention
  (D5), and the units conversion applied. Without it the archive is unreproducible and the study is
  uncitable.
- **D9 — The seam is product-agnostic, but only ERA5-Land is in scope.** The acquisition module takes
  a request spec and a post-processing hook, so IMERG (M-A5's plan) slots in without rework.
  **Building IMERG acquisition here is explicitly out of scope** — designed for, not delivered.
- **D10 — Task exit gate** (`docs/workflow.md:376`), referenced by every code task: the task's own
  test, `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check` (same paths),
  `uv run pyright src/`, `uv run pyright scripts/dhm_precip/`, `uv run pytest`, and affected docs
  updated in the same change.

## Out of scope

Point extraction at station locations (M-A5) · IMERG acquisition (M-A5's plan) · any gauge comparison
(M-A6) · bias correction of any kind · adding ERA5-Land to the operational forcing path.

## Phases and tasks

Every code task carries the D10 gate in addition to the test named below.

### Phase 1 — foundation

**1a — dependency and request spec.** Add the CDS client dependency (confirm the current package per
constraint 3). Implement the frozen request-parameter object (D2) and the per-year request builder.
Credentials read from environment/`~/.cdsapirc`, never from parameters and never logged.
*Verification:* `uv run pytest tests/unit/scripts/test_era5_request.py`

**1b — storage layout and manifest types.** Output paths under `data/dhm_precip/era5_land/`, the
provenance manifest type (D8), and sha256 helpers. Temporary-path-then-rename semantics (D4).
*Verification:* `uv run pytest tests/unit/scripts/test_era5_manifest.py`

### Phase 2 — transformation (the correctness core)

**2a — deaccumulation and units.** *(depends on 1b)* Implement D5 and D6 as **pure functions over an
`xarray.Dataset`**, with the asserted post-conditions. Includes a small diagnostic that reports the
observed accumulation convention from a downloaded year, so D5's rule is verified against data rather
than assumed.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_deaccumulate.py` — new cases
that **fail against a naive global `diff`**, specifically: a synthetic two-day grid where a global diff
yields a negative value at the day boundary, and one asserting within-day sums equal the daily total.

**2b — acquisition driver.** *(depends on 1a, 1b, 2a)* Orchestrates: per-year request → download to
temporary path → deaccumulate + convert → write → manifest update. Resumable and idempotent (D4).
The CDS call itself is injected so the driver is testable with a fake client.
*Verification:* `uv run pytest tests/unit/scripts/test_era5_driver.py` — with a fake client, covering
resume-skips-completed-year, interrupted-run-leaves-no-partial-file, and checksum-mismatch-refetches.

### Phase 3 — runner and acquisition

**3a — CLI runner.** `scripts/dhm_precip/acquire_era5.py` following the `scripts/` idiom
(shebang, `# ruff: noqa: T201`, a docstring stating *why*, `Usage:`/`Environment:` sections, explicit
exit codes). Exit codes: `0` success · `2` credentials absent/invalid · `3` request rejected by CDS ·
`4` transformation post-condition failed.
*Verification:* `uv run python scripts/dhm_precip/acquire_era5.py --help` exits 0; driver tests cover
the paths.

**3b — perform the acquisition.** Run it for real. Accept the CDS licence, download 2020–2025, verify
the manifest, and spot-check a known event against the gauge record — the 2021-10-19 eastern-Nepal
extreme is the natural candidate, since two gauges independently recorded ~400 mm that day.
*Verification:* six yearly files present with matching checksums; manifest complete; post-conditions
hold on the real data; the spot-check shows precipitation in the right place at roughly the right time.

## Exit gate for M-A4

Deaccumulated, mm-unit, hourly ERA5-Land precipitation for 2020–2025 over the study box on local disk,
**regenerable from the committed request script**, with a complete provenance manifest recording the
dataset identifier, request parameters, verified accumulation convention and per-file checksums.

## Risks

- **The CDS API may differ from expectation** (constraint 3). Task 1a's first act is to confirm the
  current client and dataset identifier; treat any documentation in this plan as indicative.
- **Queue latency is outside our control.** Task 3b may take hours to days. It is not a failure state,
  and the resumable design (D4) exists so that waiting costs nothing.
- **Deaccumulation is the silent killer.** A wrong rule produces plausible-looking data with negative
  values suppressed or daily totals subtly wrong — which would propagate into M-A6 and be attributed
  to ERA5. Hence post-conditions asserted in code (D5), red-first tests (2a), and verification of the
  convention against real data rather than documentation.
- **ERA5 vs ERA5-Land are different datasets** with different resolutions and land masks. The manifest
  records the exact identifier so a mix-up is detectable after the fact.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-2a", "tasks": ["2a"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-2b", "tasks": ["2b"], "parallel": false, "depends_on": ["phase-2a"]},
    {"id": "phase-3a", "tasks": ["3a"], "parallel": false, "depends_on": ["phase-2b"]},
    {"id": "phase-3b", "tasks": ["3b"], "parallel": false, "depends_on": ["phase-3a"]}
  ]
}
```
