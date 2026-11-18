from __future__ import annotations

from pathlib import Path

import pytest

from sapphire_flow.config._overlay import (
    TomlProvenance,
    _resolve_overlay_paths,
    load_merged_toml,
)


class TestMergeProvenance:
    def test_tracing_preserves_merge_and_whole_list_replacement(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "base.toml"
        overlay = tmp_path / "overlay.toml"
        base.write_text("items = [1, 2]\n[table]\nkeep = 3\nchange = 4\n")
        overlay.write_text("items = [5]\n[table]\nchange = 6\n")
        trace = TomlProvenance()
        traced = load_merged_toml(base, [overlay], environment={}, provenance=trace)
        assert (
            traced
            == load_merged_toml(base, [overlay])
            == {"items": [5], "table": {"keep": 3, "change": 6}}
        )
        assert trace.origins[("items",)].operation == "replace"
        assert trace.origins[("items",)].document == 1
        assert trace.origins[("table", "keep")].document == 0
        assert trace.origins[("table", "change")].document == 1

    def test_table_replacement_removes_obsolete_child_origins(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "base.toml"
        overlay = tmp_path / "overlay.toml"
        base.write_text("[value]\nold = 1\n")
        overlay.write_text("value = [2]\n")
        trace = TomlProvenance()
        assert load_merged_toml(base, [overlay], provenance=trace) == {"value": [2]}
        assert ("value", "old") not in trace.origins
        assert trace.origins[("value",)].operation == "replace"

    @pytest.mark.parametrize("traced", [False, True])
    @pytest.mark.parametrize(
        "overlay_text",
        [
            "[qc_rules]\n",
            "[onboarding]\nstation_qc_thresholds = []\n",
            "[onboarding]\nqc_pending_networks = []\n",
        ],
    )
    def test_qc_overlay_prohibition_is_unchanged(
        self, tmp_path: Path, traced: bool, overlay_text: str
    ) -> None:
        base = tmp_path / "base.toml"
        overlay = tmp_path / "overlay.toml"
        base.write_text("")
        overlay.write_text(overlay_text)
        with pytest.raises(ValueError, match="must be changed in the base"):
            load_merged_toml(
                base, [overlay], provenance=TomlProvenance() if traced else None
            )

    def test_explicit_environment_never_falls_back_to_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base.toml"
        base.write_text('value = "${SAPPHIRE_TEST_VALUE}"\n')
        monkeypatch.setenv("SAPPHIRE_TEST_VALUE", "ambient")
        assert load_merged_toml(
            base, [], environment={"SAPPHIRE_TEST_VALUE": "explicit"}
        ) == {"value": "explicit"}
        with pytest.raises(ValueError, match="is not set"):
            load_merged_toml(base, [], environment={})


_BASE_TOML = """\
max_retention_days = 3650
weather_hot_days = 180

[onboarding]
data_source = "bafu"
basin_ids = ["2004", "2009", "2033", "2085", "2091", "2100"]

[[danger_levels]]
name = "Low"
level = 1
color = "#00ff00"

[[danger_levels]]
name = "High"
level = 2
color = "#ff0000"
"""


class TestLoadMergedToml:
    def test_empty_overlay_list_returns_base_unchanged(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)

        result = load_merged_toml(base_file, [])

        assert result["max_retention_days"] == 3650
        assert result["weather_hot_days"] == 180
        onboarding = result["onboarding"]
        assert isinstance(onboarding, dict)
        assert onboarding["data_source"] == "bafu"

    def test_overlay_scalar_overrides_base_scalar(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text("max_retention_days = 100\n")

        result = load_merged_toml(base_file, [overlay_file])

        assert result["max_retention_days"] == 100
        assert result["weather_hot_days"] == 180

    def test_nested_dict_deep_merge_preserves_base_keys(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text('[onboarding]\ndata_source = "dhm"\n')

        result = load_merged_toml(base_file, [overlay_file])

        onboarding = result["onboarding"]
        assert isinstance(onboarding, dict)
        assert onboarding["data_source"] == "dhm"
        assert onboarding["basin_ids"] == [
            "2004",
            "2009",
            "2033",
            "2085",
            "2091",
            "2100",
        ]

    def test_overlay_list_replaces_base_list(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text(
            '[onboarding]\nbasin_ids = ["2004", "2009", "2033", "2085", "2091"]\n'
        )

        result = load_merged_toml(base_file, [overlay_file])

        onboarding = result["onboarding"]
        assert isinstance(onboarding, dict)
        assert onboarding["basin_ids"] == ["2004", "2009", "2033", "2085", "2091"]
        assert onboarding["data_source"] == "bafu"

    def test_overlay_array_of_tables_replaces_wholesale(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text(
            '[[danger_levels]]\nname = "Only"\nlevel = 99\ncolor = "#ffffff"\n'
        )

        result = load_merged_toml(base_file, [overlay_file])

        danger_levels = result["danger_levels"]
        assert isinstance(danger_levels, list)
        assert len(danger_levels) == 1
        assert danger_levels[0]["name"] == "Only"
        assert danger_levels[0]["level"] == 99

    def test_multiple_overlays_apply_left_to_right(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_a = tmp_path / "a.toml"
        overlay_a.write_text("max_retention_days = 100\nweather_hot_days = 90\n")
        overlay_b = tmp_path / "b.toml"
        overlay_b.write_text("max_retention_days = 200\n")

        result = load_merged_toml(base_file, [overlay_a, overlay_b])

        assert result["max_retention_days"] == 200
        assert result["weather_hot_days"] == 90

    @pytest.mark.parametrize(
        "overlay_text",
        (
            '[qc_rules]\nversion = "2.0.0"\n',
            '[qc_rules]\nversion = "1.0.0"\n\n'
            '[[qc_rules.rules]]\nrule_id = "range_check"\n',
        ),
    )
    def test_qc_rules_overlay_is_rejected_before_merge(
        self, tmp_path: Path, overlay_text: str
    ) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text('[qc_rules]\nversion = "1.0.0"\n')
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text(overlay_text)

        with pytest.raises(
            ValueError, match="QC rules must be changed in the base config"
        ):
            load_merged_toml(base_file, [overlay_file])

    def test_unrelated_overlay_remains_supported_with_qc_base(
        self, tmp_path: Path
    ) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text('[qc_rules]\nversion = "1.0.0"\n')
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text('[onboarding]\ndata_source = "dhm"\n')

        result = load_merged_toml(base_file, [overlay_file])

        assert result["qc_rules"] == {"version": "1.0.0"}
        assert result["onboarding"] == {"data_source": "dhm"}

    def test_missing_overlay_path_raises_file_not_found(self, tmp_path: Path) -> None:
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        missing = tmp_path / "missing.toml"

        with pytest.raises(FileNotFoundError, match="missing.toml"):
            load_merged_toml(base_file, [missing])

    def test_env_var_substitution_after_merge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_FOO", "bar-value")
        base_file = tmp_path / "base.toml"
        base_file.write_text(_BASE_TOML)
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text('[onboarding]\ndata_source = "${SAPPHIRE_FOO}"\n')

        result = load_merged_toml(base_file, [overlay_file])

        onboarding = result["onboarding"]
        assert isinstance(onboarding, dict)
        assert onboarding["data_source"] == "bar-value"


class TestResolveOverlayPaths:
    def test_env_var_unset_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _resolve_overlay_paths() == []

    def test_env_var_empty_string_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", "")

        assert _resolve_overlay_paths() == []

    def test_trailing_comma_filters_empty_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", "foo.toml,")

        assert _resolve_overlay_paths() == [Path("foo.toml")]

    def test_whitespace_items_are_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", "  foo.toml , bar.toml  ")

        assert _resolve_overlay_paths() == [Path("foo.toml"), Path("bar.toml")]
