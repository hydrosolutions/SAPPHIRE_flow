# Plan 097 — SAP3 observability: warn when the delivered lookback is short

**Status**: DRAFT
**Priority**: low — observability/upstream complement to Plan 093; the model
guard already prevents the crash, this surfaces the *cause* earlier.
**Phase**: v0b — operational observability
**Parent**: Plan 093 (grill-me spun this out as a companion, 2026-07-03)
**Related**: `services/operational_inputs.py` (past-target/lag assembly),
`services/hindcast.py`, `models/nwp_regression.py` (`artifact.n_lags`), the FI
`PastKnownVariable.lookback` contract
**Created**: 2026-07-03

---

## Problem

Plan 093 makes `nwp_regression` return a typed `ModelFailure("insufficient lag
history: got 3, need 8")` instead of crashing. That fixes the symptom, but the
*root gap* — SAP3 delivered fewer past discharge rows than the model's declared
`lookback`/`n_lags` needs (a short observation archive) — is only visible **at
predict time, per cycle**, once the model fails. There is no earlier signal that
a station is structurally under-supplied for a model it is assigned.

## Goal

SAP3 emits an **early, explicit** warning when the past-target window it
assembles for a station/model is shorter than the model's declared `lookback`
(FI `PastKnownVariable.lookback`) — at input-assembly time, not (only) when
`predict` fails — so operators see "station X has only N of the M lag rows model
Y needs" without waiting for a failed forecast.

## Open design questions (grill-me before READY)

1. **Where to detect.** In `assemble_station_operational_inputs`
   (`services/operational_inputs.py`) after building `past_targets`, compare the
   clean row count against the model's `lookback_steps`; vs a dedicated
   onboarding-time / monitoring check. Prefer input-assembly (closest to the
   truth, every cycle).
2. **Model-declared requirement source.** Read the requirement from the model's
   `data_requirements.lookback_steps` (SAP3 side) — confirm it is populated for
   FI models via the adapter projection.
3. **Severity + channel.** A `structlog` WARNING (`operational_inputs.short_lookback`)
   only, vs also a station-status/monitoring signal (Flow 4 pipeline monitoring).
   Start with the log event; wire monitoring later.
4. **Noise control.** A permanently short archive (early in a station's life)
   would warn every cycle — do we warn once/threshold/until-N-days-accrued?
   (Ties into the Mac-mini run where lags fill in over ~7 days.)

## Non-goals

- The model-side guard itself (Plan 093 — done separately).
- Padding/synthesising missing lags (never; a short archive means a real
  data-availability limit).

## Process

DRAFT until a grill-me picks the detection point + noise policy, then phases →
READY. Small: a count-vs-lookback check + a `structlog` warning at input
assembly; test that a station with `< lookback` clean rows logs the event.
