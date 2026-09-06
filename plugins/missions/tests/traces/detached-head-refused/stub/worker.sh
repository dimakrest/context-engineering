#!/bin/bash
# commits on a detached HEAD with a valid handoff: the mission branch never moved
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff
git checkout -q --detach
echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
