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

Daily/monthly aggregates are formed **only after** applying the common hourly mask, with a stated
minimum retained-hour coverage per period. No rescaling of incomplete totals. The 0.2 mm threshold is
applied after valid aggregation, never before. Contingency tables include only jointly valid periods.

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
a manifest exists. See `docs/plans/171-era5-land-acquisition.md` for the full finding list and fixes.

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
26 station locations, using **IMERG Final** (characterisation, not the D4 adjudication role that
required satellite-only independence — our track dropped adjudication when wholesale zero-run removal
was chosen). Record the product version and the same grid/elevation diagnostics as M-A5's ERA5-Land
side. Split from M-A5 by Plan 174 (2026-08-16): the milestone doc previously named this "nearly free
once the extraction pipeline exists," which undercounted a distinct acquisition shape (see M-A5
above). No plan has been written for this milestone yet.

**Exit:** extracted IMERG series + named operator + per-station elevation mismatch table + the
operator-sensitivity comparison, all regenerable from the committed pipeline — the same shape as
M-A5's exit, for IMERG.

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

**Exit:** error characterisation; every result signed per D6 (a post-QC gauge total is a *lower
bound*), carrying the selection caveat, its named estimand, and the retained fraction it rests on;
plus the within-cell observed gauge variability figure above, with its honest limits attached.

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

**The hypothesis it exists to settle:** our DHM Lukla peaks at **02 UTC ≡ 07:45 NPT**, which sits inside
Pyramid Lukla's diurnal **minimum** (normalised 0.29/0.28 at 07–08, peaks at 22–00 NPT) — near
anti-phase from 1.4 km away. Combined with Group A's 0.01 mm resolution and sub-0.1 mm noise floor, the
hypothesis (**H1**) is that **the Group A high-altitude diurnal signal is noise-floor contamination, not
physics** (**H0**). Confirming H1 would retire a finding the vision has carried as UNRESOLVED since
2026-08-13.

**⚠️ The test as first written could not identify H1 — corrected before implementation (Plan 182).** A
negative control (a threshold ladder applied to Pyramid) was found to be **vacuous**: every positive
Pyramid JJAS value is an exact multiple of 0.2 mm (LSI-Lastem tipping-bucket resolution), so a threshold
ladder is a structural no-op on Pyramid and could never have detected anything — removed. In its place:
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

**Residual risk, still unresolved:** `data/dhm_precip/pyramid/` is gitignored and the real Zenodo files
were not present in any workspace this plan has been implemented or fixed in, so
`pyramid_loader.py`'s column-name assumptions (`TIMESTAMP`, `RR`) remain undocumented-but-typed guesses,
not verified against a real file — a mismatch raises loudly (`PyramidSchemaMismatchError`) rather than
silently misreading. `coloc_run.py`'s `main()` wires the real production DHM ingest+mask pipeline
(`loader`, `views`, `normalise`, `observations`, `qc_mask` — the same call sequence `pipeline.py` already
uses in production) and the real `pyramid_loader`, but this wiring has NOT been executed end-to-end
against real data (`DHM_PRECIP_XLSX` unset in every workspace to date). `run_coloc_adjudication()`
itself — the tested core — IS exercised end-to-end against synthetic fixtures
(`tests/unit/scripts/test_dhm_precip_coloc_run.py`), including both pairs, both windows, and the exact
two-station synthesis. Running the real wiring once both data sources are available, and re-verifying
`pyramid_loader.py`'s schema assumptions against the real files, is the tracked follow-on before this
milestone's Exit deliverables (a real H1 verdict, filed as an M-A7 correction if supported) can actually
be produced.

### M-A8 · Elevation and regime structure
**Depends: M-D2 (elevation), M-A6, M-A7.**

Elevation dependence of bias and of intensity/diurnal structure. **Must explicitly bound the
reporting-precision/altitude confound** — Group A is simultaneously the 0.01 mm subset and the
high-altitude subset, so no effect may be attributed to one rather than the other.

**Exit:** elevation relationships with the confound bounded, or a statement that the sample cannot
separate them.

### M-A9 · Synthesis, corroboration, Phase-2 decision
**Depends: M-A6, M-A7, M-A8.**

Consolidate. **Compare against the students' independent findings where available** — agreement
strengthens both, disagreement is itself a finding worth chasing. Their absence does not block this
milestone. State what would have to be true for each Phase-2 option to be viable and whether the
evidence supports it, including "no operational use".

**Exit:** written recommendation; owner decision.

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

Add gauge catch efficiency to `docs/standards/wmo.md`: WMO-168 Vol I Ch.6, WMO-SPICE. Record that
SAPPHIRE applies no numeric catch correction and why (D6).

**Exit:** doc updated; the D6 position reachable from the standards inventory and cross-linked from
the vision.

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

| ID | Milestone | Note |
|---|---|---|
| **M-G1** | Weather-station observation ingest for `StationKind.WEATHER` | `ingest_observations.py:461-462` fetches only RIVER/LAKE. Onboarding already handles WEATHER (`onboarding.py:794`) — ingest is the gap |
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
    {"id": "M-DEC", "depends_on": ["M-A9"], "kind": "decision",
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

- **The high-altitude diurnal signal is unresolved.** Above ~2,500 m our Group A stations match
  neither the literature (Norris: unimodal, ~1500 LT above 3 km) nor our own hill band —
  Olangchunggola peaks 03 UTC, Lukla 02 UTC. Both are the most noise-contaminated stations in the
  sample (54–55 % wet-hour fractions). This is the band that matters most for Dudh Koshi.
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

| # | Question | Decision |
|---|---|---|
| **OD-12** | Must the modeller train on elevation bands so we can correct band-wise? | **No.** Correct the **basin-average** series in the **forcing pipeline**, using **hypsometric weighting** — the elevation dependence enters only through the weights, which are computed offline, once per basin. **The model never changes and keeps consuming `BasinAverageForecast`.** |
| **OD-13** | Where does the correction live, and when is it switched on? | **In the forcing pipeline, behind a seam — built now, enabled at the Nepal re-training step, never before.** See the train/serve invariant below: it is the binding constraint, not the correction itself |

### Why band-wise modelling is not required — the pieces already exist

- `BandRecord` (`types/basin_package.py:109`, from `bands.gpkg`) already carries `min_elevation_m`,
  `max_elevation_m` and **`area_km2`** per band. **That is hypsometry, already in the basin package.**
- `ElevationBandForecast` (`types/weather.py:62`) is already a first-class `WeatherForecastResult`
  variant beside `BasinAverageForecast`, so the band-wise route stays open without being taken now.

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

⇒ **Sequencing:**
1. **Now (pre-deployment):** build the correction operator and its seam. **Do not enable it.** Global
   pre-training stays on uncorrected forcing.
2. **At Nepal deployment/re-training:** apply the **same** operator to the Nepali training forcing *and*
   the operational forcing, then re-train. Consistent *and* closer to physical truth.
3. **Measure both ways at step 2** — corrected-consistent vs uncorrected-consistent — because that is
   the only point where we control both sides and can actually tell which is better.

**And OD-10 remains the better long-run answer:** if DHM's parallel downscaling delivers a
convection-permitting product, that fixes the phase *physically*, and this correction becomes
unnecessary rather than merely adequate. Build the seam so that swap costs nothing.

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

## Decisions register — forcing strategy for operational P (added 2026-08-18)

| # | Question | Decision |
|---|---|---|
| **OD-7** | How do we get long hourly gauge series? | **From project partners — do NOT scrape the public DHM rainfall portal.** The portal is real-time only; starting collection now yields **a couple of months before delivery**, and every use we have (diurnal climatology, seasonality) needs **long** series. A few months of self-collected data is not worth the ingest machinery it would require. **Ask partners for the archive instead** |
| **OD-8** | Lightning data | **Pursue, via partners.** Hourly, real-time, global, and **independent of precipitation retrieval** — it indicates *when* convection occurs without undercatch or orographic-retrieval error, which is exactly the disputed quantity. Best value-per-effort of the timing sources. It gives **no amounts**, which is fine: amounts are not the problem |
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

**➡️ THESE FINDINGS NOW LIVE IN `docs/plans/183-forcing-canonicalisation-seam.md` (DRAFT).**
They were first written here, which was the WRONG home — they are architecture defects, and nobody
building an adapter would look in a precipitation research document. Plan 183 is authoritative;
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
