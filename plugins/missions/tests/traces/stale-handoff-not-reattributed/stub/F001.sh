#!/bin/bash
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
. "$(dirname "$0")/lib.sh"   # write_handoff

if [ "$attempt" = 1 ]; then
  echo "# $f" >> analytics/service.py
  git add analytics/service.py && git commit -qm "$f: stub work"
  write_handoff "$(git rev-parse HEAD)" 1
  exit 0
fi
echo "boom" >&2
exit 1
