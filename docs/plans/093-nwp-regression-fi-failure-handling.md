# Plan 093 — nwp_regression: return ModelFailure on insufficient lags (FI contract)

**Status**: DRAFT
**Priority**: medium — surfaces as noisy `hindcast.step_failed` + the operational
`matmul` failure; not fatal (onboarding completes, artifact promotes), but the
model violates the ForecastInterface "return, don't raise" contract.
**Phase**: v0b — model robustness (ForecastInterface adherence)
**Parent**: epic 088 (NWP-on); surfaced during the 2026-07-03 Mac-mini onboarding
**Related**: `models/nwp_regression.py` (`predict`, `_initial_lags`, the training
feature build), `adapters/forecast_interface.py` (ModelFailure→ModelOutputError),
FI `docs/model_interface.md`, `docs/input_requirement.md`
**Created**: 2026-07-03

---

## Problem

`NwpRegression.predict` (`models/nwp_regression.py:197`) builds each step's
feature vector as `np.concatenate(([precip[step], temp[step]], lags))` and does
`features @ coefficients`, with **no validation** that `len(lags)` equals the
trained `artifact.n_lags`. When the discharge history is shorter than `n_lags`
(near a data boundary or across a gap — common during the historical hindcast),
`_initial_lags` yields a short vector → the feature vector is `2 + k` (< `2 +
n_lags`) → numpy raises `matmul: … size 9 is different from 8`.

Observed live: recurring `hindcast.step_failed error='matmul: … size 9 is
different from 8'` during onboarding, and the operational `matmul … 9 vs 3` on
the dev host (insufficient live lags).

## Why this is a contract violation (FI)

The ForecastInterface is explicit (`docs/model_interface.md:90`): `predict` /
`hindcast` **return `ModelResult` rather than raising**; *anticipated* failure
(degraded/insufficient inputs) "must be **returned, not raised**." SAP3's
except-and-return is only a **backstop for unanticipated bugs**. Insufficient lag
history is an *anticipated* input-data condition, so the model must return
`ModelFailure(cause=FailureCause.INPUT_DATA, message=…)` (whole-run) — or, since
`nwp_regression` is single-station, a per-station `FAILURE` entry — not throw.

`max_nan` does **not** cover this: it is a SAP3 **pre-predict gate for NaN
tolerance** (`docs/input_requirement.md:100`); the matmul case is a **shape /
length** shortfall (missing lag *rows*, not NaNs), so the model owns it.

## Goal

`nwp_regression` never raises on insufficient/misaligned lag data — it validates
the feature dimension against the trained artifact and returns a typed
`ModelFailure(INPUT_DATA)` (routed by the FI adapter to graceful
skip/fallback), for both `predict` and the hindcast path.

## Open design questions (grill-me before READY)

1. **Validation point + granularity.** Guard once before the step loop (require
   `len(initial_lags) == artifact.n_lags`), or per-step? Single-station → whole
   `ModelFailure` vs a per-station `FAILURE` `VariableOutput` — pick per the FI
   rule ("`ModelFailure` reserved for total inability").
2. **Is delivery-of-exactly-`lookback` guaranteed?** Clarify with the FI whether
   SAP3 must deliver exactly `lookback` past steps (padding/failing upstream) or
   up-to-`lookback`. If the former, this is *also* a SAP3 delivery bug; if the
   latter, the model must be defensive. Likely: model defensive regardless +
   confirm the SAP3 side does not silently under-deliver.
3. **Message/cause taxonomy.** `FailureCause.INPUT_DATA` with a message naming
   got-vs-needed lag count; confirm the FI adapter maps it to the intended
   SAP3 outcome (log-only skip in hindcast, fallback in operational).
4. **`nwp_rainfall_runoff`** (weather-only, no lags) is unaffected — confirm.

## Non-goals

- Changing the ridge model math or the lag count.
- The onboarding/hindcast window range (see Plan 094).

## Process

DRAFT until grill-me resolves the questions, then phases + JSON graph → READY.
Fix is model-side (`models/nwp_regression.py`); add a RED-confirmed test that an
insufficient-lag input returns `ModelFailure`, not raises.
