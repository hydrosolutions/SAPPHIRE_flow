from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import (
    ConfigurationError,
    UnsupportedModelRequirementError,
)
from sapphire_flow.services.caravan_statics import declared_static_naming
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
    """Read classification declarations off the RAW model and copy them
    onto the ADAPTED model, once, at discovery time.

    Plan 155 D16 fixer round (blocker finding — Codex traced the path and
    found "raw model = caravan, adapted model = native"): a real FI model
    is wrapped by `ForecastInterfaceAdapter`
    (`adapters/forecast_interface.py`) before anything downstream ever
    sees it, and that adapter forwards NOTHING by default (see its
    `config_hash` property's own docstring — "this class has no
    `__getattr__` passthrough"). `model_tier`/`alert_eligibility` already
    survive this by being copied here, onto the adapted instance, exactly
    once; `static_naming` (`types/enums.py::StaticNaming`,
    `services/caravan_statics.py::declared_static_naming`) gets the
    identical treatment so every one of the five Plan 155 call sites that
    branch on `declared_static_naming(model)` sees the RAW model's own
    declaration, not the adapter's un-forwarded default.
    """
    tier = _declared_model_tier(model_id, raw_model)
    eligibility = _declared_alert_eligibility(model_id, raw_model)
    static_naming = declared_static_naming(raw_model)
    adapted_model.model_tier = tier  # type: ignore[attr-defined]
    adapted_model.alert_eligibility = eligibility  # type: ignore[attr-defined]
    adapted_model.static_naming = static_naming  # type: ignore[attr-defined]


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
        except UnsupportedModelRequirementError:
            # Plan 156: one model's unsupported (multi-FUTURE-FORCED-
            # resolution) FI requirement must not darken the WHOLE registry —
            # unlike ConfigurationError below, this entry point is skipped
            # and discovery continues for the rest.
            log.exception("model_discovery_unsupported_requirement", model_id=ep.name)
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
