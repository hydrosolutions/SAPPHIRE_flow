"""Gate-parity audit: compare CI `run:` steps against `uv run check`'s coverage.

Read-only check; exits 0 on no drift, 1 on drift. Run manually before
merging workflow changes:

    uv run python tools/gate_parity_check.py

Classification logic (applied in order, first match wins):
  covered-by-check     — run cmd starts with `uv run ruff format --check`
                         or `uv run ruff check`
  covered-by-uv-sync   — run cmd starts with `uv sync`
  allowlisted-ci-only  — (workflow-stem, step-name) pair is in CI_ONLY_ALLOWLIST
  drift                — nothing matched; needs triage
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Allowlist of (workflow-stem, step-name) pairs that are legitimately CI-only.
# Values are one-line reasons. Update when adding new CI-only steps.
# Note: steps classified as covered-by-check or covered-by-uv-sync never
# reach this allowlist; only steps with other run commands need entries here.
CI_ONLY_ALLOWLIST: dict[tuple[str, str], str] = {
    # ── ci.yml / lint ────────────────────────────────────────────────────────
    # (uv sync --frozen is covered-by-uv-sync; ruff steps are covered-by-check)
    # ── ci.yml / unit ────────────────────────────────────────────────────────
    ("ci", "Install system deps for cfgrib / rioxarray / exactextract"): (
        "system-package install (libeccodes0 libexpat1 libgeos-c1v5), "
        "not project-managed"
    ),
    ("ci", "<unnamed step in unit>"): (
        "uv run pytest tests/unit/: covered manually via 'uv run pytest tests/unit'; "
        "not gated by uv run check by design (requires system deps)"
    ),
    # ── ci.yml / wheel-only-guard ────────────────────────────────────────────
    # (uv sync --frozen --no-build ... is covered-by-uv-sync)
    # ── ci.yml / integration ─────────────────────────────────────────────────
    ("ci", "<unnamed step in integration>"): (
        "uv run pytest tests/integration/: requires postgres service + system deps; "
        "run locally as 'uv run pytest tests/integration/ -v -m not slow'"
    ),
    # ── dependency-safety.yml (Plan 119) ─────────────────────────────────────
    # (uv sync --frozen is covered-by-uv-sync)
    ("dependency-safety", "Classify dependency-bump risk"): (
        "requires a PR base SHA (${{ github.event.pull_request.base.sha }}); "
        "run locally as 'uv run python tools/dependency_safety.py "
        "--base-ref <base-sha>' against any base commit"
    ),
    # ── integration-nightly.yml ──────────────────────────────────────────────
    (
        "integration-nightly",
        "Install system deps for cfgrib / rioxarray / exactextract",
    ): (
        "system-package install (libeccodes0 libexpat1 libgeos-c1v5), "
        "not project-managed"
    ),
    ("integration-nightly", "<unnamed step in integration-nightly>"): (
        "uv run pytest slow/live tests: live external APIs + extended timeout; "
        "run locally as 'uv run pytest tests/integration/ -v -m slow' or "
        "'uv run pytest tests/integration/live -v --override-ini addopts='"
    ),
    # ── live-lindas-weekly.yml ───────────────────────────────────────────────
    # (uv sync --frozen under step name "Install dependencies" is covered-by-uv-sync)
    ("live-lindas-weekly", "Run live LINDAS schema check"): (
        "uv run pytest -m live_lindas -v: live BAFU LINDAS API call; "
        "run locally as 'uv run pytest -m live_lindas -v' "
        "(requires network + BAFU LINDAS up)"
    ),
    # ── live-lindas-weekly-autoretry.yml ────────────────────────────────────
    ("live-lindas-weekly-autoretry", "Cap retries at 12 per day"): (
        "automation: count today's dispatches via gh API"
    ),
    ("live-lindas-weekly-autoretry", "Wait 5 minutes for BAFU LINDAS to recover"): (
        "automation: bounded sleep awaiting upstream recovery"
    ),
    ("live-lindas-weekly-autoretry", "Re-dispatch live-lindas-weekly.yml"): (
        "automation: re-dispatch parent workflow"
    ),
}


_WORKFLOW_DIR = Path(".github/workflows")
_CHECK_PREFIXES = (
    "uv run ruff format --check",
    "uv run ruff check",
)
_SYNC_PREFIX = "uv sync"


def _normalize(cmd: str) -> str:
    """Collapse whitespace and strip surrounding noise."""
    return " ".join(cmd.split())


def _classify(workflow: str, step_name: str, run_cmd: str) -> str:
    cmd = _normalize(run_cmd)
    if any(cmd.startswith(p) for p in _CHECK_PREFIXES):
        return "covered-by-check"
    if cmd.startswith(_SYNC_PREFIX):
        return "covered-by-uv-sync"
    if (workflow, step_name) in CI_ONLY_ALLOWLIST:
        return "allowlisted-ci-only"
    return "drift"


def _collect_run_steps(path: Path) -> list[tuple[str, str, str, str]]:
    """Return (workflow, job, step-name, run-cmd) tuples for every `run:` step.

    Skips `uses:` steps (action invocations), since C3 v0 only audits shell steps.
    """
    data = yaml.safe_load(path.read_text())
    workflow_name = path.stem
    out: list[tuple[str, str, str, str]] = []
    for job_name, job_def in (data.get("jobs") or {}).items():
        for step in job_def.get("steps") or []:
            run_cmd = step.get("run")
            if run_cmd is None:
                continue
            step_name = step.get("name") or f"<unnamed step in {job_name}>"
            out.append((workflow_name, job_name, step_name, run_cmd))
    return out


def main() -> int:
    if not _WORKFLOW_DIR.is_dir():
        print(f"error: {_WORKFLOW_DIR} not found", file=sys.stderr)
        return 2

    rows: list[tuple[str, str, str, str, str]] = []
    for wf in sorted(_WORKFLOW_DIR.glob("*.yml")):
        for workflow, job, step, cmd in _collect_run_steps(wf):
            status = _classify(workflow, step, cmd)
            rows.append((workflow, job, step, _normalize(cmd), status))

    # Print a human-readable table.
    print(f"{'workflow':22} {'job':24} {'step':40} {'status':22} cmd")
    print("-" * 140)
    for workflow, job, step, cmd, status in rows:
        cmd_display = cmd if len(cmd) <= 60 else cmd[:57] + "..."
        print(f"{workflow:22} {job:24} {step:40} {status:22} {cmd_display}")

    drift = [r for r in rows if r[-1] == "drift"]
    if drift:
        print(
            f"\n{len(drift)} drift row(s) — "
            "populate CI_ONLY_ALLOWLIST or wire into uv run check."
        )
        return 1
    print("\nNo drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
