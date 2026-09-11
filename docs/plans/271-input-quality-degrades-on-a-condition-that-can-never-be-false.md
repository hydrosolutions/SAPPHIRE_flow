---
status: DRAFT
created: 2026-09-11
plan: 271
title: Input quality degrades on a condition the model contract guarantees, so the signal is saturated and carries no information
scope: Decide what a cold start MEANS for a state-free model, and make `input_quality` discriminate. Covers the classification rule only. NOT implementing warm-up state persistence, NOT a change to the ForecastInterface (its state-free design is deliberate), NOT a change to the other input-quality categories (observation, NWP, forcing), NOT Plan 270's forcing-gap detection.
depends_on: []
blocks: []
related: [270, 239, 262]
source: 2026-09-11 — measured on the mac mini in Plan 261 T1's first post-deploy cycle (12:00Z, 514 forecasts). Plan 270 measured the same saturation independently the same day and explicitly scoped it out as unowned (`270:139-141`). Every number below is from that host on that day; re-measure before quoting.
---

# Plan 271 — input quality degrades on a condition that can never be false

> ⚠️ **Plan numbers 270 and 271 are not PR numbers.** PR #270 is Plan 261's merge
> (`75cf80cf`). Do not conflate them.

## Status

DRAFT. Nothing here is decided. This plan exists because the one signal an operator would
use to spot a sick forecast is currently on for every forecast, and has been for at least a
week. The fix is probably small; what it should BE is a real decision, and it is the owner's.

## What was measured

All on the mac mini, 2026-09-11, the first forecast cycle after Plan 261 T1 was deployed
(`75cf80cf` / `0.1.901`, verified present in the running worker, not merely in the checkout).

| fact | value |
|---|---|
| Forecasts in the 12:00:01Z cycle | **514** |
| Of those, `input_quality = degraded` | **514 (100%)** |
| Carrying the `warm_up` cold-start flag | **512** |
| Also carrying an `observation` staleness flag | 2 |
| Rows in `model_states` | **0** |
| Distinct `warm_up_source` over the retained window (2026-09-04 →) | **`cold_start`, and nothing else** |
| Forecasts per day over that window | ~1,335 |

So this is not a Plan 261 regression and not new. Every forecast in the retained window has
been `degraded`, every day, for the same reason.

### 🔑 The condition is structural — it cannot become false

The causal chain, every link measured:

1. **The ForecastInterface is state-free BY DESIGN.** `predict()` neither accepts a prior
   state nor returns one (`forecast_interface/interface/protocol.py:27-34`, FI v0.1.20), and
   the strings `prior_state`, `stateful` and `warm_up` do not appear anywhere in the package.
2. **Our adapter says so explicitly and deliberately:** *"ForecastInterface is state-free;
   prior_state is intentionally ignored"* (`adapters/forecast_interface.py:1109`).
3. **Every deployed model returns `None` as its state** — `climatology_fallback.py:174`,
   `linear_regression_daily.py:188`, `persistence_fallback.py:117`, and the
   `nwp_regression` / `nwp_rainfall_runoff` pair.
4. **So `model_states` stays empty** (0 rows), and `load_warm_up_state`
   (`services/operational_inputs.py:96-113`) correctly returns `COLD_START` every time.
5. **And the classifier degrades on it unconditionally** — `services/input_quality.py:120-127`
   is `if warm_up_source == COLD_START: DEGRADED`, with no test for whether the model is
   capable of carrying state at all.

⛔ **This is NOT an FI gap and must not be filed as one.** `CLAUDE.md` requires that a genuine
FI shortfall go upstream as an issue rather than a SAP3-side workaround. This is the other
case: FI's state-free design is intentional and documented on both sides. The defect is
entirely in how SAP3 CLASSIFIES a condition that FI guarantees. Fix it here.

## Why this is worth a plan rather than a one-line patch

**A flag that is always on is not a degraded signal, it is no signal.** `input_quality` is the
field an operator or the review dashboard would use to find a forecast worth distrusting. At
100% saturation it cannot do that, and the two forecasts in that cycle carrying a REAL
degradation — 85.4 h stale observations — are indistinguishable from the 512 that carry a
structural artefact.

**It lands on Plan 262 directly.** `cmal_small` is a deep-learning model and is the first one
whose warm-up state could mean something. It arrives through the FI adapter, which discards
state by design — so it too will always cold-start. Whoever deploys that pilot will read
`degraded` on every forecast and have no way to tell "state-free by contract, expected" from
"this model lost its state, investigate". The pilot deserves a signal that works before it
starts, not after.

**Getting it wrong in the other direction is worse.** Silencing the flag outright would mean
that if SAP3 ever gains a genuinely stateful model, a real cold start — the case the flag was
built for — would pass unremarked. The rule has to discriminate, not disappear.

## The decisions — all OPEN, none pre-judged

**D1 — What should a cold start mean for a model that cannot hold state?**
Not a degradation at all; an informational note; or a degradation only for models that CAN
hold state. Recommendation offered, not taken: the third, because it preserves the alarm for
the case it exists to catch.

**D2 — How does the system KNOW a model is state-free?**
`ModelDataRequirements` (`types/model.py:275-300`) has no such field, and FI cannot express
it. Candidates: derive it from the model's route (anything through the FI adapter is
state-free by construction), declare it on the SAP3-side model registry, or infer it from a
model having returned `None` before — which is unsound on the first cycle. ⚠️ Deriving from
the route is the cheapest and is true today, but it encodes "FI ⇒ stateless" as an invariant
that only holds while FI stays state-free. If that is the choice, it must be written down as
a dependency on FI's design, not as an incidental implementation detail.

**D3 — Is `warm_up` the right category at all, or should a state-free model simply not
produce a `warm_up` flag?** A category that never applies to any deployed model may be better
absent than always-green.

**D4 — What happens to the ~7 days of historical rows already labelled `degraded`?**
Leave them, relabel them, or record that the label changed meaning on a date. Relabelling
rewrites history; leaving them means a metric spanning the change is meaningless. This is the
same shape as decisions the time-grid family has already taken on stamped data, and should be
answered consistently with them.

**D5 — Should this ship before Plan 262's pilot?** It is small, but it touches a live
operational signal on a host that is about to receive a deep-learning pilot. Sequencing is the
owner's call.

## Tasks

Deliberately not written yet. D1 and D2 determine the shape of the work, and drafting tasks
before them would prejudge the decision — the failure the time-grid family spent four review
rounds correcting. Once D1 and D2 are settled this plan gains: the classification change, a
locking test proving a state-free model is NOT degraded while a genuine cold start still is,
the historical-rows decision from D4, and the doc updates.

## Exit gates (provisional — firm up once tasks exist)

1. A cycle on staging produces a spread of `input_quality` values, not a single value —
   specifically, the two genuinely-stale-observation forecasts are distinguishable from the
   rest.
2. A locking test proves a genuinely stateful model with a lost state is STILL flagged.
3. No change to the `observation`, `NWP` or `forcing` categories.
4. The FI is unchanged, and the plan records WHY that is correct rather than an omission.

## Related, explicitly NOT in this plan's scope

- **Plan 270** — the forcing-gap detection defect. Measured the same saturation and correctly
  scoped it out (`270:139-141`). 271 is the owner it was waiting for. Neither plan changes the
  other's subject.
- **Plan 239** — introduced the degraded gate. Not reopened here.
- **Plan 262** — the `cmal_small` pilot. Consumes this decision; does not make it.
- **Implementing warm-up state persistence.** A much larger question — whether SAP3 should
  carry model state at all, given every model is FI-routed and FI is state-free by design.
  Genuinely unowned. Not started here.
