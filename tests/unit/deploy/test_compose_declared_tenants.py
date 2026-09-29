"""Plan 513 T2: every stack that runs `init` gives the declared-tenant step its
config path, and the hosts that declare tenants pass the overlay that does."""

from __future__ import annotations

from pathlib import Path

import yaml


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _init(path: str) -> dict[str, object]:
    compose = yaml.safe_load((_root() / path).read_text())
    return compose["services"].get("init", {})


def _merged_init(overlay: str | None) -> tuple[dict[str, str], list[str]]:
    base = _init("docker-compose.yml")
    env: dict[str, str] = dict(base["environment"])  # type: ignore[arg-type]
    volumes: list[str] = list(base["volumes"])  # type: ignore[arg-type]
    if overlay is not None:
        extra = _init(overlay)
        env.update(extra.get("environment", {}))  # type: ignore[arg-type]
        volumes.extend(extra.get("volumes", []))  # type: ignore[arg-type]
    return env, volumes


def _overlay_is_mounted(env: dict[str, str], volumes: list[str]) -> bool:
    overlay = env["SAPPHIRE_CONFIG_OVERLAY"]
    return any(v.split(":")[1] == overlay for v in volumes)


class TestBaseInit:
    def test_base_init_passes_the_config_path_and_mounts_it(self) -> None:
        env, volumes = _merged_init(None)
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert any(v.split(":")[1] == "/app/config.toml" for v in volumes)


class TestStagingInit:
    def test_staging_inherits_config_path_and_passes_its_overlay(self) -> None:
        env, volumes = _merged_init("docker-compose.staging.yml")
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert env["SAPPHIRE_CONFIG_OVERLAY"].endswith("staging-5-stations.toml")
        assert _overlay_is_mounted(env, volumes)


class TestMacMiniInit:
    def test_macmini_init_passes_config_path_and_the_mac_mini_overlay(self) -> None:
        env, volumes = _merged_init("docker-compose.macmini.yml")
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == "/app/config/overlays/mac-mini.toml"
        assert _overlay_is_mounted(env, volumes)

    def test_macmini_overlay_matches_the_one_the_workers_use(self) -> None:
        compose = yaml.safe_load((_root() / "docker-compose.macmini.yml").read_text())
        worker = compose["services"]["prefect-worker"]["environment"]
        env, _ = _merged_init("docker-compose.macmini.yml")
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == worker["SAPPHIRE_CONFIG_OVERLAY"]
