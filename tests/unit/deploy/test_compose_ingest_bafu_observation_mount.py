"""Plan 176 T1 — the collector's `DeploymentSpec` moved onto the `ingest`
work pool (D5), but the volume that pool's worker needs to WRITE the archive
must move with it. A pool move with a missing volume fails at *write* time,
after a successful fetch, which reads as a collector bug rather than a
deploy bug — this is the regression guard the plan's T1 explicitly asks for.

Rendered against the REAL merged/interpolated configuration (`docker compose
... config --format json`), mirroring `tests/unit/deploy/test_compose_backup_pool.py`.
Requires the `docker` CLI; skips (does not fail) if it is unavailable.
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


def _service(compose: dict[str, object], name: str) -> dict[str, object]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services[name]
    assert isinstance(svc, dict)
    return svc


def _archive_mount(svc: dict[str, object]) -> dict[str, object]:
    volumes = svc.get("volumes") or []
    matches = [
        v
        for v in volumes
        if isinstance(v, dict) and v.get("target") == "/data/bafu_observations"
    ]
    assert len(matches) == 1, (
        f"expected exactly one /data/bafu_observations mount, found {matches}"
    )
    return matches[0]


class TestIngestWorkerMountsTheBafuObservationArchive:
    """Not just "some volume lands on this target" — the SOURCE must be the
    named `bafu_observation_archive` volume (not e.g. a bind mount or a
    different named volume that happens to render at the same target), and
    this must hold on the ACTUAL deployed topology: base compose alone is
    never what runs on the mini — `docker-compose.macmini.yml` always layers
    on top (see `docs/standards/cicd.md`)."""

    @pytest.mark.parametrize(
        "compose_files",
        [
            ("docker-compose.yml",),
            ("docker-compose.yml", "docker-compose.macmini.yml"),
        ],
        ids=["base", "macmini-overlay"],
    )
    def test_prefect_worker_ingest_mounts_the_archive_volume_rw(
        self, compose_files: tuple[str, ...]
    ) -> None:
        compose = _rendered_compose(*compose_files)
        svc = _service(compose, "prefect-worker-ingest")
        mount = _archive_mount(svc)
        assert mount.get("type") == "volume"
        assert mount.get("source") == "bafu_observation_archive"
        assert mount.get("read_only") is not True
