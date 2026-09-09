#!/bin/bash
# The cap ended the turn with the work unfinished: a WIP commit and a handoff that says so. The
# same cap ends the next attempt in the same place, so this must stop, not re-dispatch.
set -e
f=$MISSIONS_FEATURE
echo "# $f wip" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
mkdir -p "$MISSIONS_DIR/handoffs"
cat > "$MISSIONS_DIR/handoffs/$f.md" <<EOF
# Handoff $f — cut off

## Status
partial

## Assertions claimed
- A001 — satisfied by \`analytics/service.py:1\`
- A002 — NOT satisfied; ran out of budget

## Completed
Appended $f to analytics/service.py.

## Left undone
- A002: the tenant scoping test is not written

## Commands run
| Command | Exit | Note |
|---|---|---|
| make test-unit | 0 | partial |

## Issues discovered
none

## Procedures followed
- D001 — followed

## Commit
\`$(git rev-parse HEAD)\` $f: stub work
EOF
printf '{"unit":"usd","value":0.55,"capped":true}\n' > "$MISSIONS_RUN_DIR/cost.json"
exit 1
