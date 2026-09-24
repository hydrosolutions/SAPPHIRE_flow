"""Plan 316 T1 — the fake must mirror the store's status filter exactly.

Consumers are tested against `FakeObservationStore`, not against PostgreSQL. A
fake that accepted only a scalar status would make every two-status caller
untestable — and worse, would let a consumer that IGNORES the new status pass
its tests while failing in production.
"""

from __future__ import annotations

import random
from collections.abc import Collection  # noqa: TC003 — runtime annotation use
from datetime import UTC, datetime, timedelta

import pytest

from sapphire_flow.protocols.stores import (
    ObservationStore,  # noqa: TC001 — annotation is the point of the test
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from tests.fakes.fake_stores import FakeObservationStore

_NOW = ensure_utc(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
_STATION = StationId(random.Random(7).randbytes(16).hex())


def _obs(hours: int, status: QcStatus) -> Observation:
    from uuid import uuid4

    return Observation(
        id=ObservationId(uuid4()),
        station_id=_STATION,
        timestamp=ensure_utc(_NOW + timedelta(hours=hours)),
        parameter="discharge",
        value=float(hours),
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_NOW,
    )


def _store() -> ObservationStore:
    """⚠️ Typed as the PROTOCOL, not the concrete fake. T1's Verification says
    "through one Protocol fake", and the point is to prove the CONTRACT holds —
    a helper typed as the concrete class would still pass if the Protocol and
    the fake had drifted apart."""
    store = FakeObservationStore()
    for i, status in enumerate(
        (
            QcStatus.QC_PASSED,
            QcStatus.QC_UNCHECKED,
            QcStatus.QC_FAILED,
            QcStatus.RAW,
        )
    ):
        store._observations[_obs(i, status).id] = _obs(i, status)  # noqa: SLF001
    return store


def _fetch(
    store: ObservationStore,
    qc_status: QcStatus | Collection[QcStatus] | None = None,
) -> list[Observation]:
    return store.fetch_observations(
        station_id=_STATION,
        parameter="discharge",
        start=_NOW,
        end=ensure_utc(_NOW + timedelta(hours=9)),
        qc_status=qc_status,
    )


class TestFakeMirrorsTheStore:
    def test_a_two_status_read_returns_the_union(self) -> None:
        fetched = _fetch(_store(), {QcStatus.QC_PASSED, QcStatus.QC_UNCHECKED})

        assert {o.qc_status for o in fetched} == {
            QcStatus.QC_PASSED,
            QcStatus.QC_UNCHECKED,
        }

    @pytest.mark.parametrize(
        "status",
        [
            QcStatus.QC_PASSED,
            QcStatus.QC_UNCHECKED,
            QcStatus.QC_FAILED,
            QcStatus.RAW,
        ],
    )
    def test_a_single_status_read_is_unchanged(self, status: QcStatus) -> None:
        """The compatibility half, over EVERY status — ⛔ an earlier version
        asserted only `QC_PASSED`, which cannot show that scalar semantics are
        preserved in general, only for the one value it happened to pick."""
        fetched = _fetch(_store(), status)

        assert len(fetched) == 1
        assert fetched[0].qc_status is status

    def test_no_status_returns_everything(self) -> None:
        assert len(_fetch(_store())) == 4

    def test_an_empty_collection_accepts_nothing(self) -> None:
        """It says "no statuses are acceptable", and that is what it does —
        stated because the alternative reading ("no filter") would silently
        widen a caller's population."""
        assert _fetch(_store(), set()) == []
