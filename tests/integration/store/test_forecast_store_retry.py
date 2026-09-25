"""Plan 327 T2 — the store boundary: resume an identical re-run, refuse the rest.

The decision table (§ D1) is evaluated IN ORDER, first match wins. Row 4 resumes
(returns the stored identity, writes nothing, raises nothing); ⛔ ROW 3 refuses
with a `ForecastRetryConflictError`. Rows 1 and 2 SUPERSEDE AND REPLACE since
Plan 328 — their behaviour is pinned in `test_forecast_supersession.py`.

⛔ Evidence state is NOT a classifier
— every operational forecast carries an incomplete-evidence marker, so a rule
keyed on it would refuse every real resume.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from sapphire_flow.db.metadata import forecast_values
from sapphire_flow.db.metadata import forecasts as forecasts_table
from sapphire_flow.exceptions import ForecastRetryConflictError, SapphireError
from sapphire_flow.services.forecast_evidence import capture_combined_evidence
from sapphire_flow.services.forecast_retry import ForecastRetryRow
from sapphire_flow.store.forecast_store import PgForecastStore, _build_value_rows
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.domain import ForecastQcRuleSet, QcFlag
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.forecast import OperationalForecast  # noqa: TC001
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    ForecastEvidence,
    incomplete_evidence,
)
from sapphire_flow.types.ids import POOLED_MODEL_ID, ForecastId
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_rating_curve,
    _seed_station,
    savepoint_factory,
)


def _count_rows(conn: sa.Connection) -> int:
    return conn.execute(
        sa.select(sa.func.count()).select_from(forecasts_table)
    ).scalar_one()


def _store(conn: sa.Connection) -> PgForecastStore:
    return PgForecastStore(conn, transaction_factory=savepoint_factory(conn))


class TestRow4Identical:
    def test_an_identical_rerun_returns_the_stored_id_and_writes_nothing(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        first = _make_forecast(sid, mid, aid, rng=random.Random(11))
        first_id = store.store_forecast(first)
        before = _count_rows(db_connection)

        # Same computation, new row id — exactly what a re-run produces.
        rerun = replace(first, id=ForecastId(uuid4()))
        returned = store.store_forecast(rerun)

        assert returned == first_id
        assert returned != rerun.id
        assert _count_rows(db_connection) == before
        stored = store.fetch_forecast(first_id)
        assert stored is not None
        assert stored.ensemble.values.equals(first.ensemble.values)

    def test_an_ordinary_incomplete_evidence_marker_still_resumes(
        self, db_connection: sa.Connection
    ) -> None:
        """🔴 EVERY real forecast carries one — a test built only on synthetic
        COMPLETE evidence would pass while every real resume refused."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        first = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(12)),
            evidence=incomplete_evidence("runtime_image_bytes_unpinned"),
        )
        first_id = store.store_forecast(first)

        rerun = replace(
            first,
            id=ForecastId(uuid4()),
            evidence=incomplete_evidence("runtime_image_bytes_unpinned"),
        )

        assert store.store_forecast(rerun) == first_id

    def test_a_resume_leaves_the_existing_evidence_in_place(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ It may not rewrite it; migration 0057 rejects UPDATE/DELETE."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        first = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(13)),
            evidence=incomplete_evidence("first_capture"),
        )
        first_id = store.store_forecast(first)
        before = store.fetch_evidence(first_id)
        assert before is not None

        store.store_forecast(
            replace(
                first,
                id=ForecastId(uuid4()),
                evidence=incomplete_evidence("second_capture"),
            )
        )

        after = store.fetch_evidence(first_id)
        assert after is not None
        assert after.reason == before.reason
        assert "second_capture" not in (after.reason or "")


class TestRefusals:
    """⛔ ROW 3 ONLY. Plan 328 turned rows 1 and 2 into supersessions —
    `test_forecast_supersession.py` pins those; a refusal test for them here
    would assert behaviour the store no longer has."""

    def test_row_3_qc_verdict_differs_is_refused_permanently(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ Row 3 is the one Plan 328 does NOT take."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        first = _make_forecast(sid, mid, aid, rng=random.Random(24))
        store.store_forecast(first)

        with pytest.raises(ForecastRetryConflictError) as excinfo:
            store.store_forecast(
                replace(
                    first,
                    id=ForecastId(uuid4()),
                    qc_status=QcStatus.QC_SUSPECT,
                    qc_flags=(
                        QcFlag(
                            rule_id="range_check",
                            rule_version="2.0",
                            status=QcStatus.QC_SUSPECT,
                            detail="out of range",
                        ),
                    ),
                )
            )

        assert excinfo.value.row is ForecastRetryRow.QC_VERDICT_DIFFERS
        assert "row 3" in str(excinfo.value)

    def test_an_unrelated_storage_failure_still_propagates_raw(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ Plan 038 D5 stands: only a SEMANTIC retry conflict becomes a
        domain error. Wrapping everything is the regression this guards."""
        station_a = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, station_a, mid)
        store = _store(db_connection)

        # A composite-FK violation: a curve belonging to another station.
        from sapphire_flow.store.station_store import PgStationStore
        from tests.conftest import make_station_config

        station_b = make_station_config(code="RETRY-B", rng=random.Random(91))
        PgStationStore(db_connection).store_station(station_b)
        curve_b = _seed_rating_curve(db_connection, station_b.id)

        fc = _make_forecast(
            station_a, mid, aid, rating_curve_id=curve_b, rng=random.Random(25)
        )
        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            store.store_forecast(fc)

        assert not isinstance(excinfo.value, SapphireError)


class TestCombinedContributorEvidence:
    """🔴 A combination's evidence carries each contributor's ``forecast_id``
    AND its ``evidence_sha256``; the store checks BOTH against PERSISTED
    evidence. Resuming without rebinding writes an IMMUTABLE bad record."""

    def _combined(
        self, contributors: tuple[OperationalForecast, ...]
    ) -> OperationalForecast:
        template = contributors[0]
        return replace(
            template,
            id=ForecastId(uuid4()),
            model_id=POOLED_MODEL_ID,
            model_artifact_id=None,
            combination_strategy="pooled",
            source_model_ids=[template.model_id],
            evidence=capture_combined_evidence(
                model_id=POOLED_MODEL_ID,
                strategy="pooled",
                contributors=contributors,
                weights=None,
                qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
                qc_overrides=[],
                baselines=[],
                water_level_datum_masl=None,
            ),
        )

    def _complete_evidence(self, payload: bytes) -> ForecastEvidence:
        return ForecastEvidence(
            status=EvidenceStatus.COMPLETE,
            manifest_json="{}",
            snapshot=payload,
            snapshot_sha256=hashlib.sha256(payload).hexdigest(),
            artifact=None,
            artifact_sha256=None,
        )

    def test_an_unrebound_contributor_records_the_retry_induced_gap(
        self, db_connection: sa.Connection
    ) -> None:
        """The hazard itself, demonstrated: a combination built from the
        RECOMPUTED (never-persisted) contributor identity."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        # `_pooled` is seeded by the migrations; no need to insert it.
        store = _store(db_connection)

        contributor = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(31)),
            evidence=self._complete_evidence(b'{"contributors": []}'),
        )
        store.store_forecast(contributor)

        # The resumed cycle recomputed the contributor under a NEW id.
        unrebound = replace(contributor, id=ForecastId(uuid4()))
        combined_id = store.store_forecast(self._combined((unrebound,)))

        persisted = store.fetch_evidence(combined_id)
        assert persisted is not None
        assert "contributor_evidence_not_persisted" in (persisted.reason or "")

    def test_a_rebound_contributor_records_no_gap(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        # `_pooled` is seeded by the migrations; no need to insert it.
        store = _store(db_connection)

        contributor = replace(
            _make_forecast(sid, mid, aid, rng=random.Random(32)),
            evidence=self._complete_evidence(b'{"contributors": []}'),
        )
        contributor_id = store.store_forecast(contributor)

        # What the resume path does: resolve the id AND the PERSISTED hash.
        stored = store.fetch_forecast(contributor_id)
        assert stored is not None
        assert stored.evidence is None, (
            "fetching a forecast must NOT hydrate its evidence — the resume "
            "path has to fetch it deliberately"
        )
        persisted_evidence = store.fetch_evidence(contributor_id)
        assert persisted_evidence is not None
        rebound = replace(
            contributor,
            id=contributor_id,
            evidence=ForecastEvidence(
                status=persisted_evidence.status,
                manifest_json=persisted_evidence.manifest_json,
                snapshot=persisted_evidence.snapshot,
                snapshot_sha256=persisted_evidence.snapshot_sha256,
                artifact=persisted_evidence.artifact,
                artifact_sha256=persisted_evidence.artifact_sha256,
                reason=persisted_evidence.reason,
            ),
        )

        combined_id = store.store_forecast(self._combined((rebound,)))

        persisted = store.fetch_evidence(combined_id)
        assert persisted is not None
        reason = persisted.reason or ""
        assert "contributor_evidence_not_persisted" not in reason
        assert "contributor_evidence_mismatch" not in reason

    def test_honest_historical_absence_is_still_reported(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ A contributor predating evidence capture has NO evidence row, so
        the reason is correct and must not be suppressed. Forbidding it
        unconditionally would make a branch this plan supports unsatisfiable."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        # `_pooled` is seeded by the migrations; no need to insert it.
        store = _store(db_connection)

        pre_capture = _make_forecast(
            sid, mid, aid, issued_at=_ISSUED_A, rng=random.Random(33)
        )
        # A pre-0057 row: header AND ITS VALUES, no evidence row. Written
        # directly, because the store cannot produce one any more. 🔴 The
        # values are not decoration — without them a retry of this row is
        # UNCLASSIFIABLE (`_fetch_forecast` returns None), so a header-only
        # fixture would silently not be the historical case it claims to be.
        db_connection.execute(
            sa.insert(forecasts_table).values(
                id=pre_capture.id,
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                issued_at=pre_capture.issued_at,
                time_step_seconds=int(pre_capture.ensemble.time_step.total_seconds()),
                nwp_cycle_reference_time=pre_capture.nwp_cycle_reference_time,
                nwp_cycle_source=pre_capture.nwp_cycle_source.value,
                representation=pre_capture.representation.value,
                status=pre_capture.status.value,
                version=1,
                parameter=pre_capture.ensemble.parameter,
                units=pre_capture.ensemble.units,
                created_at=pre_capture.created_at,
                updated_at=pre_capture.updated_at,
                qc_status=pre_capture.qc_status.value,
                qc_flags=[],
            )
        )
        db_connection.execute(
            sa.insert(forecast_values), _build_value_rows(pre_capture)
        )

        marker = store.fetch_evidence(pre_capture.id)
        assert marker is not None
        assert marker.reason == "pre_capture_forecast"

        # It is a real, classifiable row: an identical retry of it RESUMES
        # (row 4) and still writes no evidence record for it.
        assert (
            store.store_forecast(replace(pre_capture, id=ForecastId(uuid4())))
            == pre_capture.id
        )
        assert store.fetch_evidence(pre_capture.id).reason == "pre_capture_forecast"  # type: ignore[union-attr]

        rebound = replace(
            pre_capture,
            evidence=ForecastEvidence(
                status=marker.status,
                manifest_json=marker.manifest_json,
                snapshot=None,
                snapshot_sha256=None,
                artifact=None,
                artifact_sha256=None,
                reason=marker.reason,
            ),
        )
        combined_id = store.store_forecast(self._combined((rebound,)))

        persisted = store.fetch_evidence(combined_id)
        assert persisted is not None
        assert "contributor_evidence_not_persisted" in (persisted.reason or "")


class TestHindcastsAreUntouched:
    def test_the_same_hindcast_key_still_replaces_rather_than_refusing(
        self, db_connection: sa.Connection
    ) -> None:
        """Hindcasts use separate tables and a SIX-column key including
        ``hindcast_run_id`` AND ``forcing_type``, with approved atomic full
        replacement (Plan 040). Generalising the forecast retry policy across
        both stores is how this change would break that."""
        from tests.integration.store.test_hindcast_store import (
            _make_hindcast,
            _utc,
        )
        from tests.integration.store.test_hindcast_store import (
            _seed_artifact as _seed_hc_artifact,
        )
        from tests.integration.store.test_hindcast_store import (
            _seed_model as _seed_hc_model,
        )
        from tests.integration.store.test_hindcast_store import (
            _seed_station as _seed_hc_station,
        )
        from tests.integration.store.test_hindcast_store import (
            savepoint_factory as hc_savepoint_factory,
        )

        sid = _seed_hc_station(db_connection)
        mid = _seed_hc_model(db_connection)
        aid = _seed_hc_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=hc_savepoint_factory(db_connection)
        )
        run_id = uuid4()
        step = _utc(2026, 5, 1)

        first_id = store.store_hindcast(
            _make_hindcast(
                sid, mid, aid, hindcast_step=step, hindcast_run_id=run_id, n_steps=3
            )
        )
        # A DIFFERENT payload under the same six-column key: replace, not refuse.
        second_id = store.store_hindcast(
            _make_hindcast(
                sid, mid, aid, hindcast_step=step, hindcast_run_id=run_id, n_steps=4
            )
        )

        assert second_id == first_id


class TestUnrelatedKeysStillInsert:
    def test_a_different_parameter_under_the_same_cycle_is_not_a_retry(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = _store(db_connection)

        discharge = _make_forecast(
            sid, mid, aid, parameter="discharge", rng=random.Random(41)
        )
        water_level = _make_forecast(
            sid, mid, aid, parameter="water_level", rng=random.Random(42)
        )
        store.store_forecast(discharge)
        store.store_forecast(water_level)

        assert len(store.fetch_forecasts_for_cycle(_ISSUED_A, station_id=sid)) == 2

    def test_a_different_artifact_id_alone_does_not_bypass_the_key(
        self, db_connection: sa.Connection
    ) -> None:
        """`model_artifact_id` is NOT part of the natural key, so a re-run
        carrying a new one replaces rather than coexisting: exactly ONE
        current row survives under the key (Plan 328, decision-table row 2)."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        other_aid = _seed_artifact(db_connection, sid, _seed_model(db_connection, "v2"))
        store = _store(db_connection)

        fc = _make_forecast(sid, mid, aid, rng=random.Random(43))
        first_id = store.store_forecast(fc)
        replacement = replace(fc, id=ForecastId(uuid4()), model_artifact_id=other_aid)
        store.store_forecast(replacement)

        current = store.fetch_forecasts_for_cycle(_ISSUED_A, station_id=sid)
        assert [f.id for f in current] == [replacement.id]
        assert first_id not in {f.id for f in current}
