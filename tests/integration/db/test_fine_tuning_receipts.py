from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.fine_tuning import (
    PgFineTuningReceipts,
    fitting_lock,
    lock_decision,
)
from tests.unit.services.test_fine_tuning_review import candidate

if TYPE_CHECKING:
    import sqlalchemy as sa


class TestFineTuningReceipts:
    def test_latest_receipt_and_duplicate_fit_refusal(
        self, db_connection: sa.Connection
    ) -> None:
        receipt = candidate().receipt
        receipts = PgFineTuningReceipts(db_connection)
        receipts.append(receipt, receipt.request.evaluation_end)
        with pytest.raises(ConfigurationError, match="retained candidate"):
            receipts.refuse_retained_candidate(receipt.request)
        updated = replace(receipt, evaluation_json='{"state":"incomplete"}')
        receipts.append(updated, receipt.request.evaluation_end)
        assert receipts.latest(receipt.artifact_id).sha256 == updated.sha256

    def test_receipt_write_rolls_back_with_transaction(
        self, db_engine: sa.Engine
    ) -> None:
        receipt = candidate().receipt
        with (
            pytest.raises(RuntimeError, match="later write failed"),
            db_engine.begin() as conn,
        ):
            PgFineTuningReceipts(conn).append(receipt, receipt.request.evaluation_end)
            raise RuntimeError("later write failed")
        with (
            db_engine.connect() as conn,
            pytest.raises(ConfigurationError, match="no fine-tuning receipt"),
        ):
            PgFineTuningReceipts(conn).latest(receipt.artifact_id)

    def test_fit_excludes_other_fit_and_admin_until_release(
        self, db_engine: sa.Engine
    ) -> None:
        receipt = candidate().receipt
        with (
            pytest.raises(RuntimeError, match="fit failed"),
            fitting_lock(db_engine, receipt.request, receipt.tenant_id),
        ):
            with (
                pytest.raises(ConfigurationError, match="owns this model/group"),
                fitting_lock(db_engine, receipt.request, receipt.tenant_id),
            ):
                pytest.fail("concurrent fit acquired the lock")
            with (
                db_engine.begin() as conn,
                pytest.raises(ConfigurationError, match="owns this model/group"),
            ):
                lock_decision(conn, receipt)
            raise RuntimeError("fit failed")
        with db_engine.begin() as conn:
            lock_decision(conn, receipt)
