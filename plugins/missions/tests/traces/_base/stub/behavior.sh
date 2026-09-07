#!/bin/bash
# A behavior validator that proves every assertion the prompt names -- the format
# agents/mission-validator-behavior.md fixes.
set -e
. "$(dirname "$0")/lib.sh"   # write_behavior
proven() { printf 'proven | stub run 1: the chip is visible on the dashboard'; }
write_behavior proven > "$MISSIONS_RUN_DIR/output.md"
