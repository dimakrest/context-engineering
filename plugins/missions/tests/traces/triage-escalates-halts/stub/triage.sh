#!/bin/bash
# A triage that cannot decide: the one issue is escalated with a why. The driver must halt on it
# and leave the issue where the human will look for it.
set -e
printf '{"resolutions":[{"issue":1,"disposition":"escalate","why":"port 5435 needs an operator: the shared database is read-only and the handoff implies a schema change"}]}\n' > "$MISSIONS_RUN_DIR/output.md"
