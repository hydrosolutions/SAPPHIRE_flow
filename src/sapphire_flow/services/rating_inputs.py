from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import polars as pl

from sapphire_flow.exceptions import ConfigurationError, InsufficientDataError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.rating_input_lineage import (
    RatingDaySupport,
    RatingInputLineage,
)

if TYPE_CHECKING:
    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.datetime import UtcDatetime
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


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeRatingInputs:
    reader: RatingCurrentReader
    bindings: tuple[NativeRatingBinding, ...]
    dhm: DhmConfig

    def read(
        self,
        station_id: StationId,
        model_id: ModelId,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
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
        cursor = start
        while cursor < end:
            stop = ensure_utc(min(end, cursor + timedelta(hours=6)))
            try:
                result.extend(
                    self.reader.read_current(
                        binding, self.dhm, start=cursor, end=stop, at=at
                    )
                )
            except ValueError as exc:
                raise InsufficientDataError(f"rating source held: {exc}") from exc
            cursor = stop
        return tuple(result)


def merge_rating_days(
    measured: pl.DataFrame,
    samples: tuple[RatingDerivedDischarge, ...],
    *,
    issue_time: UtcDatetime,
    weather_cycle_time: UtcDatetime,
) -> tuple[pl.DataFrame, ForecastInputLineage | None, UtcDatetime | None]:
    cutoff = issue_time.replace(hour=0, minute=0, second=0, microsecond=0)
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
        day = ensure_utc(
            sample.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        buckets[day].append(sample)
    measured_days = {
        row["timestamp"]
        for row in measured.iter_rows(named=True)
        if isinstance(row.get("discharge"), (int, float))
        and math.isfinite(row["discharge"])
    }
    additions: list[dict[str, object]] = []
    support: list[RatingDaySupport] = []
    latest = None
    for day, rows in sorted(buckets.items()):
        if day in measured_days:
            continue
        accepted = [row for row in rows if row.discharge is not None]
        if not accepted:
            continue
        value = math.fsum(
            row.discharge / len(accepted)
            for row in accepted
            if row.discharge is not None
        )
        if not math.isfinite(value):
            raise ValueError("nonfinite rating daily aggregate")
        warnings = {warning for row in rows for warning in row.warnings}
        # No coverage claim is inferred from sparse readings or unknown cadence.
        warnings.add("partial_daily_support")
        warnings.add("accepted_measured_discharge_absent")
        for row in rows:
            valid_to = json.loads(row.content)["curve"]["valid_to"]
            if valid_to is not None and datetime.fromisoformat(valid_to) <= issue_time:
                warnings.add("rating_expired_at_issue")
        times = sorted(row.timestamp for row in accepted)
        latest = max(latest, times[-1]) if latest else times[-1]
        unassessed = sum("qc_unassessed" in row.warnings for row in accepted)
        support.append(
            RatingDaySupport(
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
        )
        additions.append({"timestamp": day, "discharge": value})
    if not additions:
        return measured, None, None
    derived = pl.DataFrame(additions)
    if "timestamp" not in measured.columns:
        merged = derived
    else:
        merged = measured.join(
            derived.rename({"discharge": "_rating_q"}),
            on="timestamp",
            how="full",
            coalesce=True,
        )
        if "discharge" not in merged.columns:
            merged = merged.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("discharge")
            )
        merged = merged.with_columns(
            pl.coalesce("discharge", "_rating_q").alias("discharge")
        ).drop("_rating_q")
    lineage = ForecastInputLineage(
        transformation_versions=("native-rating-linear-endpoints-v1",),
        rating_input=RatingInputLineage(
            issue_time=issue_time,
            weather_cycle_time=weather_cycle_time,
            days=tuple(support),
        ),
    )
    return merged.sort("timestamp"), lineage, latest
