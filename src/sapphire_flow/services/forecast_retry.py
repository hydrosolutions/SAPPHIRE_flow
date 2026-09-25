"""Plan 327 — classify a forecast cycle re-run against what is already stored.

The decision table (Plan 327 § D1) is evaluated IN ORDER, first match wins, so
the four outcomes are mutually exclusive by construction. Reference the ROW
NUMBER at every call site; describing a row in prose is how earlier versions of
this contract drifted apart.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import EnsembleRepresentation

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast


class ForecastRetryRow(Enum):
    """A row of Plan 327 § D1's decision table."""

    VALUES_DIFFER = 1
    ARTIFACT_DIFFERS = 2
    QC_VERDICT_DIFFERS = 3
    IDENTICAL = 4


#: Plan 328 T2 — the rows whose re-run SUPERSEDES the stored forecast and
#: writes the replacement.
SUPERSEDING_ROWS = (
    ForecastRetryRow.VALUES_DIFFER,
    ForecastRetryRow.ARTIFACT_DIFFERS,
)

#: ⛔ Row 3 stays refused permanently and is nobody's to replace. Widening it
#: means amending Plan 327's table first.
REFUSING_ROWS = (ForecastRetryRow.QC_VERDICT_DIFFERS,)


def classify_forecast_retry(
    *,
    stored: OperationalForecast,
    recomputed: OperationalForecast,
) -> ForecastRetryRow:
    """Classify ``recomputed`` against the ``stored`` forecast sharing its
    natural key ``(station_id, model_id, issued_at, parameter)``.

    ⛔ Evidence state is NOT a classifier (Plan 327 § D1). Every operational
    forecast carries an incomplete-evidence marker and a pre-``0057`` forecast
    has no evidence row at all, so consulting it would refuse every resume.

    ⛔ NOT compared, because they differ on every re-run by construction: the
    row id, ``created_at``/``updated_at``, ``version``, and the flow-run
    identity. Also not compared, because the table does not name them:
    ``nwp_cycle_reference_time``/``nwp_cycle_source``, the warm-up fields,
    ``observation_staleness_hours``, ``input_quality``, ``rating_curve_id`` and
    ``combination_strategy``/``source_model_ids``. Widening the comparison
    means amending the table in Plan 327, not editing this function.
    """
    # ROW 1 — the values differ, aligned by valid time and quantile/member.
    # `representation` and `units` are part of this comparison rather than a
    # row of their own: a members frame and a quantiles frame cannot be
    # aligned at all, and a bare number with a different unit is a different
    # value.
    if _value_key(stored) != _value_key(recomputed):
        return ForecastRetryRow.VALUES_DIFFER

    # ROW 2 — values equal, model artifact identity differs. The same numbers
    # from a different model version are not the same forecast, and this is
    # exactly what a re-run after a model repair produces.
    if stored.model_artifact_id != recomputed.model_artifact_id:
        return ForecastRetryRow.ARTIFACT_DIFFERS

    # ROW 3 — values and artifact equal, QC verdict differs. Equal values
    # should give equal QC unless the RULES changed, which is a real
    # difference nobody has decided how to treat. The VERDICT is the aggregate
    # status plus which rule (at which version) reached which status; the
    # free-text `detail` is deliberately excluded.
    if _qc_verdict(stored) != _qc_verdict(recomputed):
        return ForecastRetryRow.QC_VERDICT_DIFFERS

    # ROW 4 — otherwise: identical.
    return ForecastRetryRow.IDENTICAL


def describe_difference(
    row: ForecastRetryRow,
    *,
    stored: OperationalForecast,
    recomputed: OperationalForecast,
) -> str:
    """A short, specific statement of what differed, for the refusal message."""
    match row:
        case ForecastRetryRow.VALUES_DIFFER:
            return f"row 1: values differ — {_values_detail(stored, recomputed)}"
        case ForecastRetryRow.ARTIFACT_DIFFERS:
            return (
                f"row 2: model artifact identity differs "
                f"(stored {stored.model_artifact_id}, "
                f"recomputed {recomputed.model_artifact_id})"
            )
        case ForecastRetryRow.QC_VERDICT_DIFFERS:
            return (
                f"row 3: QC verdict differs "
                f"(stored {stored.qc_status.value} {_qc_verdict(stored)[1]}, "
                f"recomputed {recomputed.qc_status.value} "
                f"{_qc_verdict(recomputed)[1]})"
            )
        case ForecastRetryRow.IDENTICAL:
            return "row 4: identical"


def _values_detail(stored: OperationalForecast, recomputed: OperationalForecast) -> str:
    if stored.representation != recomputed.representation:
        return (
            f"representation {stored.representation.value} vs "
            f"{recomputed.representation.value}"
        )
    if stored.ensemble.units != recomputed.ensemble.units:
        return f"units {stored.ensemble.units} vs {recomputed.ensemble.units}"
    left = _value_key(stored)[2]
    right = _value_key(recomputed)[2]
    if len(left) != len(right):
        return f"{len(left)} stored rows vs {len(right)} recomputed"
    differing = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
    if not differing:  # pragma: no cover — unreachable when row 1 matched
        return "no aligned difference"
    (first_time, first_axis, _, first_value), (_, _, _, other_value) = differing[0]
    axis = "member" if stored.representation is EnsembleRepresentation.MEMBERS else "q"
    return (
        f"{len(differing)} of {len(left)} values, first at "
        f"valid_time={first_time} {axis}={first_axis}: "
        f"{first_value} vs {other_value}"
    )


def _qc_verdict(
    forecast: OperationalForecast,
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    return (
        forecast.qc_status.value,
        tuple(
            sorted(
                (flag.rule_id, flag.rule_version, flag.status.value)
                for flag in forecast.qc_flags
            )
        ),
    )


def _value_key(
    forecast: OperationalForecast,
) -> tuple[str, str, tuple[tuple[float, float, int, float], ...]]:
    ensemble = forecast.ensemble
    is_members = forecast.representation == EnsembleRepresentation.MEMBERS
    axis = "member_id" if is_members else "quantile"
    rows = ensemble.values.select("valid_time", axis, "value").iter_rows()
    # A NaN never equals itself, so an identical re-run of a forecast carrying
    # one would classify as ROW 1 and be refused forever. Carry the NaN-ness as
    # its own comparable flag instead; every other double is bit-identical
    # across the round trip (`forecast_values.value` is `double precision`).
    return (
        forecast.representation.value,
        ensemble.units,
        tuple(
            sorted(
                (
                    valid_time.timestamp(),
                    float(coordinate),
                    int(math.isnan(value)),
                    0.0 if math.isnan(value) else float(value),
                )
                for valid_time, coordinate, value in rows
            )
        ),
    )
