#!/bin/bash
# Two checkouts on one host: <tmp>/repo (the base fixture, mission A -- run.sh initialises it) and
# <tmp>/repo2 (the same shape, mission B, initialised here against the same stub dir, <tmp>/stub,
# which run.sh fills in before the run).
set -e
tmp="$1"
here=$(cd "$(dirname "$0")" && pwd)
plugin=$(cd "$here/../../.." && pwd)
bash "$here/../_base/repo.sh" "$tmp"
bash "$here/../_base/repo.sh" "$tmp/b"
mv "$tmp/b/repo" "$tmp/repo2"
mkdir -p "$tmp/repo2/.missions/demo"
cp -R "$here/../_base/mission/." "$tmp/repo2/.missions/demo/"
bash "$plugin/bin/missions" init "$tmp/repo2/.missions/demo" --harness stub --stub-dir "$tmp/stub"
