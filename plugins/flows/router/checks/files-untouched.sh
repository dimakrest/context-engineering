#!/usr/bin/env bash
# files-untouched.sh <worktree> <from> <to> <pathspec>...
# files-untouched.sh <worktree> <from> <to> --changed-in <a> <b>
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when no named file differs between the commits <from> and
# <to>, and none has an uncommitted change in the worktree. The second form names the files that changed between
# <a> and <b> (for example the test writer's commits).
# Why: /simplify and the code fixers may not edit the test writer's files.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
[ $# -ge 4 ] || { echo "usage: files-untouched.sh <worktree> <from> <to> <pathspec>... | --changed-in <a> <b>" >&2; exit 2; }
wt="$1"; from="$2"; to="$3"; shift 3
if [ "$1" = "--changed-in" ]; then
  [ $# -eq 3 ] || { echo "usage: files-untouched.sh <worktree> <from> <to> --changed-in <a> <b>" >&2; exit 2; }
  guarded="$(git -C "$wt" diff --name-only "$2" "$3" 2>&1)" || { echo "NOT OK files-untouched: git diff $2 $3 failed: $(printf '%s' "$guarded" | head -1)"; exit 1; }
  [ -n "$guarded" ] || { echo "OK files-untouched: nothing changed in ${2:0:9}..${3:0:9}, so nothing to guard"; exit 0; }
  changed="$(printf '%s\n' "$guarded" | (cd "$wt" && xargs git diff --name-only "$from" "$to" --) 2>&1)" || { echo "NOT OK files-untouched: git diff failed: $(printf '%s' "$changed" | head -1)"; exit 1; }
  dirty="$(printf '%s\n' "$guarded" | (cd "$wt" && xargs git status --porcelain --) 2>/dev/null)"
  n_guard="$(printf '%s\n' "$guarded" | wc -l | tr -d ' ')"
else
  changed="$(git -C "$wt" diff --name-only "$from" "$to" -- "$@" 2>&1)" || { echo "NOT OK files-untouched: git diff failed: $(printf '%s' "$changed" | head -1)"; exit 1; }
  dirty="$(git -C "$wt" status --porcelain -- "$@" 2>/dev/null)"
  n_guard="$#"
fi
if [ -n "$dirty" ]; then echo "NOT OK files-untouched: $(printf '%s\n' "$dirty" | wc -l | tr -d ' ') guarded file(s) have uncommitted changes: $(printf '%s\n' "$dirty" | head -3 | cut -c4- | tr '\n' ' ')"; exit 1; fi
if [ -z "$changed" ]; then echo "OK files-untouched: none of $n_guard guarded path(s) changed in ${from:0:9}..${to:0:9}"
else echo "NOT OK files-untouched: $(printf '%s\n' "$changed" | wc -l | tr -d ' ') guarded file(s) changed in ${from:0:9}..${to:0:9}: $(printf '%s\n' "$changed" | head -3 | tr '\n' ' ')"; exit 1; fi
