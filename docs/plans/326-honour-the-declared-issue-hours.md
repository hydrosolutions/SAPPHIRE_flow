---
status: DRAFT
created: 2026-09-24
plan: 326
title: The model says issue me at midnight, and we run it four times a day
scope: Honour a model's declared issue hours when selecting which models a forecast cycle runs, so a model that declares one issue hour is not run at three cycles where its own inputs cannot be assembled. Covers the GROUP selection path and, if the same gap exists there, the station path. NOT the seam itself (Plan 311 — both windows are individually correct), NOT the forecast schedule's cron, NOT changing any model's declaration, NOT the pilot's assignment record, NOT Plan 312's resolver (shipped).
depends_on: []
blocks: [262]
open_decisions: [D1, D2, D3]
source: 2026-09-24 — Plan 311's T1 established the seam is label-safe but leaves a one-day gap at non-midnight cycles, and its closure needed an ENFORCED restriction that has nowhere to live. Looking for that home found the declaration already exists and is unread. Every claim below was measured against `origin/main` that day.
---

# Plan 326 — the model says issue me at midnight, and we run it four times a day

⚠️ **Plan number PROVISIONAL until the owner grants it.** 340-344 are held by a concurrent session.

## Status

**DRAFT.** ⛔ No implementation until an independent review is complete and the orchestrator sets
READY. **D1 is the load-bearing one and may not be ours to answer** — see the FI clause.

## Why this plan exists

Plan 311 asked whether a daily model may be issued off midnight. Its answer: the labels are safe,
but at a non-midnight cycle the issue day falls in neither the past nor the future array, and the
model's own cadence gate then **refuses the run** with a typed input-data failure. 311's closure is
option (a) — run the pilot at 00:00Z — and the review of that closure said, correctly, that **(a) is
only (a) if the restriction is enforced.** Writing "observe the midnight cycle" does not stop the
other three firing.

Looking for somewhere to enforce it turned up something better.

⭐ **`cmal_small` already declares `issue_hours: [0]`** (`models/aquacast/configs/cmal_small.yaml:11`).
🔴 **Nothing in `src/`, `tests/` or `scripts/` reads it.** So this is not a policy we need to
invent. It is a declaration the model makes and we ignore — the same shape as the discharge
aggregation defect, where the model declared a thing and our side dropped it on the way through.

## What is measured

`origin/main`, 2026-09-24.

1. **The declaration exists and is unread.** `cmal_small.yaml:11-12` → `issue_hours: [0]`.
   `cmal_pool_pt.yaml:11` declares one too. `grep -rn issue_hours src/ tests/ scripts/` outside the
   config directory returns **nothing**.
2. **Four cycles a day.** `cli/register_deployments.py:45` — `SCHEDULE_FORECAST_CYCLE`, default
   `"0 */6 * * *"` ⇒ 00, 06, 12, 18. So a model declaring one issue hour runs at **four**, and 311
   measured that three of them leave the seam gap.
3. 🔴 **Selection cannot see the cycle time at all.** `services/run_group_forecast.py:223` —
   `discover_group_runs(models, group_store)` takes the model dict and the group store and nothing
   else. ⇒ There is no point in the current call where an hour could be compared.
4. **The assignment record has nowhere to put one either.** `group_model_assignments`
   (`db/metadata.py:1027-1053`) holds `group_id`, `model_id`, `time_step`, `status`, `priority`,
   `created_at`. ⛔ **`time_step` is NOT a cycle gate** — it is the forecast's output resolution,
   read at `run_forecast_cycle.py:3087,3522`. *Using it to decide how often a model runs would
   conflate "emits daily steps" with "runs once a day", which are different claims.*
5. ⚠️ **Even the midnight cycle is not exactly midnight.** `_resolve_cycle_time`
   (`flows/run_forecast_cycle.py:698-704`) returns **`clock()`** when no cycle time is supplied, and
   the forecast deployment (`register_deployments.py:103-107`) passes **no parameters**. So the
   00:00 cron fires and the issue time becomes `00:00:37Z` or similar. Under the future window's
   `valid_time >= issue_time` rule the issue-day bucket is then dropped. ⇒ **An hour-match alone is
   not sufficient**; see D3.
6. **SAP3 already has a home for model declarations.** `types/model.py` carries
   `declared_aggregations`, `declared_lookbacks`, `declared_horizon_semantics` (`:315`) and
   `declared_min_future_steps` (`:316`), populated from the FI requirement at the adapter boundary.
   ⇒ **`declared_issue_hours` would be the fifth of an existing pattern**, not a new concept.
7. **Scope of the refusal: FI-backed models only.** The cadence gate that refuses lives in the
   **aquacast package**, not in SAP3. The Swiss statistical models do not have it. ⇒ Today this
   affects `cmal_small` alone — but § (1) shows the declaration is not unique to it.

## Owner decisions

### D1 — does the ForecastInterface express issue hours? **OPEN — and this is not a preference.**

⛔ **`AGENTS.md` makes this mandatory, not optional.** Every model we run must go through the FI
contract, and when a model does not fit there are exactly two allowed paths: fix our side to comply,
or — *if the FI genuinely cannot express what we need* — **file an issue in the ForecastInterface
repo and co-design a resolution.** ⛔ *Patching around it on the SAP3 side is explicitly forbidden.*

| | option | when it applies |
|---|---|---|
| **(a)** | FI's `InputRequirement` already carries issue hours ⇒ read it at the adapter boundary into a new `declared_issue_hours`, exactly like `declared_horizon_semantics`. | If the field exists |
| **(b)** | FI does not carry it ⇒ **file an FI issue and co-design**, then implement against the agreed surface. `docs/fi-issues/` already holds two such issues (002, 003), so the route is established. | If it does not |
| **(c)** | ⛔ **REJECTED before it is proposed:** read `cmal_small.yaml` from SAP3. That is reaching past the contract into a model's private configuration, and it would work — which is exactly why it must be named and refused here. | never |

🔴 **T1 answers which of (a)/(b) applies by INSPECTING the FI package**, and the answer decides the
shape of everything after it. ⚠️ *The FI package is not importable in the default dev environment
(`aquacast` extra absent); T1 must install it or read the pinned checkout, and say which.*

### D2 — what a model that declares NOTHING means. **OPEN.**

Most models declare no issue hours. Two readings:

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **No declaration ⇒ run at every cycle** — today's behaviour, preserved. | Safe by construction: nothing changes for any existing model. ⚠️ A model that *should* be restricted but forgets to say so stays unrestricted. |
| (b) | No declaration ⇒ infer from the model's input cadence (a daily model runs daily). | ⛔ Rejected in drafting: it changes behaviour for every existing daily model on an inference, to fix a problem only one model has. § (7) — the Swiss models do not even refuse. |

**Recommendation: (a).** ⛔ *A silent behaviour change across the fleet is not an acceptable price
for tidiness.*

### D3 — hour-match alone, or a pinned issue time. **OPEN — § (5) is why.**

A cycle that fires at `00:00:37Z` **matches hour 0** and still drops the issue-day bucket. So:

| | option | cost |
|---|---|---|
| (a) | Match on the hour only. | Simple, and **does not actually fix the pilot** — the seam still opens at 00:00:37Z. |
| (b) ⭐ | Match on the hour **and** floor the resolved cycle time to the hour when a model declares issue hours. | Fixes it. ⚠️ Changes what `issue_time` means for those runs, which must be stated where `_resolve_cycle_time` is read. |
| (c) | Require the deployment to pass an explicit `cycle_time`. | Moves the problem to the scheduler and to every operator who triggers a run by hand. ⛔ *An operator running a cycle ad hoc would silently get the broken behaviour.* |

**Recommendation: (b)**, and ⚠️ **the plan must not pretend (a) is sufficient** — 311's closure
already made that mistake once.

## Tasks

### T1 — Establish whether FI expresses issue hours (D1)

**Outcome.** A recorded answer, from the FI source, to whether the contract can carry this — and
therefore whether this plan implements or first files an issue.

**In.**
- Inspection of the pinned FI package's `input/requirement.py` and `interface/protocol.py`,
  naming the version inspected and how it was obtained.
- If **absent**: a drafted FI issue in `docs/fi-issues/`, following 002 and 003, stating the need,
  why SAP3 cannot express it alone, and a proposed surface. ⛔ *Drafted, not filed — filing is the
  owner's.*

**Out.** ⛔ Reading the model's YAML from SAP3 (D1c). ⛔ Implementing anything before the answer.

**Pre-change.** N/A — investigation. ⚠️ **Report the answer even if it is (b)**, which blocks this
plan's remaining tasks on an external repo — ⛔ *that is a finding, not a failure, and it is exactly
what `AGENTS.md` asks for.*

**Verification.** The answer cites the FI file and version, not a memory of it.

### T2 — Carry the declaration to the selection point (D1a, D2, D3)

**Outcome.** A forecast cycle runs a model only at an hour the model accepts.

**In.**
- `declared_issue_hours` on SAP3's model type, populated at the FI adapter boundary alongside the
  four existing `declared_*` fields (§ 6).
- The cycle time threaded into group selection — `discover_group_runs` currently cannot see it
  (§ 3) — and the same check applied to the station path **if T2 finds the gap there too**.
  ⚠️ *Stated conditionally because it was not measured; do not assume symmetry.*
- D3's flooring, if (b).
- 🔴 **A structured log line when a model is skipped for the cycle**, naming the model and the
  declared hours. ⛔ *A model that silently does not run is indistinguishable from one that is
  broken — which is the operational complaint this plan exists to remove.*

**Out.** ⛔ Changing any model's declaration. ⛔ The cron schedule. ⛔ The assignment record —
§ (4): this belongs to the model, not to where it happens to be assigned. ⛔ Any behaviour change
for a model that declares nothing (D2a).

**Pre-change.** A RED test asserting the DESIRED behaviour: **a model declaring `issue_hours: [0]`
is NOT selected at a 06:00 cycle, and IS selected at 00:00.** ⚠️ It must fail because the model was
selected, not because a symbol is missing.

**Verification.**
- Declaring `[0]`: selected at 00:00, skipped at 06:00/12:00/18:00, with the skip logged.
- 🔴 **A model declaring nothing is selected at all four** — asserted, because D2a's whole promise
  is that the fleet is untouched.
- D3(b): a cycle resolving to `00:00:37Z` still selects, and the issue time it runs with is
  `00:00:00Z`.

### T3 — Close Plan 311's D1 with the enforcement it needed

**Outcome.** 311's `blocks: [262]` lifts on something enforced rather than written.

**In.** 311's D1 closed on (a) **with the mechanism named**, and its `blocks:` updated.

**Out.** ⛔ Re-opening 311's T1 finding. ⛔ Running before T2 is deployed and observed.

**Pre-change.** N/A.

**Verification.** The pilot's next scheduled non-midnight cycle produces **no** `cmal_small` run and
one skip log line — observed on the host, not inferred.

## Explicitly out of scope

- **The seam itself** — Plan 311. Both windows are individually correct; this plan stops a model
  meeting the seam, it does not close it.
- **The cron schedule.** Other models legitimately run four times a day.
- **What a one-day elision costs a 30-day-lookback model** — the modeller's question, and ⚠️ **it
  cannot be answered from pilot output while the restriction holds**, which 311 records.
- **`cmal_pool_pt`** — it declares issue hours too, but it is not onboarded and its 210-day lookback
  is unreachable here.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false,
     "note": "decides whether phase 2 implements or waits on an FI issue"},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "decision": "D1, D2, D3 CLOSED"},
    {"phase": 3, "tasks": ["T3"], "parallel": false, "note": "after T2 is deployed and observed"}
  ]
}
```
