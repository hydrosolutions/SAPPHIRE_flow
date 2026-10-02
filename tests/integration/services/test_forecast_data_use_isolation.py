from __future__ import annotations

import random
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from polars.testing import assert_frame_equal
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db.metadata import (
    measurement_feed_evidence,
    observations,
    provisional_discharges,
    rating_reference_proofs,
)
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.services.observation_alert_checker import check_observation_alerts
from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.types.alert import Alert
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    ObservationSource,
    QcStatus,
    ThresholdSource,
)
from sapphire_flow.types.ids import (
    AlertId,
    MeasurementFeedEvidenceId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.rating_reference import (
    RatingReferenceProof,
    canonical_content,
    content_digest,
    curve_snapshot,
    measurement_snapshot,
)
from sapphire_flow.types.station import StationGroup
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.integration.db.test_role_bootstrap import (
    role_harness as role_harness,
)
from tests.integration.store.test_provisional_discharge_store import (
    permit_fixture,
    seed_reference,
)
from tests.unit.services.test_provisional_discharge import NOW, inputs

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import polars as pl

    from sapphire_flow.store.alert_store import PgAlertStore
    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve
    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )

_START = NOW - timedelta(hours=4)
_STEP = timedelta(hours=1)


@dataclass(frozen=True, kw_only=True, slots=True)
class Stores:
    obs: PgObservationStore
    station: PgStationStore
    basin: PgBasinStore
    alert: PgAlertStore


def factory(conn: sa.Connection) -> Stores:
    stores = make_pg_stores(conn)
    return Stores(
        obs=cast("PgObservationStore", stores["obs_store"]),
        station=cast("PgStationStore", stores["station_store"]),
        basin=cast("PgBasinStore", stores["basin_store"]),
        alert=cast("PgAlertStore", stores["alert_store"]),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class Case:
    station_id: StationId
    curve: RatingCurve
    proof: RatingReferenceProof
    ordinary: tuple[Observation, Observation]


@pytest.fixture
def connection(
    role_harness: _RoleBootstrapHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[sa.Connection]:
    assert role_harness.run_bootstrap("ordinary-api", "ordinary-worker").returncode == 0
    monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path))
    with role_harness.owner_engine.connect() as conn, conn.begin():
        yield conn
        conn.rollback()


def use_role(
    conn: sa.Connection, role: Literal["sapphire_api", "sapphire_worker"]
) -> None:
    conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
    assert conn.scalar(sa.text("SELECT current_user")) == role


def seed_case(conn: sa.Connection, key: int = 10000) -> Case:
    _, template, _, proof = inputs()
    sid = StationId(UUID(int=key))
    station = make_station_config(
        station_id=sid, code=f"ordinary-{key}", rng=random.Random(key)
    )
    stores = factory(conn)
    stores.station.store_station(station)
    curve = replace(
        template,
        id=RatingCurveId(UUID(int=key + 1)),
        station_id=sid,
        valid_from=NOW - timedelta(days=365),
        valid_to=NOW - timedelta(hours=2, minutes=30),
        points=[
            {"water_level": 1.0, "discharge": 4.0},
            {"water_level": 2.0, "discharge": 10000.0},
        ],
    )
    PgRatingCurveStore(conn).store_rating_curve(curve)
    proof = replace(
        proof,
        id=RatingReferenceProofId(UUID(int=key + 2)),
        tenant_id=station.tenant_id,
        station_id=sid,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
    )
    seed_reference(conn, rating_reference_proofs, proof)
    rated = replace(
        make_observation(
            station_id=sid,
            timestamp=NOW - timedelta(hours=3),
            value=4.0,
            rng=random.Random(key + 3),
        ),
        source=ObservationSource.RATING_CURVE_DERIVED,
        rating_curve_id=curve.id,
        rating_curve_correction_version="fixture-v1",
    )
    manual = replace(
        make_observation(
            station_id=sid,
            timestamp=NOW - timedelta(hours=2),
            value=2.0,
            rng=random.Random(key + 4),
        ),
        source=ObservationSource.MANUAL_IMPORT,
    )
    stores.obs.store_observations([rated, manual])
    return Case(station_id=sid, curve=curve, proof=proof, ordinary=(rated, manual))


def seed_provisional(conn: sa.Connection, case: Case) -> None:
    # Owner-only synthetic permission remains inside the caller's rollback.
    template, _, feed, _ = inputs()
    store = PgProvisionalDischargeStore(conn)
    expected: list[dict[str, object]] = []
    for offset, timestamp in enumerate(
        (case.ordinary[1].timestamp, NOW - timedelta(minutes=30))
    ):
        obs = replace(
            template,
            id=ObservationId(UUID(int=case.station_id.int + 10 + offset)),
            station_id=case.station_id,
            timestamp=timestamp,
            value=2.0,
        )
        PgObservationStore(conn).store_observations([obs])
        evidence = replace(
            feed,
            id=MeasurementFeedEvidenceId(UUID(int=case.station_id.int + 20 + offset)),
            tenant_id=case.proof.tenant_id,
            station_id=case.station_id,
            observation_id=obs.id,
            measurement=measurement_snapshot(obs),
        )
        seed_reference(conn, measurement_feed_evidence, evidence)
        result = convert_provisional_discharge(
            observation=obs,
            curves=[case.curve],
            feed_evidence=evidence,
            reference_proof=case.proof,
            at=NOW,
        )
        feed_content = canonical_content(asdict(evidence))
        proof_content = canonical_content(asdict(case.proof))
        expected.append(
            {
                "timestamp": timestamp,
                "value": 2.0,
                "discharge": 10000.0,
                "observation_id": obs.id,
                "station_id": case.station_id,
                "tenant_id": case.proof.tenant_id,
                "rating_curve_id": case.curve.id,
                "feed_evidence_id": evidence.id,
                "reference_proof_id": case.proof.id,
                "feed_content": feed_content,
                "feed_fingerprint": content_digest(feed_content),
                "proof_content": proof_content,
                "proof_fingerprint": content_digest(proof_content),
                "content": result.content,
                "fingerprint": result.fingerprint,
            }
        )
        store.store_provisional_discharge(result, captured_at=NOW)
        assert result.discharge == 10000.0
        assert _START <= timestamp < NOW
    rows = (
        conn.execute(
            sa.select(
                observations.c.timestamp,
                observations.c.value,
                provisional_discharges.c.discharge,
                provisional_discharges.c.observation_id,
                provisional_discharges.c.station_id,
                provisional_discharges.c.tenant_id,
                provisional_discharges.c.rating_curve_id,
                provisional_discharges.c.feed_evidence_id,
                provisional_discharges.c.reference_proof_id,
                provisional_discharges.c.content,
                provisional_discharges.c.fingerprint,
                measurement_feed_evidence.c.content.label("feed_content"),
                measurement_feed_evidence.c.fingerprint.label("feed_fingerprint"),
                rating_reference_proofs.c.content.label("proof_content"),
                rating_reference_proofs.c.fingerprint.label("proof_fingerprint"),
            )
            .select_from(
                provisional_discharges.join(
                    observations,
                    provisional_discharges.c.observation_id == observations.c.id,
                )
                .join(
                    measurement_feed_evidence,
                    provisional_discharges.c.feed_evidence_id
                    == measurement_feed_evidence.c.id,
                )
                .join(
                    rating_reference_proofs,
                    provisional_discharges.c.reference_proof_id
                    == rating_reference_proofs.c.id,
                )
            )
            .where(provisional_discharges.c.station_id == case.station_id)
            .order_by(observations.c.timestamp)
        )
        .mappings()
        .all()
    )
    assert [dict(row) for row in rows] == expected
    assert all(_START <= row["timestamp"] < NOW for row in rows)


def grant_fixture(conn: sa.Connection, case: Case) -> None:
    permit_fixture(conn, case.proof.tenant_id)


def ordinary_rows(stores: Stores, case: Case) -> list[Observation]:
    return stores.obs.fetch_observations(
        case.station_id, "discharge", _START, NOW, qc_status=QcStatus.QC_PASSED
    )


class TestOrdinaryFactory:
    @pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
    def test_mixed_storage_preserves_positive_history_and_denies_protected_reads(
        self,
        connection: sa.Connection,
        role: Literal["sapphire_api", "sapphire_worker"],
    ) -> None:
        case = seed_case(connection)
        grant_fixture(connection, case)
        seed_provisional(connection, case)
        use_role(connection, role)
        stores = factory(connection)
        expected = list(case.ordinary)
        assert ordinary_rows(stores, case) == expected
        batch = stores.obs.fetch_observations_batch(
            [case.station_id], "discharge", _START, NOW, qc_status=QcStatus.QC_PASSED
        )
        assert {
            sid: sorted(rows, key=lambda obs: (obs.timestamp, str(obs.id)))
            for sid, rows in batch.items()
        } == {
            case.station_id: sorted(
                expected, key=lambda obs: (obs.timestamp, str(obs.id))
            )
        }
        assert (
            stores.obs.fetch_latest_timestamp(case.station_id, "discharge")
            == case.ordinary[-1].timestamp
        )
        for table in (
            "provisional_discharges",
            "measurement_feed_evidence",
            "rating_reference_proofs",
        ):
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                connection.begin_nested(),
            ):
                connection.execute(sa.text(f"SELECT * FROM {table}"))


def training(
    stores: Stores, cases: tuple[Case, ...], scope: Literal["station", "group"]
) -> pl.DataFrame:
    # Existing SAP3 fakes supply requirements only; no model/FI train or predict runs.
    requirements = replace(
        FakeStationForecastModel.data_requirements,
        past_dynamic_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
    )
    if scope == "station":
        model = FakeStationForecastModel()
        model.data_requirements = requirements
        result = assemble_station_training_data(
            station_id=cases[0].station_id,
            model=model,
            period_start=_START,
            period_end=NOW,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource([]),
            obs_store=stores.obs,
            basin_store=stores.basin,
            station_store=stores.station,
        )
    else:
        group_model = FakeGroupForecastModel()
        group_model.data_requirements = requirements
        group = StationGroup(
            id=StationGroupId(UUID(int=90000)),
            name="ordinary controls",
            station_ids=frozenset(c.station_id for c in cases),
            created_at=NOW,
        )
        result = assemble_group_training_data(
            group=group,
            model=group_model,
            period_start=_START,
            period_end=NOW,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource([]),
            obs_store=stores.obs,
            basin_store=stores.basin,
            station_store=stores.station,
        )
        assert result is not None
        assert set(result.station_ids) == {c.station_id for c in cases}
    assert result is not None
    assert result.time_step == _STEP
    sort = ["timestamp"] if scope == "station" else ["station_id", "timestamp"]
    return result.past_targets.sort(sort)


class TestOrdinaryTraining:
    @pytest.mark.parametrize("scope", ["station", "group"])
    def test_provisional_values_do_not_change_targets_but_ordinary_values_do(
        self, connection: sa.Connection, scope: Literal["station", "group"]
    ) -> None:
        cases = (seed_case(connection), seed_case(connection, 20000))
        use_role(connection, "sapphire_worker")
        before = training(factory(connection), cases, scope)
        expected_values = [4.0, 2.0] if scope == "station" else [4.0, 2.0, 4.0, 2.0]
        assert before["discharge"].to_list() == expected_values
        assert before["timestamp"].to_list() == [
            o.timestamp
            for c in (cases[:1] if scope == "station" else cases)
            for o in c.ordinary
        ]
        connection.execute(sa.text("RESET ROLE"))
        grant_fixture(connection, cases[0])
        for case in cases:
            seed_provisional(connection, case)
        use_role(connection, "sapphire_worker")
        stores = factory(connection)
        assert_frame_equal(training(stores, cases, scope), before)
        changed = replace(cases[0].ordinary[1], value=6.0)
        stores.obs.store_observations([changed])
        after = training(stores, cases, scope)
        expected_values[1] = 6.0
        assert after["discharge"].to_list() == expected_values
        assert after["timestamp"].to_list() == before["timestamp"].to_list()


def threshold(stores: Stores, case: Case) -> None:
    stores.station.store_thresholds(
        [
            StationThreshold(
                station_id=case.station_id,
                danger_level="yellow",
                parameter="discharge",
                value=100.0,
                source=ThresholdSource.AUTHORITY,
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )


def check_alerts(stores: Stores, case: Case) -> None:
    check_observation_alerts(
        {(case.station_id, "discharge")}, stores.obs, stores.station, stores.alert, NOW
    )


class TestOrdinaryObservationAlerts:
    def test_only_ordinary_controls_raise_and_resolve_alert(
        self, connection: sa.Connection
    ) -> None:
        case = seed_case(connection)
        threshold(factory(connection), case)
        grant_fixture(connection, case)
        seed_provisional(connection, case)
        use_role(connection, "sapphire_worker")
        stores = factory(connection)
        assert ordinary_rows(stores, case) == list(case.ordinary)
        check_alerts(stores, case)
        assert (
            stores.alert.fetch_active_alerts(case.station_id, AlertSource.OBSERVATION)
            == []
        )
        high = replace(
            case.ordinary[1],
            id=ObservationId(UUID(int=30001)),
            timestamp=NOW - timedelta(hours=1),
            value=150.0,
        )
        stores.obs.store_observations([high])
        check_alerts(stores, case)
        active = stores.alert.fetch_active_alerts(
            case.station_id, AlertSource.OBSERVATION
        )
        assert len(active) == 1
        assert active[0].trigger_value == 150.0
        assert active[0].status is AlertStatus.RAISED
        low = replace(
            high,
            id=ObservationId(UUID(int=30002)),
            timestamp=NOW - timedelta(minutes=45),
            value=3.0,
        )
        stores.obs.store_observations([low])
        check_alerts(stores, case)
        resolved = stores.alert.fetch_alert(active[0].id)
        assert resolved is not None and resolved.status is AlertStatus.RESOLVED
        assert (
            stores.alert.fetch_active_alerts(case.station_id, AlertSource.OBSERVATION)
            == []
        )

    def test_provisional_only_cannot_raise_or_resolve_existing_alert(
        self, connection: sa.Connection
    ) -> None:
        case = seed_case(connection)
        threshold(factory(connection), case)
        grant_fixture(connection, case)
        seed_provisional(connection, case)
        # Owner fixture scenario retains protected measurements but no ordinary Q.
        connection.execute(
            sa.text(
                "DELETE FROM observations WHERE station_id=:sid "
                "AND parameter='discharge'"
            ),
            {"sid": case.station_id},
        )
        use_role(connection, "sapphire_worker")
        stores = factory(connection)
        assert ordinary_rows(stores, case) == []
        check_alerts(stores, case)
        assert (
            stores.alert.fetch_active_alerts(case.station_id, AlertSource.OBSERVATION)
            == []
        )
        alert = Alert(
            id=AlertId(UUID(int=30003)),
            station_id=case.station_id,
            source=AlertSource.OBSERVATION,
            alert_level="yellow",
            status=AlertStatus.RAISED,
            trigger_probability=None,
            trigger_value=150.0,
            triggered_at=NOW - timedelta(hours=5),
            acknowledged_at=None,
            acknowledged_by=None,
            resolved_at=None,
            first_detected_at=NOW - timedelta(hours=5),
            notified_at=None,
            created_at=NOW - timedelta(hours=5),
        )
        stores.alert.upsert_alert(alert)
        check_alerts(stores, case)
        assert stores.alert.fetch_alert(alert.id) == alert
