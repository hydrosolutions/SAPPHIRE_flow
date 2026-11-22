from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sapphire_flow.config.deployment_profile import (
    EvaluationSummary,
    ProfileEvaluation,
    ProfileState,
    evaluate_deployment_profile,
    select_deployment_profile,
)
from tests.unit.config.profile_support import (
    incomplete_profile,
    materialize_offline_fixture,
    recipe_profile,
)

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "deployment_profile_tool", ROOT / "tools/deployment_profile.py"
)
assert spec is not None and spec.loader is not None
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def installation(
    tmp_path: Path, agency: str = "macmini", extra: str = "", environment: str = ""
) -> Path:
    selected = select_deployment_profile(
        profile_file=recipe_profile(tmp_path, agency), ambient_environment={}
    )
    path = tmp_path / "installation.toml"
    files = [str(path) for path in selected.compose_files]
    path.write_text(
        f'schema_version = 1\nagency_profile = "{selected.profile.manifest_path}"\n'
        f'project_name = "profile-test"\nproject_directory = "{ROOT}"\n'
        f"compose_files = {json.dumps(files)}\n{extra}\n[environment]\n"
        'VERSION = "1.2.3"\nSAPPHIRE_RELEASE_VERSION = "1.2.3"\n'
        'SAPPHIRE_SOURCE_REVISION = "'
        + "0" * 40
        + f'"\nSAPPHIRE_DOMAIN = "example.invalid"\n{environment}\n[secret_bindings]\n'
        + "".join(
            f'{item.name} = "{ROOT / "secrets" / item.name}"\n'
            for item in selected.profile.secret_requirements
            if item.use.value == "runtime" and item.when_compose is None
        )
    )
    return path


@pytest.fixture
def compose_render(tmp_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    selected = select_deployment_profile(
        profile_file=recipe_profile(tmp_path),
        installation_file=installation(tmp_path),
        ambient_environment={},
    )
    # Wiring-only evidence: this fixture does not certify legacy application inputs.
    evaluated = ProfileEvaluation(
        summary=EvaluationSummary.PENDING,
        diagnostics=(),
        provenance=(),
        pending_checks=(
            tool.diagnostic(
                "COMPOSE_EVIDENCE_PENDING",
                "compose",
                "Wiring not checked",
                "Render explicit recipe",
            ),
            tool.diagnostic(
                "ADAPTER_POLICY_PENDING",
                "adapters",
                "Policy not assessed",
                "Developer review required",
            ),
        ),
        unverified_runtime=("actual_host_configuration",),
    )
    try:
        rendered = tool.render_compose(selected)
    except tool.ProfileError as error:
        if error.code == "COMPOSE_UNAVAILABLE" and not os.environ.get("CI"):
            pytest.skip("Compose is unavailable under isolated configuration")
        raise
    return selected, evaluated, rendered


class TestCommand:
    @pytest.mark.parametrize(
        ("arguments", "expected"),
        [
            (
                [
                    "--profile-file",
                    str(ROOT / "deploy/examples/profile.example.toml"),
                    "show",
                ],
                0,
            ),
            (
                [
                    "--profile-file",
                    str(ROOT / "deploy/examples/profile.example.toml"),
                    "check",
                ],
                1,
            ),
        ],
    )
    def test_documented_forms(
        self, arguments: list[str], expected: int, tmp_path: Path
    ) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/deployment_profile.py"),
                *arguments,
                "--format",
                "json",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            env={
                **{
                    key: os.environ[key]
                    for key in ("PATH", "PYTHONPATH")
                    if key in os.environ
                },
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        assert completed.returncode == expected, completed.stderr
        assert json.loads(completed.stdout)["schema_version"] == 1
        assert completed.stderr == ""

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--canary-secret", "canary-secret"],
            ["--profile-file", "/canary-secret", "check"],
            ["canary-secret", "show"],
            ["--profile-file", "/canary-secret", "show", "--format", "canary-secret"],
        ],
    )
    def test_errors_are_safe_json(
        self, arguments: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert tool.main([*arguments, "--format", "json"]) == 1
        output = capsys.readouterr()
        assert "canary-secret" not in output.out + output.err
        assert json.loads(output.out)["summary"] == "FAIL"

    def test_exit_precedence_for_fully_evidenced_result(self) -> None:
        # This tests the exit contract, not an end-to-end validation exemption.
        evaluated = ProfileEvaluation(
            summary=EvaluationSummary.PASS_OFFLINE,
            diagnostics=(),
            provenance=(),
            pending_checks=(),
            unverified_runtime=("host",),
        )
        assert tool.exit_code("check", evaluated, ProfileState.CONFIGURED) == 0
        assert (
            tool.exit_code(
                "check",
                replace(evaluated, summary=EvaluationSummary.PENDING),
                ProfileState.CONFIGURED,
            )
            == 2
        )
        assert (
            tool.exit_code(
                "check",
                replace(evaluated, summary=EvaluationSummary.FAIL),
                ProfileState.CONFIGURED,
            )
            == 1
        )


class TestComposeWiring:
    def test_real_render_checks_wiring_and_keeps_policy_pending(
        self, compose_render: tuple[Any, Any, dict[str, Any]]
    ) -> None:
        selected, evaluated, rendered = compose_render
        checked = tool.check_wiring(selected, evaluated, rendered)
        assert checked.diagnostics == ()
        assert checked.summary == EvaluationSummary.PENDING
        codes = {item.code for item in checked.pending_checks}
        assert "COMPOSE_EVIDENCE_PENDING" not in codes
        assert "BAFU_REGISTRY_PENDING" not in codes
        assert "ADAPTER_POLICY_PENDING" in codes

    @pytest.mark.parametrize(
        ("mutation", "code"),
        [
            ("overlay", "CONFIG_WIRING_INVALID"),
            ("mount", "CONFIG_WIRING_INVALID"),
            ("base_env", "CONFIG_WIRING_INVALID"),
            ("grant", "SECRET_GRANTS_INVALID"),
            ("secret_target", "SECRET_TARGET_INVALID"),
            ("secret_env", "SECRET_INPUT_INVALID"),
            ("pool", "WORKER_POOL_INVALID"),
        ],
    )
    def test_inconsistent_service_fails(
        self, compose_render: tuple[Any, Any, dict[str, Any]], mutation: str, code: str
    ) -> None:
        selected, evaluated, rendered = compose_render
        changed = copy.deepcopy(rendered)
        worker = changed["services"]["prefect-worker"]
        if mutation == "overlay":
            worker["environment"]["SAPPHIRE_CONFIG_OVERLAY"] = "/app/other.toml"
        elif mutation == "mount":
            worker["volumes"] = []
        elif mutation == "base_env":
            del worker["environment"]["SAPPHIRE_CONFIG"]
        elif mutation == "grant":
            worker["secrets"].append({"source": "db_password"})
        elif mutation == "secret_target":
            worker["secrets"][0]["target"] = "different"
        elif mutation == "secret_env":
            worker["environment"]["DB_PASSWORD_SECRET"] = "/run/secrets/absent"
        else:
            worker["command"] = ["prefect", "worker", "start", "--pool", "wrong"]
        assert code in {
            item.code
            for item in tool.check_wiring(selected, evaluated, changed).diagnostics
        }

    def test_unforwarded_schedule_fails(
        self, compose_render: tuple[Any, Any, dict[str, Any]]
    ) -> None:
        selected, evaluated, rendered = compose_render
        selected = replace(
            selected,
            installation=replace(
                selected.installation,
                environment=(
                    *selected.installation.environment,
                    ("SCHEDULE_UNKNOWN", "0 * * * *"),
                ),
            ),
        )
        assert "SCHEDULE_FORWARDING_INVALID" in {
            item.code
            for item in tool.check_wiring(selected, evaluated, rendered).diagnostics
        }

    @pytest.mark.parametrize("provider", ["local", "proton", "barbican"])
    def test_delivery_files_are_never_read(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: str,
    ) -> None:
        selected, evaluated, rendered = compose_render
        secret = tmp_path / provider
        secret.write_text("credential-canary")
        bindings = tuple(
            (name, secret) for name, _ in selected.installation.secret_bindings
        )
        selected = replace(
            selected,
            installation=replace(selected.installation, secret_bindings=bindings),
        )
        rendered = copy.deepcopy(rendered)
        for name, _ in bindings:
            rendered["secrets"][name]["file"] = str(secret)
        original = Path.read_bytes

        def guarded(path: Path) -> bytes:
            if path == secret:
                pytest.fail("secret contents read")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", guarded)
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda *args, **kwargs: pytest.fail("unexpected text read"),
        )
        checked = tool.check_wiring(selected, evaluated, rendered)
        assert checked.diagnostics == ()
        assert "credential-canary" not in repr(checked)

    def test_nepal_and_installation_cli(self, tmp_path: Path) -> None:
        path = installation(tmp_path, agency="nepal-cloud")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/deployment_profile.py"),
                "--profile-file",
                str(recipe_profile(tmp_path, "nepal-cloud")),
                "check",
                "--installation",
                str(path),
                "--format",
                "json",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            env={
                **{
                    key: os.environ[key]
                    for key in ("PATH", "PYTHONPATH")
                    if key in os.environ
                },
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        assert completed.returncode == 1, completed.stderr + completed.stdout
        report = json.loads(completed.stdout)
        assert all(item["code"] == "INPUT_REQUIRED" for item in report["diagnostics"])
        assert "COMPOSE_EVIDENCE_PENDING" in {
            item["code"] for item in report["pending_checks"]
        }

    @pytest.mark.parametrize(
        ("name", "value", "code"),
        [
            ("SCHEDULE_FORECAST_CYCLE", "credential-canary", "REGISTRAR_INPUT_INVALID"),
            (
                "SCHEDULE_COLLECT_BAFU_FORECASTS",
                "0 * * * *",
                "SCHEDULE_FORWARDING_INVALID",
            ),
        ],
    )
    def test_schedule_validation(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        name: str,
        value: str,
        code: str,
    ) -> None:
        selected, evaluated, rendered = compose_render
        rendered = copy.deepcopy(rendered)
        selected = replace(
            selected,
            installation=replace(
                selected.installation,
                environment=(*selected.installation.environment, (name, value)),
            ),
        )
        if code == "REGISTRAR_INPUT_INVALID":
            rendered["services"]["init"]["environment"][name] = value
        checked = tool.check_wiring(selected, evaluated, rendered)
        assert code in {item.code for item in checked.diagnostics}
        assert "credential-canary" not in repr(checked)

    def test_optional_scope_mutations(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.syspath_prepend(str(ROOT / "tools"))
        selected, evaluated, _ = compose_render
        provider = tmp_path / "provider.toml"
        provider.write_text("credential-canary")
        files = (
            *selected.compose_files,
            ROOT / "docker-compose.operator.yml",
            ROOT / "docker-compose.nhs-forecast.yml",
        )
        selected = replace(
            selected,
            compose_files=files,
            installation=replace(
                selected.installation,
                compose_files=files,
                environment=(
                    *selected.installation.environment,
                    ("NHS_PROVIDER_CONFIG_SOURCE", str(provider)),
                    (
                        "NHS_FORECAST_JOB_OVERLAYS",
                        "/app/config/overlays/mac-mini.toml,/app/config/overlays/nhs-provider.toml",
                    ),
                ),
            ),
        )
        rendered = tool.render_compose(selected)
        baseline = tool.check_wiring(selected, evaluated, rendered)
        assert not {
            "OPERATOR_SCOPE_INVALID",
            "OPERATOR_METADATA_INVALID",
            "FORECAST_SCOPE_INVALID",
        } & {item.code for item in baseline.diagnostics}
        changed = copy.deepcopy(rendered)
        changed["services"]["operator"]["environment"]["SAPPHIRE_CONFIG_OVERLAY"] = (
            "/app/config/overlays/mac-mini.toml"
        )
        changed["services"]["operator"]["volumes"] = []
        changed["services"]["init"]["environment"][
            "SAPPHIRE_FORECAST_CONFIG_OVERLAY"
        ] = "/app/config/overlays/nhs-provider.toml"
        codes = {
            item.code
            for item in tool.check_wiring(selected, evaluated, changed).diagnostics
        }
        assert {
            "OPERATOR_SCOPE_INVALID",
            "OPERATOR_METADATA_INVALID",
            "FORECAST_SCOPE_INVALID",
        } <= codes

    @pytest.mark.parametrize("existing", [False, True])
    def test_service_env_file_is_not_read(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        existing: bool,
    ) -> None:
        selected, evaluated, _ = compose_render
        delivery = tmp_path / "private.env"
        if existing:
            delivery.write_text("TOKEN=credential-canary\n")
        extra = tmp_path / "env.yml"
        extra.write_text(f"services:\n  api:\n    env_file: {delivery}\n")
        selected = replace(selected, compose_files=(*selected.compose_files, extra))
        original = Path.read_text

        def guarded(path: Path, *args: Any, **kwargs: Any) -> str:
            if path == delivery:
                pytest.fail("env_file contents read")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded)
        monkeypatch.setattr(
            tool,
            "_capture",
            lambda *args: pytest.fail("Compose invoked for env_file recipe"),
        )
        checked = tool.check_profile(selected, evaluated)
        codes = {item.code for item in checked.pending_checks}
        assert {"ENV_FILES_UNCHECKED", "COMPOSE_EVIDENCE_PENDING"} <= codes
        assert checked.summary == EvaluationSummary.PENDING
        assert "credential-canary" not in repr(checked)

    def test_capture_errors_never_return_output(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="COMPOSE_RENDER_FAILED") as error:
            tool._capture(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('credential-canary'); "
                    "print('credential-canary', file=sys.stderr); sys.exit(1)",
                ],
                {"PYTHONDONTWRITEBYTECODE": "1"},
                tmp_path,
            )
        assert "credential-canary" not in str(error.value)

    def test_capture_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tool, "LIMIT", 8)
        with pytest.raises(ValueError, match="COMPOSE_RENDER_LIMIT"):
            tool._capture(
                [sys.executable, "-c", "print('x' * 100)"],
                {"PYTHONDONTWRITEBYTECODE": "1"},
                tmp_path,
            )

    def test_other_checkout_is_pending(
        self, compose_render: tuple[Any, Any, dict[str, Any]], tmp_path: Path
    ) -> None:
        selected, evaluated, rendered = compose_render
        selected = replace(
            selected,
            installation=replace(selected.installation, project_directory=tmp_path),
        )
        assert "CHECKOUT_CONFIG_UNVERIFIED" in {
            item.code
            for item in tool.check_wiring(selected, evaluated, rendered).pending_checks
        }

    @pytest.mark.parametrize(
        ("code", "summary"),
        [
            ("COMPOSE_INPUT_INVALID", EvaluationSummary.FAIL),
            ("COMPOSE_RENDER_FAILED", EvaluationSummary.PENDING),
        ],
    )
    def test_invalid_recipe_differs_from_missing_evidence(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        code: str,
        summary: EvaluationSummary,
    ) -> None:
        selected, evaluated, _ = compose_render

        def unavailable(selection: Any) -> dict[str, Any]:
            raise tool.ProfileError(code, "credential-canary")

        monkeypatch.setattr(tool, "render_compose", unavailable)
        checked = tool.check_profile(selected, evaluated)
        assert checked.summary == summary
        assert "credential-canary" not in repr(checked)

    def test_incomplete_show_does_not_hide_malformed_toml(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        base = tmp_path / "base.toml"
        base.write_text("[malformed")
        profile = tmp_path / "agency.toml"
        profile.write_text(
            'schema_version = 1\nname = "example"\nstate = "incomplete"\n'
            'base_config = "base.toml"\ncompose_files = []\n'
            'required_inputs = ["agency choices"]\n'
        )
        assert (
            tool.main(["--profile-file", str(profile), "show", "--format", "json"]) == 1
        )
        assert "CONFIG_LOAD_INVALID" in capsys.readouterr().out

    def test_show_is_read_only_without_network(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import socket

        profile = incomplete_profile(tmp_path)
        before = set(tmp_path.iterdir())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *args, **kwargs: pytest.fail("network access"),
        )
        monkeypatch.setattr(
            Path, "write_text", lambda *args, **kwargs: pytest.fail("file write")
        )
        monkeypatch.setattr(
            Path, "write_bytes", lambda *args, **kwargs: pytest.fail("file write")
        )
        assert (
            tool.main(["--profile-file", str(profile), "show", "--format", "json"]) == 0
        )
        assert json.loads(capsys.readouterr().out)["summary"] == "FAIL"
        assert set(tmp_path.iterdir()) == before

    def test_capture_reaps_after_eof_wait_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        events: list[str] = []
        process = MagicMock()
        process.__enter__.return_value = process

        def wait(timeout: float | None = None) -> int:
            if timeout is not None:
                events.append("bounded_wait")
                assert timeout == 20
                raise subprocess.TimeoutExpired("synthetic", timeout)
            events.append("reap")
            return -9

        def exit_process(*args: object) -> None:
            # Popen.__exit__ may wait: the child must already be killed/reaped.
            assert events == ["bounded_wait", "kill", "reap"]
            events.append("exit")

        process.wait.side_effect = wait
        process.kill.side_effect = lambda: events.append("kill")
        process.__exit__.side_effect = exit_process
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.get_map.side_effect = [{1: object(), 2: object()}, {}]
        selector.select.return_value = [
            (SimpleNamespace(fd=1, fileobj=process.stdout, data=True), 1),
            (SimpleNamespace(fd=2, fileobj=process.stderr, data=False), 1),
        ]
        monkeypatch.setattr(tool.subprocess, "Popen", lambda *args, **kwargs: process)
        monkeypatch.setattr(tool.selectors, "DefaultSelector", lambda: selector)
        monkeypatch.setattr(tool.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(tool.os, "read", lambda *args: b"")

        with pytest.raises(ValueError, match="COMPOSE_RENDER_LIMIT"):
            tool._capture(["synthetic"], {}, tmp_path)

        assert selector.unregister.call_count == 2
        assert events == ["bounded_wait", "kill", "reap", "exit"]

    def test_actionable_ambient_conflict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("COMPOSE_FILE", "/credential-canary.yml")
        assert (
            tool.main(
                [
                    "--profile-file",
                    str(recipe_profile(tmp_path)),
                    "show",
                    "--format",
                    "json",
                ]
            )
            == 1
        )
        output = capsys.readouterr().out
        item = json.loads(output)["diagnostics"][0]
        assert item["field"] == "COMPOSE_FILE"
        assert "Unset" in item["next_action"]
        assert "credential-canary" not in output

    def test_actionable_binding_and_selector_conflicts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = installation(tmp_path)
        path.write_text(path.read_text() + 'unknown_canary = "/credential-canary"\n')
        assert (
            tool.main(
                [
                    "--profile-file",
                    str(recipe_profile(tmp_path)),
                    "show",
                    "--installation",
                    str(path),
                    "--format",
                    "json",
                ]
            )
            == 1
        )
        output = capsys.readouterr().out
        item = json.loads(output)["diagnostics"][0]
        assert item["field"] == "secret_bindings"
        assert "runtime" in item["next_action"]
        assert "canary" not in output
        path = installation(tmp_path)
        assert (
            tool.main(
                [
                    "--profile-file",
                    str(recipe_profile(tmp_path, "nepal-cloud")),
                    "show",
                    "--installation",
                    str(path),
                    "--format",
                    "json",
                ]
            )
            == 1
        )
        item = json.loads(capsys.readouterr().out)["diagnostics"][0]
        assert item["field"] == "agency_profile"
        assert "same" in item["next_action"]

    def test_text_lists_runtime_limitations_and_input_owners(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            tool.main(["--profile-file", str(incomplete_profile(tmp_path)), "show"])
            == 0
        )
        output = capsys.readouterr().out
        assert "owner=agency_specialist" in output
        assert "models_and_artifacts" in output
        assert "hosting_paths_domain_ports" in output
        assert "default=fallback" in output
        assert "Unverified runtime:" in output
        assert "registered_jobs_and_pools" in output
        assert "build_credentials" in output

    @pytest.mark.parametrize(
        "content",
        [
            'weather_hot_days = "credential-canary"\n',
            '[adapters.river_stations]\ntype = "credential-canary"\n',
            'unknown_key = "https://user:credential-canary@example.invalid"\n',
        ],
    )
    def test_whole_private_command_redacts_values(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
    ) -> None:
        (tmp_path / "base.toml").write_text(content)
        profile = tmp_path / "profile.toml"
        profile.write_text(
            'schema_version = 1\nname = "private-canary"\nstate = "incomplete"\n'
            'base_config = "base.toml"\ncompose_files = []\n'
            'required_inputs = ["private-canary"]\n'
        )
        status = tool.main(["--profile-file", str(profile), "show", "--format", "json"])
        output = capsys.readouterr().out
        assert "canary" not in output
        if "weather_hot_days" in content or "river_stations" in content:
            assert status == 1
        if "river_stations" in content:
            assert "DEVELOPER_WORK_REQUIRED" in output
        assert json.loads(output)["schema_version"] == 1

    def test_symlink_delivery_binding_is_not_a_mismatch(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        selected, evaluated, rendered = compose_render
        target = tmp_path / "manager-delivery"
        target.write_text("credential-canary")
        link = tmp_path / "secret-link"
        link.symlink_to(target)
        selected = replace(
            selected,
            installation=replace(
                selected.installation,
                secret_bindings=tuple(
                    (name, target.resolve())
                    for name, _ in selected.installation.secret_bindings
                ),
            ),
        )
        rendered = copy.deepcopy(rendered)
        for name, _ in selected.installation.secret_bindings:
            rendered["secrets"][name]["file"] = str(link)
        monkeypatch.setattr(
            Path, "read_bytes", lambda *args, **kwargs: pytest.fail("secret read")
        )
        monkeypatch.setattr(
            Path, "read_text", lambda *args, **kwargs: pytest.fail("secret read")
        )
        checked = tool.check_wiring(selected, evaluated, rendered)
        assert "SECRET_BINDING_INVALID" not in {
            item.code for item in checked.diagnostics
        }

    def test_cron_redaction_is_per_job_and_bafu_limitation_visible(
        self, compose_render: tuple[Any, Any, dict[str, Any]]
    ) -> None:
        selected, evaluated, rendered = compose_render
        rendered = copy.deepcopy(rendered)
        rendered["services"]["init"]["environment"]["SCHEDULE_FORECAST_CYCLE"] = (
            "7 * * * *"
        )
        settings = {
            item.field: item
            for item in tool.check_wiring(selected, evaluated, rendered).provenance
        }
        assert settings["jobs.forecast-cycle.schedule"].visibility == "redacted"
        assert settings["jobs.forecast-cycle.schedule"].default_status == "explicit"
        assert settings["jobs.backup-database.schedule"].visibility == "value"
        assert settings["jobs.backup-database.schedule"].default_status == "review"
        assert (
            settings["jobs.collect-bafu-forecasts.forwarding"].value
            == "not_forwarded_using_registrar_default"
        )

    def test_compose_capability_is_distinct_from_render_failure(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        selected, evaluated, _ = compose_render
        monkeypatch.setattr(tool, "_capture", lambda *args: b"older config help")
        checked = tool.check_profile(selected, evaluated)
        assert "COMPOSE_CAPABILITY_MISSING" in {
            item.code for item in checked.pending_checks
        }

    @pytest.mark.parametrize("section", ["environment", "secret_bindings"])
    def test_malformed_installation_values_are_sanitized(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], section: str
    ) -> None:
        path = installation(tmp_path)
        content = path.read_text()
        key = "SAPPHIRE_DOMAIN" if section == "environment" else "db_password"
        lines = content.splitlines()
        lines = [
            f'{key} = {{ value = "credential-canary" }}'
            if line.startswith(f"{key} =")
            else line
            for line in lines
        ]
        path.write_text("\n".join(lines))
        assert (
            tool.main(
                [
                    "--profile-file",
                    str(recipe_profile(tmp_path)),
                    "show",
                    "--installation",
                    str(path),
                    "--format",
                    "json",
                ]
            )
            == 1
        )
        output = capsys.readouterr().out
        assert "credential-canary" not in output
        assert "INSTALLATION_SCHEMA_INVALID" in output

    def test_matching_checkout_contents_establish_config_parity(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
    ) -> None:
        selected, evaluated, rendered = compose_render
        for path in (selected.profile.base_config, *selected.profile.overlays):
            destination = tmp_path / path.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
        selected = replace(
            selected,
            installation=replace(selected.installation, project_directory=tmp_path),
        )
        rendered = copy.deepcopy(rendered)
        for service in rendered["services"].values():
            for mount in service.get("volumes", []):
                source = mount.get("source", "")
                if source.startswith(str(ROOT) + "/"):
                    mount["source"] = str(tmp_path / Path(source).relative_to(ROOT))
        checked = tool.check_wiring(selected, evaluated, rendered)
        assert "CONFIG_WIRING_INVALID" not in {
            item.code for item in checked.diagnostics
        }
        assert "CHECKOUT_CONFIG_UNVERIFIED" not in {
            item.code for item in checked.pending_checks
        }

    def test_empty_incomplete_requirements_still_fail(self, tmp_path: Path) -> None:
        selected = select_deployment_profile(
            profile_file=incomplete_profile(tmp_path), ambient_environment={}
        )
        selected = replace(
            selected, profile=replace(selected.profile, required_inputs=())
        )
        evaluated = evaluate_deployment_profile(selected)
        assert any(
            item.code == "INPUT_REQUIRED" and item.field == "agency_inputs"
            for item in evaluated.diagnostics
        )

    @pytest.mark.parametrize(
        "path_candidate", ["missing", "broken", "old", "generic_help"]
    )
    def test_system_compose_plugin_fallback_preserves_isolation(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        path_candidate: str,
    ) -> None:
        selected, _, rendered = compose_render
        system = "/usr/libexec/docker/cli-plugins/docker-compose"
        commands: list[list[str]] = []

        def which(name: str, *, path: str | None = None) -> str | None:
            assert name == "docker-compose"
            if path is None:
                return (
                    None if path_candidate == "missing" else "/synthetic/docker-compose"
                )
            assert path in {
                "/usr/local/lib/docker/cli-plugins",
                "/usr/local/libexec/docker/cli-plugins",
                "/usr/lib/docker/cli-plugins",
                "/usr/libexec/docker/cli-plugins",
            }
            return system if path == "/usr/libexec/docker/cli-plugins" else None

        def capture(
            command: list[str], environment: dict[str, str], directory: Path
        ) -> bytes:
            commands.append(command)
            assert environment["HOME"] == "/dev/null"
            assert environment["DOCKER_CONFIG"] == "/dev/null"
            if command[0] == "/synthetic/docker-compose":
                assert command[1:] == ["config", "--help"]
                if path_candidate == "broken":
                    raise tool.ProfileError(
                        "COMPOSE_RENDER_FAILED", "synthetic failure"
                    )
                return (
                    b"Usage: docker [OPTIONS] COMMAND"
                    if path_candidate == "generic_help"
                    else b"config without required option"
                )
            assert command[0] == system
            if command[1:] == ["config", "--help"]:
                return b"Usage: docker compose config; --no-env-resolution"
            assert command[-4:] == ["config", "--format", "json", "--no-env-resolution"]
            assert command[command.index("--env-file") + 1] == "/dev/null"
            return json.dumps(rendered).encode()

        monkeypatch.setattr(tool.shutil, "which", which)
        monkeypatch.setattr(tool, "_capture", capture)
        assert tool.render_compose(selected) == rendered
        assert commands[-1][0] == system

    @pytest.mark.parametrize(
        "bind_options", [{}, {"create_host_path": False}, {"create_host_path": True}]
    )
    def test_normalized_nhs_bind_false_omission(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        bind_options: dict[str, bool],
    ) -> None:
        monkeypatch.syspath_prepend(str(ROOT / "tools"))
        selected, evaluated, rendered = compose_render
        provider = tmp_path / "provider.toml"
        scope_file = ROOT / "docker-compose.nhs-forecast.yml"
        selected = replace(
            selected,
            compose_files=(*selected.compose_files, scope_file),
            installation=replace(
                selected.installation,
                environment=(
                    *selected.installation.environment,
                    ("NHS_PROVIDER_CONFIG_SOURCE", str(provider)),
                ),
            ),
        )
        rendered = copy.deepcopy(rendered)
        rendered["services"]["prefect-worker"]["volumes"].append(
            {
                "type": "bind",
                "source": str(provider),
                "target": "/app/config/overlays/nhs-provider.toml",
                "read_only": True,
                "bind": bind_options,
            }
        )
        codes = {
            item.code
            for item in tool.check_wiring(selected, evaluated, rendered).diagnostics
        }
        assert ("FORECAST_SCOPE_INVALID" in codes) == bind_options.get(
            "create_host_path", False
        )

    def test_render_process_error_is_not_json_error(
        self,
        compose_render: tuple[Any, Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        selected, _, _ = compose_render

        def capture(command: list[str], *args: Any) -> bytes:
            if command[-1] == "--help":
                return b"--no-env-resolution"
            raise tool.ProfileError(
                "COMPOSE_RENDER_FAILED", "synthetic private failure"
            )

        monkeypatch.setattr(tool, "_capture", capture)
        with pytest.raises(tool.ProfileError, match="COMPOSE_RENDER_FAILED"):
            tool.render_compose(selected)


@pytest.mark.parametrize("state", ["incomplete", "configured"])
@pytest.mark.parametrize(
    "value", [None, '""', '"timezone-secret-canary"', '"+02:00"', "42", "true"]
)
def test_timezone_cli_missing_vs_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str, value: str | None
) -> None:
    profile = incomplete_profile(tmp_path)
    profile.write_text(
        profile.read_text().replace('state = "incomplete"', f'state = "{state}"')
    )
    if state == "configured":
        profile.write_text(
            profile.read_text().replace(
                "compose_files = []", 'compose_files = ["compose.yml"]'
            )
        )
    application = profile.parent / "application.example.toml"
    if value is not None:
        application.write_text(f"default_display_timezone = {value}\n")
    for command in ("show", "check"):
        status = tool.main(
            ["--profile-file", str(profile), command, "--format", "json"]
        )
        output = capsys.readouterr().out
        report = json.loads(output)
        assert status == (
            0 if command == "show" and state == "incomplete" and value is None else 1
        )
        assert "timezone-secret-canary" not in output
        expected = "INPUT_REQUIRED" if value is None else "DISPLAY_TIMEZONE_INVALID"
        assert any(
            d["field"] == "default_display_timezone" and d["code"] == expected
            for d in report["diagnostics"]
        )
        rows = [
            p for p in report["provenance"] if p["field"] == "default_display_timezone"
        ]
        if value is None and state == "incomplete":
            assert rows[0]["default_status"] == "fallback"
            assert rows[0]["value"] == "UTC"
        else:
            assert rows == []


@pytest.mark.parametrize("extension", [False, True])
def test_existing_checker_preserves_new_execution_and_config_obligations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension: bool
) -> None:
    from tests.unit.config.test_deployment_profile import (
        evaluation_selection,
        stored_configuration,
        write_installation,
    )

    initial = evaluation_selection(
        tmp_path,
        stored_configuration(),
        "swiss_observation_history = true\n" if extension else "",
    )
    install = write_installation(
        tmp_path,
        extra=(
            '[environment]\nVERSION = "1.2.3"\nSAPPHIRE_RELEASE_VERSION = "1.2.3"\n'
            f'SAPPHIRE_SOURCE_REVISION = "{"0" * 40}"\n'
        ),
    )
    selection = select_deployment_profile(
        profile_file=initial.profile.manifest_path,
        installation_file=install,
        ambient_environment={},
    )
    evaluation = evaluate_deployment_profile(selection)
    assert evaluation.diagnostics == ()
    rendered_called = False

    def render(_: object) -> dict[str, Any]:
        nonlocal rendered_called
        rendered_called = True
        # Deliberately incomplete wiring: this regression checks obligation
        # preservation, not the later real-render positive acceptance case.
        return {"services": {}}

    monkeypatch.setattr(tool, "render_compose", render)
    checked = tool.check_profile(selection, evaluation)
    assert rendered_called
    codes = {item.code for item in checked.pending_checks}
    assert "COMPOSE_EVIDENCE_PENDING" not in codes
    assert "EXECUTION_COVERAGE_PENDING" in codes
    if extension:
        assert "CONFIG_EXTENSION_PENDING" in codes
    assert checked.summary is not EvaluationSummary.PASS_OFFLINE


@pytest.fixture
def standard_render(
    tmp_path: Path,
) -> tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]]:
    profile, install = materialize_offline_fixture(tmp_path)
    selected = select_deployment_profile(
        profile_file=profile, installation_file=install, ambient_environment={}
    )
    assert selected.installation is not None
    evaluated = evaluate_deployment_profile(
        selected, environment=dict(selected.installation.environment)
    )
    assert evaluated.diagnostics == ()
    try:
        actual = tool.render_compose(selected)
        canonical = tool.render_compose(
            replace(selected, compose_files=(ROOT / "docker-compose.yml",))
        )
    except tool.ProfileError as error:
        if error.code == "COMPOSE_UNAVAILABLE" and not os.environ.get("CI"):
            pytest.skip("Compose unavailable under isolated configuration")
        raise
    return selected, evaluated, actual, canonical


@pytest.fixture
def refresh_render(
    standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
) -> tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]]:
    selected, evaluated, _, canonical = standard_render
    environment = dict(selected.installation.environment)
    environment.update(
        SCHEDULE_REFRESH_OPERATIONAL_ERA5="35 0 * * *",
        SAPPHIRE_ERA5_WEATHER_SCOPE="private-scope-canary",
        SAPPHIRE_ONLY_DEPLOYMENTS=(
            "forecast-cycle,backup-database,refresh-operational-era5-history"
        ),
    )
    selected = replace(
        selected,
        installation=replace(
            selected.installation, environment=tuple(environment.items())
        ),
    )
    return selected, evaluated, tool.render_compose(selected), canonical


class TestOptionalRefreshPreview:
    def test_real_compose_forwards_pair_and_previews_without_execution_coverage(
        self,
        refresh_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        from sapphire_flow.cli.register_deployments import build_deployment_specs

        selected, evaluated, rendered, canonical = refresh_render
        specs = build_deployment_specs(rendered["services"]["init"]["environment"])
        refresh = next(s for s in specs if s.deployment_name.startswith("refresh-"))
        assert (refresh.cron, refresh.parameters, refresh.work_pool_name) == (
            "35 0 * * *",
            (("weather_scope", "private-scope-canary"),),
            "ingest",
        )
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.diagnostics == ()
        assert result.summary is EvaluationSummary.PENDING
        assert "EXECUTION_EXTENSION_PENDING" in {i.code for i in result.pending_checks}
        settings = {s.field: s for s in result.provenance}
        prefix = "jobs.refresh-operational-era5-history"
        assert settings[f"{prefix}.selection"].value == "enabled"
        assert settings[f"{prefix}.pool"].value == "ingest"
        for suffix in ("schedule", "parameters.weather_scope"):
            setting = settings[f"{prefix}.{suffix}"]
            assert setting.visibility == "redacted"
            assert setting.default_status == "explicit"
            assert setting.source == "registrar_spec_and_init_environment"
        assert "private-scope-canary" not in repr(result)
        assert "35 0 * * *" not in repr(result)

    @pytest.mark.parametrize("selector", ["only", "skip"])
    def test_excluded_refresh_is_explicitly_skipped(
        self,
        refresh_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        selector: str,
    ) -> None:
        selected, evaluated, _, canonical = refresh_render
        environment = dict(selected.installation.environment)
        if selector == "only":
            environment["SAPPHIRE_ONLY_DEPLOYMENTS"] = "forecast-cycle,backup-database"
        else:
            environment.pop("SAPPHIRE_ONLY_DEPLOYMENTS")
            environment["SAPPHIRE_SKIP_DEPLOYMENTS"] = (
                "refresh-operational-era5-history"
            )
            overlay = selected.compose_files[-1]
            overlay.write_text(
                overlay.read_text().replace(
                    "SAPPHIRE_ONLY_DEPLOYMENTS", "SAPPHIRE_SKIP_DEPLOYMENTS"
                )
            )
        selected = replace(
            selected,
            installation=replace(
                selected.installation, environment=tuple(environment.items())
            ),
        )
        result = tool.check_wiring(
            selected, evaluated, tool.render_compose(selected), canonical
        )
        assert result.diagnostics == ()
        settings = {s.field: s for s in result.provenance}
        assert settings["jobs.refresh-operational-era5-history.selection"].value == (
            "skipped"
        )
        assert result.summary is (
            EvaluationSummary.PASS_OFFLINE
            if selector == "only"
            else EvaluationSummary.PENDING
        )
        assert "private-scope-canary" not in repr(result)
        assert "35 0 * * *" not in repr(result)

    @pytest.mark.parametrize(
        "change,code",
        [
            ("scope_drift", "SCHEDULE_FORWARDING_INVALID"),
            ("cron_drift", "SCHEDULE_FORWARDING_INVALID"),
            ("drop_scope", "REGISTRAR_INPUT_INVALID"),
            ("drop_cron", "REGISTRAR_INPUT_INVALID"),
            ("drop_pair", "SCHEDULE_FORWARDING_INVALID"),
        ],
    )
    def test_changed_or_dropped_forwarding_refuses_safely(
        self,
        refresh_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        change: str,
        code: str,
    ) -> None:
        selected, evaluated, rendered, canonical = refresh_render
        env = rendered["services"]["init"]["environment"]
        # Keep refresh filtered out to prove dormant inputs are checked too.
        env["SAPPHIRE_ONLY_DEPLOYMENTS"] = "forecast-cycle,backup-database"
        selected = replace(
            selected,
            installation=replace(
                selected.installation,
                environment=tuple(
                    (k, env[k] if k == "SAPPHIRE_ONLY_DEPLOYMENTS" else v)
                    for k, v in selected.installation.environment
                ),
            ),
        )
        if change == "scope_drift":
            env["SAPPHIRE_ERA5_WEATHER_SCOPE"] = "other-private-canary"
        elif change == "cron_drift":
            env["SCHEDULE_REFRESH_OPERATIONAL_ERA5"] = "45 0 * * *"
        else:
            if change in {"drop_scope", "drop_pair"}:
                env.pop("SAPPHIRE_ERA5_WEATHER_SCOPE")
            if change in {"drop_cron", "drop_pair"}:
                env.pop("SCHEDULE_REFRESH_OPERATIONAL_ERA5")
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.summary is EvaluationSummary.FAIL
        assert code in {i.code for i in result.diagnostics}
        assert "private-canary" not in repr(result)
        assert "private-scope-canary" not in repr(result)

    @pytest.mark.parametrize(
        "change", ["missing_scope", "missing_cron", "invalid_cron", "preview_cron"]
    )
    def test_invalid_pair_refuses_even_when_refresh_is_excluded(
        self,
        refresh_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        change: str,
    ) -> None:
        selected, evaluated, _, canonical = refresh_render
        env = dict(selected.installation.environment)
        env["SAPPHIRE_ONLY_DEPLOYMENTS"] = "forecast-cycle,backup-database"
        if change in {"invalid_cron", "preview_cron"}:
            env["SCHEDULE_REFRESH_OPERATIONAL_ERA5"] = (
                "private-invalid-cron" if change == "invalid_cron" else "@daily"
            )
        else:
            env.pop(
                "SAPPHIRE_ERA5_WEATHER_SCOPE"
                if change == "missing_scope"
                else "SCHEDULE_REFRESH_OPERATIONAL_ERA5"
            )
        selected = replace(
            selected,
            installation=replace(selected.installation, environment=tuple(env.items())),
        )
        result = tool.check_wiring(
            selected, evaluated, tool.render_compose(selected), canonical
        )
        assert "REGISTRAR_INPUT_INVALID" in {i.code for i in result.diagnostics}
        assert "private-invalid-cron" not in repr(result)

    @pytest.mark.parametrize("selector", ["ONLY", "SKIP"])
    def test_selection_cannot_invent_an_opt_in_without_its_pair(
        self,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        selector: str,
    ) -> None:
        selected, evaluated, rendered, canonical = standard_render
        env = rendered["services"]["init"]["environment"]
        env.pop("SAPPHIRE_ONLY_DEPLOYMENTS")
        env[f"SAPPHIRE_{selector}_DEPLOYMENTS"] = "refresh-operational-era5-history"
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert "REGISTRAR_INPUT_INVALID" in {i.code for i in result.diagnostics}


class TestStandardExecutionCoverage:
    def test_real_render_clears_only_evidenced_execution(
        self,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        selected, evaluated, rendered, canonical = standard_render
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.diagnostics == ()
        assert result.pending_checks == ()
        assert result.summary is EvaluationSummary.PASS_OFFLINE
        assert "registered_job_overrides" in result.unverified_runtime
        assert "actual_image_identity" in result.unverified_runtime
        assert not any(
            item.field.startswith("jobs.refresh-operational-era5-history")
            for item in result.provenance
        )

    @pytest.mark.parametrize(
        "change",
        [
            "image",
            "command",
            "entrypoint",
            "backup_role",
            "backup_secret",
            "only_and_skip",
            "backup_mount",
            "backup_config",
            "subset",
            "extra_job",
            "job_overlay",
            "source_root",
            "extra_environment",
            "extra_service",
            "extra_network",
            "mount_propagation",
            "extra_mount",
            "api_origin",
        ],
    )
    def test_execution_mutations_cannot_inherit_pass(
        self,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        change: str,
    ) -> None:
        selected, evaluated, original, canonical = standard_render
        rendered = copy.deepcopy(original)
        services = rendered["services"]
        if change in {"image", "command", "entrypoint"}:
            services["api"][change] = "private-canary"
        elif change == "backup_role":
            services["prefect-worker-backup"]["environment"][
                "SAPPHIRE_BACKUP_PGUSER"
            ] = "sapphire"
        elif change == "backup_secret":
            services["prefect-worker-backup"]["environment"]["DB_PASSWORD_SECRET"] = (
                "/run/secrets/sapphire_api_db_password"
            )
        elif change == "only_and_skip":
            services["init"]["environment"]["SAPPHIRE_SKIP_DEPLOYMENTS"] = (
                "ingest-observations"
            )
        elif change == "backup_mount":
            next(
                m
                for m in services["prefect-worker-backup"]["volumes"]
                if m["target"] == "/data/backups"
            )["read_only"] = True
        elif change == "backup_config":
            services["prefect-worker-backup"]["environment"].pop("SAPPHIRE_CONFIG")
        elif change == "subset":
            services["init"]["environment"].pop("SAPPHIRE_ONLY_DEPLOYMENTS")
        elif change == "extra_job":
            services["init"]["environment"]["SAPPHIRE_ONLY_DEPLOYMENTS"] += (
                ",ingest-observations"
            )
        elif change == "job_overlay":
            services["init"]["environment"]["NHS_FORECAST_JOB_OVERLAYS"] = (
                "private-canary"
            )
        elif change == "source_root":
            services["init"]["environment"]["SAPPHIRE_FLOW_SOURCE_ROOT"] = (
                "/private-canary"
            )
        elif change == "extra_environment":
            services["api"]["environment"]["UNREVIEWED_CANARY"] = "private-canary"
        elif change == "extra_service":
            services["private-canary"] = {"image": "private-canary"}
        elif change == "extra_network":
            rendered["networks"]["private-canary"] = {"driver": "bridge"}
        elif change == "mount_propagation":
            next(
                m
                for m in services["api"]["volumes"]
                if m["target"] == "/app/config.toml"
            )["bind"]["propagation"] = "shared"
        elif change == "extra_mount":
            services["api"]["volumes"].append(
                {"type": "bind", "source": "/private-canary", "target": "/app"}
            )
        else:
            services["api"]["environment"]["SAPPHIRE_CORS_ORIGINS"] = "*"
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.summary is not EvaluationSummary.PASS_OFFLINE
        assert "private-canary" not in repr(result)
        assert any(
            p.code == "EXECUTION_COVERAGE_PENDING" for p in result.pending_checks
        )

    def test_config_extension_survives_complete_wiring(
        self,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        selected, _, rendered, canonical = standard_render
        application = selected.profile.base_config
        application.write_text(
            "swiss_observation_history = true\n" + application.read_text()
        )
        evaluated = evaluate_deployment_profile(selected)
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.summary is EvaluationSummary.PENDING
        assert any(p.code == "CONFIG_EXTENSION_PENDING" for p in result.pending_checks)

    def test_empty_runbook_is_pending(
        self,
        tmp_path: Path,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        selected, evaluated, rendered, canonical = standard_render
        runbook = tmp_path / "empty.md"
        runbook.write_text("  \n")
        selected = replace(selected, profile=replace(selected.profile, runbook=runbook))
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert any(p.code == "RUNBOOK_PENDING" for p in result.pending_checks)

    @pytest.mark.parametrize("storage", ["shared", "split", "mixed"])
    def test_shared_artifact_bind_requires_same_storage_across_consumers(
        self,
        tmp_path: Path,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        storage: str,
    ) -> None:
        selected, evaluated, original, canonical = standard_render
        rendered = copy.deepcopy(original)
        consumers = ("api",) if storage == "mixed" else ("api", "prefect-worker")
        for name in consumers:
            mount = next(
                m
                for m in rendered["services"][name]["volumes"]
                if m["target"] == "/data/artifacts"
            )
            mount["type"] = "bind"
            mount["source"] = str(
                tmp_path / (name if storage == "split" else "shared-artifacts")
            )
            mount.pop("volume", None)
            mount["bind"] = {"create_host_path": False}
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        if storage != "shared":
            assert any(p.code == "SHARED_STORAGE_INVALID" for p in result.diagnostics)
        else:
            assert result.summary is EvaluationSummary.PASS_OFFLINE

    def test_runbook_cannot_read_declared_secret_delivery(
        self,
        monkeypatch: pytest.MonkeyPatch,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        selected, evaluated, rendered, canonical = standard_render
        secret = next(iter(dict(selected.installation.secret_bindings).values()))
        secret.parent.mkdir(parents=True)
        secret.write_text("synthetic-secret-canary")
        selected = replace(selected, profile=replace(selected.profile, runbook=secret))
        original = Path.open

        def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
            assert path != secret, "Secret delivery must not be read as a runbook"
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded)
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert any(p.code == "RUNBOOK_PENDING" for p in result.pending_checks)

    @pytest.mark.parametrize("timeout", ["1200", "0", "nan", "secret-canary"])
    def test_backup_timeout_is_positive_finite_and_explicit(
        self,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
        timeout: str,
    ) -> None:
        selected, evaluated, original, canonical = standard_render
        rendered = copy.deepcopy(original)
        installation = replace(
            selected.installation,
            environment=(
                *selected.installation.environment,
                ("SAPPHIRE_BACKUP_DUMP_TIMEOUT_S", timeout),
            ),
        )
        selected = replace(selected, installation=installation)
        rendered["services"]["prefect-worker-backup"]["environment"][
            "SAPPHIRE_BACKUP_DUMP_TIMEOUT_S"
        ] = timeout
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        if timeout == "1200":
            assert result.summary is EvaluationSummary.PASS_OFFLINE
        else:
            assert any(
                item.code == "BACKUP_TIMEOUT_INVALID" for item in result.diagnostics
            )
        assert "secret-canary" not in repr(result)

    def test_different_source_checkout_retains_execution_obligation(
        self,
        tmp_path: Path,
        standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    ) -> None:
        selected, evaluated, rendered, canonical = standard_render
        selected = replace(
            selected,
            installation=replace(
                selected.installation,
                project_directory=tmp_path / "different-checkout",
            ),
        )
        result = tool.check_wiring(selected, evaluated, rendered, canonical)
        assert result.summary is EvaluationSummary.PENDING
        assert any(item.field == "project_directory" for item in result.pending_checks)


class TestCompleteOfflineCommand:
    @staticmethod
    def run_command(
        directory: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/deployment_profile.py"), *arguments],
            cwd=directory,
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(ROOT / "src"),
                        *(path for path in sys.path if path.endswith("site-packages")),
                    ]
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_complete_external_fixture_passes_real_cli_and_render(
        self, tmp_path: Path
    ) -> None:
        directory = tmp_path / "external profile"
        directory.mkdir()
        profile, installation = materialize_offline_fixture(directory)
        (directory / ".env").write_text("SAPPHIRE_ONLY_DEPLOYMENTS=secret-canary\n")
        arguments = [
            "--profile-file",
            str(profile),
            "check",
            "--installation",
            str(installation),
        ]
        completed = self.run_command(directory, [*arguments, "--format", "json"])
        report = json.loads(completed.stdout)
        if not os.environ.get("CI") and any(
            item["code"] == "COMPOSE_UNAVAILABLE" for item in report["pending_checks"]
        ):
            pytest.skip("Compose unavailable under isolated configuration")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert report["summary"] == "PASS_OFFLINE"
        assert report["diagnostics"] == report["pending_checks"] == []
        assert {
            "actual_host_configuration",
            "credential_availability",
            "provider_access",
            "station_model_database_state",
            "scientific_readiness",
            "registered_job_overrides",
            "actual_image_identity",
        } <= set(report["unverified_runtime"])
        assert report["launching"] == "not_available"
        assert (
            report["pass_meaning"]
            == "configuration_and_service_wiring_consistency_only"
        )
        assert (
            report["offline_validation"]
            == "bounded_standard_stored_data_forecast_and_backup"
        )
        assert "secret-canary" not in completed.stdout
        assert str(directory) not in completed.stdout
        assert not (directory / "secret-delivery").exists()
        text = self.run_command(directory, arguments)
        assert text.returncode == 0
        assert "PASS_OFFLINE" in text.stdout
        assert "Unverified runtime:" in text.stdout
        assert "scientific_readiness" in text.stdout
        help_result = self.run_command(directory, ["--help"])
        assert help_result.returncode == 0
        assert "--profile-file" in help_result.stdout

    @pytest.mark.parametrize(
        "mutation,expected",
        [
            ("missing_input", 1),
            ("unknown_key", 2),
            ("extra_service", 2),
            ("invalid_profile", 1),
        ],
    )
    def test_real_command_mutations_fail_or_remain_pending(
        self, tmp_path: Path, mutation: str, expected: int
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        application = tmp_path / "application.toml"
        if mutation == "missing_input":
            application.write_text(
                application.read_text().replace(
                    'default_display_timezone = "UTC"\n', ""
                )
            )
        elif mutation == "unknown_key":
            application.write_text(
                "unreviewed_canary = true\n" + application.read_text()
            )
        elif mutation == "extra_service":
            import yaml

            compose = tmp_path / "compose.yml"
            document = yaml.safe_load(compose.read_text())
            document["services"]["unreviewed_canary"] = {
                "image": "example.invalid/not-used"
            }
            compose.write_text(yaml.safe_dump(document))
        else:
            profile.write_text(
                profile.read_text() + '\ninvalid_canary = "secret-canary"\n'
            )
        completed = self.run_command(
            tmp_path,
            [
                "--profile-file",
                str(profile),
                "check",
                "--installation",
                str(installation),
                "--format",
                "json",
            ],
        )
        report = json.loads(completed.stdout)
        assert completed.returncode == expected
        assert report["summary"] == ("FAIL" if expected == 1 else "PENDING")
        assert "canary" not in completed.stdout
        assert not (tmp_path / "secret-delivery").exists()

    @pytest.mark.parametrize(
        "name",
        [
            "NHS_FORECAST_JOB_OVERLAYS",
            "NHS_PROVIDER_CONFIG_SOURCE",
            "SAPPHIRE_DHM_DELIVERY_DIR",
        ],
    )
    @pytest.mark.parametrize("value", ["", "private-secret-canary"])
    def test_unconsumed_optional_declarations_are_explicitly_pending(
        self, tmp_path: Path, name: str, value: str
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        installation.write_text(
            installation.read_text().replace(
                "[environment]", f'[environment]\n{name} = "{value}"'
            )
        )
        completed = self.run_command(
            tmp_path,
            [
                "--profile-file",
                str(profile),
                "check",
                "--installation",
                str(installation),
                "--format",
                "json",
            ],
        )
        report = json.loads(completed.stdout)
        assert completed.returncode == (2 if value else 0), completed.stdout
        if value:
            assert any(
                item["code"] == "EXECUTION_EXTENSION_PENDING"
                and item["field"] == "environment"
                for item in report["pending_checks"]
            )
        assert "private-secret-canary" not in completed.stdout

    @pytest.mark.parametrize(
        "section,known,unknown,expected",
        [
            ("weather_gap_fill.models.synthetic", "max_gap_days = 1", False, 0),
            ("weather_gap_fill.models.synthetic", "max_gap_days = 1", True, 1),
            ("skill_gate_thresholds.nse", "threshold = 0.5", False, 0),
            ("skill_gate_thresholds.nse", "threshold = 0.5", True, 2),
        ],
    )
    def test_model_mapping_fields_use_authoritative_metadata(
        self, tmp_path: Path, section: str, known: str, unknown: bool, expected: int
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        application = tmp_path / "application.toml"
        addition = f"\n[{section}]\n{known}\n"
        if unknown:
            addition += 'unknown_canary = "private-secret-canary"\n'
        application.write_text(application.read_text() + addition)
        completed = self.run_command(
            tmp_path,
            [
                "--profile-file",
                str(profile),
                "check",
                "--installation",
                str(installation),
                "--format",
                "json",
            ],
        )
        report = json.loads(completed.stdout)
        assert completed.returncode == expected, completed.stdout
        if expected == 2:
            assert any(
                item["code"] == "UNCHECKED_SECTIONS"
                for item in report["pending_checks"]
            )
        if expected == 1:
            assert any(
                item["code"] == "CONFIG_READER_INVALID"
                for item in report["diagnostics"]
            )
        assert "canary" not in completed.stdout


@pytest.mark.parametrize("filename", [".env", ".env.production", "runbook.md"])
def test_runbook_env_files_and_aliases_are_rejected_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    standard_render: tuple[Any, ProfileEvaluation, dict[str, Any], dict[str, Any]],
    filename: str,
) -> None:
    selected, evaluated, rendered, canonical = standard_render
    target = tmp_path / (filename if filename.startswith(".env") else ".env")
    target.write_text("SYNTHETIC_SECRET=private-secret-canary\n")
    runbook = tmp_path / filename
    if runbook != target:
        runbook.symlink_to(target)
    selected = replace(selected, profile=replace(selected.profile, runbook=runbook))
    original = Path.open

    def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.resolve() != target.resolve(), "Environment file must not be read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = tool.check_wiring(selected, evaluated, rendered, canonical)
    assert any(item.code == "RUNBOOK_PENDING" for item in result.pending_checks)
    assert "private-secret-canary" not in repr(result)
