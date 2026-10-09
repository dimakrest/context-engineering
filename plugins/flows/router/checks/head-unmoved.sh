#!/usr/bin/env bash
# head-unmoved.sh <worktree> <sha>
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when the worktree's HEAD is still <sha>.
# Why: a ledger or review worker reads only; after each one the branch head must not have moved.
# (router.py does the same, plus the uncommitted files, for every step marked "readonly".)
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
wt="${1:-}"; want="${2:-}"
[ -n "$wt" ] && [ -n "$want" ] || { echo "usage: head-unmoved.sh <worktree> <sha>" >&2; exit 2; }
head="$(git -C "$wt" rev-parse HEAD 2>/dev/null)" || { echo "NOT OK head: $wt is not a git checkout"; exit 1; }
if [ "$head" = "$want" ]; then echo "OK head unmoved at ${head:0:9}"; else echo "NOT OK head moved: ${want:0:9} → ${head:0:9}"; exit 1; fi
