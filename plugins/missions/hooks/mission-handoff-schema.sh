#!/bin/bash
# PostToolUse hook (matcher: Agent). Validates a mission-worker's handoff.
#
# "Ran the tests, all passing" with no commands, no exit codes and no commit is
# not a handoff -- it is a claim. The loop is told to reject those, but a rule
# the orchestrator applies to its own subordinate's output is exactly the rule
# it is most likely to rationalise past when the work looks fine.
#
# Runs after the agent returns, so it cannot prevent the work; it feeds the gap
# back to the orchestrator (exit 2) so the feature is re-dispatched or the
# handoff completed before the next one starts.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission=$(mission_active_dir) || exit 0

subagent=$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // empty')
[ "$(mission_agent_base "$subagent")" = "mission-worker" ] || exit 0

prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // empty')
feature=$(mission_prompt_feature "$prompt")
if [ -z "$feature" ]; then
  echo "MISSION: mission-worker was dispatched without a feature id (F00n) in its prompt.
A worker implements exactly one identified feature; without the id neither the
handoff nor the commit can be reconciled with the contract." >&2
  exit 2
fi

handoff="$mission/handoffs/$feature.md"
if [ ! -f "$handoff" ]; then
  echo "MISSION: $feature returned with no handoff at $handoff.

Do not advance. Either re-dispatch $feature, or have the work re-stated as a
handoff by someone who can see the diff -- and mark it 'reconstructed', because
a handoff written by an agent that can read the code is weaker evidence than one
written by the agent that did the work." >&2
  exit 2
fi

missing=()
for section in "## Status" "## Assertions claimed" "## Completed" "## Left undone" \
               "## Commands run" "## Issues discovered" "## Procedures followed" "## Commit"; do
  grep -qiF "$section" "$handoff" || missing+=("$section")
done

problems=()
[ ${#missing[@]} -gt 0 ] && problems+=("missing sections: ${missing[*]}")

status=$(awk '/^##[[:space:]]*[Ss]tatus/{getline; while ($0 ~ /^[[:space:]]*$/) getline; print tolower($0); exit}' "$handoff" | tr -d '[:space:]')

# A command with no exit code is an anecdote. Look for a markdown table row
# ending in a numeric exit column.
if ! grep -qE '^\|.*\|[[:space:]]*[0-9]+[[:space:]]*\|' "$handoff"; then
  problems+=("no command in '## Commands run' records an exit code")
fi

# The claimed commit must actually exist. A handoff citing a sha that is not in
# the repo is the single cheapest lie to tell and the cheapest to catch.
sha=$(awk '/^##[[:space:]]*[Cc]ommit/{f=1; next} f && /[0-9a-f]{7,40}/{print; exit}' "$handoff" \
      | grep -oE '[0-9a-f]{7,40}' | head -1 || true)
if [ -n "$sha" ]; then
  git -C "${CLAUDE_PROJECT_DIR:-$PWD}" cat-file -e "${sha}^{commit}" 2>/dev/null \
    || problems+=("commit $sha is not in this repository")
elif [ "$status" != "blocked" ]; then
  problems+=("no commit sha recorded (status is '${status:-unset}', not 'blocked')")
fi

if [ ${#problems[@]} -gt 0 ]; then
  printf 'MISSION: the handoff for %s is not valid evidence.\n\n' "$feature" >&2
  printf -- '- %s\n' "${problems[@]}" >&2
  printf '\nSchema: ${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md. Fix the handoff before\ndispatching the next feature.\n' >&2
  exit 2
fi

exit 0
