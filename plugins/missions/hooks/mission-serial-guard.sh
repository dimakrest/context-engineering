#!/bin/bash
# PreToolUse hook (matcher: Agent). The mission's admission control.
#
# Enforces, in this order (cheap and local first):
#   1. one WRITER at a time                 -- .writer lock (legacy: state.md marker)
#   2. progress blocks on open handoff issues
#   3. state.md size cap                    -- a writer is not dispatched against a bloated brief
#   4. spend / dispatch / wall-clock / repair-round caps from mission.md
#   5. one EXECUTOR at a time               -- .lease: anything that can run tests, benchmarks or
#                                              load holds the host; concurrent suites manufactured a
#                                              phantom regression that cost a day (retro C2)
# Static agents (no Write/Edit, no Bash) are never blocked -- fanning those out is the point.
#
# When it ALLOWS a mission agent it also takes the locks and journals the `dispatch`
# event itself. Hooks on one matcher run concurrently, so a separate dispatch hook
# could take a lock for a call this one is blocking; a single writer per event
# cannot. Exit 0 = allow, exit 2 = block.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission=$(mission_active_dir) || exit 0
mission_debug_dump "$mission" mission-serial-guard.sh "$input"

subagent=$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // empty')
[ -n "$subagent" ] || exit 0
prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // empty')
dispatch_id=$(printf '%s' "$input" | jq -r '.tool_use_id // empty')
session=$(printf '%s' "$input" | jq -r '.session_id // empty')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty')

phase=$(mission_phase "$mission")
mission_warn_phase "$mission" "$phase"

class=$(mission_agent_class "$subagent")
base=$(mission_agent_base "$subagent")
feature=$(mission_prompt_feature "$prompt")
is_mission_agent=0; case "$base" in mission-*) is_mission_agent=1 ;; esac
# The model that will actually run (seat override on the call, else the
# definition's default) -- recorded so a seat choice can be measured
# (journal-metrics.sh) instead of asserted in mission.md.
model=$(mission_resolve_model "$input" "$subagent")

journal_dispatch() {
  [ "$is_mission_agent" = 1 ] || return 0
  mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg agent "$base" --arg feature "$feature" \
    --arg id "$dispatch_id" --arg session "$session" --arg class "$class" --arg model "$model" \
    '{ts:$ts, event:"dispatch", agent:$agent, class:$class}
     + (if $model == "" then {} else {model:$model} end)
     + (if $feature == "" then {} else {feature:$feature} end)
     + (if $id == "" then {} else {dispatch_id:$id} end)
     + (if $session == "" then {} else {session_id:$session} end)')"
  # Upsert this session's spend so a later reader can sum the last value per session.
  if [ -n "$transcript" ] && [ -f "$transcript" ]; then
    usd=$(bash "${CLAUDE_PLUGIN_ROOT:-$(dirname "${BASH_SOURCE[0]}")/..}/scripts/mission-spend.sh" "$transcript" 2>/dev/null | awk '/^session_usd:/{print $2}')
    if [ -n "$usd" ] && [ "$usd" != "unknown" ]; then
      mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg session "$session" --argjson usd "$usd" \
        '{ts:$ts, event:"session_cost", session_id:$session, usd:$usd}')"
    fi
  fi
}

# ---- 0. dollar cap -- every class, static included. The researcher is the one agent
# fanned out unbounded; its spend is real money and the transcript already knows it.
# Checked before the static early-exit below so a breach stops the next dispatch,
# not the next writer.
warn_informational=0
dollar=$(mission_budget "$mission" dollar_cap)
if [ -n "$dollar" ]; then
  spend=$(bash "${CLAUDE_PLUGIN_ROOT:-$(dirname "${BASH_SOURCE[0]}")/..}/scripts/mission-spend.sh" "${transcript:-/dev/null}" "$mission/journal.jsonl" 2>/dev/null | awk '/^spend_usd:/{print $2}')
  reserve=$(mission_budget "$mission" terminal_reserve_pct); reserve=${reserve:-0}
  if [ -n "$spend" ] && [ "$spend" != "unknown" ]; then
    exempt=0; [ "$base" = "mission-reviewer" ] && [ "$phase" = "pr" ] && exempt=1
    if [ "$exempt" = 0 ] && awk -v s="$spend" -v c="$dollar" -v r="$reserve" 'BEGIN{exit !(s + c*r/100 >= c)}'; then
      mission_block "MISSION: dollar cap -- spent \$$spend of \$$dollar (terminal-review reserve ${reserve}%).

Measured from the harness (cost-state in the session transcript plus journaled
session_cost from earlier sessions). Halt (class: block); the remaining budget is
reserved for the terminal review."
    fi
  fi
else warn_informational=1; fi

# ---- static agents: never blocked by locks or count caps, still journaled if they are mission agents
if [ "$class" = "static" ]; then
  journal_dispatch
  exit 0
fi

# ---- 1. writer lock
if [ "$class" = "writer" ]; then
  wf=$(mission_writer_file "$mission")
  if [ -f "$wf" ]; then
    if mission_lock_stale "$mission" "$wf"; then
      echo "mission: stale writer lock ($(cat "$wf")) released -- holder already returned or exceeded ${MISSION_LEASE_TTL_H}h" >&2
      mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg lock "$(cat "$wf")" '{ts:$ts, event:"writer_lock_cleared", reason:"stale", lock:$lock}')"
      rm -f "$wf"
    elif [ -n "$feature" ] && [ "$(mission_lock_get "$wf" feature)" = "$feature" ] && [ -z "$dispatch_id" ]; then
      : # same feature re-dispatched without a tool id (tests / manual runs): treat as the same dispatch
    else
      mission_block "MISSION: a writing agent is already active -- $(mission_lock_get "$wf" agent) $(mission_lock_get "$wf" feature) (since $(mission_lock_get "$wf" ts), lock $wf).

One writing agent at a time. Wait for its handoff and ingest it; the lock is
released automatically when the agent returns. If the agent was killed and the
lock is stuck, it expires after ${MISSION_LEASE_TTL_H}h -- or delete $wf deliberately
and journal why.

Static agents (mission-researcher, anything without Write/Edit/Bash) are never
blocked and may run in parallel."
    fi
  else
    # Legacy missions keep the writer marker in state.md. Same semantics as before:
    # a marker naming this feature is the dispatch we are about to make.
    active=$(mission_active_writer "$mission")
    if [ -n "$active" ]; then
      if [ -z "$feature" ] || ! printf '%s' "$active" | grep -qF "$feature"; then
        mission_block "MISSION: a writing agent is already active -- '$active' (per $mission/state.md, legacy marker).

One writing agent at a time. Wait for its handoff, ingest it, clear
'**Active writing agent:**' in state.md, then dispatch the next one."
      fi
    fi
  fi

  # ---- 2. open issues
  issues=$(mission_open_issues "$mission")
  if [ -n "$issues" ]; then
    mission_block "MISSION: progress is blocked by unresolved handoff issues in $mission/state.md:

$issues

Resolve each one, or defer it explicitly into followups.md, before dispatching
another writing agent. An unaddressed issue does not disappear -- it resurfaces
later, more expensively, with less context."
  fi

  # ---- 3. state.md size cap (writers only: the brief is what bloats)
  lines=$(mission_state_lines "$mission"); cap=$(mission_state_cap "$mission")
  if [ "$(mission_has_block "$mission")" = 1 ] && [ "${lines:-0}" -gt "${cap:-200}" ]; then
    mission_block "MISSION: state.md is $lines lines, over its cap of $cap.

Archive closed milestones before dispatching another writer:
  bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/mission-archive.sh\" $mission M<n>
Every dispatched agent is briefed from this file; 35% of the last mission's was
closed history, broadcast ~100 times. (Raise state_cap_lines in the fenced block
only with a reason journaled.)"
  fi
fi

# ---- 4. count caps (writers and executors)
dcap=$(mission_budget "$mission" dispatch_cap)
if [ -n "$dcap" ]; then
  n=$(mission_journal_count "$mission" dispatch '.class != "static"')
  if [ "$n" -ge "${dcap%%.*}" ]; then
    mission_block "MISSION: dispatch cap reached -- $n of $dcap writer/executor dispatches used (mission.md, Budget).

A cap is a decision made while thinking clearly. Do not raise it to finish; halt
(class: block) and report what remains with the decision-card shape."
  fi
else warn_informational=1; fi

wcap=$(mission_budget "$mission" wall_cap_h)
if [ -n "$wcap" ] && [ -f "$mission/journal.jsonl" ]; then
  used_h=$(jq -s '[.[] | select(.event=="agent_return" or .event=="agent_stopped") | .duration_s // 0] | add // 0 | . / 3600' "$mission/journal.jsonl" 2>/dev/null)
  if [ -n "$used_h" ] && awk -v u="$used_h" -v c="$wcap" 'BEGIN{exit !(u > c)}'; then
    mission_block "MISSION: active wall-clock cap reached -- ${used_h}h of ${wcap}h of agent time (sum of journaled duration_s).

Halt (class: block) and report."
  fi
fi

rcap=$(mission_budget "$mission" repair_rounds)
if [ -n "$rcap" ] && [ -n "$feature" ] && [ -f "$mission/followups.md" ]; then
  over=$(python3 - "$mission/followups.md" "$feature" "${rcap%%.*}" <<'PY' 2>/dev/null
import re, sys, collections
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
feat, cap = sys.argv[2], int(sys.argv[3])
entries = re.split(r'(?m)^##\s+FU\d{3}', text)[1:]
repairs = collections.defaultdict(set)   # assertion -> {feature}
for e in entries:
    a = re.search(r'\*\*Assertion:\*\*\s*([A-Z]\d{3}[a-z]?)', e)
    d = re.search(r'\*\*Disposition:\*\*\s*repair as (F\d{3})', e)
    if a and d: repairs[a.group(1)].add(d.group(1))
for a, fs in repairs.items():
    if feat in fs and len(fs) > cap:
        print(f"{a} already has {len(fs)-1} repair features ({', '.join(sorted(fs - {feat}))})"); break
PY
)
  if [ -n "$over" ]; then
    mission_block "MISSION: repair-round cap ($rcap per assertion) exceeded by $feature -- $over.

A third repair for the same assertion means the diagnosis is wrong, not the
code. Run the root-cause classification (contract ambiguity / implementation
defect / inadequate evidence / bad brief / environment) and halt if it changes
scope or weakens an assertion."
  fi
fi

[ "$warn_informational" = 1 ] && echo "mission: no Dollar/Dispatch cap in $mission/mission.md -- caps are informational only for this mission" >&2

# ---- 5. execution lease (writers and executors)
lf=$(mission_lease_file "$mission")
if [ -f "$lf" ]; then
  if mission_lock_stale "$mission" "$lf"; then
    echo "mission: stale execution lease ($(cat "$lf")) released" >&2
    mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg lock "$(cat "$lf")" '{ts:$ts, event:"lease_released", reason:"stale", lock:$lock}')"
    rm -f "$lf"
  elif [ -n "$dispatch_id" ] && [ "$(mission_lock_get "$lf" dispatch_id)" = "$dispatch_id" ]; then
    : # re-entry of the same dispatch
  else
    mission_journal "$mission" "$(jq -nc --arg ts "$(mission_now)" --arg agent "$base" --arg feature "$feature" --arg holder "$(cat "$lf")" \
      '{ts:$ts, event:"lease_wait", agent:$agent, feature:$feature, holder:$holder}')"
    mission_block "MISSION: execution lease held -- $(mission_lock_get "$lf" agent) $(mission_lock_get "$lf" feature) is running on this host (since $(mission_lock_get "$lf" ts)).

One executor at a time: anything that runs tests, benchmarks, worktrees or load
takes the host lease, because concurrent suites on a laptop manufacture phantom
regressions. Wait for it to return (the lease is released automatically), or
dispatch a static agent meanwhile. Stuck lease: expires after ${MISSION_LEASE_TTL_H}h."
  fi
fi

# ---- grant
mission_lock_write "$lf" "$base" "$feature" "$dispatch_id" "$session"
[ "$class" = "writer" ] && mission_lock_write "$(mission_writer_file "$mission")" "$base" "$feature" "$dispatch_id" "$session"
journal_dispatch
exit 0
