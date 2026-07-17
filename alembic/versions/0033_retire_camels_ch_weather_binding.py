"""retire the camels-ch weather-source binding (Plan 115b4 §5E, Release B)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-17

Plan 115b4 §5B/§5D (Release A) retired the ``... -> CAMELS_CH`` tier from
every hybrid priority chain and flipped the reanalysis default to
``hybrid``. With ``single`` no longer the default, a ``camels-ch``
``station_weather_sources`` binding is now dead weight: it can never
resolve a row through the (CAMELS-tier-free) hybrid chain, and its presence
next to a MeteoSwiss binding for the same station is confusing, not useful.

**Ships as a SEPARATE, LATER release from Release A** (owner decision,
round-1 blockers 1+2 — see docs/plans/115b4-reader-flip-cutover.md §5E): the
repo runs ``alembic upgrade head`` in the ``init`` container BEFORE workers
start, so bundling this retire migration into the SAME head as the flip
would fire it before anyone has confirmed the hybrid reader is actually
serving. Deploy this revision ONLY after Release A (5A-5D + phase-6) is
confirmed serving past-dynamic features on staging.

This upgrade deletes ONLY the ``station_weather_sources`` rows where
``nwp_source = 'camels-ch'`` — it does NOT touch ``historical_forcing``.
CAMELS-CH forcing ROWS remain untouched, readable by a direct source-keyed
fetch (``PerSourceStoreReader`` / ``HistoricalForcingStore.fetch_forcing``),
and serve as the Plan 115b3 validation reference + audit trail. CAMELS-CH
also remains the runoff/discharge + static-attribute + basin-polygon source
— only the *weather* binding is retired here.

**Rollback is the repo's standard path (backup restore + previous image,
docs/standards/cicd.md) — NOT this migration's ``downgrade()``.** The
deleted rows' station set cannot be reconstructed from what remains in the
database (the binding shape is deterministic per Plan 115 §onboarding, but
WHICH stations had one is exactly the information this delete destroys), so
``downgrade()`` is a deliberate NO-OP (logs a warning) rather than
fabricating a false sense of reversibility by pretending to restore rows it
cannot know the identity of. It does NOT raise: alembic's migration chain
must stay mechanically traversable end-to-end (other migrations' downgrade
paths — and generic tooling — walk THROUGH this revision, not just to it),
and a raise here would break that for every revision above ``0033``.

NOTE for the Plan 115c implementer: this migration lands as ``0033`` — the
earmark left in ``0032``'s docstring predates this landing (Plan 115b4
lands first). 115c's ``station_weather_sources.role`` NOT NULL tightening
must take the NEXT free revision after this one (``0034``), not ``0033``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import structlog

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAMELS_CH_SOURCE = "camels-ch"

log = structlog.get_logger(__name__)


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM station_weather_sources WHERE nwp_source = :source"
        ).bindparams(source=_CAMELS_CH_SOURCE)
    )


def downgrade() -> None:
    # Deliberate no-op — see module docstring. Do NOT attempt to resurrect
    # camels-ch bindings here: which stations had one is exactly the
    # information upgrade() destroyed, so any "restore" would be fabricated.
    # Real rollback is backup-restore + previous image (docs/standards/cicd.md).
    log.warning(
        "migration.0033_downgrade_is_a_noop",
        reason=(
            "camels-ch weather bindings deleted by 0033 cannot be "
            "reconstructed; roll back via backup-restore, not alembic "
            "downgrade"
        ),
    )
