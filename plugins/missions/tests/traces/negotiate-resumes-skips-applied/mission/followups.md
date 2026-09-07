# Follow-ups — demo

## FU001 — cross-tenant rows in the aggregate (from M1-review-F001)
- **Assertion:** A002
- **Found by:** mission-reviewer (review-F001), `analytics/service.py:3` — no tenant filter
- **Severity:** high
- **Cluster:** C01 — missing tenant predicate
- **Blocking:** yes
- **Disposition:** repair as F004 — a defect, not an ambiguity
