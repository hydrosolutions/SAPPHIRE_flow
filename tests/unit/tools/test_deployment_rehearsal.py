from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from sapphire_flow.config.deployment_profile import (
    ProfileError,
)
from tests.unit.config.profile_support import materialize_offline_fixture

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "deployment_rehearsal_tool", ROOT / "tools/deployment_rehearsal.py"
)
assert spec is not None and spec.loader is not None
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(cwd),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(args[1]).resolve().parents[1] / "src")
            if len(args) > 1 and args[1].endswith(".py")
            else str(cwd / "src"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("rehearsal-source")
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "src",
            "tools",
            "docker",
            "Caddyfile",
            "docker-compose.yml",
            "RELEASE_TARGET",
            "docs/deployment-quickstart.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for name in (*tracked, "tools/deployment_rehearsal.py"):
        source = ROOT / name
        if source.is_file():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Generated version metadata is needed to import the package in this test checkout.
    version = ROOT / "src/sapphire_flow/_version.py"
    if version.exists():
        shutil.copy2(version, root / "src/sapphire_flow/_version.py")
    template = root / "empty-template"
    template.mkdir()
    for args in (
        ["git", "init", "--template=" + str(template)],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Synthetic Test",
            "commit",
            "-qm",
            "synthetic checkout",
        ],
    ):
        completed = command(list(args), root)
        assert completed.returncode == 0, completed.stderr
    return root


def fixture(directory: Path, checkout: Path) -> tuple[Path, Path]:
    profile, installation = materialize_offline_fixture(directory)
    revision = command(["git", "rev-parse", "HEAD"], checkout).stdout.strip()
    release = (checkout / "RELEASE_TARGET").read_text().strip()
    for path in directory.iterdir():
        if path.is_file():
            path.write_text(
                path.read_text()
                .replace(str(ROOT), str(checkout))
                .replace("0000000000000000000000000000000000000000", revision)
                .replace('"1.2.3"', json.dumps(release))
            )
    return profile, installation


def cli(
    directory: Path, checkout: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return command(
        [
            sys.executable,
            str(checkout / "tools/deployment_rehearsal.py"),
            "--profile-file",
            str(directory / "profile.toml"),
            "preview",
            "--installation",
            str(directory / "installation.toml"),
            "--format",
            "json",
            *extra,
        ],
        directory,
    )


class TestExternalPreview:
    def test_real_compose_preview_has_no_executable_recipe_or_runtime_claim(
        self, tmp_path: Path, checkout: Path
    ) -> None:
        fixture(tmp_path, checkout)
        completed = cli(tmp_path, checkout)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        report = json.loads(completed.stdout)
        assert report["status"] == "PLAN_READY"
        assert report["offline_status"] == "PASS_OFFLINE"
        assert report["images_probed"] is False
        assert report["services_started"] is False
        assert report["executable_recipe"] is False
        assert str(tmp_path) not in completed.stdout
        assert "services" not in report
        assert not (tmp_path / "secret-delivery").exists()
        assert {p.name for p in tmp_path.iterdir()} == {
            "profile.toml",
            "installation.toml",
            "application.toml",
            "compose.yml",
        }

    @pytest.mark.parametrize(
        "change,code",
        [
            ("identity", "SOURCE_IDENTITY_MISMATCH"),
            ("dynamic", "DYNAMIC_CONFIG_UNSUPPORTED"),
            ("invalid", "CONFIG_READER_INVALID"),
        ],
    )
    def test_refusals_are_sanitized(
        self, tmp_path: Path, checkout: Path, change: str, code: str
    ) -> None:
        fixture(tmp_path, checkout)
        path = tmp_path / (
            "installation.toml" if change == "identity" else "application.toml"
        )
        text = path.read_text()
        if change == "identity":
            revision = command(["git", "rev-parse", "HEAD"], checkout).stdout.strip()
            text = text.replace(revision, "0" * 40)
        elif change == "dynamic":
            text = text.replace('"UTC"', '"${PRIVATE_CANARY}"')
        else:
            text = text.replace(
                "max_retention_days = 365", 'max_retention_days = "PRIVATE_CANARY"'
            )
        path.write_text(text)
        completed = cli(tmp_path, checkout)
        assert completed.returncode in (1, 2)
        report = json.loads(completed.stdout)
        assert code in report["codes"]
        assert "PRIVATE_CANARY" not in completed.stdout + completed.stderr

    def test_dirty_source_remains_pending(self, tmp_path: Path, checkout: Path) -> None:
        fixture(tmp_path, checkout)
        changed = checkout / "untracked-change"
        changed.write_text("source modification")
        try:
            completed = cli(tmp_path, checkout)
            assert completed.returncode == 2
            assert json.loads(completed.stdout)["codes"] == ["SOURCE_DIRTY"]
        finally:
            changed.unlink()

    def test_explicit_development_identity_is_distinct(
        self, tmp_path: Path, checkout: Path
    ) -> None:
        _, installation = fixture(tmp_path, checkout)
        revision = command(["git", "rev-parse", "HEAD"], checkout).stdout.strip()
        release = (checkout / "RELEASE_TARGET").read_text().strip()
        installation.write_text(
            installation.read_text()
            .replace(
                f'SAPPHIRE_RELEASE_VERSION = "{release}"',
                f'SAPPHIRE_RELEASE_VERSION = "0.dev0+g{revision}"',
            )
            .replace(f'VERSION = "{release}"', 'VERSION = "synthetic-dev"')
        )
        completed = cli(tmp_path, checkout, "--image-identity", "ci-development")
        assert completed.returncode == 0, completed.stdout
        report = json.loads(completed.stdout)
        assert report["identity_kind"] == "ci-development"
        assert report["images_probed"] is False

    @pytest.mark.parametrize(
        "mutation",
        [
            "extra_secret",
            "network_mode",
            "extension",
            "missing_image",
            "missing_volumes",
            "missing_networks",
            "missing_environment",
            "malformed_mount",
        ],
    )
    def test_actual_cli_rejects_unsupported_recipe(
        self, tmp_path: Path, checkout: Path, mutation: str
    ) -> None:
        profile, installation = fixture(tmp_path, checkout)
        base = yaml.safe_load((checkout / "docker-compose.yml").read_text())
        if mutation == "extra_secret":
            base["secrets"]["extra"] = {"file": str(tmp_path / "PRIVATE_CANARY")}
            base["services"]["api"]["secrets"].append("extra")
        elif mutation == "network_mode":
            base["services"]["api"]["network_mode"] = "host"
            base["services"]["api"].pop("networks")
        elif mutation == "extension":
            base["x-private-extension"] = {"value": "PRIVATE_CANARY"}
        elif mutation == "missing_image":
            base["services"]["api"].pop("image")
        elif mutation in {"missing_volumes", "missing_networks"}:
            base.pop(mutation.removeprefix("missing_"))
        elif mutation == "missing_environment":
            # The selected overlay supplies Caddy's env, so remove that as well.
            overlay = yaml.safe_load((tmp_path / "compose.yml").read_text())
            overlay["services"].pop("caddy")
            (tmp_path / "compose.yml").write_text(yaml.safe_dump(overlay))
            base["services"]["caddy"].pop("environment", None)
        else:
            base["services"]["api"]["volumes"] = [{"type": "PRIVATE_CANARY"}]
        private_base = tmp_path / "private-base.yml"
        private_base.write_text(yaml.safe_dump(base))
        for path in (profile, installation):
            path.write_text(
                path.read_text().replace(
                    str(checkout / "docker-compose.yml"), str(private_base)
                )
            )
        completed = cli(tmp_path, checkout)
        assert completed.returncode in (1, 2)
        report = json.loads(completed.stdout)
        assert report["status"] != "PLAN_READY"
        assert "PRIVATE_CANARY" not in completed.stdout + completed.stderr
        assert "Traceback" not in completed.stderr
        if mutation in {"extra_secret", "network_mode", "extension"}:
            assert (
                "EXECUTION_EXTENSION_PENDING" in report["codes"]
                or "SECRET_GRANTS_INVALID" in report["codes"]
            )

    def test_failed_development_preview_keeps_identity_kind(
        self, tmp_path: Path, checkout: Path
    ) -> None:
        fixture(tmp_path, checkout)
        completed = cli(tmp_path, checkout, "--image-identity", "ci-development")
        assert completed.returncode == 1
        report = json.loads(completed.stdout)
        assert report["codes"] == ["SOURCE_IDENTITY_MISMATCH"]
        assert report["identity_kind"] == "ci-development"

    def test_run_command_is_unavailable(self, tmp_path: Path, checkout: Path) -> None:
        completed = command(
            [
                sys.executable,
                str(checkout / "tools/deployment_rehearsal.py"),
                "run",
                "--format",
                "json",
            ],
            tmp_path,
        )
        assert completed.returncode == 1
        assert json.loads(completed.stdout)["codes"] == ["CLI_INPUT_INVALID"]


class TestInputCapture:
    @pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink", "dotenv"])
    def test_secret_or_env_input_is_refused_before_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
    ) -> None:
        secret = tmp_path / (".env.production" if alias == "dotenv" else "delivery")
        secret.write_text("PRIVATE_CANARY")
        candidate = secret
        if alias in {"symlink", "hardlink"}:
            candidate = tmp_path / "config.toml"
            if alias == "symlink":
                candidate.symlink_to(secret)
            else:
                candidate.hardlink_to(secret)

        def denied(*args: object, **kwargs: object) -> Any:
            pytest.fail("protected input was opened")

        monkeypatch.setattr(Path, "open", denied)
        with pytest.raises(ProfileError, match="preview refused") as caught:
            tool.capture((candidate,), (secret,) if alias != "dotenv" else ())
        assert caught.value.code == "PROTECTED_INPUT"

    def test_bytes_and_mode_drift_refuse(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("original")
        frozen = tool.capture((path,), ())
        path.write_text("modified")
        with pytest.raises(ProfileError, match="preview refused") as caught:
            tool.verify_inputs(frozen, ())
        assert caught.value.code == "INPUT_DRIFT"


class TestFiniteTransformation:
    def test_exact_differences_preserve_shell_literals_and_sharing(self) -> None:
        original: dict[str, Any] = {
            "name": "selected",
            "volumes": {"data": {"name": "selected_data"}},
            "networks": {
                "backend": {"name": "selected_backend", "driver": "bridge"},
                "frontend": {"name": "selected_frontend", "driver": "bridge"},
            },
            "secrets": {"db_password": {"file": "/private/delivery"}},
            "services": {
                "caddy": {
                    "image": "caddy:pinned",
                    "environment": {
                        "SAPPHIRE_DOMAIN": "example.invalid",
                        "LITERAL": "${UNEXPANDED}",
                    },
                    "command": ["sh", "-c", "echo $$HOME $HOME"],
                    "ports": [{"target": 80, "published": "80"}],
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/private/Caddyfile",
                            "target": "/etc/caddy/Caddyfile",
                        },
                        {"type": "volume", "source": "data", "target": "/data"},
                    ],
                }
            },
        }
        transformed = tool.transform_recipe(original, {"/private/Caddyfile": "caddy"})
        tool.verify_transform(original, transformed, {"/private/Caddyfile": "caddy"})
        assert (
            transformed["services"]["caddy"]["command"]
            == original["services"]["caddy"]["command"]
        )
        assert (
            transformed["services"]["caddy"]["environment"]["LITERAL"]
            == "${UNEXPANDED}"
        )
        assert (
            transformed["services"]["caddy"]["volumes"][1]
            == original["services"]["caddy"]["volumes"][1]
        )
        changed = copy.deepcopy(transformed)
        changed["services"]["caddy"]["command"] = ["unexpected"]
        with pytest.raises(ProfileError, match="preview refused"):
            tool.verify_transform(original, changed, {"/private/Caddyfile": "caddy"})


@pytest.fixture
def selected_recipe(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    from sapphire_flow.config.deployment_profile import select_deployment_profile

    profile, installation = materialize_offline_fixture(tmp_path)
    selection = select_deployment_profile(
        profile_file=profile, installation_file=installation, ambient_environment={}
    )
    return selection, tool.checker.render_compose(selection)


class TestRecipeBoundaries:
    @pytest.mark.parametrize(
        "mutation", ["host_data", "writable_config", "external", "custom_name"]
    )
    def test_unrehearsable_storage_refuses(
        self, selected_recipe: tuple[Any, dict[str, Any]], mutation: str
    ) -> None:
        selection, recipe = selected_recipe
        if mutation == "external":
            recipe["volumes"]["pgdata"]["external"] = True
        elif mutation == "custom_name":
            recipe["volumes"]["pgdata"]["name"] = "foreign"
        elif mutation == "host_data":
            recipe["services"]["postgres"]["volumes"][0] = {
                "type": "bind",
                "source": "/private/existing-data",
                "target": "/var/lib/postgresql/data",
            }
        else:
            for mount in recipe["services"]["api"]["volumes"]:
                if mount.get("target") == "/app/config.toml":
                    mount["read_only"] = False
        with pytest.raises(ProfileError, match="preview refused"):
            tool.restrict_recipe(selection, recipe, ROOT)

    @pytest.mark.parametrize(
        "mutation",
        ["command", "privilege", "access", "sharing", "port", "network", "secret"],
    )
    def test_finite_verifier_rejects_unlisted_differences(
        self, selected_recipe: tuple[Any, dict[str, Any]], mutation: str
    ) -> None:
        selection, original = selected_recipe
        inputs = tool.restrict_recipe(selection, original, ROOT)
        candidate = tool.transform_recipe(original, inputs)
        if mutation == "command":
            candidate["services"]["init"]["command"] = ["true"]
        elif mutation == "privilege":
            candidate["services"]["api"]["privileged"] = True
        elif mutation in {"access", "sharing"}:
            mount = next(
                item
                for item in candidate["services"]["api"]["volumes"]
                if item["type"] == "volume"
            )
            mount["read_only" if mutation == "access" else "source"] = (
                False if mutation == "access" else "other"
            )
        elif mutation == "port":
            candidate["services"]["api"]["ports"] = [
                {"published": "8000", "target": 8000}
            ]
        elif mutation == "network":
            candidate["networks"]["backend"]["internal"] = False
        else:
            candidate["services"]["api"]["secrets"].append({"source": "db_password"})
        with pytest.raises(ProfileError, match="preview refused") as caught:
            tool.verify_transform(original, candidate, inputs)
        assert caught.value.code == "TRANSFORM_INVALID"

    @pytest.mark.parametrize("plugin_only", [False, True])
    def test_real_compose_literal_roundtrip(
        self,
        selected_recipe: tuple[Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        plugin_only: bool,
    ) -> None:
        selection, _ = selected_recipe
        captured_executables: list[str] = []
        original_capture = tool.checker._capture

        def capture(
            command: list[str], environment: dict[str, str], directory: Path
        ) -> bytes:
            output: bytes = original_capture(command, environment, directory)
            if "--format" in command:
                captured_executables.append(command[0])
            return output

        # Observe the actual successful production render rather than maintaining
        # another discovery policy. No Docker wrapper or private config is read.
        with monkeypatch.context() as scoped:
            scoped.setattr(tool.checker, "_capture", capture)
            original = tool.checker.render_compose(selection)
            executable = captured_executables[-1]
            if plugin_only:
                lookups: list[str | None] = []

                def plugin_lookup(name: str, path: str | None = None) -> str | None:
                    lookups.append(path)
                    return (
                        executable
                        if path == "/usr/libexec/docker/cli-plugins"
                        else None
                    )

                scoped.setattr(tool.checker.shutil, "which", plugin_lookup)
                original = tool.checker.render_compose(selection)
                assert lookups[0] is None
                assert "/usr/libexec/docker/cli-plugins" in lookups
                assert captured_executables[-1] == executable
        inputs = tool.restrict_recipe(selection, original, ROOT)
        # Literal values exercise the future serialization boundary, not an offline
        # coverage exception: this test does not claim the mutated input passes.
        original["services"]["api"]["environment"]["ROUNDTRIP"] = (
            "${NOT_EXPANDED} $$literal $HOME with spaces"
        )
        original["services"]["api"]["command"] = [
            "sh",
            "-c",
            "echo $$HOME $HOME '${NOT_EXPANDED}'",
        ]
        candidate = tool.transform_recipe(original, inputs)
        rendered = subprocess.run(
            [
                executable,
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(ROOT),
                "-p",
                "unresolved-rehearsal",
                "-f",
                "-",
                "config",
                "--no-env-resolution",
                "--no-interpolate",
                "--format",
                "json",
            ],
            input=json.dumps(candidate),
            env={"PATH": os.defpath, "HOME": "/dev/null", "DOCKER_CONFIG": "/dev/null"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert rendered.returncode == 0, "isolated Compose roundtrip failed"
        replayed = json.loads(rendered.stdout)
        tool.verify_transform(original, replayed, inputs)

    def test_config_changed_during_evaluation_refuses(
        self,
        selected_recipe: tuple[Any, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        selection, _ = selected_recipe

        def changed(selected: Any, evaluation: Any) -> Any:
            selected.profile.base_config.write_text(
                selected.profile.base_config.read_text() + "\n# drift\n"
            )
            return tool.checker.CheckedProfile(evaluation, None)

        monkeypatch.setattr(tool.checker, "inspect_profile", changed)
        with pytest.raises(ProfileError, match="preview refused") as caught:
            tool.preview(selection)
        assert caught.value.code == "INPUT_DRIFT"


class TestSelectionBoundary:
    @pytest.mark.parametrize(
        "failure", [tool.ConfigurationError, KeyError, AttributeError, IndexError]
    )
    def test_expected_post_check_boundary_failures_are_sanitized(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: type[Exception],
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)

        def failed(*args: Any, **kwargs: Any) -> Any:
            raise failure("PRIVATE_CANARY")

        # Inject only the raced post-PASS reread boundary. Evaluation and actual
        # Compose checking still run; source identity is separately tested above.
        def fixed_identity(*args: Any) -> str:
            return "0" * 40

        monkeypatch.setattr(tool, "source_identity", fixed_identity)
        monkeypatch.setattr(tool, "restrict_recipe", failed)
        result = tool.main(
            [
                "preview",
                "--profile-file",
                str(profile),
                "--installation",
                str(installation),
                "--format",
                "json",
            ]
        )
        assert result == 1
        captured = capsys.readouterr()
        assert json.loads(captured.out)["codes"] == ["INPUT_INVALID"]
        assert "PRIVATE_CANARY" not in captured.out + captured.err
        assert "Traceback" not in captured.err

    def test_manifest_changed_during_selection_refuses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        original = tool.select_deployment_profile

        def changed(**kwargs: Any) -> Any:
            selected = original(**kwargs)
            profile.write_text(profile.read_text() + "\n# drift\n")
            return selected

        monkeypatch.setattr(tool, "select_deployment_profile", changed)
        code = tool.main(
            [
                "preview",
                "--profile-file",
                str(profile),
                "--installation",
                str(installation),
                "--format",
                "json",
            ]
        )
        assert code == 1
        assert json.loads(capsys.readouterr().out)["codes"] == ["INPUT_DRIFT"]

    def test_application_alias_of_secret_never_opens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        installation.write_text(
            installation.read_text().replace(
                str(tmp_path / "secret-delivery/db_password"),
                str(tmp_path / "application.toml"),
            )
        )
        selection = tool.select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        original_open = Path.open

        def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path.resolve() == (tmp_path / "application.toml").resolve():
                pytest.fail("credential alias was read as application input")
            return cast("Any", original_open(path, *args, **kwargs))

        monkeypatch.setattr(Path, "open", guarded)
        with pytest.raises(ProfileError, match="preview refused") as caught:
            tool.preview(selection)
        assert caught.value.code == "PROTECTED_INPUT"

    def test_docker_override_does_not_read_any_input(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DOCKER_HOST", "tcp://PRIVATE_CANARY")
        result = tool.main(
            [
                "preview",
                "--profile-file",
                str(tmp_path / "absent.toml"),
                "--installation",
                str(tmp_path / "absent-install.toml"),
                "--format",
                "json",
            ]
        )
        assert result == 2
        output = capsys.readouterr().out
        assert json.loads(output)["codes"] == ["DOCKER_OVERRIDE_UNSUPPORTED"]
        assert "PRIVATE_CANARY" not in output

    @pytest.mark.parametrize("mutation", ["path", "ambient"])
    def test_invalid_selection_exits_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        mutation: str,
    ) -> None:
        profile, installation = materialize_offline_fixture(tmp_path)
        if mutation == "path":
            profile.write_text(
                profile.read_text().replace(
                    'base_config = "application.toml"',
                    'base_config = "${PRIVATE_CANARY}"',
                )
            )
        else:
            monkeypatch.setenv("COMPOSE_PROJECT_NAME", "PRIVATE_CANARY")
        code = tool.main(
            [
                "preview",
                "--profile-file",
                str(profile),
                "--installation",
                str(installation),
                "--format",
                "json",
            ]
        )
        assert code == 1
        output = capsys.readouterr().out
        assert json.loads(output)["status"] == "FAIL"
        assert "PRIVATE_CANARY" not in output
