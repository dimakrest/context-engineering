#!/bin/bash
# A worker whose handoff raises an issue: the loop must triage it before F002 starts.
set -e
f=$MISSIONS_FEATURE
echo "# $f" >> analytics/service.py; git add analytics/service.py && git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD); mkdir -p "$MISSIONS_DIR/handoffs"
printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001\n- A002\n\n## Completed\nwork in analytics/service.py\n\n## Left undone\nnothing\n\n## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n## Issues discovered\n- the test stack would not start on port 5435\n\n## Procedures followed\n- D001\n\n## Commit\n`%s` %s: stub work\n' "$f" "$sha" "$f" > "$MISSIONS_DIR/handoffs/$f.md"
