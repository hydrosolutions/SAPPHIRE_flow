#!/usr/bin/env python3
"""Capture pyright error counts as a shrink-only ratchet baseline.

Runs ``uv run pyright --outputjson src/``, reduces the report to per-file
error counts keyed by repo-root-relative POSIX paths, and writes
``tools/pyright_baseline.json`` as ``{"total": N, "by_file": {...}}``.

The reduction + path-normalization helpers here are imported by
``tools/pyright_ratchet.py`` so the CI gate keys live counts the exact
same way the baseline was captured (pyright emits absolute paths that
differ between local and CI runners).

Regenerate the baseline after a deliberate ratchet update::

    uv run python tools/pyright_baseline.py
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "tools" / "pyright_baseline.json"


def normalize_path(raw: str) -> str:
    """Return ``raw`` as a repo-root-relative POSIX path.

    Pyright emits absolute paths (``/Users/...`` locally,
    ``/home/runner/work/...`` in CI); stripping the repo-root prefix makes
    the stored keys machine-stable so baseline keys match live keys.
    """
    try:
        rel = Path(raw).resolve().relative_to(REPO_ROOT)
    except ValueError:
        rel = Path(raw)
    return rel.as_posix()


def reduce_diagnostics(report: dict) -> dict[str, object]:
    """Reduce a parsed pyright ``--outputjson`` report to ``{total, by_file}``.

    Counts only ``severity == "error"`` diagnostics (warnings/info excluded;
    this also naturally excludes the ``flows/`` carved-out Unknown rules set
    to ``"none"``). Only files with >=1 error appear in ``by_file``.

    Raises ``ValueError`` if the report is not a pyright JSON object (no
    ``generalDiagnostics`` list) so callers can treat that as MALFORMED.
    """
    if not isinstance(report, dict):
        raise ValueError("pyright report is not a JSON object")
    diags = report.get("generalDiagnostics")
    if not isinstance(diags, list):
        raise ValueError("pyright report missing 'generalDiagnostics' list")
    by_file: dict[str, int] = {}
    for diag in diags:
        if not isinstance(diag, dict) or diag.get("severity") != "error":
            continue
        key = normalize_path(str(diag.get("file", "")))
        by_file[key] = by_file.get(key, 0) + 1
    total = sum(by_file.values())
    return {"total": total, "by_file": dict(sorted(by_file.items()))}


def run_pyright() -> dict:
    """Run pyright in JSON mode and return the parsed report.

    Uses ``check=False`` because pyright exits non-zero whenever it finds
    errors (always true at baseline-capture time).
    """
    proc = subprocess.run(
        ["uv", "run", "pyright", "--outputjson", "src/"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(proc.stdout[:200] + "\n")
        raise SystemExit(f"pyright did not emit valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise SystemExit("pyright JSON top-level is not an object")
    return report


def main() -> None:
    baseline = reduce_diagnostics(run_pyright())
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    by_file = baseline["by_file"]
    n_files = len(by_file) if isinstance(by_file, dict) else 0
    print(
        f"wrote {BASELINE_PATH.relative_to(REPO_ROOT)} — "
        f"total {baseline['total']} errors across {n_files} files"
    )


if __name__ == "__main__":
    main()
