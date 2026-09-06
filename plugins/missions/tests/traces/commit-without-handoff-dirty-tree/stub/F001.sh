#!/bin/bash
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f attempt $attempt" >> analytics/service.py
if [ "$attempt" = 1 ]; then
  git add analytics/service.py && git commit -qm "$f: stub work"
  echo "half done" > analytics/wip.py
  exit 0
fi
git add -A && git commit -qm "$f: stub work $attempt"
write_handoff "$(git rev-parse HEAD)"
