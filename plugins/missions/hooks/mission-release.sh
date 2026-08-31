#!/bin/bash
# Releases the writer lock and the execution lease when their holder is gone.
#
# Registered on PostToolUse:Agent, PostToolUseFailure:Agent and SubagentStop, because
# the harness signals completion two different ways (measured with MISSION_HOOK_DEBUG=1):
#   waited dispatch:     SubagentStop {agent_id, agent_type}  then  PostToolUse {status:"completed",
#                        tool_response.agentId, duration_ms}
#   background dispatch: PostToolUse {status:"async_launched", tool_response.agentId}  at LAUNCH,
#                        then SubagentStop {agent_id, agent_type} at completion.
# So a PostToolUse with status async_launched must NOT release -- it records the
# agent id into the lock so the later SubagentStop can match it exactly. Every
# other PostToolUse releases by dispatch id; SubagentStop releases by agent id,
# falling back to agent type (one writer and one executor at a time make the
# type unambiguous). The lock a hook takes is a lock a hook releases; the old
# state.md marker stayed stale for a day when the loop was interrupted.
#
# Never fails a tool call. Always exits 0.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh" 2>/dev/null || exit 0

input=$(cat)
mission=$(mission_active_dir) || exit 0
mission_debug_dump "$mission" mission-release.sh "$input"

event=$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)
dispatch_id=$(printf '%s' "$input" | jq -r '.tool_use_id // empty' 2>/dev/null)
status=$(printf '%s' "$input" | jq -r '.tool_response.status // empty' 2>/dev/null)
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // .tool_response.agentId // .tool_response.agent_id // empty' 2>/dev/null)
agent_type=$(mission_agent_base "$(printf '%s' "$input" | jq -r '.agent_type // .tool_response.agentType // .tool_input.subagent_type // empty' 2>/dev/null)")
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0

# Background launch: annotate, do not release.
if [ "$status" = "async_launched" ]; then
  for lf in "$(mission_writer_file "$mission")" "$(mission_lease_file "$mission")"; do
    [ -f "$lf" ] || continue
    [ -n "$dispatch_id" ] && [ "$(mission_lock_get "$lf" dispatch_id)" = "$dispatch_id" ] || continue
    [ -n "$agent_id" ] && [ -z "$(mission_lock_get "$lf" agent_id)" ] && printf ' agent_id=%s' "$agent_id" >> "$lf"
  done
  exit 0
fi

release() { # <lockfile> <event name>
  local lf="$1" ev="$2" id aid ag match=0
  [ -f "$lf" ] || return 0
  id=$(mission_lock_get "$lf" dispatch_id); aid=$(mission_lock_get "$lf" agent_id); ag=$(mission_lock_get "$lf" agent)
  if [ -n "$dispatch_id" ] && [ "$id" = "$dispatch_id" ]; then match=1
  elif [ -n "$agent_id" ] && [ -n "$aid" ] && [ "$aid" = "$agent_id" ]; then match=1
  elif [ -z "$dispatch_id" ] && [ -z "$aid" ] && [ -n "$agent_type" ] && [ "$ag" = "$agent_type" ]; then match=1
  fi
  [ "$match" = 1 ] || return 0
  mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg ev "$ev" --arg lock "$(cat "$lf")" --arg via "${event:-unknown}" \
    '{ts:$ts, event:$ev, reason:"returned", via:$via, lock:$lock}')"
  rm -f "$lf"
}

release "$(mission_writer_file "$mission")" writer_lock_cleared
release "$(mission_lease_file "$mission")"  lease_released
exit 0
