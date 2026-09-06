#!/bin/bash
# a WIP commit, then the harness's own quota message and a non-zero exit
set -e
f=$MISSIONS_FEATURE
echo "# $f (WIP)" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: scaffold (WIP)"
echo "Error: You've hit your usage limit · resets 3am (UTC)" >&2
exit 1
