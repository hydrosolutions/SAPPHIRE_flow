"""Shared LINDAS (``lindas.admin.ch``) rate-limit pacing + 429-aware retry
(Plan 175 T1) — the single home for the endpoint's measured contract, so
every in-process production caller offers the same bounded load instead of
each adapter guessing its own retry policy.

**Measured contract (Plan 175 § Evidence, live 2026-08-17):** burst capacity
3 requests from an idle bucket, refill ~1 slot per 3-4 s, HTTP 429 with no
``Retry-After`` header, full recovery ~15 s.

**Scope is process-local (D6).** A token bucket inside one Python process
cannot enforce a shared budget across two different Prefect work-pool
processes — that is fixed by schedule separation (Plan 175 T4), not by this
module. This limiter is written behind the small :class:`LindasRateLimiter`
Protocol so a future cross-process implementation (e.g. a Prefect global
concurrency limit) can be substituted without touching any caller.

**Two independent waits, not one:**
- *Bucket pacing* — ``call()`` acquires one token before EVERY attempt,
  including retries (fixer round 2), not just the call's first. This paces
  the rate of independent ``call()`` invocations (e.g. many stations polled
  in a tight loop) against each other, AND makes sure a retried attempt —
  which spends a second real upstream request — is charged against the local
  model too. Charging only the first attempt let a ``call()`` that retried
  leave phantom credit for the next independent ``call()`` to spend
  immediately, recreating the exact 429 cascade this module exists to
  prevent.
- *Retry backoff* — once a request fails retryably, the wait before the next
  attempt is governed entirely by :data:`LINDAS_RETRY_FLOOR_S` / the
  response's ``Retry-After`` (D7), never by the bucket. Retry backoff already
  waits at or above the bucket's own refill period, so the per-attempt token
  acquisition above should normally find a token already refilled and NOT add
  an extra wait on top of the backoff — it exists to charge the token, not to
  gate the retry a second time.

**The 120 s deadline covers the ENTIRE call, including bucket wait — and is
actually enforced, not just checked.** ``call()`` starts its clock before
token acquisition, not after, and re-checks the remaining budget before every
attempt (including the first) AND again after token acquisition (which can
itself wait) — a starved bucket counts against the deadline exactly like a
slow retry sequence does. ``send`` is never invoked directly: it runs through
``_run_with_deadline`` (or an injected ``deadline_runner``), which executes it
on a background thread and joins with a hard ``remaining_s`` timeout. This is
the actual enforcement mechanism — passing ``timeout=`` values to an HTTP
client only bounds each connect/read/write/pool phase INDEPENDENTLY (HTTPX
0.28 applies one float across all four), so a request slow across more than
one phase could otherwise still overrun the deadline even though each phase
individually stayed under it. The calling thread returns within
``remaining_s`` regardless of what the background thread is still doing; an
abandoned thread is daemonic and exits on its own once the HTTP client's own
(independently configured, stricter) phase timeout eventually fires.

**A 429 proves the upstream bucket is empty, not just this process's local
count.** ``call()`` drains its local bucket to zero the instant a 429 comes
back, regardless of how many tokens the local model thought were left. A
``call()`` that took several retryable attempts (each a real HTTP request the
upstream bucket paid for) must not leave the next independent ``call()`` free
to spend tokens the local bucket never actually charged for those attempts.
"""

from __future__ import annotations

import email.utils
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx
import structlog

from sapphire_flow.exceptions import LindasRateLimitExhaustedError
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

# Conservative vs the measured ~3-4 s refill (§ Evidence) — floor backoff and
# bucket refill period both key off this single constant.
LINDAS_RETRY_FLOOR_S = 4.0
# D7: an upstream `Retry-After` is untrusted input; never sleep longer than this
# for a single wait regardless of what the header claims.
LINDAS_MAX_DELAY_S = 60.0
# D7: bounded *wall-clock* across all attempts of one call(), independent of
# the attempt-count bound below.
LINDAS_TOTAL_DEADLINE_S = 120.0
_BUCKET_CAPACITY = 3
_DEFAULT_MAX_ATTEMPTS = 6

_RETRYABLE_STATUS_FLOOR = 500


@dataclass(frozen=True, kw_only=True, slots=True)
class LindasLimiterConfig:
    capacity: int = _BUCKET_CAPACITY
    refill_period_s: float = LINDAS_RETRY_FLOOR_S
    max_delay_s: float = LINDAS_MAX_DELAY_S
    total_deadline_s: float = LINDAS_TOTAL_DEADLINE_S
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {self.capacity}")
        if self.refill_period_s <= 0:
            raise ValueError(f"refill_period_s must be > 0, got {self.refill_period_s}")
        if self.max_delay_s < 0:
            raise ValueError(f"max_delay_s must be >= 0, got {self.max_delay_s}")
        if self.total_deadline_s <= 0:
            raise ValueError(
                f"total_deadline_s must be > 0, got {self.total_deadline_s}"
            )
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")


@runtime_checkable
class LindasRateLimiter(Protocol):
    """Small Protocol (D6) so a cross-process limiter can be dropped in
    later without touching any adapter."""

    def call(self, send: Callable[[float], httpx.Response]) -> httpx.Response:
        """Run ``send`` (one HTTP attempt) under pacing + 429/5xx/transport
        retry. ``send`` receives the remaining wall-clock budget in seconds
        (this call's total deadline minus elapsed so far) so it can bound its
        own request timeout — the limiter cannot otherwise stop a single
        blocking HTTP call from overrunning the deadline. Returns the first
        non-retryable response (any status, including a non-2xx the caller
        must still ``raise_for_status()`` itself). Raises
        ``LindasRateLimitExhaustedError`` if every attempt was retryable and
        either bound (attempts, wall-clock deadline) was hit.
        """
        raise NotImplementedError


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= _RETRYABLE_STATUS_FLOOR


def _parse_retry_after(
    header: str, *, now: UtcDatetime, max_delay_s: float, floor_s: float
) -> float:
    """D7: honour delta-seconds and HTTP-date forms, clamp to
    ``max_delay_s``; malformed/negative/past values fall back to ``floor_s``
    — never trust the header past those bounds."""
    stripped = header.strip()
    if stripped.isdigit():
        try:
            seconds = float(int(stripped))
        except (OverflowError, ValueError):
            # Major fix: a `Retry-After` with hundreds of digits converts to
            # a Python int fine (arbitrary precision) but `float()` of that
            # int raises OverflowError once it exceeds float's ~1.8e308
            # range; a string past CPython's int-string-conversion digit
            # limit (default 4300) raises ValueError out of `int()` itself.
            # Both are untrusted-input shapes (D7) that must clamp, never
            # escape as a raw exception past this boundary.
            return max_delay_s
        if seconds < 0:
            return floor_s
        # D7 floor: never accept an upstream-supplied wait shorter than the
        # measured refill floor either — a `Retry-After: 0` or `1` is just as
        # untrusted as an oversized one, and honouring it verbatim would let
        # the retry loop hammer the endpoint faster than it can recover.
        return min(max(seconds, floor_s), max_delay_s)

    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError):
        return floor_s
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta_s = (parsed - now).total_seconds()
    if delta_s <= 0:
        return floor_s
    return min(max(delta_s, floor_s), max_delay_s)


def _run_with_deadline(
    send: Callable[[float], httpx.Response], remaining_s: float
) -> httpx.Response:
    """Default ``deadline_runner`` (blocker fix, round 2): the actual
    enforcement mechanism for the 120 s wall-clock deadline. Runs ``send`` on
    a background thread and joins with a hard ``remaining_s`` timeout — this
    bounds the CALLING thread's wait to ``remaining_s`` regardless of which
    HTTP phase (connect/read/write/pool) ``send`` is blocked in, which
    passing ``timeout=`` to an HTTP client cannot do (HTTPX 0.28 applies a
    single float independently to each phase, not to the request as a
    whole). The background thread is daemonic; if it never returns, it is
    abandoned and exits on its own once the HTTP client's own configured
    phase timeout eventually fires — this function's caller has already
    moved on.
    """
    outcome: list[httpx.Response | BaseException] = []

    def _runner() -> None:
        try:
            outcome.append(send(remaining_s))
        except BaseException as exc:  # noqa: BLE001 - forwarded to the joiner
            outcome.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(remaining_s)
    if thread.is_alive():
        raise httpx.TimeoutException(
            f"LINDAS request did not complete within its {remaining_s:.1f}s "
            "remaining deadline budget"
        )
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


class TokenBucketLindasLimiter:
    """Process-local token-bucket limiter (D6) — capacity 3, refill 1 per
    ``LINDAS_RETRY_FLOOR_S``, plus D7-bounded 429/5xx/transport retry."""

    def __init__(
        self,
        *,
        config: LindasLimiterConfig | None = None,
        clock: Callable[[], UtcDatetime] = lambda: ensure_utc(datetime.now(UTC)),
        sleeper: Callable[[float], None] = time.sleep,
        deadline_runner: Callable[
            [Callable[[float], httpx.Response], float], httpx.Response
        ]
        | None = None,
    ) -> None:
        self._config = config or LindasLimiterConfig()
        self._clock = clock
        self._sleeper = sleeper
        # Injectable (testability — CLAUDE.md DI convention) so unit tests
        # can simulate a slow/hanging `send` deterministically via the fake
        # clock instead of real threads + real wall-clock sleeps.
        self._deadline_runner = deadline_runner or _run_with_deadline
        self._tokens: float = float(self._config.capacity)
        self._last_refill: UtcDatetime = self._clock()
        # Major fix: guards the refill/reservation/drain critical section so
        # concurrent callers cannot each observe the same empty bucket,
        # sleep in parallel, and all proceed as though each had reserved a
        # distinct refilled token — see `TestConcurrentAcquisition`.
        self._lock = threading.Lock()

    def _remaining_or_raise(
        self,
        *,
        start: UtcDatetime,
        attempt: int,
        last_status: int | None,
        last_exc: Exception | None,
    ) -> float:
        elapsed_s = (self._clock() - start).total_seconds()
        remaining_s = self._config.total_deadline_s - elapsed_s
        if remaining_s <= 0:
            completed = attempt - 1
            log.warning(
                "lindas.exhausted",
                attempts=completed,
                last_status=last_status,
                bound="deadline",
                elapsed_s=elapsed_s,
            )
            raise LindasRateLimitExhaustedError(
                f"LINDAS request exceeded its {self._config.total_deadline_s}s "
                f"total deadline before attempt {attempt} "
                f"(last_status={last_status}, bound=deadline)",
                attempts=completed,
                last_status=last_status,
                last_exc=last_exc,
                bound="deadline",
            )
        return remaining_s

    def call(self, send: Callable[[float], httpx.Response]) -> httpx.Response:
        # Blocker fix: timing starts BEFORE token acquisition, so a starved
        # bucket's wait counts against the 120 s deadline exactly like a slow
        # retry sequence does — previously `start` was set only after
        # `_acquire_token()` returned, letting that wait happen for free.
        start = self._clock()
        attempt = 0
        last_status: int | None = None
        last_exc: Exception | None = None
        while True:
            attempt += 1
            remaining_s = self._remaining_or_raise(
                start=start, attempt=attempt, last_status=last_status, last_exc=last_exc
            )

            # Major fix (round 2): a token is acquired before EVERY attempt,
            # not just the call's first — a retried HTTP request also spends
            # real upstream capacity, and if the local bucket only ever
            # charges once per call() the next independent call() inherits
            # phantom credit for every retry the previous call() made. The
            # wait this can force is itself deadline-aware (never sleeps past
            # the remaining budget).
            self._acquire_token(deadline_remaining_s=remaining_s)
            remaining_s = self._remaining_or_raise(
                start=start, attempt=attempt, last_status=last_status, last_exc=last_exc
            )

            response: httpx.Response | None
            try:
                # Blocker fix: `send` never runs on the calling thread
                # directly — `_deadline_runner` bounds its wall-clock time to
                # `remaining_s` regardless of which HTTP phase it is blocked
                # in (see module docstring / `_run_with_deadline`).
                response = self._deadline_runner(send, remaining_s)
            except httpx.HTTPError as exc:
                last_exc = exc
                last_status = None
                response = None
            else:
                last_exc = None
                last_status = response.status_code
                if not _is_retryable_status(response.status_code):
                    return response
                if response.status_code == 429:
                    # Major fix: a 429 is upstream proof the bucket is
                    # actually empty right now, regardless of what the local
                    # token count says — this call may have already made
                    # several real HTTP attempts against the shared budget
                    # while only ever charging it for one. Drain the local
                    # model to match reality so the NEXT independent call()
                    # cannot spend tokens that were never really available.
                    self._drain(self._clock())

            delay = self._compute_delay(response)
            elapsed_s = (self._clock() - start).total_seconds()

            if attempt >= self._config.max_attempts:
                log.warning(
                    "lindas.exhausted",
                    attempts=attempt,
                    last_status=last_status,
                    bound="attempts",
                )
                raise LindasRateLimitExhaustedError(
                    f"LINDAS request exhausted after {attempt} attempt(s) "
                    "without a non-retryable response "
                    f"(last_status={last_status}, bound=attempts)",
                    attempts=attempt,
                    last_status=last_status,
                    last_exc=last_exc,
                    bound="attempts",
                )
            if elapsed_s + delay > self._config.total_deadline_s:
                log.warning(
                    "lindas.exhausted",
                    attempts=attempt,
                    last_status=last_status,
                    bound="deadline",
                    elapsed_s=elapsed_s,
                )
                raise LindasRateLimitExhaustedError(
                    f"LINDAS request exceeded its {self._config.total_deadline_s}s "
                    f"total deadline after {attempt} attempt(s) "
                    f"(last_status={last_status}, bound=deadline)",
                    attempts=attempt,
                    last_status=last_status,
                    last_exc=last_exc,
                    bound="deadline",
                )

            # Minor fix: `lindas.throttled` is reserved for a 429 — the
            # signal an operator reads as "rate limited, self-resolving".
            # A 5xx or transport failure is a distinct condition ("endpoint
            # broken", per the module's own docstring) and gets its own
            # event so the two are not conflated in the mini's logs.
            event = "lindas.throttled" if last_status == 429 else "lindas.retrying"
            log.warning(
                event,
                attempt=attempt,
                status=last_status,
                delay_s=delay,
            )
            self._sleeper(delay)

    def _compute_delay(self, response: httpx.Response | None) -> float:
        floor = self._config.refill_period_s
        if response is None:
            return floor
        header = response.headers.get("Retry-After")
        if header is None:
            return floor
        return _parse_retry_after(
            header,
            now=self._clock(),
            max_delay_s=self._config.max_delay_s,
            floor_s=floor,
        )

    def _acquire_token(self, *, deadline_remaining_s: float) -> None:
        # Major fix: the entire refill-check-consume-or-wait decision runs
        # under `self._lock` as one atomic critical section. Without it, two
        # threads could both see `self._tokens < 1.0`, both compute a wait
        # and sleep CONCURRENTLY (not serialized), and both then force-
        # consume once they wake — dispensing two tokens for one refilled
        # slot and breaking the "process-local offered load is bounded"
        # guarantee. Holding the lock across the sleep serializes exactly
        # the sequence the single-threaded algorithm below already assumes:
        # only one thread at a time may observe a given bucket state, wait
        # out its own deficit, and consume.
        with self._lock:
            self._refill(self._clock())
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) * self._config.refill_period_s
            # Blocker fix (round 2): never sleep past the call's remaining
            # wall-clock budget — an empty bucket's wait must count against
            # the 120 s deadline like everything else, not silently exceed it
            # via one unbounded sleep. The subsequent `_remaining_or_raise`
            # check catches a capped wait that still wasn't enough.
            wait = min(wait, max(deadline_remaining_s, 0.0))
            self._sleeper(wait)
            self._refill(self._clock())
            self._tokens = max(0.0, self._tokens - 1.0)

    def _drain(self, now: UtcDatetime) -> None:
        """Zero the local bucket at ``now`` — used when a 429 proves the
        upstream bucket is actually empty. Refills forward from this point,
        so the next independent ``call()`` only gets credit for elapsed time
        genuinely observed after the drain. Lock-guarded (major fix) for the
        same reason as `_acquire_token`: this mutates the same shared
        `_tokens`/`_last_refill` state a concurrent caller may be mid-refill
        on."""
        with self._lock:
            self._refill(now)
            self._tokens = 0.0
            self._last_refill = now

    def _refill(self, now: UtcDatetime) -> None:
        elapsed_s = (now - self._last_refill).total_seconds()
        if elapsed_s > 0:
            self._tokens = min(
                float(self._config.capacity),
                self._tokens + elapsed_s / self._config.refill_period_s,
            )
        self._last_refill = now
