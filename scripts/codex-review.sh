#!/usr/bin/env bash
# Run an independent, read-only Codex review over a prompt file.
#
# WHY THIS SCRIPT EXISTS. `codex exec` blocks on "Reading additional input from
# stdin..." unless stdin is closed. When that happens the CLI produces its whole
# review, then sits waiting until the caller's timeout kills it (exit 143) — the
# verdict is written and thrown away, and the run reports a normal, green round
# that had no independent reviewer in it at all.
#
# `.claude/workflows/{plan,implement}.js` carried `< /dev/null` only as PROSE
# inside a prompt telling an agent how to shell out. On 2026-08-28 a `plan` run
# reported "0 blockers + 1 major" with codexFailedRounds=2 of 2: the agent had
# omitted the redirect in both rounds. Prose is advisory; this script is not.
# The redirect lives here so a caller cannot forget it.
#
# NOTE ON DETECTING THE HANG. Do NOT grep the output for "Reading additional
# input from stdin" as evidence of a hang: codex prints that line during NORMAL
# startup, reads EOF from /dev/null, and proceeds. A guard on that string
# false-positives on every successful review (measured 2026-08-28 — the first
# version of this script did exactly that and failed its own smoke test). The
# real signal is the caller's timeout, which surfaces here as a non-zero exit.
#
# Usage:  scripts/codex-review.sh <prompt-file> [extra codex args...]
# Exit:   0 with Codex's output on stdout; non-zero on any failure to review.
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <prompt-file> [extra codex args...]" >&2
    exit 64
fi

prompt_file="$1"
shift

if [ ! -r "$prompt_file" ]; then
    echo "$0: prompt file not readable: $prompt_file" >&2
    exit 66
fi

prompt=$(<"$prompt_file")
prompt_no_space="${prompt//[[:space:]]/}"
if [ -z "$prompt_no_space" ] || [ "$prompt_no_space" = "temporarypromptcleared." ]; then
    echo "$0: empty or cleared prompt — NO usable prompt" >&2
    exit 65
fi

if ! command -v codex >/dev/null 2>&1; then
    echo "$0: codex CLI not found on PATH" >&2
    exit 127
fi

# `< /dev/null` is the whole point — see the header. Output is captured so an
# empty verdict can be caught even on a zero exit.
set +e
output=$(codex exec --sandbox read-only --skip-git-repo-check "$@" \
    "$prompt" < /dev/null 2>&1)
status=$?
set -e

printf '%s\n' "$output"

if [ "$status" -ne 0 ]; then
    echo "$0: codex exited $status — NO usable verdict" >&2
    exit "$status"
fi

if [ -z "${output//[[:space:]]/}" ]; then
    echo "$0: codex produced empty output — NO usable verdict" >&2
    exit 75
fi
