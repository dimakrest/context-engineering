#!/bin/bash
# Dollars actually spent, from the harness -- not from estimates.
#
# usage: mission-spend.sh <session-transcript.jsonl> [<journal.jsonl>]
#
# Claude Code appends `cost-state` records to the session transcript
# (totalCostUSD, modelUsage). The first one is always 0 and the last one wins.
# prints:
#   session_usd: <n>|unknown          this transcript's last cost-state
#   spend_usd:   <n>|unknown          session_usd + the last journaled session_cost of every OTHER session
#   sessions:    <k>                  how many sessions contributed
# Exit 0 always; "unknown" is the honest answer when nothing is recorded.
# (~/.claude/stats-cache.json is not used: it lags by days and reports costUSD 0.)

set -uo pipefail
transcript="${1:-}"; journal="${2:-}"

session_usd=unknown; session_id=""
if [ -n "$transcript" ] && [ -f "$transcript" ]; then
  last=$(grep -E '"type": ?"cost-state"' "$transcript" 2>/dev/null | tail -1)
  if [ -n "$last" ]; then
    session_usd=$(printf '%s' "$last" | jq -r '.totalCostUSD // empty' 2>/dev/null)
    session_id=$(printf '%s' "$last" | jq -r '.sessionId // empty' 2>/dev/null)
    [ -n "$session_usd" ] || session_usd=unknown
  fi
fi

others=0; sessions=0
if [ -n "$journal" ] && [ -f "$journal" ]; then
  # last session_cost per session id, excluding this transcript's session
  others=$(jq -s --arg me "$session_id" '
      [ .[] | select(.event=="session_cost") | select(.session_id != $me) ]
      | group_by(.session_id) | map(last.usd) | add // 0' "$journal" 2>/dev/null)
  sessions=$(jq -s --arg me "$session_id" '
      [ .[] | select(.event=="session_cost") | select(.session_id != $me) | .session_id ] | unique | length' "$journal" 2>/dev/null)
  others=${others:-0}; sessions=${sessions:-0}
fi

if [ "$session_usd" = unknown ]; then
  if [ "${sessions:-0}" -gt 0 ]; then spend=$others; else spend=unknown; fi
else
  spend=$(awk -v a="$session_usd" -v b="$others" 'BEGIN{printf "%.2f", a + b}')
  sessions=$(( sessions + 1 ))
fi

printf 'session_usd: %s\nspend_usd: %s\nsessions: %s\n' "$session_usd" "$spend" "$sessions"
exit 0
