from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.forecast_evidence import PersistedForecastEvidence
    from sapphire_flow.types.ids import ForecastId, ModelId, StationId


def validate_summary_parent(
    evidence: PersistedForecastEvidence,
    *,
    version: int,
    forecast_id: ForecastId,
    station_id: StationId,
    model_id: ModelId,
    issue_time: UtcDatetime,
    data_use: ForecastDataUse,
) -> None:
    manifest = json.loads(evidence.manifest_json)
    if not isinstance(manifest, dict):
        raise ValueError("selected weather manifest is not an object")
    manifest = cast("dict[str, object]", manifest)
    station_ids = manifest.get("weather_station_ids")
    if (
        manifest.get("weather_evidence_version") != version
        or manifest.get("forecast_id") != str(forecast_id)
        or manifest.get("station_id") != str(station_id)
        or manifest.get("issued_at") != issue_time.isoformat()
        or manifest.get("data_use") != data_use.value
        or manifest.get("model_id") != str(model_id)
        or manifest.get("snapshot_sha256") != evidence.snapshot_sha256
        or not isinstance(station_ids, list)
        or str(station_id) not in station_ids
    ):
        raise ValueError("selected weather parent identity mismatch")
    if (
        not evidence.snapshot
        or hashlib.sha256(evidence.snapshot).hexdigest() != evidence.snapshot_sha256
    ):
        raise ValueError("selected weather snapshot hash mismatch")
