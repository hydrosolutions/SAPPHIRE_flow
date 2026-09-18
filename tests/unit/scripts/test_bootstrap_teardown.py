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

# The SECOND compose project living on the same host: the Nepal forcing store
# (docs/operations/nepal-forcing-runbook.md). `--uninstall` boots out its
# LaunchAgent, so it must bring its database down too.
NEPAL_PROJECT = "sapphire-nepal"

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
project=""
while :; do
  case "${1:-}" in
    -f) [ -n "${2:-}" ] || { echo "fake docker: -f with no file" >&2; exit 91; }
        [ -f "$2" ] || { echo "fake docker: compose file missing: $2" >&2; exit 95; }
        files="${files}${files:+ }$(basename "$2")"
        shift 2 ;;
    -p) [ -n "${2:-}" ] || { echo "fake docker: -p with no project" >&2; exit 96; }
        project="$2"
        shift 2 ;;
    *)  break ;;
  esac
done

# The SECOND compose project on the mini: the Nepal forcing store. It is
# addressed by PROJECT NAME with no -f, because Compose v2 rebuilds a project
# from its container labels -- which is what lets the teardown skip that
# file's `secrets: file: ./secrets/nepal_db_password` declaration and the
# runbook's working-directory trap. Passing -f here is rejected so the
# contract cannot quietly drift back.
if [ -n "${project}" ]; then
  if [ "${project}" != "sapphire-nepal" ]; then
    echo "fake docker: unexpected project '${project}'" >&2; exit 96
  fi
  if [ -n "${files}" ]; then
    echo "fake docker: -p teardown must not pass -f (got [${files}])" >&2; exit 97
  fi
  case "$1" in
    down) [ "${2:-}" = "" ] || {
            echo "fake docker: nepal down takes no flags, got '$2'" >&2; exit 98; }
          exit ${NEPAL_DOWN_RC:-0} ;;
    ps)   { [ "$2" = "-q" ] && [ "$3" = "-a" ]; } || {
            echo "fake docker: expected 'ps -q -a', got 'ps $2 $3'" >&2; exit 93; }
          if [ "${NEPAL_PS_RC:-0}" -ne 0 ]; then
            echo "${NEPAL_PS_ERR:-fake docker: nepal ps exploded}" >&2
            exit "${NEPAL_PS_RC}"
          fi
          printf '%s' "${NEPAL_PS_OUT:-}"; exit 0 ;;
    *)    echo "fake docker: unexpected subcommand '$1'" >&2; exit 94 ;;
  esac
fi

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
        # Real macOS: a missing DOMAIN is 112, a missing SERVICE is 113
        # (measured 2026-09-18, Darwin 25.6.0).
        echo "Bad request." >&2
        echo "Could not find domain for user gui: ${domain##*/}" >&2
        exit 112
      fi
      if is_loaded "${label}"; then
        printf '%s = {\\n\\tactive count = 1\\n\\tstate = running\\n}\\n' "${target}"
        exit 0
      fi
      echo "Bad request." >&2
      uid_part="${domain##*/}"
      msg="Could not find service \\"${label}\\" in domain for user gui"
      echo "${msg}: ${uid_part}" >&2
      exit 113
    fi
    if [ "${nslash}" -eq 1 ]; then
      if [ "${DOMAIN_PRINT_RC:-0}" -ne 0 ]; then
        echo "${DOMAIN_PRINT_ERR:-launchctl print: could not reach launchd}" >&2
        exit "${DOMAIN_PRINT_RC}"
      fi
      if [ "${domain}" != "${valid_domain}" ]; then
        echo "Bad request." >&2
        echo "Could not find domain for user gui: ${domain##*/}" >&2
        exit 112
      fi
      # A REALISTIC domain dump, shaped after the real
      # `launchctl print gui/501` captured on Darwin 25.6.0, 2026-09-18:
      # header keys, the `services` table, `unmanaged processes`,
      # `task-special ports`, the `disabled services` override table, then
      # `properties`. The earlier fake emitted only `services = { ... }`,
      # which is why a sweep that grepped the WHOLE dump could pass every
      # test here and still fail on every real host.
      printf '%s = {\\n' "${target}"
      printf '\\ttype = login\\n\\thandle = 100023\\n\\tactive count = 467\\n'
      printf '\\tservice count = 466\\n\\tcreator = loginwindow[413]\\n'
      printf '\\tsecurity context = {\\n\\t\\tuid = %s\\n' "${FAKE_UID:-501}"
      printf '\\t\\tasid = 100023\\n\\t}\\n\\n'
      printf '\\tenvironment = {\\n'
      printf '\\t\\tSSH_AUTH_SOCK => /var/run/com.apple.launchd.x/L\\n\\t}\\n\\n'
      if [ "${DOMAIN_PRINT_NO_SERVICES:-0}" -eq 0 ]; then
        printf '\\tservices = {\\n'
        printf '\\t\\t     656      - \\tcom.apple.syncdefaultsd\\n'
        loaded_labels | while IFS= read -r l; do
          printf '\\t\\t       0     78 \\t%s\\n' "${l}"
        done
        if [ "${DOMAIN_PRINT_TRUNCATED:-0}" -eq 1 ]; then
          exit 0
        fi
        printf '\\t}\\n\\n'
      fi
      printf '\\tunmanaged processes = {\\n'
      printf '\\t\\tcom.apple.xpc.launchd.unmanaged.loginwindow.413 = {\\n'
      printf '\\t\\t\\tactive count = 2\\n'
      printf '\\t\\t\\tdynamic endpoints = {\\n\\t\\t\\t}\\n'
      printf '\\t\\t}\\n\\t}\\n\\n'
      printf '\\ttask-special ports = {\\n'
      printf '\\t\\t\\t 0x1cb03 4       bootstrap  domain.%s\\n' "${FAKE_UID:-501}"
      printf '\\t}\\n\\n'
      # The persisted per-user override database
      # (/var/db/com.apple.xpc.launchd/disabled.<uid>.plist). `launchctl
      # enable` -- which install-launchd.sh runs for every label it installs
      # -- writes an entry here PERMANENTLY, and `launchctl bootout` never
      # removes it. Measured on a real host 2026-09-18:
      # ch.hydrosolutions.sapphire-watchdog appeared here while being
      # unloaded, absent from `launchctl list`, and having no plist at all.
      # So these entries are present by DEFAULT: a teardown that is fully
      # successful still sees them.
      default_disabled="ch.hydrosolutions.sapphire"
      default_disabled="${default_disabled} ch.hydrosolutions.sapphire-watchdog"
      default_disabled="${default_disabled} ch.hydrosolutions.sapphire-docker-prune"
      default_disabled="${default_disabled} ch.hydrosolutions.sapphire-recap-probe"
      default_disabled="${default_disabled} ch.hydrosolutions.sapphire-nepal-forcing"
      printf '\\tdisabled services = {\\n'
      printf '\\t\\t"com.google.keystone.user.agent" => enabled\\n'
      for l in ${DISABLED_SERVICES-${default_disabled}}; do
        printf '\\t\\t"%s" => enabled\\n' "${l}"
      done
      printf '\\t\\t"com.apple.Siri.agent" => disabled\\n'
      printf '\\t}\\n\\n\\tproperties = gui | gui login\\n}\\n'
      exit 0
    fi
    echo "fake launchctl: malformed print target '${target}'" >&2; exit 91 ;;
  bootout)
    if [ "${nslash}" -ne 2 ] || [ -z "${label}" ]; then
      echo "fake launchctl: malformed bootout target '${target}'" >&2; exit 91
    fi
    if [ "${domain}" != "${valid_domain}" ]; then
      echo "Could not find domain for user gui: ${domain##*/}" >&2; exit 112
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

# A pass-through `grep` unless FAKE_GREP_RC is set.
#
# grep exits 1 for "no match" but >1 for a SEARCH FAILURE -- an unreadable
# input, a bad pattern, a resource limit. The residual sweep used to end in
# `|| true`, which collapsed both into success: a grep that blew up reported a
# clean launchd domain. There is no way to provoke that from the script's own
# inputs, so the failure is injected here.
_FAKE_GREP = """#!/bin/bash
if [ -n "${FAKE_GREP_RC:-}" ]; then
  echo "${FAKE_GREP_ERR:-grep: Input/output error}" >&2
  exit "${FAKE_GREP_RC}"
fi
for real in /usr/bin/grep /bin/grep; do
  [ -x "${real}" ] && exec "${real}" "$@"
done
echo "fake grep: no real grep on this host" >&2
exit 2
"""


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("docker", _FAKE_DOCKER),
        ("launchctl", _FAKE_LAUNCHCTL),
        ("id", _FAKE_ID),
        ("grep", _FAKE_GREP),
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
        DIFFERENT project than the one bootstrap brought up.

        Scoped to the SAPPHIRE project's calls: the Nepal store is a separate
        compose project, addressed by name (see the Nepal tests below)."""
        log = tmp_path / "docker.log"
        _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        calls = [
            c for c in log.read_text().splitlines() if f"-p {NEPAL_PROJECT}" not in c
        ]
        assert calls, "fake docker was never invoked for the SAPPHIRE project"
        for call in calls:
            assert f"-f {REPO_ROOT}/docker-compose.yml" in call, call
            assert f"-f {REPO_ROOT}/docker-compose.macmini.yml" in call, call

    def test_a_search_failure_in_the_residual_sweep_is_unknown_not_clean(
        self, tmp_path: Path
    ) -> None:
        """`grep` exits 1 for "no match" but >1 for a SEARCH FAILURE. The
        sweep used to end in `|| true`, which turned both into "clean" — so a
        grep that blew up over a domain full of live jobs reported a torn-down
        host."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire-still-here",
            FAKE_GREP_RC="2",
            FAKE_GREP_ERR="grep: Input/output error",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not search the launchd services list" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr, r.stderr

    def test_a_dump_with_no_services_block_is_unknown_not_clean(
        self, tmp_path: Path
    ) -> None:
        """`launchctl print`'s format is undocumented. If the block the sweep
        reads disappears, the honest answer is UNKNOWN — an empty extract must
        never be reported as an empty domain."""
        r = _run_teardown(
            tmp_path, DOWN_RC="0", PS_OUT="", DOMAIN_PRINT_NO_SERVICES="1"
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not read the 'services' block" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr, r.stderr

    def test_a_dump_whose_services_block_never_closes_is_unknown_not_clean(
        self, tmp_path: Path
    ) -> None:
        """A truncated dump is not a clean domain. If the `services` block
        opens and never closes, every row past the damage is invisible, so a
        partial extract read as clean is a false success — the same defect in
        the opposite direction to sweeping the whole dump."""
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOMAIN_PRINT_TRUNCATED="1")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not read the 'services' block" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr, r.stderr

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
        bug this module exists to prevent.

        The verdict alone does not lock this: with the guard deleted, the run
        still ends at RC=1 (every malformed `launchctl print gui//<label>`
        fails to resolve, which is correctly reported as UNKNOWN) — verified by
        mutation, that mutant SURVIVED an assertion on RC and the messages
        alone. What the guard actually buys is that the teardown ABORTS: it
        does not go on to fire malformed bootouts at the operator's launchd,
        and it does not bring compose down on a host whose launchd half can
        never be verified. So assert that nothing was touched."""
        launchctl_log = tmp_path / "launchctl.log"
        docker_log = tmp_path / "docker.log"
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=ALL_SHIPPED,
            FAKE_ID_OUT="",
            LAUNCHCTL_LOG=str(launchctl_log),
            DOCKER_LOG=str(docker_log),
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not determine the current uid" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr
        assert not launchctl_log.exists(), (
            f"teardown carried on and called launchctl with no uid: "
            f"{launchctl_log.read_text()!r}"
        )
        assert not docker_log.exists(), (
            f"teardown brought compose down on an unverifiable host: "
            f"{docker_log.read_text()!r}"
        )


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


class TestThePersistedDisabledServicesTableIsNotResidue:
    """THE regression this round exists for.

    `launchctl print gui/<uid>` does not only list what is registered. It also
    dumps `disabled services = { ... }`, the persisted per-user override
    database (`/var/db/com.apple.xpc.launchd/disabled.<uid>.plist`).
    `install-launchd.sh` writes an entry there for every label it installs
    (`launchctl enable gui/<uid>/<label>`), and `launchctl bootout` does NOT
    remove it — it survives the job being unloaded, the plist being deleted,
    and a reboot.

    Measured on a real host 2026-09-18: `ch.hydrosolutions.sapphire-watchdog`
    was not loaded, absent from `launchctl list`, and had no plist — and still
    appeared under `disabled services`. A sweep over the WHOLE dump therefore
    reported `uninstall INCOMPLETE` after every fully successful teardown,
    making `success "uninstall complete"` unreachable and deadlocking
    docs/deployment/mac-mini-staging.md's "do not wipe until it exits 0".
    """

    def _uninstall(
        self, tmp_path: Path, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), "--uninstall"],
            capture_output=True,
            text=True,
            env=_base_env(tmp_path, **env),
        )

    def test_the_fake_models_the_override_table_the_real_dump_carries(
        self, tmp_path: Path
    ) -> None:
        """The fake is half of this contract. If it stops emitting a
        `disabled services` table containing hydrosolutions labels — while
        emitting none of them in `services` — every test below goes green
        against a dump no real host produces, which is exactly how the defect
        shipped. Assert the shape directly."""
        bin_dir = _fake_bin(tmp_path)
        r = subprocess.run(
            [str(bin_dir / "launchctl"), "print", "gui/501"],
            capture_output=True,
            text=True,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "LAUNCHCTL_STATE": str(tmp_path / "launchd-state"),
            },
        )
        assert r.returncode == 0, r.stdout + r.stderr
        lines = r.stdout.splitlines()
        services_at = [i for i, ln in enumerate(lines) if ln.strip() == "services = {"]
        disabled_at = [
            i for i, ln in enumerate(lines) if ln.strip() == "disabled services = {"
        ]
        assert len(services_at) == 1, r.stdout
        assert len(disabled_at) == 1, r.stdout
        assert disabled_at[0] > services_at[0], r.stdout
        hits = [i for i, ln in enumerate(lines) if "ch.hydrosolutions." in ln]
        assert hits, r.stdout
        # Nothing hydrosolutions is REGISTERED here; every hit is an override.
        assert all(i > disabled_at[0] for i in hits), r.stdout

    def test_a_clean_teardown_with_override_entries_present_exits_zero(
        self, tmp_path: Path
    ) -> None:
        """Nothing loaded, no plists, `down` clean, no containers left — a
        fully successful teardown — while the override table still names every
        shipped label. This must exit 0 and say so."""
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "uninstall complete" in r.stdout, r.stdout
        assert "INCOMPLETE" not in r.stderr, r.stderr
        assert "STILL registered after teardown" not in r.stderr, r.stderr

    def test_an_override_entry_alone_is_not_reported_as_a_residual_job(
        self, tmp_path: Path
    ) -> None:
        """The narrower statement of the same thing, so a future change that
        re-broadens the sweep names the reason it failed."""
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            DISABLED_SERVICES="ch.hydrosolutions.sapphire-watchdog",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ch.hydrosolutions.sapphire-watchdog" not in r.stderr, r.stderr

    def test_a_registered_job_still_fails_while_overrides_are_present(
        self, tmp_path: Path
    ) -> None:
        """The other half: narrowing the sweep must not have neutered it. The
        same override table is present, and a job listed under `services` is
        still caught."""
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED="ch.hydrosolutions.sapphire-some-future-job",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "STILL registered after teardown" in r.stderr, r.stderr
        assert "ch.hydrosolutions.sapphire-some-future-job" in r.stderr, r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_disabled_override_never_masks_a_job_of_the_same_name(
        self, tmp_path: Path
    ) -> None:
        """The label that is loaded is also in the override table — the real
        state of every installed LaunchAgent. Reading only the override entry
        (or de-duplicating on the label) would report clean."""
        label = "ch.hydrosolutions.sapphire-watchdog"
        _plists(tmp_path, label)
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LAUNCHD_LOADED=label,
            LAUNCHD_STICKY="all",
            DISABLED_SERVICES=label,
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "STILL registered after teardown" in r.stderr, r.stderr


class TestTheNepalForcingStoreIsTornDownToo:
    """`--uninstall` boots out `ch.hydrosolutions.sapphire-nepal-forcing`, the
    timer that feeds the Nepal 12300 gateway-forcing store. That store is a
    SEPARATE, standing compose project (`sapphire-nepal`,
    docker-compose.nepal-forcing.yml — docs/operations/nepal-forcing-runbook.md),
    so the SAPPHIRE project's `down` never touched it: a long-running Postgres
    kept running while the banner said every container was verified gone.

    Stopping the timer and leaving its database up is the same half-true
    "torn down" this module exists to remove, so the project is brought down
    and verified like any other — but WITHOUT `-v`, so the named volume
    `sapphire-nepal_nepal_pgdata` and the accumulated forcing data survive.
    """

    def _uninstall(
        self, tmp_path: Path, *args: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), "--uninstall", *args],
            capture_output=True,
            text=True,
            env=_base_env(tmp_path, **env),
        )

    def test_the_nepal_project_is_brought_down_and_verified(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "docker.log"
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        assert r.returncode == 0, r.stdout + r.stderr
        calls = log.read_text().splitlines()
        assert f"compose -p {NEPAL_PROJECT} down" in calls, calls
        assert f"compose -p {NEPAL_PROJECT} ps -q -a" in calls, calls

    def test_the_nepal_project_is_never_brought_down_with_volumes(
        self, tmp_path: Path
    ) -> None:
        """`down -v` would delete `sapphire-nepal_nepal_pgdata` and every
        forcing record in it. Dropping the volume stays an explicit,
        operator-requested step in the runbook."""
        log = tmp_path / "docker.log"
        self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        down_calls = [c for c in log.read_text().splitlines() if "down" in c.split()]
        assert down_calls, log.read_text()
        for call in down_calls:
            assert " -v" not in call, call
            assert "--volumes" not in call, call

    def test_the_nepal_project_is_addressed_by_name_without_a_compose_file(
        self, tmp_path: Path
    ) -> None:
        """Compose v2 rebuilds a project from its container labels, so `-p`
        alone tears it down. Passing `-f docker-compose.nepal-forcing.yml`
        would drag in that file's `secrets: file: ./secrets/nepal_db_password`
        declaration and the runbook's working-directory trap, for a teardown
        that needs neither."""
        log = tmp_path / "docker.log"
        self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        nepal = [c for c in log.read_text().splitlines() if f"-p {NEPAL_PROJECT}" in c]
        assert nepal, log.read_text()
        for call in nepal:
            assert " -f " not in call, call

    def test_a_surviving_nepal_container_fails_the_uninstall(
        self, tmp_path: Path
    ) -> None:
        """The exact state the branch created: the Swiss stack is down, the
        Nepal LaunchAgent is booted out, and `sapphire-nepal-postgres-1` is
        still running."""
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", NEPAL_PS_OUT="deadbeef\n")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "containers still running" in r.stderr, r.stderr
        assert NEPAL_PROJECT in r.stderr, r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_failed_nepal_down_fails_the_uninstall(self, tmp_path: Path) -> None:
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", NEPAL_DOWN_RC="1")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "docker compose down failed" in r.stderr, r.stderr
        assert NEPAL_PROJECT in r.stderr, r.stderr

    def test_a_failed_nepal_container_check_is_unknown_not_clean(
        self, tmp_path: Path
    ) -> None:
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            NEPAL_PS_RC="2",
            NEPAL_PS_ERR="Cannot connect to the Docker daemon",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "could not verify containers were stopped" in r.stderr, r.stderr
        assert "NOT assuming" in r.stderr, r.stderr
        assert "Cannot connect to the Docker daemon" in r.stderr, r.stderr

    def test_the_banner_names_the_second_project_and_its_surviving_volume(
        self, tmp_path: Path
    ) -> None:
        """ "every container of the compose project" was true of one project
        and silent about the other. The operator must be told both were torn
        down, and that the Nepal data is still there."""
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="")
        assert r.returncode == 0, r.stdout + r.stderr
        assert NEPAL_PROJECT in r.stdout, r.stdout
        assert "nepal_pgdata" in r.stdout, r.stdout


class TestLabelDiscoveryTreatsFilenamesLiterally:
    def test_a_plist_name_with_glob_metacharacters_is_still_booted_out(
        self, tmp_path: Path
    ) -> None:
        """The discovered label comes from `basename` and is used as part of a
        `case` PATTERN. `ch.hydrosolutions.sapphire-w*g` would glob-match the
        shipped `ch.hydrosolutions.sapphire-watchdog` in the membership test,
        be judged "already queued", and never be booted out. It does not,
        because the expansion sits inside double quotes and bash treats a
        quoted portion of a pattern as literal — this locks those quotes."""
        label = "ch.hydrosolutions.sapphire-w*g"
        _plists(tmp_path, label)
        log = tmp_path / "launchctl.log"
        r = subprocess.run(
            ["bash", str(SCRIPT), "--uninstall"],
            capture_output=True,
            text=True,
            env=_base_env(
                tmp_path,
                DOWN_RC="0",
                PS_OUT="",
                LAUNCHD_LOADED=label,
                LAUNCHCTL_LOG=str(log),
            ),
        )
        assert f"bootout gui/501/{label}" in log.read_text().splitlines(), (
            log.read_text() + r.stdout + r.stderr
        )
        assert r.returncode == 0, r.stdout + r.stderr
