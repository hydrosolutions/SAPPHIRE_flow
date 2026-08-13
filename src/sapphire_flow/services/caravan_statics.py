"""T2 (Plan 155, closes G8) — Caravan <-> HydroATLAS static alias resolution.

aquacast (``cmal_pool_PT`` and any other Caravan-trained model) declares its
static inputs under Caravan's OWN names (e.g. ``slope``); our imported Swiss
package (Plan 155 T1/T1b) stores the raw values under a ``caravan:``-
namespaced key, ``caravan:`` + the raw parquet column -- a HydroATLAS code
for 21 of PT's 50 statics, the bare Caravan name itself for the other 29
"direct" ones (D15).

D15's resolution rule, in full: a declared static ``X`` resolves to
``caravan:`` + (``CARAVAN_ALIAS[X]`` if aliased, else ``X``) -- and there is
NO fallback to a bare, non-namespaced ``X``. Falling back would hand a
Caravan-declaring model a same-named CAMELS-CH attribute of a DIFFERENT
derivation (the exact silent failure D15 exists to prevent -- for ``area``
specifically, one that would rescale every discharge through the
``m3/s <-> mm/day`` conversion).

D16 (post-implementation-review fix): whether a model gets this strict,
no-fallback treatment AT ALL is the MODEL's own declaration --
``declared_static_naming`` reads a ``static_naming`` class attribute exactly
like ``model_tier`` / ``alert_eligibility`` (``services/model_registry.py``),
defaulting to ``StaticNaming.NATIVE`` (today's behaviour, byte-for-byte
unchanged) when a model does not declare it. Every call site in this codebase
must branch on this BEFORE calling ``available_declared_static_keys`` /
``project_declared_static_attributes`` -- those two functions assume the
caller already decided the model is Caravan-declaring; they must never be
invoked, nor their raw-key-set/frame output unioned with the legacy bare
keys, for a ``NATIVE`` model.

``project_declared_static_attributes`` is scoped to a caller-supplied set of
DECLARED names (a model's own ``data_requirements.static_features``) rather
than projecting every ``caravan:`` key unconditionally -- an unconditional
projection would silently shadow an INCUMBENT (non-Caravan) model's
same-named bare attribute (e.g. its own ``area``) even though that model
never asked for Caravan's derivation. Scoping to declared names keeps the
projection inert for every model that doesn't declare the name (`_static_
inputs` in ``adapters/forecast_interface.py`` already discards anything it
doesn't select for -- "extra columns are inert" -- so this module needs no
change there).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.basin import is_missing_static_value
from sapphire_flow.types.enums import StaticNaming

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

CARAVAN_PREFIX: Final[str] = "caravan:"

# Canonical Caravan/PT static name -> raw parquet column (a HydroATLAS code
# in every one of these 21 cases). Verified against the modeller's own
# `aquacast/docs/static_attributes.md` (Plan 155 G8) and independently
# confirmed against the Nepal fixture's naming. Every OTHER declared static
# name is DIRECT: it already equals the raw parquet column (either one of
# Caravan's own bare climate-index names -- e.g. "area", "p_mean" -- or a
# HydroATLAS code used as-is), so no entry is needed here for those.
CARAVAN_ALIAS: Final[dict[str, str]] = {
    "slope": "slp_dg_sav",
    "stream_gradient": "sgr_dk_sav",
    "lake_fraction": "lka_pc_sse",
    "air_temperature": "tmp_dc_syr",
    "precip_annual": "pre_mm_syr",
    "pet_annual": "pet_mm_syr",
    "aet_annual": "aet_mm_syr",
    "aridity_index": "ari_ix_sav",
    "climate_moisture_index": "cmi_ix_syr",
    "snow_cover": "snw_pc_syr",
    "snow_cover_max": "snw_pc_smx",
    "glacier_fraction": "gla_pc_sse",
    "cropland_fraction": "crp_pc_sse",
    "pasture_fraction": "pst_pc_sse",
    "clay_fraction": "cly_pc_sav",
    "silt_fraction": "slt_pc_sav",
    "sand_fraction": "snd_pc_sav",
    "soil_organic_carbon": "soc_th_sav",
    "soil_water_content": "swc_pc_syr",
    "karst_fraction": "kar_pc_sse",
    "irrigated_fraction": "ire_pc_sse",
}


def resolve_caravan_static_key(name: str) -> str:
    """D15's resolution rule for ONE declared static name -- the PRIMARY key
    a caller looks up for it; there is no bare-name fallback. For an
    ALIASED name this is the raw HydroATLAS-code column; the bare
    ``caravan:``-namespaced declared name is a distinct SECONDARY key a
    delivered package might also carry for the same concept -- see
    :func:`_collision_keys`, which the collision-detecting callers use
    instead of this function alone."""
    return f"{CARAVAN_PREFIX}{CARAVAN_ALIAS.get(name, name)}"


def _collision_keys(name: str) -> tuple[str, str | None]:
    """The primary resolved key (:func:`resolve_caravan_static_key`), plus
    -- only for an ALIASED name -- the secondary key T2's collision guard
    must also check: the bare ``caravan:``-namespaced declared name itself.
    A delivered package could in principle carry BOTH the raw HydroATLAS
    code and Caravan's own canonical name for the same concept; a resolver
    that only ever looks up the primary key can never observe that (Plan
    155 review finding -- "a guard driven by a single-valued resolver is
    dead code"). Direct (unaliased) names have no distinct secondary key:
    the primary key already IS the bare declared name."""
    primary = resolve_caravan_static_key(name)
    alias = CARAVAN_ALIAS.get(name)
    if alias is None:
        return primary, None
    return primary, f"{CARAVAN_PREFIX}{name}"


def _values_agree(a: Any, b: Any) -> bool:
    """T2 collision semantics: equal FINITE values are accepted; equal
    infinities must NOT pass (the review's explicit finding)."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            if not (math.isfinite(a) and math.isfinite(b)):
                return False
        except TypeError:
            return False
    return a == b


def declared_static_naming(model: object) -> StaticNaming:
    """Plan 155 D16: read a model's opt-in static-naming declaration off
    ``model.static_naming`` (a class attribute, exactly like ``model_tier``
    / ``alert_eligibility``, ``services/model_registry.py``). Defaults to
    ``StaticNaming.NATIVE`` -- every model that does not declare otherwise
    keeps today's behaviour byte-for-byte unchanged, so the strict Caravan
    regime is opt-in, never inferred."""
    declared = getattr(model, "static_naming", None)
    return declared if isinstance(declared, StaticNaming) else StaticNaming.NATIVE


def available_declared_static_keys(
    attributes: Mapping[str, Any] | None,
    declared_names: Iterable[str],
) -> frozenset[str]:
    """The subset of ``declared_names`` that resolve to a present, non-null
    value in ``attributes`` via :func:`resolve_caravan_static_key`. Used at
    the COMPATIBILITY boundary (onboarding's raw-key-set check, and
    training's own missing-static gate) ahead of building any frame --
    Plan 155 T2's "the projection must cover the compatibility path, not
    just the frame".

    D16: callers must invoke this ONLY for a ``StaticNaming.CARAVAN``
    model, and must NOT union its result with the model's raw (bare) key
    set -- doing so re-admits the exact bare-fallback D15 forbids.
    """
    if not attributes:
        return frozenset()
    return frozenset(
        name
        for name in declared_names
        if not is_missing_static_value(attributes.get(resolve_caravan_static_key(name)))
    )


def project_declared_static_attributes(
    attributes: Mapping[str, Any] | None,
    declared_names: Iterable[str],
    *,
    station_code: str | None = None,
) -> dict[str, Any]:
    """Build the model-facing static projection: every ORIGINAL key of
    ``attributes`` is preserved untouched, and each name in
    ``declared_names`` additionally resolves through
    :func:`resolve_caravan_static_key` into a BARE column under that exact
    declared name -- so an unmodified ``_static_inputs``
    (``adapters/forecast_interface.py``) finds a column named exactly what
    the model declared, populated with CARAVAN's value (D15: no bare
    fallback -- this intentionally overrides any same-named legacy/CAMELS-CH
    value already in ``attributes`` for exactly the names the model itself
    asked for; every other key, collision-prone or not, is left alone).

    D15's "no bare fallback" is enforced both ways: when the ``caravan:``
    source is PRESENT, it overrides any pre-existing bare key of the same
    declared name; when it is ABSENT, any pre-existing bare key of that
    name is explicitly REMOVED from the projection rather than left
    standing in for it (the Plan 155 post-implementation-review BLOCKER --
    ``dict(attributes)`` seeding this dict must not let a stale
    CAMELS-CH-derived value silently pass as if it were Caravan's).

    Guards the collision T2 requires: for an ALIASED name, a delivered
    package might carry BOTH the raw HydroATLAS code and Caravan's own
    bare name for the same concept (:func:`_collision_keys`); equal FINITE
    values are accepted, differing values -- and equal infinities -- raise
    loudly naming station, alias, canonical name and both values, rather
    than silently pick one.

    D16: callers must invoke this ONLY for a ``StaticNaming.CARAVAN``
    model; a ``NATIVE`` model must never have its attributes passed
    through this function.
    """
    if not attributes:
        return {}
    projected: dict[str, Any] = dict(attributes)
    for name in declared_names:
        primary_key, secondary_key = _collision_keys(name)
        candidate_keys = [
            key
            for key in (primary_key, secondary_key)
            if key is not None and not is_missing_static_value(attributes.get(key))
        ]
        if not candidate_keys:
            # No caravan: source at all -- missing, not a stale bare
            # fallback. The caller's own compatibility gate reports this.
            projected.pop(name, None)
            continue
        first_key = candidate_keys[0]
        value = attributes[first_key]
        for other_key in candidate_keys[1:]:
            other_value = attributes[other_key]
            if not _values_agree(value, other_value):
                station = station_code or "<unknown>"
                raise ConfigurationError(
                    f"station {station!r}: static {name!r} resolves to "
                    f"differing values from {first_key!r} ({value!r}) and "
                    f"{other_key!r} ({other_value!r}) -- refusing to "
                    "silently pick one (Plan 155 T2 collision semantics)"
                )
        projected[name] = value
    return projected


@dataclass(frozen=True, kw_only=True, slots=True)
class StaticCoverageGap:
    """One station's shortfall against T1's exit gate (Plan 155): "every
    station in the T0a manifest resolves all 50 of PT's statics to
    non-null, finite values"."""

    station_code: str
    missing_statics: frozenset[str]


def verify_static_coverage(
    basins_by_code: Mapping[str, Mapping[str, Any] | None],
    declared_names: Iterable[str],
) -> tuple[StaticCoverageGap, ...]:
    """T1's exit gate, made a checkable function instead of a one-off
    script (Plan 155 post-implementation-review finding: `is_missing_
    static_value` alone "rejects only None and NaN, so infinities pass
    it"). For every ``(station_code, basin.attributes)`` pair, resolves
    each of ``declared_names`` via :func:`resolve_caravan_static_key` and
    reports it missing when the value is absent, ``None``, NaN, OR a
    non-finite float (an infinity) -- `math.isfinite`, exactly as the exit
    gate specifies.

    Returns one :class:`StaticCoverageGap` per station with at least one
    missing static, station codes sorted, so a caller asserts ``()`` for
    full coverage and gets an actionable report otherwise.
    """
    declared = frozenset(declared_names)
    gaps: list[StaticCoverageGap] = []
    for code in sorted(basins_by_code):
        attrs = basins_by_code[code]
        missing: set[str] = set()
        for name in declared:
            value = attrs.get(resolve_caravan_static_key(name)) if attrs else None
            non_finite = isinstance(value, float) and not math.isfinite(value)
            if is_missing_static_value(value) or non_finite:
                missing.add(name)
        if missing:
            gaps.append(
                StaticCoverageGap(station_code=code, missing_statics=frozenset(missing))
            )
    return tuple(gaps)
