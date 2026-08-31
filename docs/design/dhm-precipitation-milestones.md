---
status: DRAFT
created: 2026-08-12
revised: 2026-08-12
title: DHM precipitation — milestone decomposition (our track)
scope: Milestones for OUR independent work on DHM gauge precipitation — fit-for-purpose QC, ERA5-Land comparison, and the Phase-2 operational decision — plus the no-regret repo work. The BSc students run a parallel thesis track on the same data; their output is corroboration, never a dependency.
source: docs/design/dhm-precipitation-vision.md
related: [143, 152, 153]
---

# DHM precipitation — milestones (our track)

**This plans OUR work only.** An earlier revision decomposed the students' thesis programme; that was
a supervision plan, not our workstream, and has been removed. The students run a parallel,
independent track on the same file. We **corroborate against** their findings at M-A9; we never
**wait on** them.

Revised twice: after a Codex review of the first cut, and after an owner scope correction.

## Owner decisions framing this cut

| | Decision |
|---|---|
| **Scope** | **Full independent track.** We run our own end-to-end analysis; the thesis is a cross-check, not an input. No schedule dependency on it |
| **QC depth** | **Fit for purpose, not thesis-grade.** Remove sentinels and stuck-high blocks; drop candidate zero-run periods **wholesale without adjudication**. Coarser than the thesis QC, and unblocked |
| **Repo QC rules** | **Build now**, proven by fixtures, accepting that they ship ahead of a production data path |

**Consequence — IMERG leaves our critical path.** Adjudication existed only to decide which zero runs
were genuine; discarding them all conservatively removes the need for an arbiter. Vision D4 now
governs the students' track. IMERG stays available if M-A6 turns out to need it.

## Two hard methodological rules

### Rule 1 — masking is missing-not-at-random, and identical masking does NOT fix it

Every timestamp dropped from the gauge series is dropped from ERA5-Land before any comparison. That
keeps the *pairing* consistent — but it does **not** recover unconditional behaviour. Because
`P(retained | gauge dry) < P(retained | gauge wet)`, the retained sample is MNAR and several
statistics stay biased in a known direction:

| Statistic | Effect under the mask | Status |
|---|---|---|
| Wet-hour / wet-day frequency | **inflated** | conditional-on-retention only |
| FAR (gauge-dry / ERA-wet pairs preferentially removed) | **biased low** | conditional-on-retention only |
| CSI | **inflated** | conditional-on-retention only |
| Unconditional intensity distribution | conditioned on gauge behaviour | conditional-on-retention only |
| Diurnal means | biased by hour-dependent exposure | conditional-on-retention only |
| POD | unchanged **only if** the mask removes exclusively gauge-zero hours and never adjacent wet observations — not guaranteed here | verify before reporting |
| Matched-hour mean difference, wet-hour conditional intensity bias | well-defined under the mask | reportable |

**Every masked-sample statistic is reported as a conditional-on-retention estimand or omitted.**
Unconditional totals, annual climatologies and "mm/year" figures are invalid outright. Exposure
counts by season and by hour of day are reported alongside every result.

### Rule 2 — no quantile-vector correlation

Comparing distributions by correlating quantile vectors gives r ≈ 1 by construction (verified:
exponential vs Pareto r = 0.943). Use scale-normalised comparison, divergence measures, or held-out
prediction error. This error is already in this project's record; it must not recur.

### Rule 3 — aggregation validity

Daily/monthly aggregates are formed **only after** applying the common hourly mask. No rescaling of
incomplete totals. The 0.2 mm threshold is applied after valid aggregation, never before. Contingency
tables include only jointly valid periods.

**⛔ AMENDED 2026-08-20 — the "stated minimum retained-hour coverage per period" clause is SUPERSEDED
by Plan 184 D13** (owner, grill-me 2026-08-19). A coverage minimum is exactly the completeness
threshold D13 forbids: the mask is MNAR, so filtering ON retention discards periods non-randomly and
hides the very pattern the retention figure exists to expose. **Stratify BY retention instead, never
filter ON it**, and attach each aggregate's `n` so it is self-describing at any retention. The rest of
this rule stands unchanged — mask first, never rescale, threshold after aggregation.

---

## Track D — external dependencies (asks, not work)

### M-D1 · Data authorisation
**Depends: —** Blocks M-I1 fixtures, M-I2. Cheap; answer it early or it invalidates work.

**Partly RESOLVED 2026-08-13 (owner):** **no DHM data — observations or station metadata — enters the
public repository.** Research data lives in `data/dhm_precip/`, already gitignored (`.gitignore:21`).
Test fixtures are therefore **synthetic**, reproducing defect signatures rather than real values.

**Still open:** may a derived dataset sit in the company-wide Dropbox, and under what attribution or
redistribution conditions? Still one question with storage — putting DHM data in a shared Dropbox is
a *distribution* decision of the same class.

**Exit:** written authorisation or explicit restriction for the Dropbox question. The repository half
is already decided: never commit, synthetic fixtures only.

### M-D2 · Station coordinates and elevation
**Depends: —** Hard-blocks M-A5, M-A5b, M-A6 and M-A8. *(Not M-A7 — that needs no coordinates.)*
**LANDED 2026-08-13** — coordinates and elevations for all 26 live stations, verified against the
live station set (exact match, all inside Nepal, no duplicate locations). Stored **untracked** at
`data/dhm_precip/station_coordinates.csv`.

lat/lon, elevation, DHM station ID for the 26 live stations. **Ask DHM directly and the students in
parallel, not in series** — a serial ask would reintroduce the thesis dependency this track exists to
avoid. Student-supplied metadata is an acceleration to be independently verified, not the first stage
of a chain.

**Exit:** station metadata table. There is still no *acceptable* null exit — the ERA5-Land comparison
cannot run without it — but the owner has confirmed delivery, so this is sequencing, not risk.

### M-D3 · Processing provenance

**✅ SUM vs MEAN RESOLVED 2026-08-18 — DHM hourly values are SUMS.** Reported by the students who
supplied the data. **Provenance is second-hand — a student relay, not a DHM statement — and must be
recorded that way.** This was the last open item blocking M-A6's MAGNITUDE estimands: a
mean-of-sub-hourly-samples would have made every magnitude wrong by a constant factor, while
leaving normalised shape (diurnal profile, wet/dry timing) untouched. **Magnitudes are unblocked.**

**Depends: —** Blocks M-A2. **PARTIALLY ANSWERED 2026-08-13** — DHM (Sunny Maharjan, Senior
Meteorologist), relayed by the student team.

| Question | Status |
|---|---|
| Timestamp assignment | **ANSWERED: period-ending.** 16:00 UTC = accumulation 15:00 → 16:00 UTC. **ERA5-Land is also period-ending, so the two align directly** — the ±1 h phase uncertainty is removed and no offset is needed at M-A6 |
| Off-grid sub-hourly rows | **ANSWERED: processing errors**, to be flagged and excluded. Validates the `ON_GRID` view retrospectively, and settles the Lukla sentinel count at 45 |
| **Aggregation: sum or mean** | **STILL OPEN** — follow-up with DHM. The one that changes totals by a factor |
| Station selection · the 11 empty columns · raw export availability · instrument type | still open (see `docs/requirements/dhm-precipitation-provenance-questions.md`) |

**What this unblocks now.** A sum-vs-mean error rescales every value by a constant, so it moves
**magnitudes** (totals, intensity quantiles, mass fractions) but not normalised **shape** (diurnal
profiles, wet/dry timing, between-station profile correlation). **M-A7's diurnal-shape work is
therefore unblocked**; magnitude-bearing results still wait on the aggregation answer.

**Exit:** written processing-chain statement, or a recorded "unknown" that downstream inherits as a
caveat. *Partially met: the timestamp and off-grid answers are recorded; aggregation is outstanding.*

---

## Track A — our analysis

### M-A1 · Reproducible ingest and baseline
**Depends: —** Start now.

A committed, parameterised pipeline that reads the source (sha256 `8dc57e43…f98f57`), and emits the
inventory, coverage, off-grid-row, reporting-precision, defect and climatology tables. Every wet
threshold, quantile grid and missing-data rule is an explicit parameter.

**Exit:** pipeline reproduces the vision's Findings, or the vision is corrected where they disagree.
**COMPLETE 2026-08-13** (Plan 170), **revised after a fixer round the same day**. 26 of 45
expectations reproduced exactly; 5 were corrected (a scope refinement and four draft-estimate
supersessions); 14 are `withdrawn_unreproducible` with a complete Phase-4 record each. (Counts are
parsed directly from `scripts/dhm_precip/expectations.toml`'s `disposition` field — see that file's
header comment for the exact reconciliation.) The fixer round closed three Findings the vision
quotes that the first pass never gated at all — 3-hourly coherence, modal intensity, and the
leave-one-out tail-prediction error (implemented and unit-tested from Task 2c, but never wired into
the runner) — and fixed four correctness bugs (monotonicity, daily/3-hourly coherence
completeness, per-station coverage denominator, modal-intensity population). **The citation ban
lifts per family (D11), not all at once**: it is now lifted for `AXIS_INDEPENDENT` and
`RAW_AXIS_DIAGNOSTIC` results (workbook column inventory, row/off-grid counts, station geometry) —
these may be cited externally. Every `RAW_PROVISIONAL` result (intensity distributions, shape
ratios, coherence, DJF share, missingness, sub-threshold mass, the leave-one-out tail-prediction
error) stays barred until its own D11 successor lands: M-A2 (axis normalisation), M-A3 (the QC
mask) or M-A7 (temporal characterisation). See `scripts/dhm_precip/expectations.toml` for the
per-statistic disposition and evidence record.

### M-A2 · Time-axis normalisation
**Depends: M-D3, M-A1.** Blocks M-A7.

Apply the accumulation convention and NPT→UTC handling. **Emits a normalised hourly dataset with
per-row provenance for every timestamp transformation, plus a mass-conservation check** proving
normalisation neither creates nor destroys precipitation. If the convention is unresolved, emit under
a stated assumption with a ±1 h phase-uncertainty flag carried downstream.

**Exit:** normalised dataset + provenance + passing mass-conservation check.

**COMPLETE 2026-08-14 (Plan 172).** M-D3 answered the open questions before implementation started
(period-ending, no assumption needed; the 3,350 off-grid rows are DHM processing errors, excluded and
counted rather than converted), so this milestone shrank to exactly D1's materialisation problem: **568
hours are missing from the workbook for every station** (not a per-station reporting gap — the loader
already carries those as NULL rows) — `scripts/dhm_precip/normalise.py`'s `normalise_hourly_axis`
reindexes the ON_GRID view onto one common hourly axis per station (the runner's validated 26-station
set, D6b), filling every missing `(station, hour)` cell with NULL, never `0.0` (D2). A delivered
`(station, timestamp)` key must be unique — `DuplicateDeliveredRowError` otherwise, and the join itself
is `validate="1:1"` — so a duplicate can never silently fan out into extra output rows. Conservation is
proved by row identity — not summation, which review found is itself arithmetic and not
order-independent (D5) — asserted in code via `assert_row_identity_conservation`, which the runner calls
unconditionally: a violation halts the run. That assertion checks BOTH directions (every delivered
identity survives into the output, and every preserved identity in the output traces back to a real
delivered row via anti-joins, not a row-count-plus-forward-join check that a drop-one/duplicate-another
corruption can defeat) and compares `value_mm` by IEEE bit pattern, not `==`/`eq_missing` (which treat
`0.0` and `-0.0` as the same value; a bit-identical check does not). A post-implementation review round
found and fixed both gaps. The aggregation guard (D2b) closes the same null-vs-zero
trap in `stats_climatology.per_year_totals_with_completeness` and `stats_defects.annual_totals`, both
of which previously reported a fabricated `0.0 mm` total for a wholly-unreported station-year (Polars'
`.sum()` over an all-null group returns `0.0`, not null). A new `AxisStatus.NORMALIZED` tags the two new
runner artefacts, `normalised_axis` and `normalisation_provenance`.

**Run against the real pinned workbook (2026-08-14):** 52,597 hourly slots span the record; 568 of them
have no row from any of the 26 live stations (52,029 delivered ON_GRID rows), materialising as
568 × 26 = 14,768 inserted NULL rows. The off-grid provenance grains match D3's two named populations
exactly: **3,350** `off_grid_source_timestamp_rows` and **6,633** `off_grid_non_null_observations`. The
row-identity conservation assertion passed without exception. These totals — plus 52,597 unique hourly
slots, 1,367,522 (= 52,597 × 26) normalised rows, 14,768 inserted NULL rows and unique
`(station, timestamp)` keys — are regression-locked in
`tests/integration/test_dhm_precip_reproduction.py::TestNormalisedAxisMatchesTheRecordedRealWorkbookTotals`
(gated on `DHM_PRECIP_XLSX`, per constraint 1).

### M-A3 · Fit-for-purpose QC mask
**Depends: M-A2, M-I1.** *(OD-3: this milestone is M-I1's first consumer.)* Blocks M-A6, M-A7, M-I2. *(Depends on M-A2, not M-A1: run detection needs a canonical
hourly axis. On unnormalised rows, duplicated/off-grid/ambiguous timestamps make "consecutive"
undefined.)*

Produce a **timestamp mask**, not a cleaned dataset: drop sentinel values, stuck-high blocks
(identical consecutive non-zero values), and **entire candidate zero-run periods without
adjudication**. Retain everything else.

**Run the detection through the production QC service, not through bespoke research code** (OD-3).
`Stage1QualityChecker.check()` (`services/qc.py:212`) is a pure function — observations, rule set,
overrides, baselines in; flags out — with no store or I/O. Build an **in-code precipitation
`QcRuleSet`** (as `_default_swiss_qc_rules()` does), construct `Observation` objects per station
(~52k each, chunked) from the normalised frame, and call it. This exercises M-I1's rules on ~1.37M
real values from day one and adds **no config rows**.

**Runs are defined on contiguous canonical hourly timestamps**, with a parameterised detection
contract: minimum run duration, seasonal scope, treatment of isolated missing values inside a run,
boundary-hour handling, and whether nearby runs merge. Without this, "wholesale" is reproducible only
after subjective identification.

Record what fraction of each station's record the mask removes, per season — a station losing most of
its monsoon is no longer usable and must be **excluded** from M-A6 rather than silently thinned.

**Exit:** mask + per-station/per-season removal accounting + the M-A6 exclusion list + **red-first
acceptance cases for each defect signature, including the Aiselukhark 52-day run**, the Sindhuli
stuck-high block and the Lukla sentinels.

**COMPLETE 2026-08-15 (Plan 173).** Two `Stage1QualityChecker` passes over the M-A2 normalised frame
(D3c): pass A (`range_check` 0–200 mm/h + a stuck-value `frozen_sensor`, `exclude_at_or_below=5.0`,
12h, whole series) and pass B (a long-zero-run `frozen_sensor`, no exclusion floor, **168h (7 days) —
measured, not the 12h inventory threshold**, run once per JJAS season, season scope applied outside
the checker per D3b). `scripts/dhm_precip/qc_ruleset.py`, `observations.py`, `qc_mask.py`.

**Run against the real pinned workbook (2026-08-15):** the mask holds **11,381** of 1,367,522
normalised rows (17 of 26 stations touched). Sindhuli Madhi's stuck-high block: **exactly 120 hours**
— matches the predicted duration precisely. Aiselukhark: **2,852 hours** across the 52-day run and its
siblings. Lukla: **45** sentinel hours, matching the count `M-D1`/OD-3 already settled retrospectively.
Worst JJAS retention: **Lete 0.8296**, then **Aiselukhark 0.8313**, **Nagarkot_AWS 0.9134** — matching
the plan's pre-implementation measurement (D3) to 3 decimal places. Median JJAS retention **0.984**; no
station below 0.75. **The M-A6 exclusion list is empty** — expected (D8), not a bug: the 0.50 floor
never binds on this delivery. The cross-classified `(station, season, hour_of_day, category)`
accounting reconciles exactly to the 1,367,522-row axis. Locked in
`tests/integration/test_dhm_precip_reproduction.py::TestQcMaskAgainstTheRealWorkbook` (workbook-gated
— the M-A1 reproduction gate evaluates unmasked statistics by design and would pass against an empty
mask, so these assertions run against real data specifically, not just synthetic fixtures).

Rule provenance (id, `rule_version`, thresholds, scope, per pass) ships as its own artefact
(`qc_rule_provenance`) — the mask reduction collapses flags to timestamps and discards which rule
fired, so M-I2 needs this table to package the dataset reproducibly.

**Out of scope here, as planned:** recomputing M-A1's 14 `withdrawn_unreproducible` expectations
(M-A6/M-A7's job) and adjudicating which zero runs are real (declined by design — wholesale removal).
The per-family citation ban on those withdrawn expectations stands until M-A6/M-A7 land.

### M-A4 · ERA5-Land acquisition
**Depends: —** Start now. **Owner 2026-08-12: use a direct point/grid route rather than registering
Gateway polygons.** *(Supersedes the polygon-registration plan — see OD-1.)*

Pull hourly ERA5-Land total precipitation over a Nepal bounding box for 2020–2025 from the
Copernicus CDS, and store it locally as the research pipeline's input.

**Why this beats the Gateway-polygon route.** The repo already carries the entire gridded stack —
`xarray`, `cfgrib`, `h5netcdf`, `zarr`, `rioxarray` (`pyproject.toml`), plus existing grid handling in
`preprocessing/` and `adapters/meteoswiss_nwp.py`. Point extraction is `.sel(..., method="nearest")`,
not new architecture. It needs no polygon geometry, no Gateway registration, no coordination with the
gateway team, no research geometry in a production namespace, and — critically — it gives us
**explicit control of the extraction operator**, which the Gateway's opaque area-averaging does not.

**Volume is trivial.** 0.1° over 80–89 °E / 26–31 °N × 6 years hourly ≈ **0.98 GB float32**; a
Koshi-only box (85–89 °E, 26.5–28.5 °N) ≈ **0.18 GB**.

**Two known traps:**
1. **ERA5-Land `total_precipitation` is an accumulated field** (reset daily at 00 UTC), so it must be
   deaccumulated before use. The repo has precedent in `_deaccumulate_precipitation`
   (`adapters/meteoswiss_nwp.py:157`) but ERA5-Land's convention differs — do not reuse blindly.
2. **ERA5-Land's stamp is period-ending** (hour *t* = accumulation over *t−1 → t*). That is *known*,
   unlike the gauge convention (M-D3) — so once M-D3 answers, alignment is arithmetic. If M-D3 never
   answers, ERA5-Land's known convention is what the ±1 h uncertainty is measured against.

**Verify at implementation time:** CDS has changed its API and dataset identifiers before; confirm the
current endpoint, dataset name and licence-acceptance step rather than assuming.

**Exit:** deaccumulated hourly ERA5-Land precipitation over the study box, stored locally, regenerable
from a committed request script, with the CDS dataset identifier and request parameters recorded.

**Code COMPLETE 2026-08-13 (Plan 171)**: `scripts/dhm_precip/era5_request.py` (D2 request builder, the
observed-payload literal), `era5_manifest.py` (D5 identities, atomic writer, D11 provenance), the
`era5_acquire.py`/`era5_transform.py` drivers, `era5_deaccumulate.py` (D6's accumulation-day rule,
red-first against a naive-global-diff candidate), and the `acquire_era5.py` CLI (D14 gate: 92 unit
tests, `ruff check`/`format` clean, `pyright scripts/dhm_precip/` 0 errors, `pyright src/` ratchet
clean). **Pending the operator steps that follow the code (2b, 4b)**: the real October-2021 sample
acquisition and the full 2020–2025 acquisition + transform — both require a credentialed human and are
explicitly out of a subagent's scope (plan `## Human prerequisites`); P0 (CDS account + licence
acceptance) is done, and `data/dhm_precip/era5_land_provenance.json` (D15) is in place.

**Fixer round 2026-08-13 (post-review)**: a multi-model review (Claude + independent Codex) found
blockers/majors in the first pass — raw/final schema validation was tolerance-based rather than exact
(a spatial subset or duplicated timestamps could pass as the full product); a hostile `CdsClient`
implementation could still leak credentials into a raised exception; `cdsapi.Client()`'s own retry loop
(500 attempts, real `time.sleep`) silently bypassed the injected, bounded outer retry; masked
(sea/non-land) NaN cells were wrongly treated as missing boundary context; `diagnose_accumulation_convention`
(3a) had no operator-invocable path. All are now fixed: `--stage diagnose` wires the convention
diagnostic to an already-acquired raw window (operator runs it against the real 2b sample before 4b);
every exception crossing the `CdsClient` seam is sanitized at the seam, not just logged; `RealCdsClient`
pins `retry_max=1` so the outer driver is the sole retry owner; D9's raw/final schema checks are exact
(grid count/spacing/order, time uniqueness, dtype, attrs, on-disk encoding via `validate_output_encoding`);
D6 post-condition 1b (per-day/cell post-clamp accounting) is asserted in code; a manifest-write failure
mid-revision restores the previous good product (D5); the acquisition-wide dataset is now immutable once
a manifest exists. See `docs/plans/archive/171-era5-land-acquisition.md` for the full finding list and fixes.

### M-A5 · Point extraction at station locations
**Depends: M-D2, M-A4.** *(M-A4 needs no coordinates; only the extraction does.)*

Extract ERA5-Land at the 26 station locations.

**Choose the extraction operator explicitly** (OD-1) — nearest cell centre, containing cell, or
bilinear. Unlike the polygon route this is a one-line parameter and **cheaply reversible**, so the
decision is no longer front-loaded; but it must still be stated, and its consequences measured.
**Decided (Plan 174, D1): nearest cell centre is THE operator**, locked before any numbers were seen
— precipitation is not a smooth field, and bilinear (a convex combination of four cells) cannot
preserve a cell-scale maximum, biasing exactly the tail this track cares about. Record the ERA5 grid
coordinates and **model orography elevation** per station (with an `orography_source` enum —
`MODEL_OROGRAPHY` or `DEM_PROXY`, a public-DEM fallback with a named, criteria-gated candidate list,
Plan 174 D3a), quantify the station-to-grid elevation mismatch (both sides carrying an explicit
vertical-datum enum — the station side is `UNKNOWN` until M-D2 or DHM states one, Plan 174 D3b), and
run at least one sensitivity comparison against bilinear, reported as a **named operator-sensitivity
envelope** (never an uncertainty band, never a decision gate — Plan 174 D1a).

**IMERG split out — Plan 174 (2026-08-16).** An earlier revision of this section made "also extract
IMERG at the same 26 points" part of this milestone's exit. Plan 174 corrected that: no implemented,
frozen IMERG acquisition pipeline exists (a documented survey of access routes is not code), and the
remaining work is a **different shape** from ERA5-Land's — half-hourly native resolution needing
aggregation to hourly, and a rate (mm/hr) convention rather than an accumulation, so none of the
ERA5-Land deaccumulation logic transfers. IMERG acquisition + extraction is its own milestone,
**M-A5b** (below). The rationale for wanting IMERG at all (satellite diurnal-phase trustworthiness at
our elevations; the Dawadi-vs-Adhikari peak-timing discrepancy) is unchanged and now lives there.

**Exit:** extracted ERA5-Land series (nearest, primary) + named operator, recorded in the extraction
manifest, never implied by code + per-station elevation mismatch table (`orography_source`, product
id/version, both vertical-datum enums) + the operator-sensitivity envelope, all regenerable from the
committed pipeline into an identity-addressed bundle. **Not** IMERG (M-A5b) and **not** the
Kirtipur/Khumaltar gauge-pair diagnostic (that needs the M-A3 mask, so it moved to M-A6, below).

### M-A5b · IMERG acquisition + extraction
**Depends: M-D2.** Mirrors M-A4 → M-A5 for IMERG: acquisition (half-hourly, mm/hr rate convention,
aggregate to hourly — none of ERA5-Land's deaccumulation logic transfers) then point extraction at the
26 station locations. Record the product version and the same grid/elevation diagnostics as M-A5's
ERA5-Land side. Split from M-A5 by Plan 174 (2026-08-16): the milestone doc previously named this
"nearly free once the extraction pipeline exists," which undercounted a distinct acquisition shape
(see M-A5 above).

**⛔ SUPERSEDED 2026-08-28 (Plan 211, on two points the milestone text originally got wrong):**

1. **Final → Early.** This section originally specified **IMERG Final**. The owner decided
   (Plan 209 D7, confirmed at Plan 211 READY) that the operational question is about **IMERG Early**
   (~4 h latency): Final has ~3.5-month latency and is the one run that can never serve an operational
   role, so a characterisation built on it would answer a question we do not have. Plan 211 acquires and
   extracts **Early V07**, not Final, and the manifest records the run as **RETROSPECTIVE** (a
   retrospectively downloaded Early bundle can contain inputs unavailable in live operation, so any
   later skill number derived from it may overstate live Early performance).
2. **No elevation-mismatch table.** ERA5-Land's table quantifies **model**-versus-station mismatch,
   which is what D14's lapse correction consumes; IMERG is a satellite retrieval with no model surface,
   so a "mismatch" here has no consumer. Plan 211 D6 instead records, per station, the selected cell's
   centre lat/lon and the station's own elevation (vertical datum `UNKNOWN`) — never grid indices, and
   never a DEM-derived comparison (adding a public DEM was out of scope for an acquisition milestone).

**Plan 211 T1 (acquisition) is COMPLETE up to its own projection gate** (2026-08-28): the pipeline
(`scripts/dhm_precip/imerg_acquire.py`, `scripts/dhm_precip/imerg_extract.py`) is built and unit-tested
no-network; exactly one granule was downloaded from the live GES DISC archive as the D1 contract probe,
freezing the read contract. **⛔ That probe found the live archive serving revision `V07C`, not the
`V07B` Plan 211 D1 pinned** (both measured the same day). **Post-review fixer pass (2026-08-28) made
the pin ENFORCED, not merely documentary**: `assert_revision_matches_plan_and_header` rejects any
granule whose filename revision differs from `V07B`, and independently cross-checks the filename
against the granule's own embedded `FileHeader.FileName`. ⛔ **Not `FileHeader.ProductVersion`** — the
probe measured `ProductVersion=07B` on a granule whose filename and `FileName` both read `V07C`, so the
two are different axes and equating them would reject legitimate granules. Consequence: a live
acquisition run against the archive **as observed 2026-08-28** will raise `ImergReadContractError` and
halt — D1's
own required behaviour ("stop and report rather than blending"), not a bug; resolving the halt
(confirming V07B has returned, or re-confirming the READY plan for V07C) is the owner's call, not
something this pipeline resolves unilaterally. **The bulk retrieval (105,216 granules, projected
~848 GB against 896 GB free) was NOT performed** — Plan 211's per-run scope stops at the projection
gate; bulk retrieval is the owner's call. **Post-review fixer round 2 (2026-08-28)**: T2's own `main()`
(`imerg_extract.py`) did not catch `ImergAcquisitionError` — a D1 violation raised while T2 reads a
granule (the exact halt described above) escaped as an unhandled traceback instead of the structured
`imerg_extract.cli.failed` log line + defined exit code T1's `main()` already produces for the same
hierarchy. Fixed by widening T2's `main()` exception handling to the broad `ImergAcquisitionError` and
`Era5ExtractionError` bases (mirroring `extract_era5_t2m.py`'s own `_EXIT_BY_ERROR` table), with a
locking test driving the real `V07B`-filename/`07C`-header drift through `main()` end-to-end. **Closing
round (2026-08-28)**: an independent review found the completeness invariant sitting in the wrong layer
and the pipeline over-built against D9's proportionality bar (2,851 production lines). Fixed and
simplified together — the pinned-window accounting invariant now lives in the ONE publish/discovery
predicate D9 requires (a hand-built bundle claiming one requested granule is refused at publication and
skipped by discovery, not merely rejected inside `run()`); the permanent acquisition record DERIVES both
its own and an incoming record's completeness on write, so the stored `completeness` label is gone and
nothing can *claim* COMPLETE; recorded gaps are reconciled against the pinned window; `imerg_extract`'s
exit-code table honours `ImergStorageError`'s promised exit 5; and `--out` REFUSES an existing non-empty
directory instead of `rmtree`-ing it. The acquisition manifest is now the sole owner of the full D1 read
contract and the per-granule checksums — the bundle hashes their canonical digests into its identity
rather than carrying two more copies of both — and the 26-station/complete-axis invariants are pinned
rather than passed in. **Confirming round (2026-08-29)**: the digest rewrite had moved the defect one
layer up rather than closing it — the bundle's `acquisition_record_sha256`/`read_contract_sha256` were
unauthenticated caller-supplied strings, so a bundle naming an acquisition record that does not exist
published and discovered happily (the fixtures carried literal placeholder digests, which is why two
rounds missed it). The ONE predicate now RESOLVES the permanent acquisition record, re-derives its
completeness, RECOMPUTES both digests from its contents and reconciles route/window/counts/gaps/box
against the bundle. Four smaller items landed with it: the record's D1 `read_contract` is validated
SEMANTICALLY (`ImergReadContract` is the judge — a record whose every value read `"recorded"` used to
derive as COMPLETE); a non-numeric `station_accounting` value is normalised into the typed validation
error discovery skips rather than aborting discovery with a raw `ValueError`; `sensitivity_params` is
checked against Plan 174 D1a's pinned snapshot again (as is the named NEAREST `operator_id`, hashed
into the identity but never validated); and D6's `UNKNOWN` datum plus the per-hour /
station-cell agreement are validated, with the record's gaps canonicalised chronologically so gap ORDER
cannot change an identity.

**M-A5c — the two prerequisites closed and the volume MEASURED (Plan 224, 2026-08-31).** Both
deferrals that were safe only while nothing had been published are now closed. (1) The bundle's
recorded **gaps** are reconciled against the per-station hourly `granule_count`: they are two records
of one fact and nothing compared them, so a bundle could report a gap the series does not reflect (or
an hour short a granule the acquisition never recorded as missing) and validate. The check derives the
expected count per hour from the gap SET (`expected_granule_count_by_hour`) — linear in the gaps, ⛔
never the 105,216 x 52,608 product the naive formulation implies. (2) `write_acquisition_manifest`
now refuses to replace a record whose identity-bearing content would change while published bundles
sit under the sibling `points/` root: it protected the record from being DOWNGRADED but not from being
ORPHANED, and a re-acquisition could leave every published bundle's `acquisition_record_sha256`
addressing a record that no longer exists. A rewrite that only moves the clock is still allowed —
`acquisition_record_identity_content` is now the ONE definition of what the digest covers, shared with
`acquisition_record_digest`.

**⛔ A third defect was found only by running it, and it blocked every acquisition.** The 2026-08-28
narrative above is right that the pin is enforced, but the enforcement compared the granule's embedded
`FileHeader.FileName` against the **archive** filename pattern, whose `.HDF5` literal **no real IMERG
Early granule can satisfy**: Early *is* the real-time run, so its embedded name ends **`.RT-H5`** while
the archive stores the identical bytes as `.HDF5`. Every live granule therefore halted, reporting a
revision disagreement that did not exist (`V07B` on both sides). The archive filename parser stays
strict; only the embedded-name check accepts either extension.

**Two review findings on that fix, closed 2026-08-31.** (a) The relaxation had degraded the
cross-check to a **revision** comparison: any syntactically valid IMERG embedded name carrying the
pinned `V07B` passed, whatever date or half-hour it named — and extraction takes a granule's timestamp
from its **path**, so contents from one time could be filed under another. The embedded name's
extension is now **normalised** (`.RT-H5` -> `.HDF5`) and the **complete names** are compared, which is
what the `.RT-H5` allowance was ever meant to permit. (b) Acquisition and publication are now declared
**mutually exclusive**, and the rule is enforced: the record's writer scans the published bundles and
*then* replaces the record, while publication validates the record and *then* renames a bundle in, so
interleaved they can both pass and leave a bundle naming a replaced digest. ⛔ Rather than coordinate
them, both sides take one **non-blocking advisory `flock`** (`imerg_early/.imerg-writer.lock`) over
their whole read-then-act sequence, so a violation fails loudly instead of racing. Both operations run
from the same host and are minutes-scale; the lock only makes an existing assumption explicit.

**MEASURED 2026-08-31 — one granule, `2020-07-15T00:00Z`, GES DISC HTTPS archive, full global field:**
**8,047,136 B (8.05 MB / 7.67 MiB)**. ⇒ full D5 window (105,216 granules) **846.7 GB (788.5 GiB)**;
one JJAS season (5,856 granules) **47.1 GB**; six JJAS seasons **282.7 GB**. Free disk at the time:
**939.6 GB (875.0 GiB)** — the full window fits with **~87 GiB (9.9 %) to spare**, which is a real but
thin margin on a shared disk. ⚠️ Two facts the earlier narrative could not know: the **retrospective**
window is served as **`V07B`**, so the pin holds (the `V07C` drift was observed on a *near-real-time*
granule, not an in-window one); and the route measured is the **full global field**, ⛔ **not** a
server-side subset. A subset over the frozen box is 50 x 90 = **4,500 cells** against the global
3600 x 1800 = 6,480,000 — a 1,440x reduction that would dissolve the volume question entirely, but it
would need a **different D1 read contract** (the frozen one pins the exact 1800/3600 coordinate vectors
and warns explicitly against "a subset service's own grid"), so it is an owner decision, not a
substitution this pipeline can make. ⛔ **No bulk retrieval and no published bundle**: exactly one
granule is on disk.

**Exit:** extracted IMERG **Early** series + named operator + the per-station cell-centre/elevation
record above (no DEM mismatch table) + the operator-sensitivity comparison + the manifest marked
**RETROSPECTIVE**, all regenerable from the committed pipeline — the same shape as M-A5's exit, for
IMERG Early. **Not yet met**: the bulk retrieval and the resulting published bundle are still pending
the owner's go-ahead — which now rests on a **measured** projection (Plan 224 above), not an assumed
granule size, and on the full-field-versus-subset choice that projection exposes.

**M-A5d — the OPeNDAP subset route, built and cross-checked, 847 GB → 2.7 GB (Plan 225, T1+T2 only,
2026-08-31).** M-A5c's own "different D1 read contract" caveat above is now closed. **Probed
2026-08-31**: GES DISC serves the same granule over OPeNDAP, and a server-side subset over the frozen
box (`dap4.ce=/precipitation[0][2600:2689][1160:1209];/lon[2600:2689];/lat[1160:1209]`) is **25,524 B —
315x smaller** than the archive-route granule (8,047,136 B) — ⇒ the full 2020-2025 window drops from
846.7 GB to **~2.7 GB**, dissolving the disk-margin question M-A5c's projection posed. Built (T1): a
**second, separately-frozen** read contract (`ImergSubsetReadContract`) pinning the box-local 90x50
grid, the root-level `/precipitation` path OPeNDAP's flattening of `/Grid` produces, the exact lat/lon
vectors, and — new relative to the archive contract — dtype and `scale_factor`/`add_offset` (measured:
unpacked float32, neither present); a route-distinct raw directory (`raw_subset/`) so an archive and a
subset artifact for the SAME granule can never collide on disk; contract-validation dispatch on the
manifest's recorded `route` (`GES DISC HTTPS archive` vs `GES DISC OPeNDAP subset`), so a record naming
one route can no longer be checked against the other's contract shape; and a route-aware reader
(`read_subset_granule`) normalising the subset response into the SAME `(valid_time, latitude,
longitude)` layout the archive reader produces. The archive contract/parser (`ImergReadContract`,
`contract_from_open_granule`) are **untouched**. **Cross-checked (T2), on the granule already held**
(`2020-07-15T00:00Z`): one live, authenticated OPeNDAP request — the ONE this run's per-run scope
permitted — fetched the subset over the exact same box, cached under `raw_subset/`, and compared
cell-for-cell against the archive-route granule already on disk. **Coordinate vectors matched exactly**
(50 lat + 90 lon values, bit-for-bit) and **all 4,500 decoded values matched exactly** (max abs diff
`0.0`, tolerance frozen at `0.0` from the observed unpacked-float32/no-packing dtype on both sides,
before the comparison ran, per D5) — the subset route reads the identical underlying grid, not a
resampled one. ⛔ **T3 (retrieving the full 105,216-granule window through this route) was explicitly
NOT run this pass** — it is a multi-hour, outward-facing operation gated on its own authorisation,
per Plan 225's per-run scope; the projected ~2.7 GB is a **measured extrapolation** from one granule's
subset size, not yet a bulk-retrieval result.

**Fixer round (2026-08-31), same commit's follow-up review — six findings closed.** The
route-dispatched CONTRACT validation above existed at commit time, but `imerg_extract.run()` itself
still unconditionally called the archive reader against `raw/` regardless of the acquisition record's
`route` — a subset-route bundle could not actually be extracted end to end. Closed: `run()` now
dispatches on `acquisition.route` (subset → `raw_subset/<archive-name>.dap.nc4` +
`read_subset_granule`; archive → unchanged), with its own `assert_subset_contract_consistent` and an
end-to-end `run()` test over a synthetic subset manifest (blocker). `ImergSubsetReadContract` was
tightened to pin the T2-approved lat/lon vectors and longitude convention EXACTLY (previously only
their lengths were checked, so a same-shaped grid at the wrong location — or in the wrong hemisphere
convention — would have passed) and to reject any packed (`scale_factor`/`add_offset`) response at
construction, protecting every subset read `run()` performs, not only T2's one cross-checked granule;
`cross_check_subset_against_archive` gained a matching archive-side packing/dtype check and dropped its
unconstrained `box` parameter (major x2). The archive parser's "vice versa" refusal of a subset-shaped
file is now a typed `ImergReadContractError`, not a bare `KeyError` (minor). All six locking tests were
proven to fail against the pre-fix code before being restored green.

**Fixer round 2 (2026-09-01), independent Codex pass over that commit — four more findings closed.**
1. **Longitude convention was derived from the wrong field (MAJOR).** `contract_from_open_subset_
granule` derived `longitude_convention` from the box-local `/lon` slice's own `min()` — which is
ALWAYS positive for this box (80-89E), so it could only ever derive `"UNSIGNED_360"`, no matter what
the real global grid's convention is. Inspecting the real committed probe response
(`data/dhm_precip/imerg_early/raw_subset/…HDF5.dap.nc4`, the actual live 2020-07-15T00:00Z granule)
confirmed the true source: OPeNDAP's flattening retains the `/Grid` group's own `GridHeader` attribute
at ROOT level, renamed `Grid.GridHeader` (`WestBoundingCoordinate=-180;EastBoundingCoordinate=180`),
and `/lon`'s own `LongName` independently reads "...from -180 to 180." — i.e. the box is always cut
from a SIGNED_180 global grid, and the previous fixer round's pinned `EXPECTED_SUBSET_LONGITUDE_
CONVENTION` (derived from the box's own sign) had frozen the wrong value. Fixed: `longitude_convention`
is now derived from the retained `Grid.GridHeader`'s `WestBoundingCoordinate`, and the pinned
expectation is `"SIGNED_180"` unconditionally. Five locking tests (derivation from the header despite
an all-positive local slice, rejection of a contradictory header, rejection of a missing header, and
the corrected — previously backwards — accept/reject pair) all proven to fail against the pre-fix code.
2. **Subset acquisition trusted bytes on disk without validating them (MAJOR).** `acquire_subset_
granule` treated `target.exists()` alone as sufficient to reuse a cached artifact, and installed a
freshly downloaded file via `os.replace` without ever opening it — an HTTP-200 login page, a truncated
download, or a stale malformed cache entry would be served (or reused) forever, weaker than the archive
route's own validate-and-reuse. Fixed: both the existing-artifact and the freshly-downloaded-tmp paths
now run the D1 subset contract (`observe_subset_read_contract`) before being trusted; an invalid cache
falls through to a fresh download rather than being served, and a malformed download is never installed
(the HDF5/open failure is converted to a typed `ImergReadContractError`, never a raw `OSError`). Only a
VALID cache still suppresses the HTTP call. Two locking tests (malformed new download never installed;
malformed existing cache does not suppress the re-fetch) proven to fail against the pre-fix code.
3. **The T2 hard gate had no production caller (MAJOR).** `cross_check_subset_against_archive` returns
a report on a value mismatch rather than raising, and `assert_subset_cross_check_passed` — the function
that turns a failed report into the plan's required stop — had no caller anywhere in the module; a
caller computing the report and forgetting to check `.passed` would silently continue past D5's hard
stop. Fixed: `run_subset_cross_check` is now the ONE production T2 entry point (computes the report,
then enforces the gate before ever returning it); the "value mismatch is refused" test now drives THIS
function and asserts the raise, not merely `report.passed is False`. Proven to fail against the pre-fix
code (no such function existed).
4. **Offering an archive-shaped file to the subset parser raised a raw `KeyError`, not the typed error
(minor).** The "vice versa" of the prior round's own archive-parser fix — `contract_from_open_subset_
granule`'s `f["precipitation"]` lookup was unguarded, and the fixture claiming to test this actually
built a root-level layout with a full-field SHAPE, never a real `/Grid`-shaped archive file. Fixed:
the lookup is wrapped and converted to `ImergReadContractError`; a new locking test feeds a REAL
archive-shaped fixture (`_write_fake_archive_granule_for_box`) to the subset parser and proves the
typed error, failing against the pre-fix code with a raw `KeyError`.

All nine new/changed locking tests were proven RED against the pre-fix code (source reverted via
`git stash`, tests kept) before the fix was restored and the suite turned green again.
⚠️ **Separately noted, not fixed this round (pre-existing, from fixer round 1, out of this round's
scope):** the round-1 `EXPECTED_SUBSET_LAT_VECTOR`/`LON_VECTOR` exact-pin constants do not bit-for-bit
match the REAL committed probe file's own float32 coordinate values (both within ~5e-6 of each other,
which the exact-tuple `!=` check does not tolerate) — re-running `observe_subset_read_contract` against
the real artifact on disk raises on the lat-vector pin, independent of this round's longitude-convention
fix (confirmed by reproducing the same raise against the pre-round-2 code). The narrative above
("coordinate vectors matched exactly, bit-for-bit") describes the ORIGINAL T2 run's archive-vs-subset
comparison, from BEFORE the round-1 exact-pin constants existed — it does not mean today's code
validates that same real file end to end. Flagged for the owner; not a Plan 225 D-decision this round.

**Exit (T1+T2 only):** a second, separately-frozen OPeNDAP subset read contract; route-dispatched
contract validation with a locking test per mismatched combination; a route-distinct raw directory; a
route-aware reader; and an exact, real-data cross-check against the archive route on the one granule
both routes now hold. **Not yet met**: the bulk retrieval (T3) and the resulting published bundle —
gated on a separate go-ahead, per the plan's binding per-run scope.

### M-A6 · Gauge vs ERA5-Land comparison
**Depends: M-A3, M-A5.** *(M-A2 enters transitively through M-A3 — ERA5-Land is on a canonical UTC
axis, so the gauge side must be normalised before any pairing.)* **The point of this track.**
**Two-way now** (gauge vs ERA5-Land); **three-way once M-A5b lands** (adds IMERG) — M-A5b is not a
dependency of this milestone's current exit, only of its eventual three-way extension.

Scale- and season-stratified per vision D3, under rules 1–3. Warm season quantitative; cold-season
high-altitude qualitative only (D6). ERA5-Land is never a QC input (D4).

**Estimands are named, not implied.** "Monthly bias" is undefined under an MNAR mask — a difference
of masked sums is a *conditional accumulated difference*, and a ratio is unstable when retained gauge
precipitation is small. Report instead: **matched-hour mean difference**, **conditional accumulated
difference**, and **wet-hour conditional intensity bias**. Frequency and categorical scores are
reported only as conditional-on-retention estimands per rule 1. **No annualisation.**

**Representativeness is characterised, not decomposed.** One point gauge against one ERA5 cell cannot
empirically separate grid representativeness error from model error without extra spatial
information. Characterise it via extraction-operator sensitivity (M-A5), station-to-grid elevation
difference, within-cell topographic spread and neighbouring-cell variability — and label it a
characterisation, not a decomposition (this downgrades vision D3a's wording).

**Within-cell observed gauge variability (Plan 174 D6 handoff).** Kirtipur and Khumaltar fall in one
ERA5-Land 0.1° cell, 4.33 km apart, 30 m different in elevation — ERA5 returns one identical series
for both, so their gauge-side disagreement is the only empirical representativeness signal this track
can compute. It belongs here, not in M-A5, because it needs the M-A3 mask (M-A5's D4 forbids
gauge/mask coupling) and both stations' measurement error confounds it — Khumaltar alone swings 294 mm
(2023) to 1,504 mm (2024), an undocumented-cause but disqualifying-for-a-"clean-bound" 5x inter-annual
change. Compute it on timestamps **retained by the M-A3 mask for both stations simultaneously**,
report the common-retained-hour count and each station's exposure alongside every statistic, and **⛔ CORRECTED 2026-08-18 — report it DESCRIPTIVELY; the lower-bound claim is WITHDRAWN.** "If both gauges are unbiased, half the discrepancy is a lower bound on the within-cell contribution" does NOT follow: two gauges with unbiased errors still differ over a finite sample by noise alone, so this would report a positive bound where the true spatial contribution is zero. The triangle-inequality argument needs effectively ERROR-FREE aggregates, not merely unbiased ones. **n = 1 pair, one valley, one separation — never a network-wide estimate.**

**Exit:** error characterisation; every result signed per D6 — the undercatch caveat stated as a
property of **catch efficiency** (*for a correctly-functioning gauge, catch ≤ true precipitation*),
carrying the selection caveat, its named estimand, and the retained fraction it rests on; plus the
within-cell observed gauge variability figure above, with its honest limits attached.

**⛔ CORRECTED 2026-08-20 — the parenthetical "a post-QC gauge total is a *lower bound*" is WITHDRAWN
here too.** It was withdrawn in vision D6 and Plan 184 D6 on 2026-08-19 but survived in this line: our
QC is a physical-impossibility gate, not an outlier filter (Plan 173 D4 sets `value_max = 200.0` mm/h
to be unreachable rather than discriminating), so an isolated spurious value ≤200 mm/h passes and can
push a total ABOVE truth. **The full Exit for this milestone is Plan 184's `## Exit`**, which binds the
placement rule D4/D13 require: no magnitude is quoted without BOTH its retained-hour `n` and its
sub-freezing mass fraction in the same cell.


**✅ PREREQUISITE DELIVERED 2026-08-20 — ERA5-Land `2m_temperature` (Plan 191).** Plan 184 D4/D14
replace the calendar snow carve-out with a measured sub-freezing mass fraction, which needs temperature.
Plan 171 acquired **`total_precipitation` only** (`171:121`), so this was raised as a prerequisite on
2026-08-19 and is now complete. It stayed an acquisition/transform track against 171's and 174's
contracts, deliberately NOT folded into 184 — 184 is a comparison, not an acquisition plan.

**What exists now**, under its own data root `data/dhm_precip/era5_land_t2m/` (gitignored):

| Artefact | Path | Shape |
|---|---|---|
| raw | `era5_land/raw/era5_land_t2m_raw_{window}.nc` | 74 monthly windows, Kelvin |
| product | `era5_land/degc/era5_land_t2m_degc_{2020..2025}.nc` | `temperature` / `degC`, float32, exact calendar-year hourly axes (8784 leap / 8760), 0 non-finite |
| point series | `era5_land/points/<NNNN>-<identity>/series_t2m_degc.nc` | `temperature_degc(station=26, valid_time=52608)`, UTC, 0 non-finite, −22.02 … +41.96 °C |
| manifest | `era5_land/points/<NNNN>-<identity>/extraction_manifest.json` | identity `2ceb6a49…`, operator `NEAREST`, six source hashes, referenced precipitation-bundle identity |

**Corrected 2026-08-20 (review round 2):** the table above used to show a single fixed path
(`era5_land/points/series_t2m_degc.nc`) — an earlier revision published t2m by swapping that one path
in place via a `.points.prev` backup, which could leave the canonical path briefly absent on a crash
between renames, or deadlock two concurrent publishers sharing the one backup name. t2m now publishes
the same way the precipitation bundle does: a fresh, per-run-unique `<NNNN>-<identity>` directory
(`allocate_published_dir`), discovered by the highest `NNNN` whose manifest validates
(`discover_t2m_bundle`) — see property 3 below.

**Three properties Plan 184 D14 depends on, measured not assumed:**

1. **The series is CELL-LEVEL and uncorrected.** No lapse correction is applied. D14's 6.5 °C/km
   correction from model orography down to station elevation, and the Pyramid `AT` check on it, are
   Plan 184's work — "if the check fails, widen the uncertainty" is an analysis behaviour, so a
   pre-corrected product would have to carry the validation with it.
2. **t2m and precipitation share the grid cell at all 26 stations** — verified, not assumed: identical
   `grid_i`/`grid_j`/`grid_lat`/`grid_lon`, and the `latitude`/`longitude` axes of the tp and t2m
   products are bit-identical (51 × 91). So the elevation mismatch already in M-A5's
   `station_grid_elevation.csv` **is** D14's lapse input; orography is not re-run.
3. **The reference is by IDENTITY, never by path — for BOTH bundles.** `points/` bundle directories
   carry a run-number prefix allocated per publish, so a path like `points/0006-<identity>` is a
   function of how many times the gated suite has run, not of the data. That was always true of the
   precipitation bundle t2m *references* (D6); as of 2026-08-20 it is also true of t2m's *own* bundle —
   an earlier revision published t2m to one fixed path, corrected above. **An identity is a LABEL, not
   a lookup key (`era5_extract_manifest` P3), and the same identity may cover DIFFERENT payloads — so
   do not resolve by globbing `*-<identity>` either.** Discovery is P2/P6's convention for both:
   the highest `NNNN` whose manifest validates (`_discover_precip_bundle` / `discover_t2m_bundle`).

**⚠️ Still missing for D14: a Pyramid `AT` loader.** `pyramid_loader.py` parses `RR` only — `AT` appears
in the module solely inside an error message. All six Pyramid RR stations (2,660–5,600 m) do carry `AT`,
so the data is there and the code is not. That work sits inside Plan 184, not Plan 191.

---

**✅ M-A6 IS COMPLETE — 2026-08-27 (Plan 184, T1–T6; PR #211 merged, PR #212 open).** Regenerate every
number below with one command:
`$ENV uv run python scripts/dhm_precip/ma6_run.py --out <dir>` (~40 s). Consumed identities:
precipitation `0214943312b0…`, t2m `2ceb6a49929c…`, both resolved as the highest `NNNN` whose manifest
validates. **Sign convention throughout: `gauge − ERA5`.**

#### The headline: the two magnitude estimands have OPPOSITE signs, consistently

Band means, JJAS, unweighted over member stations (D4a), each station's own `n` carried:

| band | stations | matched-hour mean difference | wet-hour bias (gauge-alone) | wet-hour bias (joint) |
|---|---|---|---|---|
| `< 700 m` | 9 | **−0.0866** mm/h | **+2.2197** mm/h | **+1.7490** mm/h |
| `700–2,000 m` | 9 | **−0.2469** | **+1.1530** | **+0.7400** |
| `2,000–3,000 m` | 5 | **−0.1804** | **+0.9181** | **+0.4813** |
| `≥ 3,000 m` | 3 | **−0.2361** | **+0.4301** | **+0.0756** |

Read the two columns together. Across **every** band, ERA5-Land is **wetter than the gauge averaged
over all retained hours** (matched-hour difference negative) yet **less intense than the gauge on the
hours the gauge calls wet** (wet-hour bias positive). That combination is what it looks like when
precipitation is spread across more hours at lower intensity.

**⛔ Do not read that as an attribution.** M-A6 cannot separate this from representativeness: a 0.1°
cell is ~110 km² and a point gauge is not its average, so a cell that rains lightly and often where the
gauge rains hard and rarely produces the same signature with no model error at all (D5 —
characterised, not decomposed). What M-A6 establishes is the **pattern and its size**, not its cause.

**The intensity deficit shrinks with elevation** — +2.22 mm/h below 700 m against +0.43 mm/h above
3,000 m, monotonic across all four bands and in both conditionings. The `≥ 3,000 m` band rests on
**three stations** (D4a), one of them Olangchunggola whose 03 UTC peak is unexplained (M-A10).

**Both wet-hour conditionings are reported** (D1 as amended). `joint` is lower than `gauge-alone` in
every band because it drops hours where ERA5 is dry. Their difference is **not** a decomposition — the
populations are nested (joint ⊆ gauge-alone), not orthogonal.

#### D4's calendar carve-out was deleted, and the measurement justifies it

Sub-freezing mass fraction, 1.5 °C at 6.5 °C/km, on the DJF/JJAS accumulated-difference subsets:

| station | elevation | DJF | JJAS |
|---|---|---|---|
| Syangboche | 3,700 m | **0.9596** (n=9,214) | 0.0000 |
| Humde | 3,401 m | **0.5158** (n=10,532) | 0.0000 |
| Olangchunggola | 3,119 m | **0.2663** (n=6,876) | 0.0000 |
| Lukla | 2,860 m | **0.0510** (n=6,248) | 0.0000 |

Monotonic in elevation, zero through the monsoon. The old rule withheld *all* high-altitude DJF
magnitudes; the measurement shows Syangboche's winter figure rests on 96 % mass the gauge cannot catch
while Lukla's, 840 m lower, rests on 5 % and is genuinely quantitative. One calendar rule would have
withheld both. The `≥ 3,000 m` DJF matched-hour difference (**+0.0665** mm/h) is published carrying its
own mean fraction of **0.5806** — annotated, not withheld, which is D4's whole design.

#### Representativeness, characterised

- **Within-cell pair (D8).** Kirtipur and Khumaltar, one cell (`33_53`), 4.33 km apart, over **47,377**
  commonly-retained hours: **7,758.6 mm vs 5,672.0 mm**, an accumulated difference of **+2,086.6 mm**.
  ERA5 returns one identical series for both. Own exposures 48,988 h and 48,804 h. **Descriptive only,
  no lower bound, n = 1 pair, one valley, one separation.**
- **Neighbouring-cell variability** (Chebyshev-8 stencil, period-total mm, population CV — a *declared*
  operationalisation chosen with the data in hand, not pre-registered): highest exactly at the
  high-mismatch stations — Olangchunggola **0.3123**, Humde **0.2570**, Lukla **0.2551**, Syangboche
  **0.2476** — against Rajbiraj **0.0216**.
- **Elevation mismatch** is a descriptive covariate, **datum-unreconciled**, with no number on the datum
  error (D7, Plan 174).

#### The D14 lapse check (Exit 8)

6.5 °C/km, never fitted. Hour-equalised, paired against Pyramid's own retained timestamps, 2020–2023:

| Pyramid station | elev | correction | discrepancy | n(paired) |
|---|---|---|---|---|
| AWS3 Lukla | 2,660 m | +7.82 °C | **+0.91** | 15,774 |
| AWS5 Namche | 3,570 m | +5.70 | **+1.10** | 19,985 |
| AWS2 Pheriche | 4,260 m | +4.31 | **+0.53** | 34,875 |
| AWS1 Pyramid | 5,035 m | +3.55 | **−1.22** | 35,013 |
| AWS4 Kala Patthar | 5,600 m | −0.12 | **−2.12** | 24,363 |

Discrepancies within ±1.2 °C over a 2,375 m transect, turning negative at the top. AWS1 and AWS4 share
a cell, giving an observed **+5.21 °C/km** on 24,357 common-retained hours — an observation, never a
candidate replacement rate (D14). The overlap is **2020–2023 only**; the rate is validated on four of
the six comparison years, which must be said beside any 2024–2025 figure.

#### Coverage and honesty conditions

- **D11's exclusion list is EMPTY** — worst JJAS retention is Lete (FNEP) at **0.8296** and the 0.50
  floor never binds. That is a measured result, not a skipped step, and the report says so.
- **A refusal renders.** Udayapur Gadhi has zero jointly-wet DJF hours and appears as `ABSENT` with its
  reason; its band reports 8 member stations rather than 9. A band silently shrinking would be the
  missing-not-at-random error this whole track exists to avoid.
- **Exit 2 holds structurally.** A magnitude cell is unconstructible without both its `n` and its mass
  fraction, verified by object identity on the shared subset — the two `n` values cannot disagree
  because there is only one subset.

#### Two defects that only real data exposed

Both were invisible to a passing unit suite and are recorded because the failure mode generalises.

1. **Negative mass.** `unclassifiable_mass_mm` was computed as `total − classifiable`, two float sums
   over the same column in different row orders; it went negative on **9 of 52** station-scale
   combinations (worst −9.09e-13 mm). Now summed directly over the unclassifiable rows. Synthetic test
   fixtures do not accumulate enough values to reproduce the drift.
2. **A non-reproducible report.** Running `ma6_run.py` four times on identical inputs gave **three
   different** values for one D12 categorical row. Two of Humde's monthly gauge buckets sum to exactly
   the 0.2 mm wet threshold, and IEEE-754 summation order — which Polars' multi-threaded group-by does
   not fix — put them one ULP either side, flipping wet/dry between runs. Bucket totals are now rounded
   to 9 decimals (far below the gauge's 0.1 mm resolution, far above float noise) before the wet call.
   Five consecutive runs are now byte-identical. The fix **corrected three rows** as well as stabilising
   them — Humde MONTHLY (now the internally consistent 1.000 / 0.107 / 0.893, where FAR = 1 − CSI),
   Olangchunggola DAILY (FAR 0.230 → 0.229, CSI 0.662 → 0.663) and Syangboche DAILY (POD 0.976 → 0.975,
   CSI 0.599 → 0.598). Syangboche's scores got *worse*: the reclassified bucket is a genuine miss, so the
   corrected number is less flattering and more truthful. Nothing else in the 588-line report moved.
   **Found by running the same command twice and diffing, not by any test.**

### M-A7 · Temporal characterisation
**Depends: M-A2, M-A3.** Parallel to M-A5/M-A6.

Per-station wet-hour intensity distributions and diurnal structure, 0.2 mm/h harmonised floor for
frequency statistics (vision D5). Body and tail transferability reported separately with bootstrap
uncertainty, per rule 2.

**Stratify the diurnal analysis by elevation, not only by station** (literature grounding, vision
2026-08-13). Three independent sources put the monsoon diurnal peak earlier at higher elevation —
Lesser Himalaya ~2,000–2,200 m afternoon–evening against southern margin ~500–700 m early-morning —
and our own exploratory check reproduces it: within Group B alone (one reporting population, 67–2,147 m,
190 pairs) profile similarity correlates with **elevation difference at r = −0.486** and with
**horizontal distance at r = −0.027**. Elevation predicts diurnal regime; proximity does not.

**Exit:** distributions and profiles with uncertainty, and a quantified statement of what transfers
between stations — **reported by elevation band as well as per station**. *Marginals plus mean profiles
inform but do not suffice to design a disaggregator — that needs temporal dependence structure, which
is a Phase-2 question.*

---

**✅ M-A7 IS COMPLETE — 2026-08-27 (Plan 193, T1–T4).** One command regenerates every number below:
`$ENV uv run python scripts/dhm_precip/ma7_run.py --out <dir>` (~85 s, 3,309-line report). The
bootstrap seed is fixed at `REPORT_SEED = 193` and printed in the report; three consecutive runs hash
identically. Computed on Plan 184 T1's M-A3-masked gauge series (D1/D10) — **no second masked
series exists**.

#### The headline: tail behaviour transfers everywhere EXCEPT the Lesser Himalaya transition

Leave-one-out tail prediction error (D3, pinned to `stats_precision.leave_one_out_tail_prediction_error`
— predict each held-out station's q99 as `median_i × pooled_ratio(excluding i)`), JJAS:

| elevation band | stations | median abs error | within 25 % |
|---|---|---|---|
| `< 700 m` | 9 | 0.122 | 0.889 |
| `700–2,000 m` | 9 | 0.133 | **1.000** |
| **`2,000–3,000 m`** | 5 | **0.460** | **0.000** |
| `≥ 3,000 m` | 3 | 0.088 | **1.000** |

**Zero of five stations in the 2,000–3,000 m band predict within 25 %**, while the bands above and
below it transfer perfectly. The cause is visible in the ratio the method predicts:

| station | elevation | q50 | q99 | **q99/q50** |
|---|---|---|---|---|
| Aiselukhark | 2,064 m | 0.60 | 19.20 | **32.00** |
| Nagarkot | 2,147 m | 0.80 | 25.08 | **31.35** |
| Lete (FNEP) | 2,490 m | 0.82 | 7.84 | **9.56** |
| Ghorepani | 2,742 m | 0.90 | 14.59 | 16.21 |
| Lukla Airport | 2,860 m | 1.00 | 12.02 | 12.02 |

A **3.3× spread** in tail-to-body ratio inside one band. This is heterogeneity, not sampling noise —
the band is the Lesser Himalaya transition zone, where the literature already places a regime change.

#### Both cuts, side by side — and the attribution DECLINED

| reporting-resolution group | stations | median abs error | within 25 % |
|---|---|---|---|
| A (0.01 mm) | 6 | 0.182 | 0.667 |
| B (0.2 mm) | 20 | 0.062 | 0.800 |

Group A transfers ~3× worse than Group B. ⛔ **M-A7 declines to attribute** (D6): Group A is
*simultaneously* the 0.01 mm subset and the high-altitude subset, and bounding that confound is
**M-A8's exit**. The refusal is carried as a checked field on the result type, not as prose a reader
can drop when quoting the number.

**Checked, not a defect:** group membership is inferred on the UNMASKED `on_grid` population, which
looked like a D1 conflict — not idle on a track where M-A10 traced Lukla's anomaly to sentinels
normalised over unmasked data. Measured both ways: **zero of 26 stations change group**, split stays
20/6, though the mask removes 47 % of rows (1,925,073 → 1,021,316).

#### Diurnal structure — the elevation hypothesis is HALF confirmed

JJAS station-equal peak hour by band (NPT = UTC + 5:45):

| band | peak (UTC) | peak (NPT) | resampled arc | `n_season_years` |
|---|---|---|---|---|
| `< 700 m` | 23 | **≈ 04:45** | 6.0 h | 6 · adequate |
| `700–2,000 m` | 19 | ≈ 00:45 | — | 6 · adequate |
| `2,000–3,000 m` | 19 | ≈ 00:45 | — | 6 · adequate |
| `≥ 3,000 m` | 19 | ≈ 00:45 | 11.0 h | 5 · adequate |

The **southern-margin early-morning peak reproduces**. But the three upper bands do **not** peak
afternoon–evening as the cited literature predicts — they sit together just after midnight, ~4 h
earlier — and **there is no gradient among them**. That is a step change at the southern margin, not
the monotonic shift the exploratory `r = −0.486` implied. The `< 700 m` band's pooled peak is 21 UTC
against 23 station-equal, so the lowest band is itself operator-sensitive.

⚠️ **The arc is NOT a confidence interval.** `circular_range_hours` is the smallest arc containing
*every* resampled value, driven by which hours occur at all rather than how often: the `≥ 3,000 m`
band's 11 h is set by hour 3 appearing in **151 of 2,000** resamples — Olangchunggola's open anomaly.
Read it as "resampled peak hours spanned this arc".

#### Coverage, adequacy and refusals

- **All four seasons for every station, none omitted** (D8 as corrected — "report where retention
  supports them" was itself the completeness threshold the same decision forbids). Syangboche, for
  example: MAM 464/10,735 wet hours · JJAS 2,770/14,052 · ON 228/6,947 · **DJF 110/9,214** — thin, and
  reported.
- **Four of 26 stations are below the ≥5 season-year bar** (Udayapur Gadhi 2; Lete, Num,
  Olangchunggola 4). They render with `n_season_years` and an explicit inadequate marker; **never
  filtered**, which would be the filter-on-retention error D13 forbids. All four *bands* are adequate
  (6/6/6/5) because band resampling draws from the **union** of member season-years — an intersection
  quantified a population the point estimate does not come from, and collapsed `700–2,000 m` to one
  common year.
- **Olangchunggola's 03 UTC peak is recorded as OPEN** (D7) — reported, not adjudicated.
- Cross-check with M-A6: Syangboche JJAS mass 3,487.9 mm and DJF wet-hour count 110 match its report
  exactly, so both milestones read the same masked series.

#### Three defects that only measurement exposed

None was caught by 1,152 passing tests; all three were found by asking what the numbers were.

1. **A 21-hour confidence interval.** T1's peak-hour bootstrap used a LINEAR percentile on a CIRCULAR
   quantity. The `< 700 m` resamples cluster tightly across midnight, so the interval read `[0.0, 21.0]`
   where the circular range is **6.0 h** — on the band carrying the headline. The plan caused it: D9
   pinned "percentile 2.5/97.5" without distinguishing linear from circular, and `coloc_bootstrap`,
   the precedent D9 names, is *titled* "circular bootstrap on the diurnal peak hour".
2. **A one-year bootstrap.** Band resampling used the intersection of member season-years, making all
   four bands inadequate and reducing `700–2,000 m` to a single common year. D9 pinned the adequacy
   *bar* but not the resampling *population*.
3. **A non-reproducible report.** Hourly means are sums-over-count and Polars' multi-threaded group-by
   fixes no summation order, so two runs rendered `Ilam Tea Estate` hour 05 as 0.088 and 0.087. Both
   aggregation sites now round to 9 decimals — the same fix and reason as M-A6's bucket totals.
   **It passed a two-run diff twice before being caught**: once by the implementer, once by M-A6's
   equivalent check. An intermittent nondeterminism passes sampling; the mechanism must be argued.

### ⭐ M-A10 · Co-located gauge-vs-gauge adjudication (Pyramid network) — NEW 2026-08-18
**Depends: M-A3.** *(Needs the QC mask; does NOT need ERA5-Land, so it can run in parallel with M-A5/M-A6.)*

**The first genuine gauge-vs-gauge comparison this track has ever had.** The Pyramid Meteorological
Network (Salerno et al. 2025, ESSD 17, 4293; Zenodo `10.5281/zenodo.15211352`, CC BY 4.0, no login)
publishes **hourly in-situ AWS** precipitation in the Khumbu, **independent of both ERA5-Land and DHM**,
with two stations effectively co-located with two of our four problem high-altitude stations:
**AWS3 Lukla ≈1.4 km from DHM Lukla Airport**, **AWS5 Namche ≈1.9 km from Syangboche Airport**.

**Why it earns its own milestone rather than folding into M-A6/M-A7:** M-A6 compares a gauge against a
*model field* and M-A7 characterises *our* timing — neither can answer "is our gauge wrong?", because
no satellite or reanalysis product can adjudicate a gauge. This can.

**Scope — REVISED by Plan 182 (three slim Codex review rounds, 2026-08-18): gauge-vs-gauge only, NOT a
three-way comparison.** ERA5-Land dropped out of this milestone's scope entirely (it belongs to M-A6);
the two co-located pairs, **DHM vs Pyramid only**, over **two windows reported separately** — the JJAS
overlap (2020–2023; Lukla's is only 2021–2023) and each station's full JJAS record (the climatological
comparison, primary — the overlap is too thin, 3 season-years at Lukla, to stand alone; see the D5
adequacy gate below). Both profiles reported **in NPT** (Pyramid's own timebase), with the DHM-side
UTC→NPT conversion and the Pyramid README's unstated period convention together declared as an
**alignment uncertainty of ±1.75h** — no phase claim is made finer than that.

**The hypothesis it exists to settle** *(and its OUTCOME — see the first-run record at the end of this
milestone)*: our DHM Lukla appeared to peak at **02 UTC ≡ 07:45 NPT**, inside Pyramid Lukla's diurnal
**minimum** (normalised 0.29/0.28 at 07–08, peaks at 22–00 NPT) — near anti-phase from 1.4 km away.
Combined with Group A's 0.01 mm resolution and sub-0.1 mm noise floor, the hypothesis (**H1**) was that
**the Group A high-altitude diurnal signal is noise-floor contamination, not physics** (**H0**).

**OUTCOME 2026-08-18: the premise itself was false.** The 02 UTC figure was a sentinel artefact of
normalising unmasked data; masked, Lukla peaks 21 NPT and AGREES with Pyramid. H1 is not supported —
the peak is immovable across the ablation ladder — and the finding the vision carried as UNRESOLVED
since 2026-08-13 is retired for Lukla, though it stands at Olangchunggola for a different reason.

**⚠️ The test as first written could not identify H1 — corrected before implementation (Plan 182).** A
negative control (a threshold ladder applied to Pyramid) was found to be **vacuous**: the smallest
positive Pyramid JJAS value is **exactly 0.2 mm and nothing falls below it** (LSI-Lastem tipping
buckets), so every rung of a 0.0/0.1/0.2 ladder selects the identical population — the ladder is a
structural no-op on Pyramid and could never have detected anything. Removed.

**⛔ CORRECTED 2026-08-21 — this passage previously justified that with "every positive Pyramid JJAS
value is an exact multiple of 0.2 mm". That is FALSE**, and Plan 182 had already said so on 2026-08-18
(`182:77`) without the correction reaching here. Measured across all six RR stations: **2,861 of 47,967
positive JJAS values (6.0 %) are not multiples of 0.2** — per station 0.9 % (AWS0) to 9.6 % (AWS2) —
forming a 0.24-family plus outliers, so Pyramid's effective resolution is **not uniform across the
record**. **The conclusion is unaffected**: D7's argument rests on the FLOOR, not on the grid, and the
floor holds. Surfaced by an external review of the data bundle prepared for the student team. In its place:
**(1)** a **matched-resolution comparison**, DHM thresholded at ≥0.2 mm against Pyramid at its native
resolution (the only apples-to-apples phase comparison available, since Pyramid cannot represent
anything finer); **(2)** a **DHM-only threshold-ladder ablation** (all values / ≥0.1 mm / ≥0.2 mm,
**zeroing, never dropping**, so a common scalar shift can never move the peak on its own — only a
genuinely hour-concentrated contribution can); **(3)** the two read together under **D9's ordered
verdict gates** (adequacy → matched-resolution agreement → ablation movement), evaluated **per station,
then synthesised** — disagreement between Lukla and Syangboche is **INDETERMINATE for the group,
reported as the finding, never averaged**. INDETERMINATE is a permitted, publishable outcome that
**blocks** the M-A7 correction rather than licensing it.

**Wet-hour fraction is reported ONLY on the common-retained-timestamp population** (D3): masking one
side only (M-A3 on DHM, a physical range check on Pyramid) preferentially removes DRY hours and
manufactures exactly the kind of gap this milestone exists to detect, so the two series are paired
hour-by-hour and compared over their intersection, with each side's own retention reported separately
so the pairing loss stays visible.

**⛔ NOT a correction source, and this is binding.** Pyramid's gauges are **explicitly unheated** — the
paper names that as its main weakness — so they undercatch snow in the same direction as DHM's.
Correcting toward them would substitute one undercatching gauge for another *while appearing
authoritative*; and fitting model forcing downward toward either gauge network injects a **dry bias into
a flood-forecasting system**. **Referee on SHAPE and TIMING only, where undercatch largely cancels;
never on magnitude.** The paper's "≤20 % snow underestimate" is inherited from Salerno et al. (2015),
not re-measured — it is not a transfer function. Vision D6/D9 stand, reinforced. **A genuine
micro-climatic or wind-driven catch-efficiency difference between the two networks remains a live
alternative this test cannot exclude** (normalisation cancels only hour-independent undercatch, and
mountain wind is strongly diurnal) — the verdict is adjudicated against H1 AND this alternative, never
against H0 alone.

**Exit:** the D7 threshold ladder (the primary result) with the peak hour at each rung; normalised JJAS
diurnal profiles for both pairs, both windows, `n` beside each; the paired wet-hour fraction; a circular
bootstrap spread on the peak hour by monsoon season (D5, with a small-sample rule — fewer than 5
season-years cannot on its own establish adequacy, however narrow the spread); per-station verdicts and
a synthesis under D9's pre-declared circular thresholds; a stated verdict on H1 phrased no finer than
±2h; and, if H1 is supported, an explicit list of affected vision/M-A1 claims filed as a correction for
M-A7 to apply. **No magnitude comparison anywhere in the output.**

**Data handling:** CC BY 4.0 permits redistribution *with attribution*, but **M-D1's bar on third-party
data in this public repo stands regardless** — the files live in `data/` alongside the DHM workbook,
never committed. Attribution is required on any published result.

**Library + runner CODE COMPLETE 2026-08-18, hardened 2026-08-18 (Plan 182 fixer round — a Codex
diff-review pass found 4 blockers + 5 majors in the first cut, all resolved in place):** the analysis
library — `scripts/dhm_precip/circular.py` (circular hour arithmetic, including D9's "toward" as a
circular-distance reduction, never a signed direction), `scripts/dhm_precip/coloc_verdict.py` (the D9
ordered-gate verdict rule — now including D5's bootstrap-spread and insufficient-disjoint-data gates —
per-station + synthesis over EXACTLY the two registered stations), `scripts/dhm_precip/stats_coloc.py`
(D7.2 zeroing ablation, normalised profile, D3 common-retained-timestamp pairing, paired wet-hour
fraction, and the D2 UTC->NPT reconciliation — `dhm_utc_to_npt`, applied to every DHM frame before
anything else touches it), `scripts/dhm_precip/coloc_bootstrap.py` (D5 circular bootstrap +
small-sample adequacy rule), `scripts/dhm_precip/coloc_pairs.py` (the two-pair registry, now also
carrying each pair's own D5a overlap-year range) and `scripts/dhm_precip/pyramid_loader.py` (Lvl1 CSV
loader with the D4 physical-range boundary: finite, in-range, duplicate-timestamp and timestamp-dtype
validation), composed by `scripts/dhm_precip/coloc_adjudication.py` (pairs FIRST, then computes the
matched-resolution ladder/Pyramid peak from the SAME paired population — never independently) and
`scripts/dhm_precip/coloc_run.py` (the runner: both pairs, both windows, full profile tables, the exact
two-station synthesis, and a Markdown report writer).

**What the fixer round corrected** (`docs/plans/182-co-located-gauge-adjudication.md` review, 4
blockers + 5 majors): (1) the declared UTC->NPT offset was dead configuration — DHM (UTC) and Pyramid
(NPT) hours were compared raw; (2) the D5 disjoint-period stationarity split at year 2020 was
structurally broken (the real DHM source record only starts 2020-01-01 — `pre` was always empty and
`peak_hour` raised) — the default split moved to 2023 and insufficient-disjoint-data now maps to
INDETERMINATE rather than crashing; (3) the D5 bootstrap peak-hour spread was computed but never gated
on; (4) D9's phase peaks were computed independently per side before D3 pairing, letting hour-dependent
masking manufacture a phase difference — now paired first; (5) `synthesize_verdict` accepted a
duplicated or unregistered station; (6) the Pyramid loader had no physical-range boundary. See that
plan doc's fixer-round changelog for the full list including minors.

**Second fixer round, 2026-08-18 — the deliverable was UNREACHABLE (3 blockers + 4 majors).** A
diff-review pass found that gate 0's <5-season adequacy rule was evaluated on the OVERLAP window, whose
real registry bounds are 3 (Lukla) and 4 (Syangboche) season-years — so against real data **both
stations could only ever return INDETERMINATE**, and every decisive-verdict test had hidden it by
feeding a synthetic 2020-2024 window that bypassed `COLOCATED_PAIRS` entirely. Resolved by the plan's
**D11**: the **FULL RECORD is the adjudicated comparison** (DHM 2020-2025 x Pyramid 2005/2002-2023, both
clearing the 5-season floor) and the **overlap is corroboration** that never gates; and by **D12**: the
disjoint-period stationarity split is **PYRAMID's** (pre-2020 vs 2020+), since DHM has no pre-2020 data
and the DHM-side split was therefore vacuous — it is still computed, but reported as additional evidence
only. Also fixed: `moved_toward_pyramid` was computed then ignored (a 4h ablation movement AWAY from
Pyramid could fire H1_SUPPORTED); the bootstrap resampled an unpaired population while the peak came
from the paired one; all-zero/empty populations raised out of the middle of an adjudication instead of
becoming INDETERMINATE (`ADEQUACY_INSUFFICIENT_SIGNAL` / `ADEQUACY_INSUFFICIENT_COMMON_DATA`); Pyramid
retention was the whole file's count rather than the window's; and `_write_report` emitted only peak
scalars — it now writes the full profile tables for both networks in both windows (hourly `n`, no
magnitudes), per-window retention, D2's ±1.75h uncertainty, D8's micro-climate/wind alternative, D7.3's
drizzle confound, the CC BY 4.0 attribution and the affected-claims list when H1 is supported. Every
runner test now drives the pipeline through the **real registry bounds** and asserts a decisive verdict
is REACHABLE.

**Pyramid schema — RESOLVED 2026-08-18.** `pyramid_loader.py`'s format assumptions were wrong on every
count except `RR`: the real Zenodo Lvl1 files are **semicolon**-delimited with **CR-only** line endings
and carry **no `TIMESTAMP` column** (header `year;month;day;hour;AT;RR;AP;RH;WS;WD`, an empty field for
each missing reading). The loader now reads that format and is covered by real-file tests gated on
`DHM_PRECIP_PYRAMID_DIR`, which lock the measured JJAS populations (AWS3 Lukla 21,567 retained / 7,133
positive; AWS5 Namche 33,180 / 9,280) and the 0.2 mm smallest positive reading D7's matched-resolution
design rests on. (Caveat measured at the same time: 0.2 mm is the floor and dominant quantum, but ~4% of
positive JJAS hours are quantised at 0.24 mm — a different bucket/logger era — so positives are NOT all
multiples of 0.2.)

**✅ FIRST REAL RUN 2026-08-18 — the wiring executed end-to-end and the milestone has its verdict.**
`coloc_run.py`'s `main()` ran against the real DHM workbook and the real Pyramid Lvl1 files (both pairs,
both windows); the report is written to `data/dhm_precip/coloc_ma10/coloc_adjudication.md` (untracked,
per M-D1). This closes the previously tracked residual risk that the production wiring — `loader`,
`views`, `normalise`, `observations`, `qc_mask`, `pyramid_loader` — had never been executed against real
data. Two dtype defects surfaced only at that seam and were fixed: the mask frame's timestamp was
hard-coded to `datetime[μs]` while the pinned workbook yields `datetime[ms]` (the anti-join raised
`SchemaError` and the report was never written), and `stats_coloc.py` needed the same reconciliation for
Pyramid's `read_csv`-derived timestamps. Both had been invisible to the synthetic-fixture tests.

**Synthesis: INDETERMINATE.** Both pairs stop at D9's FIRST gate — D5 adequacy
(`adequacy_bootstrap_spread_too_wide`): 13.00 h circular spread at Lukla, 3.00 h at Syangboche, against
the pre-declared 2.0 h threshold. The gate is not overridden here.

| pair | DHM ladder 0.0 / 0.1 / 0.2 mm (NPT) | Pyramid peak | D5 spread |
|---|---|---|---|
| DHM Lukla vs AWS3 Lukla | 22 / 22 / 22 | 23 | 13.00 h |
| Syangboche vs AWS5 Namche | 1 / 1 / 23 | 23 | 3.00 h |

**H1 is NOT supported by the evidence, though the verdict is formally INDETERMINATE.** The peak is
nocturnal at EVERY ablation rung — it does not move when the noise floor is stripped, which is the
signature H1 predicted — and at both pairs it lands within 0–2 h of an independent instrument 1.4 /
1.9 km away. Syangboche's profile is unambiguous (normalised 2.20 / 2.14 / 2.21 at 23–01, trough 0.17 at
hour 11). What stops the gate is **inter-annual variability, not a contradiction**: Lukla's five season
peaks are 23, 10, 22, 17, 21 NPT — three nocturnal, two dissenting (2022, 2024) — and 13.00 h is simply
the arc spanning all five; its central 90 % arc is 6.0 h, modal hour 21. Syangboche's five are 22, 21,
00, 00, 22, a 3 h arc, all nocturnal.

**⭐ The Lukla 02 UTC anomaly is RESOLVED — a QC artefact, not physics and not the noise floor.** It is
**6 sentinel values of −9999999** at 02 UTC (5) and 03 UTC (1) in JJAS, combined with normalising an
UNMASKED profile: those sentinels drive the grand mean to **−2,499,750 mm**, so the normalisation FLIPS
SIGN and the most-contaminated hour reports as the largest positive "peak" (+20 normalised at 02 UTC)
while real rain at 16 UTC (+472 mm) reports as −0.00. Under the M-A3 mask Lukla peaks at **16 UTC ≡
21 NPT**. The anomaly was an artefact of computing a normalised profile on unmasked data — precisely
what M-A3's mask exists to prevent, and a demonstration of its value.

**Olangchunggola's 03 UTC peak is NOT explained by this and REMAINS OPEN.** It carries **zero** JJAS
sentinels (Lukla is the only station in the workbook that does), and its peak is immovable across the
ablation ladder (03 UTC ≡ 08 NPT at all of 0.0 / 0.1 / 0.2 mm) — neither a sentinel artefact nor
noise-floor contamination. It has no co-located Pyramid station, so M-A10 cannot adjudicate it.

**✅ RESOLVED — the D5 threshold was re-examined post-run and deliberately LEFT ALONE.** The 2.0 h bar
looked miscalibrated for a 5-season record (it is the only thing standing between Syangboche and a
decisive verdict), but it turns out to be **derived, not chosen**: `params.py`'s D9 coherence validator
enforces `ablation_refuted_max >= bootstrap_adequate_max`, so the bootstrap bar is capped by the 2 h
refute boundary — itself pinned above D2's 1.75 h alignment floor and below the 4 h support boundary.
Both 3 h and 4 h are rejected by the validator, and the invariant is correct (a movement cannot be
"small enough to refute H1" if peak-hour uncertainty exceeds the movement threshold). **The binding
limitation is the DHM record length**: the bootstrap resamples DHM season-years and DHM has exactly 5
complete JJAS seasons, so Lukla cannot clear any coherent bar until that record lengthens. The one real
lever is pinning down Pyramid's unstated period convention (D2's ±1 h component) — a question for the
Pyramid authors, not a parameter change. See Plan 182 D9 for the full record of what was rejected.

### M-A8 · Elevation and regime structure
**Depends: M-D2 (elevation), M-A6, M-A7.**

Elevation dependence of bias and of intensity/diurnal structure. **Must explicitly bound the
reporting-precision/altitude confound** — Group A is simultaneously the 0.01 mm subset and the
high-altitude subset, so no effect may be attributed to one rather than the other.

**Exit:** elevation relationships with the confound bounded, or a statement that the sample cannot
separate them.


**⭐ NEWLY ENABLED 2026-08-19 (grill-me) — a rain-phase precipitation gradient from the PYRAMID TRANSECT.**
The owner wants a precipitation lapse rate for elevation-band forcing correction. **ERA5-Land structurally
cannot supply one**: per Plan 184 D7 its `total_precipitation` is interpolated from ERA5 and never sees the
0.1° orography, so any gradient fitted to it is the parent field smeared onto a finer grid, not a physical
elevation response. It must come from gauges or from a convection-permitting product (OD-10).

**The transect is real and VERIFIED on disk 2026-08-19** — the Zenodo filenames encode elevation:

| station | elev (m) | RR hours | wet hours |
|---|---:|---:|---:|
| AWS3 Lukla | 2,660 | 88,794 | 10,355 |
| AWS5 Namche | 3,570 | 111,061 | 11,958 |
| AWS2 Pheriche | 4,260 | 165,680 | 14,418 |
| AWS0 | 5,035 | 40,351 | 5,067 |
| AWS1 | 5,035 | 184,690 | 14,663 |
| AWS4 | 5,600 | 72,880 | 3,400 |

**Precipitation stops at 5,600 m**: `AWSSC_Z7986` (South Col) and `CNG_SNP_Z5700` carry **no RR column** at
all — T/RH/wind/pressure only. All six RR stations DO carry `AT`, so the D4/D14 temperature screening is
available at every one.

**⛔ Reconciling this with M-A10's binding rule.** M-A10 states Pyramid is "NOT a correction source …
referee on SHAPE and TIMING only, where undercatch largely cancels; never on magnitude." A lapse rate IS a
magnitude claim, and Pyramid's gauges are **unheated**, so undercatch GROWS with elevation — fit a gradient
up this transect raw and you measure the undercatch profile. **The rain-only screening is what makes it
legitimate**, because it is the condition under which "undercatch largely cancels" is actually true.
⇒ **Scope: a JJAS rain-phase gradient, bounded at the rain line, never extrapolated above it.** The
snow-dominated high basins — where flood interest sits — remain out of reach by this route.

**The noise floor is now MEASURED, not assumed.** AWS0 and AWS1 sit at the SAME elevation (5,035 m), giving
a direct estimate of siting/exposure scatter. On 12,819 common hours (2000–2004): wet-hour COUNT ratio
**1.01** (they agree almost exactly on *when*), accumulated AMOUNT ratio **1.31**, and rain-only
(both AT ≥ 1.5 °C) **1.18**. Two readings: the screening removes ~40 % of the same-elevation discrepancy —
empirical support for D4/D14 — and **any vertical gradient must exceed ~18 % to be distinguishable from
siting alone.** Khumbu precipitation declines far more than that over 2,660→5,600 m, so a gradient should
be resolvable. Caveats: n=1 pair, and the common window is older than the rest of the record.
*(An earlier note claiming a 1.6× same-elevation disagreement was a RECORD-LENGTH artefact — corrected.)*

---

**✅ M-A8 IS COMPLETE — 2026-08-28 (Plan 205, T1–T3).** Regenerate with
`$ENV uv run python scripts/dhm_precip/ma8_run.py --out <dir>`; two runs hash identically. M-A8
**calls** M-A6's and M-A7's own constructors and re-implements no estimand (D2), so every number below
carries the companions it was published with.

#### The confound: half of it is unidentifiable, and that is the result

| band | Group A (0.01 mm) | Group B (0.2 mm) |
|---|---|---|
| `< 700 m` | 0 | 9 |
| `700–2,000 m` | 0 | 9 |
| **`2,000–3,000 m`** | **3** | **2** |
| `≥ 3,000 m` | 3 | 0 |

**Group A spans 2,490–3,700 m; Group B spans 67–2,147 m; the ranges do not overlap.** So "Group A
differs from Group B" and "high differs from low" are the same sentence in this sample: the
**between-group contrast is unidentified**, and no covariate adjustment over these 26 stations can
separate them. ⛔ A whole-sample fit carrying both terms would report an assumption, not a measurement.

**But elevation IS analysable within each group at fixed reporting resolution** — and that is most of
this milestone's scope. Within Group B (20 stations, 2,080 m of relief), against elevation:

| assembled quantity | Pearson r | n |
|---|---|---|
| M-A6 matched-hour mean difference (JJAS) | **−0.3863** | 20 |
| M-A7 q99 wet-hour intensity (JJAS) | **−0.6970** | 20 |

**Extreme rain intensity declines strongly with elevation inside a single instrument population.**
That is the cleanest elevation signal this track has, precisely because reporting resolution is held
constant. ⛔ It is a **relationship, not an effect**: exposure, siting, catchment character and monsoon
dynamics all co-vary with elevation across those 2,080 m. Group A (6 stations) is reported **per
station**, not fitted — it occupies two D5a bands of three stations each.

**The one band containing both groups is the one M-A7's transferability failed on**, and its q99/q50
ratios split exactly on group — B at 31.35 and 32.00, A at 9.56, 12.02 and 16.21. That is the sample's
only observation bearing on the confound. It is consistent with a resolution effect and does not settle
it, since elevation still varies across the band. n = 5.

#### The Pyramid transect: an apparent gradient, and why it is not a lapse rate

**Apparent rain-phase gradient, uncorrected for wind catch**, five stations, fitted
**2009-01-01 → 2018-05-09**:

| rain screen | gradient | 95 % interval |
|---|---|---|
| `AT ≥ 0 °C` | −44.85 %/km | [−67.30, −7.01] |
| **`AT ≥ 1.5 °C`** | **−52.49 %/km** | [−67.53, −30.48] |
| `AT ≥ 2 °C` | −55.10 %/km | [−69.62, −33.64] |
| `AT ≥ 4 °C` | −72.52 %/km | [−89.10, −30.71] |

⛔ **This is an UPPER BOUND IN MAGNITUDE on any true decline, never an estimate of precipitation
decline.** Pyramid's gauges are unheated; catch efficiency falls with wind and wind exposure rises up a
transect, so observed precipitation declines faster than true. The true decline is no steeper than the
apparent one and **could in principle be nil**, with the whole apparent decline being catch.

**Two readings that the fitted slope alone would hide.** Per-station means at the 1.5 °C screen:

| station | elevation | mm/h (hour-equalised) | n rain hours |
|---|---|---|---|
| AWS3 Lukla | 2,660 m | 0.7301 | 5,060 |
| AWS5 Namche | 3,570 m | 0.2886 | 8,088 |
| AWS2 Pheriche | 4,260 m | 0.1282 | 19,137 |
| AWS1 Pyramid | 5,035 m | **0.1324** | 16,772 |
| AWS4 Kala Patthar | 5,600 m | 0.0711 | 3,714 |

1. **The transect is not monotonic — it plateaus above ~4,000 m.** AWS1 at 5,035 m is marginally wetter
   than AWS2 at 4,260 m. A single log-linear slope over 2,940 m averages a steep lower section
   (0.73 → 0.29 over 910 m, Lukla→Namche) with a nearly flat upper one.
2. **The steeper 4 °C gradient is a SAMPLE COLLAPSE, not a cleaner signal.** AWS4's rain-hour count runs
   **9,680 → 3,714 → 2,475 → 457** as the screen tightens, its mean falling to 0.0096. At 5,600 m, hours
   warmer than 4 °C are rare and unrepresentative. ⛔ Do not read "the gradient survives the unambiguous
   rain screen" as confirmation — the top station lost 95 % of its data. This is visible only because
   the report carries `n` per station per screen.

**Why the confound cannot be tested away here.** A low-wind stratification was proposed and cut in
review: low wind does not select the same precipitation under better catch, it selects **different
precipitation** (stratiform rather than convective) with its own elevation response, so neither
"flattens ⇒ catch" nor "holds ⇒ precipitation" would follow. Wind is in the same files and cheap to
load; the obstacle is identification, not ingestion.

**Same-elevation reference.** AWS0 and AWS1 both at 5,035 m, 12,819 common hours: wet-hour count ratio
**1.0111** (they agree on *when*), rain-screened amount ratio **1.1786**. ⛔ An **observed
discrepancy, NOT a resolvability floor** — a single realized pair bounds nothing, and this track already
withdrew the identical inference (Plan 184 D8).

#### ⛔ What M-A8 does NOT deliver

**A precipitation lapse rate usable for elevation-band forcing correction.** The milestone was enabled
partly to obtain one. This route cannot: the gradient is confounded with the wind-catch profile, the
confound is not identifiable from these data, and the honest output is an upper bound that admits zero.
ERA5-Land cannot supply a competing estimate either — per Plan 184 D7 its `total_precipitation` is
interpolated from ERA5 and never sees the 0.1° orography. **A usable lapse rate needs the
convection-permitting route (OD-10), not this one.**

**A correction to P1, measured during implementation.** An earlier revision of the plan said the five
fit stations overlap 2020–2023. **AWS4 — the top of the transect at 5,600 m — stops reporting `RR` on
2018-05-09**; its `AT` does continue to 2023, which is why M-A6's D14 note about a 2020–2023 overlap
held for temperature and does not transfer to precipitation. Keeping AWS4 preserves the full 2,940 m
transect at the cost of 2018–2023 for the other four; that trade was taken deliberately and the fit
period is stated wherever the gradient appears.

### M-A9 · Synthesis, corroboration, Phase-2 decision
**Depends: M-A6, M-A7, M-A8.**

Consolidate. **Compare against the students' independent findings where available** — agreement
strengthens both, disagreement is itself a finding worth chasing. Their absence does not block this
milestone. State what would have to be true for each Phase-2 option to be viable and whether the
evidence supports it, including "no operational use".

**Exit:** written recommendation; owner decision.

### M-A11 · IFS diurnal timing re-evaluation (Plan 216)
**Depends: M-A6, M-A7, M-A8, M-A9.**

M-A9's Use 1 flagged that its ERA5-Land diurnal-timing measurement was of a REANALYSIS whose
convection scheme is frozen at a 2016-era IFS cycle — not the operational IFS forecast SAPPHIRE
actually flies. M-A11 re-measures the same phase question against ECMWF IFS control-forecast `tp`
(TIGGE, via ECDS — the only source overlapping the 2020-2025 gauge record), lead-stratified (D+1 to
D+3), reusing M-A6's own phase estimator unmodified. Screening only — no correction, no magnitude
claim, no operational dependency (a measured ~48 h TIGGE embargo rules that out for any centre).

**✅ COMPLETE 2026-08-29 — ⚠️ MAGNITUDE SUPERSEDED 2026-08-31 by Plan 220.** IFS shares the
mid/high-elevation-band error's DIRECTION. ⛔ The single-season magnitude claim ("a third to a half of
ERA5-Land's") **does not survive six seasons**: the mid band spans ~9 h across 2020-2025 on the
preregistered branch, with JJAS 2025 near the middle by chance. **There is no single IFS displacement to
calibrate against**, so nothing here sizes a correction. The low band is unresolved from zero at every lead **in JJAS 2025** —
⚠️ across six seasons it crosses the ±3 h bound in **2023 (every lead)** and at D+1 in **2020**.
**NO-GO on the originally proposed ~12 h correction as designed;
GO on further MEASUREMENT (finer than 6-hourly, more stations, branch-robust aggregation) — ⛔ NOT on constructing a correction.** Full
measurement: `docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md`. Code:
`scripts/dhm_precip/tigge_ifs.py` (T1 retrieval/extraction), `scripts/dhm_precip/
tigge_gauge_timing.py` (T2 comparison), `scripts/dhm_precip/diurnal_phase.py` (D5's shared,
TRACKED phase estimator, reused by both this screen and `era5_gauge_timing_figure.py` — a review
round found the first draft (a) dynamically imported that estimator from a gitignored `data/`
artefact, which broke unit-test collection on a clean checkout, and (b) pooled raw mass across
stations before computing the phase, violating D5's own per-station-normalised, station-equal
aggregate; both are fixed and the CSV/table above are from the corrected run). One D6 correction
against the plan's own draft: the ECDS `tigge-forecasts` process has no `grid` interpolation input —
it returns the model's native reduced Gaussian grid (measured N640), not a regular 0.5° grid; the
nearest-cell extraction operator is unaffected.

**Exit:** a measured answer, with lead-dependence, the ±3 h resolution bound, and a screening verdict
bounded by D4. Owner decides (M-DEC).

### M-A11b · Multi-season IFS timing (Plan 220)
**Depends: M-A11.** Does **not** gate M-DEC (M-DEC already resolves off M-A9 + M-A11's single-season
screen) — it closes prerequisite 2 of the independent verdict that reviewed M-A11 ("potentially several
hours or a sign change" across seasons) and feeds a later retraining decision, not the Track G
deployment gate.

M-A11 measured JJAS **2025 only** — one season cannot show interannual variability. `tigge_ifs.py` and
`tigge_gauge_timing.py` are parameterised by `--year` (D1: the `TIGGE_YEAR` module constant that used to
default both the schedule and the identity gate is deleted, not checked-around) and JJAS 2020-2024 are
retrieved alongside the already-archived 2025, reusing the exact same estimator, amplitude gate, branch
convention and elevation/lead bands (D5 — no method change). The 244-run schedule is now the *expected*
count, not a required one (D2): a missing forecast run is recorded as a named gap
(`SeasonCompleteness.missing_inits`), never a season-wide rejection — real archives have unpublished
runs. The report is per-year **and** pooled (D3: `run_multi_season` concatenates the six seasons' raw
paired frames and reruns the unchanged station-equal estimator once on the union — never a median of six
already-computed point estimates, which is a different statistic), with 2020 included and hedged (D4: it
carries only **3 raw high-band candidates** against 6-8 in later years — of which the estimator
**retains 2**, the third failing `NO_PRECIPITATION_MASS`).

**✅ COMPLETE 2026-08-31.** All five remaining seasons retrieved (244/244 inits, zero gaps, each). **The
independent review's flagged risk is CONFIRMED**: the mid-elevation band's offset ranges from **-2.31 h
(2020) to -11.48 h (2021)** on the **preregistered same-day branch** — a 9.2 h spread, not a single
stable number, and still an 8.22 h range under a fixed four-station panel. 2020's high band, which
**retains 2 of its 3 raw candidates**, returns +0.47/-0.96 h at D+1/D+2 — a **reproducible
point-estimate sign change, unresolved from zero** at that `n` under the ±3 h bound, not a
demonstrated reversal. ⚠️ The cross-station median is branch-dependent, so these extremes are not
physical magnitudes. The low band, read as "near zero" off 2025 alone, crosses the ±3 h bound in 2020 and
2023. Pooled figures track the higher-coverage years, not a naive average of the six per-year values.
Full table + reading: `docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md` § M-A11b addendum.

**Exit:** the IFS-vs-gauge phase offset for every overlapping JJAS season (2020-2025), per-year and
pooled, with per-cell `n`, station counts and a per-season completeness figure (all 100%); the re-run
2025 row matches the published table exactly (MEASURED). **Does not by itself authorise retraining** —
if anything, the confirmed spread argues for more caution, not less, before any single-point IFS
correction.

---

## Working on this track — the research data is gitignored, so a worktree starts empty

**A git worktree carries no gitignored files.** `data/` is gitignored (`.gitignore:21`), so a fresh
worktree has none of this track's inputs and the failures look like regressions when they are not.

**First thing after creating a worktree for this track:**

```bash
mkdir -p <worktree>/data/dhm_precip
cp /path/to/SAPPHIRE_flow/data/dhm_precip/* <worktree>/data/dhm_precip/
```

That is the workbook, `station_coordinates.csv`, and `era5_land_provenance.json` (Plan 171's D15
operator provenance — **JSON**, the authoritative schema).

**What it looks like when you forget.** The M-A1 reproduction gate fails with
`coordinate table not found or not a file: data/dhm_precip/station_coordinates.csv` and exit code 2.
That is **D12 behaving correctly** — a missing coordinate table is a typed loader error, never a
silent skip — but it reads as a code regression until you notice the message.

**Related trap when reporting coverage.** A full `uv run pytest` reports **7 skipped**; those are all
Plan 170's M-A1 reproduction gate, which skips when `DHM_PRECIP_XLSX` is unset (its *only* permitted
skip condition, so CI stays green without the workbook). A full-suite pass therefore **does not**
exercise the reproduction gate. Run it explicitly with the workbook before claiming it green:

```bash
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run pytest tests/integration/test_dhm_precip_reproduction.py
```

## Track I — implementation

### M-I1 · Precipitation QC rules and fixtures
**Depends: —** *(Unblocked. An earlier revision gated this on M-D1, which contradicted the owner's
"build now": code and **synthetic** defect reproductions need no data authorisation. M-D1 governs only
whether real excerpts may later replace or supplement the synthetic fixtures.)* Becomes a repo plan.

- `frozen_sensor` value exclusion so zero runs do not trigger it — `_apply_frozen_sensor`
  (`services/qc.py:92`) takes only `tolerance`/`min_consecutive`; **code change, not config**.
- *No new `QcRuleId` is required for this scope.* The id set is a closed `Literal`
  (`types/domain.py:137`) **and** independently allowlisted in `config/qc_rules.py:14`, so any future
  new rule needs both changed plus checker dispatch — but M-I1 only modifies `frozen_sensor` and
  `range_check`, which already exist.
- Sentinel and range rejection at hourly step.
- **Do not reuse `gross_outlier`** — `_apply_gross_outlier` (`services/qc.py:197`) is a symmetric
  `|value − mean| > k·std` test against a climatological baseline; on a zero-inflated right-skewed
  variable it flags real heavy rain and never flags zeros.
- Fixtures for Sindhuli Madhi stuck-high and Lukla sentinels — synthetic by default, real excerpts
  substituted later if M-D1 permits.

**Deliberately excluded:** the implausible-dry-run rule. It needs a dry-spell climatology that cannot
be estimated from a record containing the false zeros without circularity, and the contract cannot
express one — `QcRuleParams.thresholds` is `dict[str, float]` (`types/domain.py:152`, scalars only)
and `ClimBaseline` carries only rolling mean/std/count (`:179`). *(An earlier revision mis-cited
`dict[str, float | None]`; that is `StationQcOverride.thresholds`, `:176`.)* If we want the rule, it
becomes its own milestone with an **independently acquired** clean calibration reference — student
labels may corroborate such a rule but must never be its calibration basis.

**Exit (red-first):** *the obvious gate is worthless — `uv run pytest tests/unit/services/test_qc.py
tests/unit/config/test_qc_rules.py` already passes 46 tests today.* The gate is **new test cases named
in the plan that fail against current `main`** and pass after, with the existing 46 still green.

**No longer ships unexercised (OD-3).** M-A3 calls `Stage1QualityChecker` with an in-code
precipitation rule set over 26 stations × 6 years, so these rules face real data immediately — the
drift risk that motivated the original split is closed without adding a single inert config row.

**COMPLETE 2026-08-14 (Plan 172).** `_apply_frozen_sensor` (`services/qc.py:92`) gained the scalar
`exclude_at_or_below` threshold (D8): a value at or below it never starts or extends a frozen run, so
setting it to `0.0` lets precipitation's normal dry spells pass while still catching a stuck sensor
(Sindhuli Madhi's ~72 mm pinned block). Absent from `thresholds`, **which observations get flagged,
with what status and detail, is unchanged** — proved by an unmodified existing discharge case plus a
new one. *Not* bit-for-bit, and the difference is deliberate: D8b also corrects the emitted
`rule_version`, so existing configured rules now stamp their own `"1.0.0"` where the hard-coded
constant previously wrote `"1.0"`. Flag *metadata* changes; flag *selection* does not. The function also now stamps
`rule.rule_version` on every emitted flag instead of the hard-coded module constant `_RULE_VERSION`
(D8b) — a flag can finally record which rule variant produced it, and existing configurations (whose
`rule_version` already matches what they intended) keep their meaning; only `frozen_sensor` was
touched, the other four rule functions still use `_RULE_VERSION` (out of scope here). Locked in
`tests/unit/services/test_qc.py` (`TestFrozenSensorExclusion`, `TestFrozenSensorRuleVersion`).

### M-I2 · Reference dataset packaging
**Depends: M-D1, M-A3.** Small.

Package the masked dataset for the research data folder with a provenance manifest: source sha256,
mask definition and version, per-station removal accounting, processing chain, caveats — including
that unconditional totals are invalid (rule 1). Not onboarded, not in the DB (vision D10).

**Storage (owner 2026-08-12/13, ~2.4 TB Dropbox available — capacity is not the constraint):**
- **Never the public repository.** `data/` and `.data/` are gitignored (`.gitignore:21`); all DHM
  observations and station metadata live under `data/dhm_precip/` locally.
- **Synced folder holds only what cannot be regenerated**: the source xlsx and the QC'd dataset +
  manifest (~50–100 MB total).
- **Raw ERA5-Land stays local, unsynced** — ~1 GB regenerable from M-A4's committed request script.
- **Request the folder be pinned/available-offline.** Dropbox Smart Sync and OneDrive Files On-Demand
  leave cloud-only placeholders that block on read and fail in headless contexts; a pipeline reading
  one is nondeterministic in wall-time.
- **The pipeline writes to a local working directory and copies only final artefacts in.** Writing
  directly into an actively-syncing folder produces "conflicted copy" duplicates — fatal for an
  artefact that is supposed to be sha-pinned.

**Exit:** a named `uv run` command validates the manifest against a stated schema and regenerates the
dataset from source via M-A1/M-A3.

### M-I3 · WMO standards inventory
**Depends: —** Small, independent, start now.

Add gauge catch efficiency to `docs/standards/wmo.md`: **WMO-No. 8 (CIMO Guide) Vol I Ch. 6,
"Measurement of precipitation"** and **WMO-SPICE, IOM Report No. 131**; WMO-168 Vol I Ch. 3 covers it
from the hydrological-practice side. ⛔ *Corrected 2026-08-28. This line read "WMO-168 Vol I Ch.6",
which conflated two publications — Ch. 6 of WMO-168 is groundwater; Ch. 6 of **WMO-No. 8** is
precipitation measurement. SPICE was separately mis-cited as "WMO No. 1131"; it is an IOM report.
Both found by independent review and verified against the WMO library.* Record that
SAPPHIRE applies no numeric catch correction and why (D6).

**Exit:** doc updated; the D6 position reachable from the standards inventory and cross-linked from
the vision.

**✅ COMPLETE 2026-08-28.** `docs/standards/wmo.md` gains **WMO-SPICE (IOM Report No. 131)** in the
document inventory and a **§ Precipitation gauge catch efficiency** subsection under Observation QC:
WMO-No. 8 Vol I Ch. 6, WMO-168 Vol I Ch. 3 and SPICE on wind-induced undercatch; why SAPPHIRE applies no numeric correction (a
defensible transfer function needs wind at gauge height, gauge/shield type and phase — the Nepali
network supplies none of the three); the signed catch-efficiency caveat that replaces it; and the
⛔ that it is **never** a lower bound on a reported total, since a physical-impossibility QC gate lets
an isolated spurious value push a total above truth. Cross-linked from vision D6.

### M-I4 · Operational precipitation QC binding
**Depends: M-I1, M-G1, M-G2.** Gated.

Bind the rules to precipitation in `config/qc_rules.py` at sub-daily step once weather observations
flow. **G1 alone is insufficient** — selecting `WEATHER` stations does not help while no adapter maps
precipitation. Range bounds from regional extreme-value literature, **not** this sample's maxima.

**Reduced by OD-3 to config rows plus an end-to-end test** — the rule logic will already have been
proven against real data by M-A3.

**Exit:** end-to-end ingest test showing a precipitation observation traversing ingest → QC → store
with the expected flag.

---

## Scoping constraint — why M-I1 adds no operational binding

`Stage1QualityChecker` has two call sites: `flows/ingest_observations.py:188` and
`services/onboarding.py:637`. Neither can carry precipitation — the ingest flow fetches only
`RIVER`/`LAKE` (`:461-462`), and onboarding QCs each station's forecast target, where
`ForecastParameter = Literal["discharge", "water_level"]` (`types/domain.py:215`). **So the precipitation
entries in `config/qc_rules.py` and `config.toml:356,363` are unused by current production adapters and
station selection.** Not *structurally* unreachable — ingest groups and QCs whatever parameter an
injected adapter returns (`ingest_observations.py:520`), so the binding is one adapter away. M-I1
therefore ships rule logic without operational bindings; M-I4 binds them, and should add a test
proving the intended binding rather than treating "unused" as an invariant.

---

## Track G — gated (require explicit Phase-2 GO)

**✅ M-G1 IS COMPLETE — 2026-08-31 (Plan 217, T1–T3).** `ingest_observations_flow` now fetches
`StationKind.WEATHER` alongside RIVER/LAKE (D1); weather stations gate on `station_status` alone,
never on the river-only `GaugingStatus.GAUGED` filter (D2); the `fetch_latest_timestamp` cursor is
keyed by an explicit `StationKind → parameter` mapping that raises on an unhandled kind rather than
defaulting (D3, `_cursor_parameter_for_kind`); and calculated-station derivation stays restricted to
RIVER/LAKE regardless of a WEATHER station's `gauging_status` (D4). Proven on the replay path (T3).
**Weather stations are eligible and cursor-correct but UNSERVED** until M-G2 supplies an adapter (D5),
and any observation that does arrive passes QC only because no rule currently matches
`"precipitation"` — the rule set is M-I4's, gated behind M-G2, not this plan's. **M-G1 must not be
scheduled against a live feed before M-I4.**

| ID | Milestone | Note |
|---|---|---|
| **M-G1** | Weather-station observation ingest for `StationKind.WEATHER` | ✅ COMPLETE (Plan 217, above) — was: `ingest_observations.py:461-462` fetches only RIVER/LAKE. Onboarding already handles WEATHER (`onboarding.py:794`) — ingest is the gap |
| **M-G2** | DHM precipitation adapter | Blocked on DHM confirming an operational precipitation API |
| **M-G3** | *(Programme, not a milestone)* Correction on the operational forcing path | Decomposes into hypothesis selection, correction design, leakage-safe fitting, uncertainty propagation, high-flow evaluation, rollback, monitoring. **Subject to vision D9.** Do not plan as one unit |
| **M-G4** | Operational-feed characterisation | Tests vision D10's assumption that sample defects recur live. Requires M-G2 |
| **M-G5** | Gateway ERA5-Land delivery parity | Does our Gateway pipeline deliver ERA5-Land correctly — unit conversion, deaccumulation, temporal alignment, basin-averaging — versus the direct CDS series from M-A4? A pipeline-correctness check, not a meteorological one (OD-2b). Our models are forced through the Gateway, not through CDS, so this matters eventually; it is not Phase-1 work |

---

## Dependency graph

```json
{
  "milestones": [
    {"id": "M-D1",  "depends_on": []},
    {"id": "M-D2",  "depends_on": []},
    {"id": "M-D3",  "depends_on": []},
    {"id": "M-A1",  "depends_on": []},
    {"id": "M-I1",  "depends_on": []},
    {"id": "M-I3",  "depends_on": []},
    {"id": "M-A2",  "depends_on": ["M-D3", "M-A1"]},
    {"id": "M-A3",  "depends_on": ["M-A2", "M-I1"]},
    {"id": "M-A4",  "depends_on": []},
    {"id": "M-A5",  "depends_on": ["M-D2", "M-A4"]},
    {"id": "M-A5b", "depends_on": ["M-D2"]},
    {"id": "M-I2",  "depends_on": ["M-D1", "M-A3"]},
    {"id": "M-A6",  "depends_on": ["M-A3", "M-A5"]},
    {"id": "M-A7",  "depends_on": ["M-A2", "M-A3"]},
    {"id": "M-A8",  "depends_on": ["M-D2", "M-A6", "M-A7"]},
    {"id": "M-A10", "depends_on": ["M-A3"],
     "note": "Co-located Pyramid adjudication. Needs only the QC mask — runs in PARALLEL with M-A5/M-A6."},
    {"id": "M-A9",  "depends_on": ["M-A6", "M-A7", "M-A8", "M-A10"]},
    {"id": "M-A11", "depends_on": ["M-A6", "M-A7", "M-A8", "M-A9"],
     "note": "Plan 216. Re-measures M-A9's Use-1 timing question against the operational ECMWF IFS forecast (TIGGE via ECDS) instead of ERA5-Land. Screening only — see docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md."},
    {"id": "M-A11b", "depends_on": ["M-A11"],
     "note": "Plan 220. Closes prerequisite 2 of M-A11's independent verdict — JJAS 2020-2024 alongside 2025, per-year and pooled, same estimator. Does NOT gate M-DEC; feeds a later retraining decision."},
    {"id": "M-DEC", "depends_on": ["M-A9", "M-A11"], "kind": "decision",
     "note": "Owner Phase-2 GO / NO-GO. M-A9 exits with a RECOMMENDATION; only this node authorises Track G."},
    {"id": "M-G1",  "depends_on": ["M-DEC"]},
    {"id": "M-G2",  "depends_on": ["M-DEC"]},
    {"id": "M-G4",  "depends_on": ["M-G2"]},
    {"id": "M-I4",  "depends_on": ["M-I1", "M-G1", "M-G2"]}
  ],
  "start_immediately": ["M-D1", "M-D2", "M-D3", "M-A1", "M-A4", "M-I1", "M-I3"],
  "note": "M-A4 is a CDS download over a bounding box \u2014 it needs no station coordinates, so it starts cold again."
}
```

*(An earlier revision let M-G1/M-G2 depend directly on M-A9 — that encoded "recommendation written"
as "deployment work authorised". M-DEC is now the only edge into Track G.)*

## Critical path and risk

With M-A3 correctly behind M-A2, the analytical spine lengthens. **Co-longest chains (6 milestones
each), not one path:**

- `M-D3 → M-A2 → M-A3 → M-A6 → M-A8 → M-A9`
- `M-A1 → M-A2 → M-A3 → M-A6 → M-A8 → M-A9`
- `M-D3 → M-A2 → M-A3 → M-A7 → M-A8 → M-A9`

`M-D2 → M-A5 → M-A6 → M-A8 → M-A9` is a 5-hop chain starting at the external dependency; with M-A4
now parallel to it, the analytical chains through M-A2/M-A3 are more likely to dominate. **Without duration estimates none of these is a scheduled critical path — only a topological
statement.** Attach rough durations before treating any of them as a plan.

**M-D2 is confirmed by the owner as an assured input**, which removes what was the programme's
largest risk. It nonetheless gates M-A4 → M-A5 → M-A6 → M-A8, so it remains the first thing on the
schedule even though it is no longer a risk.

**Switching from Gateway-polygon registration to direct CDS acquisition removed the track's largest
design risk.** The extraction operator is now a cheap, reversible parameter rather than a
front-loaded, expensive-to-undo choice (OD-1), and M-A4 no longer waits on coordinates.

The residual unknowns are ordinary engineering: ERA5-Land deaccumulation, and the CDS request itself
(account, licence acceptance, queueing, and an API that has changed before).

Unblocked regardless: **M-D1, M-D3, M-A1, M-A4, M-I1, M-I3.**

## Phase-2 hypothesis: satellite-informed diurnal correction (recorded 2026-08-13)

**Recorded as a hypothesis, not a decision** — vision D7 still holds that Phase 1 commits to no
correction design. M-A5b's IMERG extraction exists to let M-A6 *measure* whether this is viable, once
M-A5b lands (Plan 174 split IMERG out of M-A5 — see M-A5/M-A5b above).

**The idea.** Use a satellite-derived diurnal climatology to redistribute an IFS 3 h/6 h basin total
across hours, instead of inheriting IFS's own (badly displaced) timing. Its decisive advantage over
our gauges is **spatial completeness**: our central problem was "which diurnal regime applies at an
*ungauged* basin?", and a 0.1° satellite climatology answers that everywhere, which 26 gauges never can.

**Scope it correctly.** This can only correct the **mean diurnal shape** — a climatological
weighting. It cannot fix **event-level timing** in a given forecast (putting *this* storm at the right
hour). Disaggregating a 3 h/6 h total is the former, which is the tractable one.

**Three conditions it must clear before anyone builds it:**
1. **Satellite phase must be trustworthy at our elevations** — IMERG performance degrades at high
   elevation and passive-microwave retrieval is weak over snow and ice. M-A5b/M-A6 test this directly.
2. **The satellite-vs-gauge phase discrepancy must be explained** (midnight vs 21:00, above). Treating
   satellite phase as truth while it disagrees with gauges at the national scale is unsafe.
3. **It must matter hydrologically.** Dudh Koshi is ~4,000 km² with a response time of order 12–24 h;
   the catchment integrates sub-daily timing away, and intensity — not hour-of-day — governs runoff
   generation. Diurnal phase is plausibly **second-order for our first target basin** and first-order
   only for small steep tributaries. **This is a strong candidate to fail D9's hydrological test
   cheaply, and finding that out early is worth more than building it.**

## Phase-2 constraint from the literature (2026-08-13)

**"Correct the intensity, inherit the timing from the NWP" is now DISFAVOURED as a Phase-2 option.**
It was live when the phase-vs-intensity fork was discussed; the literature sweep undercuts it.

In the Himalayan foothills — where nearly all 26 stations sit — ERA5 places the diurnal peak in the
**mid-afternoon** while observations put it at **0300 IST**, approaching a **12-hour** phase error
(Hunt et al., 2022). It is structural, not incidental: ERA5 and IMDAA suffer the same error despite
different models and different assimilated data, and Norris et al. (2017) show a parametrised-convection
run **cannot** reproduce the nocturnal low-elevation peak at all. ERA5 gets the Indo-Gangetic Plain
right; the failure is specific to the orographic band we care about.

Inheriting model timing would therefore import a large systematic error. **An observationally-derived,
elevation-banded diurnal profile stops being a refinement and becomes the only defensible source of
sub-daily timing in this region** — which raises the value of M-A7's elevation stratification and of
any future regime classification. Recorded as a constraint, not a decision: vision D7 still holds that
Phase 1 commits to no correction design.

## Known weaknesses carried forward

- **The high-altitude diurnal signal is unresolved AT OLANGCHUNGGOLA ONLY** *(narrowed by M-A10,
  2026-08-18)*. Above ~2,500 m our Group A stations matched neither the literature (Norris: unimodal,
  ~1500 LT above 3 km) nor our own hill band. **Lukla is now resolved** — its 02 UTC peak was 6
  sentinel values of −9999999 normalised over unmasked data; masked it peaks 21 NPT, agreeing with
  co-located Pyramid AWS3. **Olangchunggola's 03 UTC ≡ 08 NPT peak stands**: zero sentinels, and
  immovable across the 0.0/0.1/0.2 mm ablation ladder, so it is neither an artefact nor the noise
  floor. It has no co-located reference gauge, so M-A10 cannot adjudicate it. This is the band that
  matters most for Dudh Koshi.
- **Duplication of the students' QC and characterisation is DELIBERATE, not a cost to minimise**
  (owner 2026-08-12). Two independent passes over the same file are expected to teach each side
  something; findings flow both ways during the work, not only at M-A9. Where our fit-for-purpose
  mask and their adjudicated QC disagree, that disagreement is itself a result.
- **Wholesale zero-run removal discards real dry spells.** Sound only under identical masking
  (rule 1); every masked-sample result must state the fraction removed.
- ~~M-I1 ships ahead of its data path~~ — **closed by OD-3**: M-A3 exercises it on real data.
- **The extraction operator still shapes the answer**, even though it is now cheap to change: an
  ERA5-Land cell averages ~110 km² of Himalayan relief. M-A5's sensitivity comparison **quantifies
  operator spread** as a named envelope (Plan 174 D1a — not an uncertainty bound and not a decision
  gate: nearest is locked, D1); it does not remove the underlying representativeness gap.
- **No effort estimates anywhere**, so "critical path" remains an assertion about topology.
- **We validate ERA5-Land the dataset, not ERA5-Land as our pipeline delivers it.** Our models are
  forced through the Gateway; this track reads CDS. Parity between the two is a real question, parked
  as M-G5 (OD-2b).

## Forcing-correction architecture (added 2026-08-18)

⛔ **OWNER DECISION 2026-08-31 (Plan 220) — OD-13 IS HELD; OD-12 STANDS.**
These decisions were taken when IFS's diurnal displacement was believed to be a single, stable
quantity. **It is not**: across JJAS 2020-2025 the mid band spans ~9 h on the preregistered branch
(8.22 h even under a fixed four-station panel), and there is **no single displacement to calibrate
against**.

- **OD-12 — SURVIVES.** It settles *where* the correction sits, not how large it is: the model keeps
  consuming `BasinAverageForecast` and only the forcing is corrected. Unaffected by the magnitude
  finding. ✅ **Confirmed by the owner 2026-08-31.**
- 🔴 **OD-13 — HELD. Do NOT build the correction operator yet.** Its instruction was "built now,
  enabled at the Nepal re-training step". ⇒ **Delay building until the displacement can be sized**
  (owner, 2026-08-31). Building a correction whose magnitude cannot be determined invites someone to
  fill it in later from whichever season's number is nearest to hand.
- 🔴 **"One fix serves both products" — HELD, and doubted.** See the binding section below.

| # | Question | Decision |
|---|---|---|
| **OD-12** | Must the modeller train on elevation bands so we can correct band-wise? | **No.** Correct the **basin-average** series in the **forcing pipeline**, using **hypsometric weighting** — the elevation dependence enters only through the weights, which are computed offline, once per basin. **The model never changes and keeps consuming `BasinAverageForecast`.** |
| **OD-13** | Where does the correction live, and when is it switched on? | 🔴 **HELD 2026-08-31 — DO NOT BUILD YET** (owner; Plan 220: no single displacement to size it against). *Superseded wording: "in the forcing pipeline, behind a seam — built now, enabled at the Nepal re-training step, never before."* **Where** it lives is unchanged; **when it is built** is now: after the displacement can be sized.** See the train/serve invariant below: it is the binding constraint, not the correction itself |

### Why band-wise modelling is not required — the pieces already exist

- `BandRecord` (`types/basin_package.py:109`, from `bands.gpkg`) already carries `min_elevation_m`,
  `max_elevation_m` and **`area_km2`** per band. **That is hypsometry, already in the basin package.**
- `ElevationBandForecast` (`types/weather.py:62`) is already a first-class `WeatherForecastResult`
  variant beside `BasinAverageForecast`, so the band-wise route stays open without being taken now.

🔴 **THE OPERATOR DESCRIBED BELOW IS HELD (2026-08-31) AND IS NOT AN INSTRUCTION** (the *seam* is separately authorised — see the sequencing block). It describes how a
correction *would* be built once the displacement can be sized. ⛔ Do not act on it: OD-13 is held,
and Plan 220 shows there is no single magnitude to build against. Retained as design reasoning.

**The cheap operator:** take the **observed elevation-banded diurnal profiles** (M-A7's deliverable),
weight them by each basin's **band `area_km2`**, and collapse to an **observation-derived expected
basin-average diurnal shape**. Compare with the IFS basin-average diurnal shape. The correction is a
**redistribution in time of an unchanged daily total** — it moves rain to the right hours, it does not
change how much fell. That last property matters: it keeps the correction **orthogonal to D6/D9**,
which forbid touching magnitude.

**When band-wise WOULD be required:** if a basin's diurnal phase varies so strongly with elevation that
a single collapsed profile misrepresents the mixture — plausible for basins spanning ~500–8,000 m, where
the low band peaks nocturnally and the ~5,000 m band peaks in the afternoon. **Testable:** compare the
hypsometrically-weighted mixture against a band-wise correction re-aggregated to basin average. If they
agree within the operator-sensitivity envelope, the cheap route is sufficient. **Do the cheap one first
and measure**; `ElevationBandForecast` is the escape hatch if it fails.

### ⚠️ THE BINDING CONSTRAINT: train/serve consistency, not the correction

Runoff models are **pre-trained on GLOBAL data** (no operational Nepali observations exist yet) and
**re-trained in Nepal after deployment**. That makes *when* the correction is enabled more important
than *how* it is computed:

- **A model learns the timing relationship implicit in its training forcing.** Feeding it forcing whose
  diurnal phase differs from what it trained on is a **train/serve skew** — an OOD input, not a fix.
- This is the same logic as **OD-6** (aquacast trained on ERA5-Land and run on ERA5-Land, so the bias
  largely cancels). **But global pre-training WEAKENS that cancellation**, because the model learns
  timing mostly from regions where the reanalysis phase is fine, not from the Himalaya where it is ~12 h
  wrong. ⇒ For a globally pre-trained model, correcting the operational forcing is **plausibly
  beneficial** — the model expects physically-timed rain. **Plausibly, not certainly: the sign is an
  empirical question the Nepal re-training will settle, and it must be measured, not assumed.**

**The invariant, whichever way that goes: TRAIN AND SERVE MUST MATCH.** An
uncorrected-but-consistent pipeline can beat a corrected-but-inconsistent one.

⇒ **Sequencing** — 🔴 **step 1 is HELD; the sequence resumes only once the displacement can be sized:**
1. **Now (pre-deployment):** build **the seam only** — ✅ authorised (owner 2026-08-31), it needs no
   magnitude. 🔴 ~~build the correction operator~~ **HELD** until the displacement can be sized
   (Plan 220). Global pre-training stays on uncorrected forcing either way.
2. **At Nepal deployment/re-training:** apply the **same** operator to the Nepali training forcing *and*
   the operational forcing, then re-train. Consistent *and* closer to physical truth.
3. **Measure both ways at step 2** — corrected-consistent vs uncorrected-consistent — because that is
   the only point where we control both sides and can actually tell which is better.

**And OD-10 remains the better long-run answer:** if DHM's parallel downscaling delivers a
convection-permitting product, that fixes the phase *physically*, and this correction becomes
unnecessary rather than merely adequate. **Build the seam so that swap costs nothing.**
✅ **OWNER DECISION 2026-08-31 — the SEAM may be built NOW; only the OPERATOR is held.** A seam is an
insertion point and needs **no magnitude**, so it is separable from the correction it will one day
carry: building it keeps the option open at no cost and commits to nothing. ⛔ **Do not build the
operator** (OD-13, held until the displacement can be sized) — an empty seam is the whole of what is
authorised here.

### M-D4 · Lightning data — partner ask (NEW, partner-gated)
**Depends: —.** Ask project partners for stroke-level lightning: **timestamp, lat/lon, detection
network**, 2020–2025, box 26–31 N / 80–89 E (the same box as our ERA5-Land, so it drops straight into
the elevation banding). Networks differ in how they are obtained: **WWLLN** is research-consortium and
usually free to academic partners — the likeliest yes; **GLD360** (Vaisala) and **ENTLN** are
commercial, so an existing partner licence matters more than price; **Blitzortung** is free but its
Nepal coverage is probably thin.
**⛔ Do NOT draft the acquisition plan until the data is in hand.** This track has now specified against
documentation three times and been wrong every time — the ERA5-Land accumulation convention, the CDS
payload shape, and the CDS cost limit. The plan gets written **after** we see the real format, exactly
as Plan 171's own constraint 3 requires.

#### ✅ NASA ISS-LIS RETRIEVED 2026-08-18 — partner-independent, and it settles what lightning can do here

**WWLLN is not available to us** (owner, 2026-08-18), so the partner ask is not the only route after all.
**NASA ISS-LIS V3** (`isslis_v3_fin`, Earthdata login, free) was fetched directly by stream-and-subset:
per-orbit granules downloaded, flashes inside **26–31 N / 80–89 E** kept, granule deleted (~17 GB passed
through, ~49 KB retained). **JJAS 2020–2023, 5,786 granules processed, 4,214 flashes**, in
`data/dhm_precip/lightning/iss_lis_flashes_nepal_box.parquet` (untracked, per M-D1). One granule was lost
to a failed download.

**⚠️ 4,214 flashes is NOT a large sample — the effective n is 352.** This correction is the most
important thing on this page about lightning, because the raw count invites exactly the wrong reading.
**Flashes inside one storm overpass are one observation of the diurnal cycle, not one hundred.**
Clustering by a 10-minute time gap gives **352 overpass clusters** — the real sampling unit. The top 10
clusters alone are **22.3 %** of all flashes; the median cluster is **5** flashes. That is **~14.7
overpasses per hour-of-day bin**, so Poisson 1σ is **±26 %**, not the ±7.5 % a naive 4,214/24 implies.

**What the data therefore CANNOT do: pin a peak hour.** Overpass-weighted, hours 16 (24 overpasses),
19 (23), and 0/15/20 (20 each) are statistically indistinguishable. A raw flash-weighted profile peaks
at 16 NPT and a view-normalised one at 04 NPT — the disagreement is noise, not signal, and neither
number should be quoted.

**What it CAN do: establish the regime shape**, which survives the noise because it is a broad
nine-hour pattern rather than a single bin (overpass-weighted, normalised to the 24-hour mean):

| NPT hours | normalised | reading |
|---|---|---|
| 12–20 | all ≥ 1.09, max 1.64 (h16) and 1.57 (h19) | **afternoon–evening convective maximum** |
| 05–11 | all ≤ 0.89, floor 0.34 (h05), 0.41 (h06) | **morning minimum** |
| 00–04 | 1.36, 1.16, 0.68, 1.09, 1.30 | secondary nocturnal bump |

That is a textbook convective cycle and it is consistent with the literature for the low/mid elevations
that dominate the box.

**Cross-link to M-A10, hedged.** The lightning maximum (16–19 NPT) is several hours EARLIER than the
high-altitude gauge peak M-A10 established at Lukla and Syangboche (21–23 NPT). Were the nocturnal gauge
signal an instrument artefact, it should not care about elevation; instead the convective proxy and the
high-altitude gauges differ in the direction the literature predicts for an elevation-dependent phase
shift. **Corroborating, not decisive** — the box spans plains, foothills and high Himalaya together, so
the comparison cannot be attributed cleanly to elevation.

**Scope CONFIRMED, not merely assumed: foothill/box-wide validation only, no elevation stratification.**
14.7 overpasses per bin cannot be split across elevation bands — it is marginal even unsplit. Lightning
also proxies CONVECTIVE precipitation, while high-altitude Himalayan precipitation is substantially
snow, so the high-altitude timing question stays with M-A10's co-located gauge pair.

**Caveat on the sampling check.** ISS precesses, so hour-of-day view time is not uniform a priori. The
proxy used was the hour distribution of all 5,786 processed granules (208–268 per hour, max/min 1.29),
which detected no strong non-uniformity — but granule START times smear across a ~90 min orbit, so this
is weak evidence of uniformity, not a demonstration of it. A proper treatment needs the per-second view
time from the granules' `lightning_one_second` group, which the stream-and-subset run did NOT retain.
**Do not upgrade the shape result to a quantitative diurnal climatology without that.**

**⚠️ Reproducibility gap:** the fetch and analysis scripts currently live only in the session scratchpad
(a copy of the fetcher sits beside the data at `data/dhm_precip/lightning/fetch_iss_lis_foothill.py`,
untracked). Nothing in the repo reproduces the numbers above. Promoting the fetcher and the
overpass-clustering analysis into `scripts/dhm_precip/` is an open follow-on, deliberately not done
inside the M-A10 branch.

**The partner ask is NOT retired by this.** ISS-LIS gives 352 overpasses over four monsoons; a
ground-based network (WWLLN/GLD360/ENTLN) gives continuous coverage and would raise the effective sample
by orders of magnitude, which is what an elevation-stratified diurnal result would need.

## Decisions register — forcing strategy for operational P (added 2026-08-18)

| # | Question | Decision |
|---|---|---|
| **OD-7** | How do we get long hourly gauge series? | **From project partners — do NOT scrape the public DHM rainfall portal.** The portal is real-time only; starting collection now yields **a couple of months before delivery**, and every use we have (diurnal climatology, seasonality) needs **long** series. A few months of self-collected data is not worth the ingest machinery it would require. **Ask partners for the archive instead** |
| **OD-8** | Lightning data | **Pursue, via partners.** Hourly, real-time, global, and **independent of precipitation retrieval** — it indicates *when* convection occurs without undercatch or orographic-retrieval error, which is exactly the disputed quantity. Best value-per-effort of the timing sources. It gives **no amounts**, which is fine: amounts are not the problem. **UPDATED 2026-08-18 — partly self-served: WWLLN is unavailable to us, so NASA ISS-LIS V3 was retrieved directly (Earthdata, free). It establishes the convective REGIME SHAPE (afternoon-evening max, morning min) but its effective sample is **352 overpasses**, not the 4,214 flashes the raw count suggests, so it cannot pin a peak hour and cannot be stratified by elevation. The partner ask therefore STANDS. See M-D4** |
| **OD-9** | Weather radar | **Not available** (owner, 2026-08-18). Nepal has one C-band dual-pol radar at **Surkhet (2019, western)**, with Palpa and Udaipur planned; Surkhet's ~200 km range does not usefully cover our central/eastern basins. **Not on the critical path. Do not design around it** |
| **OD-10** 📌 **TODO** | Do we build convection-permitting downscaling ourselves? | **NO.** DHM has **several parallel projects improving their own weather forecasts**, and may couple their downscaled NWP to this system in future. Duplicating that is wasted effort in someone else's lane. ⇒ **What we owe instead is a forcing interface that can accept an externally produced downscaled product later** — the investment goes into the *seam*, not into the model |
| **OD-11** | How much P work is warranted at all? | **Only what is necessary to get P right for operational runoff forecasting** (owner, 2026-08-18). See the scope test below — this is a real constraint, not a platitude, and it currently excludes most diurnal work from the delivery path |

### 📌 TODO — OD-10's forcing seam review (owner-noted 2026-08-18, NOT scheduled)

**The one item here that is answerable NOW, with no new data.** OD-10 says we will not build
convection-permitting downscaling ourselves, and will instead accept an externally produced downscaled
product from DHM if their parallel projects deliver one. That promise is only worth anything if the
**forcing seam can actually take it without redesign**.

**Scope (~half a day, architectural review — no new research):** can `WeatherForecastResult` accept an
external downscaled product as-is? `BasinAverageForecast` and `ElevationBandForecast`
(`types/weather.py:62`) already exist, so the seam may largely be there already — the review is to find
out, not to build. Questions: what would a DHM-supplied product look like as a `WeatherForecastResult`;
does the adapter boundary assume ECMWF/ICON-shaped input anywhere; and what would have to change if the
answer is "a new variant".

**Why it is worth doing independently of the precipitation research:** it de-risks the *cheapest
possible* fix for the diurnal phase problem — someone else solving it physically — and it is the kind of
seam that is painful to retrofit once models are trained against a fixed forcing shape.

### ✅ REVIEW DONE 2026-08-18 — the seam is NOT currently keepable. Five findings, all verified.

An agent reviewed it and a parallel sweep corroborated; **I re-read every cited site myself** before
recording. *(Caveat: only the sweep addendum was returned to me — its ranked change list was not, so
what follows is what I verified, not a complete remediation plan.)*

| # | Finding | Verified |
|---|---|---|
| 1 | **No adapter registry exists at all** — `adapters/__init__.py` is **0 bytes**. Adding a source is a build, not an extension | `wc -c` = 0 |
| 2 | **No shared canonicalisation layer.** Variable renaming, unit conversion and **precipitation de-accumulation** are adapter-PRIVATE if-chains (`adapters/meteoswiss_nwp.py:157-177`). `types/forcing_schema.py` declares the canonical units but has **ZERO consumers** | grep: no consumers |
| 3 | **`ForcingResolution` has only `DAILY`** (`types/forcing_schema.py:25-26`) — the one place the forcing contract is written down **cannot express the 3-hourly product v1 promises** | read |
| 4 | **A provider assumption lives in `types/`, not an adapter**: `HORIZON_CEILING_FLOORS` pins 5 steps because "5 = MeteoSwiss ICON-CH2-EPS's 120 h" — the comment itself says *"This 5 is a PROVIDER assumption, not a modelling judgement"* | `types/ids.py:63-72` |
| 5 | **The grid extractor OVERWRITES the CRS instead of reading it**: `.rio.write_crs("EPSG:4326")` (`exact_extract_grid_extractor.py:98`). A projected or rotated-pole grid is **silently mis-georeferenced**, surfacing later as "polygon(s) outside grid extent". No regridding exists anywhere in the repo | read |

### the highest-risk item is #2, and it is the one that fails SILENTLY

A DHM adapter must reimplement the accumulation/unit contract **with nothing enforcing it**. A
**rate-vs-accumulation mismatch** would not raise — it would produce plausible, wrong forcing, and the
models are trained against a fixed forcing shape, so it would be baked in.
**This track has already lost two days to exactly that class of bug**: ERA5-Land's accumulation
convention was stated wrongly in Plan 171's first draft and only real data settled it. IMERG, when it
comes, is a **rate (mm/hr)** product — precisely the mismatch waiting to happen.
=> **Smallest change that most reduces risk: give `forcing_schema` a real consumer** — make the
canonical unit/accumulation contract something an adapter must satisfy rather than something it may
ignore — **before a second forecast source exists**. #5 is the same failure mode (assume rather than
assert) and should be fixed with it: read the file's CRS and reproject, never overwrite.

**➡️ THESE FINDINGS NOW LIVE IN `docs/plans/187-forcing-canonicalisation-seam.md` (DRAFT).**
They were first written here, which was the WRONG home — they are architecture defects, and nobody
building an adapter would look in a precipitation research document. Plan 187 is authoritative;
this section is the decision record that points at it. **Still not scheduled.**

### ✅ RESOLVED 2026-08-18 — v1 IS SUB-DAILY (3-hourly), so DIURNAL PHASE IS ON THE CRITICAL PATH

**Owner, 2026-08-18: Nepal v1 produces a forecast every 3 hours.** The gating question below is
answered, and the answer puts the diurnal work **on the delivery path, not in characterisation**.

**A ~12 h phase error is FOUR TIMESTEPS of displacement at 3-hourly resolution.** ERA5's Himalayan
foothill peak is off by up to ~12 h, and the failure is structural — parametrised convection cannot
produce the nocturnal peak, and ERA5 and IMDAA fail *identically* despite different models and data
(Norris et al. 2017; Hunt et al. 2022). At 3 h that error is not a refinement; it puts the rain in the
wrong part of the day.

⚠️ **Correction to an earlier framing in this file (written 2026-08-18, before the timestep was
fixed): "a diurnal phase error integrates out of a daily total" was TOO CLEAN.** A 12 h shift does not
merely redistribute rain *within* a day — it displaces rain **across day boundaries**, moving a monsoon
burst from day *D* to *D+1*. It averages out over a long climatology; it does **not** average out for
the individual days a flood system exists to get right. The error was therefore never fully harmless
even at daily resolution. At 3-hourly it is direct.

⇒ **Consequences, binding:**
- **M-A7 (temporal characterisation) moves onto the critical path.** The elevation-banded, observation-
  derived diurnal profile is no longer optional context — it is the only defensible sub-daily timing
  source we have, because no available model of this class gets the phase right.
- **The lightning ask (OD-8) is now urgent rather than a cheap option.** It is the one timing source
  that is independent of precipitation retrieval, so it can validate phase where gauges are sparse and
  satellites struggle over terrain.
- **M-A10's co-located adjudication becomes load-bearing**, not merely interesting: if our Group A
  high-altitude diurnal signal is noise-floor contamination, a 3-hourly product built on it would
  encode an artefact into operational timing.
- **OD-10's forcing seam matters more, not less.** If DHM's parallel downscaling projects deliver a
  convection-permitting product, that is the *physical* fix for phase; our interface must be able to
  take it without redesign.

**Owner clarification, same day: v1 delivers BOTH a daily AND a 3-hourly product.** So the earlier
open question — cycle cadence vs output resolution — is closed in the direction that sets the
requirement at its **strongest**:

| Product | What a phase error does | What it requires |
|---|---|---|
| **3-hourly** | ~12 h ⇒ **four timesteps** of displacement — rain in the wrong part of the day | the full **intra-day profile** |
| **daily** | displaces rain **across the day boundary** — a burst lands on *D+1* instead of *D* | correct **day-attribution** |

🔴 **HELD 2026-08-31 (Plan 220) — the owner's reading is that ONE FIX MAY NOT BE ENOUGH.**
The argument below was made from a single, stable ~12 h error. With a displacement that moves ~9 h
between seasons, a correction tuned to one product's needs may not suit the other's, so the
"no trade-off" claim **cannot be assumed**. ⇒ **Hold this conclusion until the displacement can be
sized.** The structural reasoning may well survive; its premise does not. *(The text below is retained
as the original argument, not as a live decision.)*

**One fix serves both, and that is the useful part:** getting the intra-day phase right *necessarily*
fixes day-attribution, because day-boundary displacement is just the phase error crossing midnight.
There is no trade-off between the two products and no reason to build two corrections — **the 3-hourly
requirement dominates, and the daily product inherits the benefit for free.**

**Unchanged by any of this:** vision **D6/D9** stand. No numeric undercatch correction at any timestep;
the flood-safety asymmetry — correcting forcing *down* toward undercatching gauges injects a **dry bias
into a flood-forecasting system** — is independent of resolution.

## Decisions register — all resolved 2026-08-12

| # | Decision | Outcome |
|---|---|---|
| **OD-1** | Extraction operator | **Downgraded to a parameter.** Was "polygon size" and the least-reversible choice in the track; direct CDS made it an xarray argument, changeable in minutes. Recommend **nearest cell centre** primary, **bilinear** as M-A5's sensitivity run. Decide when M-A5 is written |
| **OD-2** | Research namespace / `basin_average` mislabelling | **Dissolved.** Existed only because the Gateway route required registering 26 research polygons in a production namespace. Nothing is registered now |
| **OD-2b** | Validating the Gateway delivery path | **Deferred to Track G as M-G5.** It answers a different question — *does our pipeline deliver ERA5-Land correctly* (units, deaccumulation, alignment, basin-averaging) — which is pipeline correctness, not meteorology. Real, but not Phase 1 |
| **OD-3** | How M-I1's rules get exercised | **The research pipeline is their first consumer.** M-A3 calls `Stage1QualityChecker` with an in-code precipitation `QcRuleSet` over 26 stations × 6 years. Rules face real data on day one; **no config rows added**; M-I4 shrinks to config + an end-to-end test |
| **OD-4** | Track A is ~4–5 focused weeks | **MVP first.** Run M-A1 → A2 → A3 → A4 → A5 → A6 (~9–13 d) for the ERA5-Land answer, then decide whether M-A7/A8 are still worth it — informed by our result *and* by what the students have covered |
| **OD-5** | What crosses to the students | **Methods and defects early; results held.** Share the defect inventory and the methodological cautions now — both affect their thesis validity. Hold our QC mask decisions, ERA5-Land results and conclusions until both tracks have written theirs down |
| **OD-6** | Does aquacast (Plan 152) wait? | **No — independent.** aquacast is trained on ERA5-Land and run on ERA5-Land, so a gauge-vs-ERA5 bias largely cancels rather than propagating (vision D2a). Plan 152 proceeds on its own schedule. *Accepted risk: if the bias is large we learn it late* |
| **Storage** | Where research data lives | Synced Dropbox holds only non-regenerable artefacts (source xlsx, QC'd dataset + manifest); raw ERA5-Land stays local and unsynced; folder pinned available-offline; pipeline writes locally and copies in. Asked as one question with M-D1, since it is a distribution decision |

## Phasing (OD-4)

**Phase 1 — the ERA5-Land answer (~9–13 focused days)**
`M-A1 → M-A2 → M-A3 → M-A4 → M-A5 → M-A6`, with `M-I1` ahead of M-A3 and `M-D1`/`M-D2`/`M-D3`/`M-I3`
running alongside. M-A4 is parallel throughout — it needs no coordinates.

**Decision point.** Does M-A6 answer the question well enough, and what have the students already
covered?

**Phase 2 — characterisation extras (~4 days, optional)**
`M-A7`, `M-A8`, then `M-A9`. Neither changes the Phase-2 go/no-go; both are candidates to drop if the
students' track has covered the same ground.

**Track I runs on its own clock**: M-I3 now (hours), M-I1 before M-A3, M-I2 after M-A3.
