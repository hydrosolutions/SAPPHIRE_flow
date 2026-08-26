"""Plan 198 T7 — the `api` service needs BOTH a compose-level mount of the
quarantined BAFU forecast archive AND a resolvable config path to it, or the
Forecast Lab snapshot route reports "archive not mounted" forever even with
the volume correctly attached (D2).

Two separate assertions, deliberately not one:

- (a) Wiring — string/dict comparison over the RENDERED compose JSON, no
  config loading. Catches the compose wiring being wrong or later removed.
  Red on today's pre-T7 repo (F1, F2).
- (b) Path derivation — `load_config()` against the REAL repo-relative
  files, with `SAPPHIRE_CONFIG_OVERLAY` pointed at the repo copy rather than
  the container path. Catches the mac-mini overlay's path declaration being
  removed or renamed. Green already (a guard, not a red-first assertion) —
  the overlay declares the archive path as a literal container-absolute
  string, so the value a host-run test derives is the value the container
  derives.

Rendered against the REAL merged/interpolated configuration (`docker
compose ... config --format json`), mirroring
`tests/unit/deploy/test_compose_ingest_bafu_observation_mount.py` — the same
mechanism, one service over.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _rendered_compose(*filenames: str) -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    root = _repo_root()
    args = ["docker", "compose"]
    for name in filenames:
        args += ["-f", str(root / name)]
    args += ["config", "--format", "json"]
    env = dict(os.environ)
    env.setdefault("VERSION", "0.0.0-test")
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        args, cwd=root, env=env, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed: {result.stderr}")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _api_service(compose: dict[str, object]) -> dict[str, object]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services["api"]
    assert isinstance(svc, dict)
    return svc


def _mount(svc: dict[str, object], target: str) -> dict[str, object] | None:
    volumes = svc.get("volumes") or []
    matches = [v for v in volumes if isinstance(v, dict) and v.get("target") == target]
    if not matches:
        return None
    assert len(matches) == 1, f"expected at most one mount at {target}, found {matches}"
    return matches[0]


class TestApiServiceWiring:
    """(a) — string/dict comparison over rendered compose JSON."""

    def test_api_mounts_the_bafu_forecast_archive_read_only(self) -> None:
        compose = _rendered_compose("docker-compose.yml", "docker-compose.macmini.yml")
        svc = _api_service(compose)
        mount = _mount(svc, "/data/bafu_forecasts")
        assert mount is not None, "api service has no /data/bafu_forecasts mount"
        assert mount.get("type") == "volume"
        assert mount.get("source") == "bafu_forecast_archive"
        assert mount.get("read_only") is True

    def test_api_sets_sapphire_config(self) -> None:
        compose = _rendered_compose("docker-compose.yml", "docker-compose.macmini.yml")
        svc = _api_service(compose)
        env = svc.get("environment")
        assert isinstance(env, dict)
        assert env.get("SAPPHIRE_CONFIG") == "/app/config.toml"

    def test_api_sets_config_overlay_and_mounts_it_on_the_mini(self) -> None:
        compose = _rendered_compose("docker-compose.yml", "docker-compose.macmini.yml")
        svc = _api_service(compose)
        env = svc.get("environment")
        assert isinstance(env, dict)
        assert (
            env.get("SAPPHIRE_CONFIG_OVERLAY") == "/app/config/overlays/mac-mini.toml"
        )
        mount = _mount(svc, "/app/config/overlays/mac-mini.toml")
        assert mount is not None, (
            "api service has no mount for the mac-mini config overlay file"
        )
        assert mount.get("type") == "bind"
        assert mount.get("read_only") is True
        # The SOURCE matters too: without this a bind from any unrelated file
        # to the right target would satisfy the assertions above, and the
        # container would load the wrong overlay (or none of ours).
        source = mount.get("source")
        assert source is not None
        assert (
            Path(str(source)).resolve()
            == (_repo_root() / "config/overlays/mac-mini.toml").resolve()
        ), f"overlay mount source is {source!r}"

    def test_base_compose_alone_has_no_archive_mount_on_api(self) -> None:
        """The base file alone is never what runs on the mini (the overlay
        always layers on top) — but the BASE mount itself must still exist
        without the overlay, since the overlay adds only the path/env
        pieces, not the volume (that lives in docker-compose.yml)."""
        compose = _rendered_compose("docker-compose.yml")
        svc = _api_service(compose)
        mount = _mount(svc, "/data/bafu_forecasts")
        assert mount is not None
        assert mount.get("source") == "bafu_forecast_archive"
        assert mount.get("read_only") is True


class TestArchivePathResolvesThroughTheOverlay:
    """(b) — path derivation via `load_config()` against the real files."""

    def test_archive_path_resolves_with_the_mini_overlay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config.deployment import load_config

        root = _repo_root()
        overlay = root / "config" / "overlays" / "mac-mini.toml"
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
        config = load_config(root / "config.toml")
        assert config.bafu_forecast_archive_path == Path("/data/bafu_forecasts")

    def test_archive_path_is_none_without_the_overlay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config.deployment import load_config

        root = _repo_root()
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        config = load_config(root / "config.toml")
        assert config.bafu_forecast_archive_path is None
