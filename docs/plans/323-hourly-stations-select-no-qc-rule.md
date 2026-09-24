---
status: DRAFT
created: 2026-09-24
plan: 323
title: Five Swiss stations report hourly and select no QC rule at all
scope: Give the observation QC rule set a 3600 s cadence for the parameters the hourly BAFU stations deliver, so those stations are actually checked instead of selecting zero rules. NOT the DHM/Nepal rule rows (303), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT per-station overrides (269), NOT re-QC of the rows already stored (owner, 2026-09-24 — history is left), NOT the pick-up set (317, merged), NOT the consumer policy (316, merged).
depends_on: []
blocks: []
related: [264, 269, 272, 303, 313, 316, 317, 318]
open_decisions: [D1, D2, D3]
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

### D1 — the hourly threshold values. **OPEN — the owner's, as hydrologist.**

§ (6) shows no interpolation rule exists. Three ways to settle it:

| | option | cost |
|---|---|---|
| (a) | The owner sets each hourly value directly. | Correct by construction. Needs their time on ~14 numbers. |
| (b) | Derive from the 600 s row by a stated factor per rule, owner approving the factors. | Fewer decisions, but § (6) says the existing pairs contradict any single factor. |
| (c) | Copy the 600 s values unchanged. | ⛔ Rejected in the drafting: it makes `rate_of_change` and `spike` **6× stricter** in physical terms (the same delta over 6× the interval) and would flag ordinary hourly variation as suspect. § (8) makes `frozen_sensor` worse still. |

⚠️ Whatever is chosen, **the plan records the basis for each number**, because § (5) shows an
unexplained threshold survives for months without anyone noticing it never fired.

### D2 — which rules get an hourly row. **OPEN.**

The 600 s set has 5 rules for discharge and water_level and 4 for water_temperature; the 86400 s set
drops `frozen_sensor` entirely. So precedent exists for a cadence carrying a *subset*. The question
is whether hourly gets the full 600 s set (with `frozen_sensor`'s count re-expressed per § 8) or the
86400 s shape.

### D3 — the next unseen cadence. **OPEN — and deliberately raised now.**

This plan adds one cadence because one appeared. The sixth station at some third cadence reopens it.
The general alternative — matching a station's cadence to the *nearest* declared rule rather than an
exact equal — is Plan 264's territory and changes selection for all 148 stations, so it is not in
scope here. **What is in scope is deciding whether we keep adding rows or commission that work**,
and recording the answer so the next occurrence is not rediscovered from a Slack alert.

⛔ **Not a decision: the stored history.** The owner closed it on 2026-09-24 — *leave it* — on the
same reasoning as Plan 315 D3: real customer deployments are onboarded fresh, so a mixed-era corpus
exists only on this development host. § (5)'s 1,277 rows stay as they are.

## Tasks

### T1 — Add the hourly rules (D1, D2)

**Outcome.** A station delivering hourly selects a non-empty rule list, and its readings carry a
real verdict.

**In.**
- The `[[qc_rules.rules]]` rows D1 and D2 settle, at `time_step_seconds = 3600`, each with the basis
  for its value recorded.
- 🔴 **A test that an hourly group selects a non-empty rule list**, by `resolve_selection` —
  the operation Plan 272 built for exactly this question. ⛔ *Not "the config parses", which is
  not this defect.*
- `frozen_sensor`'s `min_consecutive` re-expressed for the new cadence if D2 includes it (§ 8).

**Out.** ⛔ Any change to the 600 s or 86400 s rows — this adds, it does not retune. ⛔ Any change
to how selection MATCHES (that is D3/264). ⛔ Re-QC of stored rows. ⛔ DHM/Nepal rows (303).

**Pre-change.** A RED test asserting the DESIRED behaviour: **a group whose inferred cadence is
3600 s resolves a non-empty rule list** — which fails today because nothing declares 3600 s.
⚠️ It must fail on the empty list, not on a missing config key.

**Verification.**
- An hourly group selects the D2 rule set; a 600 s group's selection is **unchanged** (asserted, so
  the addition cannot perturb the 140 stations that were fine).
- A synthetic hourly series with a value outside `range_check` comes back `QC_FAILED`; an ordinary
  one comes back `QC_PASSED` — i.e. the rules can both fire and not fire.
- ⭐ **On staging after deploy: the five stations' next readings are no longer `QC_UNCHECKED`**, and
  the Plan 318 watchdog stops warning for them. This is the only verification that proves the live
  defect closed; the unit tests prove the mechanism.

### T2 — Record what we will do at the next unseen cadence (D3)

**Outcome.** D3's answer written where the next person meets it, not in this plan alone.

**In.** D3's choice, in `docs/standards/wmo.md` or the QC section of the conventions — wherever the
rule set's shape is described — plus a line in the touchpoint map for observation ingest.

**Out.** ⛔ Implementing nearest-cadence matching. That is 264.

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
    {"phase": 2, "tasks": ["T2"], "parallel": false}
  ]
}
```
