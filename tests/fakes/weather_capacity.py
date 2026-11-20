from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.services.operational_weather_projection import future_receipt_scope
from sapphire_flow.services.operational_weather_proof import prove_future_receipt
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model import GroupModelInputs
from sapphire_flow.types.operational_weather import (
    QUALIFIED_SOURCE_REVISION,
    FutureWeatherAttempt,
    FutureWeatherRequest,
    FutureWeatherRequirement,
    FutureWeatherUse,
    OperationalWeatherDay,
    OperationalWeatherSample,
    OperationalWeatherSource,
    OperationalWeatherWindow,
    PastWeatherRequirement,
    PastWeatherUse,
    WeatherCacheAttempt,
    WeatherCacheKey,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherParameter,
    WeatherProductIdentity,
    WeatherUse,
)
from tests.fakes.operational_weather import FINGERPRINT, ISSUE
from tests.unit.services.test_recap_radiation import radiation_rows

PARAMETERS: tuple[WeatherParameter, ...] = (
    "precipitation",
    "temperature",
    "solar_net_radiation",
    "thermal_net_radiation",
)


def cmal_capacity_inputs(
    *, days: int = 380, station_count: int = 6
) -> GroupModelInputs:
    start = ensure_utc(ISSUE - timedelta(days=days))
    history: list[OperationalWeatherDay] = []
    for offset in range(days):
        day = ensure_utc(start + timedelta(days=offset))
        samples = tuple(
            OperationalWeatherSample(
                parameter=parameter,
                source=OperationalWeatherSource.ERA5_LAND,
                source_run=day,
                valid_time=ensure_utc(day + timedelta(hours=hour)),
                value=-123.12345678901234
                if parameter == "thermal_net_radiation"
                else 123.12345678901234,
                product=WeatherProductIdentity.REANALYSIS,
                interval_end=ensure_utc(day + timedelta(hours=hour + 1))
                if parameter != "temperature"
                else None,
                contract_revision="rad-int-v1"
                if "radiation" in parameter
                else QUALIFIED_SOURCE_REVISION,
            )
            for parameter in PARAMETERS
            for hour in range(24)
        )
        history.append(
            OperationalWeatherDay(
                day=day,
                precipitation_mm=24 * 123.12345678901234,
                temperature_c=123.12345678901234,
                samples=samples,
            )
        )
    stations = tuple(StationId(UUID(int=i + 1)) for i in range(station_count))
    sources: list[tuple[StationId, StationSourceEvidence]] = []
    success = WeatherLegOutcome(state=WeatherLegState.SUCCESS)
    for station_id in stations:
        window = OperationalWeatherWindow(
            station_id=station_id,
            binding_fingerprint=FINGERPRINT,
            issue_time=ISSUE,
            start=start,
            end=ISSUE,
            days=tuple(history),
        )
        snapshot = make_past_snapshot(
            window=window,
            retrieved_at=ISSUE,
            snapshot_id=UUID(int=100 + station_id.int),
            max_bytes=16 * 1024 * 1024,
        )
        past = PastWeatherUse(
            freshness=WeatherFreshness.CURRENT,
            refresh_outcome=success,
            retrieved_at=ISSUE,
            requirement=PastWeatherRequirement(
                start=start, end=ISSUE, parameters=PARAMETERS
            ),
            snapshot_id=snapshot.id,
            snapshot_sha256=snapshot.payload_sha256,
            snapshot_issue_time=ISSUE,
            binding_fingerprint=FINGERPRINT,
            days=tuple(history),
        )
        native = radiation_rows()[:84]
        rows = tuple(
            replace(
                row,
                station_id=station_id,
                id=UUID(int=100000 + station_id.int * 1000 + pi * 100 + ri),
                parameter=parameter,
            )
            for pi, parameter in enumerate(PARAMETERS)
            for ri, row in enumerate(native)
        )
        request = FutureWeatherRequest(
            station_id=station_id,
            binding_fingerprint=FINGERPRINT,
            source="ifs_ecmwf",
            issue_time=ISSUE,
            requirements=tuple(
                FutureWeatherRequirement(
                    parameter=parameter,
                    valid_times=tuple(
                        ensure_utc(ISSUE + timedelta(days=i)) for i in range(15)
                    ),
                    member_ids=(0,),
                )
                for parameter in PARAMETERS
            ),
        )
        scope = future_receipt_scope(request, cycle_time=ISSUE)
        assert scope is not None
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=list(rows),
            readback=list(rows),
        )
        future = FutureWeatherUse(
            freshness=WeatherFreshness.CURRENT,
            refresh_attempt=FutureWeatherAttempt(
                scope=request, outcome=success, resolved_cycle=ISSUE
            ),
            requirement=request,
            receipt=receipt,
            consumed_record_ids=tuple(row.id for row in rows),
        )
        use = WeatherUse(
            station_id=station_id,
            issue_time=ISSUE,
            used_at=ISSUE,
            attempt=WeatherCacheAttempt(
                key=WeatherCacheKey(
                    station_id=station_id, binding_fingerprint=FINGERPRINT
                ),
                generation=1,
                id=UUID(int=1000 + station_id.int),
                issue_time=ISSUE,
                started_at=ISSUE,
            ),
            refresh_finished_at=ISSUE,
            past=past,
            future=future,
        )
        sources.append(
            (station_id, StationSourceEvidence(weather_use=use, future_weather=rows))
        )

    def weather_frame(count: int, start_at: UtcDatetime) -> pl.DataFrame:
        return pl.DataFrame(
            [
                {
                    "station_id": str(sid),
                    "timestamp": start_at + timedelta(days=i),
                    **{p: 123.12345678901234 for p in PARAMETERS},
                }
                for sid in stations
                for i in range(count)
            ]
        )

    past_frame = weather_frame(days, start)
    return GroupModelInputs(
        group_id=StationGroupId(UUID(int=999)),
        station_ids=stations,
        past_targets=past_frame.select("station_id", "timestamp").with_columns(
            pl.lit(1.0).alias("discharge")
        ),
        past_dynamic=past_frame,
        future_dynamic=weather_frame(15, ISSUE),
        static=pl.DataFrame(
            [
                {
                    "station_id": str(sid),
                    **{f"static_{i}": 123.12345678901234 for i in range(78)},
                }
                for sid in stations
            ]
        ),
        issue_time=ISSUE,
        forecast_horizon_steps=15,
        time_step=timedelta(days=1),
        source_evidence=tuple(sources),
    )
