#!/bin/bash
# PreToolUse hook (matcher: Agent). Keeps validators blind.
#
# The creator-verifier split is the load-bearing property of a mission: the
# check is worth something only because the checker never saw how or why the
# code was written. Leaking the handoff into a reviewer prompt silently turns an
# independent verdict into an echo of the author -- and it still *looks* like a
# review afterwards, which is why this is a hook and not a reminder.
# Exit 0 = allow, exit 2 = block.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission_active_dir >/dev/null || exit 0

subagent=$(mission_agent_base "$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // empty')")
case "$subagent" in
  mission-reviewer|mission-validator-behavior|mission-validator-scrutiny) ;;
  *) exit 0 ;;
esac

prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // empty')

# Handoff content pasted into a validator prompt, by path or by section header.
if printf '%s' "$prompt" | grep -qE 'handoffs?/F[0-9]{3}|^#+[[:space:]]*Handoff|Assertions claimed|Procedures followed|Left undone'; then
  mission_block "MISSION: this '$subagent' prompt contains handoff content.

A validator receives the assertions (and, for mission-reviewer, the diff) and
nothing else -- never the worker's handoff, reasoning, or another validator's
findings. Remove it and dispatch again.

If the validator genuinely needs a fact that only the handoff records, that fact
belongs in the contract or in state.md, not in the validator's prompt."
fi

# The reviewer reads a materialised per-feature patch, never the branch. On a file
# touched 37 times, `git diff origin/main...HEAD -- path` is the cumulative branch
# diff, not the feature's change (retro C1); `git log`/`git show` surface the
# author's commit body, which is the reasoning blindness exists to withhold (C3).
if [ "$subagent" = "mission-reviewer" ]; then
  if printf '%s' "$prompt" | grep -qE 'origin/main\.\.\.|git[[:space:]]+(log|show)([[:space:]]|$)|git[[:space:]]+diff[[:space:]]'; then
    mission_block "MISSION: this mission-reviewer prompt tells the reviewer to run git itself.

Hand it a patch file instead:
  bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/mission-patch.sh\" <mission-dir> F00n <base-sha> <head-sha> -- <paths>
then brief it with: Patch: .missions/<slug>/patches/F00n.patch (base <sha>, head <sha>).
A three-dot branch diff reviews the whole branch under one feature's name, and
git log/show leak the author's reasoning."
  fi
  if ! printf '%s' "$prompt" | grep -qE 'patches/F[0-9]{3}\.patch'; then
    mission_block "MISSION: this mission-reviewer prompt names no patch file.

Every review is of exactly one materialised patch:
  bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/mission-patch.sh\" <mission-dir> F00n <base-sha> <head-sha> -- <paths>
and the brief must cite it as 'Patch: .missions/<slug>/patches/F00n.patch (base <sha>, head <sha>)'."
  fi
fi

# The behavior validator proves assertions by using the system, not by reading
# the code. A diff in its prompt makes it test what the code does instead of
# what the contract requires -- and those are different things.
if [ "$subagent" = "mission-validator-behavior" ]; then
  if printf '%s' "$prompt" | grep -qE '^(diff --git|\+\+\+ b/|--- a/|@@ )|```diff'; then
    mission_block "MISSION: this mission-validator-behavior prompt contains a diff.

The behavior validator must not read the implementation. Give it the
assertions, the environment, and the call/UI budget -- then let it go use the
system and report what actually happened."
  fi
fi

exit 0
