"""Plan 155 T1b/T2 — RED-FIRST locking tests for the Caravan<->HydroATLAS
static resolution rule (D15) and the alias projection (G8).
"""

from __future__ import annotations

import math

import polars as pl

from sapphire_flow.services.caravan_statics import (
    CARAVAN_ALIAS,
    CARAVAN_PREFIX,
    available_declared_static_keys,
    project_declared_static_attributes,
    resolve_caravan_static_key,
)
from sapphire_flow.types.basin import non_null_static_keys


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


class TestExitGateAllFiftyResolve:
    def test_every_aliased_and_a_sample_of_direct_statics_resolve_non_null(
        self,
    ) -> None:
        """T1's exit gate (plan 155): every declared static resolves to a
        non-null, finite value; `area` specifically must be present."""
        direct_sample = ("area", "p_mean", "frac_snow", "ele_mt_sav")
        declared = frozenset(CARAVAN_ALIAS) | frozenset(direct_sample)

        attributes: dict[str, float] = {}
        for name in declared:
            raw = CARAVAN_ALIAS.get(name, name)
            attributes[f"{CARAVAN_PREFIX}{raw}"] = 1.0

        projected = project_declared_static_attributes(attributes, declared)

        assert "area" in projected
        for name in declared:
            value = projected[name]
            assert value is not None
            assert math.isfinite(value)
