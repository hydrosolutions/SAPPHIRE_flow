from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import polars as pl

from sapphire_flow.exceptions import ConfigurationError, InsufficientDataError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.rating_input_lineage import (
    RatingDaySupport,
    RatingInputLineage,
    RatingPeriodInputLineage,
    RatingPeriodSupport,
    RatingSampleSupport,
    RatingUnreconciledSource,
)
from sapphire_flow.types.rating_reconciliation import (
    RatingEvidenceError,
    RatingHeldSource,
    RatingHoldReason,
    RatingInputsUnavailable,
    RatingWindowRead,
)
from sapphire_flow.types.time_grid import PeriodLabel

if TYPE_CHECKING:
    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


class RatingCurrentReader(Protocol):
    def read_current(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]: ...


@runtime_checkable
class RatingCurrentWindowReader(Protocol):
    def read_current_window(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> RatingWindowRead: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeRatingInputs:
    reader: RatingCurrentReader
    bindings: tuple[NativeRatingBinding, ...]
    dhm: DhmConfig

    def period_start(
        self,
        station_id: StationId,
        model_id: ModelId,
        *,
        start: UtcDatetime,
        issue: UtcDatetime,
        period: ForecastPeriodDefinition,
    ) -> UtcDatetime:
        matches = [
            b
            for b in self.bindings
            if b.station_id == station_id and model_id in b.model_ids
        ]
        if len(matches) != 1:
            raise InsufficientDataError(
                "station/model lacks unique provisional rating opt-in"
            )
        lower = max(start, issue - timedelta(days=matches[0].reconciliation_days))
        aligned = period.grid.floor(ensure_utc(lower))
        return ensure_utc(aligned + period.grid.step) if aligned < lower else aligned

    def read(
        self,
        station_id: StationId,
        model_id: ModelId,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
        return self.read_window(
            station_id, model_id, start=start, end=end, at=at
        ).records

    def read_window(
        self,
        station_id: StationId,
        model_id: ModelId,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> RatingWindowRead:
        matches = [
            b
            for b in self.bindings
            if b.station_id == station_id and model_id in b.model_ids
        ]
        if len(matches) != 1:
            raise InsufficientDataError(
                "station/model lacks unique provisional rating opt-in"
            )
        binding = matches[0]
        if end - start > timedelta(days=binding.reconciliation_days):
            raise ConfigurationError(
                "rating reconciliation window is shorter than native model lookback"
            )
        result: list[RatingDerivedDischarge] = []
        held: list[RatingHeldSource] = []
        source_count = 0
        cursor = start
        while cursor < end:
            stop = ensure_utc(min(end, cursor + timedelta(hours=6)))
            try:
                if isinstance(self.reader, RatingCurrentWindowReader):
                    window = self.reader.read_current_window(
                        binding,
                        self.dhm,
                        start=cursor,
                        end=stop,
                        at=at,
                    )
                    hard = tuple(
                        source
                        for source in window.held
                        if source.reason is not RatingHoldReason.UNRECONCILED
                    )
                    if hard:
                        raise RatingInputsUnavailable(hard)
                    result.extend(window.records)
                    held.extend(window.held)
                    source_count += window.source_count
                else:
                    records = self.reader.read_current(
                        binding,
                        self.dhm,
                        start=cursor,
                        end=stop,
                        at=at,
                    )
                    result.extend(records)
                    source_count += len(records)
            except ValueError as exc:
                raise RatingInputsUnavailable(
                    (
                        RatingHeldSource(
                            observation_id=None,
                            source_version_sha256=None,
                            reason=exc.reason
                            if isinstance(exc, RatingEvidenceError)
                            else RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                        ),
                    )
                ) from None
            cursor = stop
        return RatingWindowRead(
            start=start,
            end=end,
            records=tuple(result),
            held=tuple(held),
            source_count=source_count,
        )


def merge_rating_days(
    measured: pl.DataFrame,
    samples: tuple[RatingDerivedDischarge, ...],
    *,
    issue_time: UtcDatetime,
    weather_cycle_time: UtcDatetime,
    unreconciled_sources: tuple[RatingUnreconciledSource, ...] = (),
    period: ForecastPeriodDefinition | None = None,
) -> tuple[pl.DataFrame, ForecastInputLineage | None, UtcDatetime | None]:
    if period is not None and (
        period.grid.step != timedelta(days=1)
        or period.aggregation not in (AggregationMethod.MEAN, AggregationMethod.MAX)
    ):
        raise ConfigurationError(
            "native rating points require daily mean or max aggregation"
        )
    cutoff = (
        period.grid.floor(issue_time)
        if period is not None
        else issue_time.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    label_offset = (
        period.grid.step
        if period is not None and period.label is PeriodLabel.END
        else timedelta(0)
    )
    if any(source.timestamp >= cutoff for source in unreconciled_sources):
        raise ValueError("rating omissions must precede the current UTC day")
    availability = "available_at_us:discharge"
    if period is not None and "discharge" in measured.columns:
        epoch = ensure_utc(datetime.fromisoformat("1970-01-01T00:00:00+00:00"))
        issue_us = (issue_time - epoch) // timedelta(microseconds=1)
        if availability not in measured.columns:
            measured = measured.with_columns(
                pl.lit(None, dtype=pl.Int64).alias(availability)
            )
        measured = measured.with_columns(
            pl.when(
                pl.col(availability).is_not_null() & (pl.col(availability) <= issue_us)
            )
            .then(pl.col("discharge"))
            .otherwise(None)
            .alias("discharge")
        )
    if "discharge" in measured.columns:
        measured = measured.with_columns(
            pl.when(pl.col("discharge").is_finite())
            .then(pl.col("discharge"))
            .otherwise(None)
            .alias("discharge")
        )
    buckets: dict[UtcDatetime, list[RatingDerivedDischarge]] = defaultdict(list)
    identities: dict[object, RatingDerivedDischarge] = {}
    natural: dict[tuple[object, UtcDatetime], RatingDerivedDischarge] = {}
    for sample in samples:
        if sample.timestamp >= cutoff:
            raise ValueError("rating inputs must precede the current UTC day")
        previous = identities.setdefault(sample.observation_id, sample)
        duplicate = natural.setdefault((sample.station_id, sample.timestamp), sample)
        if previous != sample or duplicate != sample:
            raise ValueError("conflicting derived source identity or natural key")
    for sample in identities.values():
        if period is not None and (
            sample.recorded_at is None or sample.recorded_at > issue_time
        ):
            continue
        day = (
            period.grid.floor(sample.timestamp)
            if period is not None
            else ensure_utc(
                sample.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            )
        )
        buckets[day].append(sample)
    measured_days = {
        row["timestamp"]
        for row in measured.iter_rows(named=True)
        if isinstance(row.get("discharge"), (int, float))
        and math.isfinite(row["discharge"])
    }
    additions: list[dict[str, object]] = []
    legacy_support: list[RatingDaySupport] = []
    period_support: list[RatingPeriodSupport] = []
    latest = None
    policies: set[str] = set()
    for day, rows in sorted(buckets.items()):
        label = day + label_offset
        if label in measured_days:
            continue
        accepted = [row for row in rows if row.discharge is not None]
        if not accepted:
            continue
        policies.update(json.loads(row.content)["policy"] for row in rows)
        value = math.fsum(
            row.discharge / len(accepted)
            for row in accepted
            if row.discharge is not None
        )
        if period is not None and period.aggregation is AggregationMethod.MAX:
            value = max(row.discharge for row in accepted if row.discharge is not None)
        if not math.isfinite(value):
            raise ValueError("nonfinite rating daily aggregate")
        warnings = {warning for row in rows for warning in row.warnings}
        # No coverage claim is inferred from sparse readings or unknown cadence.
        warnings.add("partial_daily_support")
        warnings.add("accepted_measured_discharge_absent")
        if any(
            day <= source.timestamp < day + timedelta(days=1)
            for source in unreconciled_sources
        ):
            warnings.add("rating_unreconciled_sources_omitted")
        for row in rows:
            valid_to = json.loads(row.content)["curve"]["valid_to"]
            if valid_to is not None and datetime.fromisoformat(valid_to) <= issue_time:
                warnings.add("rating_expired_at_issue")
        times = sorted(row.timestamp for row in accepted)
        latest = max(latest, times[-1]) if latest else times[-1]
        unassessed = sum("qc_unassessed" in row.warnings for row in accepted)
        available_at = max(
            [
                day + timedelta(days=1),
                *(row.recorded_at for row in rows if row.recorded_at is not None),
            ]
        )
        support = RatingSampleSupport(
            day=day,
            fingerprints=tuple(sorted(row.fingerprint for row in rows)),
            accepted=len(accepted),
            rejected=len(rows) - len(accepted),
            extrapolated=sum(
                any(w.startswith("extrapolated_") for w in row.warnings)
                for row in accepted
            ),
            assessed=len(accepted) - unassessed,
            unassessed=unassessed,
            first=times[0],
            last=times[-1],
            warnings=tuple(sorted(warnings)),
        )
        if period is None:
            legacy_support.append(RatingDaySupport(**asdict(support)))
        else:
            period_support.append(
                RatingPeriodSupport(**asdict(support), available_at=available_at)
            )
        addition: dict[str, object] = {"timestamp": label, "discharge": value}
        if period is not None:
            epoch = ensure_utc(datetime.fromisoformat("1970-01-01T00:00:00+00:00"))
            addition["available_at_us:discharge"] = (available_at - epoch) // timedelta(
                microseconds=1
            )
        additions.append(addition)
    if not additions:
        return measured, None, None
    derived = pl.DataFrame(additions)
    if "timestamp" not in measured.columns:
        merged = derived
    else:
        renamed = {"discharge": "_rating_q"}
        if period is not None:
            renamed[availability] = "_rating_available"
        merged = measured.join(
            derived.rename(renamed),
            on="timestamp",
            how="full",
            coalesce=True,
        )
        if "discharge" not in merged.columns:
            merged = merged.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("discharge")
            )
        if period is not None:
            if availability not in merged.columns:
                merged = merged.with_columns(
                    pl.lit(None, dtype=pl.Int64).alias(availability)
                )
            merged = merged.with_columns(
                pl.when(pl.col("discharge").is_not_null())
                .then(pl.col(availability))
                .otherwise(pl.col("_rating_available"))
                .cast(pl.Int64)
                .alias(availability)
            ).drop("_rating_available")
        merged = merged.with_columns(
            pl.coalesce("discharge", "_rating_q").alias("discharge")
        ).drop("_rating_q")
    omissions = tuple(
        source
        for source in unreconciled_sources
        if (
            period.grid.floor(source.timestamp) + label_offset
            if period is not None
            else source.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        not in measured_days
    )
    rating_lineage = (
        RatingInputLineage(
            issue_time=issue_time,
            weather_cycle_time=weather_cycle_time,
            days=tuple(legacy_support),
            unreconciled_sources=omissions,
        )
        if period is None
        else RatingPeriodInputLineage(
            issue_time=issue_time,
            weather_cycle_time=weather_cycle_time,
            days=tuple(period_support),
            unreconciled_sources=omissions,
            period_definition=period,
        )
    )
    lineage = ForecastInputLineage(
        transformation_versions=tuple(sorted(policies)),
        rating_input=rating_lineage,
    )
    return merged.sort("timestamp"), lineage, latest
