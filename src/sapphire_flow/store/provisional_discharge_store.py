from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from sapphire_flow.db.metadata import (
    dhm_rating_declarations,
    expired_rating_test_permissions,
    observations,
    provisional_discharges,
    rating_curves,
    restricted_delivery_generations,
    tenants,
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
    content_digest,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.rating_curve import RatingCurve


def _rating_curve_rows_equivalent(
    left: sa.engine.row.RowMapping, right: sa.engine.row.RowMapping
) -> bool:
    return all(
        left[name] == right[name]
        for name in (
            "valid_from",
            "valid_to",
            "points",
            "interpolation",
            "rating_type_label",
        )
    )


class PgProvisionalDischargeStore:
    """Append-only protected store on an injected transaction; never commits."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_provisional_discharge(
        self, discharge: ProvisionalDischarge, *, captured_at: UtcDatetime
    ) -> str:
        isolation = self._conn.execute(
            sa.text("SELECT current_setting('transaction_isolation')")
        ).scalar_one()
        if isolation != "read committed":
            raise ValueError("protected provisional writes require READ COMMITTED")
        database_now = self._conn.execute(
            sa.select(sa.func.clock_timestamp())
        ).scalar_one()
        if captured_at > database_now:
            raise ValueError("provisional capture time is in the future")
        nepal21_permission = self._lock_nepal21_expired_rating_permission(
            discharge.tenant_id
        )
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
        # Reload evidence unfiltered; strict reconversion rejects nonpassing QC.
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
        curves = self._curves_for_conversion(
            discharge=discharge, permission=nepal21_permission, captured_at=captured_at
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
        if nepal21_permission is None:
            if checked != discharge:
                raise ValueError("provisional snapshot or fingerprint disagreement")
        else:
            self._assert_nepal21_permission_fingerprint(
                discharge, checked, nepal21_permission
            )
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

    def _curves_for_conversion(
        self,
        *,
        discharge: ProvisionalDischarge,
        permission: sa.engine.row.RowMapping | None,
        captured_at: UtcDatetime,
    ) -> list[RatingCurve]:
        curves = PgRatingCurveStore(self._conn).fetch_all_curves_for_station(
            discharge.station_id
        )
        if permission is None:
            return curves
        self._assert_nepal21_catalog_native_selection(
            discharge=discharge, permission=permission, captured_at=captured_at
        )
        selected = [curve for curve in curves if curve.id == discharge.rating_curve_id]
        if len(selected) != 1:
            raise ValueError("Nepal21 expired conversion curve is not catalog selected")
        return selected

    def _assert_nepal21_catalog_native_selection(
        self,
        *,
        discharge: ProvisionalDischarge,
        permission: sa.engine.row.RowMapping,
        captured_at: UtcDatetime,
    ) -> None:
        declarations = (
            self._conn.execute(
                sa.select(
                    dhm_rating_declarations.c.from_local_date,
                    dhm_rating_declarations.c.function_equivalence_sha256,
                    dhm_rating_declarations.c.usable_rating_curve_id,
                ).where(
                    dhm_rating_declarations.c.generation_id
                    == permission["generation_id"],
                    dhm_rating_declarations.c.station_id == discharge.station_id,
                )
            )
            .mappings()
            .all()
        )
        if not declarations:
            raise ValueError("Nepal21 expired conversion declaration is absent")
        latest_start = max(row["from_local_date"] for row in declarations)
        latest = [row for row in declarations if row["from_local_date"] == latest_start]
        equivalence_hashes = {row["function_equivalence_sha256"] for row in latest}
        if len(equivalence_hashes) > 1:
            raise ValueError(
                "Nepal21 expired conversion has equal-start non-equivalent declarations"
            )
        selected_latest = [
            row
            for row in latest
            if row["usable_rating_curve_id"] == discharge.rating_curve_id
        ]
        if len(selected_latest) != 1:
            raise ValueError(
                "Nepal21 expired conversion latest declaration is not usable"
            )
        selected_row = (
            self._conn.execute(
                sa.select(rating_curves).where(
                    rating_curves.c.id == discharge.rating_curve_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if selected_row is None:
            raise ValueError("Nepal21 expired conversion curve is not catalog selected")
        declared_curve_ids = {
            row["usable_rating_curve_id"]
            for row in declarations
            if row["usable_rating_curve_id"] is not None
        }
        candidates = (
            self._conn.execute(
                sa.select(rating_curves).where(
                    rating_curves.c.station_id == discharge.station_id,
                    sa.cast(
                        sa.func.timezone("Asia/Kathmandu", rating_curves.c.valid_from),
                        sa.Date,
                    )
                    >= latest_start,
                    rating_curves.c.id.not_in(declared_curve_ids),
                )
            )
            .mappings()
            .all()
        )
        unmatched = [
            row
            for row in candidates
            if not _rating_curve_rows_equivalent(row, selected_row)
        ]
        if unmatched:
            raise ValueError(
                "Nepal21 expired conversion has unmatched native rating candidate"
            )

    def _lock_nepal21_expired_rating_permission(
        self, tenant_id: TenantId
    ) -> sa.engine.row.RowMapping | None:
        tenant_code = self._conn.scalar(
            sa.select(tenants.c.code).where(tenants.c.id == tenant_id)
        )
        if tenant_code != "chwrr":
            return None
        generation_id = self._conn.scalar(
            sa.select(restricted_delivery_generations.c.id).where(
                restricted_delivery_generations.c.tenant_id == tenant_id,
                restricted_delivery_generations.c.network == "dhm",
                restricted_delivery_generations.c.cohort == "nepal21",
                restricted_delivery_generations.c.state == "active_qc_complete",
            )
        )
        if generation_id is None:
            raise ValueError(
                "Nepal21 expired conversion requires QC-complete generation"
            )
        self._conn.execute(
            sa.text(
                "SELECT public.sf_lock_dhm21_conversion_append("
                ":tenant_id, :generation_id)"
            ),
            {"tenant_id": tenant_id, "generation_id": generation_id},
        )
        permission = (
            self._conn.execute(
                sa.select(
                    expired_rating_test_permissions,
                    restricted_delivery_generations.c.qc_policy_sha256,
                    restricted_delivery_generations.c.qc_completion_sha256,
                )
                .join(
                    restricted_delivery_generations,
                    restricted_delivery_generations.c.id
                    == expired_rating_test_permissions.c.generation_id,
                )
                .where(
                    expired_rating_test_permissions.c.tenant_id == tenant_id,
                    expired_rating_test_permissions.c.generation_id == generation_id,
                    expired_rating_test_permissions.c.state == "enabled",
                )
            )
            .mappings()
            .one_or_none()
        )
        if permission is None:
            raise ValueError("Nepal21 expired rating test permission is disabled")
        return permission

    def _assert_nepal21_permission_fingerprint(
        self,
        discharge: ProvisionalDischarge,
        checked: ProvisionalDischarge,
        permission: sa.engine.row.RowMapping,
    ) -> None:
        if (
            discharge.tenant_id != checked.tenant_id
            or discharge.station_id != checked.station_id
            or discharge.observation_id != checked.observation_id
            or discharge.rating_curve_id != checked.rating_curve_id
            or discharge.feed_evidence_id != checked.feed_evidence_id
            or discharge.reference_proof_id != checked.reference_proof_id
            or discharge.measurement != checked.measurement
            or discharge.curve != checked.curve
            or discharge.discharge != checked.discharge
        ):
            raise ValueError("provisional snapshot or fingerprint disagreement")
        document = json.loads(discharge.content)
        permission_doc = document.get("nepal21_expired_permission")
        if not isinstance(permission_doc, dict):
            raise ValueError("Nepal21 provisional content lacks permission fingerprint")
        expected = {
            "permission_id": str(permission["id"]),
            "permission_version": permission["permission_version"],
            "generation_id": str(permission["generation_id"]),
            "owner_authorization_sha256": permission["owner_authorization_sha256"],
            "precedence_sha256": permission["precedence_sha256"],
            "selection_manifest_sha256": permission["selection_manifest_sha256"],
            "qc_policy_sha256": permission["qc_policy_sha256"],
            "qc_completion_sha256": permission["qc_completion_sha256"],
            "selected_rating_curve_id": str(discharge.rating_curve_id),
        }
        if permission_doc != expected:
            raise ValueError("Nepal21 provisional permission fingerprint disagreement")
        if content_digest(discharge.content) != discharge.fingerprint:
            raise ValueError("provisional fingerprint disagreement")
        selected = self._conn.scalar(
            sa.select(
                sa.exists().where(
                    dhm_rating_declarations.c.generation_id
                    == permission["generation_id"],
                    dhm_rating_declarations.c.station_id == discharge.station_id,
                    dhm_rating_declarations.c.usable_rating_curve_id
                    == discharge.rating_curve_id,
                )
            )
        )
        if not selected:
            raise ValueError("Nepal21 expired conversion curve is not catalog selected")

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
