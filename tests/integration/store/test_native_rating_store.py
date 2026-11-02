from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from sapphire_flow.cli.import_dhm_delivery import (
    load_station_metadata,
    register_stations,
)
from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import NativeRatingBinding
from sapphire_flow.db.metadata import (
    observations,
    rating_curves,
    rating_delivery_files,
    rating_derived_discharge,
)
from sapphire_flow.services.rating_delivery import admit_rating_deliveries
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rating_derived_store import (
    PgRatingDerivedStore,
    rating_transaction_factory,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.ids import ModelId, RatingReferenceProofId
from sapphire_flow.types.rating_reference import RatingReferenceProof, curve_snapshot
from tests.conftest import make_observation
from tests.integration.cli.test_import_dhm_delivery import _CHWRR, _NOW, _ensure_chwrr
from tests.integration.cli.test_rating_only import FIXTURE, delivery


class TestNativeRatingStore:
    def test_committed_retry_qc_generation_and_retention(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        with engine.begin() as conn:
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
                conn,
                tenant_id,
                (delivery("native-store-test", date(2026, 9, 8)),),
                now=_NOW,
            )
            row = (
                conn.execute(
                    sa.select(rating_delivery_files).where(
                        rating_delivery_files.c.delivery_id == "native-store-test"
                    )
                )
                .mappings()
                .one()
            )
            station_id = row["station_id"]
            conn.execute(
                sa.text("UPDATE stations SET water_level_unit='m' WHERE id=:id"),
                {"id": station_id},
            )
            curves = PgRatingCurveStore(conn).fetch_all_curves_for_station(station_id)
            curve = max(curves, key=lambda c: c.valid_from)
            obs = make_observation(
                station_id=station_id,
                parameter="water_level",
                value=0.2,
                timestamp=_NOW,
            )
            PgObservationStore(conn).store_observations([obs])
        endpoint = "https://example.invalid/levels/"
        reference = RatingReferenceProof(
            id=RatingReferenceProofId(uuid4()),
            tenant_id=tenant_id,
            station_id=station_id,
            endpoint=endpoint,
            api_station_id=1,
            evidence_reference="fixture-only native binding",
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
        binding = NativeRatingBinding(
            tenant_id=tenant_id,
            station_id=station_id,
            station_code="450",
            model_ids=frozenset({ModelId("fixture")}),
            reference=reference,
            custody_start=_NOW - timedelta(days=1),
            custody_end=_NOW + timedelta(days=1),
            custody_reference="fixture-only source custody",
            reconciliation_days=1,
        )
        dhm = DhmConfig(
            endpoint=endpoint,
            bindings=(
                DhmBinding(
                    network="dhm",
                    station_code="450",
                    api_station_id=1,
                    level_reference="gauge_zero",
                ),
            ),
            max_requests_per_batch=1,
            max_bytes_per_batch=1000,
            max_batch_seconds=30.0,
        )
        store = PgRatingDerivedStore(rating_transaction_factory(engine.url))
        window = dict(start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW)
        first = store.reconcile_chunk(binding, dhm, **window)
        assert len(first) == 1 and first[0].discharge == pytest.approx(4.0)
        assert store.reconcile_chunk(binding, dhm, **window) == first
        assert store.read_current(binding, dhm, **window) == first
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(rating_derived_discharge)
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.select(observations.c.value).where(observations.c.id == obs.id)
                )
                == 0.2
            )
            conn.execute(
                sa.update(observations)
                .where(observations.c.id == obs.id)
                .values(qc_rule_version="new-qc-generation")
            )
        assert store.read_current(binding, dhm, **window) == ()
        changed = store.reconcile_chunk(binding, dhm, **window)
        assert changed[0].fingerprint != first[0].fingerprint
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(rating_derived_discharge)
                )
                == 2
            )
        with pytest.raises(IntegrityError, match="foreign key"), engine.begin() as conn:
            conn.execute(sa.delete(observations).where(observations.c.id == obs.id))
        with (
            pytest.raises(sa.exc.DatabaseError, match="immutable"),
            engine.begin() as conn,
        ):
            conn.execute(
                sa.update(rating_curves)
                .where(rating_curves.c.id == curve.id)
                .values(version=999)
            )
