---
status: DRAFT
created: 2026-09-24
revised: 2026-09-24
plan: 323
title: Five Swiss stations report hourly and select no QC rule at all
scope: Give the observation QC rule set a 3600 s cadence for the parameters the hourly BAFU stations deliver, so those stations are actually checked instead of selecting zero rules. NOT the DHM/Nepal rule rows (303), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT per-station overrides (269), NOT re-QC of the rows already stored (owner, 2026-09-24 — history is left), NOT the pick-up set (317, merged), NOT the consumer policy (316, merged).
depends_on: []
blocks: []
related: [264, 269, 272, 303, 313, 316, 317, 318]
open_decisions: []
source: 2026-09-24 — the owner reported Slack warnings that BAFU observations were unchecked. Every claim in § What is measured was measured on the staging host that day against v0.1.965 and against `main` at `3b515f6b`; each says how.
---

# Plan 323 — five Swiss stations report hourly and select no QC rule at all

⚠️ **Plan number PROVISIONAL until the owner grants it.** 320, 321 and 322 were taken by a
concurrent session while this was being written.

## Status

**DRAFT.** ⛔ No implementation until an independent review is complete and the orchestrator sets
READY. D1 is a hydrology decision and is the owner's; it cannot be inferred from the repo.

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
- **Every rule needs its own statistic.** `range_check` wants the value distribution; `rate_of_change`
  and `spike` want the distribution of change BETWEEN consecutive readings; `frozen_sensor` wants the
  longest run of near-identical values. ⛔ *One summary table of values cannot answer all four.*

⚠️ `gross_outlier` is the exception: `k_sigma` is a multiplier on a climatological baseline, not a
value in the series' units, and it is identical at 600 s and 86400 s (§ 6). It is cadence-independent
and carries across unchanged — T1 need not measure for it.

### D2 — which rules get an hourly row. **⚖️ CLOSED — owner, 2026-09-24: all five.**

Hourly data is frequent enough for every check to be meaningful, `frozen_sensor` included. ⛔ *The
86400 s set drops it; hourly does not follow that precedent.*

🔴 **`min_consecutive` must be re-expressed, not copied** (§ 8): it counts READINGS, so the 600 s
value of 12 means 2 h there and would mean 12 h at 3600 s. T1 measures the longest flat run in
hours; T2 converts to a reading count at 3600 s.

### D3 — the next unseen cadence. **⚖️ CLOSED — owner, 2026-09-24: write the answer down, act later.**

The general fix — matching a station to the NEAREST declared rule instead of requiring exact
equality — is the right answer and is Plan 264's territory, since it changes selection for all 148
stations. It is **not** commissioned by this plan. What this plan owes is the written answer, so the
next person meeting a third cadence finds it instead of rediscovering it from an alert (T3).

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
  99th and 99.9th percentiles).
- Per series, the distribution of **|change between consecutive readings|** at the hourly cadence —
  same percentiles. This is what `rate_of_change` and `spike` are judged against, and it is a
  different statistic from the value distribution.
- Per series, the **longest run of near-identical values, in hours**, at the `tolerance` the 600 s
  rule already uses (0.001 for discharge and water_level), so `frozen_sensor`'s count can be set
  from observed behaviour rather than guessed.
- ⚠️ **The span each series actually has, stated per series.** A series with only weeks of history
  yields provisional bounds and T2 must mark them so.
- The analysis run as a heredoc against the staging database per the repo convention, with the query
  recorded in the plan so it can be re-run when the drought ends.

**Out.** ⛔ Changing any rule — T1 only measures. ⛔ Excluding outliers by judgement: the point is
to see them. ⛔ Any claim about *why* these five are hourly.

**Pre-change.** N/A — measurement.

**Verification.** Every number T2 writes traces to a cell in T1's table. ⭐ **A reviewer can re-run
the recorded query and get the same table** — otherwise the thresholds rest on a measurement nobody
can reproduce.

🔴 **Blocked while the staging host is off the network** (2026-09-24). T1 cannot be done from the
repo; it needs the live store.

### T2 — Add the hourly rules (D1, D2)

**Outcome.** A station delivering hourly selects a non-empty rule list, and its readings carry a
real verdict.

**In.**
- Five `[[qc_rules.rules]]` rows at `time_step_seconds = 3600` for `discharge` and for
  `water_level`, and the `water_temperature` set for Oberwald — D2 closed this as the full 600 s
  shape, `frozen_sensor` included.
- 🔴 **Each threshold carries the T1 statistic it came from**, in a comment beside it. § (5) is the
  argument: an unexplained threshold survived months without anyone noticing it never fired.
- `frozen_sensor.min_consecutive` expressed as a **reading count at 3600 s** derived from T1's
  longest-flat-run-in-hours (D2), never copied from the 600 s row.
- `gross_outlier.k_sigma` carried across unchanged, per D1's exception.
- 🔴 **A test that an hourly group selects a non-empty rule list**, via `resolve_selection` — the
  operation Plan 272 built for exactly this question. ⛔ *Not "the config parses", which is not this
  defect.*

**Out.** ⛔ Any change to the 600 s or 86400 s rows — this adds, it does not retune. ⛔ Any change
to how selection MATCHES (D3/264). ⛔ Re-QC of stored rows. ⛔ DHM/Nepal rows (303).

**Pre-change.** A RED test asserting the DESIRED behaviour: **a group whose inferred cadence is
3600 s resolves a non-empty rule list** — which fails today because nothing declares 3600 s.
⚠️ It must fail on the empty list, not on a missing config key.

**Verification.**
- An hourly group selects all five rules; **a 600 s group's selection is unchanged** (asserted, so
  the addition cannot perturb the 140 stations that were fine).
- A synthetic hourly series with a value outside `range_check` comes back `QC_FAILED`; an ordinary
  one comes back `QC_PASSED` — the rules must be able both to fire and not to fire.
- 🔴 **Each threshold is exercised at least once against T1's percentiles**: a value at the 99.9th
  percentile does NOT flag, one beyond the chosen bound does. ⛔ *Otherwise a threshold can be
  mistyped by an order of magnitude and every test still passes.*
- ⭐ **On staging after deploy: the five stations' next readings are no longer `QC_UNCHECKED`**, and
  the Plan 318 watchdog stops warning for them. The unit tests prove the mechanism; only this proves
  the live defect closed.

### T3 — Record what happens at the next unseen cadence (D3)

**Outcome.** D3's answer written where the next person meets it, not in this plan alone.

**In.** D3's choice — nearest-rule matching is the general fix, it is Plan 264's, and it is not
commissioned here — stated in the QC section of the conventions or `docs/standards/wmo.md`,
wherever the rule set's shape is described, plus a line in the observation-ingest touchpoint map.

**Out.** ⛔ Implementing nearest-cadence matching.

**Pre-change.** N/A — documentation.

**Verification.** A reader meeting a third cadence finds the answer without re-deriving it.

## Explicitly out of scope

- **`rate_of_change`'s arithmetic** — Plan 313. ⚠️ **But note the interaction, because it changes
  what D1's numbers MEAN.** Today `max_rate` is compared against the raw difference between
  consecutive readings, with no division by elapsed time; 313 would make it a true rate. If 313
  lands after this plan, every `max_rate` set here must be revisited. ⇒ **313 must state that**, or
  this plan's numbers will silently change meaning. Sequencing is the orchestrator's call; the
  dependency is recorded here either way.
- **Nearest-cadence or most-specific-wins matching** — Plan 264.
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
