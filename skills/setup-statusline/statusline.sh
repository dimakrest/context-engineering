#!/bin/bash

# Status line for Claude Code.
#
# Line 1: model, effort, fast-mode, directory, git, worktree, PR
# Line 2: context bar, pricing tier, cost, duration, diff size, prompt-cache health
#
# This can run on a timer (statusLine.refreshInterval) as well as on Claude
# Code's own events, so it is written to avoid forks. Every payload field comes
# from ONE jq call, and the rest is bash builtins: parameter expansion rather
# than basename/sed/cut/cksum, integer math rather than awk, $EPOCHSECONDS
# rather than date where available. The only other subprocess is a single
# `git status`. Measured ~20ms per render, against ~100ms for a
# one-jq-call-per-field script.
#
# Portability: avoids mapfile (bash 4+) and hard-depending on $EPOCHSECONDS
# (bash 5+) so it still works on the bash 3.2 that macOS ships as /bin/bash.

CACHE_TTL=5          # git status cache, seconds
MISS_WINDOW=90       # how long a prompt-cache miss stays on the bar, seconds
EXPIRY_WARN=300      # show the cache countdown under this many seconds left

JSON=$(cat)

# One jq call. Order here must match the F[] assignments below.
#
# Booleans use `== true` rather than `// false`: jq treats `false` as falsy, so
# `.x // "-"` would silently rewrite a real `false` into "-". `== true` is safe
# for absent, null and false alike. Numbers that can be null (used_percentage
# and all of current_usage after /compact) get `// 0`.
#
# Read with a while loop rather than `mapfile -t`, which bash 3.2 lacks.
F=()
while IFS= read -r _line; do F[${#F[@]}]=$_line; done < <(printf '%s' "$JSON" | jq -r '
[ (.model.display_name // "Unknown")                                  # 0
, (.model.id // "")                                                   # 1
, (.workspace.current_dir // "")                                      # 2
, ((.context_window.used_percentage // 0) | floor)                    # 3
, ( (.context_window.current_usage.input_tokens // 0)
  + (.context_window.current_usage.cache_creation_input_tokens // 0)
  + (.context_window.current_usage.cache_read_input_tokens // 0) )    # 4
, (.context_window.context_window_size // 200000)                     # 5
, (.cost.total_cost_usd // 0)                                         # 6
, (.cost.total_duration_ms // 0)                                      # 7
, (.cost.total_api_duration_ms // 0)                                  # 8
, (.cost.total_lines_added // 0)                                      # 9
, (.cost.total_lines_removed // 0)                                    # 10
, (.exceeds_200k_tokens == true)                                      # 11
, (.fast_mode == true)                                                # 12
, (.effort.level // "")                                               # 13
, (.pr.number // "")                                                  # 14
, (.pr.review_state // "")                                            # 15
, (.worktree.name // .workspace.git_worktree // "")                   # 16
, (if .prompt_cache then "1" else "" end)                             # 17
, (.prompt_cache.warm == true)                                        # 18
, (.prompt_cache.ttl // "")                                           # 19
, (((.prompt_cache.hit_ratio // 0) * 100) | floor)                    # 20
, (.prompt_cache.expires_at // 0)                                     # 21
, (.prompt_cache.last_miss_cause.causes[0] // "")                     # 22
, (.prompt_cache.recache_tokens_if_cold // 0)                         # 23
, (.prompt_cache.miss_recache_tokens // 0)                            # 24
, (.prompt_cache.last_miss_at // 0)                                   # 25
] | map(tostring) | .[]')

MODEL=${F[0]};         MODEL_ID=${F[1]};       CURRENT_DIR=${F[2]}
CONTEXT_PCT=${F[3]};   TOTAL_TOKENS=${F[4]};   MAX_TOKENS=${F[5]}
COST=${F[6]};          DURATION_MS=${F[7]};    API_MS=${F[8]}
LINES_ADD=${F[9]};     LINES_DEL=${F[10]}
EXCEEDS_200K=${F[11]}; FAST_MODE=${F[12]};     PAYLOAD_EFFORT=${F[13]}
PR_NUM=${F[14]};       PR_STATE=${F[15]};      WT_NAME=${F[16]}
PC_PRESENT=${F[17]};   PC_WARM=${F[18]};       PC_TTL=${F[19]}
PC_HIT=${F[20]};       PC_EXPIRES=${F[21]};    PC_CAUSE=${F[22]}
PC_RECACHE=${F[23]};   PC_MISSTOK=${F[24]};    PC_MISS_AT=${F[25]}

# $EPOCHSECONDS is bash 5.0+; fall back to a fork on older bash.
if [ -n "${EPOCHSECONDS:-}" ]; then NOW=$EPOCHSECONDS; else NOW=$(date +%s); fi

# ---------------------------------------------------------------- effort
# Claude Code ships the live value two ways: .effort.level in the payload and
# $CLAUDE_EFFORT in this process's environment. Either tracks a mid-session
# /effort change; the settings files do not, so they are only a fallback for
# builds that omit both. Effort is stored per model under
# modelSettings["<id>"].effortLevel, and the payload's id may carry a context
# suffix ("claude-opus-5[1m]") that the settings key does not.
#
# (CLAUDE_CODE_EFFORT_LEVEL, which earlier versions of this script checked, is
# not a variable Claude Code sets, so it never matched.)
EFFORT="$PAYLOAD_EFFORT"
[ -z "$EFFORT" ] && EFFORT="${CLAUDE_EFFORT:-}"
if [ -z "$EFFORT" ]; then
    MODEL_KEY="${MODEL_ID%%[*}"
    for SETTINGS_FILE in \
        "$CURRENT_DIR/.claude/settings.local.json" \
        "$CURRENT_DIR/.claude/settings.json" \
        "$HOME/.claude/settings.json"; do
        [ -f "$SETTINGS_FILE" ] || continue
        EFFORT=$(MODEL_KEY="$MODEL_KEY" jq -r \
            '(.modelSettings[env.MODEL_KEY].effortLevel // .effortLevel // empty)' \
            "$SETTINGS_FILE" 2>/dev/null)
        [ -n "$EFFORT" ] && break
    done
fi

# No default. A model that does not support reasoning effort - Haiku 4.5,
# Sonnet 4.x, Opus 4.x - has no .effort in the payload and no $CLAUDE_EFFORT,
# and printing "medium" there would invent a level that does not exist. The
# segment is omitted instead.
#
# An unrecognised level renders verbatim in grey rather than being relabelled.
EFF=""
case "$EFFORT" in
    "")     ;;
    low)    EFF="\033[34mlow\033[0m" ;;
    medium) EFF="\033[33mmedium\033[0m" ;;
    high)   EFF="\033[35mhigh\033[0m" ;;
    xhigh)  EFF="\033[91mxhigh\033[0m" ;;
    max)    EFF="\033[31mmax\033[0m" ;;
    *)      EFF="\033[90m${EFFORT}\033[0m" ;;
esac

# ---------------------------------------------------------------- helpers
tok() {  # 62k, 1.0M - integer math, no awk
    local n=$1
    if   [ "$n" -ge 1000000 ]; then printf '%d.%dM' $(( n / 1000000 )) $(( (n % 1000000) / 100000 ))
    elif [ "$n" -ge 1000 ];    then printf '%dk' $(( n / 1000 ))
    else                            printf '%d' "$n"; fi
}
dur() {  # 6d4h / 2h31m / 45m21s / 21s - sessions can run for days
    local s=$1
    if   [ "$s" -ge 86400 ]; then printf '%dd%dh' $(( s / 86400 )) $(( (s % 86400) / 3600 ))
    elif [ "$s" -ge 3600 ];  then printf '%dh%dm' $(( s / 3600 ))  $(( (s % 3600) / 60 ))
    elif [ "$s" -ge 60 ];    then printf '%dm%ds' $(( s / 60 ))    $(( s % 60 ))
    else                          printf '%ds' "$s"; fi
}

# "Opus 5 (1M context)" -> "Opus 5 1M", buying back width for the new segments.
# Regex held in a variable: bash 3.2 requires it unquoted on the right of =~.
MODEL_SHORT=$MODEL
_model_re='^(.*) \((.*) context\)$'
[[ $MODEL =~ $_model_re ]] && MODEL_SHORT="${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"

DIR_NAME="unknown"
if [ -n "$CURRENT_DIR" ]; then DIR_NAME=${CURRENT_DIR%/}; DIR_NAME=${DIR_NAME##*/}; fi

SEP="\033[90m·\033[0m"

# ---------------------------------------------------------------- git
# The payload carries workspace.repo and worktree.branch but no plain branch or
# dirty count, so this still needs a subprocess - one, not three: a single
# `status --porcelain -b` yields the branch and both counts, and its failure
# also tells us this is not a repo. (Testing for a `.git` directory, as earlier
# versions did, is wrong inside worktrees and submodules, where .git is a file,
# and in any subdirectory of a repo.)
#
# Cached because Claude Code can fire several renders inside a 300ms debounce
# window. The timestamp lives on line 1 of the cache file so reading it costs
# no stat - and the file is keyed by directory, not by $$, which never hit and
# leaked one /tmp file per render.
GIT_BRANCH=""; GIT_STATUS=""
if [ -n "$CURRENT_DIR" ]; then
    # Keep the tail of the path, but only trim when it is actually too long:
    # `${K: -80}` on a shorter string yields the EMPTY string rather than
    # clamping, which would give every project one shared cache file - and so
    # another project's branch on the bar.
    CACHE_KEY="${CURRENT_DIR//[^a-zA-Z0-9]/_}"
    if [ "${#CACHE_KEY}" -gt 80 ]; then CACHE_KEY=${CACHE_KEY:${#CACHE_KEY}-80}; fi
    CACHE_FILE="${TMPDIR:-/tmp}/claude_statusline_git_${CACHE_KEY}"

    CACHED=0
    if [ -f "$CACHE_FILE" ]; then
        C=()
        while IFS= read -r _line; do C[${#C[@]}]=$_line; done < "$CACHE_FILE"
        case "${C[0]:-}" in
            ''|*[!0-9]*) : ;;
            *) if [ $(( NOW - ${C[0]} )) -lt "$CACHE_TTL" ]; then
                   GIT_BRANCH=${C[1]:-}; GIT_STATUS=${C[2]:-}; CACHED=1
               fi ;;
        esac
    fi

    if [ "$CACHED" -eq 0 ]; then
        GS=()
        while IFS= read -r _line; do GS[${#GS[@]}]=$_line; done \
            < <(git -C "$CURRENT_DIR" status --porcelain -b 2>/dev/null)
        if [ "${#GS[@]}" -gt 0 ]; then
            B=${GS[0]#\#\# }           # "main...origin/main" | "HEAD (no branch)"
            B=${B%%...*}
            case "$B" in
                *'(no branch)'*)       B="" ;;                      # detached HEAD
                'No commits yet on '*) B=${B#No commits yet on } ;; # fresh repo
            esac
            GIT_BRANCH=$B

            STAGED=0; MODIFIED=0
            _i=1
            while [ "$_i" -lt "${#GS[@]}" ]; do
                L=${GS[$_i]}
                case "${L:0:1}" in [MADRC]) STAGED=$(( STAGED + 1 )) ;; esac
                case "${L:1:1}" in [MD])    MODIFIED=$(( MODIFIED + 1 )) ;; esac
                _i=$(( _i + 1 ))
            done
            [ "$STAGED" -gt 0 ]   && GIT_STATUS="${GIT_STATUS} \033[32m+${STAGED}\033[0m"
            [ "$MODIFIED" -gt 0 ] && GIT_STATUS="${GIT_STATUS} \033[33m~${MODIFIED}\033[0m"
        fi
        # Written even when this is not a repo, so a non-repo directory costs
        # one git fork per CACHE_TTL rather than one per render.
        printf '%s\n%s\n%s\n' "$NOW" "$GIT_BRANCH" "$GIT_STATUS" > "$CACHE_FILE" 2>/dev/null
    fi
fi

# ---------------------------------------------------------------- line 1
L1="[\033[1m${MODEL_SHORT}\033[0m]"
[ -n "$EFF" ] && L1="${L1} [${EFF}]"
[ "$FAST_MODE" = "true" ] && L1="${L1} \033[93m⚡\033[0m"
L1="${L1} ${DIR_NAME}"
[ -n "$GIT_BRANCH" ] && L1="${L1} \033[90m⑂\033[0m ${GIT_BRANCH}${GIT_STATUS}"
[ -n "$WT_NAME" ]    && L1="${L1} ${SEP} \033[36m⧉ ${WT_NAME}\033[0m"
if [ -n "$PR_NUM" ]; then
    case "$PR_STATE" in
        approved)          PRC="\033[32m✓\033[0m" ;;
        changes_requested) PRC="\033[31m✗\033[0m" ;;
        draft)             PRC="\033[90m◌\033[0m" ;;
        *)                 PRC="\033[33m◔\033[0m" ;;
    esac
    L1="${L1} ${SEP} \033[94mPR #${PR_NUM}\033[0m ${PRC}"
fi

# ---------------------------------------------------------------- line 2
BAR_LENGTH=10
FILLED=$(( CONTEXT_PCT * BAR_LENGTH / 100 )); EMPTY=$(( BAR_LENGTH - FILLED ))
if   [ "$CONTEXT_PCT" -lt 70 ]; then BAR_COLOR="\033[32m"
elif [ "$CONTEXT_PCT" -lt 90 ]; then BAR_COLOR="\033[33m"
else                                 BAR_COLOR="\033[31m"; fi
BAR=$BAR_COLOR
_i=0; while [ "$_i" -lt "$FILLED" ]; do BAR="${BAR}▓"; _i=$(( _i + 1 )); done
BAR="${BAR}\033[0m"
_i=0; while [ "$_i" -lt "$EMPTY" ];  do BAR="${BAR}░"; _i=$(( _i + 1 )); done

L2="${BAR} ${CONTEXT_PCT}% ($(tok "$TOTAL_TOKENS")/$(tok "$MAX_TOKENS"))"
# Claude Code computes this from the last assistant message's usage, not the
# running total: a live "that request was billed in the premium tier" flag.
[ "$EXCEEDS_200K" = "true" ] && L2="${L2} \033[33m⚠200k\033[0m"

L2="${L2} ${SEP} \$$(printf '%.2f' "$COST")"
L2="${L2} ${SEP} $(dur "$(( DURATION_MS / 1000 ))")"
# Before the first request there is no API time to report, so don't print "(0s api)".
if [ "$API_MS" -gt 0 ]; then
    L2="${L2} \033[90m($(dur "$(( API_MS / 1000 ))") api)\033[0m"
fi
if [ "$LINES_ADD" -gt 0 ] || [ "$LINES_DEL" -gt 0 ]; then
    L2="${L2} ${SEP} \033[32m+${LINES_ADD}\033[0m \033[31m−${LINES_DEL}\033[0m"
fi

# Prompt-cache segment: grey and ignorable when healthy, loud only when it costs
# money. last_miss_cause persists for the rest of the session, so the miss state
# is gated on last_miss_at being recent - otherwise one early miss would pin the
# bar red for the whole run.
if [ -n "$PC_PRESENT" ]; then
    LEFT=$(( PC_EXPIRES - NOW ))
    if   [ -n "$PC_CAUSE" ] && [ "$PC_MISS_AT" -gt 0 ] && [ $(( NOW - PC_MISS_AT )) -lt "$MISS_WINDOW" ]; then
        CSEG="\033[31m✗ cache ${PC_CAUSE}\033[0m"
        [ "$PC_MISSTOK" -gt 0 ] && CSEG="${CSEG} \033[31m$(tok "$PC_MISSTOK")\033[0m"
    elif [ "$PC_WARM" != "true" ]; then
        CSEG="\033[33m○ cache cold\033[0m"
        [ "$PC_RECACHE" -gt 0 ] && CSEG="${CSEG} \033[33m$(tok "$PC_RECACHE")\033[0m"
    elif [ "$PC_EXPIRES" -gt 0 ] && [ "$LEFT" -gt 0 ] && [ "$LEFT" -lt "$EXPIRY_WARN" ]; then
        CSEG="\033[33m⏳ cache ${PC_TTL} $(dur "$LEFT")\033[0m"
    else
        CSEG="\033[90m✓ cache ${PC_TTL} ${PC_HIT}%\033[0m"
    fi
    L2="${L2} ${SEP} ${CSEG}"
fi

printf '%b\n' "$L1"
printf '%b\n' "$L2"
