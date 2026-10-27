from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import tenants


def test_committed_rows_do_not_enter_rollback_database(
    isolated_committed_db_engine: sa.Engine, db_connection: sa.Connection
) -> None:
    tenant_id = uuid4()
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            sa.insert(tenants).values(
                id=tenant_id, code=str(tenant_id), name="Committed isolation sentinel"
            )
        )
    query = sa.select(tenants.c.id).where(tenants.c.id == tenant_id)
    with isolated_committed_db_engine.connect() as reader:
        assert reader.execute(query).scalar_one() == tenant_id
    assert db_connection.execute(query).scalar_one_or_none() is None
