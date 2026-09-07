# Mission demo — state

```mission-state
phase: implementing
milestone: M1
spend_usd: unknown
resume_next: dispatch F001 (M1 feature 1 of 2)
state_cap_lines: 200
```

**Branch:** mission/demo

## Open issues — these block the next feature
- none

## Standing constraints for every agent
- Never push, merge, `--no-verify`, `--admin`
- Tests: `make test-unit` (mocked layer) — never `pytest tests/`
- DB: ports 5435/5436 are read-only
- Codebase intelligence: none

## Key facts established during planning (do not re-research)
- The aggregation lives in `analytics/service.py:1`

## M1 — planning notes
- F001 before F002: the tenancy filter builds on the window

**Last updated:** fixture
