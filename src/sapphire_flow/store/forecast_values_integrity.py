from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from sapphire_flow.types.ids import ForecastId


def forecast_values_integrity(
    connection: Connection, forecast_id: ForecastId
) -> tuple[int, str]:
    row = connection.execute(
        sa.text(
            "SELECT count(*) AS value_count, "
            "encode(sha256(convert_to(COALESCE(jsonb_agg("
            "jsonb_build_array(id, issued_at, valid_time, lead_time_hours, "
            "member_id, quantile, value) ORDER BY id)::text, '[]'), "
            "'UTF8')), 'hex') AS value_hash "
            "FROM forecast_values WHERE forecast_id = :forecast_id"
        ),
        {"forecast_id": forecast_id},
    ).one()
    return row.value_count, row.value_hash
