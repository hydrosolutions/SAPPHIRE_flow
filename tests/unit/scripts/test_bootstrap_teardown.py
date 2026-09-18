"""`teardown_stack` (bootstrap-mac-mini.sh) must report a failed teardown.

Shell-script tests, mirroring the source-and-call convention in
tests/unit/scripts/test_bootstrap_backup_target_verified.py — the other test
module for this same script: it returns early when sourced, so
`teardown_stack` is defined above that guard and is called directly here with
a fake `docker`/`launchctl`/`id` trio first on PATH.

The defect being locked: the uninstall path used to run `docker compose down
|| true` and then print "uninstall complete" unconditionally, so a teardown
that left containers running reported success. Two behaviours are required —
a non-zero `down` is a failure, and an exit-0 `down` is NOT by itself proof
the containers are gone. A FAILED verification must read as UNKNOWN, never as
"nothing running"; that is the silent-success bug already fixed once in
prune-docker.sh (tests/unit/ops/test_launchd_prune_docker.py).

The same two rules apply to launchd, and that half had its own holes:

* only three of the five ch.hydrosolutions.* LaunchAgents this repo ships
  were booted out, while the summary told the operator to delete the whole
  `ch.hydrosolutions.*.plist` glob — stranding the recap-probe and
  nepal-forcing jobs loaded with no plist to boot them out from;
* verification was gated on the plist existing, so a job that was loaded but
  whose plist had already been removed was counted as gone.

The FAKE is the other half of the contract. An earlier version of this module
switched only on the launchctl SUBCOMMAND and ignored its target, so nothing
here could tell `bootout gui/501/<label>` from `bootout gui/501/<label>-NOPE`,
from `bootout user/501/<label>`, or from a `print` aimed at a label that never
existed: four mutations of the script survived the whole suite. The fake below
therefore (a) validates the full target string and (b) models per-label
registration state — a label stays loaded until the CORRECT
`bootout <domain>/<label>` is issued — so a test can prove the bootout
actually unloaded the thing rather than that a message was printed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-mac-mini.sh"

# Every label the repo ships a plist for. install-launchd.sh installs the
# first three; the last two are installed by hand from their runbooks. All
# five run on the mini, so all five must be torn down.
SHIPPED_LABELS = (
    "ch.hydrosolutions.sapphire",
    "ch.hydrosolutions.sapphire-watchdog",
    "ch.hydrosolutions.sapphire-docker-prune",
    "ch.hydrosolutions.sapphire-recap-probe",
    "ch.hydrosolutions.sapphire-nepal-forcing",
)
ALL_SHIPPED = " ".join(SHIPPED_LABELS)

_FAKE_DOCKER = """#!/bin/bash
# Validates the REAL call shape before answering:
#   compose -f <repo>/docker-compose.yml -f <repo>/docker-compose.macmini.yml \\
#           (down | ps -q -a)
# A crude token-matcher would keep passing after the macmini overlay was
# dropped from compose_args, or after the compose path was pointed at a file
# that does not exist, so the file BASENAMES and their existence are both
# checked -- not merely "at least one -f".
[ -n "${DOCKER_LOG:-}" ] && printf '%s\\n' "$*" >> "${DOCKER_LOG}"
if [ "$1" != "compose" ]; then
  echo "fake docker: expected 'compose', got '$1'" >&2; exit 90
fi
shift
files=""
while [ "${1:-}" = "-f" ]; do
  [ -n "${2:-}" ] || { echo "fake docker: -f with no file" >&2; exit 91; }
  [ -f "$2" ] || { echo "fake docker: compose file missing: $2" >&2; exit 95; }
  files="${files}${files:+ }$(basename "$2")"
  shift 2
done
expected="docker-compose.yml docker-compose.macmini.yml"
if [ "$files" != "$expected" ]; then
  echo "fake docker: compose files [${files}] != [${expected}]" >&2; exit 92
fi
case "$1" in
  down) exit ${DOWN_RC:-0} ;;
  ps)   { [ "$2" = "-q" ] && [ "$3" = "-a" ]; } || {
          echo "fake docker: expected 'ps -q -a', got 'ps $2 $3'" >&2; exit 93; }
        if [ "${PS_RC:-0}" -ne 0 ]; then
          echo "${PS_ERR:-fake docker: ps exploded}" >&2; exit "${PS_RC}"
        fi
        printf '%s' "${PS_OUT:-}"; exit 0 ;;
  *)    echo "fake docker: unexpected subcommand '$1'" >&2; exit 94 ;;
esac
"""

# Stateful fake launchctl.
#
# Knobs (all env):
#   LAUNCHD_LOADED   space-separated labels this launchd knows and has loaded
#   LAUNCHD_STICKY   labels (or "all") that survive a correct bootout
#   BOOTOUT_ERR      what a failing bootout prints (implies exit 5)
#   PRINT_FAIL_RC    make SERVICE-level `print` fail operationally (not "absent")
#   DOMAIN_PRINT_RC  make DOMAIN-level `print` (the residual sweep) fail
#   FAKE_UID         the only uid whose gui/<uid> domain exists (default 501)
#   LAUNCHCTL_STATE  directory holding the per-label bootout markers
#
# Everything is judged against the FULL target: a wrong label, a wrong uid or
# a wrong domain type does not silently succeed, and a label stays loaded
# until the exact `bootout gui/<uid>/<label>` for it has been issued.
_FAKE_LAUNCHCTL = """#!/bin/bash
[ -n "${LAUNCHCTL_LOG:-}" ] && printf '%s\\n' "$*" >> "${LAUNCHCTL_LOG}"

state="${LAUNCHCTL_STATE:-}"
[ -n "${state}" ] || { echo "fake launchctl: LAUNCHCTL_STATE unset" >&2; exit 90; }
mkdir -p "${state}"
valid_domain="gui/${FAKE_UID:-501}"

known_label() { case " ${LAUNCHD_LOADED:-} " in *" $1 "*) return 0 ;; esac; return 1; }
booted_out()  { [ -f "${state}/booted-$1" ]; }
is_loaded()   { known_label "$1" && ! booted_out "$1"; }
is_sticky() {
  [ "${LAUNCHD_STICKY:-}" = "all" ] && return 0
  case " ${LAUNCHD_STICKY:-} " in *" $1 "*) return 0 ;; esac
  return 1
}
loaded_labels() {
  local l
  for l in ${LAUNCHD_LOADED:-}; do
    is_loaded "${l}" && printf '%s\\n' "${l}"
  done
  return 0
}

sub="${1:-}"
target="${2:-}"
slashes="${target//[!\\/]/}"
nslash="${#slashes}"
domain=""
label=""
if [ "${nslash}" -eq 2 ]; then
  domain="${target%/*}"; label="${target##*/}"
elif [ "${nslash}" -eq 1 ]; then
  domain="${target}"
fi

case "${sub}" in
  print)
    if [ "${nslash}" -eq 2 ] && [ -n "${label}" ]; then
      if [ "${PRINT_FAIL_RC:-0}" -ne 0 ]; then
        echo "${PRINT_FAIL_ERR:-launchctl print: operational failure}" >&2
        exit "${PRINT_FAIL_RC}"
      fi
      if [ "${domain}" != "${valid_domain}" ]; then
        echo "Could not find domain for ${target}" >&2; exit 113
      fi
      if is_loaded "${label}"; then
        printf '%s = {\\n\\tactive count = 1\\n\\tstate = running\\n}\\n' "${target}"
        exit 0
      fi
      echo "Could not find service \\"${label}\\" in domain for gui" >&2
      exit 113
    fi
    if [ "${nslash}" -eq 1 ]; then
      if [ "${DOMAIN_PRINT_RC:-0}" -ne 0 ]; then
        echo "${DOMAIN_PRINT_ERR:-launchctl print: could not reach launchd}" >&2
        exit "${DOMAIN_PRINT_RC}"
      fi
      if [ "${domain}" != "${valid_domain}" ]; then
        echo "Could not find domain for ${target}" >&2; exit 113
      fi
      printf '%s = {\\n\\ttype = user\\n\\thandle = %s\\n\\tservices = {\\n' \\
        "${target}" "${FAKE_UID:-501}"
      loaded_labels | while IFS= read -r l; do printf '\\t\\t0\\t-\\t%s\\n' "${l}"; done
      printf '\\t}\\n}\\n'
      exit 0
    fi
    echo "fake launchctl: malformed print target '${target}'" >&2; exit 91 ;;
  bootout)
    if [ "${nslash}" -ne 2 ] || [ -z "${label}" ]; then
      echo "fake launchctl: malformed bootout target '${target}'" >&2; exit 91
    fi
    if [ "${domain}" != "${valid_domain}" ]; then
      echo "Boot-out failed: 113: Could not find domain for ${target}" >&2; exit 113
    fi
    if ! is_loaded "${label}"; then
      echo "Boot-out failed: 3: No such process" >&2; exit 3
    fi
    if is_sticky "${label}"; then
      # bootout claims success (or fails loudly) but the job survives.
      if [ -n "${BOOTOUT_ERR:-}" ]; then echo "${BOOTOUT_ERR}" >&2; exit 5; fi
      exit 0
    fi
    : > "${state}/booted-${label}"
    exit 0 ;;
  *)
    echo "fake launchctl: unexpected subcommand '${sub}'" >&2; exit 92 ;;
esac
"""

# `id -u`. FAKE_ID_OUT="" models a host where the uid cannot be determined.
_FAKE_ID = """#!/bin/bash
printf '%s\\n' "${FAKE_ID_OUT-501}"
"""


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("docker", _FAKE_DOCKER),
        ("launchctl", _FAKE_LAUNCHCTL),
        ("id", _FAKE_ID),
    ):
        exe = bin_dir / name
        exe.write_text(body)
        exe.chmod(0o755)
    return bin_dir


def _base_env(tmp_path: Path, **env: str) -> dict[str, str]:
    bin_dir = _fake_bin(tmp_path)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LAUNCHCTL_STATE": str(tmp_path / "launchd-state"),
        **env,
    }


def _plists(tmp_path: Path, *labels: str) -> None:
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    for label in labels:
        (agents / f"{label}.plist").write_text("<plist/>")


def _run_teardown(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{SCRIPT}"\n'
        "DRY_RUN=0\n"
        # the script sets -euo pipefail, which is inherited on source: a bare
        # call would kill this shell before the status could be printed.
        "if teardown_stack; then rc=0; else rc=$?; fi\n"
        'echo "RC=$rc"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_base_env(tmp_path, **env),
    )


class TestTeardownStackReportsFailure:
    def test_reports_failure_when_compose_down_exits_non_zero(
        self, tmp_path: Path
    ) -> None:
        r = _run_teardown(tmp_path, DOWN_RC="1")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "docker compose down failed" in r.stderr

    def test_reports_failure_when_containers_survive_a_clean_down(
        self, tmp_path: Path
    ) -> None:
        """Exit 0 from `down` is not proof. This is the case the old
        `|| true` reported as a successful uninstall."""
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="abc123\n")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "containers still running" in r.stderr

    def test_a_failed_verification_is_unknown_not_empty(self, tmp_path: Path) -> None:
        """`docker compose ps` failing must NOT be read as 'no containers'."""
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_RC="2")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not verify" in r.stderr
        assert "NOT assuming" in r.stderr

    def test_succeeds_only_when_the_stack_is_verifiably_down(
        self, tmp_path: Path
    ) -> None:
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="")
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        assert "FAIL" not in r.stderr

    def test_the_container_check_includes_stopped_containers(
        self, tmp_path: Path
    ) -> None:
        """`ps -q` alone calls an exited-but-not-removed container clean.
        `down` is supposed to REMOVE containers, so the post-condition must
        enumerate all of them (`-a`), which is strictly stronger."""
        log = tmp_path / "docker.log"
        _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        ps_calls = [ln for ln in log.read_text().splitlines() if " ps " in f" {ln} "]
        assert ps_calls, log.read_text()
        assert all(c.endswith("ps -q -a") for c in ps_calls), ps_calls

    def test_the_macmini_overlay_reaches_every_compose_call(
        self, tmp_path: Path
    ) -> None:
        """Dropping the overlay from `compose_args` would tear down a
        DIFFERENT project than the one bootstrap brought up."""
        log = tmp_path / "docker.log"
        _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        calls = log.read_text().splitlines()
        assert calls, "fake docker was never invoked"
        for call in calls:
            assert f"-f {REPO_ROOT}/docker-compose.yml" in call, call
            assert f"-f {REPO_ROOT}/docker-compose.macmini.yml" in call, call

    def test_a_residual_hydrosolutions_job_fails_the_teardown(
        self, tmp_path: Path
    ) -> None:
        """The per-label loop only knows the labels this repo ships. launchd
        itself is the authority on what is still registered."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire-some-future-job",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "STILL registered after teardown" in r.stderr
        assert "ch.hydrosolutions.sapphire-some-future-job" in r.stderr

    def test_a_failed_container_check_reports_why(self, tmp_path: Path) -> None:
        """ "could not verify" with no reason sends the operator back to the
        host blind. Whatever docker said must reach them."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_RC="2",
            PS_ERR="Cannot connect to the Docker daemon",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "Cannot connect to the Docker daemon" in r.stderr, r.stderr

    def test_a_failed_launchd_enumeration_is_unknown_not_clean(
        self, tmp_path: Path
    ) -> None:
        """Same rule as the container check: a failed enumeration is UNKNOWN,
        never 'no jobs loaded'."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            DOMAIN_PRINT_RC="3",
            DOMAIN_PRINT_ERR="boom",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not enumerate launchd jobs" in r.stderr
        assert "NOT assuming" in r.stderr
        assert "boom" in r.stderr

    def test_the_residual_sweep_enumerates_the_domain_the_bootouts_used(
        self, tmp_path: Path
    ) -> None:
        """Over SSH with no console login, the calling session's domain and
        `gui/<uid>` are different launchd domains. A sweep that enumerates one
        while the bootouts addressed the other can report "clean" about a
        domain nothing was ever unloaded from."""
        log = tmp_path / "launchctl.log"
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", LAUNCHCTL_LOG=str(log))
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        lines = log.read_text().splitlines()
        assert lines[-1] == "print gui/501", lines
        assert not [ln for ln in lines if ln.startswith("list")], lines

    def test_a_domain_that_does_not_exist_is_never_reported_clean(
        self, tmp_path: Path
    ) -> None:
        """The uid and the domain TYPE are both part of the target. If the
        script addresses a domain this launchd does not have (`user/501`
        instead of `gui/501`, or a uid that is not ours), every bootout and
        every print fails to resolve — which must read as UNKNOWN, not as
        "all five labels confirmed absent"."""
        _plists(tmp_path, *SHIPPED_LABELS)
        r = _run_teardown(
            tmp_path, DOWN_RC="0", PS_OUT="", LAUNCHD_LOADED=ALL_SHIPPED, FAKE_UID="999"
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not verify launchd job was unloaded" in r.stderr, r.stderr
        assert "could not enumerate launchd jobs" in r.stderr, r.stderr

    def test_a_missing_uid_is_unknown_not_a_clean_teardown(
        self, tmp_path: Path
    ) -> None:
        """`id -u` returning nothing makes the domain `gui/` — malformed. Every
        launchctl call then fails, and reading those failures as absence is the
        bug this module exists to prevent."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=ALL_SHIPPED,
            FAKE_ID_OUT="",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not determine the current uid" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr


class TestUninstallEntryPointHonoursTheResult:
    """The defect lived at the CALL SITE, not in the helper: `teardown_stack ||
    true` there would reproduce false success while every helper test above
    still passed (verified by mutation). These run the real script."""

    def _uninstall(
        self, tmp_path: Path, *args: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), "--uninstall", *args],
            capture_output=True,
            text=True,
            env=_base_env(tmp_path, **env),
        )

    def test_exits_non_zero_and_does_not_claim_completion_on_failure(
        self, tmp_path: Path
    ) -> None:
        r = self._uninstall(tmp_path, DOWN_RC="1")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "uninstall complete" not in r.stdout
        assert "INCOMPLETE" in r.stderr

    def test_reports_completion_only_when_teardown_succeeded(
        self, tmp_path: Path
    ) -> None:
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "uninstall complete" in r.stdout

    def test_the_completion_banner_claims_only_what_was_verified(
        self, tmp_path: Path
    ) -> None:
        """The checks cover launchd registrations and the compose project.
        scripts/launchd/run-nepal-forcing.sh does its work in a
        `docker run --rm` one-shot, OUTSIDE the compose project, so
        `docker compose ps` cannot see it: a teardown mid-cycle leaves that
        container alive. The banner must not sweep it into "verified gone"."""
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "NOT covered by those checks" in r.stdout, r.stdout
        assert "run-nepal-forcing.sh" in r.stdout, r.stdout
        assert "docker ps" in r.stdout, r.stdout

    def test_dry_run_does_not_claim_an_uninstall_happened(self, tmp_path: Path) -> None:
        r = self._uninstall(tmp_path, "--dry-run")
        assert "uninstall complete" not in r.stdout, r.stdout
        assert "dry-run complete" in r.stdout

    def test_dry_run_never_touches_launchctl(self, tmp_path: Path) -> None:
        """`--dry-run` must PRINT the bootout, not perform it. Without the
        DRY_RUN guard in `bootout_label`, a dry run would really unload the
        operator's LaunchAgents."""
        _plists(tmp_path, "ch.hydrosolutions.sapphire")
        log = tmp_path / "launchctl.log"
        r = self._uninstall(tmp_path, "--dry-run", LAUNCHCTL_LOG=str(log))
        assert "would run: launchctl bootout" in r.stdout, r.stdout
        assert not log.exists(), (
            f"dry run invoked launchctl: {log.read_text()!r}\n{r.stdout}"
        )

    def test_a_launchd_job_that_survives_bootout_fails_the_uninstall(
        self, tmp_path: Path
    ) -> None:
        """Docker stopping cleanly is not enough: a LaunchAgent still
        registered afterwards must also fail the uninstall. Without this the
        docker half is honest and the launchd half silently is not."""
        _plists(tmp_path, "ch.hydrosolutions.sapphire")
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire",
            LAUNCHD_STICKY="all",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "still registered after bootout" in r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_surviving_job_reports_what_bootout_said(self, tmp_path: Path) -> None:
        """Same rule as the container check: the operator gets the reason, not
        just the verdict."""
        _plists(tmp_path, "ch.hydrosolutions.sapphire")
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire",
            LAUNCHD_STICKY="all",
            BOOTOUT_ERR="Boot-out failed: 5: Input/output error",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "Boot-out failed: 5: Input/output error" in r.stderr, r.stderr

    @pytest.mark.parametrize("label", SHIPPED_LABELS)
    def test_every_shipped_launchagent_is_verified_gone(
        self, tmp_path: Path, label: str
    ) -> None:
        """One case per label. Dropping any of them from the teardown loop —
        including the two installed by hand from their runbooks, which the
        uninstall summary nonetheless tells the operator to `rm` — lets a live
        job survive an uninstall that reports success.

        The assertion matches the WHOLE line. "still registered after bootout:
        ch.hydrosolutions.sapphire" is a PREFIX of the message for every other
        label (`...-watchdog`, `...-recap-probe`, ...), so a substring check
        let the main-stack case be satisfied by any of the other four: the
        label could be deleted from SAPPHIRE_LAUNCHD_LABELS with all 22 tests
        still green."""
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=ALL_SHIPPED,
            LAUNCHD_STICKY="all",
        )
        expected = (
            f"[bootstrap] FAIL launchd job still registered after bootout: {label}"
        )
        assert expected in r.stderr.splitlines(), r.stderr

    def test_the_launchctl_calls_name_the_exact_domain_and_label(
        self, tmp_path: Path
    ) -> None:
        """Every launchctl call is judged on its FULL target. A bootout or a
        print aimed one character off — `<label>-NOPE`, `user/` instead of
        `gui/`, an empty uid — unloads or interrogates nothing, and the only
        way to see that is to read back what was actually run."""
        _plists(tmp_path, *SHIPPED_LABELS)
        log = tmp_path / "launchctl.log"
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=ALL_SHIPPED,
            LAUNCHCTL_LOG=str(log),
        )
        assert r.returncode == 0, r.stdout + r.stderr
        expected = [
            call
            for label in SHIPPED_LABELS
            for call in (
                f"bootout gui/501/{label}",
                f"print gui/501/{label}",
            )
        ] + ["print gui/501"]
        assert log.read_text().splitlines() == expected

    def test_bootout_unloads_only_the_label_it_named(self, tmp_path: Path) -> None:
        """State, not messages: after the run, the label whose bootout took
        effect is absent and the one that survived is still loaded. A fake
        with a single global "is anything registered" flag cannot tell those
        apart — which is how a bootout aimed at the wrong target went
        unnoticed."""
        survivor = "ch.hydrosolutions.sapphire-watchdog"
        unloaded = "ch.hydrosolutions.sapphire"
        _plists(tmp_path, survivor, unloaded)
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=f"{survivor} {unloaded}",
            LAUNCHD_STICKY=survivor,
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert (
            f"[bootstrap] FAIL launchd job still registered after bootout: {survivor}"
            in r.stderr.splitlines()
        ), r.stderr
        assert (
            f"[bootstrap] FAIL launchd job still registered after bootout: {unloaded}"
            not in r.stderr.splitlines()
        ), r.stderr
        # ... and the survivor is the only thing the domain sweep still sees.
        assert "STILL registered after teardown" in r.stderr
        assert survivor in r.stderr

    def test_a_failed_launchctl_print_is_unknown_not_absence(
        self, tmp_path: Path
    ) -> None:
        """`launchctl print` exits non-zero both when the job is not there and
        when the QUERY failed (no such domain, EPERM, launchd not answering).
        Those are different facts. Reading every non-zero exit as absence is
        the same silent-success shape as reading a failed `docker compose ps`
        as "no containers"."""
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            PRINT_FAIL_RC="5",
            PRINT_FAIL_ERR="launchctl print: Bad file descriptor",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "could not verify launchd job was unloaded" in r.stderr, r.stderr
        assert "NOT assuming this means the job is gone" in r.stderr, r.stderr
        assert "Bad file descriptor" in r.stderr, r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_hydrosolutions_agent_the_script_does_not_know_is_torn_down_too(
        self, tmp_path: Path
    ) -> None:
        """Teardown covers the whole `ch.hydrosolutions.*.plist` glob the
        uninstall summary tells operators to delete, not just the hardcoded
        list."""
        unknown = "ch.hydrosolutions.sapphire-unknown"
        _plists(tmp_path, unknown)
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=unknown,
            LAUNCHD_STICKY="all",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert (
            f"[bootstrap] FAIL launchd job still registered after bootout: {unknown}"
            in r.stderr.splitlines()
        ), r.stderr

    def test_a_plist_whose_internal_label_differs_is_caught_by_the_sweep(
        self, tmp_path: Path
    ) -> None:
        """Glob discovery derives the label from the FILENAME. A plist whose
        internal <Label> differs is therefore booted out under a name that
        resolves to nothing — documented as an assumption at the call site.
        The domain sweep is what keeps that assumption safe: the real label is
        still loaded, so the uninstall reports INCOMPLETE instead of success."""
        _plists(tmp_path, "ch.hydrosolutions.sapphire-alias")
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire-real",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "STILL registered after teardown" in r.stderr
        assert "ch.hydrosolutions.sapphire-real" in r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_loaded_job_whose_plist_is_gone_is_not_counted_as_torn_down(
        self, tmp_path: Path
    ) -> None:
        """The old code only VERIFIED a label when its plist was present and
        logged "(skipping)" otherwise — contributing 0 to the failure count
        for exactly the state the uninstall summary tells operators to create
        (`rm ~/Library/LaunchAgents/ch.hydrosolutions.*.plist`). No plists
        exist here, yet every label still resolves."""
        assert not (tmp_path / "Library" / "LaunchAgents").exists()
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=ALL_SHIPPED,
            LAUNCHD_STICKY="all",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "still registered after bootout" in r.stderr
        assert "its plist is already gone" in r.stderr
        assert "uninstall complete" not in r.stdout
