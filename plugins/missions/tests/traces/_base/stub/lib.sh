# Shared helpers for the stubs. run.sh copies _base/stub/. into every case's stub dir before the
# case's own overlay, so a stub sources this as "$(dirname "$0")/lib.sh". The stub adapter
# resolves scripts by role, step and feature name, so this file is inert.
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

# The assertion ids the prompt lists, one per line (the `  A00n — text` rows prompts.py writes).
prompt_assertions() { sed -n 's/^  \(A[0-9]\{3\}[a-z]\{0,1\}\) .*/\1/p' "$MISSIONS_PROMPT"; }

# write_review encodes the reviewer report SCHEMA -- keep it in step with agents/mission-reviewer.md,
# in this one place. $1 = a verdict function: called with an assertion id, it prints the row's
# Verdict and Evidence cells (`satisfied | stub`); $2 = extra `## Defects` rows, one per line,
# when the review found any.
write_review() {
  local a
  printf '## Assertion verdicts\n| ID | Verdict | Evidence / breaking case |\n|---|---|---|\n'
  for a in $(prompt_assertions); do printf '| %s | %s |\n' "$a" "$("$1" "$a")"; done
  printf '\n## Design conformance\n| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |\n|---|---|---|\n| D001 | conforms | stub |\n'
  printf '\n## Impact\n| Changed symbol | Caller (file:line) | Behaviour change | Covered by | Verdict |\n|---|---|---|---|---|\n'
  printf '\n## Defects\n| Severity | file:line | What breaks, and the concrete input that breaks it |\n|---|---|---|\n'
  [ -z "${2:-}" ] || printf '%s\n' "$2"
  printf '\n## Not covered by any assertion\nnone\n'
}
