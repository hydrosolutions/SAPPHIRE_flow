"""Plan 399 T4 — the WarmStartRecord invariants.

An unexplained NULL is the failure this record type exists to prevent: UNKNOWN
and known-absent are different states, and conflating them is how provenance
becomes unrecoverable (§ 5a). The DB-level behaviour — RESTRICT on the donor,
survival across supersession — is covered in
`tests/integration/store/test_model_artifact_warm_start.py`.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from sapphire_flow.store.model_artifact_warm_start import WarmStartRecord
from sapphire_flow.types.ids import ArtifactId

_CHILD = ArtifactId(UUID(int=1))
_BASE = ArtifactId(UUID(int=2))


class TestWarmStartRecordInvariants:
    def test_a_fully_known_donor_is_accepted(self) -> None:
        record = WarmStartRecord(
            artifact_id=_CHILD,
            base_artifact_id=_BASE,
            run_config={"finetuning": {"strategy": "last_layer"}},
            base_config_path="models/aquacast/configs/cmal_small.yaml",
            base_config_sha256="a" * 64,
            base_params_path="runs/2026-09-26/params.json",
        )
        assert record.base_artifact_id == _BASE
        assert record.run_config["finetuning"]["strategy"] == "last_layer"

    def test_an_unexplained_null_config_path_is_refused(self) -> None:
        with pytest.raises(
            ValueError, match="base_config_path is NULL without a reason"
        ):
            WarmStartRecord(
                artifact_id=_CHILD,
                base_artifact_id=_BASE,
                run_config={},
                base_config_path=None,
                base_params_path="p",
            )

    def test_an_unexplained_null_params_path_is_refused(self) -> None:
        with pytest.raises(
            ValueError, match="base_params_path is NULL without a reason"
        ):
            WarmStartRecord(
                artifact_id=_CHILD,
                base_artifact_id=_BASE,
                run_config={},
                base_config_path="c",
                base_config_sha256="b" * 64,
                base_params_path=None,
            )

    def test_a_null_path_with_a_reason_is_accepted(self) -> None:
        """🔑 `cmal_small`'s expected shape: imported, so its external training
        params are not recoverable from SAP3 — UNKNOWN, not known-absent."""
        record = WarmStartRecord(
            artifact_id=_CHILD,
            base_artifact_id=_BASE,
            run_config={},
            base_config_path="models/aquacast/configs/cmal_small.yaml",
            base_config_sha256="c" * 64,
            base_params_path=None,
            base_params_unknown_reason=(
                "donor was imported, not trained by SAP3; its external training "
                "params are not recoverable here"
            ),
        )
        assert record.base_params_path is None
        assert record.base_params_unknown_reason
