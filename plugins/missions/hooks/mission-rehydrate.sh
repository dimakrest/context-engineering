#!/bin/bash
# SessionStart hook (matcher: startup|resume|compact). Cheap re-entry.
#
# Ten compactions in the last mission, zero /mission-resume invocations, and
# after four of them the entire rehydration read was `sed -n '1,80p' state.md`
# of a 1,178-line file -- which never reached the standing constraints. The
# durability guarantee was fictional. This hook prints the mission digest as
# session context whenever a mission is active, so the first thing a rehydrated
# orchestrator sees is the fenced state, the locks, the open issues, the standing
# constraints and `resume_next`. It cannot create a turn; it makes the next one
# cheap. Always exits 0.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/mission-lib.sh" 2>/dev/null || exit 0

mission=$(mission_active_dir) || exit 0
root="${CLAUDE_PLUGIN_ROOT:-$(dirname "${BASH_SOURCE[0]}")/..}"

digest=$(bash "$root/scripts/mission-state.sh" "$mission" 2>&1) || true
printf 'MISSION ACTIVE: %s\n\n%s\n\nNext: run /missions:mission-resume if git and state disagree; otherwise act on resume_next. Do not re-read the mission files wholesale -- use the digest and id-scoped reads.\n' \
  "$(basename "$mission")" "$digest"
exit 0
