---
name: setup-statusline
description: Install a two-line status bar for Claude Code that shows model name, reasoning effort, project directory, git branch with staged/modified counts, worktree, PR number and review state, context window usage with progress bar, premium-pricing-tier flag, session cost, duration, lines changed, and prompt-cache health with miss causes. Use this skill when the user wants to set up a status line, customize their Claude Code status bar, see token usage or cost in the terminal, add git info to their prompt, or mentions "statusline", "status bar", or "status line setup".
disable-model-invocation: true
user_invocable: true
allowed-tools: Bash, Read, Edit, Write
---

# Setup Status Line

Install a two-line status bar that keeps you informed at a glance while working in Claude Code.

**Line 1** — session context: model, reasoning effort, project directory, git branch with change counts, and (when present) worktree and PR:
```
[Opus 5 1M] [xhigh] my-project ⑂ main ~3 · ⧉ issue-28 · PR #35 ✓
```

**Line 2** — resource usage: context window progress bar (green/yellow/red), token counts, session cost, elapsed and API time, lines changed, and prompt-cache health:
```
▓▓▓░░░░░░░ 34% (341k/1.0M) · $8.41 · 45m21s (18m0s api) · +156 −23 · ✓ cache 1h 94%
```

Every segment after the directory hides itself when there is nothing to say, so a
quiet session stays short.

## What the segments mean

**Effort** is read from `.effort.level` in the status line payload, falling back
to `$CLAUDE_EFFORT` in the environment. Those are the only two sources that
track a mid-session `/effort` change, and they are the only two consulted:
`settings.json` is deliberately *not* a fallback, because its per-model key is a
canonical model id that the payload's id does not always match, and a miss there
lands on a global `effortLevel` that would paint a level on models which have
none. Models that do not support reasoning effort (Haiku 4.5, Sonnet 4.x, Opus
4.x) carry no effort level at all, and the segment is omitted rather than
showing an invented default.

**`⚠200k`** appears when the last request was billed in the premium pricing tier.
Claude Code computes it from the last assistant message's usage, not the running
context total, so it reflects what you were just charged rather than a threshold
you crossed once.

**`!N`** in red counts paths with an unresolved merge conflict (porcelain `U`
in either column). It is deliberately the loudest thing on line 1: a half-done
merge is worth interrupting for. `+N` green counts staged paths and `~N` yellow
unstaged ones, typechanges included.

**Prompt cache** has four states, grey when healthy and loud only when a rebuild
costs you tokens. The whole segment is hidden when the payload reports
`caching_observed: false` — some providers and gateways never report cache
usage, so `warm` stays false forever and a permanent amber "cold" would be a
standing false alarm:

| rendered | meaning |
|---|---|
| `✓ cache 1h 94%` | warm, with hit ratio — ignorable |
| `✓ cache 1h` | warm, hit ratio not yet measured |
| `⏳ cache 1h 3m6s` | expiring within 5 minutes |
| `○ cache cold 338k` | cold, and what a rebuild would cost |
| `✗ cache tools_changed 310k` | a miss just happened, with its cause and cost |

Miss causes come from `prompt_cache.last_miss_cause` and include
`tools_changed`, `system_prompt_changed`, `model_changed`, `messages_rewritten`,
`ttl_expired_5m`, `ttl_expired_1h`, `likely_server_side` and `unknown`. The
cause is optional detail: the miss state is driven by `last_miss_at` being
within 90 seconds, and an undiagnosed miss shows as plain `✗ cache miss`.
Gating on the cause instead would report a healthy cache in the very render
that re-paid for it, and gating on nothing would pin the bar red for the rest
of the session, since `last_miss_cause` never clears.

## Process

1. **Check prerequisites.** Verify `jq` is installed by running `which jq`. If
   missing, tell the user to install it (`brew install jq` on macOS,
   `sudo apt install jq` on Linux) and stop.

   The script needs only bash 3.2, so the `/bin/bash` that macOS ships is fine.

2. **Handle an existing statusline.** Check whether `~/.claude/statusline.sh`
   already exists. If it does, first compare it with the bundled script
   (`cmp -s`); if they are identical there is nothing to preserve, so skip the
   backup. Otherwise back it up to a *timestamped* name and tell the user where
   it is:
   ```bash
   cp ~/.claude/statusline.sh "~/.claude/statusline.sh.bak.$(date +%Y%m%d%H%M%S)"
   ```
   Do not write a fixed `statusline.sh.bak`: on a second run of this skill
   (a version bump, or the user re-invoking it) that would overwrite their
   original with a copy of the bundled script — destroying the very thing the
   backup exists to protect.

3. **Install the script.** Copy the bundled script and make it executable:
   ```bash
   cp "${CLAUDE_SKILL_DIR}/statusline.sh" ~/.claude/statusline.sh
   chmod +x ~/.claude/statusline.sh
   ```

4. **Configure settings.** Read `~/.claude/settings.json`, then use the Edit tool
   to add or update the `statusLine` key while preserving all other settings:
   ```json
   "statusLine": {
     "type": "command",
     "command": "~/.claude/statusline.sh",
     "padding": 2,
     "refreshInterval": 5
   }
   ```
   If `~/.claude/settings.json` doesn't exist, create it with just this config
   wrapped in `{}`.

   **`refreshInterval`** re-runs the command every N seconds on top of Claude
   Code's own event-driven updates. It is what makes the elapsed clock and the
   cache countdown tick — neither has an event behind it — and it keeps the git
   branch honest when you change it in another terminal. The script renders in
   ~20ms, so at 5 seconds this costs well under 1% of a core. Drop the key if the
   user prefers a bar that only repaints on events.

   If you change this away from 5, keep it *below* `CACHE_TTL` at the top of
   `statusline.sh` (15 seconds). At or above it, the git cache expires exactly
   as the next timed render arrives and never serves once, so every render
   forks git.

   **Do not clobber an existing `command` that points somewhere else.** Some
   setups chain the status line through a wrapper script (to tee the payload to
   telemetry, for instance). If `command` already points at something other than
   `~/.claude/statusline.sh`, read that file first: if it pipes the payload on to
   `~/.claude/statusline.sh`, leave `command` alone — installing the script is
   enough. Only change `command` if nothing is chaining to it.

5. **Verify.** Confirm the script exists, is executable, and `settings.json`
   contains the `statusLine` entry and is still valid JSON (`jq empty
   ~/.claude/settings.json`). To see it render without waiting for Claude Code,
   pipe a payload through it:
   ```bash
   echo '{"model":{"id":"claude-opus-5","display_name":"Opus 5"},
          "effort":{"level":"xhigh"},
          "workspace":{"current_dir":"'"$PWD"'"},
          "context_window":{"used_percentage":34,"context_window_size":1000000,
            "current_usage":{"cache_read_input_tokens":341000}},
          "cost":{"total_cost_usd":8.41,"total_duration_ms":2721000},
          "prompt_cache":{"warm":true,"ttl":"1h","hit_ratio":0.94}}' \
     | ~/.claude/statusline.sh
   ```

6. **Done.** Tell the user to restart Claude Code (or start a new session) to
   pick up the `settings.json` change. Note that `~/.claude/` is user-level
   config, so the new bar applies to every Claude Code session on the machine,
   not just the current project.
