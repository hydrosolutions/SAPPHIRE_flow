# DHM precipitation — Phase-2 recommendation (M-A9)

**Plan 209 T1. Written 2026-08-28 against M-A6, M-A7 and M-A8, all complete and merged.**
This document states, for each candidate operational use, **what would have to be true** and whether
the evidence supports it. **"No operational use" is a permitted conclusion** and is reached for two of
the five. The owner decides (M-DEC); this document does not.

Every number below carries the conditions it was published with. **⛔ A magnitude quoted without its
retained-hour `n`, an M-A7 figure without its adequacy designation, or a gradient without its period
and its bound-direction is a defect, not a formatting preference** — a synthesis is exactly where
companions get stripped.

Regenerate any number: `$ENV uv run python scripts/dhm_precip/ma6_run.py --out <dir>` (likewise
`ma7_run.py`, `ma8_run.py`). The IMERG latency table in §6 comes from this document's own probe and
exists nowhere else.

---

## 1. What the three milestones established

**M-A6 — ERA5-Land's error has a consistent shape.** Averaged over all commonly-retained hours it is
**wetter** than the gauge; on the hours the gauge calls wet it is **less intense**. The wet-hour
intensity deficit shrinks with elevation: **+2.2197 mm/h** in the `< 700 m` band (9 stations, joint
wet-hour `n` = 9,673, mean sub-freezing mass fraction 0.0000) against **+0.4301 mm/h** above 3,000 m
(3 stations, `n` = 4,464, mass fraction 0.0000).

⛔ **This is a pattern, not an attribution.** A 0.1° cell is ~110 km²; a cell that rains lightly and
often where a point gauge rains hard and rarely produces this exact signature with **no model error at
all**. M-A6 cannot separate representativeness from model error and does not try.

**M-A7 — tail behaviour transfers between stations, except in one band.** Leave-one-out prediction of
each held-out station's q99 from its median and the pooled ratio, fraction within 25 %: **0.889**
below 700 m, **1.000** at 700–2,000 m, **0.000** at 2,000–3,000 m, **1.000** above 3,000 m. Inside the
failing band the q99/q50 ratio spans **9.56 to 32.00** — genuine heterogeneity in the Lesser Himalaya
transition, not sampling noise.

**Diurnal structure is half-confirmed.** JJAS station-equal peak hour: 23 UTC (≈ 04:45 NPT) below
700 m, and **19 UTC (≈ 00:45 NPT) in all three higher bands** — the southern-margin early-morning peak
reproduces, but there is **no gradient among the upper bands**, and the lowest band is
operator-sensitive (pooled 21 UTC against station-equal 23 UTC). This is a step change at the margin,
not the monotonic shift the exploratory `r = −0.486` suggested.

**M-A8 — one clean elevation signal; one unidentifiable confound.** Within Group B (20 stations at
**fixed** 0.2 mm reporting resolution, spanning 2,080 m) q99 wet-hour intensity correlates **−0.697**
with elevation, and M-A6's matched-hour mean difference **−0.386**. This is the cleanest elevation
result the track has — clean *because* resolution is held constant rather than statistically adjusted
for. ⛔ It is a **relationship, not an effect**: exposure, siting, catchment and monsoon dynamics all
co-vary with elevation.

The **between**-group contrast is unidentifiable. Group A spans **2,490–3,700 m**, Group B
**67–2,147 m**, with **no overlap** — so "instrument differs" and "altitude differs" are the same
sentence in this sample. No covariate adjustment over 26 stations separates them.

---

## 2. Use 1 — elevation-banded diurnal **timing** correction

**The idea.** Weight M-A7's banded diurnal profiles by each basin's band `area_km2`, collapse to an
observation-derived expected basin-average diurnal shape, compare with the IFS shape, and redistribute
an **unchanged daily total** in time. The track puts this on the critical path, calling the
observation-derived banded profile *"the only defensible sub-daily timing source we have, because no
available model of this class gets the phase right."*

**What would have to be true.**
1. The bands' diurnal shapes must actually **differ from each other** — otherwise banding buys nothing.
2. The weighting must not import magnitude information (**vision D6/D9** forbid touching magnitude).
3. The observed shape must be stable enough to correct against.

**What the evidence says.** (1) **fails as stated**: three of four bands share the same peak hour
(19 UTC) with no gradient among them, so the operator reduces to a two-way split — margin versus
everything above it. (2) **is not satisfied by either obvious operator**: area-weighting M-A7's
profiles *as published* weights **unnormalised mean mm/h**, importing exactly the magnitude
information vision D6/D9 forbid; normalising each band first instead assumes equal precipitation per
unit area. A third variant — normalised profiles weighted by independent or IFS band-total fractions —
would avoid both, but M-A7 supplies neither those weights nor deployment evidence. (3) is weakest in
the high band, whose resampled peak hours span an **11-hour arc**, and the lowest band's peak moves
with the choice of estimator.

> **Verdict: PROMISING, NOT DEPLOYABLE ON PRESENT EVIDENCE.** The physical case is good and the
> milestone's framing is right that no model of this class gets the phase right. But M-A7 as delivered
> does not licence any of the three operators. ⛔ Do not build this into v1 on the strength of the
> milestone's "only defensible source" wording — that is an argument about the *absence of
> alternatives*, not evidence that this one works.
>
> **To unlock it:** demonstrate that the margin-versus-upper split (not four bands) carries a stable
> shape difference, and supply band weights from a source other than the profiles being weighted.

**Update 2026-08-29 — the first unlock condition now has independent support; the second does not.**
A separate ERA5-Land-versus-gauge timing analysis (`data/dhm_precip/figures/era5-timing/`, JJAS
2020–2025, 20 retained stations of 26) reproduces **exactly the two-way split** by a different route —
normalised per-station diurnal cycles, circular lag estimators, NPT rather than UTC, elevation bands
`<1,000 / 1,000–2,000 / ≥2,000 m`:

| band | gauge vs ERA5-Land offset (median) |
|---|---|
| `< 1,000 m` | **+0.6 h** — ERA5-Land essentially aligned |
| `1,000–2,000 m` | **−14.4 h** |
| `≥ 2,000 m` | **−11.9 h** |

Stable to ≲1.5 h under exclusions, season definition (JJA/JJAS/MJJASO/JA), band edges, nearest-vs-
bilinear extraction, and ±1 h period-convention flips. ⛔ **The SIGN is not identifiable** — at near
antiphase −14.4 h and +9.6 h are the same angle; the **magnitude** is what is measured, and physics
(parameterized convection firing at local noon) is what favours "model too early". The gauge-timezone
hypothesis shifts everything +6 h uniformly but leaves the **between-band contrast invariant**.

⇒ Condition (1) is now supported by two independent methods. **Condition (2) — band weights from a
source other than the profiles being weighted — remains unmet, so the verdict is UNCHANGED.**

⛔ **And note what was measured: ERA5-Land, a REANALYSIS.** The operational forcing is ECMWF **IFS**.
ERA5-Land's precipitation is interpolated from ERA5, whose convection scheme is a frozen 2016-era IFS
cycle; operational IFS has moved on many cycles. **We have not measured the product we actually fly.**

**Update 2026-08-29 (Plan 216, M-A11) — now measured.** IFS control-forecast `tp` (TIGGE via ECDS, JJAS
2025, leads D+1-D+3) shares the mid/high-band error's DIRECTION but at roughly **a third to a half of
ERA5-Land's magnitude** (mid ~−5.2 to −5.7 h vs ERA5-Land's −14.4 h; high ~−5.2 to −5.7 h vs −11.9 h),
well beyond the ±3 h resolution bound 6-hourly data permits; the low band is unresolved from zero at
every lead. See `docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md` for the full measurement
(numbers here are from that document's D5-corrected re-run — a review round found the first pass pooled
raw mass across stations before computing the phase, which this document's original 2026-08-29 update
inherited). **NO-GO on the originally proposed ~12 h correction as designed** (it would over-correct
IFS); **GO on finer-resolution correction work calibrated to IFS's own, smaller displacement.**

---

## 3. Use 2 — gap-filling the gauge record with ERA5-Land

**What would have to be true.** ERA5-Land would have to be unbiased *in the statistic being filled*,
or biased in a known and correctable direction.

**What the evidence says.** M-A6 shows it is neither: it is **wetter overall and less intense when
raining**, simultaneously, in every band. A gap filled with ERA5-Land inherits both errors, and their
opposite signs mean no single scalar adjustment fixes both. Worse for the operational case, the
filled values would be least trustworthy exactly where gaps cluster — M-A3 found candidate zero-runs
up to **52 days** at Aiselukhark, and those are monsoon-season gaps.

> **Verdict: NO for magnitude-sensitive use; ACCEPTABLE only for presence/absence context.** Filling a
> gap changes a total, and M-A6 gives no defensible correction for the fill.

---

## 4. Use 3 — bias-correcting ERA5-Land forcing from the gauges

**What would have to be true.** A correction derived at gauge locations would have to **transfer** to
the ungauged cells where forcing is actually needed.

**What the evidence says.** M-A7 measured transfer directly. It works in three of four bands
(within-25 % fractions 0.889, 1.000, 1.000) and **fails completely in the 2,000–3,000 m band (0.000)**
— which is precisely the transition zone between the gauged lowlands and the ungauged high basins. A
correction fitted across stations would hold where we already have gauges and break in the band it
would have to cross.

> **Verdict: CONDITIONALLY VIABLE below ~2,000 m; NOT viable across the transition.** ⛔ Do not fit one
> correction across the whole domain. If pursued, fit and apply **within** the bands where transfer was
> demonstrated, and state that the 2,000–3,000 m band is excluded by measurement, not by omission.

---

## 5. Use 4 — elevation-band forcing correction (the lapse rate)

**What would have to be true.** A precipitation-versus-elevation gradient separable from gauge catch
behaviour.

**What the evidence says.** M-A8 fitted the apparent rain-phase gradient on the Pyramid transect —
five stations, **2009-01-01 → 2018-05-09**, JJAS, `AT ≥ 1.5 °C`: **−52.49 %/km** (95 % CI
[−67.53, −30.48]). It is an **UPPER BOUND IN MAGNITUDE** on any true decline: Pyramid's gauges are
unheated, catch efficiency falls with wind, wind exposure rises up a transect, so observed
precipitation declines faster than true — **the true decline is no steeper, and could be nil.**

The confound is **not identifiable from these data**. A low-wind stratification was considered and
rejected: low wind does not select the same precipitation under better catch, it selects **different
precipitation** (stratiform rather than convective) with its own elevation response. Two further
readings the slope hides: the transect **plateaus above ~4,000 m** (AWS1 at 5,035 m is marginally
wetter than AWS2 at 4,260 m), and the apparently steeper 4 °C result is **AWS4 losing 95 % of its
hours** (9,680 → 457), not a cleaner phase screen.

ERA5-Land cannot supply a competing estimate: its `total_precipitation` is interpolated from ERA5 and
**never sees the 0.1° orography**, so any gradient fitted to it is the parent field on a finer grid.

> **Verdict: NO. Answered, not deferred.** ⛔ Do not soften this to "promising but uncertain". A usable
> lapse rate needs the convection-permitting route (**OD-10**), not this track.

---

## 6. Use 5 — operational ingest of DHM real-time observations

**What would have to be true.** The real-time feed would have to be at least as clean as this sample,
and its failure modes detectable automatically.

**What the evidence says.** This sample is **already QC'd by DHM** and still contains candidate
zero-runs to **52 days** (M-A3 flags them; they are not adjudicated as false), a stuck-high block of
**exactly 120 hours** at Sindhuli Madhi (~1,728 mm/day), and Lukla's sentinel values — **45 on the
normalised hourly grid**, 46 in the raw file. The operational feed is understood to deliver
10-/15-minute data with **little or no QC**, through an API whose shape we have not seen.

⛔ This is a **quality precedent**, not a capability claim: we are not asserting the live feed is bad,
we are recording that the *post-QC* sample sets the floor.

> **Verdict: NOT WITHOUT AN AUTOMATED QC GATE.** The defects above are exactly the class that would
> corrupt a forecast silently — a stuck sensor reads as an extreme event, a false-zero run reads as a
> dry spell. ⛔ Do not ingest before the M-I1 rules run on the live shape.

---

## 7. IMERG — assessment, and whether M-A5b is worth writing

**⚠️ M-A5b as currently written specifies IMERG *Final*.** Final has ~3.5-month latency and is the one
run we could **never** use operationally. **The owner has decided the target is IMERG Early**
(2026-08-28). ⇒ **M-A5b must be rewritten around Early before it is worth executing.**

### 7.1 Measured latency (this document's own probe, 2026-08-28)

Endpoint: **GES DISC HTTPS archive**, `https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07/`,
NASA short name **`GPM_3IMERGHHE_07`**, authenticated via Earthdata Login. "Available" is defined by the
archive's **`Last-Modified`** header. Retrievability **confirmed** (HTTP 200, 48 granules/day).

| granule nominal end (UTC) | available (UTC) | measured lag |
|---|---|---|
| 2026-08-28 02:29:59 | 2026-08-28 07:20:32 | **4 h 50 m** |
| 2026-08-28 02:59:59 | 2026-08-28 07:20:32 | 4 h 20 m |
| 2026-08-28 03:29:59 | 2026-08-28 08:20:23 | **4 h 50 m** |
| 2026-08-28 03:59:59 | 2026-08-28 08:20:23 | 4 h 20 m |

**Two operational facts the documented "~4 h" hides.** The measured lag is **4 h 20 m – 4 h 50 m**, not
4 h. And granules publish in **hourly batches of two** (note the paired `Last-Modified` values), so the
freshest available observation is never fresher than ~4 h 20 m and is routinely ~4 h 50 m old.

### 7.2 What the literature supports, and what it does not transfer

**⛔ Early is not "Final without calibration".** Per the NASA V07 ATBD, Early's standard precipitation
field **does** receive Final-derived **climatological** calibration. Differences **include**
forward-only morphing and fewer microwave inputs — and also climatological versus contemporaneous GPCC
adjustment, different GPROF/CORRA products and calibration windows, motion-vector sources, Kalman
windows, phase inputs and manual QC. ⇒ **Final results are non-transferable evidence about Early, not
a bound in either direction.** Every quoted skill figure must be attributed to the run it was measured
on.

**A trap specific to operational evaluation:** *retrospectively downloaded* Early can contain inputs
unavailable in live operation, so even Early-based published skill can **overstate live performance**.
Any future evaluation must state whether its Early data was retrospective or captured in real time.

**Two limitations, and the second is probably the binding one.**
- **Geographic.** Published Nepal validation finds satellite products correlate well in the southern
  and middle hills and poorly over the northern high mountains, and underestimate mean annual
  precipitation. Passive-microwave retrieval degrades over snow and ice, and IMERG deliberately
  **retains** microwave estimates over snow/ice within 60°N–S rather than masking them — so the high
  basins return numbers whose conditions make them least trustworthy. ⇒ **IMERG is strongest where our
  gauge network is already densest and weakest where it is sparsest.**
- **Temporal.** All three runs reproduce **daily** patterns considerably better than **event-level
  sub-daily** rainfall — and this track's operational resolution is 3-hourly. ⛔ Stated at the scale
  the evidence supports: this concerns event-level correction and does **not** by itself establish that
  a long-record **climatological** diurnal shape from IMERG would be poor.

### 7.3 Recommendation on M-A5b

> **Worth writing — narrowly, and rewritten around Early.** The case rests on one asymmetry: IMERG's
> demonstrated strength is at low and mid elevation, which is exactly where Group B's 20 stations can
> validate it independently, and exactly where use 3's correction was found viable. Its weakness is the
> high basins, where neither our gauges nor this track's other routes reach either.
>
> **⛔ It is NOT worth writing as a route to the high basins**, and not as a nowcast corrector at
> event scale without first testing sub-daily skill against our own gauges. A ~4 h 20 m – 4 h 50 m
> latency also rules IMERG out of any correction inside that window — it can inform a forecast issued
> later, never the nowcast covering the hours it describes.

**Status update 2026-08-29 — this recommendation has been acted on.** M-A5b was rewritten around
**Early** as **Plan 211**, implemented, and merged (**PR #229**, `main` `bf7b4265`, v0.1.837) after
four independent review rounds.

⛔ **What merged is the PIPELINE, not the data. No bulk retrieval has run and no bundle has ever been
published** — the plan halts at T1's projection gate (measure one granule, project over 105,216, stop
before committing several hundred GB). **That retrieval remains an owner decision** and is not implied
by the merge.

🔴 **Two prerequisites must close before any real acquisition or re-acquisition** (safe to defer only
while nothing is published): reconcile acquisition gaps against each station's hourly `granule_count`,
and protect a record that an already-published bundle's digest names.

---

## 8. Summary

| use | verdict |
|---|---|
| 1 · diurnal timing correction | **Promising, not deployable on present evidence** |
| 2 · gap-filling with ERA5-Land | **No** for magnitude; context only |
| 3 · bias-correcting ERA5-Land forcing | **Conditionally viable below ~2,000 m**; not across the transition |
| 4 · elevation-band lapse rate | **No** — needs OD-10 |
| 5 · operational DHM real-time ingest | **Not without an automated QC gate** |
| IMERG / M-A5b | **Written and merged** (Plan 211, PR #229) — pipeline only, ⛔ no retrieval run |

**The single most consequential finding for the project:** the DHM sample supports *characterisation*
well and *correction* poorly, and every route to the snow-dominated high basins — where the flood
interest sits — is closed by this data. That is a real result, and it is the one to carry into M-DEC.
