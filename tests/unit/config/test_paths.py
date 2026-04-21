from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sapphire_flow.config.paths import resolve_artifact_dir, resolve_data_dir

if TYPE_CHECKING:
    import pytest


class TestTierPrecedence:
    def test_env_var_wins_over_config_arg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_dir = tmp_path / "from_env"
        cfg_dir = tmp_path / "from_config"
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(env_dir))

        result = resolve_data_dir(config_data_dir=str(cfg_dir))

        assert result == env_dir.resolve()

    def test_config_arg_wins_over_platformdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_DATA_DIR", raising=False)
        cfg_dir = tmp_path / "from_config"
        monkeypatch.setattr(
            "sapphire_flow.config.paths.platformdirs.user_data_dir",
            lambda _app: (_ for _ in ()).throw(AssertionError("should not be called")),
        )

        result = resolve_data_dir(config_data_dir=str(cfg_dir))

        assert result == cfg_dir.resolve()

    def test_platformdirs_fallback_when_both_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_DATA_DIR", raising=False)
        platform_dir = tmp_path / "platform"
        monkeypatch.setattr(
            "sapphire_flow.config.paths.platformdirs.user_data_dir",
            lambda _app: str(platform_dir),
        )

        result = resolve_data_dir()

        assert result == platform_dir.resolve()


class TestEdgeCases:
    def test_empty_env_var_falls_through_to_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", "")
        cfg_dir = tmp_path / "from_config"

        result = resolve_data_dir(config_data_dir=str(cfg_dir))

        assert result == cfg_dir.resolve()

    def test_empty_config_falls_through_to_platformdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_DATA_DIR", raising=False)
        platform_dir = tmp_path / "platform"
        monkeypatch.setattr(
            "sapphire_flow.config.paths.platformdirs.user_data_dir",
            lambda _app: str(platform_dir),
        )

        result = resolve_data_dir(config_data_dir="")

        assert result == platform_dir.resolve()

    def test_tilde_in_env_var_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", "~/sapphire-data")

        result = resolve_data_dir()

        assert result == (tmp_path / "sapphire-data").resolve()

    def test_relative_path_is_resolved_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", "relative/dir")
        monkeypatch.chdir(tmp_path)

        result = resolve_data_dir()

        assert result.is_absolute()
        assert result == (tmp_path / "relative" / "dir").resolve()

    def test_returns_path_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path / "data"))

        result = resolve_data_dir()

        assert isinstance(result, Path)


class TestSubdirectoryCreation:
    def test_creates_raw_artifacts_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = tmp_path / "root"
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(data_root))

        resolve_data_dir()

        assert (data_root / "raw").is_dir()
        assert (data_root / "artifacts").is_dir()
        assert (data_root / "cache").is_dir()

    def test_idempotent_on_repeat_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = tmp_path / "root"
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(data_root))

        first = resolve_data_dir()
        second = resolve_data_dir()

        assert first == second

    def test_creates_nested_parents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deep_path = tmp_path / "a" / "b" / "c"
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(deep_path))

        resolve_data_dir()

        assert deep_path.is_dir()
        assert (deep_path / "raw").is_dir()


class TestResolveArtifactDir:
    def test_returns_artifacts_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = tmp_path / "root"
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(data_root))

        result = resolve_artifact_dir()

        assert result == data_root.resolve() / "artifacts"
