from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003

import polars as pl
import pytest

from sapphire_flow.adapters.lindas_rate_limiter import (
    LindasLimiterConfig,
    TokenBucketLindasLimiter,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.bafu_observation import BafuObservationRow
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import PipelineHealthStatus
from tests.fakes.fake_stores import FakePipelineHealthStore


def _import_flow_module() -> object:
    try:
        from sapphire_flow.flows import collect_bafu_observations

        return collect_bafu_observations
    except ImportError as exc:  # pragma: no cover - red-first guard
        pytest.fail(
            "sapphire_flow.flows.collect_bafu_observations does not exist yet "
            f"(T3/T4 not implemented): {exc}"
        )


def _make_config(**overrides: object) -> DeploymentConfig:
    defaults: dict[str, object] = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)  # type: ignore[arg-type]


def _row(
    gauge_code: str,
    lindas_kind: str = "river",
    parameter: str = "discharge",
    value: float = 12.3,
    measurement_time: datetime | None = None,
) -> BafuObservationRow:
    ts = measurement_time or datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
    return BafuObservationRow(
        gauge_code=gauge_code,
        lindas_kind=lindas_kind,  # type: ignore[arg-type]
        parameter=parameter,  # type: ignore[arg-type]
        value=value,
        measurement_time=ensure_utc(ts),
    )


class _FakeAdapter:
    def __init__(
        self,
        rows: list[BafuObservationRow] | Exception,
        raw_payload: dict[str, object] | None = None,
    ) -> None:
        self._rows = rows
        self._raw_payload = raw_payload or {"results": {"bindings": []}}
        self.calls = 0

    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, object]]:
        self.calls += 1
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows, self._raw_payload


class _NeverCalledAdapter:
    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, object]]:
        raise AssertionError("adapter must not be used when the archive path is unset")


class _ClockSpy:
    def __init__(self, value: datetime) -> None:
        self._value = ensure_utc(value)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self._value


def _coherent_limiter(*, max_retries: int = 2) -> TokenBucketLindasLimiter:
    """Limiter whose injected sleeper ADVANCES its injected clock.

    A no-op sleeper paired with the real clock is an incoherent time source:
    the limiter's wait budget drains while the bucket never refills, so a
    starved bucket — e.g. after a 429 drains it — can never be waited out.
    Production never sees this (`time.sleep` and `datetime.now` always agree);
    only the DI seam can. Here the sleep costs no real time but time still
    MOVES, which is what the limiter's arithmetic assumes.
    """
    now = [ensure_utc(datetime(2026, 8, 17, 8, 0, 0, tzinfo=UTC))]

    def clock() -> UtcDatetime:
        return now[0]

    def sleeper(seconds: float) -> None:
        now[0] = ensure_utc(now[0] + timedelta(seconds=seconds))

    return TokenBucketLindasLimiter(
        config=LindasLimiterConfig(max_attempts=max_retries + 1),
        clock=clock,
        sleeper=sleeper,
    )


class TestQuarantineGate:
    def test_blank_archive_path_is_noop(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=Path("  "))
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_NeverCalledAdapter(),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result == module._EMPTY_RESULT  # type: ignore[attr-defined]
        # No parquet/raw files anywhere under tmp_path.
        assert list(tmp_path.rglob("*")) == []

    def test_unset_archive_path_is_noop(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=None)
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_NeverCalledAdapter(),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result == module._EMPTY_RESULT  # type: ignore[attr-defined]


class TestModalTieBreakPicksTheOLDERSlot:
    """A 50/50 tie between two slots must resolve to the **older** one.

    At a real slot transition the network briefly splits: half the gauges have
    advanced, half have not. If the tie resolved to the NEWER slot, the
    collector would claim that slot's path while holding only half its data —
    and the later, complete response (whose modal is unambiguously that same
    newer slot) would find the path present and **dedup-skip permanently**.
    Resolving to the older slot means the half-advanced response lands under
    the slot it mostly represents, and the newer slot stays unclaimed until a
    response actually represents it.

    `min(...)` is what implements this; a `max(...)` mutant passes every other
    modal test in this file, which is why this one exists.
    """

    def test_equal_counts_resolve_to_the_older_slot(self) -> None:
        module = _import_flow_module()
        older = ensure_utc(datetime(2026, 7, 21, 15, 30, tzinfo=UTC))
        newer = ensure_utc(datetime(2026, 7, 21, 15, 40, tzinfo=UTC))
        # Exactly two gauges on each side — a true 50/50 split.
        rows = [
            _row("2135", measurement_time=older),
            _row("2200", measurement_time=older),
            _row("2300", measurement_time=newer),
            _row("2400", measurement_time=newer),
        ]

        assert module._modal_cycle_at(rows) == older  # noqa: SLF001

    def test_a_tie_then_a_complete_response_archives_both_slots(
        self, tmp_path: Path
    ) -> None:
        """The consequence of picking the older slot, exercised end-to-end.

        (An earlier version of this test asserted only that an all-newer
        response keys to the newer slot — which involves no tie at all and
        passes unchanged under the `min`->`max` mutant it claimed to guard.
        It proved nothing; this runs a real tie through the archive.)

        A half-advanced response must land under the OLDER slot, leaving the
        newer slot unclaimed so the complete response can still archive it.
        Under `max()` the tie would claim the newer slot early and the
        complete response would dedup-skip, losing it permanently.
        """
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        older = datetime(2026, 7, 21, 15, 30, tzinfo=UTC)
        newer = datetime(2026, 7, 21, 15, 40, tzinfo=UTC)

        # Mid-transition: two gauges advanced, two have not.
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row("2135", measurement_time=older),
                    _row("2200", measurement_time=older),
                    _row("2300", measurement_time=newer),
                    _row("2400", measurement_time=newer),
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 45, tzinfo=UTC)),
        )
        # Transition complete: the whole network is on the newer slot.
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row("2135", measurement_time=newer),
                    _row("2200", measurement_time=newer),
                    _row("2300", measurement_time=newer),
                    _row("2400", measurement_time=newer),
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 47, tzinfo=UTC)),
        )

        stems = sorted(p.name for p in tmp_path.rglob("*.parquet"))
        assert stems == [
            "obs-20260721T153000Z.parquet",
            "obs-20260721T154000Z.parquet",
        ], f"a tie must not consume the newer slot — got {stems}"


class TestCycleAtIsDataDerived:
    """Plan 176 D2: `cycle_at` is the response's MODAL `measurement_time`
    across distinct (gauge_code, lindas_kind) identities, truncated to the
    10-minute grid — never the clock. `run_at` is deliberately placed in a
    DIFFERENT clock hour/minute bucket than the data in every case below, so
    a clock-derived (or 5-minute-truncated) implementation cannot coincide
    with the expected answer by accident."""

    @pytest.mark.parametrize(
        ("data_minute", "expected_slot_minute"),
        [(0, 0), (7, 0), (9, 0), (10, 10), (17, 10), (59, 50)],
    )
    def test_exact_cycle_at_and_filename_from_modal_measurement_time(
        self, tmp_path: Path, data_minute: int, expected_slot_minute: int
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        ts = datetime(2026, 7, 21, 12, data_minute, tzinfo=UTC)
        run_at = datetime(2026, 7, 21, 13, 45, tzinfo=UTC)  # different hour AND minute
        rows = [_row("2135", measurement_time=ts), _row("2200", measurement_time=ts)]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(run_at),
        )
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        expected_cycle_at = ensure_utc(
            datetime(2026, 7, 21, 12, expected_slot_minute, tzinfo=UTC)
        )
        assert f"obs-{expected_cycle_at:%Y%m%dT%H%M%SZ}" == Path(parquet_files[0]).stem
        df = pl.read_parquet(parquet_files[0])
        assert df["cycle_at"].to_list()[0] == expected_cycle_at

    def test_minority_gauge_ahead_of_bulk_keys_to_the_bulks_slot(
        self, tmp_path: Path
    ) -> None:
        """D2's robustness fix (modal, not `max()`): proven RED against a
        `max()` mutant, which would key to the outlier's slot and make the
        real bulk response dedup-skip — losing most of the network's
        observations for that slot, silently."""
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        bulk_ts = datetime(2026, 7, 21, 12, 10, tzinfo=UTC)
        outlier_ts = datetime(2026, 7, 21, 12, 20, tzinfo=UTC)  # one slot AHEAD
        rows = [
            _row("2135", measurement_time=bulk_ts),
            _row("2200", measurement_time=bulk_ts),
            _row("2300", measurement_time=bulk_ts),
            _row("9999", measurement_time=outlier_ts),
        ]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 12, 25, tzinfo=UTC)),
        )
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        expected = ensure_utc(bulk_ts)
        assert f"obs-{expected:%Y%m%dT%H%M%SZ}" == Path(parquet_files[0]).stem

    def test_modal_is_identity_weighted_not_row_weighted(self, tmp_path: Path) -> None:
        """D2's mode is over DISTINCT (gauge_code, lindas_kind) identities,
        not raw rows: every fixture above gives each identity exactly one
        row, so a subtly wrong row-weighted mode (counting every row's
        measurement_time, not one entry per identity) would still pass by
        coincidence. Here the AHEAD identity carries THREE parameter rows
        (discharge/water_level/water_temperature, all at its own slot)
        while TWO SEPARATE bulk identities each carry only ONE row on the
        earlier slot — row-weighted counting would wrongly pick the ahead
        identity's slot (3 rows > 2 rows); identity-weighted counting
        correctly picks the bulk slot (2 identities > 1 identity)."""
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        bulk_ts = datetime(2026, 7, 21, 12, 10, tzinfo=UTC)
        ahead_ts = datetime(2026, 7, 21, 12, 20, tzinfo=UTC)  # one slot AHEAD
        rows = [
            _row("2135", parameter="discharge", measurement_time=bulk_ts),
            _row("2200", parameter="discharge", measurement_time=bulk_ts),
            _row("9999", parameter="discharge", measurement_time=ahead_ts),
            _row("9999", parameter="water_level", measurement_time=ahead_ts),
            _row("9999", parameter="water_temperature", measurement_time=ahead_ts),
        ]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 12, 25, tzinfo=UTC)),
        )
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        expected = ensure_utc(bulk_ts)
        assert f"obs-{expected:%Y%m%dT%H%M%SZ}" == Path(parquet_files[0]).stem


class TestClockDerivedKeyIsDead:
    """Pinned-clock proof that the key is data-derived, not clock-derived
    (Plan 176 D2/T3). Neither test can pass against a clock-based
    `cycle_at` — "different clock times" alone is not a sufficient kill
    because both could still fall inside the same hour."""

    def test_identical_run_at_different_data_slots_writes_two_archives(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        run_at = datetime(2026, 7, 21, 12, 45, tzinfo=UTC)  # SAME clock both runs

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row(
                        "2135",
                        measurement_time=datetime(2026, 7, 21, 12, 7, tzinfo=UTC),
                    )
                ]
            ),
            clock=_ClockSpy(run_at),
        )
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row(
                        "2135",
                        measurement_time=datetime(2026, 7, 21, 12, 17, tzinfo=UTC),
                    )
                ]
            ),
            clock=_ClockSpy(run_at),
        )
        assert len(list(tmp_path.rglob("*.parquet"))) == 2

    def test_different_clock_hours_same_data_slot_dedups_to_one_archive(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        ts = datetime(2026, 7, 21, 12, 7, tzinfo=UTC)  # SAME data both runs

        first_adapter = _FakeAdapter([_row("2135", measurement_time=ts)])
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=first_adapter,
            clock=_ClockSpy(datetime(2026, 7, 21, 12, 9, tzinfo=UTC)),
        )
        second_adapter = _FakeAdapter([_row("2135", measurement_time=ts)])
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=second_adapter,
            clock=_ClockSpy(datetime(2026, 7, 21, 13, 3, tzinfo=UTC)),  # DIFFERENT HOUR
        )
        assert len(list(tmp_path.rglob("*.parquet"))) == 1
        # D2/D3: dedup happens AFTER the fetch — a data-derived key cannot be
        # known without fetching first.
        assert second_adapter.calls == 1


class TestFetchBeforeDedup:
    def test_same_slot_retry_fetches_but_writes_zero_new_files(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        rows = [_row("2135"), _row("3001", lindas_kind="lake", parameter="water_level")]

        first = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 1, tzinfo=UTC)),
        )
        parquet_files_after_first = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files_after_first) == 1
        assert first.row_count == 2

        # A poll a few minutes later sees the SAME data (no slot advance
        # yet) — must still fetch (D2/D3: the key cannot be known without
        # fetching) and must dedup-skip the WRITE.
        second_adapter = _FakeAdapter(rows)
        second = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=second_adapter,
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 4, tzinfo=UTC)),
        )
        assert list(tmp_path.rglob("*.parquet")) == parquet_files_after_first
        assert second_adapter.calls == 1  # fetched — dedup is post-fetch now
        assert second.row_count == 0  # nothing NEW archived this run
        assert second.dedup_skipped is True

    def test_slot_advance_writes_a_new_snapshot(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row(
                        "2135",
                        measurement_time=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
                    )
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 1, tzinfo=UTC)),
        )
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row(
                        "2135",
                        measurement_time=datetime(2026, 7, 21, 10, 10, tzinfo=UTC),
                    )
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 11, tzinfo=UTC)),
        )
        assert len(list(tmp_path.rglob("*.parquet"))) == 2


class TestHeartbeatOnDedupSkip:
    """Plan 176 D3 — the trap inside D2: a dedup skip must still compute
    freshness and append a heartbeat using `run_at`, or the freshness gate
    becomes unreachable after the first archived copy of a frozen slot."""

    def test_later_same_slot_fresh_poll_refreshes_heartbeat_without_writing(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        ts = datetime(2026, 7, 21, 12, 7, tzinfo=UTC)
        rows = [_row("2135", measurement_time=ts)]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 12, 9, tzinfo=UTC)),
            pipeline_health_store=health_store,
        )
        parquet_before = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_before) == 1

        second_run_at = datetime(2026, 7, 21, 12, 13, tzinfo=UTC)
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(second_run_at),
            pipeline_health_store=health_store,
        )
        assert result.dedup_skipped is True
        assert list(tmp_path.rglob("*.parquet")) == parquet_before  # no new files

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 2  # one per run, including the skip
        latest = records[-1]
        assert latest.status is PipelineHealthStatus.OK
        assert latest.checked_at == ensure_utc(second_run_at)
        assert latest.detail["row_count"] == 1

    def test_later_same_slot_frozen_poll_emits_stale_without_writing(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        ts = datetime(2026, 7, 21, 12, 7, tzinfo=UTC)
        rows = [_row("2135", measurement_time=ts)]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 12, 9, tzinfo=UTC)),
            pipeline_health_store=health_store,
        )
        parquet_before = list(tmp_path.rglob("*.parquet"))

        # Poll well past the module's own staleness threshold — the network
        # is frozen at the same slot (no advance), so this run STILL
        # dedup-skips the write while the heartbeat must go CRITICAL.
        stale_threshold: timedelta = module._STALE_MEASUREMENT_THRESHOLD  # type: ignore[attr-defined]
        frozen_run_at = ensure_utc(ts + stale_threshold + timedelta(minutes=5))
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(frozen_run_at),
            pipeline_health_store=health_store,
        )
        assert result.dedup_skipped is True
        assert list(tmp_path.rglob("*.parquet")) == parquet_before  # no new files

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        latest = records[-1]
        assert latest.status is PipelineHealthStatus.CRITICAL
        assert latest.detail["error_type"] == "stale_measurement_time"
        assert latest.checked_at == frozen_run_at


class TestRestatement:
    """Plan 176 D2 narrows Plan 136's restatement guarantee: a correction is
    preserved only if the NETWORK SLOT advances between the two fetches —
    which, at a ≤4 min poll ceiling against a 10-minute grid, it essentially
    always will before a correction lands. This test locks the realistic
    whole-graph case: the corrected gauge keeps its own timestamp while the
    MODAL network slot advances (the other gauges tick forward), so the
    slot-level key still changes and both snapshots survive. The no-advance
    case (a correction landing with literally zero network advance) is a
    known, accepted narrowing — not covered here."""

    def test_correction_survives_when_the_modal_network_slot_advances(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        corrected_ts = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row("2135", value=100.0, measurement_time=corrected_ts),
                    _row(
                        "2200", measurement_time=datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
                    ),
                    _row(
                        "2300", measurement_time=datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
                    ),
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 9, 1, tzinfo=UTC)),
        )
        # A later poll: gauge 2135 restates its 09:00 value (timestamp
        # UNCHANGED — a genuine correction, not a new observation), but the
        # rest of the network (2200, 2300 — the majority/bulk) has moved on
        # to 09:10 — so the MODAL slot advances and this snapshot gets its
        # own path.
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(
                [
                    _row("2135", value=101.0, measurement_time=corrected_ts),
                    _row(
                        "2200",
                        measurement_time=datetime(2026, 7, 21, 9, 10, tzinfo=UTC),
                    ),
                    _row(
                        "2300",
                        measurement_time=datetime(2026, 7, 21, 9, 10, tzinfo=UTC),
                    ),
                ]
            ),
            clock=_ClockSpy(datetime(2026, 7, 21, 9, 11, tzinfo=UTC)),
        )

        parquet_files = sorted(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 2
        corrected_values = sorted(
            pl.read_parquet(f)
            .filter(pl.col("gauge_code") == "2135")["value"]
            .to_list()[0]
            for f in parquet_files
        )
        assert corrected_values == [100.0, 101.0]  # both survive


class TestMultiGaugeArchive:
    def test_one_run_archives_many_distinct_gauges(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        rows = [
            _row("2135"),
            _row("2200"),
            _row("3001", lindas_kind="lake", parameter="water_level"),
        ]
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result.row_count == 3
        assert result.gauge_count == 3

        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        df = pl.read_parquet(parquet_files[0])
        assert set(df["gauge_code"].to_list()) == {"2135", "2200", "3001"}
        assert "cycle_at" in df.columns


class TestGzipStreamClosesBeforeRename:
    """D6 locks *ordering*, not just the final bytes: the gzip stream must be
    CLOSED before `os.replace` publishes the temp file.

    Checking the decompressed bytes after the flow returns cannot see this — by
    then Python has closed the file either way. A mutant that renames from
    inside the still-open gzip context would publish a truncated member and
    still pass a bytes-only assertion, because the buffer flushes on scope
    exit a moment later. In production that window is where a crash leaves an
    unreadable snapshot occupying a slot the collector believes is archived.

    So assert the invariant at the moment it matters: when `os.replace` is
    called, the source file must already be a complete, decompressible gzip
    member.
    """

    def test_temp_file_is_a_complete_gzip_member_when_rename_fires(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        raw_payload = {"results": {"bindings": [{"fake": "payload"}]}}

        real_replace = os.replace
        checked: list[str] = []

        def spy_replace(src, dst):  # type: ignore[no-untyped-def]
            if str(dst).endswith(".json.gz"):
                # Read the SOURCE as it stands at rename time — not after.
                with gzip.open(src, "rb") as gz:
                    payload = gz.read()
                assert payload, "gzip temp file was empty at rename time"
                checked.append(str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr(module.os, "replace", spy_replace)

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135")], raw_payload=raw_payload),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )

        assert checked, "no .json.gz rename was observed — test did not exercise D6"


class TestRawArchival:
    def test_raw_companion_is_gzipped_and_round_trips_byte_for_byte(
        self, tmp_path: Path
    ) -> None:
        """Plan 176 D6: the raw JSON companion is written `.json.gz`
        (reversing Plan 136's "plain .json, no gzip") — SPARQL JSON
        compresses ~42x, and at the new cadence plain JSON would cost
        ~12.87 GB/yr on a host that has already hit 94% disk."""
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        raw_payload = {"results": {"bindings": [{"fake": "payload"}]}}

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135")], raw_payload=raw_payload),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        raw_files = list(tmp_path.rglob("*.json.gz"))
        assert len(raw_files) == 1
        assert raw_files[0].name.endswith(".json.gz")
        # No plain, uncompressed .json companion anywhere in the archive.
        assert list(tmp_path.rglob("*.json")) == []

        with gzip.open(raw_files[0], "rb") as gz:
            decompressed = gz.read()
        # Byte-for-byte, not parse-and-compare: a `json.loads` round-trip
        # would hide a formatting regression (indent, key order, separators)
        # that changes the bytes without changing the parsed value.
        assert decompressed == json.dumps(raw_payload).encode("utf-8")


class TestHeartbeat:
    def test_ok_status_on_clean_run_with_one_stale_many_fresh(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        stale_ts = datetime(2025, 5, 28, 0, 0, tzinfo=UTC)
        fresh_ts = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
        rows = [
            _row("9999", measurement_time=stale_ts),  # dead gauge, still present
            _row("2135", measurement_time=fresh_ts),
            _row("2200", measurement_time=fresh_ts),
        ]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
            pipeline_health_store=health_store,
        )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK
        assert records[0].detail["row_count"] == 3
        assert records[0].detail["newest_measurement_time"] == fresh_ts.isoformat()

    def test_transient_429_then_200_writes_ok_and_archives(
        self, tmp_path: Path
    ) -> None:
        """Plan 175 T2 regression test for the incident itself: a rate-limit
        429 followed by a 200 must be retried through the shared limiter
        (not treated as fatal on the first attempt), archive the snapshot,
        and write an OK heartbeat — never the CRITICAL alert that fired
        against the pre-fix code."""
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        fresh_ts = "2026-08-17T08:00:00Z"
        bindings = [
            {
                "subject": {
                    "type": "uri",
                    "value": (
                        "https://environment.ld.admin.ch/foen/hydro/river/"
                        "observation/2135"
                    ),
                },
                "predicate": {
                    "type": "uri",
                    "value": (
                        "https://environment.ld.admin.ch/foen/hydro/dimension/"
                        "measurementTime"
                    ),
                },
                "object": {"type": "literal", "value": fresh_ts},
            },
            {
                "subject": {
                    "type": "uri",
                    "value": (
                        "https://environment.ld.admin.ch/foen/hydro/river/"
                        "observation/2135"
                    ),
                },
                "predicate": {
                    "type": "uri",
                    "value": (
                        "https://environment.ld.admin.ch/foen/hydro/dimension/discharge"
                    ),
                },
                "object": {"type": "literal", "value": "12.3"},
            },
        ]
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={})
            return httpx.Response(
                200,
                json={"head": {"vars": []}, "results": {"bindings": bindings}},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            max_retries=2,
            limiter=_coherent_limiter(),
        )

        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=adapter,
            clock=_ClockSpy(datetime(2026, 8, 17, 8, 0, 30, tzinfo=UTC)),
            pipeline_health_store=health_store,
        )

        assert calls["n"] == 2
        assert result.row_count == 1

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK

    def test_empty_response_writes_critical_and_reraises(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        with pytest.raises(AdapterError):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=_FakeAdapter([]),
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["row_count"] == 0
        assert records[0].detail["newest_measurement_time"] is None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_http_error_fetch_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # Drives a REAL BafuObservationAdapter (not a hand-fed AdapterError)
        # over an httpx.MockTransport 500 response, through the real flow, so
        # this locks the actual production path: the adapter must normalize
        # the HTTP failure to AdapterError, and the flow must write CRITICAL
        # before re-raising it.
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            limiter=_coherent_limiter(),
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="failed with status 500"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_malformed_json_shape_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # Drives a REAL BafuObservationAdapter over an httpx.MockTransport
        # response that IS valid JSON but has the WRONG top-level shape (a
        # bare list) — `payload["results"]["bindings"]` then raises
        # TypeError, not ValueError/KeyError. This locks the T8 contract:
        # the adapter must normalize even a TypeError-shaped malformed
        # response to AdapterError, and the flow must write CRITICAL before
        # re-raising it (never let a bare TypeError skip the heartbeat).
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            limiter=_coherent_limiter(),
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="not a well-formed SPARQL"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_bindings_null_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # A well-formed envelope whose `bindings` is null: extraction
        # succeeds (bindings=None), then len(None) would raise a bare
        # TypeError OUTSIDE the try. The adapter's isinstance guard must
        # surface it as AdapterError, and the flow must write CRITICAL before
        # re-raising it (the T8 contract for a wrong-shaped bindings value).
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": None}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            limiter=_coherent_limiter(),
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="is not a list"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_all_gauges_stale_writes_critical_but_archives_and_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        # A served-but-FROZEN feed: every gauge's newest measurement_time is
        # older than the staleness threshold. The collection SUCCEEDED — the
        # snapshot must still be archived and the run must NOT re-raise — but
        # the heartbeat must be CRITICAL (reason "stale_measurement_time") so
        # the watchdog alarms on a frozen graph that would otherwise report OK
        # forever.
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        run_at = datetime(2026, 7, 21, 15, 5, tzinfo=UTC)
        # 6h behind run_at — well past the ~3h threshold, for every gauge.
        stale_ts = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
        rows = [
            _row("2135", measurement_time=stale_ts),
            _row("2200", measurement_time=stale_ts),
            _row(
                "3001",
                lindas_kind="lake",
                parameter="water_level",
                measurement_time=stale_ts,
            ),
        ]

        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(run_at),
            pipeline_health_store=health_store,
        )

        # Did NOT raise, and the snapshot WAS archived.
        assert result.dedup_skipped is False
        assert result.row_count == 3
        assert len(list(tmp_path.rglob("*.parquet"))) == 1

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] == "stale_measurement_time"
        assert records[0].detail["row_count"] == 3
        assert (
            records[0].detail["newest_measurement_time"]
            == ensure_utc(stale_ts).isoformat()
        )

    def test_truncated_fetch_writes_critical(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        with pytest.raises(AdapterError, match="LIMIT"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=_FakeAdapter(AdapterError("hit the safety LIMIT")),
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert records[0].status is PipelineHealthStatus.CRITICAL

    def test_health_store_outage_does_not_fail_the_run(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)

        class _RaisingHealthStore:
            def append_health_record(self, record: object) -> None:
                raise RuntimeError("db unavailable")

        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135")]),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
            pipeline_health_store=_RaisingHealthStore(),
        )
        assert result.row_count == 1


class TestStaleMeasurementThresholdBoundary:
    """Plan 176 D4: `_STALE_MEASUREMENT_THRESHOLD` is re-derived for the
    10-minute grid — worst-case HEALTHY age is ~15 min publish lag plus one
    ~10-11 min publish interval (~26 min), so 30 min must clear a healthy
    feed without hiding a frozen one. Boundary tested on both sides of the
    module's OWN constant (not a hardcoded duplicate), so the boundary
    tests below track whatever value D4 locks; this one locks the value
    itself."""

    def test_threshold_is_thirty_minutes(self) -> None:
        module = _import_flow_module()
        assert timedelta(minutes=30) == module._STALE_MEASUREMENT_THRESHOLD  # type: ignore[attr-defined]

    def test_age_exactly_at_threshold_is_not_stale(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        threshold: timedelta = module._STALE_MEASUREMENT_THRESHOLD  # type: ignore[attr-defined]
        ts = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
        run_at = ensure_utc(ts + threshold)  # age == threshold, NOT > threshold

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135", measurement_time=ts)]),
            clock=_ClockSpy(run_at),
            pipeline_health_store=health_store,
        )
        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert records[-1].status is PipelineHealthStatus.OK

    def test_age_one_second_past_threshold_is_stale(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        threshold: timedelta = module._STALE_MEASUREMENT_THRESHOLD  # type: ignore[attr-defined]
        ts = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
        run_at = ensure_utc(ts + threshold + timedelta(seconds=1))

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135", measurement_time=ts)]),
            clock=_ClockSpy(run_at),
            pipeline_health_store=health_store,
        )
        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert records[-1].status is PipelineHealthStatus.CRITICAL
        assert records[-1].detail["error_type"] == "stale_measurement_time"
