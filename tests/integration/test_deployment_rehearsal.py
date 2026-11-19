from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
import yaml

from tools import deployment_rehearsal as rehearsal
from tools import qualify_deployment_smoke as sentinel

ROOT = Path(__file__).resolve().parents[2]


def materialize(directory: Path, revision: str, tag: str) -> tuple[Path, Path]:
    directory.mkdir(mode=0o700)
    fixture = ROOT / "tests/fixtures/deployment_rehearsal"
    shared = ROOT / "tests/fixtures/deployment_profile"
    replacements = {
        "@CHECKOUT@": str(ROOT),
        "@PROFILE_DIR@": str(directory),
        "@IMAGE_TAG@": tag,
        "@SOURCE_REVISION@": revision,
    }
    for name in (
        "profile.toml",
        "installation.toml",
        "application.toml",
        "compose.yml",
    ):
        text = (
            (fixture if name in {"profile.toml", "installation.toml"} else shared)
            / name
        ).read_text()
        for key, value in replacements.items():
            text = text.replace(key, json.dumps(value)[1:-1])
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    return directory / "profile.toml", directory / "installation.toml"


def cli_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def assert_absent(run_id: str) -> None:
    project = "sapphire-rehearsal-" + run_id
    for kind, listing in (
        ("container", ["ps", "-a"]),
        ("network", ["network", "ls"]),
        ("volume", ["volume", "ls"]),
    ):
        for selector in (
            "label=org.sapphire.rehearsal=" + run_id,
            "label=com.docker.compose.project=" + project,
            "name=^" + project + "[-_]",
        ):
            result = subprocess.run(
                [
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    *listing,
                    "--filter",
                    selector,
                    "--format",
                    "{{.ID}}" if kind != "volume" else "{{.Name}}",
                ],
                env=cli_environment(),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0 and not result.stdout.strip(), (
                "independent cleanup inventory failed"
            )


def expected_cleanup_counts() -> dict[str, int]:
    recipe = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    # Compose creates volumes referenced by started services, not unused declarations.
    volumes: set[str] = set()
    for service in recipe["services"].values():
        for mount in service.get("volumes", []):
            source = (
                mount.split(":", 1)[0]
                if isinstance(mount, str)
                else mount.get("source")
            )
            if source in recipe["volumes"]:
                volumes.add(source)
    return {
        "container": len(recipe["services"]),
        "network": len(recipe["networks"]),
        "volume": len(volumes),
    }


def retain_outcome(
    result: dict[str, Any], evidence: dict[str, Any], returncode: int
) -> None:
    keys = (
        "status",
        "code",
        "codes",
        "stage",
        "offline_status",
        "failure_code",
        "failure_operation",
        "cleanup_code",
        "cleanup_cause",
        "cleanup_operation",
        "run_id",
        "identity_kind",
        "source_revision",
        "image_ids",
        "verified_checks",
        "forecast_executed",
        "cleanup_counts",
    )
    evidence.update({key: result[key] for key in keys if key in result})
    evidence["exit_code"] = returncode


def launch(
    profile: Path,
    installation: Path,
    report: Path,
    *,
    fault: bool,
    deadline: float,
    canaries: tuple[str, ...],
    evidence: dict[str, Any],
) -> None:
    command = [
        sys.executable,
        str(ROOT / "tools/deployment_rehearsal.py"),
        "--profile-file",
        str(profile),
        "run",
        "--installation",
        str(installation),
        "--report-dir",
        str(report),
        "--image-identity",
        "ci-development",
        "--format",
        "json",
    ]
    injected = False
    with subprocess.Popen(
        command,
        cwd=profile.parent,
        env=cli_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            while process.poll() is None:
                if time.monotonic() > deadline - 120:
                    process.send_signal(signal.SIGTERM)
                    break
                summary = report / "summary.json"
                if fault and not injected and summary.exists():
                    progress = json.loads(summary.read_text())
                    if progress.get("stage") == "init":
                        record = rehearsal.read_recovery(report)
                        runtime = rehearsal.Runtime(
                            directory=report, deadline=deadline, record=record
                        )
                        try:
                            identifier = runtime.service("init")
                        except rehearsal.RehearsalRuntimeError:
                            identifier = None
                        if identifier is not None:
                            runtime.inspect_owned("container", identifier)
                            runtime.run(["kill", identifier])
                            injected = True
                time.sleep(0.1)
            output, errors = process.communicate(
                timeout=max(1, min(120, deadline - time.monotonic()))
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    if any(value in output or value in errors for value in canaries):
        evidence.update(status="FAILED", code="harness_secret_output_detected")
        raise AssertionError("credential canary escaped")
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError
    except ValueError:
        evidence.update(status="FAILED", code="harness_output_invalid")
        raise AssertionError("invalid safe rehearsal report") from None
    result = cast("dict[str, Any]", result)
    retain_outcome(result, evidence, process.returncode)
    assert result["scope"] == "disposable_rehearsal"
    if fault:
        assert injected, "owned init failure was not injected"
        assert process.returncode == 1 and result["failure_code"] == "init_failed", (
            "injected init failure was not reported"
        )
    else:
        assert process.returncode == 0 and result["status"] == "PASS_REHEARSAL", (
            "rehearsal did not pass"
        )
        assert result["cleanup_counts"] == expected_cleanup_counts()
        assert result["forecast_executed"] is False
        assert {
            "paused_pools_before_init",
            "jobs_schedules_pools_paused",
            "worker_heartbeats",
            "internal_api_health_auth_empty_collection",
            "no_execution",
        } <= set(result["verified_checks"])
    assert_absent(result["run_id"])
    assert result["cleanup_code"] is None, "rehearsal cleanup was incomplete"
    assert not (report / "secrets").exists() and not (report / "inputs").exists()
    assert not (report / "recipe.json").exists()
    # Cleanup must work with no original selected files or credential content.
    cleaned = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/deployment_rehearsal.py"),
            "cleanup",
            "--report-dir",
            str(report),
            "--format",
            "json",
        ],
        cwd=report,
        env=cli_environment(),
        capture_output=True,
        text=True,
        timeout=130,
        check=False,
    )
    assert cleaned.returncode == 0, "idempotent cleanup failed"


@pytest.mark.deployment
class TestDeploymentRehearsal:
    def test_two_runs_init_failure_and_sentinel(self, tmp_path: Path) -> None:
        if os.environ.get("SAPPHIRE_REHEARSAL_ACCEPTANCE") != "dedicated-test-daemon":
            pytest.skip("requires explicit dedicated-daemon opt-in and prepared images")
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tag = os.environ["SAPPHIRE_REHEARSAL_IMAGE_TAG"]
        deadline = time.monotonic() + 2700
        profile, installation = materialize(tmp_path / "private-profile", revision, tag)
        delivery = profile.parent / "secret-delivery"
        delivery.mkdir(mode=0o700)
        roles = tuple(
            sorted(tomllib.loads(installation.read_text())["secret_bindings"])
        )
        canaries = tuple("rehearsal-original-canary-" + uuid4().hex for _ in roles)
        for name, value in zip(roles, canaries, strict=True):
            (delivery / name).write_text(value)
        sentinel_dir = tmp_path / "sentinel-control"
        sentinel_dir.mkdir(mode=0o700)
        (sentinel_dir / "docker-config").mkdir(mode=0o700)
        docker = rehearsal.Runtime(directory=sentinel_dir, deadline=deadline)
        daemon = docker.daemon()
        identity = uuid4().hex
        reports: list[Path] = []
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "scope": "hosted_development_rehearsal_qualification",
            "passed": False,
            "sentinel_preserved": False,
            "sentinel_cleanup_verified": False,
            "runs": [],
        }
        safe_report = Path(
            os.environ.get(
                "SAPPHIRE_REHEARSAL_QUALIFICATION_REPORT",
                str(tmp_path / "qualification.json"),
            )
        )
        before: Any = None
        try:
            image = json.loads(
                docker.run(["image", "inspect", "sapphire-flow:" + tag])
            )[0]["Id"]
            sentinel.create_sentinel(docker, identity, image)
            before = sentinel.sentinel_snapshot(docker, identity)
            for index in range(3):
                report = tmp_path / f"run-{index}"
                reports.append(report)
                outcome: dict[str, Any] = {
                    "status": "UNVERIFIED",
                    "recovery_status": "unverified",
                    "absence_verified": False,
                }
                evidence["runs"].append(outcome)
                launch(
                    profile,
                    installation,
                    report,
                    fault=index == 2,
                    deadline=deadline,
                    canaries=canaries,
                    evidence=outcome,
                )
                assert sentinel.sentinel_snapshot(docker, identity) == before, (
                    "unrelated sentinel changed"
                )
                assert (
                    tuple((delivery / name).read_text() for name in roles) == canaries
                )
            evidence["passed"] = True
            evidence["sentinel_preserved"] = True
        finally:
            try:
                for report, outcome in zip(reports, evidence["runs"], strict=True):
                    if (report / "recovery.json").exists():
                        recovered = subprocess.run(
                            [
                                sys.executable,
                                str(ROOT / "tools/deployment_rehearsal.py"),
                                "cleanup",
                                "--report-dir",
                                str(report),
                                "--format",
                                "json",
                            ],
                            env=cli_environment(),
                            capture_output=True,
                            timeout=130,
                            check=False,
                        )
                        outcome["recovery_status"] = (
                            "cleaned" if recovered.returncode == 0 else "failed"
                        )
                        record = rehearsal.read_recovery(report)
                        assert_absent(record.run_id)
                        outcome["absence_verified"] = True
                        assert recovered.returncode == 0, "recovery failed"
                    else:
                        outcome["recovery_status"] = "no_record"
            finally:
                try:
                    docker.deadline = time.monotonic() + 120
                    assert docker.daemon() == daemon, "sentinel daemon identity changed"
                    try:
                        if before is not None:
                            evidence["sentinel_preserved"] = (
                                sentinel.sentinel_snapshot(docker, identity) == before
                            )
                    finally:
                        sentinel.cleanup_sentinel(docker, identity)
                    evidence["sentinel_cleanup_verified"] = True
                finally:
                    safe_report.write_text(json.dumps(evidence, sort_keys=True) + "\n")
