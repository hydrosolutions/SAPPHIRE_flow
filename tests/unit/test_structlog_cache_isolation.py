from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from sapphire_flow.logging import configure_cli_logging, configure_test_logging


class TestStructlogCacheNeverSticksMidTest:
    """Plan 201 fixer follow-up.

    The original fix (an autouse ``configure_test_logging()`` at test SETUP)
    only closes the window BETWEEN tests. It does nothing about a test whose
    OWN body calls a production configurator (``configure_cli_logging()`` and
    friends set ``cache_logger_on_first_use=True``, matching production) and
    then first-binds some logger before reading it back with
    ``capture_logs()`` — the per-proxy cache takes effect at that very bind,
    not at the next fixture setup, so a per-test reset at setup time cannot
    see it coming. ``tests/conftest.py``'s
    ``_reset_structlog_config_before_each_test`` now also intercepts every
    ``structlog.configure()`` call made for the DURATION of the test and
    forces the flag off, so this can no longer happen even when both halves
    of the sequence occur inside a single test.
    """

    def test_production_configure_then_first_bind_then_capture_still_works(
        self,
    ) -> None:
        # A single module-level proxy, exactly like the `log = structlog.
        # get_logger(__name__)` pattern production modules use (bound once,
        # reused for every subsequent call on this object).
        log = structlog.get_logger("plan201.fixer.regression")

        # Simulate what the Plan 201 trigger test does: call a PRODUCTION
        # configurator, which in real code sets cache_logger_on_first_use=True.
        configure_cli_logging()
        # First bind while that config is nominally "live" — the exact
        # moment structlog would permanently cache the proxy's .bind if the
        # True flag actually took effect.
        log.info("pre-capture bind, must not cache")

        # Simulate the NEXT test's fixture setup — a fresh, independent
        # structlog.configure() call, exactly what
        # `_reset_structlog_config_before_each_test` performs. This is the
        # reviewer's literal reproduction: "configure_cli_logging(), first
        # bind, configure_test_logging(), capture_logs() returned []" — the
        # intervening configure() call replaces the global processor chain
        # with a NEW list object, so a merely-coincidental shared-list
        # aliasing can no longer paper over a logger that actually cached
        # itself.
        configure_test_logging()

        with capture_logs() as captured:
            log.info("captured event")

        events = [entry["event"] for entry in captured]
        assert events == ["captured event"]
