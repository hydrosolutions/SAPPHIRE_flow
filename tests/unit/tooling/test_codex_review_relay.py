"""Behavioral checks for the Codex prompt relay."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RELAY = REPO_ROOT / "scripts" / "codex-review.sh"


def _fake_codex(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    executable = bin_dir / "codex"
    executable.write_text(
        """#!/bin/sh
touch "$CODEX_MARKER"
for arg do last=$arg; done
printf '%s\\n' "$last"
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, marker


def _run_relay(
    prompt: Path, bin_dir: Path, marker: Path, *extra_args: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CODEX_MARKER": str(marker),
    }
    return subprocess.run(
        [str(RELAY), str(prompt), *extra_args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_relay_passes_the_captured_prompt_once(tmp_path: Path) -> None:
    bin_dir, marker = _fake_codex(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("concise review", encoding="utf-8")

    result = _run_relay(prompt, bin_dir, marker)

    assert result.returncode == 0
    assert result.stdout == "concise review\n"
    assert marker.exists()


def test_relay_rejects_empty_or_cleared_prompt_before_codex(tmp_path: Path) -> None:
    bin_dir, marker = _fake_codex(tmp_path)

    for content in (" \n\t", "  temporary prompt cleared.\n"):
        prompt = tmp_path / "prompt.md"
        prompt.write_text(content, encoding="utf-8")
        marker.unlink(missing_ok=True)

        result = _run_relay(prompt, bin_dir, marker)

        assert result.returncode != 0
        assert "NO usable prompt" in result.stderr
        assert not marker.exists()


def test_relay_rejects_extra_codex_arguments_before_invocation(tmp_path: Path) -> None:
    bin_dir, marker = _fake_codex(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("concise review", encoding="utf-8")

    result = _run_relay(
        prompt, bin_dir, marker, "--dangerously-bypass-approvals-and-sandbox"
    )

    assert result.returncode == 64
    assert "usage:" in result.stderr
    assert not marker.exists()


def test_relay_source_reads_prompt_once() -> None:
    source = RELAY.read_text(encoding="utf-8")

    assert 'prompt=$(<"$prompt_file")' in source
    assert '$(cat "$prompt_file")' not in source
    assert '"$@"' not in source
