#!/bin/bash
# The feature is finished -- commit on the branch, schema-valid complete handoff -- and THEN the
# driver's own --max-budget-usd ends the turn: rc 1, from a cap we set ourselves. Every check the
# driver makes of any other run passes, so this is `done`, not a rejection.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
printf '{"unit":"usd","value":0.55,"capped":true}\n' > "$MISSIONS_RUN_DIR/cost.json"
exit 1
