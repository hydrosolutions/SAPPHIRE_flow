from __future__ import annotations

import errno
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa

from sapphire_flow.api.deps import get_stores

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def sqlite_conn() -> Iterator[sa.Connection]:
    # get_stores constructs several stores that read `conn.engine.begin` at
    # __init__ time (never actually executing a query) — a real Connection
    # backed by an in-memory sqlite engine satisfies that without needing
    # Postgres.
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        yield conn
    engine.dispose()


class TestGetStoresReadOnlyDataRoot:
    """Plan 133 — the dependency-level regression that reproduces the outage.

    `api/deps.py::get_stores` resolves the artifact dir under a read-only
    data root (the real `read_only: true` container posture: only
    `/data/artifacts` is mounted, `/data/raw` and `/data/cache` do not
    exist and cannot be created). Must not raise.

    Soundness: this test fails against the pre-fix `config/paths.py` (the
    un-tolerated EROFS from the eager `mkdir("raw")` propagates out of
    `get_stores` as an uncaught OSError, matching the live 500).
    """

    def test_get_stores_does_not_raise_under_read_only_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        sqlite_conn: sa.Connection,
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        (data_root / "artifacts").mkdir(mode=0o750)
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(data_root))

        original_mkdir = Path.mkdir

        def fake_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
            if self.name in ("raw", "cache"):
                raise OSError(errno.EROFS, "Read-only file system")
            original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir)

        stores = get_stores(conn=sqlite_conn)

        assert "artifact_store" in stores
        assert not (data_root / "raw").exists()
        assert not (data_root / "cache").exists()
