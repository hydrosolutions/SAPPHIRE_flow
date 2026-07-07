from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import AlertEligibility, ModelTier
from sapphire_flow.types.ids import ALERT_ELIGIBILITIES, MODEL_TIERS
from sapphire_flow.types.model import ModelRecord, ModelRegistryEntry

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import ModelStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId

log = structlog.get_logger()

_ENTRY_POINT_GROUP = "sapphire_flow.models"


def _derive_display_name(model_id: str) -> str:
    return " ".join(part.capitalize() for part in model_id.split("_"))


def _declared_model_tier(model_id: ModelId, model: object) -> ModelTier:
    configured = MODEL_TIERS.get(model_id)
    if configured is not None:
        return configured
    declared = getattr(model, "model_tier", None)
    if isinstance(declared, ModelTier):
        return declared
    raise ConfigurationError(
        f"model {model_id} must declare ModelTier via MODEL_TIERS or model_tier"
    )


def _declared_alert_eligibility(
    model_id: ModelId,
    model: object,
) -> AlertEligibility:
    configured = ALERT_ELIGIBILITIES.get(model_id)
    if configured is not None:
        return configured
    declared = getattr(model, "alert_eligibility", None)
    if isinstance(declared, AlertEligibility):
        return declared
    raise ConfigurationError(
        f"model {model_id} must declare AlertEligibility via "
        "ALERT_ELIGIBILITIES or alert_eligibility"
    )


def _assert_model_classification_declared(
    model_id: ModelId,
    raw_model: object,
    adapted_model: object,
) -> None:
    tier = _declared_model_tier(model_id, raw_model)
    eligibility = _declared_alert_eligibility(model_id, raw_model)
    adapted_model.model_tier = tier  # type: ignore[attr-defined]
    adapted_model.alert_eligibility = eligibility  # type: ignore[attr-defined]


def discover_models() -> dict[ModelId, ForecastModel]:
    # adapt_if_fi wraps a `forecastinterface` model into the SAP3
    # StationForecastModel boundary; native SAP3 models pass through unchanged
    # (idempotent). Wrapping HERE means every discovery caller — train-models,
    # onboard-model, the forecast cycle — gets a SAP3-compatible model with
    # `data_requirements`, not a raw FI object exposing only `input_requirement`.
    from sapphire_flow.adapters.forecast_interface import adapt_if_fi
    from sapphire_flow.types.ids import ModelId as _ModelId

    eps = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    result: dict[ModelId, ForecastModel] = {}
    for ep in eps:
        model_id = _ModelId(ep.name)
        try:
            cls = ep.load()
            raw_instance = cls()
            instance = adapt_if_fi(raw_instance)
            _assert_model_classification_declared(
                model_id,
                raw_instance,
                instance,
            )
            result[model_id] = instance
            log.info("model_discovered", model_id=ep.name)
        except ConfigurationError:
            log.exception("model_discovery_classification_failed", model_id=ep.name)
            raise
        except Exception:
            log.exception("model_discovery_failed", model_id=ep.name)
    return result


def build_registry_entry(
    model_id: ModelId,
    model: ForecastModel,
    registered_at: UtcDatetime,
) -> ModelRegistryEntry:
    display_name: str = getattr(model, "display_name", None) or _derive_display_name(
        str(model_id)
    )
    description: str = getattr(model, "description", "") or ""

    return ModelRegistryEntry(
        id=model_id,
        display_name=display_name,
        description=description,
        artifact_scope=model.artifact_scope,
        data_requirements=model.data_requirements,
        registered_at=registered_at,
    )


def register_models(
    models: dict[ModelId, ForecastModel],
    store: ModelStore,
    clock: Callable[[], UtcDatetime],
) -> list[ModelRegistryEntry]:
    now = clock()
    entries: list[ModelRegistryEntry] = []
    for model_id, model in models.items():
        entry = build_registry_entry(model_id, model, registered_at=now)
        record = ModelRecord(
            id=entry.id,
            display_name=entry.display_name,
            artifact_scope=entry.artifact_scope,
            description=entry.description,
            created_at=now,
        )
        store.register_model(record)
        entries.append(entry)
        log.info("model_registered", model_id=str(model_id))
    return entries
