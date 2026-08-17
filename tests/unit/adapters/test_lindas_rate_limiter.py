"""Locked tests for the shared LINDAS rate limiter (Plan 175 T1).

Fakes only — a fake clock and a recording sleeper, never real time or the
network (CLAUDE.md testability: no bare ``time.sleep`` in tests either).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sapphire_flow.exceptions import LindasRateLimitExhaustedError
from sapphire_flow.types.datetime import ensure_utc


def _import_module() -> object:
    # Red-first guard: the module does not exist yet at the start of T1.
    # Import lazily so a missing module is a genuine pytest.fail() ASSERTION,
    # never a collection-time ImportError.
    try:
        from sapphire_flow.adapters import lindas_rate_limiter

        return lindas_rate_limiter
    except ImportError as exc:  # pragma: no cover - red-first guard
        pytest.fail(
            "sapphire_flow.adapters.lindas_rate_limiter does not exist yet "
            f"(T1 not implemented): {exc}"
        )


class _FakeClock:
    """A clock that only advances when told to — paired with ``_sleeper``
    below so a simulated sleep also advances what the limiter sees as
    elapsed wall-clock time."""

    def __init__(self, start: datetime) -> None:
        self.now = ensure_utc(start)

    def __call__(self) -> object:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = ensure_utc(self.now + timedelta(seconds=seconds))


class _SleepSpy:
    def __init__(self, clock: _FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(seconds)


def _limiter(module, *, clock: _FakeClock, sleeper: _SleepSpy, **config_kwargs):  # type: ignore[no-untyped-def]
    config = module.LindasLimiterConfig(**config_kwargs)
    return module.TokenBucketLindasLimiter(config=config, clock=clock, sleeper=sleeper)


def _responses(*statuses: int, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    queue = list(statuses)

    def send(remaining_s: float) -> httpx.Response:
        del remaining_s
        status = queue.pop(0)
        return httpx.Response(status, headers=headers or {})

    return send


_START = datetime(2026, 8, 17, 8, 0, 0, tzinfo=UTC)


class TestRetrySucceedsAfterThrottling:
    def test_429_429_200_retries_and_succeeds(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 429, 200)
        response = limiter.call(send)

        assert response.status_code == 200
        assert len(spy.calls) == 2
        assert all(c >= module.LINDAS_RETRY_FLOOR_S for c in spy.calls)


class TestRetryAfterHonoured:
    def test_delta_seconds_retry_after_is_honoured(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "7"})
        limiter.call(send)

        assert spy.calls == [7.0]

    def test_http_date_retry_after_is_honoured(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        future = _START + timedelta(seconds=10)
        header_value = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            first = not send.called  # type: ignore[attr-defined]
            send.called = True  # type: ignore[attr-defined]
            if first:
                return httpx.Response(429, headers={"Retry-After": header_value})
            return httpx.Response(200)

        send.called = False  # type: ignore[attr-defined]
        limiter.call(send)

        assert spy.calls == pytest.approx([10.0], abs=0.5)

    def test_malformed_retry_after_falls_back_to_floor(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "not-a-value"})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_RETRY_FLOOR_S]

    def test_negative_retry_after_falls_back_to_floor(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "-5"})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_RETRY_FLOOR_S]

    def test_past_http_date_falls_back_to_floor(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        past = _START - timedelta(seconds=10)
        header_value = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
        send = _responses(429, 200, headers={"Retry-After": header_value})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_RETRY_FLOOR_S]

    def test_retry_after_zero_is_clamped_up_to_the_floor(self) -> None:
        # Minor fix: `Retry-After: 0` is just as untrusted as an oversized
        # value — honouring it verbatim would let the retry loop hammer the
        # endpoint faster than its measured refill floor.
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "0"})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_RETRY_FLOOR_S]

    def test_retry_after_one_second_is_clamped_up_to_the_floor(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "1"})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_RETRY_FLOOR_S]


class TestD7Blocker:
    """Locks the folded D7 blocker: an unbounded Retry-After must never
    produce an unbounded sleep, and total wall-clock is bounded independently
    of the attempt count."""

    def test_retry_after_86400_is_clamped_to_max_delay(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(429, 200, headers={"Retry-After": "86400"})
        limiter.call(send)

        assert spy.calls == [module.LINDAS_MAX_DELAY_S]
        assert module.LINDAS_MAX_DELAY_S < 86400

    def test_slow_sequence_aborts_at_total_deadline(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        # High attempt cap so the DEADLINE bound is what fires, not attempts.
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=50)

        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            return httpx.Response(429, headers={"Retry-After": "86400"})

        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(send)

        assert exc_info.value.bound == "deadline"
        # Every sleep was clamped to the max delay, and the loop aborted
        # once cumulative elapsed time would exceed the total deadline —
        # nowhere close to 50 attempts worth of real waiting.
        assert all(c == module.LINDAS_MAX_DELAY_S for c in spy.calls)
        assert len(spy.calls) < 50

    def test_bucket_starvation_counts_against_the_deadline(self) -> None:
        # Blocker fix: `start` must be captured BEFORE `_acquire_token()`,
        # not after — otherwise a starved bucket's wait is free real
        # wall-clock time the deadline never sees. Deadline (3s) is smaller
        # than one refill period (4s default floor), so the SECOND call's
        # bucket wait alone must already exceed the deadline.
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)  # paired: the bucket-wait sleep really
        # advances simulated time, the way a real `time.sleep` would.
        limiter = _limiter(
            module,
            clock=clock,
            sleeper=spy,
            capacity=1,
            total_deadline_s=3.0,
            max_attempts=50,
        )
        limiter.call(_responses(200))  # drains the sole starting token

        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            # Must wait ~1 refill period (4s) to reacquire a token — that
            # wait alone already exceeds the 3s deadline, so this must raise
            # before ever attempting an HTTP request.
            limiter.call(_responses(200))

        assert exc_info.value.bound == "deadline"
        assert exc_info.value.attempts == 0

    def test_slow_send_never_lets_total_elapsed_exceed_the_deadline(self) -> None:
        """A `send` that honours its remaining-budget argument (as a real
        HTTP client would via a `timeout=` override) can never itself
        overrun the deadline — proving `call()` bounds elapsed time even
        when each individual attempt is slow, not just when attempts are
        cheap and the loop count is what mattered."""
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(
            module, clock=clock, sleeper=spy, max_attempts=1000, total_deadline_s=120.0
        )

        def budget_honouring_send(remaining_s: float) -> httpx.Response:
            # Simulates an HTTP client bounded by the timeout it was handed:
            # it cannot advance the clock past what it was told remained.
            clock.advance(min(50.0, remaining_s))
            return httpx.Response(429)

        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(budget_honouring_send)

        elapsed_s = (clock.now - _START).total_seconds()
        assert exc_info.value.bound == "deadline"
        assert elapsed_s <= 120.0


class TestCrossCallBucketSync:
    """Locks the major fix: a 429 is upstream proof the local bucket's token
    count no longer reflects reality — a call() that retries spends real
    upstream capacity the bucket only ever charged once for. Without
    draining on 429, that unaccounted capacity lets subsequent independent
    calls() slip through immediately, recreating the 429 cascade."""

    def test_429_then_success_still_paces_the_next_calls(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        # Call A: first attempt 429 (real upstream request #1), retry
        # succeeds (real upstream request #2) — two real HTTP attempts, but
        # the bucket only ever charged one token up front.
        response_a = limiter.call(_responses(429, 200))
        assert response_a.status_code == 200
        sleeps_after_a = len(spy.calls)

        # Three more logical calls, sent back-to-back immediately after A.
        for _ in range(3):
            response = limiter.call(_responses(200))
            assert response.status_code == 200

        # Real upstream (capacity 3, refill ~1/floor) was drained to 0 by
        # the 429 and only had time to refill ~1 slot before these three ran
        # — at least two of them must still be paced, not sent instantly
        # (the pre-fix bucket model let all three through with zero waits).
        extra_sleeps = len(spy.calls) - sleeps_after_a
        assert extra_sleeps >= 2


class TestNonRetryableStatus:
    def test_404_is_returned_immediately_with_zero_sleep(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        send = _responses(404)
        response = limiter.call(send)

        assert response.status_code == 404
        assert spy.calls == []


class TestExhaustion:
    def test_all_429_raises_exhausted_after_attempt_cap(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=3)

        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            return httpx.Response(429)

        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(send)

        assert exc_info.value.bound == "attempts"
        assert exc_info.value.attempts == 3
        assert exc_info.value.last_status == 429


class TestBucketPacing:
    def test_n_calls_beyond_capacity_pace_at_the_floor(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)  # never advances on its own
        spy = _SleepSpy()  # deliberately NOT paired with clock: a frozen
        # clock means the bucket never refills between calls, so every call
        # beyond the initial capacity must wait the full floor.
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        n = 6
        for _ in range(n):
            response = limiter.call(_responses(200))
            assert response.status_code == 200

        capacity = module.LindasLimiterConfig().capacity
        expected_paced_calls = n - capacity
        assert len(spy.calls) >= expected_paced_calls
        assert all(c >= module.LINDAS_RETRY_FLOOR_S for c in spy.calls)
