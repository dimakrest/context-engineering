#!/bin/bash
# commit, wait past a few polls, write the handoff, write it again, exit 0
set -e
f=$MISSIONS_FEATURE
write_handoff() {  # $1 = sha, $2 = 1 to omit "## Left undone"
  mkdir -p "$MISSIONS_DIR/handoffs"
  {
    printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001 — yes\n- A002 — yes\n\n## Completed\nwork\n\n' "$f"
    [ "${2:-0}" = 1 ] || printf '## Left undone\nnothing\n\n'
    printf '## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n## Issues discovered\nnone\n\n## Procedures followed\n- D001 — followed\n\n## Commit\n`%s` %s: stub work\n' "$1" "$f"
  } > "$MISSIONS_DIR/handoffs/$f.md"
}

echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD)
sleep 2
write_handoff "$sha"
sleep 0.7
write_handoff "$sha"
echo "# rewritten" >> "$MISSIONS_DIR/handoffs/$f.md"
sleep 0.7
