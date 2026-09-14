#!/bin/bash
# Compatible entrypoint: snapshot.sh <mission> <snapshot>, or --print <mission>.
# Captures content hashes, including already-dirty files; missing evidence fails closed.
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/crosscheck.py" snapshot "$@"
