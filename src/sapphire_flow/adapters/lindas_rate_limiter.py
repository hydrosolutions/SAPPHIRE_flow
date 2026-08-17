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
- *Bucket pacing* — ``call()`` acquires one token at the very start, before
  its first attempt. This paces the rate of independent ``call()``
  invocations (e.g. many stations polled in a tight loop) against each
  other. It fires at most once per ``call()``.
- *Retry backoff* — once a request fails retryably, the wait before the next
  attempt is governed entirely by :data:`LINDAS_RETRY_FLOOR_S` / the
  response's ``Retry-After`` (D7), never by the bucket. Retry backoff already
  waits at or above the bucket's own refill period, so gating retries through
  the bucket a second time would only double-count the same wait.

**The 120 s deadline covers the ENTIRE call, including bucket wait.**
``call()`` starts its clock before ``_acquire_token()``, not after, and
re-checks the remaining budget before every attempt (including the first) —
a starved bucket counts against the deadline exactly like a slow retry
sequence does. ``send`` receives that remaining budget (seconds) on every
invocation so it can bound its own HTTP timeout; a caller-side network stack
that ignores it is the one thing this module cannot force from outside a
synchronous call.

**A 429 proves the upstream bucket is empty, not just this process's local
count.** ``call()`` drains its local bucket to zero the instant a 429 comes
back, regardless of how many tokens the local model thought were left. A
``call()`` that took several retryable attempts (each a real HTTP request the
upstream bucket paid for) must not leave the next independent ``call()`` free
to spend tokens the local bucket never actually charged for those attempts.
"""

from __future__ import annotations

import email.utils
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
        seconds = float(int(stripped))
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


class TokenBucketLindasLimiter:
    """Process-local token-bucket limiter (D6) — capacity 3, refill 1 per
    ``LINDAS_RETRY_FLOOR_S``, plus D7-bounded 429/5xx/transport retry."""

    def __init__(
        self,
        *,
        config: LindasLimiterConfig | None = None,
        clock: Callable[[], UtcDatetime] = lambda: ensure_utc(datetime.now(UTC)),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config or LindasLimiterConfig()
        self._clock = clock
        self._sleeper = sleeper
        self._tokens: float = float(self._config.capacity)
        self._last_refill: UtcDatetime = self._clock()

    def call(self, send: Callable[[float], httpx.Response]) -> httpx.Response:
        # Blocker fix: timing starts BEFORE token acquisition, so a starved
        # bucket's wait counts against the 120 s deadline exactly like a slow
        # retry sequence does — previously `start` was set only after
        # `_acquire_token()` returned, letting that wait happen for free.
        start = self._clock()
        self._acquire_token()
        attempt = 0
        last_status: int | None = None
        last_exc: Exception | None = None
        while True:
            attempt += 1
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

            response: httpx.Response | None
            try:
                # Blocker fix: `send` is handed the remaining budget so it can
                # bound its own HTTP timeout — the limiter cannot otherwise
                # stop one blocking request from overrunning the deadline.
                response = send(remaining_s)
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

            log.warning(
                "lindas.throttled",
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

    def _acquire_token(self) -> None:
        self._refill(self._clock())
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        wait = (1.0 - self._tokens) * self._config.refill_period_s
        self._sleeper(wait)
        self._refill(self._clock())
        self._tokens = max(0.0, self._tokens - 1.0)

    def _drain(self, now: UtcDatetime) -> None:
        """Zero the local bucket at ``now`` — used when a 429 proves the
        upstream bucket is actually empty. Refills forward from this point,
        so the next independent ``call()`` only gets credit for elapsed time
        genuinely observed after the drain."""
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
