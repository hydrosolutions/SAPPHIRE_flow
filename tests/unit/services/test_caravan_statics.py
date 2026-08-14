"""Plan 155 T1b/T2 — RED-FIRST locking tests for the Caravan<->HydroATLAS
static resolution rule (D15) and the alias projection (G8).
"""

from __future__ import annotations

import json
import math
import pathlib
import uuid
from datetime import UTC, datetime

import polars as pl
import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import (
    CARAVAN_ALIAS,
    CARAVAN_PREFIX,
    available_declared_static_keys,
    declared_static_naming,
    project_declared_static_attributes,
    resolve_available_static_keys_for_stations,
    resolve_caravan_static_key,
    resolve_shared_static_frame,
    verify_static_coverage,
)
from sapphire_flow.types.basin import Basin, non_null_static_keys
from sapphire_flow.types.enums import EnsembleMode, SpatialRepresentation, StaticNaming
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeBasinStore, FakeStationStore


class TestResolveCaravanStaticKey:
    def test_direct_name_resolves_to_bare_caravan_key(self) -> None:
        # "area" is one of the seven colliding names (Plan 155): no alias
        # entry, so D15's rule resolves it to `caravan:` + the bare name.
        assert resolve_caravan_static_key("area") == "caravan:area"

    def test_aliased_name_resolves_to_the_raw_hydroatlas_code(self) -> None:
        assert resolve_caravan_static_key("slope") == "caravan:slp_dg_sav"

    def test_every_declared_alias_pair_matches_the_plan_table(self) -> None:
        # Plan 155 G8: "Checked against PT's 50: 29 direct + 21 aliased =
        # 50, zero unresolved." — pin the exact 21-pair table so a future
        # edit cannot silently drop or corrupt one.
        expected = {
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
        assert expected == CARAVAN_ALIAS


class TestProjectDeclaredStaticAttributes:
    def test_area_resolves_to_caravans_value_not_camels_ch(self) -> None:
        """T1/T1b's red test, verbatim (plan 155): resolving PT's `area`
        for a Swiss station returns Caravan's value, and the fixture's two
        `area` values are asserted to differ so the test cannot pass by
        coincidence."""
        attributes = {
            "area": 100.0,  # CAMELS-CH's own (pre-existing) bare attribute
            f"{CARAVAN_PREFIX}area": 250.0,  # Caravan's, namespaced (D15)
        }
        assert attributes["area"] != attributes[f"{CARAVAN_PREFIX}area"]

        projected = project_declared_static_attributes(attributes, {"area"})

        assert projected["area"] == 250.0
        # The original CAMELS-CH bare attribute is a DIFFERENT dict entry
        # (not this one) -- the projection never mutates the source.
        assert attributes["area"] == 100.0

    def test_aliased_name_projects_from_the_raw_hydroatlas_column(self) -> None:
        attributes = {f"{CARAVAN_PREFIX}slp_dg_sav": 12.5}
        projected = project_declared_static_attributes(attributes, {"slope"})
        assert projected["slope"] == 12.5

    def test_original_keys_are_preserved_unchanged(self) -> None:
        attributes = {f"{CARAVAN_PREFIX}area": 250.0, "unrelated": "x"}
        projected = project_declared_static_attributes(attributes, {"area"})
        assert projected[f"{CARAVAN_PREFIX}area"] == 250.0
        assert projected["unrelated"] == "x"

    def test_missing_source_key_is_simply_absent_from_the_projection(self) -> None:
        projected = project_declared_static_attributes({}, {"area"})
        assert "area" not in projected

    def test_none_attributes_returns_empty_dict(self) -> None:
        assert project_declared_static_attributes(None, {"area"}) == {}

    def test_repeated_declared_name_resolves_consistently_without_raising(self) -> None:
        # A duplicate in `declared_names` (e.g. two units both declaring
        # "slope") must not spuriously trip the collision guard -- the
        # SAME source key always yields the SAME value.
        attributes = {f"{CARAVAN_PREFIX}slp_dg_sav": 1.0}
        projected = project_declared_static_attributes(attributes, ["slope", "slope"])
        assert projected["slope"] == 1.0


class TestAvailableDeclaredStaticKeys:
    def test_compatibility_reports_zero_missing_statics(self) -> None:
        """T2's red assertion, verbatim (plan 155): compatibility reports
        ZERO missing statics for a Swiss station -- exercised at both
        boundaries (this is the compatibility-KEY-SET boundary)."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,  # aliased -> "slope"
            f"{CARAVAN_PREFIX}area": 250.0,  # direct -> "area"
            f"{CARAVAN_PREFIX}p_mean": 3.1,  # direct -> "p_mean"
        }
        declared = frozenset({"slope", "area", "p_mean"})

        available = non_null_static_keys(attributes) | available_declared_static_keys(
            attributes, declared
        )
        missing = declared - available

        assert missing == frozenset()

    def test_missing_caravan_source_is_reported_missing(self) -> None:
        available = available_declared_static_keys({}, frozenset({"area"}))
        assert "area" not in available

    def test_null_source_value_is_not_available(self) -> None:
        attributes = {f"{CARAVAN_PREFIX}area": None}
        available = available_declared_static_keys(attributes, frozenset({"area"}))
        assert "area" not in available

    def test_agrees_with_the_frame_when_only_the_secondary_key_is_present(
        self,
    ) -> None:
        """Plan 155 round-2 review MAJOR, locked directly: "compatibility
        and frame resolution disagree -- available_declared_static_keys
        checks only the primary raw-code key while
        project_declared_static_attributes will accept the secondary
        canonical key alone". A package carrying ONLY Caravan's own bare
        canonical name for an aliased static (no raw HydroATLAS code at
        all) must be reported AVAILABLE here, matching the frame -- one
        resolver now serves both boundaries."""
        # "slope" is aliased to "slp_dg_sav"; only the bare secondary key
        # (`caravan:slope`) is present, never the primary `caravan:slp_dg_sav`.
        attributes = {f"{CARAVAN_PREFIX}slope": 12.5}

        available = available_declared_static_keys(attributes, frozenset({"slope"}))
        projected = project_declared_static_attributes(attributes, {"slope"})

        assert "slope" in available
        assert projected["slope"] == 12.5


class TestFrameResolvesAllDeclaredStatics:
    def test_hydroatlas_named_frame_resolves_all_declared_statics(self) -> None:
        """T2's red assertion, verbatim (plan 155): a HydroATLAS-named
        static frame resolves all of PT's declared statics -- the FRAME
        boundary (the converse, "an unaliased frame raises", already
        passes today via `_static_inputs` and is characterization, not the
        gate)."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
            f"{CARAVAN_PREFIX}area": 250.0,
            f"{CARAVAN_PREFIX}p_mean": 3.1,
        }
        declared = frozenset({"slope", "area", "p_mean"})

        projected = project_declared_static_attributes(attributes, declared)
        frame = pl.DataFrame([projected])

        missing = declared - set(frame.columns)
        assert missing == frozenset()


# PT's REAL 50-static declared contract, vendored from the artifact
# (`cmal_pool_PT/config.yaml :: static_features`) into
# `tests/fixtures/reference/cmal_pool_PT_static_features.json` on
# 2026-08-14. This REPLACES an earlier golden list that padded 28 confirmed
# names with 22 invented `direct_static_NN` placeholders: that list could
# not fail against a broken resolver for any of PT's real direct names, so
# it proved cardinality rather than the contract (round-3 review, MAJOR).
#
# The vendored list independently CONFIRMS `CARAVAN_ALIAS`: the 50 names
# split exactly 21 aliased / 29 direct against it, which is the split T1b
# derived separately from the modeller's `static_attributes.md`.
_PT_STATICS_FIXTURE = (
    pathlib.Path(__file__).parents[2]
    / "fixtures"
    / "reference"
    / "cmal_pool_PT_static_features.json"
)
_PT_STATICS_RAW: list[str] = json.loads(_PT_STATICS_FIXTURE.read_text())[
    "static_features"
]
PT_FIFTY_STATICS: frozenset[str] = frozenset(_PT_STATICS_RAW)


class TestExitGateAllFiftyResolve:
    def test_the_golden_list_is_exactly_fifty_unique_names(self) -> None:
        assert len(PT_FIFTY_STATICS) == 50

    def test_every_declared_static_resolves_non_null(self) -> None:
        """T1's exit gate (plan 155): every ONE of the model's 50 declared
        statics resolves to a non-null, finite value; `area` specifically
        must be present.

        Plan 155 fixer round finding: the previous version of this test
        gave every value the same synthetic `1.0`, so it could not have
        caught the resolver accidentally returning the WRONG key's value
        (any resolution bug producing "some other declared name's 1.0"
        would still pass). Distinct, non-uniform values per name close
        that gap; `verify_static_coverage` is exercised too, not just the
        frame-boundary function, so both T1 exit-gate paths are covered by
        one realistic manifest -- against the FULL 50-name golden list,
        not a 25-name subset.
        """
        declared = PT_FIFTY_STATICS

        attributes: dict[str, float] = {}
        for index, name in enumerate(sorted(declared)):
            raw = CARAVAN_ALIAS.get(name, name)
            attributes[f"{CARAVAN_PREFIX}{raw}"] = float(index) + 0.1

        projected = project_declared_static_attributes(attributes, declared)

        assert "area" in projected
        seen_values: set[float] = set()
        for name in declared:
            value = projected[name]
            assert value is not None
            assert math.isfinite(value)
            assert value not in seen_values  # every name got ITS OWN value
            seen_values.add(value)
        assert len(seen_values) == 50  # every one of the 50 resolved distinctly

        assert verify_static_coverage({"2009": attributes}, declared) == ()

    def test_a_single_missing_direct_static_among_the_fifty_is_caught(self) -> None:
        """The converse of the above, closing the exact gap the review
        flagged: a coverage bug affecting one of the 25 PREVIOUSLY
        untested direct names must be reported, not silently pass."""
        declared = PT_FIFTY_STATICS
        # A REAL direct (non-aliased) name of PT's contract, derived rather
        # than hard-coded so this test follows the vendored fixture instead
        # of a placeholder that could silently vanish.
        omitted = sorted(declared - frozenset(CARAVAN_ALIAS))[0]
        attributes: dict[str, float] = {}
        for index, name in enumerate(sorted(declared)):
            if name == omitted:
                continue
            raw = CARAVAN_ALIAS.get(name, name)
            attributes[f"{CARAVAN_PREFIX}{raw}"] = float(index) + 0.1

        gaps = verify_static_coverage({"2009": attributes}, declared)

        assert len(gaps) == 1
        assert gaps[0].missing_statics == frozenset({omitted})


class TestMissingCaravanSourceIsNotABareFallback:
    def test_non_empty_legacy_bare_key_is_removed_not_leaked(self) -> None:
        """Plan 155 post-implementation-review BLOCKER, locked directly:
        `project_declared_static_attributes({"area": 123.0}, ["area"])`
        must NOT return `{"area": 123.0}` -- the caravan: source is
        entirely absent, so D15's no-bare-fallback rule means the bare
        CAMELS-CH value must be dropped, not left standing in for
        Caravan's. This is the exact scenario the original test suite
        never covered (it only ever exercised an EMPTY attributes dict)."""
        projected = project_declared_static_attributes({"area": 123.0}, {"area"})
        assert "area" not in projected

    def test_other_original_keys_survive_the_removal(self) -> None:
        projected = project_declared_static_attributes(
            {"area": 123.0, "unrelated": "x"}, {"area"}
        )
        assert "area" not in projected
        assert projected["unrelated"] == "x"


class TestCollisionSemantics:
    """T2: "if a package ever carries both the raw code and the canonical
    Caravan name, equal finite values are accepted and conflicting values
    fail loudly." The guard must look up BOTH keys for an aliased name --
    a single-valued resolver can never observe this (Plan 155
    post-implementation-review MAJOR)."""

    def test_both_keys_present_with_equal_values_resolves_without_raising(self) -> None:
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,  # the raw HydroATLAS code
            f"{CARAVAN_PREFIX}slope": 12.5,  # Caravan's own canonical name
        }
        projected = project_declared_static_attributes(attributes, {"slope"})
        assert projected["slope"] == 12.5

    def test_both_keys_present_with_differing_values_raises_naming_both(self) -> None:
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": 11.0,
            f"{CARAVAN_PREFIX}slope": 99.0,
        }
        with pytest.raises(ConfigurationError, match="slope") as exc_info:
            project_declared_static_attributes(
                attributes, {"slope"}, station_code="2009"
            )
        message = str(exc_info.value)
        assert "2009" in message
        assert "11.0" in message
        assert "99.0" in message

    def test_equal_infinities_do_not_pass_the_collision_guard(self) -> None:
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": math.inf,
            f"{CARAVAN_PREFIX}slope": math.inf,
        }
        with pytest.raises(ConfigurationError, match="slope"):
            project_declared_static_attributes(attributes, {"slope"})

    def test_unaliased_direct_name_has_no_secondary_key_to_collide_on(self) -> None:
        # "area" is direct (unaliased): its primary key already IS the bare
        # `caravan:area` name, so there is no distinct secondary key --
        # a second, differently-valued "area"-shaped key cannot exist to
        # collide with it. Sanity check that direct names never spuriously
        # raise.
        attributes = {f"{CARAVAN_PREFIX}area": 250.0}
        projected = project_declared_static_attributes(attributes, {"area"})
        assert projected["area"] == 250.0

    def test_a_null_primary_alongside_a_valid_secondary_raises(self) -> None:
        """Plan 155 fixer round (major finding): candidates were filtered
        for missing/null BEFORE checking whether both keys existed, so a
        delivered-but-null primary value alongside a valid secondary value
        silently resolved to the non-null one instead of being treated as
        a disagreement. Both keys being PRESENT (even one null) must
        require agreement, not a silent pick."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": None,  # delivered, sanitised to null
            f"{CARAVAN_PREFIX}slope": 12.5,  # a genuinely valid value
        }
        with pytest.raises(ConfigurationError, match="slope"):
            project_declared_static_attributes(attributes, {"slope"})

    def test_a_nan_primary_alongside_a_valid_secondary_raises(self) -> None:
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": math.nan,
            f"{CARAVAN_PREFIX}slope": 12.5,
        }
        with pytest.raises(ConfigurationError, match="slope"):
            project_declared_static_attributes(attributes, {"slope"})

    def test_both_keys_present_and_both_null_resolves_to_missing_not_a_collision(
        self,
    ) -> None:
        """Fixer round (independent Codex review, major finding): two
        present keys that are BOTH missing (None/NaN) carry no genuine,
        differing information -- they are not a "conflicting values"
        collision, they simply mean the static is missing for this
        station. Must not raise, and must be reported as unavailable via
        `available_declared_static_keys` (the compatibility boundary),
        not via a `ConfigurationError`."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": None,
            f"{CARAVAN_PREFIX}slope": None,
        }
        projected = project_declared_static_attributes(attributes, {"slope"})
        assert "slope" not in projected

        available = available_declared_static_keys(
            attributes, {"slope"}, station_code="2009"
        )
        assert available == frozenset()

    def test_both_keys_present_one_null_one_nan_resolves_to_missing(self) -> None:
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": None,
            f"{CARAVAN_PREFIX}slope": math.nan,
        }
        projected = project_declared_static_attributes(attributes, {"slope"})
        assert "slope" not in projected

    def test_equal_strings_do_not_pass_the_collision_guard(self) -> None:
        """`_values_agree` must require both operands to be NUMERIC, not
        merely equal -- two equal strings are not a legitimate agreement
        (Plan 155 fixer round finding: the old check only inspected
        finiteness when at least one operand was already a float, so two
        equal non-numeric values silently 'agreed')."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": "12.5",
            f"{CARAVAN_PREFIX}slope": "12.5",
        }
        with pytest.raises(ConfigurationError, match="slope"):
            project_declared_static_attributes(attributes, {"slope"})

    def test_equal_bool_and_int_do_not_pass_the_collision_guard(self) -> None:
        """`True == 1` in Python -- a bool/int pair that compares equal
        must still be rejected as non-numeric-agreement, matching the exit
        gate's own bool exclusion (`_is_finite_numeric`)."""
        attributes = {
            f"{CARAVAN_PREFIX}slp_dg_sav": True,
            f"{CARAVAN_PREFIX}slope": 1,
        }
        with pytest.raises(ConfigurationError, match="slope"):
            project_declared_static_attributes(attributes, {"slope"})


class TestDeclaredStaticNaming:
    """Plan 155 D16: the model itself declares whether it gets Caravan
    resolution at all; the default is NATIVE (today's behaviour,
    unchanged) for every model that does not opt in."""

    def test_a_model_with_no_declaration_defaults_to_native(self) -> None:
        class _PlainModel:
            pass

        assert declared_static_naming(_PlainModel()) is StaticNaming.NATIVE

    def test_a_model_declaring_caravan_is_read_back(self) -> None:
        class _CaravanModel:
            static_naming = StaticNaming.CARAVAN

        assert declared_static_naming(_CaravanModel()) is StaticNaming.CARAVAN

    def test_a_present_but_non_enum_declaration_raises(self) -> None:
        """Plan 155 fixer round (major finding): a PRESENT-but-malformed
        declaration -- including the plausible near-miss of the plain
        string "caravan" instead of the enum member -- must raise, not
        silently downgrade to NATIVE. Only a genuinely ABSENT attribute
        defaults."""

        class _MisdeclaredModel:
            static_naming = "caravan"  # a plain string, not the enum

        with pytest.raises(ConfigurationError, match="static_naming"):
            declared_static_naming(_MisdeclaredModel())

    def test_a_declared_none_also_raises(self) -> None:
        """The sentinel-based ABSENT/PRESENT distinction must not conflate
        "never declared" with "declared as None"."""

        class _NoneDeclaredModel:
            static_naming = None

        with pytest.raises(ConfigurationError, match="static_naming"):
            declared_static_naming(_NoneDeclaredModel())


class TestVerifyStaticCoverage:
    """T1's exit gate (Plan 155): every station in the manifest resolves
    all of a model's declared statics to non-null, FINITE values."""

    def test_full_coverage_returns_no_gaps(self) -> None:
        basins_by_code = {
            "2009": {
                f"{CARAVAN_PREFIX}area": 250.0,
                f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
            }
        }
        gaps = verify_static_coverage(basins_by_code, {"area", "slope"})
        assert gaps == ()

    def test_a_missing_static_is_reported_by_station_code(self) -> None:
        basins_by_code = {"2009": {f"{CARAVAN_PREFIX}area": 250.0}}
        gaps = verify_static_coverage(basins_by_code, {"area", "slope"})
        assert len(gaps) == 1
        assert gaps[0].station_code == "2009"
        assert gaps[0].missing_statics == frozenset({"slope"})

    def test_a_non_finite_value_is_reported_missing_even_though_present(self) -> None:
        # `is_missing_static_value` alone accepts an infinity (it only
        # rejects None/NaN) -- the exit gate requires `math.isfinite`.
        basins_by_code = {"2009": {f"{CARAVAN_PREFIX}area": math.inf}}
        gaps = verify_static_coverage(basins_by_code, {"area"})
        assert len(gaps) == 1
        assert gaps[0].missing_statics == frozenset({"area"})

    def test_a_station_with_no_basin_attributes_is_reported_missing(self) -> None:
        basins_by_code = {"2009": None}
        gaps = verify_static_coverage(basins_by_code, {"area"})
        assert gaps[0].missing_statics == frozenset({"area"})

    def test_station_codes_are_sorted_for_a_stable_report(self) -> None:
        basins_by_code = {"9999": {}, "0001": {}}
        gaps = verify_static_coverage(basins_by_code, {"area"})
        assert [gap.station_code for gap in gaps] == ["0001", "9999"]

    def test_a_boolean_value_is_reported_missing_not_accepted_as_finite(self) -> None:
        """Plan 155 fixer round (major finding): `isinstance(True, int)` is
        `True` in Python, so a bool must be explicitly rejected or it
        would silently pass the exit gate as a valid numeric static."""
        basins_by_code = {"2009": {f"{CARAVAN_PREFIX}area": True}}
        gaps = verify_static_coverage(basins_by_code, {"area"})
        assert len(gaps) == 1
        assert gaps[0].missing_statics == frozenset({"area"})

    def test_a_string_value_is_reported_missing_not_accepted_as_finite(self) -> None:
        basins_by_code = {"2009": {f"{CARAVAN_PREFIX}area": "not-a-number"}}
        gaps = verify_static_coverage(basins_by_code, {"area"})
        assert len(gaps) == 1
        assert gaps[0].missing_statics == frozenset({"area"})

    def test_an_unresolved_collision_is_reported_via_collision_error(self) -> None:
        """Plan 155 fixer round (major finding): the exit gate must run
        T2's own collision resolution, not just look up the primary key
        directly -- a package carrying both the raw HydroATLAS code and
        Caravan's bare name with DIFFERING values must be surfaced by the
        exit gate, not silently pass with the primary key's value."""
        basins_by_code = {
            "2009": {
                f"{CARAVAN_PREFIX}slp_dg_sav": 11.0,
                f"{CARAVAN_PREFIX}slope": 99.0,
            }
        }
        gaps = verify_static_coverage(basins_by_code, {"slope"})
        assert len(gaps) == 1
        assert gaps[0].station_code == "2009"
        assert gaps[0].missing_statics == frozenset({"slope"})
        assert gaps[0].collision_error is not None
        assert "11.0" in gaps[0].collision_error
        assert "99.0" in gaps[0].collision_error

    def test_an_agreeing_collision_still_resolves_via_the_exit_gate(self) -> None:
        basins_by_code = {
            "2009": {
                f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
                f"{CARAVAN_PREFIX}slope": 12.5,
            }
        }
        gaps = verify_static_coverage(basins_by_code, {"slope"})
        assert gaps == ()

    def test_both_keys_present_and_both_null_is_a_plain_missing_gap_not_a_collision(
        self,
    ) -> None:
        """Fixer round (independent Codex review, major finding): two
        present-but-null keys must NOT poison the exit gate into reporting
        `collision_error` (which, before the fix, marked EVERY declared
        static for the station missing, not just the affected one) -- it
        is exactly as if the static were simply absent."""
        basins_by_code = {
            "2009": {
                f"{CARAVAN_PREFIX}area": 250.0,
                f"{CARAVAN_PREFIX}slp_dg_sav": None,
                f"{CARAVAN_PREFIX}slope": None,
            }
        }
        gaps = verify_static_coverage(basins_by_code, {"area", "slope"})
        assert len(gaps) == 1
        assert gaps[0].missing_statics == frozenset({"slope"})
        assert gaps[0].collision_error is None


def _model(
    *, static_features: frozenset[str], static_naming: StaticNaming | None = None
) -> object:
    reqs = ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=static_features,
        supported_time_steps=frozenset(),
        lookback_steps=1,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    attrs: dict[str, object] = {"data_requirements": reqs}
    if static_naming is not None:
        attrs["static_naming"] = static_naming
    return type("_Model", (), attrs)()


class TestResolveSharedStaticFrame:
    """Plan 155 fixer round (major finding): a shared per-station static
    frame assembled from a cross-model UNION of declared static names must
    scope Caravan resolution PER assigned model, not gate the whole frame
    on a single representative model's declaration."""

    def test_no_models_declare_caravan_leaves_the_frame_untouched(self) -> None:
        attributes = {"area": 100.0}
        native_only = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.NATIVE
        )
        result = resolve_shared_static_frame(attributes, [native_only])
        assert result == {"area": 100.0}

    def test_a_caravan_declaring_models_own_name_resolves(self) -> None:
        attributes = {"area": 100.0, f"{CARAVAN_PREFIX}area": 250.0}
        caravan_model = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.CARAVAN
        )
        result = resolve_shared_static_frame(attributes, [caravan_model])
        assert result["area"] == 250.0

    def test_disjoint_names_each_get_their_own_models_treatment(self) -> None:
        # `area` is NATIVE-declared (untouched); `slope` is CARAVAN-declared
        # (resolved through the alias). No collision -- disjoint names.
        attributes = {
            "area": 100.0,
            f"{CARAVAN_PREFIX}area": 250.0,
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
        }
        native_model = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.NATIVE
        )
        caravan_model = _model(
            static_features=frozenset({"slope"}), static_naming=StaticNaming.CARAVAN
        )
        result = resolve_shared_static_frame(attributes, [native_model, caravan_model])
        assert result["area"] == 100.0  # NATIVE model's own bare value, untouched
        assert result["slope"] == 12.5  # CARAVAN model's own resolved value

    def test_same_name_under_differing_regimes_raises(self) -> None:
        """The exact silent-share D15/D16 exist to prevent: one co-assigned
        model wants `area` NATIVE (CAMELS-CH's bare value), another wants
        it CARAVAN (Caravan's derivation) -- there is no single correct
        shared value, so this must raise rather than pick one."""
        attributes = {"area": 100.0, f"{CARAVAN_PREFIX}area": 250.0}
        native_model = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.NATIVE
        )
        caravan_model = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.CARAVAN
        )
        with pytest.raises(ConfigurationError, match="area"):
            resolve_shared_static_frame(
                attributes, [native_model, caravan_model], station_code="2009"
            )

    def test_empty_attributes_returns_empty_regardless_of_models(self) -> None:
        caravan_model = _model(
            static_features=frozenset({"area"}), static_naming=StaticNaming.CARAVAN
        )
        assert resolve_shared_static_frame(None, [caravan_model]) == {}
        assert resolve_shared_static_frame({}, [caravan_model]) == {}


def _basin_for_resolution_test(basin_id: BasinId) -> Basin:
    return Basin(
        id=basin_id,
        code="B-001",
        name="Basin B-001",
        geometry=None,
        area_km2=100.0,
        attributes={
            "area": 999.0,  # CAMELS-CH's own bare "area" (must not win under CARAVAN)
            f"{CARAVAN_PREFIX}area": 250.0,
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
        },
        band_geometries=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        network="bafu",
    )


class TestResolveAvailableStaticKeysForStations:
    """Plan 155 round-2 review MAJOR ("test surface"): the D16 compatibility
    resolution loop used to be duplicated byte-for-byte in
    `flows/onboard_model.py::_validate_compatibility_task` AND
    `services/model_onboarding.py::onboard_model`'s own Step 1, with no
    single source of truth to diverge from and no direct test of either.
    Both now call this one function -- locked directly here."""

    def test_caravan_model_resolves_through_the_alias_no_bare_fallback(self) -> None:
        station_id = StationId(uuid.uuid4())
        basin_id = BasinId(uuid.uuid4())
        station_store = FakeStationStore()
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin_for_resolution_test(basin_id))
        model = _model(
            static_features=frozenset({"area", "slope"}),
            static_naming=StaticNaming.CARAVAN,
        )

        available = resolve_available_static_keys_for_stations(
            model,
            [station_id],
            station_store=station_store,
            basin_store=basin_store,
        )

        assert available[station_id] == frozenset({"area", "slope"})

    def test_native_model_gets_the_raw_bare_key_set(self) -> None:
        station_id = StationId(uuid.uuid4())
        basin_id = BasinId(uuid.uuid4())
        station_store = FakeStationStore()
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin_for_resolution_test(basin_id))
        model = _model(static_features=frozenset({"area", "slope"}))  # NATIVE default

        available = resolve_available_static_keys_for_stations(
            model,
            [station_id],
            station_store=station_store,
            basin_store=basin_store,
        )

        # NATIVE gets the RAW key set (today's behaviour) -- "slope" is
        # simply not a key present in attributes at all under that name.
        assert available[station_id] == frozenset(
            {"area", f"{CARAVAN_PREFIX}area", f"{CARAVAN_PREFIX}slp_dg_sav"}
        )
        assert "slope" not in available[station_id]

    def test_a_compatibility_time_collision_names_the_station(self) -> None:
        """Plan 155 fixer round (minor finding): compatibility-time
        collision errors used to lose the station identity because
        `resolve_available_static_keys_for_stations` called
        `available_declared_static_keys` without a `station_code`, so a
        conflicting alias at onboarding reported station '<unknown>'.
        This must name the REAL station code."""
        station_id = StationId(uuid.uuid4())
        basin_id = BasinId(uuid.uuid4())
        station_store = FakeStationStore()
        station_store.store_station(
            make_station_config(
                station_id=station_id, code="2009-collision", basin_id=basin_id
            )
        )
        basin_store = FakeBasinStore()
        basin = _basin_for_resolution_test(basin_id)
        basin_store.store_basin(
            Basin(
                id=basin.id,
                code=basin.code,
                name=basin.name,
                geometry=basin.geometry,
                area_km2=basin.area_km2,
                attributes={
                    **basin.attributes,
                    f"{CARAVAN_PREFIX}slope": 99.0,  # disagrees with slp_dg_sav's 12.5
                },
                band_geometries=basin.band_geometries,
                created_at=basin.created_at,
                network=basin.network,
            )
        )
        model = _model(
            static_features=frozenset({"slope"}), static_naming=StaticNaming.CARAVAN
        )

        with pytest.raises(ConfigurationError, match="2009-collision"):
            resolve_available_static_keys_for_stations(
                model,
                [station_id],
                station_store=station_store,
                basin_store=basin_store,
            )

    def test_station_with_no_basin_resolves_to_an_empty_set(self) -> None:
        station_id = StationId(uuid.uuid4())
        station_store = FakeStationStore()
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=None)
        )
        basin_store = FakeBasinStore()
        model = _model(static_features=frozenset({"area"}))

        available = resolve_available_static_keys_for_stations(
            model,
            [station_id],
            station_store=station_store,
            basin_store=basin_store,
        )

        assert available[station_id] == frozenset()


class TestAdmissibilityIsSharedAcrossBoundaries:
    """Round-3 review (MINOR): compatibility/frame and the exit gate must
    agree on whether a VALUE is usable, not merely on which KEY to read.
    `is_missing_static_value` rejects only None/NaN, so an infinity, string
    or bool under a `caravan:` key used to be reported "available" and
    projected into a model's static frame while `verify_static_coverage`
    rejected it. `area` is one of PT's 50, so a non-finite value there
    corrupts the m3/s <-> mm/day conversion.
    """

    @pytest.mark.parametrize(
        "bad_value",
        [math.inf, -math.inf, "12.5", True],
        ids=["inf", "-inf", "str", "bool"],
    )
    def test_a_non_finite_value_is_neither_available_nor_projected(
        self, bad_value: object
    ) -> None:
        attributes = {f"{CARAVAN_PREFIX}area": bad_value}

        assert (
            available_declared_static_keys(attributes, ["area"], station_code="2009")
            == frozenset()
        )

        projected = project_declared_static_attributes(
            attributes, ["area"], station_code="2009"
        )
        assert projected.get("area") is None

        gaps = verify_static_coverage({"2009": attributes}, ["area"])
        assert gaps and gaps[0].missing_statics == frozenset({"area"})

    def test_a_finite_value_still_resolves(self) -> None:
        attributes = {f"{CARAVAN_PREFIX}area": 500.0}
        assert available_declared_static_keys(
            attributes, ["area"], station_code="2009"
        ) == frozenset({"area"})
        assert (
            project_declared_static_attributes(
                attributes, ["area"], station_code="2009"
            )["area"]
            == 500.0
        )
        assert verify_static_coverage({"2009": attributes}, ["area"]) == ()


class TestVendoredContractMatchesTheAliasMap:
    """The vendored fixture makes PT's REAL contract checkable in-repo.
    Two facts worth locking, both verified against the delivered parquet
    on 2026-08-14: the 50 names split exactly 21 aliased / 29 direct
    against `CARAVAN_ALIAS` (an independent confirmation of the map that
    T1b derived from the modeller's `static_attributes.md`), and every one
    of the 50 resolves to a DISTINCT key -- an alias collapsing two
    declared names onto one source column would silently feed a model the
    same value twice.
    """

    def test_the_vendored_list_is_fifty_entries_with_no_duplicates(self) -> None:
        """Assert the RAW list, not the frozenset: collapsing to a set first
        would let a 51-entry fixture containing one duplicate still read as
        "exactly fifty" (review MINOR)."""
        assert len(_PT_STATICS_RAW) == 50
        assert len(set(_PT_STATICS_RAW)) == 50

    def test_the_contract_splits_exactly_twentyone_aliased_and_twentynine_direct(
        self,
    ) -> None:
        aliased = PT_FIFTY_STATICS & frozenset(CARAVAN_ALIAS)
        assert len(PT_FIFTY_STATICS) == 50
        assert len(aliased) == 21
        assert len(PT_FIFTY_STATICS - aliased) == 29

    def test_every_declared_static_resolves_to_a_distinct_key(self) -> None:
        keys = [resolve_caravan_static_key(n) for n in PT_FIFTY_STATICS]
        assert len(set(keys)) == len(keys)
