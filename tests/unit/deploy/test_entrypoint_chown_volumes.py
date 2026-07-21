"""Structural regression test: every writable ``/data/*`` named-volume mount
on an app-image service (``prefect-worker``, ``prefect-worker-ingest``,
``api``, ``init`` — all built from the shared Dockerfile and therefore run
``docker/entrypoint.sh`` and drop privileges to ``app``) must be listed in
entrypoint.sh's ``chown app:app ...`` line, or the first write to a
freshly-created (root-owned) named volume fails with EACCES.

Regression: Plan 136 added
``bafu_observation_archive:/data/bafu_observations:rw`` to
``docker-compose.yml`` (the ``prefect-worker`` service) but the entrypoint
chown line was not updated to include it.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _compose() -> dict[str, object]:
    return yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())


def _parse_volume_entry(entry: object) -> tuple[str, str, str]:
    """Return ``(source, target, mode)`` for a compose volume entry.

    Supports both short (``"source:target[:mode]"``) and long (mapping)
    syntax. Mode defaults to ``"rw"`` when unspecified, matching Compose's
    own default.
    """
    if isinstance(entry, str):
        parts = entry.split(":")
        if len(parts) == 3:
            source, target, mode = parts
        elif len(parts) == 2:
            source, target = parts
            mode = "rw"
        else:
            raise ValueError(f"unparseable volume entry: {entry!r}")
        return source, target, mode
    if isinstance(entry, dict):
        source = str(entry.get("source", ""))
        target = str(entry.get("target", ""))
        mode = "ro" if entry.get("read_only") else "rw"
        return source, target, mode
    raise TypeError(f"unexpected volume entry type: {entry!r}")


def _writable_data_named_volume_targets(compose: dict[str, object]) -> set[str]:
    """Targets under ``/data/`` mounted read-write, via a top-level NAMED
    volume (not a bind mount), on a service built from the shared
    Dockerfile — i.e. a service whose container runs ``entrypoint.sh``."""
    services = compose["services"]
    assert isinstance(services, dict)
    named_volumes = compose.get("volumes") or {}
    assert isinstance(named_volumes, dict)

    targets: set[str] = set()
    for svc in services.values():
        assert isinstance(svc, dict)
        if "build" not in svc:
            # postgres / prefect-server / caddy use their own upstream
            # images directly — they never run our entrypoint.sh and manage
            # their own volume ownership.
            continue
        for entry in svc.get("volumes") or []:
            source, target, mode = _parse_volume_entry(entry)
            if source not in named_volumes:
                continue  # bind mount (e.g. ./config.toml), not a named volume
            if not target.startswith("/data/"):
                continue
            if mode == "ro":
                continue
            targets.add(target)
    return targets


def _entrypoint_chown_targets() -> list[str]:
    entrypoint = (_repo_root() / "docker" / "entrypoint.sh").read_text()
    for line in entrypoint.splitlines():
        stripped = line.strip()
        if stripped.startswith("chown app:app /data"):
            command = stripped.split("2>/dev/null")[0]
            return command.removeprefix("chown app:app").split()
    raise AssertionError(
        "no `chown app:app /data...` line found in docker/entrypoint.sh"
    )


class TestEntrypointChownCoversWritableDataVolumes:
    def test_every_writable_data_named_volume_is_chowned(self) -> None:
        compose = _compose()
        expected_targets = _writable_data_named_volume_targets(compose)
        chowned = set(_entrypoint_chown_targets())

        missing = expected_targets - chowned
        assert not missing, (
            "docker-compose.yml mounts writable /data/* named volumes that "
            f"docker/entrypoint.sh never chowns: {sorted(missing)}"
        )

    def test_bafu_observations_volume_is_chowned(self) -> None:
        # Narrow, explicit guard for the Plan 136 finding: entrypoint.sh
        # omitted /data/bafu_observations from the chown list.
        assert "/data/bafu_observations" in _entrypoint_chown_targets()
