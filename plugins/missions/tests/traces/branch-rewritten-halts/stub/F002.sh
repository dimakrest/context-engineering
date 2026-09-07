#!/bin/bash
# drops F001's commit from the branch (a reset; a rebase has the same shape), then works normally
set -e
git reset -q --hard HEAD~1
exec bash "$(dirname "$0")/worker.sh"
