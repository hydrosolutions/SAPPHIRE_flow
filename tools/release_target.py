#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

# -I excludes the script directory. Resolve only this checkout's trusted tools.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_identity import (  # noqa: E402
    ReleaseIdentityError,
    Version,
    git,
    parse_release_target,
    read_release_target,
    release_target_blob,
    require_full_sha,
)

BOOTSTRAP_BASE_SHA = "259f16e1f32dec0379f4ce9d2175657f944a2dab"
BOOTSTRAP_TARGET = Version(0, 1, 1068)
TOOL_REQUIREMENT = "bump-my-version==1.6.0"
CHILD_ENV_KEYS = frozenset(
    {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}
)
SAFE_CONFIG = {
    "tool": {
        "bumpversion": {
            "commit": False,
            "tag": False,
            "files": [
                {
                    "filename": "RELEASE_TARGET",
                    "search": "{current_version}\n",
                    "replace": "{new_version}\n",
                }
            ],
        }
    }
}


def child_environment(environ: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in environ.items() if key in CHILD_ENV_KEYS}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_GRAFT_FILE": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def regular_bytes(path: Path) -> bytes:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ReleaseIdentityError(
                f"target/config must be a regular file: {path.name}"
            )
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseIdentityError(f"cannot read target/config: {path.name}") from exc


def validate_config(path: Path) -> bytes:
    raw = regular_bytes(path)
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ReleaseIdentityError("invalid bump config") from exc
    if config != SAFE_CONFIG or any(
        config["tool"]["bumpversion"][key] is not False for key in ("commit", "tag")
    ):
        raise ReleaseIdentityError(
            "unsafe bump config: only the single target rule is allowed"
        )
    return raw


def next_target(root: Path, base_sha: str) -> Version:
    raw = release_target_blob(base_sha, cwd=root)
    if raw is None:
        if base_sha != BOOTSTRAP_BASE_SHA:
            raise ReleaseIdentityError("base target missing outside exact bootstrap")
        return BOOTSTRAP_TARGET
    base = parse_release_target(raw)
    return Version(base.major, base.minor, base.patch + 1)


def tool_argv(uv: str, python: str, config: Path, current: Version) -> list[str]:
    return [
        uv,
        "tool",
        "run",
        "--no-config",
        "--no-env-file",
        "--isolated",
        "--python",
        python,
        "--no-python-downloads",
        "--default-index",
        "https://pypi.org/simple",
        "--keyring-provider",
        "disabled",
        "--from",
        TOOL_REQUIREMENT,
        "bump-my-version",
        "bump",
        "patch",
        "--config-file",
        str(config),
        "--current-version",
        current.text,
        "--no-commit",
        "--no-tag",
        "--allow-dirty",
    ]


def bump(root: Path, base_sha: str) -> None:
    root = root.resolve()
    target = root / "RELEASE_TARGET"
    config = root / ".bumpversion.toml"
    current = parse_release_target(regular_bytes(target))
    config_raw = validate_config(config)
    expected = next_target(root, base_sha)
    if current == expected:
        return
    if current != read_release_target(base_sha, cwd=root):
        raise ReleaseIdentityError("working target must equal base or its next patch")
    uv = shutil.which("uv")
    if uv is None:
        raise ReleaseIdentityError("uv is required for the isolated pinned bump tool")
    proc = subprocess.run(
        tool_argv(str(Path(uv).resolve()), sys.executable, config, current),
        cwd=root,
        env=child_environment(dict(os.environ)),
        check=False,
    )
    if proc.returncode:
        raise ReleaseIdentityError(f"isolated bump tool failed ({proc.returncode})")
    if (
        parse_release_target(regular_bytes(target)) != expected
        or regular_bytes(config) != config_raw
    ):
        raise ReleaseIdentityError("unexpected post-bump target/config")


def check_pr(root: Path, base_sha: str, head_sha: str, merge_sha: str) -> None:
    for sha in (base_sha, head_sha, merge_sha):
        require_full_sha(sha)
        if git(["cat-file", "-t", sha], cwd=root, harden=True) != "commit":
            raise ReleaseIdentityError("PR identity must name commit objects")
    if git(["rev-parse", "HEAD"], cwd=root, harden=True) != merge_sha:
        raise ReleaseIdentityError("HEAD must equal event merge SHA")
    parents = git(
        ["rev-list", "--parents", "-n", "1", merge_sha], cwd=root, harden=True
    ).split()
    if parents != [merge_sha, base_sha, head_sha]:
        raise ReleaseIdentityError(
            "event merge must have exactly base/head parents in order"
        )
    head = read_release_target(head_sha, cwd=root)
    if read_release_target(merge_sha, cwd=root) != head:
        raise ReleaseIdentityError("merge target differs from PR head target")
    if head != next_target(root, base_sha):
        raise ReleaseIdentityError(
            "PR target must advance tested base patch exactly once"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-PR release target; not package identity"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bump_parser = commands.add_parser("bump")
    bump_parser.add_argument("--base-sha", required=True)
    check = commands.add_parser("check")
    check.add_argument("--base-sha", required=True)
    check.add_argument("--head-sha", required=True)
    check.add_argument("--merge-sha", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "bump":
            bump(root, args.base_sha)
        else:
            check_pr(root, args.base_sha, args.head_sha, args.merge_sha)
    except (ReleaseIdentityError, OSError) as exc:
        parser.exit(1, f"release target: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
