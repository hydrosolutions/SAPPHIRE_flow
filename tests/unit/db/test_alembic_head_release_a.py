"""Plan 115b4 §5E — enforces the two-sequenced-releases choreography at the
repo level, not just in docs.

The camels-ch weather-binding retirement migration (Release B, revision
`0033`) must NEVER share `main`'s Alembic head with Release A's reader flip
(5A-5D + phase-6) — `init` runs ``alembic upgrade head`` before any
worker/API confirms the new hybrid reader is actually serving
(``docker-compose.yml`` init container, ``docs/standards/cicd.md``). A
standard ``alembic upgrade head`` deploy on a branch that already carries the
retire migration would apply it in the SAME deploy as the flip, with no
operator confirmation step in between — exactly the choreography the
two-release split (owner decision, 115b4 round-1 blockers 1+2) exists to
prevent.

This is a cheap, DB-free, CI-enforceable guard: it walks
``alembic/versions/`` directly and fails loudly if a camels-ch-retirement
migration (or any migration downstream of Release A's head) has been
re-introduced onto this branch before Release B (Plan 115b5) is confirmed
ready to merge. Release B's migration ships on a separate branch
(``docs/plans/115b5-camels-ch-retire-migration.md``) and this test is
expected to be deleted/updated only as part of THAT merge, after the
Release-A staging deploy-gate has passed.
"""

from __future__ import annotations

from pathlib import Path

_ALEMBIC_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

# Release A's confirmed Alembic head (5A-5D + phase-6, Plan 115b4). No
# revision beyond this may exist on this branch until Release B (115b5) is
# merged AFTER the Release-A staging deploy-gate passes.
_RELEASE_A_HEAD = "0032"


def _revision_ids() -> set[str]:
    return {
        path.name.split("_", 1)[0]
        for path in _ALEMBIC_VERSIONS_DIR.glob("*.py")
        if path.stem != "__init__"
    }


def _down_revisions() -> dict[str, str | None]:
    """Map ``revision -> down_revision`` by importing each migration module's
    top-level string literals — cheap static parse, no DB, no alembic
    ``ScriptDirectory`` machinery required for this narrow check."""
    import ast

    graph: dict[str, str | None] = {}
    for path in _ALEMBIC_VERSIONS_DIR.glob("*.py"):
        if path.stem == "__init__":
            continue
        tree = ast.parse(path.read_text())
        revision: str | None = None
        down_revision: str | None = None
        for node in ast.walk(tree):
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if node.value is None or not isinstance(node.value, ast.Constant):
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "revision":
                    revision = node.value.value
                if target.id == "down_revision":
                    down_revision = node.value.value
        if revision is not None:
            graph[revision] = down_revision
    return graph


class TestReleaseAAlembicHeadHasNoRetireMigration:
    def test_camels_ch_retire_migration_is_absent_from_main(self) -> None:
        # The specific 115b4 round-1/round-2 blocker: revision 0033 (or any
        # migration authored to retire the camels-ch weather binding) must
        # not exist as a file on this branch. It ships on a separate branch
        # (115b5) merged only after Release A is confirmed serving.
        camels_retire_files = [
            path
            for path in _ALEMBIC_VERSIONS_DIR.glob("*.py")
            if "retire" in path.stem and "camels" in path.stem
        ]
        assert camels_retire_files == [], (
            "A camels-ch retire migration is present on this branch "
            f"({[p.name for p in camels_retire_files]}) — Plan 115b4 §5E "
            "requires it ship as a SEPARATE, LATER release (115b5), merged "
            "only after the Release-A staging deploy-gate passes."
        )

    def test_alembic_head_is_release_a_head(self) -> None:
        # No revision's down_revision points at _RELEASE_A_HEAD, and
        # _RELEASE_A_HEAD itself is a revision that exists — i.e. 0032 is a
        # true leaf (the current head) on this branch.
        graph = _down_revisions()
        assert _RELEASE_A_HEAD in graph, (
            f"expected revision {_RELEASE_A_HEAD} to exist as Release A's head"
        )
        children = [rev for rev, down in graph.items() if down == _RELEASE_A_HEAD]
        assert children == [], (
            f"revision {_RELEASE_A_HEAD} is no longer Alembic's head — "
            f"{children} build on top of it. If this is Release B (115b5) "
            "landing, update _RELEASE_A_HEAD only as part of that gated "
            "merge, not incidentally."
        )
