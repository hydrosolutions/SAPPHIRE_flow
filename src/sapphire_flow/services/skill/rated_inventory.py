from __future__ import annotations

import hashlib
import math
from dataclasses import fields
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.store.hindcast_period_codec import source_periods_content
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.rating_reference import canonical_content

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.observation import Observation


def rated_input_digest(
    hindcasts: list[HindcastForecast], observations: list[Observation]
) -> Sha256Digest:
    """Hash the exact scoring inputs, including values and mutable QC metadata."""
    digest = hashlib.sha256()

    def add(value: object) -> None:
        digest.update(canonical_content(value, dataclass_values=True).encode())
        digest.update(b"\n")

    def finite(value: object) -> object:
        return (
            repr(value)
            if isinstance(value, float) and not math.isfinite(value)
            else value
        )

    for hindcast in sorted(hindcasts, key=lambda item: str(item.id)):
        add(
            {
                field.name: source_periods_content(hindcast.source_periods)
                if field.name == "source_periods"
                else getattr(hindcast, field.name)
                for field in fields(hindcast)
                if field.name != "ensemble"
            }
        )
        ensemble = hindcast.ensemble
        add(
            {
                field.name: (
                    (
                        ensemble.period_definition.content
                        if ensemble.period_definition
                        else None
                    )
                    if field.name == "period_definition"
                    else getattr(ensemble, field.name).total_seconds()
                    if isinstance(getattr(ensemble, field.name), timedelta)
                    else getattr(ensemble, field.name)
                )
                for field in fields(ensemble)
                if field.name != "values"
            }
        )
        rows = ensemble.values.sort(ensemble.values.columns).to_dicts()
        for row in rows:
            add({key: finite(value) for key, value in row.items()})
    for observation in sorted(observations, key=lambda item: str(item.id)):
        add(
            {
                field.name: finite(getattr(observation, field.name))
                for field in fields(observation)
                if field.name != "qc_read_token"
            }
        )
    return Sha256Digest(value=digest.hexdigest())
