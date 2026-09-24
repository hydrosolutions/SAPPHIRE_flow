"""The Prefect home must be scoped PER XDIST WORKER, not per checkout.

All workers of one `-n auto` run otherwise share a single SQLite database. Two
workers that each start an ephemeral Prefect server then contend, and one fails
with `RuntimeError: Failed to reach API` — a failure that reads as a regression
in whatever you happen to be building. It is distribution-dependent: adding or
removing any test reshuffles xdist's allocation and can expose or hide it.

⚠️ Two limits of these tests, stated rather than discovered:

1. **A SERIAL run cannot catch the scoping bug.** It only takes the serial
   branch. The parallel assertion binds under `-n auto`, which is what CI runs.
2. **They must not assume the home is ours.** A caller may set `PREFECT_HOME`
   explicitly and our scoping then deliberately does not apply —
   `tools/standing_snapshot.py:559` does exactly that, pointing it at a scratch
   directory, and an earlier version of these tests would have broken that tool.
"""

from __future__ import annotations

import os
import pathlib

import pytest

# The marker vouches for a specific PATH, not merely "a default was set" — it
# is inherited by child processes, and a bare flag would wrongly authorise
# overriding a nested run's own explicit home.
_OURS = os.environ.get("_SAPPHIRE_PREFECT_HOME_IS_DEFAULT") == os.environ.get(
    "PREFECT_HOME"
)


def test_prefect_home_is_scoped_to_this_worker() -> None:
    """Under xdist the path carries the worker id; serially it does not."""
    if not _OURS:
        pytest.skip("PREFECT_HOME was set explicitly; our scoping does not apply")

    home = pathlib.Path(os.environ["PREFECT_HOME"]).name
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")

    if worker:
        assert home == f".prefect-{worker}", (
            f"worker {worker} shares {home} with every other worker"
        )
    else:
        assert home == ".prefect"


def test_the_default_home_is_never_the_shared_user_home() -> None:
    """The original defect: an unscoped `~/.prefect` shared by every checkout on
    the machine. It reached 17 GB once and presented as hangs, not failures.

    ⛔ Asserted as "not the shared home", NOT as "inside this checkout" — an
    explicit home may legitimately live anywhere, and asserting containment
    would break any caller that sets one.
    """
    if not _OURS:
        pytest.skip("PREFECT_HOME was set explicitly; our scoping does not apply")

    home = pathlib.Path(os.environ["PREFECT_HOME"]).resolve()

    assert home != pathlib.Path.home() / ".prefect"
