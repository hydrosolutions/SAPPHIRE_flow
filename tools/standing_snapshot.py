"""Collect the facts a standing review needs, and flag claimed-vs-measured drift.

Mechanical only: no judgement, no ranking. It answers "what does the repo claim,
and what is actually true" — the delta is the signal. The verdict is authored on
top of this by the `standing` skill.

    uv run python tools/standing_snapshot.py                 # human summary
    uv run python tools/standing_snapshot.py --out snap.json # + machine-readable
    uv run python tools/standing_snapshot.py --no-live       # skip the staging host
    uv run python tools/standing_snapshot.py --tests         # also run the fast suite
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

REPO = Path(__file__).resolve().parent.parent
PLANS_DIR = REPO / "docs" / "plans"
ARCHIVE_DIR = PLANS_DIR / "archive"

DEFAULT_HOST = "sapphire@192.168.1.136"
DEFAULT_HEALTH_URL = "http://192.168.1.136:8000/api/v1/health"
COMPOSE = "-f docker-compose.yml -f docker-compose.macmini.yml"

# Statuses that mean "this plan is finished" — anything else is live work.
DONE_STATUSES = frozenset({"MERGED", "COMPLETE", "DONE", "ARCHIVED", "SUPERSEDED"})

Severity = Literal["info", "warn", "critical"]
SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True, kw_only=True, slots=True)
class Finding:
    """One claimed-vs-measured discrepancy, stated so a human can verify it."""

    check: str
    severity: Severity
    subject: str
    detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class Plan:
    number: str
    path: Path
    status: str

    @property
    def head_status(self) -> str:
        return self.status.split()[0].upper().strip("(),:.") if self.status else "NONE"

    @property
    def is_done(self) -> bool:
        return self.head_status in DONE_STATUSES


@dataclass(kw_only=True, slots=True)
class Snapshot:
    repo: dict[str, Any] = field(default_factory=dict)
    plans: dict[str, Any] = field(default_factory=dict)
    github: dict[str, Any] = field(default_factory=dict)
    live: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


def run(cmd: list[str], *, timeout: int = 60) -> str:
    """Run a command, returning stdout; empty string on any failure."""
    try:
        proc = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


# --------------------------------------------------------------------------- repo


def collect_repo() -> dict[str, Any]:
    version = ""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if match:
        version = match.group(1)

    migrations = sorted((REPO / "alembic" / "versions").glob("*.py"))
    down_revisions: set[str] = set()
    revisions: dict[str, str] = {}
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision(?::\s*str)?\s*=\s*"([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision[^=]*=\s*"([^"]+)"', text, re.MULTILINE)
        if rev:
            revisions[rev.group(1)] = path.name
        if down:
            down_revisions.add(down.group(1))
    heads = sorted(set(revisions) - down_revisions)

    worktrees = [
        line.split()[1]
        for line in run(["git", "worktree", "list"]).splitlines()
        if len(line.split()) > 1
    ]
    return {
        "head": run(["git", "rev-parse", "--short", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "version": version,
        "dirty": bool(run(["git", "status", "--porcelain"])),
        "alembic_heads": heads,
        "migration_count": len(migrations),
        "worktrees": worktrees,
    }


# -------------------------------------------------------------------------- plans


_STATUS_PATTERNS = (
    re.compile(r"^status:\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\*\*Status\*\*:?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.IGNORECASE),
)


def _status_from_lines(lines: list[str]) -> str:
    for line in lines[:40]:
        for pattern in _STATUS_PATTERNS:
            hit = pattern.match(line.strip())
            if hit:
                return hit.group(1).strip().strip("*")[:70]
    return ""


def _plan_number(name: str) -> str | None:
    match = re.match(r"^(\d+)([a-z]?)-", name)
    return match.group(1) + match.group(2) if match else None


def _parse_plan(path: Path) -> Plan | None:
    number = _plan_number(path.name)
    if not number:
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return Plan(number=number, path=path, status=_status_from_lines(lines) or "NONE")


def _main_subjects() -> list[str]:
    for ref in ("origin/main", "main"):
        log = run(["git", "log", "--format=%s", ref], timeout=120)
        if log:
            return log.splitlines()
    return []


def _shipping_commits(number: str, subjects: list[str]) -> list[str]:
    """Subjects that look like they shipped this plan's CODE (not its plan doc)."""
    bare = number.rstrip("abcdefghijklmnopqrstuvwxyz")
    patterns = (
        re.compile(rf"\bPlan\s+0*{bare}\b", re.IGNORECASE),
        re.compile(rf"\(0*{bare}\)"),
    )
    hits: list[str] = []
    for subject in subjects:
        if subject.lower().startswith("docs("):
            continue  # plan-doc commits are not evidence of shipped code
        if any(p.search(subject) for p in patterns):
            hits.append(subject)
    return hits


def collect_plans(findings: list[Finding]) -> dict[str, Any]:
    active = [p for p in map(_parse_plan, sorted(PLANS_DIR.glob("*.md"))) if p]
    archived = [p for p in map(_parse_plan, sorted(ARCHIVE_DIR.glob("*.md"))) if p]

    by_status: dict[str, list[str]] = defaultdict(list)
    for plan in active:
        by_status[plan.head_status].append(plan.number)

    _check_duplicate_numbers(active + archived, findings)
    shipped = _check_shipped_but_open(active, findings)
    stranded = _check_stranded(active + archived, findings)

    return {
        "active_count": len(active),
        "archived_count": len(archived),
        "by_status": {k: sorted(v) for k, v in sorted(by_status.items())},
        "shipped_but_open": shipped,
        "stranded": stranded,
    }


def _check_duplicate_numbers(plans: list[Plan], findings: list[Finding]) -> None:
    """D1 — the same plan number claimed by two files."""
    seen: dict[str, list[str]] = defaultdict(list)
    for plan in plans:
        seen[plan.number].append(str(plan.path.relative_to(REPO)))
    for number, paths in sorted(seen.items()):
        if len(paths) > 1:
            findings.append(
                Finding(
                    check="duplicate-plan-number",
                    severity="warn",
                    subject=f"Plan {number}",
                    detail="claimed by " + " and ".join(paths),
                )
            )


def _check_shipped_but_open(
    active: list[Plan], findings: list[Finding]
) -> list[dict[str, str]]:
    """D2 — code merged on main while the plan still sits in the active index.

    Reported as ONE aggregate finding: this repo's convention is that a plan
    keeps ``READY`` until someone moves it to ``archive/``, so per-plan rows
    would be noise. The signal is the size of the un-archived backlog — an
    index that lists shipped work as pending mis-ranks any planning round.
    Per-plan detail stays in the JSON for follow-up.
    """
    subjects = _main_subjects()
    rows: list[dict[str, str]] = []
    for plan in active:
        if plan.is_done:
            continue
        merged = [
            hit
            for hit in _shipping_commits(plan.number, subjects)
            if re.search(r"\(#\d+\)$", hit)
        ]
        if merged:
            rows.append(
                {"plan": plan.number, "status": plan.status, "commit": merged[0]}
            )

    if rows:
        numbers = ", ".join(r["plan"] for r in rows)
        findings.append(
            Finding(
                check="shipped-but-unarchived",
                severity="warn" if len(rows) > 5 else "info",
                subject=f"{len(rows)} plans",
                detail=(
                    f"listed as active but main carries their merged code — {numbers}. "
                    "Heuristic (commit-subject match); confirm before archiving."
                ),
            )
        )
    return rows


def _check_stranded(known: list[Plan], findings: list[Finding]) -> list[dict[str, Any]]:
    """D3 — a plan whose NUMBER exists nowhere on main, only on a branch.

    Compares by number, not path: a plan that main has since moved into
    ``archive/`` is landed, not stranded.
    """
    numbers_on_main = {p.number for p in known}
    merged_branches = {
        b.strip().lstrip("* ")
        for b in run(["git", "branch", "-a", "--merged", "main"]).splitlines()
    }
    branches = [
        b.strip()
        for b in run(["git", "branch", "-a", "--format=%(refname:short)"]).splitlines()
        if b.strip() and b.strip() not in {"main", "origin/main", "origin/HEAD"}
    ]

    best: dict[str, dict[str, Any]] = {}
    for branch in branches:
        if branch in merged_branches:
            continue
        files = run(
            ["git", "ls-tree", "-r", "--name-only", branch, "--", "docs/plans"]
        ).splitlines()
        for path in files:
            name = Path(path).name
            number = _plan_number(name)
            if not number or number in numbers_on_main:
                continue
            behind_raw = run(["git", "rev-list", "--count", f"{branch}..main"])
            behind = int(behind_raw) if behind_raw.isdigit() else 10**6
            blob = run(["git", "show", f"{branch}:{path}"], timeout=20)
            status = _status_from_lines(blob.splitlines()) or "NONE"
            prior = best.get(number)
            if prior is None or behind < prior["behind_main"]:
                best[number] = {
                    "plan": number,
                    "path": path,
                    "branch": branch,
                    "behind_main": behind,
                    "status": status,
                }

    stranded = sorted(best.values(), key=lambda e: -e["behind_main"])
    for entry in stranded:
        ready = entry["status"].upper().startswith("READY")
        findings.append(
            Finding(
                check="stranded-plan",
                severity="critical" if ready else "info",
                subject=f"Plan {entry['plan']}",
                detail=(
                    f"{entry['status']} on `{entry['branch']}`, "
                    f"{entry['behind_main']} commits behind main, absent from main"
                ),
            )
        )
    return stranded


# ------------------------------------------------------------------------- github


def collect_github(findings: list[Finding]) -> dict[str, Any]:
    runs_raw = run(
        [
            "gh",
            "run",
            "list",
            "--branch",
            "main",
            "--limit",
            "10",
            "--json",
            "conclusion,status,displayTitle,createdAt",
        ],
        timeout=60,
    )
    prs_raw = run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "50",
            "--json",
            "number,title,author,isDraft,headRefName,statusCheckRollup",
        ],
        timeout=90,
    )
    if not runs_raw and not prs_raw:
        return {"available": False}

    runs: list[dict[str, Any]] = json.loads(runs_raw) if runs_raw else []
    prs: list[dict[str, Any]] = json.loads(prs_raw) if prs_raw else []

    failed = [r for r in runs if r.get("conclusion") == "failure"]
    if failed:
        findings.append(
            Finding(
                check="ci-red-on-main",
                severity="critical",
                subject="CI",
                detail=(
                    f"{len(failed)} of the last {len(runs)} main runs failed; "
                    f"newest: {failed[0]['displayTitle']}"
                ),
            )
        )

    pr_rows: list[dict[str, Any]] = []
    for pr in prs:
        checks = pr.get("statusCheckRollup") or []
        states = [c.get("conclusion") or c.get("state") or "" for c in checks]
        red = sum(1 for s in states if s in {"FAILURE", "TIMED_OUT", "ERROR"})
        author = (pr.get("author") or {}).get("login", "?")
        pr_rows.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "author": author,
                "draft": pr.get("isDraft", False),
                "branch": pr.get("headRefName", ""),
                "checks_failing": red,
                "checks_total": len(states),
            }
        )
        if red:
            findings.append(
                Finding(
                    check="pr-ci-failing",
                    severity="warn",
                    subject=f"PR #{pr['number']} ({author})",
                    detail=f"{red}/{len(states)} checks failing — {pr['title']}",
                )
            )

    return {
        "available": True,
        "main_runs": [
            {
                "title": r["displayTitle"],
                "conclusion": r.get("conclusion") or r.get("status"),
            }
            for r in runs[:5]
        ],
        "open_prs": pr_rows,
    }


# --------------------------------------------------------------------------- live

_COUNTER_SQL = """
select 'stations', count(*)::text from stations
union all select 'observations', count(*)::text from observations
union all select 'observations_latest',
  coalesce(max(\\"timestamp\\")::text, '-') from observations
union all select 'weather_forecast_rows', count(*)::text from weather_forecasts
union all select 'weather_forecast_cycle',
  coalesce(max(cycle_time)::text, '-') from weather_forecasts
union all select 'forecasts', count(*)::text from forecasts
union all select 'forecasts_latest',
  coalesce(max(issued_at)::text, '-') from forecasts
union all select 'hindcasts', count(*)::text from hindcast_forecasts
union all select 'skill_scores', count(*)::text from skill_scores
union all select 'model_artifacts', count(*)::text from model_artifacts
union all select 'historical_forcing', count(*)::text from historical_forcing
union all select 'station_thresholds', count(*)::text from station_thresholds
union all select 'alerts', count(*)::text from alerts
union all select 'gateway_bindings',
  count(*)::text from recap_gateway_polygon_bindings
union all select 'access_tokens', count(*)::text from access_tokens
union all select 'audit_log', count(*)::text from audit_log
"""

_HEALTH_SQL = """
select check_type || '=' || status || '@' || max(checked_at)::text
from pipeline_health group by check_type, status order by 1
"""

# Capabilities shipped in code whose production row count proves they have
# never actually been exercised.
_UNEXERCISED = {
    "station_thresholds": "alerting is wired but no threshold is configured",
    "alerts": "no alert has ever been produced in production",
    "gateway_bindings": "no station is bound to the recap Gateway",
}


def _psql(host: str, sql: str) -> str:
    remote = (
        "export PATH=/usr/local/bin:$PATH; "
        "export DOCKER_HOST=unix:///var/run/docker.sock; "
        f"cd ~/SAPPHIRE_flow && docker compose {COMPOSE} exec -T postgres "
        f'psql -U sapphire -d sapphire -At -F"|" -c "{sql}"'
    )
    return run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, remote],
        timeout=90,
    )


def collect_live(host: str, health_url: str, findings: list[Finding]) -> dict[str, Any]:
    api = "unreachable"
    try:
        with urllib.request.urlopen(health_url, timeout=8) as response:  # noqa: S310
            api = json.loads(response.read().decode()).get("status", "?")
    except (urllib.error.URLError, OSError, ValueError):
        api = "unreachable"

    if api == "unreachable":
        findings.append(
            Finding(
                check="host-unreachable",
                severity="critical",
                subject="staging host",
                detail=(
                    f"{health_url} did not answer — verify LAN reachability from "
                    "THIS machine before diagnosing the host"
                ),
            )
        )

    counters: dict[str, str] = {}
    for line in _psql(host, " ".join(_COUNTER_SQL.split())).splitlines():
        if "|" in line:
            key, _, value = line.partition("|")
            counters[key.strip()] = value.strip()

    checks = [
        line for line in _psql(host, " ".join(_HEALTH_SQL.split())).splitlines() if line
    ]

    for key, message in _UNEXERCISED.items():
        if counters.get(key) == "0":
            findings.append(
                Finding(
                    check="shipped-never-exercised",
                    severity="critical",
                    subject=key,
                    detail=message,
                )
            )

    return {"api_health": api, "counters": counters, "pipeline_health": checks}


# -------------------------------------------------------------------------- tests


def collect_tests(scratch: Path) -> dict[str, Any]:
    scratch.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["uv", "run", "pytest", "-q", "--timeout=300", "-p", "no:randomly"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=2400,
        check=False,
        env={**os.environ, "PREFECT_HOME": str(scratch / "prefect")},
    )
    summary = ""
    for line in reversed(proc.stdout.splitlines()):
        if any(token in line for token in (" passed", " failed", " error")):
            summary = line.strip()
            break
    if proc.returncode != 0:
        summary = f"FAILING — {summary}"
    return {"exit_code": proc.returncode, "summary": summary}


# ------------------------------------------------------------------------- report


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 74)


def emit(snapshot: Snapshot) -> None:
    repo = snapshot.repo
    _rule("REPO")
    print(
        f"  {repo['branch']} @ {repo['head']}   v{repo['version']}   "
        f"alembic {','.join(repo['alembic_heads']) or '?'}   "
        f"{'DIRTY' if repo['dirty'] else 'clean'}   "
        f"{len(repo['worktrees'])} worktrees"
    )

    plans = snapshot.plans
    _rule("PLANS")
    print(f"  {plans['active_count']} active, {plans['archived_count']} archived")
    for status, numbers in plans["by_status"].items():
        listing = " ".join(numbers[:12])
        more = " …" if len(numbers) > 12 else ""
        print(f"    {status:<14} {len(numbers):>3}  {listing}{more}")

    gh = snapshot.github
    if gh.get("available"):
        _rule("GITHUB")
        for entry in gh["main_runs"]:
            print(f"  main   {entry['conclusion']:<12} {entry['title'][:56]}")
        for pr in gh["open_prs"]:
            flag = (
                f"{pr['checks_failing']}/{pr['checks_total']} FAILING"
                if pr["checks_failing"]
                else "checks ok"
            )
            title = pr["title"][:46]
            print(f"  #{pr['number']:<5} {pr['author']:<16} {flag:<14} {title}")

    live = snapshot.live
    if live:
        _rule("LIVE HOST")
        print(f"  api: {live['api_health']}")
        for key, value in live["counters"].items():
            print(f"    {key:<24} {value}")
        for check in live["pipeline_health"]:
            print(f"    health  {check}")

    if snapshot.tests:
        _rule("TESTS")
        print(f"  {snapshot.tests['summary'] or 'no summary parsed'}")

    _rule("DRIFT — claimed vs measured")
    if not snapshot.findings:
        print("  none")
    for finding in sorted(
        snapshot.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.check)
    ):
        colour = {"critical": "\033[31m", "warn": "\033[33m", "info": "\033[2m"}[
            finding.severity
        ]
        print(
            f"  {colour}{finding.severity.upper():<8}\033[0m "
            f"{finding.check:<24} {finding.subject}"
        )
        print(f"           └─ {finding.detail}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the snapshot as JSON here")
    parser.add_argument("--no-live", action="store_true", help="skip the staging host")
    parser.add_argument("--no-github", action="store_true", help="skip the gh queries")
    parser.add_argument("--tests", action="store_true", help="run the fast suite too")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument(
        "--scratch",
        type=Path,
        default=Path("/tmp/sapphire-standing"),
        help="scratch dir for an isolated PREFECT_HOME when --tests is used",
    )
    args = parser.parse_args()

    snapshot = Snapshot()
    snapshot.repo = collect_repo()
    snapshot.plans = collect_plans(snapshot.findings)
    if not args.no_github:
        snapshot.github = collect_github(snapshot.findings)
    if not args.no_live:
        snapshot.live = collect_live(args.host, args.health_url, snapshot.findings)
    if args.tests:
        snapshot.tests = collect_tests(args.scratch)

    emit(snapshot)

    if args.out:
        payload = {
            "repo": snapshot.repo,
            "plans": snapshot.plans,
            "github": snapshot.github,
            "live": snapshot.live,
            "tests": snapshot.tests,
            "findings": [
                {
                    "check": f.check,
                    "severity": f.severity,
                    "subject": f.subject,
                    "detail": f.detail,
                }
                for f in snapshot.findings
            ],
        }
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"snapshot → {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
