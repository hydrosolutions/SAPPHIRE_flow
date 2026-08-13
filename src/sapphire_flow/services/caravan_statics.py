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

from typing import TYPE_CHECKING, Any, Final

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.basin import is_missing_static_value

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
    """D15's resolution rule for ONE declared static name -- the ONLY key a
    caller may look up for it; there is no bare-name fallback."""
    return f"{CARAVAN_PREFIX}{CARAVAN_ALIAS.get(name, name)}"


def available_declared_static_keys(
    attributes: Mapping[str, Any] | None,
    declared_names: Iterable[str],
) -> frozenset[str]:
    """The subset of ``declared_names`` that resolve to a present, non-null
    value in ``attributes`` via :func:`resolve_caravan_static_key`. Used at
    the COMPATIBILITY boundary (onboarding's raw-key-set check, and
    training's own missing-static gate) ahead of building any frame --
    Plan 155 T2's "the projection must cover the compatibility path, not
    just the frame"."""
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

    Guards the one collision a fixed, one-way alias table cannot itself
    prevent: two DIFFERENT ``caravan:``-namespaced source keys resolving to
    the SAME declared name with DIFFERING values (an internally
    inconsistent package) raise loudly rather than silently pick one.
    """
    if not attributes:
        return {}
    projected: dict[str, Any] = dict(attributes)
    resolved_from: dict[str, tuple[str, Any]] = {}
    for name in declared_names:
        source_key = resolve_caravan_static_key(name)
        if source_key not in attributes:
            continue  # missing -- the caller's own compatibility gate reports this
        value = attributes[source_key]
        prior = resolved_from.get(name)
        if prior is not None and prior[1] != value:
            station = station_code or "<unknown>"
            raise ConfigurationError(
                f"station {station!r}: static {name!r} resolves to "
                f"differing values from {prior[0]!r} ({prior[1]!r}) and "
                f"{source_key!r} ({value!r}) -- refusing to silently pick "
                "one (Plan 155 T2 collision semantics)"
            )
        resolved_from[name] = (source_key, value)
        projected[name] = value
    return projected
