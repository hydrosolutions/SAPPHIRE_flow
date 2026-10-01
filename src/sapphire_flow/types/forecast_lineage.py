from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from sapphire_flow.types.rating_reference import canonical_content, content_digest

if TYPE_CHECKING:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ForecastId, StationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.weather import WeatherForecastRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastInputSnapshot:
    """An as-used source record, not a pointer to a mutable current value."""

    kind: Literal["observation", "historical_forcing", "weather_forecast"]
    units: str
    content: str

    def __post_init__(self) -> None:
        fields = {
            "observation": {
                "id",
                "station_id",
                "timestamp",
                "parameter",
                "value",
                "source",
                "rating_curve_id",
                "rating_curve_correction_version",
                "qc_status",
                "qc_flags",
                "qc_rule_version",
                "delivery_id",
            },
            "historical_forcing": {
                "station_id",
                "source",
                "version",
                "valid_time",
                "parameter",
                "spatial_type",
                "band_id",
                "member_id",
                "value",
            },
            "weather_forecast": {
                "id",
                "station_id",
                "nwp_source",
                "cycle_time",
                "valid_time",
                "parameter",
                "spatial_type",
                "band_id",
                "member_id",
                "value",
                "is_gap",
                "gap_status",
            },
        }
        raw: object = json.loads(self.content)
        if self.kind not in fields or not isinstance(raw, dict):
            raise ValueError("unknown consumed input snapshot kind")
        data = cast("dict[str, Any]", raw)
        if set(data) != fields[self.kind] or not self.units.strip():
            raise ValueError(
                "consumed input snapshot requires complete source and units"
            )
        if canonical_content(data) != self.content:
            raise ValueError("consumed input snapshot must be canonical")
        if not isinstance(data["parameter"], str) or not data["parameter"].strip():
            raise ValueError("consumed input parameter is missing")
        value = data["value"]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("consumed input value must be finite or missing")
        time_fields = ("timestamp",) if self.kind == "observation" else ("valid_time",)
        if self.kind == "weather_forecast":
            time_fields += ("cycle_time",)
        for field in time_fields:
            if datetime.fromisoformat(data[field]).utcoffset() is None:
                raise ValueError("consumed input time must be timezone aware")
        source = data.get("source", data.get("nwp_source"))
        if not isinstance(source, str) or not source.strip():
            raise ValueError("consumed input source is missing")
        UUID(data["station_id"])
        if "id" in data:
            UUID(data["id"])


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
        if not self.source.strip() or not self.version.strip():
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
        if not (
            self.snapshots
            or self.static_attributes
            or self.provisional_discharge_fingerprints
            or self.contributor_forecast_ids
        ):
            raise ValueError("input lineage requires consumed inputs")
        if not self.transformation_versions or any(
            not version.strip() for version in self.transformation_versions
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
