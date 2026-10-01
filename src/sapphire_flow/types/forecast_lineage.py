from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Annotated, Literal, cast
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    FiniteFloat,
    StringConstraints,
)

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus, SpatialRepresentation
from sapphire_flow.types.rating_reference import canonical_content, content_digest

if TYPE_CHECKING:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ForecastId, StationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.weather import WeatherForecastRecord


_Nonempty = Annotated[str, StringConstraints(pattern=r"\S")]


class _QcFlagBoundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    rule_id: _Nonempty
    rule_version: _Nonempty
    status: QcStatus
    detail: str | None


class _ObservationSnapshotBoundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: UUID
    station_id: UUID
    timestamp: AwareDatetime
    parameter: _Nonempty
    value: int | FiniteFloat | None
    source: ObservationSource
    rating_curve_id: UUID | None
    rating_curve_correction_version: _Nonempty | None
    qc_status: QcStatus
    qc_flags: list[_QcFlagBoundary]
    qc_rule_version: _Nonempty | None
    delivery_id: _Nonempty | None


class _HistoricalSnapshotBoundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    station_id: UUID
    source: _Nonempty
    version: _Nonempty
    valid_time: AwareDatetime
    parameter: _Nonempty
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: int | FiniteFloat


class _WeatherSnapshotBoundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: UUID
    station_id: UUID
    nwp_source: _Nonempty
    cycle_time: AwareDatetime
    valid_time: AwareDatetime
    parameter: _Nonempty
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: int | FiniteFloat
    is_gap: bool
    gap_status: Literal["recovered", "unrecoverable"] | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastInputSnapshot:
    """An as-used source record, not a pointer to a mutable current value."""

    kind: Literal["observation", "historical_forcing", "weather_forecast"]
    units: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(cast("object", self.units), str) or not self.units.strip():
            raise ValueError(
                "consumed input snapshot requires complete source and units"
            )
        parsers = {
            "observation": _ObservationSnapshotBoundary,
            "historical_forcing": _HistoricalSnapshotBoundary,
            "weather_forecast": _WeatherSnapshotBoundary,
        }
        if self.kind not in parsers:
            raise ValueError("unknown consumed input snapshot kind")
        try:
            parsed = parsers[self.kind].model_validate_json(self.content)
            if canonical_content(parsed.model_dump()) != self.content:
                raise ValueError(
                    "snapshot must use canonical UUID and UTC timestamp spelling"
                )
            if isinstance(parsed, _ObservationSnapshotBoundary):
                from sapphire_flow.types.observation import Observation

                fields = parsed.model_dump(exclude={"qc_flags"})
                flags = [QcFlag(**flag.model_dump()) for flag in parsed.qc_flags]
                Observation(
                    **fields, qc_flags=flags, created_at=ensure_utc(parsed.timestamp)
                )
            else:
                if (parsed.spatial_type == SpatialRepresentation.ELEVATION_BAND) != (
                    parsed.band_id is not None
                ):
                    raise ValueError(
                        "snapshot spatial representation and band disagree"
                    )
                if (
                    isinstance(parsed, _WeatherSnapshotBoundary)
                    and parsed.is_gap
                    and parsed.gap_status is None
                ):
                    raise ValueError(
                        "snapshot gap_status is required for a weather gap"
                    )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"consumed input snapshot invalid: {exc}") from exc


def snapshot_consumed_input(
    record: Observation | RawHistoricalForcing | WeatherForecastRecord,
    *,
    units: str,
) -> ForecastInputSnapshot:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.weather import WeatherForecastRecord

    kind: Literal["observation", "historical_forcing", "weather_forecast"]
    if isinstance(record, Observation):
        kind = "observation"
    elif isinstance(record, RawHistoricalForcing):
        kind = "historical_forcing"
    elif isinstance(cast("object", record), WeatherForecastRecord):
        kind = "weather_forecast"
    else:
        raise TypeError("unsupported consumed input record")
    data = asdict(record)
    data.pop("created_at", None)
    return ForecastInputSnapshot(
        kind=kind, units=units, content=canonical_content(data)
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastStaticAttributes:
    station_id: StationId
    source: str
    version: str
    values: tuple[tuple[str, float | None], ...]

    def __post_init__(self) -> None:
        if not isinstance(cast("object", self.station_id), UUID):
            raise ValueError("consumed static attributes require a station identity")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (cast("object", self.source), cast("object", self.version))
        ):
            raise ValueError("consumed static attributes require source and version")
        if type(self.values) is not tuple or not self.values:
            raise ValueError("consumed static attributes require immutable values")
        names: set[str] = set()
        for item in self.values:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("consumed static attributes require name/value pairs")
            name, value = item
            if (
                not isinstance(cast("object", name), str)
                or not name.strip()
                or name in names
            ):
                raise ValueError("consumed static attribute names must be unique")
            names.add(name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(cast("object", value), (float, int))
                or not math.isfinite(value)
            ):
                raise ValueError("consumed static attributes must be finite or missing")


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastInputLineage:
    """Caller-declared actual consumption; assemblers must establish completeness."""

    transformation_versions: tuple[str, ...]
    snapshots: tuple[ForecastInputSnapshot, ...] = ()
    static_attributes: tuple[ForecastStaticAttributes, ...] = ()
    provisional_discharge_fingerprints: tuple[str, ...] = ()
    contributor_forecast_ids: tuple[ForecastId, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(items) is not tuple
            for items in (
                self.snapshots,
                self.static_attributes,
                self.provisional_discharge_fingerprints,
                self.contributor_forecast_ids,
                self.transformation_versions,
            )
        ):
            raise ValueError("input lineage collections must be immutable tuples")
        if any(
            not isinstance(cast("object", item), ForecastInputSnapshot)
            for item in self.snapshots
        ):
            raise ValueError("input lineage requires typed snapshots")
        if any(
            not isinstance(cast("object", item), ForecastStaticAttributes)
            for item in self.static_attributes
        ):
            raise ValueError("input lineage requires typed static attributes")
        if any(
            not isinstance(cast("object", item), UUID)
            for item in self.contributor_forecast_ids
        ):
            raise ValueError("input lineage requires typed forecast identities")
        if len(set(self.snapshots)) != len(self.snapshots):
            raise ValueError("duplicate consumed input snapshots")
        static_keys = {
            (item.station_id, item.source, item.version)
            for item in self.static_attributes
        }
        if len(static_keys) != len(self.static_attributes):
            raise ValueError("duplicate consumed static attribute source versions")
        if not (
            self.snapshots
            or self.static_attributes
            or self.provisional_discharge_fingerprints
            or self.contributor_forecast_ids
        ):
            raise ValueError("input lineage requires consumed inputs")
        if not self.transformation_versions or any(
            not isinstance(cast("object", version), str) or not version.strip()
            for version in self.transformation_versions
        ):
            raise ValueError("input lineage requires transformation versions")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in self.provisional_discharge_fingerprints
        ):
            raise ValueError("invalid provisional discharge fingerprint")

    @property
    def content(self) -> str:
        return canonical_content(
            {
                "static_attributes": sorted(
                    (
                        {
                            "station_id": str(attributes.station_id),
                            "source": attributes.source,
                            "version": attributes.version,
                            "values": dict(attributes.values),
                        }
                        for attributes in self.static_attributes
                    ),
                    key=canonical_content,
                ),
                "snapshots": sorted(
                    (asdict(snapshot) for snapshot in self.snapshots),
                    key=canonical_content,
                ),
                "provisional_discharge_fingerprints": sorted(
                    set(self.provisional_discharge_fingerprints)
                ),
                "contributor_forecast_ids": sorted(
                    {str(i) for i in self.contributor_forecast_ids}
                ),
                # Pipeline order is meaningful, unlike source-record order.
                "transformation_versions": self.transformation_versions,
            }
        )

    @property
    def fingerprint(self) -> str:
        return content_digest(self.content)

    @classmethod
    def from_content(cls, content: str) -> ForecastInputLineage:
        from sapphire_flow.types.ids import ForecastId, StationId

        try:
            data = json.loads(content)
            result = cls(
                static_attributes=tuple(
                    ForecastStaticAttributes(
                        station_id=StationId(UUID(item["station_id"])),
                        source=item["source"],
                        version=item["version"],
                        values=tuple(item["values"].items()),
                    )
                    for item in data["static_attributes"]
                ),
                snapshots=tuple(
                    ForecastInputSnapshot(**item) for item in data["snapshots"]
                ),
                provisional_discharge_fingerprints=tuple(
                    data["provisional_discharge_fingerprints"]
                ),
                contributor_forecast_ids=tuple(
                    ForecastId(UUID(i)) for i in data["contributor_forecast_ids"]
                ),
                transformation_versions=tuple(data["transformation_versions"]),
            )
            if result.content != content:
                raise ValueError("input lineage must be canonical")
            return result
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(
                "consumed input lineage is malformed or noncanonical"
            ) from exc
