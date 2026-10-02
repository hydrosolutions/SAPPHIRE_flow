from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UV_FIXTURE_ENV_BLOCKLIST = {"UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC", "UV_OFFLINE"}

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
    assert "python:3.14.6-slim" in dockerfile


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

    def installed_versions(version: str) -> tuple[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in _UV_FIXTURE_ENV_BLOCKLIST
        }
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "UV_CACHE_DIR": str(cache_dir),
                "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CACHE_PROBE": version,
            }
        )
        sync = subprocess.run(
            ["uv", "sync"],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert sync.returncode == 0, sync.stderr
        proc = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "import cache_probe, importlib.metadata as md; "
                "print(cache_probe.__version__); print(md.version('cache-probe'))",
            ],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        runtime_version, metadata_version = proc.stdout.strip().splitlines()
        return runtime_version, metadata_version

    assert installed_versions("1.2.3") == ("1.2.3", "1.2.3")
    assert installed_versions("1.2.4") == ("1.2.4", "1.2.4")
