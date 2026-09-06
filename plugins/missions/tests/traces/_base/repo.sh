#!/bin/bash
# Builds the fixture checkout at <tmp>/repo on branch mission/demo, with a bare origin whose main
# is the base commit -- the shape a real mission has after /missions:mission-plan.
set -e
tmp="$1"
git init -q --bare -b main "$tmp/origin.git"
mkdir -p "$tmp/repo" && cd "$tmp/repo"
git init -q -b main .
git config user.email driver@test
git config user.name driver
printf '.missions/\n' > .gitignore
mkdir -p analytics tests/unit ui/src
printf 'def aggregate(rows):\n    return sum(rows)\n' > analytics/service.py
printf 'def test_a():\n    assert True\n' > tests/unit/test_a.py
printf 'export const Filters = () => null;\n' > ui/src/Filters.tsx
git add -A
git commit -qm 'base: fixture'
git remote add origin "$tmp/origin.git"
git push -q origin main
git checkout -q -b mission/demo
