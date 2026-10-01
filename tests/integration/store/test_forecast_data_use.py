from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.forecast import OperationalForecast

from dataclasses import replace
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import forecasts
from sapphire_flow.exceptions import ForecastRetryConflictError
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.types.enums import ForecastDataUse, ForecastStatus, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import ForecastId
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _ISSUED_B,
    _make_forecast,
    _seed_artifact,
    _seed_model,
    savepoint_factory,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    convert,
    permit_fixture,
    seed,
)


@pytest.fixture
def structural_connection(db_connection: sa.Connection) -> Iterator[sa.Connection]:
    # Disposable synthetic database only; rollback restores precisely this guard.
    with db_connection.begin_nested() as transaction:
        db_connection.execute(
            sa.text("DROP TRIGGER trg_forecast_test_write_refused ON public.forecasts")
        )
        try:
            yield db_connection
        finally:
            transaction.rollback()


def _stores(conn: sa.Connection) -> tuple[PgForecastStore, PgForecastStore]:
    return (
        PgForecastStore(conn, transaction_factory=savepoint_factory(conn)),
        PgForecastStore(
            conn,
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            transaction_factory=savepoint_factory(conn),
        ),
    )


def _pair(conn: sa.Connection) -> tuple[OperationalForecast, OperationalForecast]:
    args = seed(conn)
    provisional = convert(*args)
    if not conn.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM provisional_discharge_permissions "
            "WHERE tenant_id=:tenant)"
        ),
        {"tenant": provisional.tenant_id},
    ):
        permit_fixture(conn, provisional.tenant_id)
    PgProvisionalDischargeStore(conn).store_provisional_discharge(
        provisional, captured_at=NOW
    )
    sid = provisional.station_id
    mid = _seed_model(conn, "model_" + uuid4().hex)
    aid = _seed_artifact(conn, sid, mid)
    ordinary = _make_forecast(sid, mid, aid)
    test = replace(
        ordinary,
        id=ForecastId(uuid4()),
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        input_lineage=ForecastInputLineage(
            provisional_discharge_fingerprints=(provisional.fingerprint,),
            transformation_versions=("daily-mean-v1",),
        ),
    )
    return ordinary, test


class TestDeployedForecastGuard:
    def test_copy_also_hits_deployed_refusal(
        self, db_connection: sa.Connection
    ) -> None:
        import psycopg

        _, test = _pair(db_connection)
        driver = db_connection.connection.driver_connection
        with (
            pytest.raises(
                psycopg.errors.RaiseException, match="test forecast writes are disabled"
            ),
            db_connection.begin_nested(),
            driver.cursor() as cursor,
            cursor.copy(
                "COPY forecasts (id, station_id, model_id, issued_at, representation, "
                "parameter, units, data_use, input_lineage) FROM STDIN"
            ) as copy,
        ):
            copy.write_row(
                (
                    test.id,
                    test.station_id,
                    test.model_id,
                    test.issued_at,
                    "members",
                    "discharge",
                    "m3/s",
                    "expired_rating_test",
                    test.input_lineage.content,
                )
            )

    def test_insert_select_also_hits_deployed_refusal(
        self, db_connection: sa.Connection
    ) -> None:
        _, test = _pair(db_connection)
        with (
            pytest.raises(sa.exc.DBAPIError, match="test forecast writes are disabled"),
            db_connection.begin_nested(),
        ):
            # INSERT SELECT exercises server-side bulk insertion without
            # changing any trigger or granting a capability to application roles.
            db_connection.execute(
                sa.text(
                    "INSERT INTO forecasts (id, station_id, model_id, issued_at, "
                    "representation, parameter, units, data_use, input_lineage) "
                    "SELECT :id, :station, :model, :issued, 'members', 'discharge', "
                    "'m3/s', 'expired_rating_test', :lineage"
                ),
                {
                    "id": test.id,
                    "station": test.station_id,
                    "model": test.model_id,
                    "issued": test.issued_at,
                    "lineage": test.input_lineage.content,
                },
            )

    def test_typed_and_direct_sql_test_inserts_refused(
        self, db_connection: sa.Connection
    ) -> None:
        ordinary, test = _pair(db_connection)
        standard_store, test_store = _stores(db_connection)
        assert standard_store.store_forecast(ordinary) == ordinary.id
        with pytest.raises(
            sa.exc.DBAPIError, match="test forecast writes are disabled"
        ):
            test_store.store_forecast(test)
        with (
            pytest.raises(sa.exc.DBAPIError, match="test forecast writes are disabled"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(forecasts).values(
                    id=test.id,
                    station_id=test.station_id,
                    model_id=test.model_id,
                    issued_at=test.issued_at,
                    representation="members",
                    parameter="discharge",
                    units="m3/s",
                    data_use="expired_rating_test",
                    input_lineage=test.input_lineage.content,
                )
            )

    def test_standard_cannot_be_promoted_by_update(
        self, db_connection: sa.Connection
    ) -> None:
        ordinary, _ = _pair(db_connection)
        store = _stores(db_connection)[0]
        store.store_forecast(ordinary)
        with (
            pytest.raises(sa.exc.DBAPIError, match="immutable"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == ordinary.id)
                .values(data_use="expired_rating_test")
            )


class TestStructuralForecastIsolation:
    @pytest.mark.parametrize("different", [False, True])
    @pytest.mark.parametrize("test_first", [False, True])
    def test_cross_class_natural_keys_never_alias_or_supersede(
        self, structural_connection: sa.Connection, different: bool, test_first: bool
    ) -> None:
        conn = structural_connection
        ordinary, test = _pair(conn)
        if different:
            test = replace(
                test,
                ensemble=replace(
                    test.ensemble,
                    values=test.ensemble.values.with_columns(
                        (pl.col("value") + 10).alias("value")
                    ),
                ),
            )
        standard_store, test_store = _stores(conn)
        ordered = [(standard_store, ordinary), (test_store, test)]
        for store, forecast in reversed(ordered) if test_first else ordered:
            assert store.store_forecast(forecast) == forecast.id
        assert standard_store.fetch_forecast(ordinary.id).status is ForecastStatus.RAW
        assert test_store.fetch_forecast(test.id).status is ForecastStatus.RAW
        assert standard_store.fetch_forecast(test.id) is None
        assert test_store.fetch_forecast(ordinary.id) is None
        assert standard_store.fetch_evidence(test.id) is None
        assert test_store.fetch_evidence(ordinary.id) is None

    def test_wrong_purpose_rejected_before_write(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, test = _pair(structural_connection)
        standard_store, test_store = _stores(structural_connection)
        for store, forecast in ((standard_store, test), (test_store, ordinary)):
            with pytest.raises(ValueError, match="data use"):
                store.store_forecast(forecast)

    def test_equal_values_changed_lineage_supersedes_and_qc_joint_change_refuses(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, first = _pair(structural_connection)
        standard_store, store = _stores(structural_connection)
        standard_store.store_forecast(ordinary)
        store.store_forecast(first)
        evidence = store.fetch_evidence(first.id)
        changed = replace(
            first,
            id=ForecastId(uuid4()),
            input_lineage=replace(
                first.input_lineage, transformation_versions=("daily-mean-v2",)
            ),
        )
        with pytest.raises(ForecastRetryConflictError, match="row 3"):
            store.store_forecast(replace(changed, qc_status=QcStatus.QC_SUSPECT))
        assert store.fetch_forecast(first.id).status is ForecastStatus.RAW
        assert store.store_forecast(changed) == changed.id
        assert store.fetch_forecast(first.id).status is ForecastStatus.SUPERSEDED
        assert store.fetch_evidence(first.id) == evidence
        assert standard_store.fetch_forecast(ordinary.id).status is ForecastStatus.RAW
        assert store.fetch_forecast(changed.id).input_lineage == changed.input_lineage

    def test_all_ordinary_reader_paths_are_class_local(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, test = _pair(structural_connection)
        standard_store, test_store = _stores(structural_connection)
        test_store.store_forecast(test)
        assert standard_store.fetch_latest_forecast(ordinary.station_id) is None
        assert standard_store.fetch_forecasts_for_cycle(_ISSUED_A) == []
        assert standard_store.fetch_latest_uncombined_issued_at(_ISSUED_B) is None
        assert (
            standard_store.fetch_forecasts_in_range(
                ordinary.station_id, _ISSUED_A, _ISSUED_B, status=ForecastStatus.RAW
            )
            == []
        )
        assert standard_store.fetch_forecast_summaries(
            ordinary.station_id, _ISSUED_A, _ISSUED_B
        ) == ([], 0)
        standard_store.store_forecast(ordinary)
        standard_store.transition_status(ordinary.id, 1, ForecastStatus.SUPERSEDED)
        historical = standard_store.fetch_forecasts_in_range(
            ordinary.station_id, _ISSUED_A, _ISSUED_B, status=ForecastStatus.SUPERSEDED
        )
        assert [f.id for f in historical] == [ordinary.id]
        assert historical[0].data_use is ForecastDataUse.STANDARD

    def test_failed_replacement_keeps_lineage_values_and_evidence(
        self, structural_connection: sa.Connection
    ) -> None:
        from tests.integration.store.test_forecast_evidence_store import _evidence

        ordinary, first = _pair(structural_connection)
        store = _stores(structural_connection)[1]
        store.store_forecast(first)
        evidence = store.fetch_evidence(first.id)
        changed = replace(
            first,
            id=ForecastId(uuid4()),
            input_lineage=replace(
                first.input_lineage, transformation_versions=("daily-mean-v2",)
            ),
            evidence=replace(_evidence(), snapshot_sha256="0" * 64),
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            store.store_forecast(changed)
        retained = store.fetch_forecast(first.id)
        assert retained.status is ForecastStatus.RAW
        assert retained.input_lineage == first.input_lineage
        assert retained.ensemble.values.equals(first.ensemble.values)
        assert store.fetch_evidence(first.id) == evidence
        assert store.fetch_forecast(changed.id) is None

    def test_lineage_cannot_be_updated(
        self, structural_connection: sa.Connection
    ) -> None:
        _, test = _pair(structural_connection)
        _stores(structural_connection)[1].store_forecast(test)
        with (
            pytest.raises(sa.exc.DBAPIError, match="immutable"),
            structural_connection.begin_nested(),
        ):
            structural_connection.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == test.id)
                .values(input_lineage="{}")
            )

    def test_source_identity_survives_restatement_and_cleanup(
        self, structural_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import observations, stations, tenants
        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.types.forecast_lineage import snapshot_consumed_input
        from tests.conftest import make_observation
        from tests.integration.store.test_forecast_store import _seed_station

        conn = structural_connection
        _, test = _pair(conn)
        # A different station in the same tenant is a legitimate group input.
        obs = make_observation(station_id=_seed_station(conn))
        PgObservationStore(conn).store_observations([obs])
        snapshot = snapshot_consumed_input(obs, units="m")
        test = replace(
            test, input_lineage=replace(test.input_lineage, snapshots=(snapshot,))
        )
        store = _stores(conn)[1]
        store.store_forecast(test)
        conn.execute(
            sa.update(observations).where(observations.c.id == obs.id).values(value=9.0)
        )
        assert store.fetch_forecast(test.id).input_lineage.snapshots == (snapshot,)
        # Remove mutable current data. The retained lineage alone now pins scope.
        conn.execute(sa.delete(observations).where(observations.c.id == obs.id))
        other_tenant = uuid4()
        conn.execute(
            sa.insert(tenants).values(
                id=other_tenant, code="foreign-" + uuid4().hex, name="Foreign fixture"
            )
        )
        for statement in (
            sa.update(stations)
            .where(stations.c.id == obs.station_id)
            .values(tenant_id=other_tenant),
            sa.delete(stations).where(stations.c.id == obs.station_id),
        ):
            with (
                pytest.raises(sa.exc.IntegrityError, match="forecast_input_stations"),
                conn.begin_nested(),
            ):
                conn.execute(statement)

    def test_provisional_input_must_exist_in_output_tenant(
        self, structural_connection: sa.Connection
    ) -> None:
        _, test = _pair(structural_connection)
        test = replace(
            test,
            input_lineage=replace(
                test.input_lineage, provisional_discharge_fingerprints=("a" * 64,)
            ),
        )
        with pytest.raises(sa.exc.DBAPIError, match="input identity or tenant"):
            _stores(structural_connection)[1].store_forecast(test)

    def test_wrong_purpose_status_transition_is_denied(
        self, structural_connection: sa.Connection
    ) -> None:
        from sapphire_flow.exceptions import ConflictError

        ordinary, test = _pair(structural_connection)
        standard_store, test_store = _stores(structural_connection)
        test_store.store_forecast(test)
        with pytest.raises(ConflictError, match="Version mismatch"):
            standard_store.transition_status(test.id, 1, ForecastStatus.SUPERSEDED)
        assert test_store.fetch_forecast(test.id).status is ForecastStatus.RAW

    def test_cross_tenant_provisional_reference_is_refused(
        self, structural_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import tenants
        from sapphire_flow.types.ids import TenantId

        conn = structural_connection
        _, test = _pair(conn)
        tenant = TenantId(uuid4())
        conn.execute(
            sa.insert(tenants).values(
                id=tenant, code="foreign-" + uuid4().hex, name="Foreign"
            )
        )
        args = seed(conn, tenant_id=tenant)
        provisional = convert(*args)
        permit_fixture(conn, tenant)
        PgProvisionalDischargeStore(conn).store_provisional_discharge(
            provisional, captured_at=NOW
        )
        changed = replace(
            test,
            input_lineage=replace(
                test.input_lineage,
                provisional_discharge_fingerprints=(provisional.fingerprint,),
            ),
        )
        with pytest.raises(sa.exc.DBAPIError, match="input identity or tenant"):
            _stores(conn)[1].store_forecast(changed)

    def test_contributors_require_same_class_and_retained_evidence(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, first = _pair(structural_connection)
        standard_store, store = _stores(structural_connection)
        standard_store.store_forecast(ordinary)
        store.store_forecast(first)
        combined = replace(
            first,
            id=ForecastId(uuid4()),
            input_lineage=ForecastInputLineage(
                transformation_versions=("combine-v1",),
                contributor_forecast_ids=(ordinary.id,),
            ),
        )
        with pytest.raises(
            sa.exc.DBAPIError, match="contributor identity, class or tenant"
        ):
            store.store_forecast(combined)
        combined = replace(
            combined,
            input_lineage=replace(
                combined.input_lineage, contributor_forecast_ids=(first.id,)
            ),
        )
        assert store.store_forecast(combined) == combined.id

    def test_consumed_statics_pin_their_source_station(
        self, structural_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import stations
        from sapphire_flow.types.forecast_lineage import ForecastStaticAttributes
        from tests.integration.store.test_forecast_store import _seed_station

        conn = structural_connection
        _, test = _pair(conn)
        sid = _seed_station(conn)
        attributes = ForecastStaticAttributes(
            station_id=sid,
            source="basin-package",
            version="fixture-immutable-v1",
            values=(("area", 10.0),),
        )
        test = replace(
            test,
            input_lineage=ForecastInputLineage(
                static_attributes=(attributes,),
                transformation_versions=("static-projection-v1",),
            ),
        )
        store = _stores(conn)[1]
        store.store_forecast(test)
        assert (
            store.fetch_forecast(test.id).input_lineage.fingerprint
            == test.input_lineage.fingerprint
        )
        with (
            pytest.raises(sa.exc.IntegrityError, match="forecast_input_stations"),
            conn.begin_nested(),
        ):
            conn.execute(sa.delete(stations).where(stations.c.id == sid))

    @pytest.mark.parametrize(
        "contributor_use",
        [ForecastDataUse.STANDARD, ForecastDataUse.EXPIRED_RATING_TEST],
    )
    def test_cross_class_contributor_evidence_is_incomplete(
        self,
        structural_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        contributor_use: ForecastDataUse,
    ) -> None:
        from sapphire_flow.services.forecast_evidence import capture_combined_evidence
        from sapphire_flow.types.domain import ForecastQcRuleSet
        from sapphire_flow.types.forecast_evidence import EvidenceStatus
        from tests.integration.store.test_forecast_evidence_store import _evidence

        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        ordinary, test = _pair(structural_connection)
        standard, testing = _stores(structural_connection)
        test = replace(
            ordinary if contributor_use is ForecastDataUse.STANDARD else test,
            evidence=_evidence(),
        )
        (
            standard if contributor_use is ForecastDataUse.STANDARD else testing
        ).store_forecast(test)
        evidence = capture_combined_evidence(
            model_id=ordinary.model_id,
            strategy="pooled",
            contributors=(test,),
            weights=None,
            qc_rules=ForecastQcRuleSet(version="1", rules=()),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        ).with_thresholds(())
        combined = replace(
            ordinary,
            id=ForecastId(uuid4()),
            issued_at=_ISSUED_B,
            combination_strategy="pooled",
            evidence=evidence,
        )
        standard.store_forecast(combined)
        saved = standard.fetch_evidence(combined.id)
        assert saved.status is EvidenceStatus.INCOMPLETE
        assert ("contributor_evidence_not_persisted" in (saved.reason or "")) == (
            contributor_use is ForecastDataUse.EXPIRED_RATING_TEST
        )

    @pytest.mark.parametrize(
        "purpose", [ForecastDataUse.STANDARD, ForecastDataUse.EXPIRED_RATING_TEST]
    )
    def test_retry_conflict_message_real_fake_parity(
        self, structural_connection: sa.Connection, purpose: ForecastDataUse
    ) -> None:
        from tests.fakes.fake_stores import FakeForecastStore

        ordinary, test = _pair(structural_connection)
        first = ordinary if purpose is ForecastDataUse.STANDARD else test
        real = _stores(structural_connection)[
            purpose is ForecastDataUse.EXPIRED_RATING_TEST
        ]
        fake = FakeForecastStore(data_use=purpose)
        messages = []
        for store in (real, fake):
            store.store_forecast(first)
            with pytest.raises(ForecastRetryConflictError) as error:
                store.store_forecast(
                    replace(first, id=ForecastId(uuid4()), qc_status=QcStatus.QC_FAILED)
                )
            messages.append(str(error.value))
        assert messages[0] == messages[1]
        assert ("Do not retimestamp" in messages[0]) == (
            purpose is ForecastDataUse.EXPIRED_RATING_TEST
        )

    @pytest.mark.parametrize(
        "changes",
        [
            {"version": {}},
            {"member_id": []},
            {"band_id": 2},
            {"value": "NaN"},
            {"extra": "field"},
        ],
    )
    def test_direct_sql_rejects_malformed_historical_structure(
        self, structural_connection: sa.Connection, changes: dict[str, object]
    ) -> None:
        import json

        from sapphire_flow.types.rating_reference import canonical_content

        conn = structural_connection
        _, test = _pair(conn)
        record = dict(
            station_id=str(test.station_id),
            source="recap",
            version="v1",
            valid_time="2026-10-01T00:00:00+00:00",
            parameter="temperature",
            spatial_type="point",
            band_id=None,
            member_id=None,
            value=1.0,
        )
        lineage = json.loads(test.input_lineage.content)
        lineage["snapshots"] = [
            dict(
                kind="historical_forcing",
                units="K",
                content=canonical_content(record | changes),
            )
        ]
        with pytest.raises(sa.exc.DBAPIError, match="snapshot"), conn.begin_nested():
            conn.execute(
                sa.insert(forecasts).values(
                    id=test.id,
                    station_id=test.station_id,
                    model_id=test.model_id,
                    issued_at=test.issued_at,
                    representation="members",
                    parameter="discharge",
                    units="m3/s",
                    data_use="expired_rating_test",
                    input_lineage=canonical_content(lineage),
                )
            )

    @pytest.mark.parametrize(
        "kind,changes",
        [
            ("observation", {"qc_flags": {}}),
            ("observation", {"qc_status": "missing"}),
            (
                "observation",
                {
                    "qc_flags": [
                        dict(rule_id="r", rule_version="v", status="raw", detail=None)
                    ]
                },
            ),
            ("observation", {"rating_curve_correction_version": []}),
            ("weather_forecast", {"is_gap": "false"}),
            ("weather_forecast", {"is_gap": True}),
            ("weather_forecast", {"member_id": []}),
            ("weather_forecast", {"valid_time": None}),
        ],
    )
    def test_direct_sql_per_kind_metadata_and_valid_control(
        self,
        structural_connection: sa.Connection,
        kind: str,
        changes: dict[str, object],
    ) -> None:
        import json

        from sapphire_flow.db.metadata import observations, weather_forecasts
        from sapphire_flow.types.rating_reference import canonical_content

        conn = structural_connection
        _, test = _pair(conn)
        if kind == "observation":
            row = dict(
                conn.execute(
                    sa.select(observations).where(
                        observations.c.station_id == test.station_id
                    )
                )
                .mappings()
                .first()
            )
            row.pop("created_at")
        else:
            row = dict(
                id=uuid4(),
                station_id=test.station_id,
                nwp_source="icon",
                cycle_time=NOW,
                valid_time=NOW,
                parameter="temperature",
                spatial_type="point",
                band_id=None,
                member_id=1,
                value=1.0,
            )
            conn.execute(sa.insert(weather_forecasts).values(**row))
            row.update(is_gap=False, gap_status=None)
        lineage = json.loads(test.input_lineage.content)
        header = dict(
            id=test.id,
            station_id=test.station_id,
            model_id=test.model_id,
            issued_at=test.issued_at,
            representation="members",
            parameter="discharge",
            units="m3/s",
            data_use="expired_rating_test",
        )
        lineage["snapshots"] = [
            dict(
                kind=kind,
                units="fixture-units",
                content=canonical_content(row | changes),
            )
        ]
        with pytest.raises(sa.exc.DBAPIError, match="snapshot"), conn.begin_nested():
            conn.execute(
                sa.insert(forecasts).values(
                    **header, input_lineage=canonical_content(lineage)
                )
            )
        lineage["snapshots"][0]["content"] = canonical_content(row)
        with conn.begin_nested() as transaction:
            conn.execute(
                sa.insert(forecasts).values(
                    **header, input_lineage=canonical_content(lineage)
                )
            )
            transaction.rollback()
