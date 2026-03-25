---
name: setup-statusline
description: Install a two-line status bar for Claude Code that shows model name, effort level, project directory, git branch with staged/modified counts, context window usage with progress bar, session cost, and duration. Use this skill when the user wants to set up a status line, customize their Claude Code status bar, see token usage or cost in the terminal, add git info to their prompt, or mentions "statusline", "status bar", or "status line setup".
disable-model-invocation: true
user_invocable: true
allowed-tools: Bash, Read, Edit, Write
---

# Setup Status Line

Install a two-line status bar that keeps you informed at a glance while working in Claude Code.

**Line 1** shows your session context — model, effort level, project directory, and git branch with change counts:
```
[Opus] [high] my-project | main +2 ~3
```

**Line 2** shows resource usage — context window progress bar (color-coded green/yellow/red), token counts, session cost, and elapsed time:
```
▓▓▓░░░░░░░ 30% (150k/1000k) | $1.23 | 5m 30s
```

## Process

1. **Check prerequisites.** Verify `jq` is installed by running `which jq`. If missing, tell the user to install it (`brew install jq` on macOS, `sudo apt install jq` on Linux) and stop.

2. **Handle existing statusline.** Check if `~/.claude/statusline.sh` already exists. If it does, let the user know you'll replace it with the new version.

3. **Install the script.** Copy the bundled script and make it executable:
   ```bash
   cp "${CLAUDE_SKILL_DIR}/statusline.sh" ~/.claude/statusline.sh
   chmod +x ~/.claude/statusline.sh
   ```

4. **Configure settings.** Read `~/.claude/settings.json`, then use the Edit tool to add or update the `statusLine` key while preserving all other settings:
   ```json
   "statusLine": {
     "type": "command",
     "command": "~/.claude/statusline.sh",
     "padding": 2
   }
   ```
   If `~/.claude/settings.json` doesn't exist, create it with just this config wrapped in `{}`.

5. **Verify.** Confirm the script exists, is executable, and settings.json contains the `statusLine` entry.

6. **Done.** Tell the user to restart Claude Code (or start a new session) to see the status line.
