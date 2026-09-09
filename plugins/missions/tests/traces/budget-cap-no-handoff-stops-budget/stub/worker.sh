#!/bin/bash
# The cap ended the turn before anything landed: no commit, no handoff. That is a cap, not a
# crash -- retrying a crash is reasonable, retrying a cap is not.
printf '{"unit":"usd","value":0.55,"capped":true}\n' > "$MISSIONS_RUN_DIR/cost.json"
echo "budget exhausted" >&2
exit 1
