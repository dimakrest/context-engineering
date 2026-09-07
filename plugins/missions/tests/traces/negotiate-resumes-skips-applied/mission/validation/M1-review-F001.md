<!-- review-F001#1 · mission-reviewer · stub · 2026-09-01T00:00:10Z -->

## Assertion verdicts
| ID | Verdict | Evidence / breaking case |
|---|---|---|
| A001 | satisfied | stub: `analytics/service.py:1` |
| A002 | not satisfied | tenant A with rows of tenant B: `analytics/service.py:3` has no tenant filter |

## Design conformance
| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |
|---|---|---|
| D001 | conforms | stub |

## Impact
| Changed symbol | Caller (file:line) | Behaviour change | Covered by | Verdict |
|---|---|---|---|---|

## Defects
| Severity | file:line | What breaks, and the concrete input that breaks it |
|---|---|---|
| high | analytics/service.py:3 | no tenant filter; input: tenant A, rows of B |

## Not covered by any assertion
none
