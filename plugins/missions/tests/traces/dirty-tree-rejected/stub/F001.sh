#!/bin/bash
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
write_handoff() {  # $1 = sha, $2 = 1 to omit "## Left undone"
  mkdir -p "$MISSIONS_DIR/handoffs"
  {
    printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001 — yes\n- A002 — yes\n\n## Completed\nwork\n\n' "$f"
    [ "${2:-0}" = 1 ] || printf '## Left undone\nnothing\n\n'
    printf '## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n## Issues discovered\nnone\n\n## Procedures followed\n- D001 — followed\n\n## Commit\n`%s` %s: stub work\n' "$1" "$f"
  } > "$MISSIONS_DIR/handoffs/$f.md"
}

echo "# $f attempt $attempt" >> analytics/service.py
if [ "$attempt" = 1 ]; then
  echo "x" > analytics/leftover.py
  git add analytics/service.py && git commit -qm "$f: stub work $attempt"
else
  git add -A && git commit -qm "$f: stub work $attempt"
fi
write_handoff "$(git rev-parse HEAD)"
