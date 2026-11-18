from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "qualification_tool", _ROOT / "tools/qualify_deployment_smoke.py"
)
assert _spec is not None and _spec.loader is not None
qualify = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = qualify
_spec.loader.exec_module(qualify)
_CANARY = "SECRET_MUST_NOT_ENTER_PUBLIC_REPORT"


def passing_report() -> dict[str, Any]:
    return {
        "forecast_verified": True,
        "worker_evidence_verified": True,
        "cleanup_verified": True,
        "host_port_verified": False,
        "comparisons": 60,
        "observations_passed": 72,
        "weather_records": 3,
    }


class FakeDocker:
    def __init__(
        self, state: dict[str, Any], environment: dict[str, str], deadline: float
    ) -> None:
        self.state, self.environment, self.deadline = state, environment, deadline

    def run(self, args: list[str]) -> str:
        self.state["calls"].append(args)
        resources = self.state["resources"]
        if args[:2] == ["context", "inspect"]:
            return json.dumps(
                [{"Endpoints": {"docker": {"Host": "unix:///local.sock"}}}]
            )
        if args[:2] == ["image", "inspect"]:
            return json.dumps([{"Id": "sha256:" + "a" * 64}])
        if args[0] == "pull":
            assert self.deadline <= time.monotonic() + 90
            return ""
        if args[:2] == ["ps", "-aq"]:
            return "container-id" if "container" in resources else ""
        if args[1] == "ls":
            return args[0] + "-id" if args[0] in resources else ""
        if args[1] == "create":
            kind = args[0]
            name = args[args.index("--name") + 1] if kind == "container" else args[-1]
            identity = args[args.index("--label") + 1].split("=", 1)[1]
            record: dict[str, Any] = {
                "Name": name,
                "Id": kind + "-id",
                "Labels": {qualify._LABEL: identity},
            }
            if kind == "container":
                record.update(
                    Config={"Labels": record.pop("Labels")},
                    State={"Running": True, "StartedAt": "fixed"},
                    RestartCount=0,
                    Mounts=[{"Name": name}],
                    NetworkSettings={"Networks": {name: {"NetworkID": "network-id"}}},
                )
            if kind == "volume":
                record["CreatedAt"] = "fixed"
            resources[kind] = record
            self.state["identity"] = identity
            return kind + "-id"
        if args[1] == "inspect":
            return json.dumps([resources[args[0]]])
        if args[1] == "start":
            return ""
        if args[0] == "exec":
            if "write_text" in " ".join(args):
                self.state["content"] = args[-1]
                return ""
            return hashlib.sha256(self.state["content"].encode()).hexdigest()
        if args[1] == "rm":
            del resources[args[0]]
            return ""
        raise AssertionError(args)


def prepare(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"resources": {}, "calls": [], "runs": []}

    def docker(environment: dict[str, str], deadline: float) -> FakeDocker:
        return FakeDocker(state, environment, deadline)

    monkeypatch.setattr(qualify.smoke, "Docker", docker)
    return state


class TestQualification:
    def test_rejected_endpoint_never_reaches_resource_or_cleanup_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = prepare(monkeypatch)

        def refuse(docker: Any) -> str:
            raise qualify.smoke.SmokeError("remote_docker_refused")

        monkeypatch.setattr(qualify.smoke, "local_endpoint", refuse)
        assert (
            qualify.qualify("prepared", tmp_path / "qualification", "release-target")
            == 1
        )
        assert state["calls"] == []

    def test_failed_second_run_retains_evidence_and_erases_secrets_on_cleanup_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = prepare(monkeypatch)

        def run(docker: Any, image: str, path: Path, mode: str) -> int:
            path.mkdir()
            if path.name == "run-2":
                (path / "run.json").write_text("{}")
                (path / "secrets").mkdir()
                (path / "secrets/db_password").write_text(_CANARY)
                raise qualify.QualificationError("smoke_timeout")
            (path / "report.json").write_text(json.dumps(passing_report()))
            return 0

        def refuse(*args: Any) -> None:
            raise qualify.smoke.SmokeError("foreign_resource_refuses_cleanup")

        monkeypatch.setattr(qualify, "run_cli", run)

        def read_record(path: Path) -> dict[str, Any]:
            return {}

        monkeypatch.setattr(qualify.smoke, "read_record", read_record)
        monkeypatch.setattr(qualify.smoke, "cleanup", refuse)
        path = tmp_path / "qualification"
        assert qualify.qualify("prepared", path, "release-target") == 1
        report = (path / "qualification.json").read_text()
        assert json.loads(report)["runs"] == [passing_report()]
        assert json.loads(report)["sentinel_unchanged"] == [True, True]
        assert not (path / "run-2/secrets").exists()
        assert not state["resources"]
        assert _CANARY not in report

    def test_two_clean_runs_preserve_sentinel_and_public_report_is_curated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        state = prepare(monkeypatch)

        def run(docker: Any, image: str, path: Path, mode: str) -> int:
            assert mode == "ci-development"
            assert not path.exists()
            state["runs"].append(path.name)
            path.mkdir()
            (path / "report.json").write_text(
                json.dumps(
                    {
                        **passing_report(),
                        "token": _CANARY,
                        "endpoint": _CANARY,
                        "logs": _CANARY,
                    }
                )
            )
            return 0

        monkeypatch.setattr(qualify, "run_cli", run)
        path = tmp_path / "qualification"
        assert qualify.qualify("prepared", path, "ci-development") == 0
        summary = json.loads((path / "qualification.json").read_text())
        assert summary["qualified"] is True
        assert state["runs"] == ["run-1", "run-2"]
        assert summary["sentinel_unchanged"] == [True, True]
        assert summary["sentinel_cleanup_verified"] is True
        assert not state["resources"]
        assert _CANARY not in json.dumps(summary) + capsys.readouterr().out
        assert not any(args[0] == "run" for args in state["calls"])

    @pytest.mark.parametrize(
        "failure", ["content", "restart", "network", "volume", "child", "interrupt"]
    )
    def test_mutation_or_failed_run_cannot_qualify_and_owned_sentinel_is_removed(
        self, failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = prepare(monkeypatch)

        def run(docker: Any, image: str, path: Path, mode: str) -> int:
            path.mkdir()
            (path / "report.json").write_text(json.dumps(passing_report()))
            if failure == "content":
                state["content"] = "changed"
            if failure == "restart":
                state["resources"]["container"]["State"]["StartedAt"] = "new"
            if failure == "network":
                state["resources"]["network"]["Id"] = "replacement"
            if failure == "volume":
                state["resources"]["volume"]["CreatedAt"] = "new"
            if failure == "interrupt":
                raise KeyboardInterrupt
            return 1 if failure == "child" else 0

        monkeypatch.setattr(qualify, "run_cli", run)
        path = tmp_path / "qualification"
        assert qualify.qualify("prepared", path, "release-target") == 1
        summary = json.loads((path / "qualification.json").read_text())
        assert not summary["qualified"]
        assert summary["sentinel_cleanup_verified"] is True
        assert not state["resources"]

    def test_foreign_resource_refuses_teardown_before_any_removal(self) -> None:
        state: dict[str, Any] = {"resources": {}, "calls": []}
        docker = FakeDocker(state, {}, time.monotonic() + 100)
        qualify.create_sentinel(docker, "a" * 32, "prepared")
        state["resources"]["volume"]["Labels"][qualify._LABEL] = "foreign"
        with pytest.raises(
            qualify.QualificationError, match="sentinel_ownership_mismatch"
        ):
            qualify.cleanup_sentinel(docker, "a" * 32)
        assert len(state["resources"]) == 3
        assert not any(args[1] == "rm" for args in state["calls"])

    def test_cli_watchdog_terminates_owned_process_and_uses_reduced_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args_seen: list[str] = []
        signals: list[tuple[int, int]] = []

        class Process:
            pid = 1234
            calls = 0

            def wait(self, timeout: float) -> int:
                self.calls += 1
                assert timeout <= 390
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("synthetic", timeout)
                return 1

        def start(args: list[str], **kwargs: Any) -> Process:
            args_seen.extend(args)
            assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
            assert kwargs["start_new_session"] is True
            return Process()

        monkeypatch.setattr(qualify.subprocess, "Popen", start)

        def killpg(pid: int, signum: int) -> None:
            signals.append((pid, signum))

        monkeypatch.setattr(qualify.os, "killpg", killpg)
        docker = FakeDocker({}, {}, time.monotonic() + 1000)
        with pytest.raises(qualify.QualificationError, match="smoke_timeout"):
            qualify.run_cli(docker, "prepared", tmp_path, "ci-development")
        assert args_seen[-2:] == ["--runtime-timeout-seconds", "240"]
        assert signals == [(1234, qualify.signal.SIGTERM)]


class TestCiContract:
    def test_admin_guide_uses_pinned_images_and_dependency_free_host_commands(
        self,
    ) -> None:
        guide = (_ROOT / "docs/deployment/hello-world.md").read_text()
        assert qualify.smoke.POSTGRES_IMAGE in guide
        assert qualify.smoke.PREFECT_IMAGE in guide
        commands = [
            line
            for line in guide.splitlines()
            if line.startswith("uv run") and "tools/" in line
        ]
        assert commands
        assert all(
            "--no-project --no-python-downloads" in command for command in commands
        )

    @pytest.mark.parametrize("mode", ["release-target", "ci-development"])
    def test_image_identity_is_derived_from_checkout_not_ambient_override(
        self, mode: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        smoke = qualify.smoke
        manifest_bytes = (smoke._DATA / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        checked = {
            "resources": manifest["sha256"],
            "code": smoke.source_hashes(),
            "manifest": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        calls: list[tuple[str, str]] = []

        class Docker:
            def run(self, args: list[str], **kwargs: Any) -> str:
                if args[:2] == ["compose", "version"]:
                    return '{"version":"2.0"}'
                if args[:2] == ["image", "inspect"]:
                    return '[{"Os":"linux","Architecture":"amd64"}]'
                return json.dumps(checked)

        def endpoint(docker: Any) -> str:
            return "unix:///local.sock"

        def git(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, "b" * 40)

        def probe(
            image: str, release: str, revision: str, **kwargs: Any
        ) -> SimpleNamespace:
            calls.append((release, revision))
            return SimpleNamespace(image_id="sha256:" + "a" * 64)

        monkeypatch.setenv("EXPECTED_VERSION", _CANARY)
        monkeypatch.setattr(smoke, "local_endpoint", endpoint)
        monkeypatch.setattr(smoke.subprocess, "run", git)
        monkeypatch.setattr(smoke._probe, "probe_image", probe)
        if mode == "release-target":
            smoke.preflight(Docker(), "prepared")
            expected = (smoke._ROOT / "RELEASE_TARGET").read_text().strip()
        else:
            smoke.preflight(Docker(), "prepared", image_identity="ci-development")
            expected = "0.dev0+g" + "b" * 40
        assert calls == [(expected, "b" * 40)]

    @pytest.mark.parametrize(
        "argument",
        [["--runtime-timeout-seconds", "601"], ["--image-identity", "arbitrary"]],
    )
    def test_admin_cli_refuses_longer_timeout_or_arbitrary_identity(
        self, argument: list[str]
    ) -> None:
        with pytest.raises(SystemExit, match="2"):
            qualify.smoke.main(
                ["run", "--image", "prepared", "--report-dir", "/unused", *argument]
            )

    def test_named_mandatory_gate_uses_local_image_and_uploads_only_curated_file(
        self,
    ) -> None:
        config = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
        job = config["jobs"]["build-image-and-scan"]
        assert job["timeout-minutes"] == 80
        step = next(
            s for s in job["steps"] if s.get("name") == "Qualify deployment smoke twice"
        )
        assert step["timeout-minutes"] == 20
        assert "--image sapphire-flow:ci-${{ github.sha }}" in step["run"]
        assert "--image-identity ci-development" in step["run"]
        assert "continue-on-error" not in step
        upload = next(
            s
            for s in job["steps"]
            if s.get("name") == "Upload deployment smoke qualification"
        )
        assert (
            upload["with"]["path"]
            == "${{ runner.temp }}/deployment-smoke/qualification.json"
        )

    @pytest.mark.parametrize(
        "tool", ["deployment_smoke.py", "qualify_deployment_smoke.py"]
    )
    def test_real_cli_help_documents_identity_and_timeout_choices(
        self, tool: str
    ) -> None:
        result = subprocess.run(
            [sys.executable, str(_ROOT / "tools" / tool), "run", "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "release-target,ci-development" in result.stdout
        if tool == "deployment_smoke.py":
            assert "--runtime-timeout-seconds 1..600" in result.stdout
