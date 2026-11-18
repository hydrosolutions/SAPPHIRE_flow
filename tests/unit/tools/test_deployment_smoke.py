from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "smoke_tool", _ROOT / "tools/deployment_smoke.py"
)
assert _spec is not None and _spec.loader is not None
smoke = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = smoke
_spec.loader.exec_module(smoke)
_EXPECTED = json.loads(
    (_ROOT / "src/sapphire_flow/data/deployment_smoke/expected-result.json").read_text()
)
_CANARY = "DO_NOT_RETAIN_SMOKE_CREDENTIAL"


def _evidence() -> dict[str, Any]:
    return dict(
        station_id="station",
        artifact_id="artifact",
        forecast_id="forecast",
        parent_flow_run_id="parent",
        child_flow_run_id="child",
        health="degraded",
        stations_attempted=1,
        stations_succeeded=1,
        stations_failed=0,
        forecasts_stored=1,
        cycle_duration_ms=12.0,
        observations_passed=72,
        weather_records=3,
    )


def _payload() -> dict[str, Any]:
    return dict(
        id="forecast",
        station_id="station",
        model_artifact_id="artifact",
        model_id="deployment_smoke",
        model_tier="skill",
        parameter="discharge",
        representation="members",
        status="raw",
        qc_status="qc_passed",
        input_quality="degraded",
        warm_up_source="cold_start",
        observation_staleness_hours=1.0,
        nwp_cycle_source="primary",
        qc_flags=[],
        issued_at=_EXPECTED["issue_time"],
        nwp_cycle_reference_time=_EXPECTED["issue_time"],
        input_quality_flags=[
            dict(
                category="warm_up",
                level="degraded",
                detail="Cold start (no warm-up snapshot available)",
            )
        ],
        ensemble=dict(
            representation="members",
            parameter="discharge",
            units="m³/s",
            forecast_horizon_steps=3,
            time_step_seconds=3600,
            member_count=20,
            valid_times=[s["valid_time"] for s in _EXPECTED["forecast"]],
            series={
                str(m): [s["values"][i] for s in _EXPECTED["forecast"]]
                for i, m in enumerate(_EXPECTED["member_ids"])
            },
        ),
    )


class FakeDocker:
    def __init__(self, *, failure: str = "") -> None:
        self.deadline = time.monotonic() + 600
        self.failure = failure
        self.resources: dict[str, list[str]] = {
            "container": [],
            "volume": [],
            "network": [],
        }
        self.record: dict[str, Any] = {}
        self.calls: list[list[str]] = []
        self.probe_inputs: list[dict[str, Any]] = []

    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        self.calls.append(args)
        if args[:2] == ["context", "inspect"]:
            return json.dumps(
                [{"Endpoints": {"docker": {"Host": "unix:///local.sock"}}}]
            )
        if args[:2] == ["ps", "-aq"]:
            return "\n".join(self.resources["container"])
        if args[:2] in (["volume", "ls"], ["network", "ls"]):
            return "\n".join(self.resources[args[0]])
        if len(args) > 1 and args[1] == "inspect":
            labels = {
                "com.docker.compose.project": self.record["project"],
                "org.sapphire-flow.smoke.run": self.record["run_id"]
                if self.failure != "foreign"
                else "foreign",
            }
            return json.dumps([{"Config": {"Labels": labels}, "Labels": labels}])
        if len(args) > 1 and args[1] == "rm":
            if self.failure == "cleanup":
                raise smoke.SmokeError(_CANARY)
            self.resources[args[0]].remove(args[-1])
            return ""
        if "up" in args:
            self.resources = {
                "container": ["container-id"],
                "volume": ["volume-id"],
                "network": ["network-id"],
            }
            if self.failure == "setup":
                raise smoke.SmokeError(_CANARY)
            if self.failure == "interrupt":
                raise KeyboardInterrupt
            return ""
        if "logs" in args:
            return _CANARY + '\n{"smoke_stage":"observation_loader_qc"}\n'
        if "cat" in args:
            return "1024"
        if "du" in args:
            return "256 directory"
        if "sapphire_flow.cli.deployment_smoke" in args:
            command = args[args.index("sapphire_flow.cli.deployment_smoke") + 1]
            if command == "trigger":
                if self.failure == "worker":
                    raise smoke.SmokeError(_CANARY)
                return json.dumps({"parent_flow_run_id": "parent"})
            if command == "status":
                return json.dumps({"state": "COMPLETED"})
            if command == "evidence":
                return json.dumps(_evidence())
            if command == "token":
                assert args[args.index("-T") + 1] == "api"
                return json.dumps({"token": _CANARY})
            if command == "api-probe":
                assert args[args.index("-T") + 1] == "worker"
                assert stdin is not None
                self.probe_inputs.append(json.loads(stdin))
                return json.dumps(
                    {
                        "unauthorized_status": 200
                        if self.failure == "api_auth"
                        else 401,
                        "forecast_status": 403
                        if self.failure == "api_authorized"
                        else 200,
                        "forecast": _payload(),
                        "station_status": 200,
                        "station": {"no_floor": self.failure != "api_station"},
                    }
                )
        raise AssertionError(args)


def _prepare(monkeypatch: pytest.MonkeyPatch, docker: FakeDocker) -> None:
    def preflight(runner: object, image: str) -> dict[str, str]:
        return dict(
            endpoint="unix:///local.sock",
            image_id="sha256:fixed",
            release="0.1.1",
            revision="a" * 40,
        )

    monkeypatch.setattr(smoke, "preflight", preflight)
    original = smoke.create_run_directory

    def create(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
        record = original(path, identity)
        docker.record = record
        return record

    monkeypatch.setattr(smoke, "create_run_directory", create)


class TestSmokeController:
    @pytest.mark.parametrize(
        "failure, reason",
        [
            ("api_auth", "api_authentication_not_enforced"),
            ("api_authorized", "api_authorized_request_failed"),
            ("api_station", "api_station_scope_mismatch"),
        ],
    )
    def test_api_failure_retains_verified_worker_evidence_and_safe_reason(
        self,
        failure: str,
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docker = FakeDocker(failure=failure)
        _prepare(monkeypatch, docker)
        path = tmp_path / "report"
        assert smoke.run_smoke(docker, "prepared", path) == 1
        report = json.loads((path / "report.json").read_text())
        assert report["worker_evidence_verified"] is True
        assert report["forecast_verified"] is False
        assert report["reason"] == reason
        assert report["parent_flow_run_id"] == "parent"
        assert report["child_flow_run_id"] == "child"
        assert report["forecast_id"] == "forecast"
        assert report["observations_passed"] == 72
        assert report["cleanup_verified"] is True
        assert json.loads(capsys.readouterr().out)["reason"] == reason

    def test_unknown_exception_text_cannot_become_a_reason_code(self) -> None:
        error = smoke.SmokeError(_CANARY)
        assert str(error) == error.code == "unexpected_failure"

    def test_timed_out_image_probe_removes_only_its_owned_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name, identity = "", ""
        present = False
        removed: list[str] = []

        def execute(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal name, identity, present
            command = args[1:]
            if command[0] == "run":
                name = command[command.index("--name") + 1]
                identity = command[command.index("--label") + 1].split("=", 1)[1]
                present = True
                raise subprocess.TimeoutExpired(args, 1, stderr=_CANARY)
            if command[:2] == ["ps", "-aq"]:
                output = "owned-probe-id" if present else ""
            elif command[:2] == ["container", "inspect"]:
                output = json.dumps(
                    [
                        {
                            "Name": "/" + name,
                            "Config": {
                                "Labels": {"org.sapphire-flow.smoke.run": identity}
                            },
                        }
                    ]
                )
            elif command[:2] == ["container", "rm"]:
                removed.append(command[-1])
                present = False
                output = ""
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(args, 0, output, "")

        monkeypatch.setattr(smoke.subprocess, "run", execute)
        docker = smoke.Docker({}, time.monotonic() + 10)
        with pytest.raises(smoke.SmokeError, match="docker_unavailable_or_timeout"):
            docker.run(["run", "--rm", "--network", "none", "prepared"])
        assert removed == ["owned-probe-id"]
        assert docker.remaining_probes == []

    def test_success_requires_forecast_and_cleanup_and_retains_no_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docker = FakeDocker()
        _prepare(monkeypatch, docker)
        path = tmp_path / "report"
        assert smoke.run_smoke(docker, "prepared", path) == 0
        report = json.loads((path / "report.json").read_text())
        assert report["forecast_verified"] and report["cleanup_verified"]
        assert report["comparisons"] == 60
        assert report["host_port_verified"] is False
        assert report["api_probe"] == {"origin": "worker", "target": "http://api:8000"}
        assert docker.probe_inputs == [
            {"forecast_id": "forecast", "station_id": "station", "token": _CANARY}
        ]
        assert _CANARY not in json.dumps(docker.calls)
        assert not any("port" in args for args in docker.calls)
        assert report["resource_measurements"]["status"] == "measured"
        assert not (path / "secrets").exists()
        assert not any(docker.resources.values())
        assert (
            _CANARY not in (path / "report.json").read_text() + capsys.readouterr().out
        )

    @pytest.mark.parametrize("failure", ["setup", "worker", "interrupt"])
    def test_failures_and_interrupts_cleanup_without_copying_child_output(
        self,
        failure: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docker = FakeDocker(failure=failure)
        _prepare(monkeypatch, docker)
        path = tmp_path / "report"
        assert smoke.run_smoke(docker, "prepared", path) == 1
        report = json.loads((path / "report.json").read_text())
        assert not report["forecast_verified"] and report["cleanup_verified"]
        if failure == "setup":
            assert report["application_stage"] == "observation_loader_qc"
        assert not (path / "secrets").exists()
        assert (
            _CANARY not in (path / "report.json").read_text() + capsys.readouterr().out
        )

    def test_partial_secret_preparation_is_cleaned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        docker = FakeDocker()
        _prepare(monkeypatch, docker)

        def fail(path: Path) -> None:
            (path / "secrets").mkdir()
            (path / "secrets/db_password").write_text(_CANARY)
            raise OSError(_CANARY)

        monkeypatch.setattr(smoke, "create_secrets", fail)
        path = tmp_path / "report"
        assert smoke.run_smoke(docker, "prepared", path) == 1
        assert not (path / "secrets").exists()
        assert (path / "run.json").exists()
        assert _CANARY not in (path / "report.json").read_text()

    def test_cleanup_failure_is_nonzero_with_remaining_ids_and_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        docker = FakeDocker(failure="cleanup")
        _prepare(monkeypatch, docker)
        path = tmp_path / "report"
        assert smoke.run_smoke(docker, "prepared", path) == 1
        report = json.loads((path / "report.json").read_text())
        assert report["forecast_verified"] and not report["cleanup_verified"]
        assert report["remaining_resources"]["container"] == ["container-id"]
        assert report["cleanup_command"][-3:] == ["cleanup", "--report-dir", str(path)]
        assert not (path / "secrets").exists()

    def test_missing_image_refuses_before_creating_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*args: object) -> None:
            raise smoke.SmokeError(_CANARY)

        monkeypatch.setattr(smoke, "preflight", fail)
        path = tmp_path / "report"
        assert smoke.run_smoke(FakeDocker(), "missing", path) == 2
        assert not path.exists()

    def test_foreign_label_refuses_all_resource_removal(self, tmp_path: Path) -> None:
        docker = FakeDocker(failure="foreign")
        docker.record = {
            "project": "sapphire-smoke-" + "a" * 32,
            "run_id": "a" * 32,
            "endpoint": "unix:///local.sock",
        }
        docker.resources["container"] = ["foreign-id"]
        with pytest.raises(smoke.SmokeError, match="foreign_resource"):
            smoke.cleanup(docker, tmp_path, docker.record)
        assert not any(len(args) > 1 and args[1] == "rm" for args in docker.calls)

    def test_private_parent_protects_linux_readable_secret_mounts(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "report"
        smoke.create_run_directory(path, {"endpoint": "unix:///local.sock"})
        smoke.create_secrets(path)
        assert path.stat().st_mode & 0o777 == 0o700
        assert (path / "secrets").stat().st_mode & 0o777 == 0o700
        assert all(
            file.stat().st_mode & 0o777 == 0o444
            for file in (path / "secrets").iterdir()
        )
        smoke.erase_credentials(path)

    @pytest.mark.parametrize(
        "selector",
        ["COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "DOCKER_HOST", "DOCKER_CONTEXT"],
    )
    def test_ambient_selectors_refuse(self, selector: str) -> None:
        with pytest.raises(smoke.SmokeError, match="ambient"):
            smoke.isolated_environment({selector: "foreign"})

    def test_child_environment_omits_configuration_and_provider_credentials(
        self,
    ) -> None:
        assert smoke.isolated_environment(
            {
                "PATH": "/bin",
                "HOME": "/home/test",
                "DATABASE_URL": _CANARY,
                "RECAP_API_KEY": _CANARY,
                "SAPPHIRE_CONFIG": "private",
            }
        ) == {"PATH": "/bin", "HOME": "/home/test"}


class TestApiProof:
    @pytest.mark.parametrize(
        "mutation",
        [
            "value",
            "missing_member",
            "short",
            "time",
            "unit",
            "quality",
            "artifact",
            "nan",
        ],
    )
    def test_malformed_forecast_cannot_pass(self, mutation: str) -> None:
        payload = copy.deepcopy(_payload())
        ensemble = payload["ensemble"]
        if mutation == "value":
            ensemble["series"]["20"][2] += 0.01
        elif mutation == "missing_member":
            del ensemble["series"]["1"]
        elif mutation == "short":
            ensemble["series"]["1"].pop()
        elif mutation == "time":
            ensemble["valid_times"][0] = _EXPECTED["issue_time"]
        elif mutation == "unit":
            ensemble["units"] = "mm"
        elif mutation == "quality":
            payload["input_quality_flags"].append({"category": "nwp"})
        elif mutation == "artifact":
            payload["model_artifact_id"] = "other"
        else:
            ensemble["series"]["1"][0] = float("nan")
        with pytest.raises(smoke.SmokeError, match="api_"):
            smoke.verify_api(payload, _evidence(), _EXPECTED)


class TestSmokeCompose:
    def test_only_isolated_services_and_scoped_runtime_roles(self) -> None:
        config = yaml.safe_load((_ROOT / "docker-compose.smoke.yml").read_text())
        assert set(config["services"]) == {
            "postgres",
            "prefect-server",
            "init",
            "worker",
            "api",
        }
        assert config["networks"]["smoke"]["internal"] is True
        for service in config["services"].values():
            assert service["networks"] == ["smoke"]
            assert service["pull_policy"] == "never"
            assert "build" not in service and "container_name" not in service
            assert (
                service["labels"]["org.sapphire-flow.smoke.run"]
                == "${SMOKE_RUN_ID:?required}"
            )
            assert "ports" not in service
        assert config["services"]["postgres"]["platform"] == "linux/amd64"
        assert config["services"]["postgres"]["healthcheck"]["test"] == [
            "CMD",
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            "sapphire",
            "-d",
            "sapphire",
        ]
        assert config["services"]["worker"]["secrets"] == ["worker_password"]
        assert config["services"]["api"]["secrets"] == [
            "api_password",
            "access_token_pepper",
        ]
        assert (
            "sapphire_worker@"
            in config["services"]["worker"]["environment"]["DATABASE_URL_TEMPLATE"]
        )
        assert (
            "sapphire_api@"
            in config["services"]["api"]["environment"]["DATABASE_URL_TEMPLATE"]
        )
