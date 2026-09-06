#!/bin/bash
# attempt 1 commits a WIP change and is cut off by the deadline before any handoff; attempt 2 finishes
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
. "$(dirname "$0")/lib.sh"   # write_handoff
echo "# $f attempt $attempt" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work $attempt"
[ "$attempt" = 1 ] && sleep 30
write_handoff "$(git rev-parse HEAD)"
