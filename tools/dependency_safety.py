#!/usr/bin/env python3
"""Plan 119 dependency-bump safety gate — BLOCK/REVIEW/ALLOW classifier.

Diffs a fixed watched-file set (docker-compose.yml, Dockerfile, pyproject.toml,
uv.lock, .github/workflows/ci.yml) against the PR base commit and classifies
the change:

  BLOCK  — a stateful-service image major bump (any docker-compose.yml
           `image:` with a `volumes:` mount whose parsed version increased),
           a Dockerfile base-image change (CPython risk axis = MINOR, not
           semver-major), or any `requires-python` change in pyproject.toml.
  REVIEW — FI/recap git-pin or wheel-guard machinery changes, a MAJOR bump of
           a native/compiled-extension runtime dep (cfgrib, rioxarray,
           exactextract, forecastinterface), or a postgis-major confined to
           ci.yml's ephemeral `services:` container.
  ALLOW  — everything else (patch/minor of a normal library, action patch
           bumps, dev-dependency patches) — silent, no finding emitted.

CRITICAL: version fields are parsed from the machine-readable YAML `image:`
value (before `@sha256:...`), never from the trailing `# name:tag` comment —
Dependabot does not keep that comment in sync (see docs/plans/119).

Usage::

    uv run python tools/dependency_safety.py --base-ref <sha>

Exit codes: 0 = pass (ALLOW/REVIEW, or unresolved BLOCK cleared by the
committed `.dependency-safety-allowlist`), 1 = unresolved BLOCK.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class Verdict(Enum):
    ALLOW = auto()
    REVIEW = auto()
    BLOCK = auto()


_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.ALLOW: 0,
    Verdict.REVIEW: 1,
    Verdict.BLOCK: 2,
}


@dataclass(frozen=True, kw_only=True, slots=True)
class Finding:
    verdict: Verdict
    file: str
    key: str
    message: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ClassificationResult:
    verdict: Verdict
    findings: tuple[Finding, ...]
    overridden: tuple[Finding, ...]


WATCHED_FILES: tuple[str, ...] = (
    "docker-compose.yml",
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/ci.yml",
)

# Native/compiled-extension runtime deps: a MAJOR bump carries ABI/GDAL/wheel
# risk a fresh-env CI run may not surface. Ordinary pure-Python library
# majors (pandas, pydantic, ...) are deliberately NOT here — unit/integration
# already exercise them (Plan 119 §1).
_NATIVE_EXTENSION_PACKAGES: tuple[str, ...] = (
    "cfgrib",
    "rioxarray",
    "exactextract",
    "forecastinterface",
)

ALLOWLIST_PATH = Path(".dependency-safety-allowlist")

_CPYTHON_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(-.*)?$")
_LEADING_INT_RE = re.compile(r"^(\d+)")
_REQUIRES_PYTHON_RE = re.compile(r'^requires-python\s*=\s*"([^"]*)"', re.MULTILINE)
_FI_REV_RE = re.compile(
    r'forecastinterface\s*=\s*\{[^}]*rev\s*=\s*"([^"]+)"[^}]*\}', re.DOTALL
)
_FROM_RE = re.compile(r"^FROM\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def _as_dict(value: object) -> dict[str, Any]:
    """Narrow an arbitrary YAML/TOML node to a str-keyed dict, else {}."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _load_yaml_mapping(text: str) -> dict[str, Any]:
    return _as_dict(yaml.safe_load(text))


def _parse_image_field(value: str) -> tuple[str, str]:
    """Split an `image:` field value into (repo, tag), dropping any @sha256 digest.

    Operates on the parsed YAML value, never on a trailing `# name:tag`
    comment — comments are not even visible to `yaml.safe_load`.
    """
    without_digest = value.split("@", 1)[0]
    repo, _, tag = without_digest.rpartition(":")
    return repo, tag


def _leading_int(tag: str) -> int | None:
    """Leading integer version-axis of a tag: '16-3.4' -> 16, '3-python3.11' -> 3."""
    match = _LEADING_INT_RE.match(tag)
    return int(match.group(1)) if match else None


def parse_cpython_tag(tag: str) -> tuple[int, int, int, str] | None:
    """Parse a CPython 'X.Y.Z[-flavor]' tag into (X, Y, Z, flavor)."""
    match = _CPYTHON_TAG_RE.match(tag)
    if match is None:
        return None
    x, y, z, flavor = match.groups()
    return int(x), int(y), int(z), flavor or ""


def _override_note(key: str) -> str:
    return (
        f" Override (once verified safe, with a dated justification): add `{key}` "
        "to `.dependency-safety-allowlist`."
    )


def classify_docker_compose_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK: any volume-mounted service image whose parsed version increased.

    Generic rule — no hardcoded per-image list. Covers postgis, prefect-server,
    caddy, and any future stateful service uniformly.
    """
    old_services = _as_dict(_load_yaml_mapping(old_text).get("services"))
    new_services = _as_dict(_load_yaml_mapping(new_text).get("services"))
    findings: list[Finding] = []

    for name, new_svc_raw in new_services.items():
        new_svc = _as_dict(new_svc_raw)
        old_svc = _as_dict(old_services.get(name))
        old_image = _as_str(old_svc.get("image"))
        new_image = _as_str(new_svc.get("image"))
        if not old_image or not new_image or old_image == new_image:
            continue
        has_volume = bool(new_svc.get("volumes")) or bool(old_svc.get("volumes"))
        if not has_volume:
            continue

        _, old_tag = _parse_image_field(old_image)
        new_repo, new_tag = _parse_image_field(new_image)
        old_major, new_major = _leading_int(old_tag), _leading_int(new_tag)
        if old_major is None or new_major is None or new_major <= old_major:
            continue

        key = f"docker-compose.yml:{new_repo}"
        message = (
            f"\U0001f6d1 `{new_repo}` **{old_tag} → {new_tag}** is a major version "
            f"bump of the stateful `{name}` service (persistent volume mount). A major "
            "bump can break the on-disk data format under that volume, which CI's "
            "always-empty containers never exercise. This PR must not merge until a "
            "tested upgrade path exists."
        )
        if new_repo == "postgis/postgis":
            message += " See Plan 118 for the required migration."
        message += _override_note(key)
        findings.append(
            Finding(
                verdict=Verdict.BLOCK,
                file="docker-compose.yml",
                key=key,
                message=message,
            )
        )
    return findings


def classify_dockerfile_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK: base-image family change, or (for CPython) minor/flavor/major change.

    Risk axis for CPython's X.Y.Z tag scheme is the MINOR (Y) — NOT a generic
    "semver-major increased" test, which would miss 3.14 -> 3.15.
    """
    old_images: list[str] = _FROM_RE.findall(old_text)
    new_images: list[str] = _FROM_RE.findall(new_text)
    if not old_images or not new_images:
        return []
    old_image, new_image = old_images[0], new_images[0]
    if old_image == new_image:
        return []

    old_repo, old_tag = _parse_image_field(old_image)
    new_repo, new_tag = _parse_image_field(new_image)

    if old_repo != new_repo:
        key = f"Dockerfile:{new_repo}-base-image"
        message = (
            f"\U0001f6d1 `Dockerfile` base image family changed: `{old_repo}` → "
            f"`{new_repo}`. This is a new base image entirely."
        ) + _override_note(key)
        return [
            Finding(verdict=Verdict.BLOCK, file="Dockerfile", key=key, message=message)
        ]

    if old_repo == "python":
        old_parts, new_parts = parse_cpython_tag(old_tag), parse_cpython_tag(new_tag)
        if old_parts is None or new_parts is None:
            return []
        old_x, old_y, _, old_flavor = old_parts
        new_x, new_y, _, new_flavor = new_parts
        if (old_x, old_y, old_flavor) == (new_x, new_y, new_flavor):
            return []
        key = "Dockerfile:python-base-image"
        message = (
            f"\U0001f6d1 `Dockerfile` base image change: `python:{old_tag}` → "
            f"`python:{new_tag}`. For CPython's X.Y.Z scheme the risk axis is the "
            "MINOR, not semver-major — CI may not even run this interpreter version "
            "yet."
        ) + _override_note(key)
        return [
            Finding(verdict=Verdict.BLOCK, file="Dockerfile", key=key, message=message)
        ]

    old_major, new_major = _leading_int(old_tag), _leading_int(new_tag)
    if old_major is None or new_major is None or old_major == new_major:
        return []
    key = f"Dockerfile:{new_repo}-base-image"
    message = (
        f"\U0001f6d1 `Dockerfile` base image `{new_repo}` major bump: `{old_tag}` "
        f"→ `{new_tag}`."
    ) + _override_note(key)
    return [Finding(verdict=Verdict.BLOCK, file="Dockerfile", key=key, message=message)]


def classify_pyproject_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK on any `requires-python` change; REVIEW on the FI git-pin rev."""
    findings: list[Finding] = []

    old_rp_match = _REQUIRES_PYTHON_RE.search(old_text)
    new_rp_match = _REQUIRES_PYTHON_RE.search(new_text)
    old_rp = old_rp_match.group(1) if old_rp_match else None
    new_rp = new_rp_match.group(1) if new_rp_match else None
    if old_rp != new_rp:
        key = "pyproject.toml:requires-python"
        message = (
            f"\U0001f6d1 `pyproject.toml` `requires-python` changed: `{old_rp}` "
            f"→ `{new_rp}`. Only humans edit this field — verify CI's "
            "`python-version` matrix in `ci.yml` still satisfies the new floor "
            "before merging."
        ) + _override_note(key)
        findings.append(
            Finding(
                verdict=Verdict.BLOCK, file="pyproject.toml", key=key, message=message
            )
        )

    old_rev_match = _FI_REV_RE.search(old_text)
    new_rev_match = _FI_REV_RE.search(new_text)
    old_rev = old_rev_match.group(1) if old_rev_match else None
    new_rev = new_rev_match.group(1) if new_rev_match else None
    if old_rev != new_rev:
        key = "pyproject.toml:forecastinterface-git-pin"
        message = (
            f"⚠️ ForecastInterface git-pin changed: `{old_rev}` → "
            f"`{new_rev}`. Environment-coupled, not fully exercised by the test "
            "suite — verify the `wheel-only-guard` job and "
            "`docs/standards/security.md` § Wheel-only dependency-update guard "
            "are still accurate."
        )
        findings.append(
            Finding(
                verdict=Verdict.REVIEW, file="pyproject.toml", key=key, message=message
            )
        )

    return findings


def _lock_versions(text: str) -> dict[str, str]:
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    packages: list[dict[str, Any]] = data.get("package") or []
    return {str(p["name"]): str(p.get("version", "")) for p in packages if "name" in p}


def classify_uv_lock_diff(old_text: str, new_text: str) -> list[Finding]:
    """REVIEW: a MAJOR bump of a listed native/compiled-extension runtime dep."""
    old_versions, new_versions = _lock_versions(old_text), _lock_versions(new_text)
    findings: list[Finding] = []
    for name in _NATIVE_EXTENSION_PACKAGES:
        old_v, new_v = old_versions.get(name), new_versions.get(name)
        if not old_v or not new_v or old_v == new_v:
            continue
        old_major, new_major = _leading_int(old_v), _leading_int(new_v)
        if old_major is None or new_major is None or new_major <= old_major:
            continue
        key = f"uv.lock:{name}"
        message = (
            f"⚠️ `{name}` major bump: `{old_v}` → `{new_v}`. "
            "Native/compiled-extension ABI risk not fully exercised by the test "
            "suite — verify manually before merge."
        )
        findings.append(
            Finding(verdict=Verdict.REVIEW, file="uv.lock", key=key, message=message)
        )
    return findings


def _extract_job(text: str, job_name: str) -> dict[str, Any] | None:
    jobs = _as_dict(_load_yaml_mapping(text).get("jobs"))
    job = jobs.get(job_name)
    return job if job is not None else None


def classify_ci_workflow_diff(old_text: str, new_text: str) -> list[Finding]:
    """REVIEW: postgis-major confined to ci.yml's ephemeral services:, or
    wheel-only-guard / FI machinery edits.
    """
    findings: list[Finding] = []

    old_jobs = _as_dict(_load_yaml_mapping(old_text).get("jobs"))
    new_jobs = _as_dict(_load_yaml_mapping(new_text).get("jobs"))
    for job_name, new_job_raw in new_jobs.items():
        new_job = _as_dict(new_job_raw)
        old_job = _as_dict(old_jobs.get(job_name))
        old_services = _as_dict(old_job.get("services"))
        new_services = _as_dict(new_job.get("services"))
        for svc_name, new_svc_raw in new_services.items():
            new_svc = _as_dict(new_svc_raw)
            old_svc = _as_dict(old_services.get(svc_name))
            old_image = _as_str(old_svc.get("image"))
            new_image = _as_str(new_svc.get("image"))
            if not old_image or not new_image or old_image == new_image:
                continue
            _, old_tag = _parse_image_field(old_image)
            new_repo, new_tag = _parse_image_field(new_image)
            if new_repo != "postgis/postgis":
                continue
            old_major, new_major = _leading_int(old_tag), _leading_int(new_tag)
            if old_major is None or new_major is None or new_major <= old_major:
                continue
            key = f".github/workflows/ci.yml:{job_name}:{svc_name}:postgis"
            message = (
                f"⚠️ `postgis/postgis` major bump confined to `ci.yml`'s "
                f"ephemeral `{job_name}` service container (`{old_tag}` → "
                f"`{new_tag}`). No persistent volume — CI-only risk — but verify this "
                "pin stays in lockstep with `docker-compose.yml`'s postgis pin so CI "
                "keeps testing against the deployed engine version."
            )
            findings.append(
                Finding(
                    verdict=Verdict.REVIEW,
                    file=".github/workflows/ci.yml",
                    key=key,
                    message=message,
                )
            )

    old_guard = _extract_job(old_text, "wheel-only-guard")
    new_guard = _extract_job(new_text, "wheel-only-guard")
    if old_guard != new_guard and (old_guard is not None or new_guard is not None):
        key = ".github/workflows/ci.yml:wheel-only-guard"
        message = (
            "⚠️ `wheel-only-guard` job changed. Environment-coupled "
            "dependency-install guard — verify the source-build exception list in "
            "`docs/standards/security.md` § Wheel-only dependency-update guard is "
            "still accurate."
        )
        findings.append(
            Finding(
                verdict=Verdict.REVIEW,
                file=".github/workflows/ci.yml",
                key=key,
                message=message,
            )
        )

    return findings


_CLASSIFIERS: dict[str, Callable[[str, str], list[Finding]]] = {
    "docker-compose.yml": classify_docker_compose_diff,
    "Dockerfile": classify_dockerfile_diff,
    "pyproject.toml": classify_pyproject_diff,
    "uv.lock": classify_uv_lock_diff,
    ".github/workflows/ci.yml": classify_ci_workflow_diff,
}


def classify_pr(
    changed_files: Mapping[str, tuple[str, str]],
    allowlist: frozenset[str] = frozenset(),
) -> ClassificationResult:
    """Classify a PR's changed watched files. Empty input -> ALLOW (skip-pass)."""
    findings: list[Finding] = []
    for path, (old_text, new_text) in changed_files.items():
        classifier = _CLASSIFIERS.get(path)
        if classifier is None:
            continue
        findings.extend(classifier(old_text, new_text))

    effective: list[Finding] = []
    overridden: list[Finding] = []
    for finding in findings:
        if finding.verdict is Verdict.BLOCK and finding.key in allowlist:
            overridden.append(finding)
        else:
            effective.append(finding)

    overall = max(
        (f.verdict for f in effective),
        key=lambda v: _VERDICT_RANK[v],
        default=Verdict.ALLOW,
    )
    return ClassificationResult(
        verdict=overall, findings=tuple(effective), overridden=tuple(overridden)
    )


def load_allowlist(path: Path = ALLOWLIST_PATH) -> frozenset[str]:
    """Parse the committed override allowlist: one key per line.

    `#`-comments and blank lines are skipped.
    """
    if not path.exists():
        return frozenset()
    keys = {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return frozenset(keys)


def _git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _read_current(path: str) -> str | None:
    file = Path(path)
    return file.read_text() if file.exists() else None


def gather_changed_watched_files(base_ref: str) -> dict[str, tuple[str, str]]:
    """Diff each watched file's base-ref content against the current working tree."""
    changed: dict[str, tuple[str, str]] = {}
    for path in WATCHED_FILES:
        old = _git_show(base_ref, path)
        new = _read_current(path)
        if old == new:
            continue
        changed[path] = (old or "", new or "")
    return changed


def _write_step_summary(result: ClassificationResult) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    review = [f for f in result.findings if f.verdict is Verdict.REVIEW]
    if not review and not result.overridden:
        return
    lines = ["## Dependency-safety gate\n\n"]
    if review:
        lines.append("### REVIEW — advisory, verify before merge\n\n")
        lines.extend(f"- {f.message}\n" for f in review)
        lines.append("\n")
    if result.overridden:
        lines.append("### BLOCK overridden by `.dependency-safety-allowlist`\n\n")
        lines.extend(f"- {f.message}\n" for f in result.overridden)
    with open(summary_path, "a") as fh:
        fh.writelines(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="PR base SHA to diff against")
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    args = parser.parse_args(argv)

    changed = gather_changed_watched_files(args.base_ref)
    if not changed:
        print("dependency-safety: no watched files changed against base — skip-pass.")
        return 0

    allowlist = load_allowlist(args.allowlist)
    result = classify_pr(changed, allowlist)

    for finding in result.overridden:
        print(f"[OVERRIDDEN by allowlist] {finding.file}: {finding.message}")
    for finding in result.findings:
        prefix = "::error::" if finding.verdict is Verdict.BLOCK else "::warning::"
        print(f"{prefix}{finding.message}")

    _write_step_summary(result)

    if result.verdict is Verdict.BLOCK:
        print("\ndependency-safety: BLOCK — see messages above.", file=sys.stderr)
        return 1

    suffix = (
        " (with REVIEW notices — see step summary)"
        if result.verdict is Verdict.REVIEW
        else ""
    )
    print(f"dependency-safety: pass{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
