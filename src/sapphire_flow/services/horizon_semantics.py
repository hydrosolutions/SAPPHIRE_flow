"""How many future steps a model actually requires.

A model declares `future_steps`, but that number alone cannot say whether it is a
**floor** ("fewer is an error") or a **ceiling** ("fewer is acceptable and yields a
shorter forecast"). FI v0.1.20 added `FutureKnownVariable.horizon_semantics` +
`min_future_steps` so a model can say which it means — the contract gap SAP3 raised in
`docs/fi-issues/002-future-steps-at-most-semantics.md`.

Resolution:

1. **The model's own declaration wins.** `horizon_semantics=AT_MOST` -> use its
   `min_future_steps`; an explicit `EXACT` -> strict.
2. **Otherwise strict** — the declared number, unchanged. A model that says nothing is
   never silently truncated.

⚠️ **"Declared" means the model SET the field, not that the field has a value.** FI
>= 0.1.20 DEFAULTS `horizon_semantics` to `EXACT`, so reading the value alone would make
every model look explicitly strict. The FI adapter distinguishes them via pydantic's
`model_fields_set` and projects the result onto `ModelDataRequirements`
(`adapters/forecast_interface.py`); this module reads that projection first, because
an FI-discovered model is wrapped in an adapter that does not re-expose
`input_requirement`.

*History (Plan 241).* An interim provider opt-in table (`HORIZON_CEILING_FLOORS`)
used to sit between rungs 1 and 2, because the declaration could not reach this code
at all: the adapter dropped it, so rung 1 could never fire on any FI version. Both the
table and that rung were deleted once the declaration actually arrived —
`cmal_pool_pt` now resolves via `model_at_most`. Do not reintroduce a provider-side
table; if a model needs a different horizon contract, fix the declaration upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

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
    source: str  # "declared" | "model_at_most"

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
    # Plan 241 T2: an FI-discovered model is wrapped in `ForecastInterfaceAdapter`,
    # which consumes `input_requirement` internally and never re-exposes it — so the
    # walk below finds nothing and rung 1 could never fire. The adapter now projects
    # the resolved declaration onto `ModelDataRequirements`; prefer that when present.
    projected = getattr(model, "data_requirements", None)
    declared = getattr(projected, "declared_horizon_semantics", None)
    if declared == _AT_MOST:
        floor = getattr(projected, "declared_min_future_steps", None)
        if isinstance(floor, int) and not isinstance(floor, bool):
            return floor
        return None
    if declared is not None:
        # An explicit EXACT: strict, and the provider opt-in must not be consulted.
        return _DECLARED_EXACT

    # Native (non-adapter) models may expose the FI requirement directly.
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

    return RequiredSteps(
        steps=declared_steps, declared_steps=declared_steps, source="declared"
    )
