#!/bin/bash
# PreToolUse hook (matcher: Bash). Keeps the crosscheck reviewer's package sealed.
#
# /missions:mission-crosscheck buys an unbiased second opinion by handing an external
# reviewer a spec package with our conclusions stripped out. That works only if
# the reviewer cannot reach the originals. The first real run failed exactly
# here: the package was staged inside .missions/, the prompt declared .missions/
# out of bounds, the reviewer's own search excluded .missions/** and found
# nothing -- so it went looking by filename and read the unstripped file. The
# resulting review was fluent, well cited, and an echo of our own answer,
# indistinguishable from a real one by reading it. That is why this is a hook
# and not a line in a prompt.
# Exit 0 = allow, exit 2 = block.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission_active_dir >/dev/null || exit 0

raw=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -n "$raw" ] || exit 0

# Two views, on purpose. Detect the *invocation* on the stripped command, so a
# document that merely writes about codex is not mistaken for running it. Search
# for leaks in the *unstripped* text, because a prompt fed through a heredoc is
# exactly the content that needs inspecting -- and mission_strip_heredocs drops
# heredoc bodies wholesale.
cmd=$(printf '%s\n' "$raw" | mission_strip_heredocs)

printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])codex([[:space:]]|$)' || exit 0

if printf '%s' "$raw" | grep -qE '(^|[^[:alnum:]_./-])\.missions/'; then
  mission_block "MISSION: this codex invocation references .missions/.

The crosscheck reviewer must not be able to reach the mission's own working
files -- contract.md, features.md, design.md, state.md, the journal. Those hold
the conclusions it is being asked to reach independently.

Stage the spec package OUTSIDE the repository (the session scratchpad), give the
copies distinct names (SPEC-1-contract.md, never contract.md) so a filename
search cannot collide with the originals, and pass absolute paths in the task
file. Naming .missions/ as out of bounds is not enough on its own: an exclusion
that also hides your package is what sends the reviewer hunting for the real one.

If you are deliberately showing the reviewer our design, that is pass 2 -- and it
runs only after pass 1's report is saved to disk."
fi

if printf '%s' "$raw" | grep -qE '(^|[^[:alnum:]_./-])docs/plans/'; then
  mission_block "MISSION: this codex invocation references docs/plans/.

That directory holds the mission's plan and decision documents -- our reasoning
in prose. Handing it to the crosscheck reviewer anchors it on our answer, which
is the one thing this pass exists to avoid.

Stage the spec package outside the repository and pass absolute paths."
fi

exit 0
