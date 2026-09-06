#!/bin/bash
# commits, then idles: never writes the handoff (S3 F012)
set -e
f=$MISSIONS_FEATURE
cp "$MISSIONS_DIR/journal.jsonl" "$MISSIONS_RUN_DIR/journal-at-commit.jsonl"
echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
sleep 60
