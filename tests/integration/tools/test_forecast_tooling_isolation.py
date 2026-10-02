from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.types.enums import ForecastStatus
from sapphire_flow.types.ids import ForecastId
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_forecast_data_use import _pair, _stores

_ROOT = Path(__file__).parents[3]


def _module(path: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location(Path(path).stem, _ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _feed_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    return _module("scripts/forecast_feed_resilience.py", monkeypatch)


def _seed_mixed(conn: sa.Connection) -> tuple[Any, Any, Any]:
    # Exact dormant writer guard is removed only inside caller's rolled-back tx.
    conn.execute(sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts"))
    ordinary, test = _pair(conn)
    from sapphire_flow.types.domain import InputQualityFlag, QcFlag
    from sapphire_flow.types.enums import (
        InputQualityCategory,
        InputQualityLevel,
        QcStatus,
    )

    ordinary = replace(
        ordinary,
        qc_status=QcStatus.QC_SUSPECT,
        qc_flags=(
            QcFlag(
                rule_id="synthetic",
                rule_version="1",
                status=QcStatus.QC_SUSPECT,
                detail="safe control",
            ),
        ),
        input_quality=InputQualityLevel.DEGRADED,
        input_quality_flags=(
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.DEGRADED,
                detail="safe synthetic gap",
            ),
        ),
    )
    standard_store, test_store = _stores(conn)
    standard_store.store_forecast(ordinary)
    standard_store.transition_status(ordinary.id, 1, ForecastStatus.SUPERSEDED)
    test_store.store_forecast(test)
    newer_test = replace(
        test, id=ForecastId(uuid4()), issued_at=test.issued_at + timedelta(hours=3)
    )
    test_store.store_forecast(newer_test)
    upper = replace(
        ordinary,
        id=ForecastId(uuid4()),
        issued_at=ordinary.issued_at + timedelta(hours=2),
    )
    standard_store.store_forecast(upper)
    return ordinary, upper, newer_test


class TestBlackoutProjection:
    @pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
    def test_safe_standard_history_under_real_runtime_role(
        self,
        role_harness: Any,
        role: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )
        module = _feed_module(monkeypatch)
        with role_harness.owner_engine.connect() as conn, conn.begin():
            ordinary, upper, newer_test = _seed_mixed(conn)
            expected = dict(
                conn.execute(
                    sa.text("SELECT * FROM forecasts WHERE id=:id"), {"id": ordinary.id}
                )
                .mappings()
                .one()
            )
            expected.pop("input_lineage")
            conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
            assert conn.scalar(sa.text("SELECT current_user")) == role
            for projection in ("input_lineage", "*"):
                with (
                    pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                    conn.begin_nested(),
                ):
                    conn.execute(sa.text(f"SELECT {projection} FROM forecasts"))
            monkeypatch.setattr(
                module,
                "_engine",
                lambda: SimpleNamespace(connect=lambda: nullcontext(conn)),
            )
            args = module._build_parser().parse_args(
                [
                    "capture-snapshot",
                    "--output-dir",
                    str(tmp_path),
                    "--blackout-start",
                    ordinary.issued_at.isoformat(),
                    "--blackout-end",
                    upper.issued_at.isoformat(),
                ]
            )
            module.capture_snapshot(args)
            payload = json.loads((tmp_path / "plan100_step0_snapshot.json").read_text())
            assert set(payload) == {
                "captured_at",
                "environment",
                "model_assignments",
                "group_model_assignments",
                "model_artifacts",
                "blackout_forecasts",
                "blackout_alerts",
                "latest_nwp_cycle",
            }
            assert payload["blackout_forecasts"] == json.loads(
                json.dumps([expected], default=module._json_default)
            )
            assert payload["blackout_forecasts"][0]["status"] == "superseded"
            assert payload["blackout_forecasts"][0]["qc_status"] == "qc_suspect"
            assert payload["blackout_forecasts"][0]["input_quality"] == "degraded"
            assert "input_lineage" not in payload["blackout_forecasts"][0]
            # Both ordinary upper-bound control and newer TEST are now inside.
            args.blackout_end = (
                newer_test.issued_at + timedelta(seconds=1)
            ).isoformat()
            module.capture_snapshot(args)
            expanded = json.loads(
                (tmp_path / "plan100_step0_snapshot.json").read_text()
            )
            assert [row["id"] for row in expanded["blackout_forecasts"]] == [
                str(ordinary.id),
                str(upper.id),
            ]
            conn.rollback()


class TestStandingClassCounters:
    @pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
    def test_real_counter_sql_separates_class_history_and_audit(
        self, role_harness: Any, monkeypatch: pytest.MonkeyPatch, role: str
    ) -> None:
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )
        module = _module("tools/standing_snapshot.py", monkeypatch)
        with role_harness.owner_engine.connect() as conn, conn.begin():
            empty = dict(
                conn.execute(sa.text(module._COUNTER_SQL.replace('\\"', '"'))).all()
            )
            assert empty["forecasts_standard_count"] == "0"
            assert empty["forecasts_expired_rating_test_latest_issued_at"] == "-"
            ordinary, upper, newer_test = _seed_mixed(conn)
            counters = dict(
                conn.execute(sa.text(module._COUNTER_SQL.replace('\\"', '"'))).all()
            )
            assert counters["forecasts_standard_count"] == "2"
            assert counters["forecasts_expired_rating_test_count"] == "2"
            assert counters["forecasts_all_classes_audit_count"] == "4"
            assert counters["forecasts_standard_latest_issued_at"] == conn.scalar(
                sa.text("SELECT CAST(:issued AS timestamptz)::text"),
                {"issued": upper.issued_at},
            )
            assert counters[
                "forecasts_expired_rating_test_latest_issued_at"
            ] == conn.scalar(
                sa.text("SELECT CAST(:issued AS timestamptz)::text"),
                {"issued": newer_test.issued_at},
            )
            assert (
                counters["forecasts_all_classes_audit_latest_issued_at"]
                == counters["forecasts_expired_rating_test_latest_issued_at"]
            )
            assert "forecasts" not in counters and "forecasts_latest" not in counters
            # Forecast-only aggregate clauses can also run under runtime safe ACLs.
            branches = [
                part
                for part in module._COUNTER_SQL.split("union all")
                if "from forecasts" in part
            ]
            conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
            assert conn.scalar(sa.text("SELECT current_user")) == role
            runtime = dict(conn.execute(sa.text("union all".join(branches))).all())
            assert runtime == {
                key: value
                for key, value in counters.items()
                if key.startswith("forecasts_")
            }
            conn.rollback()


class TestCaptureWindowCompatibility:
    @pytest.mark.parametrize("window", ["defaults", "offset"])
    def test_real_cli_strings_bound_both_forecasts_and_nonempty_alerts(
        self,
        role_harness: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        window: str,
    ) -> None:
        from datetime import datetime

        from sapphire_flow.db.metadata import alerts

        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )
        module = _feed_module(monkeypatch)
        argv = ["capture-snapshot", "--output-dir", str(tmp_path)]
        if window == "offset":
            argv += [
                "--blackout-start",
                "2026-07-03T05:45:00+05:45",
                "--blackout-end",
                "2026-07-03T07:45:00+05:45",
            ]
        args = module._build_parser().parse_args(argv)
        assert isinstance(args.blackout_start, str) and isinstance(
            args.blackout_end, str
        )
        start = datetime.fromisoformat(args.blackout_start)
        end = datetime.fromisoformat(args.blackout_end)
        times = [
            start - timedelta(seconds=1),
            start,
            start + timedelta(seconds=1),
            end,
            end + timedelta(seconds=1),
        ]
        with role_harness.owner_engine.connect() as conn, conn.begin():
            ordinary, _ = _pair(conn)
            store = _stores(conn)[0]
            expected_forecasts = []
            expected_alerts = []
            for index, timestamp in enumerate(times):
                forecast = replace(
                    ordinary, id=ForecastId(uuid4()), issued_at=timestamp
                )
                store.store_forecast(forecast)
                alert_id = uuid4()
                conn.execute(
                    sa.insert(alerts).values(
                        id=alert_id,
                        station_id=ordinary.station_id,
                        source="forecast",
                        alert_level=f"control-{index}",
                        status="raised",
                        trigger_value=float(index),
                        triggered_at=timestamp,
                    )
                )
                if index in {1, 2}:
                    expected_forecasts.append(str(forecast.id))
                    expected_alerts.append(str(alert_id))
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            monkeypatch.setattr(
                module,
                "_engine",
                lambda: SimpleNamespace(connect=lambda: nullcontext(conn)),
            )
            args.func(args)
            payload = json.loads((tmp_path / "plan100_step0_snapshot.json").read_text())
            assert [
                row["id"] for row in payload["blackout_forecasts"]
            ] == expected_forecasts
            assert [row["id"] for row in payload["blackout_alerts"]] == expected_alerts
            assert [row["trigger_value"] for row in payload["blackout_alerts"]] == [
                1.0,
                2.0,
            ]
            conn.rollback()

    def test_original_alert_string_predicate_reproduces_preexisting_failure(
        self,
        db_connection: sa.Connection,
    ) -> None:
        from sapphire_flow.db.metadata import alerts

        with (
            pytest.raises(
                sa.exc.ProgrammingError,
                match="timestamp with time zone >= character varying",
            ),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.select(alerts).where(
                    alerts.c.triggered_at >= "2026-07-03T00:00:00+00:00"
                )
            )

    def test_secondary_station_and_model_ordering(
        self,
        role_harness: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        assert (
            role_harness.run_bootstrap("api-fixture", "worker-fixture").returncode == 0
        )
        module = _feed_module(monkeypatch)
        with role_harness.owner_engine.connect() as conn, conn.begin():
            first, _ = _pair(conn)
            second, _ = _pair(conn)
            store = _stores(conn)[0]
            sibling = replace(
                first,
                id=ForecastId(uuid4()),
                model_id=second.model_id,
                model_artifact_id=None,
            )
            expected = sorted(
                [second, sibling, first],
                key=lambda f: (f.issued_at, str(f.station_id), str(f.model_id)),
            )
            rows = list(reversed(expected))
            insertion_keys = [
                (f.issued_at, str(f.station_id), str(f.model_id)) for f in rows
            ]
            expected_keys = [
                (f.issued_at, str(f.station_id), str(f.model_id)) for f in expected
            ]
            assert insertion_keys != expected_keys
            for row in rows:
                store.store_forecast(row)
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            monkeypatch.setattr(
                module,
                "_engine",
                lambda: SimpleNamespace(connect=lambda: nullcontext(conn)),
            )
            args = module._build_parser().parse_args(
                [
                    "capture-snapshot",
                    "--output-dir",
                    str(tmp_path),
                    "--blackout-start",
                    first.issued_at.isoformat(),
                    "--blackout-end",
                    (first.issued_at + timedelta(seconds=1)).isoformat(),
                ]
            )
            args.func(args)
            payload = json.loads((tmp_path / "plan100_step0_snapshot.json").read_text())
            assert [item["id"] for item in payload["blackout_forecasts"]] == [
                str(f.id) for f in expected
            ]
            conn.rollback()
