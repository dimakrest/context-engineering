#!/bin/bash
# Mission A's worker starts a second driver on mission B (another checkout, same host) and holds
# the host lease until B is provably waiting on it, then finishes normally. B's worker is this
# same script (one stub dir for both), so it spawns nothing: only A's checkout matches.
set -e
f=$MISSIONS_FEATURE
. "$(dirname "$0")/lib.sh"   # write_handoff
case "$MISSIONS_DIR" in
  */repo/.missions/demo)
    b="$(cd "$MISSIONS_DIR/../../.." && pwd)/repo2/.missions/demo"
    setsid bash "$MISSIONS_BIN" run "$b" --limit 1 --until validate >"$MISSIONS_RUN_DIR/b.log" 2>&1 &
    for _ in $(seq 1 300); do
      grep -q '"event":"lease_wait"' "$b/journal.jsonl" 2>/dev/null && break
      sleep 0.1
    done
    ;;
esac
echo "# $f" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work"
write_handoff "$(git rev-parse HEAD)"
