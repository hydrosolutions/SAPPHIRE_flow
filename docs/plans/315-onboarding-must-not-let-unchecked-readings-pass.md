---
status: DRAFT
created: 2026-09-23
revised: 2026-09-23
plan: 315
title: The onboarding loader must not let unchecked readings pass
scope: The observation-onboarding QC path's fail-open (`services/onboarding.py:772-841`) — re-measuring whether a zero-rule group can still occur there once the QC ladder has landed, and closing the fail-open together with the three in-run consumers that read its output. NOT the scheduled ingest path (Plan 272, shipped), NOT the network dimension of rule selection (264), NOT per-station threshold overrides (269), NOT `rate_of_change`'s arithmetic (313), NOT new `precipitation`/`temperature` rule rows (303), NOT the rollout controls (314), NOT re-QC over already-stored history (closed by D3).
depends_on: []
blocks: []
related: [264, 269, 272, 303, 313, 314]
open_decisions: []
reviews:
  - "codex 2026-09-23 r1 — NOT READY, 1 HIGH + 6 MEDIUM, all against the evidence; every one verified and folded"
source: 2026-09-23 — owner, on reviewing PR #297: *"for now it's ok, we'll have to have a plan that follows the full QC implementation to check if this still happens. Once QC stands, we should not allow the loader to let unchecked readings pass."* Every claim below was measured on `feat/plan-272-qc-selection` at `54673d79` (v0.1.960), i.e. against the tree as it will be once #297 merges — not against today's `main`, which does not yet contain `QC_UNCHECKED`.
---

# Plan 315 — the onboarding loader must not let unchecked readings pass

⚠️ **The plan number 315 is PROVISIONAL until the owner grants it.** 302–305 were granted in
prose and never written, so the sequence is not a reliable allocator (Plan 314 records the same
caveat).

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY.

⏸️ **This plan runs LAST — owner, 2026-09-23.** Its premise is that the QC ladder has landed and
the rule set has stopped changing shape. Scheduling it earlier measures a moving target. See D1.

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
⚠️ **Claims 4, 6 and 7 were each overstated in the first draft and are corrected here** — the
corrections are marked, because in this project a wrong claim has twice been cited back as review
evidence.

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

**4. The datum seam cannot empty the set on its own.** `OBS_DATUM_DEPENDENT_RULES =
{range_check, gross_outlier}` (`services/qc_datum.py:14`) and the skip applies only to
`water_level` (`:29-32`). Against the table above, a datum-less water-level station still keeps
`rate_of_change`, `frozen_sensor`, `spike` at 600 s and `rate_of_change`, `spike` at 86400 s.
⇒ **Never zero on the deployed rule set.**

⛔ **Corrected — the first draft blamed Plan 264 for this, and that was wrong.** 264 is
most-specific-wins *per `(rule_id, parameter, cadence)`*: a network rule **replaces the generic
rule of the same id** (`264:95-100`), and *"an override cannot suppress a rule"* (`264:382`). So
network selection does not remove the surviving non-datum rules and cannot thin the set to
datum-only. **What can** is configuration — *"a TOML set omitting the generic rules"*
(`264:266`), which is a property of what someone writes, not of 264's mechanism. ⇒ This is a
config-review concern, and it is not a reason to sequence behind 264.

**5. Onboarding infers ONE cadence for the whole history.** `Stage1QualityChecker.check` groups
by `(station_id, parameter)` and infers a single step per group (`services/qc.py:304-307`), and
Step 5 hands it the entire `start_utc → end_utc` window at once. ⚠️ **So "the window is wide"
cuts both ways.** It is what makes a homogeneous series resolve — and it is also what makes a
*mixed-cadence* history (daily before some date, 10-minute after) collapse to one median that may
match neither. D5's argument assumes homogeneity and does not say so.

**6. Three consumers read this path's output, in the same run, immediately after it.**

| site | what it computes | filter | its own minimum |
|---|---|---|---|
| `onboarding.py:258` | per-station skill scores + diagrams | `qc_status=QC_PASSED` | 🔴 **none** — `sample_size` is recorded (`services/skill/service.py:418`) but nothing gates on it |
| `onboarding.py:855` (Step 5b, `:843`) | climatological baselines | `qc_status=QC_PASSED` | `min_samples = 10` **per day-of-year window** (`services/baselines.py:21,46`) |
| `onboarding.py:902` (Step 5c, `:890`) | flow regimes | `qc_status=QC_PASSED` | `min_observations = 365` (`services/flow_regime.py:28,41`) |

🔴 **This is the whole risk, and it is a loop, not a leak.** Baselines feed `gross_outlier`, which
the *ingest* path then runs (`_apply_gross_outlier`, `services/qc.py:253`, dispatched at `:337`).
So marking onboarding rows `QC_UNCHECKED` shrinks the baseline population, which degrades a live
QC rule on a path this plan does not touch. Plan 269 found the same loop from the threshold side
and called it *"a real limitation, not a rounding error."*

⛔ **Corrected — "the station loses its baseline" was wrong, and the true behaviour is worse in
one direction and better in the other.** `onboarding.py:875-877` stores only `if clim:`, and
`store_baselines` upserts (`store/clim_baseline_store.py:16-42`). So:
- **A shrunken population that still clears `min_samples` writes a NARROWER baseline over the old
  one** — a silent quality change, not an outage. This is the one that matters.
- **A population that clears nothing writes nothing**, so an existing baseline SURVIVES, stale,
  and a first-ever onboarding simply produces none.
- 🔑 **And the upsert is keyed per day-of-year** (`station_id, parameter, day_of_year`), so the
  two mix: the days that still clear `min_samples` are overwritten, the days that no longer do
  keep their old rows. The stored baseline ends up **partly refreshed and partly stale, with
  nothing recording which is which** — a state neither of the two bullets above describes on its
  own, and the reason the hold criterion belongs at the station level rather than per row.

⇒ **Closing the fail-open without deciding the consumer policy in the same change is the way to
degrade the fleet quietly.** That is D2.

**7. Onboarding's Step 5 cannot overwrite an ingest verdict — but the onboarding RUN can.**
Step 5 fetches `RAW` only (`:780-782`), so re-QC never touches a row ingest has judged. ⛔
**Corrected: the first draft generalised that to the whole run, and it does not hold.** Step 3
(`onboarding.py:600-605`) calls `store_raw_observations`, which upserts on the natural key and
**resets `qc_status` to `RAW`, clearing `qc_flags` and `qc_rule_version`, whenever the value or
the rating-curve provenance changed** (`store/observation_store.py:97-117`, Plan 035 Task 2). A
restated row therefore re-enters Step 5 and is re-judged. ⇒ **The blast radius is backfilled
history *plus* any row a restatement touches** — not history alone.

**8. 🔴 The `-norules` sentinel that Plan 272 T0 specified did NOT ship.** T0's In-list requires a
sentinel `qc_rule_version` (`"1.0-norules"` and the datum variants) when the resolved rule set is
empty (`272:715-717`). Measured at `54673d79`: `obs_qc_rule_version`
(`services/qc_datum.py:23-27`) returns `"1.0"`, `"1.1-datum"` or `"1.1-datum-skip"` and has no
zero-rule variant; the flow writes that value unconditionally
(`flows/ingest_observations.py:378,388`). Nothing in `src/` or `scripts/` contains the string
`norules`. **Two consequences, both outside this plan's scope but owed to whoever owns them:**
- With `QC_UNCHECKED` stored, zero-rule rows *are* identifiable by `qc_status`, so the sentinel is
  arguably superseded rather than missing. **Someone has to say which.**
- ⛔ **Plan 314's E1(a) rests on it**: *"The rewritten rows keep T0's `-norules` sentinel in
  `qc_rule_version`, so they stay identifiable afterwards — the revert does not re-hide the
  defect."* Against the shipped code that sentence is **false** — after E1(a)'s `UPDATE` the rows
  are indistinguishable again. 314 is suspended, so this is not urgent; it is recorded so the
  suspension does not preserve a wrong sentence.

## Owner decisions — all three CLOSED 2026-09-23

### D1 — the trigger. **CLOSED: this plan runs last.**

Owner: *"yes, it runs last."*

⇒ It is scheduled after the plans that change the **shape** of the rule set — **303**
(`precipitation`/`temperature` rule rows, and the cadences they declare), **269** (per-station
thresholds), **313** (`rate_of_change`'s arithmetic) and **264** (the network dimension) — all
merged and deployed.

⚠️ **The reason is that T1's census expires whenever any of them lands**, not that any one of them
creates the defect. § What is measured (4) retracts the first draft's claim that 264 does. 264 is
included because it changes what `rules_for` returns, so the census must be taken against the
final selector — not because it thins the set.

### D2 — the consumer policy. **CLOSED: exclude, and hold the station loudly.**

Owner: *"yes to consumer action"* — the recommended option. All three consumers exclude
`QC_UNCHECKED`; a station whose checked population falls short is **HELD, not promoted**, with the
reason recorded, so the shortfall is visible at onboarding time instead of surfacing later as a
quietly narrowed baseline.

⚠️ **Two of the three minima already exist and must be used, not invented** — § What is measured
(6): `min_samples = 10` per day-of-year window for baselines, `min_observations = 365` for flow
regimes. ⛔ *The first draft said these "must be made explicit"; they already are.*

🔴 **Skill is the one that has none.** `services/skill/service.py:418` records `sample_size` and
gates on nothing. T2 must state a hold criterion for it — a small design decision inside the
task, not a new open decision.

### D3 — history already stamped `QC_PASSED`. **CLOSED: leave it.**

Owner: *"history is left. we'll onboard for a real deployment for a customer a-fresh. this here is
the test case for development."*

⇒ **No rewrite, and no boundary record either.** The first draft proposed recording the onboarding
run from which the loader stopped failing open; the owner's reasoning removes its purpose. A
mixed-era corpus only ever exists on *this* development deployment, because a customer deployment
is onboarded from scratch — so there is no later reader who needs to tell the eras apart.

⛔ **Two things the first draft got wrong here, corrected so the closure does not preserve them:**
- **The boundary record was not buildable as specified.** `observations` carries no QC timestamp,
  and a run timestamp cannot label a row's era anyway, because a `RAW` row can be checked at any
  later run. Dropping it is right for a second reason.
- **A `qc_status → RAW` reset is NOT a missing operation.** `update_qc`
  (`store/observation_store.py:143-157`) accepts any `QcStatus`, `RAW` included, and Step 3's
  upsert already performs exactly that reset (§ What is measured (7)). What does not exist is a
  re-QC *workflow*. ⛔ *The first draft said the operation itself was absent and used that as the
  argument against re-running QC. The real argument is the owner's: fresh onboarding for real
  deployments makes the question moot.*

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
- **The mixed-cadence probe from § (5), stated as a test of the DECLARED SET, not as a
  difference.** ⛔ *A first draft compared the whole-window median against a recent-window median
  and treated a difference as the finding; that is a non-sequitur — both can be declared values
  (600 and 86400 are the two that exist), and the comparison would report a station that is fine.*
  The probe is: **the whole-window median is outside `{600, 86400}` while a recent-window median
  is inside it.** That, and only that, shows the wide window causing the mismatch.
- Run it on the staging host against the live corpus, not on a fixture.

**Out.** ⛔ Any write. ⛔ Any change to `services/onboarding.py`.

**Pre-change.** N/A — a measurement.

**Verification.** The census output, recorded in this plan, with the date and the commit it ran
against. ⚠️ **It expires** — per D1, re-run it at the trigger and say so next to the number.

### T2 — Close the fail-open, and apply D2's consumer policy in the same change

**Outcome.** A group that resolves zero rules at onboarding is stored `QC_UNCHECKED`, the three
consumers exclude it, and a station without enough checked data is held rather than promoted on a
quietly narrowed baseline.

**In.**
- `services/onboarding.py` Step 5 calls `resolve_selection` with the same rows and skip set it
  passes to `check`, and stores `QC_UNCHECKED` for a zero-rule group — the shape
  `flows/ingest_observations.py:358-388` already uses, so this is symmetry with the ingest path,
  not a second mechanism. ⚠️ **Do not re-invent it**: if the two paths need the same logic, the
  shared piece moves to one place rather than being copied. A hand-copied twin is exactly what
  Plan 272 had to delete from `scripts/dhm_precip/qc_mask.py`.
- **D2's policy at all three sites** (`:258`, `:855`, `:902`), each touched deliberately and each
  named in the diff — ⛔ *not a category ("the onboarding consumers"); Plan 272 T4 records why a
  category cannot be checked off against the code.*
- **The hold criterion**, using the existing minima for baselines and flow regimes, and a stated
  one for skill (D2).
- ⚠️ **The narrowed-baseline case is the one to test**, not the empty one: `onboarding.py:875-877`
  stores only `if clim:`, so a shrunken-but-sufficient population overwrites the old baseline with
  a narrower one and nothing reports it (§ What is measured (6)).
- The onboarding counters and the returned result gain the unchecked count, as
  `IngestResult.qc_unchecked` did.
- **Delete the refusal comment at `services/onboarding.py:811-817`** in the same change. A comment
  explaining why a gap is deliberate, left standing after the gap is closed, is a lie with a
  citation.

**Out.** ⛔ The scheduled ingest path. ⛔ Re-QC over stored history (D3). ⛔ Any threshold value.
⛔ `services/calculated_station_onboarding.py` — measured: it contains no `check` call and no
`aggregate_qc_status` call at `54673d79`, so it has no fail-open of its own to close.

**Pre-change.** A RED test proving the current behaviour: an onboarding run over a group whose
cadence matches no rule stores `QC_PASSED` today. ⚠️ **It must fail because the status is wrong,
not because a symbol is missing** — the standard Plan 272 § the same standard sets.

**Verification.**
- A zero-rule onboarding group stores `QC_UNCHECKED`; a group that resolves rules and passes them
  still stores `QC_PASSED`. The two are asserted separately.
- Each of the three consumers is asserted at its own site.
- A station whose checked population falls below its consumer's minimum is HELD with the reason
  recorded; a station above it is promoted exactly as today.
- **A restated row re-enters Step 5 and is judged under the new rule** — § What is measured (7)
  makes this reachable, so it is asserted rather than assumed.

### T3 — Retire the records that still describe the old behaviour

**Outcome.** No document still says onboarding fails open, and the two plans that *argued* the
exclusion record that it was closed.

**In.**
- `docs/v1-scope.md` § QC posture point 4 — the "once QC is fine-tuned" half stops being a
  commitment and becomes a description.
- ⚠️ **A sweep by VALUE, not by site**: every place that states the onboarding exclusion,
  including Plan 272 D5 and its § the second fail-open call site, and Plan 269 § What this
  deliberately does not do. *(Plan 272's own § Out-of-scope entry for re-QC stays true under D3
  and must not be edited to imply otherwise.)*
- **Also fix, because T1's census hits it immediately:** `docs/v1-scope.md` § QC posture has a
  broken blockquote — the owner's quote is split mid-sentence by the `enable_observation_alerts`
  warning, leaving *"— and only from that point do the data-retention guarantees have to hold as
  planned"* orphaned after it. Restore the quote, then place the warning after it.

**Out.** ⛔ Any code change. ⛔ A boundary record of any kind (D3).

**Pre-change.** N/A.

**Verification.** A grep for the exclusion's wording returns only the records that say it was
closed; the blockquote renders as one quotation.

## Explicitly out of scope

- **The scheduled ingest path** — Plan 272, shipped in PR #297. This plan makes onboarding match
  it; it does not revisit it.
- **The network dimension of selection** (264), **per-station thresholds** (269),
  **`precipitation`/`temperature` rule rows** (303) and **`rate_of_change`'s arithmetic** (313).
  This plan runs after them by D1 and inherits whatever shape they leave.
- **The rollout controls** — Plan 314, suspended. ⚠️ Its E1(a) sentence is falsified by § What is
  measured (8); that is recorded, not fixed here.
- **A re-QC workflow over stored history** — closed by D3. ⚠️ *Not* because the reset operation is
  missing: it exists (D3). Because real deployments onboard afresh.
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
