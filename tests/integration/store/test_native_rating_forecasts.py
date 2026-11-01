from __future__ import annotations

import threading
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.cli.import_dhm_delivery import (
    load_station_metadata,
    register_stations,
)
from sapphire_flow.db.metadata import rating_delivery_files, rating_derived_discharge
from sapphire_flow.services.rating_delivery import admit_rating_deliveries
from sapphire_flow.services.rating_derived import derive_rating_sample
from sapphire_flow.services.rating_inputs import merge_rating_days
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import ForecastId, RatingReferenceProofId
from sapphire_flow.types.rating_derived import NativeFeedCustody
from sapphire_flow.types.rating_reference import (
    RatingReferenceProof,
    canonical_content,
    curve_snapshot,
)
from sapphire_flow.types.rejected_forecast import validate_rejected_assignment
from tests.conftest import make_observation
from tests.integration.api.test_dashboard_forecasts import _client, app_overrides_clear
from tests.integration.cli.test_import_dhm_delivery import _CHWRR, _NOW, _ensure_chwrr
from tests.integration.cli.test_rating_only import FIXTURE, delivery
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
)
from tests.integration.store.test_rejected_forecast_store import (
    _entry,
    savepoint_factory,
)


def native_pair(conn: sa.Connection) -> tuple[OperationalForecast, OperationalForecast]:
    tenants = PgTenantStore(conn)
    tenant_id = _ensure_chwrr(tenants)
    register_stations(
        tenants,
        PgStationStore(conn),
        _CHWRR,
        load_station_metadata(FIXTURE / "stations.toml"),
        audit_log_store=PgAuditLogStore(conn),
        now=_NOW,
    )
    admit_rating_deliveries(
        conn, tenant_id, (delivery("native-forecast", date(2026, 9, 8)),), now=_NOW
    )
    file = conn.execute(sa.select(rating_delivery_files)).mappings().one()
    sid = file["station_id"]
    curve = max(
        PgRatingCurveStore(conn).fetch_all_curves_for_station(sid),
        key=lambda c: c.valid_from,
    )
    obs = make_observation(
        station_id=sid, parameter="water_level", value=0.2, timestamp=_NOW
    )
    PgObservationStore(conn).store_observations([obs])
    custody = NativeFeedCustody(
        tenant_id=tenant_id,
        station_id=sid,
        endpoint="https://example.invalid/levels/",
        api_station_id=1,
        evidence_reference="fixture native custody",
        start=_NOW - timedelta(days=1),
        end=_NOW + timedelta(days=2),
    )
    reference = RatingReferenceProof(
        id=RatingReferenceProofId(uuid4()),
        tenant_id=tenant_id,
        station_id=sid,
        endpoint=custody.endpoint,
        api_station_id=1,
        evidence_reference="fixture reference",
        verified_by="fixture",
        verified_at=_NOW,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
        level_unit="m",
        curve_unit="m",
        level_reference="gauge_zero",
        curve_reference="gauge_zero",
        offset_m=0.0,
    )
    sample = derive_rating_sample(
        obs,
        curve,
        file_id=file["id"],
        file_sha256=file["sha256"],
        delivered_on="2026-09-08",
        feed=custody,
        reference=reference,
        at=_NOW,
    )
    values = asdict(sample)
    values.pop("warnings")
    conn.execute(
        sa.insert(rating_derived_discharge).values(
            **values, fingerprint=sample.fingerprint, captured_at=_NOW
        )
    )
    issue = _NOW + timedelta(days=1)
    _, lineage, _ = merge_rating_days(
        pl.DataFrame(), (sample,), issue_time=issue, weather_cycle_time=_NOW
    )
    assert lineage is not None and lineage.rating_input is not None
    mid = _seed_model(conn, "native-fixture-" + uuid4().hex)
    aid = _seed_artifact(conn, sid, mid)
    lineage = replace(
        lineage, rating_input=replace(lineage.rating_input, artifact_id=aid)
    )
    ordinary = _make_forecast(sid, mid, aid, issued_at=issue)
    native = replace(
        ordinary,
        id=ForecastId(uuid4()),
        data_use=ForecastDataUse.PROVISIONAL_RATING,
        input_lineage=lineage,
    )
    return ordinary, native


class TestNativeRatingForecasts:
    def test_success_roundtrip_retry_and_cross_purpose(
        self, db_connection: sa.Connection
    ) -> None:
        conn = db_connection
        ordinary, native = native_pair(conn)
        standard = PgForecastStore(conn, transaction_factory=savepoint_factory(conn))
        scoped = PgForecastStore(
            conn,
            data_use=ForecastDataUse.PROVISIONAL_RATING,
            transaction_factory=savepoint_factory(conn),
        )
        assert standard.store_forecast(ordinary) == ordinary.id
        assert scoped.store_forecast(native) == native.id
        assert scoped.store_forecast(native) == native.id
        assert scoped.fetch_forecast(native.id).input_lineage == native.input_lineage
        assert scoped.fetch_forecast(ordinary.id) is None
        assert standard.fetch_forecast(native.id) is None
        with pytest.raises(ValueError, match="purpose|data use|data-use"):
            standard.store_forecast(native)
        with pytest.raises(ValueError, match="purpose|data use|data-use"):
            scoped.store_forecast(ordinary)

    def test_rejection_roundtrip_and_cross_purpose(
        self, db_connection: sa.Connection
    ) -> None:
        conn = db_connection
        _, native = native_pair(conn)
        entry = _entry(
            station_id=native.station_id,
            model_id=native.model_id,
            issued_at=native.issued_at,
        )
        entry = replace(
            entry,
            payload=replace(
                entry.payload,
                model_artifact_id=native.model_artifact_id,
                data_use=native.data_use,
                input_lineage=native.input_lineage,
            ),
        )
        scoped = PgRejectedForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        )
        standard = PgRejectedForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        )
        scoped.write_batch([entry], abandon=threading.Event())
        rows, total = scoped.fetch_rejected_forecasts(
            native.station_id,
            native.issued_at - timedelta(days=1),
            native.issued_at + timedelta(days=1),
        )
        assert total == 1 and rows[0].input_lineage == native.input_lineage
        assert (
            standard.fetch_rejected_forecasts(
                native.station_id,
                native.issued_at - timedelta(days=1),
                native.issued_at + timedelta(days=1),
            )[1]
            == 0
        )
        with pytest.raises(ValueError, match="purpose|data use|data-use"):
            standard.write_batch([entry], abandon=threading.Event())

    def test_wrong_lineage_variants_are_refused(
        self, db_connection: sa.Connection
    ) -> None:
        _, native = native_pair(db_connection)
        expired = ForecastInputLineage(
            transformation_versions=("v1",),
            provisional_discharge_fingerprints=("a" * 64,),
        )
        with pytest.raises(ValueError, match="lineage"):
            replace(native, input_lineage=expired)
        with pytest.raises(ValueError, match="lineage"):
            replace(native, data_use=ForecastDataUse.EXPIRED_RATING_TEST)
        entry = _entry(
            station_id=native.station_id,
            model_id=native.model_id,
            issued_at=native.issued_at,
        )
        with pytest.raises(ValueError, match="lineage"):
            validate_rejected_assignment(
                replace(
                    entry.payload,
                    data_use=ForecastDataUse.PROVISIONAL_RATING,
                    input_lineage=expired,
                ),
                ForecastDataUse.PROVISIONAL_RATING,
            )
        with pytest.raises(ValueError, match="lineage"):
            validate_rejected_assignment(
                replace(
                    entry.payload,
                    data_use=ForecastDataUse.EXPIRED_RATING_TEST,
                    input_lineage=native.input_lineage,
                ),
                ForecastDataUse.EXPIRED_RATING_TEST,
            )

    def test_authenticated_explicit_api_badge_and_default_exclusion(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.api.routes import tables

        conn = db_connection
        ordinary, native = native_pair(conn)
        PgForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).store_forecast(ordinary)
        PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        ).store_forecast(native)
        tables._reflected = None
        client = _client(conn)
        try:
            assert client.get(f"/forecasts/{native.id}/").status_code == 404
            response = client.get(
                f"/forecasts/{native.id}/?data_use=provisional_rating"
            )
            assert response.status_code == 200
            assert (
                "PROVISIONAL" in response.text
                and "rating_expired_at_issue" in response.text
            )
            data = client.get(
                f"/api/v1/forecasts/{native.id}/data.json?data_use=provisional_rating"
            ).json()
            assert data["data_use"] == "provisional_rating"
            assert "rating_expired_at_issue" in data["warnings"]
            assert str(native.id) not in client.get("/forecasts/").text
            assert (
                str(ordinary.id)
                not in client.get("/forecasts/?data_use=provisional_rating").text
            )
            assert client.get("/forecasts/?data_use=unknown").status_code == 422
            assert (
                client.get("/forecasts/?data_use=expired_rating_test").status_code
                == 422
            )
        finally:
            client.close()
            app_overrides_clear()
            tables._reflected = None


if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast


def test_invalid_newest_original_block_is_a_barrier(
    db_connection: sa.Connection,
) -> None:
    import hashlib
    import json

    from sapphire_flow.adapters.dhm_files import parse_rating_tables
    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.store.rating_derived_store import PgRatingDerivedStore
    from sapphire_flow.store.rating_reference_store import parse_reference_evidence

    conn = db_connection
    _, native = native_pair(conn)
    source = json.loads(conn.scalar(sa.select(rating_derived_discharge.c.content)))
    reference = parse_reference_evidence(canonical_content(source["reference"]))
    conn.execute(
        sa.text("UPDATE stations SET water_level_unit='m' WHERE id=:id"),
        {"id": native.station_id},
    )
    original = delivery("newer-invalid", date(2026, 10, 4))
    invalid = original.original.replace(b"0.1,3", b"0.1,1")
    newer = replace(
        original,
        original=invalid,
        sha256=hashlib.sha256(invalid).hexdigest(),
        table=parse_rating_tables(invalid.decode()),
    )
    admit_rating_deliveries(conn, reference.tenant_id, (newer,), now=_NOW)
    binding = NativeRatingBinding(
        tenant_id=reference.tenant_id,
        station_id=native.station_id,
        station_code="450",
        model_ids=frozenset({native.model_id}),
        reference=reference,
        custody_start=_NOW,
        custody_end=_NOW + timedelta(days=1),
        custody_reference="fixture",
        reconciliation_days=1,
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )
    store = PgRatingDerivedStore(savepoint_factory(conn))
    with pytest.raises(
        ValueError, match="selected historical rating block is invalid; no fallback"
    ):
        store.reconcile_chunk(
            binding, dhm, start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW
        )
    assert (
        conn.scalar(sa.select(sa.func.count()).select_from(rating_derived_discharge))
        == 1
    )
    assert (
        conn.scalar(
            sa.select(rating_delivery_files.c.original).where(
                rating_delivery_files.c.delivery_id == "newer-invalid"
            )
        )
        == invalid
    )
