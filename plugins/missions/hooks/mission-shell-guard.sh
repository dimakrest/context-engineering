#!/bin/bash
# PreToolUse hook (matcher: Bash). What a mission agent's own shell may not do.
#
# Two promises the prompts make that no earlier hook could keep:
#   - BLINDNESS. mission-blind-review.sh polices the reviewer's *brief*; but the
#     reviewer holds Bash, and `git log`, `gh pr view` or `graphify prs` typed
#     into its own shell hand it the author's reasoning just the same. The
#     harness names the calling subagent in `agent_type` on every tool event
#     fired inside a subagent, so this hook can tell the reviewer's shell from
#     the orchestrator's -- which legitimately runs `git log` to verify a handoff.
#   - SPEND. graphify's labelling / semantic extraction and repowise's page
#     generation call an LLM through their own provider keys: spend that never
#     reaches the session transcript mission-spend.sh measures, so no cap can
#     see it. Blocked for every caller while a mission is active; the index-only
#     forms (`graphify update`, `repowise update|init --index-only`) stay
#     allowed, as is `repowise init --no-prose` on current versions. Exceptions
#     belong to an individual invocation, never to the whole shell command.
# Exit 0 = allow, exit 2 = block.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh"

input=$(cat)
mission=$(mission_active_dir) || exit 0
mission_debug_dump "$mission" mission-shell-guard.sh "$input"

raw=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -n "$raw" ] || exit 0

# Writing documentation that mentions `repowise update` is not running it.
cmd=$(printf '%s\n' "$raw" | mission_strip_heredocs)

# Who is calling. The harness documents `agent_type` on every tool event fired
# inside a subagent; no payload on this machine has been captured to confirm it,
# so a guard keyed on that field alone could be silently dead. Fallback: the
# execution lease. While `.lease` names mission-reviewer, a blindness-breaking
# command is treated as the reviewer's -- the orchestrator (waiting on a
# foreground dispatch) has no business running `git log` in that window, and if
# it does, it gets a message instead of a silent pass. Settle it on the first
# dry run: MISSION_HOOK_DEBUG=1 and read .hook-debug.log.
agent=$(mission_agent_base "$(printf '%s' "$input" | jq -r '.agent_type // empty')")
via="agent_type"
if [ -z "$agent" ]; then
  lf=$(mission_lease_file "$mission")
  if [ -f "$lf" ] && [ "$(mission_lock_get "$lf" agent)" = "mission-reviewer" ]; then
    agent=mission-reviewer; via="execution lease (no agent_type in the payload)"
  fi
fi

# ---- spend: LLM-backed index commands, whoever runs them
# block_llm_spend <tool> <subcommands> <safe flags> <message>
# Check each literal invocation independently. This is a conservative shell guard,
# not a shell interpreter: ambiguous/dynamic forms must be run as simple commands.
block_llm_spend() {
  if python3 - "$cmd" "$1" "$2" "$3" <<'PYGUARD'
import os, re, shlex, sys
command, tool, subcommands, safe_flags = sys.argv[1:]
pattern = re.compile(r"(?:^|[;&|\s/()\"'])" + re.escape(tool)
                     + r"\s+(?:" + subcommands + r")(?=\s|[;&|()]|$)")
if not pattern.search(command):
    sys.exit(0)
# Substitution and escaped spellings cannot establish a literal safe invocation.
if any(c in command for c in ("$", "`", "\\")):
    sys.exit(1)
try:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    tokens = list(lexer)
except ValueError:
    sys.exit(1)
separators = set(";&|()\n")
matched = 0
for i, token in enumerate(tokens[:-1]):
    if os.path.basename(token) != tool or tokens[i + 1] not in subcommands.split("|"):
        continue
    matched += 1
    args, safe_args = [], []
    redirected = False
    for arg in tokens[i + 2:]:
        if arg == "--" or (arg and all(c in separators for c in arg)):
            break
        # A redirect's filename cannot supply a safe flag. Keep scanning for
        # conflicting options after redirections, but accept safe flags before them.
        if arg and all(c in ";&|()<>\n" for c in arg) and any(c in "<>" for c in arg):
            redirected = True
        args.append(arg)
        if not redirected:
            safe_args.append(arg)
    # --full dispatches before index-only in Repowise; positive docs/prose
    # overrides are also refused even alongside a safe flag.
    if tool == "repowise" and any(a.split("=", 1)[0] in
                                   {"--full", "--docs", "--prose"} for a in args):
        sys.exit(1)
    if not safe_flags or not set(safe_flags.split("|")).intersection(safe_args):
        sys.exit(1)
# Quoted shell programs / wrappers may hide an invocation inside one token.
# Never let another invocation's safe flag authorize that unparsed program.
if matched != len(pattern.findall(command)):
    sys.exit(1)
PYGUARD
  then
    return 0
  fi
  mission_block "$4"
}
GRAPHIFY_MSG="MISSION: graphify label / extract / cluster-only (without --no-label) call an LLM.

That spend goes through graphify's own provider key and never reaches the
session transcript the dollar cap is measured from. During a mission the graph
is refreshed with \`graphify update <path>\` (AST-only) and nothing else."
REPOWISE_MSG="MISSION: repowise update / init generate wiki pages through an LLM.

That spend is billed to repowise's provider key, outside every mission cap, and
\`repowise init\` also rewrites the repo's CLAUDE.md and .mcp.json. The only
forms a mission may run are \`repowise update --index-only\` for refreshes and
\`repowise init --no-prose\` for current initialization (\`init --index-only\`
on older builds). Do not combine these with --full, --docs or --prose. Read
the index with \`repowise health\` / \`repowise search\` / the MCP tools."
block_llm_spend graphify 'label|extract' ''             "$GRAPHIFY_MSG"
block_llm_spend graphify 'cluster-only'  '--no-label'   "$GRAPHIFY_MSG"
block_llm_spend repowise 'generate'      ''             "$REPOWISE_MSG"
block_llm_spend repowise 'update'        '--index-only' "$REPOWISE_MSG"
block_llm_spend repowise 'init'          '--index-only|--no-prose' "$REPOWISE_MSG"

# ---- blindness: the reviewer's shell
[ "$agent" = "mission-reviewer" ] || exit 0

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+(log|show|diff|blame|reflog|shortlog)([[:space:]]|$)'; then
  mission_block "MISSION: the blind reviewer does not run git log / show / diff / blame.
(caller identified via $via)

Your input is the patch file named in your brief -- the exact range, without
the author's commit body. Read that file; a branch diff is not your feature's
diff, and the commit body is the reasoning blindness exists to withhold.
If you are the orchestrator: wait for the reviewer to return, then run this."
fi

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])gh([[:space:]]|$)|(^|[;&|[:space:]])graphify[[:space:]]+prs([[:space:]]|$)'; then
  mission_block "MISSION: the blind reviewer does not read PRs.

\`gh\` and \`graphify prs\` return PR titles and bodies -- the author's account
of the change. Grade the patch and its callers; nothing else."
fi

if printf '%s' "$cmd" | grep -qE 'handoffs?/F[0-9]{3}'; then
  mission_block "MISSION: the blind reviewer does not read handoffs.

The handoff is the worker's claim about its own work. Your verdict is worth
something only because you never saw it."
fi

exit 0
