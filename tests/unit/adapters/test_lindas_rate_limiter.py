"""Locked tests for the shared LINDAS rate limiter (Plan 175 T1).

Fakes only — a fake clock and a recording sleeper, never real time or the
network (CLAUDE.md testability: no bare ``time.sleep`` in tests either).
"""

from __future__ import annotations

import threading
import time as real_time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import structlog.testing

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


def _limiter(  # type: ignore[no-untyped-def]
    module,
    *,
    clock: _FakeClock,
    sleeper: _SleepSpy,
    **config_kwargs,
):
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
    produce an unbounded sleep, and one call()'s wall clock is bounded
    independently of the attempt count — i.e. no new attempt and no retry
    sleep starts past the deadline. An attempt already in flight is bounded
    by the HTTP client's own phase timeouts, not by this deadline."""

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

    def test_429_then_success_paces_the_very_next_call_by_itself(self) -> None:
        """Round-2 major fix: the retry's SUCCESSFUL attempt (429 -> sleep ->
        200) also spends a real upstream token, but pre-fix the bucket was
        only ever charged once per call() (at the very start) — so by the
        time the retry backoff sleep had elapsed one whole refill period, the
        local model believed a FULL extra token had accrued that upstream
        never actually had spare. That let the very next independent call()
        send immediately, unpaced. Acquiring a token before EVERY attempt
        (including the retry) closes this: the retry's acquire consumes the
        refilled token itself, so the bucket is honestly at 0 when call() B
        starts, not falsely at 1."""
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        response_a = limiter.call(_responses(429, 200))
        assert response_a.status_code == 200
        sleeps_after_a = len(spy.calls)

        response_b = limiter.call(_responses(200))

        assert response_b.status_code == 200
        assert len(spy.calls) > sleeps_after_a, (
            "the call immediately following a recovered 429 must itself be "
            "paced, not sent on phantom bucket credit left over from the "
            "retry's real (but never locally charged) HTTP attempt"
        )


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
        clock = _FakeClock(_START)
        # The sleeper IS paired with the clock: a sleep that does not advance
        # the clock is not a thing that can happen in production (`time.sleep`
        # and `datetime.now` always agree), and pretending otherwise made the
        # limiter's local wait budget drain while its wall-clock budget did
        # not. Refill is still the only thing that advances time here, so
        # every call beyond the initial capacity must wait the full floor.
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        n = 6
        for _ in range(n):
            response = limiter.call(_responses(200))
            assert response.status_code == 200

        capacity = module.LindasLimiterConfig().capacity
        expected_paced_calls = n - capacity
        assert len(spy.calls) >= expected_paced_calls
        assert all(c >= module.LINDAS_RETRY_FLOOR_S for c in spy.calls)


class TestAcquisitionNeverDispatchesWithoutAToken:
    """Fix: `_acquire_token` returns False rather than silently falling
    through when the call's budget runs out mid-wait.

    It used to return `None` on both paths, leaving `call()` to infer failure
    from the wall clock. With a sleeper that does not advance the injected
    clock — a documented DI seam — the wall-clock check still saw budget
    left, so `send` was dispatched having consumed no token at all: an
    unpaced request, which is the one thing this limiter exists to prevent."""

    def test_a_wait_that_exhausts_the_budget_raises_instead_of_sending(
        self,
    ) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        # Sleeper deliberately does NOT advance the clock, so the local wait
        # budget drains while the wall clock does not — the exact seam.
        spy = _SleepSpy()
        limiter = _limiter(
            module,
            clock=clock,
            sleeper=spy,
            capacity=1,
            total_deadline_s=10.0,
            max_attempts=5,
        )

        limiter.call(_responses(200))  # consumes the only token

        sent = 0

        def counting_send(remaining_s: float) -> httpx.Response:
            del remaining_s
            nonlocal sent
            sent += 1
            return httpx.Response(200)

        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(counting_send)

        assert exc_info.value.bound == "deadline"
        assert sent == 0, "dispatched a request without holding a token"


class TestDeadlineActuallyEnforced:
    """The total deadline bounds when a NEW attempt may start, and no sleep
    may run past it. It does not abort an in-flight request — each attempt is
    bounded by the HTTP client's own configured phase timeouts. (An earlier
    design ran `send` on a daemon thread joined with a hard timeout; that
    bounded the caller's wait but abandoned the thread and its live request,
    so repeated timeouts leaked both while reporting the call exhausted.)

    Here a slow `send` consumes budget by advancing the fake clock, which is
    exactly what a slow real request does to the deadline."""

    @staticmethod
    def _slow_send(clock: _FakeClock, *, cost_s: float, status: int = 503):  # type: ignore[no-untyped-def]
        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            clock.advance(cost_s)
            return httpx.Response(status)

        return send

    def test_no_new_attempt_starts_once_the_budget_is_spent(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(
            module,
            clock=clock,
            sleeper=spy,
            total_deadline_s=30.0,
            max_attempts=99,
        )

        # Each attempt burns 20s of wall clock, so the second attempt lands
        # past the 30s deadline and a third must never start.
        send = self._slow_send(clock, cost_s=20.0)
        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(send)

        assert exc_info.value.bound == "deadline"
        # Bounded by the attempts actually begun before the deadline, NOT by
        # max_attempts=99 — proves the deadline, not the attempt cap, stopped it.
        assert exc_info.value.attempts <= 2

    def test_a_single_attempt_slower_than_the_whole_budget_still_terminates(
        self,
    ) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(
            module,
            clock=clock,
            sleeper=spy,
            total_deadline_s=30.0,
            max_attempts=5,
        )

        # One attempt costs 200s — far past the deadline. The call must end
        # on the deadline bound rather than looping.
        send = self._slow_send(clock, cost_s=200.0)
        with pytest.raises(LindasRateLimitExhaustedError) as exc_info:
            limiter.call(send)

        assert exc_info.value.bound == "deadline"
        assert exc_info.value.attempts == 1

    def test_a_non_retryable_response_is_returned_even_if_it_was_slow(self) -> None:
        # The deadline gates STARTING work, so a slow attempt that does come
        # back with a usable answer must be honoured, not discarded.
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, total_deadline_s=30.0)

        response = limiter.call(self._slow_send(clock, cost_s=200.0, status=200))

        assert response.status_code == 200


class TestRetryAfterOverflowGuard:
    """Major fix: an absurdly large numeric `Retry-After` must clamp to
    `LINDAS_MAX_DELAY_S`, not escape `_parse_retry_after` as a raw
    OverflowError (from `float()` of a huge-but-valid Python int) or
    ValueError (from `int()`'s own digit-count limit on a truly enormous
    string)."""

    def test_a_few_hundred_digit_retry_after_clamps_to_the_max_delay(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        # 400 digits: well within Python's int-string conversion limit (so
        # `int()` succeeds), but far past float's ~308-digit max magnitude —
        # `float(int(...))` raises OverflowError here pre-fix.
        huge_digits = "7" * 400
        send = _responses(429, 200, headers={"Retry-After": huge_digits})

        limiter.call(send)

        assert spy.calls == [module.LINDAS_MAX_DELAY_S]

    def test_a_retry_after_beyond_pythons_int_string_limit_clamps_to_the_max_delay(
        self,
    ) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        # Past CPython's default int-string-conversion digit limit (4300) —
        # `int()` itself raises ValueError here pre-fix, before `float()` is
        # even reached.
        absurd_digits = "9" * 5000
        send = _responses(429, 200, headers={"Retry-After": absurd_digits})

        limiter.call(send)

        assert spy.calls == [module.LINDAS_MAX_DELAY_S]


class TestConcurrentAcquisition:
    """`_acquire_token` must serialize concurrent callers so no two threads
    can observe the same empty bucket, wait in parallel, and both proceed as
    though each had reserved a distinct refilled token.

    Timestamps are recorded INSIDE `send` — i.e. when a request is actually
    dispatched — not when `call()` returns. Timing the returns would let an
    implementation that fires the whole burst immediately and merely delays
    its return values pass, which is precisely the failure this guards.

    Real threads + real `time.sleep`/`datetime.now` (a small refill period to
    stay fast): a fake clock is not thread-safe and would not exercise the
    race the lock closes. Workers are released together by a barrier so they
    genuinely contend."""

    def test_dispatches_are_paced_at_the_refill_period(self) -> None:
        module = _import_module()
        capacity = 2
        refill_period_s = 0.15
        n_threads = 6
        limiter = module.TokenBucketLindasLimiter(
            config=module.LindasLimiterConfig(
                capacity=capacity,
                refill_period_s=refill_period_s,
                max_attempts=5,
                total_deadline_s=10.0,
            )
        )

        send_times: list[float] = []
        send_lock = threading.Lock()
        barrier = threading.Barrier(n_threads)
        started_at = real_time.perf_counter()

        def worker() -> None:
            def send(remaining_s: float) -> httpx.Response:
                del remaining_s
                with send_lock:
                    send_times.append(real_time.perf_counter() - started_at)
                return httpx.Response(200)

            barrier.wait(timeout=5.0)
            assert limiter.call(send).status_code == 200

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        assert not any(th.is_alive() for th in threads), "a worker thread hung"
        assert len(send_times) == n_threads

        ordered = sorted(send_times)
        # The bucket starts full, so `capacity` dispatches may go out at once.
        # Every dispatch beyond that must wait for a refill it alone consumed.
        # Under the pre-fix race all overflow callers waited in PARALLEL and
        # dispatched together, collapsing these gaps to ~0.
        for i in range(capacity, n_threads):
            gap = ordered[i] - ordered[i - 1]
            assert gap >= refill_period_s * 0.5, (
                f"dispatch {i} went out only {gap:.3f}s after dispatch "
                f"{i - 1} (refill period {refill_period_s}s) — overflow "
                "callers were not serialized, so more than the bucket's "
                "capacity worth of load escaped unpaced"
            )

        # And the burst itself must not exceed capacity: the first `capacity`
        # dispatches are the only ones allowed to be near-simultaneous.
        burst = [s for s in ordered if s < refill_period_s * 0.5]
        assert len(burst) <= capacity, (
            f"{len(burst)} dispatches went out inside half a refill period — "
            f"burst exceeded capacity={capacity}"
        )


class TestRefillIsMonotonic:
    """Fix: `_refill` must ignore a non-advancing clock rather than rewind
    `_last_refill`.

    `_drain` used to read the clock BEFORE taking the lock, so a caller that
    then blocked could hand `_refill` a timestamp older than `_last_refill`.
    That moved `_last_refill` backward, and the next refill measured elapsed
    time from the rewound point — crediting the same seconds twice and
    handing back the tokens the 429 drain had just removed."""

    def test_a_stale_refill_cannot_credit_the_same_seconds_twice(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, refill_period_s=4.0)

        limiter._drain()  # noqa: SLF001 - invariant under test

        # A caller that read the clock before blocking on the lock hands
        # `_refill` a timestamp from BEFORE the drain. Rewinding
        # `_last_refill` here is harmless on its own — the damage lands on
        # the NEXT refill, which then measures elapsed time from the rewound
        # point and credits seconds that never passed.
        limiter._refill(_START - timedelta(seconds=30))  # noqa: SLF001

        # No real time has passed since the drain, so the bucket is still
        # empty and this caller must wait a full refill period.
        limiter._acquire_token(deadline_remaining_s=60.0)  # noqa: SLF001

        assert spy.calls, (
            "a drained bucket handed out a token with no wait — the stale "
            "refill rewound `_last_refill`, so the next refill credited 30s "
            "of phantom elapsed time"
        )
        assert spy.calls[0] == pytest.approx(4.0, abs=0.01)


class TestWaitersDoNotConvoy:
    """Regression lock for the lock-held-during-sleep defect.

    The dispatch-spacing test above cannot catch it: an implementation that
    sleeps while HOLDING the lock still paces dispatches correctly — it just
    serializes every waiter behind every other waiter's full sleep, so a
    caller's own deadline is not evaluated until it wins the lock. That is
    invisible to timing-of-dispatch assertions.

    This observes the property directly: two callers that both find an empty
    bucket must be able to be INSIDE their wait at the same time. The sleeper
    trips a 2-party barrier, so a convoying implementation never gets both
    parties there and the barrier times out."""

    def test_two_waiters_can_be_in_their_wait_simultaneously(self) -> None:
        module = _import_module()
        both_waiting = threading.Barrier(2)
        overlapped = threading.Event()

        def sleeper(seconds: float) -> None:
            try:
                both_waiting.wait(timeout=2.0)
                overlapped.set()
            except threading.BrokenBarrierError:
                # Expected once one waiter has won the token and only a
                # single party is left waiting — not a failure by itself.
                pass
            real_time.sleep(seconds)

        limiter = module.TokenBucketLindasLimiter(
            config=module.LindasLimiterConfig(
                capacity=1,
                refill_period_s=0.3,
                max_attempts=5,
                total_deadline_s=10.0,
            ),
            sleeper=sleeper,
        )

        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            return httpx.Response(200)

        limiter.call(send)  # drains the single token

        threads = [
            threading.Thread(target=lambda: limiter.call(send)) for _ in range(2)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        assert not any(th.is_alive() for th in threads), "a worker thread hung"
        assert overlapped.is_set(), (
            "the two waiters never overlapped inside their wait — waiters are "
            "convoyed (the wait happens while holding the bucket lock), so a "
            "caller's deadline cannot be evaluated until it wins the lock"
        )


class TestThrottledVsRetryingEvents:
    """Minor fix: `lindas.throttled` is reserved for a 429 (the plan's own
    definition: '429 seen, retrying'); a 5xx or transport failure emits the
    distinct `lindas.retrying` event instead, so operators can tell rate
    limiting apart from an endpoint/network fault."""

    def test_429_emits_lindas_throttled(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        with structlog.testing.capture_logs() as captured:
            limiter.call(_responses(429, 200))

        events = [e["event"] for e in captured]
        assert "lindas.throttled" in events
        assert "lindas.retrying" not in events
        (throttled,) = [e for e in captured if e["event"] == "lindas.throttled"]
        assert throttled["status"] == 429

    def test_5xx_emits_lindas_retrying_not_throttled(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        with structlog.testing.capture_logs() as captured:
            limiter.call(_responses(503, 200))

        events = [e["event"] for e in captured]
        assert "lindas.retrying" in events
        assert "lindas.throttled" not in events
        (retrying,) = [e for e in captured if e["event"] == "lindas.retrying"]
        assert retrying["status"] == 503

    def test_transport_error_emits_lindas_retrying_not_throttled(self) -> None:
        module = _import_module()
        clock = _FakeClock(_START)
        spy = _SleepSpy(clock)
        limiter = _limiter(module, clock=clock, sleeper=spy, max_attempts=5)

        calls = {"n": 0}

        def send(remaining_s: float) -> httpx.Response:
            del remaining_s
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("no route to host")
            return httpx.Response(200)

        with structlog.testing.capture_logs() as captured:
            limiter.call(send)

        events = [e["event"] for e in captured]
        assert "lindas.retrying" in events
        assert "lindas.throttled" not in events
        (retrying,) = [e for e in captured if e["event"] == "lindas.retrying"]
        assert retrying["status"] is None
