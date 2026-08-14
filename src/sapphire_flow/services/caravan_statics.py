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

    from sapphire_flow.protocols.stores import BasinStore, StationStore
    from sapphire_flow.types.ids import StationId

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


def _is_finite_numeric(value: Any) -> bool:
    """T1's exit gate (and T2's collision guard) require a genuine numeric,
    finite value. ``isinstance(True, int)`` is ``True`` in Python, so a
    ``bool`` must be excluded explicitly or it would silently pass as
    "finite"; a ``str`` (e.g. an un-parsed parquet cell) is rejected the
    same way (Plan 155 fixer round: ``is_missing_static_value`` alone
    accepts both)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _values_agree(a: Any, b: Any) -> bool:
    """T2 collision semantics: BOTH operands must be numeric, non-boolean
    AND finite, and equal, to be accepted as agreeing (Plan 155 fixer
    round, major finding: the previous check only inspected finiteness
    when at least one operand was already a ``float``, so two equal
    strings, two equal booleans, or a bool/int pair equal under Python's
    own ``True == 1`` coercion silently 'agreed'; equal infinities must
    also NOT pass)."""
    if not (_is_finite_numeric(a) and _is_finite_numeric(b)):
        return False
    return a == b


_UNDECLARED: Final[object] = object()


def declared_static_naming(model: object) -> StaticNaming:
    """Plan 155 D16: read a model's opt-in static-naming declaration off
    ``model.static_naming`` (a class attribute, exactly like ``model_tier``
    / ``alert_eligibility``, ``services/model_registry.py``). Defaults to
    ``StaticNaming.NATIVE`` -- every model that does not declare otherwise
    keeps today's behaviour byte-for-byte unchanged, so the strict Caravan
    regime is opt-in, never inferred.

    Fixer round (major finding): the ABSENT case (no ``static_naming``
    attribute at all) is the only one that silently defaults -- a
    PRESENT-but-malformed declaration (e.g. the plain string ``"caravan"``
    instead of ``StaticNaming.CARAVAN``) now raises ``ConfigurationError``
    instead of being silently downgraded to ``NATIVE``, matching
    ``model_registry.py``'s ``_declared_model_tier``/
    ``_declared_alert_eligibility`` pattern of failing loudly on a bad
    declaration. A sentinel (not ``None``) distinguishes "never declared"
    from "declared as ``None``" -- both are malformed if declared, but only
    the former is a legitimate default.
    """
    declared = getattr(model, "static_naming", _UNDECLARED)
    if declared is _UNDECLARED:
        return StaticNaming.NATIVE
    if isinstance(declared, StaticNaming):
        return declared
    raise ConfigurationError(
        f"{type(model).__name__} declares static_naming={declared!r}, which is "
        "not a valid StaticNaming member -- expected StaticNaming.NATIVE or "
        "StaticNaming.CARAVAN (Plan 155 D16)"
    )


_NO_CANDIDATE: Final[object] = object()


def _resolve_declared_value(
    attributes: Mapping[str, Any],
    name: str,
    *,
    station_code: str | None = None,
) -> Any:
    """THE single collision-aware resolver for one declared static name,
    shared by :func:`available_declared_static_keys` (the compatibility-KEY
    boundary), :func:`project_declared_static_attributes` (the frame
    boundary) and, transitively, :func:`verify_static_coverage` (T1's exit
    gate). Plan 155 round-2 review MAJOR: a resolver duplicated per
    boundary had drifted -- compatibility checked only the PRIMARY key
    (:func:`resolve_caravan_static_key`) while the frame accepted the
    SECONDARY key alone too (:func:`_collision_keys`), so a station could
    pass one boundary and raise (or fail) at the other for the identical
    name. One function now serves all three call sites, so they can never
    again disagree.

    Returns :data:`_NO_CANDIDATE` when neither the primary nor (for an
    aliased name) the secondary key is present at all; raises
    :class:`ConfigurationError` when BOTH keys are present with differing
    (or equal-but-non-finite, or equal-but-non-numeric) values, per T2's
    collision semantics.

    Plan 155 fixer round (major finding): key PRESENCE and value
    USABILITY are deliberately distinct checks, evaluated in that order.
    The old implementation filtered out a null/NaN candidate BEFORE
    checking whether both keys existed, so a null direct value alongside a
    valid aliased value (or vice versa) silently resolved to the non-null
    one instead of being treated as a disagreement -- exactly the silent
    pick T2's collision guard exists to forbid. Presence is now `key in
    attributes` (a delivered-but-null column still counts as "present" and
    therefore participates in the agreement check); usability (finite,
    non-boolean, non-null) is checked only AFTER any multi-key collision
    has already been resolved or ruled out.

    Fixer round (major finding, independent Codex review): "both keys
    present" is NOT itself a collision when every present value is
    independently missing (`is_missing_static_value`) -- two keys that
    agree by both being ``None``/``NaN`` carry no genuine, differing
    information to disagree about. That case falls through to
    :data:`_NO_CANDIDATE` (mirroring the no-present-keys case) instead of
    raising. The existing "one null, one genuinely valid" raise (per
    :func:`_values_agree`, which itself rejects a null/NaN operand via
    `_is_finite_numeric`) is unchanged -- only the all-present-and-all-
    missing case is exempted from the collision check.
    """
    primary_key, secondary_key = _collision_keys(name)
    present_keys = [
        key
        for key in (primary_key, secondary_key)
        if key is not None and key in attributes
    ]
    if not present_keys:
        return _NO_CANDIDATE
    if all(is_missing_static_value(attributes[key]) for key in present_keys):
        return _NO_CANDIDATE
    first_key = present_keys[0]
    value = attributes[first_key]
    for other_key in present_keys[1:]:
        other_value = attributes[other_key]
        if not _values_agree(value, other_value):
            station = station_code or "<unknown>"
            raise ConfigurationError(
                f"station {station!r}: static {name!r} resolves to "
                f"differing values from {first_key!r} ({value!r}) and "
                f"{other_key!r} ({other_value!r}) -- refusing to "
                "silently pick one (Plan 155 T2 collision semantics)"
            )
    # Round-3 review (MINOR): admissibility must match what T1's exit gate
    # enforces, or compatibility/frame and coverage disagree about the SAME
    # value -- `is_missing_static_value` rejects only None/NaN, so an
    # infinity, string or bool under a `caravan:` key was reported
    # "available" and projected straight into a model's static frame while
    # `verify_static_coverage` would have rejected it. `area` is among the
    # 50, so a non-finite value there corrupts the m3/s <-> mm/day
    # conversion. One admissibility rule everywhere.
    return _NO_CANDIDATE if not _is_finite_numeric(value) else value


def available_declared_static_keys(
    attributes: Mapping[str, Any] | None,
    declared_names: Iterable[str],
    *,
    station_code: str | None = None,
) -> frozenset[str]:
    """The subset of ``declared_names`` that resolve to a present, non-null
    value in ``attributes`` via :func:`_resolve_declared_value` -- the SAME
    collision-aware resolver the frame boundary uses (round-2 review
    MAJOR: this used to check only the primary key, disagreeing with the
    frame whenever a package carried only the secondary/canonical-name
    key). Used at the COMPATIBILITY boundary (onboarding's raw-key-set
    check, and training's own missing-static gate) ahead of building any
    frame -- Plan 155 T2's "the projection must cover the compatibility
    path, not just the frame".

    D16: callers must invoke this ONLY for a ``StaticNaming.CARAVAN``
    model, and must NOT union its result with the model's raw (bare) key
    set -- doing so re-admits the exact bare-fallback D15 forbids.

    ``station_code`` (Plan 155 fixer round minor finding) is forwarded
    into a collision error's message so a compatibility-time collision
    names the station it occurred at, matching the frame boundary's own
    diagnostic -- previously always ``<unknown>`` here.
    """
    if not attributes:
        return frozenset()
    return frozenset(
        name
        for name in declared_names
        if _resolve_declared_value(attributes, name, station_code=station_code)
        is not _NO_CANDIDATE
    )


def resolve_available_static_keys_for_stations(
    model: object,
    station_ids: Iterable[StationId],
    *,
    station_store: StationStore,
    basin_store: BasinStore,
) -> dict[StationId, frozenset[str]]:
    """The D16 compatibility-KEY-SET resolution loop, shared by BOTH
    real-boundary callers that used to duplicate it byte-for-byte
    (`flows/onboard_model.py::_validate_compatibility_task` and
    `services/model_onboarding.py::onboard_model`'s own Step 1) -- Plan
    155 round-2 review MAJOR ("test surface"): the duplicated inline logic
    was untested at ITS boundary in `model_onboarding.py` and had no
    single source of truth to diverge from. One function now serves both
    Prefect-task and plain-Python compatibility callers, and is directly
    testable in isolation.

    For each station: with no bound basin, the available set is empty;
    otherwise resolves through :func:`available_declared_static_keys` (no
    bare-key union) for a ``StaticNaming.CARAVAN``-declaring model, or the
    raw non-null key set (``types/basin.py::non_null_static_keys``,
    today's unprojected behaviour) for a ``NATIVE`` one.
    """
    from typing import cast

    from sapphire_flow.types.basin import non_null_static_keys

    declared_names = cast(
        "frozenset[str]",
        model.data_requirements.static_features,  # type: ignore[attr-defined]
    )
    static_naming = declared_static_naming(model)
    available: dict[StationId, frozenset[str]] = {}
    for sid in station_ids:
        station = station_store.fetch_station(sid)
        if station is None or station.basin_id is None:
            available[sid] = frozenset()
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        attrs = basin.attributes if basin is not None else None
        available[sid] = (
            available_declared_static_keys(
                attrs, declared_names, station_code=station.code
            )
            if static_naming is StaticNaming.CARAVAN
            else non_null_static_keys(attrs)
        )
    return available


def project_declared_static_attributes(
    attributes: Mapping[str, Any] | None,
    declared_names: Iterable[str],
    *,
    station_code: str | None = None,
) -> dict[str, Any]:
    """Build the model-facing static projection: every ORIGINAL key of
    ``attributes`` is preserved untouched, and each name in
    ``declared_names`` additionally resolves through
    :func:`_resolve_declared_value` into a BARE column under that exact
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
        value = _resolve_declared_value(attributes, name, station_code=station_code)
        if value is _NO_CANDIDATE:
            # No caravan: source at all -- missing, not a stale bare
            # fallback. The caller's own compatibility gate reports this.
            projected.pop(name, None)
            continue
        projected[name] = value
    return projected


def resolve_shared_static_frame(
    attributes: Mapping[str, Any] | None,
    models: Iterable[object],
    *,
    station_code: str | None = None,
) -> dict[str, Any]:
    """Plan 155 fixer round (major finding): a per-station static frame
    assembled ONCE and shared across every co-assigned model
    (``services/operational_inputs.py::assemble_station_operational_inputs``
    with a ``requirements_override`` UNIONING ``static_features`` across
    every assigned model -- ``flows/run_forecast_cycle.py``'s fallback-chain
    assembly) must NOT gate D16's Caravan resolution on a single
    representative model's ``static_naming`` declaration while resolving
    against the cross-model SUPERSET of declared names. Doing so silently
    hands one co-assigned model's declared name the OTHER model's
    resolution regime for that same bare name -- exactly the silent
    failure D15/D16 exist to prevent (``area`` rescales every discharge).

    Scopes resolution PER model instead: only a name that a
    ``StaticNaming.CARAVAN``-declaring model itself asks for is projected
    through :func:`project_declared_static_attributes`; a name exclusively
    declared by ``NATIVE`` model(s) is left as today's untouched bare
    attribute. If two co-assigned models declare the SAME bare name under
    DIFFERING regimes (one wants Caravan's derivation, one wants the
    incumbent bare value), there is no single correct shared value --
    raises ``ConfigurationError`` naming the station and the conflicting
    name(s) rather than silently picking one model's answer for both.
    """
    if not attributes:
        return {}
    caravan_names: set[str] = set()
    native_names: set[str] = set()
    for model in models:
        declared_names = set(
            getattr(getattr(model, "data_requirements", None), "static_features", ())
            or ()
        )
        bucket = (
            caravan_names
            if declared_static_naming(model) is StaticNaming.CARAVAN
            else native_names
        )
        bucket |= declared_names
    conflicting = sorted(caravan_names & native_names)
    if conflicting:
        raise ConfigurationError(
            f"station {station_code or '<unknown>'}: static name(s) "
            f"{conflicting} are declared under DIFFERING static_naming "
            "regimes by co-assigned models at this station -- refusing to "
            "resolve a single shared value for both (Plan 155 D16 "
            "fallback-chain gap)"
        )
    if not caravan_names:
        return dict(attributes)
    return project_declared_static_attributes(
        attributes, caravan_names, station_code=station_code
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class StaticCoverageGap:
    """One station's shortfall against T1's exit gate (Plan 155): "every
    station in the T0a manifest resolves all 50 of PT's statics to
    non-null, finite values". ``collision_error`` is set instead of
    ``missing_statics`` being individually diagnosed when T2's collision
    guard itself raised for this station (Plan 155 fixer round) -- the raw
    package delivers two disagreeing values for one declared name, so
    every declared name is reported missing (the whole resolution failed,
    not just one)."""

    station_code: str
    missing_statics: frozenset[str]
    collision_error: str | None = None


def verify_static_coverage(
    basins_by_code: Mapping[str, Mapping[str, Any] | None],
    declared_names: Iterable[str],
) -> tuple[StaticCoverageGap, ...]:
    """T1's exit gate, made a checkable function instead of a one-off
    script (Plan 155 post-implementation-review finding: `is_missing_
    static_value` alone "rejects only None and NaN, so infinities pass
    it"). For every ``(station_code, basin.attributes)`` pair, resolves
    ``declared_names`` through the SAME collision-aware resolution the
    frame boundary uses (:func:`project_declared_static_attributes` --
    Plan 155 fixer round: the exit gate previously looked up only the
    PRIMARY key via `resolve_caravan_static_key` directly, so it could
    never observe a T2 collision a delivered package might carry) and
    reports a name missing when its resolved value is absent OR not a
    finite number (:func:`_is_finite_numeric` -- a ``str``/``bool`` no
    longer passes as "finite").

    Returns one :class:`StaticCoverageGap` per station with at least one
    missing static (or an unresolved collision), station codes sorted, so
    a caller asserts ``()`` for full coverage and gets an actionable
    report otherwise.
    """
    declared = frozenset(declared_names)
    gaps: list[StaticCoverageGap] = []
    for code in sorted(basins_by_code):
        attrs = basins_by_code[code]
        try:
            projected = project_declared_static_attributes(
                attrs, declared, station_code=code
            )
        except ConfigurationError as exc:
            gaps.append(
                StaticCoverageGap(
                    station_code=code,
                    missing_statics=declared,
                    collision_error=str(exc),
                )
            )
            continue
        missing = frozenset(
            name for name in declared if not _is_finite_numeric(projected.get(name))
        )
        if missing:
            gaps.append(StaticCoverageGap(station_code=code, missing_statics=missing))
    return tuple(gaps)
