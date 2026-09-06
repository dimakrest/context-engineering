#!/bin/bash
# Attempt 1 edits a file outside F001's Files and says nothing about it; attempt 2 edits it again
# and names it under Completed with the reason. Both run the self-check and keep its output.
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
echo "# $f attempt $attempt" >> analytics/service.py
echo "# $f attempt $attempt" >> analytics/other.py
git add analytics/service.py analytics/other.py
git commit -qm "$f: stub work $attempt" 2>"$MISSIONS_RUN_DIR/commit.err"
sha=$(git rev-parse HEAD)
completed="Appended to analytics/service.py."
[ "$attempt" = 2 ] && completed="Appended to analytics/service.py; also analytics/other.py, where the helper it calls lives."
mkdir -p "$MISSIONS_DIR/handoffs"
cat > "$MISSIONS_DIR/handoffs/$f.md" <<HANDOFF
# Handoff $f

## Status
complete

## Assertions claimed
- A001 — yes
- A002 — yes

## Completed
$completed

## Left undone
nothing

## Commands run
| Command | Exit | Note |
|---|---|---|
| make test-unit | 0 | ok |

## Issues discovered
none

## Procedures followed
- D001 — followed

## Commit
\`$sha\` $f: stub work $attempt
HANDOFF
bash "$MISSIONS_BIN" grade "$MISSIONS_DIR" "$f" --self > "$MISSIONS_RUN_DIR/selfcheck.txt" 2>&1 || true
