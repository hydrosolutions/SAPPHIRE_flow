---
status: READY
created: 2026-09-24
revised: 2026-09-29
plan: 323
title: Five Swiss stations report hourly and select no QC rule at all
scope: Give the observation QC rule set a 3600 s cadence for the parameters the hourly BAFU stations deliver, so those stations are actually checked instead of selecting zero rules — on ~95% of checks; the owner accepted the leftover on 2026-09-25 — and, so that no hourly reading is marked passed unjudged, require that a reading passes only if some selected check could actually judge it (T4; recorded, not alarmed on — D5; owner 2026-09-26). NOT supplying water-level datums (Plan 403). NOT how the cadence is inferred (Plan 400), NOT an hourly `frozen_sensor` row (no plan yet), NOT the DHM/Nepal rule rows (303), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT per-station overrides (269), NOT re-QC of the rows already stored (owner, 2026-09-24 — history is left), NOT the pick-up set (317, merged), NOT the consumer policy (316, merged).
depends_on: []
blocks: [400, 403]
related: [264, 269, 272, 303, 313, 315, 316, 317, 318, 400, 403]
open_decisions: []
source: 2026-09-24 — the owner reported Slack warnings that BAFU observations were unchecked. Every claim in § What is measured was measured on the staging host that day against v0.1.965 and against `main` at `3b515f6b`; each says how. Items 10-13 were added on 2026-09-25 from an independent Claude review, measured on staging (running `main` at `7a7f2aae`) and against `main` at `2fe660e2`.
---

# Plan 323 — five Swiss stations report hourly and select no QC rule at all

## Status

**READY** — set by the orchestrator on 2026-09-26, on the owner's confirmation and Codex's READY
recommendation. Eleven review rounds ran — a Claude review on 2026-09-25, then ten Claude + Codex
rounds on 2026-09-26. Rounds 1-6 were NOT READY; round 7 was Codex READY and Claude NOT READY;
**rounds 8-11 were READY from both, round 11 on the exact text this status change was applied to**
(§ Review record). Owner decisions changed on both days (D1, D2, D3), and D4 and D5 were added on
2026-09-26.

⚠️ **T1 needed the staging store**, which was off the network while the owner was away from the office
(about two days from 2026-09-26); **T1 has since been run (2026-09-29, § T1 result).** T2 may not invent a number T1 has not produced (D1). ⇒ **Owner,
2026-09-26: build T4 first** — it needs no measured number. Its RED test therefore uses a case that
exists on today's rule set (a 10-minute datum-less water-level group, § T4 Pre-change) instead of
the hourly one, which becomes a T2 verification. All tasks stay on one branch and one PR, so T5's
"T2 and T4 deployed together" still holds.

📏 **T1 measured 2026-09-29** (§ T1 result). The five gauges have 26.9 days of hourly history, so every
derived threshold is provisional. Two of the eleven D1 values are derived (water_temperature `max_rate`
2.358, discharge `spike` `tolerance` 0.750); the rest are copied 600 s values. The premise D3 rested on
is **consistent with the new measurement**: 95.1% of these stations' readings had a stored hourly
neighbour when they were stored (§ (10) estimated 95.3%) — a proxy for "the window infers 3600 s", not
a replay of it (§ T1 result); T5 measures the real share. A first reading of the live zero-rule records
looked like 57.7%, but those entries count runs, not readings, and are not comparable.

🔄 **Reconciled with `main` on 2026-09-29 (after PRs #315, #324, #328, #332).** T4 is built and
re-applied on top of that `main` — branch `feat/plan-323-t4-rebased`, reviewed READY by an independent
Claude and by Codex on the rebased code; T1 is measured (§ T1 result); T2 is built (eleven hourly rows on all three surfaces, `TestHourlyRules`); T3 and T5 are not. What changed under this plan,
and what it changes for the tasks still to build:
- **Selection is now per network** (#315): `resolve_selection`, `check` and T4's `check_with_coverage`
  take a required `station_networks` mapping, and `rules_for` lets a network-specific rule replace a
  network-less one with the same rule id, parameter and cadence. The eleven 3600 s rows have no
  `network`, so they apply to every station; a later `network = "…"` row at 3600 s (303, for DHM)
  would replace them for that network. T2's selection test must pass a network for its station.
- **Flags now carry their own rule's `rule_version`**; the module constant `_RULE_VERSION = "1.2"` is
  gone. T2's eleven rows therefore need a `rule_version`: **`"1.0.0"`, as all 29 shipped rows use.** (`docs/spec/config-reference.toml` loads 31 rules and the
  fallback 28: the three surfaces do not hold the same set, so T2 must not assume they do.)
  The rule set's own `version` moves when rows are added (#332 took it to `1.1.0` for its three DHM
  rows): **T2 sets it to `1.2.0`.** Both follow the precedent, not a new decision; the reviewer of T2
  should confirm them.
- **Per-station thresholds apply at ingest** (#324), and `_run_qc_task` now also takes `overrides`.
  No change to this plan's scope: an override still needs a rule at the same cadence to exist.
- **The DHM delivery import (#332) does not use T4's guard**: it calls the checker's original `check`
  and its own status logic, so T4 does not change it (checked 2026-09-29: the first row of a daily
  series is still judged by `range_check`).
- **Line numbers.** Citations of the form `file.py:NNN` in § What is measured and D1-D5 are as of the
  `main` named in `source:` and have moved (`flows/ingest_observations.py` gained about 150 lines,
  `services/qc.py` about 60); they are evidence of what was measured then, not a map of today's code.
  T2 and T4 now name the code by symbol.

⭐ **What this plan now promises, and what it does not.** It makes the five hourly stations
*checkable*: about 95% of their checks should run real rules (the § (10) estimate; the T1 proxy is
consistent with it; T5 measures it). It is **not expected** to make the
Plan 318 watchdog go quiet — § (10)'s replay estimates why (an estimate, not an established post-deploy
schedule), and the owner accepted that leftover on 2026-09-25 (D3).
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

   The time ratio is 144×, and no threshold scales by it. ⇒ **There is no formula to carry a value
   from 600 s to 3600 s.** D1 exists because of this row: range bounds and `k_sigma` are copied
   (they do not depend on cadence), and change limits are measured.
7. **`spike` takes either key and this is deliberate.** `_apply_spike` branches on `max_delta`
   (absolute) and otherwise reads `tolerance` (relative to the previous value). Discharge uses the
   relative form, water_level the absolute one. ⛔ *Checked because it looked like a defect. It is
   not — do not "fix" it.*
8. ⚠️ **`frozen_sensor.min_consecutive` is a COUNT, not a duration.** 12 readings span 110 min at 600 s
   and would span **11 h at 3600 s**. Carrying the number across unchanged silently changes what the
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
    preceding 14 days (less a 24 h warm-up, so every check has a full window behind it): **2,554 of
    2,679 checks (95.3%) infer exactly 3600 s**; misses 7200 s ×111,
    `None` ×6, 3900 s ×4, 6900 s ×2, 3450 s ×2. The anchoring differs from production, so this is
    an estimate of the rate, not of the reason code an operator sees; T1 re-measures it from the
    live zero-rule records. Because the watchdog alerts whenever ANY zero-rule record exists in the last 6 h
    (`ops/watchdog.py:220`), replaying those misses against 5-minute watchdog ticks puts it in the
    failing state **64% of the time, on 15 of 15 days** *in that replay* — evidence that residual warnings
    are likely, not an established post-deploy schedule (§ T1 result). ⇒ This plan is not expected to
    make the warning stop; D3 records the owner's acceptance of that, and Plan 400 is the fix.
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

### D1 — the hourly threshold values. **⚖️ CLOSED — owner, 2026-09-24: derive them from these stations' OWN behaviour; amended by the owner 2026-09-26: LOOSE, informed by that data.**

Owner, 2026-09-24: *measure what the five gauges actually do — typical hourly change, observed
range, longest flat stretch — and set the thresholds from that*, rather than from judgement or from
a factor applied to the 600 s row. § (6) is why: the existing pairs scale by ×10, ×4, ×5 and not at
all depending on the rule, so there is nothing to interpolate.

Owner, 2026-09-26, after a review found that D1 as written conflicted with the standing QC posture
(`docs/v1-scope.md` § QC posture — *LOOSE FIRST, narrowed with data*, owner 2026-09-23: "Do NOT carry
narrow thresholds in from the beginning"): **loose limits, informed by the data.** Concretely:

| rule | hourly value | source |
|---|---|---|
| `range_check` | **the same parameter's 600 s bounds, copied** — discharge 0 … 100000, water_level −2 … 20 (datum-relative), water_temperature −2 … 40 | impossibility gates, not plausibility filters; they do not depend on cadence |
| `gross_outlier` | the same parameter's 600 s `k_sigma`, copied (5.0 / 5.0 / 4.0) | a multiplier on a baseline, cadence-independent |
| `rate_of_change` `max_rate`, `spike` `max_delta` / `tolerance` | **the larger of** 2 × the 99.9th percentile of the rule's own statistic over the gauges' measured hourly history (T1), **and** the same parameter's 600 s value | measured behaviour with a generous margin; never tighter than the 10-minute rule |

⚠️ **Why "never tighter than the 600 s value":** an hour allows more change than ten minutes, so an
hourly limit below the 10-minute one would be tighter where it should be looser.

🔴 **These rows reach every series that infers 3600 s, not only these five.** Selection has no
network dimension until Plan 264, and Plan 269 lets thresholds differ per station without changing
which series the rows reach; and the
staging host runs the live Swiss observation-alert path on `QC_PASSED` readings
(`docs/v1-scope.md` § QC posture). Loose-first is what makes a fleet-wide hourly row safe to ship.

⇒ **The measurement is T1 and it is a deliverable, not preparation.** T2 may not invent a derived
number T1 did not produce; a copied number cites the 600 s row it came from.

🔴 **Hazards the measurement must handle, or it produces confidently wrong thresholds:**
- **The data has never been quality controlled** (§ 5). It therefore CONTAINS the very outliers the
  rules exist to catch, so the observed maximum may itself be an outlier. The table above uses the
  99.9th percentile, not the maximum, for that reason; T1 reports both.
- 🔴 **Only the gauges' own HOURLY MEASURED readings.** These five are CAMELS-CH onboarding basins
  (`config.toml` `[onboarding] basin_ids`), and onboarding imported their CAMELS history as **daily**
  `discharge` and `water_level` rows with `source = manual_import`
  (`adapters/camelsch_adapter.py:60-70,96-101`) into the same `observations` table. Decades of daily
  means would swamp weeks of hourly readings and flatten every peak. ⇒ T1 uses only
  `source = measured` rows, and change statistics only on consecutive pairs exactly 3600 s apart.
  Imported daily history is reported separately and not used.
- **Switzerland is in drought** (owner, 2026-09-24). The hourly history is weeks long and sampled at
  low flow, so its change statistics are low. The 2× margin and the 600 s floor are the protection;
  T1 states the span per series and T2 marks every derived number provisional.
- **Every rule needs its own statistic, in the form the rule computes it.** `range_check` wants the
  value distribution; `rate_of_change` wants the distribution of `|x − prev|`, the absolute
  difference between consecutive readings (`services/qc.py:131-149`, no division by time — Plan
  313); `spike` fires
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

⚠️ `gross_outlier` needs no measurement (table above); where no baseline exists it runs nothing (§ 12).

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
(§ 8): it counts READINGS, so the 600 s value of 12 spans 110 min there and would span 11 h at 3600 s.
And per D1's first hazard it must come from a stated percentile of the flat-run distribution, not
from the longest run observed. T1 of this plan measures that distribution so the later plan need
not.

### D3 — the checks that still find no rule. **⚖️ CLOSED — owner, 2026-09-25: accept the leftover here; fix the interval in Plan 400.**

🔄 *Re-measured 2026-09-29 (§ T1 result): from production timestamps, 95.1% of readings had a stored
hourly neighbour at the time they were stored — a neighbour-availability proxy that is consistent with
the ~5% leftover this decision rested on, not a replay of the window. T5 measures the real figure after
deploy; this decision is unchanged.*

§ (10) estimated that about 5% of hourly checks will still infer no cadence, or one no rule
declares, because one missing reading in a two- or three-reading window leaves one reading or
changes the median gap. The owner's call: this plan
ships the hourly rules and **accepts that leftover** — the Plan 318 watchdog is expected to keep
warning intermittently for these stations until Plan 400 lands (an expectation from § (10)'s replay, not
measured after deploy) — and **Plan 400 fixes how the cadence is worked out** for a
series with occasional gaps.

⛔ **Replaced, not amended:** the 2026-09-24 text named nearest-rule matching as "Plan 264's
territory". It is not. Plan 264's scope is the network dimension of selection and never takes on
cadence matching; an in-flight rewrite of 264 in another session (uncommitted, seen 2026-09-25)
excludes "cadence repair" in its own scope line. Nearest-rule matching is commissioned by **no**
plan, and Plan 400 does not need it — see Plan 400 § What is measured.

⛔ **Not a decision: the stored history.** The owner closed it on 2026-09-24 — *leave it* — on the
same reasoning as Plan 315 D3: real customer deployments are onboarded fresh, so a mixed-era corpus
exists only on this development host. § (5)'s 1,277 rows stay as they are.

### D4 — a reading passes only if some check could judge it. **⚖️ CLOSED — owner, 2026-09-26: fix it in this plan, so it lands first.**

A round-4 review of Plan 400 found that a group can *select* rules none of which can *evaluate* a
given reading, and today's aggregation stores that reading `QC_PASSED`
(`_aggregate_qc_status`, `flows/ingest_observations.py:152-163`, fed a per-group `rules_ran=` at
`:531`). It is the Plan 272 fail-open through a different door, and it is reachable **under this
plan alone**.

🔴 **Its reach is wide, because Swiss river stations have no water-level datum.** CAMELS onboarding
sets `water_level_datum_masl = None` for every river station (`adapters/camelsch_adapter.py:173-189`)
and `config.toml` supplies none, so `range_check` and `gross_outlier` are skipped for water level
fleet-wide (`services/qc_datum.py:32-35`); only `rate_of_change`, `spike` and (at 600 s)
`frozen_sensor` remain. A water-level reading with no usable neighbour — the first after a gap
longer than the window, or the oldest reading when a later run re-examines it (Plan 317) — can be
judged by none of them. DHM stage-relative stations are datum-less by construction too
(`adapters/dhm.py:108-110`). ⇒ Today such readings are stored `QC_PASSED`; after T4 they are
`QC_UNCHECKED`. Measured by the round-5 review with T4 simulated: all 109 ingest/QC tests stay
green, but three DHM tests silently flip a reading from passed to unchecked because they assert
only "not `RAW`" (T4 pins them).

The owner moved the fix into this plan (T4) so no window exists in which hourly readings pass
unjudged; Plan 400 relies on it. The lasting fix for Swiss water level is a datum — **Plan 403**
(owner, 2026-09-26) onboards BAFU gauge-zero datums from the hydrological yearbook, after which
`range_check` judges most water-level readings (every validated river station's). ⚠️ **DHM is not covered by 403**: a gauge-zero-
referenced DHM binding requires a null datum (`adapters/dhm.py:108-110`), so after T4 its
water-level readings with no neighbour stay `QC_UNCHECKED`. **No plan owns that** — recorded for
the owner; a likely fix is to treat a `gauge_zero` binding as datum 0 / unit `m` for QC.

### D5 — how an unjudgeable reading is reported. **⚖️ CLOSED — owner, 2026-09-26: record it, do not alarm on it.**

A reading T4 leaves unjudged stays `QC_UNCHECKED` and is excluded from alerting, skill and training
like any unchecked reading, and it is recorded — but the Slack watchdog does **not** alarm on it.
Until Plan 403 supplies datums it would fire after every missed hourly reading and every feed outage.

Mechanism, and why: the watchdog reads only the **latest** `observation_qc_unchecked` record
(`ops/watchdog.py:174-184`, `limit=1`). Filtering by reason inside that record would let a run with
only unjudgeable readings hide an earlier run's real alarm and post a false recovery. ⇒ Unjudgeable
groups go into a **separate** health record, `check_type = observation_qc_unjudged`, which the
watchdog does not probe. `check_type` is a text column (`db/metadata.py:1984`), so no migration.
The existing record and its alarm keep their meaning: no cadence could be inferred, or no rule
declares it — ⚠️ **including its counts**: today every `QC_UNCHECKED` verdict feeds
`counts["unchecked"]` (`flows/ingest_observations.py:553-557`), which the zero-rule record carries
as `observations_unchecked` (`:300`) and the Slack alert prints as zero-rule readings
(`ops/watchdog.py:1641-1664`). Unjudged readings are counted separately
(`observations_unjudged`, carried only in the new record).

⚠️ **A record counts runs, not readings.** An unjudged reading stays `QC_UNCHECKED`, so Plan 317
re-examines — and re-reports — it on every run while it is in the window, and the LINDAS adapter
ignores `since` (`adapters/hydro_scraper.py:109,122`), so that is every 5 minutes. ⇒ The new record
carries the **observation ids** it reports, and every measurement of unjudged readings in these
plans counts **distinct ids**, never records.

⚠️ **Rollback.** `store/pipeline_health_store.py:43` parses `PipelineCheckType(row["check_type"])`,
so after a code rollback stored `observation_qc_unjudged` rows would make the unfiltered
`/api/v1/health/detail` and the health dashboard fail. Rolling T4 back therefore includes deleting
those rows first.

## Tasks

### T1 — Measure what the five gauges actually do (D1)

**Outcome.** A table, per station and parameter, of the statistics D1 names — enough that every
threshold T2 writes can cite a row of it.

**In.**
- 🔴 **Population:** `source = measured` rows only, over the whole measured history (D1's hazards);
  `manual_import` (CAMELS daily) history reported separately — row count and span — and not used.
- Per series: the measured row count, the span in days, and the value distribution (min, max, and
  the 1st, 50th, 99th and 99.9th percentiles) — for the record; `range_check` is copied (D1). For
  water_level, on `value − water_level_datum_masl`, the value the rules see, with a per-station note
  of whether a datum exists at all.
- Per series, each change statistic **in the form its rule computes it** (D1), same percentiles
  plus the maximum: `|x − prev|` over consecutive pairs exactly 3600 s apart (`rate_of_change`);
  `min(|x − prev|, |x − next|)` over triples whose **two** gaps are both exactly 3600 s, for
  water_level's absolute `spike`; the same divided by `|prev|`, `prev = 0` excluded, for
  discharge's relative `spike`. Then the D1 value per parameter: the
  larger of 2 × the 99.9th percentile (pooled across the parameter's series) and the 600 s value.
- Per series, the **distribution of flat-run lengths, in hours**, on the same measured hourly rows —
  percentiles and the longest — at
  the `tolerance` the 600 s rule already uses (0.001 for discharge and water_level, **0.01 for
  water_temperature**, § 13). This plan adds no `frozen_sensor` row (D2); the measurement is
  recorded for whichever later plan does.
- ⚠️ **The span each series actually has, stated per series.** A series with only weeks of history
  yields provisional bounds and T2 must mark them so.
- The analysis run as a heredoc against the staging database per the repo convention, with the query
  recorded in the plan so it can be re-run when the drought ends.
- **The pre-deploy per-check share** — a diagnostic, not a gate, and measured before T2 only: the
  zero-rule health records (Plan 318) carry each group's reason and inferred seconds per run, and
  today every hourly group is in them, so the share of the five stations' entries with an inferred
  cadence of exactly 3600 s is well defined. ⚠️ *(Measured 2026-09-29: these entries count runs, not
  readings, so this share is a diagnostic of timing only — § T1 result.)* ⛔ It cannot be re-measured after T2: a group that
  selects rules writes no record (`flows/ingest_observations.py:281`), so no denominator exists.
- **Datum coverage, fleet-wide** (D4): the number of water-level groups whose station has no
  `water_level_datum_masl`, and which of the five are among them — so T4's extra `QC_UNCHECKED`
  load is measured, not assumed.

**Out.** ⛔ Changing any rule — T1 only measures. ⛔ Excluding outliers by judgement: the point is
to see them. ⛔ Any claim about *why* these five are hourly.

**Pre-change.** N/A — measurement.

**Verification.** Every number T2 writes traces to a cell in T1's table. ⭐ **A reviewer can re-run
the recorded query and get the same table** — otherwise the thresholds rest on a measurement nobody
can reproduce.

T1 needed the live store and was run on 2026-09-29 (the staging host was reachable again); its result follows.

#### T1 result — measured on the staging host, 2026-09-29 (main `11b9019a`, mini at 0.1.1027; read-only)

**Population.** `source = measured` rows only, non-null values, five stations. Span per series is
**26.9 days for every series — every derived number below is PROVISIONAL** (drought; § D1 hazards).
`manual_import` (CAMELS daily, not used): 2251 and 2252 — 14,061 rows per parameter, 1981-01-01 →
2019-07-01; 2319 — 14,610, → 2020-12-31; 2474 — 14,329, → 2020-12-31; 2623 has none. Four stations
deliver discharge and water_level; 2623 delivers water_temperature only (the plan's "eleven rows" cover
all three parameters, so nothing changes). Readings arrive on a 3600 s grid with occasional 7200 s
gaps (e.g. 2251: 537 hourly pairs, 30 two-hour gaps, 8 of 3300 s).

**Value distribution** (for the record — `range_check` is copied). No station has a
`water_level_datum_masl` (all five NULL). Stored water_level is stage-like for 2251 (0.07–0.12) and
2252 (−0.002–0.019) but **metres above sea level for 2319 (≈1508.3–1508.7) and 2474 (≈745.3–745.6)**, so
no datum-relative range can be judged for any of them — the datum skip removes `range_check` and
`gross_outlier` there (Plan 403's subject).

| series | n | min | p1 | p50 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|---|
| 2251 discharge | 591 | 1.719 | 1.719 | 2.987 | 5.529 | 6.759 | 6.839 |
| 2252 discharge | 625 | 0.005 | 0.005 | 0.038 | 0.117 | 0.129 | 0.135 |
| 2319 discharge | 629 | 0.310 | 0.313 | 0.399 | 1.606 | 1.686 | 1.699 |
| 2474 discharge | 635 | 2.996 | 3.017 | 3.283 | 4.594 | 5.350 | 5.350 |
| 2251 water_level | 591 | 0.068 | 0.068 | 0.085 | 0.109 | 0.118 | 0.119 |
| 2252 water_level | 625 | −0.002 | −0.002 | 0.007 | 0.017 | 0.018 | 0.019 |
| 2319 water_level | 629 | 1508.304 | 1508.305 | 1508.336 | 1508.689 | 1508.710 | 1508.713 |
| 2474 water_level | 635 | 745.276 | 745.279 | 745.317 | 745.486 | 745.573 | 745.573 |
| 2623 water_temperature | 609 | 3.451 | 3.639 | 5.321 | 8.146 | 8.298 | 8.358 |

**Change statistics, in the form each rule computes it** (pooled across the parameter's series; p99.9
and max per series are in the recorded output — the maximum is reported alongside, § D1 hazards):

| rule / parameter | pairs or triples | p50 | p90 | p99 | p99.9 | max | 2 × p99.9 | 600 s value | **D1 hourly value** |
|---|---|---|---|---|---|---|---|---|---|
| `rate_of_change` discharge (abs) | 2371 | 0.003 | 0.044 | 0.147 | 0.411 | 0.812 | 0.822 | 50 | **50** (copied) |
| `rate_of_change` water_level (abs) | 2371 | 0.001 | 0.004 | 0.022 | 0.076 | 0.095 | 0.152 | 0.5 | **0.5** (copied) |
| `rate_of_change` water_temperature (abs) | 570 | 0.224 | 0.554 | 0.888 | 1.179 | 1.196 | 2.358 | 2.0 | **2.358** (derived) |
| `spike` water_level `max_delta` | 2295 | 0.000 | 0.002 | 0.011 | 0.041 | 0.053 | 0.082 | 1.0 | **1.0** (copied) |
| `spike` discharge `tolerance` (relative) | 2295 | 0.000 | 0.009 | 0.103 | 0.375 | 1.688 | 0.750 | 0.1 | **0.750** (derived) |

⚠️ Discharge's relative spike is dominated by 2252, whose flow is 0.005–0.135 m³/s (relative change on
a near-zero denominator: p99.9 0.916, max 1.688); the other three stay under 0.20. The derived 0.750
therefore comes from one very-low-flow gauge. The two derived values (water_temperature `max_rate`
2.358 and discharge `tolerance` 0.750) are the only ones that exceed their 600 s row; T2 writes them at
no less than these values, marks them provisional, and rounding is T2's call.

**Flat runs** (counts of consecutive hourly readings within the 600 s rule's `tolerance` — 0.001, and 0.01
for water_temperature; recorded for the later `frozen_sensor` plan, D2 adds no row): 2251 discharge
p50 3, p99 13, longest 14; 2251 water_level p50 3, p99 13.7, longest 20; 2252 discharge p50 6, p99 34,
longest 45; 2252 water_level p50 9.5, p99 110, longest **117**; 2319 discharge longest 9, water_level
24; 2474 discharge 4, water_level 8; 2623 water_temperature 2. A run of N readings spans N − 1 hours (2252 water_level's 117 readings = 116 hours). These are
descriptive counts of adjacent-pair differences, not `frozen_sensor`-equivalent durations (that rule
compares against the run's reference value, so a slow drift can pass this definition and still exceed
its tolerance), and the series is un-QCed, so "long" is not a finding about the sensor.

**Datum coverage, fleet-wide (D4).** Water-level groups with measured readings in the last 2 days:
**142; 142 of them have no datum** — every one, including four of the five. T4's extra `QC_UNCHECKED`
load is therefore not confined to these stations: any group whose rules cannot judge a reading at the
edge of a window is affected.

**Pre-deploy share of zero-rule log entries — a diagnostic that does NOT measure the leftover.**
Zero-rule records (`observation_qc_unchecked`), 2026-09-24 11:25 → 2026-09-29 12:55 UTC, the five
stations' entries: **5,847 infer exactly 3600 s (57.7%) and 4,268 infer `None`
(`no_cadence_inferable`)**; 14 infer 3300 s and 12 infer 3900 s. ⚠️ **These entries count runs, not
readings.** Every measured hourly reading of these stations in the last 48 hours is still `QC_UNCHECKED` (all 45
per series; older history is mixed, e.g. 2251 discharge has 475 passed and 116 unchecked), and ingest
picks up `QC_UNCHECKED` rows still inside the 2 h window (or the fetched times) again on every run
(`_QC_PICKUP_STATUSES`), so each 5-minute run re-logs the same unchecked readings: 1,154 runs with an
entry × nine groups ≈ the 10,141 entries. After T2, a reading that receives a terminal verdict is no
longer pending, so the runs between arrivals log nothing for it; a reading no rule can judge, or one
after a missing hour that selects no rule, stays pending and is re-reported (T4 leaves unjudgeable
readings pending on purpose). The 57.7% is therefore not comparable to § (10)'s 95.3% and neither refutes nor confirms it.
What it does show is *when* the entries fall, by minute past the hour: `None` entries 1,604 (:00-:09),
1,486 (:10-:19), 759 (:20-:29), then 137/140/142; 3600 s entries 0, 153, 1,097, then ~1,530 per bucket.

**The per-reading figure that D3 rested on, re-measured from production timestamps (2026-09-29).**
For each of the five stations' 4,111 measured readings in the last 20 days: would a reading exactly
3600 s earlier or later, already stored at or before this reading's own `created_at`, be in the window
on the run that stored it? **3,908 of 4,111 (95.1%) do**, against § (10)'s replay estimate of 95.3% —
**consistent with** the premise D3 was decided on; it does not establish it (limitations below). Readings are stored a median of 25.0 min after their hour
(mean 23.9, p90 25.1) — that is when they reach our database (`created_at`), not when BAFU publishes
them, which is not measured. A late newest reading is consistent with, and by the window definition
sufficient for, runs in the first ~20 minutes seeing one reading and logging `None`; the data do not
exclude other causes (the series also has real gaps, e.g. 30 two-hour gaps at 2251). ⚠️ **This is a neighbour-availability proxy, not a replay.** It checks that a reading ±3600 s away was
already stored; it does not check that the neighbour lies inside the QC window `[now − 2 h, now + 1 h)`
of the run that stored the reading — for these BAFU stations a fixed window, because the widening
by `fetched_times` (`_run_qc_task`) applies only to DHM river `water_level` (a reading stored at 12:25 can match an already-stored 10:00 one
that is outside `[10:25, …)`), does not compute the median gap `infer_time_step` uses (an extra off-grid
timestamp can move it), and `created_at <= created_at` excludes a neighbour stored later in the same
batch. It therefore can overstate (catch-up batches after gaps, a stray older row) and can understate
(same-batch neighbours; an unchecked reading is re-picked while it stays in the window, so eventual
coverage is higher than first-run coverage). The definitive
figure is T5's per-reading share after deploy, with the post-deploy zero-rule records (T5 already
requires their reasons); ⇒ the watchdog's behaviour after T2 is **not predicted from this data**.

**Recorded queries, so the table can be re-run.** ⚠️ As-of instant **2026-09-29 13:00 UTC** (the
extraction and queries ran between ~12:55 and ~14:50; the printed baseline is bounded at that instant
by `< '2026-09-29 13:00+00'` below). The extraction itself had no upper bound in the run recorded here
(the latest measured reading is 2026-09-29 12:00 UTC, so the bound changes nothing for it). The
bounded per-reading and datum queries were re-run and return the printed figures (4,111 / 3,908 /
95.1% / mean 23.9, p50 25.0, p90 25.1 min; 142 of 142). Extraction (mini, `docker compose exec -T postgres
psql -U sapphire -d sapphire`):
```sql
\copy (select s.code, o.parameter, o.source, o.timestamp, o.value, o.qc_status
       from observations o join stations s on s.id = o.station_id
       where s.code in ('2251','2252','2319','2474','2623')
         and o.source in ('measured','manual_import') order by 1,2,3,4) to stdout with csv header
-- share and arrival time (zero-rule entries for the five stations)
select (extract(minute from r.checked_at)::int/10)*10 as minute_bucket,
       count(*) filter (where g->>'inferred_time_step_seconds'='3600.0') as step_3600,
       count(*) filter (where g->>'reason'='no_cadence_inferable') as none_inferable
from pipeline_health r cross join lateral jsonb_array_elements(r.detail->'zero_rule_groups') g
join stations s on s.id::text = g->>'station_id' and s.code in ('2251','2252','2319','2474','2623')
where r.check_type='observation_qc_unchecked'
  and r.checked_at >= '2026-09-24 11:25+00' and r.checked_at < '2026-09-29 13:00+00'
  group by 1 order by 1;
-- per-reading: would each reading find an hourly neighbour on the run that stored it? (last 20 days)
with r as (select s.code, o.parameter, o.timestamp, o.created_at
           from observations o join stations s on s.id=o.station_id
           where s.code in ('2251','2252','2319','2474','2623') and o.source='measured'
             and o.value is not null
             and o.timestamp > '2026-09-09 13:00+00' and o.timestamp < '2026-09-29 13:00+00'),
x as (select a.*, exists (select 1 from r b where b.code=a.code and b.parameter=a.parameter
        and b.timestamp in (a.timestamp - interval '1 hour', a.timestamp + interval '1 hour')
        and b.created_at <= a.created_at) as has_hourly_neighbour from r a)
select count(*), count(*) filter (where has_hourly_neighbour),
       avg(extract(epoch from (created_at - timestamp))/60) as mean_min,
       percentile_cont(0.5) within group (order by extract(epoch from (created_at - timestamp))/60) as p50_min,
       percentile_cont(0.9) within group (order by extract(epoch from (created_at - timestamp))/60) as p90_min
from x;
-- datum coverage
select count(*) filter (where st.water_level_datum_masl is null) as no_datum, count(*) as total
from (select distinct o.station_id from observations o where o.parameter='water_level'
      and o.source='measured'
      and o.timestamp > '2026-09-27 13:00+00' and o.timestamp < '2026-09-29 13:00+00') w
join stations st on st.id = w.station_id;
```
Analysis (Python, on the extracted CSV): keep `source='measured'` with non-null values, sort by
station, parameter, timestamp; `rate_of_change` = `|x − prev|` over consecutive pairs whose gap is
exactly 3600 s; `spike` = `min(|x − prev|, |x − next|)` over triples whose two gaps are both exactly
3600 s (water_level absolute; discharge divided by `|prev|`, `prev = 0` excluded); quantiles by
`numpy.quantile` on the pooled arrays; flat run = consecutive 3600 s pairs with `|x − prev| ≤ tolerance`,
reported as readings in the run, counting runs of at least 2 readings (a single reading is not a run). The 600 s values are `config.toml`'s `time_step_seconds = 600` rows.

### T2 — Add the hourly rules (D1, D2)

**Outcome.** A station delivering hourly selects a non-empty rule list, and its readings carry a
real verdict.

**In.**
- `[[qc_rules.rules]]` rows at `time_step_seconds = 3600` exactly as D2's table lists them: four
  each for `discharge` and `water_level`, three for `water_temperature` — **eleven rows**, no
  `frozen_sensor`, with the values D1's table prescribes.
- The same eleven rows in `docs/spec/config-reference.toml` (the two files must agree on these
  rows; ⛔ existing differences between them are not this plan's) and in
  `_default_swiss_qc_rules()` (`config/qc_rules.py`) — a live fallback when `SAPPHIRE_CONFIG` is
  unset. ⚠️ The three surfaces already differ on existing rows (the fallback carries 28 rules to
  `config.toml`'s 29 — re-counted 2026-09-29 after #332 added three DHM rows, and some daily values differ), and
  `test_qc_rules.py::test_the_swiss_defaults_agree_with_the_shipped_config` pins only the discharge
  ceiling — so nothing today would catch the eleven rows missing from one surface. ⛔ The existing
  differences are not this plan's.
- Existing tests that pin the cadence set by value, updated by value:
  `test_water_level_spike_rules_use_max_delta` (asserts water_level `spike` cadences are exactly
  {600, 86400}) and `TestShippedDischargeCeiling::test_both_toml_surfaces_ship_the_loose_ceiling`
  (asserts discharge ceilings exactly {600: 100000, 86400: 100000}) — both extended with 3600, not
  loosened; and `tests/unit/api/test_api_review_qc_rules.py::TestDeploymentScopeResolution::
  test_same_rule_id_at_two_cadences_returns_both_rows` (asserts the served discharge `range_check`
  cadences are exactly {600, 86400} — found by the full unit run, not by the plan; extended the same way).
- Each new row carries `rule_version = "1.0.0"` (flags report it) and the rule set's `version`
  becomes `1.2.0` in `config.toml` **and** `docs/spec/config-reference.toml` (a test pins the two
  equal); `_default_swiss_qc_rules()` keeps its own version `1.0.0` (a test pins it) (§ Status,
  "Reconciled with `main`"). The rows carry no `network`. The selection
  test passes a network for its station, and a 600 s group's selection is asserted unchanged for both
  a network-less and a `dhm` station.
- ⚠️ `test_qc_rules.py`'s parity test between the shipped config and the reference now also compares
  the thresholds of the network-specific rows (#332). It still skips network-less rows, so it does not
  cover the eleven; the new eleven-row parity test below is still needed.
- 🔴 **Each threshold carries its source in a comment beside it**: a derived one names the T1
  statistic and the rule (2 × P99.9 or the 600 s floor, whichever won); a copied one names the
  600 s row. § (5) is the argument: an unexplained threshold survived months without anyone
  noticing it never fired.
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
- **The eleven 3600 s rows are identical across `config.toml`, `docs/spec/config-reference.toml`
  and `_default_swiss_qc_rules()`** — rule ids, parameters and every threshold — asserted by a new
  test, since the existing parity test covers only the discharge ceiling.
- An hourly group selects exactly its D2 rows — 4 for discharge and water_level, 3 for
  water_temperature — asserted by `rule_id`, not only by count, with a datum set for water_level;
  and a water_level group **without** a datum reports 2 runnable rules (`rate_of_change`,
  `spike`) (D2); **a 600 s group's selection is
  unchanged** (asserted, so the addition cannot perturb the 140 stations that were fine). The test
  loads the shipped `config.toml` rule set, not a hand-built one — a hand-built set proves nothing
  about the file this plan edits.
- **T4's guard on the hourly rows** (T4 is built first, § Status): an hourly water_level group, no
  datum, reading X at −1 h already `QC_UNCHECKED` and a new reading at 0 h, `now` = 0 h + 5 min — the
  window holds X and 0 h, infers 3600 s, selects `rate_of_change` and `spike`, and X is stored
  `QC_UNCHECKED` with `no_check_could_run`, not `QC_PASSED`.
- ⚠️ The selection count includes `gross_outlier`, which runs nothing where no baseline exists
  (§ 12). It is therefore not evidence that a check ran; the range and temporal rules are.
- A synthetic hourly series with a value outside `range_check` comes back `QC_FAILED`; an ordinary
  one comes back `QC_PASSED` — the rules must be able both to fire and not to fire.
- 🔴 **Each threshold is exercised on each side it has**: a derived change threshold does not flag
  a change at T1's 99.9th percentile and does flag one just beyond the chosen value; each
  `range_check` bound is tested at its own side (just inside the minimum passes, just below fails;
  likewise the maximum); each copied `k_sigma` is exercised against a synthetic baseline. ⛔
  *Otherwise a threshold can be mistyped by an order of magnitude and every test still passes.*
- On staging: see T5 — the hourly rows are deployed together with T4's guard, never alone.

### T4 — A reading passes only if some check could judge it (D4, D5)

**Outcome.** No reading is stored `QC_PASSED` unless at least one selected, non-skipped rule
actually evaluated it; otherwise it is `QC_UNCHECKED`, recorded in its own health record, and not
alarmed on.

**In.**
- 🔴 **"Could evaluate" comes from the rule functions themselves**, not from a list kept beside
  them: each `_apply_*` in `services/qc.py` reports *not evaluable* separately from *clean* (an
  internal return value; ⛔ what each rule computes and flags does not change). Every precondition
  the functions already have therefore counts: a missing value (`:115`, `:137`), a missing or
  valueless neighbour (`:137`, `:220-223`), the relative `spike`'s zero reference (`:241-243`), a
  missing baseline (`:270-272`). **`frozen_sensor`, defined per reading:** a reading is judged by it
  only if it lies within a stretch of at least `min_consecutive` distinct instants whose effective
  values (not `None`, not excluded by `exclude_at_or_below`) exist — enough context to tell stuck
  from not stuck; a stretch broken by a `None` or an excluded value restarts.
- **How the result leaves the checker, without touching its contract.** `Stage1QualityChecker`
  gains a method returning both the flags and the set of judged observation ids (a frozen
  dataclass); `check` delegates to it and still returns only the flags. ⇒ The `QualityChecker`
  Protocol (`protocols/stores.py`), onboarding (`services/onboarding.py`) and
  `scripts/dhm_precip/` (`qc_mask.py`, `build_dudh_koshi_handover.py` — no pyright gate
  there) are unchanged. `resolve_selection` is unchanged (it returns `(cadence, count)`,
  `services/qc.py`); the new method receives the same inputs `check` does, so Plan 400's
  look-back cadence reaches it through the same keyword.
- `_run_qc_task` calls the new method; `_aggregate_qc_status` (`flows/ingest_observations.py`)
  becomes per reading: `QC_PASSED` needs the reading in the judged set.
- **Reporting (D5), pending readings only** — a context row already judged in an earlier run is not
  re-reported: a new `PipelineCheckType.OBSERVATION_QC_UNJUDGED` (`types/enums.py`, beside
  `OBSERVATION_QC_UNCHECKED` at `:236`) and a record written like the Plan 318 one
  (`flows/ingest_observations.py`), carrying `reason = "no_check_could_run"`, the groups,
  the reported **observation ids as strings**, and `observations_unjudged`. 🔴 `ObservationId` is a
  `UUID` (`types/ids.py`) and the engine has no JSON serializer for it, so a raw id in the JSONB
  `detail` raises at insert — and the Plan 318 writer catches that and only logs a warning
  (`:311-329`), so the record would be lost silently in production while the fake store, which never
  serialises, passes every test. The watchdog does not probe the new type.
- **How it reaches the flow:** `QcTaskOutcome` (`flows/ingest_observations.py`, the Plan 318
  mechanism) gains the unjudged groups and ids; the flow writes the record, as it does the zero-rule
  one (`:978-983`).
- **Counting rules.** (a) **Zero-rule wins:** a pending reading in a group that resolved zero rules
  is reported only in the zero-rule record, never also as unjudged. (b) Unjudged readings leave
  `counts["unchecked"]` (D5), so every reader of that counter gains a separate `unjudged` count:
  the zero-rule record (`observations_unjudged` goes only in the new record),
  `IngestResult.qc_unchecked` (`:63`, set at `:1044`, logged at `:1062`) beside a new
  `qc_unjudged`, and the `ingest.qc_complete` log (`:967`).
- Text that would otherwise become false: the enum comment at `types/enums.py` ("unlike every
  other member…") now describes two presence-type members; five comments that define
  `QC_UNCHECKED`/`QC_PASSED` by selection alone now also cover a selected rule that could not judge
  the reading — the `_aggregate_qc_status` docstring (`flows/ingest_observations.py`), the
  `QcStatus.QC_UNCHECKED` comment (`types/enums.py`), and in
  `docs/spec/types-and-protocols.md` the `QC_UNCHECKED` comment (`:90-93`), the `QualityChecker`
  comment (`:757`) and the `aggregate_qc_status` docstring (`:526-533`, "ONLY when rules actually
  ran"); `ZeroRuleGroup`'s docstring
  (`flows/ingest_observations.py`) and the `qc.no_rules_selected` log event
  (`flows/ingest_observations.py`) stay for the zero-rule reasons, and the unjudged case gets
  its own log event.
- The three DHM tests the round-5 review found flipping silently get a verdict assertion:
  `test_ingest_observations_dhm.py::…::test_six_hour_recovery_qcs_old_rows_with_preceding_context`
  (its `inside_id` row), `test_qc_exclusive_end_includes_latest_even_beyond_recent_window` and
  `test_repeated_polls_advance_the_level_cursor`.

**Out.** ⛔ Changing what any rule computes or flags. ⛔ The watchdog's alarm logic. ⛔ The
`QualityChecker` Protocol and `resolve_selection`'s return. ⛔ Onboarding's aggregation: it has the
same hole (the first row of a datum-less water-level group) and **no plan owns it** — recorded for
Plan 315's owner, whose scope is onboarding's zero-rule fail-open. ⛔ Supplying datums (Plan 403).

**Pre-change.** A RED test through `_run_qc_task` (asserting its stored statuses and its
`QcTaskOutcome`; the record itself is asserted through the flow, below) with the shipped
`config.toml` **as it is before T2** (T4 is built first — § Status): a 600 s water_level group,
**no datum**, four pending readings at −30, −20, −10 and 0 min after a gap longer than the window
(the first readings after an outage), `now` = 0 min + 5 min. The window holds all four, infers
600 s, and — the datum skip removing `range_check` and `gross_outlier` — selects `rate_of_change`,
`spike` and `frozen_sensor`. The oldest reading, X at −30 min, has no previous reading, so neither
`rate_of_change` nor `spike` can judge it, and four instants are fewer than `frozen_sensor`'s 12.
**Measured on `main` at `bd4daad2`: today X is stored `QC_PASSED` with no flags and no zero-rule
group.** It must fail on X's status.

**Verification.**
- X is stored `QC_UNCHECKED` and appears in the task's `QcTaskOutcome` unjudged ids; the −20, −10
  and 0 min readings get a real verdict from `rate_of_change`. Through `ingest_observations_flow` with a fake
  health store: an `observation_qc_unjudged` record lists X's id with `no_check_could_run`, no
  zero-rule record is written, and `observations_unchecked` does not count X.
- **The record survives serialisation:** its `detail` round-trips through `json.dumps`/`json.loads`
  unchanged, and one integration test writes it through `PgPipelineHealthStore` against real
  Postgres and reads it back — ⛔ the fake store cannot catch a UUID in JSONB.
- **Zero-rule wins:** a pending reading in a zero-rule group appears in the zero-rule record only;
  `IngestResult.qc_unchecked` and `qc_unjudged` add up to the stored `QC_UNCHECKED` rows. ⚠️ The
  zero-rule fixture uses a cadence no row will declare (e.g. 1800 s) — not 3600 s, which T2 adds
  and which would silently turn the fixture into a selecting group later on the same branch.
- **A valueless neighbour:** the −10 min reading exists with `value = None` — the 0 min reading has
  no valued previous reading, no next one and fewer than 12 instants, so it is not judgeable,
  `QC_UNCHECKED`.
- **Relative spike, zero reference:** `spike` with a `tolerance` and a previous value of 0 reports
  *not evaluable*, not *clean* (a unit test on the rule function — for discharge, `range_check`
  still judges the reading).
- **`frozen_sensor` per reading:** a clean, non-flat 600 s datum-less water-level group of at
  least 12 distinct instants — every reading in the stretch is judged and passes; the same group
  broken by a `None` into stretches shorter than 12 — readings in them are not judged by
  `frozen_sensor`; values excluded by `exclude_at_or_below` are not judged by it. Existing
  `frozen_sensor` flags are unchanged (asserted on the existing tests).
- **Context rows are not re-reported:** an already-`QC_PASSED` oldest reading with a judgeable
  pending successor produces no unjudged record.
- The same case **with** a datum: X gets a real verdict from `range_check`.
- The hourly datum-less case is verified in **T2** once its rows exist — ⛔ not a T4 gate.
- **The watchdog is isolated from the new record, through the real probe and a real filter.** The
  existing stub pattern (`tests/unit/ops/test_watchdog_qc_unchecked.py`) returns a fixed
  payload whatever the URL, so it cannot tell a filtered request from an unfiltered one. ⇒ Either
  drive the real health route through Starlette's `TestClient(app)` (an `httpx.Client`) over a fake
  health store holding both records, or a stub that serves from a two-record list filtered by the
  request's `check_type` and `limit=1`. With an older `observation_qc_unchecked` record and a newer
  `observation_qc_unjudged` one ⇒ the alarm stands and no false recovery is posted; unjudged-only ⇒
  silence.
- `check` still returns the same flags as before on every existing QC test; every existing ingest
  and QC test passes; the three DHM tests above now assert the verdict.

### T5 — Prove it on staging (T2 + T4 deployed together)

**Outcome.** The five hourly gauges' readings get real verdicts, and what remains unchecked is the
accepted leftover and nothing else.

**In.** Over the first full day after T2 and T4 are deployed **together**, with each query recorded
here:
- **Numerator / denominator:** the five stations' readings received that day whose stored status at
  the end of the day is `QC_PASSED`, `QC_SUSPECT` or `QC_FAILED` — ⛔ not merely "not
  `QC_UNCHECKED`", which would count `RAW` rows a failed QC run left behind — over all their
  readings received that day; `RAW` reported separately. Reported per parameter; it must be at
  least 90% for discharge and water_temperature. Water level is reported with its datum status
  (T1): without a datum its unjudged readings are the accepted D4 leftover until Plan 403.
- **Every** `observation_qc_unchecked` group for the five stations that day carries reason
  `no_cadence_inferable` or an off-grid cadence (a gap — Plan 400's leftover), never
  `no_rule_declares_it` at 3600 s. Unjudged readings are counted as **distinct observation ids**
  from the `observation_qc_unjudged` records (D5 — records count runs), per parameter.
- ⛔ *Not* "the watchdog stops warning": gap-caused `no_cadence_inferable` records still alarm
  until Plan 400 (§ 10, D3).

**Out.** ⛔ Tuning anything in response — findings go to the owner.

**Pre-change.** The same figures for the day before deploy.

**Verification.** Every figure recorded in this plan beside the query that produced it.

### T3 — Record what happens when a cadence matches no rule (D3)

**Outcome.** The two causes of a zero-rule group, and where each is answered, written where the
next person meets them — not in this plan alone.

**In.** In the observation-ingest map of `docs/touchpoint-maps.md`, beside the Plan 317 pick-up
entry, and under Stage 1 QC (step 2.3) of `docs/architecture-context.md` § Two-stage QC design,
where the rule kinds are described (`docs/standards/wmo.md` defers observation QC to that section):
- **A cadence the rule set does not declare** (this plan's case) is answered by adding rule rows
  under the loose-first posture (D1): range bounds and `k_sigma` copied from an existing cadence,
  change limits the larger of 2 × the 99.9th percentile of the series' measured statistic and the
  shorter cadence's value — not by a factor on another row.
- **A gap inside a known cadence** (§ 10) is answered by Plan 400's inference change; until it
  lands, such checks stay `QC_UNCHECKED` and the watchdog reports them.
- Nearest-rule matching is commissioned by no plan (D3).
- `docs/spec/types-and-protocols.md`: the new checker method and its result type, and the
  `PipelineCheckType` member.
- **A selected rule that could not judge a reading** leaves it `QC_UNCHECKED`, recorded as
  `no_check_could_run` in its own health record and not alarmed on (D4, D5) — never a pass; datums
  (Plan 403) remove most of these for water level.

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
    {"phase": 1, "tasks": ["T4"], "parallel": false},
    {"phase": 2, "tasks": ["T1"], "parallel": false},
    {"phase": 3, "tasks": ["T2"], "parallel": false},
    {"phase": 4, "tasks": ["T3"], "parallel": false},
    {"phase": 5, "tasks": ["T5"], "parallel": false}
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
- **2026-09-26 — round 3: independent Claude review and independent Codex review of `cd6ead60`:
  both NOT READY; all findings folded.** Claude: T1 would have mixed the CAMELS daily history into
  hourly statistics (D1 hazard, T1 population); D1 conflicted with the standing loose-first QC
  posture and did not say the rows are fleet-wide — **escalated; owner chose "loose, informed by
  data"** (D1 table); T2 missed three tests and the live defaults fallback (T2 In); a ~0% "share
  checked" cannot be a baseline (T1). Codex: `RAW` rows would count as checked (T2 numerator);
  `gross_outlier` and range bounds cannot follow a percentile rule (D1 table, T2 verification);
  `config-reference.toml` does not mirror today's rule set (T2 In). Both: `rate_of_change`'s
  statistic is `|x − prev|` (D1, T1). Claude: D3's watchdog sentence scoped to these stations.
- **2026-09-26 — round 4: independent Claude review and independent Codex review of `7e0886d0`:
  both NOT READY, no decision contradicted; all findings folded.** Both: T1's per-check baseline and
  T2's per-reading share are different populations (T1 → diagnostic; T2 keeps the 90% gate and
  explains why the two differ); the defaults parity test pins only the discharge ceiling (T2 In
  corrected; a three-surface equality test added). Codex: § 6 and T3 still stated the superseded
  threshold policy. Claude: § Status stale (four rounds; D1 too); D3/README described the leftover
  as off-grid only (`None` dominates); `spike` needs triples with both gaps 3600 s (T1); Plan 269
  does not add a selection dimension (D1).
- **2026-09-26 — while folding round 4 into Plan 400:** Codex's fail-open (a selected rule that
  cannot evaluate a reading still yields `QC_PASSED`) traced to be reachable under this plan alone
  for datum-less hourly water_level. **Escalated; owner moved the fix into this plan** (D4, T4).
- **2026-09-26 — round 5: independent Claude review and independent Codex review of `ba93dc9d`:
  both NOT READY; all findings folded.** Both: T4's "could evaluate" list missed preconditions the
  rule functions have (valueless neighbour, zero reference) → derived from the functions themselves.
  Codex: report only pending readings, not context rows; the staging gate rejected the outcome T4
  requires; T2's staging day ran before T4's guard existed (→ T5, deployed together); the
  post-deploy per-check share had no denominator (→ pre-deploy only). Claude: Swiss river stations
  have **no water-level datum**, so T4 reaches every water-level group and hourly water level keeps
  an unjudged leftover — **escalated; owner: onboard datums from the hydrological yearbook (Plan
  403) and record-don't-alarm meanwhile (D5)**; the alert text and log event would lie for the new
  case (→ separate record type); three DHM tests flip silently (pinned); T4's function must take
  the selection, not infer its own cadence (Plan 400's third consumer); onboarding's same hole has
  no owner (recorded for 315); the 600 s fixture needs fewer than 12 instants.
- **2026-09-26 — round 6: independent Claude review and independent Codex review of `a2f32b5f`:
  both NOT READY for this plan (both READY for 400); all findings folded, no decision needed.**
  Both: T4 had no path for "not evaluable" out of the checker and misdescribed `resolve_selection`
  (→ a new checker method; Protocol, `check` and `resolve_selection` unchanged); `frozen_sensor`
  needed a per-reading definition and clean-run tests; fake watchdog probes cannot prove the record
  isolation (→ real probe, stub transport). Claude: unjudged readings would inflate the zero-rule
  alarm's count (→ `observations_unjudged`); records count runs, not readings (→ ids in the record,
  distinct-id measurements); DHM gauge-zero stations are datum-less and no plan owns them
  (recorded); enum comment, spec and file names for citations; rollback must delete the new rows.
- **2026-09-26 — round 7: Codex READY; Claude NOT READY (one MEDIUM); all findings folded, no
  decision needed.** Claude: observation ids are UUIDs and would make the JSONB insert fail, which
  the Plan 318 writer swallows — the record would vanish in production while fakes pass (→ ids as
  strings; a JSON round-trip and a real-Postgres test); the RED test asked `_run_qc_task` for a
  record only the flow writes (→ `QcTaskOutcome` field, record asserted through the flow);
  zero-rule wins over unjudged, and every reader of the unchecked counter gains an unjudged count;
  the watchdog-isolation stub must actually filter; `blocks` gains 403.
- **2026-09-26 — round 8: READY from both reviewers**; Claude's two LOW findings folded: § Status's
  round tally, and four comments that define `QC_UNCHECKED`/`QC_PASSED` by selection alone (T4).
- **2026-09-26 — round 9: READY from both reviewers**; Claude's two LOW findings folded: a fifth
  comment defining the statuses by selection alone (spec `aggregate_qc_status`, T4), and D4's
  "every water-level reading" → "most" (403 refuses some stations and excludes lakes and DHM).
- **2026-09-26 — round 10: READY from both reviewers**; one LOW folded (T4's "four comments" now
  lists five).
- **2026-09-26 — round 11: READY from both reviewers, no findings on this plan** (the fold-check of
  round 10's LOW). **Set READY by the orchestrator** on the owner's confirmation; Codex's readiness
  advice: READY, no blockers — staging's absence schedules T1, it does not hold READY.
- **2026-09-26 — amendment after READY (owner):** staging is off the network for about two days, so
  **T4 is built first**. T4's RED test moves to a 10-minute datum-less water-level case that exists on
  today's rule set (measured failing on `main` at `bd4daad2`); the hourly case becomes a T2
  verification. Phase graph: T4 → T1 → T2 → T3 → T5. One branch, one PR, so T2 and T4 still deploy
  together. **Amendment reviewed before T4 was built: READY from both reviewers** (Codex no findings;
  Claude six LOW wording findings, folded on the implementation branch with the T4 code).
