#!/bin/bash
# The judgment role with nothing to add: negotiate finds every assertion proven; triage defers
# every open issue the prompt lists (`- [i] text`) into a follow-up. MISSIONS_STEP says which
# step is running; a case that needs another answer overlays negotiate.sh or triage.sh (the stub
# adapter resolves <step>.sh before <role>.sh).
set -e
out="$MISSIONS_RUN_DIR/output.md"
case "$MISSIONS_STEP" in
  negotiate)
    printf '{"findings":[],"repairs":[],"contract_wrong":false,"reason":"all proven"}\n' > "$out" ;;
  triage)
    {
      printf '{"resolutions":['
      sep=""
      while IFS= read -r line; do
        i=${line#- \[}; i=${i%%\]*}
        text=${line#- \[*\] }
        text=${text//\\/\\\\}; text=${text//\"/\\\"}
        printf '%s{"issue":%s,"disposition":"defer","why":"deferred by the stub","followup":{"title":"%s","assertion":null,"severity":"low","cluster":"C09","cluster_label":"triage","blocking":false},"repair":null}' "$sep" "$i" "$text"
        sep=","
      done < <(grep -E '^- \[[0-9]+\] ' "$MISSIONS_PROMPT")
      printf ']}\n'
    } > "$out" ;;
  *) echo "judgment.sh: unknown step '$MISSIONS_STEP'" >&2; exit 1 ;;
esac
