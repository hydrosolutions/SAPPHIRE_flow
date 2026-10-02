from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness

from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from sapphire_flow.db.metadata import forecasts
from sapphire_flow.exceptions import StoreError
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_forecast_data_use import _pair, _stores
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _seed_model,
    _seed_station,
)


class TestForecastDataUseMigration:
    def test_legacy_standard_roundtrip_and_index(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0068")
        fid = uuid4()
        with engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO forecasts "
                    "(id,station_id,model_id,issued_at,representation,parameter,units) "
                    "VALUES (:id,:sid,:mid,:issued,'members','discharge','m3/s')"
                ),
                {"id": fid, "sid": sid, "mid": mid, "issued": _ISSUED_A},
            )
        command.upgrade(config, "0069")
        with engine.connect() as conn:
            row = conn.execute(sa.select(forecasts).where(forecasts.c.id == fid)).one()
            assert row.data_use == "standard"
            assert row.input_lineage is None
            indexes = sa.inspect(conn).get_indexes("forecasts")
            index = next(
                i
                for i in indexes
                if i["name"] == "uq_forecasts_station_model_issued_param"
            )
            assert index["column_names"] == [
                "station_id",
                "model_id",
                "issued_at",
                "parameter",
                "data_use",
            ]
            assert index["unique"]
        command.downgrade(config, "0068")
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM forecasts WHERE id=:id"), {"id": fid}
                )
                == 1
            )
        command.upgrade(config, "0069")

    def test_nonempty_test_downgrade_refused(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0069")
        with engine.begin() as conn:
            # This database is disposable; restore exactly the dormant trigger
            # before committing the synthetic fixture used by downgrade tests.
            conn.execute(
                sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts")
            )
            _, test = _pair(conn)
            _stores(conn)[1].store_forecast(test)
            conn.execute(
                sa.text(
                    "CREATE TRIGGER trg_forecast_test_write_refused "
                    "BEFORE INSERT ON forecasts FOR EACH ROW "
                    "EXECUTE FUNCTION public.forecast_test_write_refused()"
                )
            )
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(config, "0068")
        with engine.connect() as conn:
            assert (
                conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "0069"
            )


class TestDeployedRuntimeRoleGuard:
    @pytest.mark.parametrize(
        "role,password",
        [
            ("sapphire_worker", "worker-fixture"),
            ("sapphire_api", "api-fixture"),
        ],
    )
    def test_actual_runtime_login_cannot_write_test_forecasts(
        self, role_harness: _RoleBootstrapHarness, role: str, password: str
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.begin() as conn:
            ordinary, test = _pair(conn)
        engine = sa.create_engine(role_harness.role_url(role, password))
        refusal = (
            "test forecast writes are disabled pending deployment isolation"
            if role == "sapphire_worker"
            else "disabled|permission denied"
        )
        try:
            with engine.begin() as conn:
                assert conn.scalar(sa.text("SELECT session_user")) == role
                store = PgForecastStore(
                    conn, data_use=ForecastDataUse.EXPIRED_RATING_TEST
                )
                # API may refuse at its ACL before reaching the trigger. This
                # proves login-level denial; owner SQL/COPY tests prove the guard.
                with pytest.raises(StoreError, match="protected forecast"):
                    store.store_forecast(test)
            with (
                engine.begin() as conn,
                pytest.raises(sa.exc.DBAPIError, match=refusal),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.insert(forecasts).values(
                        id=test.id,
                        station_id=test.station_id,
                        model_id=test.model_id,
                        issued_at=test.issued_at,
                        representation="members",
                        parameter="discharge",
                        units="m3/s",
                        data_use="expired_rating_test",
                        input_lineage=test.input_lineage.content,
                    )
                )
            if role == "sapphire_worker":
                with engine.connect() as conn:
                    assert PgForecastStore(conn).store_forecast(ordinary) == ordinary.id
                with (
                    engine.begin() as conn,
                    pytest.raises(sa.exc.DBAPIError, match="immutable"),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        sa.update(forecasts)
                        .where(forecasts.c.id == ordinary.id)
                        .values(data_use="expired_rating_test")
                    )
        finally:
            engine.dispose()
