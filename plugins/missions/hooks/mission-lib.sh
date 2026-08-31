#!/bin/bash
# Shared helpers for the mission-* hooks and scripts. Sourced, never executed directly.
#
# Design rule for every mission hook: when no mission is running, do nothing and
# exit 0. Registered through the plugin, these fire on Agent / Write / Edit / Bash
# in EVERY session in EVERY repo -- so a bug that fires outside a mission doesn't
# just break this project's work, it breaks all of it. "No active mission" is by
# far the most common case, and must be both the cheapest path and the safest.
#
# Two sources of truth, deliberately separated:
#   - state.md  is the ORCHESTRATOR's file. Hooks read it, never write it.
#   - .writer / .lease  are the HOOKS' files. They hold the writer lock and the
#     host execution lease, are taken by mission-serial-guard.sh when it allows a
#     dispatch, and are released by mission-release.sh when the agent returns.
#     Locks that lived inside state.md went stale whenever the loop skipped its
#     bookkeeping, and then blocked the next day's work (analytics-hour-filter,
#     2026-08-31). A lock nobody has to remember to clear cannot go stale that way.

MISSION_ROOT="${CLAUDE_PROJECT_DIR:-.}/.missions"
MISSION_PHASES="planning implementing validating negotiating pr halted done"
MISSION_LEASE_TTL_H="${MISSION_LEASE_TTL_H:-3}"
MISSION_STATE_CAP_LINES_DEFAULT=200

# ---------------------------------------------------------------- state fields

# Prints one field from the fenced ```mission-state block at the top of state.md.
# Trailing "# comment" is stripped. Empty when the block or the key is absent.
mission_field() {
  awk -v key="$2" '
    /^```mission-state[[:space:]]*$/ { inblock = 1; next }
    inblock && /^```/                { exit }
    inblock {
      line = $0
      sub(/[[:space:]]+#.*$/, "", line)
      if (match(line, "^" key ":[[:space:]]*")) {
        print substr(line, RLENGTH + 1)
        exit
      }
    }
  ' "$1/state.md" 2>/dev/null
}

# Prints the value of a legacy `**Label:**` line (the pre-v2 prose header).
mission_legacy_field() {
  grep -m1 -iE "^\*\*$2:\*\*" "$1/state.md" 2>/dev/null \
    | sed -E 's/^[^:]*:\**[[:space:]]*//; s/[[:space:]]*$//'
}

# 1 when state.md has a fenced block, else 0.
mission_has_block() {
  grep -qE '^```mission-state[[:space:]]*$' "$1/state.md" 2>/dev/null && echo 1 || echo 0
}

# Prints the phase, lowercased and normalised to the enum. Falls back to the
# legacy header. Unknown tokens are returned as-is (callers decide; a mission
# with an unknown phase is treated as ACTIVE, never silently inert).
mission_phase() {
  local v
  v=$(mission_field "$1" phase)
  [ -n "$v" ] || v=$(mission_legacy_field "$1" Phase | awk '{print $1}')
  v=$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z].*$//')
  case "$v" in
    implementation|implement) v=implementing ;;
    validation|validate)      v=validating ;;
    negotiation|negotiate)    v=negotiating ;;
    plan|planned)             v=planning ;;
    complete|completed)       v=done ;;
    halt|paused)              v=halted ;;
  esac
  printf '%s' "$v"
}

# 0 when the phase is in the enum, 1 otherwise.
mission_phase_known() {
  local p
  for p in $MISSION_PHASES; do [ "$p" = "$1" ] && return 0; done
  return 1
}

# Prints a stderr warning once per hook run when the phase is not in the enum.
mission_warn_phase() {
  local phase="$2"
  mission_phase_known "$phase" && return 0
  echo "mission: state.md phase '$phase' is not one of [$MISSION_PHASES]; treating the mission as active" >&2
}

# Prints the active mission directory, or nothing. Returns 1 when none.
# A mission is active while its state.md phase is not `done` or `halted`.
# If several are active we take the first: the architecture assumes one mission
# at a time, and guessing between two is worse than being predictable.
mission_active_dir() {
  local d phase
  [ -d "$MISSION_ROOT" ] || return 1
  for d in "$MISSION_ROOT"/*/; do
    [ -f "${d}state.md" ] || continue
    phase=$(mission_phase "${d%/}")
    case "$phase" in
      done|halted) continue ;;
    esac
    printf '%s' "${d%/}"
    return 0
  done
  return 1
}

# Prints the open-issue bullets under `## Open issues`, one per line.
# A single bullet reading "none" counts as no open issues.
mission_open_issues() {
  awk '
    /^##[[:space:]]+[Oo]pen issues/ { inblock = 1; next }
    inblock && /^##[[:space:]]/     { inblock = 0 }
    inblock && /^[[:space:]]*-[[:space:]]*/ {
      line = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      if (tolower(line) == "none" || line == "") next
      print line
    }
  ' "$1/state.md" 2>/dev/null
}

mission_state_lines() { wc -l < "$1/state.md" 2>/dev/null | tr -d ' '; }
mission_state_cap() {
  local v; v=$(mission_field "$1" state_cap_lines)
  printf '%s' "${v:-$MISSION_STATE_CAP_LINES_DEFAULT}"
}

# ---------------------------------------------------------------- locks

mission_now()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
mission_epoch() { date -u +%s; }

# Lock files are one line of key=value tokens:
#   agent=mission-worker feature=F003 dispatch_id=toolu_x session=abc ts=... epoch=1234
mission_lock_get() { # <lockfile> <key>
  [ -f "$1" ] || return 0
  tr ' ' '\n' < "$1" | awk -F= -v k="$2" '$1==k {sub(/^[^=]*=/, ""); print; exit}'
}

mission_writer_file() { printf '%s/.writer' "$1"; }
mission_lease_file()  { printf '%s/.lease' "$1"; }

# Prints the active writer as "<agent> <feature>" -- from .writer when present,
# else from the legacy `**Active writing agent:**` line. Empty when idle.
mission_active_writer() {
  local wf; wf=$(mission_writer_file "$1")
  if [ -f "$wf" ]; then
    printf '%s %s' "$(mission_lock_get "$wf" agent)" "$(mission_lock_get "$wf" feature)"
    return 0
  fi
  local v; v=$(mission_legacy_field "$1" "Active writing agent")
  case "$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')" in
    ''|none|-|idle) ;;
    *) printf '%s' "$v" ;;
  esac
}

# 0 when a lock is stale: its holder's agent_return is already journaled, or it
# is older than MISSION_LEASE_TTL_H hours. A stale lock is released, not obeyed --
# the alternative is a phantom writer blocking a day of work.
mission_lock_stale() { # <mission> <lockfile>
  local id aid ep now ttl
  [ -f "$2" ] || return 1
  id=$(mission_lock_get "$2" dispatch_id); aid=$(mission_lock_get "$2" agent_id)
  if [ -f "$1/journal.jsonl" ] && jq -e --arg id "$id" --arg aid "$aid" \
       'select((.event == "agent_return" and $id != "" and .dispatch_id == $id)
            or (.event == "agent_stopped" and $aid != "" and .agent_id == $aid))' "$1/journal.jsonl" >/dev/null 2>&1; then
    return 0
  fi
  ep=$(mission_lock_get "$2" epoch); now=$(mission_epoch)
  ttl=$(( ${MISSION_LEASE_TTL_H%%.*} * 3600 ))
  [ -n "$ep" ] && [ $(( now - ep )) -gt "$ttl" ] && return 0
  return 1
}

mission_lock_write() { # <lockfile> <agent> <feature> <dispatch_id> <session>
  printf 'agent=%s feature=%s dispatch_id=%s session=%s ts=%s epoch=%s\n' \
    "$2" "$3" "$4" "$5" "$(mission_now)" "$(mission_epoch)" > "$1"
}

# ---------------------------------------------------------------- agents

# Strips a plugin namespace ("missions:mission-worker" -> "mission-worker").
mission_agent_base() { printf '%s' "${1##*:}"; }

# Prints the path of an agent's definition file, searching the plugin, the
# project and the user level, or nothing.
mission_agent_def() {
  local base d
  base=$(mission_agent_base "$1")
  for d in "${CLAUDE_PLUGIN_ROOT:-}/agents" "${CLAUDE_PROJECT_DIR:-.}/.claude/agents" "$HOME/.claude/agents"; do
    [ -f "$d/$base.md" ] && { printf '%s' "$d/$base.md"; return 0; }
  done
  return 1
}

# Prints the agent's declared tools, one per line, from the YAML frontmatter
# (inline "tools: A, B" or a "- item" list). Empty when undeclared.
mission_agent_tools() {
  local def; def=$(mission_agent_def "$1") || return 0
  awk '
    NR==1 && /^---/ { fm = 1; next }
    fm && /^---/    { exit }
    fm && /^tools:/ {
      line = $0; sub(/^tools:[[:space:]]*/, "", line)
      gsub(/[\[\]]/, "", line)
      if (line != "") { n = split(line, a, /,[[:space:]]*/); for (i = 1; i <= n; i++) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", a[i]); if (a[i] != "") print a[i] } }
      inlist = 1; next
    }
    fm && inlist && /^[[:space:]]+-[[:space:]]*/ { line = $0; sub(/^[[:space:]]+-[[:space:]]*/, "", line); gsub(/[[:space:]]+$/, "", line); print line; next }
    fm && inlist && /^[^[:space:]]/ { inlist = 0 }
  ' "$def"
}

# Prints the agent definition's `model:` from the YAML frontmatter, verbatim (an
# alias -- sonnet, opus, haiku, fable, inherit -- or whatever full id the author
# wrote; this reader does not validate, check.sh validates seat lines). Empty when
# undeclared -- the harness then uses its own default, which we do not guess at.
mission_agent_model() {
  local def; def=$(mission_agent_def "$1") || return 0
  awk '
    NR==1 && /^---/ { fm = 1; next }
    fm && /^---/    { exit }
    fm && /^model:/ { v = $0; sub(/^model:[[:space:]]*/, "", v); gsub(/[[:space:]]+$/, "", v); print v; exit }
  ' "$def"
}

# Prints the model a dispatch will actually run on: the Agent call's `model:`
# override when the payload carries one, else the definition's default. Used by
# the serial guard (dispatch) and the journal (agent_return) so both record the
# same answer.   usage: mission_resolve_model <hook input json> <agent type>
mission_resolve_model() {
  local m
  m=$(printf '%s' "$1" | jq -r '.tool_input.model // empty' 2>/dev/null)
  [ -n "$m" ] || m=$(mission_agent_model "$2")
  printf '%s' "$m"
}

# Prints the agent's class: writer | executor | static.
#   writer   -- can change files (Write/Edit/NotebookEdit/MultiEdit): takes the writer lock AND the lease
#   executor -- can run commands (Bash) but not edit: takes the execution lease
#   static   -- neither: never blocked, fans out freely
# Built-in read-only agents are static. Anything with no definition is a writer
# (default-deny: a new agent type is guarded until someone declares its tools).
mission_agent_class() {
  local base tools
  base=$(mission_agent_base "$1")
  case "$base" in
    Explore|Plan|claude-code-guide|statusline-setup) echo static; return 0 ;;
  esac
  tools=$(mission_agent_tools "$1")
  if [ -z "$tools" ]; then
    mission_agent_def "$1" >/dev/null || { echo writer; return 0; }
    echo writer; return 0
  fi
  if printf '%s\n' "$tools" | grep -qiE '^(Write|Edit|NotebookEdit|MultiEdit|\*)$'; then echo writer; return 0; fi
  if printf '%s\n' "$tools" | grep -qiE '^Bash(\(.*\))?$'; then echo executor; return 0; fi
  echo static
}

# Prints the feature id a dispatch prompt is for. The dispatch template puts
# "Feature: F00n" on the first line; a digest pasted lower down may name other
# features, so the first F0nn anywhere is only the legacy fallback.
mission_prompt_feature() {
  local f
  f=$(printf '%s' "$1" | head -1 | grep -oE 'Feature:[[:space:]]*F[0-9]{3}' | grep -oE 'F[0-9]{3}' | head -1)
  [ -n "$f" ] || f=$(printf '%s' "$1" | grep -oE '(^|[^A-Za-z])F[0-9]{3}' | grep -oE 'F[0-9]{3}' | head -1)
  printf '%s' "$f"
}

# ---------------------------------------------------------------- budget

# Prints a numeric budget from mission.md's `## Budget` block, or nothing.
# keys: dollar_cap | dispatch_cap | wall_cap_h | repair_rounds | terminal_reserve_pct
mission_budget() {
  local f="$1/mission.md" pat
  [ -f "$f" ] || return 0
  case "$2" in
    dollar_cap)           pat='Dollar cap' ;;
    dispatch_cap)         pat='Dispatch cap' ;;
    wall_cap_h)           pat='Active wall-clock cap' ;;
    repair_rounds)        pat='Repair rounds per assertion' ;;
    terminal_reserve_pct) pat='Terminal-review reserve' ;;
    *) return 0 ;;
  esac
  grep -m1 -iE "^-[[:space:]]*(\*\*)?$pat" "$f" | sed -E 's/^[^:]*:[[:space:]]*//' \
    | grep -oE '[0-9]+([.][0-9]+)?' | head -1
}

# ---------------------------------------------------------------- journal

# Appends one JSON object (already serialised) to the mission journal. Never fails.
mission_journal() { printf '%s\n' "$2" >> "$1/journal.jsonl" 2>/dev/null || true; }

# Counts journal events by name, optionally filtered by a jq expression.
mission_journal_count() { # <mission> <event> [<jq filter>]
  [ -f "$1/journal.jsonl" ] || { echo 0; return 0; }
  jq -c --arg e "$2" "select(.event == \$e) | select(${3:-true})" "$1/journal.jsonl" 2>/dev/null | wc -l | tr -d ' '
}

# ---------------------------------------------------------------- debug

# With MISSION_HOOK_DEBUG=1 in the environment, every hook appends its raw stdin
# to <mission>/.hook-debug.log -- the only way to learn what a harness event
# actually carries. Off by default; never affects the exit code.
mission_debug_dump() { # <mission> <hook name> <input>
  [ "${MISSION_HOOK_DEBUG:-0}" = 1 ] || return 0
  printf '%s %s %s\n' "$(mission_now)" "$2" "$3" >> "$1/.hook-debug.log" 2>/dev/null || true
}

# ---------------------------------------------------------------- misc

# Strips heredoc bodies from a shell command so that a document *containing*
# "git push" is not mistaken for a command that runs it.
mission_strip_heredocs() {
  awk '
    {
      if (delim != "") {
        line = $0
        sub(/^[ \t]+/, "", line)
        if (line == delim) { delim = "" }
        next
      }
      if (match($0, /<<-?[ \t]*['"'"'"]?[A-Za-z_][A-Za-z0-9_]*['"'"'"]?/)) {
        tag = substr($0, RSTART, RLENGTH)
        gsub(/^<<-?[ \t]*|['"'"'"]/, "", tag)
        delim = tag
      }
      print
    }
  '
}

# Blocks the tool call: message to stderr, exit 2.
mission_block() {
  echo "$1" >&2
  exit 2
}
