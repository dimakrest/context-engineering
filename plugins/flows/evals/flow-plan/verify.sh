#!/usr/bin/env bash
# Re-runs the check the graders read from dryrun.txt, on the flow file a kept eval run wrote:
#   claude plugin eval <plugin> --case flow-plan-three-items --keep-temp ...; verify.sh <the run's working directory>
# A throwaway state directory, so the check reads no other run. Exit 0 when the dry run accepts the file.
set -euo pipefail
dir=${1:?usage: verify.sh <eval run directory holding flow.json>}
here=$(cd "$(dirname "$0")" && pwd)
state=$(mktemp -d)
trap 'rm -rf "$state"' EXIT
ROUTER_STATE=$state python3 "$here/../../router/router.py" flow apply "$dir/flow.json" --dry-run
