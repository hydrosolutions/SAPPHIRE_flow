from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
from sapphire_flow.flows import ingest_observations as flow
from sapphire_flow.services.rating_inputs import NativeRatingInputs
from sapphire_flow.services.rating_reconciliation import RatingReconciliationState
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.rating_reference import canonical_content
from tests.unit.services.test_provisional_discharge import NOW, inputs


class RecordingWriter:
    def __init__(self, fail_first: bool = False) -> None:
        self.windows: list[tuple[object, object]] = []
        self.fail_first = fail_first

    def reconcile_chunk(
        self, binding: object, dhm: object, *, start: object, end: object, at: object
    ) -> tuple:
        self.windows.append((start, end))
        if self.fail_first and len(self.windows) == 1:
            raise ValueError("held fixture chunk")
        return ()


def config() -> tuple[SimpleNamespace, DhmConfig]:
    _, _, _, reference = inputs()
    binding = ProvisionalRatingConfig(
        tenant_id=reference.tenant_id,
        station_id=reference.station_id,
        station_code="450",
        model_ids=("cmal_small",),
        reference_content=canonical_content(asdict(reference)),
        reconciliation_days=30,
        custody_start=NOW - timedelta(days=31),
        custody_end=NOW + timedelta(days=1),
        custody_reference="fixture native source",
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )
    return SimpleNamespace(provisional_ratings=(binding,)), dhm


class TestNativeReconciliationFlow:
    def test_default_off_does_not_even_load_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def forbidden() -> None:
            raise AssertionError("adapter config must not load")

        monkeypatch.setattr(flow, "_load_adapter_config", forbidden)
        assert flow._reconcile_native_ratings_task.fn(None, None, NOW) == ()

    def test_backfill_has_no_provider_or_ordinary_stages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured, dhm = config()
        monkeypatch.setattr(flow, "_load_adapter_config", lambda: dhm)
        monkeypatch.setattr(
            flow,
            "_reconcile_native_ratings_task",
            flow._reconcile_native_ratings_task.fn,
        )
        writer = RecordingWriter()
        result = flow.ingest_observations_flow.fn(
            station_store=object(),
            adapter=object(),
            rating_derived_store=writer,
            deployment_config=configured,
            clock=lambda: NOW,
            rating_backfill_station_ids=[
                str(configured.provisional_ratings[0].station_id)
            ],
            rating_backfill_start=NOW - timedelta(days=1),
            rating_backfill_end=NOW,
        )
        assert (
            result.stations_polled
            == result.observations_fetched
            == result.observations_stored
            == 0
        )
        assert len(writer.windows) == 4
        assert result.rating_results[0].state is RatingReconciliationState.COMPLETE

    def test_recurring_revisits_full_lookback_after_partial_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured, dhm = config()
        monkeypatch.setattr(flow, "_load_adapter_config", lambda: dhm)
        writer = RecordingWriter(fail_first=True)
        result = flow._reconcile_native_ratings_task.fn(writer, configured, NOW)
        assert len(writer.windows) == 120
        assert writer.windows[0][0] == NOW - timedelta(days=30)
        assert writer.windows[-1][1] == NOW
        assert (
            result[0].failed_chunks == 1
            and result[0].state is RatingReconciliationState.HELD
        )

    def test_missing_native_adapter_is_station_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured, _ = config()
        monkeypatch.setattr(flow, "_load_adapter_config", lambda: object())
        result = flow._reconcile_native_ratings_task.fn(
            RecordingWriter(), configured, NOW
        )
        assert result[0].state is RatingReconciliationState.HELD

    def test_native_model_longer_than_reconciliation_is_refused(self) -> None:
        from sapphire_flow.exceptions import ConfigurationError

        configured, dhm = config()
        binding = configured.provisional_ratings[0].to_domain()
        reader = NativeRatingInputs(reader=object(), bindings=(binding,), dhm=dhm)
        with pytest.raises(
            ConfigurationError, match="shorter than native model lookback"
        ):
            reader.read(
                binding.station_id,
                ModelId("cmal_small"),
                start=NOW - timedelta(days=31),
                end=NOW,
                at=NOW,
            )


@pytest.mark.parametrize("days", [1, 366])
def test_recurring_nonmidnight_retains_complete_history_and_today(
    monkeypatch: pytest.MonkeyPatch, days: int
) -> None:
    configured, dhm = config()
    configured.provisional_ratings = (
        configured.provisional_ratings[0].model_copy(
            update={"reconciliation_days": days}
        ),
    )
    monkeypatch.setattr(flow, "_load_adapter_config", lambda: dhm)
    writer = RecordingWriter()
    at = NOW + timedelta(hours=1)
    result = flow._reconcile_native_ratings_task.fn(writer, configured, at)
    assert writer.windows[0][0] == NOW - timedelta(days=days)
    assert writer.windows[-1] == (NOW, at)
    assert len(writer.windows) == days * 4 + 1
    assert result[0].state is RatingReconciliationState.COMPLETE


def test_bad_window_is_per_binding_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    configured, dhm = config()
    configured.provisional_ratings = configured.provisional_ratings * 2
    monkeypatch.setattr(flow, "_load_adapter_config", lambda: dhm)
    result = flow._reconcile_native_ratings_task.fn(
        RecordingWriter(), configured, NOW, start=NOW - timedelta(days=367), end=NOW
    )
    assert len(result) == 2
    assert all(part.state is RatingReconciliationState.HELD for part in result)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("has_observations", [False, True])
def test_recurring_call_sites_are_truly_default_off(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, has_observations: bool
) -> None:
    from sapphire_flow.config.deployment import DeploymentConfig
    from tests.conftest import make_station_config
    from tests.fakes.fake_adapters import FakeStationDataSource
    from tests.fakes.fake_stores import (
        FakeClimBaselineStore,
        FakeObservationStore,
        FakeStationStore,
        FakeTenantStore,
    )
    from tests.unit.flows.test_ingest_observations import _NOW, _QC_RULES, _make_obs

    for name in (
        "_fetch_observations_task",
        "_store_raw_task",
        "_run_qc_task",
        "_derive_calculated_task",
    ):
        monkeypatch.setattr(flow, name, getattr(flow, name).fn)
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
    configured, _ = config()
    deployment = DeploymentConfig(
        max_retention_days=600,
        enable_observation_alerts=False,
        provisional_ratings=configured.provisional_ratings if enabled else (),
    )
    calls = []

    def reconcile(*args: object, **kwargs: object) -> tuple:
        if not enabled:
            raise AssertionError("disabled native reconciliation task invoked")
        calls.append(args)
        return ()

    monkeypatch.setattr(flow, "_reconcile_native_ratings_task", reconcile)
    station = make_station_config()
    stations = FakeStationStore()
    stations.store_station(station)
    result = flow.ingest_observations_flow.fn(
        station_store=stations,
        tenant_store=FakeTenantStore(),
        obs_store=FakeObservationStore(),
        baseline_store=FakeClimBaselineStore(),
        adapter=FakeStationDataSource(
            [_make_obs(station.id)] if has_observations else []
        ),
        qc_rules=_QC_RULES,
        clock=lambda: _NOW,
        deployment_config=deployment,
    )
    assert len(calls) == int(enabled)
    assert result.observations_stored == int(has_observations)
    assert result.rating_results == ()
