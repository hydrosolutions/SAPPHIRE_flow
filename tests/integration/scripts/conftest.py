"""Test isolation for the Plan 188 CLI integration tests.

These tests are the exception to this suite's usual isolation rule. Every other
integration module takes the `db_connection` fixture, which wraps each test in a
transaction and **rolls it back** (`tests/integration/conftest.py`). These cannot:
the CLI under test builds its *own* engine from `DATABASE_URL` and opens its own
transaction — that structural independence is the point of D3's rollback sentinel
— so it cannot join a test-owned transaction, and the rows it writes must really
commit for the assertions to mean anything.

Committing into a session-scoped engine leaks across modules. It did: seeded
stations survived into
`store/test_station_store.py::TestFetchAllWithKindFilter::test_empty_returns_empty_list`,
which asserts an empty fleet, and CI failed on PR #217. It passed locally only
because the caravan tests had been run on their own — the full suite reproduces it.

So these tests clean up after themselves. Every row they create is keyed by the
`T188CLI-` code prefix, and this fixture removes exactly those, before and after
each test — before as well, so a previously crashed run cannot poison the next one.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

_TEST_CODE_PREFIX = "T188CLI-"


def _purge(engine: sa.Engine) -> None:
    """Delete only rows this module's helpers create, in dependency order.

    `basins` is referenced from three directions — `stations.basin_id`,
    `basin_versions.basin_id` (which `store_basin` writes as a side effect) and
    `recap_gateway_polygon_bindings` — so deleting basins first trips a foreign
    key. Children first, parents last."""
    like = {"p": f"{_TEST_CODE_PREFIX}%"}
    basin_ids = sa.text("SELECT id FROM basins WHERE code LIKE :p")
    with engine.connect() as conn:
        conn.execute(sa.text("DELETE FROM stations WHERE code LIKE :p"), like)
        conn.execute(
            sa.text(
                "DELETE FROM model_artifact_basin_versions WHERE basin_version_id IN "
                f"(SELECT id FROM basin_versions WHERE basin_id IN ({basin_ids.text}))"
            ),
            like,
        )
        conn.execute(
            sa.text(f"DELETE FROM basin_versions WHERE basin_id IN ({basin_ids.text})"),
            like,
        )
        conn.execute(
            sa.text(
                "DELETE FROM recap_gateway_polygon_bindings WHERE basin_id IN "
                f"({basin_ids.text})"
            ),
            like,
        )
        conn.execute(sa.text("DELETE FROM basins WHERE code LIKE :p"), like)
        conn.commit()


@pytest.fixture(autouse=True)
def _isolate_cli_test_rows(db_engine: sa.Engine):
    _purge(db_engine)
    yield
    _purge(db_engine)
