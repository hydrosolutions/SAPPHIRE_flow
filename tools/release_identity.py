#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeGuard, cast

CANONICAL_HTTPS_URL = "https://github.com/hydrosolutions/SAPPHIRE_flow.git"
# Observed 2026-10-01 for the canonical retired publisher path
# `.github/workflows/tag-main.yml` in hydrosolutions/SAPPHIRE_flow. Keep this
# internal and numeric so bootstrap does not rely on filename lookup after the
# workflow file is removed from main.
LEGACY_TAG_MAIN_WORKFLOW_ID = 339307243
STATE_REF = "refs/heads/release-state"
HELPER_MARKER = "SAPPHIRE-FLOW-RELEASE-IDENTITY-v1"
IDENTITY_NAME = "SAPPHIRE Flow Release Identity"
IDENTITY_EMAIL = "sapphire-flow-release@example.invalid"
IDENTITY_TIME = "946684800 +0000"
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_ALLOWED_RUN_STATES = {"completed", "success", "failure", "cancelled", "skipped"}
_NONTERMINAL_RUN_STATES = {
    "queued",
    "waiting",
    "pending",
    "requested",
    "in_progress",
    "running",
}


class ReleaseIdentityError(RuntimeError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            raise ReleaseIdentityError(f"version must be canonical X.Y.Z: {text!r}")
        return cls(*(int(part) for part in match.groups()))

    @classmethod
    def parse_tag(cls, text: str) -> Version:
        match = _TAG_RE.fullmatch(text)
        if match is None:
            raise ReleaseIdentityError(f"tag must be canonical vX.Y.Z: {text!r}")
        return cls(*(int(part) for part in match.groups()))

    @property
    def text(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self.text}"


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    marker: str
    tag: str
    source: str
    ceiling_tag: str
    ceiling_source: str


@dataclass(frozen=True, slots=True)
class RemoteTag:
    name: str
    version: Version
    object_oid: str
    peeled_oid: str
    annotated: bool
    direct_object_oid: str | None
    direct_type: str | None
    embedded_name: str | None
    metadata: ReleaseMetadata | None


@dataclass(frozen=True, slots=True)
class ReceiptImage:
    variant: Literal["default", "aquacast"]
    mutable_tag: str
    image_id: str
    labels: dict[str, str]
    runtime_import_version: str


class ProbedImageLike(Protocol):
    variant: Literal["default", "aquacast"]
    mutable_tag: str
    image_id: str
    labels: dict[str, str]
    runtime_import_version: str


class ImageProbeModule(Protocol):
    ImageIdentityProbeError: type[Exception]

    def probe_image(
        self,
        image_tag: str,
        expected_version: str,
        expected_revision: str,
        *,
        variant: Literal["default", "aquacast"],
    ) -> ProbedImageLike: ...


def _load_image_probe() -> ImageProbeModule:
    loaded = sys.modules.get("image_identity_probe")
    if loaded is not None:
        return cast("ImageProbeModule", loaded)
    probe_path = Path(__file__).with_name("image_identity_probe.py")
    spec = importlib.util.spec_from_file_location("image_identity_probe", probe_path)
    if spec is None or spec.loader is None:
        raise ReleaseIdentityError("could not load image identity probe")
    module = importlib.util.module_from_spec(spec)
    sys.modules["image_identity_probe"] = module
    spec.loader.exec_module(module)
    return cast("ImageProbeModule", module)


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    package_version: str
    source_revision: str
    git_tree: str
    remote_tag: str
    remote_tag_object: str
    remote_tag_target: str
    release_state: str
    images: list[ReceiptImage]


class DockerImageBuilder(Protocol):
    def build(self, context: Path, version: str, source: str) -> list[ReceiptImage]: ...


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _as_object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ReleaseIdentityError("expected a list of objects")
    items = cast("list[object]", value)
    result: list[dict[str, object]] = []
    for item in items:
        if not _is_json_object(item):
            raise ReleaseIdentityError("expected a list of objects")
        result.append(item)
    return result


def _git_env(*, harden: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": IDENTITY_NAME,
            "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
            "GIT_AUTHOR_DATE": IDENTITY_TIME,
            "GIT_COMMITTER_NAME": IDENTITY_NAME,
            "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
            "GIT_COMMITTER_DATE": IDENTITY_TIME,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_GRAFT_FILE": os.devnull,
        }
    )
    if harden:
        for key in list(env):
            if (
                key == "GIT_CONFIG_PARAMETERS"
                or key == "GIT_CONFIG_COUNT"
                or key.startswith("GIT_CONFIG_KEY_")
                or key.startswith("GIT_CONFIG_VALUE_")
            ):
                env.pop(key)
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_COUNT": "5",
                "GIT_CONFIG_KEY_0": "commit.gpgsign",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "tag.gpgSign",
                "GIT_CONFIG_VALUE_1": "false",
                "GIT_CONFIG_KEY_2": "user.name",
                "GIT_CONFIG_VALUE_2": IDENTITY_NAME,
                "GIT_CONFIG_KEY_3": "user.email",
                "GIT_CONFIG_VALUE_3": IDENTITY_EMAIL,
                "GIT_CONFIG_KEY_4": "i18n.commitEncoding",
                "GIT_CONFIG_VALUE_4": "UTF-8",
            }
        )
    return env


def git(
    args: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    harden: bool = False,
) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        env=_git_env(harden=harden),
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseIdentityError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def require_full_sha(text: str) -> str:
    if text != text.lower() or re.fullmatch(r"[0-9a-f]{40}", text) is None:
        raise ReleaseIdentityError("source must be a full 40-character lowercase SHA")
    return text


def token_message(tag: str, source: str, ceiling_tag: str, ceiling_source: str) -> str:
    return (
        f"{HELPER_MARKER}\n"
        f"tag={tag}\n"
        f"source={source}\n"
        f"legacy-ceiling-tag={ceiling_tag}\n"
        f"legacy-ceiling-source={ceiling_source}\n"
    )


def tag_message(tag: str, source: str, ceiling_tag: str, ceiling_source: str) -> str:
    return token_message(tag, source, ceiling_tag, ceiling_source)


def parse_metadata(message: str) -> ReleaseMetadata:
    lines = message.splitlines()
    if not lines or lines[0] != HELPER_MARKER:
        raise ReleaseIdentityError("missing helper-era release marker")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition("=")
        if sep != "=":
            raise ReleaseIdentityError(f"invalid release metadata line: {line!r}")
        values[key] = value
    required = {"tag", "source", "legacy-ceiling-tag", "legacy-ceiling-source"}
    if values.keys() != required:
        raise ReleaseIdentityError("release metadata fields did not match exactly")
    Version.parse_tag(values["tag"])
    require_full_sha(values["source"])
    Version.parse_tag(values["legacy-ceiling-tag"])
    require_full_sha(values["legacy-ceiling-source"])
    return ReleaseMetadata(
        marker=HELPER_MARKER,
        tag=values["tag"],
        source=values["source"],
        ceiling_tag=values["legacy-ceiling-tag"],
        ceiling_source=values["legacy-ceiling-source"],
    )


def create_token_commit(
    source: str, tag: str, ceiling_tag: str, ceiling_source: str
) -> str:
    source = require_full_sha(source)
    ceiling_source = require_full_sha(ceiling_source)
    tree = git(["rev-parse", f"{source}^{{tree}}"])
    return git(
        ["commit-tree", tree, "-p", source],
        input_text=token_message(tag, source, ceiling_tag, ceiling_source),
        harden=True,
    )


def create_annotated_tag_object(
    tag: str, source: str, ceiling_tag: str, ceiling_source: str
) -> str:
    source = require_full_sha(source)
    ceiling_source = require_full_sha(ceiling_source)
    body = tag_message(tag, source, ceiling_tag, ceiling_source)
    payload = (
        f"object {source}\n"
        "type commit\n"
        f"tag {tag}\n"
        f"tagger {IDENTITY_NAME} <{IDENTITY_EMAIL}> {IDENTITY_TIME}\n"
        "\n"
        f"{body}"
    )
    return git(["mktag"], input_text=payload, harden=True)


def _config_values(name: str) -> list[str]:
    try:
        return git(["config", "--get-all", name]).splitlines()
    except ReleaseIdentityError:
        return []


def _redacted_url(value: str) -> str:
    if "://" not in value or "@" not in value:
        return value
    scheme, rest = value.split("://", 1)
    host_path = rest.split("@", 1)[1]
    return f"{scheme}://<redacted>@{host_path}"


def validate_destination() -> None:
    origin_urls = _config_values("remote.origin.url")
    if len(origin_urls) != 1:
        raise ReleaseIdentityError("exactly one origin.url is required")
    origin = origin_urls[0]
    if origin != CANONICAL_HTTPS_URL:
        raise ReleaseIdentityError(f"unexpected origin.url: {_redacted_url(origin)!r}")
    if _config_values("remote.origin.pushurl"):
        raise ReleaseIdentityError("remote.origin.pushurl is not allowed")
    if _config_values("remote.pushDefault"):
        raise ReleaseIdentityError("remote.pushDefault is not allowed")
    all_config = git(["config", "--list", "--show-origin"])
    rewrite_lines = [
        line
        for line in all_config.splitlines()
        if ".insteadof" in line.lower() or ".pushinsteadof" in line.lower()
    ]
    if rewrite_lines:
        raise ReleaseIdentityError(
            "url.*.insteadOf and url.*.pushInsteadOf rewrites are not allowed "
            "for release publication"
        )
    push_url = git(["remote", "get-url", "--push", "origin"])
    if push_url != CANONICAL_HTTPS_URL:
        raise ReleaseIdentityError("effective push destination is not canonical")


def ls_remote(refs: list[str]) -> dict[str, str]:
    lines = git(["ls-remote", "origin", *refs]).splitlines()
    result: dict[str, str] = {}
    for line in lines:
        if not line:
            continue
        oid, ref = line.split("\t", 1)
        result[ref] = oid
    return result


def _parse_tag_object(raw: str) -> tuple[str | None, str | None, str | None, str]:
    header_text, _, message = raw.partition("\n\n")
    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        key, _, value = line.partition(" ")
        headers[key] = value
    return headers.get("object"), headers.get("type"), headers.get("tag"), message


def _fetch_ref(remote_ref: str, local_ref: str, expected_oid: str) -> None:
    git(["fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"])
    actual = git(["rev-parse", local_ref])
    if actual != expected_oid:
        raise ReleaseIdentityError(f"remote ref moved while fetching {remote_ref}")


def fetch_remote_state() -> tuple[str, str | None, list[RemoteTag]]:
    remote = ls_remote(["refs/heads/main", STATE_REF, "refs/tags/v*"])
    main = remote.get("refs/heads/main")
    if main is None:
        raise ReleaseIdentityError("remote origin/main is missing")
    _fetch_ref("refs/heads/main", "refs/sapphire-flow-release-identity/main", main)
    state = remote.get(STATE_REF)
    if state is not None:
        _fetch_ref(
            STATE_REF, "refs/sapphire-flow-release-identity/release-state", state
        )
    tags_by_name: dict[str, dict[str, str]] = {}
    for ref, oid in remote.items():
        if not ref.startswith("refs/tags/"):
            continue
        name = ref.removeprefix("refs/tags/").removesuffix("^{}")
        entry = tags_by_name.setdefault(name, {})
        if ref.endswith("^{}"):
            entry["peeled"] = oid
        else:
            entry["object"] = oid
    tags: list[RemoteTag] = []
    for name, values in tags_by_name.items():
        try:
            version = Version.parse_tag(name)
        except ReleaseIdentityError:
            continue
        object_oid = values.get("object")
        peeled_oid = values.get("peeled", object_oid)
        if object_oid is None or peeled_oid is None:
            continue
        local_ref = f"refs/sapphire-flow-release-identity/tags/{name}"
        _fetch_ref(f"refs/tags/{name}", local_ref, object_oid)
        second_remote = ls_remote([f"refs/tags/{name}", f"refs/tags/{name}^{{}}"])
        if second_remote.get(f"refs/tags/{name}") != object_oid:
            raise ReleaseIdentityError(f"remote tag moved while fetching {name}")
        second_peeled = second_remote.get(f"refs/tags/{name}^{{}}", object_oid)
        if second_peeled != peeled_oid:
            raise ReleaseIdentityError(f"remote peeled tag moved while fetching {name}")
        obj_type = git(["cat-file", "-t", object_oid])
        annotated = obj_type == "tag"
        metadata: ReleaseMetadata | None = None
        direct_object_oid: str | None = None
        direct_type: str | None = None
        embedded_name: str | None = None
        if annotated:
            raw = git(["cat-file", "tag", object_oid])
            direct_object_oid, direct_type, embedded_name, message = _parse_tag_object(
                raw
            )
            try:
                metadata = parse_metadata(message)
            except ReleaseIdentityError as exc:
                if message.splitlines()[:1] == [HELPER_MARKER]:
                    raise ReleaseIdentityError(
                        f"{name} has malformed helper-era metadata"
                    ) from exc
                metadata = None
        tags.append(
            RemoteTag(
                name=name,
                version=version,
                object_oid=object_oid,
                peeled_oid=peeled_oid,
                annotated=annotated,
                direct_object_oid=direct_object_oid,
                direct_type=direct_type,
                embedded_name=embedded_name,
                metadata=metadata,
            )
        )
    tags.sort(key=lambda item: item.version)
    return main, state, tags


def is_first_parent_ancestor(ancestor: str, descendant: str) -> bool:
    revs = git(["rev-list", "--first-parent", descendant]).splitlines()
    return ancestor in revs


def ensure_source_on_main(source: str, main: str) -> None:
    source = require_full_sha(source)
    if not is_first_parent_ancestor(source, main):
        raise ReleaseIdentityError("source is not on remote main first-parent history")


def validate_old_workflow_runs(path: Path) -> None:
    try:
        data_raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ReleaseIdentityError(
            "old workflow run evidence is not valid JSON"
        ) from exc
    if not _is_json_object(data_raw):
        raise ReleaseIdentityError("old workflow run evidence must be an object")
    if data_raw.get("query_error"):
        raise ReleaseIdentityError("old workflow run query reported an error")
    if data_raw.get("complete") is not True or data_raw.get("paginated") is not True:
        raise ReleaseIdentityError(
            "old workflow run query must be complete and paginated"
        )
    runs = _as_object_list(data_raw.get("runs"))
    for run in runs:
        status_raw = run.get("status")
        conclusion_raw = run.get("conclusion")
        state = str(
            status_raw if status_raw is not None else conclusion_raw or ""
        ).lower()
        if state in _NONTERMINAL_RUN_STATES:
            raise ReleaseIdentityError(
                f"old tag-main workflow run is nonterminal: {state}"
            )
        if state not in _ALLOWED_RUN_STATES:
            raise ReleaseIdentityError(
                f"old tag-main workflow run state is unknown: {state}"
            )


def ensure_tag_main_retired(source: str) -> None:
    try:
        git(["cat-file", "-e", f"{source}:.github/workflows/tag-main.yml"])
    except ReleaseIdentityError:
        return
    raise ReleaseIdentityError(
        "retired tag-main.yml still exists in the requested source"
    )


def max_legacy_tag(tags: list[RemoteTag]) -> RemoteTag:
    if not tags:
        raise ReleaseIdentityError("bootstrap needs at least one legacy canonical tag")
    return max(tags, key=lambda item: item.version)


def validate_helper_tag_binding(tag: RemoteTag) -> None:
    if not tag.annotated or tag.metadata is None:
        raise ReleaseIdentityError(f"{tag.name} lacks helper-era annotated metadata")
    if tag.direct_type != "commit":
        raise ReleaseIdentityError(f"{tag.name} does not directly target a commit")
    if tag.direct_object_oid != tag.peeled_oid:
        raise ReleaseIdentityError(
            f"{tag.name} direct target does not match peeled target"
        )
    if tag.embedded_name != tag.name:
        raise ReleaseIdentityError(f"{tag.name} embedded tag header mismatch")
    if tag.metadata.tag != tag.name:
        raise ReleaseIdentityError(f"{tag.name} metadata tag mismatch")
    if tag.metadata.source != tag.peeled_oid:
        raise ReleaseIdentityError(f"{tag.name} metadata source mismatch")


def validate_state(state_oid: str, tags: list[RemoteTag], main: str) -> ReleaseMetadata:
    raw = git(["cat-file", "commit", state_oid])
    message = raw.split("\n\n", 1)[1] if "\n\n" in raw else ""
    metadata = parse_metadata(message)
    expected = create_token_commit(
        metadata.source, metadata.tag, metadata.ceiling_tag, metadata.ceiling_source
    )
    if expected != state_oid:
        raise ReleaseIdentityError(
            "release-state token does not match deterministic token"
        )
    ensure_source_on_main(metadata.source, main)
    by_name = {tag.name: tag for tag in tags}
    ceiling = by_name.get(metadata.ceiling_tag)
    if ceiling is None or ceiling.peeled_oid != metadata.ceiling_source:
        raise ReleaseIdentityError(
            "frozen legacy ceiling tag is missing or points elsewhere"
        )
    if ceiling.metadata is not None:
        raise ReleaseIdentityError("frozen legacy ceiling cannot be helper-era tag")
    current = by_name.get(metadata.tag)
    if current is None:
        raise ReleaseIdentityError("current helper-era tag is missing")
    validate_helper_tag_binding(current)
    if current.metadata != metadata or current.peeled_oid != metadata.source:
        raise ReleaseIdentityError("current helper-era tag metadata or target mismatch")
    if current.version != max(tag.version for tag in tags):
        raise ReleaseIdentityError(
            "release-state tag is not the greatest canonical tag"
        )
    helper_tags = [tag for tag in tags if tag.metadata is not None]
    for tag in helper_tags:
        validate_helper_tag_binding(tag)
        tag_metadata = tag.metadata
        if tag_metadata is None:
            raise ReleaseIdentityError("helper-era tag lacks metadata")
        if tag.version <= ceiling.version:
            raise ReleaseIdentityError(
                "helper-era tag is not strictly beyond the frozen legacy ceiling"
            )
        if (
            tag_metadata.ceiling_tag != metadata.ceiling_tag
            or tag_metadata.ceiling_source != metadata.ceiling_source
        ):
            raise ReleaseIdentityError(
                "helper-era tag changed the frozen legacy ceiling"
            )

    seen_sources: set[str] = {ceiling.peeled_oid}
    future_tags = [tag for tag in tags if tag.version > ceiling.version]
    previous_source = metadata.ceiling_source
    for tag in future_tags:
        validate_helper_tag_binding(tag)
        tag_metadata = tag.metadata
        if tag_metadata is None:
            raise ReleaseIdentityError("post-ceiling tag lacks helper-era metadata")
        if tag.peeled_oid in seen_sources:
            raise ReleaseIdentityError("duplicate future canonical tag target")
        seen_sources.add(tag.peeled_oid)
        ensure_source_on_main(tag.peeled_oid, main)
        if previous_source == tag.peeled_oid:
            raise ReleaseIdentityError("future release source must strictly descend")
        if not is_first_parent_ancestor(previous_source, tag.peeled_oid):
            raise ReleaseIdentityError(
                "future release source does not descend from previous release"
            )
        previous_source = tag.peeled_oid
    return metadata


def require_clean_helper_main() -> str:
    git(
        [
            "fetch",
            "--prune",
            "--no-tags",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ]
    )
    remote_main = git(["rev-parse", "refs/remotes/origin/main"])
    head = git(["rev-parse", "HEAD"])
    if head != remote_main:
        raise ReleaseIdentityError("helper checkout HEAD is not fetched origin/main")
    if git(["status", "--porcelain=v1", "--untracked-files=all"]):
        raise ReleaseIdentityError("helper checkout must be clean before publication")
    return remote_main


def confirm_destination(*, yes: bool) -> None:
    print(f"Release destination: {CANONICAL_HTTPS_URL}")
    if not yes:
        raise ReleaseIdentityError(
            "owner confirmation required; pass --yes after verifying destination"
        )


def query_old_workflow_runs() -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    page = 1
    total_count: int | None = None
    while True:
        proc = subprocess.run(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/hydrosolutions/SAPPHIRE_flow/actions/workflows/{LEGACY_TAG_MAIN_WORKFLOW_ID}/runs",
                "-f",
                "per_page=100",
                "-f",
                f"page={page}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ReleaseIdentityError("GitHub workflow-run query failed")
        data_raw = json.loads(proc.stdout)
        if not _is_json_object(data_raw):
            raise ReleaseIdentityError(
                "GitHub workflow-run query returned invalid JSON"
            )
        total_raw = data_raw.get("total_count")
        if not isinstance(total_raw, int) or total_raw < 0:
            raise ReleaseIdentityError(
                "GitHub workflow-run query returned invalid total_count"
            )
        if total_count is None:
            total_count = total_raw
        elif total_count != total_raw:
            raise ReleaseIdentityError(
                "GitHub workflow-run query total_count changed during pagination"
            )
        page_runs = _as_object_list(data_raw.get("workflow_runs"))
        for run in page_runs:
            workflow_id = run.get("workflow_id")
            if workflow_id != LEGACY_TAG_MAIN_WORKFLOW_ID:
                raise ReleaseIdentityError(
                    "GitHub workflow-run query returned an unexpected workflow_id"
                )
        runs.extend(page_runs)
        if len(runs) >= total_count:
            if len(runs) != total_count:
                raise ReleaseIdentityError("GitHub workflow-run pagination mismatch")
            return runs
        if len(page_runs) != 100:
            raise ReleaseIdentityError("GitHub workflow-run pagination incomplete")
        page += 1


def validate_old_workflow_runs_from_github(runs: list[dict[str, object]]) -> None:
    for run in runs:
        status = str(run.get("status", "")).lower()
        conclusion = str(run.get("conclusion", "")).lower()
        state = status if status and status != "completed" else conclusion or status
        if status in _NONTERMINAL_RUN_STATES or state in _NONTERMINAL_RUN_STATES:
            raise ReleaseIdentityError(
                f"old tag-main workflow run is nonterminal: {state}"
            )
        if status not in _ALLOWED_RUN_STATES and state not in _ALLOWED_RUN_STATES:
            raise ReleaseIdentityError(
                f"old tag-main workflow run state is unknown: {state}"
            )


def publish(args: argparse.Namespace) -> int:
    version = Version.parse(args.version)
    source = require_full_sha(args.source)
    validate_destination()
    require_clean_helper_main()
    confirm_destination(yes=args.yes)
    main, state, tags = fetch_remote_state()
    ensure_source_on_main(source, main)
    ensure_tag_main_retired(main)
    ensure_tag_main_retired(source)
    helper_tags = [tag for tag in tags if tag.metadata is not None]

    if state is None:
        if not args.bootstrap:
            if helper_tags:
                raise ReleaseIdentityError(
                    "release-state is missing after helper-era evidence; "
                    "stop and escalate"
                )
            raise ReleaseIdentityError(
                "release-state is missing; use deliberate --bootstrap "
                "with old-run evidence"
            )
        validate_old_workflow_runs_from_github(query_old_workflow_runs())
        main, state, tags = fetch_remote_state()
        ensure_source_on_main(source, main)
        ensure_tag_main_retired(main)
        ensure_tag_main_retired(source)
        helper_tags = [tag for tag in tags if tag.metadata is not None]
        if state is not None:
            raise ReleaseIdentityError("release-state appeared during bootstrap checks")
        if helper_tags:
            raise ReleaseIdentityError(
                "bootstrap refused because helper-era tag metadata already exists"
            )
        ceiling = max_legacy_tag(tags)
        if (
            not is_first_parent_ancestor(ceiling.peeled_oid, source)
            or ceiling.peeled_oid == source
        ):
            raise ReleaseIdentityError(
                "bootstrap source must strictly descend from the legacy ceiling source"
            )
        if version <= max(tag.version for tag in tags):
            raise ReleaseIdentityError(
                "bootstrap version must be greater than every canonical tag"
            )
        expected_lease = ""
    else:
        metadata = validate_state(state, tags, main)
        if version <= Version.parse_tag(metadata.tag):
            existing = next((tag for tag in tags if tag.name == version.tag), None)
            if existing is not None and existing.peeled_oid == source:
                print(f"release {version.tag} already published for {source}")
                return 0
            raise ReleaseIdentityError(
                "requested version is not greater than current release state"
            )
        if (
            not is_first_parent_ancestor(metadata.source, source)
            or metadata.source == source
        ):
            raise ReleaseIdentityError(
                "requested source must strictly descend from current released source"
            )
        if any(tag.peeled_oid == source for tag in tags):
            raise ReleaseIdentityError("requested source already has a canonical tag")
        ceiling = next(tag for tag in tags if tag.name == metadata.ceiling_tag)
        expected_lease = state

    token_oid = create_token_commit(
        source, version.tag, ceiling.name, ceiling.peeled_oid
    )
    tag_oid = create_annotated_tag_object(
        version.tag, source, ceiling.name, ceiling.peeled_oid
    )
    git(
        [
            "update-ref",
            f"refs/sapphire-flow-release-identity/new-tags/{version.tag}",
            tag_oid,
        ]
    )
    lease_arg = f"--force-with-lease={STATE_REF}:{expected_lease}"
    try:
        git(
            [
                "push",
                "--atomic",
                lease_arg,
                "origin",
                f"{tag_oid}:refs/tags/{version.tag}",
                f"{token_oid}:{STATE_REF}",
            ]
        )
    except ReleaseIdentityError:
        pushed_main, pushed_state, pushed_tags = fetch_remote_state()
        if pushed_state == token_oid and pushed_state is not None:
            validate_state(pushed_state, pushed_tags, pushed_main)
        else:
            raise
    new_main, new_state, new_tags = fetch_remote_state()
    if new_state != token_oid or new_state is None:
        raise ReleaseIdentityError("post-push release-state did not match token")
    validate_state(new_state, new_tags, new_main)
    print(
        json.dumps(
            {
                "version": version.text,
                "source": source,
                "tag": version.tag,
                "tag_object": tag_oid,
                "state": token_oid,
            },
            sort_keys=True,
        )
    )
    return 0


def ensure_clean_context(source: str) -> str:
    head = git(["rev-parse", "HEAD"])
    if head != source:
        raise ReleaseIdentityError("HEAD does not match requested source")
    if git(["status", "--porcelain=v1", "--untracked-files=all"]):
        raise ReleaseIdentityError(
            "worktree has tracked or untracked build-context changes"
        )
    return git(["rev-parse", f"{source}^{{tree}}"])


def run_docker(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        ["docker", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseIdentityError(
            f"docker {' '.join(args)} failed: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def inspect_image(
    image_tag: str, expected_version: str, expected_source: str
) -> ReceiptImage:
    probe_module = _load_image_probe()
    variant: Literal["default", "aquacast"] = (
        "aquacast" if "aquacast" in image_tag else "default"
    )
    try:
        image = probe_module.probe_image(
            image_tag,
            expected_version,
            expected_source,
            variant=variant,
        )
    except probe_module.ImageIdentityProbeError as exc:
        raise ReleaseIdentityError(str(exc)) from exc
    return ReceiptImage(
        variant=image.variant,
        mutable_tag=image.mutable_tag,
        image_id=image.image_id,
        labels=dict(image.labels),
        runtime_import_version=image.runtime_import_version,
    )


def create_git_tree_context(source: str) -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory(prefix="sapphire-release-context-")
    archive = Path(temp.name) / "context.tar"
    git(["archive", "--format=tar", f"--output={archive}", source])
    shutil.unpack_archive(str(archive), temp.name, "tar")
    archive.unlink()
    generated = Path(temp.name) / "src" / "sapphire_flow" / "_version.py"
    if generated.exists():
        raise ReleaseIdentityError("generated version metadata leaked into context")
    return temp


def atomic_write_receipt(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise ReleaseIdentityError("receipt path already exists") from exc
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def build_receipt(
    args: argparse.Namespace, *, docker_builder: DockerImageBuilder | None = None
) -> int:
    version = Version.parse(args.version)
    source = require_full_sha(args.source)
    receipt_path = Path(args.receipt)
    if receipt_path.exists():
        raise ReleaseIdentityError("receipt path already exists")
    validate_destination()
    main, state, tags = fetch_remote_state()
    if state is None:
        raise ReleaseIdentityError("release-state is required before artifact proof")
    validate_state(state, tags, main)
    tag = next((item for item in tags if item.name == version.tag), None)
    if tag is None or tag.metadata is None or tag.peeled_oid != source:
        raise ReleaseIdentityError(
            "remote helper-era tag does not match version/source"
        )
    tree = ensure_clean_context(source)
    default_tag = f"sapphire-flow:{version.text}"
    aquacast_tag = f"sapphire-flow-aquacast:{version.text}"
    images: list[ReceiptImage] = []
    with create_git_tree_context(source) as context_dir:
        context = Path(context_dir)
        if docker_builder is not None:
            images = docker_builder.build(context, version.text, source)
        else:
            run_docker(
                [
                    "build",
                    "--secret",
                    "id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN",
                    "--build-arg",
                    f"SAPPHIRE_RELEASE_VERSION={version.text}",
                    "--build-arg",
                    f"SAPPHIRE_SOURCE_REVISION={source}",
                    "-t",
                    default_tag,
                    ".",
                ],
                cwd=context,
            )
            images.append(inspect_image(default_tag, version.text, source))
            run_docker(
                [
                    "build",
                    "--secret",
                    "id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN",
                    "--secret",
                    "id=aquacast_token,env=AQUACAST_TOKEN",
                    "--build-arg",
                    "WITH_AQUACAST=1",
                    "--build-arg",
                    f"SAPPHIRE_RELEASE_VERSION={version.text}",
                    "--build-arg",
                    f"SAPPHIRE_SOURCE_REVISION={source}",
                    "-t",
                    aquacast_tag,
                    ".",
                ],
                cwd=context,
            )
            images.append(inspect_image(aquacast_tag, version.text, source))
    if {image.variant for image in images} != {"default", "aquacast"}:
        raise ReleaseIdentityError("receipt requires default and aquacast images")
    refreshed_main, refreshed_state, refreshed_tags = fetch_remote_state()
    if refreshed_state is None:
        raise ReleaseIdentityError("release-state disappeared before receipt write")
    validate_state(refreshed_state, refreshed_tags, refreshed_main)
    refreshed_tag = next(
        (item for item in refreshed_tags if item.name == version.tag), None
    )
    if refreshed_tag is None or refreshed_tag.metadata is None:
        raise ReleaseIdentityError("remote helper-era tag changed before receipt write")
    if refreshed_tag.object_oid != tag.object_oid:
        raise ReleaseIdentityError("release tag object changed before receipt write")
    if refreshed_tag.peeled_oid != source:
        raise ReleaseIdentityError("remote helper-era tag changed before receipt write")
    receipt = ArtifactReceipt(
        package_version=version.text,
        source_revision=source,
        git_tree=tree,
        remote_tag=version.tag,
        remote_tag_object=refreshed_tag.object_oid,
        remote_tag_target=refreshed_tag.peeled_oid,
        release_state=refreshed_state,
        images=images,
    )
    payload = json.dumps(asdict(receipt), indent=2, sort_keys=True) + "\n"
    atomic_write_receipt(receipt_path, payload)
    parsed = json.loads(receipt_path.read_text())
    if parsed["package_version"] != version.text or parsed["source_revision"] != source:
        raise ReleaseIdentityError("receipt readback mismatch")
    if len(parsed.get("images", [])) != 2:
        raise ReleaseIdentityError("receipt readback image count mismatch")
    print(str(receipt_path))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish and verify SAPPHIRE Flow release identity."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("--version", required=True)
    pub.add_argument("--source", required=True)
    pub.add_argument("--bootstrap", action="store_true")
    pub.add_argument("--yes", action="store_true")
    pub.set_defaults(func=publish)
    receipt = sub.add_parser("build-receipt")
    receipt.add_argument("--version", required=True)
    receipt.add_argument("--source", required=True)
    receipt.add_argument("--receipt", required=True)
    receipt.set_defaults(func=build_receipt)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ReleaseIdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
