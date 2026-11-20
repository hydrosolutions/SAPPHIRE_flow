#!/usr/bin/env python3
"""Install an identified wheel outside the mutable application checkout."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(args: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, text=True, capture_output=True, timeout=600
    ).stdout.strip()


def switch_link(root: Path, name: str, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=".link-", dir=root) as temporary:
        link = Path(temporary) / name
        link.symlink_to(target)
        link.replace(root / name)


def smoke(environment: Path) -> dict[str, str]:
    # -I ignores PYTHONPATH, user site packages and the current directory.
    code = (
        "import json, pathlib, sys, importlib.metadata; import sapphire_flow; "
        "from sapphire_flow.ops import watchdog; "
        "assert pathlib.Path(sapphire_flow.__file__).is_relative_to(sys.prefix); "
        "assert sapphire_flow.__version__ == "
        "importlib.metadata.version('sapphire-flow'); "
        "print(json.dumps({'version': sapphire_flow.__version__, "
        "'package_path': sapphire_flow.__file__}))"
    )
    python = str(environment / "bin/python")
    with tempfile.TemporaryDirectory(prefix="sapphire-monitor-smoke-") as outside:
        result = json.loads(run([python, "-I", "-c", code], cwd=Path(outside)))
        run(
            [python, "-I", "-m", "sapphire_flow.ops.watchdog", "--help"],
            cwd=Path(outside),
        )
    return result


def install(source: Path, root: Path, *, uv: str, python: str) -> Path:
    source = source.resolve(strict=True)
    root = root.resolve()
    if root.is_relative_to(source):
        raise ValueError("Monitor installation must be outside the source checkout")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source):
        raise ValueError("Build source must be a clean, committed checkout")
    revision = run(["git", "rev-parse", "HEAD"], cwd=source)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o022:
        raise ValueError(
            "Monitor root must be owned by this user and not group/world writable"
        )
    for name in ("current", "previous"):
        if (root / name).exists() and not (root / name).is_symlink():
            raise ValueError(f"Refusing to replace non-symlink {root / name}")
        if (root / name).is_symlink() and not (root / name).resolve().is_relative_to(
            root
        ):
            raise ValueError(
                f"Monitor link must remain inside installation root: {name}"
            )
    with (root / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return install_locked(source, root, revision=revision, uv=uv, python=python)


def install_locked(
    source: Path, root: Path, *, revision: str, uv: str, python: str
) -> Path:
    # Virtual environments must be created at their final path: their scripts
    # embed it. Keep failed candidates for diagnosis; never activate them.
    environment = Path(tempfile.mkdtemp(prefix=f"{revision[:12]}-", dir=root))
    artifacts = environment / "artifacts"
    artifacts.mkdir()
    run([uv, "build", "--wheel", "--out-dir", str(artifacts)], cwd=source)
    wheels = list(artifacts.glob("sapphire_flow-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Build must produce exactly one SAPPHIRE Flow wheel")
    wheel = wheels[0]
    requirements = artifacts / "host-monitor.txt"
    run(
        [
            uv,
            "export",
            "--frozen",
            "--only-group",
            "host-monitor",
            "--no-emit-project",
            "--output-file",
            str(requirements),
        ],
        cwd=source,
    )
    run(
        [uv, "venv", "--allow-existing", "--python", python, str(environment)], cwd=root
    )
    interpreter = str(environment / "bin/python")
    run(
        [
            uv,
            "pip",
            "install",
            "--no-config",
            "--python",
            interpreter,
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        cwd=root,
    )
    run(
        [
            uv,
            "pip",
            "install",
            "--no-config",
            "--python",
            interpreter,
            "--no-deps",
            str(wheel),
        ],
        cwd=root,
    )
    # Detect source changes during the build before activating its result.
    if revision != run(["git", "rev-parse", "HEAD"], cwd=source) or run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source
    ):
        raise ValueError("Build source changed during installation")
    identity = smoke(environment)
    with wheel.open("rb") as wheel_file:
        wheel_hash = hashlib.file_digest(wheel_file, "sha256").hexdigest()
    manifest = {
        **identity,
        "source_revision": revision,
        "wheel_sha256": wheel_hash,
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
    }
    (environment / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix=".launcher-", dir=root) as temporary:
        launcher = Path(temporary) / "run-monitor"
        launcher.write_bytes((source / "scripts/run_host_monitor.sh").read_bytes())
        launcher.chmod(0o755)
        launcher.replace(root / "run-monitor")
    if (root / "current").is_symlink():
        switch_link(root, "previous", (root / "current").resolve(strict=True))
    switch_link(root, "current", environment)
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Clean release checkout"
    )
    parser.add_argument(
        "--root", type=Path, required=True, help="Private host-monitor install root"
    )
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    try:
        environment = install(args.source, args.root, uv=args.uv, python=args.python)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Do not dump subprocess output: package tooling can include credentials.
        sys.stderr.write(
            f"Host monitor installation failed: {type(exc).__name__}: {exc}\n"
        )
        return 1
    sys.stdout.write(json.dumps({"installed_environment": str(environment)}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
