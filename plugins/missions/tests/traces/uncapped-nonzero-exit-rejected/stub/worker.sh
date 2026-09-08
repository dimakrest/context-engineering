#!/bin/bash
# Finishes the feature properly -- commit on the branch, schema-valid complete handoff -- and then
# exits non-zero for a reason NOBODY set: no cap was passed, so the exit is unexplained. That is
# still a malformed handoff, and this case is what keeps the #21 exemption honest: the exemption
# is for the driver's OWN cap, not for every non-zero exit after a good-looking handoff.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
exit 1
