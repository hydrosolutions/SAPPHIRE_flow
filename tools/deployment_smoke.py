from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from types import FrameType
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
_DATA = _ROOT / "src/sapphire_flow/data/deployment_smoke"
_LABEL = "org.sapphire-flow.smoke.run"
POSTGRES_IMAGE = (
    "postgis/postgis:16-3.5@sha256:"
    "94146ac37bc61e2322f88016056c5920729cb8c64c8542ed590af8fc2abdac07"
)
PREFECT_IMAGE = (
    "prefecthq/prefect:3-python3.11@sha256:"
    "369d8f0bb1fbf84c28b90d9af62dea8ef1b90d261fbc321f211dbba96883613c"
)
_SECRETS = (
    "db_password",
    "api_password",
    "worker_password",
    "backup_password",
    "access_token_pepper",
)
_CODE = (
    "cli/deployment_smoke.py",
    "flows/deployment_smoke.py",
    "services/deployment_smoke.py",
    "models/deployment_smoke.py",
    "adapters/deployment_smoke_weather.py",
)
_spec = importlib.util.spec_from_file_location(
    "smoke_image_identity_probe", _ROOT / "tools/image_identity_probe.py"
)
assert _spec is not None and _spec.loader is not None
_probe = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _probe
_spec.loader.exec_module(_probe)


class SmokeError(RuntimeError):
    """Only fixed, safe stage codes may reach the report."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _SAFE_FAILURE_CODES else "unexpected_failure"
        super().__init__(self.code)


_SAFE_FAILURE_CODES = frozenset(
    {
        "foreign_probe_refuses_cleanup",
        "probe_cleanup_failed",
        "timeout",
        "docker_unavailable_or_timeout",
        "docker_command_failed",
        "ambient_docker_or_compose_selector",
        "remote_docker_refused",
        "compose_version_unsupported",
        "postgres_platform_mismatch",
        "image_scenario_differs_from_checkout",
        "report_directory_must_be_new_and_absolute",
        "unsupported_path_characters",
        "unsafe_cleanup_record",
        "foreign_resource_refuses_cleanup",
        "cleanup_endpoint_changed",
        "cleanup_resources_remain",
        "unsafe_secret_directory",
        "api_forecast_metadata_mismatch",
        "api_issue_time_mismatch",
        "api_unexpected_input_quality_reason",
        "api_ensemble_metadata_mismatch",
        "api_valid_times_mismatch",
        "api_members_mismatch",
        "api_forecast_values_mismatch",
        "worker_run_failed",
        "worker_timeout",
        "api_authentication_not_enforced",
        "api_authorized_request_failed",
        "api_station_scope_mismatch",
    }
)


@dataclass
class Docker:
    environment: dict[str, str]
    deadline: float
    remaining_probes: list[str] = field(default_factory=list[str])

    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        if args[:1] == ["run"]:
            return self._run_probe(args, stdin=stdin)
        return self._execute(args, stdin=stdin)

    def _run_probe(self, args: list[str], *, stdin: str | None) -> str:
        identity = uuid4().hex
        name = "sapphire-smoke-probe-" + identity
        self.remaining_probes.append(name)
        try:
            return self._execute(
                ["run", "--name", name, "--label", f"{_LABEL}={identity}", *args[1:]],
                stdin=stdin,
            )
        finally:
            previous_deadline = self.deadline
            self.deadline = time.monotonic() + 20
            try:
                ids = self._execute(
                    ["ps", "-aq", "--filter", f"name=^/{name}$"]
                ).splitlines()
                for identifier in ids:
                    item = json.loads(
                        self._execute(["container", "inspect", identifier])
                    )[0]
                    if (
                        item["Name"] != "/" + name
                        or item["Config"]["Labels"].get(_LABEL) != identity
                    ):
                        raise SmokeError("foreign_probe_refuses_cleanup")
                    self._execute(["container", "rm", "--force", identifier])
                if self._execute(["ps", "-aq", "--filter", f"name=^/{name}$"]):
                    raise SmokeError("probe_cleanup_failed")
                self.remaining_probes.remove(name)
            finally:
                self.deadline = previous_deadline

    def _execute(self, args: list[str], *, stdin: str | None = None) -> str:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeError("timeout")
        try:
            result = subprocess.run(
                ["docker", *args],
                input=stdin,
                capture_output=True,
                text=True,
                env=self.environment,
                timeout=min(remaining, 300),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SmokeError("docker_unavailable_or_timeout") from exc
        if result.returncode:
            raise SmokeError("docker_command_failed")
        return result.stdout.strip()


def isolated_environment(environment: dict[str, str]) -> dict[str, str]:
    forbidden = {
        key
        for key in environment
        if key.startswith("COMPOSE_")
        or key
        in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
    }
    if forbidden:
        raise SmokeError("ambient_docker_or_compose_selector")
    return {
        key: environment[key]
        for key in ("PATH", "HOME", "USER", "TMPDIR", "DOCKER_CONFIG")
        if key in environment
    }


def local_endpoint(docker: Docker) -> str:
    data = json.loads(docker.run(["context", "inspect"]))
    endpoint = data[0]["Endpoints"]["docker"]["Host"]
    if not isinstance(endpoint, str) or not endpoint.startswith("unix:///"):
        raise SmokeError("remote_docker_refused")
    return endpoint


def source_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256(
            (_ROOT / "src/sapphire_flow" / name).read_bytes()
        ).hexdigest()
        for name in _CODE
    }


def preflight(docker: Docker, image: str) -> dict[str, Any]:
    endpoint = local_endpoint(docker)
    version = json.loads(docker.run(["compose", "version", "--format", "json"]))[
        "version"
    ]
    if not re.match(r"v?[2-9]\.\d+", version):
        raise SmokeError("compose_version_unsupported")
    for name in (POSTGRES_IMAGE, PREFECT_IMAGE):
        data = json.loads(docker.run(["image", "inspect", name]))[0]
        if name == POSTGRES_IMAGE and (data["Os"], data["Architecture"]) != (
            "linux",
            "amd64",
        ):
            raise SmokeError("postgres_platform_mismatch")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    release = (_ROOT / "RELEASE_TARGET").read_text().strip()
    probe = _probe.probe_image(
        image, release, revision, variant="default", runner=docker
    )
    payload = (
        "code = "
        + repr(_CODE)
        + "\n"
        + """import hashlib, json
from pathlib import Path
import sapphire_flow
from sapphire_flow.services.deployment_smoke import RESOURCE_DIR, verify_resources
root = Path(sapphire_flow.__file__).parent
print(json.dumps({
    "resources": verify_resources(),
    "code": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
             for name in code},
    "manifest": hashlib.sha256((RESOURCE_DIR / "manifest.json").read_bytes())
                .hexdigest(),
}))
"""
    )
    checked = json.loads(
        docker.run(
            _probe.probe_run_args(probe.image_id, release, revision), stdin=payload
        )
    )
    manifest_bytes = (_DATA / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if checked != {
        "resources": manifest["sha256"],
        "code": source_hashes(),
        "manifest": hashlib.sha256(manifest_bytes).hexdigest(),
    }:
        raise SmokeError("image_scenario_differs_from_checkout")
    return {
        "endpoint": endpoint,
        "image_id": probe.image_id,
        "release": release,
        "revision": revision,
        "scenario_id": manifest["scenario_id"],
        "resource_hashes": checked["resources"],
    }


def create_run_directory(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise SmokeError("report_directory_must_be_new_and_absolute")
    path.mkdir(mode=0o700, parents=False)
    run_id = uuid4().hex
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "project": "sapphire-smoke-" + run_id,
        **identity,
    }
    (path / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def create_secrets(path: Path) -> None:
    secret_dir = path / "secrets"
    secret_dir.mkdir(mode=0o700)
    for name in _SECRETS:
        file = secret_dir / name
        file.write_text(secrets.token_hex(32) + "\n")
        # The host parent is private. File mounts must be readable by Linux UID1000.
        file.chmod(0o444)


def compose_args(path: Path, record: dict[str, Any]) -> list[str]:
    return [
        "compose",
        "--env-file",
        str(path / "compose.env"),
        "--project-name",
        record["project"],
        "--project-directory",
        str(path),
        "-f",
        str(_ROOT / "docker-compose.smoke.yml"),
    ]


def write_compose_env(path: Path, record: dict[str, Any]) -> None:
    values = {
        "SMOKE_RUN_ID": record["run_id"],
        "SMOKE_IMAGE": record["image_id"],
        "SMOKE_SECRETS": str(path / "secrets"),
        "SMOKE_INIT_DB": str(_ROOT / "docker/init-db.sh"),
    }
    if any(any(c in value for c in "\n\r'$") for value in values.values()):
        raise SmokeError("unsupported_path_characters")
    (path / "compose.env").write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items())
    )


def read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or (path / "run.json").is_symlink():
        raise SmokeError("unsafe_cleanup_record")
    record = json.loads((path / "run.json").read_text())
    run_id = record.get("run_id", "")
    if (
        record.get("schema_version") != 1
        or not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or record.get("project") != "sapphire-smoke-" + run_id
    ):
        raise SmokeError("unsafe_cleanup_record")
    return record


def owned_resources(docker: Docker, record: dict[str, Any]) -> dict[str, list[str]]:
    resources: dict[str, list[str]] = {}
    for kind, list_command in (
        ("container", ["ps", "-aq"]),
        ("volume", ["volume", "ls", "-q"]),
        ("network", ["network", "ls", "-q"]),
    ):
        ids = docker.run(
            [
                *list_command,
                "--filter",
                "label=com.docker.compose.project=" + record["project"],
            ]
        ).splitlines()
        resources[kind] = ids
        for identifier in ids:
            item = json.loads(docker.run([kind, "inspect", identifier]))[0]
            labels = (
                item.get("Config", {}).get("Labels", {})
                if kind == "container"
                else item.get("Labels", {})
            )
            if (
                labels.get(_LABEL) != record["run_id"]
                or labels.get("com.docker.compose.project") != record["project"]
            ):
                raise SmokeError("foreign_resource_refuses_cleanup")
    return resources


def cleanup(docker: Docker, path: Path, record: dict[str, Any]) -> None:
    if local_endpoint(docker) != record["endpoint"]:
        raise SmokeError("cleanup_endpoint_changed")
    resources = owned_resources(docker, record)
    for kind in ("container", "network", "volume"):
        for identifier in resources[kind]:
            docker.run(
                [kind, "rm", *(["--force"] if kind == "container" else []), identifier]
            )
    if any(owned_resources(docker, record).values()):
        raise SmokeError("cleanup_resources_remain")


def erase_credentials(path: Path) -> None:
    secret_dir = path / "secrets"
    if secret_dir.is_symlink():
        raise SmokeError("unsafe_secret_directory")
    for name in _SECRETS:
        (secret_dir / name).unlink(missing_ok=True)
    if secret_dir.exists():
        secret_dir.rmdir()
    (path / "compose.env").unlink(missing_ok=True)


def app_command(
    docker: Docker, compose: list[str], command: str, parent: str | None = None
) -> dict[str, Any]:
    args = [
        *compose,
        "exec",
        "-T",
        "api",
        "/entrypoint.sh",
        "python",
        "-m",
        "sapphire_flow.cli.deployment_smoke",
        command,
    ]
    if parent is not None:
        args += ["--parent-id", parent]
    # App diagnostics may use stderr; stdout must be the sole JSON result.
    return json.loads(docker.run(args))


def api_probe(
    docker: Docker, compose: list[str], evidence: dict[str, Any], token: str
) -> dict[str, Any]:
    return json.loads(
        docker.run(
            [
                *compose,
                "exec",
                "-T",
                "worker",
                "/entrypoint.sh",
                "python",
                "-m",
                "sapphire_flow.cli.deployment_smoke",
                "api-probe",
            ],
            stdin=json.dumps(
                {
                    "forecast_id": evidence["forecast_id"],
                    "station_id": evidence["station_id"],
                    "token": token,
                }
            ),
        )
    )


def verify_api(
    payload: dict[str, Any], evidence: dict[str, Any], expected: dict[str, Any]
) -> int:
    fixed: dict[str, Any] = {
        "id": evidence["forecast_id"],
        "station_id": evidence["station_id"],
        "model_artifact_id": evidence["artifact_id"],
        "model_id": "deployment_smoke",
        "model_tier": "skill",
        "parameter": "discharge",
        "representation": "members",
        "status": "raw",
        "qc_status": "qc_passed",
        "input_quality": "degraded",
        "warm_up_source": "cold_start",
        "observation_staleness_hours": 1.0,
        "nwp_cycle_source": "primary",
        "qc_flags": [],
    }
    if any(payload.get(key) != value for key, value in fixed.items()):
        raise SmokeError("api_forecast_metadata_mismatch")
    for key in ("issued_at", "nwp_cycle_reference_time"):
        if datetime.fromisoformat(payload[key]) != datetime.fromisoformat(
            expected["issue_time"]
        ):
            raise SmokeError("api_issue_time_mismatch")
    if payload.get("input_quality_flags") != [
        {
            "category": "warm_up",
            "level": "degraded",
            "detail": "Cold start (no warm-up snapshot available)",
        }
    ]:
        raise SmokeError("api_unexpected_input_quality_reason")
    ensemble = payload["ensemble"]
    for key, value in {
        "representation": "members",
        "parameter": "discharge",
        "units": "m³/s",
        "forecast_horizon_steps": 3,
        "time_step_seconds": 3600,
        "member_count": 20,
    }.items():
        if ensemble.get(key) != value:
            raise SmokeError("api_ensemble_metadata_mismatch")
    if [datetime.fromisoformat(t) for t in ensemble["valid_times"]] != [
        datetime.fromisoformat(step["valid_time"]) for step in expected["forecast"]
    ]:
        raise SmokeError("api_valid_times_mismatch")
    series = ensemble["series"]
    if set(series) != {str(m) for m in expected["member_ids"]}:
        raise SmokeError("api_members_mismatch")
    for index, member in enumerate(expected["member_ids"]):
        values = series[str(member)]
        reference = [step["values"][index] for step in expected["forecast"]]
        if len(values) != 3 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or abs(value - target) > 1e-8
            for value, target in zip(values, reference, strict=True)
        ):
            raise SmokeError("api_forecast_values_mismatch")
    return 60


def last_setup_stage(docker: Docker, compose: list[str]) -> str:
    allowed = {
        "setup_identity",
        "station_creation",
        "observation_loader_qc",
        "observation_qualification",
        "artifact_import",
        "assignment_activation",
        "prefect_registration",
    }
    stage = "service_bootstrap"
    try:
        output = docker.run(
            [*compose, "logs", "--no-color", "--no-log-prefix", "--tail", "50", "init"]
        )
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                candidate = cast("dict[str, object]", item).get("smoke_stage")
                if isinstance(candidate, str) and candidate in allowed:
                    stage = candidate
    except Exception:
        pass
    return stage


def measure_resources(docker: Docker, compose: list[str]) -> dict[str, object]:
    peaks: dict[str, int] = {}
    sizes: dict[str, int] = {}
    try:
        for service in ("postgres", "prefect-server", "worker", "api"):
            peaks[service] = int(
                docker.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        service,
                        "cat",
                        "/sys/fs/cgroup/memory.peak",
                    ]
                )
            )
        for service, directory in (
            ("postgres", "/var/lib/postgresql/data"),
            ("api", "/data/artifacts"),
        ):
            sizes[service] = (
                int(
                    docker.run(
                        [*compose, "exec", "-T", service, "du", "-sk", directory]
                    ).split()[0]
                )
                * 1024
            )
    except (SmokeError, ValueError, IndexError):
        return {
            "status": "unavailable",
            "memory_peak_bytes": peaks,
            "volume_bytes": sizes,
        }
    return {"status": "measured", "memory_peak_bytes": peaks, "volume_bytes": sizes}


def run_smoke(docker: Docker, image: str, path: Path) -> int:
    started = time.monotonic()
    stage = "preflight"
    record: dict[str, Any] | None = None
    report: dict[str, Any] = {
        "schema_version": 1,
        "forecast_verified": False,
        "worker_evidence_verified": False,
        "cleanup_verified": False,
        "api_probe": {"origin": "worker", "target": "http://api:8000"},
        "host_port_verified": False,
    }
    exit_code = 2
    try:
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise SmokeError("report_directory_must_be_new_and_absolute")
        identity = preflight(docker, image)
        record = create_run_directory(path, identity)
        report.update(identity)
        report["run_id"] = record["run_id"]
        exit_code = 1
        stage = "prepare_credentials"
        create_secrets(path)
        write_compose_env(path, record)
        compose = compose_args(path, record)
        stage = "service_start"
        docker.run(
            [
                *compose,
                "up",
                "--detach",
                "--pull",
                "never",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "240",
            ]
        )
        report["resources"] = owned_resources(docker, record)
        stage = "worker_run"
        parent = app_command(docker, compose, "trigger")["parent_flow_run_id"]
        while True:
            state = app_command(docker, compose, "status", parent)["state"]
            if state == "COMPLETED":
                break
            if state in {"FAILED", "CRASHED", "CANCELLED", "CANCELLING"}:
                raise SmokeError("worker_run_failed")
            if time.monotonic() >= docker.deadline:
                raise SmokeError("worker_timeout")
            time.sleep(2)
        evidence = app_command(docker, compose, "evidence", parent)
        report.update(
            {
                key: evidence[key]
                for key in (
                    "station_id",
                    "artifact_id",
                    "forecast_id",
                    "parent_flow_run_id",
                    "child_flow_run_id",
                    "health",
                    "stations_attempted",
                    "stations_succeeded",
                    "stations_failed",
                    "forecasts_stored",
                    "cycle_duration_ms",
                    "observations_passed",
                    "weather_records",
                )
            }
        )
        report["worker_evidence_verified"] = True
        stage = "authenticated_api"
        token = app_command(docker, compose, "token")["token"]
        try:
            response = api_probe(docker, compose, evidence, token)
        finally:
            token = ""
        if response["unauthorized_status"] != 401:
            raise SmokeError("api_authentication_not_enforced")
        if response["forecast_status"] != 200:
            raise SmokeError("api_authorized_request_failed")
        expected = json.loads((_DATA / "expected-result.json").read_text())
        count = verify_api(response["forecast"], evidence, expected)
        if (
            response["station_status"] != 200
            or response["station"].get("no_floor") is not True
        ):
            raise SmokeError("api_station_scope_mismatch")
        report["resource_measurements"] = measure_resources(docker, compose)
        report.update(forecast_verified=True, comparisons=count)
        exit_code = 0
    except KeyboardInterrupt:
        report["failure"] = "interrupted"
    except Exception as exc:
        # Never copy exception text, child stdout/stderr, tokens, or connection URLs.
        report["failure"] = stage + "_failed"
        report["reason"] = (
            exc.code if isinstance(exc, SmokeError) else "unexpected_failure"
        )
        if stage == "service_start" and record is not None:
            report["application_stage"] = last_setup_stage(
                docker, compose_args(path, record)
            )
        report["next_action"] = (
            "Check the named stage and prerequisites; retry in a new directory."
        )
    finally:
        if record is not None:
            stage = "cleanup"
            docker.deadline = time.monotonic() + 120
            try:
                cleanup(docker, path, record)
                report["cleanup_verified"] = True
            except Exception as exc:
                report["cleanup_verified"] = False
                report["cleanup_reason"] = (
                    exc.code if isinstance(exc, SmokeError) else "unexpected_failure"
                )
                exit_code = 1
                report["cleanup_command"] = [
                    sys.executable,
                    str(_ROOT / "tools/deployment_smoke.py"),
                    "cleanup",
                    "--report-dir",
                    str(path),
                ]
                try:
                    report["remaining_resources"] = owned_resources(docker, record)
                except Exception:
                    report["remaining_resources"] = (
                        "unverified; inspect recorded project before retry"
                    )
                report["next_action"] = "Run the recorded cleanup command."
            finally:
                erase_credentials(path)
            report["duration_seconds"] = round(time.monotonic() - started, 3)
            (path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "forecast_verified": report["forecast_verified"],
                "cleanup_verified": report["cleanup_verified"],
                "failure": report.get("failure"),
                "reason": report.get("reason"),
                "remaining_probe_containers": getattr(docker, "remaining_probes", []),
            }
        )
    )
    return exit_code


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a disposable synthetic forecast through a process worker "
            "and authenticated API. Preload images first; never pulls or builds."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--image", required=True)
    run.add_argument("--report-dir", required=True, type=Path)
    clean = sub.add_parser("cleanup")
    clean.add_argument("--report-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        env = isolated_environment(dict(os.environ))
        docker = Docker(env, time.monotonic() + (600 if args.command == "run" else 120))
        if args.command == "run":
            signal.signal(signal.SIGTERM, _interrupt)
            return run_smoke(docker, args.image, args.report_dir)
        record = read_record(args.report_dir)
        try:
            cleanup(docker, args.report_dir, record)
        finally:
            erase_credentials(args.report_dir)
        report_path = args.report_dir / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["cleanup_verified"] = True
            report.pop("remaining_resources", None)
            report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"cleanup_verified": True}))
        return 0
    except Exception as exc:
        reason = exc.code if isinstance(exc, SmokeError) else "unexpected_failure"
        print(json.dumps({"failure": "preflight_or_cleanup_refused", "reason": reason}))
        return 2 if args.command == "run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
