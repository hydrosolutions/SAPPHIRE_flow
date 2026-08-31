---
status: DRAFT
created: 2026-08-31
plan: 220
title: M-A11b — the remaining overlapping JJAS seasons, per-year and pooled
scope: Parameterise the TIGGE retrieval and timing analysis by season year, retrieve JJAS 2020-2024 alongside the existing 2025, and report the phase offset per year and pooled. NOT a new estimator, NOT a correction, NOT a change to any 2025 number.
depends_on: [216]
blocks: [M-DEC follow-on, any retraining decision]
source: docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md — independent verdict's prerequisite 2
---

# Plan 220 — M-A11b multi-season IFS timing

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**This is more data through an existing, reviewed pipeline — not a new measurement.** The estimator,
amplitude gate, branch convention, elevation bands and lead bands are all settled and **must not
change**. No new framework, abstraction layer or file format. **Adding length is a cost.**

## Why this plan exists

M-A11 established that operational IFS carries roughly 40–55 % of ERA5-Land's monsoon phase error in
the hills (−5.5 to −6.5 h against −11.9/−14.4 h), with the Terai inside the ±3 h bound. An independent
review judged that result **not sufficient for a retraining commitment** and named five prerequisites.

**One is now closed:** DHM confirmed on 2026-08-31 that the gauge timebase is **UTC and period-ending**,
so the as-labelled reading is correct and the offsets stand.

**This plan closes the largest of the remaining four: one season only.** JJAS 2025 alone cannot show
interannual or IFS-cycle variability, which the review flagged as "potentially several hours or a sign
change".

## What is on disk, and what is not

**Only JJAS 2025** (`data/dhm_precip/tigge/raw/tigge_ecmwf_cf_tp_jjas2025.grib`, 18 MB) — **1 of 6**
overlapping seasons. The gauge record runs 2020-01-01 → 2025-12-31, so JJAS **2020–2024** are all
retrievable from TIGGE and all overlap. ⇒ Five more seasons, ~90 MB total. Volume is a non-issue.

**Gauge coverage is NOT uniform across those seasons** (measured 2026-08-31, stations with ≥ 500 valid
JJAS hours):

| season | stations | ≥ 2,000 m | 1,000–2,000 m | < 1,000 m |
|---|---|---|---|---|
| **2020** | **18** | **3** | 7 | 8 |
| 2021 | 24 | 7 | 8 | 9 |
| 2022 | 25 | 8 | 8 | 9 |
| 2023 | 25 | 8 | 8 | 9 |
| 2024 | 22 | 6 | 8 | 8 |
| 2025 | 22 | 6 | 9 | 7 |

⚠️ **2020 is thin in the band that matters most** — 3 high-elevation stations against 6–8 later. Five
of the high stations begin reporting around 2020-10-12, *after* that JJAS.

## Decisions

- **D1 — The year becomes a parameter, and EVERYTHING derives from it.** The current constant is
  deliberately hardcoded, and the code says why: *"a wrong year, mislabelled 'JJAS 2025' downstream, is
  the failure mode"* (`tigge_ifs.py:51`). That hazard is real and this plan must close it, not inherit
  it. ⇒ The raw filename, the points filename, the manifest, the attribution sidecar and every log line
  **derive from the same single year value** — ⛔ no second source, no format string built from a
  different variable. This is the identity rule this track has re-learned four times: **a value carried
  alongside can disagree; a value derived cannot.**

- **D2 — 244 is the EXPECTED schedule, not a required count. Real seasons have gaps.**
  ⚠️ **Owner correction 2026-08-31:** *"there may not be full 244 forecast runs — sometimes forecasts
  are not published."* The current gate demands **exactly** 244 inits and rejects anything else
  (`tigge_ifs.py`), which would throw away an entire season for one unpublished run. JJAS 2025 happened
  to be complete; that is luck, not a contract.
  ⇒ **Check the retrieved inits against the expected 244-run schedule and record every absence as an
  explicit, named gap** — the same treatment IMERG's missing granules get in Plan 211: a gap is *data*,
  not a failure. ⛔ Still reject a retrieval that returns runs **outside** the expected schedule,
  duplicates, or the wrong init hours — those mean the retrieval is wrong, which is different from the
  archive being incomplete.
  ⇒ **Publish a completeness figure per season** (inits present / 244) beside every result, and state
  it in the report. ⛔ A season that is materially incomplete must say so next to its numbers; do not
  let a 70 %-complete season sit in a table looking like a 100 %-complete one.
  *(The arithmetic behind 244 is still right and unchanged: JJAS is 122 days in every year — no
  February — so 122 × 2 = 244 for 2020–2025 alike. What changes is that this is the expected count, not
  an admission requirement.)*

- **D3 — Report PER-YEAR *and* POOLED.** *(Owner decision 2026-08-31.)* ⛔ Never pooled-only, and
  ⚠️ **the pooled figure must state how it weights unequal seasons**: 2020 contributes 3 high-band
  stations where 2022 contributes 8, so a naive pool silently gives some years more influence.
  State the weighting; if per-year and pooled disagree, the per-year spread is the finding. The question is interannual variability, and a
  pooled median answers a different question — it would hide exactly the spread the review asked for.
  ⇒ Every band × lead cell carries six per-year values plus the pooled figure, each with its own `n`.

- **D4 — 2020 is INCLUDED, with a hedge attached to every 2020 number.** *(Owner decision 2026-08-31: include, do not exclude.)* ⛔ Do not silently pool a 3-station high band with
  6–8-station seasons. Carry its station count beside every 2020 cell, and state in the report that the
  high band that season rests on three gauges. If a 2020 cell fails the existing coverage or amplitude
  gate, that is a result — record it, do not widen a gate to admit it.

- **D5 — NO estimator changes.** Same pairing, 6-hour period-ending gauge windows, `sum/count` hourly
  means, first-harmonic phase, `MIN_HARMONIC_AMPLITUDE = 0.05`, same `[-18,+6)` branch, same elevation
  and lead bands. ⛔ Changing the method and the sample together makes the comparison uninterpretable.

- **D6 — This is a fresh six-season analysis; the 2025 check is a DIAGNOSTIC, not a freeze.**
  Owner direction: *"we re-do the analysis."* ⇒ The deliverable is the whole 2020–2025 result, not a
  patch to the 2025 one. **But still re-run 2025 and compare** against the published
  −1.34/−6.47/−5.52, −0.39/−5.78/−5.63, −0.68/−6.15/−6.52.
  ⛔ **A difference is not automatically acceptable — it must be EXPLAINED and attributed** to a named
  cause (e.g. D2's gap handling admitting runs the old exact-count gate rejected). An unexplained change
  means the parameterisation broke something. ⛔ Do not silently re-baseline.

## Tasks

### T1 — parameterise the season (depends: nothing)
**In:** D1 — thread a single year value through retrieval, filenames, manifest, attribution and logs;
D2 leaves the 244 gate untouched.
**Out:** any retrieval; any analysis change.
**Verify:** D6's 2025 regression test passes **before any new data is fetched** — the parameterisation
alone must be inert. ⛔ Plus a test that a mismatched year cannot be written into a filename while a
different year is recorded in the manifest — the hazard `tigge_ifs.py:51` names.

### T2 — retrieve 2020–2024 (depends: T1)
**In:** five ECDS requests, same route and read contract as D6 of Plan 216 (`tigge-forecasts`, 0.5°,
study box, `type: cf`, 6-hourly, `kg m**-2` asserted from the file attribute).
**Out:** any analysis.
**Verify:** each season's inits are checked against the expected 244-run schedule with absences recorded as named gaps (D2), and its completeness figure published; each raw file and its
points parquet carry the correct year in name and manifest; the 2025 artifacts are **untouched**.
⚠️ ECDS queues — expect this to take time, not to fail.

### T3 — per-year and pooled report (depends: T2)
**In:** D3's per-year and pooled table; D4's 2020 caveat; the existing amplitude gate and statuses
applied unchanged per season.
**Out:** any correction; any estimator change; any claim beyond phase.
**Verify:** 2025's row equals the published table exactly (D6); every cell carries its `n` and station
count; the ±3 h bound is restated per D4 of Plan 216. ⛔ State plainly whether the offsets are stable
across years or not — **"they vary by several hours" is a permitted and valuable result** and would
change the retraining answer.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit

The IFS-versus-gauge phase offset for **every overlapping JJAS season (2020–2025)**, reported per year
and pooled, with per-cell `n`, station counts and a per-season completeness figure; 2020 included and hedged;
and the re-run 2025 row either matching the published table or its difference explained and
attributed. **This closes prerequisite 2 of the independent verdict's four remaining
items** — it does not close the other three (estimator/branch sensitivity, station representativeness,
finer-than-6-hourly forcing), and does not by itself authorise retraining.

## Non-goals

Any estimator, gate or branch change (D5) · any correction or bias work · any re-baselining of the 2025
numbers (D6) · other TIGGE centres · operational use (the ~48 h embargo stands) · seasons outside the
gauge overlap · leave-one-station-out or alternative-estimator sensitivity (prerequisites 3 and 4,
separate work).
