#!/bin/bash
# Compatible positional entrypoint: audit.sh <transcript> <mission> <snapshot>.
# Completion now requires the separately captured process/package evidence.
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/crosscheck.py" audit "$@"
