# Plan 093 — nwp_regression: return ModelFailure on insufficient lags (FI contract)

**Status**: DONE (implemented via PR #53, 2026-07-03 — the length guard + a
RED-confirmed test; plan-review workflow converged 2 rounds / 0 blockers-majors
and the grill-me resolved the two residual forks, both recorded below)
**Priority**: medium — surfaces as noisy `hindcast.step_failed` + the operational
`matmul` failure; not fatal (onboarding completes, artifact promotes), but the
model violates the ForecastInterface "return, don't raise" contract.

**Grill-me resolution (2026-07-03):** (1) **Scope = length-shortfall only** —
guard `len(lags) != artifact.n_lags`; a `KeyError` from a *missing* obs/discharge
key is **out of scope** (it cannot occur through the real pipeline — the FI
adapter always builds that entry — only via hand-built `ModelInputs`). (2) The
upstream "SAP3 warns when it under-delivers the lookback window" concern is a
**companion follow-up (Plan 097)**, not part of 093.
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

The ForecastInterface is explicit (`docs/model_interface.md:90`): `predict`
**returns `ModelResult` rather than raising**; *anticipated* failure
(degraded/insufficient inputs) "must be **returned, not raised**." SAP3's
except-and-return is only a **backstop for unanticipated bugs**. Insufficient lag
history is an *anticipated* input-data condition, so the model must return
`ModelFailure(cause=FailureCause.INPUT_DATA, message=…)` — not throw.

`max_nan` does **not** cover this: it is a SAP3 **pre-predict gate for NaN
tolerance** (`docs/input_requirement.md:100`); the matmul case is a **shape /
length** shortfall (missing lag *rows*, not NaNs), so the model owns it.

## Goal

`nwp_regression` never raises on insufficient/misaligned lag data — it validates
the feature dimension against the trained artifact **once, before the step loop**
and returns a typed `ModelFailure(cause=FailureCause.INPUT_DATA)`.

**What the fix actually delivers, per call site** (both drive the model through
`ForecastInterfaceAdapter.predict`, whose `_output_from_result` re-raises
`ModelFailure` as `ModelOutputError` — `adapters/forecast_interface.py:369-373`;
so `ModelFailure` is *not* a clean bypass, it becomes a **typed, informative**
`ModelOutputError` upstream):

- **Operational forecast**: `run_station_forecast.py:201-208` wraps the
  `model.predict` call in `except Exception` and returns a graceful fallback
  reason string (`f"predict failed: {exc}"`). So the `ModelOutputError` is caught
  here and the station skips gracefully instead of aborting — the improvement is
  a **meaningful message** ("insufficient lag history: got 3, need 8") in place
  of the opaque `matmul` `ValueError`. (Caller verified; the earlier "aborts the
  cycle" concern does not apply — this service swallows all `predict` exceptions.)
- **Hindcast**: `hindcast.py:354` calls the same `model.predict`; the resulting
  `ModelOutputError` is caught by the generic `except Exception` at
  `hindcast.py:381` and logged as `hindcast.step_failed`. The step **still
  fails** (same outcome as today's matmul crash) — the win is purely that the log
  line is now actionable (typed cause + got-vs-needed counts) instead of an
  opaque numpy shape error.

## Resolved design decisions (were open questions; now closed)

1. **Granularity — `ModelFailure`, not a per-station `FAILURE` entry.**
   For a single-station model this is unambiguous. A per-station
   `VariableStatus.FAILURE` `VariableOutput` is **not** a viable peer: the FI
   adapter's `_ensembles_from_station_variables`
   (`adapters/forecast_interface.py:405-406`) silently `continue`s past `FAILURE`
   variables, producing empty ensembles, which then trips the empty-output guard
   downstream — so it collapses to the *same* `ModelOutputError` with **worse**
   diagnostics (no cause taxonomy, no got-vs-needed message) plus wasted
   `VariableOutput` construction. `ModelFailure(cause=FailureCause.INPUT_DATA)` is
   the correct and only answer for a total-inability, single-station condition.
2. **Guard is unconditional; the SAP3-delivery question does not gate it.**
   `_initial_lags` (`nwp_regression.py:264`) uses `values[-self._n_lags:]`, which
   Python **silently truncates** when `len(values) < n_lags` (negative-index
   over-slicing never raises). So even if SAP3 delivers the full `lookback`
   window, a short observation archive still yields a short lag vector. The guard
   is therefore required in **all** cases and is independent of any SAP3 delivery
   guarantee. Whether SAP3 should pad or fail upstream is a **separate
   observability concern** (tracked below), not a prerequisite for this fix.
3. **Cause/message taxonomy.** `FailureCause.INPUT_DATA` with a message naming
   got-vs-needed lag count (see the exact constructor call in Implementation).
4. **`nwp_rainfall_runoff`** (weather-only, no lags: `_n_lags == 0`, so
   `_initial_lags` returns `np.empty(0)` — `nwp_regression.py:260-261`) is
   unaffected; the guard is a no-op when `n_lags == 0`.

## Implementation (single guard in `predict`)

There is **no `hindcast()` method** on `NwpRegression` — it does not implement
`BatchHindcastModel`. The hindcast service drives the model by calling `predict`
in a per-step loop (`hindcast.py:354`), and the operational service does the same
(`run_station_forecast.py:195`). **One guard in `predict` covers every caller**;
do not search for or add a separate `hindcast()` override.

1. **Add imports.** `ModelFailure` and `FailureCause` are **not** currently
   imported in `nwp_regression.py` (the `from forecast_interface import (...)`
   block at lines 28-48 pulls in `ModelResult`, `ModelSuccess`, etc. — but not
   these two). Add both to that block, or the new return site raises `NameError`.
2. **Guard after `_initial_lags`, before the step loop.** In `predict`
   (`nwp_regression.py:216`), after `lags = self._initial_lags(dynamic)`, compare
   against the **artifact's** trained lag count — `artifact.n_lags`, the
   source of truth set at training time — **not** `self._n_lags` (the class-level
   constant); they should be equal, but `artifact.n_lags` catches an
   artifact/class mismatch too. `artifact` is already in scope (parameter at
   `nwp_regression.py:199`):

   ```python
   if len(lags) != artifact.n_lags:
       log.warning(
           "nwp_regression.insufficient_lags",
           got=len(lags),
           need=artifact.n_lags,
       )
       return ModelFailure(
           model_name=self._model_name,
           issue_datetime=issue_datetime,
           cause=FailureCause.INPUT_DATA,
           message=(
               f"insufficient lag history: got {len(lags)}, "
               f"need {artifact.n_lags}"
           ),
       )
   ```

   **Both `model_name` and `issue_datetime` are REQUIRED, non-nullable fields on
   `ModelFailure`** (`forecast_interface/interface/result.py:18-23`; `model_name`
   also has a non-empty validator). Omitting either raises a Pydantic
   `ValidationError` at runtime — which would just replace the matmul crash with a
   different crash. Both are in scope: `issue_datetime` is a `predict` parameter
   (`nwp_regression.py:202`) and `self._model_name` exists on `_NwpRegressionBase`.
   The `log.warning` line doubles as the observability signal for the SAP3-delivery
   question (records got-vs-needed at the model boundary).

## Non-goals

- Changing the ridge model math or the lag count.
- The onboarding/hindcast window range (see Plan 094).
- **Adding a `BatchHindcastModel.hindcast()` method to `NwpRegression`** — out of
  scope. If one is ever added, the same lag-length guard must be replicated there
  (the guard lives in `predict` today because that is the only prediction entry
  point).
- **Making SAP3 pad/fail upstream on under-delivered lookback** — a separate
  observability/upstream concern → **Plan 097** (companion); the model guard
  stands regardless.
- **Handling a missing obs/discharge KEY (`KeyError` in `_initial_lags`)** —
  out of scope (grill-me decision): the guard covers the length shortfall, the
  only failure mode reachable through the FI adapter. A `KeyError` requires a
  hand-built `ModelInputs`, not the real pipeline.

## Process

DRAFT → self-review → user confirms → READY. The four design questions above are
now resolved (kept as documented decisions, not open items). Fix is model-side
(`models/nwp_regression.py`); add a RED-confirmed test that an insufficient-lag
input returns `ModelFailure` (not raises). The RED fixture must pass an
**artifact whose `n_lags` exceeds the number of lag rows supplied** (e.g.
`artifact.n_lags = 7` but only 3 discharge history rows), so the test exercises
the `len(lags) != artifact.n_lags` branch rather than a class-level short
`_n_lags`. Assert `isinstance(result, ModelFailure)`, `result.cause is
FailureCause.INPUT_DATA`, and the got/need counts appear in `result.message`.
Also assert `result.model_name == "nwp_regression"` and
`result.issue_datetime == <the input issue_datetime>` — these exercise the
Pydantic `ModelFailure` field validators (`forecast_interface/interface/result.py:25-29`),
so a wrongly-empty `model_name` raises `ValidationError` (a new crash) instead of
passing silently. The test must import `ModelFailure` + `FailureCause` (add to
the existing `from forecast_interface import (...)` line, or use
`fi_boundary.ModelFailure` / `fi_boundary.FailureCause`).
