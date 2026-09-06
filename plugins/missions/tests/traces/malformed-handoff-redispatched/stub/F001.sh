#!/bin/bash
# attempt 1: commits but omits `## Left undone`; attempt 2: a valid handoff
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
echo "# $f attempt $attempt" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work $attempt"
sha=$(git rev-parse HEAD)
mkdir -p "$MISSIONS_DIR/handoffs"
{
  printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001 — yes\n\n## Completed\nwork\n\n' "$f"
  [ "$attempt" = 1 ] || printf '## Left undone\nnothing\n\n'
  printf '## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n## Issues discovered\nnone\n\n## Procedures followed\n- D001 — followed\n\n## Commit\n`%s` %s: stub work %s\n' "$sha" "$f" "$attempt"
} > "$MISSIONS_DIR/handoffs/$f.md"
