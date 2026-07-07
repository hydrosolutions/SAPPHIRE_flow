from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig, load_config
from sapphire_flow.db.metadata import (
    alerts,
    group_model_assignments,
    model_artifacts,
    model_assignments,
    pipeline_health,
    stations,
    weather_forecasts,
)
from sapphire_flow.types.enums import AlertSource, AlertStatus, PipelineCheckType
from sapphire_flow.types.ids import (
    FALLBACK_ASSIGNMENT_PRIORITIES,
    FALLBACK_MODEL_IDS,
    ModelId,
)

_DEFAULT_OVERRIDE_ALLOWLIST = frozenset(
    {
        ("2009", "nwp_rainfall_runoff"),
        ("2091", "nwp_rainfall_runoff"),
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n"
    )


def _write_stdout(payload: object) -> None:
    sys.stdout.write(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
    )
    sys.stdout.write("\n")


def _engine() -> sa.Engine:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise RuntimeError("DATABASE_URL is required")
    return sa.create_engine(database_url, pool_pre_ping=True)


def _load_deployment_config(path: str | None) -> DeploymentConfig:
    if path is not None:
        return load_config(path)
    env_path = os.environ.get("SAPPHIRE_CONFIG")
    return (
        load_config(env_path)
        if env_path is not None
        else DeploymentConfig(max_retention_days=600)
    )


def _row_dicts(rows: list[sa.engine.RowMapping]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def capture_snapshot(args: argparse.Namespace) -> None:
    out_dir = Path(args.output_dir)
    with _engine().connect() as conn:
        payload = {
            "captured_at": _utc_now(),
            "environment": {
                "SAPPHIRE_CONFIG": os.environ.get("SAPPHIRE_CONFIG"),
                "SAPPHIRE_CONFIG_OVERLAY": os.environ.get("SAPPHIRE_CONFIG_OVERLAY"),
                "SAPPHIRE_REQUIRE_NWP": os.environ.get("SAPPHIRE_REQUIRE_NWP"),
            },
            "model_assignments": _row_dicts(
                conn.execute(sa.select(model_assignments)).mappings().all()
            ),
            "group_model_assignments": _row_dicts(
                conn.execute(sa.select(group_model_assignments)).mappings().all()
            ),
            "model_artifacts": _row_dicts(
                conn.execute(
                    sa.select(model_artifacts).where(
                        model_artifacts.c.status.in_(["active", "training", "rejected"])
                    )
                )
                .mappings()
                .all()
            ),
            "blackout_forecasts": _row_dicts(
                conn.execute(
                    sa.text(
                        """
                        SELECT *
                        FROM forecasts
                        WHERE issued_at >= :start AND issued_at < :end
                        ORDER BY issued_at, station_id, model_id
                        """
                    ),
                    {"start": args.blackout_start, "end": args.blackout_end},
                )
                .mappings()
                .all()
            ),
            "blackout_alerts": _row_dicts(
                conn.execute(
                    sa.select(alerts)
                    .where(alerts.c.triggered_at >= args.blackout_start)
                    .where(alerts.c.triggered_at < args.blackout_end)
                    .order_by(alerts.c.triggered_at)
                )
                .mappings()
                .all()
            ),
            "latest_nwp_cycle": conn.execute(
                sa.select(sa.func.max(weather_forecasts.c.cycle_time))
            ).scalar(),
        }
    _write_json(out_dir / "plan100_step0_snapshot.json", payload)


def _assignment_rows(conn: sa.Connection) -> list[dict[str, Any]]:
    station_rows = conn.execute(
        sa.select(
            sa.literal("station").label("scope"),
            stations.c.code.label("subject_code"),
            model_assignments.c.station_id.label("subject_id"),
            model_assignments.c.model_id,
            model_assignments.c.priority,
            model_assignments.c.time_step,
            model_assignments.c.status,
        )
        .select_from(model_assignments.join(stations))
        .order_by(stations.c.code, model_assignments.c.model_id)
    ).mappings()
    group_rows = conn.execute(
        sa.select(
            sa.literal("group").label("scope"),
            group_model_assignments.c.group_id.label("subject_code"),
            group_model_assignments.c.group_id.label("subject_id"),
            group_model_assignments.c.model_id,
            group_model_assignments.c.priority,
            group_model_assignments.c.time_step,
            group_model_assignments.c.status,
        ).order_by(
            group_model_assignments.c.group_id,
            group_model_assignments.c.model_id,
        )
    ).mappings()
    return [dict(row) for row in [*station_rows, *group_rows]]


def _target_priority(
    *,
    model_id: str,
    config: DeploymentConfig,
) -> int:
    typed = ModelId(model_id)
    if typed in FALLBACK_MODEL_IDS:
        return config.assignment_priority_for_model(typed)
    return config.priority_for_model(model_id)


def audit_priorities(args: argparse.Namespace) -> None:
    config = _load_deployment_config(args.config)
    allowlist = _DEFAULT_OVERRIDE_ALLOWLIST | {
        tuple(item.split(":", 1)) for item in args.allow_override
    }
    with _engine().connect() as conn:
        rows = _assignment_rows(conn)

    diffs = []
    heterogeneous_time_steps: dict[str, list[str]] = {}
    grouped_time_steps: dict[str, set[str]] = {}
    for row in rows:
        model_id = str(row["model_id"])
        target = _target_priority(model_id=model_id, config=config)
        key = (str(row["subject_code"]), model_id)
        action = "keep_override" if key in allowlist else "reconcile"
        if int(row["priority"]) != target and action == "reconcile":
            diffs.append({**row, "target_priority": target, "action": action})
        grouped_time_steps.setdefault(
            f"{row['scope']}:{row['subject_code']}", set()
        ).add(str(row["time_step"]))

    for subject, time_steps in grouped_time_steps.items():
        if len(time_steps) > 1:
            heterogeneous_time_steps[subject] = sorted(time_steps)

    payload = {
        "audited_at": _utc_now(),
        "diffs": diffs,
        "allowlist": sorted(f"{sid}:{mid}" for sid, mid in allowlist),
        "heterogeneous_time_steps": heterogeneous_time_steps,
    }
    if args.output is not None:
        _write_json(Path(args.output), payload)
    else:
        _write_stdout(payload)


def reconcile_priorities(args: argparse.Namespace) -> None:
    if not args.apply:
        audit_priorities(args)
        return
    if not args.backup_reference:
        raise RuntimeError("--backup-reference is required with --apply")

    config = _load_deployment_config(args.config)
    allowlist = _DEFAULT_OVERRIDE_ALLOWLIST | {
        tuple(item.split(":", 1)) for item in args.allow_override
    }
    rows_changed = 0
    applied: list[dict[str, Any]] = []

    with _engine().begin() as conn:
        conn.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 100}
        )
        for row in _assignment_rows(conn):
            model_id = str(row["model_id"])
            typed_model_id = ModelId(model_id)
            if (str(row["subject_code"]), model_id) in allowlist:
                continue
            target = _target_priority(model_id=model_id, config=config)
            if int(row["priority"]) == target:
                continue
            if row["scope"] == "station":
                stmt = (
                    sa.update(model_assignments)
                    .where(model_assignments.c.station_id == row["subject_id"])
                    .where(model_assignments.c.model_id == model_id)
                    .values(priority=target)
                )
            else:
                stmt = (
                    sa.update(group_model_assignments)
                    .where(group_model_assignments.c.group_id == row["subject_id"])
                    .where(group_model_assignments.c.model_id == model_id)
                    .values(priority=target)
                )
            if typed_model_id in FALLBACK_MODEL_IDS and target < min(
                FALLBACK_ASSIGNMENT_PRIORITIES.values()
            ):
                raise RuntimeError(
                    f"refusing below-tier fallback priority for {model_id}"
                )
            conn.execute(stmt)
            rows_changed += 1
            applied.append({**row, "target_priority": target})

        conn.execute(
            sa.insert(pipeline_health).values(
                check_type=PipelineCheckType.PRIORITY_MIGRATION_AUDIT.value,
                checked_at=_utc_now(),
                status="ok",
                subject="m0a_priority_reconciliation",
                detail={
                    "rows_changed": rows_changed,
                    "triaged_overrides": sorted(
                        f"{sid}:{mid}" for sid, mid in allowlist
                    ),
                    "backup_reference": args.backup_reference,
                    "applied": applied,
                },
                cycle_time=None,
            )
        )


def audit_floor(args: argparse.Namespace) -> None:
    with _engine().connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    """
                SELECT s.id, s.code, s.name
                FROM stations s
                WHERE s.station_kind <> 'weather'
                  AND s.station_status = 'operational'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM model_artifacts ma
                    WHERE ma.station_id = s.id
                      AND ma.model_id = 'climatology_fallback'
                      AND ma.status = 'active'
                  )
                ORDER BY s.code
                """
                )
            )
            .mappings()
            .all()
        )
    payload = {"audited_at": _utc_now(), "floorless_operational": _row_dicts(rows)}
    if args.output is not None:
        _write_json(Path(args.output), payload)
    else:
        _write_stdout(payload)


def audit_forecast_alerts(args: argparse.Namespace) -> None:
    fallback_ids = [str(model_id) for model_id in FALLBACK_MODEL_IDS]
    with _engine().connect() as conn:
        rows = (
            conn.execute(
                sa.select(alerts)
                .where(alerts.c.source == AlertSource.FORECAST.value)
                .where(
                    alerts.c.status.in_(
                        [AlertStatus.RAISED.value, AlertStatus.ACKNOWLEDGED.value]
                    )
                )
                .where(alerts.c.model_ids.op("?|")(fallback_ids))
                .order_by(alerts.c.triggered_at)
            )
            .mappings()
            .all()
        )
    payload = {
        "audited_at": _utc_now(),
        "fallback_model_ids": fallback_ids,
        "active_forecast_alerts_with_fallback_models": _row_dicts(rows),
    }
    if args.output is not None:
        _write_json(Path(args.output), payload)
    else:
        _write_stdout(payload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)

    snap = sub.add_parser("capture-snapshot")
    snap.add_argument("--output-dir", required=True)
    snap.add_argument("--blackout-start", default="2026-07-03T00:00:00+00:00")
    snap.add_argument("--blackout-end", default="2026-07-06T23:59:59+00:00")
    snap.set_defaults(func=capture_snapshot)

    audit = sub.add_parser("audit-priorities")
    audit.add_argument("--config")
    audit.add_argument("--output")
    audit.add_argument("--allow-override", action="append", default=[])
    audit.set_defaults(func=audit_priorities)

    reconcile = sub.add_parser("reconcile-priorities")
    reconcile.add_argument("--config")
    reconcile.add_argument("--output")
    reconcile.add_argument("--allow-override", action="append", default=[])
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--backup-reference")
    reconcile.set_defaults(func=reconcile_priorities)

    floor = sub.add_parser("audit-floor")
    floor.add_argument("--output")
    floor.set_defaults(func=audit_floor)

    alert_audit = sub.add_parser("audit-forecast-alerts")
    alert_audit.add_argument("--output")
    alert_audit.set_defaults(func=audit_forecast_alerts)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
