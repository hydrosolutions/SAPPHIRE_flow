---
status: DRAFT
created: 2026-03-30
scope: investigation + design — forecast QC insertion in Flow 1, fallback behavior, alert suppression
depends_on: []  # blocks Phase 8 (Flow 1 implementation)
---

# 012 — Forecast QC Integration in Flow 1

## Problem

The `ForecastOutputQualityChecker` service is fully implemented
(`services/forecast_qc.py`) with 7 rules (negative_value, range_check, flat_ensemble,
ensemble_spread, climatology_outlier, temporal_consistency, quantile_crossing). Types
are complete (`ForecastQcRuleSet`, `QcFlag`, `QcStatus`). DB schema supports it.

**The gap:** There is **no step in Flow 1** (operational forecast) that invokes the
checker. The architecture shows: model output (1.8) → post-process (1.9) → store (1.10)
→ alert thresholds (1.11). Forecast plausibility checking is missing between model
output and storage/alerting.

## Investigation Tasks

1. Confirm the service is not called anywhere in the operational flow code.
2. Determine the correct insertion point in Flow 1 — likely between steps 1.8 and 1.9
   (or between 1.9 and 1.10 if post-processing should happen before QC).
3. Design the fallback behavior when `SanityCheckFailure` is raised — the exception
   is defined but never caught. Architecture mentions "try fallback model" but this
   isn't implemented.
4. Decide whether hindcast flow (Flow 7) should also apply forecast QC (for
   consistency / flag propagation to skill scores).
5. Check if QC-failed forecasts should suppress alerts (likely yes — don't alert on
   implausible forecasts).
6. Document the integration in `architecture-context.md` as an explicit step.

## Urgency

Blocks Phase 8 (Flow 1 implementation). Must be resolved before the forecast cycle
is wired up.

## Origin

Extracted from plan 011 §C.
