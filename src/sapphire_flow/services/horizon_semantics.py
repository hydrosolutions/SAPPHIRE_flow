"""Plan 159 T0d — INTERIM: how many future steps a model actually requires.

A model declares `future_steps`, but the ForecastInterface could not say whether that
number is a **floor** ("fewer is an error") or a **ceiling** ("fewer is acceptable and
yields a shorter forecast"). FI v0.1.20 fixes that with
`FutureKnownVariable.horizon_semantics`; until a model declares it, a provider has to
choose, and choosing "ceiling" for everyone would hand a strictly-15-day model a 5-day
input and get a plausible-but-wrong forecast back.

So resolution runs in this order, and **the interim rung disappears on its own**:

1. **The model's own declaration wins.** `horizon_semantics=AT_MOST` -> use its
   `min_future_steps`. Read defensively, because FI v0.1.19 has no such field.
2. **Otherwise the provider opt-in** (`HORIZON_CEILING_FLOORS`, `types/ids.py`), logged
   at WARNING on every use. That log is the retirement signal: when it stops appearing,
   the table can be deleted.
3. **Otherwise strict** — the declared number, unchanged, which is every model that
   opts into nothing.

**This module is DELETE-ON-ARRIVAL.** It exists because `cmal_pool_PT` declares
`future_steps=15` while its architecture accepts fewer, and the maintainer who can add
the declaration is away. When aquacast declares `AT_MOST`, rung 2 stops firing — delete
`HORIZON_CEILING_FLOORS` and this module's opt-in branch. Do not grow it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from sapphire_flow.types.ids import HORIZON_CEILING_FLOORS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sapphire_flow.types.ids import ModelId

log = structlog.get_logger(__name__)

_AT_MOST = "at_most"


@dataclass(frozen=True, kw_only=True, slots=True)
class RequiredSteps:
    """How many future steps to demand, and why.

    The reason is carried so callers can log a truncated run distinctly from a full
    one, rather than silently equating them.
    """

    steps: int
    declared_steps: int
    source: str  # "declared" | "model_at_most" | "provider_opt_in"

    @property
    def is_truncated(self) -> bool:
        return self.steps < self.declared_steps


# Distinguishes "the model declared EXACT" from "the model declared nothing". They are
# NOT the same: EXACT is a model asserting it genuinely needs its full horizon, and a
# provider opt-in must never override that. Conflating them is the silent-wrongness
# this whole design exists to prevent.
_DECLARED_EXACT = object()


def _values(mapping: Any) -> list[Any]:
    """`isinstance(x, dict)` narrows only to `dict[Unknown, Unknown]`, which makes
    every downstream value untyped. Funnel iteration through one explicitly-`Any`
    helper instead of scattering ignores through the walk."""
    return list(mapping.values())


def _model_declared_floor(model: object) -> int | object | None:
    """The model's OWN horizon declaration.

    Returns an ``int`` floor for ``AT_MOST``, ``_DECLARED_EXACT`` if it explicitly
    declares a strict horizon, or ``None`` if it declares nothing at all.

    Walks the FI requirement defensively: on FI v0.1.19 `horizon_semantics` does not
    exist, and a model may legitimately expose no `input_requirement` at all. Anything
    unreadable means "not declared", never a crash — this runs inside the forecast
    cycle, where an exception would take down the whole group.
    """
    requirement = getattr(model, "input_requirement", None)
    dynamic: Any = getattr(requirement, "dynamic", None)
    if not isinstance(dynamic, dict):
        return None

    floors: list[int] = []
    for spatial in _values(dynamic):
        data: Any = getattr(spatial, "data", None)
        if not isinstance(data, dict):
            continue
        for spec in _values(data):
            future_known: Any = getattr(spec, "future_known", None)
            if not isinstance(future_known, dict):
                continue
            for by_source in _values(future_known):
                if not isinstance(by_source, dict):
                    continue
                for variable in _values(by_source):
                    semantics = getattr(variable, "horizon_semantics", None)
                    declared = getattr(semantics, "value", None)
                    if declared is None:
                        return None  # FI < 0.1.20, or simply not declared
                    if declared != _AT_MOST:
                        # One EXACT variable makes the WHOLE model strict, and does so
                        # explicitly — the opt-in must not be consulted.
                        return _DECLARED_EXACT
                    floor = getattr(variable, "min_future_steps", None)
                    if isinstance(floor, int) and not isinstance(floor, bool):
                        floors.append(floor)
    # The binding floor across variables is the LARGEST: satisfying the least
    # tolerant variable satisfies the rest.
    return max(floors) if floors else None


def resolve_required_steps(
    model: object,
    model_id: ModelId,
    declared_steps: int,
    *,
    opt_in: dict[Any, int] | None = None,
) -> RequiredSteps:
    """Resolve the future-step requirement for one model. See the module docstring."""
    model_floor = _model_declared_floor(model)
    if model_floor is _DECLARED_EXACT:
        return RequiredSteps(
            steps=declared_steps, declared_steps=declared_steps, source="declared"
        )
    if isinstance(model_floor, int):
        return RequiredSteps(
            steps=min(model_floor, declared_steps),
            declared_steps=declared_steps,
            source="model_at_most",
        )

    table = HORIZON_CEILING_FLOORS if opt_in is None else opt_in
    provider_floor = table.get(model_id)
    if provider_floor is not None:
        log.warning(
            # INTERIM path (Plan 159 T0d). Its absence from the logs is the signal
            # that HORIZON_CEILING_FLOORS can be deleted.
            "horizon.provider_ceiling_opt_in",
            model_id=str(model_id),
            declared_steps=declared_steps,
            required_steps=provider_floor,
            detail=(
                "model declares no horizon_semantics; treating its declared horizon "
                "as a CEILING via the provider opt-in. Retire this entry once the "
                "model declares AT_MOST (ForecastInterface >= v0.1.20)."
            ),
        )
        return RequiredSteps(
            steps=min(provider_floor, declared_steps),
            declared_steps=declared_steps,
            source="provider_opt_in",
        )

    return RequiredSteps(
        steps=declared_steps, declared_steps=declared_steps, source="declared"
    )
