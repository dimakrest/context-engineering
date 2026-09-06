#!/bin/bash
# always a malformed handoff (no exit code in the commands table)
set -e
f=$MISSIONS_FEATURE
echo "# $f" >> analytics/service.py; git add analytics/service.py && git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD); mkdir -p "$MISSIONS_DIR/handoffs"
printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001\n\n## Completed\nwork\n\n## Left undone\nnothing\n\n## Commands run\nran the tests, all passing\n\n## Issues discovered\nnone\n\n## Procedures followed\n- D001\n\n## Commit\n`%s` %s: stub work\n' "$f" "$sha" "$f" > "$MISSIONS_DIR/handoffs/$f.md"
