#!/bin/bash
# A reviewer that finds nothing: every assertion the prompt names is `satisfied`, in the format
# agents/mission-reviewer.md fixes. It also records whether the handoffs were visible to it --
# the reviewer-cannot-see-handoffs trace asserts `absent`.
set -e
. "$(dirname "$0")/lib.sh"   # write_review
if ls "$MISSIONS_DIR/handoffs" >/dev/null 2>&1; then echo present; else echo absent; fi > "$MISSIONS_RUN_DIR/handoffs-visible.txt"
satisfied() { printf 'satisfied | stub: `analytics/service.py:1`'; }
write_review satisfied > "$MISSIONS_RUN_DIR/output.md"
