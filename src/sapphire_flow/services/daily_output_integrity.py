from __future__ import annotations

import math
from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from sapphire_flow.types.daily_output import DailyGridAnchor, DailyOutputEligibility
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast_reference import ForecastOutputKey

_PLAUSIBILITY = frozenset({"negative_value", "range_check"})


def assess_daily_output(
    ensemble: ForecastEnsemble,
    key: ForecastOutputKey,
    contract: DailyOutputContract,
    qc_status: QcStatus,
    qc_flags: tuple[QcFlag, ...],
) -> DailyOutputEligibility:
    """Evaluation-only classification; never changes ordinary QC or publication."""
    if not _matches_contract(ensemble, key, contract):
        return DailyOutputEligibility.INTEGRITY_REJECTED
    if any(
        flag.rule_id == "quantile_crossing" and flag.status is not QcStatus.QC_PASSED
        for flag in qc_flags
    ):
        return DailyOutputEligibility.INTEGRITY_REJECTED
    failed = {flag.rule_id for flag in qc_flags if flag.status is QcStatus.QC_FAILED}
    if failed - _PLAUSIBILITY:
        return DailyOutputEligibility.INTEGRITY_REJECTED
    if qc_status not in (QcStatus.QC_PASSED, QcStatus.QC_SUSPECT, QcStatus.QC_FAILED):
        return DailyOutputEligibility.QC_UNASSESSED
    if (qc_status is QcStatus.QC_FAILED) != bool(failed):
        return DailyOutputEligibility.QC_UNASSESSED
    if failed:
        return DailyOutputEligibility.PLAUSIBILITY_FLAGGED
    return DailyOutputEligibility.USABLE


def _matches_contract(
    ensemble: ForecastEnsemble, key: ForecastOutputKey, contract: DailyOutputContract
) -> bool:
    if (
        ensemble.station_id != key.station_id
        or ensemble.model_id != key.model_id
        or ensemble.issued_at != key.issued_at
        or ensemble.parameter != key.parameter
        or ensemble.parameter != contract.parameter
        or ensemble.units != contract.units
        or ensemble.representation is not contract.representation
        or ensemble.time_step != contract.time_step
        or not (
            (contract.minimum_horizon_steps or contract.horizon_steps)
            <= ensemble.forecast_horizon_steps
            <= contract.horizon_steps
        )
    ):
        return False
    series_col = (
        "member_id"
        if contract.representation is EnsembleRepresentation.MEMBERS
        else "quantile"
    )
    frame = ensemble.values
    if not {"valid_time", series_col, "value"} <= set(frame.columns):
        return False
    if frame.height != ensemble.forecast_horizon_steps * len(contract.series_keys):
        return False
    anchor = key.issued_at
    if contract.grid_anchor is DailyGridAnchor.NEXT_UTC_MIDNIGHT:
        anchor = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        if anchor < key.issued_at:
            anchor += timedelta(days=1)
    expected_times = {
        anchor + contract.first_lead + step * contract.time_step
        for step in range(ensemble.forecast_horizon_steps)
    }
    expected_keys = set(contract.series_keys)
    cells: dict[tuple[datetime, float], float] = {}
    for valid_time, series, value in frame.select(
        "valid_time", series_col, "value"
    ).iter_rows():
        if not isinstance(valid_time, datetime) or valid_time.utcoffset() != timedelta(
            0
        ):
            return False
        if (
            not isinstance(series, (int, float))
            or isinstance(series, bool)
            or series not in expected_keys
        ):
            return False
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            return False
        cell = (valid_time, float(series))
        if valid_time not in expected_times or cell in cells:
            return False
        cells[cell] = float(value)
    if contract.representation is EnsembleRepresentation.QUANTILES:
        levels = sorted(expected_keys)
        return all(
            cells[(timestamp, lower)] <= cells[(timestamp, upper)]
            for timestamp in expected_times
            for lower, upper in pairwise(levels)
        )
    return True
