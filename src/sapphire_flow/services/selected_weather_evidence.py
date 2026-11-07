from __future__ import annotations

import hashlib
import json
import zlib
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, cast
from uuid import UUID

import structlog
from pydantic import TypeAdapter

from sapphire_flow.config.operational_weather import MAX_SELECTED_WEATHER_EVIDENCE_BYTES
from sapphire_flow.exceptions import WeatherEvidenceCaptureError
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.ids import ForecastId, StationId
from sapphire_flow.types.operational_weather import (
    DerivedWeatherUse,
    FutureWeatherUse,
    WeatherUse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from zoneinfo import ZoneInfo

    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_evidence import (
        ForecastEvidence,
        PersistedForecastEvidence,
        StationSourceEvidence,
    )
    from sapphire_flow.types.ids import ModelId

MAX_WEATHER_EVIDENCE_BYTES = MAX_SELECTED_WEATHER_EVIDENCE_BYTES
_WEATHER_USE = TypeAdapter(WeatherUse)
_WEATHER_USE.rebuild(
    _types_namespace={
        "UtcDatetime": UtcDatetime,
        "StationId": StationId,
        "ForecastId": ForecastId,
        "UUID": UUID,
    }
)

_DERIVED = TypeAdapter(DerivedWeatherUse)
_DERIVED.rebuild(
    _types_namespace={
        "UtcDatetime": UtcDatetime,
        "StationId": StationId,
        "ForecastId": ForecastId,
        "UUID": UUID,
    }
)


class SelectedWeatherIssuanceRefusal(Enum):
    WEATHER_EXPIRED = "weather_expired"
    ISSUE_DAY_MISMATCH = "issue_day_mismatch"


def selected_weather_issuance_refusal(
    source: StationSourceEvidence | None,
    *,
    issue_time: UtcDatetime,
    at: UtcDatetime,
    max_age_hours: float,
    automatic_issue_timezone: ZoneInfo | None = None,
) -> SelectedWeatherIssuanceRefusal | None:
    if source is None or not isinstance(source.weather_use, WeatherUse):
        raise WeatherEvidenceCaptureError("selected weather assembly facts missing")
    use = source.weather_use
    if use.issue_time != issue_time:
        raise WeatherEvidenceCaptureError("selected weather issue mismatch")
    cycle = (
        use.future.receipt.cycle_time
        if isinstance(use.future, FutureWeatherUse)
        else None
    )
    refusal = None
    if automatic_issue_timezone is not None and (
        issue_time.astimezone(automatic_issue_timezone).date()
        != at.astimezone(automatic_issue_timezone).date()
    ):
        refusal = SelectedWeatherIssuanceRefusal.ISSUE_DAY_MISMATCH
    elif cycle is not None and (
        cycle > min(at, issue_time) or at - cycle > timedelta(hours=max_age_hours)
    ):
        refusal = SelectedWeatherIssuanceRefusal.WEATHER_EXPIRED
    if refusal is not None:
        structlog.get_logger(__name__).warning(
            "selected_weather.issuance_refused",
            station_id=str(use.station_id),
            issue_time=issue_time.isoformat(),
            cycle_time=cycle.isoformat() if cycle is not None else None,
            checked_at=at.isoformat(),
            age_hours=(at - cycle).total_seconds() / 3600
            if cycle is not None
            else None,
            max_age_hours=max_age_hours,
            reason=refusal.value,
        )
    return refusal


def _object(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("selected weather evidence requires an object")
    mapping = cast("dict[object, object]", raw)
    if any(not isinstance(key, str) for key in mapping):
        raise ValueError("selected weather evidence requires string keys")
    return cast("dict[str, object]", mapping)


def _array(raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError("selected weather evidence requires an array")
    return cast("list[object]", raw)


def restore_bounded_weather_snapshot(
    snapshot: bytes, *, max_bytes: int
) -> dict[str, object]:
    if not 0 < max_bytes <= MAX_WEATHER_EVIDENCE_BYTES:
        raise ValueError("invalid selected weather evidence limit")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(snapshot, max_bytes + 1)
    if len(raw) > max_bytes or not decoder.eof or decoder.unused_data:
        raise ValueError("selected weather evidence is oversized or malformed")
    return _object(json.loads(raw))


def restore_selected_weather(
    evidence: ForecastEvidence | PersistedForecastEvidence,
    *,
    model_id: ModelId,
    issue_time: UtcDatetime,
    max_bytes: int = MAX_WEATHER_EVIDENCE_BYTES,
    expected: Mapping[StationId, WeatherUse] | None = None,
) -> dict[StationId, WeatherUse | DerivedWeatherUse]:
    try:
        snapshot = evidence.snapshot
        if (
            not snapshot
            or hashlib.sha256(snapshot).hexdigest() != evidence.snapshot_sha256
        ):
            raise ValueError("selected weather snapshot hash mismatch or absence")
        manifest = _object(json.loads(evidence.manifest_json))
        if (
            type(manifest.get("weather_evidence_version")) is not int
            or manifest["weather_evidence_version"] != 1
            or manifest.get("model_id") != str(model_id)
            or manifest.get("snapshot_sha256") != evidence.snapshot_sha256
        ):
            raise ValueError("selected weather manifest mismatch")
        payload = restore_bounded_weather_snapshot(snapshot, max_bytes=max_bytes)
        if payload.get("model_id") != str(model_id) or payload.get(
            "kind"
        ) != manifest.get("kind"):
            raise ValueError("selected weather snapshot identity mismatch")
        if payload.get("issue_time") != issue_time.isoformat():
            raise ValueError("selected weather issue time mismatch")
        if payload["kind"] == "combined":
            derived = _DERIVED.validate_json(
                json.dumps(payload.get("weather_use"), allow_nan=False),
                strict=True,
                extra="forbid",
            )
            if (
                derived.issue_time != issue_time
                or payload.get("station_id") != str(derived.station_id)
                or manifest.get("weather_station_ids") != [str(derived.station_id)]
                or expected is not None
            ):
                raise ValueError("derived weather scope mismatch")
            references = _array(payload.get("contributors"))
            reference_keys = tuple(
                (_object(item).get("forecast_id"), _object(item).get("evidence_sha256"))
                for item in references
            )
            if reference_keys != tuple(
                (str(item.forecast_id), item.evidence_sha256)
                for item in derived.contributors
            ):
                raise ValueError("derived weather contributor references mismatch")
            return {derived.station_id: derived}
        if payload["kind"] == "station":
            sources = {str(payload["station_id"]): payload.get("source_records")}
            declared = [payload["station_id"]]
        elif payload["kind"] == "group":
            pairs = _array(payload.get("source_records"))
            sources: dict[str, object] = {}
            for raw_pair in pairs:
                pair = _array(raw_pair)
                if len(pair) != 2 or not isinstance(pair[0], str) or pair[0] in sources:
                    raise ValueError("selected group source map malformed")
                sources[pair[0]] = pair[1]
            declared = _array(payload.get("station_ids"))
        else:
            raise ValueError("unsupported selected weather snapshot kind")
        if not declared or any(not isinstance(sid, str) for sid in declared):
            raise ValueError("selected weather station scope missing")
        if len(set(declared)) != len(declared) or set(declared) != set(sources):
            raise ValueError("selected weather station scope mismatch")
        if manifest.get("weather_station_ids") != sorted(sources):
            raise ValueError("selected weather manifest station scope mismatch")
        uses: dict[StationId, WeatherUse] = {}
        for sid, raw_source in sources.items():
            source = _object(raw_source)
            use = _WEATHER_USE.validate_json(
                json.dumps(source.get("weather_use"), allow_nan=False),
                strict=True,
                extra="forbid",
            )
            if str(use.station_id) != sid or use.issue_time != issue_time:
                raise ValueError("selected weather use identity mismatch")
            if (
                source.get("historical_forcing") != []
                or source.get("tail_weather") != []
            ):
                raise ValueError("selected weather claims legacy history")
            future = _array(source.get("future_weather"))
            record_ids = tuple(_object(row).get("id") for row in future)
            expected_ids = (
                tuple(str(value) for value in use.future.consumed_record_ids)
                if isinstance(use.future, FutureWeatherUse)
                else ()
            )
            if record_ids != expected_ids:
                raise ValueError(
                    "selected weather consumed archive identities mismatch"
                )
            uses[use.station_id] = use
        if expected is not None and uses != dict(expected):
            raise ValueError("selected weather differs from immutable assembly facts")
        return dict(uses)
    except (ValueError, TypeError, KeyError, zlib.error) as exc:
        raise WeatherEvidenceCaptureError(
            "selected weather evidence unavailable"
        ) from exc


def validate_selected_capture(
    evidence: ForecastEvidence,
    *,
    sources: Mapping[StationId, StationSourceEvidence | None],
    model_id: ModelId,
    issue_time: UtcDatetime,
    max_bytes: int,
) -> None:
    expected: dict[StationId, WeatherUse] = {}
    for station_id, source in sources.items():
        if source is None or not isinstance(source.weather_use, WeatherUse):
            raise WeatherEvidenceCaptureError("selected weather assembly facts missing")
        expected[station_id] = source.weather_use
    if evidence.weather_evidence_version != 1:
        raise WeatherEvidenceCaptureError("selected weather evidence marker missing")
    restore_selected_weather(
        evidence,
        model_id=model_id,
        issue_time=issue_time,
        max_bytes=max_bytes,
        expected=expected,
    )


def validate_selected_forecast(forecast: OperationalForecast) -> None:
    evidence = forecast.evidence
    if forecast.weather_evidence_version == 1:
        if evidence is None or evidence.weather_evidence_version != 1:
            raise WeatherEvidenceCaptureError(
                "selected forecast requires matching weather evidence"
            )
        weather = restore_selected_weather(
            evidence,
            model_id=forecast.model_id,
            issue_time=forecast.issued_at,
        )
        if forecast.station_id not in weather:
            raise WeatherEvidenceCaptureError(
                "selected forecast weather station mismatch"
            )
    elif evidence is not None and evidence.weather_evidence_version is not None:
        raise WeatherEvidenceCaptureError(
            "unselected forecast cannot carry selected weather evidence"
        )
