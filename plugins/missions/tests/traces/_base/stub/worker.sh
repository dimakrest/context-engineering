#!/bin/bash
# A well-behaved worker: one edit, one commit "F00n: ...", a schema-valid handoff, exit 0.
# It also snapshots what the driver had written at launch, for the launch-grades-nothing trace.
set -e
f=$MISSIONS_FEATURE
cp "$MISSIONS_DIR/journal.jsonl" "$MISSIONS_RUN_DIR/journal-at-launch.jsonl"
sed -n "/^### $f/,/^##/p" "$MISSIONS_DIR/features.md" | grep 'Status' > "$MISSIONS_RUN_DIR/status-at-launch.txt" || true
echo "# $f" >> analytics/service.py
git add analytics/service.py
git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD)
# the feature's own assertions, from features.md (a claim outside them is rejected by the grade)
claims=$(sed -n "/^### $f /,/^### /p" "$MISSIONS_DIR/features.md" | sed -n 's/^- \*\*Assertions:\*\* //p' | head -1 \
  | tr ',' '\n' | sed 's/^ *//; s/ *$//' | sed 's/^\(A[0-9]*\).*/- \1 — satisfied by `analytics\/service.py:1`/')
mkdir -p "$MISSIONS_DIR/handoffs"
cat > "$MISSIONS_DIR/handoffs/$f.md" <<EOF
# Handoff $f — stub work

## Status
complete

## Assertions claimed
$claims

## Completed
Appended $f to analytics/service.py.

## Left undone
nothing

## Commands run
| Command | Exit | Note |
|---|---|---|
| make test-unit | 0 | stub |

## Issues discovered
none

## Procedures followed
- D001 — followed

## Commit
\`$sha\` $f: stub work
EOF
