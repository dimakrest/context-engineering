# Planning access and amendment scenario review

Reviewed 2026-09-13 against the shared runtime policy, planning/design/resume/amendment
entrypoints, researcher body and state/journal templates. An independent read-only reviewer
walked the instruction-level scenarios below. Its findings about pre-gate documentation
research, CLI availability and completing partial planning artifacts were corrected.

These are expected outcomes derived from instructions, **not live MCP integration tests or
proof of model compliance**. The executable regressions cover the local inventory snippet,
waiver metadata in repeated digest reads, byte limits, and packaging. No driver API or native
hook enforcement was added for these session-level gates.

| Scenario | Required result |
|---|---|
| Both MCPs succeed against the target repository | Planning/design may proceed. |
| Either/both return successful empty results from usable target indexes | Count each as verified; no fallback waiver needed. |
| Graphify unavailable, Repowise works | Block dependent work for Graphify. |
| Repowise unavailable, Graphify works | Block dependent work for Repowise. |
| Both unavailable | Report both blockers; one restoration or waiver alone cannot clear both. |
| Missing tools or disabled registration | Missing callable lookup is a blocker; registration is not access. |
| CLI/index files available but no MCP | Still block; inventory reports local indexes only. |
| Working MCP without local CLI/index | MCP is verified; do not invoke unavailable CLI commands. |
| Authentication/connection failure or timeout | Report provider, attempted lookup/target, available redacted evidence and affected stage; stop. |
| Wrong/missing/unusable repository index | Block even if the server is connected; do not treat the error as an empty result. |
| Human refuses, stays silent, times out or says only “continue” | No waiver; unresolved blocker remains. |
| Human explicitly waives Graphify only; Repowise works | Proceed within that waiver's fallback and stage scope. |
| Human explicitly waives Repowise only; Graphify works | Same, for Repowise only. |
| One provider waived; other also unavailable | Other provider still blocks. |
| Both providers explicitly waived | Proceed within approved fallback/scope; report both limitations. |
| Waiver limited to planning or a narrower fallback | Do not broaden it to design or other fallback tools. |
| Planning/design resumes with a valid mission waiver | Recover full decision beyond journal tail; repeat probes, honor approval without asking again. |
| Another mission's waiver or unsupported summary | Does not authorize fallback. |
| Waived access restored | Update verified capabilities and resolve relevant blocker, retain waiver history. |
| Unwaived access restored | Verify a fresh target lookup, clear only that blocker and resume its stage. |
| Failure before state exists | Conversation blocker, no artifact creation merely for bookkeeping; later approval persists at normal creation. |
| Failure with existing state | Keep planning phase, named open issue and actionable `resume_next`; append evidence without losing journal history. |
| Parent succeeds, child fails without waiver | Child stops and reports; dependent orchestrator authoring stops. |
| Parent succeeds, child fails with applicable waiver | Child reports failure and uses only permitted fallback; handover identifies waiver. |
| Digest carries either/both provider waivers | Preserve providers, fallback, scope, lifetime, timestamp and full-decision reference on repeated reads. |
| Digest exceeds 2048 bytes, including Unicode metadata | Fail visibly, never emit a successful truncated digest. |
| Codex amendment changes contract or otherwise requires crosscheck | Complete read-only scope map, then refuse before any artifact/state/journal writes. |
| Codex amendment needs no crosscheck | Proceed through existing replacement, residue, coherence and recording steps. |
| Supported runtime amendment changes contract | Verify crosscheck support before writes; mandatory post-amendment audit still must pass. |
| Re-plan wins or scope mapping is incomplete | No early amendment record; stop for re-plan or continue read-only mapping. |

Run the executable regressions with `python3 plugins/missions/tests/driver-selftest.py
PlanningAccessTests PackagingTests`, and the existing amendment/coherence cases with
`bash plugins/missions/tests/run.sh 'check/*'`.
