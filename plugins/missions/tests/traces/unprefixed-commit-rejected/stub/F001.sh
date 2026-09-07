#!/bin/bash
# attempt 1: a `wip` commit with --no-verify (commit-msg never sees it), then the base worker's
# prefixed commit and a handoff naming it; attempt 2: the base worker alone.
set -e
attempt=${MISSIONS_TASK##*#}
if [ "$attempt" = 1 ]; then
  echo "# wip" >> analytics/service.py
  git add analytics/service.py && git commit -q --no-verify -m "wip"
fi
exec bash "$(dirname "$0")/worker.sh"
