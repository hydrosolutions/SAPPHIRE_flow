"""Plan 262 T2: the forecast worker builds its OWN aquacast/torch image; every
other built service keeps the default, torch-free one.

The Dockerfile's rule is that "only the forecast-cycle worker image installs it,
because it pulls torch and the whole ML stack". Before this plan all five built
services shared one `build: *app-build` anchor AND one image tag, so putting
`WITH_AQUACAST=1` on the anchor would have shipped torch everywhere. These tests
lock the split structurally so a later edit cannot quietly re-anchor the worker
or collapse the two tags back into one.

Structural YAML parse — no docker dependency, no substring scans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_IMAGE_SERVICES = frozenset(
    {"prefect-worker-ingest", "prefect-worker-backup", "api", "init"}
)
_AQUACAST_SERVICE = "prefect-worker"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _compose() -> dict[str, Any]:
    return yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())


def _service(name: str) -> dict[str, Any]:
    return _compose()["services"][name]


class TestTheForecastWorkerBuildsItsOwnImage:
    def test_it_does_not_share_the_default_image_tag(self) -> None:
        """The whole point: two tags, so torch cannot reach the other four."""
        worker_image = _service(_AQUACAST_SERVICE)["image"]

        assert worker_image.startswith("sapphire-flow-aquacast:")
        for name in _DEFAULT_IMAGE_SERVICES:
            assert _service(name)["image"].startswith("sapphire-flow:")
            assert _service(name)["image"] != worker_image

    def test_it_declares_the_aquacast_build_argument(self) -> None:
        build = _service(_AQUACAST_SERVICE)["build"]

        assert build["args"]["WITH_AQUACAST"] == "1"

    def test_it_carries_both_build_secrets(self) -> None:
        """The aquacast clone needs its own token; the recap clone still needs its."""
        build = _service(_AQUACAST_SERVICE)["build"]

        assert set(build["secrets"]) == {"recap_dg_client_token", "aquacast_token"}

    def test_the_aquacast_token_is_env_sourced_not_file_based(self) -> None:
        """A `file:` entry would break `docker compose build` and `config` on any host
        without that file — including CI and every dev machine.
        """
        secret = _compose()["secrets"]["aquacast_token"]

        assert "environment" in secret
        assert "file" not in secret
        assert secret["environment"] == "AQUACAST_TOKEN"


class TestTheOtherBuiltServicesStayTorchFree:
    def test_none_of_them_requests_the_aquacast_extra(self) -> None:
        """If any picked up WITH_AQUACAST — directly or by sharing the worker's build
        block — torch would ship in the default image, which the Dockerfile forbids.
        """
        for name in _DEFAULT_IMAGE_SERVICES:
            build = _service(name)["build"]
            args = build.get("args", {}) if isinstance(build, dict) else {}

            assert "WITH_AQUACAST" not in args, name

    def test_none_of_them_mounts_the_aquacast_build_secret(self) -> None:
        for name in _DEFAULT_IMAGE_SERVICES:
            build = _service(name)["build"]
            secrets = build.get("secrets", []) if isinstance(build, dict) else []

            assert "aquacast_token" not in secrets, name

    def test_exactly_one_built_service_carries_the_ml_stack(self) -> None:
        """Guards the reverse regression too: a second service quietly opting in."""
        services = _compose()["services"]
        with_aquacast = {
            name
            for name, spec in services.items()
            if isinstance(spec.get("build"), dict)
            and spec["build"].get("args", {}).get("WITH_AQUACAST") == "1"
        }

        assert with_aquacast == {_AQUACAST_SERVICE}
