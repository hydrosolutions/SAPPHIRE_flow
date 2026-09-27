from __future__ import annotations

import sys
import types
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from sapphire_flow.store.model_artifact_warm_start import resolve_donor_params
from sapphire_flow.types.ids import ArtifactId


class TestResolveDonorParams:
    def test_non_mapping_run_config_is_reported_as_unknown(self) -> None:
        fake_provenance = types.ModuleType("sapphire_flow.store.model_artifact_provenance")
        fake_provenance.fetch_artifact_provenance = lambda conn, artifact_id: None
        sys.modules["sapphire_flow.store.model_artifact_provenance"] = fake_provenance

        inherited = SimpleNamespace(run_config=[])
        with patch(
            "sapphire_flow.store.model_artifact_warm_start.fetch_warm_start",
            return_value=inherited,
        ):
            path, reason = resolve_donor_params(object(), ArtifactId(UUID(int=1)))

        assert path is None
        assert reason is not None
        assert "non-mapping run_config" in reason
        assert "Recorded as UNKNOWN" in reason

    def test_empty_mapping_run_config_stays_known_empty(self) -> None:
        fake_provenance = types.ModuleType("sapphire_flow.store.model_artifact_provenance")
        fake_provenance.fetch_artifact_provenance = lambda conn, artifact_id: None
        sys.modules["sapphire_flow.store.model_artifact_provenance"] = fake_provenance

        inherited = SimpleNamespace(run_config=MappingProxyType({}))
        with patch(
            "sapphire_flow.store.model_artifact_warm_start.fetch_warm_start",
            return_value=inherited,
        ):
            path, reason = resolve_donor_params(object(), ArtifactId(UUID(int=1)))

        assert path is None
        assert reason is not None
        assert "NO configuration overrides" in reason
