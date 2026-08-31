#!/bin/bash
# Materialise exactly one feature's change as a patch file for the blind reviewer.
#
# usage: mission-patch.sh <mission-dir> F00n <base-sha> <head-sha> -- <path> [<path>...]
#
# Writes .missions/<slug>/patches/F00n.patch:
#   base: <sha> head: <sha> paths: <...>
#   <git diff base head -- paths>
# and prints its path. No commit messages, no author, no reasoning -- a reviewer
# that receives the whole branch under one feature's name (three-dot diff on a
# file touched 37 times) or the author's commit body (git show) is not blind.
# Exit 0 = written. Exit 2 = base is not an ancestor of head, or nothing to diff.

set -uo pipefail
m="${1:-}"; feature="${2:-}"; base="${3:-}"; head="${4:-}"
shift 4 2>/dev/null || { echo "usage: mission-patch.sh <mission-dir> F00n <base-sha> <head-sha> -- <paths...>" >&2; exit 1; }
[ "${1:-}" = "--" ] && shift
paths=("$@")
[ -d "$m" ] && [[ "$feature" =~ ^F[0-9]{3}$ ]] && [ -n "$base" ] && [ -n "$head" ] || {
  echo "usage: mission-patch.sh <mission-dir> F00n <base-sha> <head-sha> -- <paths...>" >&2; exit 1; }

repo="${CLAUDE_PROJECT_DIR:-$PWD}"
git -C "$repo" cat-file -e "${base}^{commit}" 2>/dev/null || { echo "mission-patch: base $base is not a commit" >&2; exit 2; }
git -C "$repo" cat-file -e "${head}^{commit}" 2>/dev/null || { echo "mission-patch: head $head is not a commit" >&2; exit 2; }
git -C "$repo" merge-base --is-ancestor "$base" "$head" 2>/dev/null || {
  echo "mission-patch: $base is not an ancestor of $head -- the range is wrong, not the reviewer" >&2; exit 2; }

mkdir -p "$m/patches"
out="$m/patches/$feature.patch"
{
  printf 'feature: %s\nbase: %s\nhead: %s\npaths: %s\n\n' "$feature" "$(git -C "$repo" rev-parse "$base")" "$(git -C "$repo" rev-parse "$head")" "${paths[*]:-<all>}"
  if [ ${#paths[@]} -gt 0 ]; then git -C "$repo" diff "$base" "$head" -- "${paths[@]}"
  else git -C "$repo" diff "$base" "$head"; fi
} > "$out"

if ! grep -q '^diff --git' "$out"; then
  rm -f "$out"; echo "mission-patch: empty diff for $feature ($base..$head${paths:+ -- ${paths[*]}})" >&2; exit 2
fi
printf '%s\n' "$out"
exit 0
