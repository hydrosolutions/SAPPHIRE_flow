#!/usr/bin/env python3
"""Check whether a plan is ready for implementation.

Exit codes:
    0: The document has YAML frontmatter with status exactly READY.
    1: The document is not ready (reason printed to stderr).
"""

import json
import re
import sys
from pathlib import Path

MANIFEST_MAX_BYTES = 8192
_DIAGNOSTIC_LIMIT = 5
_DIAGNOSTIC_MAX_CHARS = 240
_CANONICAL_STATUSES = {
    "DRAFT",
    "READY",
    "BLOCKED",
    "DEFERRED",
    "PARTIAL",
    "SUPERSEDED",
    "COMPLETE",
}
_TASK_HEADING = re.compile(
    r"^###\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s+(?:—|-)\s+.+$", re.MULTILINE
)
_FIELD_HEADING = re.compile(
    r"^\*\*(Outcome|In|Out|Pre-change|Verification):\*\*\s*", re.MULTILINE
)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract fields from a leading YAML frontmatter block."""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace():
            continue
        key_value = line.split(":", 1)
        if len(key_value) == 2:
            key = key_value[0]
            if key in fields:
                fields[key] = "__INVALID_DUPLICATE__"
            else:
                fields[key] = key_value[1].strip()
    return fields


def check_readiness(path: Path) -> tuple[bool, str, str]:
    """Return readiness, reason, and parsed status for a plan document."""
    if not path.is_file():
        return False, f"File not found: {path}", "N/A"

    frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"))
    status = frontmatter.get("status", "")
    if not status:
        return False, "No 'status' field in YAML frontmatter", "MISSING"
    if status != "READY":
        return False, f"Frontmatter status is '{status}', expected 'READY'", status
    return True, "YAML frontmatter status is READY", status


def fingerprint(text: str) -> str:
    """Return the shared FNV-1a fingerprint over UTF-8 bytes."""
    value = 0x811C9DC5
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def _bounded_diagnostics(items: list[str]) -> list[str]:
    return [item[:_DIAGNOSTIC_MAX_CHARS] for item in items[:_DIAGNOSTIC_LIMIT]]


def _section(text: str, heading: str, diagnostics: list[str]) -> str:
    matches = list(re.finditer(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE))
    if len(matches) != 1:
        diagnostics.append(f"expected exactly one '## {heading}' section")
        return ""
    tail = text[matches[0].end() :]
    next_heading = re.search(r"^##\s+", tail, re.MULTILINE)
    return tail[: next_heading.start()] if next_heading else tail


def _fields(block: str) -> tuple[dict[str, str], set[str]]:
    matches = list(_FIELD_HEADING.finditer(block))
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        name = match.group(1)
        if name in fields:
            duplicates.add(name)
        fields[name] = block[match.end() : end].strip()
    return fields, duplicates


def _task_manifest(section: str, diagnostics: list[str]) -> list[dict[str, str]]:
    headings = list(_TASK_HEADING.finditer(section))
    if not headings:
        diagnostics.append("Tasks section contains no canonical task headings")
        return []

    tasks: list[dict[str, str]] = []
    task_ids: set[str] = set()
    for index, heading in enumerate(headings):
        task_id = heading.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        fields, duplicates = _fields(section[heading.end() : end])
        required = ("Outcome", "In", "Out", "Pre-change", "Verification")
        missing = [name for name in required if not fields.get(name, "").strip()]
        duplicate_required = sorted(duplicates.intersection(required))
        if task_id in task_ids:
            diagnostics.append(f"duplicate task ID: {task_id}")
        if missing:
            diagnostics.append(f"task {task_id} missing: {', '.join(missing)}")
        if duplicate_required:
            diagnostics.append(
                f"task {task_id} duplicate fields: {', '.join(duplicate_required)}"
            )
        task_ids.add(task_id)
        pre_change = fields.get("Pre-change", "").strip()
        verification = fields.get("Verification", "").strip()
        tasks.append(
            {
                "id": task_id,
                "preChangeMode": "N/A"
                if re.match(r"^N/A\b", pre_change, re.IGNORECASE)
                else "executable",
                "preChangeFingerprint": fingerprint(pre_change),
                "verificationFingerprint": fingerprint(verification),
            }
        )
    return tasks


def _gate_manifest(section: str, diagnostics: list[str]) -> list[dict[str, str]]:
    fences = list(re.finditer(r"```(?:bash|sh)\s*\n(.*?)```", section, re.DOTALL))
    if len(fences) != 1:
        diagnostics.append("Exit gates must contain exactly one bash/sh command fence")
        return []
    fence = fences[0]
    commands = [
        line.strip()
        for line in fence.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not commands or any(command.endswith("\\") for command in commands):
        diagnostics.append("Exit gates must contain complete one-line commands")
        return []
    return [
        {"id": f"G{index}", "fingerprint": fingerprint(command)}
        for index, command in enumerate(commands, start=1)
    ]


def serialize_manifest(manifest: dict[str, object]) -> str:
    """Serialize a manifest deterministically without presentation whitespace."""
    return json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def inspect_plan(path: Path) -> tuple[bool, dict[str, object]]:
    """Return structural validity and a bounded identity-only plan manifest."""
    if not path.is_file():
        return False, {
            "status": "N/A",
            "documentFingerprint": fingerprint(""),
            "tasks": [],
            "exitGates": [],
            "valid": False,
            "diagnostics": [f"File not found: {path}"[:_DIAGNOSTIC_MAX_CHARS]],
        }

    text = path.read_text(encoding="utf-8")
    diagnostics: list[str] = []
    parsed_status = parse_frontmatter(text).get("status", "MISSING")
    status = parsed_status if len(parsed_status) <= 64 else "__INVALID__"
    if parsed_status not in _CANONICAL_STATUSES:
        diagnostics.append(f"invalid or missing canonical status: {parsed_status}")
    tasks = _task_manifest(_section(text, "Tasks", diagnostics), diagnostics)
    gates = _gate_manifest(_section(text, "Exit gates", diagnostics), diagnostics)
    manifest: dict[str, object] = {
        "status": status,
        "documentFingerprint": fingerprint(text),
        "tasks": tasks,
        "exitGates": gates,
        "valid": not diagnostics,
        "diagnostics": _bounded_diagnostics(diagnostics),
    }
    size = len(serialize_manifest(manifest).encode("utf-8"))
    if size > MANIFEST_MAX_BYTES:
        manifest = {
            "status": status,
            "documentFingerprint": fingerprint(text),
            "tasks": [],
            "exitGates": [],
            "valid": False,
            "diagnostics": [
                f"manifest exceeds {MANIFEST_MAX_BYTES} bytes ({size} bytes)"
            ],
        }
    return manifest["valid"] is True, manifest


def main() -> None:
    inspect_json = len(sys.argv) == 3 and sys.argv[1] == "--inspect-json"
    if not inspect_json and len(sys.argv) != 2:
        print(
            "Usage: check_readiness.py [--inspect-json] <path-to-document>",
            file=sys.stderr,
        )
        sys.exit(1)

    path = Path(sys.argv[2] if inspect_json else sys.argv[1]).resolve()
    if inspect_json:
        is_valid, manifest = inspect_plan(path)
        print(serialize_manifest(manifest))
        if not is_valid:
            sys.exit(1)
        return

    is_ready, reason, status = check_readiness(path)
    verdict = "READY" if is_ready else f"NOT READY — {reason}"

    print(f"Readiness check: {path}")
    print(f"  Frontmatter status: {status}")
    print(f"  Verdict: {verdict}")

    if not is_ready:
        print(f"Error: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
