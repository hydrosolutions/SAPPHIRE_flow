#!/usr/bin/env python3
"""CI ratchet gate: fail if pyright error counts exceed the baseline.

Usage::

    uv run python tools/pyright_ratchet.py <live_pyright.json> <baseline.json>

``<live_pyright.json>`` is a raw ``pyright --outputjson src/`` dump;
``<baseline.json>`` is the reduced ``tools/pyright_baseline.json``.

Exit codes:
  0  every per-file count and the total are <= baseline   -> pass
  1  some file (or the total) exceeds its baseline         -> ratchet violation
  2  the live pyright JSON is malformed / unreadable        -> MALFORMED

The live dump is reduced with the SAME helpers the baseline was captured
with (``tools/pyright_baseline.py``), so the normalized keys match on any
machine. A crashed/empty/non-JSON pyright run is MALFORMED (exit 2) and is
NEVER read as "0 errors, pass".
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pyright_baseline import reduce_diagnostics  # noqa: E402


def load_live(path: str) -> dict[str, object]:
    """Parse + reduce a raw pyright --outputjson dump. Exit 2 if malformed."""
    file = Path(path)
    raw = file.read_text() if file.exists() else ""
    if not raw.strip():
        sys.stderr.write(f"MALFORMED: live pyright JSON '{path}' is empty/0-byte\n")
        raise SystemExit(2)
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write(
            f"MALFORMED: live pyright JSON '{path}' is not valid JSON; "
            f"first 200 chars:\n{raw[:200]}\n"
        )
        raise SystemExit(2) from None
    try:
        return reduce_diagnostics(report)
    except ValueError as exc:
        sys.stderr.write(f"MALFORMED: {exc}; first 200 chars:\n{raw[:200]}\n")
        raise SystemExit(2) from None


def load_baseline(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text())


def main() -> None:
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: pyright_ratchet.py <live_pyright.json> <baseline.json>\n"
        )
        raise SystemExit(2)

    live = load_live(sys.argv[1])
    baseline = load_baseline(sys.argv[2])

    base_files: dict[str, int] = baseline.get("by_file", {})  # type: ignore[assignment]
    live_files: dict[str, int] = live["by_file"]  # type: ignore[assignment]

    # New file in live but not baseline -> baseline 0 (new files must start clean).
    # File in baseline but absent/0 in live -> improvement -> silently passes.
    violations = [
        (f, base_files.get(f, 0), n)
        for f, n in sorted(live_files.items())
        if n > base_files.get(f, 0)
    ]
    total_base = int(baseline.get("total", 0))  # type: ignore[call-overload]
    total_live = int(live["total"])  # type: ignore[call-overload]
    total_violation = total_live > total_base

    if not violations and not total_violation:
        print(f"pyright ratchet OK — total {total_live} <= baseline {total_base}")
        return

    print("pyright ratchet FAILED — error counts rose above the baseline:")
    for f, was, now in violations:
        print(f"  {f}: was {was}, now {now}  (+{now - was})")
    if total_violation:
        delta = total_live - total_base
        print(f"  TOTAL: was {total_base}, now {total_live}  (+{delta})")
    print(
        "\nFix the new errors, or regenerate the baseline deliberately with "
        "`uv run python tools/pyright_baseline.py`."
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
