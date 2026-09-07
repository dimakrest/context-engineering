#!/bin/bash
# F001's reviewer: on the first review A002 is not satisfied, with the concrete breaking input;
# on the repair round every assertion is satisfied. F002 and the repair use the base reviewer.
set -e
. "$(dirname "$0")/lib.sh"   # write_review
if ls "$MISSIONS_DIR/handoffs" >/dev/null 2>&1; then echo present; else echo absent; fi > "$MISSIONS_RUN_DIR/handoffs-visible.txt"
first=0; [ "$MISSIONS_TASK" = "review-F001#1" ] && first=1
verdict() {
  if [ "$1" = A002 ] && [ "$first" = 1 ]; then printf 'not satisfied | tenant A with rows of tenant B: `analytics/service.py:3` has no tenant filter'
  else printf 'satisfied | stub: `analytics/service.py:1`'; fi
}
defects=""; [ "$first" = 1 ] && defects='| high | analytics/service.py:3 | no tenant filter; input: tenant A, rows of B |'
write_review verdict "$defects" > "$MISSIONS_RUN_DIR/output.md"
