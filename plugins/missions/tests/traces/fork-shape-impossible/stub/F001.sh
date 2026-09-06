#!/bin/bash
# does the real work, but first leaves a background child holding the driver's stdout -- the S3 shape
sleep 60 &
echo $! > "$MISSIONS_RUN_DIR/child.pid"
exec bash "$(dirname "$MISSIONS_STUB_SCRIPT")/worker.sh"
