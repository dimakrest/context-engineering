#!/bin/bash
# Negotiate: when F001's reviewer left A002 not satisfied (the verdict summary says so), one
# blocking finding in cluster C01 and one repair for the cluster; otherwise nothing to add.
set -e
if grep -qE '^  A002 \[structural\] — reviewer F001: not satisfied' "$MISSIONS_PROMPT"; then
  cat > "$MISSIONS_RUN_DIR/output.md" <<'JSON'
{"findings":[{"title":"cross-tenant rows in the aggregate","assertion":"A002","found_by":"mission-reviewer (review-F001)","where":"`analytics/service.py:3` — no tenant filter","severity":"high","cluster":"C01","cluster_label":"missing tenant predicate","blocking":true,"disposition":"repair","why":"a defect, not an ambiguity"}],
 "repairs":[{"cluster":"C01","title":"tenancy filter","assertions":["A002"],"files":["analytics/service.py"],"procedures":"make test-unit","out_of_scope":"the summary query"}],
 "contract_wrong":false,"reason":"one defect"}
JSON
else
  printf '{"findings":[],"repairs":[],"contract_wrong":false,"reason":"all proven"}\n' > "$MISSIONS_RUN_DIR/output.md"
fi
