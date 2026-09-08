#!/bin/bash
# Every attempt here is a genuine rejection: a handoff with no "## Left undone" section.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff
echo "# $f ${MISSIONS_TASK##*#}" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)" 1     # 1 = omit "## Left undone"
