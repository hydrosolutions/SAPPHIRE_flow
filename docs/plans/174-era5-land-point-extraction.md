---
status: READY
created: 2026-08-15
revised: 2026-08-16 (post-implementation review — 4 plan-level blockers corrected)
plan: 174
title: M-A5 — ERA5-Land point extraction at the 26 station locations
scope: Extract the M-A4 hourly-mm ERA5-Land product at the 26 gauge locations with a NAMED, recorded extraction operator; acquire and freeze an orography source; record per-station grid coordinates and orography elevation; quantify station-to-grid elevation mismatch; run an operator-sensitivity comparison; propagate the IMERG scope split into the milestone doc. Explicitly NOT the gauge-vs-ERA5 comparison (M-A6), NOT IMERG (split to its own plan — no acquisition path exists), NOT any QC-mask application (the mask is the gauge side; pairing is M-A6), NOT the Kirtipur/Khumaltar gauge-pair diagnostic (moved to M-A6 — see D6), NOT a correction design.
depends_on: [171]
blocks: [M-A6]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 174 — M-A5 ERA5-Land point extraction

## Status
**READY — owner-confirmed 2026-08-16.**

Gated by one `/plan` round that **stalled** (3 rounds; 4 blockers, 11 majors). Its design reasoning
was sound and is kept; **all four blockers were defects in machinery the loop itself invented**, and
each is resolved above by removing structure rather than adding more (D5.0, the `OrographySpec` /
`OrographySourceRecord` split, 1b's narrowed scope, D7's three publication corrections).

### Residual review findings — KNOWN, accepted into implementation

Not folded above. The owner set READY with these open; they are recorded so the implementer meets
them deliberately rather than rediscovering them.

| # | Finding | Disposition |
|---|---|---|
| M-7 | The persisted accumulation diagnostic (Task 1c) is not yet a trustworthy gate — `diagnose` doesn't reconcile the raw file against its manifest sha256, and with no `--window` it resolves *all* windows including a one-hour edge window the diagnostic must reject | **Fix in 1c**: require exactly one explicit approved window, reconcile its sha256 before decoding, pin a minimum complete-day count, and store records per window |
| M-8 | The orography raster schema is not actually frozen — no pinned variable name, dims, dtype, encoding or mask variable, and those choices are absent from `orography_identity`; `datum_reconciled` is called an enum but never defined | **Fix in 1b/3a**: pin the raster schema and fold it into the identity; define `DatumReconciliationStatus` and its derivation |
| M-9 | Task 5b must propagate the split through *every* authoritative reference — Plan 171's front matter and out-of-scope section, and the milestone doc's "three-way" and **"bounds"** wording (which now contradicts D1a) | **Widen 5b** to all of them; replace "bounds" with "quantifies operator spread" |
| M-10 | Phase 5 depends only on 4a but Task 5a must record 4b's real-data results — the plan can self-record before the results exist | **Fix the graph**: 5a depends on 4b |
| M-11 | Task 4a's identity test checks only a subset of what D7 declares the identity covers, so a broken implementation passes | **Widen the test** to every declared identity input |
| M-12 | D1a's statistical surface is combinatorial (8 quantiles × season *and* JJAS × absolute *and* ratio *and* sign-agreement × per-station *and* across-station) for a diagnostic that is explicitly not a decision gate | **Proportionality — trim** at implementation; the milestone asks only for "at least one sensitivity comparison" |
| M-13 | 2d's red-first cases partly duplicate behaviour `load_station_coordinates` already provides — the duplicate-station case is handled today, so that assertion proves nothing new | **Keep only genuinely new** cases; wrap the existing error rather than re-implementing it |
| m-1 | `series_bilinear.nc` is a full first-class published artefact though it is only D1a's comparand | Consider demoting it below the published bundle |
| m-2 | "checksum mismatch **before any read/file open**" is literally impossible — checksumming reads the file | Reword to "before the payload is decoded" |
| m-3 | The Problem section says M-A4 "left" the products on disk; Constraint 1 correctly says none exists yet | Internal contradiction — fix the Problem wording |
| m-4 | Two stale citations (the Plan 171 "freeze payload" precedent, and the per-file `os.replace` line reference) | Repoint to the real locations |

### ⛔ POST-IMPLEMENTATION REVIEW (2026-08-16) — 5 blockers, 5 majors, 2 minors

Commit `50dd58d` builds the plan and the **full suite is green (3,753 passed, 0 failed)** with
`ruff`/`pyright`/ratchet clean. It was nevertheless **never reviewed** by the workflow (escalated at
round 0), and the implementer self-reports `acceptanceTestsRedFirst: false` for most of Phase 2–4.
An independent Codex pass plus a manual read then found the following.

**Four of the five blockers are defects in THIS PLAN, not in the code.** Every one of them survived a
fully green suite, because a suite written against a wrong spec cannot fail on it. Each is corrected
in place above:

| # | Blocker | Origin | Corrected in |
|---|---|---|---|
| B1 | Geopotential band bounded the field **minimum**; the Terai (~588 m² s⁻²) is in the box ⇒ Branch A raises on **every** real run | **this plan** | D3a |
| B2 | `orography_identity` covers the route only — a changed source byte-for-byte keeps the identity, stale raster silently reused | **this plan** (the D3a spec/record split moved the hashes out and D7 was not updated — the two clauses contradicted each other) | D7 |
| B3 | "Adopt if the manifest reconciles" was never defined; an **empty payload map reconciles vacuously** and the adopt path **deletes the fresh complete bundle** | **this plan** | D7.3 |
| B4 | Cardinality 26 never enforced — equality against a self-supplied inventory is not a constraint | **this plan** (the 2d single-source decision removed the only pin) | D8/2d |
| B5 | Accumulation diagnostic still not a trustworthy gate: decodes before reconciling the manifest sha256, and the passing predicate ignores `source_sha256`, `terminal_hour`, `sample_size_days` | **recorded as M-7 and set READY over it** | below |

**B5 is a process failure, not a spec bug.** M-7 was written into the residual table with the
disposition "fix in 1c" and READY was set anyway. It came back as a blocker. **Recording a known
defect is not handling it** — a residual that is load-bearing for correctness must either be fixed
before READY or explicitly descoped, never carried as a note. ⇒ **1c is now a required task with
explicit criteria:** exactly one approved `--window`; reconcile its sha256 **before** decoding; the
passing predicate must include `source_sha256`, `terminal_hour` and a minimum `sample_size_days`;
records stored per window.

**Majors, all required in the fixer round:** `zero_policy` hashed-but-unapplied (D1a, corrected
above) · sign-agreement and excluded-hour grain (D1a, corrected above) · the manifest omitting
`OrographySpec` fields, source file hashes/sizes and D11's per-station `n_hours`/`n_finite`/`n_nan`
and first/last-NaN stamps · the D9 NetCDF schema not implemented (variable-length station strings
instead of fixed-length, no semantic UTC attribute, encoding absent from the identity).

**Downgraded on inspection:** the "area-weighted" aggregation is genuinely unweighted, but within one
0.1° cell `cos(lat)` varies ~0.17% — negligible against hundreds of metres of intra-cell relief.
⇒ **Correct the claim, do not implement weighting**; rename the rule to what it is
(`mean_of_contained_cells`) so the manifest stops asserting a method that was not used.

**Minors:** `Era5AcquisitionError` precedes its `Era5StorageError` subclass in the CLI's exit-code
dispatch, so storage errors exit 4 instead of 5, and raw `OSError` escapes to 1 · **and one test that
proves nothing**: `tests/unit/scripts/test_era5_extract.py:312` asserts
`elev_mismatch_m == row.station_elev_m − row.orography_elev_m`, recomputing the implementation's own
formula from its own output — it passes against any wrong orography cell. It must assert an
**independently derived** expected cell and value, and the required 1 m haversine tolerance.

**The lesson worth keeping:** a green suite plus clean static gates said nothing about any of this.
The defects lived in the specification and in tests written after the code, which is precisely the
pair that a test run cannot separate. [[feedback_independent_review_beats_automated_loop]]

**Proportionality note, on the record.** At ~760 lines this plan is heavy for work whose core is
"extract a grid at 26 points," and M-12 is a symptom. The orography Branch-A/Branch-B probe carries
most of the weight (two of the four blockers lived there). Dropping Branch A — the owner has already
accepted a DEM proxy — would collapse that apparatus, at the cost of measuring terrain rather than
what the land-surface scheme actually ran on. Raised and **not** taken; recorded here so the trade
stays visible.

## Scope correction this plan makes to the milestone

M-A5 as written (`docs/design/dhm-precipitation-milestones.md:300-330`) says "**also extract IMERG at
the same 26 points** … nearly free once the extraction pipeline exists," and makes IMERG part of
M-A5's exit. That is **wrong**, though a first statement of *why* was also wrong and is corrected
here.

**⚠️ Correction (review finding, verified).** An earlier revision of this plan claimed "IMERG appears
nowhere in this repo except two design documents." **That is false.**
`docs/publication/paper-0/source-reviews/precipitation_products.md:54` already records three access
routes — NASA GES DISC (OPeNDAP/HTTPS), Google Earth Engine (`NASA/GPM_L3/IMERG_V07`), and **AWS Open
Data** — plus Nepal validation figures directly relevant to this track (*POD decreases with
elevation; underestimation increases above ~3,000 m* — our Group A band exactly). The AWS route needs
**no** Earthdata credential, so the "operator act" barrier was overstated too.

**The real gap, stated accurately:** there is **no implemented, frozen acquisition pipeline** for
IMERG — no request builder, no manifest, no identity, no transform, no tests. What exists is a
documented survey of routes, not code. And the work that remains is still M-A4-shaped: **half-hourly**
native resolution requiring aggregation to hourly, and a **rate (mm/hr) convention rather than an
accumulation**, so none of M-A4's deaccumulation logic transfers. That is a body of work, not a
parameter — which is what justifies the split, on narrower grounds than first claimed.

**Owner decision 2026-08-15: SPLIT.** This plan is ERA5-Land only. IMERG acquisition + extraction
becomes its own milestone (**M-A5b**) and its own plan, mirroring M-A4/M-A5.
**Consequence: M-A6 opens as a two-way comparison** (gauge vs ERA5-Land) and becomes three-way when
the IMERG plan lands. This is the right order anyway — ERA5-Land is the milestone's stated primary
and the one that de-risks Plan 152's OOD concern.

**The split is not real until the authoritative documents say so.** Task 5b does that work
(D12): M-A5's exit and M-A6's dependency text in `docs/design/dhm-precipitation-milestones.md`, the
milestone JSON graph at `docs/design/dhm-precipitation-milestones.md:555-575`, and Plan 171's
forward reference at `docs/plans/171-era5-land-acquisition.md:357-366` ("M-A5 decides, once IMERG's
real API is known, whether to refactor this module or write a sibling") all still route IMERG to
M-A5. A scope correction that lives only in this plan is a scope correction nobody downstream sees.

## Problem

M-A4 built the acquisition and transform pipeline that PRODUCES a per-year gridded product at
`data/dhm_precip/era5_land/hourly_mm/era5_land_tp_mm_{year}.nc`
(`scripts/dhm_precip/era5_manifest.py:48`) — hourly, mm, period-ending UTC, on a 0.1° grid over
26–31 N / 80–89 E (`scripts/dhm_precip/era5_request.py:29`). *(m-3: no such file has actually been
produced yet — Plan 171's real-data run (Task 4b) has not run; see Constraint 1.)* M-A6 needs those
values **at the gauges**, not on the grid. Turning a 0.1° field into 26 point series requires choosing
an operator, and that choice is not neutral for this dataset.

## Measured facts this plan rests on

Run against `data/dhm_precip/station_coordinates.csv` (26 stations, gitignored) on 2026-08-15.
Distances are **haversine on WGS84 spherical radius 6371.0088 km**, recomputed 2026-08-15 (an earlier
revision of this table quoted 3.5 km for the pair below, which was the latitude component only):

| Fact | Value | Why it matters |
|---|---|---|
| Stations inside the acquired box | **26 of 26** | Every station has a real containing cell today. This is a fact about the current table, **not** a safety property — see D11 |
| Elevation range | **67 → 3,700 m** | The mismatch against a smoothed 0.1° orography will be large and elevation-dependent |
| Max offset to nearest grid node | **5.36 km lat / 4.81 km lon** | Up to ~7 km diagonal in a ~10 km cell — nearest and bilinear will differ materially. The operator choice is not cosmetic |
| Grid cells holding >1 station | **1** — Kirtipur (1,364 m) and Khumaltar (1,334 m), **4.33 km** apart | Recorded here as a grid fact (D6). The gauge-pair diagnostic itself moves to M-A6 |

## Design decisions

- **D1 — Nearest cell centre is THE operator. Owner-decided 2026-08-15; locked before any numbers
  were seen.** Precipitation is not a smooth field, and **bilinear interpolation is a weighted
  average of four cells, so it cannot produce a value above the largest contributing cell** — an
  averaging operator cannot preserve a cell-scale maximum. Our track's central finding is that the
  distribution **tail** is what fails to transfer and what a flood system depends on, so an operator
  that averages toward the tail's interior biases exactly the quantity of interest.
  *Precision note (review finding, accepted):* "bilinear systematically damps extremes" is **not** a
  guaranteed pairwise ordering against nearest at every station and hour — a station near a cell
  corner can draw a bilinear value above its own nearest cell. The defensible statement is the
  bounded one above (a convex combination is bounded by its inputs), plus the distributional
  expectation that averaging compresses upper quantiles. D1 rests on the bounded statement; the
  pairwise question is what D1a measures rather than asserts. Nearest preserves the model's own
  distribution shape at the cost of a positional error bounded by half a cell diagonal (~7 km here,
  measured above). The operator is recorded in the extraction manifest (D7), never implied by code.

- **D1a — The operator comparison is an operator-sensitivity envelope, NOT an uncertainty band and
  NOT a decision gate.** Because D1 is locked, the nearest-vs-bilinear comparison no longer selects
  anything — which removes the risk the plan was originally guarding against (choosing, after the
  fact, the operator that flatters the comparison).
  *Correction folded from review:* an earlier revision called this an "uncertainty band that M-A6
  must carry alongside its error characterisation," and said a bias smaller than the operator spread
  "is not a finding." That over-promotes it. **Two deterministic operators are two point estimates;
  they define no probability distribution, no confidence interval, and no bound on true
  representativeness error.** The governing milestone asks only for "at least one sensitivity
  comparison against a second operator" (`docs/design/dhm-precipitation-milestones.md:300-330`).
  ⇒ Report it as a **named sensitivity envelope**, reported **alongside** M-A6's effect estimates as
  context, explicitly **not** as a significance veto on them; report it as a spread, not as a
  ranking; and **do not** phrase any result as "bilinear would have been better".
  **Pinned so the number is reproducible** (all on the frozen parameter object, none inline):
  - **population** — all 26 stations, all six study years (`scripts/dhm_precip/era5_request.py:50`),
    the **unmasked** ERA5 series (D4); per-station results plus an across-station summary
  - **seasons** — the same season definition the gauge side uses (`scripts/dhm_precip/seasons.py`),
    reported per season and for JJAS specifically
  - **zero / wet-hour policy** — `wet_threshold_mm_per_h = 0.2` with `wet_threshold_side = ">="`
    (`scripts/dhm_precip/params.py:43-44`) and `zero_policy = "exclude_zero"`
    (`scripts/dhm_precip/params.py:48`).
    **⛔ CORRECTED 2026-08-16 — pinned, hashed into the identity, and then NOT APPLIED.** The
    implementation computes every quantile over all common-finite hours, while the manifest records
    `zero_policy="exclude_zero"` — **the artefact asserts a policy the computation did not use.**
    Two consequences, the second worse than the first: ERA5-Land is dry most hours, so the lower half
    of the grid collapses toward 0.0 for both operators; and the numbers become **incomparable with
    every other quantile in this track** — all eight M-A1 intensity expectations state
    `zero_policy = "exclude_zero"` over *"JJAS wet-hour (≥ 0.2 mm/h) non-null observations"*
    (`scripts/dhm_precip/expectations.toml:284,303,322,341,781,800,819,835`), which is exactly the
    population M-A6 will want to compare against.
    ⇒ **Quantiles are computed on the WET-HOUR population** (`value >= 0.2`, per operator), never on
    all finite hours. **A parameter that is hashed into the identity but never read is worse than an
    unpinned one** — it is false provenance. The acceptance test must assert the *population*, not
    merely that `quantile_grid` was used (`tests/unit/scripts/test_era5_extract.py:386` asserts only
    the grid, which is why this survived a green suite).
  - **quantile definition and grid** — `quantile_definition = "linear"`
    (`scripts/dhm_precip/params.py:47`) and the existing
    `quantile_grid = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999)`
    (`scripts/dhm_precip/params.py:49`) — reused, not re-invented
  - **delta statistic** — per station and per quantile, report both the **absolute difference**
    `nearest − bilinear` (mm/h) and the **ratio** `nearest / bilinear`, plus the **sign-agreement
    fraction** across stations at each quantile (which is what tells us whether any ordering is
    systematic at all); wet-hour mean intensity and wet-hour frequency reported the same way.
    **⛔ CORRECTED 2026-08-16 — both accounting columns are computed at the wrong grain.**
    Sign agreement is produced only at a hard-coded `q=0.5`, leaving it null for the other seven
    quantiles *and* for both wet-hour statistics — so the one column that reveals whether an ordering
    is systematic is absent everywhere it matters (D1's bilinear-damps-the-tail question lives at
    q0.99/q0.999, not the median). And a **single global excluded-hour count is copied onto every
    row**, so a station-and-season figure silently reports a whole-run total.
    ⇒ **`sign_agreement_fraction` is computed for EVERY (season, statistic, quantile) combination**
    on `ACROSS_STATION` rows, and **`n_hours_excluded` / `n_hours_common_finite` are computed at each
    row's own grain** — per station and season for `STATION` rows, per season for `ACROSS_STATION`.

- **D2 — "Containing cell" and "nearest cell centre" are NOT the same operator and must not be
  conflated.** ERA5-Land values are cell-centre-registered on whole-0.1° nodes. "Nearest node" and
  "the cell whose bounds contain the point" coincide only when the cell is defined as centred on the
  node. State the registration explicitly and assert it against the product's own coordinate vector
  rather than assuming — the Plan 171 lesson (**the first written statement of ERA5-Land's
  accumulation convention was wrong**) applies to registration too.

- **D3 — Orography is NOT in the acquired product, and this is a real gap.**
  `scripts/dhm_precip/era5_request.py:27` acquires `total_precipitation` and nothing else. M-A5's
  exit requires **model-orography elevation per station**, which ERA5-Land does not ship as an
  ordinary hourly variable. The route must be **verified against the service before it is coded**
  (D3a); the plan must not hard-code an assumed dataset name. This is the single largest unknown in
  this plan.

- **D3a — Phase 1 produces a FROZEN OROGRAPHY SPECIFICATION. It branches; it never stops.**
  *(Rewritten from review: the previous revision said "if it needs an operator act, stop and raise
  it," which contradicted the owner's accepted DEM fallback in the same decision and left the
  fallback with no acquisition path at all.)*
  Task 1a is a probe whose **only** exit is a frozen, committed `OrographySpec` record — the same
  discipline Plan 171 used to freeze the CDS payload (m-4, corrected citation:
  `docs/plans/171-era5-land-acquisition.md:92-94`, "## Observed CDS payload — supplied by the
  operator" — the previous citation, `:340-355`, is D11's manifest-atomicity discipline, unrelated).
  It **must** resolve to exactly one of two branches, and "stop" is not one of them:

  **Branch A — model orography (preferred).** ERA5-Land's static surface geopotential. If it is
  reachable with the credentials and licence already accepted in Plan 171's P0, take it.
  **Unit conversion is mandatory and part of the spec:** the field is *surface geopotential*
  Φ in m² s⁻², and elevation is `z = Φ / g0` with **`g0 = 9.80665 m s⁻²`** (the WMO standard
  gravity). The spec records whether the retrieved field is geopotential or already metres; a
  retrieved field whose magnitude is inconsistent with the declared unit is a **typed failure**, not
  a silent divide.

  **⛔ CORRECTED 2026-08-16 — the first band was wrong and would have failed every real run.**
  The original wording ("Nepal box: metres ⇒ tens to thousands; geopotential ⇒ 10⁴–10⁵") was applied
  as a bound on the field **minimum**, and the acquired box (26–31 N) contains the **Terai lowlands**:

  | | elevation | geopotential = z·g0 |
  |---|---|---|
  | Terai lowland (in box) | ~60 m | **~588 m² s⁻²** |
  | Everest | 8,849 m | ~86,779 m² s⁻² |

  `10000 <= 588` is false, so **Branch A raises `Era5OrographyError` on any real field.** Synthetic
  fixtures were chosen in-band, so no test catches it.

  **The minimum carries no signal — it is small under both units. Discriminate on the MAXIMUM:**
  - `field_max > 9_999` ⇒ the field is **geopotential** (Everest ⇒ ~86,779) → apply `Φ/g0`
  - `field_max <= 9_999` ⇒ the field is **already metres** (Everest ⇒ 8,849) → identity
  - the declared `conversion_rule` must **agree** with that classification, else typed failure —
    this is the check, replacing the min-bound entirely
  - a lower sanity bound may only reject the physically impossible: `field_min < -500 m`
    (or `< -5000 m² s⁻²`) ⇒ typed failure, which catches an unmasked no-data sentinel such as −32768

  Vertical reference: as documented by the producer, recorded verbatim in the spec.

  **Branch B — public DEM proxy (owner-accepted fallback, taken whenever Branch A needs a further
  operator act or is unreachable in this window).** The probe selects **exactly one** product from
  the ordered candidate list below, by the acceptance criteria below, and freezes it. The plan
  deliberately does **not** hard-code a URL it has not observed — that is the Plan 171 rule
  (constraint 3) and it binds here too; what the plan hard-codes is the **criteria, the ordering, and
  the required contents of the frozen record**.

  *Ordered candidates* (first that satisfies every acceptance criterion wins; the probe records why
  each rejected candidate was rejected):
  1. **Copernicus DEM GLO-90 (COP-DEM_GLO-90-DGED / DTED), current public release** — anonymous
     access via the AWS Open Data mirror; ellipsoidal→EGM2008 orthometric as published; global.
  2. **GMTED2010, 7.5-arc-second `mean` layer, USGS** — public-domain US Government work, direct
     public HTTP tiles, no account.
  3. **ETOPO 2022, 15-arc-second bedrock/surface, NOAA NCEI** — public domain, direct public HTTP,
     coarser than the two above and therefore last.

  *Acceptance criteria, all mandatory:*
  - **No operator act** — anonymous, unauthenticated download. (A fallback that needs a second
    account is not a fallback; it is Branch A's problem again.)
  - **Full coverage** of the acquired box 26–31 N / 80–89 E with no gap tiles.
  - **Redistributable-free licence**, name and version recorded. (We publish *derived per-station
    elevations*, not the DEM.)
  - **Documented CRS and vertical reference** (geoid model or ellipsoid), recorded verbatim.
  - **Documented units and no-data sentinel**, recorded verbatim.

  **TWO records, because one cannot exist.** *(Blocker, folded from review: the previous revision put
  "sha256 of every downloaded file" inside a spec frozen by 1a, while 1a explicitly does not download
  — and then had 1b refuse to run without those hashes. Unsatisfiable in that order.)*

  - ***`OrographySpec`* — frozen by 1a, describes the ROUTE, contains no hashes:** product id and
    version/release · exact observed download URL(s) or tile pattern · licence name + version + a
    URL · source CRS · vertical reference · units · no-data sentinel · the **aggregation rule**
    (below) · the **conversion rule** (Branch A's `Φ/g0`, or `identity` for a metric DEM) · probe date
    and the rejected-candidate log.
  - ***`OrographySourceRecord`* — written by 1b, describes what was actually MATERIALISED:** the
    `OrographySpec` it was fetched under, plus **sha256 of every downloaded file**, byte sizes, and
    the fetch timestamp (injected clock).

  1b fetches **per the spec**, then writes the record; it refuses only if the *spec* is absent or
  invalid. A re-run verifies observed hashes against the **existing record** (an unexplained change
  in a "static" source is a typed failure), which is the check the previous wording was reaching for.

  *Aggregation / resampling rule (frozen, identical for both branches where applicable):*
  reproject to EPSG:4326 if not already; then **area-weighted arithmetic mean of source cells whose
  centres fall inside the 0.1° ERA5-Land cell**, computed per ERA5-Land cell over the acquired box.
  **No-data policy:** a target cell with **any** contributing no-data source cell is aggregated over
  the valid remainder and **flagged**; a target cell with **>5 % no-data by area, or zero valid
  source cells, is emitted as NaN and is a typed failure if any station's cell is affected.**
  *Exact ERA-grid validation:* the aggregated raster must reopen with **the identical latitude and
  longitude coordinate vectors** as a D9-schema product (element-wise, to 1e-9 deg) — reusing the same
  spacing/shape checks the transform already enforces
  (`scripts/dhm_precip/era5_request.py:40`, `scripts/dhm_precip/era5_deaccumulate.py:455-470`).
  **Station-cell finiteness is NOT checked here.** *(Blocker, folded from review: 1b both required
  finiteness "at all 26 station cells" and declared per-station lookup out of scope, and it cannot
  compare against a real product that Plan 171 Task 4b has not yet produced.)* 1b validates only the
  **aggregated grid and its no-data mask**, against the **synthetic** D9-conformant fixture from 2a.
  Per-station finiteness moves to **3a**, which already depends on 2c/2d and is where station lookup
  legitimately lives. Materialising against the *real* product is an operator step gated on Plan 171
  Task 4b, exactly like D10's real-data gate.

  **The branch label is load-bearing, not a caveat.** Model orography is what the ERA5-Land
  land-surface scheme actually ran on; a DEM aggregate is what the terrain actually is; the
  difference between them is a real part of the model's error. The label therefore appears as an
  **enum field in the elevation table** (per CLAUDE.md — never a boolean):
  `OrographySource ∈ {MODEL_OROGRAPHY, DEM_PROXY}`, plus the product id and version, so no downstream
  consumer can read the number without reading its provenance. **M-A5 does not block on the real
  thing** — but it does block on a frozen spec.

- **D3b — Station elevation has no known vertical datum, and the mismatch must say so.**
  `StationCoordinate.elev_m` (`scripts/dhm_precip/domain_types.py:68`) is a bare float; the delivered
  coordinate table (`data/dhm_precip/station_coordinates.csv`, Plan 170 D12) carries no datum, and
  DHM has not stated one. This plan **does not change** the Plan 170 D12 type or its CSV contract —
  that would re-open a settled boundary. Instead the elevation table carries its own
  `VerticalDatum ∈ {EGM96, EGM2008, WGS84_ELLIPSOID, LOCAL_MSL, UNKNOWN}` on **both sides**:
  `station_elevation_datum = UNKNOWN` (today's honest value, until M-D2 or DHM says otherwise) and
  `orography_elevation_datum` from the frozen spec. The mismatch column is therefore explicitly a
  **datum-unreconciled difference**, and a row is emitted with that flag rather than pretending the
  two numbers share a reference. Geoid–ellipsoid separation over Nepal is tens of metres — the same
  order as the 30 m Kirtipur/Khumaltar difference — so this is a material label, not pedantry.

- **D4 — Extraction never applies the QC mask.** The M-A3 mask (Plan 173) is the **gauge** side.
  Applying it here would bake a pairing decision into the ERA5 artefact and make it unusable for
  anything else. Rule 1 (MNAR) binds M-A6, which must apply the mask **identically to both sides at
  pairing time**. This plan produces a complete, unmasked ERA5 series per station.

- **D5 — Time axis: validate what the artefact can actually prove; source the semantic claim from
  provenance.** *(Rewritten from review.)* M-D3 established the gauge convention is **period-ending**,
  and ERA5-Land is also period-ending, so they align with **no offset** — the fact that removed the
  ±1 h uncertainty blocking all diurnal work.
  The previous revision said extraction "asserts the product's own stamp convention." **It cannot.**
  The transform writes `period_ending_convention` into the product's attributes unconditionally
  (`scripts/dhm_precip/era5_transform.py:314-322`), so reading it back proves only that the producer
  wrote the expected string. Splitting the claim in two:
  **D5.0 — On-disk time is CF-encoded and timezone-NAIVE. Do not "fix" this.**
  *(Blocker, folded from review, which probed the pinned encoder directly and got a `TypeError`.)*
  M-A4 deliberately writes naive `numpy.datetime64` values that *denote* UTC, with integer CF time
  encoding (`scripts/dhm_precip/era5_deaccumulate.py:410`,
  `scripts/dhm_precip/era5_transform.py:79,102`). A tz-aware coordinate **cannot be written by the
  pinned xarray/h5netcdf encoder at all**, and a validator that rejects a naive axis would reject
  **every real M-A4 product**. ⇒ NetCDF stays CF-encoded naive-denoting-UTC; validation checks the
  **epoch encoding plus the semantic UTC attribute**; conversion to `UtcDatetime` happens only at the
  Python/domain boundary, per the repo's `ensure_utc()` convention. There is no "naive axis raises"
  test.

  1. **What extraction validates itself (mechanical, and genuinely checkable):** the `valid_time`
     axis is strictly increasing, uniquely stamped, **exactly hourly** with no gaps, CF-encoded with
     a UTC epoch and the semantic UTC attribute (D5.0 — *not* tz-aware on disk);
     coverage spans the full requested years with the expected stamp count; the per-year source file
     **sha256 matches the acquisition manifest** (`scripts/dhm_precip/era5_manifest.py:59`, `:190`);
     and the declared `period_ending_convention` / `accumulation_rule` / `output_schema_version`
     attributes are present and equal to the expected literals. A mismatch is a typed error.
  2. **What establishes the physical stamp semantics (a prerequisite, not a check):** the **empirical
     accumulation diagnostic run against real data** —
     `diagnose_accumulation_convention` (`scripts/dhm_precip/era5_deaccumulate.py:164`), reached via
     `--stage diagnose` (`scripts/dhm_precip/acquire_era5.py:244-259`). Today that CLI **logs a
     result and persists nothing**, so there is no trusted record for M-A5 to consume.
     ⇒ **Task 1c** makes `--stage diagnose` write an `AccumulationDiagnosticRecord`
     (window id · source sha256 · observed `reset_hour` / `terminal_hour` /
     `monotone_within_day` / `sample_size_days` · injected-clock timestamp) into the acquisition
     manifest, and **Task 4a's real-data path refuses to publish** unless the manifest carries a
     passing record for a real window. Under Plan 171's proposed 2b sample that window is
     **2021-10** (`docs/plans/171-era5-land-acquisition.md:444`); any real window is accepted.
     M-A5 **cites** that record; it does not claim to have re-established the semantics.

- **D6 — The Kirtipur/Khumaltar gauge-pair diagnostic MOVES to M-A6. This plan emits only the ERA-side
  grid fact.** *(Changed from the previous revision, on two independent review findings.)*
  The grid fact is in scope and stays here: the two stations **fall in one 0.1° cell**, are
  **4.33 km** apart (haversine, `data/dhm_precip/station_coordinates.csv:14-15`), and differ by
  **30 m** in elevation — so ERA5 returns one identical series for both. The extraction emits
  `shared_cell_id` and `stations_in_cell` on the elevation/grid table, which is a pure ERA-side
  property and needs no gauge data.
  **What is removed, and why.** The previous revision called their *observed gauge disagreement* a
  "pure lower bound on within-cell representativeness error … no model error, no operator choice
  enters it." That claim does not survive contact with this repo:
  - It **loads gauge data inside a plan whose own D4 forbids gauge/mask coupling** and whose scope
    line excludes mask application — the diagnostic needs the M-A3 mask to be meaningful, which makes
    it a pairing operation, i.e. M-A6's job by definition.
  - It **omits gauge measurement/QC error**, which is a first-order confound **at exactly this pair**:
    Khumaltar totals **294 mm in 2023 vs 1,504 mm in 2024**
    (`scripts/dhm_precip/expectations.toml:434-464`), a ~5× swing between adjacent years at a station
    whose 30 m elevation difference from its partner cannot explain it.
    **⚠️ Precision (folded from review — an earlier revision of this bullet, and a verbal summary of
    it, over-attributed the cause).** Those expectations record the two annual totals and **name no
    mechanism**; the M-A3 long-zero-run population does not name Khumaltar. So the correct statement
    is: *the swing is documented, its cause is not.* That is still disqualifying for a "clean bound"
    — an unexplained 5× inter-annual swing is exactly the kind of gauge behaviour that would dominate
    a small representativeness signal — but it is **not** evidence of a specific defect, and must not
    be cited as one.
    Plan 170 quotes the raw pair disagreement at ~37 % of seasonal total
    (`docs/plans/170-dhm-precip-reproducible-baseline.md:55-58`) — the *same order* as the known
    defect. Unmasked, the "representativeness bound" would be dominated by a gauge malfunction.
  - Even fully masked, gauge-to-gauge disagreement carries **both** stations' residual measurement
    error; the triangle inequality supports only the weaker conditional statement below, not a bound
    on representativeness alone.
  - It contradicts the milestone's own position that representativeness is "characterised, not
    decomposed" (`docs/design/dhm-precipitation-milestones.md:344`).

  **Handoff to M-A6 (this is the deliverable, and Task 5b writes it into the milestone doc):**
  M-A6 computes it under the name **"within-cell observed gauge variability"**, on timestamps
  **retained by the M-A3 mask for both stations simultaneously**, reporting the common-retained hour
  count and each station's exposure alongside every statistic, and stating the implication only in
  its conditional form: *if both gauges are unbiased over the retained sample, half the observed
  pair discrepancy is a lower bound on the within-cell representativeness contribution at ~4.3 km
  separation.* Honest limits travel with it: **n = 1 pair**, one valley, one separation — never a
  network-wide estimate.
  **Trade-off noted:** M-A5 therefore exits without an empirical representativeness number. That is
  the correct trade — the number was not computable here without violating D4, and computing it
  unmasked would have shipped a known-contaminated figure under a "clean named diagnostic" label.

- **D7 — Deterministic and regenerable, with a COMPLETE identity over a bundle published as a unit.**
  Mirror M-A4's two-identity pattern (`scripts/dhm_precip/era5_manifest.py:87`, `:95`), extended
  where review showed M-A4's shape is insufficient here.
  **Two identities:**
  - **⛔ CORRECTED 2026-08-16 — this clause contradicted D3a and opened a provenance hole.** It said
    `orography_identity` = sha256 of the `OrographySpec` "**including source sha256s**" — but the D3a
    fix had just *moved* those hashes out of `OrographySpec` into `OrographySourceRecord`. The
    implementation resolved the contradiction by following D3a, so the identity covers the **route
    only**. Consequence: **a source file whose bytes change at the same URL does not change the
    identity**, and the stale raster is silently reused. Split cleanly instead:
    - `orography_route_identity` = sha256(canonical-JSON of `OrographySpec` — product id + version,
      URL/tile pattern, licence, CRS, vertical reference, units, no-data sentinel, aggregation rule,
      conversion rule, probe date, rejected-candidate log — plus `orography_schema_version` and
      `orography_code_version`). Computable at **1a**, before anything is downloaded.
    - `orography_identity` = sha256(`orography_route_identity` **+ every `OrographySourceRecord`
      file sha256 + the derived raster's own sha256 + the frozen raster schema** — variable name,
      dims, dtype, encoding, mask/flag variable, per M-8). Computable only at **1b**, which is where
      the materialised bytes first exist. This is the one `extraction_identity` consumes.
    - **A materialised raster is never trusted because a file of the right name exists.** On every
      run, re-verify the source hashes against the record and the raster's own sha256 against the
      manifest; a mismatch is a typed failure, not a silent reuse.
  - `extraction_identity` = sha256(canonical-JSON of **every value-affecting input**):
    operator id · station-coordinate-table sha256 · the **list of source product sha256s consumed**
    (validated against the acquisition manifest before use, never trusted from the filename) ·
    `orography_identity` · the D1a sensitivity-parameter snapshot (seasons, wet threshold + side,
    zero policy, quantile definition + grid, delta statistics) · the datum enums of D3b ·
    `output_schema_version` · output format/dtype/**full** encoding spec · `extraction_code_version`.
    *A version bump alone forces regeneration*, exactly as `transform_identity` does
    (`scripts/dhm_precip/era5_manifest.py:110-115`).
  **Publication is a bundle, and per-file `os.replace` is not atomic across a bundle** (review
  finding — M-A4 gets away with it because its unit *is* one file plus a manifest; the actual
  `os.replace`-via-`publish_atomic` call is `scripts/dhm_precip/era5_transform.py:366`, m-4 corrected
  citation — the previous citation, `:336`, is the `to_netcdf` write call, not the publish step).
  Therefore:
  write **all** outputs into a staging directory keyed by the extraction identity
  (`…/era5_land/points/.staging/<extraction_identity>/`), reopen-and-validate every file there, then
  publish the completed directory once, then switch the pointer. Three constraints the previous
  revision got wrong (**blocker, folded from review**):

  1. **The manifest hashes PAYLOAD FILES ONLY — never itself, never the pointer.** "Every output's
     sha256" included `extraction_manifest.json` and `CURRENT`, which is not constructible: a
     manifest cannot contain its own hash. The manifest lives *inside* the identity directory and
     covers the payload artefacts beside it.
  2. **The pointer lives at `…/era5_land/points/CURRENT`, one level ABOVE the identity
     directories.** The previous revision placed it inside `<extraction_identity>/`, where it can
     only ever name its own parent and therefore selects nothing. It is written adjacent-temp +
     `os.replace` (a file, so genuinely atomic).
  3. **`os.replace` cannot replace a non-empty directory with another non-empty directory.** So the
     "regenerate an existing identity" path is not a replace at all. If `<extraction_identity>/`
     already exists: **validate and adopt it** if it reconciles, otherwise **quarantine it**
     (rename to `<extraction_identity>.orphan-<n>/`) and publish fresh. A staging directory with no
     pointer is unreferenced garbage the next run deletes; a published directory with no matching
     manifest entry is **re-validated, never assumed good** (Plan 171 D11's rule).

     **⛔ CORRECTED 2026-08-16 — "reconciles" was never defined, and the loose reading DESTROYS the
     good bundle.** As implemented, reconcile iterates only whatever `payload_sha256s` the existing
     manifest happens to list — so **an empty payload map reconciles vacuously** — and the adopt path
     then *deletes the freshly generated, complete staging directory* in favour of the incomplete
     published one. Adoption is the dangerous branch, not the safe one, so it must be the
     hard-to-satisfy one. **Reconcile means ALL of:**
     1. the manifest parses, and its recorded identity **equals the directory name**;
     2. `payload_sha256s` is **non-empty** and its key set **equals exactly** the D9 payload set
        (`series_nearest.nc`, `series_bilinear.nc`, `station_grid_elevation.csv`,
        `operator_sensitivity.csv`) — no missing entries, no extras;
     3. every listed file exists and its sha256 matches;
     4. the **D9 reopen-and-validate bundle validator passes** on the directory — the same validator
        the staging path must pass before publication. A published bundle is held to the identical
        standard as a fresh one.

     **Any failure ⇒ quarantine and publish fresh. Never delete staging before adoption has fully
     succeeded** — the ordering must be validate-then-discard, not discard-then-adopt.

- **D8 — Station identity is the coordinate table's `station` column,** the same key the gauge side
  uses (`scripts/dhm_precip/loader.py:251` loads and validates it; `:306` already asserts the
  coordinate station set matches the live station set). Assert the extracted station set **equals**
  the 26-station coordinate set exactly, with **no duplicates** and **exactly one row per station**
  in every emitted table — a silent partial join here would corrupt every M-A6 pairing downstream.

- **D9 — The outputs, the layout and the CLI are part of the contract, not an implementation
  detail.** *(New — the previous revision named no schemas, so "regenerable" was unverifiable.)*
  Under `data/dhm_precip/era5_land/points/<extraction_identity>/`:

  | Artefact | Format | Key / dims | Required columns / vars |
  |---|---|---|---|
  | `series_nearest.nc` | NetCDF (h5netcdf) | `(station, valid_time)` | `precipitation_mm_per_h` float32; `station` a fixed-length string coord; `valid_time` **CF-encoded naive-denoting-UTC** hourly (D5.0 — matches M-A4; tz-aware is unwritable by the pinned encoder) |
  | `series_bilinear.nc` | NetCDF | same | same — the sensitivity comparand only (D1a), never the primary |
  | `station_grid_elevation.csv` | CSV | one row per station, **26 rows** | `station`, `lat`, `lon`, `grid_lat`, `grid_lon`, `grid_i`, `grid_j`, `offset_km`, `station_elev_m`, `station_elevation_datum`, `orography_elev_m`, `orography_elevation_datum`, `orography_source` (enum), `orography_product_id`, `orography_product_version`, `elev_mismatch_m`, `datum_reconciled` (enum), `shared_cell_id`, `stations_in_cell` |
  | `operator_sensitivity.csv` | CSV | `(scope, station, season, statistic, quantile)` | `scope` enum {`STATION`, `ACROSS_STATION`} · `station` (null when `ACROSS_STATION`) · `season` · `statistic` enum {`QUANTILE`, `WET_MEAN_INTENSITY`, `WET_FREQUENCY`} · `quantile` (null unless `QUANTILE`) · `nearest_value`, `bilinear_value` · `delta_absolute` + `delta_unit` enum {`MM_PER_H`, `FRACTION`} (**wet frequency is a fraction, not mm/h** — the previous single `delta_mm_per_h` column mislabelled it) · `ratio` (null when the bilinear denominator is 0) · `n_hours_common_finite` · `n_hours_excluded` (D11.3) · `n_wet_nearest`, `n_wet_bilinear` (**both**, since the wet set differs by operator — one `n_wet_hours` was ambiguous) · `sign_agreement_fraction` (populated only on `ACROSS_STATION` rows) |
  | `extraction_manifest.json` | JSON | — | both identities, operator id, every input sha256, **the payload artefacts' sha256s only — never its own, never the pointer's** (D7.1), the cited `AccumulationDiagnosticRecord`, the frozen `OrographySpec` + `OrographySourceRecord`, injected-clock timestamps |

  And **one level above**, outside the identity directories (D7.2):

  | Artefact | Format | Location | Contents |
  |---|---|---|---|
  | `CURRENT` | text pointer | `data/dhm_precip/era5_land/points/CURRENT` | the published extraction identity — the single atomic switch. **Not** inside `<extraction_identity>/`, where it could only name its own parent |

  **CLI:** `scripts/dhm_precip/extract_era5.py`, mirroring `acquire_era5.py`'s shape —
  `--stage {orography,extract,sensitivity,all}`, `--data-root`, `--out`.
  **Exit codes** (mirroring `docs/plans/171-era5-land-acquisition.md:510`): `0` success ·
  `2` inputs absent (no product / no coordinate table / no orography spec) · `3` orography
  acquisition or validation failed · `4` an extraction post-condition failed (bounds, NaN, station
  set, axis, checksum) · `5` storage/manifest write failed.
  **Typed errors**, extending the existing hierarchy in `scripts/dhm_precip/era5_errors.py:12`:
  `Era5ExtractionError(Era5AcquisitionError)`; `Era5OrographyError`,
  `StationOutsideGridError`, `NonFiniteExtractionError`, `StationSetMismatchError`,
  `SourceChecksumMismatchError`, `ExtractionPostConditionError` beneath it. No bare `except`.

- **D10 — The real-data gate is opt-in by its own variable, and it is gated on Plan 171 Task 4b —
  not 2b.** *(Corrected on review: 171 Task 2b is acquisition-only for a single October window and
  "is never transformed into a product" — `docs/plans/171-era5-land-acquisition.md:444-455`. The six
  annual `hourly_mm` files this plan consumes are produced by **171 Task 4b**,
  `docs/plans/171-era5-land-acquisition.md:523`.)
  Mirroring the `DHM_PRECIP_XLSX` pattern (`tests/integration/test_dhm_precip_reproduction.py:37`):
  **`DHM_PRECIP_ERA5_ROOT` unset is the ONLY skip condition.** When it is set, the gate must **fail**
  — never skip, never warn — on: fewer than the six expected annual files; any file whose sha256
  disagrees with the acquisition manifest; a D9 schema-validation failure on reopen; incomplete
  temporal coverage of any study year; a missing or failing `AccumulationDiagnosticRecord` (D5);
  any station outside the grid (D11); or a non-finite extracted value (D11).

- **D11 — Explicit bounds and NaN post-conditions. There is no "no silent-NaN class".**
  *(New — the previous revision's claim that an out-of-box station "would extract as NaN rather than
  error" was wrong in both directions.)*
  1. **Out-of-bounds does not yield NaN.** The documented nearest path,
     `.sel(..., method="nearest")` (`docs/design/dhm-precipitation-milestones.md:253`), snaps an
     out-of-range target to the **nearest boundary coordinate** and returns a real number — silently
     relocating the station. ⇒ **Before any extraction**, assert each station's lat/lon lies within
     the product's coordinate range (plus a half-spacing registration allowance, D2) and raise
     `StationOutsideGridError` with the station, its coordinate and the bounds otherwise. The 26/26
     containment measured above is a *fact about today's table*, not a guarantee.
  2. **Source NaN is permitted by the M-A4 schema and can reach a station.**
     `validate_output_schema` fails only when the field is *entirely* non-finite
     (`scripts/dhm_precip/era5_deaccumulate.py:471-479`), so a partially masked field is a valid
     product and an inside station can select a masked cell.
     ⇒ **After each operator**, report per station and in total: `n_hours`, `n_finite`, `n_nan`, and
     the first/last NaN stamp. **Policy: any NaN at any station is a typed failure**
     (`NonFiniteExtractionError`) for the primary nearest series — ERA5-Land over land in this box
     should be complete, so a NaN means something is wrong upstream, and M-A6 must not silently
     average over holes. The counts are emitted in the manifest either way.
  3. **Bilinear needs all four neighbours.** A single NaN contributor propagates. ⇒ validate that
     **all four** contributing cells are finite; if not, emit NaN for that (station, hour), **count
     it**, and record the count in the manifest. The sensitivity comparison (D1a) is computed on
     hours finite under **both** operators, and reports the excluded-hour count. This is the
     documented missing-neighbour policy; it never silently substitutes nearest.

- **D12 — The IMERG split must land in the authoritative documents, in this plan.** Task 5b edits
  `docs/design/dhm-precipitation-milestones.md` (M-A5 exit loses IMERG; M-A6's "three-way" becomes
  two-way-now/three-way-when-M-A5b-lands; a new **M-A5b · IMERG acquisition + extraction** milestone
  is added with `depends_on: ["M-D2"]` and *no* edge into M-A6's current exit; the JSON graph at
  `:555-575` updated to match) and adds a superseded note at
  `docs/plans/171-era5-land-acquisition.md:357-366`. Without this, the milestone doc still makes
  IMERG part of M-A5's exit and M-A5 can never be marked complete.

- **D13 — `blocks` is M-A6 only.** The previous revision listed M-A7. The authoritative graph makes
  **M-A7 depend on M-A2 and M-A3** (`docs/design/dhm-precipitation-milestones.md:564`), not M-A5.
  Corrected in the front matter.

## Constraints

1. **No real ERA5-Land product exists on disk yet** — Plan 171's Task 4b has not run. This plan is
   therefore built and tested **against synthetic NetCDF fixtures** conforming to the M-A4 D9 schema,
   exactly as Plan 171 built acquisition ahead of the real call. The real-data gate is authored and
   **skips only while `DHM_PRECIP_ERA5_ROOT` is unset** (D10).
2. **The repo is public.** Station coordinates and extracted series are gitignored research data
   (`data/dhm_precip/`). Test fixtures are **synthetic**; no real coordinates in committed tests.
   The DEM/orography raster is **not** committed — only derived per-station elevations, and those
   only into the gitignored data root.
3. **Verify against the service, not the documentation** (Plan 171's hardest-won rule) — binds D2,
   D3a and D5.
4. **`scripts/` is outside the default type gate.** `pyrightconfig.json:2` includes only `src`, and
   the pre-push ratchet runs `pyright src/`. Strict mode still applies to explicitly-named files, so
   every task's gate runs **`uv run pyright scripts/dhm_precip/`** explicitly, as Plans 170/171/173
   already do (`docs/plans/171-era5-land-acquisition.md:381`).
5. **Dev-only dependencies.** Any new raster dependency needed by Branch B must be `uv add --dev`
   (Plan 171 D13) — the production image installs `--no-dev` (`Dockerfile:32`). Prefer the stack
   already present (`xarray`, `rioxarray`, `h5netcdf`) and add nothing if it suffices.

## Out of scope

The gauge-vs-ERA5 comparison and every estimand it names (M-A6) · the Kirtipur/Khumaltar gauge-pair
diagnostic (moved to M-A6 by D6) · applying the M-A3 mask to anything (D4) · IMERG acquisition or
extraction (M-A5b) · any bias-correction or downscaling design · changing Plan 170's D12
`StationCoordinate` type or CSV contract (D3b) · changing Plan 171's transform, request or manifest
*semantics* (Task 1c only **adds** a diagnostic record; it changes no existing field) · committing any
DEM raster or real station coordinate to the repo.

## Phases and tasks

Every code task additionally runs the standard gate: `uv run ruff format`, `uv run ruff check --fix`,
`uv run pyright src/`, **`uv run pyright scripts/dhm_precip/`**, `uv run pytest`, and affected docs
updated in the same change.

### Phase 1 — freeze the unknowns

**1a — probe and freeze the `OrographySpec`.** *(probe; no production code beyond the frozen record
and its type.)*
*In scope:* determine Branch A's reachability with Plan 171's existing credentials/licence; if
unreachable or operator-gated, walk the D3a candidate list and select the first product meeting every
acceptance criterion; download nothing beyond what is needed to observe URL shape, licence text,
CRS/datum/units/no-data; write the frozen record and the rejected-candidate log into this plan under
a new "## Observed orography route" section **and** as
`scripts/dhm_precip/era5_orography_spec.py` (a frozen dataclass literal, per CLAUDE.md — not JSON in
a docstring).
*Out of scope:* downloading the full raster, aggregating, or touching extraction.
*Target files:* `docs/plans/174-era5-land-point-extraction.md`,
`scripts/dhm_precip/era5_orography_spec.py`, `scripts/dhm_precip/domain_types.py` (add
`OrographySource`, `VerticalDatum` enums).
*Verification:* `uv run pytest tests/unit/scripts/test_era5_orography.py -k spec` — the frozen spec
instance constructs; **`__post_init__` rejects** a blank product id, a non-`https` URL, a missing
sha256, an unrecognised `VerticalDatum`, and (Branch A) a conversion rule other than `geopotential_g0`
or `identity`; `OrographySource` and `VerticalDatum` are `StrEnum`s with the exact members D3a/D3b
name.
*Escalation, not stopping:* if **no** candidate meets the criteria, that is an owner ask raised
immediately with the rejected-candidate log attached — and Phase 2 continues regardless (see the
dependency note below).

**1b — acquire, convert, aggregate and validate the orography raster.** *(depends on 1a **and 2a**,
for the synthetic D9 fixture it validates against)*
*In scope:* fetch per the frozen `OrographySpec`; **write the `OrographySourceRecord`** (observed
sha256s, sizes, injected-clock fetch time), and on a re-run verify observed hashes against the
existing record; apply the conversion rule (`Φ/g0` with `g0 = 9.80665`, or identity) with the
magnitude sanity check of D3a; reproject if needed; area-weighted-mean aggregate to the ERA5-Land
0.1° grid; apply the no-data policy; assert coordinate-vector equality **against the synthetic
D9-schema fixture from 2a**; write
`data/dhm_precip/era5_land/orography/<orography_identity>.nc` + its record.
*Out of scope:* per-station lookup **and per-station finiteness** (both are 3a); comparison against a
real M-A4 product (gated on Plan 171 Task 4b); any network call not named in the frozen spec.
*Target files:* `scripts/dhm_precip/era5_orography.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_orography.py` —
a synthetic 0.01° source raster with a known analytic surface aggregates to **hand-computed** 0.1°
means; a **geopotential-valued** input converts to metres at `g0` and a metres-valued input declared
as geopotential **raises** the magnitude check; a re-fetch whose sha256 disagrees with the **existing
`OrographySourceRecord`** raises `Era5OrographyError` before the payload is decoded (m-2: checksumming
necessarily reads raw bytes — the guarantee is no *decode* of those bytes as a raster happens first; a
first fetch, with no prior record, succeeds and writes one); a target cell with 10 % no-data area is **NaN and flagged**, while
2 % aggregates over the remainder and sets the flag; an aggregated grid whose lat/lon vectors differ
from the 2a fixture's in the last element by 1e-6 deg **raises**; the happy path reopens and
revalidates.

**1c — persist the accumulation diagnostic into the acquisition manifest.** *(independent of 1a/1b)*
*In scope:* add `AccumulationDiagnosticRecord` to `Era5ProvenanceManifest`
(`scripts/dhm_precip/era5_manifest.py:203`) and have `--stage diagnose`
(`scripts/dhm_precip/acquire_era5.py:244`) write it atomically, with the injected clock; a helper
that answers "does this manifest carry a passing real-data diagnostic?".
*Out of scope:* changing the diagnostic's algorithm, thresholds, or any existing manifest field; the
CLI's exit-code contract.
*Target files:* `scripts/dhm_precip/era5_manifest.py`, `scripts/dhm_precip/acquire_era5.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_manifest.py
tests/unit/scripts/test_acquire_era5_cli.py` — a diagnose run writes a record carrying window id,
source sha256, the four observed fields and a frozen injected-clock timestamp; the manifest
round-trips through pydantic with the new field; **a manifest written before this change (no record)
still loads**, and the helper reports "absent" rather than raising; a failing diagnostic still raises
`Era5TransformFailedError` as today and writes **no** passing record.

> **Dependency note (narrowed on review).** The previous revision made all of Phase 2 depend on 1a.
> None of 2a–2d touches orography — only 3a does. Since D3a names the probe as this plan's largest
> stall risk, gating the whole extraction core on it was the wrong structure. **Only 3a depends on
> the orography chain (1a → 1b).** Phase 2 depends on nothing in Phase 1.

### Phase 2 — extraction core *(no dependency on Phase 1)*

**2a — synthetic ERA5-Land fixture builder.**
*In scope:* build D9-schema-conformant NetCDF products (correct dims, coord names, units, the
required attrs `scripts/dhm_precip/era5_transform.py:314-322` writes, the real encoding spec) over a
small sub-box, filled with a **known analytic field** (a linear ramp in lat/lon, so nearest and
bilinear are both hand-computable) plus variants: a NaN patch, a single-hour gap, a duplicate stamp,
a non-hourly stride, a truncated year.
*Out of scope:* any real coordinate or real data; the operators themselves.
*Target files:* `scripts/dhm_precip/fixtures.py` (extend), `tests/unit/scripts/test_era5_extract.py`.
*Verification:* `uv run pytest tests/unit/scripts/test_era5_extract.py -k fixture` — the clean
fixture **passes** `validate_output_schema` (`scripts/dhm_precip/era5_deaccumulate.py:380`) and each
defective variant fails it for the expected reason (proving the fixtures are defective in the way the
later tests assume, not in some other way).

**2b — axis, registration and source-integrity assertions (D2, D5.1, D7).**
*In scope:* the pure validators — strictly increasing unique hourly `valid_time` with full year
coverage, **CF-encoded naive-denoting-UTC per D5.0** (epoch encoding + semantic UTC attribute, never
a tz-aware dtype); whole-0.1° cell-centre registration asserted against the product's own coordinate
vector; required attrs present and equal to the expected literals; per-file sha256 checked against
the acquisition manifest before the payload is decoded (m-2).
*Out of scope:* claiming these establish the physical stamp semantics (D5.2 does that, via 1c).
*Target files:* `scripts/dhm_precip/era5_extract.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_extract.py -k validate` —
each 2a defect raises its own typed error with the offending value in the message: duplicate stamp,
non-hourly stride, a year short by one hour, a **missing or non-UTC epoch/semantic attribute** (D5.0
— *not* "naive dtype", which every valid product has), a coordinate vector offset by 0.05°
(registration), a missing `period_ending_convention` attr, and a **sha256 that disagrees with the
manifest raises `SourceChecksumMismatchError` before the file is opened**.
**A clean, unmodified real-shaped M-A4 fixture must PASS every validator** — the regression that
catches a validator written against an impossible contract.

**2c — the two operators behind one named, recorded interface (D1, D11).**
*In scope:* `nearest` and `bilinear` as an enum-selected operator with a recorded id;
the **pre-extraction bounds check** raising `StationOutsideGridError` (D11.1); per-station finite
accounting and `NonFiniteExtractionError` on the nearest series (D11.2); bilinear's all-four-finite
rule with counted NaN output (D11.3).
*Out of scope:* choosing between them (D1 locked it); the sensitivity statistics (3b).
*Target files:* `scripts/dhm_precip/era5_extract.py`, `scripts/dhm_precip/domain_types.py`
(`ExtractionOperator` StrEnum).
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_extract.py -k operator` —
on the analytic ramp, nearest returns the **exact hand-computed node value** and bilinear the
**exact hand-computed weighted mean**, for a station at a node, at a cell corner, and at an arbitrary
offset; a station **0.3° outside** the box raises `StationOutsideGridError` **and does not** return
the boundary value (locking the D11.1 finding — this test must fail against a bare
`.sel(method="nearest")`); a station over the NaN patch raises `NonFiniteExtractionError` on nearest;
a station with **one** NaN neighbour yields NaN under bilinear, increments the excluded count, and
does **not** fall back to nearest; the operator id appears in the returned record.

**2d — station set, cardinality and uniqueness (D8).**
**The expected-station inventory needs a source, and today there isn't one here.** *(Major, folded
from review.)* `load_station_coordinates` (`scripts/dhm_precip/loader.py:251,301`) *requires* an
`expected_stations` argument and validates against it — and the existing runner derives that set from
the **gauge workbook** (`scripts/dhm_precip/run.py:100`), which this task excludes. Without a source,
the "renamed station raises" case below is not merely untested, it is **impossible**.
⇒ **Decision: admit the workbook-derived usable-station inventory as an explicit boundary input to
this plan**, passed in and recorded in the manifest — rather than inventing a second, divergent
canonical list. (A committed canonical inventory is the tidier long-run answer, but it duplicates a
fact the workbook already owns and would silently rot against it; that belongs to M-I2, not here.)

**⛔ CORRECTED 2026-08-16 — that decision removed the only thing pinning the COUNT.** With a single
source, the loader checks the extracted set *equals the inventory* — and an inventory of 25 satisfies
that perfectly. A workbook that silently yields 25 stations extracts 25 and publishes happily; the
publication check compares against `len(stations)`, i.e. against itself. Equality to a self-supplied
list is not a constraint.
⇒ **Pin the cardinality independently of the inventory.** `expected_station_count = 26` on the frozen
parameter object; the run raises `StationSetMismatchError` if the workbook-derived inventory is not
exactly that size, **before** any extraction, naming the count it got. This is deliberately a
hard-coded number and not derived — it is a *tripwire on the boundary input*, and deriving it from
the same source it guards would defeat it. If the delivery legitimately changes size, the number is
updated in one place, as a visible decision.
*In scope:* load the coordinate table via the existing `load_station_coordinates`, passing the
workbook-derived inventory; assert the extracted station set **equals** it; assert exactly 26 rows,
no duplicate station, one row per station in every emitted table; wrap the loader's existing
`SchemaMismatchError` as `StationSetMismatchError`.
*Out of scope:* re-deriving or re-validating the gauge frame itself; any gauge *values*.
*Target files:* `scripts/dhm_precip/era5_extract.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_extract.py -k station_set` —
a coordinate table with 25 rows, with 27, with a duplicate station, and with one station renamed each
raise `StationSetMismatchError` naming the symmetric difference; the happy path emits exactly one row
per station and the series' `station` coordinate is unique and equal to the table's.

### Phase 3 — diagnostics

**3a — per-station grid + elevation table.** *(depends on 1b, 2c, 2d)*
*In scope:* every column D9 specifies for `station_grid_elevation.csv`, including both datum enums,
`orography_source`, product id/version, `datum_reconciled`, `shared_cell_id` and `stations_in_cell`.
*Out of scope:* any gauge value; any interpretation of the mismatch (M-A6).
*Target files:* `scripts/dhm_precip/era5_extract.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_extract.py -k elevation` —
26 rows, one per station; `orography_source` is the enum's value, and a row is **rejected** if it is
absent or a bare bool/string outside the enum; `station_elevation_datum == UNKNOWN` and
`datum_reconciled` says so (D3b); `elev_mismatch_m` equals
`station_elev_m − orography_elev_m` to 1e-9 on a synthetic pair; `offset_km` matches an independent
haversine to 1 m; two synthetic stations placed in one cell share a `shared_cell_id` and both carry
`stations_in_cell == 2`, while every other station carries 1.

**3b — operator-sensitivity envelope.** *(depends on 2c; pinned by D1a)*
*In scope:* the pinned statistics on the pinned population/seasons/thresholds/quantile grid, per
station and summarised; the excluded-hour count from D11.3; output as
`operator_sensitivity.csv` under D9's schema.
*Out of scope:* any ranking of operators; any veto language; any gauge data.
*Target files:* `scripts/dhm_precip/era5_extract.py`, `scripts/dhm_precip/params.py` (extend the
frozen parameter object — no inline literals).
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_era5_extract.py -k sensitivity` —
the quantile grid used **is** `DEFAULT_PARAMS.quantile_grid` (`scripts/dhm_precip/params.py:49`) and
the wet threshold/side and zero policy are read from the same object (a test that changes the object
changes the result); on a constructed field where bilinear provably exceeds nearest at one station
and undershoots at another, the **sign-agreement fraction is 0.5** and no output field or column name
implies a winner; hours NaN under bilinear are excluded from **both** operators' statistics and the
excluded count is reported; season assignment matches `scripts/dhm_precip/seasons.py`.

### Phase 4 — publish

**4a — extraction identity, bundle publication, manifest.** *(depends on 3a, 3b)*
*In scope:* both identities of D7; the identity-addressed staging directory, reopen-and-validate of
every file, per-output sha256, directory `os.replace`, `CURRENT` pointer written last; the D5.2
prerequisite check (refuse to publish a real-data run without a passing `AccumulationDiagnosticRecord`
from 1c); recovery of an orphaned staging directory and of a published directory with no manifest
entry.
*Out of scope:* changing Plan 171's manifest writer or the M-A4 product layout.
*Target files:* `scripts/dhm_precip/era5_extract_manifest.py`, `scripts/dhm_precip/extract_era5.py`
(CLI, D9), `scripts/dhm_precip/era5_errors.py`.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_extract_era5_cli.py
tests/unit/scripts/test_era5_extract.py -k publish` — changing **each** identity input in turn
(operator, coordinate-table sha, a source sha, the orography identity, the quantile grid, the schema
version, the code version) yields a **different** `extraction_identity` and a **new** directory, and
the previous one survives; a crash simulated **between** the directory replace and the pointer write
leaves the previous `CURRENT` intact and the next run re-validates rather than trusting the orphan; a
staging directory left by a crash is deleted, not published; a real-data run with no passing
diagnostic record exits **4**; each typed failure of D9 maps to its exit code (2/3/4/5) and `--help`
exits 0.

**4b — real-data run.** *(operator step; **gated on Plan 171 Task 4b**, not 2b — D10.)*
*In scope:* a human with the six annual products on disk exports `DHM_PRECIP_ERA5_ROOT` and runs
`--stage all`, then reports: the 26 extracted series' finite counts; the elevation table with its
`orography_source`; the sensitivity envelope; every checksum matching; the cited diagnostic record.
*Out of scope:* any gauge comparison (M-A6), explicitly and by design.
*Verification:* `DHM_PRECIP_ERA5_ROOT=… uv run pytest tests/integration/test_dhm_precip_era5_extraction.py`
— skipping **only** while the variable is unset, and **failing** on every condition D10 enumerates.

### Phase 5 — record

**5a — plan self-record.** Fold the observed orography route (1a) and the real-data results (4b, once
run) back into this plan.
*Verification:* the "## Observed orography route" section is present and matches
`scripts/dhm_precip/era5_orography_spec.py`.

**5b — propagate the IMERG split and the D6 handoff into the authoritative documents (D12, D6).**
*In scope:* `docs/design/dhm-precipitation-milestones.md` — M-A5's exit loses IMERG and gains the
orography-source enum and the sensitivity envelope; M-A6 becomes two-way now / three-way on M-A5b and
**gains the "within-cell observed gauge variability" deliverable with its masking and conditional-claim
wording (D6)**; a new **M-A5b · IMERG acquisition + extraction** milestone; the JSON graph at `:555-575`
updated (`M-A5b` added; `M-A5` unchanged in its edges; M-A7 untouched — it depends on M-A2/M-A3,
D13). Plus a superseded note at `docs/plans/171-era5-land-acquisition.md:357-366`.
*Out of scope:* drafting the M-A5b plan itself; editing the vision doc's findings.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_expectations.py` still green, and a
grep shows **no** remaining statement that M-A5's exit includes IMERG.

## Exit gates

- Extracted per-station hourly series for all **26** stations, **unmasked**, under both operators,
  regenerable from the committed pipeline into an identity-addressed bundle (D7/D9)
- Operator **named and recorded in the extraction manifest**, not implied by code — and it is
  **nearest** (D1)
- A **frozen `OrographySpec`** committed, and a per-station elevation-mismatch table carrying
  `orography_source` (`MODEL_OROGRAPHY` | `DEM_PROXY`), the product id/version, and **both** vertical
  datum enums with `station_elevation_datum = UNKNOWN` (D3a/D3b) — fields, not footnotes
- Operator-**sensitivity envelope** including tail quantiles on the pinned parameters, reported
  alongside — never as a veto on — M-A6's estimates (D1a)
- **Bounds and NaN post-conditions asserted, not assumed**: a station outside the grid raises rather
  than snapping to the boundary; nearest-series NaN is a typed failure; bilinear's missing-neighbour
  policy is documented, counted and applied (D11)
- `AccumulationDiagnosticRecord` persisted by `--stage diagnose` and **cited** by the extraction
  manifest; a real-data publish without one exits 4 (D5)
- **The Kirtipur/Khumaltar gauge-pair diagnostic is NOT emitted here** — the grid fact is, and the
  masked diagnostic is written into M-A6's exit by task 5b (D6)
- Milestone doc and Plan 171 updated: IMERG removed from M-A5's exit, M-A5b created, graph consistent
  (D12)
- `ruff` + `pyright src/` + **`pyright scripts/dhm_precip/`** clean; pyright ratchet honoured; full
  suite green
- Real-data gate authored and **skipping only while `DHM_PRECIP_ERA5_ROOT` is unset**, failing on
  everything else (D10)

## Risks

- **The orography probe (1a) is the stall risk**, which is why Phase 2 no longer depends on it and
  why D3a's fallback is a defined branch with acceptance criteria rather than an intention.
- **A DEM proxy is a different physical quantity from model orography.** The enum makes that
  unmissable, but it does not make the mismatch number mean the same thing — M-A6 must read the enum
  before it reads the number.
- **The datum is unknown on the station side** and may stay unknown. `datum_reconciled` keeps that
  visible; it does not fix it. Tens of metres of geoid separation over Nepal is the same order as the
  differences being discussed.
- **M-A5 exits with no empirical representativeness number** (D6's trade-off). Accepted: the
  alternative was shipping a figure contaminated by a documented 5× gauge defect under a clean label.

## Owner decisions (2026-08-15)

| Question | Decision | Where it lands |
|---|---|---|
| May the sensitivity result overturn the operator choice? | **No — nearest is locked**, decided before any numbers were seen. The comparison stays, re-purposed as a sensitivity envelope | D1, D1a |
| Is a DEM proxy acceptable if model orography needs another operator act? | **Yes** — labelled in the artefact as a proxy, from a named ordered candidate list with hard acceptance criteria. M-A5 does not block on it | D3a |
| Is IMERG in M-A5? | **No — split to M-A5b**, and the split is propagated to the milestone doc and Plan 171 by task 5b | Scope correction, D12 |

**New owner question raised by this revision (needs an answer before READY):** if **no** DEM
candidate meets D3a's acceptance criteria without an operator act, is the owner willing to accept
*one* account registration for the orography source, or should M-A5 exit with the elevation table's
orography columns empty and `orography_source` absent? The plan currently escalates rather than
choosing.

The residual unknown is otherwise technical, not a decision: **how ERA5-Land's static orography is
actually obtained** (task 1a), which is why 1a is a probe whose exit is a frozen spec.

## Observed orography route — probed 2026-08-16 (task 1a, task 5a self-record)

**Branch A (model orography) is reachable — no operator act needed, no owner question to answer.**
The "new owner question" above (whether to accept a Branch-B account registration) turned out moot:
the probe found Branch A reachable on the first try, so the question raised in "Owner decisions" was
never reached.

Probed live against the public CDS dataset pages and ECMWF's parameter database (constraint 3,
"verify against the service, not the documentation"):

- The `reanalysis-era5-land` dataset's own download-form variable list
  (`https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download`) includes
  `"geopotential"` under its "Invariant" field group, alongside `total_precipitation`'s own group —
  the SAME dataset id, SAME CDS client (`cdsapi.Client()`), SAME licence Plan 171 already accepted
  (P0). No second account, no second licence, no second service.
- ECMWF's parameter database (`https://codes.ecmwf.int/parameter-database/api/v1/param/129`, param
  129, shortName `z`) documents geopotential as "the gravitational potential energy of a unit mass...
  relative to mean sea level... The geopotential height can be calculated by dividing the geopotential
  by the Earth's gravitational acceleration, g (=9.80665 m s-2)... often referred to as the orography."
  Units (`unit_id` 15, `https://codes.ecmwf.int/parameter-database/api/v1/unit/15`) are `m**2 s**-2`,
  confirming D3a's declared `Φ/g0` conversion with `g0 = 9.80665 m s⁻²` exactly.
- The producer states the vertical reference as "relative to mean sea level" and does not name a
  specific geoid realisation (EGM96/EGM2008) — recorded verbatim as `VerticalDatum.LOCAL_MSL` rather
  than guessing a geoid model the producer itself does not commit to (the same honesty D3b applies to
  the station side).
- Because `geopotential` is an invariant field of the SAME `reanalysis-era5-land` dataset, it is
  delivered on the identical 0.1° ERA5-Land grid already used for `total_precipitation`
  (`scripts/dhm_precip/era5_request.py:37`) — the aggregation rule therefore degenerates to the
  identity case of the one general area-weighted-mean aggregator 1b implements (a single source cell,
  weight 1, per target cell). 1b's exact-grid-vector check (D3a) is the real proof of this, run once
  the raster is actually materialised.

**Branch B was not evaluated.** Branch A satisfied D3a's "no operator act" test on the first probe, so
the ordered DEM candidate list's acceptance criteria were never applied to any candidate — the
rejected-candidate log is empty by construction, not by omission.

**Frozen record:** `scripts/dhm_precip/era5_orography_spec.py::OBSERVED_OROGRAPHY_SPEC` (the same
content as above, as code — `product_id="reanalysis-era5-land:geopotential"`, `source =
OrographySource.MODEL_OROGRAPHY`, `conversion_rule = OrographyConversionRule.GEOPOTENTIAL_G0`,
`vertical_reference = VerticalDatum.LOCAL_MSL`, `rejected_candidates=()`). Neither branch has been
downloaded: this freezes the ROUTE only. Materialisation (1b) is gated on an operator step, exactly
like Plan 171's Task 4b — no credentials are available to this implementer.

**4b (real-data run) has not yet been executed** — it is an operator step gated on Plan 171 Task 4b
producing the six real annual `hourly_mm` products, neither of which exists at implementation time.
This section will be updated with the real-data results (finite counts, elevation table summary,
sensitivity envelope, checksums, cited diagnostic record) once an operator runs it.

```json
{
  "phases": [
    {"id": "phase-1a", "tasks": ["1a"], "parallel": false},
    {"id": "phase-1b", "tasks": ["1b"], "parallel": false, "depends_on": ["phase-1a"]},
    {"id": "phase-1c", "tasks": ["1c"], "parallel": false},
    {"id": "phase-2", "tasks": ["2a", "2b", "2c", "2d"], "parallel": false},
    {"id": "phase-3a", "tasks": ["3a"], "parallel": false, "depends_on": ["phase-1b", "phase-2"]},
    {"id": "phase-3b", "tasks": ["3b"], "parallel": false, "depends_on": ["phase-2"]},
    {"id": "phase-4a", "tasks": ["4a"], "parallel": false, "depends_on": ["phase-1c", "phase-3a", "phase-3b"]},
    {"id": "phase-4b", "tasks": ["4b"], "parallel": false, "depends_on": ["phase-4a"]},
    {"id": "phase-5", "tasks": ["5a", "5b"], "parallel": true, "depends_on": ["phase-4a"]}
  ]
}
```
