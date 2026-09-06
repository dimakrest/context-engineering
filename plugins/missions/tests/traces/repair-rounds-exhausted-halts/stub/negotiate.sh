#!/bin/bash
# A negotiate step that proposes the same repair of A002 every round.
set -e
cat > "$MISSIONS_RUN_DIR/output.md" <<'JSON'
{"findings":[{"title":"cross-tenant rows in the aggregate","assertion":"A002","found_by":"mission-reviewer (review-F001)","where":"`analytics/service.py:3`","severity":"high","cluster":"C01","cluster_label":"missing tenant predicate","blocking":true,"disposition":"repair","why":"a defect"}],
 "repairs":[{"cluster":"C01","title":"tenancy filter","assertions":["A002"],"files":["analytics/service.py"],"procedures":"make test-unit","out_of_scope":""}],
 "contract_wrong":false,"reason":"one defect"}
JSON
