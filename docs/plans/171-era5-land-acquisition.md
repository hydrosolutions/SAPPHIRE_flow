---
status: READY
created: 2026-08-13
revised: 2026-08-13
plan: 171
title: M-A4 — ERA5-Land acquisition for the DHM precipitation comparison
scope: A committed, parameterised, resumable acquisition of hourly ERA5-Land total precipitation over a Nepal bounding box for 2020-2025 from the Copernicus CDS — raw accumulations retained and checksummed, then deaccumulated and unit-converted by a separately re-runnable local transform — stored under data/dhm_precip/ with a provenance manifest. Explicitly NOT point extraction (M-A5), NOT the gauge comparison (M-A6), NOT IMERG acquisition (M-A5's plan).
depends_on: [170]
blocks: [M-A5, M-A6]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 171 — M-A4 ERA5-Land acquisition

## Status
**READY — owner-confirmed 2026-08-13.** Arrived here after a `/plan` escalation (stalled at 2 rounds,
3 blockers + 10 majors) followed by a targeted repair and **six manual Codex rounds** converging
3B+10M → 4M → 2M → 2M → 1M → clean. The loop earned its keep: it corrected this plan's original
(wrong) statement of the ERA5-Land accumulation convention and added the packing-error policy.

Implements milestone **M-A4** (`docs/design/dhm-precipitation-milestones.md:177`).
Unblocked: a bounding-box request needs no station coordinates. Builds on Plan 170's
`scripts/dhm_precip/` package.

## Problem

M-A6 — the point of Track A — compares DHM gauges against ERA5-Land. Nothing can start until the
ERA5-Land data is on disk in a form we trust. That is this plan, and it is mostly *not* an analysis
problem: it is a data-acquisition problem whose accumulation semantics silently corrupt everything
downstream if handled naively.

## Constraints

1. **No DHM or ERA5 data enters the public repository** (Plan 170 constraint 1). Everything lands in
   `data/dhm_precip/`, already gitignored (`.gitignore:21`).
2. **CDS access is credentialed and manual to set up.** An account plus a one-time dataset licence
   acceptance — a **legal act by the account owner**, not delegatable (see *Human prerequisites*).
   Credentials come from the environment or `~/.cdsapirc`; **never committed, never logged** — and
   that "never logged" is *tested*, not asserted (task 4a).
3. **CDS has changed its API and dataset identifiers before.** Confirm the current endpoint, client
   package, dataset name and **exact request payload** at implementation time rather than assuming.
   Every CDS-shaped statement in this plan is indicative and is superseded by what task 1a observes.
4. **CDS requests are queued** and can take minutes to hours. Acquisition must be **resumable and
   idempotent**, and — because it is slow and precious while the transform is fast and iterative —
   **acquisition and transformation are separate, separately-checkpointed stages** (D3).
5. **CI has no CDS credentials and must stay green.** All transformation logic is unit-tested on
   synthetic grids; the CDS call is the only untested I/O. The two real-data steps (2b, 4b) are
   **operator-gated** and never run in CI.
6. **Polars is the repo standard for tabular data**, but this is gridded — `xarray` + `cfgrib` /
   `h5netcdf`, already dependencies. Convert to Polars only at the point extraction (M-A5), not here.

## Human prerequisites (operator gate, not delegatable tasks)

`docs/workflow.md:17` defines a task as *a unit of work delegatable to a single subagent*. The
following are neither delegatable nor code, and are therefore listed here rather than as tasks. They
gate tasks 2b and 4b.

- **P0 — CDS account, licence acceptance, credential provisioning.** The account owner registers,
  **accepts the ERA5-Land dataset licence in the CDS web UI** (a legal acceptance that must be
  performed by the human account holder), and places credentials in the environment or `~/.cdsapirc`
  on the operator machine. Records the CDS portal URL, dataset landing page, licence name/version and
  acceptance date, which the implementer folds into the manifest's provenance block (D8).
- **P1 — the operator runs 2b and 4b.** These invoke the real CDS API from a credentialed machine.
  A subagent produces the code and the checks; a human runs them and reports the output.

## Design decisions

- **D1 — Extend `scripts/dhm_precip/`**, do not start a new package. Plan 170 established it
  (`scripts/dhm_precip/params.py:31` is the parameter-object precedent); the acquisition modules sit
  alongside the statistics modules and share the frozen-parameter-object pattern.

- **D2 — One frozen request-parameter object, whose field set is frozen by observation, not by this
  plan.** Fields: dataset identifier, variable, year, bounding box, and the **two distinct format
  fields the current CDS API separates** — `data_format` (the payload encoding, e.g. netcdf/grib) and
  `download_format` (archived vs unarchived delivery; an unarchived single file must be requested
  explicitly). **The final on-disk archive format is a third, independent choice** made by our
  transform stage, not by CDS.
  - `product_type` is **not** sent unless task 1a observes it in the live download form. Current
    official `reanalysis-era5-land` examples omit it. Task 1a's deliverable is the **exact payload
    dictionary emitted by the current CDS download form**, copied verbatim, and a test asserting our
    builder reproduces it.
  - **CDS `area` is ordered north/west/south/east** — encoded once, in the builder, with a test on the
    literal ordering. The study box is 26–31 °N, 80–89 °E → `[31, 80, 26, 89]`.
  - Defaults match this study: 2020–2025, hourly `total_precipitation`.

  **A bare `year` field is not quite the unit we need** (task 2b wants a single October), and CDS
  exposes *independent* year/month/day/time lists, so a careless multi-period selection
  **over-selects the Cartesian product** rather than being rejected. Therefore:
  - **`AcquisitionWindow`** — an explicit, validated set of `(year, month, day, hour)` stamps
    describing exactly the valid times wanted. It is the unit of request, identity, artifact and
    manifest accounting.
  - **One window = one CDS payload = one raw artifact.** No bundles, no conditional cardinality.
    A calendar year is expressible in a single payload, and task 2b's October is a smaller window of
    the same shape.
  - **Acquisition windows and product years are different sets.** Every window yields a raw artifact;
    only a **product year** yields a final file. The two edge-context windows (D4) and 2b's sample
    window are acquisition-only — they are never transformed on their own, so they never fail D6 for
    lack of context they were never meant to have.
  - **A window must be exactly expressible as CDS's independent `year`/`month`/`day`/`time` lists** —
    i.e. a clean Cartesian product. Whole years, whole months, and single days or hours qualify; a
    span like "30 Sep through 1 Nov" does **not**, because no combination of those lists selects it
    without also selecting stamps outside it. Windows are therefore restricted to whole calendar
    units, and the builder **rejects** a window it cannot express exactly rather than silently
    over-selecting.
  - **Test obligation:** the set of valid-time stamps implied by a payload equals the window exactly
    — no missing stamps and **no Cartesian spill** — plus a case asserting that a non-expressible span
    is rejected.
  - The CLI exposes a `--window` argument, so 2b's October sample uses the same path as a full year.

- **D3 — Two stages, two checkpoints: acquire (slow, remote) then transform (fast, local).**
  The raw per-year CDS download is **persisted and checksummed on disk and retained**
  (`data/dhm_precip/era5_land/raw/`); the transform reads local raw files and writes the final
  mm/hourly product (`data/dhm_precip/era5_land/hourly_mm/`). Rationale is this plan's own risk
  profile: deaccumulation is the central correctness risk (D5, Risks) and may need correcting after
  the fact; fusing download and transform would force a full six-year re-download from a queued API
  (constraint 4) to fix a local arithmetic bug. Each stage is independently idempotent and resumable.
  **Trade-off, accepted:** ~1 GB of raw is kept alongside ~1 GB of product. Both are gitignored and
  regenerable; disk is cheaper than CDS queue time.

- **D4 — One window per calendar year; boundary context comes from adjacent windows on disk.**
  Six yearly windows of ~165 MB rather than one ~1 GB request. **The boundary context D6 needs is not
  stuffed into the request** — an earlier shape did that and forced a conditional one-or-many payload
  design through the whole plan. Instead, the transform for year *Y* reads *Y*'s raw file **plus its
  neighbours' raw files**, which for 2021–2024 are already on disk because we are downloading the
  whole range anyway. Only the two edges need anything extra: a small **Dec 2019** window and a
  **1 Jan 2026 00 UTC** window — each still one window, one payload, one artifact.
  Net effect: **eight windows, eight payloads, eight raw artifacts**, and no cardinality branch
  anywhere downstream.

- **D5 — Idempotent and resumable, keyed on **two separate identities** — one per stage.**
  A single identity spanning both stages would make a transform-only change invalidate the *raw*
  checkpoint and force a six-year re-download from a queued API, destroying D3's whole purpose.
  So:
  - **`raw_request_identity` = sha256(canonical-JSON of `{dataset id, the exact literal request
    payload sent}`)**. Nothing about the transform enters it. Client package version is recorded as
    *provenance* but is **not** part of the identity unless 1a observes that it changes response
    semantics.
  - **`transform_identity` = sha256(canonical-JSON of `{raw artifact sha256s consumed, transform
    parameter snapshot (accumulation rule id, packing tolerance, units factor), output_schema_version,
    final format/dtype/encoding}`)**.

  A stage output is skipped only when **all** of: (i) the output file exists, (ii) its recorded sha256
  matches the file on disk, (iii) a manifest entry exists for that window and stage, and (iv) the
  entry's identity **for that stage** equals the identity derived from the current spec. Any mismatch
  → that stage re-runs; never a silent reuse of valid-but-wrong data. Changing the bounding box forces
  a refetch; changing the packing tolerance or the accumulation rule forces a **re-transform only**. **Partial writes are never treated as complete**, and the ordering matters:
  **write temporary → close → reopen and fully validate the temporary → checksum the temporary →
  `os.replace` → update the manifest.** Validating *after* replacement would leave an invalid artifact
  occupying the final path. A failed re-transform over an existing good file must leave the previous
  file **and** its matching manifest entry untouched — tested for both a fresh failure and a failed
  revision over valid output.

- **D6 — ERA5-Land accumulation semantics, stated exactly (the central correctness risk).**
  `_deaccumulate_precipitation` (`src/sapphire_flow/adapters/meteoswiss_nwp.py:157`) is a plain
  `pad().diff("valid_time")` — correct for a per-cycle accumulation, **wrong for ERA5-Land**.
  ERA5-Land accumulations come from forecasts started at 00 UTC, so the accumulator runs from
  **01 UTC through the next day's 00 UTC** and then resets:
  - `01 UTC` of day *D* is step 1 — the hourly value **is** the accumulator itself.
  - `02–23 UTC` of day *D*: hourly value = `A(t) − A(t−1h)`.
  - `00 UTC` of day *D+1* is step 24 — it carries **day D's 24-hour total**; its hourly value is
    `A(00) − A(23 of D)`. It belongs to accumulation day *D*, **not** to calendar day *D+1*.

  Define the **accumulation day** as `01 UTC of D … 00 UTC of D+1`. The rule: *difference against the
  immediately preceding valid time **within the same accumulation day**; the accumulation day's first
  step (01 UTC) is taken as itself.* Grouping by calendar day is wrong at exactly the `23 → 00 → 01`
  seam, and a naive global diff produces a large negative spike at every `00 → 01` reset.

  **Boundary context.** A calendar-year file cannot deaccumulate its opening `00 UTC` stamp without the
  previous year's `23 UTC` accumulator, and cannot close its final accumulation day (31 Dec) without
  the next year's `00 UTC` stamp. Hence D4's leading full 31 December and trailing 00 UTC 1 January.
  Context stamps are **trimmed from the output** after transformation; the output for year *Y* is
  exactly the period-ending stamps `00:00 1 Jan Y … 23:00 31 Dec Y`. If required boundary context is unavailable, the transform **fails with a typed error
  (exit code 4)** — it does not emit a partial year. Dropping the stamp instead would contradict D9's
  requirement of complete hourly coverage and produce a file that silently fails its own schema.
  2019-12-31 and 2026-01-01 both exist, so this path is not exercised by the 2020–2025 window; it is
  coded and unit-tested as a failure, not as a fallback.

  **Post-conditions, asserted in code, not just tested:**
  1. **Conservation, asserted on the PRE-clamp increments:** for every accumulation day fully
     contained in the file, the sum of the *unclamped* hourly increments over `01 … next-day 00`
     equals that day's `00 UTC` accumulator, to floating-point tolerance, checked against the
     *original accumulator field*. Clamping (D7) necessarily breaks exact telescoping, so asserting
     conservation after it would be asserting a falsehood.
  1b. **Post-clamp accounting, per accumulation day and per cell, in mm:** the published sum equals
     `1000 x original_terminal_accumulator_m + mass_adjustment_mm(day, cell)`. **Both sides are mm** —
     the accumulator is metres (D8), so the factor is explicit here rather than implied — and the
     adjustment is accounted **per accumulation day and cell**, then *aggregated* to the per-year
     figure the manifest reports (D7). Both equations carry explicit tolerances and both are tested.
  2. **Non-negativity, after the packing policy of D7** — not before it.
  3. **Accumulator monotone within an accumulation day, to within D7's packing tolerance** — a
     decrease **beyond** that tolerance means the assumed reset point is wrong, and is the
     diagnostic's primary signal in task 3a. Stated with the tolerance because D7 explicitly permits
     sub-tolerance negative increments; an unconditional monotonicity assert would contradict it.

  The rule above is **encoded from documentation in task 3a and empirically confirmed against a real
  sample in the same task** — the sample is acquired by operator step 2b, which precedes 3a in the
  phase graph precisely so this confirmation is executable rather than aspirational. If the observed
  convention contradicts the rule, 3a corrects the rule **before any full-year download is
  transformed**.

- **D7 — Packing-error tolerance, explicit and accounted.** Consecutive GRIB fields carry different
  packing errors, so differencing two accumulators legitimately yields tiny negatives. A blanket "no
  negative anywhere" assertion would reject correct data. Policy, in this order:
  1. Values `< -tolerance` → **post-condition failure** (exit code 4). Material negatives mean the
     rule is wrong, and must never be silently zeroed.
  2. Values in `[-tolerance, 0)` → **set to zero**, and counted.
  3. The manifest records, per year: `packing_corrected_cells`, `max_correction_mm`, and the
     `mass_adjustment_mm` (total added by clamping), so the correction is auditable and reviewable.
  `tolerance` is a field on the transform parameter object with a default of `1e-4` mm
  (≈`1e-7` m, the scale of ERA5-Land short-packing residue); its adequacy is a reported number in the
  real-data gate (4b), and the default is revised there if the observed residue is larger.
  Non-negativity (D6 post-condition 2) is asserted **after** steps 1–3.

- **D8 — Units: ERA5-Land `tp` is metres; convert to mm (×1000) once, at the transform stage.**
  Precedent: `_metres_to_mm` (`src/sapphire_flow/adapters/recap_gateway.py:45`), wired for ERA5
  precipitation at `src/sapphire_flow/adapters/recap_gateway.py:81` — same convention, so downstream
  units match the rest of the system. **The conversion is guarded, not blind:** the source variable's
  `units` attribute is asserted to be metres before scaling; the input must be the raw accumulator
  variable (`tp`) and the output is written under a different name (D9), so a second pass over an
  already-converted file fails loudly rather than multiplying by 1000 twice.

- **D9 — The final file has a declared schema, validated by reopening it.** Multiplying numbers does
  not produce a correct product. The transform's output contract, asserted on reopen after write and
  covered by both synthetic and real-data checks:
  - **format: NetCDF4 written via `h5netcdf`** (already a dependency), one file per **product year**,
    `era5_land_tp_mm_<window-id>.nc`; `float32`; zlib compression level 4; chunked by
    `(valid_time=24, latitude=-1, longitude=-1)`; fill value `NaN`; `valid_time` CF-encoded as
    `hours since 1970-01-01 00:00:00` UTC. These are part of `transform_identity` (D5), so changing
    any of them invalidates the product but not the raw download;
  - data variable `precipitation` (matching the canonical name used at
    `src/sapphire_flow/adapters/recap_gateway.py:81` and by the MeteoSwiss adapter), with
    `units = "mm"`;
  - coordinates `valid_time` (UTC, tz-aware or explicitly UTC-encoded), `latitude`, `longitude`;
  - one final file **per product year** (not per acquisition window — D2); `valid_time` strictly
    increasing, unique, exactly hourly, and covering exactly that calendar year at period-ending
    stamps (`00:00 1 Jan … 23:00 31 Dec`), leap days included;
  - latitude/longitude within the requested box, spacing 0.1° to tolerance, count matching the box;
  - attrs recording the period-ending convention (D10), the accumulation rule, `transform_version`,
    `output_schema_version` and the source dataset identifier;
  - missing-value policy: values are finite or NaN over sea/non-land mask cells; **the count of
    non-finite cells is recorded in the manifest** and a fully-NaN field is a failure.

- **D10 — Period-ending stamps are recorded, not silently assumed.** ERA5-Land's convention (hour *t*
  covers *t−1 → t*) is *known*, unlike the gauge convention (M-D3). The manifest and the file attrs
  record it explicitly so M-A6's alignment is arithmetic once M-D3 answers — and so that, if M-D3
  never answers, this is the fixed reference the ±1 h uncertainty is measured against.

- **D11 — A provenance manifest per acquisition, written atomically after every completed stage.**
  Contents: dataset identifier; the full literal request payload sent; client package version; P0's
  licence name/version and acceptance date; **per-window** raw sha256 and **per-product-year** final sha256; the
  **both identities — `raw_request_identity` and `transform_identity` (D5)**; download and transform
  timestamps from an **injected UTC clock**
  (`AGENTS.md:498` — no `datetime.now()` in the driver); the confirmed accumulation convention (D6);
  the units conversion applied (D8); the packing-correction accounting (D7); the non-finite cell
  counts and any dropped boundary stamp (D6, D9).
  **Atomicity:** the manifest is serialised to an adjacent temporary file on the same filesystem and
  `os.replace`d onto the final path **after each completed year and stage**, so an interruption mid-
  write leaves the previous manifest intact and resume still works. Note that Plan 170's existing
  writer (`scripts/dhm_precip/manifest_io.py:73`) uses a plain `path.write_text` and is **not**
  atomic; this plan adds its own atomic writer for the acquisition manifest and **does not** change
  170's writer (out of scope — flagged here so the inconsistency is deliberate and visible).
  The resume logic must also handle *file replaced, manifest not yet updated*: a final file with no
  matching manifest entry is **re-verified and re-transformed**, never assumed good.

- **D12 — ERA5-Land + CDS only. No speculative generalisation.** This plan does **not** claim a
  product-agnostic seam for IMERG: IMERG comes from NASA GES DISC under Earthdata auth, with a
  different client, request model, granule format and accumulation convention, and nothing here
  validates that a CDS-shaped request object would fit it.
  What is honestly reusable is the **transform stage** — pure functions over an `xarray.Dataset`, with
  no provider knowledge. The **client/auth/request layer (1a, 2a) is CDS-specific by construction.**
  The only structure this plan builds for testability is a single injected CDS-call seam, which the
  fake-client tests need regardless. M-A5 decides, once IMERG's real API is known, whether to
  refactor this module or write a sibling — a comparison that cannot be made honestly today.

- **D13 — Dev-only dependency.** `uv add --dev cdsapi` (or whatever 1a observes to be current).
  Loose `scripts/` tooling is dev-only and out of the image (`docs/plans/122-package-operational-scripts.md:59`),
  and the production build installs with `uv sync --frozen --no-dev` (`Dockerfile:32`) — a research
  network client must not enter production dependencies.

- **D15 — Operator provenance has a real input path.** P0 produces operator-specific metadata (CDS
  portal URL, dataset landing page, licence name/version, acceptance date) that D11 requires in the
  manifest and 4b gates on — but no task accepted it as input. It is supplied as a **gitignored
  operator-provenance file** under `data/dhm_precip/` (never committed; contains no secrets), passed
  via `--provenance`. **A missing or incomplete provenance file blocks manifest completion** with a
  typed error, and that is tested; it never silently produces a manifest with empty licence fields.

- **D14 — Task exit gate** (`docs/workflow.md:376`), referenced by every code task: the task's own
  test, `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check` (same paths),
  `uv run pyright src/`, `uv run pyright scripts/dhm_precip/`, `uv run pytest`, and affected docs
  updated in the same change.

## Out of scope

Point extraction at station locations (M-A5) · IMERG acquisition (M-A5's plan) · **any gauge
comparison, including event-level spot-checks (M-A6)** · bias correction of any kind · adding
ERA5-Land to the operational forcing path · changing Plan 170's non-atomic manifest writer.

## Phases and tasks

Every code task carries the D14 gate in addition to the test named below.

### Phase 1 — foundation

**1a — dependency and request spec.** Add the CDS client as a **dev** dependency (D13), confirming the
current package and dataset identifier per constraint 3. **First act: open the live CDS download form
for `reanalysis-era5-land` and capture the exact payload dictionary it emits**; the frozen request
object (D2) is modelled on that payload — `data_format` and `download_format` as separate fields,
`product_type` only if present, `area` in north/west/south/east order. Implement the
**`AcquisitionWindow` → payload** builder (D2/D4): one window, one payload. Credentials are read from environment/`~/.cdsapirc`,
never passed as parameters, never logged.
*Verification:* `uv run pytest tests/unit/scripts/test_era5_request.py` — asserts the **literal
payload** for a known window (field set, `area` ordering, date/time lists); that the **set of
valid-time stamps implied by the payload equals the window exactly — no missing stamps and no
Cartesian spill**; that an October-only window (2b) round-trips; and that credentials never appear in
the payload or its repr.

**1b — storage layout, identities and atomic manifest.** Paths under `data/dhm_precip/era5_land/raw/`
and `.../hourly_mm/`; the provenance manifest type (D11); sha256 helpers; **both identity functions —
`raw_request_identity` and `transform_identity` (D5)**; the atomic `tmp → os.replace` writer used for
both data files and the manifest.
*Verification:* `uv run pytest tests/unit/scripts/test_era5_manifest.py` — identity changes when the
bounding box changes, when the dataset id changes, and when `transform_version` is bumped; identity is
stable under key ordering; a simulated crash during manifest serialisation leaves the previous
manifest readable and parseable.

### Phase 2 — acquisition stage (raw bytes only)

**2a — raw acquisition driver.** *(depends on 1a, 1b)* Per window: build payload → CDS call (injected,
so tests use a fake client) → download to an adjacent temporary path → **reopen and validate the
artifact before publishing it** → checksum → `os.replace` into `raw/` → atomic manifest update.
**No transformation whatsoever.**

**Validation is not optional, and a checksum is not validation.** A stable sha256 is equally happy to
certify an HTML error page, a truncated archive, the wrong container type or the wrong variable — and
once checkpointed, resume would skip that artifact forever while the transform failed against it every
time. So before `os.replace`: reopen the temporary file with the selected engine and assert container
type, the expected variable, its `units` attribute, spatial coordinates within the requested box, and
**exactly** the requested temporal coverage. Precedent for rejecting unparsable payloads and missing
variables: `src/sapphire_flow/adapters/meteoswiss_open_data_reanalysis.py:911`.

**Retry contract** (owner: this driver): retryable = transport errors, HTTP 5xx and documented CDS
queue-transient states; **not** retryable = auth failure, licence-not-accepted, malformed request,
and any validation failure above. Bounded attempts with exponential backoff, both parameters on the
request-parameter object; the sleep is **injected** so tests are instant. Exhausting retries exits 3. Resumable and idempotent on raw
bytes alone (D5). An injected UTC clock supplies download timestamps (D11).
*Verification:* `uv run pytest tests/unit/scripts/test_era5_acquire.py` — with a fake client:
resume-skips-a-completed-year; interrupted download leaves **no** file at the final path; checksum
mismatch triggers refetch; a changed bounding box (identity mismatch) triggers refetch rather than
reuse; a raw file present with no manifest entry is refetched; timestamps come from the injected
clock (frozen, tz-aware UTC).

**2b — acquire one real sample.** *(operator step; depends on 2a and P0.)* **Not a subagent task** —
a human with credentials runs 2a for a single short window. **Proposal: the whole of October 2021** —
a clean Cartesian selection (`month=[10]`, all days, all hours) as D2 requires, small and fast, and
inside the wettest season so accumulations are non-trivial. It is **acquisition-only**: the diagnostic
reads the accumulation days fully contained in the month — under D6 those are days **1–30 Oct**
(day *D* running `01 UTC D … 00 UTC D+1`, so 30 Oct closes at 00 UTC on the 31st; 31 Oct would need
00 UTC 1 Nov and is therefore not contained) — which is far more than it needs to observe the
convention, so it never depends on a neighbouring window and is never transformed into
a product. Purpose: give task 3a's convention diagnostic **real
data to run against**, before any six-year download is committed to.
*Verification:* one raw file on disk, its sha256 recorded in the manifest, its response schema
(variables, dims, coordinate names, units attribute) reported by a read-only inspection.

### Phase 3 — transformation (the correctness core)

**3a — deaccumulation, units and the convention diagnostic.** *(depends on 1b, 2b)* Implement D6, D7,
D8 and D9 as **pure functions over an `xarray.Dataset`**, with the post-conditions asserted in code.
Includes the diagnostic that reports the *observed* accumulation convention (reset point, which stamp
carries the daily total, monotonicity within the accumulation day) from the **real sample acquired in
2b**; if the observation contradicts D6, the rule is corrected here and the correction is recorded in
the plan and the manifest.

*Red-first, with demonstrated teeth.* The bare label "red-first" is unverifiable when the module under
test does not exist — a missing-module collection error is not evidence that a test detects the bug it
claims to detect. Required sequence, in this order:
  1. Write `tests/unit/scripts/test_era5_deaccumulate.py` containing a **local, deliberately naive
     candidate** — a scratch `pad().diff("valid_time")` mirroring
     `src/sapphire_flow/adapters/meteoswiss_nwp.py:157`, defined **inside the test module** (the
     production adapter is not imported; it is correct for its own dataset and must not be coupled to
     this research script).
  2. Run the synthetic fixtures through that naive candidate and assert it **violates** the D6
     post-conditions — a negative value at the `00 → 01` reset and a within-accumulation-day sum that
     disagrees with the `00 UTC` accumulator. This assertion **passes** on the current repo, proving
     the fixtures have teeth.
  3. Point the contract tests at the naive candidate and show them **fail on a real assertion** (not
     an import error). Only then implement the D6 rule and repoint them at it.
  4. Keep the naive-candidate test as a permanent guard against a regression to global-diff.

*Verification:* `uv run pytest tests/unit/scripts/test_era5_deaccumulate.py`, covering: the
`23 → 00 → 01` seam explicitly; a calendar-day grouping (which must **fail** the conservation
assertion, distinguishing it from accumulation-day grouping); a **year boundary** (`31 Dec 23:00 →
1 Jan 00:00 → 01:00`); a **leap day** (29 Feb 2020/2024); a **missing-boundary-context** case, which must raise the
typed transform failure (D6) rather than emit a partial year; conservation of each complete accumulation day against
the original `00 UTC` accumulator; a **tolerated** packing negative (clamped to zero, counted,
mass adjustment recorded) and a **material** negative (post-condition failure); a source whose `units`
attribute is not metres (rejected before scaling); an already-converted input (rejected, not scaled
twice); and the D9 output schema on the produced dataset.

**3b — transform driver.** *(depends on 2a, 3a)* Reads local raw files, applies 3a, trims boundary
context, writes the final product atomically, updates the manifest atomically, and **reopens each
written file to validate the D9 schema** before recording it as complete. Re-runnable against local
raw files without touching CDS; skips only on full identity + checksum + manifest agreement (D5).
*Verification:* `uv run pytest tests/unit/scripts/test_era5_transform.py` — resume-skips-a-completed-
year; a bumped `transform_version` re-transforms rather than skipping; a crash between file replace
and manifest update leads the next run to re-verify and re-transform, not to skip; the reopened file
satisfies the D9 schema; a post-condition failure leaves **no** final file in place.

### Phase 4 — runner and acquisition

**4a — CLI runner.** *(depends on 2a, 3b)* `scripts/dhm_precip/acquire_era5.py` with a docstring
stating *why*, `Usage:`/`Environment:` sections, explicit exit codes, `--stage acquire|transform|all`
(D3), a `--window` argument (D2), and a `--provenance` path (D15).
**Logging follows the repo standard, not the loose-script habit**: call `configure_cli_logging()` and
emit through `structlog` — `docs/standards/logging.md:166` bans `print()` without exception and
`:75` requires CLI configuration; the shipped precedent is
`src/sapphire_flow/cli/access_tokens.py:237`. **No `# ruff: noqa: T201` suppression.**
Exit codes: `0` success · `2` credentials absent/invalid · `3` request rejected by CDS or transient
failure after retries · `4` transformation post-condition failed · `5` storage/manifest write failed.
*Verification:* `uv run pytest tests/unit/scripts/test_acquire_era5_cli.py` — **typed fake failures**
injected at the seam produce each of exit codes 0/2/3/4/5; and a **credential-redaction test** that
sets a sentinel secret in a temporary environment/`.cdsapirc` and asserts the sentinel appears in
**none** of stdout, stderr, the captured structlog output, any raised exception's string form, or the
written manifest. **The redaction test must construct the real client offline** (and additionally
exercise a hostile fake that raises secret-bearing errors) — a fake-only test proves nothing about the
component most likely to leak, since it is the real client's config and error paths that carry the
credential. Third-party failures are mapped to generic typed messages rather than propagated verbatim.
`uv run python scripts/dhm_precip/acquire_era5.py --help` exits 0 (a smoke check, not the exit-code
contract).

**4b — perform the acquisition.** *(operator step; depends on 4a and P0.)* **Not a subagent task** —
a human runs `--stage all` for 2020–2025 and reports the results. Verification is deliberately
**product-internal**: no gauge data is loaded, and no station-level comparison is performed.
*Verification:*
  - **eight raw artifacts** (six years plus the two edge-context windows, D4) and **six final
    files**; all checksums matching the manifest; manifest complete, including licence provenance,
    every request payload and client version;
  - the response schema observed matches what 1a froze;
  - **boundary semantics on real data:** the `23 → 00 → 01` seam and each year boundary reproduce the
    D6 rule; the conservation post-condition holds for every complete accumulation day in all six
    years;
  - **packing accounting reported:** corrected-cell counts, max correction and mass adjustment per
    year — used to confirm or revise D7's default tolerance;
  - **magnitude summaries, product-internal:** annual and monsoon-season basin-box totals, the maximum
    hourly and daily grid-cell value, and the spatial pattern of the annual mean — each judged against
    ERA5-Land's own published climatology, not against gauges;
  - the D9 schema validates on reopening every file.
*Explicitly deferred to M-A6:* comparison of any ERA5-Land value against any DHM gauge record,
including the 2021-10-19 eastern-Nepal event. An earlier draft made that spot-check part of this exit
gate; it is removed for three reasons — it is M-A6's subject matter (out of scope, above), it needs
M-A2's time-axis normalisation and M-A5's extraction to be meaningful, and the ~438 mm / ~403 mm daily
figures it would have been checked against are `axis_status = "RAW_PROVISIONAL"`
(`scripts/dhm_precip/expectations.toml:475`, `:491`) — provisional numbers cannot serve as a pass/fail
criterion.

## Exit gate for M-A4

Deaccumulated, mm-unit, hourly ERA5-Land precipitation for 2020–2025 over the study box on local disk,
conforming to the D9 schema on reopen, **regenerable from the committed request script** — and, more
cheaply, **re-transformable from the retained raw files without re-contacting CDS** — with a complete,
atomically-written provenance manifest recording the dataset identifier, the literal request payloads,
the confirmed accumulation convention, the units conversion, the packing-correction accounting, and
per-file raw and final checksums.

## Risks

- **The CDS API may differ from expectation** (constraint 3). Task 1a's first act is to capture the
  live download form's payload; treat every CDS detail in this plan as indicative and superseded by
  that observation — including the field set in D2 and the request count in D4.
- **Queue latency is outside our control.** Task 4b may take hours to days. It is not a failure state,
  and the resumable design (D5) exists so that waiting costs nothing.
- **Deaccumulation is the silent killer.** A wrong rule produces plausible-looking data with negative
  values suppressed or daily totals subtly wrong — which would propagate into M-A6 and be attributed
  to ERA5. Mitigations, in order of strength: the exact `01…00` accumulation-day rule with boundary
  context (D6); post-conditions asserted in code including conservation against the original
  accumulator; the real-sample diagnostic in 3a *before* the full download; red-first tests with a
  demonstrated naive-candidate failure (3a); and the D3 split, so a late correction costs a local
  re-transform rather than six CDS downloads.
- **Packing artefacts vs real errors.** D7's tolerance is a judgement call; too loose and a real bug
  hides inside it. Mitigated by *reporting* the correction accounting rather than swallowing it, and
  by revising the default from the observed residue in 4b.
- **ERA5 vs ERA5-Land are different datasets** with different resolutions and land masks. The manifest
  records the exact identifier and `raw_request_identity`, so a mix-up is detectable after the fact
  and forces a refetch rather than a silent reuse.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-2a", "tasks": ["2a"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-2b", "tasks": ["2b"], "parallel": false, "depends_on": ["phase-2a"], "operator_step": true, "requires": ["P0"]},
    {"id": "phase-3a", "tasks": ["3a"], "parallel": false, "depends_on": ["phase-2b"]},
    {"id": "phase-3b", "tasks": ["3b"], "parallel": false, "depends_on": ["phase-3a"]},
    {"id": "phase-4a", "tasks": ["4a"], "parallel": false, "depends_on": ["phase-3b"]},
    {"id": "phase-4b", "tasks": ["4b"], "parallel": false, "depends_on": ["phase-4a"], "operator_step": true, "requires": ["P0"]}
  ]
}
```
