from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal
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


@pytest.mark.parametrize("evidence_mode", ["legacy", "native", "mixed"])
def test_worker_admits_reconciles_and_records_noop_outcome(
    role_harness: _RoleBootstrapHarness,
    evidence_mode: Literal["legacy", "native", "mixed"],
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin() as transaction:
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
        if evidence_mode != "legacy":
            from tests.integration.store.test_native_rating_capture_selection import (
                capture,
                native_binding,
            )

            assert not conn.scalar(
                sa.text(
                    "SELECT has_table_privilege(current_user, "
                    "'native_measurement_captures', 'UPDATE')"
                )
            )
            legacy_records = ()
            if evidence_mode == "mixed":
                legacy_records = store.reconcile_chunk(
                    binding,
                    dhm,
                    start=_NOW + timedelta(minutes=1),
                    end=_NOW + timedelta(hours=1),
                    at=_NOW + timedelta(hours=1),
                )
                assert {record.evidence_version for record in legacy_records} == {1}
            capture(conn, binding)
            binding = native_binding(binding)
            window["end"] = _NOW + timedelta(seconds=30)
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            first = store.reconcile_chunk(binding, dhm, **window)
            assert len(first) == 1
            assert first[0].evidence_version == 2
            assert first[0].discharge is not None
            assert store.read_current(binding, dhm, **window) == first
            assert store.reconcile_chunk(binding, dhm, **window) == first

            import threading

            import polars as pl

            from sapphire_flow.services.rating_inputs import merge_rating_days
            from sapphire_flow.store.forecast_store import PgForecastStore
            from sapphire_flow.store.rejected_forecast_store import (
                PgRejectedForecastStore,
            )
            from sapphire_flow.types.forecast_lineage import ForecastInputLineage
            from tests.integration.store.test_rejected_forecast_store import _entry

            _, lineage, _ = merge_rating_days(
                pl.DataFrame(),
                (*first, *legacy_records),
                issue_time=native.issued_at,
                weather_cycle_time=native.issued_at,
            )
            assert lineage is not None and lineage.rating_input is not None
            assert set(lineage.transformation_versions) == (
                {
                    "native-rating-linear-endpoints-v1",
                    "native-rating-linear-endpoints-v2",
                }
                if evidence_mode == "mixed"
                else {"native-rating-linear-endpoints-v2"}
            )
            lineage = replace(
                lineage,
                rating_input=replace(
                    lineage.rating_input, artifact_id=native.model_artifact_id
                ),
            )
            native = replace(native, input_lineage=lineage)
            forecasts = PgForecastStore(
                conn,
                data_use=native.data_use,
                transaction_factory=savepoint_factory(conn),
            )
            from sapphire_flow.db.metadata import (
                forecasts as forecast_table,
            )
            from sapphire_flow.db.metadata import (
                rejected_forecasts,
            )

            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(forecast_table)
                    .where(forecast_table.c.id == native.id)
                )
                == 0
            )
            assert forecasts.store_forecast(native) == native.id
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
                    input_lineage=lineage,
                ),
            )
            rejected = PgRejectedForecastStore(
                conn,
                data_use=native.data_use,
                transaction_factory=savepoint_factory(conn),
            )
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(rejected_forecasts)
                    .where(
                        rejected_forecasts.c.station_id == native.station_id,
                        rejected_forecasts.c.model_id == native.model_id,
                    )
                )
                == 0
            )
            rejected.write_batch([entry], abandon=threading.Event())
            conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
            assert forecasts.fetch_forecast(native.id).input_lineage == lineage
            rejected_rows, total = rejected.fetch_rejected_forecasts(
                native.station_id,
                native.issued_at - timedelta(days=1),
                native.issued_at + timedelta(days=1),
            )
            assert total == 1 and rejected_rows[0].input_lineage == lineage
            for identity, is_rejected in (
                (native.id, False),
                (rejected_rows[0].id, True),
            ):
                encoded = conn.scalar(
                    sa.select(sa.func.native_rating_lineage(identity, is_rejected))
                )
                assert ForecastInputLineage.from_content(encoded) == lineage
            transaction.rollback()
            return
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
        after = conn.execute(audit_query).scalars().all()
        assert len(after) == 2
        noop = next(row for row in after if row["inserted"] == 0)
        assert (noop["accepted"], noop["rejected"], noop["schema_version"]) == (2, 4, 2)
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
        transaction.rollback()


if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness
