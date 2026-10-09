---
type: regex
target:
  source: file
  path: dryrun.txt
pattern: '^  - flow created: 3 PRs .*\n  - (\S+) added\n  - (\S+) added after \1\n  - \S+ added after \2$'
flags: m
---
