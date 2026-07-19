"""Plan 115b5 (Release B) — post-Release-B Alembic head invariant.

Supersedes the pre-Release-B guard (Plan 115b4 §5E) that asserted revision
`0033` (the camels-ch weather-binding retirement) was ABSENT from `main`'s
head — that invariant existed only to keep the two-release choreography
honest (Release B must never share a deploy with the un-confirmed Release A
reader flip). Release A was confirmed serving on staging 2026-07-17 and
Release B (this migration) has now landed, so that invariant is retired
along with the guard that enforced it.

This test keeps the cheap, DB-free, CI-enforceable shape of its predecessor
(walks ``alembic/versions/`` directly, no DB, no Alembic ``ScriptDirectory``
machinery) and pins the ONE thing that still matters at this level: Alembic
has exactly one head and it is `0033`. A rogue migration branching off an
earlier revision (instead of chaining onto `0033`) would give Alembic
multiple heads at deploy time — an opaque runtime error this test catches
statically instead.

The migration's actual BEHAVIOUR (guard raises when a station has no
replacement reanalysis binding; the guard/delete predicate matches the
reader's effective membership rule incl. NULL-role/inactive edge cases;
`historical_forcing` rows survive; the retire deletes only the camels-ch
binding) is pinned by the DB-backed integration tests in
``tests/integration/db/test_migration_0033_camels_retire.py`` — a real
Postgres container is required to exercise `upgrade()`/`downgrade()`, which
this file deliberately does not do.
"""

from __future__ import annotations

from pathlib import Path

_ALEMBIC_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

# Release B's confirmed Alembic head was 0033 (Plan 115b5 — the camels-ch
# weather-binding retirement). Plan 035 Task 1 chained migration 0034
# (rating_curves table) onto 0033, and Task 2 chained 0035 (observation +
# forecast rating-curve binding) onto 0034 — deliberate, reviewed migration
# additions per the guidance below — advancing the pinned head to 0035.
_RELEASE_B_HEAD = "0035"


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


class TestReleaseBAlembicHeadIsSingleAndCurrent:
    def test_alembic_head_is_release_b_head(self) -> None:
        # No revision's down_revision points at _RELEASE_B_HEAD, and
        # _RELEASE_B_HEAD itself is a revision that exists — i.e. 0033 is a
        # true leaf (the current head) on this branch.
        graph = _down_revisions()
        assert _RELEASE_B_HEAD in graph, (
            f"expected revision {_RELEASE_B_HEAD} to exist as Release B's head"
        )
        children = [rev for rev, down in graph.items() if down == _RELEASE_B_HEAD]
        assert children == [], (
            f"revision {_RELEASE_B_HEAD} is no longer Alembic's head — "
            f"{children} build on top of it. Update _RELEASE_B_HEAD only as "
            "part of a deliberate, reviewed migration addition."
        )

    def test_alembic_has_exactly_one_head_and_it_is_release_b(self) -> None:
        # A leaf is any revision that is not itself a down_revision of some
        # other revision. Checking only "nothing builds on 0033" (the test
        # above) misses a rogue migration that branches off an EARLIER
        # revision (e.g. down_revision = "0031") — that would create a
        # SECOND head while leaving 0033 untouched, and Alembic would refuse
        # to run (multiple heads) even though the check above still passes.
        # Assert the full leaf set is exactly {0033} so any such stray branch
        # is caught here instead of surfacing as an opaque Alembic runtime
        # error during deploy.
        graph = _down_revisions()
        leaves = set(graph) - {down for down in graph.values() if down is not None}
        assert leaves == {_RELEASE_B_HEAD}, (
            f"expected exactly one Alembic head ({_RELEASE_B_HEAD}), found "
            f"{sorted(leaves)} — a migration branches off an earlier "
            "revision instead of chaining onto Release B's head, which "
            "would give Alembic multiple heads at deploy time."
        )
