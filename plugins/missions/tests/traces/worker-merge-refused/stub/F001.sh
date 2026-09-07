#!/bin/bash
# Creates a side branch from HEAD with one commit, tries to merge it (--no-ff, so a merge commit
# would be created), records the hook's refusal, puts the tree back, then does the base worker's
# job. The side commit is made with --no-verify: the pre-commit hook would refuse a commit off
# the mission branch, and that is not what this trace is about.
set -e
f=$MISSIONS_FEATURE
git switch -qc side
echo "# side" >> tests/unit/test_a.py
git add tests/unit/test_a.py && git commit -q --no-verify -m "side: work"
git switch -q "$MISSIONS_BRANCH"
rc=0
git merge --no-ff -m "$f: merge side" side 2>"$MISSIONS_RUN_DIR/merge.err" || rc=$?
echo "$rc" > "$MISSIONS_RUN_DIR/merge-rc.txt"
git merge --abort 2>/dev/null || true
git branch -qD side
exec bash "$(dirname "$0")/worker.sh"
