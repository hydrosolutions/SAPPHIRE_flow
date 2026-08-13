# DHM hourly precipitation — provenance questions and what we found

**To:** the BSc precipitation team (and, where noted, DHM) &nbsp; **From:** hydrosolutions (SAPPHIRE Flow) &nbsp; **Date:** 2026-08-13

Thank you for `combined_precipitation_37_stations.xlsx` and for the station coordinates — both arrived
and both were immediately useful.

We have now built a reproducible pipeline over the file and checked it carefully. This note has two
halves, and **the second half is the more useful one to you**:

1. **§1–2 — questions we need answered**, because a few of them decide whether some of our numbers are
   right at all.
2. **§3–4 — what we found in the data, and the methodological traps we hit.** Several of these affect
   your analysis as much as ours, and one of them cost us real time. Please treat this as sharing, not
   criticism — none of it is your doing.

Short answers are perfect. **"Not sure / will check"** is a completely fine answer; just mark it so we
know to follow up. Items marked ⭐ are the ones blocking us.

---

## 1. How the file was made ⭐

These are about the processing between DHM's raw export and the delivered workbook. We ask because
some of them change our results by a **factor**, not a margin.

| # | Question | Why it matters | Your answer |
|---|---|---|---|
| 1.1 ⭐ | If the source was sub-hourly, was the hourly value formed by **summing** or **averaging** the sub-hourly values? | If averaged, **every total in the file is too low by roughly the number of sub-intervals per hour**. This is the single question we most need answered. | |
| 1.2 ⭐ | Does a timestamp label the hour **ending** at that time, or the hour **beginning**? | A one-hour offset shifts every sub-daily result. It currently blocks all our diurnal analysis. | |
| 1.3 ⭐ | Was any **timezone conversion** applied (NPT = UTC+5:45), and if so, to **all** rows or only some? | 3,350 rows sit off the hourly grid, mostly at minute 15 and 45 — the signature of an NPT→UTC conversion applied to part of the data. See §3. | |
| 1.4 | How were the **37 stations selected**? | Any statement we make about "Nepal" is conditional on this. Without it we cannot generalise beyond the sample. | |
| 1.5 ⭐ | The file has **37 station columns, but 11 of them contain no data at all** — zero values across all 55,379 rows, header only. Were those stations expected to have data? Requested but not delivered by DHM, or lost somewhere in processing? | This is 30 % of the delivered columns, so the file is effectively **26 stations**. If the data exists at DHM, recovering it is the cheapest possible improvement to the dataset. See §3. | |
| 1.6 | Is the **raw DHM export** still available? | We would rather start from it than from a processed copy. | |
| 1.7 | **Instrument type per station** — tipping bucket or weighing gauge; heated; shielded; orifice height. | Six stations report to 0.01 mm and twenty to 0.2 mm. We do not know whether that is two instrument types or two processing chains — see §3. | |

## 2. Data availability

| # | Question | Your answer |
|---|---|---|
| 2.1 | *(We understand this is a subset of the national network and are not asking for all of it — the question is only about the 11 empty columns in §1.5, which were apparently meant to be included.)* For context: Adhikari et al. (2025, *J. Inst. Sci. Tech.*) used 63 DHM automatic weather stations, so more hourly data does exist should a wider sample become useful later. | |
| 2.2 | Are QC flags available, so that **"missing" can be distinguished from "removed by QC"**? At present the two are indistinguishable in the file. | |
| 2.3 | Is precipitation available **operationally** (the 10–15 min feed), and through what interface? | |

---

## 3. What we found in the file

All of this survived DHM's own QC. **It affects your results as much as ours.**

### Defects

| Station | What | Detail |
|---|---|---|
| **Lukla Airport** | sentinel values | **46 values of `-9999999`** (45 of them on the hour). Any total including these is meaningless. |
| **Sindhuli Madhi** | stuck sensor | 2025-08-03 → 08-08, **every hour pinned at ~72 mm** → **1,728 mm/day for four consecutive days**, 8,642 mm in 120 hours. Not physical. |
| **Aiselukhark** | implausible dry run | **52.5 consecutive days of exact zero during the monsoon** |
| Nagarkot_AWS | " | 36.9 days |
| Lete (FNEP) | " | 35.5 days |
| Pakhribas | " | 23.4 days |

Long monsoon zero-runs may be a clogged or disconnected gauge, QC-removed data written as zero, or a
logger default — we cannot tell from the file alone, which is part of why §1 matters.

**A worked consequence.** Khumaltar's 2023 annual total is **294 mm**, against **1,504 mm** in 2024 —
while passing a ≥85 % data-coverage filter. The gap is a long zero-run, not missing data.

### Structure

- **26 usable stations of 37**; empty: Kathmandu Airport (AWOS), Dhankuta_AWS, Okhaldhunga_AWS,
  Chautara, Salleri, Sarmathang, Mai Pokhari, Gaighat, Dharan Bazar, Gaida (Kankai), Madi Kalyanpur.
- **3,350 rows off the hourly grid** (6.0 % of rows, but only 0.64 % of observations — off-grid rows
  are far sparser). Mostly Lukla and Udayapur Gadhi.
- **Two reporting populations**: six stations report to 0.01 mm, twenty to 0.2 mm. The 0.01 mm group
  reports rain in up to 55 % of monsoon hours, which looks like an un-de-noised sensor floor rather
  than rain. **These six are also, exactly, the six highest stations** (2,490–3,700 m) while the other
  twenty span 67–2,147 m — **zero overlap**. So reporting precision and altitude cannot be separated
  in this sample, and no result can attribute an effect to one rather than the other.

---

## 4. Traps we hit — offered so you don't

**Coverage percentage is not a quality filter for precipitation.** Khumaltar 2023 above passes ≥85 %
coverage and is still wrong by a factor of five. A completeness threshold cannot see a stuck-zero run.

**Do not compare distributions by correlating quantile vectors.** We did this and reached a confident,
wrong conclusion. Quantile vectors are monotonically increasing by construction, so **two completely
unrelated distributions correlate at r > 0.94** (we measured exponential vs Pareto at 0.943). Use
scale-normalised ratios, a divergence measure, or held-out prediction error instead.

**Neighbour corroboration is weak in Nepal.** Median nearest-neighbour distance across these stations
is **27 km**, and monsoon inter-station correlation is ~0.05 hourly and ~0.28 daily. Only 7 of 323
station pairs are within 25 km. An outlier rule of the form "flag unless a nearby station agrees" has
very little to work with here.

**Harmonise detection thresholds before any frequency statistic.** With 0.01 mm and 0.2 mm stations
mixed, a wet-hour or wet-day frequency computed across the network compares instruments, not climates.

**Dropping suspect periods biases what remains.** If you exclude periods because they look dry, the
retained sample over-represents wet conditions — so wet-day frequency, false-alarm rate and CSI are
all biased even if you mask both datasets identically. Report such statistics as conditional on
retention, and avoid annual totals computed from masked data.

**Gauge undercatch is signed.** Gauges under-measure — modestly for rain in wind, severely for snow.
So a reanalysis or satellite product appearing "wetter than the gauge" is not by itself evidence that
the product is too wet.

---

## 5. Where we differ, and why that is useful

We are running an independent pass over the same file, deliberately — not to duplicate your work but
so that two analyses can be compared. Our QC is coarser than yours will be (we discard suspect periods
wholesale rather than adjudicating them, because we only need a forcing comparison).

**Where our results and yours disagree, that disagreement is itself a finding**, and we would like to
chase it together rather than quietly reconcile it. We are holding our comparative results until both
sides have written theirs down, for the same reason.

One early example already: the published national diurnal peak from gauges (21:00) and from satellite
(around midnight) differ. Our data suggests that gap may be a **sampling artefact** — timing varies
systematically with elevation, so a station network and an area-weighted satellite product sample
different elevation distributions. That is testable, and it would be a genuine contribution.
