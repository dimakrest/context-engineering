#!/bin/bash
set -e
f=$MISSIONS_FEATURE; attempt=${MISSIONS_TASK##*#}
. "$(dirname "$0")/lib.sh"   # write_handoff

echo "# $f attempt $attempt" >> analytics/service.py
git add analytics/service.py && git commit -qm "$f: stub work $attempt"
omit=0; [ "$attempt" = 1 ] && omit=1
write_handoff "$(git rev-parse HEAD)" "$omit"
# attempt 2: valid, but partial -- the self-check must say what the driver will do with it
[ "$attempt" = 2 ] && sed -i 's/^complete$/partial/; s/^nothing$/the A002 tenancy test/' "$MISSIONS_DIR/handoffs/$f.md"
set +e
bash "$MISSIONS_BIN" grade "$MISSIONS_DIR" "$f" --self > "$MISSIONS_RUN_DIR/selfcheck.txt" 2>&1
echo $? > "$MISSIONS_RUN_DIR/selfcheck.rc"
exit 0
