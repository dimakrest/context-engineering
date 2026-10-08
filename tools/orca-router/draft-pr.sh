#!/usr/bin/env bash
# draft-pr.sh <worktree> <base branch> <title> <body file>
#
# Opens the inner PR as a draft, on the coordinator's authority, so that step needs no model. Prints
# "VAR PR_URL=<url>" (a router script step stores it) and one verdict line; exits 0 (OK) or 1 (NOT OK).
# It refuses when the base is main or master, when the worktree's branch is not pushed, or when the pushed head
# is not the local head. It pushes nothing. If a PR from the branch into this base is already open it prints that
# one, so a retry opens no second PR; one into another base is a refusal. DRAFT_PR_DRY_RUN=1 creates nothing.
# Needs: git, gh (as the session's account; never `gh auth switch`).
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
[ $# -eq 4 ] || { echo "usage: draft-pr.sh <worktree> <base branch> <title> <body file>" >&2; exit 2; }
wt="$1"; base="$2"; title="$3"; body="$4"
case "$base" in main|master) echo "NOT OK draft PR: the base is $base; inner PRs go into the integration branch"; exit 1 ;; esac
[ -f "$body" ] || { echo "NOT OK draft PR: no body file $body"; exit 1; }
branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)" || { echo "NOT OK draft PR: $wt is not a git checkout"; exit 1; }
[ "$branch" != HEAD ] || { echo "NOT OK draft PR: $wt is on a detached HEAD"; exit 1; }
[ "$branch" != "$base" ] || { echo "NOT OK draft PR: the worktree is on the base branch $base"; exit 1; }
local_head="$(git -C "$wt" rev-parse HEAD)"
remote_head="$(git -C "$wt" ls-remote --heads origin "$branch" 2>/dev/null | cut -f1)"
[ -n "$remote_head" ] || { echo "NOT OK draft PR: $branch is not pushed to origin"; exit 1; }
[ "$remote_head" = "$local_head" ] || { echo "NOT OK draft PR: origin/$branch is ${remote_head:0:9}, the worktree is ${local_head:0:9}"; exit 1; }
open="$(cd "$wt" && gh pr list --head "$branch" --state open --json baseRefName,url --jq '.[] | "\(.baseRefName)\t\(.url)"' 2>/dev/null)"
url="$(printf '%s\n' "$open" | awk -F'\t' -v b="$base" '$1 == b { print $2; exit }')"
if [ -n "$url" ]; then echo "VAR PR_URL=$url"; echo "OK draft PR: already open, $url"; exit 0; fi
other="$(printf '%s\n' "$open" | awk -F'\t' 'NF == 2 { print $2 " (into " $1 ")"; exit }')"
[ -z "$other" ] || { echo "NOT OK draft PR: $branch already has an open PR into another base: $other"; exit 1; }
if [ -n "${DRAFT_PR_DRY_RUN:-}" ]; then echo "OK draft PR (dry run): gh pr create --draft --base $base --head $branch --title \"$title\" --body-file $body"; exit 0; fi
out="$(cd "$wt" && gh pr create --draft --base "$base" --head "$branch" --title "$title" --body-file "$body" 2>&1)" || { echo "NOT OK draft PR: gh said: $(printf '%s' "$out" | tail -1 | cut -c1-200)"; exit 1; }
url="$(printf '%s\n' "$out" | grep -Eo 'https://[^ ]+/pull/[0-9]+' | tail -1)"
[ -n "$url" ] || { echo "NOT OK draft PR: gh printed no PR url: $(printf '%s' "$out" | tail -1 | cut -c1-200)"; exit 1; }
echo "VAR PR_URL=$url"; echo "OK draft PR: $url"
