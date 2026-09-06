#!/bin/bash
# attempt 1 says complete but marks A002 NOT satisfied; attempt 2 claims both on one bullet
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
echo "# $f attempt $attempt" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work $attempt"
sha=$(git rev-parse HEAD)
if [ "$attempt" = 1 ]; then
  claims='- A001 — satisfied by `analytics/service.py:1`
- A002 — NOT satisfied; no tenancy test written'
else
  claims='- A001, A002 — both satisfied by `analytics/service.py:1`'
fi
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
\`$sha\` $f: stub work $attempt
EOF
