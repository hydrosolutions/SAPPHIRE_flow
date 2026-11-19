from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "qualification_smoke", _ROOT / "tools/deployment_smoke.py"
)
assert _spec is not None and _spec.loader is not None
smoke = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = smoke
_spec.loader.exec_module(smoke)
_LABEL = "org.sapphire-flow.smoke.sentinel"
_REASONS = {
    "qualification_failed",
    "sentinel_changed",
    "sentinel_ownership_mismatch",
    "sentinel_cleanup_failed",
    "smoke_failed",
    "smoke_timeout",
    "invalid_smoke_report",
    "interrupted",
}


class QualificationError(RuntimeError):
    pass


def sentinel_resources(docker: Any, identity: str) -> dict[str, list[str]]:
    return {
        kind: docker.run(
            [*command, "--filter", f"label={_LABEL}={identity}"]
        ).splitlines()
        for kind, command in (
            ("container", ["ps", "-aq"]),
            ("network", ["network", "ls", "-q"]),
            ("volume", ["volume", "ls", "-q"]),
        )
    }


def inspect_sentinel(docker: Any, identity: str) -> dict[str, dict[str, Any]]:
    found = sentinel_resources(docker, identity)
    records: dict[str, dict[str, Any]] = {}
    name = "sapphire-smoke-sentinel-" + identity
    for kind, ids in found.items():
        if len(ids) > 1:
            raise QualificationError("sentinel_ownership_mismatch")
        if not ids:
            continue
        record = json.loads(docker.run([kind, "inspect", ids[0]]))[0]
        labels = record.get("Config", record).get("Labels", {})
        if record.get("Name", "").lstrip("/") != name or labels.get(_LABEL) != identity:
            raise QualificationError("sentinel_ownership_mismatch")
        records[kind] = record
    return records


def create_sentinel(docker: Any, identity: str, image: str) -> None:
    name = "sapphire-smoke-sentinel-" + identity
    label = f"{_LABEL}={identity}"
    docker.run(["network", "create", "--internal", "--label", label, name])
    docker.run(["volume", "create", "--label", label, name])
    docker.run(
        [
            "container",
            "create",
            "--name",
            name,
            "--label",
            label,
            "--network",
            name,
            "--mount",
            f"type=volume,source={name},target=/sentinel",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "0",
            "--entrypoint",
            "python",
            image,
            "-I",
            "-B",
            "-c",
            "import time; time.sleep(86400)",
        ]
    )
    docker.run(["container", "start", name])
    docker.run(
        [
            "exec",
            name,
            "python",
            "-I",
            "-c",
            "from pathlib import Path; import sys; "
            "Path('/sentinel/value').write_text(sys.argv[1])",
            identity,
        ]
    )


def sentinel_snapshot(docker: Any, identity: str) -> dict[str, Any]:
    records = inspect_sentinel(docker, identity)
    if set(records) != {"container", "network", "volume"}:
        raise QualificationError("sentinel_changed")
    container, network, volume = (
        records[k] for k in ("container", "network", "volume")
    )
    if container["State"]["Running"] is not True:
        raise QualificationError("sentinel_changed")
    content = docker.run(
        [
            "exec",
            container["Id"],
            "python",
            "-I",
            "-c",
            "from pathlib import Path; import hashlib; "
            "print(hashlib.sha256(Path('/sentinel/value').read_bytes()).hexdigest())",
        ]
    )
    if content != hashlib.sha256(identity.encode()).hexdigest():
        raise QualificationError("sentinel_changed")
    return {
        "container_id": container["Id"],
        "running": container["State"]["Running"],
        "started_at": container["State"]["StartedAt"],
        "restart_count": container["RestartCount"],
        "network_id": network["Id"],
        "volume_name": volume["Name"],
        "volume_created_at": volume["CreatedAt"],
        "content_sha256": content,
        "mounts": container["Mounts"],
        "networks": container["NetworkSettings"]["Networks"],
    }


def cleanup_sentinel(docker: Any, identity: str) -> None:
    # Validate every remaining resource before removing any of them.
    records = inspect_sentinel(docker, identity)
    for kind in ("container", "network", "volume"):
        if kind in records:
            record = records[kind]
            docker.run(
                [
                    kind,
                    "rm",
                    *(["-f"] if kind == "container" else []),
                    record.get("Id", record["Name"]),
                ]
            )
    if any(sentinel_resources(docker, identity).values()):
        raise QualificationError("sentinel_cleanup_failed")


def safe_run_report(path: Path) -> dict[str, Any]:
    raw = json.loads((path / "report.json").read_text())
    fields = (
        "forecast_verified",
        "worker_evidence_verified",
        "cleanup_verified",
        "host_port_verified",
    )
    if any(type(raw.get(key)) is not bool for key in fields):
        raise QualificationError("invalid_smoke_report")
    report = {key: raw[key] for key in fields}
    for key, limit in (
        ("comparisons", 60),
        ("observations_passed", 72),
        ("weather_records", 3),
    ):
        value = raw.get(key, 0)
        if type(value) is not int or not 0 <= value <= limit:
            raise QualificationError("invalid_smoke_report")
        report[key] = value
    reason = raw.get("reason")
    if reason is not None:
        report["reason"] = (
            reason if reason in smoke._SAFE_FAILURE_CODES else "unexpected_failure"
        )
    return report


def run_cli(
    docker: Any,
    image: str,
    path: Path,
    image_identity: Literal["release-target", "ci-development"],
) -> int:
    remaining = docker.deadline - time.monotonic()
    if remaining <= 0:
        raise QualificationError("smoke_timeout")
    command = [
        sys.executable,
        str(_ROOT / "tools/deployment_smoke.py"),
        "run",
        "--image",
        image,
        "--report-dir",
        str(path),
        "--image-identity",
        image_identity,
        "--runtime-timeout-seconds",
        "240",
    ]
    # Reports are the only retained child output. No raw logs enter CI artifacts.
    process = subprocess.Popen(
        command,
        cwd=_ROOT,
        env=docker.environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=min(390, remaining))
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        raise QualificationError(
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "smoke_timeout"
        ) from None


def recover_runs(environment: dict[str, str], deadline: float, path: Path) -> bool:
    recovered = True
    for number in (1, 2):
        run_path = path / f"run-{number}"
        if not (run_path / "run.json").exists():
            continue
        recovery = smoke.Docker(environment, min(deadline - 30, time.monotonic() + 45))
        try:
            smoke.cleanup(recovery, run_path, smoke.read_record(run_path))
        except Exception:
            recovered = False
        finally:
            try:
                smoke.erase_credentials(run_path)
            except Exception:
                recovered = False
    return recovered


def qualify(
    image: str, path: Path, image_identity: Literal["release-target", "ci-development"]
) -> int:
    # 17 minutes of work plus 2 minutes reserved for recovery, under CI's 20.
    deadline = time.monotonic() + 1140
    docker = smoke.Docker(smoke.isolated_environment(dict(os.environ)), deadline - 120)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "qualified": False,
        "runs": [],
        "sentinel_unchanged": [],
        "sentinel_cleanup_verified": False,
    }
    identity = uuid4().hex
    prepared = False
    endpoint: str | None = None
    try:
        if not path.is_absolute() or path.exists():
            raise QualificationError("qualification_failed")
        path.mkdir(mode=0o700)
        prepared = True
        endpoint = smoke.local_endpoint(docker)
        (path / "sentinel.json").write_text(
            json.dumps({"identity": identity, "endpoint": endpoint}) + "\n"
        )
        image_id = json.loads(docker.run(["image", "inspect", image]))[0]["Id"]
        for infrastructure in (smoke.POSTGRES_IMAGE, smoke.PREFECT_IMAGE):
            pull = smoke.Docker(
                docker.environment, min(docker.deadline, time.monotonic() + 90)
            )
            pull.run(
                [
                    "pull",
                    *(
                        ["--platform", "linux/amd64"]
                        if infrastructure == smoke.POSTGRES_IMAGE
                        else []
                    ),
                    infrastructure,
                ]
            )
        create_sentinel(docker, identity, image_id)
        before = sentinel_snapshot(docker, identity)
        for number in (1, 2):
            run_path = path / f"run-{number}"
            try:
                code = run_cli(docker, image_id, run_path, image_identity)
                report = safe_run_report(run_path)
                summary["runs"].append(report)
            finally:
                unchanged = sentinel_snapshot(docker, identity) == before
                summary["sentinel_unchanged"].append(unchanged)
                if not unchanged:
                    raise QualificationError("sentinel_changed")
            if code or report != {
                "forecast_verified": True,
                "worker_evidence_verified": True,
                "cleanup_verified": True,
                "host_port_verified": False,
                "comparisons": 60,
                "observations_passed": 72,
                "weather_records": 3,
            }:
                raise QualificationError("smoke_failed")
        summary["qualified"] = True
    except KeyboardInterrupt:
        summary["reason"] = "interrupted"
    except Exception as exc:
        summary["reason"] = (
            str(exc)
            if isinstance(exc, QualificationError) and str(exc) in _REASONS
            else "qualification_failed"
        )
    finally:
        if prepared:
            if not recover_runs(docker.environment, deadline, path):
                summary["qualified"] = False
                summary["reason"] = "qualification_failed"
            docker.deadline = deadline
            try:
                if endpoint is not None:
                    if smoke.local_endpoint(docker) != endpoint:
                        raise QualificationError("sentinel_ownership_mismatch")
                    cleanup_sentinel(docker, identity)
                    summary["sentinel_cleanup_verified"] = True
            except Exception:
                summary["qualified"] = False
                summary["reason"] = "sentinel_cleanup_failed"
            (path / "qualification.json").write_text(
                json.dumps(summary, indent=2) + "\n"
            )
    print(json.dumps(summary))
    return 0 if summary["qualified"] else 1


def cleanup(path: Path) -> int:
    record = json.loads((path / "sentinel.json").read_text())
    identity = record["identity"]
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise QualificationError("sentinel_ownership_mismatch")
    docker = smoke.Docker(
        smoke.isolated_environment(dict(os.environ)), time.monotonic() + 120
    )
    if smoke.local_endpoint(docker) != record["endpoint"]:
        raise QualificationError("sentinel_ownership_mismatch")
    recovered = recover_runs(docker.environment, docker.deadline, path)
    cleanup_sentinel(docker, identity)
    if not recovered:
        raise QualificationError("qualification_failed")
    print(json.dumps({"cleanup_verified": True}))
    return 0


def interrupted(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify the smoke command twice beside an unrelated sentinel; "
        "prepares pinned infrastructure images."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--image", required=True)
    run.add_argument("--report-dir", required=True, type=Path)
    run.add_argument(
        "--image-identity",
        choices=["release-target", "ci-development"],
        default="release-target",
    )
    clean = sub.add_parser("cleanup")
    clean.add_argument("--report-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return (
            cleanup(args.report_dir)
            if args.command == "cleanup"
            else qualify(args.image, args.report_dir, args.image_identity)
        )
    except Exception:
        print(json.dumps({"qualified": False, "reason": "qualification_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
