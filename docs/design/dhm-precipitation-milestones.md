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

May real DHM excerpts be committed to the repository as test fixtures? May a derived dataset be
retained, and **where** — specifically, may DHM data sit in the company-wide Dropbox? Attribution or
redistribution conditions?

**Ask storage and fixtures as ONE question**: putting DHM data in a shared Dropbox is a *distribution*
decision, the same class of question as committing excerpts to a repository.

**Exit:** written authorisation or explicit restriction, with the fixture strategy chosen — real
excerpts or synthetic reproductions of each defect signature.

### M-D2 · Station coordinates and elevation
**Depends: —** Hard-blocks M-A5, M-A6 and M-A8. *(Not M-A7 — that needs no coordinates.)*
**Owner 2026-08-12: coordinates will be obtained — treat as an assured input, not a project risk.**

lat/lon, elevation, DHM station ID for the 26 live stations. **Ask DHM directly and the students in
parallel, not in series** — a serial ask would reintroduce the thesis dependency this track exists to
avoid. Student-supplied metadata is an acceleration to be independently verified, not the first stage
of a chain.

**Exit:** station metadata table. There is still no *acceptable* null exit — the ERA5-Land comparison
cannot run without it — but the owner has confirmed delivery, so this is sequencing, not risk.

### M-D3 · Processing provenance
**Depends: —** Blocks M-A2.

What happened between DHM's raw export and the delivered file: aggregation method (**sum or mean** —
if mean, every total in the file is wrong by a factor), timestamp assignment (period-beginning or
period-ending), unit and timezone conversion, station-selection mechanism, why 11 columns are empty,
whether the raw export is available, and instrument type per station.

**Exit:** written processing-chain statement, or a recorded "unknown" that downstream inherits as a
caveat.

---

## Track A — our analysis

### M-A1 · Reproducible ingest and baseline
**Depends: —** Start now.

A committed, parameterised pipeline that reads the source (sha256 `8dc57e43…f98f57`), and emits the
inventory, coverage, off-grid-row, reporting-precision, defect and climatology tables. Every wet
threshold, quantile grid and missing-data rule is an explicit parameter.

**Exit:** pipeline reproduces the vision's Findings, or the vision is corrected where they disagree.
**Until this lands, no number in the vision may be cited externally.**

### M-A2 · Time-axis normalisation
**Depends: M-D3, M-A1.** Blocks M-A7.

Apply the accumulation convention and NPT→UTC handling. **Emits a normalised hourly dataset with
per-row provenance for every timestamp transformation, plus a mass-conservation check** proving
normalisation neither creates nor destroys precipitation. If the convention is unresolved, emit under
a stated assumption with a ±1 h phase-uncertainty flag carried downstream.

**Exit:** normalised dataset + provenance + passing mass-conservation check.

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

### M-A5 · Point extraction at station locations
**Depends: M-D2, M-A4.** *(M-A4 needs no coordinates; only the extraction does.)*

Extract ERA5-Land at the 26 station locations.

**Choose the extraction operator explicitly** (OD-1) — nearest cell centre, containing cell, or
bilinear. Unlike the polygon route this is a one-line parameter and **cheaply reversible**, so the
decision is no longer front-loaded; but it must still be stated, and its consequences measured.
Record the ERA5 grid coordinates and **model orography elevation** per station, quantify the
station-to-grid elevation mismatch, and run at least one sensitivity comparison against a second
operator.

**Exit:** extracted series + named operator + per-station elevation mismatch table + the
operator-sensitivity comparison, all regenerable from the committed pipeline.

### M-A6 · Gauge vs ERA5-Land comparison
**Depends: M-A3, M-A5.** *(M-A2 enters transitively through M-A3 — ERA5-Land is on a canonical UTC
axis, so the gauge side must be normalised before any pairing.)* **The point of this track.**

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

**Exit:** error characterisation; every result signed per D6 (a post-QC gauge total is a *lower
bound*), carrying the selection caveat, its named estimand, and the retained fraction it rests on.

### M-A7 · Temporal characterisation
**Depends: M-A2, M-A3.** Parallel to M-A5/M-A6.

Per-station wet-hour intensity distributions and diurnal structure, 0.2 mm/h harmonised floor for
frequency statistics (vision D5). Body and tail transferability reported separately with bootstrap
uncertainty, per rule 2.

**Exit:** distributions and profiles with uncertainty, and a quantified statement of what transfers
between stations. *Marginals plus mean profiles inform but do not suffice to design a disaggregator —
that needs temporal dependence structure, which is a Phase-2 question.*

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

### M-I2 · Reference dataset packaging
**Depends: M-D1, M-A3.** Small.

Package the masked dataset for the research data folder with a provenance manifest: source sha256,
mask definition and version, per-station removal accounting, processing chain, caveats — including
that unconditional totals are invalid (rule 1). Not onboarded, not in the DB (vision D10).

**Storage (owner 2026-08-12, ~2.4 TB Dropbox available — capacity is not the constraint):**
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
    {"id": "M-I2",  "depends_on": ["M-D1", "M-A3"]},
    {"id": "M-A6",  "depends_on": ["M-A3", "M-A5"]},
    {"id": "M-A7",  "depends_on": ["M-A2", "M-A3"]},
    {"id": "M-A8",  "depends_on": ["M-D2", "M-A6", "M-A7"]},
    {"id": "M-A9",  "depends_on": ["M-A6", "M-A7", "M-A8"]},
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

## Known weaknesses carried forward

- **Duplication of the students' QC and characterisation is DELIBERATE, not a cost to minimise**
  (owner 2026-08-12). Two independent passes over the same file are expected to teach each side
  something; findings flow both ways during the work, not only at M-A9. Where our fit-for-purpose
  mask and their adjudicated QC disagree, that disagreement is itself a result.
- **Wholesale zero-run removal discards real dry spells.** Sound only under identical masking
  (rule 1); every masked-sample result must state the fraction removed.
- ~~M-I1 ships ahead of its data path~~ — **closed by OD-3**: M-A3 exercises it on real data.
- **The extraction operator still shapes the answer**, even though it is now cheap to change: an
  ERA5-Land cell averages ~110 km² of Himalayan relief. M-A5's sensitivity comparison bounds this;
  it does not remove it.
- **No effort estimates anywhere**, so "critical path" remains an assertion about topology.
- **We validate ERA5-Land the dataset, not ERA5-Land as our pipeline delivers it.** Our models are
  forced through the Gateway; this track reads CDS. Parity between the two is a real question, parked
  as M-G5 (OD-2b).

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
