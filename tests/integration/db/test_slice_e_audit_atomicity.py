"""Plan 147 Slice E — success-path atomicity (the plan's Slice-B §Verify
rollback test, on a REAL non-AUTOCOMMIT transaction).

A discrete audited write (here: artifact promotion) and its `audit_log` INSERT
run in ONE real transaction via the production `AuditedWriter` /
`make_audited_stores` seam — so a failed audit insert ROLLS BACK the paired
domain mutation. The transaction and its rollback are genuine (a real Postgres
`engine.begin()` block, real `PgModelArtifactStore` mutation); only the audit
INSERT is made to fail (a real `PgAuditLogStore.append_entry` that raises inside
the txn — the plan-endorsed "audit store / entry that raises inside the txn",
NOT a fake-transactional store).

Red-first proof lives in this file as a matched pair:
- ``test_audit_failure_rolls_back_promotion_in_real_transaction`` — the FIX:
  real txn → the promotion is rolled back (status stays TRAINING).
- ``test_audit_failure_persists_promotion_on_autocommit_wiring`` — the pre-fix
  wiring characterization: the SAME audit failure on the shared AUTOCOMMIT
  connection (`setup_production_stores`) leaves the mutation COMMITTED (status
  becomes ACTIVE). This is exactly the non-atomic behavior the fix closes — the
  rollback assertion above would FAIL against this AUTOCOMMIT wiring.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.services.training import promote_artifact
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.audited_writer import AuditedWriter
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_BASIN_RING = [(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)]


def _raise_append(self: object, entry: object) -> None:  # noqa: ARG001
    raise RuntimeError("audit boom")


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[sa.Engine]:
    """Throwaway PostGIS container migrated to head (mirrors
    `test_migration_0045_0046_audit_log.py`)."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_slice_e_atomicity",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            from alembic.config import Config

            from alembic import command

            cfg = Config("alembic.ini")
            cfg.set_main_option("sqlalchemy.url", url)
            command.upgrade(cfg, "head")
            yield engine
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _seed_tenant(
    engine: sa.Engine, code: str, name: str = "Foreign Tenant"
) -> TenantId:
    tid = TenantId(uuid4())
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO tenants (id, code, name) VALUES (:id, :c, :n)"),
            {"id": str(tid), "c": code, "n": name},
        )
    return tid


def _seed_training_artifact(
    engine: sa.Engine,
    artifact_dir: Path,
    tenant_id: TenantId = DEFAULT_TENANT_ID,
) -> tuple[ModelId, ArtifactId, StationId]:
    """Store a `models` row, a `stations` row, and a TRAINING-status artifact
    (committed). Unique ids per call so the module-scoped engine is reusable."""
    model_id = ModelId(f"slice_e_atomicity_{uuid4().hex[:8]}")
    station = make_station_config(
        station_id=StationId(uuid4()),
        code=f"SLICE-E-ATOM-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO models (id, display_name, artifact_scope, description) "
                "VALUES (:id, :dn, 'station', :d)"
            ),
            {"id": str(model_id), "dn": "Slice E atomicity", "d": "test model"},
        )
        PgStationStore(conn).store_station(station)
        aid, _ = PgModelArtifactStore(conn, artifact_dir).store_artifact(
            model_id=model_id,
            artifact_bytes=b"artifact-bytes",
            training_period_start=_NOW,
            training_period_end=_NOW,
            trained_at=_NOW,
            station_id=station.id,
            status=ModelArtifactStatus.TRAINING,
        )
    return model_id, aid, station.id


def _status(engine: sa.Engine, aid: ArtifactId) -> ModelArtifactStatus:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT status FROM model_artifacts WHERE id = :id"),
            {"id": str(aid)},
        ).one()
    return ModelArtifactStatus(row.status)


class TestPromoteAuditAtomicity:
    def test_audit_failure_rolls_back_promotion_in_real_transaction(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model_id, aid, station_id = _seed_training_artifact(pg_engine, tmp_path)
        assert _status(pg_engine, aid) == ModelArtifactStatus.TRAINING

        # Make the real audit-log INSERT fail inside the txn.
        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)

        # The production seam: mutation store + audit store on ONE real txn.
        writer = AuditedWriter(begin=pg_engine.begin)
        with (
            pytest.raises(RuntimeError, match="audit boom"),
            writer.transaction() as stores,
        ):
            promote_artifact(
                artifact_store=stores["artifact_store"],  # type: ignore[arg-type]
                model_id=model_id,
                new_id=aid,
                station_id=station_id,
                audit_log_store=stores["audit_log_store"],  # type: ignore[arg-type]
                now=_NOW,
            )

        # The paired ACTIVE transition rolled back with the failed audit insert.
        assert _status(pg_engine, aid) == ModelArtifactStatus.TRAINING

    def test_audit_failure_persists_promotion_on_autocommit_wiring(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Red-first characterization of the pre-fix wiring: the artifact + audit
        # stores share the AUTOCOMMIT connection (`setup_production_stores`), so
        # the ACTIVE transition commits the instant it runs and the later audit
        # failure canNOT roll it back — the promotion PERSISTS. The rollback
        # assertion in the test above would fail against this wiring.
        model_id, aid, station_id = _seed_training_artifact(pg_engine, tmp_path)
        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)

        with pg_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            store = PgModelArtifactStore(conn, tmp_path)
            audit = PgAuditLogStore(conn)
            with pytest.raises(RuntimeError, match="audit boom"):
                promote_artifact(
                    artifact_store=store,
                    model_id=model_id,
                    new_id=aid,
                    station_id=station_id,
                    audit_log_store=audit,
                    now=_NOW,
                )

        assert _status(pg_engine, aid) == ModelArtifactStatus.ACTIVE


class TestProductionChokepointAtomicityAndRejection:
    """MAJOR 5: exercise the PRODUCTION write chokepoints (not a hand-built
    AuditedWriter) — the promote task, `onboard_model`, and `_run_onboarding`
    Step 2 — so a future regression that reverts a call site to the AUTOCOMMIT
    store, or drops a pre-authorization, is caught."""

    def test_promote_task_rolls_back_domain_write_on_audit_failure(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Through the flow's promote chokepoint (`_promote_artifact_task`) with
        # a real `audited_writer`: a failed audit insert rolls the ACTIVE
        # transition back. Regression guard — goes RED if the task ever wires
        # the AUTOCOMMIT-bound store into the promotion.
        from datetime import timedelta

        from sapphire_flow.flows.onboard_model import _promote_artifact_task
        from sapphire_flow.store.audited_writer import make_audited_writer
        from sapphire_flow.store.station_group_store import PgStationGroupStore
        from sapphire_flow.types.training import TrainingUnit

        model_id, aid, station_id = _seed_training_artifact(pg_engine, tmp_path)
        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)
        unit = TrainingUnit(
            model_id=model_id,
            station_id=station_id,
            group_id=None,
            station_ids=frozenset({station_id}),
            training_period_start=_NOW,
            training_period_end=_NOW,
            time_step=timedelta(days=1),
        )
        conn = pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            with pytest.raises(RuntimeError, match="audit boom"):
                _promote_artifact_task.fn(
                    unit=unit,
                    artifact_id=aid,
                    artifact_store=PgModelArtifactStore(conn, tmp_path),
                    station_store=PgStationStore(conn),
                    group_store=PgStationGroupStore(conn),
                    clock=lambda: _NOW,
                    principal=None,
                    audit_log_store=PgAuditLogStore(conn),
                    audited_writer=make_audited_writer(conn),
                )
        finally:
            conn.close()
        assert _status(pg_engine, aid) == ModelArtifactStatus.TRAINING

    def test_onboard_model_rejects_foreign_unit_before_any_store(
        self, pg_engine: sa.Engine, tmp_path: Path
    ) -> None:
        # BLOCKER 2 (hardened): a foreign-tenant unit must be rejected at the
        # TOP of the loop — BEFORE any domain write — with a DURABLE rejection
        # audit. The unit is fully trainable: a MINIMAL VALID station model,
        # real QC-passed observations, and a basin-linked station, so it WOULD
        # store a TRAINING artifact AND a lineage row if the loop-top
        # `enforce_tenant_isolation` did not block first. The no-write
        # assertions therefore prove authorization runs before any domain
        # mutation — not that an invalid stub crashed on first use.
        import random
        from datetime import timedelta

        from shapely.geometry import MultiPolygon, Polygon

        from sapphire_flow.config.deployment import DeploymentConfig
        from sapphire_flow.exceptions import TenantIsolationError
        from sapphire_flow.flows._db import make_pg_stores
        from sapphire_flow.services.model_onboarding import onboard_model
        from sapphire_flow.store.audited_writer import make_audited_writer
        from sapphire_flow.store.basin_store import PgBasinStore
        from sapphire_flow.store.model_artifact_lineage import PgArtifactLineageWriter
        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.types.basin import Basin
        from sapphire_flow.types.enums import SpatialRepresentation
        from sapphire_flow.types.ids import BasinId
        from sapphire_flow.types.model import ModelDataRequirements
        from sapphire_flow.types.training import TrainingUnit
        from sapphire_flow.types.write_principal import WritePrincipal
        from tests.conftest import make_observations
        from tests.fakes.fake_models import FakeStationForecastModel

        class _MinimalStationModel(FakeStationForecastModel):
            # No forcing/static requirements → training data assembles from the
            # seeded observations alone, keeping setup minimal while the model
            # stays REAL (trains + serializes a genuine artifact).
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset(),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(days=1)}),
                lookback_steps=1,
                forecast_horizon_steps=1,
                spatial_input_type=SpatialRepresentation.POINT,
            )

        period_start = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
        period_end = ensure_utc(datetime(2025, 2, 1, tzinfo=UTC))

        tenant_b = _seed_tenant(pg_engine, f"tenant-b-{uuid4().hex[:6]}")
        model_id = ModelId(f"blocker2_{uuid4().hex[:8]}")
        basin_id = BasinId(uuid4())
        basin = Basin(
            id=basin_id,
            code=f"B2-BASIN-{uuid4().hex[:8]}",
            name="B2 Basin",
            geometry=MultiPolygon([Polygon(_BASIN_RING)]),
            area_km2=10.0,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=_NOW,
            network="bafu",
        )
        station = make_station_config(
            station_id=StationId(uuid4()),
            code=f"B2-{uuid4().hex[:8]}",
            basin_id=basin_id,
            tenant_id=tenant_b,
        )
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO models (id, display_name, artifact_scope, "
                    "description) VALUES (:id, :dn, 'station', :d)"
                ),
                {"id": str(model_id), "dn": "b2", "d": "t"},
            )
            PgBasinStore(conn).store_basin(basin)
            PgStationStore(conn).store_station(station)
            PgObservationStore(conn).store_observations(
                make_observations(
                    n=15,
                    station_id=station.id,
                    parameter="discharge",
                    start=period_start,
                    interval=timedelta(days=1),
                )
            )
        unit = TrainingUnit(
            model_id=model_id,
            station_id=station.id,
            group_id=None,
            station_ids=frozenset({station.id}),
            training_period_start=period_start,
            training_period_end=period_end,
            time_step=timedelta(days=1),
        )
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)  # tenant A

        conn = pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        raised = False
        try:
            stores = make_pg_stores(conn)
            try:
                onboard_model(
                    model_id=model_id,
                    model=_MinimalStationModel(),
                    units=(unit,),
                    model_store=stores["model_store"],  # type: ignore[arg-type]
                    station_store=stores["station_store"],  # type: ignore[arg-type]
                    group_store=stores["group_store"],  # type: ignore[arg-type]
                    artifact_store=stores["artifact_store"],  # type: ignore[arg-type]
                    obs_store=stores["obs_store"],  # type: ignore[arg-type]
                    basin_store=stores["basin_store"],  # type: ignore[arg-type]
                    hindcast_store=stores["hindcast_store"],  # type: ignore[arg-type]
                    skill_store=stores["skill_store"],  # type: ignore[arg-type]
                    flow_regime_store=stores["flow_regime_store"],  # type: ignore[arg-type]
                    forcing_source=object(),  # type: ignore[arg-type]  # unused (no forcing features)
                    config=DeploymentConfig(max_retention_days=600),
                    clock=lambda: _NOW,
                    rng=random.Random(0),
                    principal=principal,
                    skip_smoke_test=True,
                    audit_log_store=stores["audit_log_store"],  # type: ignore[arg-type]
                    audited_writer=make_audited_writer(conn),
                    lineage_writer=PgArtifactLineageWriter(conn),
                )
            except TenantIsolationError:
                raised = True
        finally:
            conn.close()

        with pg_engine.connect() as c:
            n_art = c.execute(
                sa.text("SELECT count(*) FROM model_artifacts WHERE station_id = :sid"),
                {"sid": str(station.id)},
            ).scalar_one()
            n_lineage = c.execute(
                sa.text(
                    "SELECT count(*) FROM model_artifact_basin_versions mabv "
                    "JOIN model_artifacts ma ON ma.id = mabv.model_artifact_id "
                    "WHERE ma.station_id = :sid"
                ),
                {"sid": str(station.id)},
            ).scalar_one()
            n_hindcast = c.execute(
                sa.text(
                    "SELECT count(*) FROM hindcast_forecasts WHERE station_id = :sid"
                ),
                {"sid": str(station.id)},
            ).scalar_one()
            n_skill = c.execute(
                sa.text("SELECT count(*) FROM skill_scores WHERE station_id = :sid"),
                {"sid": str(station.id)},
            ).scalar_one()
            n_rej = c.execute(
                sa.text(
                    "SELECT count(*) FROM audit_log WHERE event_type = "
                    "'model_rejected' AND target_id = :tid"
                ),
                {"tid": str(station.id)},
            ).scalar_one()
        # Loop-top authorization blocked BEFORE any domain write ran: with the
        # gate removed, the trainable unit would have written an artifact +
        # lineage row (these asserts go RED), proving ordering — not a stub crash.
        assert n_art == 0  # rejected BEFORE the TRAINING artifact was stored
        assert n_lineage == 0  # ...and before any lineage row
        assert n_hindcast == 0
        assert n_skill == 0
        assert raised  # the foreign unit was rejected fail-loud (not skipped)
        assert n_rej >= 1  # durable rejection audit survived

    def test_run_onboarding_rejects_cross_tenant_station_takeover(
        self, pg_engine: sa.Engine
    ) -> None:
        # BLOCKER 1: a tenant-A batch that collides on (code, network) with a
        # pre-existing tenant-B station must be rejected (durable audit + raise)
        # and must NOT flip B's tenant.
        from sapphire_flow.exceptions import TenantIsolationError
        from sapphire_flow.flows._db import make_pg_stores
        from sapphire_flow.services.onboarding import _run_onboarding
        from sapphire_flow.store.audited_writer import make_audited_writer
        from sapphire_flow.types.domain import QcRuleSet
        from sapphire_flow.types.write_principal import WritePrincipal

        tenant_b = _seed_tenant(pg_engine, f"tenant-b-{uuid4().hex[:6]}")
        code = f"COLLIDE-{uuid4().hex[:8]}"
        existing = make_station_config(
            station_id=StationId(uuid4()),
            code=code,
            network="bafu",
            tenant_id=tenant_b,
        )
        with pg_engine.begin() as conn:
            PgStationStore(conn).store_station(existing)
        incoming = make_station_config(
            station_id=StationId(uuid4()),
            code=code,
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
        )
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        conn = pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            stores = make_pg_stores(conn)
            with pytest.raises(TenantIsolationError):
                _run_onboarding(
                    stations=[incoming],
                    basins=[],
                    obs_by_station={},
                    forcing_by_station={},
                    basin_store=stores["basin_store"],  # type: ignore[arg-type]
                    station_store=stores["station_store"],  # type: ignore[arg-type]
                    obs_store=stores["obs_store"],  # type: ignore[arg-type]
                    forcing_store=stores["forcing_store"],  # type: ignore[arg-type]
                    baseline_store=stores["baseline_store"],  # type: ignore[arg-type]
                    flow_regime_store=stores["flow_regime_store"],  # type: ignore[arg-type]
                    qc_rules=QcRuleSet(version="t", rules=()),
                    clock=lambda: _NOW,
                    start_utc=_NOW,
                    end_utc=_NOW,
                    tenant_id=DEFAULT_TENANT_ID,
                    principal=principal,
                    audit_log_store=stores["audit_log_store"],  # type: ignore[arg-type]
                    audited_writer=make_audited_writer(conn),
                )
        finally:
            conn.close()

        with pg_engine.connect() as c:
            row = c.execute(
                sa.text("SELECT tenant_id FROM stations WHERE id = :id"),
                {"id": str(existing.id)},
            ).one()
            n_rej = c.execute(
                sa.text(
                    "SELECT count(*) FROM audit_log WHERE event_type = "
                    "'station_onboarded' AND target_id = :tid"
                ),
                {"tid": str(existing.id)},
            ).scalar_one()
        assert str(row.tenant_id) == str(tenant_b)  # B's row NOT taken over
        assert n_rej >= 1  # durable rejection audit survived


def _run_onboarding_prod(
    engine: sa.Engine,
    stations: list[object],
    *,
    principal: object | None = None,
) -> object:
    """Drive the PRODUCTION Step-2 station chokepoint (`_run_onboarding` with a
    real `audited_writer` + `audit_log_store`), so the per-station
    `_write_station_with_audit` helper runs — NOT a hand-built writer."""
    from sapphire_flow.flows._db import make_pg_stores
    from sapphire_flow.services.onboarding import _run_onboarding
    from sapphire_flow.store.audited_writer import make_audited_writer
    from sapphire_flow.types.domain import QcRuleSet

    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        stores = make_pg_stores(conn)
        return _run_onboarding(
            stations=stations,  # type: ignore[arg-type]
            basins=[],
            obs_by_station={},
            forcing_by_station={},
            basin_store=stores["basin_store"],  # type: ignore[arg-type]
            station_store=stores["station_store"],  # type: ignore[arg-type]
            obs_store=stores["obs_store"],  # type: ignore[arg-type]
            forcing_store=stores["forcing_store"],  # type: ignore[arg-type]
            baseline_store=stores["baseline_store"],  # type: ignore[arg-type]
            flow_regime_store=stores["flow_regime_store"],  # type: ignore[arg-type]
            qc_rules=QcRuleSet(version="t", rules=()),
            clock=lambda: _NOW,
            start_utc=_NOW,
            end_utc=_NOW,
            tenant_id=DEFAULT_TENANT_ID,
            principal=principal,  # type: ignore[arg-type]
            audit_log_store=stores["audit_log_store"],  # type: ignore[arg-type]
            audited_writer=make_audited_writer(conn),
        )
    finally:
        conn.close()


class TestStationWriteAuditAtomicity:
    """MAJOR 4: each station's `store_station`/`update_station` + its
    STATION_ONBOARDED audit row run in ONE per-station transaction (the
    production `_write_station_with_audit` helper wired through
    `_run_onboarding`), so an audit-insert failure ROLLS the station mutation
    back. Regression guard for BOTH the insert (new station) and update
    (existing station) paths — goes RED if the helper regresses to the shared
    AUTOCOMMIT station store (the mutation would then commit before the audit
    insert fails and could not roll back)."""

    def test_audit_failure_rolls_back_new_station_insert(
        self, pg_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        incoming = make_station_config(
            station_id=StationId(uuid4()),
            code=f"ATOM-INS-{uuid4().hex[:8]}",
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
        )
        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)

        result = _run_onboarding_prod(pg_engine, [incoming])

        # The per-station txn rolled back: the failure is recorded (not raised)
        # and no `stations` row was committed.
        assert result.stations_created == 0  # type: ignore[attr-defined]
        with pg_engine.connect() as c:
            n = c.execute(
                sa.text("SELECT count(*) FROM stations WHERE id = :id"),
                {"id": str(incoming.id)},
            ).scalar_one()
        assert n == 0  # station insert rolled back with the failed audit insert

    def test_audit_failure_rolls_back_existing_station_update(
        self, pg_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code = f"ATOM-UPD-{uuid4().hex[:8]}"
        original = make_station_config(
            station_id=StationId(uuid4()),
            code=code,
            name="Original Name",
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
        )
        with pg_engine.begin() as conn:
            PgStationStore(conn).store_station(original)

        # Same (code, network) → the update path; a changed name would persist
        # if the mutation were not txn-paired with the audit insert.
        incoming = make_station_config(
            station_id=StationId(uuid4()),
            code=code,
            name="Changed Name",
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
        )
        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)

        result = _run_onboarding_prod(pg_engine, [incoming])

        assert result.stations_updated == 0  # type: ignore[attr-defined]
        with pg_engine.connect() as c:
            name = c.execute(
                sa.text("SELECT name FROM stations WHERE id = :id"),
                {"id": str(original.id)},
            ).scalar_one()
        assert name == "Original Name"  # update rolled back — row UNCHANGED
