from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
from numbers import Real
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.adapters.recap_gateway import (
    RECAP_VARIABLES,
    RecapConfigurationError,
    RecapDataUnavailableError,
    RecapPayloadIntegrityError,
    guarded_recap_fetch,
)
from sapphire_flow.preprocessing.recap_operational_weather import (
    reduce_daily_window,
    reduce_weather_periods,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.operational_weather import (
    LEGACY_WEATHER_PARAMETERS,
    OPERATIONAL_WEATHER_POLICY,
    QUALIFIED_SOURCE_REVISION,
    WEATHER_PARAMETERS,
    OperationalWeatherCoverageError,
    OperationalWeatherSample,
    OperationalWeatherSource,
    OperationalWeatherWindow,
    WeatherCoverage,
    WeatherProductIdentity,
    require_daily_grid_boundary,
    require_midnight,
)
from sapphire_flow.types.operational_weather_v2 import (
    OperationalWeatherWindowV2,
    require_context,
)
from sapphire_flow.types.recap_intervals import (
    RADIATION_CONTRACT_REVISION,
    RADIATION_PARAMETERS,
    UNQUALIFIED_INTERVALS,
    GatewayIntervalQualification,
    radiation_mean_flux,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from sapphire_flow.adapters.recap_gateway import (
        EcmwfApiLike,
        GatewayPolygonRef,
        GatewayPolygonResolver,
    )
    from sapphire_flow.config.operational_weather import OperationalWeatherConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import WeatherParameter
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.time_grid import TimeGrid


log = structlog.get_logger(__name__)


class _SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid_resolution: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    ifs_type: Literal["fc"] | None = None
    member: Literal["fc"] | None = None


class OperationalWeatherSupportError(RecapPayloadIntegrityError):
    """Invalid numeric or day support within an identified response."""


class _ProviderSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: OperationalWeatherSource
    source_run: datetime
    valid_time: datetime
    value: float = Field(allow_inf_nan=False, strict=True)


def parse_operational_samples(
    response: object,
    *,
    polygon: str,
    parameter: WeatherParameter,
    start: UtcDatetime,
    end: UtcDatetime,
    max_response_bytes: int,
    mode: Literal["samples", "identity"] = "samples",
    interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
) -> tuple[OperationalWeatherSample, ...]:
    if not isinstance(response, pd.DataFrame):
        raise RecapPayloadIntegrityError("operational response is not a dataframe")
    decoded_bytes = sum(
        cast("Iterable[int]", response.memory_usage(index=True, deep=True))
    )
    if decoded_bytes > max_response_bytes:
        raise RecapPayloadIntegrityError(
            "operational response exceeds decoded byte limit"
        )
    if not response.columns.is_unique or not isinstance(
        response.index, pd.DatetimeIndex
    ):
        raise RecapPayloadIntegrityError(
            "operational response has invalid columns/time"
        )
    required = {polygon, "source", "source_run", "source_meta"}
    if not required.issubset(response.columns):
        raise RecapPayloadIntegrityError(
            "operational response lacks required provenance"
        )
    if parameter in RADIATION_PARAMETERS:
        interval_qualification.require_radiation()
    converter = RECAP_VARIABLES[parameter].convert
    if converter is None and parameter not in RADIATION_PARAMETERS:
        raise RecapPayloadIntegrityError("operational variable has no unit contract")
    samples: list[OperationalWeatherSample] = []
    seen: set[UtcDatetime] = set()
    try:
        for timestamp, series in response.iterrows():
            valid_time = ensure_utc(cast("pd.Timestamp", timestamp).to_pydatetime())
            source = OperationalWeatherSource(series["source"])
            if not start <= valid_time < end:
                continue
            if valid_time in seen:
                raise ValueError("duplicate operational sample timestamp")
            seen.add(valid_time)
            raw_meta = cast("object", series["source_meta"])
            if isinstance(raw_meta, str):
                metadata = _SourceMetadata.model_validate_json(raw_meta)
            else:
                metadata = _SourceMetadata.model_validate(raw_meta)
            if (
                source is OperationalWeatherSource.IFS
                and metadata.grid_resolution != 0.1
            ):
                raise ValueError(
                    "operational IFS contradicts qualified HRES 0.1-degree product"
                )
            row = _ProviderSample.model_validate(
                {
                    "source": source,
                    "source_run": series["source_run"],
                    "valid_time": valid_time,
                    "value": 0.0,
                }
            )
            source_run = ensure_utc(row.source_run)
            lead = (valid_time - source_run).total_seconds() / 3600
            hours = (
                1
                if source is OperationalWeatherSource.ERA5_LAND
                else (3 if lead < 144 else 6)
            )
            if source is OperationalWeatherSource.ERA5_LAND:
                require_midnight(source_run)
                if source_run.date() != valid_time.date():
                    raise ValueError("ERA5 source run differs from UTC day")
            elif (
                source_run.hour % 6
                or any((source_run.minute, source_run.second, source_run.microsecond))
                or source_run > valid_time
            ):
                raise ValueError("invalid IFS cycle identity")
            if mode == "identity":
                continue
            raw_value = cast("object", series[polygon])
            missing_value = (
                raw_value is None
                or raw_value is pd.NA
                or (isinstance(raw_value, Real) and math.isnan(float(raw_value)))
            )
            try:
                # Validate missing samples' source grid; never retain the placeholder.
                value_row = _ProviderSample.model_validate(
                    {**row.model_dump(), "value": 0.0 if missing_value else raw_value}
                )
                sample = OperationalWeatherSample(
                    parameter=parameter,
                    source=source,
                    valid_time=valid_time,
                    source_run=source_run,
                    value=(
                        radiation_mean_flux(
                            value_row.value,
                            start=valid_time,
                            source_run=source_run,
                            source=source,
                            qualification=interval_qualification,
                        )
                        if parameter in RADIATION_PARAMETERS
                        else cast("Callable[[float], float]", converter)(
                            value_row.value
                        )
                    ),
                    contract_revision=RADIATION_CONTRACT_REVISION
                    if parameter in RADIATION_PARAMETERS
                    else QUALIFIED_SOURCE_REVISION,
                    product=(
                        WeatherProductIdentity.REANALYSIS
                        if source is OperationalWeatherSource.ERA5_LAND
                        else WeatherProductIdentity.CONTROL
                    ),
                    interval_end=(
                        ensure_utc(valid_time + timedelta(hours=hours))
                        if parameter == "precipitation"
                        or parameter in RADIATION_PARAMETERS
                        else None
                    ),
                )
                if not missing_value:
                    samples.append(sample)
            except (ValueError, TypeError) as exc:
                raise OperationalWeatherSupportError(
                    "invalid operational numeric/day support"
                ) from exc
    except OperationalWeatherSupportError:
        raise
    except (ValueError, TypeError, ValidationError) as exc:
        raise RecapPayloadIntegrityError("invalid operational weather sample") from exc
    return tuple(samples)


def operational_binding_fingerprint(
    source: StationWeatherSource,
    ref: GatewayPolygonRef,
    *,
    parameters: tuple[WeatherParameter, ...] = LEGACY_WEATHER_PARAMETERS,
    interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
) -> str:
    descriptor: dict[str, object] = {
        "source": source.nwp_source,
        "role": source.role.value,
        "extraction": source.extraction_type.value,
        "hru": ref.hru_name,
        "polygon": ref.polygon_name,
        "variables": ["total_precipitation", "tp", "2m_temperature", "2t"],
        "fill_cycle_hours": 6,
        "policy": OPERATIONAL_WEATHER_POLICY,
        "contract_revision": QUALIFIED_SOURCE_REVISION,
    }
    if RADIATION_PARAMETERS.intersection(parameters):
        interval_qualification.require_radiation()
        descriptor["radiation"] = {
            "parameters": sorted(parameters),
            "unit": "W/m²",
            "variables": {
                p: [RECAP_VARIABLES[p].era5_name, RECAP_VARIABLES[p].ifs_name]
                for p in sorted(parameters)
            },
            "semantic_revision": RADIATION_CONTRACT_REVISION,
            "interval_contract": interval_qualification.contract.value,
            "normalization_revision": interval_qualification.revision,
        }
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def future_binding_fingerprint(
    source: StationWeatherSource,
    ref: GatewayPolygonRef,
    member_ids: tuple[int, ...],
    *,
    parameters: tuple[WeatherParameter, ...] = LEGACY_WEATHER_PARAMETERS,
    interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
) -> str:
    descriptor: dict[str, object] = {
        "source": source.nwp_source,
        "role": source.role.value,
        "extraction": source.extraction_type.value,
        "hru": ref.hru_name,
        "polygon": ref.polygon_name,
        "members": sorted(member_ids),
        "variables": ["tp", "2t"],
        "policy": OPERATIONAL_WEATHER_POLICY,
        "contract_revision": QUALIFIED_SOURCE_REVISION,
    }
    if RADIATION_PARAMETERS.intersection(parameters):
        interval_qualification.require_radiation()
        descriptor["radiation"] = {
            "parameters": sorted(parameters),
            "unit": "W/m²",
            "variables": {
                p: [RECAP_VARIABLES[p].era5_name, RECAP_VARIABLES[p].ifs_name]
                for p in sorted(parameters)
            },
            "semantic_revision": RADIATION_CONTRACT_REVISION,
            "interval_contract": interval_qualification.contract.value,
            "normalization_revision": interval_qualification.revision,
        }
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _period_binding_fingerprint(source_digest: str, grid: TimeGrid) -> str:
    if grid.step != timedelta(days=1):
        raise ValueError("period weather binding requires a daily grid")
    descriptor: dict[str, object] = {
        "source_binding_sha256": source_digest,
        "policy": "recap-start-declared-daily-v2",
        "grid": {
            "step_seconds": 86400,
            "phase_offset_seconds": int(grid.phase.total_seconds()),
        },
    }
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def operational_binding_fingerprint_v2(
    source: StationWeatherSource,
    ref: GatewayPolygonRef,
    grid: TimeGrid,
    *,
    parameters: tuple[WeatherParameter, ...] = LEGACY_WEATHER_PARAMETERS,
    interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
) -> str:
    return _period_binding_fingerprint(
        operational_binding_fingerprint(
            source,
            ref,
            parameters=parameters,
            interval_qualification=interval_qualification,
        ),
        grid,
    )


def future_binding_fingerprint_v2(
    source: StationWeatherSource,
    ref: GatewayPolygonRef,
    member_ids: tuple[int, ...],
    grid: TimeGrid,
    *,
    parameters: tuple[WeatherParameter, ...] = LEGACY_WEATHER_PARAMETERS,
    interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
) -> str:
    return _period_binding_fingerprint(
        future_binding_fingerprint(
            source,
            ref,
            member_ids,
            parameters=parameters,
            interval_qualification=interval_qualification,
        ),
        grid,
    )


class RecapGatewayOperationalAdapter:
    def __init__(
        self,
        *,
        api: EcmwfApiLike,
        resolver: GatewayPolygonResolver,
        config: OperationalWeatherConfig,
        interval_qualification: GatewayIntervalQualification = UNQUALIFIED_INTERVALS,
    ) -> None:
        self._api = api
        self._resolver = resolver
        self._config = config
        self.interval_qualification = interval_qualification

    def fetch_windows(
        self,
        *,
        sources: list[StationWeatherSource],
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters_by_station: Mapping[StationId, tuple[WeatherParameter, ...]]
        | None = None,
    ) -> dict[StationId, OperationalWeatherWindow]:
        windows = self._fetch_windows(
            sources=sources,
            issue_time=issue_time,
            start=start,
            end=end,
            parameters_by_station=parameters_by_station,
        )
        assert all(
            isinstance(window, OperationalWeatherWindow) for window in windows.values()
        )
        return cast("dict[StationId, OperationalWeatherWindow]", windows)

    def fetch_period_windows(
        self,
        *,
        sources: list[StationWeatherSource],
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
        grid: TimeGrid,
        eligibility_cutoff: UtcDatetime,
        parameters_by_station: Mapping[StationId, tuple[WeatherParameter, ...]]
        | None = None,
    ) -> dict[StationId, OperationalWeatherWindowV2]:
        windows = self._fetch_windows(
            sources=sources,
            issue_time=issue_time,
            start=start,
            end=end,
            grid=grid,
            eligibility_cutoff=eligibility_cutoff,
            parameters_by_station=parameters_by_station,
        )
        assert all(
            isinstance(window, OperationalWeatherWindowV2)
            for window in windows.values()
        )
        return cast("dict[StationId, OperationalWeatherWindowV2]", windows)

    def _fetch_windows(
        self,
        *,
        sources: list[StationWeatherSource],
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
        grid: TimeGrid | None = None,
        eligibility_cutoff: UtcDatetime | None = None,
        parameters_by_station: Mapping[StationId, tuple[WeatherParameter, ...]]
        | None = None,
    ) -> dict[StationId, OperationalWeatherWindow | OperationalWeatherWindowV2]:
        if grid is None:
            for bound in (issue_time, start, end):
                require_midnight(bound)
            ceiling = issue_time
        else:
            if eligibility_cutoff is None:
                raise RecapConfigurationError(
                    "period weather requires an explicit cutoff"
                )
            require_context(grid, issue_time, eligibility_cutoff)
            for bound in (start, end):
                require_daily_grid_boundary(bound, grid)
            ceiling = eligibility_cutoff
        if start >= end or end > ceiling:
            raise RecapConfigurationError("combined past bounds must end by issue time")
        request_start = min(
            start,
            ensure_utc(
                issue_time - timedelta(days=self._config.provider_min_history_days)
            ),
        )
        request_start = ensure_utc(
            request_start.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        if issue_time - request_start > timedelta(
            days=self._config.provider_max_history_days
        ):
            raise RecapConfigurationError(
                "combined history exceeds provider request bound"
            )
        if parameters_by_station is not None and set(parameters_by_station) != {
            source.station_id for source in sources
        }:
            raise RecapConfigurationError("requested parameter station set mismatch")
        grouped: dict[
            tuple[str, tuple[WeatherParameter, ...]],
            list[tuple[StationWeatherSource, GatewayPolygonRef]],
        ] = {}
        seen_stations: set[StationId] = set()
        seen_polygons: set[tuple[str, str]] = set()
        for source in sources:
            if (
                source.nwp_source != self._config.source
                or source.role is not WeatherSourceRole.OPERATIONAL
                or source.status is not WeatherSourceStatus.ACTIVE
                or source.extraction_type is not SpatialRepresentation.BASIN_AVERAGE
                or source.station_id in seen_stations
            ):
                raise RecapConfigurationError("invalid combined operational binding")
            ref = self._resolver.resolve(source)
            if (
                ref is None
                or ref.station_id != source.station_id
                or ref.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
                or ref.band_id is not None
                or not ref.hru_name
                or not ref.polygon_name
                or (ref.hru_name, ref.polygon_name) in seen_polygons
            ):
                raise RecapConfigurationError("invalid combined polygon mapping")
            seen_stations.add(source.station_id)
            seen_polygons.add((ref.hru_name, ref.polygon_name))
            parameters = (
                LEGACY_WEATHER_PARAMETERS
                if parameters_by_station is None
                else parameters_by_station[source.station_id]
            )
            if (
                not parameters
                or len(set(parameters)) != len(parameters)
                or not set(parameters) <= set(WEATHER_PARAMETERS)
            ):
                raise RecapConfigurationError(
                    "invalid requested operational parameters"
                )
            if RADIATION_PARAMETERS.intersection(parameters):
                self.interval_qualification.require_radiation()
            grouped.setdefault((ref.hru_name, tuple(sorted(parameters))), []).append(
                (source, ref)
            )
        windows: dict[
            StationId, OperationalWeatherWindow | OperationalWeatherWindowV2
        ] = {}
        for (hru, parameters), bindings in grouped.items():
            responses: dict[str, object] = {}
            for parameter in parameters:
                try:
                    responses[parameter] = guarded_recap_fetch(
                        self._api.operational,
                        hru_code=hru,
                        start_date=request_start.date(),
                        era5_variable_name=RECAP_VARIABLES[parameter].era5_name,
                        ifs_variable_name=RECAP_VARIABLES[parameter].ifs_name,
                        subdaily_resolution=6,
                        include_provenance=True,
                    )
                except RecapDataUnavailableError:
                    if parameter not in RADIATION_PARAMETERS:
                        raise
                    # Retain other native channels; consuming models still need
                    # complete radiation and cannot fill this absent support.
                    log.warning(
                        "recap_operational.radiation_unavailable", parameter=parameter
                    )
            for source, ref in bindings:
                windows[source.station_id] = self._reduce_pair(
                    source,
                    ref,
                    responses,
                    parameters=parameters,
                    issue_time=issue_time,
                    start=start,
                    end=end,
                    grid=grid,
                    eligibility_cutoff=eligibility_cutoff,
                )
        return windows

    def _reduce_pair(
        self,
        source: StationWeatherSource,
        ref: GatewayPolygonRef,
        responses: dict[str, object],
        *,
        parameters: tuple[WeatherParameter, ...] = LEGACY_WEATHER_PARAMETERS,
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
        grid: TimeGrid | None = None,
        eligibility_cutoff: UtcDatetime | None = None,
    ) -> OperationalWeatherWindow | OperationalWeatherWindowV2:
        buffer_end = ensure_utc(end + timedelta(days=self._config.rollover_buffer_days))
        samples = tuple(
            sample
            for parameter in parameters
            if parameter in responses
            for sample in parse_operational_samples(
                responses[parameter],
                polygon=ref.polygon_name,
                parameter=parameter,
                start=start,
                end=end,
                max_response_bytes=self._config.max_response_bytes,
                interval_qualification=self.interval_qualification,
            )
        )
        if grid is not None:
            assert eligibility_cutoff is not None
            return OperationalWeatherWindowV2(
                station_id=source.station_id,
                binding_fingerprint=operational_binding_fingerprint_v2(
                    source,
                    ref,
                    grid,
                    parameters=parameters,
                    interval_qualification=self.interval_qualification,
                ),
                grid=grid,
                issue_time=issue_time,
                eligibility_cutoff=eligibility_cutoff,
                start=start,
                end=end,
                periods=reduce_weather_periods(
                    start=start,
                    end=end,
                    samples=samples,
                    grid=grid,
                    coverage=WeatherCoverage.ALLOW_GAPS,
                    parameters=parameters,
                ),
            )
        fingerprint = operational_binding_fingerprint(
            source,
            ref,
            parameters=parameters,
            interval_qualification=self.interval_qualification,
        )
        try:
            required = reduce_daily_window(
                station_id=source.station_id,
                binding_fingerprint=fingerprint,
                issue_time=issue_time,
                start=start,
                end=end,
                samples=samples,
                coverage=WeatherCoverage.ALLOW_GAPS,
                parameters=parameters,
            )
        except OperationalWeatherCoverageError as exc:
            raise RecapDataUnavailableError(
                "operational weather required support is incomplete",
                code="source_data_missing",
            ) from exc
        except ValueError as exc:
            raise RecapPayloadIntegrityError(
                "invalid operational daily window"
            ) from exc
        if buffer_end == end:
            return required
        for parameter in parameters:
            if parameter not in responses:
                continue
            parse_operational_samples(
                responses[parameter],
                polygon=ref.polygon_name,
                parameter=parameter,
                start=end,
                end=buffer_end,
                max_response_bytes=self._config.max_response_bytes,
                interval_qualification=self.interval_qualification,
                mode="identity",
            )
        # Keep the complete prefix, including when the provider's last day is
        # partial. Slice first rather than rescanning the entire history daily.
        days = list(required.days)
        cursor = end
        while cursor < buffer_end:
            next_day = ensure_utc(cursor + timedelta(days=1))
            try:
                optional = tuple(
                    sample
                    for parameter in parameters
                    if parameter in responses
                    for sample in parse_operational_samples(
                        _response_day(responses[parameter], cursor, next_day),
                        polygon=ref.polygon_name,
                        parameter=parameter,
                        start=cursor,
                        end=next_day,
                        max_response_bytes=self._config.max_response_bytes,
                        interval_qualification=self.interval_qualification,
                    )
                )
                buffer = reduce_daily_window(
                    station_id=source.station_id,
                    binding_fingerprint=fingerprint,
                    issue_time=issue_time,
                    start=cursor,
                    end=next_day,
                    samples=optional,
                    parameters=parameters,
                )
            except (
                OperationalWeatherSupportError,
                OperationalWeatherCoverageError,
                ValueError,
            ):
                break
            days.extend(buffer.days)
            cursor = next_day
        return replace(required, end=cursor, days=tuple(days))


def _response_day(
    response: object, start: UtcDatetime, end: UtcDatetime
) -> pd.DataFrame:
    if not isinstance(response, pd.DataFrame):
        raise RecapPayloadIntegrityError("operational response is not a dataframe")
    if not isinstance(response.index, pd.DatetimeIndex):
        raise RecapPayloadIntegrityError("operational response has invalid time index")
    index = (
        response.index.tz_localize("UTC")  # pyright: ignore[reportUnknownMemberType] -- pandas stub tz parameter
        if response.index.tz is None
        else response.index.tz_convert("UTC")  # pyright: ignore[reportUnknownMemberType] -- pandas stub tz parameter
    )
    return cast("pd.DataFrame", response.loc[(index >= start) & (index < end)])
