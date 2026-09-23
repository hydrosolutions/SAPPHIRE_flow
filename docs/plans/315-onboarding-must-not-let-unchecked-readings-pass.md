---
status: DRAFT
created: 2026-09-23
plan: 315
title: The onboarding loader must not let unchecked readings pass
scope: The observation-onboarding QC path's fail-open (`services/onboarding.py:772-841`) — re-measuring whether a zero-rule group can still occur there once the QC ladder has landed, and closing the fail-open together with the three in-run consumers that read its output. NOT the scheduled ingest path (Plan 272, shipped), NOT the network dimension of rule selection (264), NOT per-station threshold overrides (269), NOT `rate_of_change`'s arithmetic (313), NOT new `precipitation`/`temperature` rule rows (303), NOT the rollout controls (314), NOT re-QC over already-stored history unless D3 opens it.
depends_on: []
blocks: []
related: [264, 269, 272, 303, 313, 314]
open_decisions: [D1, D2, D3]
source: 2026-09-23 — owner, on reviewing PR #297: *"for now it's ok, we'll have to have a plan that follows the full QC implementation to check if this still happens. Once QC stands, we should not allow the loader to let unchecked readings pass."* Every claim below was measured on `feat/plan-272-qc-selection` at `54673d79` (v0.1.960), i.e. against the tree as it will be once #297 merges — not against today's `main`, which does not yet contain `QC_UNCHECKED`.
---

# Plan 315 — the onboarding loader must not let unchecked readings pass

⚠️ **The plan number 315 is PROVISIONAL until the owner grants it.** 302–305 were granted in
prose and never written, so the sequence is not a reliable allocator (Plan 314 records the same
caveat).

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY.

⏸️ **This plan is deliberately NOT next.** Its whole premise is that the QC ladder has landed and
the interim posture has expired; running it before that measures a rule set that is still
changing shape. D1 fixes the trigger. Until D1 closes, this is a placeholder with a measurement
task attached, not work to schedule.

## Why this plan exists

The same gap has now been descoped by two separate plans, each for a good local reason, and
neither left an owner behind:

- **Plan 272 D5** shipped `QC_UNCHECKED` on the *scheduled ingest* path and stated the onboarding
  exclusion in terms: `aggregate_qc_status([])` there still yields `QC_PASSED`. The reason given
  was that the marking lives in the flow rather than in `check`, and that onboarding's window is
  historical and wide, so the daily rules resolve. An independent Codex review of the
  implementation filed that exclusion as a HIGH; it was refused, with the reason written into
  `services/onboarding.py:811-817`, and Codex accepted the refusal as a policy question for the
  owner.
- **Plan 269 § What this deliberately does not do** descoped the same path for per-station
  thresholds, and ends: *"If that becomes load-bearing, it is a follow-on that should be designed
  against the onboarding path's own gate, not bolted onto this one."*

**This plan is that follow-on.** It is also the thing that makes `docs/v1-scope.md` § QC posture
point 4 true rather than aspirational — that section already commits to *"Once QC is in place and
fine-tuned: unchecked data must NOT enter forecasting"*, and nothing currently delivers it on the
onboarding side.

## What is measured

Measured on `feat/plan-272-qc-selection` at `54673d79` unless stated otherwise.

**1. The fail-open, exactly.** `services/onboarding.py:772-841` is Step 5. It fetches
`qc_status=RAW` rows only (`:780-782`), calls `Stage1QualityChecker.check` with `overrides=[]`
(`:796-801`), and feeds the result to `aggregate_qc_status` (`:819`, `types/domain.py:104-106`),
where **an empty flag list returns `QC_PASSED`**. It does not call `resolve_selection`, so it
cannot tell "every rule passed" from "no rule ran". The comment at `:811-817` says so.

**2. Onboarding QCs only the station's forecast target.** `station_target.get(station_id)`
(`:775`) — a station with no forecast target is skipped entirely (`:776-778`). In the deployed
config the targets are `discharge` and `water_level`, so `precipitation` and `temperature` never
reach this path. ⚠️ **This also means the six DHM gauges are skipped today**, because Plan 268
leaves their `forecast_targets` unset — Plan 269 measured the same thing.

**3. Only two cadences are declared anywhere.** Parsed from `config.toml` §`qc_rules.rules`
(26 rules): every rule declares either **600 s or 86400 s**, and no other value appears.

| parameter | 600 s | 86400 s |
|---|---|---|
| `discharge` | frozen_sensor, gross_outlier, range_check, rate_of_change, spike | gross_outlier, range_check, rate_of_change, spike |
| `water_level` | frozen_sensor, gross_outlier, range_check, rate_of_change, spike | gross_outlier, range_check, rate_of_change, spike |
| `precipitation` | — | gross_outlier, range_check |
| `temperature` | — | gross_outlier, range_check |
| `water_temperature` | frozen_sensor, gross_outlier, range_check, rate_of_change | — |

⇒ **A group whose inferred median is neither 600 s nor 86400 s selects zero rules, for every
parameter.** An hourly series is the obvious case and it is not hypothetical — it is what Plan
303 exists to declare rules for.

**4. The datum seam cannot empty the set on its own — checked, because it was the first
suspicion.** `OBS_DATUM_DEPENDENT_RULES = {range_check, gross_outlier}`
(`services/qc_datum.py:14`) and the skip applies only to `water_level`
(`:29-32`). Against the table above, a datum-less water-level station still keeps
`rate_of_change`, `frozen_sensor`, `spike` at 600 s and `rate_of_change`, `spike` at 86400 s.
⇒ **Never zero on the deployed rule set.** It reaches zero only where a rule set is thin enough
that every matching rule is datum-dependent — which is a shape Plan **264** can create, since it
adds a network dimension to selection. *(This is the one point where the deployed-config
reassurance does not carry forward, and it is why D1's trigger is after 264, not before.)*

**5. Onboarding infers ONE cadence for the whole history.** `Stage1QualityChecker.check` groups
by `(station_id, parameter)` and infers a single step per group (`services/qc.py:304-307`), and
Step 5 hands it the entire `start_utc → end_utc` window at once. ⚠️ **So "the window is wide"
cuts both ways.** It is what makes a homogeneous series resolve — and it is also what makes a
*mixed-cadence* history (daily before some date, 10-minute after) collapse to one median that
may match neither. D5's argument assumes homogeneity and does not say so.

**6. Three consumers read this path's output, in the same run, immediately after it.**

| site | what it computes | filter |
|---|---|---|
| `onboarding.py:258` | per-station skill scores + diagrams | `qc_status=QC_PASSED` |
| `onboarding.py:855` (Step 5b, `:843`) | climatological baselines | `qc_status=QC_PASSED` |
| `onboarding.py:902` (Step 5c, `:890`) | flow regimes | `qc_status=QC_PASSED` |

🔴 **This is the whole risk, and it is a loop, not a leak.** Baselines feed `gross_outlier`, which
the *ingest* path then runs (`_apply_gross_outlier`, `services/qc.py:253`, dispatched at
`:337`). So marking onboarding rows `QC_UNCHECKED` removes them from the baseline population, which weakens or empties a baseline, which degrades a
live QC rule on a path this plan does not touch. Plan 269 found the same loop from the threshold
side and called it *"a real limitation, not a rounding error."* ⇒ **Closing the fail-open without
deciding D2 at the same time is the way to darken the fleet.**

**7. Onboarding cannot overwrite an ingest verdict.** It fetches `RAW` only (`:780-782`).
Verified in Plan 269 round 5; re-checked here. ⇒ The blast radius of this plan is backfilled
history, not live verdicts.

**8. 🔴 The `-norules` sentinel that Plan 272 T0 specified did NOT ship.** T0's In-list requires a
sentinel `qc_rule_version` (`"1.0-norules"` and the datum variants) when the resolved rule set is
empty (`272:715-717`). Measured at `54673d79`: `obs_qc_rule_version`
(`services/qc_datum.py:23-27`) returns `"1.0"`, `"1.1-datum"` or `"1.1-datum-skip"` and has no
zero-rule variant; the flow writes that value unconditionally
(`flows/ingest_observations.py:378,388`). Nothing in `src/` contains the string `norules`.
**Two consequences, both outside this plan's scope but owed to whoever owns them:**
- With `QC_UNCHECKED` stored, zero-rule rows *are* identifiable by `qc_status`, so the sentinel is
  arguably superseded rather than missing. **Someone has to say which.**
- ⛔ **Plan 314's E1(a) rests on it**: *"The rewritten rows keep T0's `-norules` sentinel in
  `qc_rule_version`, so they stay identifiable afterwards — the revert does not re-hide the
  defect."* Against the shipped code that sentence is **false** — after E1(a)'s `UPDATE` the rows
  are indistinguishable again. 314 is suspended, so this is not urgent; it is recorded so the
  suspension does not preserve a wrong sentence.

## Owner decisions

### D1 — What is the trigger? "Once QC stands" has to name something.

A plan that starts when a phrase becomes true never starts. The trigger must be a landed set.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Trigger on a named set: 264 (network selection), 269 (per-station thresholds), 303 (precipitation/temperature rules), 313 (`rate_of_change`) all merged and deployed.** Those four are what change the *shape* of the rule set, which is what determines whether a zero-rule group is still reachable. | Concrete and checkable. ⚠️ Three of the four are DRAFT and 269 is BLOCKED, so this is not soon — which is the correct answer, not a problem with the option. |
| **(b)** | Trigger on the owner declaring the interim posture over (`docs/v1-scope.md` § QC posture point 4 flipping from "now" to "once"). | Matches the source decision's own wording; but it is a judgement call with no checklist, and it can be declared while a rule-set-shaping plan is still in flight. |
| **(c)** | Run T1's measurement **now**, keep T2/T3 behind (a). | ⭐ Cheapest useful thing: the measurement is read-only and its answer may be "zero stations affected", which would shrink this plan to a guard and a test. ⚠️ But the answer goes stale the moment 264 or 303 lands, so it must be re-run at the trigger regardless. |

**Recommendation: (a) for the plan, (c) for T1 alone** — measure early because it is free and may
descope the rest, then measure again at the trigger because the first answer expires.

### D2 — 🔴 When onboarding writes `QC_UNCHECKED`, what do its three consumers do?

Per § What is measured (6), skill, baselines and flow regimes all filter `QC_PASSED` in the same
run. This is the decision that determines whether closing the fail-open darkens stations.

| | option | cost |
|---|---|---|
| **(a)** | All three **exclude** `QC_UNCHECKED` — the honest reading. | Correct by the status's own meaning. ⛔ A station whose history is largely unchecked loses its baseline, and `gross_outlier` on the *live ingest* path then runs against a censored or absent baseline. Silent, and on a path this plan does not touch. |
| **(b)** ⭐ | **Exclude, and make the shortfall loud**: onboarding HOLDS the station (does not promote it) and records why, when the checked population falls below the threshold each consumer already needs to produce a meaningful result. | Same correctness as (a), and the failure is visible at onboarding time instead of surfacing later as a weak QC rule. ⚠️ Needs a stated minimum per consumer — baselines and flow regimes already have implicit ones; they must be made explicit, not invented. |
| **(c)** | All three **accept** `QC_UNCHECKED` alongside `QC_PASSED`. | Preserves today's numbers exactly. ⛔ Then the status is cosmetic on this path and the plan delivers nothing the owner asked for. |
| **(d)** | Split: baselines and flow regimes accept (they are statistical and tolerate noise), skill excludes (it is a published number). | Defensible, but it makes `gross_outlier` rest on a population nobody checked — the loop in (6) — and the asymmetry would need its own argument. |

**Recommendation: (b).** ⚠️ Whichever is chosen, it must be decided **in the same change** as the
fail-open, never after it — (6) is why.

### D3 — What happens to history already stamped `QC_PASSED` by the old loader?

Plan 272 D3 settled "no backfill" for the ingest path. The same question recurs here and the
answer is not automatically the same, because onboarding's whole population *is* history.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Leave it, and make the boundary queryable** — record the onboarding-run identity or timestamp from which the loader stopped failing open. | No data rewrite, and a later reader can tell which era a row's `qc_passed` came from. ⚠️ Needs a place to record it; `observations` carries no QC timestamp (Plan 314 § What is measured (3) measured this). |
| **(b)** | **Re-run onboarding QC over the stored history.** | The only option that makes the stored corpus mean one thing. ⛔ Expensive, and `Step 5` fetches `RAW` only, so it would not even re-read rows already stamped — re-QC needs a status reset that does not exist (272 § D5 records the same missing operation). |
| **(c)** | Do nothing and say nothing. | ⛔ Rejected on sight: it reproduces exactly the ambiguity this whole thread exists to remove, one layer down. |

**Recommendation: (a).** ⚠️ **Not (b) on a whim** — it needs an operation (`qc_status → RAW`) that
this codebase does not have, and building that is a plan of its own.

## Tasks

### T1 — Measure whether a zero-rule group is still reachable at onboarding

**Outcome.** A count, per station and parameter, of onboarding groups that resolve zero rules —
and therefore a factual answer to "does this still happen", which may descope T2 and T3 entirely.

**In.**
- A read-only census over the deployed observation history, using `resolve_selection`
  (`services/qc.py:70-100`) with the same rows and the same skip set Step 5 would pass
  (`obs_skipped_rules(parameter, datum)`), per `(station_id, forecast target)`.
- **Report the inferred cadence alongside the count**, because the *reason* separates the two
  regimes and they have different fixes: a median outside `{600, 86400}` is § (3), a group of
  fewer than two distinct timestamps is the cold case.
- **The mixed-cadence probe from § (5)**: for each station, the median over the whole window
  against the median over the most recent N months. Where they differ, the wide window is
  *causing* the mismatch rather than curing it.
- Run it on the staging host against the live corpus, not on a fixture.

**Out.** ⛔ Any write. ⛔ Any change to `services/onboarding.py`.

**Pre-change.** N/A — a measurement.

**Verification.** The census output, recorded in this plan, with the date and the commit it ran
against. ⚠️ **It expires** — D1(c): re-run at the trigger, and say so next to the number.

### T2 — Close the fail-open, and decide the consumers in the same change

**Outcome.** A group that resolves zero rules at onboarding is stored `QC_UNCHECKED`, and the
three consumers behave as D2 decides — neither silently nor by accident.

**In.**
- `services/onboarding.py` Step 5 calls `resolve_selection` with the same rows and skip set it
  passes to `check`, and stores `QC_UNCHECKED` for a zero-rule group — the shape
  `flows/ingest_observations.py:358-388` already uses, so this is symmetry with the ingest path,
  not a second mechanism. ⚠️ **Do not re-invent it**: if the two paths need the same logic, the
  shared piece moves to one place rather than being copied. A hand-copied twin is exactly what
  Plan 272 had to delete from `scripts/dhm_precip/qc_mask.py`.
- **D2's consumer policy at all three sites** (`:258`, `:855`, `:902`), each touched
  deliberately and each named in the diff — ⛔ *not a category ("the onboarding consumers"); Plan
  272 T4 records why a category cannot be checked off against the code.*
- The onboarding counters and the returned result gain the unchecked count, as
  `IngestResult.qc_unchecked` did.
- **Delete the refusal comment at `services/onboarding.py:811-817`** in the same change. A
  comment explaining why a gap is deliberate, left standing after the gap is closed, is a lie
  with a citation.

**Out.** ⛔ The scheduled ingest path. ⛔ Re-QC over stored history (D3). ⛔ Any threshold value.
⛔ `calculated_station_onboarding.py` — measured: it contains no `check` call and no
`aggregate_qc_status` call at `54673d79`, so it has no fail-open of its own to close.

**Pre-change.** A RED test proving the current behaviour: an onboarding run over a group whose
cadence matches no rule stores `QC_PASSED` today. ⚠️ **It must fail because the status is wrong,
not because a symbol is missing** — the standard Plan 272 § the same standard sets.

**Verification.**
- A zero-rule onboarding group stores `QC_UNCHECKED`; a group that resolves rules and passes them
  still stores `QC_PASSED`. The two are asserted separately.
- Each of the three consumers is asserted at its own site under D2's policy.
- Under D2(b): a station whose checked population falls below the stated minimum is HELD, with
  the reason recorded — and a station above it is promoted exactly as today.

### T3 — Record the boundary, and the documents that still describe the old behaviour

**Outcome.** D3's boundary is queryable, and no document still says onboarding fails open.

**In.**
- D3's chosen record of the boundary.
- `docs/v1-scope.md` § QC posture point 4 — the "once QC is fine-tuned" half stops being a
  commitment and starts being a description.
- ⚠️ **A sweep by VALUE, not by site**: every place that states the onboarding exclusion,
  including Plan 272 D5 and its § the second fail-open call site, and Plan 269 § What this
  deliberately does not do. Those are the two that *argued* the exclusion; they must record that
  it was closed and by what. *(Plan 272's own § Out-of-scope entry for re-QC stays true under
  D3(a) and must not be edited to imply otherwise.)*
- **Also fix, because T1's census hits it immediately:** `docs/v1-scope.md` § QC posture has a
  broken blockquote — the owner's quote is split mid-sentence by the `enable_observation_alerts`
  warning, leaving *"— and only from that point do the data-retention guarantees have to hold as
  planned"* orphaned after it. Restore the quote, then place the warning after it.

**Out.** ⛔ Any code change.

**Pre-change.** N/A.

**Verification.** A grep for the exclusion's wording returns only the records that say it was
closed; the blockquote renders as one quotation.

## Explicitly out of scope

- **The scheduled ingest path** — Plan 272, shipped in PR #297. This plan makes onboarding match
  it; it does not revisit it.
- **The network dimension of selection** (264), **per-station thresholds** (269),
  **`precipitation`/`temperature` rule rows** (303) and **`rate_of_change`'s arithmetic** (313).
  This plan runs *after* them by D1 and inherits whatever shape they leave.
- **The rollout controls** — Plan 314, suspended. ⚠️ Its E1(a) sentence is falsified by § What is
  measured (8); that is recorded, not fixed here.
- **A `qc_status → RAW` reset operation** — needed by D3(b), does not exist, and would be its own
  plan. Plan 272 D5 records the same gap from the other side.
- **The six DHM gauges' onboarding** — they have no forecast target, so Step 5 skips them
  entirely (§ What is measured (2)). Giving them one is Plan 268's.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
