from collections.abc import Callable
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from sapphire_flow.store.bafu_unit_read_filter import (
    latest_unit_read_predicate,
    unit_read_predicate,
)
from sapphire_flow.types.observation_read import ObservationReadPurpose


class TestLatestUnitReadPredicate:
    def test_only_latest_lookup_keeps_correlation_boundary(self) -> None:
        rows = sa.table("observations", sa.column("id", sa.Uuid))
        ordinary = str(unit_read_predicate(rows).compile(dialect=postgresql.dialect()))
        latest = str(
            latest_unit_read_predicate(rows).compile(dialect=postgresql.dialect())
        )
        assert "OFFSET 0" in latest
        assert "OFFSET" not in ordinary
        assert latest.replace("\n LIMIT ALL OFFSET 0", "") == ordinary

    @pytest.mark.parametrize(
        "predicate", [unit_read_predicate, latest_unit_read_predicate]
    )
    @pytest.mark.parametrize(
        ("purpose", "message"),
        [
            (ObservationReadPurpose.SOURCE_AUDIT, "restricted D2"),
            ("trusted", "must be typed"),
        ],
    )
    def test_rejects_invalid_read_purpose(
        self,
        predicate: Callable[
            [sa.FromClause, ObservationReadPurpose], sa.ColumnElement[bool]
        ],
        purpose: object,
        message: str,
    ) -> None:
        rows = sa.table("observations", sa.column("id", sa.Uuid))
        with pytest.raises(ValueError, match=message):
            predicate(rows, cast("ObservationReadPurpose", purpose))
