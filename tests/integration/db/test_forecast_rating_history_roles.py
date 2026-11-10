from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.store.forecast_store import PgForecastStore
from tests.integration.db.test_role_bootstrap import (
    _RoleBootstrapHarness,
)
from tests.integration.db.test_role_bootstrap import (
    role_harness as role_harness,
)
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast

_ENTRY = "public.forecast_rating_input_history(uuid)"
_HELPER = "public.rating_history_json_nodes(text,integer)"
_CACHED_HELPER = "public.rating_history_sample_json_nodes(text,integer,text)"
_ROLES = (
    "sapphire_api",
    "sapphire_worker",
    "sapphire_operator",
    "sapphire_publication_health",
)


def _bootstrap(harness: _RoleBootstrapHarness) -> None:
    result = harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr


def _seed(conn: sa.Connection) -> OperationalForecast:
    _, native = native_pair(conn)
    PgForecastStore(
        conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
    ).store_forecast(native)
    return native


def _projection(conn: sa.Connection, forecast_id: object) -> dict:
    return conn.execute(
        sa.text("SELECT public.forecast_rating_input_history(:id)"),
        {"id": forecast_id},
    ).scalar_one()


def _assert_acl(conn: sa.Connection) -> None:
    signatures = [_ENTRY, _HELPER]
    if conn.scalar(sa.text("SELECT to_regprocedure(:sig)"), {"sig": _CACHED_HELPER}):
        signatures.append(_CACHED_HELPER)
    for signature in signatures:
        assert not conn.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc p, "
                "LATERAL aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) a "
                "WHERE p.oid=to_regprocedure(:signature) AND a.grantee=0 "
                "AND a.privilege_type='EXECUTE')"
            ),
            {"signature": signature},
        )
        for role in _ROLES:
            assert conn.scalar(
                sa.text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                {"role": role, "signature": signature},
            ) is (signature == _ENTRY and role == "sapphire_api")


def test_api_reads_nonempty_projection_but_not_raw_evidence(
    role_harness: _RoleBootstrapHarness,
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn, conn.begin():
        native = _seed(conn)
        expected = _projection(conn, native.id)
        assert expected["forecast_id"] == str(native.id)
        assert len(expected["samples"]) == 1
        assert expected["samples"][0]["discharge"] is not None
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert _projection(conn, native.id) == expected
        for query in (
            "SELECT input_lineage FROM public.forecasts",
            "SELECT content FROM public.rating_derived_discharge",
            "SELECT source_content FROM public.native_measurement_captures",
            "SELECT evidence_content FROM public.native_measurement_captures",
        ):
            with (
                pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text(query))


@pytest.mark.parametrize("role", _ROLES[1:])
def test_non_api_roles_cannot_execute(
    role_harness: _RoleBootstrapHarness, role: str
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn, conn.begin():
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        with (
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
            conn.begin_nested(),
        ):
            _projection(conn, uuid4())


@pytest.mark.parametrize("role", _ROLES)
def test_helper_is_private_and_runtime_cannot_replace_or_reach_owner(
    role_harness: _RoleBootstrapHarness, role: str
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn, conn.begin():
        owner, definer, settings = conn.execute(
            sa.text(
                "SELECT pg_get_userbyid(proowner), prosecdef, proconfig "
                "FROM pg_proc WHERE oid=to_regprocedure(:signature)"
            ),
            {"signature": _ENTRY},
        ).one()
        assert owner not in _ROLES
        assert definer
        assert "search_path=pg_catalog, pg_temp" in settings or (
            "search_path=pg_catalog,pg_temp" in settings
        )
        assert "TimeZone=UTC" in settings
        assert not conn.scalar(
            sa.text("SELECT pg_has_role(:role,:owner,'MEMBER')"),
            {"role": role, "owner": owner},
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT prosecdef FROM pg_proc WHERE oid=to_regprocedure(:sig)"
                ),
                {"sig": _HELPER},
            )
            is False
        )
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        for query in (
            "SELECT public.rating_history_json_nodes('{}',10)",
            "SELECT public.rating_history_sample_json_nodes('{}',10,NULL)",
            "CREATE OR REPLACE FUNCTION public.forecast_rating_input_history"
            "(identity uuid) "
            "RETURNS jsonb LANGUAGE sql AS 'SELECT NULL::jsonb'",
            f"ALTER FUNCTION {_ENTRY} OWNER TO {role}",
        ):
            with (
                pytest.raises(
                    sa.exc.ProgrammingError, match="permission denied|must be owner"
                ),
                conn.begin_nested(),
            ):
                conn.execute(sa.text(query))


def test_temp_relation_and_helper_shadows_do_not_change_projection(
    role_harness: _RoleBootstrapHarness,
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.connect() as conn, conn.begin():
        native = _seed(conn)
        expected = _projection(conn, native.id)
        for table in (
            "forecasts",
            "stations",
            "rating_derived_discharge",
            "native_measurement_captures",
        ):
            conn.execute(
                sa.text(f"CREATE TEMP TABLE {table} (poison text) ON COMMIT DROP")
            )
        conn.execute(
            sa.text(
                "CREATE FUNCTION pg_temp.rating_history_json_nodes(text,integer) "
                "RETURNS integer LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'shadow helper called'; END $$"
            )
        )
        conn.execute(sa.text("SET LOCAL search_path=pg_temp,public,pg_catalog"))
        conn.execute(sa.text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert _projection(conn, native.id) == expected


def test_bootstrap_repairs_dirty_acl_and_is_repeatable(
    role_harness: _RoleBootstrapHarness,
) -> None:
    _bootstrap(role_harness)
    with role_harness.owner_engine.begin() as conn:
        for signature in (_ENTRY, _HELPER, _CACHED_HELPER):
            conn.execute(
                sa.text(
                    f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC, "
                    + ", ".join(_ROLES)
                )
            )
    for _ in range(2):
        _bootstrap(role_harness)
        with role_harness.owner_engine.connect() as conn:
            _assert_acl(conn)


def test_upgrade_downgrade_reupgrade_preserves_frozen_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer(
        "postgis/postgis:16-3.4", username="test", password="test", dbname="sapphire"
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0082")
        engine = sa.create_engine(url)
        try:
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("CREATE DATABASE prefect"))
            harness = _RoleBootstrapHarness(postgres, engine)
            _bootstrap(harness)
            with engine.begin() as conn:
                native = _seed(conn)
                old_reader = conn.scalar(
                    sa.text(
                        "SELECT pg_get_functiondef("
                        "'public.native_rating_lineage(uuid,boolean)'::regprocedure)"
                    )
                )
                evidence = conn.execute(
                    sa.text(
                        "SELECT fingerprint,content FROM "
                        "public.rating_derived_discharge "
                        "ORDER BY fingerprint"
                    )
                ).all()
            command.upgrade(config, "0083")
            _bootstrap(harness)
            with engine.connect() as conn:
                expected = _projection(conn, native.id)
                assert expected["samples"]
                _assert_acl(conn)
                original_projection = conn.scalar(
                    sa.text("SELECT pg_get_functiondef(to_regprocedure(:sig))"),
                    {"sig": _ENTRY},
                )
            command.upgrade(config, "0084")
            _bootstrap(harness)
            with engine.connect() as conn:
                assert _projection(conn, native.id) == expected
                _assert_acl(conn)
            command.downgrade(config, "0083")
            with engine.connect() as conn:
                assert (
                    conn.scalar(
                        sa.text("SELECT pg_get_functiondef(to_regprocedure(:sig))"),
                        {"sig": _ENTRY},
                    )
                    == original_projection
                )
                assert (
                    conn.scalar(
                        sa.text("SELECT to_regprocedure(:sig)"), {"sig": _CACHED_HELPER}
                    )
                    is None
                )
                assert _projection(conn, native.id) == expected
                _assert_acl(conn)
            command.upgrade(config, "0084")
            with engine.connect() as conn:
                assert _projection(conn, native.id) == expected
                _assert_acl(conn)
            command.downgrade(config, "0082")
            _bootstrap(harness)
            with engine.connect() as conn:
                for signature in (_ENTRY, _HELPER):
                    assert (
                        conn.scalar(
                            sa.text("SELECT to_regprocedure(:sig)"), {"sig": signature}
                        )
                        is None
                    )
                assert (
                    conn.scalar(
                        sa.text(
                            "SELECT pg_get_functiondef("
                            "'public.native_rating_lineage(uuid,boolean)'::regprocedure)"
                        )
                    )
                    == old_reader
                )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT fingerprint,content FROM "
                            "public.rating_derived_discharge "
                            "ORDER BY fingerprint"
                        )
                    ).all()
                    == evidence
                )
            command.upgrade(config, "0083")
            _bootstrap(harness)
            _bootstrap(harness)
            with engine.connect() as conn:
                _assert_acl(conn)
                assert _projection(conn, native.id) == expected
        finally:
            engine.dispose()
