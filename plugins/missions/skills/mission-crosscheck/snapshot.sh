#!/bin/bash
# Pre-run state capture for /missions:mission-crosscheck, and the single definition of
# what "a mission file" means for the audit gate.
#
# usage:
#   snapshot.sh <mission-dir> <snap-dir>   write snap/git.txt + snap/mission.sha
#   snapshot.sh --print <mission-dir>      print the same checksum set to stdout
#
# This exists so the baseline and the recompute cannot drift. They used to be
# two copies of the same `find` -- one in SKILL.md's Step 3, one inside
# audit.sh -- and a gate whose two halves can disagree eventually accuses an
# innocent run. audit.sh now calls --print rather than carrying its own copy.
#
# Exit 0 on success.

set -uo pipefail

# The checksum set. Two exclusions, both deliberate:
#
#   VOID*        quarantined transcripts from a failed pass. Large, and not
#                state -- they are evidence about a run, not part of the mission.
#
#   crosscheck/  this skill's OWN workspace. It writes progress.md throughout
#                the run, pass1-report.md at step 4 and report.html at step 7,
#                so including it guarantees the baseline diverges from the live
#                tree -- and *when* it diverges depends on nothing more than the
#                order the operator happened to run two commands in. That is a
#                false VOID on a gate whose whole value is that you do not wave
#                its failures away.
#
#                Nothing is lost by skipping it. What the gate protects lives
#                outside: contract.md, features.md, design.md, state.md,
#                mission.md, journal.jsonl, handoffs/ and validation/.
mission_sha() {
  local mission="$1"
  find "$mission" -type f \
    ! -name 'VOID*' \
    ! -path '*/crosscheck/*' \
    -exec shasum {} \; 2>/dev/null | sort
}

if [ "${1:-}" = "--print" ]; then
  mission="${2:-}"
  [ -n "$mission" ] && [ -d "$mission" ] || {
    echo "snapshot.sh --print: no mission directory at '$mission'" >&2; exit 1; }
  mission_sha "$mission"
  exit 0
fi

mission="${1:-}"; snap="${2:-}"
[ -n "$mission" ] && [ -d "$mission" ] || {
  echo "snapshot.sh: no mission directory at '$mission'" >&2; exit 1; }
[ -n "$snap" ] || { echo "snapshot.sh: no snapshot directory given" >&2; exit 1; }

mkdir -p "$snap"
git status --short > "$snap/git.txt" 2>/dev/null || : > "$snap/git.txt"
mission_sha "$mission" > "$snap/mission.sha"

echo "snapshot: $(wc -l < "$snap/git.txt" | tr -d ' ') git lines, \
$(wc -l < "$snap/mission.sha" | tr -d ' ') mission files (crosscheck/ and VOID* excluded)"
