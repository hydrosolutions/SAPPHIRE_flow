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

import functools
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


@functools.cache
def _base_compose() -> dict[str, object]:
    return _rendered_compose("docker-compose.yml")


def _service(compose: dict[str, object], name: str) -> dict[str, object]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services[name]
    assert isinstance(svc, dict)
    return svc


def _volume_targets(svc: dict[str, object]) -> list[str]:
    volumes = svc.get("volumes") or []
    return [v["target"] for v in volumes if isinstance(v, dict) and "target" in v]


class TestIngestWorkerMountsTheBafuObservationArchive:
    def test_prefect_worker_ingest_mounts_the_archive_volume_rw(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-ingest")
        assert "/data/bafu_observations" in _volume_targets(svc)
        volumes = svc.get("volumes") or []
        matches = [
            v
            for v in volumes
            if isinstance(v, dict) and v.get("target") == "/data/bafu_observations"
        ]
        assert len(matches) == 1
        assert matches[0].get("read_only") is not True
