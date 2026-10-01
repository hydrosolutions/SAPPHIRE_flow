from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.enums import ForecastDataUse


def forecast_columns(
    table: sa.Table, data_use: ForecastDataUse
) -> list[sa.ColumnElement[Any]]:
    """Ordinary readers never request protected consumed-input content."""
    return [
        sa.cast(sa.null(), sa.Text).label("input_lineage")
        if column.name == "input_lineage" and data_use is ForecastDataUse.STANDARD
        else column
        for column in table.columns
    ]


@contextmanager
def protect_lineage_errors(data_use: ForecastDataUse) -> Iterator[None]:
    try:
        yield
    except SQLAlchemyError:
        if data_use is ForecastDataUse.STANDARD:
            raise
        raise StoreError("protected forecast persistence or retrieval failed") from None
