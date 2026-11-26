from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.training_data import (
    aggregation_method_for,
    resolved_aggregation_methods,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import AggregationMethod, QcStatus
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.hindcast_inputs import (
    HistoricalAvailabilityPolicy,
    HistoricalSampling,
    HistoricalSourcePeriod,
    PreparedHindcastInputs,
)
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ObservationId, StationId
    from sapphire_flow.types.model import ModelDataRequirements
    from sapphire_flow.types.observation import Observation

_ZERO = timedelta(0)
_US = timedelta(microseconds=1)


def source_fetch_bounds(
    start: UtcDatetime,
    end: UtcDatetime,
    declarations: Sequence[HistoricalSourcePeriod],
) -> tuple[UtcDatetime, UtcDatetime]:
    """Conservative stored-label bounds; qualification still checks physical support."""
    if not declarations:
        return start, end
    starts = [start]
    ends = [end]
    for item in declarations:
        offset = (
            item.period.grid.step if item.period.label is PeriodLabel.END else _ZERO
        )
        shift = offset - item.stored_label_shift
        starts.append(
            ensure_utc(start + shift + min(_ZERO, item.input_support_displacement))
        )
        ends.append(
            ensure_utc(end + shift + max(_ZERO, item.input_support_displacement) + _US)
        )
    return min(starts), max(ends)


def aggregate_historical_periods(
    frame: pl.DataFrame,
    *,
    source: HistoricalSourcePeriod,
    target: ForecastPeriodDefinition,
    availability_policy: HistoricalAvailabilityPolicy,
    support_displacement: timedelta = _ZERO,
) -> pl.DataFrame:
    """Qualify complete native support before reducing or assigning model labels."""
    schema = {
        "timestamp": pl.Datetime("us", "UTC"),
        "value": pl.Float64,
        "available_at_us": pl.Int64,
    }
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    native = source.period
    shifted_grid = TimeGrid(
        step=native.grid.step,
        phase=(native.grid.phase - support_displacement) % native.grid.step,
    )
    if not shifted_grid.nests_into(target.grid):
        raise ConfigurationError(
            "historical source periods do not nest into the requested grid"
        )
    if support_displacement and (
        native.grid.step != target.grid.step or native.aggregation != target.aggregation
    ):
        raise ConfigurationError(
            "approximate input support requires equal period duration and aggregation"
        )
    if source.sampling is HistoricalSampling.SNAPSHOT:
        if target.aggregation not in {AggregationMethod.MEAN, AggregationMethod.MAX}:
            raise ConfigurationError(
                "snapshot samples support only declared mean or max reduction"
            )
    elif native.aggregation != target.aggregation:
        raise ConfigurationError("historical source and target aggregation differ")
    dtype = frame.schema.get("timestamp")
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise ConfigurationError(
            "historical source timestamps must be timezone-aware UTC"
        )
    if frame["timestamp"].null_count():
        raise ConfigurationError("historical source contains null timestamps")
    if "available_at_us" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("available_at_us")
        )
    elif frame.schema["available_at_us"] != pl.Int64:
        raise ConfigurationError(
            "historical availability must use integer UTC microseconds"
        )
    source_label = native.grid.step if native.label is PeriodLabel.END else _ZERO
    native_us = native.grid.step // _US
    target_us = target.grid.step // _US
    phase_us = target.grid.phase // _US
    physical = frame.select(
        (
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")).cast(pl.Int64)
            + (source.stored_label_shift - source_label) // _US
        ).alias("_start"),
        pl.col("value").cast(pl.Float64),
        pl.col("available_at_us"),
    )
    if physical["_start"].is_duplicated().any():
        raise ConfigurationError("historical source has duplicate native periods")
    if physical.filter(
        (pl.col("_start") - native.grid.phase // _US) % native_us != 0
    ).height:
        raise ConfigurationError(
            "historical source labels disagree with the declared native grid"
        )
    physical = physical.with_columns((pl.col("_start") + native_us).alias("_close"))
    available = pl.col("available_at_us")
    if (
        availability_policy is HistoricalAvailabilityPolicy.RETROSPECTIVE_CLOSURE
        and source.parameter == "discharge"
    ):
        available = available.fill_null(pl.col("_close"))
    physical = physical.with_columns(
        pl.when(available.is_not_null())
        .then(pl.max_horizontal(available, "_close"))
        .otherwise(None)
        .cast(pl.Int64)
        .alias("available_at_us"),
        (
            (
                (
                    (pl.col("_start") - support_displacement // _US - phase_us)
                    // target_us
                )
                * target_us
            )
            + phase_us
        ).alias("_bucket"),
    )
    reduction = {
        AggregationMethod.MEAN: pl.col("value").mean(),
        AggregationMethod.SUM: pl.col("value").sum(),
        AggregationMethod.MAX: pl.col("value").max(),
    }[target.aggregation]
    reduced = physical.group_by("_bucket").agg(
        reduction.alias("value"),
        pl.len().alias("_count"),
        pl.col("value").is_finite().fill_null(False).all().alias("_finite"),
        pl.col("available_at_us").is_not_null().all().alias("_known"),
        pl.col("available_at_us").max().alias("available_at_us"),
    )
    complete = (pl.col("_count") == target_us // native_us) & pl.col("_finite")
    label_offset = target_us if target.label is PeriodLabel.END else 0
    return reduced.select(
        (pl.col("_bucket") + label_offset)
        .cast(pl.Datetime("us", "UTC"))
        .alias("timestamp"),
        pl.when(complete).then("value").otherwise(None).alias("value"),
        pl.when(complete & pl.col("_known"))
        .then("available_at_us")
        .otherwise(None)
        .cast(pl.Int64)
        .alias("available_at_us"),
    ).sort("timestamp")


def prepare_hindcast_inputs(
    *,
    station_id: StationId,
    requirements: ModelDataRequirements,
    source_periods: tuple[HistoricalSourcePeriod, ...],
    availability_policy: HistoricalAvailabilityPolicy,
    observations: Sequence[Observation],
    forcing: Sequence[RawHistoricalForcing],
    start: UtcDatetime,
    end: UtcDatetime,
    static: pl.DataFrame | None,
    observed_availability: Mapping[ObservationId, UtcDatetime] | None = None,
) -> PreparedHindcastInputs:
    declared = requirements.time_grid
    if declared is None:
        raise ConfigurationError("period preparation requires a declared model grid")
    if not any(item.station_id == station_id for item in source_periods):
        raise ConfigurationError("station has no historical source declarations")
    catalog = {item.key: item for item in source_periods}
    if len(catalog) != len(source_periods):
        raise ConfigurationError("duplicate historical source declaration")
    methods = resolved_aggregation_methods(requirements)
    targets = requirements.required_past_targets
    if targets is None:
        targets = requirements.target_parameters
    weather = requirements.past_dynamic_features | requirements.future_dynamic_features
    rows: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in observations:
        if (
            item.station_id == station_id
            and item.parameter in targets
            and item.qc_status is QcStatus.QC_PASSED
        ):
            available = (observed_availability or {}).get(item.id)
            rows[item.source.value, item.parameter].append(
                {
                    "timestamp": item.timestamp,
                    "value": item.value,
                    "available_at_us": int(available.timestamp() * 1_000_000)
                    if available is not None
                    else None,
                }
            )
    for point in forcing:
        if point.station_id == station_id and point.parameter in weather:
            if point.band_id is not None or point.member_id is not None:
                raise ConfigurationError(
                    "historical period preparation requires deterministic basin inputs"
                )
            rows[point.source, point.parameter].append(
                {
                    "timestamp": point.valid_time,
                    "value": point.value,
                    "available_at_us": None,
                }
            )
    by_parameter: dict[str, list[pl.DataFrame]] = defaultdict(list)
    for (source_name, parameter), records in rows.items():
        declaration = catalog.get((station_id, source_name, parameter))
        if declaration is None:
            raise ConfigurationError(
                "historical period declaration missing for "
                f"{station_id}/{source_name}/{parameter}"
            )
        target = ForecastPeriodDefinition(
            grid=declared.grid,
            label=declared.label,
            aggregation=methods.get(parameter, aggregation_method_for(parameter)),
        )
        frame = pl.DataFrame(
            records,
            schema={
                "timestamp": pl.Datetime("us", "UTC"),
                "value": pl.Float64,
                "available_at_us": pl.Int64,
            },
        )
        by_parameter[parameter].append(
            aggregate_historical_periods(
                frame,
                source=declaration,
                target=target,
                availability_policy=availability_policy,
                support_displacement=declaration.input_support_displacement,
            )
        )
    label_offset = declared.grid.step if declared.label is PeriodLabel.END else _ZERO
    labels = pl.DataFrame(
        {
            "timestamp": pl.datetime_range(
                start + label_offset,
                end + label_offset,
                interval=declared.grid.step,
                closed="left",
                eager=True,
                time_unit="us",
            )
        }
    )

    def wide(parameters: frozenset[str], with_availability: bool) -> pl.DataFrame:
        result = labels
        for parameter in sorted(parameters):
            parts = by_parameter.get(parameter, [])
            if not parts:
                result = result.with_columns(
                    pl.lit(None, dtype=pl.Float64).alias(parameter)
                )
                if with_availability:
                    result = result.with_columns(
                        pl.lit(None, dtype=pl.Int64).alias(
                            f"available_at_us:{parameter}"
                        )
                    )
                continue
            combined = pl.concat(parts)
            if combined["timestamp"].is_duplicated().any():
                raise ConfigurationError(
                    f"overlapping historical sources for {parameter}"
                )
            selected = combined.select(
                "timestamp",
                pl.col("value").alias(parameter),
                *(
                    [pl.col("available_at_us").alias(f"available_at_us:{parameter}")]
                    if with_availability
                    else []
                ),
            )
            result = result.join(selected, on="timestamp", how="left")
        return result.sort("timestamp")

    return PreparedHindcastInputs(
        station_id=station_id,
        requirements=requirements,
        targets=wide(targets, True),
        forcing=wide(weather, False),
        static=static,
        source_periods=tuple(
            item for item in source_periods if item.station_id == station_id
        ),
        availability_policy=availability_policy,
    )
