# ruff: noqa: F811 -- shared HTTP composition fixtures
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from polars.testing import assert_frame_equal

from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.ids import ForecastId, RejectedForecastId
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import (
    _entry,
    savepoint_factory,
)
from tests.unit.api.test_swiss_overview import api as api
from tests.unit.services.test_evaluation_forecasts import case as case


class TestOverviewExactBatches:
    def test_accepted_exact_ids_and_native_lineage_isolation(
        self, db_connection: sa.Connection
    ) -> None:
        ordinary, native = native_pair(db_connection)
        standard = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        provisional = PgForecastStore(
            db_connection,
            data_use=native.data_use,
            transaction_factory=savepoint_factory(db_connection),
        )
        standard.store_forecast(ordinary)
        provisional.store_forecast(native)
        ids = [ordinary.id, native.id, ForecastId(uuid4()), ordinary.id]
        result = standard.fetch_forecasts_by_ids(ids)
        assert set(result) == {ordinary.id}
        assert result[ordinary.id].input_lineage is None
        assert_frame_equal(
            result[ordinary.id].ensemble.values,
            standard.fetch_forecast(ordinary.id).ensemble.values,
        )
        result = provisional.fetch_forecasts_by_ids(ids)
        assert set(result) == {native.id}
        assert result[native.id].input_lineage == native.input_lineage
        assert_frame_equal(
            result[native.id].ensemble.values,
            provisional.fetch_forecast(native.id).ensemble.values,
        )
        assert standard.fetch_forecasts_by_ids([]) == {}
        assert standard.fetch_forecasts_by_ids([native.id]) == {}

    def test_rejected_exact_ids_and_native_lineage_isolation(
        self, db_connection: sa.Connection
    ) -> None:
        ordinary, native = native_pair(db_connection)
        standard = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        provisional = PgRejectedForecastStore(
            db_connection,
            data_use=native.data_use,
            transaction_factory=savepoint_factory(db_connection),
        )
        entry = _entry(
            station_id=ordinary.station_id,
            model_id=ordinary.model_id,
            issued_at=ordinary.issued_at,
        )
        entry = replace(
            entry,
            payload=replace(
                entry.payload, model_artifact_id=ordinary.model_artifact_id
            ),
        )
        native_entry = replace(
            entry,
            attempt_id=uuid4(),
            payload=replace(
                entry.payload,
                data_use=native.data_use,
                input_lineage=native.input_lineage,
            ),
        )
        standard.write_batch([entry], abandon=threading.Event())
        provisional.write_batch([native_entry], abandon=threading.Event())
        from datetime import timedelta

        start, end = (
            ordinary.issued_at - timedelta(days=1),
            ordinary.issued_at + timedelta(days=1),
        )
        standard_row = standard.fetch_rejected_forecasts(
            ordinary.station_id, start, end
        )[0][0]
        native_row = provisional.fetch_rejected_forecasts(
            ordinary.station_id, start, end
        )[0][0]
        ids = [
            standard_row.id,
            native_row.id,
            RejectedForecastId(uuid4()),
            standard_row.id,
        ]
        assert standard.fetch_rejected_forecasts_by_ids(ids) == {
            standard_row.id: standard_row
        }
        assert provisional.fetch_rejected_forecasts_by_ids(ids) == {
            native_row.id: native_row
        }
        assert native_row.input_lineage == native.input_lineage
        assert standard_row.input_lineage is None
        assert standard.fetch_rejected_forecasts_by_ids([]) == {}
        assert standard.fetch_rejected_forecasts_by_ids([native_row.id]) == {}


class TestStoredDecodeBoundary:
    @pytest.mark.parametrize(
        ("rejected", "column", "payload"),
        [
            (
                False,
                "qc_flags",
                [{"rule_id": "bad", "rule_version": "1", "status": "not-a-status"}],
            ),
            (
                True,
                "qc_flags",
                [{"rule_id": "bad", "rule_version": "1", "status": "not-a-status"}],
            ),
            (False, "qc_flags", [{"status": "qc_passed"}]),
            (True, "qc_flags", [{"status": "qc_passed"}]),
            (False, "qc_flags", {"status": "qc_passed"}),
            (True, "qc_flags", [None]),
            (False, "input_quality_flags", [{"category": "observation"}]),
            (False, "input_quality_flags", "not-a-list"),
            (True, "values", []),
            (True, "values", {"0": None}),
            (True, "values", {"0": [["2025-01-01T00:00:00+00:00"]]}),
            (True, "values", {"0": [["2025-01-01T00:00:00+00:00", {}]]}),
            (True, "values", {"0": [["2025-01-01T00:00:00+00:00", []]]}),
        ],
    )
    def test_malformed_exact_record_is_typed(
        self,
        db_connection: sa.Connection,
        rejected: bool,
        column: str,
        payload: object,
        api: SimpleNamespace,
        case: SimpleNamespace,
        tmp_path: Path,
    ) -> None:
        from sapphire_flow.db.metadata import forecasts, rejected_forecasts
        from sapphire_flow.exceptions import StoredForecastDecodeError

        ordinary, _ = native_pair(db_connection)
        if rejected:
            store = PgRejectedForecastStore(
                db_connection, transaction_factory=savepoint_factory(db_connection)
            )
            entry = _entry(
                station_id=ordinary.station_id,
                model_id=ordinary.model_id,
                issued_at=ordinary.issued_at,
            )
            entry = replace(
                entry,
                payload=replace(
                    entry.payload, model_artifact_id=ordinary.model_artifact_id
                ),
            )
            ids = list(store.write_committed(entry).values())
            reader = store.fetch_rejected_forecasts_by_ids
            table = rejected_forecasts
        else:
            store = PgForecastStore(
                db_connection, transaction_factory=savepoint_factory(db_connection)
            )
            store.store_forecast(ordinary)
            ids = [ordinary.id]
            reader = store.fetch_forecasts_by_ids
            table = forecasts
        assert set(reader(ids)) == set(ids)
        row = dict(
            db_connection.execute(sa.select(table).where(table.c.id == ids[0]))
            .mappings()
            .one()
        )
        row.update(id=uuid4())
        row[column] = payload
        if rejected:
            row["attempt_id"] = uuid4()
        else:
            from datetime import timedelta

            row["issued_at"] += timedelta(days=1)
        db_connection.execute(sa.insert(table).values(**row))
        if not rejected:
            from sapphire_flow.db.metadata import forecast_values

            values = (
                db_connection.execute(
                    sa.select(forecast_values).where(
                        forecast_values.c.forecast_id == ids[0]
                    )
                )
                .mappings()
                .all()
            )
            db_connection.execute(
                sa.insert(forecast_values),
                [
                    dict(
                        value,
                        id=uuid4(),
                        forecast_id=row["id"],
                        issued_at=row["issued_at"],
                    )
                    for value in values
                ],
            )
        ids.append(row["id"])

        from sapphire_flow.types.enums import QcStatus
        from tests.conftest import make_observation
        from tests.fakes.bafu_archive import write_raw_evidence
        from tests.unit.services.forecast_lab.test_bafu_archive import (
            _rows_from_fixture,
            _write_run,
        )

        archive_rows = _rows_from_fixture(
            case.station.code, issued_at=case.issue, produced_at=case.issue
        )
        shift = case.issue - archive_rows[0]["valid_time"]
        for archive_row in archive_rows:
            archive_row["valid_time"] += shift
        _write_run(tmp_path, case.station.code, case.issue, archive_rows)
        write_raw_evidence(tmp_path, case.station.code, case.issue, archive_rows)
        api.config.bafu_forecast_archive_path = tmp_path
        api.obs.store_observations(
            [
                make_observation(
                    station_id=case.station.id,
                    timestamp=api.now,
                    value=12,
                    qc_status=QcStatus.QC_PASSED,
                )
            ]
        )

        def read_corrupt(_ids: list) -> dict:
            return reader(ids)

        case.records.fetch_forecasts_by_ids = read_corrupt
        response = api.client.get("/api/v1/swiss/overview")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        item = response.json()["items"][0]
        assert item["forecast"] == {
            "state": "unavailable",
            "reason": "authority_integrity_error",
        }
        assert item["observation"]["latest"]["value"] == 12
        assert item["bafu_comparison"]["state"] == "available"

        with pytest.raises(StoredForecastDecodeError, match="invalid stored"):
            reader(ids)
