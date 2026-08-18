---
status: READY
created: 2026-08-18
plan: 183
title: ERA5-Land forcing from the sloth-dynamic store — the lineage aquacast was trained on
scope: aquacast's models were trained on catchment-averaged ERA5-Land; we feed MeteoSwiss. Close that substitution by reading the SAME store aquacast's data plane reads — `s3://sloth-dynamic/v1/era5/` (company infrastructure, owner-confirmed available) — and basin-averaging it onto SAP3 polygons with the mappings `aquaire` documents. Rewritten 2026-08-18: an earlier draft proposed building CDS acquisition ourselves, which is unnecessary now that the store and its reference implementation are available.
depends_on: []
blocks: [152]
supersedes: []
---

# Plan 183 — ERA5-Land forcing from the sloth-dynamic store

**READY** (owner, 2026-08-18). D1 = 5 % relative per basin; D2 = two forcings, radiation deferred.
Reviewed across three passes: one blocker (the `mean_temperature` vs `temperature` canonical-name
error, which would have failed SILENTLY), a missing chunked-backfill precedent, an unimplementable
cell-count acceptance, and a correction to T3’s framing. All folded. **Keep it small when building.**

## ⚠️ Note to reviewers: proportionality

**This is a contained ingestion plan — four tasks, mostly assembly.** Sibling plans in this track
were inflated to 400-583 lines across successive review rounds by *invented* scope, and each round
had to be discarded. Please do not repeat that.

**Out of scope by DECISION, not oversight** — proposing these is a finding against the reviewer
unless tied to a concrete failure this plan would cause:

- taking `aquaire` as a runtime dependency (decided against, with reasons, above);
- catchment delineation (we already have basin polygons);
- sub-daily ingestion (D3 defers it deliberately);
- retraining, CDS acquisition, or replacing MeteoSwiss as v0 operational forcing;
- any enforcement mechanism around the D1 tolerance beyond recording the distribution.

**What review IS wanted for:** whether the mapping transcribed from `aquaire` is faithful, whether
the three documented traps are correctly guarded, whether the T3 validation can actually distinguish
an averaging error from a store difference, and whether anything asserted here is false.

## What changed, and why this is now small

An earlier draft scoped this as *build our own CDS acquisition*, reusing Plan 171's Nepal
precipitation toolchain. **That is no longer the cheapest path.** The owner confirmed
`s3://sloth-dynamic/v1/era5/` — the exact store `aquacast`'s data plane (`aquaire`) reads — is
company infrastructure and available to us.

So we are no longer *reproducing* the training lineage. We are **reading it**. That removes the
largest risk the earlier draft carried: whether our acquisition and Caravan's agree.

**Decision (owner, 2026-08-18): read the store directly; `aquaire` is the authoritative SPEC, not a
dependency.** Taking `aquaire` as a runtime dependency would add ~10 constraints to the operational
lockfile — `pandas>=3`, `dask`, `geopandas`, `contextily`, `netcdf4`, `s3fs` — plus `rivretrieve`,
which is **not on PyPI** (a second private dependency needing its own token), and its `pyproject`
assumes an editable `../aquacast` sibling checkout rather than library consumption. We resolved one
dependency conflict from `aquacast` alone this week (`rich>=15` vs `prefect<15`, still carried as a
uv override). `aquaire` remains the reference implementation and stays usable out-of-band.

## The store, and the canonical mapping we must follow exactly

From `aquaire/src/aquaire/sources/era5.py` — this is the contract:

```
s3://sloth-dynamic/v1/era5/   ERA5-Land DAILY aggregates, ONE zarr per variable,
                              zarr v3, inline consolidated metadata,
                              0.1° global (1801×3600), LAND-ONLY so ocean is NaN, CF-1.11

AQUAIRE name           unit    store variable                     native  transform
precipitation          mm/day  total_precipitation_sum            m       × 1000
mean_temperature       degC    temperature_2m_mean                K       − 273.15
solar_net_radiation    W/m²    surface_net_solar_radiation_sum    J/m²    ÷ 86400
thermal_net_radiation  W/m²    surface_net_thermal_radiation_sum  J/m²    ÷ 86400
```

### ⛔ These are AQUAIRE's names, not SAP3's — do not write them to the store

That column says "canonical" in `aquaire`'s source. It means canonical **to aquacast**. SAP3's
canonical vocabulary is `{"precipitation", "temperature"}` — `mean_temperature` is not a SAP3
parameter, and the codebase already says so: `models/aquacast/_shim.py:97` carries
`AQUACAST_TO_CANONICAL_NAME = {"mean_temperature": "temperature"}`, and every MeteoSwiss row is
written `parameter="temperature"` (`adapters/meteoswiss_open_data_reanalysis.py:211`).

**Writing `parameter="mean_temperature"` would fail SILENTLY.** `fetch_forcing` returns nothing for
an unmatched parameter filter rather than raising, so a training or hindcast run scoped to
`cmal_pool_PT` would get precipitation back and **zero temperature rows, with no error** — and T2's
own goal of being "queryable and comparable alongside MeteoSwiss" would quietly not hold.

**T1/T2 must write `parameter="temperature"`, reusing `AQUACAST_TO_CANONICAL_NAME` as the single
source of truth** rather than re-deriving it. Acceptance must query the written rows with the same
parameter string the hybrid reader actually requests.

**Three traps `aquaire` documents, each of which produces plausible-looking wrong numbers:**

1. **Radiation is a daily ACCUMULATION** (`cell_methods: time: sum`, J/m² over the day), so the
   divisor is **86400, not 3600**. The hourly divisor inflates radiation **24×**.
2. **Daily aggregation is UTC 00–24**, verified from the store's own `history` provenance
   (`daily_reduce(..., time_shift={'hours': 0})`) rather than from its label.
3. **`thermal_net_radiation` is downward-positive and therefore negative.** Nothing clips the sign;
   "fixing" it would be wrong.

## ⚠️ This reopens radiation (G2)

`cmal_pool_PT` declares only precipitation and temperature, which is why Plan 152 scoped radiation
out. But the store and the data plane carry **four** canonical forcings, and the sub-daily artifact
may want them. **Do not silently narrow to two** — decide it (D2) rather than inherit it from what
the daily artifact happens to declare.

## Tasks

### T1 — read the store
Add `s3fs` (the only missing piece — we already have `zarr>=3.0`, `xarray`, `dask`). Open the
per-variable zarrs read-only and apply the mappings above verbatim.

**Acceptance:** a known basin's daily series comes back in canonical units with a plausible range —
and specifically that `thermal_net_radiation` is **negative**, which catches a sign "correction"
and a units error at once.

### T2 — basin-average onto SAP3 polygons
Route the grid through `ExactExtractGridExtractor` — the same extractor already used for MeteoSwiss
NWP, so the spatial representation matches what the rest of the system expects. Write to
`historical_forcing` under a distinct source, **alongside** the MeteoSwiss rows so both lineages
stay queryable and comparable.

**Scale: use the existing chunked backfill, not a direct extractor call.**
`ExactExtractGridExtractor.extract()` accumulates every row for every `valid_time` in memory before
returning, and is used today only for a single NWP cycle, a 60-day rolling window, or the existing
backfill's **annual** chunks (`reanalysis_backfill.py:302`) — never a 40-year span in one call. `services/reanalysis_backfill.py` (Plan 115b2) exists precisely for this: work units of
`(product, year, station-batch)`, each chunk persisted before the next so the full series is never
held in memory, with gap-detection for idempotent resume. Follow that pattern — roughly
40 yr × 296 basins × 2 params ≈ **8.6 M rows** is not a one-shot call.

**The land-only grid is the trap here:** ocean cells are NaN, so a coastal or partially-masked
catchment can silently average over fewer cells than it should. Assert the contributing-cell count,
not just that a number came back. **Name the mechanism:** the shared extractor hardcodes
`ops=["mean"]` (`exact_extract_grid_extractor.py:123`) and returns no count, so this needs either a
new `ops` entry — shown not to change NWP's output shape, since the extractor is shared — or a
standalone land-mask coverage check T2 owns. Decide which; do not assume the count is available.

### T3 — validate against Caravan's published indices
Even reading the same store, **our catchment-averaging is still ours** — cell weighting, boundary
handling and the polygon itself all differ from Caravan's.

**But be honest about what a mismatch would prove: T3 is an END-TO-END PARITY check, not an
averaging-specific one.** Caravan's indices derive from *Caravan's own* ERA5-Land archive; the
statics parquet is a Caravan passthrough (Plan 155). **Nothing establishes that Caravan's archive and
`sloth-dynamic` are the same bytes** — Plan 117 explicitly left archive identity open. So a
disagreement could be our averaging *or* a store difference, and T3 alone cannot separate them.

That does not weaken the gate: a **match** is strong evidence the whole chain reproduces the training
lineage, which is what we actually need. It means a **mismatch** starts an investigation rather than
pointing at a culprit — budget for that, and do not write the acceptance as though a failure localises
to our code.

The statics parquet carries Caravan's climate indices, computed from Caravan's own ERA5-Land, for
**296 Swiss basins**: `p_mean` (mean 4.61 mm/day), `frac_snow` (0.248), `high_prec_freq` (0.033),
`low_prec_dur` (3.07). Recompute them from our extraction over Caravan's window
(1981-01-01 → 2020-12-31) and compare per basin.

**Ordered so each failure localises:** `p_mean` first (systematic bias), then `frac_snow` (exercises
the temperature path), then the `freq`/`dur` pair (daily-boundary convention).

**Tolerance is set BEFORE the comparison (D1), not after seeing the numbers.**

### T4 — expose as an injectable `WeatherReanalysisSource`
So training, hindcast and operational paths select it via `DeploymentConfig.reanalysis_source`
without touching call sites — the injectability `v0-scope.md` §I2 already requires.

## Open decisions

| # | Question | Recommendation |
|---|---|---|
| **D1** | Agreement tolerance for T3? | **RESOLVED (owner, 2026-08-18): 5 %, relative, per basin.** Deliberately tight — the point is to detect an averaging discrepancy, and a loose bound would pass a systematically biased extraction. **The owner expects this number may be revised**, so record the observed distribution (not just pass/fail) when T3 runs, or a later revision has nothing to reason about. |
| **D2** | Two forcings or four? | **Two — defer radiation.** This reverses an earlier recommendation to ingest all four, on a better argument: T3 validates against Caravan indices derived from precipitation and temperature ONLY, so radiation would ship **unvalidated** — exactly what this plan exists to prevent. `cmal_pool_PT` does not consume it, and a follow-on re-derives the mapping from `aquaire` when the sub-daily artifact needs it. |
| **D3** | Daily store only, or sub-daily too? | Daily first. `aquaire` has a separate `sources/subdaily.py`; treat sub-daily as a follow-on once the daily path is validated. |

## Workload

Much smaller than the CDS draft. **T1/T2/T4 are assembly** — the extractor, the store schema and the
Protocol all exist. **T3 remains the uncertain one**, but its risk has dropped from "does our
acquisition match theirs" to "does our averaging match theirs", which is a narrower question with a
smaller failure surface.

**No CDS download**, so the multi-day acquisition long-pole of the earlier draft is gone entirely.

## Non-goals
- Retraining any model (the rejected option).
- Replacing MeteoSwiss as v0 operational forcing — this lands alongside it.
- Taking `aquaire` as a runtime dependency (D above); it stays the spec.
- Catchment delineation — we have basin polygons already.

## References
- `sandrohuni/aquaire` — `src/aquaire/sources/era5.py` (the mapping contract), `sources/grid.py`
  (lat/lon slicing conventions), `store.py` (cache layout). **Private; read as specification.**
- `hydrosolutions/static-attrs-nepal` — the DHM deployment appliance; same lineage, different packaging.
- Plan 152 § four substitutions — this closes "NWP ≠ ERA5-Land".

---

## Fixer-round input — findings from the independent review (2026-08-18)

The `/implement` run produced commit `6d669dd` on `feat/plan-183-era5-land-forcing`, then **escalated at
round 0 without ever running the review loop**: its verify agent returned a `PENDING` placeholder rather
than waiting out the ~11-minute suite. The escalation was a **false negative about the verifier, not the
implementation** — re-run independently in the worktree, every gate is green: `4218 passed, 15 skipped,
11 deselected` (660 s), `ruff check`/`format --check` clean, pyright ratchet `432 <= 432`.

An independent Codex pass over the committed diff was then run by hand. Findings below are the ones
**confirmed by reading the cited code**, not the raw reviewer output. Fix these; do not re-litigate them.

### B1 — the validator silently narrows its own scope
`services/era5_land_validation.py:190-250`. `validate_era5_land_against_caravan` compares whatever days
happen to intersect: no minimum coverage over the 1981-2020 window, and a station missing a basin,
attributes, or either parameter is dropped by a bare `continue`. `_caravan_reference` (`:183-189`) and
`compare_climate_indices` (`:157-181`) likewise skip indices absent from either side. So a fleet where
most basins were skipped, or a basin compared on two days and one index, returns an **all-green** result
indistinguishable from a real 40-year, four-index parity pass.

This is the one thing T3 exists to be trusted about. Require an explicit coverage floor over the window
and return the skips **as data** (station, reason) alongside the agreements, plus the day count actually
compared, so a caller cannot mistake a narrow pass for a broad one. Related, already a rule on this
track: *a validator does not re-derive the computation* — nor may it quietly shrink its own sample.

### M2 — `_assert_land_coverage` is a no-op exactly where it is needed
`adapters/era5_land_reanalysis.py:209-227`. Coverage is measured with `shapely.contains_xy` over cell
**centres**; when a basin contains none, `total == 0` and the basin is skipped. At ERA5-Land's 0.1°
(~11 km) that is the ordinary small-Swiss-catchment case, so the guard switches itself off for the
basins most at risk.

The comment justifying that skip is also wrong: it claims "the extractor itself raises `ExtractionError`
for this case". `ExactExtractGridExtractor` runs `exact_extract(..., ops=["mean"])`
(`preprocessing/exact_extract_grid_extractor.py:121-134`), which is coverage-weighted over **intersecting**
cells — a sub-cell basin gets a value with no centre inside — and it only reports out-of-extent when
**all** values are NaN (`:139-145`). Measure coverage over intersecting cells, and correct the comment.

### M3 — the mesh is rebuilt globally, per basin, per chunk
Same function: a 1801x3600 global mesh and a full point-in-polygon per basin, repeated per year, per
station batch and per parameter by `services/era5_land_backfill.py:224-246`. Over the stated 40-year
fleet that is on the order of 10^11 predicates before extraction begins. The land/ocean mask is
time-invariant and the basin is tiny relative to the globe: subset to the basin bounding box, and
compute the mask once per grid rather than once per chunk.

### M4 — no operator entrypoint, so `era5_land` reads an empty store
`services/era5_land_backfill.py:184` has no caller. `adapters/hybrid_reanalysis_factories.py:105-115`
only selects a *reader*, so enabling the mode on a fresh deployment silently yields nothing. The
precedent is direct and small: `scripts/backfill_meteoswiss_history.py:194` drives `run_backfill` for
MeteoSwiss. Mirror it — a script, not a flow.

### Minors
- `tests/unit/adapters/test_reanalysis_selection.py:40-48` asserts on the private `_source` of a reader
  backed by an empty fake, so a selector that returns zero temperature rows still passes. Add a
  writer -> store -> selected-reader round-trip: that is the headline silent failure this plan exists to
  close, and nothing currently guards it end to end.
- `tests/unit/services/test_era5_land_backfill.py:207-273` never re-runs after the interrupted chunk, so
  it does not actually demonstrate resume.
- Radiation transforms (`adapters/era5_land_reanalysis.py:127-136`) are implemented although D2 defers
  radiation. They are unreachable — the operational whitelist at `:143-147` excludes them — so leave them
  as documentation of the mapping rather than churning the diff; just do not extend them.

### Out of scope for this fixer round
Running T3 against real S3/Caravan data (no credentials in the build environment; owner action after the
backfill populates). Modifying the shared `ExactExtractGridExtractor`. The forcing-canonicalisation seam.

### Plan-number collision — RESOLVED 2026-08-18
`183-forcing-canonicalisation-seam.md` (committed `edb4a43`, same day) also claimed 183. No branch was
working it — a single DRAFT commit on main, no follow-ups — so the owner had it renumbered to **187**
rather than this one, which was already READY, implemented and carried in a branch name. References in
`touchpoint-maps.md` and `docs/design/dhm-precipitation-milestones.md` were updated with it.

## Second fixer round — verified input (2026-08-18)

Commit `2ca2579` addressed all four findings above. `/implement` escalated a second time for the
**same reason as the first** — its verify agent had pytest auto-backgrounded and was forced to report
before the result arrived. Re-run by hand on `2ca2579`: **4225 passed, 15 skipped, 11 deselected**
(660 s), ruff clean, pyright ratchet `432 <= 432`. Gates are green; both escalations were about the
verifier's wall-clock, not the code.

An independent round-2 Codex pass then reviewed the fix. Its findings were checked against the code —
and, where the claim was about a test, **by running that test against the pre-fix source**. Two of its
items are real, one is rejected, and one was simply wrong.

### R2-1 [major] The land-coverage fix traded a false negative for a false positive
`adapters/era5_land_reanalysis.py:270`. `shapely.intersects` is true for a cell that touches the basin
only along an edge or at a corner — zero overlap area. `exact_extract` gives such a cell weight 0, so it
contributes nothing to the mean. A NaN ocean cell touching the boundary therefore enters `total` but can
never enter `land`, depressing the fraction and raising `ExtractionError` on a basin that is entirely
fine. Measure positive-area overlap instead (e.g. `shapely.area(shapely.intersection(geom, cells)) > 0`),
which is also what "contributing cell" means to the extractor.

### R2-2 [major] Nothing subsets space anywhere — this is what makes the backfill impractical
`adapters/era5_land_reanalysis.py:355`. `read_variable` slices on `valid_time` **only**. The full global
ERA5-Land grid (1801x3600) is then materialised — by `da0.values` in the coverage check, and by
`exact_extract` itself — for every day of every chunk. For this plan's actual deliverable, 40 years x
two parameters over Swiss basins, that reads the whole global record to use a few hundred cells of it:
roughly 26 MB per day per parameter against a Swiss bounding box worth single-digit KB.

Round 2 saw only the `values_all` line, which is the symptom. Subset to the **fleet's** bounding box
(plus a margin) inside `read_variable`, before extraction; per-basin cropping in the coverage check then
becomes an ordinary detail rather than the only defence, and `values_all` stops mattering. This is the
one change that decides whether T2's 40-year backfill is runnable.

### R2-3 [minor] The round-trip test does not exercise the writer
`tests/unit/adapters/test_reanalysis_selection.py:60`. It stores rows it hardcodes as
`parameter="temperature"` and reads them back, never calling `Era5LandReanalysisAdapter.fetch_reanalysis`.
So it proves the *reader* honours the source and parameter filter — useful — but its docstring claims it
closes the `mean_temperature` silent failure, which it cannot: the adapter never runs. Drive the
round-trip through the adapter into the store, then read via the selected reader, or correct the
docstring to claim only what the test does.

### R2-4 [REJECTED] "`CaravanValidationResult` has no `__bool__`"
Reported as a blocker: `if validate_era5_land_against_caravan(...)` is truthy even for an all-skipped
fleet. Rejected. Every dataclass is truthy; `is_full_parity_pass` already requires non-empty coverage,
zero skips, all four indices and every agreement within tolerance
(`services/era5_land_validation.py:255-270`), and the class docstring says to read it. A `__bool__` that
means "full parity" would trade one silent misreading for another, subtler one. No caller exists.
**Do not add it.**

### R2-5 [REVIEWER WRONG] "the sub-cell locking test passes pre-fix"
Checked by reverting `adapters/era5_land_reanalysis.py` to `98e842a` and running the test:

```
FAILED TestLandCoverageCheck::test_raises_for_sub_cell_basin_with_no_contained_cell_centre
E  Failed: DID NOT RAISE <class 'sapphire_flow.exceptions.ExtractionError'>
```

It discriminates exactly as intended — the pre-fix guard skipped the basin. Its sibling
(`test_sub_cell_basin_fully_inside_a_land_cell_passes`) does pass both ways, correctly: it is a
no-false-positive guard, not a locking test. The backfill resume test was likewise judged against the
wrong control — the fixer changed the *test*, not the backfill code, so "passes pre-fix" says nothing;
the control for it is a deliberately broken resume.

**Scope for this round: R2-1, R2-2, R2-3 only.** Everything in the previous round's out-of-scope list
still applies.

## Live store probe + owner decisions (2026-08-18)

`s3://sloth-dynamic/v1/era5/` was read directly with `AWS_PROFILE=work`. It is reachable, and the
path convention T1 had **inferred** from prose (`{root}/{store_variable}.zarr`) is confirmed —
16 variables, including the two D2 persists. That closes the implementer's residual risk.

| measured | value |
|---|---|
| extent | **1980-01-01 → 2025-12-31**, 16802 daily steps |
| grid | 1801 x 3600 (0.1°), dims named `lat`/`lon` (`_standardize_dims` renames) |
| chunking | **(1826, 50, 50)** — time-major: ~5 years x 5° x 5° per chunk |

### D4 — the climatology window is the FULL record, and per-organisation configurable
T3's hardcoded 1981-2020 is replaced by `ERA5_LAND_RECORD_START/_END` (the measured extent) with
`DeploymentConfig.climatology_window` as a per-org override. Nothing in this repo recorded which
window the delivered `caravan:` statics used, and the only window documented anywhere in-repo — the
Gateway feature catalog — says **1991-2020**. Comparing a recomputation over one window against
indices published for another is not a parity test; at 5% it measures the offset between windows.
**Still worth confirming with Sandro which window the Caravan statics use.**

### The chunk layout makes R2-2 load-bearing, and annual chunking wasteful
Chunks span **1826 days**, so reading one day reads five years of that block. Per five-year block per
variable: global ≈ **47 GB**; cropped to a Swiss bbox ≈ **72 MB**. R2-2's spatial subset is the
difference between the two — not an optimisation.

The corollary is a **follow-on, not a defect**: `run_era5_land_backfill` chunks by *year*, so each
5-year store chunk is fetched ~5x. Aligning backfill spans to the store's time chunking would cut
that redundancy. Deferred deliberately — correctness first, and the cost is tolerable at Swiss scale.

### Running T3 for real: backfill yes, parity NO — the reference side is missing
The dev machine's local stack (postgres up, 2 stations, 2 basins) can run the backfill today. It
**cannot** run the parity check: both basins carry 300 attributes and **zero `caravan:`-prefixed**
keys. The four indices exist under bare names with different semantics — `high_prec_freq` is 18.41
and 13.692 (days/year) where Caravan's is a fraction (~0.033 Swiss mean). Those are CAMELS-CH
attributes, not Caravan's.

So `validate_era5_land_against_caravan` would skip every basin — reported as skips with reasons,
which is exactly what the B1 fix bought. **T3 is blocked on the Plan 155 Caravan import running for
real**, which still has no production caller. That is the next step for this track, not more work
inside Plan 183.
