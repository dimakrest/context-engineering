#!/bin/bash

# Status Line Script for Claude Code
# Displays: Model, Effort, Directory, Git Status, Context Usage, Cost, Duration

# Cache file for git status (5-second TTL)
CACHE_FILE="/tmp/claude_statusline_git_cache_$$"
CACHE_TTL=5

# Read JSON from stdin
JSON=$(cat)

# Extract fields using jq
MODEL=$(echo "$JSON" | jq -r '.model.display_name // "Unknown"')
CURRENT_DIR=$(echo "$JSON" | jq -r '.workspace.current_dir // ""')
CONTEXT_PCT=$(echo "$JSON" | jq -r '.context_window.used_percentage // 0')
INPUT_TOKENS=$(echo "$JSON" | jq -r '.context_window.current_usage.input_tokens // 0')
CACHE_CREATION=$(echo "$JSON" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
CACHE_READ=$(echo "$JSON" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
MAX_TOKENS=$(echo "$JSON" | jq -r '.context_window.context_window_size // 200000')
COST=$(echo "$JSON" | jq -r '.cost.total_cost_usd // 0')
DURATION_MS=$(echo "$JSON" | jq -r '.cost.total_duration_ms // 0')

# Effort level: env var > project-local > project > user settings > default "medium"
EFFORT=""
if [ -n "$CLAUDE_CODE_EFFORT_LEVEL" ]; then
    EFFORT="$CLAUDE_CODE_EFFORT_LEVEL"
elif [ -n "$CURRENT_DIR" ] && [ -f "$CURRENT_DIR/.claude/settings.local.json" ]; then
    EFFORT=$(jq -r '.effortLevel // empty' "$CURRENT_DIR/.claude/settings.local.json" 2>/dev/null)
fi
if [ -z "$EFFORT" ] && [ -n "$CURRENT_DIR" ] && [ -f "$CURRENT_DIR/.claude/settings.json" ]; then
    EFFORT=$(jq -r '.effortLevel // empty' "$CURRENT_DIR/.claude/settings.json" 2>/dev/null)
fi
if [ -z "$EFFORT" ] && [ -f "$HOME/.claude/settings.json" ]; then
    EFFORT=$(jq -r '.effortLevel // empty' "$HOME/.claude/settings.json" 2>/dev/null)
fi
EFFORT="${EFFORT:-medium}"

case "$EFFORT" in
    low)    EFFORT_INDICATOR="\033[34mlow\033[0m" ;;
    minor)  EFFORT_INDICATOR="\033[36mminor\033[0m" ;;
    high)   EFFORT_INDICATOR="\033[35mhigh\033[0m" ;;
    max)    EFFORT_INDICATOR="\033[31mmax\033[0m" ;;
    *)      EFFORT_INDICATOR="\033[33mmedium\033[0m" ;;
esac

# Get basename of current directory
if [ -n "$CURRENT_DIR" ]; then
    DIR_NAME=$(basename "$CURRENT_DIR")
else
    DIR_NAME="unknown"
fi

# Calculate total tokens used (input + cache creation + cache read)
TOTAL_TOKENS=$((INPUT_TOKENS + CACHE_CREATION + CACHE_READ))

# Format tokens in k format
TOKENS_USED_K=$((TOTAL_TOKENS / 1000))
MAX_TOKENS_K=$((MAX_TOKENS / 1000))

# Format cost
COST_FORMATTED=$(printf "%.2f" "$COST")

# Format duration (convert ms to minutes and seconds)
DURATION_S=$((DURATION_MS / 1000))
DURATION_M=$((DURATION_S / 60))
DURATION_SEC=$((DURATION_S % 60))

# Git status (cached)
GIT_BRANCH=""
GIT_STATUS=""

if [ -n "$CURRENT_DIR" ] && [ -d "$CURRENT_DIR/.git" ]; then
    # Check cache
    if [ -f "$CACHE_FILE" ]; then
        CACHE_AGE=$(($(date +%s) - $(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)))
        if [ "$CACHE_AGE" -lt "$CACHE_TTL" ]; then
            # Use cached data
            GIT_INFO=$(cat "$CACHE_FILE")
            GIT_BRANCH=$(echo "$GIT_INFO" | head -1)
            GIT_STATUS=$(echo "$GIT_INFO" | tail -1)
        fi
    fi

    # Update cache if needed
    if [ -z "$GIT_BRANCH" ]; then
        cd "$CURRENT_DIR" 2>/dev/null || true
        GIT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")

        if [ -n "$GIT_BRANCH" ]; then
            # Get git status
            STAGED=$(git diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
            MODIFIED=$(git diff --numstat 2>/dev/null | wc -l | tr -d ' ')

            GIT_STATUS=""
            if [ "$STAGED" -gt 0 ]; then
                GIT_STATUS="${GIT_STATUS} \033[32m+${STAGED}\033[0m"
            fi
            if [ "$MODIFIED" -gt 0 ]; then
                GIT_STATUS="${GIT_STATUS} \033[33m~${MODIFIED}\033[0m"
            fi

            # Save to cache
            echo "$GIT_BRANCH" > "$CACHE_FILE"
            echo "$GIT_STATUS" >> "$CACHE_FILE"
        fi
    fi
fi

# Build Line 1
LINE1="[\033[1m${MODEL}\033[0m] [${EFFORT_INDICATOR}] ${DIR_NAME}"
if [ -n "$GIT_BRANCH" ]; then
    LINE1="${LINE1} | ${GIT_BRANCH}${GIT_STATUS}"
fi

# Build progress bar (10 blocks)
BAR_LENGTH=10
FILLED=$((CONTEXT_PCT * BAR_LENGTH / 100))
EMPTY=$((BAR_LENGTH - FILLED))

# Color based on percentage
if [ "$CONTEXT_PCT" -lt 70 ]; then
    BAR_COLOR="\033[32m"  # Green
elif [ "$CONTEXT_PCT" -lt 90 ]; then
    BAR_COLOR="\033[33m"  # Yellow
else
    BAR_COLOR="\033[31m"  # Red
fi

PROGRESS_BAR="${BAR_COLOR}"
for ((i=0; i<FILLED; i++)); do
    PROGRESS_BAR="${PROGRESS_BAR}▓"
done
PROGRESS_BAR="${PROGRESS_BAR}\033[0m"
for ((i=0; i<EMPTY; i++)); do
    PROGRESS_BAR="${PROGRESS_BAR}░"
done

# Build Line 2
LINE2="${PROGRESS_BAR} ${CONTEXT_PCT}% (${TOKENS_USED_K}k/${MAX_TOKENS_K}k) | \$${COST_FORMATTED} | ${DURATION_M}m ${DURATION_SEC}s"

# Output both lines
echo -e "$LINE1"
echo -e "$LINE2"
