# Mission demo — state

```mission-state
phase: validating
milestone: M1
spend_usd: unknown
resume_next: validate M1 round 1: scrutiny, a blind review per feature, negotiate
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

**Last updated:** fixture
