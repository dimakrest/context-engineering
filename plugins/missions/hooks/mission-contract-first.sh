#!/bin/bash
# PreToolUse hook (matcher: Write|Edit). Blocks product-code edits while a
# mission is in its planning phase.
#
# This is the hook that protects the whole idea. A contract written after a
# scaffold exists is not a specification -- it is a description of what was
# already built, shaped by the implementation it is supposed to be judging.
# Tests written after the code confirm decisions; they do not catch bugs.
# Exit 0 = allow, exit 2 = block.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission=$(mission_active_dir) || exit 0

[ "$(mission_phase "$mission")" = "planning" ] || exit 0

path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -n "$path" ] || exit 0

# Normalise to a repo-relative path so the allowlist below is comparable.
root="${CLAUDE_PROJECT_DIR:-$PWD}"
rel="${path#"$root"/}"

# A file outside the project is not this project's product code. The mission
# guards the repo it runs in -- not the user's ~/.claude config, not a sibling
# checkout, not a scratch dir. Without this the prefix strip above is a no-op,
# `rel` stays absolute, the `.claude/*` allowlist below cannot match a
# user-level `~/.claude/...` path, and planning blocks every edit anywhere.
case "$path" in
  "$root"/*) ;;
  *) exit 0 ;;
esac

# Planning legitimately writes: mission state, plan docs, documentation, and
# Claude Code config. Everything else is product code.
case "$rel" in
  .missions/*|docs/*|wiki/*|.claude/*|development-context/*|*.md) exit 0 ;;
esac

mission_block "MISSION: phase is 'planning' -- no product code yet.

Blocked write: $rel

Write contract.md first. An assertion authored after the code exists describes
the implementation instead of constraining it, and every downstream check in the
mission inherits that bias.

If this file genuinely is part of planning, move it under .missions/ or docs/.
If planning is finished, set '**Phase:**' in $mission/state.md to
'implementing' and dispatch a mission-worker -- the orchestrator does not write
product code itself."
