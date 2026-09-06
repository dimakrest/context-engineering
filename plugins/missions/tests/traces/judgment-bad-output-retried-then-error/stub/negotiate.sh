#!/bin/bash
# A negotiate step that never answers in the shape asked for.
set -e
printf 'not json, and no object anywhere in this reply\n' > "$MISSIONS_RUN_DIR/output.md"
