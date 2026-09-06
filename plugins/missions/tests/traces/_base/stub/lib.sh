# Shared helpers for the stub workers. run.sh copies _base/stub/. into every case's stub dir
# before the case's own overlay, so a stub sources this as "$(dirname "$0")/lib.sh". The stub
# adapter resolves scripts by exact name (<feature>.sh, else <role>.sh), so this file is inert.
#
# write_handoff encodes the handoff SCHEMA -- keep it in step with
# templates/MISSIONS_TEMPLATES.md and hooks/mission-handoff-schema.sh, in this one place.
# Uses $f (the feature id) from the caller. Completed names the paths the commit touched, as a
# worker is told to: a path outside the feature's Files that the handoff does not name is rejected.
write_handoff() {  # $1 = sha, $2 = 1 to omit "## Left undone"
  mkdir -p "$MISSIONS_DIR/handoffs"
  local touched
  touched=$(git diff-tree --no-commit-id --name-only -r "$1" 2>/dev/null | tr '\n' ' ')
  {
    printf '# Handoff %s\n\n## Status\ncomplete\n\n## Assertions claimed\n- A001 — yes\n- A002 — yes\n\n## Completed\nwork in %s\n\n' "$f" "${touched:-the tree}"
    [ "${2:-0}" = 1 ] || printf '## Left undone\nnothing\n\n'
    printf '## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n## Issues discovered\nnone\n\n## Procedures followed\n- D001 — followed\n\n## Commit\n`%s` %s: stub work\n' "$1" "$f"
  } > "$MISSIONS_DIR/handoffs/$f.md"
}
