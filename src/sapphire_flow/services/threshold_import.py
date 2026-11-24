from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import ThresholdDirection, ThresholdSource
from sapphire_flow.types.threshold_import import (
    ThresholdImportStatus,
    classify_threshold_import,
)

if TYPE_CHECKING:
    from sapphire_flow.adapters.bafu_thresholds import BafuThresholdSnapshot
    from sapphire_flow.protocols.stores import StationStore
    from sapphire_flow.types.domain import DangerLevelDefinition
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.station import StationConfig


class ThresholdImportMode(Enum):
    PREVIEW = "preview"
    APPLY = "apply"


BAFU_LEVEL_NAMES = {2: "Moderate", 3: "Considerable", 4: "High", 5: "Very High"}


def bafu_thresholds(
    station: StationConfig,
    snapshot: BafuThresholdSnapshot,
    levels: list[DangerLevelDefinition],
) -> list[StationThreshold]:
    if station.network != "bafu" or station.code != snapshot.station_code:
        raise ConfigurationError("threshold source does not match BAFU station")
    if "discharge" not in station.measured_parameters:
        raise ConfigurationError(
            "BAFU discharge thresholds require a discharge station"
        )
    if snapshot.bounds is None:
        return []
    configured = {level.display_order: level for level in levels}
    if any(
        number not in configured
        or configured[number].name != name
        or configured[number].direction is not ThresholdDirection.ABOVE
        for number, name in BAFU_LEVEL_NAMES.items()
    ):
        raise ConfigurationError(
            "deployment danger levels do not match BAFU levels 2–5"
        )
    return [
        StationThreshold(
            station_id=station.id,
            danger_level=BAFU_LEVEL_NAMES[number],
            parameter="discharge",
            value=value,
            source=ThresholdSource.AUTHORITY,
            created_at=snapshot.retrieved_at,
            updated_at=snapshot.retrieved_at,
        )
        for number, value in zip(range(2, 6), snapshot.bounds, strict=True)
    ]


def import_station_thresholds(
    *,
    station: StationConfig,
    tenant_id: TenantId,
    thresholds: list[StationThreshold],
    store: StationStore,
    mode: ThresholdImportMode,
) -> ThresholdImportStatus:
    if station.tenant_id != tenant_id:
        raise ConfigurationError("threshold station is outside the requested tenant")
    if any(item.station_id != station.id for item in thresholds):
        raise ConfigurationError("threshold values belong to another station")
    if mode is ThresholdImportMode.APPLY:
        return store.import_thresholds(thresholds)
    return classify_threshold_import(thresholds, store.fetch_thresholds(station.id))
