from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from sapphire_flow.db.metadata import (
    observations,
    provisional_discharges,
)
from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rating_reference_store import PgRatingReferenceStore
from sapphire_flow.types.ids import (
    MeasurementFeedEvidenceId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationId,
    TenantId,
)
from sapphire_flow.types.provisional_discharge import ProvisionalDischarge
from sapphire_flow.types.rating_reference import (
    CurveSnapshot,
    MeasurementSnapshot,
    canonical_content,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgProvisionalDischargeStore:
    """Append-only protected store on an injected transaction; never commits."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_provisional_discharge(
        self, discharge: ProvisionalDischarge, *, captured_at: UtcDatetime
    ) -> str:
        self._conn.execute(sa.text("LOCK TABLE public.rating_curves IN SHARE MODE"))
        row = (
            self._conn.execute(
                sa.select(observations)
                .where(observations.c.id == discharge.observation_id)
                .where(observations.c.station_id == discharge.station_id)
                .with_for_update(read=True)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("persisted measured level is absent or foreign")
        measured = PgObservationStore(self._conn).fetch_observations(
            station_id=discharge.station_id,
            parameter=row["parameter"],
            start=row["timestamp"],
            end=row["timestamp"] + timedelta(microseconds=1),
        )
        observation = next(
            (o for o in measured if o.id == discharge.observation_id), None
        )
        if observation is None:
            raise ValueError("persisted measured level is absent or foreign")
        curves = PgRatingCurveStore(self._conn).fetch_all_curves_for_station(
            discharge.station_id
        )
        references = PgRatingReferenceStore(self._conn)
        checked = convert_provisional_discharge(
            observation=observation,
            curves=curves,
            feed_evidence=references.fetch_feed_evidence(discharge.feed_evidence_id),
            reference_proof=references.fetch_reference_proof(
                discharge.reference_proof_id
            ),
            at=captured_at,
        )
        if checked != discharge:
            raise ValueError("provisional snapshot or fingerprint disagreement")
        values = {
            name: getattr(discharge, name)
            for name in (
                "fingerprint",
                "tenant_id",
                "station_id",
                "observation_id",
                "rating_curve_id",
                "feed_evidence_id",
                "reference_proof_id",
                "discharge",
                "content",
            )
        }
        self._conn.execute(
            insert(provisional_discharges)
            .values(**values, captured_at=captured_at)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
        )
        persisted = self._conn.execute(
            sa.select(provisional_discharges.c.content).where(
                provisional_discharges.c.fingerprint == discharge.fingerprint
            )
        ).scalar_one()
        if persisted != discharge.content:
            raise ValueError("provisional fingerprint disagreement")
        return discharge.fingerprint

    def fetch_provisional_discharge(
        self, fingerprint: str
    ) -> ProvisionalDischarge | None:
        row = (
            self._conn.execute(
                sa.select(provisional_discharges).where(
                    provisional_discharges.c.fingerprint == fingerprint
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        document = json.loads(row["content"])
        return ProvisionalDischarge(
            tenant_id=TenantId(row["tenant_id"]),
            station_id=StationId(row["station_id"]),
            observation_id=ObservationId(row["observation_id"]),
            rating_curve_id=RatingCurveId(row["rating_curve_id"]),
            feed_evidence_id=MeasurementFeedEvidenceId(row["feed_evidence_id"]),
            reference_proof_id=RatingReferenceProofId(row["reference_proof_id"]),
            measurement=MeasurementSnapshot(
                content=canonical_content(document["measurement"])
            ),
            curve=CurveSnapshot(content=canonical_content(document["curve"])),
            discharge=row["discharge"],
            content=row["content"],
            fingerprint=row["fingerprint"],
        )
