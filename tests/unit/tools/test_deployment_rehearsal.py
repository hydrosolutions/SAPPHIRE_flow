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

    def test_run_requires_explicit_inputs(self, tmp_path: Path, checkout: Path) -> None:
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
        assert completed.returncode == 2
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


class DockerFake:
    def __init__(self, record: Any) -> None:
        self.record = record
        self.daemon = record.daemon
        self.resources: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[list[str]] = []

    def add(self, kind: str, identifier: str, name: str | None = None) -> None:
        labels = {
            tool._LABEL: self.record.run_id,
            tool._PROJECT_LABEL: self.record.project,
        }
        self.resources[kind, identifier] = {
            "Name": name or self.record.names(kind)[0],
            "Id": identifier,
            "Config": {"Labels": labels},
            "Labels": labels,
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        }

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        timeout: float,
        stdin: str | None = None,
    ) -> Any:
        self.calls.append(command)
        assert set(environment) == {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST"}
        assert environment["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        args = command[3:]
        output = ""
        if args[0] == "info":
            output = self.daemon
        elif "inspect" in args:
            item = self.resources[args[0], args[-1]]
            output = json.dumps([item])
        elif "rm" in args:
            del self.resources[args[0], args[-1]]
        else:
            kind = "container" if args[0] == "ps" else args[0]
            filters = [
                args[index + 1] for index, word in enumerate(args) if word == "--filter"
            ]
            matches: list[str] = []
            for (resource_kind, identifier), item in self.resources.items():
                if kind != resource_kind:
                    continue

                def matches_filter(
                    value: str,
                    item: dict[str, Any] = item,
                    identifier: str = identifier,
                ) -> bool:
                    if value.startswith("label="):
                        key, expected = value[6:].split("=", 1)
                        return item["Labels"].get(key) == expected
                    if value.startswith("name="):
                        return item["Name"].lstrip("/") == value[5:].strip("^$")
                    return value == "id=" + identifier

                if all(matches_filter(value) for value in filters):
                    matches.append(identifier)
            output = "\n".join(matches)
        return tool.CommandOutput(stdout=output, stderr="", returncode=0)


def fake_runtime(tmp_path: Path) -> tuple[Any, DockerFake]:
    tmp_path.chmod(0o700)
    record = tool.Recovery(run_id="a" * 32, daemon="daemon-1")
    fake = DockerFake(record)
    runtime = tool.Runtime(
        directory=tmp_path,
        deadline=100,
        clock=lambda: 0.0,
        executor=fake,
        docker="docker",
        record=record,
    )
    tool.save_recovery(tmp_path, record)
    return runtime, fake


class TestRecovery:
    def test_cleanup_is_idempotent_without_source_or_secret_files(
        self, tmp_path: Path
    ) -> None:
        runtime, fake = fake_runtime(tmp_path)
        for kind in ("container", "network", "volume"):
            fake.add(kind, kind + "-id")
        runtime.checkpoint()
        readback = tool.read_recovery(tmp_path)
        assert len(readback.known) == 3
        result = tool.cleanup_runtime(runtime)
        assert result == {"container": 1, "network": 1, "volume": 1}
        assert not fake.resources
        assert tool.cleanup_runtime(runtime) == {
            "container": 0,
            "network": 0,
            "volume": 0,
        }
        assert not tool.read_recovery(tmp_path).known

    @pytest.mark.parametrize(
        "mutation", ["daemon", "run_label", "project_label", "name"]
    )
    def test_changed_ownership_never_authorizes_removal(
        self, tmp_path: Path, mutation: str
    ) -> None:
        runtime, fake = fake_runtime(tmp_path)
        fake.add("container", "container-id")
        runtime.checkpoint()
        if mutation == "daemon":
            fake.daemon = "another-daemon"
        elif mutation == "name":
            fake.resources["container", "container-id"]["Name"] = "foreign"
        else:
            label = tool._LABEL if mutation == "run_label" else tool._PROJECT_LABEL
            fake.resources["container", "container-id"]["Labels"].pop(label)
        secret_directory = tmp_path / "secrets"
        secret_directory.mkdir(mode=0o700)
        (secret_directory / "db_password").write_text("CANARY")
        with pytest.raises(
            tool.RehearsalRuntimeError, match="daemon_changed|foreign_resource_refused"
        ):
            tool.cleanup_runtime(runtime)
        assert fake.resources
        assert not any("rm" in args for args in fake.calls)
        assert not secret_directory.exists()
        assert (tmp_path / "recovery.json").exists()

    def test_recorded_network_id_cannot_disappear_through_relabelling(
        self, tmp_path: Path
    ) -> None:
        runtime, fake = fake_runtime(tmp_path)
        fake.add("network", "network-id")
        runtime.checkpoint()
        item = fake.resources["network", "network-id"]
        item["Name"] = "different-name"
        item["Labels"].clear()
        with pytest.raises(
            tool.RehearsalRuntimeError, match="foreign_resource_refused"
        ):
            tool.cleanup_runtime(runtime)
        assert fake.resources
        assert not any("rm" in command for command in fake.calls)

    def test_stopped_probe_is_discovered_without_recorded_id(
        self, tmp_path: Path
    ) -> None:
        runtime, fake = fake_runtime(tmp_path)
        fake.add("container", "probe-id", runtime.record.project + "-probe-default")
        fake.resources["container", "probe-id"]["State"]["Status"] = "exited"
        assert not runtime.record.known
        assert tool.cleanup_runtime(runtime)["container"] == 1

    @pytest.mark.parametrize("kind", ["symlink", "fifo", "mode", "malformed"])
    def test_unsafe_recovery_record_refuses_before_read(
        self, tmp_path: Path, kind: str
    ) -> None:
        runtime, _ = fake_runtime(tmp_path)
        path = tmp_path / "recovery.json"
        if kind == "mode":
            path.chmod(0o644)
        elif kind == "malformed":
            path.write_text('{"run_id": "PRIVATE_CANARY"}')
        else:
            path.unlink()
            if kind == "symlink":
                path.symlink_to(tmp_path / "outside")
            else:
                os.mkfifo(path, 0o600)
        with pytest.raises(
            (tool.RehearsalRuntimeError, KeyError),
            match="unsafe_recovery_record|schema_version",
        ):
            tool.read_recovery(runtime.directory)


class TestRuntimeChannels:
    @pytest.mark.parametrize(
        "stream,status", [("stdout", 0), ("stderr", 0), ("stderr", 1), ("stdout", 1)]
    )
    def test_secret_canary_is_detected_before_command_failure(
        self, tmp_path: Path, stream: str, status: int
    ) -> None:
        def executor(
            command: list[str],
            environment: dict[str, str],
            timeout: float,
            stdin: str | None = None,
        ) -> Any:
            return tool.CommandOutput(
                stdout="PRIVATE_CANARY" if stream == "stdout" else "",
                stderr="PRIVATE_CANARY" if stream == "stderr" else "",
                returncode=status,
            )

        runtime = tool.Runtime(
            directory=tmp_path,
            deadline=1,
            clock=lambda: 0.0,
            executor=executor,
            canaries=("PRIVATE_CANARY",),
        )
        with pytest.raises(tool.RehearsalRuntimeError, match="secret_output_detected"):
            runtime.command(["ignored"])

    def test_real_subprocess_keeps_failed_output_private(self) -> None:
        output = tool.execute(
            [
                sys.executable,
                "-c",
                "import sys; print('synthetic-out'); "
                "sys.stderr.write('synthetic-error'); sys.exit(3)",
            ],
            {"PATH": os.defpath},
            10,
        )
        assert output.returncode == 3
        assert output.stdout == "synthetic-out"
        assert output.stderr == "synthetic-error"
        assert "synthetic" not in repr(output)

    def test_runtime_binding_roundtrip_with_literal_shell_values(
        self, selected_recipe: tuple[Any, dict[str, Any]], tmp_path: Path
    ) -> None:
        selection, original = selected_recipe
        snapshots = tool.restrict_recipe(selection, original, ROOT)
        original["services"]["api"]["environment"]["LITERAL"] = "$${UNEXPANDED} $$HOME"
        original["services"]["api"]["command"] = ["sh", "-c", "echo $$HOME"]
        images = tuple(
            (value["image"], "sha256:" + str(index) * 64)
            for index, value in enumerate(original["services"].values())
        )
        bindings = tool.RecipeBindings(
            project="sapphire-rehearsal-" + "a" * 32,
            run_id="a" * 32,
            workspace=tmp_path / "path with $dollars",
            images=images,
        )
        candidate = tool.transform_recipe(original, snapshots, bindings)
        executable = tool.compose_executable(selection)
        bindings.workspace.mkdir(mode=0o700)
        recipe_file = bindings.workspace / "recipe.json"
        recipe_file.write_text(json.dumps(candidate))
        before = recipe_file.read_bytes()

        class RenderOnlyRuntime:
            directory = bindings.workspace

            def guard_daemon(self) -> Any:
                from types import SimpleNamespace

                return SimpleNamespace(project=bindings.project)

            def command(self, arguments: list[str]) -> str:
                assert "config" in arguments
                assert arguments[arguments.index("-f") + 1] == str(recipe_file)
                completed = subprocess.run(
                    arguments,
                    env={
                        "PATH": os.defpath,
                        "HOME": "/dev/null",
                        "DOCKER_CONFIG": "/dev/null",
                    },
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                assert completed.returncode == 0
                return completed.stdout

        prepared = tool.Prepared(
            preview=tool.Preview(status="PLAN_READY", codes=()),
            recipe=original,
            snapshots=tuple(snapshots.items()),
        )
        tool.render_runtime_recipe(RenderOnlyRuntime(), executable, prepared, bindings)
        assert recipe_file.read_bytes() == before

    @pytest.mark.parametrize("replacement", [None, True])
    def test_executable_snapshot_bind_must_explicitly_disable_directory_creation(
        self, selected_recipe: tuple[Any, dict[str, Any]], replacement: bool | None
    ) -> None:
        selection, original = selected_recipe
        snapshots = tool.restrict_recipe(selection, original, ROOT)
        candidate = tool.transform_recipe(original, snapshots)
        mount = next(
            item
            for item in candidate["services"]["api"]["volumes"]
            if item["type"] == "bind"
        )
        if replacement is None:
            mount["bind"].pop("create_host_path")
        else:
            mount["bind"]["create_host_path"] = replacement
        with pytest.raises(ProfileError, match="TRANSFORM_INVALID"):
            tool.verify_transform(original, candidate, snapshots)
        if replacement is True:
            with pytest.raises(ProfileError, match="TRANSFORM_INVALID"):
                tool.verify_transform(
                    original, candidate, snapshots, representation="rendered"
                )

    def test_execute_kills_and_reaps_after_eof_wait_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        events: list[str] = []

        class Process:
            stdin = SimpleNamespace(fileno=lambda: 0, close=lambda: None)
            stdout = SimpleNamespace(fileno=lambda: 1)
            stderr = SimpleNamespace(fileno=lambda: 2)
            returncode = None

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> None:
                assert "reaped" in events

            def wait(self, timeout: float) -> int:
                if "killed" not in events:
                    raise subprocess.TimeoutExpired("private command", timeout)
                events.append("reaped")
                return 0

            def poll(self) -> None:
                return None

            def kill(self) -> None:
                events.append("killed")

        class Selector:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

            def register(self, *args: Any) -> None:
                pass

            def get_map(self) -> dict[str, Any]:
                return {}

        def popen(*args: Any, **kwargs: Any) -> Process:
            return Process()

        def blocking(*args: Any) -> None:
            pass

        monkeypatch.setattr(tool.subprocess, "Popen", popen)
        monkeypatch.setattr(tool.selectors, "DefaultSelector", Selector)
        monkeypatch.setattr(tool.os, "set_blocking", blocking)
        with pytest.raises(
            tool.RehearsalRuntimeError, match="command_unavailable_or_timeout"
        ):
            tool.execute(["unused"], {}, 1)
        assert events == ["killed", "reaped"]


class TestLifecycleFaults:
    @pytest.mark.parametrize(
        "stage",
        [
            "image_preflight",
            "init",
            "workers_and_api",
            "api",
            "unexpected_flow_execution",
            "command_timeout",
            "interrupted",
            "cleanup_failure",
            "unexpected_exception",
            "profile_drift",
        ],
    )
    def test_failure_always_cleans_generated_inputs_and_owned_objects(
        self,
        selected_recipe: tuple[Any, dict[str, Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        stage: str,
    ) -> None:
        selection, recipe = selected_recipe
        record = tool.Recovery(run_id="a" * 32, daemon="daemon-1")
        fake = DockerFake(record)
        snapshot_paths = tool.restrict_recipe(selection, recipe, ROOT)
        frozen = tool.capture(tuple(Path(path) for path in snapshot_paths), ())
        prepared = tool.Prepared(
            preview=tool.Preview(status="PLAN_READY", codes=(), revision="0" * 40),
            recipe=recipe,
            frozen=frozen,
            snapshots=tuple(snapshot_paths.items()),
        )

        def prepare(*args: Any, **kwargs: Any) -> Any:
            return prepared

        def images(*args: Any) -> tuple[tuple[str, str], ...]:
            if stage == "image_preflight":
                raise tool.RehearsalRuntimeError("image_identity_invalid")
            return tuple(
                (item["image"], "sha256:" + "1" * 64)
                for item in recipe["services"].values()
            )

        def identity(*args: Any) -> str:
            return "0" * 40

        def render(*args: Any) -> None:
            pass

        def start(
            runtime: Any,
            executable: str,
            prepared: Any,
            bindings: Any,
            tenant: str,
            sleep: Any,
            mark: Any,
            checks: list[str],
        ) -> None:
            fake.add("container", "owned-container")
            mark(stage)
            if stage == "cleanup_failure":
                fake.resources["container", "owned-container"]["Labels"].pop(
                    tool._LABEL
                )
                raise tool.RehearsalRuntimeError("unexpected_flow_execution")
            if stage == "unexpected_exception":
                raise RuntimeError("private-exception-canary")
            if stage == "profile_drift":
                raise tool.ProfileError("INPUT_DRIFT", "private-exception-canary")
            raise tool.RehearsalRuntimeError(stage)

        def compose(*args: Any) -> str:
            return "compose-bin"

        def executable(*args: Any) -> str:
            return "docker-bin"

        monkeypatch.setattr(tool, "prepare", prepare)
        monkeypatch.setattr(tool, "source_identity", identity)
        monkeypatch.setattr(tool, "resolve_images", images)
        monkeypatch.setattr(tool, "render_runtime_recipe", render)
        monkeypatch.setattr(tool, "start_and_verify", start)
        monkeypatch.setattr(tool, "compose_executable", compose)
        monkeypatch.setattr(tool.shutil, "which", executable)
        report_directory = tmp_path / "runtime-report"
        code, report = tool.run_rehearsal(
            selection,
            report_directory,
            clock=lambda: 0.0,
            executor=fake,
            run_id_source=lambda: "a" * 32,
        )
        assert code == (2 if stage == "image_preflight" else 1)
        assert report["status"] == "FAIL_REHEARSAL"
        assert report["forecast_executed"] == (
            True if stage in {"unexpected_flow_execution", "cleanup_failure"} else None
        )
        if stage == "cleanup_failure":
            assert fake.resources
            assert report["cleanup_code"] == "cleanup_incomplete"
            assert report["cleanup_cause"] == "foreign_resource_refused"
            assert report["failure_code"] == "unexpected_flow_execution"
        else:
            assert not fake.resources
        assert not (report_directory / "secrets").exists()
        assert not (report_directory / "inputs").exists()
        assert not (report_directory / "recipe.json").exists()
        assert (report_directory / "recovery.json").exists()
        assert "private-exception-canary" not in json.dumps(report)
        if stage in {"unexpected_exception", "profile_drift"}:
            assert report["failure_code"] == (
                "unexpected_failure"
                if stage == "unexpected_exception"
                else "INPUT_DRIFT"
            )
        assert str(selection.profile.base_config) not in json.dumps(report)

    @pytest.mark.parametrize("worker_running", [True, False])
    def test_normal_startup_order_keeps_pools_paused_before_workers(
        self, monkeypatch: pytest.MonkeyPatch, worker_running: bool
    ) -> None:
        events: list[str] = []

        class Runtime:
            directory = Path("/private/synthetic")
            record = tool.Recovery(run_id="a" * 32, daemon="daemon")
            deadline = 800

            def clock(self) -> float:
                return 0.0

            def guard_daemon(self) -> Any:
                return self.record

            def command(self, args: list[str]) -> str:
                assert (
                    "--no-build" in args and args[args.index("--pull") + 1] == "never"
                )
                assert "--no-deps" in args
                events.append("up:" + args[-1])
                return ""

            def checkpoint(self) -> None:
                pass

            def service(self, name: str) -> str:
                return name

            def run(self, args: list[str], *, timeout: float = 120) -> str:
                if args[0] == "wait":
                    assert timeout == 800
                return "0"

            def inspect_owned(self, kind: str, identifier: str) -> dict[str, Any]:
                return {"State": {"Running": worker_running}}

            def inventory(self) -> dict[str, list[str]]:
                return {"container": []}

        def checked(runtime: Any, image: str, action: str) -> None:
            events.append(action)

        def healthy(runtime: Any, names: tuple[str, ...], sleep: Any) -> None:
            events.append("healthy:" + names[0])

        def database(*args: Any) -> None:
            events.append("database")

        monkeypatch.setattr(tool, "helper", checked)
        monkeypatch.setattr(tool, "wait_services", healthy)
        monkeypatch.setattr(tool, "verify_database", database)
        prepared = tool.Prepared(
            preview=tool.Preview(status="PLAN_READY", codes=()),
            recipe={"services": {"api": {"image": "default"}}},
        )
        checks: list[str] = []

        def sleep(seconds: float) -> None:
            pass

        if not worker_running:
            with pytest.raises(
                tool.RehearsalRuntimeError, match="isolated_startup_failed"
            ):
                tool.start_and_verify(
                    Runtime(),
                    "compose",
                    prepared,
                    tool.RecipeBindings(images=(("default", "image-id"),)),
                    "tenant",
                    sleep,
                    events.append,
                    checks,
                )
            return
        tool.start_and_verify(
            Runtime(),
            "compose",
            prepared,
            tool.RecipeBindings(images=(("default", "image-id"),)),
            "synthetic",
            sleep,
            events.append,
            checks,
        )
        assert events.index("prepare-pools") < events.index("up:init")
        assert (
            events.index("database")
            < events.index("pause-jobs")
            < events.index("up:prefect-worker-backup")
        )
        assert events[-3:] == ["workers", "api", "workers"]
        assert "no_execution" in checks


class TestPrivateDelivery:
    def test_private_input_becomes_readable_snapshot_without_changing_original(
        self, selected_recipe: tuple[Any, dict[str, Any]], tmp_path: Path
    ) -> None:
        selection, recipe = selected_recipe
        selection.profile.base_config.chmod(0o600)
        snapshots = tool.restrict_recipe(selection, recipe, ROOT)
        frozen = tool.capture(tuple(Path(path) for path in snapshots), ())
        prepared = tool.Prepared(
            preview=tool.Preview(status="PLAN_READY", codes=()),
            recipe=recipe,
            frozen=frozen,
            snapshots=tuple(snapshots.items()),
        )
        directory = tmp_path / "private"
        directory.mkdir(mode=0o700)
        runtime = tool.Runtime(directory=directory, deadline=1)
        bindings = tool.RecipeBindings(workspace=directory)
        tool.write_inputs(prepared, bindings, runtime)
        copied = (
            directory
            / "inputs"
            / snapshots[str(selection.profile.base_config.resolve())]
        )
        assert copied.read_bytes() == selection.profile.base_config.read_bytes()
        assert copied.stat().st_mode & 0o777 == 0o444
        assert selection.profile.base_config.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700
        for item in frozen:
            snapshot = directory / "inputs" / snapshots[str(item.path.resolve())]
            assert snapshot.stat().st_mode & 0o111 == item.mode & 0o111
        assert len(set(runtime.canaries)) == 5
        tool.erase_private_inputs(directory)
        assert selection.profile.base_config.exists()

    def test_interrupted_atomic_recovery_write_can_be_retried(
        self, tmp_path: Path
    ) -> None:
        runtime, _ = fake_runtime(tmp_path)
        temporary = tmp_path / "recovery.json.new"
        temporary.write_text('{"incomplete":')
        temporary.chmod(0o600)
        tool.save_recovery(tmp_path, runtime.record)
        assert tool.read_recovery(tmp_path) == runtime.record
        assert not temporary.exists()

    @pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM", "SIGHUP"])
    def test_catchable_signal_maps_to_cleanup_failure_path_and_restores_handlers(
        self,
        signal_name: str,
    ) -> None:
        import signal

        if not hasattr(signal, signal_name):
            pytest.skip("signal not available")
        signum = getattr(signal, signal_name)
        previous = signal.getsignal(signum)
        with tool.runtime_signals():
            handler = signal.getsignal(signum)
            assert callable(handler)
            with pytest.raises(tool.RehearsalRuntimeError, match="interrupted"):
                handler(signum, None)
        assert signal.getsignal(signum) == previous


def test_runtime_refusal_text_lists_all_actions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool.print_runtime_result(
        2, {"status": "REFUSED", "codes": ["SOURCE_DIRTY", "INPUT_DRIFT"]}, False, None
    )
    output = capsys.readouterr().out
    for code in ("SOURCE_DIRTY", "INPUT_DRIFT"):
        assert code + ": " + tool.action(code) in output


def test_api_helper_prefect_home_is_exec_confined() -> None:
    calls: list[list[str]] = []

    class Runtime:
        def guard_daemon(self) -> Any:
            return tool.Recovery(run_id="a" * 32, daemon="daemon")

        def service(self, name: str) -> str:
            assert name == "api"
            return "owned-api"

        def run(self, args: list[str]) -> str:
            calls.append(args)
            return '{"status": "ok", "stage": "api"}'

    tool.helper(Runtime(), "unused-image", "api")
    assert calls[0][:6] == [
        "exec",
        "-e",
        "SAPPHIRE_REHEARSAL_RUN_ID=" + "a" * 32,
        "-e",
        "PREFECT_HOME=/tmp/prefect",
        "owned-api",
    ]


@pytest.mark.parametrize("code", ["SOURCE_DIRTY", "SOURCE_IDENTITY_MISMATCH"])
def test_hosted_harness_retains_preflight_refusal_before_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    spec = importlib.util.spec_from_file_location(
        "rehearsal_acceptance_test",
        ROOT / "tests/integration/test_deployment_rehearsal.py",
    )
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    class Process:
        returncode = 2

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def poll(self) -> int:
            return 2

        def communicate(self, **kwargs: Any) -> tuple[str, str]:
            return json.dumps(
                {
                    "scope": "disposable_rehearsal",
                    "status": "REFUSED",
                    "codes": [code],
                    "private_unknown": "not-exported",
                }
            ), ""

    def popen(*args: Any, **kwargs: Any) -> Process:
        return Process()

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    evidence: dict[str, Any] = {}
    with pytest.raises(AssertionError, match="rehearsal did not pass"):
        harness.launch(
            tmp_path / "profile",
            tmp_path / "install",
            tmp_path / "report",
            fault=False,
            deadline=100,
            canaries=(),
            evidence=evidence,
        )
    assert evidence == {"status": "REFUSED", "codes": [code], "exit_code": 2}


def test_failed_operation_uses_fixed_label_without_private_arguments(
    tmp_path: Path,
) -> None:
    def fail(*args: Any) -> Any:
        return tool.CommandOutput(stdout="", stderr="private-canary", returncode=1)

    runtime = tool.Runtime(
        directory=tmp_path, deadline=100, clock=lambda: 0, executor=fail
    )
    with pytest.raises(tool.RehearsalRuntimeError, match="command_failed"):
        runtime.run(["exec", "private-container-name", "private-command"])
    assert runtime.failed_operation == "docker_exec"
