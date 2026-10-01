from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import measurement_feed_evidence, observations
from sapphire_flow.store.rating_reference_store import PgRatingReferenceStore
from tests.integration.store.test_provisional_discharge_store import (
    seed,
    seed_reference,
)


class TestRatingReferenceStore:
    def test_absent_proof_and_feed_return_none(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgRatingReferenceStore(db_connection)
        assert store.fetch_feed_evidence(uuid4()) is None
        assert store.fetch_reference_proof(uuid4()) is None

    def test_new_feed_evidence_cannot_attest_stale_snapshot(
        self, db_connection: sa.Connection
    ) -> None:
        _, _, feed, _ = seed(db_connection)
        db_connection.execute(sa.update(observations).values(value=1.7))
        with pytest.raises(sa.exc.DBAPIError, match="persisted measurement"):
            seed_reference(
                db_connection, measurement_feed_evidence, replace(feed, id=uuid4())
            )

    def test_foreign_observation_cannot_be_bound(
        self, db_connection: sa.Connection
    ) -> None:
        _, _, feed, _ = seed(db_connection)
        with pytest.raises(ValueError, match="identity mismatch"):
            replace(feed, observation_id=uuid4())
