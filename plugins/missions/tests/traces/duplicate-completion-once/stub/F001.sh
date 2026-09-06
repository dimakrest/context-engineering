#!/bin/bash
# commit, wait past a few polls, write the handoff, write it again, exit 0
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD)
sleep 2
write_handoff "$sha"
sleep 0.7
write_handoff "$sha"
echo "# rewritten" >> "$MISSIONS_DIR/handoffs/$f.md"
sleep 0.7
