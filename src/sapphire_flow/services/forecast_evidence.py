from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import zlib
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import polars as pl
import structlog
from pydantic import BaseModel

from sapphire_flow import __version__
from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.services.ensemble_fanout import prediction_member_inputs
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    ForecastEvidence,
    incomplete_evidence,
)

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        StationForecastQcOverride,
        StationThreshold,
    )
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.model import GroupModelInputs, StationModelInputs

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _dependency_versions() -> dict[str, str]:
    return {
        distribution.metadata["Name"]: distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }


def _plain(value: Any) -> Any:
    if isinstance(value, pl.DataFrame):
        return {"polars_ipc_base64": _frame_bytes(value)}
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": repr(value)}
    if isinstance(value, dict):
        entries = cast("dict[object, object]", value)
        return {str(_plain(key)): _plain(item) for key, item in entries.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        entries = cast(
            "list[object] | tuple[object, ...] | set[object] | frozenset[object]", value
        )
        items = [_plain(item) for item in entries]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def serialize_thresholds(thresholds: tuple[StationThreshold, ...]) -> str:
    return json.dumps(_plain(thresholds), sort_keys=True, separators=(",", ":"))


def _frame_bytes(frame: pl.DataFrame) -> str:
    buffer = BytesIO()
    frame.write_ipc(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _frame_map(frames: dict[str, pl.DataFrame | None]) -> dict[str, str | None]:
    return {
        name: (_frame_bytes(frame) if frame is not None else None)
        for name, frame in frames.items()
    }


def _evidence(
    payload: dict[str, object],
    *,
    artifact_bytes: bytes | None,
    missing_reason: str | None = None,
) -> ForecastEvidence:
    raw = json.dumps(
        _plain(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    artifact_hash = (
        hashlib.sha256(artifact_bytes).hexdigest()
        if artifact_bytes is not None
        else None
    )
    manifest = {
        "schema_version": 1,
        "kind": payload["kind"],
        "model_id": payload.get("model_id"),
        "snapshot_sha256": hashlib.sha256(compressed).hexdigest(),
        "artifact_sha256": artifact_hash,
        "code_version": __version__,
        "runtime_image_digest": os.environ.get("SAPPHIRE_IMAGE_DIGEST"),
        "python_version": platform.python_version(),
        "dependency_versions": _dependency_versions(),
    }
    missing = [missing_reason] if missing_reason else []
    image_digest = manifest["runtime_image_digest"]
    if not image_digest:
        missing.append("runtime_image_digest_unavailable")
    elif not re.fullmatch(r"sha256:[0-9a-f]{64}", str(image_digest)):
        missing.append("runtime_image_digest_invalid")
    else:
        # A digest identifies an image; T2 must prove its bytes survive a restore.
        missing.append("runtime_image_bytes_unpinned")
    combined_reason = ";".join(missing) if missing else None
    return ForecastEvidence(
        status=(
            EvidenceStatus.INCOMPLETE if combined_reason else EvidenceStatus.COMPLETE
        ),
        manifest_json=json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        snapshot=compressed,
        snapshot_sha256=hashlib.sha256(compressed).hexdigest(),
        artifact=artifact_bytes,
        artifact_sha256=artifact_hash,
        reason=combined_reason,
    )


def capture_station_evidence(
    *,
    inputs: StationModelInputs,
    model: object,
    model_id: ModelId,
    artifact_bytes: bytes,
    prior_state: bytes | None,
    rng_state: object,
    config: DeploymentConfig,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    water_level_datum_masl: float | None,
    fanout_features: frozenset[str] | None = None,
) -> ForecastEvidence:
    try:
        payload: dict[str, object] = {
            "schema_version": 1,
            "kind": "station",
            "station_id": str(inputs.station_id),
            "model_id": str(model_id),
            "model_class": (
                model.wrapped_model_class
                if isinstance(model, ForecastInterfaceAdapter)
                else f"{type(model).__module__}.{type(model).__qualname__}"
            ),
            "adapter_class": (
                f"{type(model).__module__}.{type(model).__qualname__}"
                if isinstance(model, ForecastInterfaceAdapter)
                else None
            ),
            "model_config_hash": getattr(model, "config_hash", None),
            "issue_time": inputs.issue_time,
            "time_step_seconds": inputs.time_step.total_seconds(),
            "forecast_horizon_steps": inputs.forecast_horizon_steps,
            "forcing_route": inputs.forcing_route.value,
            "frames": _frame_map(
                {
                    "past_targets": inputs.data.past_targets,
                    "past_dynamic": inputs.data.past_dynamic,
                    "future_dynamic": inputs.data.future_dynamic,
                    "static": inputs.data.static,
                }
            ),
            "fi_model_inputs": (
                model.evidence_inputs(inputs)
                if isinstance(model, ForecastInterfaceAdapter)
                and fanout_features is None
                else None
            ),
            "fanout_inputs": (
                [
                    {
                        "member_id": member,
                        "frames": _frame_map(
                            {
                                "past_targets": member_inputs.data.past_targets,
                                "past_dynamic": member_inputs.data.past_dynamic,
                                "future_dynamic": member_inputs.data.future_dynamic,
                                "static": member_inputs.data.static,
                            }
                        ),
                        "fi_model_inputs": (
                            model.evidence_inputs(member_inputs)
                            if isinstance(model, ForecastInterfaceAdapter)
                            else None
                        ),
                    }
                    for member, member_inputs in prediction_member_inputs(
                        inputs, future_features=fanout_features
                    )
                ]
                if fanout_features is not None
                else None
            ),
            "source_records": inputs.source_evidence,
            "prior_state": prior_state,
            "rng_state": rng_state,
            "data_requirements": getattr(model, "data_requirements", None),
            "config_sha256": hashlib.sha256(
                config.model_dump_json().encode("utf-8")
            ).hexdigest(),
            "deployment_config": config.model_dump(mode="json"),
            "forecast_qc_rules": qc_rules,
            "forecast_qc_overrides": qc_overrides,
            "forecast_qc_baselines": baselines,
            "water_level_datum_masl": water_level_datum_masl,
        }
        return _evidence(
            payload,
            artifact_bytes=artifact_bytes,
            missing_reason=(
                "source_provenance_unavailable"
                if inputs.source_evidence is None
                else None
            ),
        )
    except Exception as exc:
        log.warning("forecast_evidence.capture_failed", error=str(exc))
        return incomplete_evidence(f"capture_failed:{type(exc).__name__}")


def capture_group_evidence(
    *,
    inputs: GroupModelInputs,
    model: object,
    model_id: ModelId,
    artifact_bytes: bytes,
    rng_state: object,
    config: DeploymentConfig,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines_by_station: dict[StationId, list[ClimBaseline]],
    water_level_datums_masl: dict[StationId, float | None],
) -> ForecastEvidence:
    try:
        payload: dict[str, object] = {
            "schema_version": 1,
            "kind": "group",
            "group_id": str(inputs.group_id),
            "station_ids": [str(station_id) for station_id in inputs.station_ids],
            "model_id": str(model_id),
            "model_class": (
                model.wrapped_model_class
                if isinstance(model, ForecastInterfaceAdapter)
                else f"{type(model).__module__}.{type(model).__qualname__}"
            ),
            "adapter_class": (
                f"{type(model).__module__}.{type(model).__qualname__}"
                if isinstance(model, ForecastInterfaceAdapter)
                else None
            ),
            "model_config_hash": getattr(model, "config_hash", None),
            "issue_time": inputs.issue_time,
            "time_step_seconds": inputs.time_step.total_seconds(),
            "forecast_horizon_steps": inputs.forecast_horizon_steps,
            "frames": _frame_map(
                {
                    "past_targets": inputs.past_targets,
                    "past_dynamic": inputs.past_dynamic,
                    "future_dynamic": inputs.future_dynamic,
                    "static": inputs.static,
                }
            ),
            "fi_model_inputs": (
                model.evidence_inputs(inputs)
                if isinstance(model, ForecastInterfaceAdapter)
                else None
            ),
            "source_records": inputs.source_evidence,
            "rng_state": rng_state,
            "data_requirements": getattr(model, "data_requirements", None),
            "config_sha256": hashlib.sha256(
                config.model_dump_json().encode("utf-8")
            ).hexdigest(),
            "deployment_config": config.model_dump(mode="json"),
            "forecast_qc_rules": qc_rules,
            "forecast_qc_overrides": qc_overrides,
            "forecast_qc_baselines_by_station": baselines_by_station,
            "water_level_datums_masl": water_level_datums_masl,
        }
        has_all_sources = {
            station_id for station_id, _ in inputs.source_evidence
        } == set(inputs.station_ids)
        return _evidence(
            payload,
            artifact_bytes=artifact_bytes,
            missing_reason=None if has_all_sources else "source_provenance_unavailable",
        )
    except Exception as exc:
        log.warning("forecast_evidence.capture_failed", error=str(exc))
        return incomplete_evidence(f"capture_failed:{type(exc).__name__}")


def capture_combined_evidence(
    *,
    model_id: ModelId,
    strategy: str,
    contributors: tuple[OperationalForecast, ...],
    weights: dict[ModelId, float] | None,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    water_level_datum_masl: float | None,
    bma_sampling_counts: dict[ModelId, int] | None = None,
) -> ForecastEvidence:
    try:
        payload: dict[str, object] = {
            "schema_version": 1,
            "kind": "combined",
            "model_id": str(model_id),
            "strategy": strategy,
            "contributors": [
                {
                    "forecast_id": str(fc.id),
                    "model_id": str(fc.model_id),
                    "artifact_id": str(fc.model_artifact_id),
                    "nwp_cycle_reference_time": fc.nwp_cycle_reference_time,
                    "nwp_cycle_source": fc.nwp_cycle_source.value,
                    "evidence_sha256": (
                        fc.evidence.snapshot_sha256 if fc.evidence else None
                    ),
                }
                for fc in contributors
            ],
            "weights": {str(key): value for key, value in (weights or {}).items()},
            "bma_eligible_model_order": (
                [str(mid) for mid in bma_sampling_counts]
                if bma_sampling_counts is not None
                else None
            ),
            "bma_sampling_counts": (
                {str(mid): count for mid, count in bma_sampling_counts.items()}
                if bma_sampling_counts is not None
                else None
            ),
            "forecast_qc_rules": qc_rules,
            "forecast_qc_overrides": qc_overrides,
            "forecast_qc_baselines": baselines,
            "water_level_datum_masl": water_level_datum_masl,
            "sampling_seeds": (
                {
                    str(model_id): int(abs(hash(str(model_id)))) % (2**31)
                    for model_id in (weights or {})
                }
                if strategy == "bma"
                else None
            ),
        }
        missing = any(
            fc.evidence is None or fc.evidence.status is EvidenceStatus.INCOMPLETE
            for fc in contributors
        )
        return _evidence(
            payload,
            artifact_bytes=None,
            missing_reason="contributor_evidence_incomplete" if missing else None,
        )
    except Exception as exc:
        log.warning("forecast_evidence.capture_failed", error=str(exc))
        return incomplete_evidence(f"capture_failed:{type(exc).__name__}")


def restore_snapshot(snapshot: bytes) -> dict[str, object]:
    return json.loads(zlib.decompress(snapshot))


def restore_frame(encoded: str) -> pl.DataFrame:
    return pl.read_ipc(BytesIO(base64.b64decode(encoded)))
