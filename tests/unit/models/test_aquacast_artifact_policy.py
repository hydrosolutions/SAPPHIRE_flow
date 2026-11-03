"""Real Aquacast artifact boundary; requires the aquacast extra."""

from __future__ import annotations

import io
import pickle
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aquacast.serialization import ModelBundle

pytest.importorskip("aquacast", reason="needs the `aquacast` extra")


@pytest.fixture(autouse=True)
def isolated_artifact_registry() -> Iterator[None]:
    import torch
    from aquacast.data.protocols import FeatureLayout
    from aquacast.domain import FeatureStats, NormalizationStats, Resolution
    from aquacast.models.components.protocols import SharedDims
    from aquacast.models.config import ComponentConfig, DailyEncoderConfig, ModelConfig
    from aquacast.serialization import BundleMeta, FeatureManifest, ModelBundle
    from aquacast.training.trainers.swag import SWAGPosterior

    scoped_types = {
        ModelConfig,
        ComponentConfig,
        NormalizationStats,
        FeatureStats,
        Resolution,
        FeatureManifest,
        ModelBundle,
        BundleMeta,
        SharedDims,
        FeatureLayout,
        DailyEncoderConfig,
        SWAGPosterior,
    }
    initial = set(torch.serialization.get_safe_globals())
    initial_types = {
        entry[0] if isinstance(entry, tuple) else entry for entry in initial
    }
    assert scoped_types.isdisjoint(initial_types)
    yield
    final = set(torch.serialization.get_safe_globals())
    final_types = {entry[0] if isinstance(entry, tuple) else entry for entry in final}
    assert scoped_types.isdisjoint(final_types)
    assert final == initial


@pytest.fixture
def portable_bundle() -> ModelBundle:
    import torch
    from aquacast.domain import FeatureStats, NormalizationStats, Resolution
    from aquacast.models.components.protocols import SharedDims
    from aquacast.models.config import ComponentConfig, ModelConfig
    from aquacast.serialization import BundleMeta, FeatureManifest, ModelBundle

    daily = Resolution.DAILY
    return ModelBundle(
        state_dict={"weight": torch.tensor([1.0, 2.0], dtype=torch.float64)},
        config=ModelConfig(
            tokenizer=ComponentConfig(type="linear"),
            backbone=ComponentConfig(type="lstm"),
            head=ComponentConfig(type="cmal"),
            hidden_size=2,
        ),
        normalization=NormalizationStats(
            dynamic={daily: {"discharge": FeatureStats(mean=0.0, std=1.0)}},
            static={"area": FeatureStats(mean=1.0, std=2.0)},
        ),
        features=FeatureManifest(
            dynamic={daily: ("discharge",)},
            static=("area",),
            targets={daily: ("discharge",)},
            layout={},
        ),
        meta=BundleMeta(
            aquacast_version="0.1.372",  # Synthetic legacy producer metadata.
            resolutions=(daily,),
            created_at="2026-01-01T00:00:00+00:00",
            train_basins=("fixture_station",),
        ),
        shared=SharedDims(
            hidden_size=2,
            dynamic_input_dim={daily: 1},
            past_dim={daily: 1},
            future_dim={daily: 0},
            static_dim=1,
            lookback={daily: 2},
            forecast={daily: 1},
            target_idx={daily: 0},
            n_targets={daily: 1},
        ),
    )


class _UnrelatedAllowedType:
    pass


class TestAquacastArtifactBoundary:
    def test_round_trip_preserves_cpu_weights_and_station_metadata(
        self, portable_bundle: ModelBundle
    ) -> None:
        import torch

        from sapphire_flow.models.aquacast import CmalSmall

        model = CmalSmall()
        restored = model.deserialize_artifact(model.serialize_artifact(portable_bundle))

        assert torch.equal(
            restored.state_dict["weight"], portable_bundle.state_dict["weight"]
        )
        assert restored.state_dict["weight"].dtype == torch.float64
        assert restored.state_dict["weight"].device.type == "cpu"
        assert restored.meta == portable_bundle.meta
        assert restored.config == portable_bundle.config
        assert restored.shared == portable_bundle.shared
        assert restored.normalization == portable_bundle.normalization
        assert restored.features == portable_bundle.features

    def test_real_loader_requires_restricted_cpu_restore(
        self, portable_bundle: ModelBundle
    ) -> None:
        import torch

        from sapphire_flow.models.aquacast import CmalSmall

        model = CmalSmall()
        raw = model.serialize_artifact(portable_bundle)
        with patch("torch.load", wraps=torch.load) as load:
            model.deserialize_artifact(raw)

        assert load.call_count == 1
        assert load.call_args.kwargs == {"weights_only": True, "map_location": "cpu"}

    def test_unknown_pickle_type_is_rejected_without_retry(self) -> None:
        import torch

        from sapphire_flow.models.aquacast import CmalSmall

        buffer = io.BytesIO()
        torch.save(SimpleNamespace(unapproved=True), buffer)
        model = CmalSmall()
        with (
            patch("torch.load", wraps=torch.load) as load,
            pytest.raises(pickle.UnpicklingError, match="Unsupported global"),
        ):
            model.deserialize_artifact(buffer.getvalue())

        assert load.call_count == 1

    def test_unrelated_existing_safe_globals_survive_success_and_failure(
        self, portable_bundle: ModelBundle
    ) -> None:
        import torch

        from sapphire_flow.models.aquacast import CmalSmall

        model = CmalSmall()
        valid = model.serialize_artifact(portable_bundle)
        invalid = io.BytesIO()
        torch.save(SimpleNamespace(unapproved=True), invalid)
        with torch.serialization.safe_globals([_UnrelatedAllowedType]):
            initial = set(torch.serialization.get_safe_globals())
            model.deserialize_artifact(valid)
            assert set(torch.serialization.get_safe_globals()) == initial
            with pytest.raises(pickle.UnpicklingError, match="Unsupported global"):
                model.deserialize_artifact(invalid.getvalue())
            assert set(torch.serialization.get_safe_globals()) == initial

    def test_export_removes_only_known_donor_path_without_mutating_input(
        self, portable_bundle: ModelBundle
    ) -> None:
        from aquacast.models.config import DailyEncoderConfig

        from sapphire_flow.models.aquacast import CmalSmall

        encoder = DailyEncoderConfig(checkpoint="training-only/donor.pt", embed_dim=4)
        original = portable_bundle._replace(
            config=replace(portable_bundle.config, daily_encoder=encoder)
        )
        model = CmalSmall()
        restored = model.deserialize_artifact(model.serialize_artifact(original))

        assert original.config.daily_encoder == encoder
        assert restored.config == replace(
            original.config, daily_encoder=replace(encoder, checkpoint="")
        )
        assert restored.meta == original.meta
