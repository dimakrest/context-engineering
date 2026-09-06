# Validation contract — demo

Status values: `unproven` → `claimed` → `proven`

| ID | Assertion | Proof class | Feature(s) | Status | Evidence | Proof budget |
|---|---|---|---|---|---|---|
| A001 | Omitting the window equals the whole day | structural | F001 | claimed | — | min: named test; max: 1 pinning feature |
| A002 | Tenant A never sees tenant B | structural | F001, F002 | claimed | — | min: mutation (tenancy); max: 1 pinning feature |
| A003 | The filter chip is visible on the dashboard | interface | F003 | unproven | — | min: playwright; max: 1 run |
