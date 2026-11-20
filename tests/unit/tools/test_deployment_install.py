from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from sapphire_flow.config.deployment_profile import (
    ProfileError,
    select_deployment_profile,
)
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
        selection = select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        assert selection.installation
        paths = {path for _, path in selection.installation.secret_bindings}
        original = Path.open

        def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            assert path not in paths, "preview opened a credential"
            return cast("Any", original(path, *args, **kwargs))

        monkeypatch.setattr(Path, "open", guarded_open)
        monkeypatch.setattr(tool.rehearsal.checker, "ROOT", checkout)
        prepared = tool.prepare(
            selection,
            tool.InstallationLayout(state_dir=tmp_path / "state"),
            root=checkout,
        )
        assert prepared.preview.status == "PLAN_READY"


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
