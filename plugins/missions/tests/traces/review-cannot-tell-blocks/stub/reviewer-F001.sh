#!/bin/bash
# F001's reviewer can settle A001 from the diff but not A002: the tenancy predicate is built in a
# helper this patch does not touch, so the diff shows nothing either way. `cannot tell` is the
# word agents/mission-reviewer.md fixes for that, and it settles nothing.
set -e
. "$(dirname "$0")/lib.sh"   # write_review
if ls "$MISSIONS_DIR/handoffs" >/dev/null 2>&1; then echo present; else echo absent; fi > "$MISSIONS_RUN_DIR/handoffs-visible.txt"
verdict() {
  if [ "$1" = A002 ]; then printf 'cannot tell | the tenant predicate is built in a helper this patch does not touch'
  else printf 'satisfied | stub: `analytics/service.py:1`'; fi
}
write_review verdict > "$MISSIONS_RUN_DIR/output.md"
