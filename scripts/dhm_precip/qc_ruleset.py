"""Task 1a (Plan 173, M-A3) — the in-code precipitation `QcRuleSet` (OD-3).

Two `frozen_sensor` instances (D3) — `QcRuleSet.rules_for` filters by
parameter and time step only, never deduplicating by `rule_id`
(`sapphire_flow/types/domain.py:163`), so one rule set legitimately holds
both, distinguished by `rule_version`:

- **stuck-value**: `exclude_at_or_below = stuck_high_min_value_mm` (5.0) so a
  value at or below it never starts or extends a run — this is what lets
  ordinary dry spells pass while still catching a saturated sensor pinned
  well above typical rainfall (Sindhuli Madhi's ~72 mm block). Whole series,
  `minimum_run_duration_hours` (12h).
- **long-zero-run**: no exclusion floor — every value is eligible, so this is
  really "any long flat run" — tight (near-exact-equality) tolerance,
  `qc_mask_long_zero_run_min_consecutive_hours` (7 days, measured D3). Scoped
  to JJAS OUTSIDE the checker (D3b) — `_apply_frozen_sensor` has no season
  parameter — see `scripts/dhm_precip/qc_mask.py`.

Plus `range_check` at `0.0 / 200.0` mm/h (D4) — a physical-impossibility
gate, not an outlier filter.

This is config/qc_rules.py's precipitation daily-historical entry's research
sibling, built here instead of there because M-I4 (config rows) is gated —
OD-3 exercises the rules directly, in code, against real data.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.domain import QcRuleParams, QcRuleSet

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams

PRECIPITATION_PARAMETER = "precipitation"
QC_MASK_TIME_STEP = timedelta(seconds=3600)

RANGE_CHECK_RULE_VERSION = "dhm-precip-range-check-1.0"
STUCK_VALUE_RULE_VERSION = "dhm-precip-stuck-value-1.0"
LONG_ZERO_RUN_RULE_VERSION = "dhm-precip-long-zero-run-1.0"

PASS_A_RULE_VERSIONS: frozenset[str] = frozenset(
    {RANGE_CHECK_RULE_VERSION, STUCK_VALUE_RULE_VERSION}
)
"""D3c pass A — range_check + the stuck-value `frozen_sensor` instance,
applied over a station's whole series."""
PASS_B_RULE_VERSIONS: frozenset[str] = frozenset({LONG_ZERO_RUN_RULE_VERSION})
"""D3c pass B — the long-zero-run `frozen_sensor` instance only, applied one
JJAS season at a time (D3b)."""


def _range_check_rule(params: DhmPrecipParams) -> QcRuleParams:
    return QcRuleParams(
        rule_id="range_check",
        rule_version=RANGE_CHECK_RULE_VERSION,
        parameter=PRECIPITATION_PARAMETER,
        time_step=QC_MASK_TIME_STEP,
        thresholds={
            "value_min": params.qc_mask_range_check_value_min_mm,
            "value_max": params.qc_mask_range_check_value_max_mm,
        },
    )


def _stuck_value_rule(params: DhmPrecipParams) -> QcRuleParams:
    return QcRuleParams(
        rule_id="frozen_sensor",
        rule_version=STUCK_VALUE_RULE_VERSION,
        parameter=PRECIPITATION_PARAMETER,
        time_step=QC_MASK_TIME_STEP,
        thresholds={
            "tolerance": params.stuck_high_tolerance_mm,
            "min_consecutive": float(params.minimum_run_duration_hours),
            "exclude_at_or_below": params.stuck_high_min_value_mm,
        },
    )


def _long_zero_run_rule(params: DhmPrecipParams) -> QcRuleParams:
    return QcRuleParams(
        rule_id="frozen_sensor",
        rule_version=LONG_ZERO_RUN_RULE_VERSION,
        parameter=PRECIPITATION_PARAMETER,
        time_step=QC_MASK_TIME_STEP,
        thresholds={
            "tolerance": params.zero_run_tolerance_mm,
            "min_consecutive": float(
                params.qc_mask_long_zero_run_min_consecutive_hours
            ),
        },
    )


def build_precipitation_qc_rule_set(params: DhmPrecipParams) -> QcRuleSet:
    """The full in-code precipitation rule set — `range_check` plus BOTH
    `frozen_sensor` instances. Used directly for characterisation (1a) and
    as the single source `qc_mask.py` filters by `rule_version` into its two
    passes (D3c) — never redeclared twice."""
    return QcRuleSet(
        version="dhm-precip-qc-mask-1.0",
        rules=(
            _range_check_rule(params),
            _stuck_value_rule(params),
            _long_zero_run_rule(params),
        ),
    )


def rule_subset(rule_set: QcRuleSet, rule_versions: frozenset[str]) -> QcRuleSet:
    """D3c — a pass-specific rule set, filtered by `rule_version` (never by
    `skipped_rule_ids`, which cannot distinguish two `frozen_sensor`
    instances sharing one `rule_id` — `services/qc.py:256`)."""
    return QcRuleSet(
        version=rule_set.version,
        rules=tuple(r for r in rule_set.rules if r.rule_version in rule_versions),
    )
