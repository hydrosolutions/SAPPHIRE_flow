#!/usr/bin/env python3
"""Plan 119 dependency-bump safety gate — BLOCK/REVIEW/ALLOW classifier.

Diffs a fixed watched-file set (see `WATCHED_FILES` — docker-compose.yml,
Dockerfile, pyproject.toml, uv.lock, ci.yml, and the gate's own self-policy
files) against the PR base commit and classifies the change:

  BLOCK  — a stateful (persistent-named-volume) service image major bump in
           docker-compose.yml (keyed on the VOLUME, not the service name —
           survives a rename/delete+re-add), a Dockerfile base-image change
           in ANY `FROM` stage (CPython risk axis = MINOR, not semver-major;
           accepts both `X.Y` and `X.Y.Z` tags), or any `requires-python`
           change in pyproject.toml. Every BLOCK key is EXACT-scoped to the
           specific old->new transition — an allowlist override clears only
           the bump it names, never the whole image class.
  REVIEW — FI/recap git-pin or wheel-guard machinery changes, a MAJOR bump of
           a native/compiled-extension runtime dep (cfgrib, rioxarray,
           exactextract, forecastinterface), a postgis-major confined to
           ci.yml's ephemeral `services:` container, a digest-only change on
           a stateful volume under an unchanged tag (fail-closed — cannot be
           proven same-major from the tag alone), a brand-new volume-backed
           image, an image-repo change on a persisting volume, an edit to
           the `.dependency-safety-allowlist` override file, or an edit to
           the gate's own policy files (this module, its trigger workflow,
           `.github/dependabot.yml`) — self-modification is never silent.
  ALLOW  — everything else (patch/minor of a normal library, action patch
           bumps, dev-dependency patches) — silent, no finding emitted.

CRITICAL: version fields are parsed from the machine-readable YAML `image:`
value (before `@sha256:...`), never from the trailing `# name:tag` comment —
Dependabot does not keep that comment in sync (see docs/plans/119).

Base-content resolution FAILS CLOSED: if a watched file the base-ref diff
reports as modified cannot have its base content read, the run aborts
non-zero rather than silently treating the base as empty (see
`BaseResolutionError`) — the one exception is a file confirmed added at
HEAD, which legitimately has no base content.

Usage::

    uv run python tools/dependency_safety.py --base-ref <sha>

Exit codes: 0 = pass (ALLOW/REVIEW, or unresolved BLOCK cleared by the
committed `.dependency-safety-allowlist`), 1 = unresolved BLOCK or a
base-content resolution failure.
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
    # Gate self-policy files (2026-07-15 hardening): a PR that weakens the
    # classifier, its trigger workflow, Dependabot's ignore rules, or the
    # override allowlist while ALSO making a dangerous bump elsewhere must
    # never have the neutered classifier judge itself silently.
    "tools/dependency_safety.py",
    ".github/workflows/dependency-safety.yml",
    ".github/dependabot.yml",
    ".dependency-safety-allowlist",
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

# CPython tag scheme accepts BOTH `X.Y` and `X.Y.Z` (Docker Hub publishes
# both, e.g. `python:3.15-slim` and `python:3.15.0-slim`) — the risk axis is
# the minor `Y`, so the patch component is optional and ignored.
_CPYTHON_TAG_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(-.*)?$")
_LEADING_INT_RE = re.compile(r"^(\d+)")
_REQUIRES_PYTHON_RE = re.compile(r'^requires-python\s*=\s*"([^"]*)"', re.MULTILINE)
_FI_REV_RE = re.compile(
    r'forecastinterface\s*=\s*\{[^}]*rev\s*=\s*"([^"]+)"[^}]*\}', re.DOTALL
)
# Skip any `--flag[=value]` tokens (e.g. `--platform=linux/amd64`) between
# `FROM` and the image reference — otherwise the flag itself gets captured
# as "the image". Captures every `FROM` line (multi-stage builds), not just
# the first.
_FROM_RE = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)", re.IGNORECASE | re.MULTILINE)


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


def _parse_digest(value: str) -> str | None:
    """The `@sha256:...` digest suffix of an `image:` field value, if pinned."""
    parts = value.split("@", 1)
    return parts[1] if len(parts) > 1 else None


def _leading_int(tag: str) -> int | None:
    """Leading integer version-axis of a tag: '16-3.4' -> 16, '3-python3.11' -> 3."""
    match = _LEADING_INT_RE.match(tag)
    return int(match.group(1)) if match else None


def parse_cpython_tag(tag: str) -> tuple[int, int, int, str] | None:
    """Parse a CPython 'X.Y[.Z][-flavor]' tag into (X, Y, Z, flavor).

    Z defaults to 0 when the tag omits the patch component (`python:3.15-slim`
    is a valid Docker Hub tag, not just `python:3.15.0-slim`).
    """
    match = _CPYTHON_TAG_RE.match(tag)
    if match is None:
        return None
    x, y, z, flavor = match.groups()
    z_value = int(z) if z is not None else 0
    return int(x), int(y), z_value, flavor or ""


def _override_note(key: str) -> str:
    return (
        f" Override (once verified safe, with a dated justification): add `{key}` "
        "to `.dependency-safety-allowlist`."
    )


def _is_bind_mount_source(source: str) -> bool:
    """Compose short-syntax rule: a bind-mount source is always a path
    (`./relative`, `/absolute`, `~/home`); a named-volume source is a bare
    identifier (`pgdata`, `prefect_data`, ...)."""
    return source.startswith((".", "/", "~"))


def _named_volume_sources(svc: dict[str, Any]) -> list[str]:
    """Persistent named-volume sources a compose service mounts.

    Handles both the short string syntax (`pgdata:/path`) and the long
    mapping syntax (`{type: volume, source: pgdata, ...}`); bind mounts and
    `type: tmpfs` entries are excluded.
    """
    entries_raw = svc.get("volumes")
    if not isinstance(entries_raw, list):
        return []
    entries = cast("list[object]", entries_raw)
    sources: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            source = entry.split(":", 1)[0]
            if source and not _is_bind_mount_source(source):
                sources.append(source)
        else:
            entry_dict = _as_dict(entry)
            if entry_dict.get("type") == "volume":
                volume_source = _as_str(entry_dict.get("source"))
                if volume_source:
                    sources.append(volume_source)
    return sources


@dataclass(frozen=True, kw_only=True, slots=True)
class _VolumeImage:
    service: str
    repo: str
    tag: str
    digest: str | None


def _volume_image_map(services: dict[str, Any]) -> dict[str, _VolumeImage]:
    """Map each persistent named-volume source to the image mounting it.

    Keying on the volume (not the service name) survives a service rename
    or a delete+re-add of the same service — a service-name-keyed diff
    misses both entirely.
    """
    result: dict[str, _VolumeImage] = {}
    for svc_name, svc_raw in services.items():
        svc = _as_dict(svc_raw)
        image = _as_str(svc.get("image"))
        if not image:
            continue
        repo, tag = _parse_image_field(image)
        digest = _parse_digest(image)
        for volume_name in _named_volume_sources(svc):
            result[volume_name] = _VolumeImage(
                service=svc_name, repo=repo, tag=tag, digest=digest
            )
    return result


def classify_docker_compose_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK/REVIEW keyed on the persistent NAMED VOLUME, not the service key.

    Generic rule — no hardcoded per-image list. Covers postgis, prefect-server,
    caddy, and any future stateful service uniformly, and survives a service
    rename or delete+re-add that reuses the same volume.
    """
    old_services = _as_dict(_load_yaml_mapping(old_text).get("services"))
    new_services = _as_dict(_load_yaml_mapping(new_text).get("services"))
    old_map = _volume_image_map(old_services)
    new_map = _volume_image_map(new_services)

    findings: list[Finding] = []
    for volume_name, new_vi in new_map.items():
        old_vi = old_map.get(volume_name)

        if old_vi is None:
            key = f"docker-compose.yml:{volume_name}:{new_vi.repo}:new-volume"
            message = (
                f"⚠️ new persistent-volume-mounted image `{new_vi.repo}:"
                f"{new_vi.tag}` on volume `{volume_name}` (service "
                f"`{new_vi.service}`). No prior version on this volume to diff a "
                "major bump against — verify this stateful image's data format / "
                "upgrade story before merge."
            )
            findings.append(
                Finding(
                    verdict=Verdict.REVIEW,
                    file="docker-compose.yml",
                    key=key,
                    message=message,
                )
            )
            continue

        if old_vi.repo != new_vi.repo:
            key = f"docker-compose.yml:{volume_name}:{old_vi.repo}->{new_vi.repo}"
            message = (
                f"⚠️ volume `{volume_name}` is now backed by a different "
                f"image repo: `{old_vi.repo}` (service `{old_vi.service}`) → "
                f"`{new_vi.repo}` (service `{new_vi.service}`). A version delta cannot "
                "be computed across image families — verify the new image can read "
                "data written by the old one before merge."
            )
            findings.append(
                Finding(
                    verdict=Verdict.REVIEW,
                    file="docker-compose.yml",
                    key=key,
                    message=message,
                )
            )
            continue

        if old_vi.tag == new_vi.tag:
            if old_vi.digest != new_vi.digest:
                key = (
                    f"docker-compose.yml:{volume_name}:{new_vi.repo}:digest:"
                    f"{old_vi.digest}->{new_vi.digest}"
                )
                message = (
                    f"⚠️ `{new_vi.repo}` digest changed under an UNCHANGED "
                    f"tag `{new_vi.tag}` on stateful volume `{volume_name}`: "
                    f"`{old_vi.digest}` → `{new_vi.digest}`. The tag alone cannot "
                    "prove this is not a major-version change — verify the digest "
                    "manually before merge."
                )
                findings.append(
                    Finding(
                        verdict=Verdict.REVIEW,
                        file="docker-compose.yml",
                        key=key,
                        message=message,
                    )
                )
            continue

        old_major, new_major = _leading_int(old_vi.tag), _leading_int(new_vi.tag)
        if old_major is None or new_major is None or new_major <= old_major:
            continue

        key = (
            f"docker-compose.yml:{volume_name}:{new_vi.repo}:{old_vi.tag}->{new_vi.tag}"
        )
        message = (
            f"\U0001f6d1 `{new_vi.repo}` **{old_vi.tag} → {new_vi.tag}** is a major "
            f"version bump of the stateful `{new_vi.service}` service (persistent "
            f"volume `{volume_name}`). A major bump can break the on-disk data format "
            "under that volume, which CI's always-empty containers never exercise. "
            "This PR must not merge until a tested upgrade path exists."
        )
        if new_vi.repo == "postgis/postgis":
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


def _classify_one_from_change(old_image: str, new_image: str) -> list[Finding]:
    """BLOCK: base-image family change, or (for CPython) minor/flavor/major change.

    Risk axis for CPython's X.Y[.Z] tag scheme is the MINOR (Y) — NOT a generic
    "semver-major increased" test, which would miss 3.14 -> 3.15.
    """
    old_repo, old_tag = _parse_image_field(old_image)
    new_repo, new_tag = _parse_image_field(new_image)

    if old_repo != new_repo:
        key = f"Dockerfile:{old_repo}->{new_repo}-base-image"
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
        key = f"Dockerfile:python-base-image:{old_tag}->{new_tag}"
        message = (
            f"\U0001f6d1 `Dockerfile` base image change: `python:{old_tag}` → "
            f"`python:{new_tag}`. For CPython's X.Y[.Z] scheme the risk axis is the "
            "MINOR, not semver-major — CI may not even run this interpreter version "
            "yet."
        ) + _override_note(key)
        return [
            Finding(verdict=Verdict.BLOCK, file="Dockerfile", key=key, message=message)
        ]

    old_major, new_major = _leading_int(old_tag), _leading_int(new_tag)
    if old_major is None or new_major is None or old_major == new_major:
        return []
    key = f"Dockerfile:{new_repo}-base-image:{old_tag}->{new_tag}"
    message = (
        f"\U0001f6d1 `Dockerfile` base image `{new_repo}` major bump: `{old_tag}` "
        f"→ `{new_tag}`."
    ) + _override_note(key)
    return [Finding(verdict=Verdict.BLOCK, file="Dockerfile", key=key, message=message)]


def classify_dockerfile_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK: any `FROM` stage's base-image family/CPython-minor/major change.

    Compares ALL `FROM` instructions (multi-stage builds), positionally —
    not just the first — so a PR bumping only the final (runtime) stage while
    leaving the builder stage pinned is still caught.
    """
    old_images: list[str] = _FROM_RE.findall(old_text)
    new_images: list[str] = _FROM_RE.findall(new_text)
    if not old_images or not new_images:
        return []

    seen: set[tuple[str, str]] = set()
    findings: list[Finding] = []
    for old_image, new_image in zip(old_images, new_images, strict=False):
        if old_image == new_image or (old_image, new_image) in seen:
            continue
        seen.add((old_image, new_image))
        findings.extend(_classify_one_from_change(old_image, new_image))
    return findings


def classify_pyproject_diff(old_text: str, new_text: str) -> list[Finding]:
    """BLOCK on any `requires-python` change; REVIEW on the FI git-pin rev."""
    findings: list[Finding] = []

    old_rp_match = _REQUIRES_PYTHON_RE.search(old_text)
    new_rp_match = _REQUIRES_PYTHON_RE.search(new_text)
    old_rp = old_rp_match.group(1) if old_rp_match else None
    new_rp = new_rp_match.group(1) if new_rp_match else None
    if old_rp != new_rp:
        key = f"pyproject.toml:requires-python:{old_rp}->{new_rp}"
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


def classify_allowlist_diff(old_text: str, new_text: str) -> list[Finding]:
    """REVIEW: any edit to the override allowlist — an allowlist-only PR must
    never skip-pass silently (Plan 119 hardening, blocker 4)."""
    del old_text, new_text  # any diff at all is flagged; content doesn't matter
    key = ".dependency-safety-allowlist:changed"
    message = (
        "⚠️ `.dependency-safety-allowlist` changed. This file overrides BLOCK "
        "findings from the dependency-safety gate — verify every added entry "
        "names an EXACT bump (file + service-or-volume + image repo + "
        "old→new tag/digest), not a class-wide clearance, and carries a dated "
        "justification."
    )
    return [
        Finding(
            verdict=Verdict.REVIEW,
            file=".dependency-safety-allowlist",
            key=key,
            message=message,
        )
    ]


def _make_self_policy_classifier(path: str) -> Callable[[str, str], list[Finding]]:
    """REVIEW: any edit to a gate self-policy file — the classifier module,
    its trigger workflow, or `.github/dependabot.yml`'s ignore rules. A PR
    that weakens the gate while ALSO making a dangerous bump elsewhere must
    not have the neutered classifier judge itself silently (Plan 119
    hardening, major 2)."""

    def _classify(old_text: str, new_text: str) -> list[Finding]:
        del old_text, new_text
        key = f"{path}:changed"
        message = (
            f"⚠️ `{path}` (dependency-safety gate policy file) changed in this "
            "PR. Verify this edit is reviewed on its own merits, not incidental "
            "to a dependency bump elsewhere in the same PR — a self-modifying "
            "gate cannot be trusted to judge its own weakening."
        )
        return [Finding(verdict=Verdict.REVIEW, file=path, key=key, message=message)]

    return _classify


_CLASSIFIERS: dict[str, Callable[[str, str], list[Finding]]] = {
    "docker-compose.yml": classify_docker_compose_diff,
    "Dockerfile": classify_dockerfile_diff,
    "pyproject.toml": classify_pyproject_diff,
    "uv.lock": classify_uv_lock_diff,
    ".github/workflows/ci.yml": classify_ci_workflow_diff,
    ".dependency-safety-allowlist": classify_allowlist_diff,
    "tools/dependency_safety.py": _make_self_policy_classifier(
        "tools/dependency_safety.py"
    ),
    ".github/workflows/dependency-safety.yml": _make_self_policy_classifier(
        ".github/workflows/dependency-safety.yml"
    ),
    ".github/dependabot.yml": _make_self_policy_classifier(".github/dependabot.yml"),
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


class BaseResolutionError(RuntimeError):
    """A watched file's base-ref content could not be read and the file was
    not confirmed added at HEAD (`git diff --name-status` status `A`).

    FAIL CLOSED here rather than silently substituting "" for the base
    content: an empty "old" text is indistinguishable to every classifier
    from "this file was just added", which can make a real dangerous change
    (e.g. a stateful-image major bump) classify as ALLOW instead of raising
    the alarm (Plan 119 hardening, major 1).
    """


def _git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _read_current(path: str) -> str | None:
    file = Path(path)
    return file.read_text() if file.exists() else None


def _git_diff_name_status(base_ref: str) -> dict[str, str]:
    """`path -> git diff --name-status letter` for the watched set, diffing
    `base_ref` against the current working tree (mirrors `_read_current`,
    which reads on-disk content, not HEAD)."""
    result = subprocess.run(
        ["git", "diff", "--name-status", base_ref, "--", *WATCHED_FILES],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BaseResolutionError(
            f"`git diff --name-status {base_ref}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    statuses: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        status, path = fields[0][0], fields[-1]
        statuses[path] = status
    return statuses


def gather_changed_watched_files(base_ref: str) -> dict[str, tuple[str, str]]:
    """Diff each watched file's base-ref content against the current working tree.

    Raises `BaseResolutionError` (fail closed) if a watched file the diff
    reports as MODIFIED cannot have its base-ref content read — the one
    exception is a file confirmed ADDED at HEAD (status `A`), which
    legitimately has no base content.
    """
    statuses = _git_diff_name_status(base_ref)
    changed: dict[str, tuple[str, str]] = {}
    for path in WATCHED_FILES:
        status = statuses.get(path)
        if status is None:
            continue
        old = _git_show(base_ref, path)
        new = _read_current(path)
        if old is None and status != "A":
            raise BaseResolutionError(
                f"could not read base content for watched file {path!r} at "
                f"{base_ref!r} (git diff status={status!r}, expected 'A' for a "
                "legitimate add) — refusing to classify with missing base content"
            )
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

    try:
        changed = gather_changed_watched_files(args.base_ref)
    except BaseResolutionError as exc:
        print(f"::error::dependency-safety: {exc}", file=sys.stderr)
        print(
            "\ndependency-safety: BLOCK (fail-closed) — see error above.",
            file=sys.stderr,
        )
        return 1
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
