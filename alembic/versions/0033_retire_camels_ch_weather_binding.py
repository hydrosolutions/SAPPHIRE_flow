"""retire the camels-ch weather binding (Release B, in-migration guard)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-18

Plan 115b5 — Release B of the 115b4 two-release cutover. Retires the
``camels-ch`` ``station_weather_sources`` REANALYSIS binding now that
Release A (the hybrid MeteoSwiss-priority reader) is confirmed serving on
staging.

GUARD (SELECT + raise, same transaction as the DELETE — precedent
``0023_add_regional_basin_and_unique_constraint.py`` /
``0030_weather_source_role.py``, both wrapped by Alembic's single online
migration transaction, ``alembic/env.py:39-47``): a reanalysis binding
supplies the hybrid reader's per-station MEMBERSHIP, not just a source tag —
``fetch_reanalysis_bindings`` -> ``PerSourceStoreReader`` reduces configs to
station_ids before fetching (``per_source_store_reader.py:47-60``). Deleting
camels-ch is only safe for a station that ALSO has a surviving non-camels-ch
reanalysis binding (115b2 §2A backfilled
``meteoswiss_open_data_reanalysis``) — the guard PROVES that per-station
rather than assuming it. If any station would be left with no reanalysis
binding, the whole transaction raises and rolls back (nothing deleted).

The predicate matches the reader's EFFECTIVE membership rule
(``store/station_store.py``'s ``_row_to_weather_source`` /
``_legacy_role_for_source``), not a naive SQL filter:
- no ``status`` filter — ``fetch_reanalysis_bindings`` does not filter by
  status, so an INACTIVE camels-ch binding is still a binding to the reader.
- a NULL ``role`` on a ``camels-ch`` row is still REANALYSIS — the legacy
  backfill allowlist maps ``camels-ch``'s NULL role to REANALYSIS
  (``_KNOWN_REANALYSIS_SOURCES = {"camels-ch"}``). A NULL role on any OTHER
  source is NOT treated as reanalysis here (the only other known legacy
  source, ``icon_ch2_eps``, is FORECAST) — this matches
  ``_legacy_role_for_source`` exactly rather than guessing NULL == reanalysis
  universally.

Does NOT touch ``historical_forcing`` (``db/metadata.py:417-424``) — CAMELS
forcing rows stay as the 115b3 validation reference + audit trail; CAMELS
remains the runoff/discharge + static-attribute + basin-polygon source. Only
the weather BINDING is retired.

``downgrade()`` is a deliberate NO-OP: the deleted rows' station set cannot
be honestly reconstructed from schema state alone, and the locked
full-chain downgrade test (``tests/integration/db/test_migration_0026_downgrade.py``)
walks every revision's ``downgrade()`` from head, so a ``raise`` here would
break it. Rollback = restore the DB backup + previous image
(``docs/standards/cicd.md`` § Rollback), not a schema downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import structlog

from alembic import op

log = structlog.get_logger(__name__)

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A station_weather_sources row the reader treats as a camels-ch reanalysis
# binding: nwp_source='camels-ch' with an explicit role='reanalysis', OR a
# legacy NULL role (camels-ch's only legacy mapping is reanalysis).
_STRANDED_STATIONS_SQL = sa.text(
    """
    SELECT DISTINCT s.station_id
    FROM station_weather_sources s
    WHERE s.nwp_source = 'camels-ch'
      AND (s.role = 'reanalysis' OR s.role IS NULL)
      AND NOT EXISTS (
        SELECT 1 FROM station_weather_sources o
        WHERE o.station_id = s.station_id
          AND o.nwp_source <> 'camels-ch'
          AND o.role = 'reanalysis'
      )
    """
)

_DELETE_CAMELS_CH_BINDING_SQL = sa.text(
    "DELETE FROM station_weather_sources "
    "WHERE nwp_source = 'camels-ch' AND (role = 'reanalysis' OR role IS NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()

    stranded = bind.execute(_STRANDED_STATIONS_SQL).scalars().all()
    if stranded:
        raise RuntimeError(
            "migration 0033: refusing to retire the camels-ch weather binding — "
            f"station(s) {sorted(str(s) for s in stranded)} would be left with "
            "no surviving reanalysis binding (no non-camels-ch reanalysis-role "
            "weather source). Assign a replacement binding (e.g. "
            "meteoswiss_open_data_reanalysis) for these stations before retrying."
        )

    op.execute(_DELETE_CAMELS_CH_BINDING_SQL)


def downgrade() -> None:
    # Deliberate no-op — see module docstring. Resurrects nothing; does not
    # raise, so the migration chain stays mechanically traversable.
    log.warning(
        "migration_0033.downgrade_is_a_noop",
        detail=(
            "0033 downgrade does not resurrect the deleted camels-ch "
            "weather-binding rows — their station set cannot be honestly "
            "reconstructed from schema state alone. Rollback = restore the "
            "DB backup + previous image, not a schema downgrade."
        ),
    )
