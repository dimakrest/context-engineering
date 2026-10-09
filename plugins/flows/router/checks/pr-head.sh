#!/usr/bin/env bash
# pr-head.sh <worktree> <pr number or url>
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when the PR's head commit is the worktree's HEAD and no
# tracked file has an uncommitted change. Read-only: it runs `gh pr view`.
# Why: CI and the reviews look at what was pushed. A commit that /simplify or a fixer made and did not push would
# otherwise get "OK ci" for the commit before it.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
[ $# -eq 2 ] || { echo "usage: pr-head.sh <worktree> <pr number or url>" >&2; exit 2; }
wt="$1"; pr="$2"
local_head="$(git -C "$wt" rev-parse HEAD 2>/dev/null)" || { echo "NOT OK pr-head: $wt is not a git checkout"; exit 1; }
dirty="$(git -C "$wt" status --porcelain --untracked-files=no 2>/dev/null)"
[ -z "$dirty" ] || { echo "NOT OK pr-head: $(printf '%s\n' "$dirty" | wc -l | tr -d ' ') tracked file(s) have uncommitted changes: $(printf '%s\n' "$dirty" | head -3 | cut -c4- | tr '\n' ' ')"; exit 1; }
pr_head="$(cd "$wt" && gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null)"
[ -n "$pr_head" ] || { echo "NOT OK pr-head: gh could not read the head of $pr"; exit 1; }
if [ "$pr_head" = "$local_head" ]; then echo "OK pr-head: the PR and the worktree are both at ${local_head:0:9}"
else echo "NOT OK pr-head: the PR is at ${pr_head:0:9}, the worktree at ${local_head:0:9} (not pushed, or pushed from elsewhere)"; exit 1; fi
