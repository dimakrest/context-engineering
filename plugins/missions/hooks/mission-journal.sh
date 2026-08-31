#!/bin/bash
# PostToolUse:Agent and SubagentStop hook. Journals what actually ran.
#
# Recorded here rather than by the loop so that it reflects what happened, not
# what the orchestrator remembered to write down afterwards. The harness signals
# completion two ways (see mission-release.sh), so three events:
#   agent_launched  PostToolUse with status async_launched -- background dispatch started
#   agent_return    PostToolUse with any other status      -- waited dispatch finished;
#                   duration_s from the harness's duration_ms (measured, not joined)
#   agent_stopped   SubagentStop -- the agent's own end; duration_s only when it can be
#                   joined to a dispatch through agent_id (background dispatches), else null
# A waited dispatch therefore produces agent_stopped (no duration) then agent_return
# (with duration); a background one produces agent_launched then agent_stopped
# (with duration). Consumers sum duration_s over both return-type events without
# double counting. Anything not known is null, never invented.
#
# Never fails a tool call: journalling is bookkeeping. Always exits 0.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh" 2>/dev/null || exit 0

input=$(cat)
mission=$(mission_active_dir) || exit 0
mission_debug_dump "$mission" mission-journal.sh "$input"

event=$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0

agent=$(mission_agent_base "$(printf '%s' "$input" | jq -r '.agent_type // .tool_response.agentType // .tool_input.subagent_type // empty' 2>/dev/null)")
case "$agent" in mission-*) ;; *) exit 0 ;; esac

prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // empty' 2>/dev/null)
feature=$(mission_prompt_feature "$prompt")
dispatch_id=$(printf '%s' "$input" | jq -r '.tool_use_id // empty' 2>/dev/null)
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // .tool_response.agentId // .tool_response.agent_id // empty' 2>/dev/null)
status=$(printf '%s' "$input" | jq -r '.tool_response.status // empty' 2>/dev/null)
duration_ms=$(printf '%s' "$input" | jq -r '.duration_ms // empty' 2>/dev/null)
# The model that ran, by the serial guard's rule. A SubagentStop carries no tool_input, so
# there it comes from the joined dispatch below (background dispatches) or stays unrecorded --
# a definition default would be wrong for an overridden seat: not known, not invented.
model=""
[ "$event" = "SubagentStop" ] || model=$(mission_resolve_model "$input" "$agent")

ev=agent_return; duration=null
if [ "$event" = "SubagentStop" ]; then
  ev=agent_stopped
  # Join through agent_id -> agent_launched.dispatch_id -> dispatch.ts (background dispatches only).
  if [ -n "$agent_id" ] && [ -f "$mission/journal.jsonl" ]; then
    did=$(jq -r --arg a "$agent_id" 'select(.event=="agent_launched" and .agent_id==$a) | .dispatch_id // empty' "$mission/journal.jsonl" 2>/dev/null | tail -1)
    if [ -n "$did" ]; then
      # One pass over the journal for everything the dispatch record carries.
      IFS=$'\t' read -r started feature model < <(jq -r --arg id "$did" \
        'select(.event=="dispatch" and .dispatch_id==$id) | [.ts // "", .feature // "", .model // ""] | @tsv' \
        "$mission/journal.jsonl" 2>/dev/null | tail -1)
      if [ -n "${started:-}" ]; then
        start_epoch=$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$started" +%s 2>/dev/null || date -u -d "$started" +%s 2>/dev/null)
        [ -n "$start_epoch" ] && duration=$(( $(mission_epoch) - start_epoch ))
      fi
      dispatch_id="$did"
    fi
  fi
elif [ "$status" = "async_launched" ]; then
  ev=agent_launched
elif [ -n "$duration_ms" ]; then
  duration=$(( duration_ms / 1000 ))
fi

jq -nc \
  --arg ts "$(mission_now)" --arg ev "$ev" --arg agent "$agent" --arg feature "${feature:-}" \
  --arg id "${dispatch_id:-}" --arg agent_id "$agent_id" --arg status "$status" --arg via "$event" \
  --arg model "$model" --argjson duration "$duration" \
  '{ts:$ts, event:$ev, agent:$agent, via:$via}
   + (if $ev == "agent_launched" then {} else {duration_s:$duration} end)
   + (if $model == "" then {} else {model:$model} end)
   + (if $feature == "" then {} else {feature:$feature} end)
   + (if $id == "" then {} else {dispatch_id:$id} end)
   + (if $agent_id == "" then {} else {agent_id:$agent_id} end)
   + (if $status == "" then {} else {status:$status} end)' \
  >> "$mission/journal.jsonl" 2>/dev/null

exit 0
