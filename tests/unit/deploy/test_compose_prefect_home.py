"""Plan 103 (D1): writable ``PREFECT_HOME`` under the read-only container.

The three Prefect-client services (``prefect-worker``, ``prefect-worker-ingest``,
``init``) run ``read_only: true`` with no ``PREFECT_HOME`` set, so Prefect
defaults to ``/home/app/.prefect`` on the read-only root filesystem: a
``Failed to create the Prefect home directory`` warning on every start, and
``[Errno 30] Read-only file system`` on any local write (CLI profile, result
persistence).

Fix: set ``PREFECT_HOME=/tmp/prefect`` on those three services only. ``/tmp``
is already a writable tmpfs on each (``tmpfs: [/tmp]``). ``api`` is HTTP-only
(no Prefect client import) and must NOT get the variable.

Structural YAML-parse checks only — no docker dependency, no live stack.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _base_compose() -> dict[str, object]:
    return yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())


def _service_env(compose: dict[str, object], service: str) -> dict[str, object]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services[service]
    assert isinstance(svc, dict)
    env = svc.get("environment") or {}
    assert isinstance(env, dict)
    return env


class TestPrefectHomeSetOnClientServices:
    """The three Prefect-client services must set PREFECT_HOME to a path
    under the writable /tmp tmpfs (not the default /home/app/.prefect)."""

    def test_prefect_worker_has_prefect_home_under_tmp(self) -> None:
        env = _service_env(_base_compose(), "prefect-worker")
        assert env.get("PREFECT_HOME") == "/tmp/prefect"

    def test_prefect_worker_ingest_has_prefect_home_under_tmp(self) -> None:
        env = _service_env(_base_compose(), "prefect-worker-ingest")
        assert env.get("PREFECT_HOME") == "/tmp/prefect"

    def test_init_has_prefect_home_under_tmp(self) -> None:
        env = _service_env(_base_compose(), "init")
        assert env.get("PREFECT_HOME") == "/tmp/prefect"


class TestApiExcludedFromPrefectHome:
    """`api` is HTTP-only (no Prefect client import) and must NOT get the
    variable — it never writes to PREFECT_HOME."""

    def test_api_has_no_prefect_home(self) -> None:
        env = _service_env(_base_compose(), "api")
        assert "PREFECT_HOME" not in env


class TestPrefectHomeUnderWritableTmpfs:
    """/tmp/prefect must live under a tmpfs mount so the read-only root FS
    does not block the write (structural check: /tmp listed in `tmpfs:`)."""

    def test_prefect_worker_tmp_is_tmpfs(self) -> None:
        compose = _base_compose()
        svc = compose["services"]["prefect-worker"]
        assert "/tmp" in svc.get("tmpfs", [])

    def test_prefect_worker_ingest_tmp_is_tmpfs(self) -> None:
        compose = _base_compose()
        svc = compose["services"]["prefect-worker-ingest"]
        assert "/tmp" in svc.get("tmpfs", [])

    def test_init_tmp_is_tmpfs(self) -> None:
        compose = _base_compose()
        svc = compose["services"]["init"]
        assert "/tmp" in svc.get("tmpfs", [])
