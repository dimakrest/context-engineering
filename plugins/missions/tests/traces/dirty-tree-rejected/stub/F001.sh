#!/bin/bash
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f attempt $attempt" >> analytics/service.py
if [ "$attempt" = 1 ]; then
  echo "x" > analytics/leftover.py
  git add analytics/service.py && git commit -qm "$f: stub work $attempt"
else
  git add -A && git commit -qm "$f: stub work $attempt"
fi
write_handoff "$(git rev-parse HEAD)"
