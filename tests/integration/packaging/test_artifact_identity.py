from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Literal

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UV_FIXTURE_ENV_BLOCKLIST = {
    "UV_FROZEN",
    "UV_LOCKED",
    "UV_NO_SYNC",
    "UV_OFFLINE",
    "UV_PROJECT_ENVIRONMENT",
    "VIRTUAL_ENV",
    "UV_PROJECT",
    "UV_WORKING_DIR",
}

_RESOURCES = [
    "sapphire_flow/py.typed",
    "sapphire_flow/data/icon_ch2_eps_grid.npz",
    "sapphire_flow/api/templates/base.html",
    "sapphire_flow/models/aquacast/configs/cmal_small.yaml",
]


def test_raw_archive_without_git_or_generated_metadata_fails_clearly(
    tmp_path: Path,
) -> None:
    package = tmp_path / "sapphire_flow"
    package.mkdir()
    (package / "__init__.py").write_text(
        (_REPO_ROOT / "src/sapphire_flow/__init__.py").read_text()
    )
    script = f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import sapphire_flow"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "version metadata is missing" in proc.stderr


def test_wheel_and_sdist_contain_and_load_resources(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    proc = subprocess.run(
        ["uv", "build", "--out-dir", str(dist), "--no-cache"],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    wheel = next(dist.glob("*.whl"))
    sdist = next(dist.glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for resource in [*_RESOURCES, "sapphire_flow/_version.py"]:
        assert resource in names
    with tarfile.open(sdist) as archive:
        sdist_names = {Path(name).as_posix() for name in archive.getnames()}
    for resource in _RESOURCES:
        assert any(name.endswith(resource) for name in sdist_names)

    venv = tmp_path / "wheel-env"
    assert subprocess.run(["uv", "venv", str(venv)], check=False).returncode == 0
    assert (
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin/python"),
                "--no-deps",
                str(wheel),
            ],
            text=True,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    script = f"""import importlib.resources as r, sapphire_flow
resources = {repr(_RESOURCES)}
assert sapphire_flow.__version__
for item in resources:
    relative = item.removeprefix('sapphire_flow/')
    assert r.files('sapphire_flow').joinpath(relative).is_file(), item
"""
    proc = subprocess.run([str(venv / "bin/python"), "-c", script], check=False)
    assert proc.returncode == 0


def test_build_inputs_are_python_312_and_docker_314_compatible() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text()
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
    assert 'requires-python = ">=3.12"' in pyproject
    expected_base = (
        "python:3.14.8-slim@sha256:"
        "a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170"
    )
    assert dockerfile.count(f"FROM {expected_base}") == 2


def _run_probe_uv(
    args: list[str], *, project: Path, tmp_path: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    target = Path(env["UV_PROJECT_ENVIRONMENT"]).resolve()
    assert project.resolve().is_relative_to(tmp_path.resolve()), "probe project escaped"
    assert target == (project / ".venv").resolve(), "unexpected probe environment"
    assert target.is_relative_to(tmp_path.resolve()), "probe environment escaped"
    assert target != Path(sys.prefix).resolve(), "probe targets running interpreter"
    for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        parent = os.environ.get(key)
        assert not parent or target != Path(parent).resolve(), "probe targets parent"
    assert not {"VIRTUAL_ENV", "UV_PROJECT", "UV_WORKING_DIR"}.intersection(env), (
        "conflicting probe selector"
    )
    return subprocess.run(
        ["uv", *args],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _installed_probe_versions(
    version: str, *, project: Path, tmp_path: Path, cache_dir: Path
) -> tuple[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _UV_FIXTURE_ENV_BLOCKLIST
    }
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": str(cache_dir),
            "UV_PROJECT_ENVIRONMENT": str((project / ".venv").resolve()),
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CACHE_PROBE": version,
        }
    )
    sync = _run_probe_uv(["sync"], project=project, tmp_path=tmp_path, env=env)
    assert sync.returncode == 0, sync.stderr
    proc = _run_probe_uv(
        [
            "run",
            "python",
            "-c",
            "import cache_probe, importlib.metadata as md; "
            "print(cache_probe.__version__); print(md.version('cache-probe'))",
        ],
        project=project,
        tmp_path=tmp_path,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    runtime_version, metadata_version = proc.stdout.strip().splitlines()
    return runtime_version, metadata_version


def test_native_uv_cache_key_changes_when_pretend_version_changes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "cache-probe"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """[project]
name = "cache-probe"
dynamic = ["version"]
requires-python = ">=3.12"
[build-system]
requires = [
    "setuptools==84.0.0",
    "setuptools-scm==10.3.4",
    "packaging==26.3",
    "vcs-versioning==2.5.0",
]
build-backend = "setuptools.build_meta"
[tool.uv]
cache-keys = [
    { file = "pyproject.toml" },
    { env = "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CACHE_PROBE" },
]
[tool.setuptools]
package-dir = {"" = "src"}
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools_scm]
version_file = "src/cache_probe/_version.py"
"""
    )
    package = project / "src" / "cache_probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from cache_probe._version import __version__\n"
    )
    cache_dir = tmp_path / "uv-cache"

    assert _installed_probe_versions(
        "1.2.3", project=project, tmp_path=tmp_path, cache_dir=cache_dir
    ) == ("1.2.3", "1.2.3")
    assert _installed_probe_versions(
        "1.2.4", project=project, tmp_path=tmp_path, cache_dir=cache_dir
    ) == ("1.2.4", "1.2.4")


@pytest.mark.parametrize("version", ["1.2.3", "1.2.4"])
def test_cache_probe_environment_is_disposable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: Literal["1.2.3", "1.2.4"]
) -> None:
    project = tmp_path / "probe"
    project.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    sentinel = parent / "sentinel"
    sentinel.write_bytes(b"parent unchanged")
    for key in (
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "UV_PROJECT",
        "UV_WORKING_DIR",
    ):
        monkeypatch.setenv(key, str(parent))
    monkeypatch.setenv("UV_PYTHON", "3.12")
    original = dict(os.environ)
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["UV_PROJECT_ENVIRONMENT"] == str((project / ".venv").resolve())
        assert not {"VIRTUAL_ENV", "UV_PROJECT", "UV_WORKING_DIR"}.intersection(env)
        assert env["UV_PYTHON"] == "3.12"
        assert kwargs["cwd"] == project
        calls.append(args)
        output = "" if args[1] == "sync" else f"{version}\n{version}\n"
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert _installed_probe_versions(
        version, project=project, tmp_path=tmp_path, cache_dir=tmp_path / "cache"
    ) == (version, version)
    assert [args[:2] for args in calls] == [["uv", "sync"], ["uv", "run"]]
    assert dict(os.environ) == original
    assert sentinel.read_bytes() == b"parent unchanged"


@pytest.mark.parametrize("command", ["sync", "run"])
@pytest.mark.parametrize(
    "conflict",
    ["outside", "interpreter", "parent-active", "parent-project", "selector"],
)
def test_cache_probe_rejects_unsafe_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: Literal["sync", "run"],
    conflict: Literal[
        "outside", "interpreter", "parent-active", "parent-project", "selector"
    ],
) -> None:
    project = tmp_path / "probe"
    target = project / ".venv"
    env = {"UV_PROJECT_ENVIRONMENT": str(target)}
    if conflict == "outside":
        env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path.parent / "outside")
    elif conflict == "interpreter":
        monkeypatch.setattr(sys, "prefix", str(target))
    elif conflict == "parent-active":
        monkeypatch.setenv("VIRTUAL_ENV", str(target))
    elif conflict == "parent-project":
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(target))
    else:
        env["UV_PROJECT"] = str(tmp_path / "parent")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unsafe target must not reach subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    original = dict(os.environ)
    with pytest.raises(AssertionError, match="probe"):
        _run_probe_uv([command], project=project, tmp_path=tmp_path, env=env)
    assert dict(os.environ) == original


def test_cache_probe_rejects_unsafe_target_between_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "probe"
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args == ["uv", "sync"]
        env = kwargs["env"]
        assert isinstance(env, dict)
        env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "parent")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AssertionError, match="unexpected probe environment"):
        _installed_probe_versions(
            "1.2.3", project=project, tmp_path=tmp_path, cache_dir=tmp_path / "cache"
        )
    assert calls == [["uv", "sync"]]
