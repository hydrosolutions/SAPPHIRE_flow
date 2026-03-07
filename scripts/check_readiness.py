#!/usr/bin/env python3
"""Check whether a plan or design doc is ready for implementation.

Verifies frontmatter status, review round count, and latest round results.

Exit codes:
    0: Document has status: READY and passes all checks.
    1: Document is not ready (reason printed to stderr).
"""

import re
import sys
from pathlib import Path


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter fields from text between --- delimiters."""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = line.split(":", 1)
        if len(kv) == 2:
            fields[kv[0].strip()] = kv[1].strip()
    return fields


def find_review_history_section(text: str) -> str | None:
    """Return the content of the ## Review History section, or None."""
    pattern = re.compile(r"^## Review History\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1)


def parse_review_table(section: str) -> list[dict[str, str]]:
    """Parse a markdown table from the Review History section.

    Expects a header row, a separator row, then data rows.
    Returns a list of dicts keyed by lowercase, stripped header names.
    """
    lines = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return []

    header_line = lines[0]
    # lines[1] is the separator (e.g. |---|---|)
    data_lines = lines[2:]

    headers = [h.strip().lower() for h in header_line.strip("|").split("|")]

    rows: list[dict[str, str]] = []
    for line in data_lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def extract_blocking_count(value: str) -> int | None:
    """Extract an integer from a blocking column value.

    Handles plain integers and strings like '0 blocking' or '2'.
    """
    match = re.search(r"\d+", value)
    if match:
        return int(match.group())
    return None


def check_readiness(path: Path) -> tuple[bool, str, dict[str, str]]:
    """Run all readiness checks on the document at path.

    Returns (is_ready, reason, report_fields).
    report_fields contains keys: status, rounds, blocking, latest_status, verdict, reason.
    """
    if not path.is_file():
        return False, f"File not found: {path}", {
            "status": "N/A",
            "rounds": "N/A",
            "blocking": "N/A",
            "latest_status": "N/A",
        }

    text = path.read_text(encoding="utf-8")

    # --- Frontmatter status ---
    frontmatter = parse_frontmatter(text)
    fm_status = frontmatter.get("status", "")
    if not fm_status:
        return False, "No 'status' field in YAML frontmatter", {
            "status": "MISSING",
            "rounds": "N/A",
            "blocking": "N/A",
            "latest_status": "N/A",
        }

    report: dict[str, str] = {"status": fm_status}

    # --- Review History section ---
    section = find_review_history_section(text)
    if section is None:
        report.update({"rounds": "0", "blocking": "N/A", "latest_status": "N/A"})
        return False, "No '## Review History' section found", report

    rows = parse_review_table(section)
    num_rounds = len(rows)
    report["rounds"] = str(num_rounds)

    if num_rounds == 0:
        report.update({"blocking": "N/A", "latest_status": "N/A"})
        return False, "Review History table has no data rows", report

    latest = rows[-1]

    # --- Blocking count ---
    blocking_col = latest.get("blocking", latest.get("blocking findings", ""))
    if not blocking_col:
        # Try to find any column with "block" in the name
        for key, val in latest.items():
            if "block" in key:
                blocking_col = val
                break

    blocking_count = extract_blocking_count(blocking_col) if blocking_col else None
    report["blocking"] = str(blocking_count) if blocking_count is not None else "PARSE_ERROR"

    # --- Latest status ---
    status_col = latest.get("status", latest.get("outcome", ""))
    if not status_col:
        for key, val in latest.items():
            if "status" in key or "outcome" in key:
                status_col = val
                break
    report["latest_status"] = status_col if status_col else "MISSING"

    # --- Evaluate ---
    if fm_status != "READY":
        return False, f"Frontmatter status is '{fm_status}', expected 'READY'", report

    if num_rounds < 2:
        return False, f"Only {num_rounds} review round(s) completed, minimum 2 required", report

    if blocking_count is None:
        return False, "Could not parse blocking count from latest review round", report

    if blocking_count > 0:
        return False, f"{blocking_count} blocking finding(s) remain in latest round", report

    if status_col != "user-confirmed":
        return False, f"Latest round status is '{status_col}', expected 'user-confirmed'", report

    return True, "All checks pass", report


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: check_readiness.py <path-to-document>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1]).resolve()
    is_ready, reason, report = check_readiness(path)

    verdict = "READY" if is_ready else f"NOT READY — {reason}"

    print(f"Readiness check: {path}")
    print(f"  Frontmatter status: {report.get('status', 'N/A')}")
    print(f"  Review rounds: {report.get('rounds', 'N/A')} (minimum 2)")
    print(f"  Latest blocking: {report.get('blocking', 'N/A')}")
    print(f"  Latest status: {report.get('latest_status', 'N/A')}")
    print(f"  Verdict: {verdict}")

    if not is_ready:
        print(f"Error: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
