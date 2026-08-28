"""Ops tests for the Nepal 12300 feed's dead-man ping.

Tests assert the Healthchecks.io reporting contract in
``scripts/launchd/run-nepal-forcing.sh``:

  (a) no URL file          -> no ping at all, run behaves exactly as before
  (b) successful run       -> `/start` then a bare check-in, exit 0
  (c) failed run           -> `/start` then `/fail`, exit 1
  (d) credential guard hit -> `/fail`, and docker is NEVER invoked
  (e) the ping itself fails -> the RUN's exit status is unchanged

(e) is the load-bearing one: a dead-man outage must never break the process
it monitors. (a) is what keeps CI and dev checkouts working, where the URL
file is absent by design.

Shell-script tests, same harness convention as
``test_recap_probe_wrapper.py``: we shell out to the real wrapper, and fake
both `docker` and `curl` via absolute-path stubs injected through
``DOCKER_CMD`` / ``CURL_CMD`` (not PATH injection). Every host path is
overridden so CI never touches ``/Users/sapphire/...``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_WRAPPER_SCRIPT = _SCRIPTS_DIR / "run-nepal-forcing.sh"

_PING_URL = "https://hc-ping.com/00000000-1111-2222-3333-444444444444"
_GOOD_RECORD = '{"hru": "12300", "ok": true, "rows": 8568, "members": 51}'


def _write_fake_docker(
    bin_dir: Path, *, args_log: Path, stderr: str, exit_code: int
) -> Path:
    """Stand in for `docker inspect` (image resolution) and `docker run`
    (the fetch). Records every invocation so a test can assert docker was
    never reached at all."""
    fake = bin_dir / "docker"
    fake.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                f'printf \'%s\\n\' "$1" >> "{args_log}"',
                'if [[ "$1" == "inspect" ]]; then',
                "    echo sapphire-flow:0.0.0-test",
                "    exit 0",
                "fi",
                f"cat >&2 <<'__STDERR__'\n{stderr}\n__STDERR__",
                f"exit {exit_code}",
                "",
            ]
        )
    )
    fake.chmod(0o755)
    return fake


def _write_fake_curl(bin_dir: Path, *, args_log: Path, exit_code: int = 0) -> Path:
    """Stand in for curl. Logs the URL it was handed (always the LAST
    argument in this wrapper) so tests can assert on the ping suffix."""
    fake = bin_dir / "curl"
    fake.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                'for arg in "$@"; do url="$arg"; done',
                f'printf \'%s\\n\' "$url" >> "{args_log}"',
                f"exit {exit_code}",
                "",
            ]
        )
    )
    fake.chmod(0o755)
    return fake


def _run_wrapper(
    tmp_path: Path,
    *,
    docker_cmd: Path,
    curl_cmd: Path,
    deadman_url_file: Path | None,
    key_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    run_script = repo / "nepal_forcing_run.py"
    run_script.write_text("# stub run script\n")

    if key_file is None:
        key_file = tmp_path / "api_key"
        key_file.write_text("fake-key\n")
    db_password = tmp_path / "db_password"
    db_password.write_text("fake-password\n")

    env = {
        **os.environ,
        "DOCKER_CMD": str(docker_cmd),
        "CURL_CMD": str(curl_cmd),
        "NEPAL_REPO": str(repo),
        "NEPAL_RUN_SCRIPT": str(run_script),
        "NEPAL_KEY_FILE": str(key_file),
        "NEPAL_DB_PASSWORD_FILE": str(db_password),
        "NEPAL_HOST_LOG": str(tmp_path / "feed.jsonl"),
        "NEPAL_HOST_SUMMARY": str(tmp_path / "feed.summary.log"),
        "NEPAL_IMAGE": "sapphire-flow:0.0.0-test",
        # A path that does not exist == feature-off, the CI default.
        "NEPAL_DEADMAN_URL_FILE": str(
            deadman_url_file
            if deadman_url_file is not None
            else tmp_path / "no_such_url_file"
        ),
    }
    return subprocess.run(
        ["bash", str(_WRAPPER_SCRIPT)], capture_output=True, text=True, env=env
    )


def _url_file(tmp_path: Path, url: str = _PING_URL) -> Path:
    path = tmp_path / "nepal_deadman_url"
    path.write_text(url + "\n")  # trailing newline must be stripped
    return path


def _pings(args_log: Path) -> list[str]:
    if not args_log.exists():
        return []
    return [line for line in args_log.read_text().splitlines() if line]


class TestFeatureOff:
    """No URL file configured -> the wrapper must behave exactly as it did
    before the dead-man existed. This is the CI/dev default."""

    def test_absent_url_file_pings_nothing_and_still_succeeds(
        self, tmp_path: Path
    ) -> None:
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log),
            deadman_url_file=None,
        )
        assert result.returncode == 0
        assert _pings(curl_log) == []

    def test_empty_url_file_pings_nothing(self, tmp_path: Path) -> None:
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log),
            deadman_url_file=_url_file(tmp_path, url=""),
        )
        assert result.returncode == 0
        assert _pings(curl_log) == []


class TestOutcomeReporting:
    def test_successful_run_pings_start_then_plain_checkin(
        self, tmp_path: Path
    ) -> None:
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log),
            deadman_url_file=_url_file(tmp_path),
        )
        assert result.returncode == 0
        assert _pings(curl_log) == [f"{_PING_URL}/start", _PING_URL]

    def test_failed_run_pings_fail(self, tmp_path: Path) -> None:
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        failure_record = (
            '{"hru": "12300", "ok": false, "error_code": "source_data_missing"}'
        )
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=failure_record, exit_code=1
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log),
            deadman_url_file=_url_file(tmp_path),
        )
        assert result.returncode == 1
        assert _pings(curl_log) == [f"{_PING_URL}/start", f"{_PING_URL}/fail"]

    def test_missing_key_file_pings_fail_without_invoking_docker(
        self, tmp_path: Path
    ) -> None:
        """The 2026-08-28 shape: a credential file vanishes. The feed must
        report `/fail` rather than dying quietly before it ever pings."""
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log),
            deadman_url_file=_url_file(tmp_path),
            key_file=tmp_path / "vanished_key",
        )
        assert result.returncode == 1
        assert _pings(curl_log) == [f"{_PING_URL}/fail"]
        assert _pings(docker_log) == []


class TestPingNeverBreaksTheRun:
    """A dead-man outage must never break the process it monitors."""

    def test_failing_ping_leaves_a_good_run_exit_zero(self, tmp_path: Path) -> None:
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log, exit_code=7),
            deadman_url_file=_url_file(tmp_path),
        )
        assert result.returncode == 0
        assert _pings(curl_log) == [f"{_PING_URL}/start", _PING_URL]

    def test_failing_ping_does_not_leak_the_url_into_output(
        self, tmp_path: Path
    ) -> None:
        """The ping URL is a capability secret — anyone holding it can forge
        check-ins — so it must never reach the launchd log."""
        docker_log, curl_log = tmp_path / "docker.log", tmp_path / "curl.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=_write_fake_docker(
                tmp_path, args_log=docker_log, stderr=_GOOD_RECORD, exit_code=0
            ),
            curl_cmd=_write_fake_curl(tmp_path, args_log=curl_log, exit_code=7),
            deadman_url_file=_url_file(tmp_path),
        )
        assert "dead-man ping failed" in result.stderr
        assert _PING_URL not in result.stderr
        assert _PING_URL not in result.stdout
