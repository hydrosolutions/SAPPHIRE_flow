from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import savepoint_factory


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
def test_native_lineage_read_is_narrow_and_worker_can_write(
    role_harness: _RoleBootstrapHarness, role: str
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        ordinary, native = native_pair(conn)
        if role == "sapphire_api":
            PgForecastStore(
                conn,
                data_use=native.data_use,
                transaction_factory=savepoint_factory(conn),
            ).store_forecast(native)
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        scoped = PgForecastStore(
            conn,
            data_use=ForecastDataUse.PROVISIONAL_RATING,
            transaction_factory=savepoint_factory(conn),
        )
        if role == "sapphire_worker":
            assert scoped.store_forecast(native) == native.id
            assert scoped.store_forecast(native) == native.id
        assert scoped.fetch_forecast(native.id).input_lineage == native.input_lineage
        assert PgForecastStore(conn).fetch_forecast(native.id) is None
        assert (
            conn.scalar(sa.select(sa.func.native_rating_lineage(ordinary.id, False)))
            is None
        )
        with (
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
            conn.begin_nested(),
        ):
            conn.execute(sa.text("SELECT input_lineage FROM forecasts"))
        if role == "sapphire_api":
            with (
                pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text("SELECT content FROM rating_derived_discharge"))


def test_unrelated_old_new_when_does_not_break_operator_preflight(
    role_harness: _RoleBootstrapHarness,
) -> None:
    with role_harness.owner_engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE public.native_when_canary (id integer)"))
        conn.execute(
            sa.text(
                "CREATE TRIGGER native_when_canary BEFORE UPDATE "
                "ON public.native_when_canary "
                "FOR EACH ROW WHEN (OLD.id IS DISTINCT FROM NEW.id) "
                "EXECUTE FUNCTION public.native_rating_immutable()"
            )
        )
    try:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            assert conn.scalar(
                sa.text(
                    "SELECT has_table_privilege('sapphire_operator', "
                    "'rating_curves', 'INSERT')"
                )
            )
    finally:
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE public.native_when_canary"))


def test_native_trigger_definers_have_no_callable_runtime_authority(
    role_harness: _RoleBootstrapHarness,
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn:
        for function in ("native_rating_curve_guard", "native_rating_lineage_guard"):
            for role in ("sapphire_api", "sapphire_worker", "sapphire_operator"):
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_function_privilege(:role, :function, 'EXECUTE')"
                    ),
                    {"role": role, "function": f"public.{function}()"},
                )


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize(
    "day", [{}, {"day": "2026-09-28T00:00:00Z"}, {"fingerprints": ["a" * 64]}]
)
def test_worker_cannot_insert_missing_daily_evidence(
    role_harness: _RoleBootstrapHarness, rejected: bool, day: dict
) -> None:
    import json
    import threading
    from dataclasses import replace
    from uuid import uuid4

    from sapphire_flow.db.metadata import forecasts, rejected_forecasts
    from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
    from tests.integration.store.test_rejected_forecast_store import _entry

    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        _, native = native_pair(conn)
        table = rejected_forecasts if rejected else forecasts
        if rejected:
            entry = _entry(
                station_id=native.station_id,
                model_id=native.model_id,
                issued_at=native.issued_at,
            )
            entry = replace(
                entry,
                payload=replace(
                    entry.payload,
                    model_artifact_id=native.model_artifact_id,
                    data_use=native.data_use,
                    input_lineage=native.input_lineage,
                ),
            )
            PgRejectedForecastStore(
                conn,
                data_use=native.data_use,
                transaction_factory=savepoint_factory(conn),
            ).write_batch([entry], abandon=threading.Event())
        else:
            PgForecastStore(
                conn,
                data_use=native.data_use,
                transaction_factory=savepoint_factory(conn),
            ).store_forecast(native)
        row = dict(
            conn.execute(
                sa.select(table).where(
                    table.c.data_use == "provisional_rating",
                    table.c.model_id == native.model_id,
                )
            )
            .mappings()
            .one()
        )
        payload = json.loads(row["input_lineage"])
        from datetime import timedelta

        row["issued_at"] += timedelta(days=1)
        payload["rating_input"]["issue_time"] = row["issued_at"].isoformat()
        payload["rating_input"]["days"] = [day]
        row.update(id=uuid4(), input_lineage=json.dumps(payload))
        if not rejected:
            row["version"] += 1
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(sa.exc.DBAPIError, match="native rating daily input"),
            conn.begin_nested(),
        ):
            conn.execute(sa.insert(table).values(**row))


@pytest.mark.parametrize(
    "column", ["id", "issued_at", "model_artifact_id", "rating_curve_id"]
)
def test_worker_native_header_identity_is_immutable_without_evidence(
    role_harness: _RoleBootstrapHarness, column: str
) -> None:
    from datetime import timedelta
    from uuid import uuid4

    from sapphire_flow.db.metadata import forecasts, rating_curves

    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        _, native = native_pair(conn)
        PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        ).store_forecast(native)
        value = (
            native.issued_at + timedelta(hours=1)
            if column == "issued_at"
            else conn.scalar(sa.select(rating_curves.c.id).limit(1))
            if column == "rating_curve_id"
            else uuid4()
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(sa.exc.DBAPIError, match="native rating"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == native.id)
                .values(**{column: value})
            )
        conn.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == native.id)
            .values(status="superseded")
        )
        assert (
            conn.scalar(
                sa.select(forecasts.c.status).where(forecasts.c.id == native.id)
            )
            == "superseded"
        )


def test_worker_day_window_is_24_hours_across_session_dst(
    role_harness: _RoleBootstrapHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sapphire_flow.db.metadata import forecasts
    from tests.integration.store import test_native_rating_forecasts as fixture

    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr(fixture, "_NOW", datetime(2026, 11, 2, 0, 30, tzinfo=UTC))
    from dataclasses import replace

    make_observation = fixture.make_observation
    monkeypatch.setattr(
        fixture,
        "make_observation",
        lambda **kwargs: replace(make_observation(**kwargs), id=uuid4()),
    )
    with role_harness.owner_engine.connect() as conn, conn.begin():
        _, native = fixture.native_pair(conn)
        PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        ).store_forecast(native)
        row = dict(
            conn.execute(sa.select(forecasts).where(forecasts.c.id == native.id))
            .mappings()
            .one()
        )
        payload = json.loads(row["input_lineage"])
        row["id"] = uuid4()
        row["issued_at"] += timedelta(days=1)
        payload["rating_input"]["issue_time"] = row["issued_at"].isoformat()
        payload["rating_input"]["days"][0]["day"] = "2026-11-01T00:00:00+00:00"
        row["input_lineage"] = json.dumps(payload)
        conn.execute(sa.text("SET LOCAL TIME ZONE 'America/New_York'"))
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(
                sa.exc.DBAPIError, match="input identity or tenant disagreement"
            ),
            conn.begin_nested(),
        ):
            conn.execute(sa.insert(forecasts).values(**row))


if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


def test_worker_cannot_consume_issue_days_partial_bucket(
    role_harness: _RoleBootstrapHarness,
) -> None:
    import json
    from datetime import timedelta
    from uuid import uuid4

    from sapphire_flow.db.metadata import forecasts

    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        _, native = native_pair(conn)
        PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        ).store_forecast(native)
        row = dict(
            conn.execute(sa.select(forecasts).where(forecasts.c.id == native.id))
            .mappings()
            .one()
        )
        payload = json.loads(row["input_lineage"])
        row["id"] = uuid4()
        row["issued_at"] -= timedelta(hours=18)
        payload["rating_input"]["issue_time"] = row["issued_at"].isoformat()
        row["input_lineage"] = json.dumps(payload)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(sa.exc.DBAPIError, match="native rating daily input"),
            conn.begin_nested(),
        ):
            conn.execute(sa.insert(forecasts).values(**row))
