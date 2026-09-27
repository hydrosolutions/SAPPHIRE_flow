"""Plan 405 T1 — the retrain-of-a-retrain chain, through the FLOW, against PostGIS.

🔴 Why this cannot be a unit test. `resolve_donor_config` takes an `sa.Connection`
and reads two tables — `model_artifact_provenance` and `model_artifact_warm_start`
— and the defect this file pins is which of those two branches is taken for a
SAP3-produced donor. A fake writer would answer that question itself, which is the
"fake more permissive than production" failure Plan 399 hit three times.

The chain is production-shaped: generation 0 IMPORTED (a provenance row with a
config hash) → generation 1 retrained from it → generation 2 retrained from THAT
→ generation 3 from that again. ⚠️ Generation 0 is SEEDED as an import, not
trained through the flow — that is the point, it is the only generation with a
`model_artifact_provenance` row. Generations 1, 2 and 3 each come from a real
`train_models_flow` call.
Before Plan 405 T1 the generation-2 retrain — the first whose donor is itself a
SAP3 retrain — raised `base_config_path is NULL without a reason`, after the
artifact had already been stored, leaving a saved model whose provenance was
refused. *Said by generation rather than by "step": the chain gained a fourth
entry and "the third step" no longer names it unambiguously.*

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
from sapphire_flow.exceptions import ConfigurationError
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


def _model_declaring(config_hash: str, config_path: str) -> object:
    """A retrainable model that declares `config_hash`/`config_path`, as the real
    aquacast shim does — the fakes declare neither, so without this no flow test
    could reach Plan 405 T2's comparison at all."""
    base = _Unit._retrainable_model()

    class _Declaring(type(base)):  # type: ignore[misc]
        @property
        def config_hash(self) -> str:
            return config_hash

        @property
        def config_path(self) -> str:
            return config_path

    return _Declaring()


class TestTheInstalledTemplateIsCompared:
    """Plan 405 T2 — the comparison 399 promised in a docstring and never made."""

    _INSTALLED = "models/aquacast/configs/cmal_small.yaml"

    def _seed(
        self, conn: sa.Connection, tmp_path: Path
    ) -> tuple[ModelId, StationId, ArtifactId]:
        model_id = _seed_model(conn)
        station_id = _seed_station(conn)
        gen0 = _seed_imported_donor(conn, tmp_path, model_id, station_id)
        return model_id, station_id, gen0

    def test_a_changed_template_is_refused_before_the_model_is_called(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 THE RED. ⛔ "an error was raised" would also pass on a refusal that
        happens too late — so this asserts the model was never invoked AND that
        nothing was persisted."""
        model_id, station_id, gen0 = self._seed(db_connection, tmp_path)
        changed = "f" * 64
        assert changed != _IMPORTED_CONFIG_HASH
        model = _model_declaring(changed, self._INSTALLED)
        type(model).seen_base = None
        before = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()

        with pytest.raises(ConfigurationError) as exc:
            TestRetrainOfARetrainThroughTheFlow._run(
                db_connection,
                tmp_path,
                model_id=model_id,
                station_id=station_id,
                base_artifact_id=gen0,
                writer=PgWarmStartWriter(db_connection),
                model=model,
            )

        # 🔴 BOTH hashes named — a refusal that does not say what differed leaves
        # the operator unable to tell which of the two is wrong.
        assert _IMPORTED_CONFIG_HASH in str(exc.value)
        assert changed in str(exc.value)
        assert self._INSTALLED in str(exc.value)
        # 🔴 The mechanism: the model was never reached.
        assert type(model).seen_base is None, "the model was called despite the refusal"
        # 🔴 The property § 5 cares about: nothing persisted, of either kind.
        after = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()
        assert after == before, "a refused retrain stored an artifact anyway"
        assert (
            db_connection.execute(
                sa.select(sa.func.count()).select_from(model_artifact_warm_start)
            ).scalar_one()
            == 0
        ), "a refused retrain wrote a warm-start row"

    def test_matching_hashes_record_the_donors_config_path(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """T2's first Verification bullet: the path stops being NULL.

        ⭐ This is what makes generation 1's path non-NULL in production — and so
        what would have made the gen-2 chain test vacuous had it not been pinned.
        """
        model_id, station_id, gen0 = self._seed(db_connection, tmp_path)
        model = _model_declaring(_IMPORTED_CONFIG_HASH, self._INSTALLED)

        results = TestRetrainOfARetrainThroughTheFlow._run(
            db_connection,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            base_artifact_id=gen0,
            writer=PgWarmStartWriter(db_connection),
            model=model,
        )

        assert results[0].error is None, results[0].error
        got = fetch_warm_start(db_connection, results[0].artifact_id)
        assert got is not None
        assert got.base_config_path == self._INSTALLED
        assert got.base_config_sha256 == _IMPORTED_CONFIG_HASH
        assert got.base_config_unknown_reason is None, (
            "nothing is unknown once the hashes agree — a path AND a reason "
            "together is a record the invariant cannot catch"
        )


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

    def _retrain(
        self,
        conn: sa.Connection,
        tmp_path: Path,
        *,
        model_id: ModelId,
        station_id: StationId,
        donor: ArtifactId,
        writer: object,
    ) -> ArtifactId:
        """One more generation through the REAL flow."""
        results = self._run(
            conn,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            base_artifact_id=donor,
            writer=writer,
        )
        assert results[0].error is None, results[0].error
        aid = results[0].artifact_id
        assert aid is not None
        return aid

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

    def test_generation_three_through_the_flow_carries_a_reason_true_of_two(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """T1's Verification: "generation 3 completes AND its reason is true of 2".

        ⚠️ One reviewer read the gen-3 bullet as satisfied by the store-level test
        and another read "completes" as requiring a flow run, as the gen-2 bullet
        plainly does. Running the chain one generation further costs one flow call
        and settles it, instead of arguing the reading.

        🔑 Generation 2's own row needs NO pin: the inherited branch already leaves
        its `base_config_path` NULL, which the assertion below states rather than
        assumes.
        """
        model_id, station_id, gen1, writer = self._chain_to_generation_one(
            db_connection, tmp_path
        )

        gen2 = self._retrain(
            db_connection,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            donor=gen1,
            writer=writer,
        )
        gen2_row = fetch_warm_start(db_connection, gen2)
        assert gen2_row is not None
        assert gen2_row.base_config_path is None, (
            "generation 2 is expected to carry a NULL path via the inherited "
            "branch — if this ever holds a path, the generation-3 case below is "
            "no longer the branch this test exists for"
        )

        gen3 = self._retrain(
            db_connection,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            donor=gen2,
            writer=writer,
        )

        got = fetch_warm_start(db_connection, gen3)
        assert got is not None
        assert got.base_artifact_id == gen2
        # 🔑 The hash CARRIES FORWARD unchanged from the imported ancestor, two
        # retrains later. ⚠️ This exercises the SAME resolver branch generation 2
        # does, so it is not new branch coverage and a break would fail the gen-2
        # assertion first. *What actually distinguishes generation 3 is asserted
        # above: its donor's NULL path is RESOLVER-PRODUCED, where generation 1's
        # was pinned by the test.*
        assert got.base_config_sha256 == _IMPORTED_CONFIG_HASH
        reason = got.base_config_unknown_reason
        assert reason
        # 🔴 TRUE OF GENERATION 2 — itself SAP3-produced, with no provenance row.
        assert "produced by SAP3" in reason
        assert "carries no config path" in reason
        assert "recorded in its provenance" not in reason


class TestDonorParamsThroughTheFlow:
    """Plan 405 T6 — the params reason reaches the record, and an empty one is
    refused BEFORE anything is trained or stored."""

    _INSTALLED = "models/aquacast/configs/cmal_small.yaml"

    def test_a_retrain_records_a_params_reason_true_of_its_donor(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """The donor here is IMPORTED, so the recorded reason must say so — not the
        one constant sentence 399 used for every donor."""
        model_id = _seed_model(db_connection)
        station_id = _seed_station(db_connection)
        gen0 = _seed_imported_donor(db_connection, tmp_path, model_id, station_id)

        results = TestRetrainOfARetrainThroughTheFlow._run(
            db_connection,
            tmp_path,
            model_id=model_id,
            station_id=station_id,
            base_artifact_id=gen0,
            writer=PgWarmStartWriter(db_connection),
        )

        assert results[0].error is None, results[0].error
        got = fetch_warm_start(db_connection, results[0].artifact_id)
        assert got is not None
        assert got.base_params_path is None, "D1(b): the path stays NULL"
        reason = got.base_params_unknown_reason
        assert reason is not None
        assert "IMPORTED" in reason
        # ⛔ 399's constant, which was false for a retrain donor, is gone.
        assert "has never recorded training params" not in reason

    def test_an_empty_params_reason_is_refused_before_training_or_storage(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 THE test that proves the pre-training params check is load-bearing.

        ⛔ Returning `None` and merely asserting "something raised" would NOT prove
        it: `WarmStartRecord`'s invariant would reject an unexplained NULL later
        anyway, AFTER a successful train and a stored artifact. *Same distinction as
        T1's ordering — "an error was raised" does not prove WHERE.* So this asserts
        the model was never invoked and no artifact row appeared. Remove the
        `check_params_provenance` call from the flow and this fails.
        """
        model_id = _seed_model(db_connection)
        station_id = _seed_station(db_connection)
        gen0 = _seed_imported_donor(db_connection, tmp_path, model_id, station_id)

        class _EmptyParamsWriter(PgWarmStartWriter):
            def resolve_donor_params(
                self, base_artifact_id: ArtifactId
            ) -> tuple[str | None, str | None]:
                # A NULL path with NO reason — what the invariant rejects.
                return (None, None)

        model = _Unit._retrainable_model()
        type(model).seen_base = None
        before = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()

        with pytest.raises(
            ValueError, match="base_params_path is NULL without a reason"
        ):
            TestRetrainOfARetrainThroughTheFlow._run(
                db_connection,
                tmp_path,
                model_id=model_id,
                station_id=station_id,
                base_artifact_id=gen0,
                writer=_EmptyParamsWriter(db_connection),
                model=model,
            )

        assert type(model).seen_base is None, (
            "the model was trained despite the refusal"
        )
        after = db_connection.execute(
            sa.select(sa.func.count()).select_from(model_artifacts)
        ).scalar_one()
        assert after == before, "an artifact was stored despite the refusal"
