"""Plan 159 T1 — the aquacast shim: zero-argument construction + the SAP3 boundary.

Two things force this class to exist, both verified against the real package:

1. **Construction.** `discover_models` builds every entry point with NO arguments
   (`services/model_registry.py`), while `AquacastModel.__init__` requires a
   `template` (`aquacast/operational/model.py`). SAP3 therefore cannot instantiate
   it, and something must bind the config at import time. That is what a
   zero-argument subclass per trained config is for.
2. **Discovery.** `discover_models()` sees only INSTALLED entry points, and aquacast
   declares none at all.

Two further boundaries are handled here by CHOICE, not necessity (Plan 159 D17 holds
the reasoning): SAP3 does have `area`, so the unit conversion could live in the FI
adapter. Keeping it here keeps `adapters/forecast_interface.py` — the single
SAP3<->FI boundary — free of per-model special cases.

**Not fixed here, deliberately:** `cmal_pool_PT` declares `future_steps=15` even
though the modeller relaxed the trained horizon to a *maximum*. The FI contract has no
"at most" form, so a strict provider still refuses a short horizon. The shim must NOT
paper over that by declaring a smaller number — that would silently claim a capability
the artifact does not advertise. See
`docs/fi-issues/002-future-steps-at-most-semantics.md`.
"""

from __future__ import annotations

import importlib
from importlib import resources
from typing import TYPE_CHECKING, Any, ClassVar, Final

from sapphire_flow.types.enums import AlertEligibility, ModelTier, StaticNaming

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

_CONFIG_PACKAGE: Final[str] = "sapphire_flow.models.aquacast.configs"

# aquacast declares `mean_temperature`; SAP3's canonical dynamic vocabulary is
# {"precipitation", "temperature"} (`config/deployment.py`). One entry, not a general
# mechanism: a second divergence should be a second explicit line, not a pattern.
AQUACAST_TO_CANONICAL_NAME: Final[dict[str, str]] = {"mean_temperature": "temperature"}
CANONICAL_TO_AQUACAST_NAME: Final[dict[str, str]] = {
    canonical: aquacast for aquacast, canonical in AQUACAST_TO_CANONICAL_NAME.items()
}


def _config_path(filename: str) -> Path:
    """The vendored trained config, shipped as package data (Plan 152 D1).

    Read at IMPORT time because the adapter computes `data_requirements` during
    construction and `discover_models` constructs with no arguments — there is no
    later hook to supply it.
    """
    return resources.files(_CONFIG_PACKAGE).joinpath(filename)  # type: ignore[return-value]


class AquacastShim:
    """Base for one trained aquacast config.

    Subclasses set `CONFIG_FILENAME` and the classification attributes
    `discover_models` requires. Instantiating this base directly is a programming
    error — it has no config to bind.
    """

    CONFIG_FILENAME: ClassVar[str]

    # `discover_models` requires both, via MODEL_TIERS/ALERT_ELIGIBILITIES or these
    # attributes. Declared per subclass so a new artifact must state its own.
    model_tier: ClassVar[ModelTier]
    alert_eligibility: ClassVar[AlertEligibility]

    # Plan 155 D16: aquacast's statics are Caravan-named, so this model opts into the
    # strict `caravan:`-namespaced, no-bare-fallback resolution. Plan 155 records the
    # shim as the natural owner of this flag, since it already binds the config.
    static_naming: ClassVar[StaticNaming] = StaticNaming.CARAVAN

    def __init__(self) -> None:
        cls = type(self)
        filename = getattr(cls, "CONFIG_FILENAME", None)
        if not filename:
            raise TypeError(
                f"{cls.__name__} must set CONFIG_FILENAME — AquacastShim binds a "
                "trained config at import time and cannot be constructed without one"
            )
        # Imported lazily so `sapphire_flow` stays importable WITHOUT the `aquacast`
        # extra. discover_models tolerates an entry point that fails to construct, but
        # an import-time failure here would break unrelated model discovery.
        # `aquacast` is an OPTIONAL extra, absent from the base install that CI
        # type-checks. Imported through `importlib` rather than a `from` statement so
        # the optionality is explicit and no unresolved-import diagnostics have to be
        # silenced: the module genuinely is not present in every environment.
        config_mod: Any = importlib.import_module("aquacast.operational.config")
        model_mod: Any = importlib.import_module("aquacast.operational.model")

        template: Any = config_mod.ModelTemplate.from_yaml(str(_config_path(filename)))
        self._inner: Any = model_mod.AquacastModel(template)

    @property
    def artifact_scope(self) -> object:
        return self._inner.artifact_scope

    @property
    def input_requirement(self) -> object:
        return self._inner.input_requirement


class CmalPoolPT(AquacastShim):
    """`cmal_pool_PT` — the pooled CMAL artifact (12,952 basins).

    DAILY only, precipitation + temperature, 50 Caravan-named statics, quantile and
    deterministic heads, ArtifactScope.GROUP.
    """

    CONFIG_FILENAME = "cmal_pool_pt.yaml"
    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.SKILL_FORECAST
