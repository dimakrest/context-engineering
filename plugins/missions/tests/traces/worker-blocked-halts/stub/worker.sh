#!/bin/bash
# no commit; the handoff says blocked and why
set -e
f=$MISSIONS_FEATURE
mkdir -p "$MISSIONS_DIR/handoffs"
cat > "$MISSIONS_DIR/handoffs/$f.md" <<EOF
# Handoff $f — blocked

## Status
blocked

## Assertions claimed
- A001 — NOT satisfied; blocked
- A002 — NOT satisfied; blocked

## Completed
nothing

## Left undone
- the whole feature: A002 needs a tenant column on events, and migrations are out of scope for this mission

## Commands run
| Command | Exit | Note |
|---|---|---|
| make test-unit | 0 | baseline only |

## Issues discovered
none

## Procedures followed
- D001 — n/a

## Commit
none
EOF
