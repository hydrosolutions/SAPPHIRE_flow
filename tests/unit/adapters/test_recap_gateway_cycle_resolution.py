"""Plan 082 Task 2B: resolve the latest available Gateway IFS cycle.

No Gateway health API (Non-goals) — probes candidate run_date/run_hour via the
real forecast endpoint, treating ``source_data_missing`` as candidate-
unavailable and walking back in ``cadence_hours`` steps until either data is
found or ``max_age_hours`` is exhausted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sapphire_flow.adapters.recap_gateway import resolve_latest_cycle
from sapphire_flow.types.datetime import ensure_utc

_NOW = ensure_utc(datetime(2026, 7, 16, 13, 0, tzinfo=UTC))  # not on a 6h boundary


class _FakeClientError(Exception):
    def __init__(self, message: str, **attrs: object) -> None:
        super().__init__(message)
        for key, value in attrs.items():
            setattr(self, key, value)


class _FlakyEcmwf:
    """Raises source_data_missing for the newest N candidates, then succeeds."""

    def __init__(self, *, missing_count: int) -> None:
        self.missing_count = missing_count
        self.calls: list[dict[str, object]] = []

    def ifs_forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if len(self.calls) <= self.missing_count:
            raise _FakeClientError("no data yet", code="source_data_missing")
        return object()


class _AlwaysMissingEcmwf:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ifs_forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        raise _FakeClientError("no data ever", code="source_data_missing")


class _FakeClient:
    def __init__(self, ecmwf: object) -> None:
        self.ecmwf = ecmwf


class TestResolveLatestCycle:
    def test_falls_back_to_first_available_older_cycle(self) -> None:
        ecmwf = _FlakyEcmwf(missing_count=2)
        client = _FakeClient(ecmwf)

        resolved = resolve_latest_cycle(
            client,  # type: ignore[arg-type]
            hru_code="hru_dhm_west_v001",
            now=_NOW,
            max_age_hours=24.0,
        )

        assert resolved is not None
        assert len(ecmwf.calls) == 3
        # Candidates walk back in 6h steps from the cadence-floored now.
        floored = ensure_utc(datetime(2026, 7, 16, 12, 0, tzinfo=UTC))
        assert resolved == ensure_utc(floored - timedelta(hours=12))

    def test_all_missing_within_max_age_returns_none(self) -> None:
        ecmwf = _AlwaysMissingEcmwf()
        client = _FakeClient(ecmwf)

        resolved = resolve_latest_cycle(
            client,  # type: ignore[arg-type]
            hru_code="hru_dhm_west_v001",
            now=_NOW,
            max_age_hours=12.0,
        )

        assert resolved is None
        # 12h / 6h cadence + 1 (newest) = 3 candidates probed.
        assert len(ecmwf.calls) == 3

    def test_first_candidate_available_short_circuits(self) -> None:
        ecmwf = _FlakyEcmwf(missing_count=0)
        client = _FakeClient(ecmwf)

        resolved = resolve_latest_cycle(
            client,  # type: ignore[arg-type]
            hru_code="hru_dhm_west_v001",
            now=_NOW,
            max_age_hours=24.0,
        )

        assert resolved is not None
        assert len(ecmwf.calls) == 1
