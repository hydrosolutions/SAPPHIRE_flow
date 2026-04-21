from __future__ import annotations

from pathlib import Path

import pytest

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,
    load_merged_toml,
)

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
