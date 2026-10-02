from __future__ import annotations

from typing import Protocol

from setuptools import setup


class ScmVersionLike(Protocol):
    exact: bool
    node: str | None

    def format_choice(
        self,
        clean_format: str,
        dirty_format: str,
        **kwargs: object,
    ) -> str: ...


def full_revision_local_scheme(version: ScmVersionLike) -> str:
    if version.exact:
        return ""
    if version.node is None:
        raise ValueError("Git revision is required for development versions")
    return version.format_choice(
        "+{full_node}",
        "+{full_node}.d{time:%Y%m%d}",
        full_node=version.node,
    )


setup(use_scm_version={"local_scheme": full_revision_local_scheme})
