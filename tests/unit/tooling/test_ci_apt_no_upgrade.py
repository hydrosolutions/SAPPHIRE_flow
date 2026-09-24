"""The CI apt step must install what is missing and upgrade nothing.

On 2026-09-24 two `unit` shards failed with a 404 on
`libexpat1`/`libexpat1-dev 2.6.1-2ubuntu0.6`: Ubuntu had published a newer
security build and pulled the old `.deb` while the index the job had just
fetched still named it. Everything we actually need — eccodes, geos and their
dependencies — downloaded fine. Apt only tried to UPGRADE `libexpat1`, which
the runner image already carries, because the step names it explicitly.

`--no-upgrade` removes that entire class of failure. This file exists because
the flag lives at **three** sites across two workflow files, which is exactly
the shape where a later edit fixes two and misses the third — and the miss
would not show up as a test failure, only as an intermittent red build weeks
later.

⚠️ Asserted over the parsed YAML and selected by step NAME, never by a regex
over the raw file: the same text now appears in the explanatory comment beside
each command, so a substring search over the file would pass on the comment
alone while the real flag was gone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STEP_NAME = "Install system deps for cfgrib / rioxarray / exactextract"

# (workflow file, job id) for every job carrying the apt step.
_SITES = (
    (".github/workflows/ci.yml", "unit"),
    (".github/workflows/ci.yml", "integration"),
    (".github/workflows/integration-nightly.yml", "integration-nightly"),
)


def _apt_run(workflow: str, job: str) -> str:
    loaded = yaml.safe_load((_REPO_ROOT / workflow).read_text())
    steps: list[dict[str, Any]] = loaded["jobs"][job]["steps"]
    matching = [step for step in steps if step.get("name") == _STEP_NAME]
    assert len(matching) == 1, (
        f"{workflow}:{job} has {len(matching)} steps named {_STEP_NAME!r}"
    )
    return matching[0]["run"]


def _install_command(run: str) -> str:
    """The apt-get install invocation, with its comment lines stripped.

    The comment beside it mentions the flag, so the assertions below would pass
    on the prose alone if the command text were not isolated first.
    """
    lines = [line for line in run.splitlines() if not line.lstrip().startswith("#")]
    joined = " ".join(line.rstrip(" \\") for line in lines)
    _, _, after = joined.partition("apt-get")
    return after


class TestAptStepNeverUpgrades:
    @pytest.mark.parametrize(("workflow", "job"), _SITES)
    def test_the_install_passes_no_upgrade(self, workflow: str, job: str) -> None:
        command = _install_command(_apt_run(workflow, job))

        assert "install" in command
        assert "--no-upgrade" in command, (
            f"{workflow}:{job} may upgrade packages the runner already has; "
            "that is what 404'd on 2026-09-24"
        )

    @pytest.mark.parametrize(("workflow", "job"), _SITES)
    def test_the_packages_we_need_are_still_requested(
        self, workflow: str, job: str
    ) -> None:
        """⛔ `--no-upgrade` must not be paired with quietly dropping a package.

        `libexpat1` is the one that 404'd, and removing it from the list would
        also make this build go green — by weakening the declaration instead of
        fixing the cause. The Dockerfile records it as a rasterio runtime
        requirement, and a Python wheel never asks apt for its system deps.
        """
        command = _install_command(_apt_run(workflow, job))

        for package in ("libeccodes0", "libexpat1", "libgeos-c1v5"):
            assert package in command, f"{workflow}:{job} no longer installs {package}"

    @pytest.mark.parametrize(("workflow", "job"), _SITES)
    def test_apt_get_update_is_still_retried_in_a_fresh_process(
        self, workflow: str, job: str
    ) -> None:
        """The pre-existing protection, asserted so this change cannot erode it.

        A stalled `update` used to eat the whole job budget; the retry loop runs
        a FRESH process each attempt so DNS re-resolves. `--no-upgrade` is a
        different failure mode and must not replace it.
        """
        run = _apt_run(workflow, job)

        assert "apt-get" in run
        assert "update" in run
        assert "for attempt in" in run
