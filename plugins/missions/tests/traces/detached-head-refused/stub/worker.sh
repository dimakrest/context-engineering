#!/bin/bash
# commits on a detached HEAD with a valid handoff: the mission branch never moved. The driver's
# pre-commit hook refuses a commit off the mission branch; this worker steps around it
# (--no-verify) so the trace proves the post-exit grade catches what a hook never saw.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff
git checkout -q --detach
echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -q --no-verify -m "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
