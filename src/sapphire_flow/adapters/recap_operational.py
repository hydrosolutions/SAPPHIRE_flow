from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.adapters.recap_gateway import (
    RECAP_VARIABLES,
    RecapConfigurationError,
    RecapDataUnavailableError,
    RecapPayloadIntegrityError,
    guarded_recap_fetch,
)
from sapphire_flow.preprocessing.recap_operational_weather import reduce_daily_window
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.operational_weather import (
    OPERATIONAL_WEATHER_POLICY,
    QUALIFIED_SOURCE_REVISION,
    OperationalWeatherCoverageError,
    OperationalWeatherSample,
    OperationalWeatherSource,
    OperationalWeatherWindow,
    WeatherProductIdentity,
    require_midnight,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    converter = RECAP_VARIABLES[parameter].convert
    if converter is None:
        raise RecapPayloadIntegrityError("operational variable has no unit contract")
    samples: list[OperationalWeatherSample] = []
    try:
        for timestamp, series in response.iterrows():
            valid_time = ensure_utc(cast("pd.Timestamp", timestamp).to_pydatetime())
            source = OperationalWeatherSource(series["source"])
            if not start <= valid_time < end:
                continue
            raw_meta = cast("object", series["source_meta"])
            if isinstance(raw_meta, str):
                _SourceMetadata.model_validate_json(raw_meta)
            else:
                _SourceMetadata.model_validate(raw_meta)
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
            try:
                value_row = _ProviderSample.model_validate(
                    {**row.model_dump(), "value": series[polygon]}
                )
                samples.append(
                    OperationalWeatherSample(
                        parameter=parameter,
                        source=source,
                        valid_time=valid_time,
                        source_run=source_run,
                        value=converter(value_row.value),
                        product=(
                            WeatherProductIdentity.REANALYSIS
                            if source is OperationalWeatherSource.ERA5_LAND
                            else WeatherProductIdentity.CONTROL
                        ),
                        interval_end=(
                            ensure_utc(valid_time + timedelta(hours=hours))
                            if parameter == "precipitation"
                            else None
                        ),
                    )
                )
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
    source: StationWeatherSource, ref: GatewayPolygonRef
) -> str:
    descriptor = {
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
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def future_binding_fingerprint(
    source: StationWeatherSource,
    ref: GatewayPolygonRef,
    member_ids: tuple[int, ...],
) -> str:
    descriptor = {
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
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RecapGatewayOperationalAdapter:
    def __init__(
        self,
        *,
        api: EcmwfApiLike,
        resolver: GatewayPolygonResolver,
        config: OperationalWeatherConfig,
    ) -> None:
        self._api = api
        self._resolver = resolver
        self._config = config

    def fetch_windows(
        self,
        *,
        sources: list[StationWeatherSource],
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> dict[StationId, OperationalWeatherWindow]:
        for bound in (issue_time, start, end):
            require_midnight(bound)
        if start >= end or end > issue_time:
            raise RecapConfigurationError("combined past bounds must end by issue time")
        request_start = min(
            start,
            ensure_utc(
                issue_time - timedelta(days=self._config.provider_min_history_days)
            ),
        )
        if (issue_time - request_start).days > self._config.provider_max_history_days:
            raise RecapConfigurationError(
                "combined history exceeds provider request bound"
            )
        grouped: dict[str, list[tuple[StationWeatherSource, GatewayPolygonRef]]] = {}
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
            grouped.setdefault(ref.hru_name, []).append((source, ref))
        windows: dict[StationId, OperationalWeatherWindow] = {}
        for hru, bindings in grouped.items():
            responses = {
                parameter: guarded_recap_fetch(
                    self._api.operational,
                    hru_code=hru,
                    start_date=request_start.date(),
                    era5_variable_name=RECAP_VARIABLES[parameter].era5_name,
                    ifs_variable_name=RECAP_VARIABLES[parameter].ifs_name,
                    subdaily_resolution=6,
                    include_provenance=True,
                )
                for parameter in ("precipitation", "temperature")
            }
            for source, ref in bindings:
                windows[source.station_id] = self._reduce_pair(
                    source, ref, responses, issue_time=issue_time, start=start, end=end
                )
        return windows

    def _reduce_pair(
        self,
        source: StationWeatherSource,
        ref: GatewayPolygonRef,
        responses: dict[str, object],
        *,
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> OperationalWeatherWindow:
        buffer_end = ensure_utc(end + timedelta(days=self._config.rollover_buffer_days))
        samples = tuple(
            sample
            for parameter in ("precipitation", "temperature")
            for sample in parse_operational_samples(
                responses[parameter],
                polygon=ref.polygon_name,
                parameter=parameter,
                start=start,
                end=end,
                max_response_bytes=self._config.max_response_bytes,
            )
        )
        fingerprint = operational_binding_fingerprint(source, ref)
        try:
            required = reduce_daily_window(
                station_id=source.station_id,
                binding_fingerprint=fingerprint,
                issue_time=issue_time,
                start=start,
                end=end,
                samples=samples,
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
        for parameter in ("precipitation", "temperature"):
            parse_operational_samples(
                responses[parameter],
                polygon=ref.polygon_name,
                parameter=parameter,
                start=end,
                end=buffer_end,
                max_response_bytes=self._config.max_response_bytes,
                mode="identity",
            )
        try:
            optional = tuple(
                sample
                for parameter in ("precipitation", "temperature")
                for sample in parse_operational_samples(
                    responses[parameter],
                    polygon=ref.polygon_name,
                    parameter=parameter,
                    start=end,
                    end=buffer_end,
                    max_response_bytes=self._config.max_response_bytes,
                )
            )
        except OperationalWeatherSupportError:
            return required
        try:
            return reduce_daily_window(
                station_id=source.station_id,
                binding_fingerprint=fingerprint,
                issue_time=issue_time,
                start=start,
                end=buffer_end,
                samples=samples + optional,
            )
        except OperationalWeatherCoverageError:
            # Optional incomplete support cannot invalidate the required past.
            return required
        except ValueError:
            # Identity is validated above; only optional day support differs.
            return required
