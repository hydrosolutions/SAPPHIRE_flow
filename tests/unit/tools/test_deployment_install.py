from __future__ import annotations

import builtins
import copy
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
import yaml

from sapphire_flow.config.deployment_profile import (
    ProfileError,
    select_deployment_profile,
)
from sapphire_flow.exceptions import ConfigurationError
from tests.unit.config.profile_support import materialize_offline_fixture
from tools import deployment_install as tool

ROOT = Path(__file__).resolve().parents[3]


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(cwd),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("installation-source")
    files = command(
        [
            "git",
            "ls-files",
            "tools",
            "docker",
            "Caddyfile",
            "docker-compose.yml",
            "RELEASE_TARGET",
            "docs/deployment-quickstart.md",
        ],
        ROOT,
    ).stdout.splitlines()
    for name in (*files, "tools/deployment_install.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    template = root / "empty-template"
    template.mkdir()
    for args in (
        ["git", "init", "--template=" + str(template)],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "synthetic checkout",
        ],
    ):
        result = command(args, root)
        assert result.returncode == 0, result.stderr
    return root


def fixture(directory: Path, checkout: Path) -> tuple[Path, Path]:
    profile, installation = materialize_offline_fixture(directory)
    revision = command(["git", "rev-parse", "HEAD"], checkout).stdout.strip()
    release = (checkout / "RELEASE_TARGET").read_text().strip()
    for path in directory.iterdir():
        path.write_text(
            path.read_text()
            .replace(str(ROOT), str(checkout))
            .replace("0" * 40, revision)
            .replace('"1.2.3"', json.dumps(release))
            .replace('SAPPHIRE_DOMAIN = "example.invalid"', 'SAPPHIRE_DOMAIN = ":80"')
            .replace(
                'SAPPHIRE_CORS_ORIGINS = "https://example.invalid"',
                'SAPPHIRE_CORS_ORIGINS = ""',
            )
        )
    return profile, installation


def cli(
    directory: Path, checkout: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return command(
        [
            sys.executable,
            str(checkout / "tools/deployment_install.py"),
            "--profile-file",
            str(directory / "profile.toml"),
            "preview",
            "--installation",
            str(directory / "installation.toml"),
            "--state-dir",
            str(directory / "state"),
            "--format",
            "json",
            *extra,
        ],
        directory,
    )


class TestPreview:
    def test_real_external_preview_preserves_provenance_without_runtime(
        self,
        tmp_path: Path,
        checkout: Path,
    ) -> None:
        fixture(tmp_path, checkout)
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        result = cli(tmp_path, checkout, "--http-port", "18080")
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["status"] == "PLAN_READY"
        assert report["offline"]["summary"] == "PASS_OFFLINE"
        assert report["offline"]["provenance"]
        assert report["http_binding"] == "127.0.0.1:18080:80/tcp"
        assert report["launching"] == "not_available"
        assert report["proposed"]["services"] == 8
        assert report["proposed"]["runtime_credentials"] == 5
        assert all(
            report[key] is False
            for key in (
                "daemon_contacted",
                "credentials_read",
                "images_probed",
                "services_started",
                "executable_recipe",
            )
        )
        assert str(tmp_path) not in result.stdout
        assert str(checkout) not in result.stdout
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before

    @pytest.mark.parametrize(
        "change,code",
        [
            ("route", "LOCAL_ROUTE_REQUIRED"),
            ("cors", "LOCAL_ROUTE_REQUIRED"),
            ("identity", "SOURCE_IDENTITY_MISMATCH"),
            ("invalid_config", "CONFIG_READER_INVALID"),
            ("dynamic_config", "DYNAMIC_CONFIG_UNSUPPORTED"),
            ("alias", "PROTECTED_INPUT"),
            ("state", "STATE_PATH_OCCUPIED"),
            ("extension", "EXECUTION_EXTENSION_PENDING"),
        ],
    )
    def test_refuses_unsupported_inputs_without_disclosure(
        self,
        tmp_path: Path,
        checkout: Path,
        change: str,
        code: str,
    ) -> None:
        _, installation = fixture(tmp_path, checkout)
        if change in {"route", "cors", "identity", "alias"}:
            replacements = {
                "route": (
                    'SAPPHIRE_DOMAIN = ":80"',
                    'SAPPHIRE_DOMAIN = "PRIVATE_CANARY"',
                ),
                "cors": (
                    'SAPPHIRE_CORS_ORIGINS = ""',
                    'SAPPHIRE_CORS_ORIGINS = "https://private.invalid"',
                ),
                "identity": (
                    command(["git", "rev-parse", "HEAD"], checkout).stdout.strip(),
                    "f" * 40,
                ),
                "alias": (
                    str(tmp_path / "secret-delivery/db_password"),
                    str(tmp_path / "application.toml"),
                ),
            }
            installation.write_text(
                installation.read_text().replace(*replacements[change])
            )
        elif change in {"invalid_config", "dynamic_config"}:
            application = tmp_path / "application.toml"
            old, new = (
                ("max_retention_days = 365", 'max_retention_days = "PRIVATE_CANARY"')
                if change == "invalid_config"
                else ('"UTC"', '"${PRIVATE_CANARY}"')
            )
            application.write_text(application.read_text().replace(old, new))
        elif change == "state":
            (tmp_path / "state").mkdir()
        else:
            overlay = tmp_path / "compose.yml"
            overlay.write_text(
                overlay.read_text() + "\nx-private-extension: PRIVATE_CANARY\n"
            )
        result = cli(tmp_path, checkout)
        assert result.returncode == 2, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["status"] == "REFUSED"
        assert code in report["codes"]
        assert "PRIVATE_CANARY" not in result.stdout + result.stderr
        assert "private.invalid" not in result.stdout + result.stderr
        assert "Traceback" not in result.stderr

    def test_dirty_checkout_refuses(self, tmp_path: Path, checkout: Path) -> None:
        fixture(tmp_path, checkout)
        dirty = checkout / "untracked-private-change"
        dirty.write_text("private")
        try:
            result = cli(tmp_path, checkout)
            assert result.returncode == 2
            assert json.loads(result.stdout)["codes"] == ["SOURCE_DIRTY"]
        finally:
            dirty.unlink()

    @pytest.mark.parametrize(
        "mutation,code",
        [
            ("caddy_route", "ENVIRONMENT_FORWARDING_INVALID"),
            ("api_cors", "API_ROUTE_INVALID"),
            ("missing_caddy", "EXECUTION_EXTENSION_PENDING"),
        ],
    )
    def test_shared_checker_enforces_rendered_route_and_caddy(
        self,
        tmp_path: Path,
        checkout: Path,
        mutation: str,
        code: str,
    ) -> None:
        profile, installation = fixture(tmp_path, checkout)
        if mutation == "missing_caddy":
            base = yaml.safe_load((checkout / "docker-compose.yml").read_text())
            base["services"].pop("caddy")
            private_base = tmp_path / "base.yml"
            private_base.write_text(yaml.safe_dump(base))
            for path in (profile, installation):
                path.write_text(
                    path.read_text().replace(
                        str(checkout / "docker-compose.yml"), str(private_base)
                    )
                )
        overlay = yaml.safe_load((tmp_path / "compose.yml").read_text())
        if mutation == "caddy_route":
            overlay["services"]["caddy"]["environment"]["SAPPHIRE_DOMAIN"] = (
                "private.invalid"
            )
        elif mutation == "api_cors":
            overlay["services"]["api"]["environment"] = {
                "SAPPHIRE_CORS_ORIGINS": "https://private.invalid"
            }
        else:
            overlay["services"].pop("caddy")
        (tmp_path / "compose.yml").write_text(yaml.safe_dump(overlay))
        result = cli(tmp_path, checkout)
        assert result.returncode == 2, result.stdout + result.stderr
        assert code in json.loads(result.stdout)["codes"]
        assert "private.invalid" not in result.stdout + result.stderr

    @pytest.mark.parametrize("runtime_command", ["install", "verify", "stop", "start"])
    def test_runtime_command_position_is_not_implemented(
        self,
        tmp_path: Path,
        checkout: Path,
        runtime_command: str,
    ) -> None:
        result = command(
            [
                sys.executable,
                str(checkout / "tools/deployment_install.py"),
                runtime_command,
                "--format",
                "json",
            ],
            tmp_path,
        )
        assert result.returncode == 2
        assert json.loads(result.stdout)["codes"] == ["CLI_INPUT_INVALID"]

    @pytest.mark.parametrize(
        "extra",
        [
            ("--http-port", "0"),
            ("--http-port", "65536"),
            ("--state-dir", "relative"),
            ("install",),
            ("start",),
        ],
    )
    def test_invalid_cli_cannot_launch(
        self, tmp_path: Path, checkout: Path, extra: tuple[str, ...]
    ) -> None:
        fixture(tmp_path, checkout)
        result = cli(tmp_path, checkout, *extra)
        assert result.returncode == 2
        assert json.loads(result.stdout)["status"] == "REFUSED"
        assert not (tmp_path / "state").exists()

    def test_no_credential_file_is_opened(
        self, tmp_path: Path, checkout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile, installation = fixture(tmp_path, checkout)
        secret_dir = tmp_path / "secret-delivery"
        secret_dir.mkdir()
        names = (
            "db_password",
            "sapphire_api_db_password",
            "sapphire_worker_db_password",
            "sapphire_backup_db_password",
            "access_token_pepper",
        )
        paths = {secret_dir / name for name in names}
        for path in paths:
            path.write_text("PRIVATE_CREDENTIAL_CANARY")

        def guard(original: Any) -> Any:
            def guarded_open(path: Any, *args: Any, **kwargs: Any) -> Any:
                if isinstance(path, (str, os.PathLike)):
                    assert (
                        Path(cast("str | os.PathLike[str]", path)).resolve()
                        not in paths
                    ), "preview opened a credential"
                return original(path, *args, **kwargs)

            return guarded_open

        for module in (builtins, io, os):
            monkeypatch.setattr(module, "open", guard(module.open))
        monkeypatch.setattr(tool.rehearsal.checker, "ROOT", checkout)
        original_prepare = tool.prepare

        def prepare_at_checkout(
            selection: Any, layout: Any, *, identity_kind: Any
        ) -> Any:
            return original_prepare(
                selection, layout, identity_kind=identity_kind, root=checkout
            )

        monkeypatch.setattr(tool, "prepare", prepare_at_checkout)
        assert (
            tool.main(
                [
                    "--profile-file",
                    str(profile),
                    "preview",
                    "--installation",
                    str(installation),
                    "--state-dir",
                    str(tmp_path / "state"),
                    "--format",
                    "json",
                ]
            )
            == 0
        )

    @pytest.mark.parametrize(
        "failure,expected",
        [
            (KeyError("PRIVATE_CANARY"), 1),
            (ConfigurationError("PRIVATE_CANARY"), 2),
        ],
    )
    def test_distinguishes_tool_defects_from_invalid_configuration(
        self,
        tmp_path: Path,
        checkout: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: Exception,
        expected: int,
    ) -> None:
        profile, installation = fixture(tmp_path, checkout)

        def fail(*args: Any, **kwargs: Any) -> NoReturn:
            raise failure

        monkeypatch.setattr(tool, "prepare", fail)
        assert (
            tool.main(
                [
                    "preview",
                    "--profile-file",
                    str(profile),
                    "--installation",
                    str(installation),
                    "--state-dir",
                    str(tmp_path / "state"),
                    "--format",
                    "json",
                ]
            )
            == expected
        )
        output = capsys.readouterr()
        assert "PRIVATE_CANARY" not in output.out + output.err
        assert json.loads(output.out)["status"] == (
            "FAIL" if expected == 1 else "REFUSED"
        )


class TestLayout:
    @pytest.mark.parametrize(
        "kind", ["inside", "ancestor", "symlink", "root", "control"]
    )
    def test_refuses_unsafe_state_location(self, tmp_path: Path, kind: str) -> None:
        root = tmp_path / "source"
        root.mkdir()
        paths = {
            "inside": root / "state",
            "ancestor": tmp_path,
            "symlink": tmp_path / "alias",
            "root": Path("/"),
            "control": tmp_path / "bad\npath",
        }
        if kind == "symlink":
            paths[kind].symlink_to(tmp_path / "absent")
        with pytest.raises(ProfileError, match="profile-installation") as error:
            tool.check_layout(tool.InstallationLayout(state_dir=paths[kind]), root)
        assert error.value.code in {"STATE_PATH_INVALID", "STATE_PATH_OCCUPIED"}


class TestTransform:
    @pytest.fixture
    def prepared(
        self, tmp_path: Path, checkout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Any, ...]:
        monkeypatch.setattr(tool.rehearsal.checker, "ROOT", checkout)
        profile, installation = fixture(tmp_path, checkout)
        selection = select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        layout = tool.InstallationLayout(state_dir=tmp_path / "state")
        prepared = tool.prepare(selection, layout, root=checkout)
        assert prepared.recipe is not None
        snapshots = dict(prepared.snapshots)
        candidate = tool.transform_recipe(prepared.recipe, snapshots, selection, layout)
        return selection, layout, prepared.recipe, snapshots, candidate

    def test_preserves_persistent_contract(self, prepared: tuple[Any, ...]) -> None:
        selection, layout, original, snapshots, candidate = prepared
        tool.verify_transform(original, candidate, snapshots, selection, layout)
        assert candidate["name"] == original["name"]
        for name, service in original["services"].items():
            transformed = candidate["services"][name]
            assert transformed["restart"] == service["restart"]
            assert transformed.get("command") == service.get("command")
            assert transformed.get("secrets") == service.get("secrets")
            assert "build" not in transformed
        for name, network in original["networks"].items():
            assert candidate["networks"][name] == {
                **network,
                "labels": {tool.LABEL: tool.SYMBOL},
            }
        assert {
            name: value["file"] for name, value in candidate["secrets"].items()
        } == {name: str(path) for name, path in selection.installation.secret_bindings}
        assert not layout.state_dir.exists()
        ports = [
            (name, port)
            for name, service in candidate["services"].items()
            for port in service.get("ports", [])
        ]
        assert ports == [
            (
                "caddy",
                {
                    "target": 80,
                    "published": "8080",
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                },
            )
        ]
        assert candidate["services"]["caddy"]["environment"]["SAPPHIRE_DOMAIN"] == ":80"
        assert (
            candidate["services"]["api"]["environment"]["SAPPHIRE_CORS_ORIGINS"] == ""
        )
        for service in candidate["services"].values():
            for mount in service.get("volumes", []):
                if mount["type"] == "bind":
                    assert Path(mount["source"]).parent == layout.state_dir / "inputs"
                    assert mount["read_only"] is True
                    assert mount["bind"]["create_host_path"] is False

    @pytest.mark.parametrize(
        "mutation",
        [
            "port",
            "privileges",
            "command",
            "restart",
            "network",
            "secret",
            "mount",
            "image",
        ],
    )
    def test_rejects_transform_drift(
        self, prepared: tuple[Any, ...], mutation: str
    ) -> None:
        selection, layout, original, snapshots, candidate = prepared
        candidate = copy.deepcopy(candidate)
        match mutation:
            case "port":
                candidate["services"]["caddy"]["ports"][0]["host_ip"] = "0.0.0.0"
            case "privileges":
                candidate["services"]["api"]["privileged"] = True
            case "command":
                candidate["services"]["init"]["command"] = ["true"]
            case "restart":
                candidate["services"]["init"]["restart"] = "always"
            case "network":
                candidate["networks"]["frontend"]["internal"] = True
            case "secret":
                candidate["secrets"]["db_password"]["file"] = "/other"
            case "mount":
                candidate["services"]["api"]["volumes"] = []
            case "image":
                candidate["services"]["api"]["image"] = "other:latest"
            case _:
                raise AssertionError(mutation)
        with pytest.raises(ProfileError, match="profile-installation") as error:
            tool.verify_transform(original, candidate, snapshots, selection, layout)
        assert error.value.code == "TRANSFORM_INVALID"
