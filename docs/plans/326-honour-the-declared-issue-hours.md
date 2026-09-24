---
status: DEFERRED
created: 2026-09-24
revised: 2026-09-24
plan: 326
title: Nothing records the hours at which a model may be issued — so a daily model runs four times a day
scope: Give the forecast cycle a way to know that a model must only be issued at certain hours, and honour it when selecting which models a cycle runs. Covers the GROUP and STATION selection paths. NOT the seam itself (Plan 311 — both windows are individually correct), NOT the cron schedule, NOT Plan 312's resolver (shipped), NOT changing any model's training config, NOT the model's own cadence gate (that lives in aquacast and is correct).
depends_on: []
blocks: [262]
open_decisions: [D1, D2, D3]
source: 2026-09-24 — Plan 311's closure needed an ENFORCED midnight restriction and had nowhere to put it. ⛔ The first draft of this plan claimed the model already declared one and we ignored it; an independent review proved that false and it is corrected below. Every claim was measured against `origin/main`, FI at the pinned `ad19597`, and aquacast at the pinned `5460f89`.
---

# Plan 326 — nothing records when a model may be issued

⚠️ **Plan number PROVISIONAL until the owner grants it.** 340-344 are held by a concurrent session.

## Status

**⏸️ DEFERRED — owner, 2026-09-24.** ⛔ *Not abandoned, and not blocked on anything external.*

🔑 **The trigger: revive this when the pilot has produced a forecast we are happy with, and we want
it to run unattended.** Until then it is enforcement for a pipeline that has never once succeeded
end to end.

⭐ **Why parking is the right call, not a delay.** The last pilot run failed on a missing group
resolver; **that fix is merged and already running on staging**, and *nobody has tried since*. So
the next run may simply work. Building a scheduling gate first would be building it for something
we have not seen produce a single forecast — and if the run fails for a different reason, this
plan's design may be the wrong shape anyway.

⚠️ **The restriction is only worth its cost when nobody is watching.** A run triggered by hand can
be pinned to midnight with an existing parameter (`cycle_time`), which needs no code at all. The
three-failures-a-day problem this plan solves is an *unattended* problem.

⛔ **Do not implement, and do not treat as neglected.** D1/D2/D3 stay open deliberately; D1's
recommendation (a deployment decision, not a model property) is the owner's, taken 2026-09-24, and
survives the pause.

## Why this plan exists

Plan 311 established that at a non-midnight cycle the issue day falls in neither the past nor the
future array, and the model's own cadence gate then **refuses the run**. Its closure is *run the
pilot at 00:00Z* — and a review of that closure said, correctly, that **(a) is only (a) if the
restriction is enforced.** Writing *"observe the midnight cycle"* does not stop the other three
firing. There is nowhere to enforce it.

⛔ **THE FIRST DRAFT OF THIS PLAN GOT THE REASON WRONG, and the correction matters more than the
original claim.** It said `cmal_small.yaml:11`'s `issue_hours: [0]` was a declaration we ignore.
**It is not a declaration.** Measured at the pinned aquacast (`config/experiment.py:1482-1487`):

> `window.issue_hours is only meaningful for HOURLY regimes` — and for a non-hourly model aquacast
> **raises** unless the value is exactly `(0,)`.

`cmal_small` is `resolutions: [daily]`. ⇒ **`[0]` is the only legal value it could hold.** It
carries no information about operational availability, and reading it as one would have built the
pilot's safety on a constant. ⭐ *Recorded at this length because the premise was plausible, wrong,
and would not have failed loudly.*

⇒ **The true state: nothing anywhere records the hours at which a model may be issued.** Not the
model config, not FI, not our assignment record, not the cycle.

## What is measured

`origin/main`, FI `ad19597` (v0.1.20), aquacast `5460f89`, all 2026-09-24.

1. 🔴 **`issue_hours` is not an availability contract** — see above. The only two files containing
   it are the vendored configs; `_shim.py:581` passes the YAML to aquacast's parser, which reads it
   for the training template. **SAP3 has no selection gate of any kind.**
2. 🔴 **FI cannot express it.** `InputRequirement` (`input/requirement.py:38`) carries targets,
   dynamic requirements and statics. `predict(issue_datetime=...)` accepts a requested instant and
   **declares no allowed schedule.** Inspected at the pinned commit, matching `uv.lock`.
3. **Four cycles a day.** `cli/register_deployments.py:45` and `docker-compose.yml:417` —
   `"0 */6 * * *"` ⇒ 00/06/12/18. ⚠️ *Attempted runs; assignment and input gates still apply, and a
   runtime override is possible.*
4. **Selection cannot see the cycle time.** `services/run_group_forecast.py:223` —
   `discover_group_runs(models, group_store)` and nothing else.
5. **The assignment record has nowhere to put one.** `group_model_assignments`
   (`db/metadata.py:1027-1053`): `group_id`, `model_id`, `time_step`, `status`, `priority`,
   `created_at`. ⛔ **`time_step` is the output resolution** (`run_forecast_cycle.py:3087,3522`),
   not a cycle gate — conflating them claims "emits daily steps" means "runs once a day".
6. ⚠️ **The midnight cycle is not midnight.** `_resolve_cycle_time` (`:698-704`) returns `clock()`;
   the deployment passes no parameters. The scheduled start time is used only for the **run name**
   (`:2106-2116`). ⇒ A 00:00 cron issues at `00:00:37Z`, and `valid_time >= issue_time` then drops
   the issue-day bucket. **An hour match alone does not fix the pilot.**
7. **SAP3 already projects four model declarations** — `declared_aggregations`,
   `declared_lookbacks`, `declared_horizon_semantics`, `declared_min_future_steps` — at
   `adapters/forecast_interface.py:789`. A fifth would follow an existing pattern.
8. **Scope of the refusal: aquacast-backed models.** The cadence gate is aquacast's
   (`operational/datasource.py:100,540` → `model.py:637`, `ModelFailure(INPUT_DATA)`), not SAP3's
   and not FI's. ⚠️ *"aquacast-backed", not "FI-backed" — and the tree alone cannot prove exactly
   one deployed assignment is affected; that needs the host.*
9. ⛔ **Correction: `forecastinterface` is a BASE dependency** (`pyproject.toml:28`), not behind the
   `aquacast` extra. *The first draft said otherwise and would have sent T1 installing something it
   already had.*

## Owner decisions

### D1 — is "when may this be issued" a MODEL property or a DEPLOYMENT decision? **OPEN — decides everything below.**

| | option | consequence |
|---|---|---|
| **(a)** | **A model property.** The model knows its own valid issue times. ⇒ FI cannot express it (§ 2), so `AGENTS.md` requires **filing an FI issue and co-designing**, then aquacast populates it, the shim preserves it, and the adapter projects it. ⛔ *Patching around FI is forbidden.* | Correct if it is genuinely the model's to say. **Blocks the pilot on an upstream round-trip.** |
| **(b)** ⭐ | **A deployment decision.** *We* decided to restrict the pilot to midnight; the model never asked. ⇒ it belongs on **our** assignment record, needs no FI change, and is not a workaround — recording an operator's choice in our own database is what that table is for. | Ships without upstream. ⚠️ A model that genuinely cannot be issued at some hour still has no way to say so — a real gap, deferred honestly rather than solved by accident. |
| (c) | Both, later. | ⛔ Not now: two mechanisms for one question, and no evidence yet that any model needs (a). |

**Recommendation: (b), and say plainly what it does not solve.** ⭐ *The restriction we actually
need is one WE chose. Routing an operator's decision through an upstream contract change would
block a pilot for weeks to express something the model has no opinion about.* ⚠️ **If (a) is
chosen, T1 becomes "draft the FI issue" and the pilot waits.**

### D2 — what "no restriction recorded" means. **OPEN.**

**(a)** ⭐ **No restriction ⇒ run at every cycle**, today's behaviour, preserved — safe by
construction, nothing changes for any existing model.
(b) Infer from the model's input cadence. ⛔ **Rejected in drafting:** it changes behaviour across
the fleet on an inference, to fix a problem one model has; § (8) shows the Swiss models do not even
refuse.

### D3 — an hour gate alone, or a logical issue time. **OPEN — and larger than the first draft admitted.**

§ (6) shows an hour match leaves the pilot broken. But **flooring the issue time is not a one-line
change**, and the first draft recommended it without naming what it touches:

- ⛔ **Flooring the shared `resolved_cycle_time` changes undeclared models too** — violating D2(a).
- **Flooring only the restricted model creates two issue times inside one cycle.** Shared station
  inputs cannot simply be reused, and a combined forecast currently inherits its **first
  contributor's** issue time.
- 🔴 **Storage collides.** Forecast uniqueness is `(station, model, issued_at, parameter)` and the
  write is a plain INSERT with duplicate rejection under test; the group path treats a storage
  failure as **fatal**. Two runs inside hour 0 would collide by construction.
- **`lead_time_hours` is derived from `issued_at`** and changes with it.
- **Group model state** is stored against the original `resolved_cycle_time`
  (`run_forecast_cycle.py:3611`), and **rating curves are selected once** at the original cycle time
  (`:2512`). Both would disagree with a floored forecast.
- **Delayed and manual runs:** a job starting at `01:00` is skipped; an explicit `00:59` backdates
  by almost an hour.

⇒ **The real question is whether we introduce a LOGICAL ISSUE TIME distinct from the run time**,
and if so, everything above must agree on it. ⛔ *NWP reference time and actual creation time stay
separate from it.* ⚠️ **Do not adopt flooring as a small change.** A third option — the scheduler
supplies an explicit cycle time — moves the problem to every operator who triggers a run by hand,
which § (6) shows is where the silent breakage already lives.

## Tasks

### T1 — Settle D1, and record the gap either way

**Outcome.** A decided mechanism, and the unsolved half written down.

**In.** D1's answer with its reasoning. If **(a)**: a drafted FI issue in `docs/fi-issues/`
following 002 and 003 — ⛔ *drafted, not filed; filing is the owner's* — and this plan pauses. If
**(b)**: a note recording that **no model can yet declare its own issue constraints**, so the next
model that genuinely has one meets an empty road.

**Out.** ⛔ Reading a model's private config as if it were a contract (§ 1 — the first draft's
error). ⛔ Implementing before D1 is closed.

**Verification.** The FI claim cites the pinned file and version, not a memory of it.

### T2 — Honour the restriction at selection (D1b, D2, D3)

**Outcome.** A cycle does not run a model at an hour that model is restricted from.

**In.**
- The restriction stored where D1 decides, with a migration if it is the assignment record.
- The cycle time threaded into **group** selection (§ 4) **and** the **station** paths — 🔴 *both
  `run_all_station_forecasts` and its per-track counterpart lack the gate; filtering only group
  discovery leaves a station-scoped restricted model running off-hours.* ⚠️ **Filter at SELECTION,
  not at prediction:** a model filtered too late still influences shared input requirements and
  forcing resolution.
- 🔴 If D1 is **(a)**: the **whole** declaration path — agreed FI surface → aquacast populates →
  **shim preserves** → adapter projects — with a test through the real shim. ⛔ *`_canonical_requirement`
  (`_shim.py:269`) reconstructs only targets, dynamic and static; adding an FI field and a SAP3
  projection alone would silently leave the pilot unrestricted.*
- D3's mechanism, with every site it names agreeing on the same instant.
- 🔴 **A distinct schedule-skip log** naming the model, the evaluated time and the restriction.
  ⛔ **Never convert an input, configuration or prediction failure into a schedule skip** — a model
  that silently does not run is indistinguishable from one that is broken.

**Out.** ⛔ The cron schedule. ⛔ Any behaviour change for a model with no restriction (D2a).
⛔ Concealing a GROUP model wrongly assigned to the station route — the adapter rejects that
deliberately; it is a configuration error, not something scheduling should hide.

**Pre-change.** A RED test: **a restricted model is NOT selected at 06:00 and IS at 00:00.** ⚠️ It
must fail because the model was selected, not because a symbol is missing.

**Verification.**
- Restricted: selected at 00:00, skipped at the other three, each skip logged with its reason.
- 🔴 **Unrestricted models are selected at all four** — asserted, because that is D2(a)'s promise.
- **Both station routes** tested with a restricted and an unrestricted model *together*.
- D3: a cycle resolving to `00:00:37Z` selects, and every site in D3's list records the same instant.
- 🔴 **A repeated and a partially-completed cycle within the same hour** — § D3's collision.

### T3 — Close Plan 311's D1 on an enforced restriction

**Outcome.** 311's `blocks: [262]` lifts on a mechanism, not a sentence.

**In.** 311's D1 closed with the mechanism named; its `blocks:` updated.

**Out.** ⛔ Running before T2 is deployed and observed.

**Verification.** 🔴 **Not a skip log alone — that proves suppression, not recovery.** Required:
(1) a non-midnight cycle producing no pilot run and one skip line; (2) **an observed midnight run
with continuous assembled inputs and a persisted pilot forecast row**; and (3) proof that a failure
at an *eligible* hour is still visible. ⛔ *Without (2) and (3), fallback models keep cycle
freshness green while the pilot never succeeds — which is exactly today's state.*

## Explicitly out of scope

- **The seam** — Plan 311. This stops a model meeting it; it does not close it.
- **The cron schedule.** Other models legitimately run four times a day.
- **What a one-day elision costs a 30-day-lookback model** — the modeller's, and ⚠️ unanswerable
  from pilot output while the restriction holds.
- **`cmal_pool_pt`** — not onboarded; its 210-day lookback is unreachable here.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false,
     "note": "D1 decides whether phase 2 implements or waits on an upstream round-trip"},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "decision": "D1, D2, D3 CLOSED"},
    {"phase": 3, "tasks": ["T3"], "parallel": false, "note": "after T2 is deployed and observed"}
  ]
}
```

## Changelog

**2026-09-24 — created, then substantially rewritten** after an independent review returned
**6 major + 1 minor**. The findings are applied in the text above, not appended:

| finding | effect |
|---|---|
| **M1 — the premise was false** | `issue_hours` is aquacast's training-template field, *"only meaningful for HOURLY regimes"*, and a non-hourly model **must** set `(0,)` or aquacast raises. It is a mandatory constant, not a declaration. The plan's reason for existing is rewritten. |
| **M2 — the declaration path is four hops** | If D1(a): FI → aquacast → **shim** → adapter, with a test through the real shim; `_canonical_requirement` rebuilds only targets/dynamic/static |
| **M3 — flooring is not small** | D3 now lists shared cycle time, two issue times in one cycle, combined forecasts, group state, rating curves, delayed and manual runs — and asks the real question: a **logical issue time** |
| **M4 — flooring collides in storage** | uniqueness is `(station, model, issued_at, parameter)`, a plain INSERT, fatal on the group path; `lead_time_hours` moves with it. Now a verification clause |
| **M5 — the station paths were assumed** | both station routes named explicitly, and the filter must act at SELECTION, not prediction |
| **M6 — T3 proved suppression, not recovery** | verification now requires a persisted pilot forecast and proof that eligible-hour failures stay visible |
| **m1 — FI is a base dependency** | § (9); the draft's claim that it needs the `aquacast` extra was wrong |

⭐ **D1 also changed shape.** The review established FI cannot express issue hours, which the first
draft treated as forcing an upstream round-trip. Re-reading the need: **the restriction we actually
want is one WE chose**, which makes it a deployment decision and legitimately ours — not a
workaround. That is now D1(b) and the recommendation.
