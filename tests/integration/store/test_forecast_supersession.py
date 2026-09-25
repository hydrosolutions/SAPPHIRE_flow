"""Plan 328 T2/T3 — supersede, replace, and stop the readers serving the old row.

Plan 327's decision table is consumed BY ROW NUMBER. Rows 1 and 2 reach this
module; ⛔ row 3 does not — it stays refused by 327 and its test lives in
``test_forecast_store_retry.py``.
"""

from __future__ import annotations

import random
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from sapphire_flow.db.metadata import forecasts as forecasts_table
from sapphire_flow.store import forecast_store as forecast_store_module
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import ForecastStatus
from sapphire_flow.types.forecast_evidence import incomplete_evidence
from sapphire_flow.types.ids import ForecastId
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _ISSUED_B,
    _capturing_spy_factory,  # noqa: PLC2701
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)


def _store(conn: sa.Connection) -> PgForecastStore:
    return PgForecastStore(conn, transaction_factory=savepoint_factory(conn))


def _status_of(conn: sa.Connection, forecast_id: ForecastId) -> str:
    return conn.execute(
        sa.select(forecasts_table.c.status).where(forecasts_table.c.id == forecast_id)
    ).scalar_one()


def _mark_superseded_directly(conn: sa.Connection, forecast_id: ForecastId) -> None:
    """Seed the READER fixtures' state without going through a replacement.

    🔑 The reader tests need a forecast that is superseded and has NO current
    sibling under its key, which ``store_forecast`` cannot produce — it always
    writes the replacement in the same transaction. Seeding the row state
    directly is what makes those tests deterministic by FIXTURE instead of by
    tie-break luck.
    """
    conn.execute(
        sa.update(forecasts_table)
        .where(forecasts_table.c.id == forecast_id)
        .values(status=ForecastStatus.SUPERSEDED.value)
    )


class TestSupersedeAndReplace:
    def test_row_1_differing_values_supersedes_and_replaces(
        self, db_connection: sa.Connection
    ) -> None:
        """RED before T2: ``store_forecast`` raised
        ``ForecastRetryConflictError`` for a differing same-key re-run and
        wrote nothing (``test_forecast_store.py`` ::
        ``test_unique_constraint_rejects_duplicate_param``)."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = _make_forecast(sid, mid, aid, rng=random.Random(101))
        original_id = store.store_forecast(original)

        replacement = _make_forecast(sid, mid, aid, rng=random.Random(102))
        replacement_id = store.store_forecast(replacement)

        assert replacement_id == replacement.id
        assert replacement_id != original_id
        assert _status_of(db_connection, original_id) == "superseded"
        assert _status_of(db_connection, replacement_id) == "raw"

        # The original is still on record, with ITS OWN values.
        kept = store.fetch_forecast(original_id)
        assert kept is not None
        assert kept.status is ForecastStatus.SUPERSEDED
        assert kept.ensemble.values.equals(original.ensemble.values)

        current = store.fetch_forecast(replacement_id)
        assert current is not None
        assert current.ensemble.values.equals(replacement.ensemble.values)

    def test_row_2_artifact_differs_supersedes_and_replaces(
        self, db_connection: sa.Connection
    ) -> None:
        """Same numbers, different model version — not the same forecast."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        other_model = _seed_model(db_connection, "linreg_v2")
        other_aid = _seed_artifact(db_connection, sid, other_model)
        store = _store(db_connection)

        original = _make_forecast(sid, mid, aid, rng=random.Random(103))
        original_id = store.store_forecast(original)

        replacement = replace(
            original, id=ForecastId(uuid4()), model_artifact_id=other_aid
        )
        replacement_id = store.store_forecast(replacement)

        assert replacement_id == replacement.id
        assert _status_of(db_connection, original_id) == "superseded"
        assert _status_of(db_connection, replacement_id) == "raw"

    def test_the_originals_evidence_survives_untouched(
        self, db_connection: sa.Connection
    ) -> None:
        """🔴 An obligation, not a choice: migration 0057 rejects UPDATE and
        DELETE on ``forecast_evidence``."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(104)),
            evidence=incomplete_evidence("original_capture"),
        )
        original_id = store.store_forecast(original)
        before = store.fetch_evidence(original_id)
        assert before is not None

        replacement = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(105)),
            evidence=incomplete_evidence("replacement_capture"),
        )
        replacement_id = store.store_forecast(replacement)

        after = store.fetch_evidence(original_id)
        assert after is not None
        assert after.reason == before.reason
        assert after.manifest_json == before.manifest_json
        assert "replacement_capture" not in (after.reason or "")

        # The replacement gets its OWN evidence.
        replacement_evidence = store.fetch_evidence(replacement_id)
        assert replacement_evidence is not None
        assert "replacement_capture" in (replacement_evidence.reason or "")

    def test_an_identical_rerun_supersedes_nothing(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = _make_forecast(sid, mid, aid, rng=random.Random(106))
        original_id = store.store_forecast(original)

        returned = store.store_forecast(replace(original, id=ForecastId(uuid4())))

        assert returned == original_id
        assert _status_of(db_connection, original_id) == "raw"
        assert (
            db_connection.execute(
                sa.select(sa.func.count())
                .select_from(forecasts_table)
                .where(forecasts_table.c.status == "superseded")
            ).scalar_one()
            == 0
        )

    def test_interrupted_between_the_mark_and_the_insert_keeps_the_original(
        self, db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 The specific rollback assertion: the ORIGINAL REMAINS CURRENT
        with its values and evidence intact, and the replacement is ABSENT.
        ⛔ Not "neither survives" — the original pre-existed."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(107)),
            evidence=incomplete_evidence("original_capture"),
        )
        original_id = store.store_forecast(original)
        evidence_before = store.fetch_evidence(original_id)

        def _boom(_: object) -> dict[str, object]:
            raise RuntimeError("interrupted between the mark and the insert")

        monkeypatch.setattr(forecast_store_module, "_forecast_row", _boom)

        replacement = _make_forecast(sid, mid, aid, rng=random.Random(108))
        with pytest.raises(RuntimeError, match="interrupted between"):
            store.store_forecast(replacement)

        monkeypatch.undo()

        assert _status_of(db_connection, original_id) == "raw"
        survivor = store.fetch_forecast(original_id)
        assert survivor is not None
        assert survivor.status is ForecastStatus.RAW
        assert survivor.ensemble.values.equals(original.ensemble.values)
        assert store.fetch_evidence(original_id) == evidence_before
        assert store.fetch_forecast(replacement.id) is None


class TestTheMarkSharesTheReplacementsTransaction:
    """🔴 The rollback test alone does NOT prove this.

    Its `savepoint_factory` nests a savepoint on the SAME connection the store
    holds as ``self._conn``, so a mark routed through ``self._conn`` still
    rolls back *in that fixture* — while in production ``self._begin`` is
    ``conn.engine.begin``, a DIFFERENT connection, and the flows' connection is
    AUTOCOMMIT: the mark would commit on its own and survive the failure it
    was supposed to roll back with.

    The spy records only what is executed through the INJECTED handle, so a
    statement issued on ``self._conn`` is invisible to it. Asserting the
    supersession UPDATE appears in the SAME spy as the replacement INSERT is
    what pins mark and insert to one transaction.
    """

    def test_the_supersession_update_goes_through_the_injected_txn(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)

        store = _store(db_connection)
        original_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(131))
        )

        spies, factory = _capturing_spy_factory(db_connection)
        replacing_store = PgForecastStore(db_connection, transaction_factory=factory)
        replacement = _make_forecast(sid, mid, aid, rng=random.Random(132))
        replacing_store.store_forecast(replacement)

        assert len(spies) == 1, "the replacement must use exactly ONE transaction"
        spy = spies[0]
        updates = [
            stmt
            for stmt in spy.executed
            if isinstance(stmt, sa.Update)
            and getattr(getattr(stmt, "table", None), "name", None) == "forecasts"
        ]
        inserts = [
            stmt
            for stmt in spy.executed
            if isinstance(stmt, sa.Insert)
            and getattr(getattr(stmt, "table", None), "name", None) == "forecasts"
        ]

        # ⛔ A mark issued on `self._conn` instead of the injected handle would
        # leave this list EMPTY while every assertion about the final row state
        # still passed.
        assert len(updates) == 1, (
            f"the supersession UPDATE on `forecasts` did not go through the "
            f"injected transaction — spy recorded {len(updates)} such updates"
        )
        assert len(inserts) == 1, "the replacement INSERT must share that spy"
        assert spy.executed.index(updates[0]) < spy.executed.index(inserts[0]), (
            "the mark must precede the replacement insert"
        )
        assert _status_of(db_connection, original_id) == "superseded"

    def test_an_identical_rerun_issues_no_update_at_all(
        self, db_connection: sa.Connection
    ) -> None:
        """Row 4 writes nothing — no mark either."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)

        store = _store(db_connection)
        original = _make_forecast(sid, mid, aid, rng=random.Random(133))
        store.store_forecast(original)

        spies, factory = _capturing_spy_factory(db_connection)
        resuming = PgForecastStore(db_connection, transaction_factory=factory)
        resuming.store_forecast(replace(original, id=ForecastId(uuid4())))

        assert not [stmt for stmt in spies[0].executed if isinstance(stmt, sa.Update)]


class TestReadersStopServingTheSupersededRow:
    """T3 — the part a schema-only change would have missed.

    🔑 Every fixture here seeds a superseded row whose key has NO current
    sibling, so the reader has exactly one candidate to get wrong. An
    original and its replacement share an ``issued_at``, so a test built on
    the pair would be decided by row ordering, not by the filter.
    """

    def test_fetch_latest_forecast_returns_none_when_the_only_candidate_is_superseded(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        only = _make_forecast(sid, mid, aid, rng=random.Random(111))
        only_id = store.store_forecast(only)
        _mark_superseded_directly(db_connection, only_id)

        assert store.fetch_latest_forecast(sid, mid, "discharge") is None

    def test_fetch_latest_forecast_returns_the_replacement(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(112))
        )
        replacement = _make_forecast(sid, mid, aid, rng=random.Random(113))
        store.store_forecast(replacement)

        latest = store.fetch_latest_forecast(sid, mid, "discharge")
        assert latest is not None
        assert latest.id == replacement.id
        assert latest.id != original_id

    def test_fetch_forecasts_for_cycle_excludes_the_superseded_id_by_identity(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(114))
        )
        replacement = _make_forecast(sid, mid, aid, rng=random.Random(115))
        store.store_forecast(replacement)

        returned = {f.id for f in store.fetch_forecasts_for_cycle(_ISSUED_A, sid)}

        assert original_id not in returned
        assert returned == {replacement.id}

    def test_fetch_forecasts_for_cycle_returns_nothing_when_all_are_superseded(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        only_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(116))
        )
        _mark_superseded_directly(db_connection, only_id)

        assert store.fetch_forecasts_for_cycle(_ISSUED_A, sid) == []

    def test_fetch_forecasts_in_range_excludes_superseded_unless_asked_for_it(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        only_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(117))
        )
        _mark_superseded_directly(db_connection, only_id)

        assert store.fetch_forecasts_in_range(sid, _ISSUED_A, _ISSUED_B) == []
        asked = store.fetch_forecasts_in_range(
            sid, _ISSUED_A, _ISSUED_B, status=ForecastStatus.SUPERSEDED
        )
        assert [f.id for f in asked] == [only_id]

    def test_fetch_latest_uncombined_issued_at_ignores_a_superseded_row(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        only_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(118))
        )
        _mark_superseded_directly(db_connection, only_id)

        assert store.fetch_latest_uncombined_issued_at(_ISSUED_B) is None

    def test_by_id_access_is_preserved_and_distinguishable(
        self, db_connection: sa.Connection
    ) -> None:
        """⚠️ § (6): the evidence is permanent, so the forecast that carries
        it must stay readable — and must read as superseded, not as current."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(119)),
            evidence=incomplete_evidence("original_capture"),
        )
        original_id = store.store_forecast(original)
        store.store_forecast(_make_forecast(sid, mid, aid, rng=random.Random(120)))

        by_id = store.fetch_forecast(original_id)
        assert by_id is not None
        assert by_id.status is ForecastStatus.SUPERSEDED
        assert by_id.ensemble.values.equals(original.ensemble.values)

        evidence = store.fetch_evidence(original_id)
        assert evidence is not None
        assert "original_capture" in (evidence.reason or "")

    def test_the_record_listing_still_shows_the_superseded_row(
        self, db_connection: sa.Connection
    ) -> None:
        """``fetch_forecast_summaries`` is DELIBERATELY unfiltered: it is the
        record, it carries each row's status, and it is how a superseded
        forecast's id is discovered at all."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original_id = store.store_forecast(
            _make_forecast(sid, mid, aid, rng=random.Random(121))
        )
        replacement = _make_forecast(sid, mid, aid, rng=random.Random(122))
        store.store_forecast(replacement)

        rows, total = store.fetch_forecast_summaries(sid, _ISSUED_A, _ISSUED_B)
        by_id = {row.id: row for row in rows}

        assert total == 2
        assert by_id[original_id].status is ForecastStatus.SUPERSEDED
        assert by_id[replacement.id].status is ForecastStatus.RAW


class TestPostgresParity:
    """The facts `tests/unit/fakes/test_fake_forecast_store_supersession.py`
    asserts about `FakeForecastStore`, asserted here against Postgres.

    ⚠️ A fake that drifts from these makes every flow test built on it
    meaningless — the failure class Plan 327's original fake already hit.
    """

    def test_a_superseded_row_does_not_occupy_the_natural_key(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        superseded = _make_forecast(sid, mid, aid, rng=random.Random(141))
        superseded_id = store.store_forecast(superseded)
        _mark_superseded_directly(db_connection, superseded_id)

        # The SAME numbers arriving again: nothing CURRENT holds the key, so
        # this is an INSERT, not a resume.
        arriving = replace(superseded, id=ForecastId(uuid4()))
        returned = store.store_forecast(arriving)

        assert returned == arriving.id
        assert returned != superseded_id
        assert _status_of(db_connection, superseded_id) == "superseded"

    def test_a_row_1_rerun_reusing_the_original_id_is_rejected_and_rolls_back(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ The primary key refuses it, and the supersession mark goes back
        with it — the original stays CURRENT with its evidence."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(142)),
            evidence=incomplete_evidence("original_capture"),
        )
        original_id = store.store_forecast(original)
        evidence_before = store.fetch_evidence(original_id)

        colliding = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(143)), id=original_id
        )
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            store.store_forecast(colliding)

        assert _status_of(db_connection, original_id) == "raw"
        survivor = store.fetch_forecast(original_id)
        assert survivor is not None
        assert survivor.ensemble.values.equals(original.ensemble.values)
        assert store.fetch_evidence(original_id) == evidence_before
        # The SAME four facts the fake is held to: status, version, still
        # current, and a later re-run resumes rather than colliding.
        assert survivor.status is ForecastStatus.RAW
        assert survivor.version == original.version
        latest = store.fetch_latest_forecast(sid, mid, "discharge")
        assert latest is not None
        assert latest.id == original_id
        assert store.store_forecast(replace(original, id=ForecastId(uuid4()))) == (
            original_id
        )

    def test_resubmitting_the_very_same_forecast_still_resumes(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        original = _make_forecast(sid, mid, aid, rng=random.Random(144))
        original_id = store.store_forecast(original)

        assert store.store_forecast(original) == original_id
        assert _status_of(db_connection, original_id) == "raw"
