#!/bin/bash
# A well-behaved worker: one edit to the first path of its feature's Files line, one commit
# "F00n: ...", a schema-valid handoff, exit 0. It also snapshots what the driver had written at
# launch, for the launch-grades-nothing trace.
set -e
f=$MISSIONS_FEATURE
cp "$MISSIONS_DIR/journal.jsonl" "$MISSIONS_RUN_DIR/journal-at-launch.jsonl"
sed -n "/^### $f/,/^##/p" "$MISSIONS_DIR/features.md" | grep 'Status' > "$MISSIONS_RUN_DIR/status-at-launch.txt" || true
section=$(sed -n "/^### $f /,/^### /p" "$MISSIONS_DIR/features.md")
# the first path of the Files line (a path outside it would need a reason in the handoff)
target=$(printf '%s\n' "$section" | sed -n 's/^- \*\*Files:\*\* //p' | head -1 | sed 's/^`//; s/`.*//')
[ -n "$target" ] || target=analytics/service.py
mkdir -p "$(dirname "$target")"
echo "# $f" >> "$target"
git add "$target"
git commit -qm "$f: stub work"
sha=$(git rev-parse HEAD)
# the feature's own assertions, from features.md (a claim outside them is rejected by the grade)
claims=$(printf '%s\n' "$section" | sed -n 's/^- \*\*Assertions:\*\* //p' | head -1 \
  | tr ',' '\n' | sed 's/^ *//; s/ *$//' | sed "s|^\(A[0-9]*\).*|- \1 — satisfied by \`$target:1\`|")
mkdir -p "$MISSIONS_DIR/handoffs"
cat > "$MISSIONS_DIR/handoffs/$f.md" <<EOF
# Handoff $f — stub work

## Status
complete

## Assertions claimed
$claims

## Completed
Appended $f to $target.

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
