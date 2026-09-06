#!/bin/bash
# A worker that pushes before it works. The fixture's origin is a local path, so no credential
# is involved: what refuses the push is the driver's pre-push hook. The run then goes on normally.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff
set +e
git push origin HEAD:refs/heads/mission/demo 2>"$MISSIONS_RUN_DIR/push.err"
echo $? > "$MISSIONS_RUN_DIR/push.rc"
set -e
echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
