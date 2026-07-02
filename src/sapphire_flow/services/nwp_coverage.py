"""Shared NWP future-forcing coverage validation (Plan 090 D1/D2/D3).

A model that declares ``future_dynamic_features`` must receive at least its own
``forecast_horizon_steps`` clean future daily buckets for EVERY required variable
AND every required member, or it must NOT forecast — otherwise a short/partial
NWP frame silently truncates the horizon (``NwpRegression`` forecasts
``horizon = len(future_times)``, so a 1-row frame becomes a 1-step forecast).

This module is the single source of truth for that check, shared by the STATION
path (``run_station_forecast``) and the GROUP path (``run_group_forecast``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from sapphire_flow.types.enums import EnsembleMode


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageResult:
    """Outcome of a future-forcing coverage check for one model.

    ``available_steps`` is the fewest clean (non-null) future daily rows found
    across every required feature/member column — 0 when a required feature (or,
    for an ensemble model, its member-suffixed columns) is absent. ``detail`` is
    a short human-readable reason for logging.
    """

    adequate: bool
    available_steps: int
    detail: str


def col_matches_feature(col: str, feature: str) -> bool:
    """True if ``col`` is ``feature`` itself or a ``{feature}_{member}`` column.

    Ensemble ``future_dynamic`` frames carry member-suffixed columns
    (``precipitation_0`` .. ``precipitation_20``); deterministic frames carry the
    bare feature name. The member suffix is an integer, so a bare multi-word
    feature (e.g. ``snow_depth``) is not mistaken for a member column.
    """
    if col == feature:
        return True
    if col.startswith(f"{feature}_"):
        return col[len(feature) + 1 :].isdigit()
    return False


def _member_indices(columns: list[str], feature: str) -> frozenset[int]:
    """The set of member indices ``k`` present as ``{feature}_{k}`` columns."""
    pattern = re.compile(rf"^{re.escape(feature)}_(\d+)$")
    return frozenset(
        int(match.group(1)) for col in columns if (match := pattern.match(col))
    )


def _nonnull_count(future_dynamic: pl.DataFrame, col: str) -> int:
    return int(future_dynamic.select(pl.col(col).is_not_null().sum()).item())


def assess_future_coverage(
    future_dynamic: pl.DataFrame,
    *,
    required_features: frozenset[str],
    required_steps: int,
    ensemble_mode: EnsembleMode,
) -> CoverageResult:
    """Assess whether ``future_dynamic`` covers a model's future-forcing needs.

    Plan 090 D1: coverage is the number of future daily buckets available for
    every required NWP variable AND every required member.

    For an ``ENSEMBLE`` model each required feature must carry a NON-EMPTY,
    IDENTICAL member-suffixed set (a required feature present only as a bare
    column is inadequate — the fan-out would silently reuse the single bare value
    for every member). For a ``SINGLE`` model a bare or suffixed column is
    acceptable. In both cases coverage is short when the fewest clean rows across
    the required columns is ``< required_steps``.
    """
    if not required_features:
        return CoverageResult(
            adequate=True, available_steps=required_steps, detail="no future features"
        )

    columns = future_dynamic.columns
    is_ensemble = ensemble_mode is EnsembleMode.ENSEMBLE

    if is_ensemble:
        member_sets: dict[str, frozenset[int]] = {}
        for feature in sorted(required_features):
            members = _member_indices(columns, feature)
            if not members:
                return CoverageResult(
                    adequate=False,
                    available_steps=0,
                    detail=(
                        f"ensemble model requires member-suffixed columns for "
                        f"'{feature}'; none present (bare-only or absent)"
                    ),
                )
            member_sets[feature] = members
        reference = next(iter(member_sets.values()))
        if any(members != reference for members in member_sets.values()):
            detail = ", ".join(
                f"{feature}={sorted(members)}"
                for feature, members in sorted(member_sets.items())
            )
            return CoverageResult(
                adequate=False,
                available_steps=0,
                detail=f"inconsistent ensemble member sets across features: {detail}",
            )

    counts: list[int] = []
    for feature in sorted(required_features):
        if is_ensemble:
            cols = [
                col
                for col in columns
                if re.fullmatch(rf"{re.escape(feature)}_\d+", col)
            ]
        else:
            cols = [col for col in columns if col_matches_feature(col, feature)]
            if not cols:
                return CoverageResult(
                    adequate=False,
                    available_steps=0,
                    detail=f"required feature '{feature}' absent",
                )
        counts.extend(_nonnull_count(future_dynamic, col) for col in cols)

    available = min(counts) if counts else 0
    return CoverageResult(
        adequate=available >= required_steps,
        available_steps=available,
        detail=f"min clean future rows={available}, required={required_steps}",
    )
