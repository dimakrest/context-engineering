#!/bin/bash
# PreToolUse hook (matcher: Bash). Commit discipline during a mission.
#
# A mission's terminal state is a branch plus a draft PR. Never a merge, never a
# push. Without this the guarantee rests on three different agents each
# remembering a rule at the moment it is most inconvenient.
# Exit 0 = allow, exit 2 = block.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission=$(mission_active_dir) || exit 0

raw=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -n "$raw" ] || exit 0

# Writing documentation that mentions `git push` is not running it.
cmd=$(printf '%s\n' "$raw" | mission_strip_heredocs)

# Pushing is allowed in exactly one phase: `pr`, the terminal review step, which needs a
# pushed branch for /code-review and /codex-adversarial-review to see. Everywhere else the
# branch stays local.
if [ "$(mission_phase "$mission")" != "pr" ] \
   && printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+push([[:space:]]|$)'; then
  mission_block "MISSION: no pushing during a mission.

The mission's output is a local branch. A human decides what leaves this
machine. If you have reached the terminal step, set **Phase: pr** in state.md
-- that is the one phase where pushing is allowed -- or hand over and let the
user push. Do not set that phase just to unblock a push you want to make now."
fi

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+merge([[:space:]]|$)|gh[[:space:]]+pr[[:space:]]+merge'; then
  mission_block "MISSION: no merging during a mission.

Terminal state is a branch plus a DRAFT PR. A human merges. If the branch has
drifted from main and you need that resolved, halt and ask."
fi

if printf '%s' "$cmd" | grep -qE '\-\-no-verify|\-\-admin'; then
  mission_block "MISSION: --no-verify / --admin are never allowed (repo rule).

Fix what the hook is complaining about. A bypassed check is a defect that a
validator will not catch, because the validator trusts the gate you just
skipped."
fi

# Commit messages carry the feature id, so the journal, the handoff and the diff
# can be reconciled later. Only enforced while implementing -- planning commits
# (docs, wiki) are not feature work.
if [ "$(mission_phase "$mission")" = "implementing" ]; then
  if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+commit([[:space:]]|$)'; then
    msg=$(printf '%s' "$cmd" | sed -nE 's/.*-m[[:space:]]+("([^"]*)"|'"'"'([^'"'"']*)'"'"').*/\2\3/p' | head -1)
    if [ -n "$msg" ] && ! printf '%s' "$msg" | grep -qE '^(F|FU)[0-9]{3}:'; then
      mission_block "MISSION: commit message must start with the feature id, e.g. 'F003: <subject>'.

Got: $msg

Follow-up features use FU00n. If this commit does not belong to a feature, it
probably should not be happening mid-mission -- every changed line traces to an
assertion or a procedure the feature was given."
    fi
  fi
fi

exit 0
