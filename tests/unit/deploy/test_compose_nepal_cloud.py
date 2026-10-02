"""Plan 511 T2: the Nepal cloud host's compose overlay and Caddyfile.

The overlay must put the Nepal config overlay on every service that reads
config (a service without it silently runs the Swiss base config), keep the
BAFU collectors unregistered, pass the domain to Caddy, and publish nothing
beyond Caddy's 80/443."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

OVERLAY = "docker-compose.nepal-cloud.yml"
CONFIG_OVERLAY_PATH = "/app/config/overlays/nepal-cloud.toml"
CONFIG_OVERLAY_MOUNT = f"./config/overlays/nepal-cloud.toml:{CONFIG_OVERLAY_PATH}:ro"
CONFIG_READING_SERVICES = ("api", "prefect-worker", "prefect-worker-ingest", "init")


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _services(path: str) -> dict[str, dict[str, object]]:
    return yaml.safe_load((_root() / path).read_text())["services"]


def _env(service: dict[str, object]) -> dict[str, str]:
    return dict(service.get("environment", {}))  # type: ignore[call-overload]


def _volumes(service: dict[str, object]) -> list[str]:
    return list(service.get("volumes", []))  # type: ignore[call-overload]


class TestConfigOverlayIsOnEveryConfigReader:
    @pytest.mark.parametrize("name", CONFIG_READING_SERVICES)
    def test_selects_and_mounts_the_nepal_overlay(self, name: str) -> None:
        service = _services(OVERLAY)[name]
        assert _env(service)["SAPPHIRE_CONFIG_OVERLAY"] == CONFIG_OVERLAY_PATH
        assert CONFIG_OVERLAY_MOUNT in _volumes(service)

    def test_the_mounted_file_exists(self) -> None:
        assert (_root() / "config/overlays/nepal-cloud.toml").is_file()

    def test_init_still_gets_the_base_config_path_from_the_base_file(self) -> None:
        base_env = _env(_services("docker-compose.yml")["init"])
        assert base_env["SAPPHIRE_CONFIG"] == "/app/config.toml"
        assert "SAPPHIRE_CONFIG" not in _env(_services(OVERLAY)["init"])


class TestBafuCollectorsAreNotRegistered:
    def test_init_skips_exactly_the_two_bafu_collectors(self) -> None:
        skip = _env(_services(OVERLAY)["init"])["SAPPHIRE_SKIP_DEPLOYMENTS"]
        assert set(skip.split(",")) == {
            "collect-bafu-forecasts",
            "collect-bafu-observations",
        }

    def test_only_init_carries_the_skip_list(self) -> None:
        for name, service in _services(OVERLAY).items():
            if name != "init":
                assert "SAPPHIRE_SKIP_DEPLOYMENTS" not in _env(service)


class TestEdge:
    def test_caddy_requires_the_domain_and_mounts_the_nepal_caddyfile(self) -> None:
        caddy = _services(OVERLAY)["caddy"]
        assert _env(caddy)["SAPPHIRE_DOMAIN"].startswith("${SAPPHIRE_DOMAIN:?")
        assert "./Caddyfile.nepal:/etc/caddy/Caddyfile" in _volumes(caddy)

    def test_the_overlay_publishes_no_ports(self) -> None:
        for name, service in _services(OVERLAY).items():
            assert "ports" not in service, name

    def test_only_caddy_publishes_ports_in_the_base_file(self) -> None:
        published = [
            name
            for name, service in _services("docker-compose.yml").items()
            if service.get("ports")
        ]
        assert published == ["caddy"]

    def test_prefect_server_is_off_the_frontend_network(self) -> None:
        prefect = _services("docker-compose.yml")["prefect-server"]
        assert prefect["networks"] == ["backend"]


class TestNepalCaddyfile:
    @staticmethod
    def _text() -> str:
        return (_root() / "Caddyfile.nepal").read_text()

    @staticmethod
    def _directives() -> str:
        """The Caddyfile with comment lines removed."""
        return "\n".join(
            line
            for line in TestNepalCaddyfile._text().splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_domain_has_no_plain_http_fallback(self) -> None:
        directives = self._directives()
        assert "{$SAPPHIRE_DOMAIN} {" in directives
        assert "{$SAPPHIRE_DOMAIN::" not in directives

    def test_sends_hsts(self) -> None:
        assert "Strict-Transport-Security" in self._directives()

    def test_forwards_only_the_api_prefix_and_404s_the_rest(self) -> None:
        directives = self._directives()
        domain_site = directives.split("http://localhost:80")[0]
        assert re.search(r"@api path_regexp \^/api/v1/", domain_site)
        assert re.search(r"handle @api\s*\{\s*reverse_proxy api:8000", domain_site)
        assert domain_site.count("reverse_proxy") == 1
        assert re.search(r"handle\s*\{\s*respond \"Not Found\" 404", domain_site)

    def test_healthcheck_site_serves_only_the_health_route(self) -> None:
        health_site = self._directives().split("http://localhost:80")[1]
        assert re.search(
            r"path /api/v1/health\s+remote_ip 127\.0\.0\.1 ::1", health_site
        )
        assert re.search(r"handle @health\s*\{\s*reverse_proxy api:8000", health_site)
        assert health_site.count("reverse_proxy") == 1

    def test_the_base_healthcheck_targets_that_localhost_site(self) -> None:
        test = _services("docker-compose.yml")["caddy"]["healthcheck"]["test"]  # type: ignore[index]
        assert "http://localhost:80/api/v1/health" in test  # type: ignore[operator]


def _compose_config_json(
    *, project_root: Path | None = None
) -> dict[str, dict[str, object]]:
    """The merged stack as Docker Compose itself renders it (the unit tests above
    parse the two files separately and so never exercise Compose's merge rules)."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    root = project_root or _root()
    with tempfile.TemporaryDirectory() as env_dir:
        env_file = Path(env_dir) / "compose.env"
        env_file.write_text(
            "VERSION=0.1.0-test\n"
            "SAPPHIRE_RELEASE_VERSION=0.1.0-test\n"
            f"SAPPHIRE_SOURCE_REVISION={'a' * 40}\n"
            "SAPPHIRE_DOMAIN=nepal-staging.hydrosolutions.ch\n"
        )
        result = subprocess.run(
            [
                docker,
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                "docker-compose.yml",
                "-f",
                OVERLAY,
                "config",
                "--format",
                "json",
            ],
            cwd=root,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(Path.home()),
            },
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        message = result.stderr[:200]
        if "compose" in message and (
            "not a docker command" in message or "unknown command" in message
        ):
            pytest.skip(f"docker compose config unavailable: {message}")
        pytest.fail(f"docker compose config failed: {message}")
    return json.loads(result.stdout)["services"]


def _mounts(service: dict[str, object]) -> list[tuple[str, str]]:
    return [
        (str(v.get("source", "")), str(v["target"]))
        for v in service.get("volumes", [])  # type: ignore[attr-defined]
    ]


class TestComposeRenderedMerge:
    def test_render_uses_explicit_env_file_not_project_dotenv(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        project = tmp_path / "project"
        source.mkdir()
        project.mkdir()
        for name in ("docker-compose.yml", OVERLAY):
            shutil.copy2(_root() / name, source / name)
        poison = "poison-secret-from-project-dotenv"
        (source / ".env").write_text(
            f"VERSION={poison}\n"
            f"SAPPHIRE_RELEASE_VERSION={poison}\n"
            f"SAPPHIRE_SOURCE_REVISION={'b' * 40}\n"
            f"SAPPHIRE_DOMAIN={poison}\n"
        )
        (source / "secrets").mkdir()
        (source / "secrets" / "token.txt").write_text(poison)
        (source / "data").mkdir()
        (source / "data" / "secret.txt").write_text(poison)
        for name in ("docker-compose.yml", OVERLAY):
            shutil.copy2(source / name, project / name)
        assert not (project / ".env").exists()
        assert not (project / "secrets").exists()
        assert not (project / "data").exists()
        (project / ".env").write_text(
            f"VERSION={poison}\n"
            f"SAPPHIRE_RELEASE_VERSION={poison}\n"
            f"SAPPHIRE_SOURCE_REVISION={'b' * 40}\n"
            f"SAPPHIRE_DOMAIN={poison}\n"
        )

        services = _compose_config_json(project_root=project)
        rendered = json.dumps(services, sort_keys=True)

        assert poison not in rendered
        assert "0.1.0-test" in rendered
        assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in rendered
        assert "nepal-staging.hydrosolutions.ch" in rendered

    def test_overlay_lands_on_every_config_reader(self) -> None:
        services = _compose_config_json()
        for name in CONFIG_READING_SERVICES:
            env = services[name]["environment"]
            assert env["SAPPHIRE_CONFIG_OVERLAY"] == CONFIG_OVERLAY_PATH  # type: ignore[index]
            assert any(
                target == CONFIG_OVERLAY_PATH and source.endswith("nepal-cloud.toml")
                for source, target in _mounts(services[name])
            ), name

    def test_the_nepal_caddyfile_replaces_the_base_mount(self) -> None:
        caddyfile_mounts = [
            source
            for source, target in _mounts(_compose_config_json()["caddy"])
            if target == "/etc/caddy/Caddyfile"
        ]
        assert len(caddyfile_mounts) == 1
        assert caddyfile_mounts[0].endswith("Caddyfile.nepal")

    def test_only_caddy_publishes_ports_and_init_alone_skips_deployments(self) -> None:
        services = _compose_config_json()
        assert [n for n, s in services.items() if s.get("ports")] == ["caddy"]
        skipping = [
            n
            for n, s in services.items()
            if "SAPPHIRE_SKIP_DEPLOYMENTS" in (s.get("environment") or {})  # type: ignore[operator]
        ]
        assert skipping == ["init"]

    def test_prefect_server_is_on_the_backend_network_only(self) -> None:
        networks = _compose_config_json()["prefect-server"]["networks"]
        assert list(networks) == ["backend"]  # type: ignore[call-overload]
