"""Plan 405 T1 — the retrain-of-a-retrain chain, through the FLOW, against PostGIS.

🔴 Why this cannot be a unit test. `resolve_donor_config` takes an `sa.Connection`
and reads two tables — `model_artifact_provenance` and `model_artifact_warm_start`
— and the defect this file pins is which of those two branches is taken for a
SAP3-produced donor. A fake writer would answer that question itself, which is the
"fake more permissive than production" failure Plan 399 hit three times.

The chain is production-shaped: generation 0 IMPORTED (a provenance row with a
config hash) → generation 1 retrained from it → generation 2 retrained from THAT.
Before Plan 405 T1 the third step raised `base_config_path is NULL without a
reason` — after the artifact had already been stored, leaving a saved model whose
provenance was refused.

Only the warm-start writer and the artifact store are real; the rest of the
training run uses the unit suite's fakes, with the rows the foreign keys require
seeded into Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts
from sapphire_flow.flows.train_models import train_models_flow
from sapphire_flow.store.model_artifact_provenance import record_artifact_provenance
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.model_artifact_warm_start import (
    PgWarmStartWriter,
    fetch_warm_start,
    model_artifact_warm_start,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.model import ModelArtifactProvenance
from tests.integration.store.test_model_artifact_warm_start import (
    _seed_model,
    _seed_station,
)
from tests.unit.flows.test_train_models import (
    _EPOCH,
    _TRAINING_END,
    _TRAINING_START,
    _flow_kwargs,
    _setup_station_stores,
)
from tests.unit.flows.test_train_models import (
    TestWarmStartRetrainThroughTheFlow as _Unit,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId

_IMPORTED_CONFIG_HASH = "a" * 64


def _seed_imported_donor(
    conn: sa.Connection, tmp_path: Path, model_id: ModelId, station_id: StationId
) -> ArtifactId:
    """Generation 0: an EXTERNALLY imported artifact, as onboarding writes it.

    The provenance row carries a `config_hash` and no path — that asymmetry is
    what makes generation 1's own record carry a NULL path, which is the whole
    setup for the defect.
    """
    aid, _ = PgModelArtifactStore(conn, tmp_path).store_artifact(
        model_id,
        b"gen0-donor-bytes",
        _TRAINING_START,
        _TRAINING_END,
        _EPOCH,
        station_id=station_id,
    )
    record_artifact_provenance(
        conn,
        ModelArtifactProvenance(
            artifact_id=aid,
            source_repository="hydrosolutions/aquacast",
            source_commit="deadbeef",
            config_hash=_IMPORTED_CONFIG_HASH,
            imported_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            imported_by="onboarding",
            notes=None,
        ),
    )
    return aid


class TestRetrainOfARetrainThroughTheFlow:
    """§ 5 — the crash, and the reason that has to be true of the donor at hand."""

    @staticmethod
    def _run(
        conn: sa.Connection,
        tmp_path: Path,
        *,
        model_id: ModelId,
        station_id: StationId,
        base_artifact_id: ArtifactId,
        writer: object,
        model: object | None = None,
    ) -> list:
        stores = _setup_station_stores(station_id, model_id)
        kwargs = _flow_kwargs(model_id, model or _Unit._retrainable_model(), *stores)
        # 🔑 The REAL artifact store: every artifact the run stores gets a real
        # `model_artifacts` row, so the warm-start FK resolves without mirroring.
        kwargs["artifact_store"] = PgModelArtifactStore(conn, tmp_path)
        return train_models_flow(
            **kwargs,
            base_artifact_id=str(base_artifact_id),
            training_params={"finetuning": {"strategy": "last_layer"}},
            warm_start_writer=writer,
        )

    def _chain_to_generation_one(
        self, conn: sa.Connection, tmp_path: Path
    ) -> tuple[ModelId, StationId, ArtifactId, PgWarmStartWriter]:
        # 🔑 The flow must run on the station that EXISTS in Postgres —
        # `model_artifacts.station_id` is a FK, and the fakes do not enforce it.
        model_id = _seed_model(conn)
        station_id = _seed_station(conn)
        gen0 = _seed_imported_donor(conn, tmp_path, model_id, station_id)
        writer = PgWarmStartWriter(conn)

        results = self._run(
            conn,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            base_artifact_id=gen0,
            writer=writer,
        )
        assert results[0].error is None, results[0].error
        gen1 = results[0].artifact_id
        assert gen1 is not None

        # ⚠️ PIN generation 1 to a NULL config path DELIBERATELY. It is already
        # NULL today, but T2 starts recording a real installed path — at which
        # point this test would silently stop exercising the inherited-NULL
        # branch it exists for. Forcing the case keeps the red meaningful.
        conn.execute(
            sa.update(model_artifact_warm_start)
            .where(model_artifact_warm_start.c.model_artifact_id == gen1)
            .values(
                base_config_path=None,
                base_config_unknown_reason=(
                    "donor's config hash is recorded in its provenance, but no "
                    "installed config path was supplied to pair with it"
                ),
            )
        )
        pinned = fetch_warm_start(conn, gen1)
        assert pinned is not None
        assert pinned.base_config_path is None, "the pin did not take"
        assert pinned.base_config_sha256 == _IMPORTED_CONFIG_HASH
        return model_id, station_id, gen1, writer

    def test_generation_two_completes_and_its_reason_is_true_of_generation_one(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 The defect: this run raised after the artifact was already stored."""
        model_id, station_id, gen1, writer = self._chain_to_generation_one(
            db_connection, tmp_path
        )

        results = self._run(
            db_connection,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            base_artifact_id=gen1,
            writer=writer,
        )

        assert results[0].error is None, results[0].error
        gen2 = results[0].artifact_id
        assert gen2 is not None

        got = fetch_warm_start(db_connection, gen2)
        assert got is not None
        assert got.base_artifact_id == gen1
        assert got.base_config_path is None
        assert got.base_config_sha256 == _IMPORTED_CONFIG_HASH
        reason = got.base_config_unknown_reason
        assert reason
        # 🔴 TRUE OF GENERATION 1 — which is SAP3-produced and has NO provenance
        # row. Inheriting generation 0's sentence would assert the opposite.
        assert "produced by SAP3" in reason
        assert "carries no config path" in reason
        assert "recorded in its provenance" not in reason

    def test_a_refusal_resolved_before_training_stores_nothing(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 The ordering, stated as a consequence: refuse ⇒ nothing persisted.

        ⚠️ The refusal CONDITION is injected — a contradictory triple cannot be
        produced by the fixed resolver, which is the point of the fix. What is
        verified is not the stand-in but the real flow's ORDERING: no
        `model_artifacts` row, no warm-start row, and `retrain` never reached.
        Before this task the resolution happened after the store, so the same
        refusal left a saved artifact behind.
        """
        model_id, station_id, gen1, _ = self._chain_to_generation_one(
            db_connection, tmp_path
        )

        class _RefusingWriter(PgWarmStartWriter):
            def resolve_donor_config(
                self,
                base_artifact_id: ArtifactId,
                *,
                installed_config_path: str | None = None,
                installed_config_sha256: str | None = None,
            ) -> tuple[str | None, str | None, str | None]:
                # A NULL path with no reason — exactly what `WarmStartRecord`
                # rejects, and what the flow must now reject BEFORE training.
                return (None, "b" * 64, None)

        model = _Unit._retrainable_model()
        type(model).seen_base = None
        before = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()

        with pytest.raises(
            ValueError, match="base_config_path is NULL without a reason"
        ):
            self._run(
                db_connection,
                tmp_path,
                model_id=model_id,
                station_id=station_id,
                base_artifact_id=gen1,
                writer=_RefusingWriter(db_connection),
                model=model,
            )

        after = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()
        assert after == before, "a refused retrain stored an artifact anyway"
        assert type(model).seen_base is None, "training ran despite the refusal"
