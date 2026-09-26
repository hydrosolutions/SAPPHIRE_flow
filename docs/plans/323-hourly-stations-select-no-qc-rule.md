---
status: DRAFT
created: 2026-09-24
revised: 2026-09-26
plan: 323
title: Five Swiss stations report hourly and select no QC rule at all
scope: Give the observation QC rule set a 3600 s cadence for the parameters the hourly BAFU stations deliver, so those stations are actually checked instead of selecting zero rules — on ~95% of checks; the owner accepted the leftover on 2026-09-25. NOT how the cadence is inferred (Plan 400), NOT an hourly `frozen_sensor` row (no plan yet), NOT the DHM/Nepal rule rows (303), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT per-station overrides (269), NOT re-QC of the rows already stored (owner, 2026-09-24 — history is left), NOT the pick-up set (317, merged), NOT the consumer policy (316, merged).
depends_on: []
blocks: [400]
related: [264, 269, 272, 303, 313, 316, 317, 318, 400]
open_decisions: []
source: 2026-09-24 — the owner reported Slack warnings that BAFU observations were unchecked. Every claim in § What is measured was measured on the staging host that day against v0.1.965 and against `main` at `3b515f6b`; each says how. Items 10-13 were added on 2026-09-25 from an independent Claude review, measured on staging (running `main` at `7a7f2aae`) and against `main` at `2fe660e2`.
---

# Plan 323 — five Swiss stations report hourly and select no QC rule at all

## Status

**DRAFT.** ⛔ No implementation until an independent review of **this exact state** is complete and
the orchestrator sets READY. Two review rounds have run — a Claude review on 2026-09-25, then
Claude and Codex on the fold on 2026-09-26 — all NOT READY, all findings folded (§ Review record).
Owner decisions changed on both days (D2, D3). **This state is unreviewed.**

⭐ **What this plan now promises, and what it does not.** It makes the five hourly stations
*checkable*: about 95% of their checks will run real rules. It does **not** make the Plan 318
watchdog go quiet — § (10) measures why, and the owner accepted that leftover on 2026-09-25 (D3).
Plan 400 is the follow-on that closes it.

## Why this plan exists

Plan 272 made a silent failure loud. Before it, a group that resolved **zero** QC rules was stamped
`QC_PASSED` — a fail-open. Since the staging deploy of 2026-09-24T10:38Z, such a group is stamped
`QC_UNCHECKED` and the Plan 318 watchdog says so out loud.

It said so immediately, and it was right. **Five BAFU stations deliver one reading per hour**, and
the rule set declares only 600 s and 86400 s. An hourly series matches neither, so zero rules are
selected and nothing is checked.

⭐ **This is not a regression and not new.** Those stations have delivered hourly every day for at
least a fortnight. What changed is that we can now see it. Their earlier readings carry a
`qc_rule_version` and **no flags at all** — the fail-open signature — so they were never quality
controlled; they were only labelled as though they had been.

⛔ **Plan 317 does not fix this and was never going to.** 317 re-examines an unchecked reading on
the next cycle, which is correct for a *transient* cause. This cause is not transient: the station
will be hourly again next cycle, and the cycle after. 317 will re-examine these readings forever
and they will stay unchecked — correctly. The missing thing is a rule that describes an hourly
station.

## What is measured

All figures from the staging host, 2026-09-24, unless stated.

1. **The affected population.** Median inter-reading gap per station-parameter group over the
   preceding 12 h: **333 groups across 140 stations at 600 s, and 9 groups across 5 stations at
   3600 s.** The five: Zernez (2319), Buseno (2474), Plaffeien-Schwyberg (2251), Plaffeien-Schwyberg
   (2252), Oberwald (2623). Parameters affected: `discharge`, `water_level`, and `water_temperature`
   at Oberwald.
2. **What the rule set declares.** `config.toml` holds 26 rules at exactly two cadences — **14 at
   600 s** (discharge ×5, water_level ×5, water_temperature ×4) and **12 at 86400 s** (discharge ×4,
   water_level ×4, precipitation ×2, temperature ×2). **Nothing at 3600 s.** Counted from the file
   that is the running config.
3. **Why zero rules follow.** `QcRuleSet.rules_for` matches the inferred cadence by **exact
   equality** (Plan 272; the owner closed the cadence as inferred, not declared, on 2026-09-19). An
   inferred 3600 s equals neither 600 nor 86400, so the rule list is empty.
4. **It is long-standing.** Median gap per station per day for the four discharge series, each of the
   preceding 14 days: **3600 s on every station on every day**, 17-24 readings on each full day. It never
   varies.
5. **The earlier rows were fail-open passed.** Over those 14 days those four series hold **1,277
   rows stamped `qc_passed`, every one with `qc_rule_version` set and zero flags**, versus 11 now
   stamped `qc_unchecked`. The first unchecked row is 11:00Z, 22 minutes after the 10:38Z restart.
6. 🔴 **There is no formula to derive hourly thresholds from.** The existing 600 s↔86400 s pairs do
   not follow one:

   | rule | discharge 600 s | discharge 86400 s | water_level 600 s | water_level 86400 s |
   |---|---|---|---|---|
   | `range_check` | 0 … 100000 | 0 … 100000 (same) | −2 … 20 | −5 … 30 (**differs**) |
   | `rate_of_change` | `max_rate` 50 | 500 (**×10**) | 0.5 | 2.0 (**×4**) |
   | `spike` | `tolerance` 0.1 | 0.5 (**×5**) | `max_delta` 1.0 | 5.0 (**×5**) |
   | `gross_outlier` | `k_sigma` 5.0 | 5.0 (same) | 5.0 | 5.0 (same) |
   | `frozen_sensor` | tol 0.001, 12 | *absent* | tol 0.001, 12 | *absent* |

   The time ratio is 144×, and no threshold scales by it. ⇒ **These are hydrological judgements,
   and the hourly ones must be too.** D1 exists because of this row.
7. **`spike` takes either key and this is deliberate.** `_apply_spike` branches on `max_delta`
   (absolute) and otherwise reads `tolerance` (relative to the previous value). Discharge uses the
   relative form, water_level the absolute one. ⛔ *Checked because it looked like a defect. It is
   not — do not "fix" it.*
8. ⚠️ **`frozen_sensor.min_consecutive` is a COUNT, not a duration.** 12 readings is 2 h at 600 s
   and would be **12 h at 3600 s**. Carrying the number across unchanged silently changes what the
   rule means. This is the one threshold that cannot be copied even if D1 says "copy".
9. **The forecast path is already safe.** Plan 316 (merged) routes an unchecked reading to
   forecasting marked `DEGRADED` and keeps it out of alerts, skill scoring and training. So this is
   five uncontrolled Swiss gauges, honestly labelled — not corrupted forecasts.
10. 🔴 **Adding a 3600 s rule does NOT stop every zero-rule check.** The cadence is inferred from the
    rows in the QC window, `[now − 2 h, now + 1 h)` (`flows/ingest_observations.py:431-432`, default
    `context_window_hours = 2.0` at `:743`; start inclusive, end exclusive,
    `store/observation_store.py:190-191`). Ingest runs every 5 minutes, so `now` is usually a few
    minutes past the hour and the window holds the **two** most recent hourly readings; one missing
    reading leaves one, which infers `None` (`no_cadence_inferable`). Only a run landing exactly on
    the hour sees three readings, where a missing one gives a 7200 s gap. Either way, no rule.
    **Estimate of the rate** — a replay anchored at each stored reading's timestamp (so it sees
    three readings, and reports the miss as 7200 s rather than `None`) over the 9 groups and the
    preceding 14 days: **2,554 of 2,679 checks (95.3%) infer exactly 3600 s**; misses 7200 s ×111,
    `None` ×6, 3900 s ×4, 6900 s ×2, 3450 s ×2. The anchoring differs from production, so this is
    an estimate of the rate, not of the reason code an operator sees; T1 re-measures it from the
    live zero-rule records. Because the watchdog alerts whenever ANY zero-rule record exists in the last 6 h
    (`ops/watchdog.py:220`), replaying those misses against 5-minute watchdog ticks puts it in the
    failing state **64% of the time, on 15 of 15 days**. ⇒ This plan cannot make the warning stop;
    D3 records the owner's acceptance of that, and Plan 400 is the fix.
11. 🔴 **`frozen_sensor` cannot fire at hourly in that window.** `min_consecutive` counts distinct
    instants inside the fetched rows (`services/qc.py:152-210`), and the window holds at most three
    hourly readings. Any count above 3 is inert; a count of 3 means "flat for ~2-3 h", which in the
    current drought is ordinary — at 600 s, `frozen_sensor` already flags ~8% of discharge and ~18%
    of water_level readings (replay over the preceding 3 days at the configured `tolerance 0.001,
    min_consecutive 12`). ⇒ D2 defers the hourly `frozen_sensor` row. Plan 400 keeps the 2 h check
    window (owner, 2026-09-26), so **no plan owns it yet**.
12. **`gross_outlier` runs only where a climatological baseline exists, and for most of these
    groups none does.** `_apply_gross_outlier` returns no flag when the baseline is missing
    (`services/qc.py:270-272`), yet `resolve_selection` counts the rule as selected. On staging,
    `clim_baselines` holds **discharge only, for four of the five stations** (366 rows each) —
    nothing for `water_level`, nothing for `water_temperature`, nothing at all for Oberwald (2623).
    So in 5 of the 9 groups `gross_outlier` is selected and inert. ⛔ Computing baselines is not
    this plan's work; the consequence is only that its selection count must not be read as "this
    many checks ran" (T2).
13. **`water_temperature` has a different 600 s shape.** Four rules, not five — `range_check`
    (−2 … 40), `rate_of_change` (`max_rate` 2.0), `frozen_sensor` (`tolerance` **0.01**,
    `min_consecutive` **18**) and `gross_outlier` (`k_sigma` **4.0**) — and **no `spike`**. § (6)'s
    table covers discharge and water_level only; Oberwald's water temperature follows this row.

## Owner decisions

### D1 — the hourly threshold values. **⚖️ CLOSED — owner, 2026-09-24: derive them from these stations' OWN behaviour.**

Owner: *measure what the five gauges actually do — typical hourly change, observed range, longest
flat stretch — and set the thresholds from that*, rather than from judgement or from a factor
applied to the 600 s row. § (6) is why: the existing pairs scale by ×10, ×4, ×5 and not at all
depending on the rule, so there is nothing to interpolate.

⇒ **The measurement is T1 and it is a deliverable, not preparation.** T2 may not invent a number
T1 did not produce.

🔴 **Three hazards the measurement must handle, or it produces confidently wrong thresholds:**
- **The data has never been quality controlled** (§ 5). It therefore CONTAINS the very outliers the
  rules exist to catch. ⛔ A threshold set at the observed maximum can never fire. Use high
  percentiles and state which, per rule.
- **Switzerland is in drought** (owner, 2026-09-24). Flows sampled now are at the low end of their
  range, and a `range_check` or `rate_of_change` bound fitted to them would flag ordinary high water
  as bad the first time it rains. ⇒ **T1 measures the longest history available for each series,
  not the recent window**, and reports how much it found; if a series has only weeks, its bounds are
  provisional and must say so.
- **Every rule needs its own statistic, in the form the rule computes it.** `range_check` wants the
  value distribution; `rate_of_change` wants the distribution of the raw difference between
  consecutive readings (`services/qc.py:131-149`, no division by time — Plan 313); `spike` fires
  only when a reading differs from **both** neighbours, so it wants the distribution of
  `min(|x − prev|, |x − next|)` — absolute for water_level (`max_delta`), and **relative** for
  discharge, i.e. divided by `|prev|` with `prev = 0` excluded, because discharge's `tolerance` is
  a fraction of the previous value (`services/qc.py:240-246`). ⛔ *One summary table of values
  cannot answer all of these.*
- 🔴 **Water level is checked relative to the station's datum, not as stored.** `range_check` and
  `gross_outlier` see `value − water_level_datum_masl` (`services/qc_datum.py:44-55`, applied at
  `flows/ingest_observations.py:466-470`); that is why the 600 s bounds are −2 … 20 m. And where a
  station has **no** datum, both rules are skipped entirely (`OBS_DATUM_DEPENDENT_RULES`,
  `services/qc_datum.py:14,32-35`). A `range_check` fitted to stored metres above sea level would
  fail every hourly water-level reading. ⇒ T1 measures water_level's value distribution on the
  datum-shifted value and records per station whether a datum exists. The change statistics
  (`rate_of_change`, `spike`, flat runs) are unaffected by a constant shift.

⚠️ `gross_outlier` is the exception: `k_sigma` is a multiplier on a climatological baseline, not a
value in the series' units, and it is identical at 600 s and 86400 s (§ 6). It is cadence-independent
and carries across unchanged from the 600 s row of the same parameter — **5.0** for discharge and
water_level, **4.0** for water_temperature (§ 13) — and T1 need not measure for it. ⚠️ Where no
baseline exists it runs nothing (§ 12).

### D2 — which rules get an hourly row. **⚖️ CLOSED — owner, 2026-09-24; amended by the owner 2026-09-25: the 600 s shape of each parameter, less `frozen_sensor`, which waits for a later plan.**

| parameter | hourly rows (this plan) | deferred — no plan yet |
|---|---|---|
| discharge | `range_check`, `rate_of_change`, `spike` (`tolerance`, relative), `gross_outlier` | `frozen_sensor` |
| water_level | `range_check`, `rate_of_change`, `spike` (`max_delta`, absolute), `gross_outlier` | `frozen_sensor` |
| water_temperature (Oberwald) | `range_check`, `rate_of_change`, `gross_outlier` — **no `spike`**, matching the 600 s set (§ 13) | `frozen_sensor` |

Why the amendment: on 2026-09-24 the owner chose all five checks, `frozen_sensor` included. The
2026-09-25 review measured that the QC window holds at most three hourly readings (§ 11), so an
hourly `frozen_sensor` could only flag a ~3 h flat stretch — ordinary during the drought, and
therefore noisy. The owner deferred it to Plan 400, expecting a wider window. On 2026-09-26 the owner
chose for Plan 400 a separate inference look-back that leaves the 2 h check window as it is, so
Plan 400 cannot carry `frozen_sensor` either: **it waits for a plan that does not exist yet.** On
water temperature the owner chose to match the 600 s set
rather than introduce a `spike` check that exists nowhere else for temperature.

⚠️ **Water level without a datum runs two of its four rows.** `range_check` and `gross_outlier` are
skipped for a water-level station with no datum (D1), so its selection is 2 runnable rules, not 4.

🔴 **Whichever plan adds hourly `frozen_sensor` must re-express `min_consecutive`, not copy it**
(§ 8): it counts READINGS, so the 600 s value of 12 means 2 h there and would mean 12 h at 3600 s.
And per D1's first hazard it must come from a stated percentile of the flat-run distribution, not
from the longest run observed. T1 of this plan measures that distribution so the later plan need
not.

### D3 — the checks that still find no rule. **⚖️ CLOSED — owner, 2026-09-25: accept the leftover here; fix the interval in Plan 400.**

§ (10) measured that about 5% of hourly checks will still infer a cadence no rule declares, because
one missing reading in a three-reading window changes the median gap. The owner's call: this plan
ships the hourly rules and **accepts that leftover** — the Plan 318 watchdog will keep warning
intermittently until Plan 400 lands — and **Plan 400 fixes how the cadence is worked out** for a
series with occasional gaps.

⛔ **Replaced, not amended:** the 2026-09-24 text named nearest-rule matching as "Plan 264's
territory". It is not. Plan 264's scope is the network dimension of selection and never takes on
cadence matching; an in-flight rewrite of 264 in another session (uncommitted, seen 2026-09-25)
excludes "cadence repair" in its own scope line. Nearest-rule matching is commissioned by **no**
plan, and Plan 400 does not need it — see Plan 400 § What is measured.

⛔ **Not a decision: the stored history.** The owner closed it on 2026-09-24 — *leave it* — on the
same reasoning as Plan 315 D3: real customer deployments are onboarded fresh, so a mixed-era corpus
exists only on this development host. § (5)'s 1,277 rows stay as they are.

## Tasks

### T1 — Measure what the five gauges actually do (D1)

**Outcome.** A table, per station and parameter, of the statistics D1 names — enough that every
threshold T2 writes can cite a row of it.

**In.**
- Per series, over **the longest history the store holds** (not the recent window — D1's drought
  hazard): the row count, the span in days, and the value distribution (min, max, and the 1st, 50th,
  99th and 99.9th percentiles). 🔴 **For water_level, on `value − water_level_datum_masl`** — the
  value the rules see (D1) — and a per-station note of whether a datum exists at all.
- Per series, each change statistic **in the form its rule computes it** (D1), same percentiles:
  the raw difference between consecutive readings (`rate_of_change`); `min(|x − prev|, |x − next|)`
  for water_level's absolute `spike`; the same divided by `|prev|`, `prev = 0` excluded, for
  discharge's relative `spike`.
- Per series, the **distribution of flat-run lengths, in hours** — percentiles and the longest — at
  the `tolerance` the 600 s rule already uses (0.001 for discharge and water_level, **0.01 for
  water_temperature**, § 13). This plan adds no `frozen_sensor` row (D2); the measurement is
  recorded for whichever later plan does.
- ⚠️ **The span each series actually has, stated per series.** A series with only weeks of history
  yields provisional bounds and T2 must mark them so.
- The analysis run as a heredoc against the staging database per the repo convention, with the query
  recorded in the plan so it can be re-run when the drought ends.
- ⭐ **The live leftover, from the production records rather than a replay.** The zero-rule health
  records (`OBSERVATION_QC_UNCHECKED`, Plan 318) carry the reason and the inferred seconds per
  group. Count, for the five stations, the readings received since the 2026-09-24 deploy and how
  many are still `QC_UNCHECKED` now (Plan 317 re-examines an unchecked reading while it remains in
  a later run's window, so the final status is what counts). This is the baseline T2's staging check is judged against; § 10's replay is only the
  estimate.

**Out.** ⛔ Changing any rule — T1 only measures. ⛔ Excluding outliers by judgement: the point is
to see them. ⛔ Any claim about *why* these five are hourly.

**Pre-change.** N/A — measurement.

**Verification.** Every number T2 writes traces to a cell in T1's table. ⭐ **A reviewer can re-run
the recorded query and get the same table** — otherwise the thresholds rest on a measurement nobody
can reproduce.

T1 needs the live store; it cannot be done from the repo. (Blocked on 2026-09-24 while the staging
host was off the network; reachable again 2026-09-25.)

### T2 — Add the hourly rules (D1, D2)

**Outcome.** A station delivering hourly selects a non-empty rule list, and its readings carry a
real verdict.

**In.**
- `[[qc_rules.rules]]` rows at `time_step_seconds = 3600` exactly as D2's table lists them: four
  each for `discharge` and `water_level`, three for `water_temperature` — **eleven rows**, no
  `frozen_sensor`. The same rows added to `docs/spec/config-reference.toml`, which mirrors the rule
  set.
- 🔴 **Each threshold carries the T1 statistic it came from**, in a comment beside it. § (5) is the
  argument: an unexplained threshold survived months without anyone noticing it never fired.
- `gross_outlier.k_sigma` carried across unchanged from the same parameter's 600 s row, per D1's
  exception (5.0, 5.0, 4.0).
- 🔴 **A test that an hourly group selects a non-empty rule list**, via `resolve_selection` — the
  operation Plan 272 built for exactly this question. ⛔ *Not "the config parses", which is not this
  defect.*

**Out.** ⛔ Any change to the 600 s or 86400 s rows — this adds, it does not retune. ⛔ Any change
to how selection MATCHES or how cadence is inferred (D3 — Plan 400). ⛔ An hourly `frozen_sensor`
row (D2 — no plan yet). ⛔ Computing climatological baselines (§ 12). ⛔ Re-QC of stored rows.
⛔ DHM/Nepal rows (303).

**Pre-change.** A RED test asserting the DESIRED behaviour: **a group whose inferred cadence is
3600 s resolves a non-empty rule list** — which fails today because nothing declares 3600 s.
⚠️ It must fail on the empty list, not on a missing config key.

**Verification.**
- An hourly group selects exactly its D2 rows — 4 for discharge and water_level, 3 for
  water_temperature — asserted by `rule_id`, not only by count, with a datum set for water_level;
  and a water_level group **without** a datum reports 2 runnable rules (`rate_of_change`,
  `spike`) (D2); **a 600 s group's selection is
  unchanged** (asserted, so the addition cannot perturb the 140 stations that were fine). The test
  loads the shipped `config.toml` rule set, not a hand-built one — a hand-built set proves nothing
  about the file this plan edits.
- ⚠️ The selection count includes `gross_outlier`, which runs nothing where no baseline exists
  (§ 12). It is therefore not evidence that a check ran; the range and temporal rules are.
- A synthetic hourly series with a value outside `range_check` comes back `QC_FAILED`; an ordinary
  one comes back `QC_PASSED` — the rules must be able both to fire and not to fire.
- 🔴 **Each threshold is exercised at least once against T1's percentiles**: a value at the 99.9th
  percentile does NOT flag, one beyond the chosen bound does. ⛔ *Otherwise a threshold can be
  mistyped by an order of magnitude and every test still passes.*
- ⭐ **On staging, over the first full day after deploy**, with the queries recorded here:
  - **Numerator / denominator:** the five stations' readings received that day whose stored status
    at the end of the day is not `QC_UNCHECKED`, over all their readings received that day. It must
    be at least T1's live baseline share and at least 90%; a lower figure is reported to the owner.
  - **Every** zero-rule health record for the five stations that day carries reason
    `no_cadence_inferable` or an inferred cadence other than 3600 s — i.e. a gap, not a missing
    rule. (A reading row stores no cadence; the health records do.)
  - ⛔ *Not* "the watchdog stops warning": it will not (§ 10, D3), and a verification that demands
    it fails for a reason this plan does not own.
  The unit tests prove the mechanism; only this proves the live defect narrowed to the accepted
  leftover.

### T3 — Record what happens when a cadence matches no rule (D3)

**Outcome.** The two causes of a zero-rule group, and where each is answered, written where the
next person meets them — not in this plan alone.

**In.** In the observation-ingest map of `docs/touchpoint-maps.md`, beside the Plan 317 pick-up
entry, and under Stage 1 QC (step 2.3) of `docs/architecture-context.md` § Two-stage QC design,
where the rule kinds are described (`docs/standards/wmo.md` defers observation QC to that section):
- **A cadence the rule set does not declare** (this plan's case) is answered by adding rule rows,
  thresholds derived from the series' own measured behaviour (D1) — not by a factor on another row.
- **A gap inside a known cadence** (§ 10) is answered by Plan 400's inference change; until it
  lands, such checks stay `QC_UNCHECKED` and the watchdog reports them.
- Nearest-rule matching is commissioned by no plan (D3).

**Out.** ⛔ Implementing nearest-cadence matching or changing inference.

**Pre-change.** N/A — documentation.

**Verification.** A reader meeting a third cadence, or a residual zero-rule warning at an hourly
station, finds the answer without re-deriving it; `grep -n "Plan 400" docs/touchpoint-maps.md`
returns the entry.

## Explicitly out of scope

- **`rate_of_change`'s arithmetic** — Plan 313. ⚠️ **But note the interaction, because it changes
  what D1's numbers MEAN.** Today `max_rate` is compared against the raw difference between
  consecutive readings, with no division by elapsed time; 313 would make it a true rate. If 313
  lands after this plan, every `max_rate` set here must be revisited. ⇒ **313 must state that**, or
  this plan's numbers will silently change meaning. Sequencing is the orchestrator's call; the
  dependency is recorded here either way. ⚠️ **Checked 2026-09-25: Plan 313 does not yet carry the
  reverse note** — it mentions hourly rules only as synthetic test fixtures. Adding it is 313's
  owner's edit, not this plan's.
- **Onboarding's QC** — Plan 315. ⚠️ 315 D1 runs after every plan that changes the rule set's shape
  or the cadences it declares, and names 303, 269, 313 and 264 — not this plan, which adds a
  cadence. Recorded for 315's owner; 315 is not edited here.
- **How the cadence is inferred for a series with gaps** — Plan 400 (D3).
- **An hourly `frozen_sensor` row** — no plan yet (D2); Plan 400 keeps the 2 h check window and cannot carry it.
- **Nearest-cadence matching** — no plan (D3). Most-specific-wins across networks is Plan 264.
- **Per-station threshold overrides** — Plan 269. A per-station override is not a substitute for a
  cadence the rule set cannot express.
- **Re-QC of stored history** — closed by the owner, above.
- **Why BAFU delivers hourly for these five.** Worth asking them, and unrelated to whether we check
  what they send.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T3"], "parallel": false}
  ]
}
```

## Review record

- **2026-09-25 — independent Claude review, NOT READY, six findings; all folded above.** (1) Adding
  a 3600 s rule leaves ~5% of hourly checks with no rule and the watchdog failing ~64% of the time
  → § 10, D3, T2 verification. (2) D3 named Plan 264 as owner of nearest-rule matching; 264 does not
  own it → D3 replaced. (3) `frozen_sensor` cannot fire in a three-reading window → § 11, D2
  (owner: defer to Plan 400 — changed 2026-09-26, see the next entry). (4) `water_temperature`'s 600 s set has four rules and different
  values → § 13, D2 (owner: match it, no `spike`). (5) `gross_outlier` inert without a baseline
  → § 12, T2. (6) Plan 313 lacks the reverse note → recorded in § Explicitly out of scope; not
  edited here.
- **2026-09-26 — independent Claude review and independent Codex review of the fold: both NOT
  READY; all findings folded.** Both: water level is checked datum-relative, and discharge's `spike`
  is relative — T1 must measure each rule's own statistic in the rule's own form (D1, T1, D2's
  datum note). Claude: the `frozen_sensor` count must come from a percentile, not the longest run
  (D2, T1); the replay's anchoring differs from production, so § 10 is an estimate and the live
  baseline comes from the zero-rule records (§ 10, T1); T2's staging check had no measurable form
  and no tolerance (T2); `blocks: [400]`; `config-reference.toml` mirrors the rules (T2); the 264
  rewrite cited is uncommitted (D3); Plan 315 is not told of the new cadence (§ Explicitly out of
  scope). Codex: the replay and the live check counted different populations (T2's numerator and
  denominator). Owner, same day: Plan 400 keeps the check window, so hourly `frozen_sensor` has no
  plan yet (§ 11, D2).
