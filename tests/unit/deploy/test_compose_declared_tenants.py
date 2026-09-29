"""Plan 513 T2: every stack that runs `init` gives the declared-tenant step its
config path, and the hosts that declare tenants pass the overlay that does."""

from __future__ import annotations

import tomllib
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


def _merged_init(
    overlay: str | None, *more_overlays: str
) -> tuple[dict[str, str], list[str]]:
    base = _init("docker-compose.yml")
    env: dict[str, str] = dict(base["environment"])  # type: ignore[arg-type]
    volumes: list[str] = list(base["volumes"])  # type: ignore[arg-type]
    for name in (overlay, *more_overlays):
        if name is None:
            continue
        extra = _init(name)
        env.update(extra.get("environment", {}))  # type: ignore[arg-type]
        volumes.extend(extra.get("volumes", []))  # type: ignore[arg-type]
    return env, volumes


def _declared_codes(mount: str) -> set[str]:
    """Tenant codes declared in the file a `source:dest:mode` mount names."""
    source = mount.split(":")[0]
    return set(tomllib.loads((_root() / source).read_text()).get("tenants", {}))


class TestBaseInit:
    def test_base_init_passes_the_config_path_and_mounts_it(self) -> None:
        env, volumes = _merged_init(None)
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert "./config.toml:/app/config.toml:ro" in volumes


class TestInitCommandChain:
    def test_tenant_step_is_joined_by_and_and_between_alembic_and_roles(self) -> None:
        command = _init("docker-compose.yml")["command"]
        assert isinstance(command, str)
        flat = " ".join(command.split())
        assert (
            "alembic upgrade head && python -m sapphire_flow.cli.provision_tenants "
            "&& /app/docker/bootstrap-roles.sh" in flat
        )


class TestStagingInit:
    def test_staging_mounts_its_own_overlay_exactly_and_selects_it(self) -> None:
        env, volumes = _merged_init("docker-compose.staging.yml")
        mount = (
            "./config/overlays/staging-5-stations.toml"
            ":/app/config/overlays/staging-5-stations.toml:ro"
        )
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == mount.split(":")[1]
        assert mount in volumes
        assert _declared_codes(mount) == set()


class TestMacMiniInit:
    def test_macmini_mounts_the_mac_mini_overlay_exactly_and_selects_it(self) -> None:
        env, volumes = _merged_init("docker-compose.macmini.yml")
        mount = "./config/overlays/mac-mini.toml:/app/config/overlays/mac-mini.toml:ro"
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == mount.split(":")[1]
        assert mount in volumes

    def test_the_file_the_mac_mini_mount_names_declares_chwrr(self) -> None:
        _, volumes = _merged_init("docker-compose.macmini.yml")
        overlay_mounts = [
            v for v in volumes if v.split(":")[1].endswith("mac-mini.toml")
        ]
        assert len(overlay_mounts) == 1
        assert "chwrr" in _declared_codes(overlay_mounts[0])

    def test_macmini_overlay_matches_the_one_the_workers_use(self) -> None:
        compose = yaml.safe_load((_root() / "docker-compose.macmini.yml").read_text())
        worker = compose["services"]["prefect-worker"]["environment"]
        env, _ = _merged_init("docker-compose.macmini.yml")
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == worker["SAPPHIRE_CONFIG_OVERLAY"]


class TestMacMiniInitWithTheOperatorOverlay:
    """Plan 510: the operator overlay adds the optional secret to `init` and must
    not disturb the Mac-mini's declared-tenant wiring."""

    _OVERLAYS = ("docker-compose.macmini.yml", "docker-compose.operator.yml")

    def test_the_declared_tenant_wiring_survives_the_operator_overlay(self) -> None:
        env, volumes = _merged_init(*self._OVERLAYS)
        mount = "./config/overlays/mac-mini.toml:/app/config/overlays/mac-mini.toml:ro"
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == "/app/config/overlays/mac-mini.toml"
        assert mount in volumes
        assert "chwrr" in _declared_codes(mount)

    def test_init_also_gets_the_operator_secret_path(self) -> None:
        env, _ = _merged_init(*self._OVERLAYS)
        assert env["SAPPHIRE_OPERATOR_DB_PASSWORD_FILE"] == (
            "/run/secrets/sapphire_operator_db_password"
        )

    def test_the_operator_overlay_alone_does_not_replace_init_command_or_overlay(
        self,
    ) -> None:
        extra = _init("docker-compose.operator.yml")
        assert "command" not in extra
        assert "SAPPHIRE_CONFIG_OVERLAY" not in extra.get("environment", {})
        assert "volumes" not in extra
