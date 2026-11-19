from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import selectors
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts/install_host_monitor.py"
_SPEC = importlib.util.spec_from_file_location("install_host_monitor", _SCRIPT)
assert _SPEC and _SPEC.loader
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


@pytest.fixture
def install_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "scripts").mkdir()
    (source / "scripts/run_host_monitor.sh").write_bytes(
        (_SCRIPT.parent / "run_host_monitor.sh").read_bytes()
    )
    root = tmp_path / "monitor"

    def run(args: list[str], *, cwd: Path) -> str:
        if args[:2] == ["git", "rev-parse"]:
            return "a" * 40
        if args[:2] == ["uv", "build"]:
            (Path(args[-1]) / "sapphire_flow-1.0-py3-none-any.whl").write_bytes(
                b"wheel"
            )
        if args[:2] == ["uv", "export"]:
            Path(args[-1]).write_text("httpx==0.28.1\n")
        return ""

    monkeypatch.setattr(installer, "run", run)
    monkeypatch.setattr(installer, "smoke", lambda path: {"version": "1.0"})
    return source, root


def test_install_preserves_previous_environment_and_records_identity(
    install_paths,
) -> None:
    source, root = install_paths
    old = installer.install(source, root, uv="uv", python="python3")
    new = installer.install(source, root, uv="uv", python="python3")
    assert (root / "current").resolve() == new
    assert (root / "previous").resolve() == old
    assert old.is_dir()
    manifest = json.loads((new / "manifest.json").read_text())
    assert manifest["source_revision"] == "a" * 40
    assert manifest["wheel_sha256"] == hashlib.sha256(b"wheel").hexdigest()


def test_failed_smoke_never_replaces_working_environment(
    install_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, root = install_paths
    old = installer.install(source, root, uv="uv", python="python3")

    def fail(path: Path) -> dict[str, str]:
        raise subprocess.CalledProcessError(1, "import watchdog")

    monkeypatch.setattr(installer, "smoke", fail)
    with pytest.raises(subprocess.CalledProcessError, match="import watchdog"):
        installer.install(source, root, uv="uv", python="python3")
    assert (root / "current").resolve() == old
    assert not (root / "previous").exists()


def test_dirty_checkout_is_rejected_before_install(
    install_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, root = install_paths
    monkeypatch.setattr(installer, "run", lambda *args, **kwargs: " M pyproject.toml")
    with pytest.raises(ValueError, match="clean, committed"):
        installer.install(source, root, uv="uv", python="python3")
    assert not root.exists()


def test_source_change_during_build_prevents_activation(
    install_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, root = install_paths
    original = installer.run
    calls = 0

    def run(args: list[str], *, cwd: Path) -> str:
        nonlocal calls
        if args[:2] == ["git", "rev-parse"]:
            calls += 1
            return ("a" if calls == 1 else "b") * 40
        return original(args, cwd=cwd)

    monkeypatch.setattr(installer, "run", run)
    with pytest.raises(ValueError, match="source changed"):
        installer.install(source, root, uv="uv", python="python3")
    assert not (root / "current").exists()


def test_install_rejects_environment_inside_source(
    install_paths: tuple[Path, Path],
) -> None:
    source, _ = install_paths
    with pytest.raises(ValueError, match="outside the source"):
        installer.install(source, source / "monitor", uv="uv", python="python3")


def test_install_does_not_replace_existing_directory(
    install_paths: tuple[Path, Path],
) -> None:
    source, root = install_paths
    (root / "current").mkdir(parents=True)
    with pytest.raises(ValueError, match="non-symlink"):
        installer.install(source, root, uv="uv", python="python3")


def test_install_rejects_group_writable_root(install_paths: tuple[Path, Path]) -> None:
    source, root = install_paths
    root.mkdir(mode=0o770)
    root.chmod(0o770)
    with pytest.raises(ValueError, match="not group/world writable"):
        installer.install(source, root, uv="uv", python="python3")


def test_concurrent_install_does_not_change_current(
    install_paths: tuple[Path, Path],
) -> None:
    source, root = install_paths
    current = installer.install(source, root, uv="uv", python="python3")
    with (root / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            installer.install(source, root, uv="uv", python="python3")
    assert (root / "current").resolve() == current


def test_scheduler_uses_installed_launcher() -> None:
    import plistlib

    root = _SCRIPT.parent / "launchd"
    plist = plistlib.loads(
        (root / "ch.hydrosolutions.sapphire-watchdog.plist").read_bytes()
    )
    args = plist["ProgramArguments"]
    assert args[0] == "/Users/sapphire/.local/share/sapphire-monitor/run-monitor"


def test_active_tick_keeps_old_imports_after_activation(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "Repository tooling requires uv"
    root = tmp_path / "monitor"
    root.mkdir()
    launcher = root / "run-monitor"
    launcher.write_bytes((_SCRIPT.parent / "run_host_monitor.sh").read_bytes())
    launcher.chmod(0o755)
    for release in ("old", "new"):
        environment = root / release
        subprocess.run(
            [uv, "venv", "--no-config", "--python", sys.executable, str(environment)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        site = (
            environment
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        package = site / "sapphire_flow"
        ops = package / "ops"
        ops.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (ops / "__init__.py").write_text("")
        (site / "release_marker.py").write_text(f"RELEASE = {release!r}\n")
        (ops / "watchdog.py").write_text(
            "import sys\n"
            "print(sys.prefix, flush=True)\n"
            "sys.stdin.readline()\n"
            "import release_marker\n"
            "print(release_marker.RELEASE, flush=True)\n"
        )
    installer.switch_link(root, "current", root / "old")
    process = subprocess.Popen(
        [str(launcher)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "Old tick did not start"
        assert (
            Path(process.stdout.readline().strip()).resolve()
            == (root / "old").resolve()
        )
        installer.switch_link(root, "current", root / "new")
        output, error = process.communicate("\n", timeout=10)
        assert process.returncode == 0, error
        assert output.strip() == "old"
        following = subprocess.run(
            [str(launcher)],
            input="\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        prefix, marker = following.stdout.splitlines()
        assert Path(prefix).resolve() == (root / "new").resolve()
        assert marker == "new"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_watchdog_help_is_a_safe_startup_probe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sapphire_flow.ops.watchdog import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "% of that volume" in capsys.readouterr().out
