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
# `git status`. Measured ~20ms per render, against ~100ms before the rewrite.
#
# Portability: avoids mapfile (bash 4+) and hard-depending on $EPOCHSECONDS
# (bash 5+) so it still works on the bash 3.2 that macOS ships as /bin/bash.

# Must exceed statusLine.refreshInterval (the skill installs 5) or the cache
# expires exactly as the next timed render arrives and never serves once.
CACHE_TTL=15         # git status cache, seconds
MISS_WINDOW=90       # how long a prompt-cache miss stays on the bar, seconds
EXPIRY_WARN=300      # show the cache countdown under this many seconds left

# A real ESC byte, so the lines can be printed with %s. Passing payload-derived
# text through printf '%b' let a backslash in a directory name be read as an
# escape - and a `\c` truncated the line and swallowed its newline, merging
# both lines of the bar into one.
E=$'\033'

# `command -v` is a builtin, so this costs no fork.
if ! command -v jq >/dev/null 2>&1; then
    printf '%s[statusline: jq not found]%s\n' "${E}[33m" "${E}[0m"
    exit 0
fi

# One jq call, read straight into named variables. The two lists below are in
# the same order, and each name sits opposite its expression: adding a field
# means adding one line to each, next to each other. (An earlier draft read
# into a positional array, where inserting a field silently shifted every
# variable after it.)
#
# Booleans use `== true` rather than `// false`: jq treats `false` as falsy, so
# `.x // "-"` would silently rewrite a real `false` into "-". `== true` is safe
# for absent, null and false alike. Numbers that can be null (used_percentage
# and all of current_usage after /compact) get `// 0`.
#
# A brace group rather than `mapfile -t`, which bash 3.2 lacks.
{
    IFS= read -r MODEL
    IFS= read -r CURRENT_DIR
    IFS= read -r CONTEXT_PCT
    IFS= read -r TOTAL_TOKENS
    IFS= read -r MAX_TOKENS
    IFS= read -r COST
    IFS= read -r DURATION_MS
    IFS= read -r API_MS
    IFS= read -r LINES_ADD
    IFS= read -r LINES_DEL
    IFS= read -r EXCEEDS_200K
    IFS= read -r FAST_MODE
    IFS= read -r PAYLOAD_EFFORT
    IFS= read -r PR_NUM
    IFS= read -r PR_STATE
    IFS= read -r WT_NAME
    IFS= read -r PC_PRESENT
    IFS= read -r PC_OBSERVED
    IFS= read -r PC_WARM
    IFS= read -r PC_TTL
    IFS= read -r PC_HIT
    IFS= read -r PC_EXPIRES
    IFS= read -r PC_CAUSE
    IFS= read -r PC_RECACHE
    IFS= read -r PC_MISSTOK
    IFS= read -r PC_MISS_AT
} < <(jq -r '
[ (.model.display_name // "Unknown")                                  # MODEL
, (.workspace.current_dir // "")                                      # CURRENT_DIR
, ((.context_window.used_percentage // 0) | floor)                    # CONTEXT_PCT
, ( .context_window.total_input_tokens                              # TOTAL_TOKENS
  // ( (.context_window.current_usage.input_tokens // 0)
     + (.context_window.current_usage.cache_creation_input_tokens // 0)
     + (.context_window.current_usage.cache_read_input_tokens // 0) ) )
, (.context_window.context_window_size // 200000)                     # MAX_TOKENS
, (.cost.total_cost_usd // 0)                                         # COST
, (.cost.total_duration_ms // 0)                                      # DURATION_MS
, (.cost.total_api_duration_ms // 0)                                  # API_MS
, (.cost.total_lines_added // 0)                                      # LINES_ADD
, (.cost.total_lines_removed // 0)                                    # LINES_DEL
, (.exceeds_200k_tokens == true)                                      # EXCEEDS_200K
, (.fast_mode == true)                                                # FAST_MODE
, (.effort.level // "")                                               # PAYLOAD_EFFORT
, (.pr.number // "")                                                  # PR_NUM
, (.pr.review_state // "")                                            # PR_STATE
, (.worktree.name // .workspace.git_worktree // "")                   # WT_NAME
, (if .prompt_cache then "1" else "" end)                             # PC_PRESENT
, (.prompt_cache.caching_observed != false)                            # PC_OBSERVED
, (.prompt_cache.warm == true)                                        # PC_WARM
, (.prompt_cache.ttl // "")                                           # PC_TTL
, (if .prompt_cache.hit_ratio == null then ""                         # PC_HIT
  else ((.prompt_cache.hit_ratio * 100) | floor) end)
, (.prompt_cache.expires_at // 0)                                     # PC_EXPIRES
, (.prompt_cache.last_miss_cause.causes[0] // "")                     # PC_CAUSE
, (.prompt_cache.recache_tokens_if_cold // 0)                         # PC_RECACHE
, (.prompt_cache.miss_recache_tokens // 0)                            # PC_MISSTOK
, (.prompt_cache.last_miss_at // 0)                                   # PC_MISS_AT
] | map(tostring) | .[]')

# If jq failed (malformed payload, or a build that changed the schema) every
# read above came back empty. Default the numerics so the bar degrades to a
# sparse line instead of a page of "integer expression expected".
: "${MODEL:=Unknown}"
: "${MAX_TOKENS:=200000}"
for _n in CONTEXT_PCT TOTAL_TOKENS COST DURATION_MS API_MS LINES_ADD LINES_DEL \
          PC_EXPIRES PC_RECACHE PC_MISSTOK PC_MISS_AT; do
    eval "[ -n \"\${$_n}\" ] || $_n=0"
done

# $EPOCHSECONDS is bash 5.0+; fall back to a fork on older bash.
if [ -n "${EPOCHSECONDS:-}" ]; then NOW=$EPOCHSECONDS; else NOW=$(date +%s); fi

# ---------------------------------------------------------------- effort
# Claude Code ships the live value two ways, both set per session and both
# tracking a mid-session /effort change: .effort.level in the payload, and
# $CLAUDE_EFFORT in this process's environment. ($CLAUDE_CODE_EFFORT_LEVEL,
# which earlier versions of this script read, is not a variable Claude Code
# sets, so it never matched.)
#
# Deliberately no settings.json tier. It cannot be made correct: effort lives
# at modelSettings["<canonical id>"].effortLevel, the payload's id does not
# always match that key (dated, Bedrock and Vertex spellings all miss), and a
# miss falls through to a global effortLevel - which paints a level on models
# that have none and the wrong level on the rest. A build old enough to send
# neither source is one whose other new segments are missing anyway, so the
# segment simply stays hidden.
EFFORT="$PAYLOAD_EFFORT"
[ -z "$EFFORT" ] && EFFORT="${CLAUDE_EFFORT:-}"

# No default. A model that does not support reasoning effort - Haiku 4.5,
# Sonnet 4.x, Opus 4.x - has no .effort in the payload and no $CLAUDE_EFFORT,
# and printing "medium" there would invent a level that does not exist. The
# segment is omitted instead.
#
# An unrecognised level renders verbatim in grey rather than being relabelled.
EFF=""
case "$EFFORT" in
    "")     ;;
    low)    EFF="${E}[34mlow${E}[0m" ;;
    medium) EFF="${E}[33mmedium${E}[0m" ;;
    high)   EFF="${E}[35mhigh${E}[0m" ;;
    xhigh)  EFF="${E}[91mxhigh${E}[0m" ;;
    max)    EFF="${E}[31mmax${E}[0m" ;;
    *)      EFF="${E}[90m${EFFORT}${E}[0m" ;;
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

# Strip every trailing slash, not just one: "/usr/share//" would otherwise
# leave "/usr/share/" and then ##*/ would eat the whole thing.
DIR_NAME="unknown"
if [ -n "$CURRENT_DIR" ]; then
    _d=$CURRENT_DIR
    while [ "$_d" != "/" ] && [ "${_d%/}" != "$_d" ]; do _d=${_d%/}; done
    DIR_NAME=${_d##*/}
    [ -z "$DIR_NAME" ] && DIR_NAME="/"      # CURRENT_DIR was "/"
fi

SEP="${E}[90m·${E}[0m"

# ---------------------------------------------------------------- git
# The payload carries workspace.repo and worktree.branch but no plain branch or
# dirty count, so this still needs a subprocess - one, not three: a single
# `status --porcelain -b` yields the branch and every count, and its failure
# also tells us this is not a repo.
#
# Cached because Claude Code can fire several renders inside a 300ms debounce
# window, and again on every refreshInterval tick. The cache holds counts
# rather than rendered colour, so nothing read back from disk is ever fed to
# the terminal as an escape sequence.
GIT_BRANCH=""; GIT_STATUS=""
if [ -n "$CURRENT_DIR" ]; then
    # Our own directory, mode 700, under the invoking user's id. /tmp is
    # world-writable: a flat predictable filename there can be pre-created by
    # another user, who then chooses what this script reads back.
    CACHE_DIR="${TMPDIR:-/tmp}/claude-statusline-$EUID"
    [ -d "$CACHE_DIR" ] || mkdir -m 700 "$CACHE_DIR" 2>/dev/null

    # -O: skip the cache entirely unless the directory is ours.
    CACHE_FILE=""
    if [ -d "$CACHE_DIR" ] && [ -O "$CACHE_DIR" ]; then
        # Collapsing punctuation to _ makes "my-app" and "my_app" collide, so
        # the full path is stored in the file and checked on read. A collision
        # then costs a recompute instead of showing another project's branch.
        CACHE_KEY="${CURRENT_DIR//[^a-zA-Z0-9]/_}"
        if [ "${#CACHE_KEY}" -gt 80 ]; then CACHE_KEY=${CACHE_KEY:${#CACHE_KEY}-80}; fi
        CACHE_FILE="${CACHE_DIR}/git_${CACHE_KEY}"
    fi

    CACHED=0
    if [ -n "$CACHE_FILE" ] && [ -f "$CACHE_FILE" ]; then
        {
            IFS= read -r _TS; IFS= read -r _PATH; IFS= read -r _BRANCH
            IFS= read -r _STAGED; IFS= read -r _MODIFIED; IFS= read -r _CONFLICT
        } < "$CACHE_FILE"
        case "$_TS" in
            ''|*[!0-9]*) : ;;                       # truncated or clobbered
            *) if [ $(( NOW - _TS )) -lt "$CACHE_TTL" ] \
                  && [ "$_PATH" = "$CURRENT_DIR" ] \
                  && [ -n "$_CONFLICT" ]; then      # last line present => not torn
                   GIT_BRANCH=$_BRANCH
                   STAGED=$_STAGED; MODIFIED=$_MODIFIED; CONFLICT=$_CONFLICT
                   CACHED=1
               fi ;;
        esac
    fi

    if [ "$CACHED" -eq 0 ]; then
        # Counted while streaming, with no array: a tree with thousands of dirty
        # files would otherwise spend hundreds of ms per render appending to one.
        # -uno for the same reason - untracked entries match no case below, and
        # skipping them also saves git the walk of every untracked directory.
        STAGED=0; MODIFIED=0; CONFLICT=0
        {
            # "## main...origin/main" | "## HEAD (no branch)"; absent if not a repo
            if IFS= read -r L; then
                B=${L#\#\# }
                B=${B%%...*}
                case "$B" in
                    *'(no branch)'*)       B="" ;;                      # detached HEAD
                    'No commits yet on '*) B=${B#No commits yet on } ;; # fresh repo
                esac
                GIT_BRANCH=$B
            fi
            while IFS= read -r L; do
                # U in either column is an unresolved merge (DD AU UD UA DU AA UU)
                # and must win: a half-done merge is the one thing worth shouting
                # about. T is a typechange, e.g. a file replaced by a symlink.
                case "$L" in
                    U?*|?U*) CONFLICT=$(( CONFLICT + 1 )); continue ;;
                esac
                case "${L:0:1}" in [MADRCT]) STAGED=$(( STAGED + 1 )) ;; esac
                case "${L:1:1}" in [MDT])    MODIFIED=$(( MODIFIED + 1 )) ;; esac
            done
        } < <(git -C "$CURRENT_DIR" status --porcelain -b -uno 2>/dev/null)

        # Written even when this is not a repo, so a non-repo directory costs
        # one git fork per CACHE_TTL rather than one per render. Via a temp file
        # and mv so a concurrent reader never sees a half-written cache, and
        # with 2>/dev/null AHEAD of the redirection - bash applies redirections
        # left to right, so a trailing one does not suppress its own failure.
        if [ -n "$CACHE_FILE" ]; then
            _TMP="${CACHE_FILE}.$$"
            if printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$NOW" "$CURRENT_DIR" \
                   "$GIT_BRANCH" "$STAGED" "$MODIFIED" "$CONFLICT" \
                   2>/dev/null > "$_TMP"; then
                mv -f "$_TMP" "$CACHE_FILE" 2>/dev/null || rm -f "$_TMP" 2>/dev/null
            fi
        fi
    fi

    # Rendered here, not cached, so the cache never holds escape sequences.
    [ "$CONFLICT" -gt 0 ] && GIT_STATUS="${GIT_STATUS} ${E}[31m!${CONFLICT}${E}[0m"
    [ "$STAGED"   -gt 0 ] && GIT_STATUS="${GIT_STATUS} ${E}[32m+${STAGED}${E}[0m"
    [ "$MODIFIED" -gt 0 ] && GIT_STATUS="${GIT_STATUS} ${E}[33m~${MODIFIED}${E}[0m"
fi

# ---------------------------------------------------------------- line 1
L1="[${E}[1m${MODEL_SHORT}${E}[0m]"
[ -n "$EFF" ] && L1="${L1} [${EFF}]"
[ "$FAST_MODE" = "true" ] && L1="${L1} ${E}[93m⚡${E}[0m"
L1="${L1} ${DIR_NAME}"
[ -n "$GIT_BRANCH" ] && L1="${L1} ${E}[90m⑂${E}[0m ${GIT_BRANCH}${GIT_STATUS}"
[ -n "$WT_NAME" ]    && L1="${L1} ${SEP} ${E}[36m⧉ ${WT_NAME}${E}[0m"
if [ -n "$PR_NUM" ]; then
    case "$PR_STATE" in
        approved)          PRC="${E}[32m✓${E}[0m" ;;
        changes_requested) PRC="${E}[31m✗${E}[0m" ;;
        draft)             PRC="${E}[90m◌${E}[0m" ;;
        *)                 PRC="${E}[33m◔${E}[0m" ;;
    esac
    L1="${L1} ${SEP} ${E}[94mPR #${PR_NUM}${E}[0m ${PRC}"
fi

# ---------------------------------------------------------------- line 2
BAR_LENGTH=10
FILLED=$(( CONTEXT_PCT * BAR_LENGTH / 100 )); EMPTY=$(( BAR_LENGTH - FILLED ))
if   [ "$CONTEXT_PCT" -lt 70 ]; then BAR_COLOR="${E}[32m"
elif [ "$CONTEXT_PCT" -lt 90 ]; then BAR_COLOR="${E}[33m"
else                                 BAR_COLOR="${E}[31m"; fi
BAR=$BAR_COLOR
_i=0; while [ "$_i" -lt "$FILLED" ]; do BAR="${BAR}▓"; _i=$(( _i + 1 )); done
BAR="${BAR}${E}[0m"
_i=0; while [ "$_i" -lt "$EMPTY" ];  do BAR="${BAR}░"; _i=$(( _i + 1 )); done

L2="${BAR} ${CONTEXT_PCT}% ($(tok "$TOTAL_TOKENS")/$(tok "$MAX_TOKENS"))"
# Claude Code computes this from the last assistant message's usage, not the
# running total: a live "that request was billed in the premium tier" flag.
[ "$EXCEEDS_200K" = "true" ] && L2="${L2} ${E}[33m⚠200k${E}[0m"

L2="${L2} ${SEP} \$$(printf '%.2f' "$COST")"
L2="${L2} ${SEP} $(dur "$(( DURATION_MS / 1000 ))")"
# Before the first request there is no API time to report, so don't print "(0s api)".
if [ "$API_MS" -gt 0 ]; then
    L2="${L2} ${E}[90m($(dur "$(( API_MS / 1000 ))") api)${E}[0m"
fi
if [ "$LINES_ADD" -gt 0 ] || [ "$LINES_DEL" -gt 0 ]; then
    L2="${L2} ${SEP} ${E}[32m+${LINES_ADD}${E}[0m ${E}[31m−${LINES_DEL}${E}[0m"
fi

# Prompt-cache segment: grey and ignorable when healthy, loud only when it costs
# money. Skipped entirely when caching_observed is false - the provider does not
# report cache usage (some gateways, Bedrock/Vertex), so `warm` is false forever
# and a permanent amber "cold" would be a standing false alarm.
if [ -n "$PC_PRESENT" ] && [ "$PC_OBSERVED" = "true" ]; then
    MISS_AGE=$(( NOW - PC_MISS_AT ))
    LEFT=$(( PC_EXPIRES - NOW ))
    # Gated on last_miss_at being recent, not on a cause being present:
    # last_miss_cause is null when the miss was not diagnosed, and gating on it
    # reported a cache as healthy in the very render that re-paid for it. The
    # cause is detail when it exists. An absent last_miss_at is 0, so MISS_AGE
    # is ~now and far outside the window; no separate test for it. The lower
    # bound rejects a timestamp in the future, which is what a switch to
    # milliseconds would look like, and would otherwise pin the bar red forever.
    if   [ "$PC_MISS_AT" -gt 0 ] && [ "$MISS_AGE" -ge 0 ] && [ "$MISS_AGE" -lt "$MISS_WINDOW" ]; then
        CSEG="${E}[31m✗ cache ${PC_CAUSE:-miss}${E}[0m"
        [ "$PC_MISSTOK" -gt 0 ] && CSEG="${CSEG} ${E}[31m$(tok "$PC_MISSTOK")${E}[0m"
    elif [ "$PC_WARM" != "true" ]; then
        CSEG="${E}[33m○ cache cold${E}[0m"
        [ "$PC_RECACHE" -gt 0 ] && CSEG="${CSEG} ${E}[33m$(tok "$PC_RECACHE")${E}[0m"
    elif [ "$LEFT" -gt 0 ] && [ "$LEFT" -lt "$EXPIRY_WARN" ]; then
        CSEG="${E}[33m⏳ cache ${PC_TTL} $(dur "$LEFT")${E}[0m"
    else
        # An absent hit_ratio means unmeasured, not 0%: omit it rather than
        # assert a number the payload did not give us.
        CSEG="${E}[90m✓ cache ${PC_TTL}${PC_HIT:+ ${PC_HIT}%}${E}[0m"
    fi
    L2="${L2} ${SEP} ${CSEG}"
fi

printf '%s\n' "$L1"
printf '%s\n' "$L2"
