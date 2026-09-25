# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import hashlib
import json
import zlib
from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import polars as pl
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    forecast_evidence,
    forecast_evidence_blobs,
    forecast_values,
    forecasts,
)
from sapphire_flow.exceptions import ConflictError, ForecastRetryConflictError
from sapphire_flow.services.forecast_evidence import (
    restore_snapshot,
    serialize_thresholds,
)
from sapphire_flow.services.forecast_retry import (
    SUPERSEDING_ROWS,
    ForecastRetryRow,
    classify_forecast_retry,
    describe_difference,
)
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.domain import InputQualityFlag, QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    QcStatus,
    WarmUpSource,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    ForecastEvidence,
    PersistedForecastEvidence,
    incomplete_evidence,
)
from sapphire_flow.types.forecast_summary import ForecastSummaryRow
from sapphire_flow.types.ids import (
    ArtifactId,
    ForecastId,
    ModelId,
    RatingCurveId,
    StationId,
)

log = structlog.get_logger(__name__)


def _is_current() -> sa.ColumnElement[bool]:
    """Plan 328 T3 — exclude a forecast that has been replaced.

    ⛔ NOT a status whitelist: a whitelist would silently drop any status added
    later (Plan 341's `withdrawn`, for one) from every current read. The
    predicate names only the not-current state, exactly as
    `uq_forecasts_station_model_issued_param` does.
    """
    return forecasts.c.status != ForecastStatus.SUPERSEDED.value


def _combined_contributor_gap(
    txn: sa.Connection, evidence: ForecastEvidence
) -> str | None:
    if evidence.snapshot is None:
        return "contributor_snapshot_unavailable"
    try:
        references = restore_snapshot(evidence.snapshot).get("contributors")
    except (ValueError, UnicodeDecodeError, zlib.error):
        return "contributor_snapshot_unreadable"
    if not isinstance(references, list) or not references:
        return "contributor_references_unavailable"
    for reference in cast("list[object]", references):
        if not isinstance(reference, dict):
            return "contributor_references_invalid"
        fields = cast("dict[str, object]", reference)
        raw_forecast_id = fields.get("forecast_id")
        if not isinstance(raw_forecast_id, str):
            return "contributor_references_invalid"
        try:
            forecast_id = UUID(raw_forecast_id)
        except ValueError:
            return "contributor_references_invalid"
        row = txn.execute(
            sa.select(
                forecast_evidence.c.status, forecast_evidence.c.snapshot_sha256
            ).where(forecast_evidence.c.forecast_id == forecast_id)
        ).one_or_none()
        if row is None:
            return "contributor_evidence_not_persisted"
        if row.snapshot_sha256 != fields.get("evidence_sha256"):
            return "contributor_evidence_mismatch"
        if row.status != EvidenceStatus.COMPLETE.value:
            return "contributor_evidence_incomplete"
    return None


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager as ContextManager

    from sqlalchemy import RowMapping

    from sapphire_flow.types.datetime import UtcDatetime


class PgForecastStore:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        transaction_factory: Callable[[], ContextManager[sa.Connection]] | None = None,
    ) -> None:
        self._conn = conn
        self._begin = (
            transaction_factory
            if transaction_factory is not None
            else conn.engine.begin
        )

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        with self._begin() as txn:
            # Plan 327 — a cycle that died partway is re-runnable. An
            # IDENTICAL recomputation (decision-table row 4) returns the
            # stored identity and writes nothing. Plan 328 T2 — a row 1 or
            # row 2 re-run MARKS the stored forecast superseded and falls
            # through to the INSERT below, in this same transaction; row 3
            # REFUSES.
            # The lookup mirrors `uq_forecasts_station_model_issued_param`'s
            # partial predicate, so a row marked `superseded` drops out of
            # both at once — which is what lets the replacement occupy the
            # natural key the original held.
            #
            # A CONCURRENT duplicate can still slip between this SELECT and
            # the INSERT below; it then raises an unwrapped SQLAlchemy
            # `IntegrityError`, exactly as it does today (Plan 038 D5).
            existing_id = txn.execute(
                sa.select(forecasts.c.id)
                .where(forecasts.c.station_id == forecast.station_id)
                .where(forecasts.c.model_id == forecast.model_id)
                .where(forecasts.c.issued_at == forecast.issued_at)
                .where(forecasts.c.parameter == forecast.ensemble.parameter)
                .where(forecasts.c.status != "superseded")
            ).scalar_one_or_none()
            if existing_id is not None:
                resumed = _resolve_retry(txn, ForecastId(existing_id), forecast)
                if resumed is not None:
                    return resumed
            txn.execute(sa.insert(forecasts).values(**_forecast_row(forecast)))
            rows = _build_value_rows(forecast)
            if rows:
                txn.execute(sa.insert(forecast_values), rows)
            evidence = forecast.evidence or incomplete_evidence(
                "prediction_capture_unavailable"
            )
            thresholds_json = (
                serialize_thresholds(evidence.thresholds)
                if evidence.thresholds is not None
                else None
            )
            status = evidence.status
            reason = evidence.reason
            if thresholds_json is None:
                status = EvidenceStatus.INCOMPLETE
                reason = (
                    f"{reason};thresholds_unavailable"
                    if reason
                    else "thresholds_unavailable"
                )
            if forecast.combination_strategy is not None:
                contributor_gap = _combined_contributor_gap(txn, evidence)
                if contributor_gap is not None:
                    status = EvidenceStatus.INCOMPLETE
                    reason = (
                        f"{reason};{contributor_gap}" if reason else contributor_gap
                    )
            manifest = json.loads(evidence.manifest_json)
            manifest.update(
                forecast_id=str(forecast.id),
                station_id=str(forecast.station_id),
                parameter=forecast.ensemble.parameter,
                issued_at=forecast.issued_at.isoformat(),
                model_artifact_id=(
                    str(forecast.model_artifact_id)
                    if forecast.model_artifact_id is not None
                    else None
                ),
                nwp_cycle_reference_time=(
                    forecast.nwp_cycle_reference_time.isoformat()
                    if forecast.nwp_cycle_reference_time is not None
                    else None
                ),
                nwp_cycle_source=forecast.nwp_cycle_source.value,
                rating_curve_id=(
                    str(forecast.rating_curve_id)
                    if forecast.rating_curve_id is not None
                    else None
                ),
                qc_status=forecast.qc_status.value,
                qc_flags=[
                    {
                        "rule_id": flag.rule_id,
                        "rule_version": flag.rule_version,
                        "status": flag.status.value,
                        "detail": flag.detail,
                    }
                    for flag in forecast.qc_flags
                ],
                input_quality=(
                    forecast.input_quality.value
                    if forecast.input_quality is not None
                    else None
                ),
                input_quality_flags=[
                    {
                        "category": flag.category.value,
                        "level": flag.level.value,
                        "detail": flag.detail,
                    }
                    for flag in forecast.input_quality_flags
                ],
                thresholds_sha256=(
                    hashlib.sha256(thresholds_json.encode("utf-8")).hexdigest()
                    if thresholds_json is not None
                    else None
                ),
            )
            for digest, payload in (
                (evidence.snapshot_sha256, evidence.snapshot),
                (evidence.artifact_sha256, evidence.artifact),
            ):
                if digest is None or payload is None:
                    continue
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError("forecast evidence blob hash mismatch")
                txn.execute(
                    pg_insert(forecast_evidence_blobs)
                    .values(sha256=digest, payload=payload, byte_length=len(payload))
                    .on_conflict_do_nothing(index_elements=["sha256"])
                )
                retained = txn.execute(
                    sa.select(
                        forecast_evidence_blobs.c.payload,
                        forecast_evidence_blobs.c.byte_length,
                    ).where(forecast_evidence_blobs.c.sha256 == digest)
                ).one()
                if (
                    retained.payload != payload
                    or retained.byte_length != len(retained.payload)
                    or hashlib.sha256(retained.payload).hexdigest() != digest
                ):
                    raise ValueError("retained forecast evidence blob mismatch")
            txn.execute(
                sa.insert(forecast_evidence).values(
                    forecast_id=forecast.id,
                    status=status.value,
                    manifest_json=json.dumps(
                        manifest, sort_keys=True, separators=(",", ":")
                    ),
                    snapshot_sha256=evidence.snapshot_sha256,
                    artifact_sha256=evidence.artifact_sha256,
                    thresholds_json=thresholds_json,
                    reason=reason,
                )
            )
        return forecast.id

    def fetch_evidence(
        self, forecast_id: ForecastId
    ) -> PersistedForecastEvidence | None:
        row = (
            self._conn.execute(
                sa.select(forecast_evidence).where(
                    forecast_evidence.c.forecast_id == forecast_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            exists = self._conn.execute(
                sa.select(forecasts.c.id).where(forecasts.c.id == forecast_id)
            ).scalar_one_or_none()
            if exists is None:
                return None
            return PersistedForecastEvidence(
                status=EvidenceStatus.INCOMPLETE,
                manifest_json="{}",
                snapshot=None,
                snapshot_sha256=None,
                artifact=None,
                artifact_sha256=None,
                thresholds_json=None,
                reason="pre_capture_forecast",
            )

        def blob(digest: str | None) -> bytes | None:
            if digest is None:
                return None
            payload = self._conn.execute(
                sa.select(forecast_evidence_blobs.c.payload).where(
                    forecast_evidence_blobs.c.sha256 == digest
                )
            ).scalar_one()
            data = bytes(payload)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("forecast evidence blob hash mismatch")
            return data

        return PersistedForecastEvidence(
            status=EvidenceStatus(row["status"]),
            manifest_json=row["manifest_json"],
            snapshot=blob(row["snapshot_sha256"]),
            snapshot_sha256=row["snapshot_sha256"],
            artifact=blob(row["artifact_sha256"]),
            artifact_sha256=row["artifact_sha256"],
            thresholds_json=row["thresholds_json"],
            reason=row["reason"],
        )

    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None:
        # Plan 328 T3 — BY-ID access is PRESERVED for a superseded forecast.
        # Its evidence is permanent (migration 0057 forbids removing it), and
        # evidence nobody can read back defeats its own purpose. The returned
        # `status` is what distinguishes it from a current forecast.
        return _fetch_forecast(self._conn, forecast_id)

    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,
        parameter: str | None = None,
    ) -> OperationalForecast | None:
        # Plan 328 T3 — CURRENT only. This reader had NO status filter at
        # all, and a superseded forecast shares its replacement's `issued_at`,
        # so `ORDER BY issued_at DESC LIMIT 1` would return an arbitrary one
        # of the two: intermittently the forecast we replaced.
        sub = (
            sa.select(forecasts.c.id)
            .where(forecasts.c.station_id == station_id)
            .where(_is_current())
        )
        if model_id is not None:
            sub = sub.where(forecasts.c.model_id == model_id)
        if parameter is not None:
            sub = sub.where(forecasts.c.parameter == parameter)
        sub = sub.order_by(forecasts.c.issued_at.desc()).limit(1).scalar_subquery()
        fid_row = self._conn.execute(sa.select(sub)).scalar_one_or_none()
        if fid_row is None:
            return None
        return self.fetch_forecast(ForecastId(fid_row))

    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        # Plan 328 T3 — CURRENT only; see `fetch_latest_forecast`. The
        # Forecast Lab takes the first candidate this returns.
        stmt = (
            sa.select(forecasts.c.id)
            .where(forecasts.c.issued_at == issued_at)
            .where(_is_current())
        )
        if station_id is not None:
            stmt = stmt.where(forecasts.c.station_id == station_id)
        if parameter is not None:
            stmt = stmt.where(forecasts.c.parameter == parameter)
        fids = [ForecastId(r[0]) for r in self._conn.execute(stmt).fetchall()]
        return self._fetch_by_ids(fids)

    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int:
        result = self._conn.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == forecast_id)
            .where(forecasts.c.version == expected_version)
            .values(
                status=new_status.value,
                version=expected_version + 1,
                updated_at=sa.func.now(),
            )
        )
        if result.rowcount == 0:
            raise ConflictError(f"Version mismatch for forecast {forecast_id}")
        return expected_version + 1

    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
        parameter: str | None = None,
    ) -> list[OperationalForecast]:
        stmt = (
            sa.select(forecasts.c.id)
            .where(forecasts.c.station_id == station_id)
            .where(forecasts.c.issued_at >= start)
            .where(forecasts.c.issued_at < end)
        )
        if model_id is not None:
            stmt = stmt.where(forecasts.c.model_id == model_id)
        if status is not None:
            stmt = stmt.where(forecasts.c.status == status.value)
        else:
            # Plan 328 T3 — an UNFILTERED range read is a read of what is
            # current. A caller that wants the replaced rows asks for them:
            # `status=ForecastStatus.SUPERSEDED` still returns them.
            stmt = stmt.where(_is_current())
        if parameter is not None:
            stmt = stmt.where(forecasts.c.parameter == parameter)
        fids = [ForecastId(r[0]) for r in self._conn.execute(stmt).fetchall()]
        return self._fetch_by_ids(fids)

    def fetch_forecast_summaries(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        model_id: ModelId | None = None,
        parameter: str | None = None,
        degraded_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ForecastSummaryRow], int]:
        # Plan 328 T3 — DELIBERATELY UNFILTERED. This is the record listing:
        # it answers "what forecasts exist for this station over this window",
        # not "what is current", and `ForecastSummaryRow.status` carries each
        # row's state so a superseded forecast reads as superseded. Filtering
        # here would make the totals disagree with the table.
        # ⛔ NOT because it is the only way to find a superseded id — it is
        # not: the admin `/forecasts/` list and the generic `/tables/` browser
        # expose it too.
        filters = [
            forecasts.c.station_id == station_id,
            forecasts.c.issued_at >= start,
            forecasts.c.issued_at < end,
        ]
        if model_id is not None:
            filters.append(forecasts.c.model_id == model_id)
        if parameter is not None:
            filters.append(forecasts.c.parameter == parameter)
        if degraded_only:
            # Plan 253 T1c / OD-2: an unknown (NULL) input_quality is never
            # reported as degraded — this IN excludes both NULL and 'full'.
            filters.append(
                forecasts.c.input_quality.in_(
                    [InputQualityLevel.PARTIAL.value, InputQualityLevel.DEGRADED.value]
                )
            )

        where = sa.and_(*filters)

        total: int = self._conn.execute(
            sa.select(sa.func.count()).select_from(forecasts).where(where)
        ).scalar_one()

        rows = (
            self._conn.execute(
                sa.select(forecasts)
                .where(where)
                .order_by(forecasts.c.issued_at.desc(), forecasts.c.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )

        return [_row_to_summary(row) for row in rows], total

    def fetch_latest_uncombined_issued_at(
        self, cutoff: UtcDatetime
    ) -> UtcDatetime | None:
        # Plan 328 T3 — CURRENT only. A supersession always writes its
        # replacement in the same transaction, so this MAX does not move in
        # practice; filtering keeps the marker a statement about what is
        # served rather than about what was ever written.
        stmt = (
            sa.select(sa.func.max(forecasts.c.issued_at))
            .where(forecasts.c.combination_strategy.is_(None))
            .where(forecasts.c.issued_at <= cutoff)
            .where(_is_current())
        )
        result = self._conn.execute(stmt).scalar_one_or_none()
        return utc_or_none(result)

    def _fetch_by_ids(self, fids: list[ForecastId]) -> list[OperationalForecast]:
        if not fids:
            return []
        rows = (
            self._conn.execute(
                sa.select(forecasts, forecast_values)
                .join(
                    forecast_values,
                    forecast_values.c.forecast_id == forecasts.c.id,
                )
                .where(forecasts.c.id.in_(fids))
                .order_by(forecasts.c.issued_at, forecast_values.c.valid_time)
            )
            .mappings()
            .all()
        )
        grouped: dict[ForecastId, list] = defaultdict(list)
        for row in rows:
            grouped[ForecastId(row["id"])].append(row)
        return [_rows_to_domain(group) for group in grouped.values()]


def _fetch_forecast(
    conn: sa.Connection, forecast_id: ForecastId
) -> OperationalForecast | None:
    rows = (
        conn.execute(
            sa.select(forecasts, forecast_values)
            .join(
                forecast_values,
                forecast_values.c.forecast_id == forecasts.c.id,
            )
            .where(forecasts.c.id == forecast_id)
            .order_by(forecast_values.c.valid_time)
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    return _rows_to_domain(rows)


def _resolve_retry(
    txn: sa.Connection,
    existing_id: ForecastId,
    forecast: OperationalForecast,
) -> ForecastId | None:
    """Plan 327 § D1 — classify a re-run against the forecast already stored
    under its natural key, and act on the row.

    Row 4 resumes (returns the stored id). Plan 328 T2: a row in
    ``SUPERSEDING_ROWS`` marks the stored forecast superseded and returns
    ``None``, so the caller's INSERT — in the SAME transaction — writes the
    replacement. ⛔ **Everything else raises**, which is the safe direction: a
    row added to Plan 327's table later refuses until someone deliberately
    lists it in ``SUPERSEDING_ROWS``.

    ``None`` therefore means "proceed to the INSERT", which is also what an
    unclassifiable retry gets.
    """
    stored = _fetch_forecast(txn, existing_id)
    if stored is None:
        # Unreachable through this store: header and values share ONE
        # transaction, so a header without its values cannot be committed
        # (proven by `test_values_insert_failure_rolls_back_header`). If it
        # ever happens there is no stored content to compare against, so
        # rather than invent a decision row, fall through to the INSERT — the
        # unique constraint then refuses it with a raw `IntegrityError`,
        # exactly as it does today.
        log.error(
            "forecast_store.retry_unclassifiable",
            forecast_id=str(existing_id),
            station_id=str(forecast.station_id),
            reason="stored forecast has no values",
        )
        return None
    row = classify_forecast_retry(stored=stored, recomputed=forecast)
    if row is ForecastRetryRow.IDENTICAL:
        log.info(
            "forecast_store.retry_identical",
            forecast_id=str(existing_id),
            station_id=str(forecast.station_id),
            model_id=str(forecast.model_id),
            issued_at=forecast.issued_at.isoformat(),
            parameter=forecast.ensemble.parameter,
        )
        return existing_id
    detail = describe_difference(row, stored=stored, recomputed=forecast)
    if row in SUPERSEDING_ROWS:
        _mark_superseded(txn, existing_id)
        log.warning(
            "forecast_store.forecast_superseded",
            superseded_forecast_id=str(existing_id),
            replacement_forecast_id=str(forecast.id),
            station_id=str(forecast.station_id),
            model_id=str(forecast.model_id),
            issued_at=forecast.issued_at.isoformat(),
            parameter=forecast.ensemble.parameter,
            decision_row=row.value,
            detail=detail,
        )
        return None
    log.error(
        "forecast_store.retry_conflict",
        forecast_id=str(existing_id),
        station_id=str(forecast.station_id),
        model_id=str(forecast.model_id),
        issued_at=forecast.issued_at.isoformat(),
        parameter=forecast.ensemble.parameter,
        decision_row=row.value,
        detail=detail,
    )
    raise ForecastRetryConflictError(
        f"Forecast {existing_id} already exists for "
        f"({forecast.station_id}, {forecast.model_id}, "
        f"{forecast.issued_at.isoformat()}, {forecast.ensemble.parameter}) "
        f"and the re-run is not identical — {detail}",
        row=row,
        forecast_id=existing_id,
        station_id=forecast.station_id,
        model_id=forecast.model_id,
        issued_at=forecast.issued_at,
        parameter=forecast.ensemble.parameter,
    )


def _mark_superseded(txn: sa.Connection, existing_id: ForecastId) -> None:
    """Plan 328 T2 — mark the stored forecast superseded, in the caller's
    transaction, immediately before the replacement is inserted.

    🔴 The forecast's VALUES, its evidence row and its evidence blobs are
    untouched. That is not a courtesy: migration 0057 rejects ``UPDATE``,
    ``DELETE`` and ``TRUNCATE`` on ``forecast_evidence`` and
    ``forecast_evidence_blobs``, so a superseded forecast keeps its evidence
    whether or not anyone wants it to.

    ``version`` advances like any other status transition, so a reader holding
    the pre-supersession version loses its optimistic lock rather than writing
    over a forecast that is no longer current.
    """
    result = txn.execute(
        sa.update(forecasts)
        .where(forecasts.c.id == existing_id)
        .where(forecasts.c.status != ForecastStatus.SUPERSEDED.value)
        .values(
            status=ForecastStatus.SUPERSEDED.value,
            version=forecasts.c.version + 1,
            updated_at=sa.func.now(),
        )
    )
    if result.rowcount != 1:
        raise ConflictError(
            f"Forecast {existing_id} could not be marked superseded "
            f"({result.rowcount} rows matched)"
        )


def _forecast_row(forecast: OperationalForecast) -> dict[str, object]:
    """The `forecasts` header row, as a plain mapping.

    Extracted so a test can interrupt `store_forecast` BETWEEN the Plan 328
    supersession mark and the replacement insert — the one window where a
    non-atomic implementation would lose the original.
    """
    return dict(
        id=forecast.id,
        station_id=forecast.station_id,
        model_id=forecast.model_id,
        model_artifact_id=forecast.model_artifact_id,
        issued_at=forecast.issued_at,
        time_step_seconds=int(forecast.ensemble.time_step.total_seconds()),
        nwp_cycle_reference_time=forecast.nwp_cycle_reference_time,
        nwp_cycle_source=forecast.nwp_cycle_source.value,
        representation=forecast.representation.value,
        status=forecast.status.value,
        version=forecast.version,
        warm_up_source=(
            forecast.warm_up_source.value
            if forecast.warm_up_source is not None
            else None
        ),
        warm_up_state_age_hours=forecast.warm_up_state_age_hours,
        observation_staleness_hours=forecast.observation_staleness_hours,
        parameter=forecast.ensemble.parameter,
        units=forecast.ensemble.units,
        created_at=forecast.created_at,
        updated_at=forecast.updated_at,
        qc_status=forecast.qc_status.value,
        qc_flags=[
            {
                "rule_id": f.rule_id,
                "rule_version": f.rule_version,
                "status": f.status.value,
                "detail": f.detail,
            }
            for f in forecast.qc_flags
        ],
        input_quality=(
            forecast.input_quality.value if forecast.input_quality is not None else None
        ),
        input_quality_flags=(
            [
                {
                    "category": f.category.value,
                    "level": f.level.value,
                    "detail": f.detail,
                }
                for f in forecast.input_quality_flags
            ]
            if forecast.input_quality is not None
            else None
        ),
        combination_strategy=forecast.combination_strategy,
        source_model_ids=(
            [str(mid) for mid in forecast.source_model_ids]
            if forecast.source_model_ids is not None
            else None
        ),
        rating_curve_id=forecast.rating_curve_id,
    )


def _build_value_rows(forecast: OperationalForecast) -> list[dict]:  # type: ignore[type-arg]
    df = forecast.ensemble.values
    issued_at = forecast.issued_at
    is_members = forecast.representation == EnsembleRepresentation.MEMBERS
    rows = []
    for row in df.iter_rows(named=True):
        vt = row["valid_time"]
        lead = int((vt.timestamp() - issued_at.timestamp()) // 3600)
        rows.append(
            {
                "id": uuid4(),
                "forecast_id": forecast.id,
                "issued_at": issued_at,
                "valid_time": vt,
                "lead_time_hours": lead,
                "member_id": row["member_id"] if is_members else None,
                "quantile": None if is_members else row["quantile"],
                "value": row["value"],
            }
        )
    return rows


def _parse_input_quality_flags(raw: object) -> tuple[InputQualityFlag, ...]:
    items: list[dict[str, object]] = raw or []  # type: ignore[assignment]
    return tuple(
        InputQualityFlag(
            category=InputQualityCategory(f["category"]),
            level=InputQualityLevel(f["level"]),
            detail=f["detail"],  # type: ignore[arg-type]
        )
        for f in items
    )


def _rows_to_domain(rows: Sequence[RowMapping]) -> OperationalForecast:
    header = rows[0]
    representation = EnsembleRepresentation(header["representation"])
    is_members = representation == EnsembleRepresentation.MEMBERS

    if is_members:
        value_rows = [
            {
                "valid_time": row["valid_time"],
                "member_id": row["member_id"],
                "value": row["value"],
            }
            for row in rows
        ]
        df = pl.DataFrame(value_rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
    else:
        value_rows = [
            {
                "valid_time": row["valid_time"],
                "quantile": row["quantile"],
                "value": row["value"],
            }
            for row in rows
        ]
        df = pl.DataFrame(value_rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        )

    # Plan 241 T4 — the stored value is AUTHORITATIVE when present. Every row
    # written since revision 0053 carries the cadence its ensemble declared, so
    # the gap-inference below is never reached for new data.
    #
    # 🔴 The LEGACY path is retained deliberately; do NOT "simplify" it away.
    # 0053 is nullable-first and performs no backfill, so pre-migration rows
    # have `time_step_seconds IS NULL` and must keep reading exactly as they did
    # before this change — including the fabricated one-hour fallback for a
    # single-timestamp row. An earlier revision of Plan 241 proposed deleting
    # that fallback, on the premise that a one-step forecast was unreachable
    # before the horizon work. THAT PREMISE IS FALSE: nothing at the storage
    # boundary ever enforced a two-step minimum (`ForecastEnsemble.from_members`
    # requires only a non-empty frame and >= 1 member, and there is no
    # write-side guard here), and it was measured against main — a one-step
    # forecast stores and reads back fine, at the fabricated 1:00:00. Deleting
    # the branch would turn a wrong NUMBER into an IndexError on a row that
    # reads today.
    stored_time_step_seconds = header["time_step_seconds"]
    if stored_time_step_seconds is not None:
        time_step = timedelta(seconds=stored_time_step_seconds)
    else:
        valid_times = df["valid_time"].sort().unique().sort()
        if len(valid_times) >= 2:
            time_step = timedelta(
                seconds=int(valid_times[1].timestamp() - valid_times[0].timestamp())
            )
        else:
            time_step = timedelta(hours=1)
            log.warning(
                "forecast.legacy_time_step_fabricated",
                forecast_id=str(header["id"]),
                station_id=str(header["station_id"]),
                reason="pre-0053 row with a single valid_time and no stored cadence",
                fabricated_time_step_seconds=int(time_step.total_seconds()),
            )

    station_id = StationId(header["station_id"])
    issued_at = utc_from_row(header["issued_at"])
    parameter = header["parameter"]
    units = header["units"]

    ensemble = (
        ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
        if is_members
        else ForecastEnsemble.from_quantiles(
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
    )

    warm_up_raw = header["warm_up_source"]
    return OperationalForecast(
        id=ForecastId(header["id"]),
        station_id=station_id,
        model_id=ModelId(header["model_id"]),
        model_artifact_id=(
            ArtifactId(header["model_artifact_id"])
            if header["model_artifact_id"] is not None
            else None
        ),
        issued_at=issued_at,
        nwp_cycle_reference_time=utc_or_none(header["nwp_cycle_reference_time"]),
        nwp_cycle_source=NwpCycleSource(header["nwp_cycle_source"]),
        representation=representation,
        status=ForecastStatus(header["status"]),
        version=header["version"],
        warm_up_source=WarmUpSource(warm_up_raw) if warm_up_raw is not None else None,
        warm_up_state_age_hours=header["warm_up_state_age_hours"],
        observation_staleness_hours=header["observation_staleness_hours"],
        ensemble=ensemble,
        created_at=utc_from_row(header["created_at"]),
        updated_at=utc_from_row(header["updated_at"]),
        qc_status=QcStatus(header["qc_status"]),
        qc_flags=tuple(
            QcFlag(
                rule_id=f["rule_id"],
                rule_version=f["rule_version"],
                status=QcStatus(f["status"]),
                detail=f.get("detail"),
            )
            for f in (header["qc_flags"] or [])
        ),
        input_quality=(
            InputQualityLevel(header["input_quality"])
            if header.get("input_quality") is not None
            else None
        ),
        input_quality_flags=_parse_input_quality_flags(
            header.get("input_quality_flags")
        ),
        combination_strategy=header.get("combination_strategy"),
        source_model_ids=(
            [ModelId(mid) for mid in header["source_model_ids"]]
            if header.get("source_model_ids") is not None
            else None
        ),
        rating_curve_id=(
            RatingCurveId(header["rating_curve_id"])
            if header.get("rating_curve_id") is not None
            else None
        ),
    )


def _row_to_summary(row: sa.engine.row.RowMapping) -> ForecastSummaryRow:
    return ForecastSummaryRow(
        id=ForecastId(row["id"]),
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        issued_at=utc_from_row(row["issued_at"]),
        parameter=row["parameter"],
        representation=EnsembleRepresentation(row["representation"]),
        status=ForecastStatus(row["status"]),
        qc_status=QcStatus(row["qc_status"]),
        nwp_cycle_source=NwpCycleSource(row["nwp_cycle_source"]),
        created_at=utc_from_row(row["created_at"]),
        input_quality=(
            InputQualityLevel(row["input_quality"])
            if row.get("input_quality") is not None
            else None
        ),
        input_quality_flags=_parse_input_quality_flags(row.get("input_quality_flags")),
    )
