"""Ensemble fan-out for member-suffixed future forcing (epic-088 M2 / M3).

A model declared ``ensemble_mode == ENSEMBLE`` is fed future-known forcing whose
member draws are delivered as suffixed columns (``precipitation_0``,
``precipitation_1``, …). ``fan_out_ensemble`` explodes those columns into one
per-member :class:`StationModelInputs`, runs each through a 1-member
``predict_fn``, and reassembles the K single-trajectory outputs into a single
K-member :class:`ForecastEnsemble` per target parameter.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.types.ensemble import ForecastEnsemble

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Iterable

    from sapphire_flow.types.model import ModelArtifact, StationModelInputs


_STATEFUL_ENSEMBLE_UNSUPPORTED = (
    "ensemble fan-out does not support warm-up/prior state; "
    "stateful ensemble models need per-member state (unsupported)"
)


def reject_stateful_ensemble_states(states: list[bytes | None]) -> None:
    """Fail loudly if any per-member fan-out state is non-``None``.

    Combining N per-member warm-up states into one aggregate is ill-defined, so a
    stateful ensemble model is unsupported. All-``None`` (stateless models) is the
    no-loss case and passes. Shared by the operational fan-out and the onboarding
    conformance harness so both reject the same models with the same message.
    """
    if any(state is not None for state in states):
        raise ModelOutputError(_STATEFUL_ENSEMBLE_UNSUPPORTED)


def reject_prior_state_for_fanout(prior_state: bytes | None) -> None:
    """Fail loudly if an aggregate ``prior_state`` is fed to the ensemble fan-out.

    The fan-out forwards the SAME aggregate prior state into every member's
    ``predict``; there is no way to split one aggregate state per member, so a
    stateful ensemble model on the INPUT side is unsupported. ``None`` (stateless
    models) is the no-loss case and passes. Mirrors the output-side
    :func:`reject_stateful_ensemble_states` and raises with the same message.
    """
    if prior_state is not None:
        raise ModelOutputError(_STATEFUL_ENSEMBLE_UNSUPPORTED)


class EnsemblesOnly:
    """Adapt a model ``predict`` (returns ``(ensembles, state)``) into a fan-out
    ``predict_fn`` that returns only the ensembles dict.

    Each per-member ``new_state`` returned by ``predict`` is captured in
    :attr:`states`. Combining N per-member warm-up states into one aggregate state
    is ill-defined, so the caller inspects :attr:`states` after the fan-out: an
    all-``None`` result is the stateless case (report ``None``, no loss); any
    non-``None`` state means a stateful ensemble model, which is unsupported and
    must fail loudly rather than silently drop the state.
    """

    def __init__(
        self,
        predict: Callable[..., tuple[dict[str, ForecastEnsemble], bytes | None]],
        artifact: ModelArtifact,
        prior_state: bytes | None = None,
    ) -> None:
        self._predict = predict
        self._artifact = artifact
        self._prior_state = prior_state
        self.states: list[bytes | None] = []

    def __call__(
        self, inputs: StationModelInputs, rng: random.Random
    ) -> dict[str, ForecastEnsemble]:
        ensembles, state = self._predict(
            self._artifact, inputs, rng, prior_state=self._prior_state
        )
        self.states.append(state)
        return ensembles


def ensembles_only(
    predict: Callable[..., tuple[dict[str, ForecastEnsemble], bytes | None]],
    artifact: ModelArtifact,
    prior_state: bytes | None = None,
) -> EnsemblesOnly:
    """Factory for :class:`EnsemblesOnly` (a state-capturing fan-out ``predict_fn``)."""
    return EnsemblesOnly(predict, artifact, prior_state)


def fan_out_ensemble(
    predict_fn: Callable[
        [StationModelInputs, random.Random], dict[str, ForecastEnsemble]
    ],
    inputs: StationModelInputs,
    rng: random.Random,
    *,
    future_features: frozenset[str],
) -> dict[str, ForecastEnsemble]:
    member_sets = _member_index_sets(
        columns=inputs.data.future_dynamic.columns,
        future_features=future_features,
    )

    if not member_sets:
        # Bare / non-ensemble forcing: single-trajectory no-op.
        return predict_fn(inputs, rng)

    member_indices = _reconcile_member_indices(member_sets)
    fanned_features = frozenset(member_sets)

    per_parameter_frames: dict[str, list[pl.DataFrame]] = {}
    templates: dict[str, ForecastEnsemble] = {}

    for member in member_indices:
        member_inputs = _slice_member(inputs, features=fanned_features, member=member)
        member_ensembles = predict_fn(member_inputs, rng)
        for parameter, ensemble in member_ensembles.items():
            # Preserve the SOURCE ICON member index (0-based, parsed from the
            # ``{feat}_{k}`` suffix). 0-based contiguous ids match the pooling
            # offset convention in ``combine_ensembles_pooled`` — a 1-based id
            # (``member + 1``) collides with a native 0-based ensemble at the
            # offset boundary, losing one member in multi-model forecasts.
            frame = ensemble.values.with_columns(
                pl.lit(member).cast(pl.Int32).alias("member_id")
            )
            per_parameter_frames.setdefault(parameter, []).append(frame)
            templates.setdefault(parameter, ensemble)

    return {
        parameter: ForecastEnsemble.from_members(
            station_id=templates[parameter].station_id,
            issued_at=templates[parameter].issued_at,
            parameter=templates[parameter].parameter,
            units=templates[parameter].units,
            time_step=templates[parameter].time_step,
            values=pl.concat(frames),
            model_id=templates[parameter].model_id,
        )
        for parameter, frames in per_parameter_frames.items()
    }


def _member_index_sets(
    *,
    columns: list[str],
    future_features: frozenset[str],
) -> dict[str, frozenset[int]]:
    member_sets: dict[str, frozenset[int]] = {}
    for feat in future_features:
        pattern = re.compile(rf"^{re.escape(feat)}_(\d+)$")
        members = frozenset(
            int(match.group(1)) for col in columns if (match := pattern.match(col))
        )
        if members:
            member_sets[feat] = members
    return member_sets


def _reconcile_member_indices(member_sets: dict[str, frozenset[int]]) -> list[int]:
    reference = next(iter(member_sets.values()))
    if any(members != reference for members in member_sets.values()):
        detail = ", ".join(
            f"{feat}={sorted(members)}" for feat, members in sorted(member_sets.items())
        )
        raise ValueError(
            "ragged / inconsistent ensemble member sets across future features: "
            f"{detail}"
        )
    return sorted(reference)


def _slice_member(
    inputs: StationModelInputs,
    *,
    features: Iterable[str],
    member: int,
) -> StationModelInputs:
    features = frozenset(features)
    columns = inputs.data.future_dynamic.columns
    # Member-k's slice replaces the fanned columns: those matching ``^{feat}_(\d+)$``
    # for a fanned feature, plus any leftover BARE column named exactly ``feat`` (the
    # member slice, aliased to ``feat``, wins). ``ensemble_mode`` is declared per
    # future variable, so ``future_dynamic`` may ALSO carry bare single-mode columns
    # (a covariate with no suffix and not a fanned feature) — those are shared across
    # members and must be carried through verbatim, not dropped.
    fanned_patterns = [re.compile(rf"^{re.escape(feat)}_(\d+)$") for feat in features]
    fanned_columns = {
        col for col in columns if any(p.match(col) for p in fanned_patterns)
    }
    carried = [
        pl.col(col)
        for col in columns
        if col != "timestamp" and col not in fanned_columns and col not in features
    ]
    future_dynamic = inputs.data.future_dynamic.select(
        "timestamp",
        *(pl.col(f"{feat}_{member}").alias(feat) for feat in features),
        *carried,
    )
    member_data = dataclasses.replace(inputs.data, future_dynamic=future_dynamic)
    return dataclasses.replace(inputs, data=member_data)
