from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlalchemy as sa

from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import ArtifactScope
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.model import ModelRecord

_CREATED_AT = UtcDatetime(datetime(2024, 1, 1, tzinfo=UTC))


class TestPgModelStore:
    def test_register_and_fetch(self, db_connection: sa.Connection) -> None:
        store = PgModelStore(db_connection)
        record = ModelRecord(
            id=ModelId("linear_regression_v1"),
            display_name="Linear Regression v1",
            artifact_scope=ArtifactScope.STATION,
            description="Baseline linear regression model",
            created_at=_CREATED_AT,
        )
        store.register_model(record)

        result = store.fetch_model(ModelId("linear_regression_v1"))

        assert result is not None
        assert result.id == ModelId("linear_regression_v1")
        assert result.display_name == "Linear Regression v1"
        assert result.artifact_scope == ArtifactScope.STATION
        assert result.description == "Baseline linear regression model"

    def test_register_idempotent(self, db_connection: sa.Connection) -> None:
        store = PgModelStore(db_connection)
        record = ModelRecord(
            id=ModelId("idempotent_model"),
            display_name="Idempotent Model",
            artifact_scope=ArtifactScope.GROUP,
            description="Test idempotency",
            created_at=_CREATED_AT,
        )
        store.register_model(record)
        store.register_model(record)

        result = store.fetch_model(ModelId("idempotent_model"))
        assert result is not None

    def test_fetch_nonexistent(self, db_connection: sa.Connection) -> None:
        store = PgModelStore(db_connection)
        result = store.fetch_model(ModelId("does_not_exist"))
        assert result is None

    def test_fetch_all(self, db_connection: sa.Connection) -> None:
        store = PgModelStore(db_connection)
        record_a = ModelRecord(
            id=ModelId("model_alpha"),
            display_name="Model Alpha",
            artifact_scope=ArtifactScope.STATION,
            description="Alpha model",
            created_at=_CREATED_AT,
        )
        record_b = ModelRecord(
            id=ModelId("model_beta"),
            display_name="Model Beta",
            artifact_scope=ArtifactScope.GROUP,
            description="Beta model",
            created_at=_CREATED_AT,
        )
        store.register_model(record_a)
        store.register_model(record_b)

        results = store.fetch_all_models()
        ids = {r.id for r in results}
        assert ModelId("model_alpha") in ids
        assert ModelId("model_beta") in ids
