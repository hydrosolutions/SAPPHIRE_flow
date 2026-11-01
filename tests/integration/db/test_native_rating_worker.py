from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import NativeRatingBinding
from sapphire_flow.db.metadata import audit_log, rating_derived_discharge
from sapphire_flow.services.rating_delivery import admit_rating_deliveries
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rating_derived_store import PgRatingDerivedStore
from sapphire_flow.store.rating_reference_store import parse_reference_evidence
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.rating_reference import canonical_content, curve_snapshot
from tests.conftest import make_observation
from tests.integration.cli.test_import_dhm_delivery import _NOW
from tests.integration.cli.test_rating_only import delivery
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import savepoint_factory


def test_worker_admits_reconciles_and_reads_mixed_samples_without_noop_audit(
    role_harness: _RoleBootstrapHarness,
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        _, native = native_pair(conn)
        content = conn.scalar(
            sa.select(rating_derived_discharge.c.content).where(
                rating_derived_discharge.c.station_id == native.station_id
            )
        )
        reference = parse_reference_evidence(
            canonical_content(json.loads(content)["reference"])
        )
        conn.execute(
            sa.text("UPDATE stations SET water_level_unit='m' WHERE id=:id"),
            {"id": native.station_id},
        )
        samples = [
            replace(
                make_observation(
                    station_id=native.station_id,
                    parameter="water_level",
                    value=value,
                    timestamp=_NOW + timedelta(minutes=i + 1),
                    qc_status=QcStatus.MISSING if value is None else QcStatus.RAW,
                ),
                id=uuid4(),
            )
            for i, value in enumerate(
                [0.3, float("nan"), float("inf"), float("-inf"), None]
            )
        ]
        PgObservationStore(conn).store_observations(samples)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        assert (
            admit_rating_deliveries(
                conn,
                reference.tenant_id,
                (delivery("worker-native", date(2026, 9, 9)),),
                now=_NOW,
            )
            == 1
        )
        curve = max(
            PgRatingCurveStore(conn).fetch_all_curves_for_station(native.station_id),
            key=lambda c: c.version,
        )
        reference = replace(
            reference, rating_curve_id=curve.id, curve=curve_snapshot(curve)
        )
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
        window = dict(
            start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW + timedelta(hours=1)
        )
        first = store.reconcile_chunk(binding, dhm, **window)
        assert len(first) == 6
        assert sum(r.discharge is not None for r in first) == 2
        assert sum(r.rejection is not None for r in first) == 4
        assert store.read_current(binding, dhm, **window) == first
        audit_query = sa.select(audit_log.c.detail).where(
            audit_log.c.target_id == str(native.station_id),
            audit_log.c.detail["command"].astext == "rating-derived-reconcile",
        )
        before = conn.execute(audit_query).scalars().all()
        assert (
            len(before) == 1
            and before[0]["inserted"] == 6
            and before[0]["rejected"] == 4
        )
        assert store.reconcile_chunk(binding, dhm, **window) == first
        assert conn.execute(audit_query).scalars().all() == before
        wrong = replace(
            dhm, bindings=(replace(dhm.bindings[0], level_reference="masl"),)
        )
        with pytest.raises(ValueError, match="datum differs"):
            store.read_current(binding, wrong, **window)
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.text("UPDATE stations SET water_level_unit='ft' WHERE id=:id"),
            {"id": native.station_id},
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with pytest.raises(ValueError, match="metre unit"):
            store.read_current(binding, dhm, **window)


if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness
