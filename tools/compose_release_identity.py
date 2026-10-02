#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys

_CANONICAL_RELEASE_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SOURCE_RE = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_SOURCE_RE = re.compile(r"\+g([0-9a-f]{40})(?:\.|$)")


class ComposeReleaseIdentityError(RuntimeError):
    pass


def validate_env(env: dict[str, str]) -> None:
    release_version = env.get("SAPPHIRE_RELEASE_VERSION", "")
    source_revision = env.get("SAPPHIRE_SOURCE_REVISION", "")
    image_tag = env.get("VERSION", "")
    if not release_version:
        raise ComposeReleaseIdentityError("SAPPHIRE_RELEASE_VERSION is required")
    if not _SOURCE_RE.fullmatch(source_revision):
        raise ComposeReleaseIdentityError(
            "SAPPHIRE_SOURCE_REVISION must be a full lowercase SHA"
        )
    if _CANONICAL_RELEASE_RE.fullmatch(release_version):
        if image_tag != release_version:
            raise ComposeReleaseIdentityError(
                "VERSION must equal SAPPHIRE_RELEASE_VERSION for canonical releases"
            )
        return
    local_source = _LOCAL_SOURCE_RE.search(release_version)
    if local_source is None:
        raise ComposeReleaseIdentityError(
            "developer release versions must include a PEP 440 +g<full-sha> "
            "local source"
        )
    if local_source.group(1) != source_revision:
        raise ComposeReleaseIdentityError(
            "SAPPHIRE_RELEASE_VERSION local source must match SAPPHIRE_SOURCE_REVISION"
        )
    if not image_tag:
        raise ComposeReleaseIdentityError(
            "developer builds with PEP 440 local versions need a Docker-safe VERSION"
        )


def main() -> int:
    try:
        validate_env(dict(os.environ))
    except ComposeReleaseIdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
