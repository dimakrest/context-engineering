#!/bin/bash
# Lints the plugin's agent definitions -- the frontmatter the hooks classify by.
#
# usage: lint-agents.sh [<agents-dir>]        default: $CLAUDE_PLUGIN_ROOT/agents
#
# The tools list and the class come from mission-lib.sh's own readers
# (mission_agent_tools / mission_agent_class), so this reports what the hooks
# will actually do -- not a second parser's opinion. Their awk stops the `tools:`
# list at the first non-indented line: a key placed between `tools:` and its
# items yields an EMPTY list, and an agent with no declared tools is
# default-denied to `writer` -- the reviewer would silently start taking the
# writer lock. Nothing else would ever report that. On top of that: the values
# the harness accepts for `model:` and `effort:`, and name/file agreement.
#
# Exit 0 = every definition is well-formed. Exit 1 = do not ship.

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
dir="${1:-${CLAUDE_PLUGIN_ROOT:-$here/..}/agents}"
[ -d "$dir" ] || { echo "LINT FAIL: no agents dir at '$dir'"; exit 1; }
dir=$(cd "$dir" && pwd)

# Point the lib's definition lookup at this directory (it searches $CLAUDE_PLUGIN_ROOT/agents first).
CLAUDE_PLUGIN_ROOT=$(dirname "$dir") CLAUDE_PROJECT_DIR=/nonexistent
export CLAUDE_PLUGIN_ROOT CLAUDE_PROJECT_DIR
source "$here/../hooks/mission-lib.sh"

fail=0
for f in "$dir"/*.md; do
  [ -f "$f" ] || continue
  name=$(basename "$f" .md)
  tools=$(mission_agent_tools "$name")
  class=$(mission_agent_class "$name")
  model=$(mission_agent_model "$name")
  ntools=$(printf '%s' "$tools" | grep -c . || true)
  effort=$(awk 'NR==1 && /^---/ {fm=1; next} fm && /^---/ {exit} fm && /^effort:/ {v=$0; sub(/^effort:[[:space:]]*/, "", v); print v; exit}' "$f")
  declared=$(awk 'NR==1 && /^---/ {fm=1; next} fm && /^---/ {exit} fm && /^name:/ {v=$0; sub(/^name:[[:space:]]*/, "", v); print v; exit}' "$f")
  printf '  %-34s model=%-8s effort=%-6s tools=%2d  class=%s\n' "$name.md" "${model:--}" "${effort:--}" "$ntools" "$class"

  err() { echo "    $name.md: $1"; fail=1; }
  [ -n "$declared" ] || err "missing \`name:\`"
  [ -z "$declared" ] || [ "$declared" = "$name" ] || err "name \`$declared\` does not match the file name"
  grep -qE '^description:[[:space:]]*\S' "$f" || err "missing \`description:\`"
  [ "$ntools" -gt 0 ] || err "\`tools:\` reads as EMPTY to the hooks (a key between \`tools:\` and its list, or no list) -- default-denied to writer"
  case "$model" in ""|sonnet|opus|haiku|fable|inherit) ;; claude-*) ;; *) err "model \`$model\` is not sonnet|opus|haiku|fable|inherit or a full claude-… id" ;; esac
  case "$effort" in ""|low|medium|high|xhigh|max) ;; *) err "effort \`$effort\` is not low|medium|high|xhigh|max" ;; esac
  while IFS= read -r t; do
    [ -z "$t" ] && continue
    printf '%s' "$t" | grep -qE '^([A-Za-z]+(\(.*\))?|mcp__[a-z0-9_-]+__[a-z0-9_]+|mcp__[a-z0-9_-]+__\*|\*)$' \
      || err "tool \`$t\` is not a tool name or an mcp__<server>__<tool> pattern"
  done <<< "$tools"
done

if [ "$fail" = 1 ]; then echo; echo "LINT FAIL: fix the lines above."; exit 1; fi
echo; echo "LINT PASS: every agent definition parses the way the hooks parse it."
exit 0
