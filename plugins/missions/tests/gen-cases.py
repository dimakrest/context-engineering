#!/usr/bin/env python3
"""Generate the fixture cases for the missions hook suite into tests/cases/ (gitignored).

run.sh calls this before every run; edit the cases here, not on disk. usage: gen-cases.py [<out-dir>]"""
import json, os, pathlib, shutil, sys, textwrap
_dumps=json.dumps
json.dumps=lambda o, **k: _dumps(o, separators=(",",":"), **k)

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent / "cases"
if ROOT.exists(): shutil.rmtree(ROOT)

# ---------------------------------------------------------------- fixtures
def state_block(phase="implementing", cap=200, extra="", resume="dispatch F002 (M1 feature 2 of 3)"):
    return f"""# Mission demo — state

```mission-state
phase: {phase}
milestone: M1
spend_usd: unknown
resume_next: {resume}
state_cap_lines: {cap}
```

**Branch:** mission/demo

## Open issues — these block the next feature
- none

## Standing constraints for every agent
- Never push, merge, `--no-verify`, `--admin`
- Tests: `make test-unit` (mocked layer) — never `pytest tests/`
- DB: ports 5435/5436 are read-only

## Key facts established during planning (do not re-research)
- The aggregation lives in `analytics/service.py:40`
{extra}
**Last updated:** test
"""

def state_legacy(phase="implementation", writer="none", extra=""):
    return f"""# Mission old — state

**Phase:** {phase} — **M1g COMPLETE.** Long prose follows.
**Milestone:** M1 — halt lifted by user decision.
**Active writing agent:** {writer}
<!-- machine-parsed field above: keep it bare. -->
worker-F060 was stopped by the user 2026-08-31 (`all_agents_stopped`, journal.jsonl).
**Branch:** `mission/old`
**Spend:** ~4.0M of **5,000,000** tokens · 0 live runs

## Open issues — these block the next feature
- none

## Standing constraints for every agent
- Never push
{extra}
"""

def mission_md(dollar=None, dispatch=None, wall=None, repair=None, reserve=None, legacy_tokens=False):
    lines = ["# Mission: demo", "", "**Goal (one sentence):** demo", "", "## Constraints",
             "- Branch: `mission/demo`", "- Autonomy ceiling: advisory", "", "## Budget"]
    if dollar is not None:  lines.append(f"- Dollar cap: ${dollar} — measured")
    if dispatch is not None: lines.append(f"- Dispatch cap: {dispatch} — dispatches")
    if wall is not None:    lines.append(f"- Active wall-clock cap: {wall} h")
    if repair is not None:  lines.append(f"- Repair rounds per assertion: {repair}")
    if reserve is not None: lines.append(f"- Terminal-review reserve: {reserve}%")
    if legacy_tokens:       lines.append("- Token/spend cap: 5,000,000 — the loop halts here")
    lines.append("- Behavior-validation cap: 3 live runs per milestone")
    return "\n".join(lines) + "\n"

CONTRACT_V2 = """# Validation contract — demo

Status values: `unproven` → `claimed` → `proven`

| ID | Assertion | Proof class | Feature(s) | Status | Evidence | Proof budget |
|---|---|---|---|---|---|---|
| A001 | Omitting the window equals the whole day | structural | F001 | proven | tests/unit/test_a.py::test_a | min: named test; max: 1 pinning feature |
| A002 | Tenant A never sees tenant B | structural | F001, F002 | unproven | — | min: mutation (tenancy); max: 1 pinning feature |
| A003 | The filter chip is visible on the dashboard | interface | F003 | unproven | — | min: playwright; max: 1 run |
"""
CONTRACT_LEGACY = """# Validation contract — demo

| ID | Assertion | Proof class | Feature(s) | Status | Evidence |
|---|---|---|---|---|---|
| A001 | Omitting the window equals the whole day | structural | F001 | proven | tests/unit/test_a.py::test_a |
| A002 | Tenant A never sees tenant B | structural | F001, F002 | unproven | — |
| A003 | The filter chip is visible on the dashboard | interface | F003 | unproven | — |
"""
# Proof budget inserted BETWEEN Proof class and Feature(s): must still parse (the :57 trap)
CONTRACT_MIDCOL = CONTRACT_V2.replace("| Proof class | Feature(s) | Status | Evidence | Proof budget |", "| Proof class | Proof budget | Feature(s) | Status | Evidence |") \
    .replace("|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|") \
    .replace("| structural | F001 | proven | tests/unit/test_a.py::test_a | min: named test; max: 1 pinning feature |", "| structural | min: named test; max: 1 pinning feature | F001 | proven | tests/unit/test_a.py::test_a |") \
    .replace("| structural | F001, F002 | unproven | — | min: mutation (tenancy); max: 1 pinning feature |", "| structural | min: mutation (tenancy); max: 1 pinning feature | F001, F002 | unproven | — |") \
    .replace("| interface | F003 | unproven | — | min: playwright; max: 1 run |", "| interface | min: playwright; max: 1 run | F003 | unproven | — |")
CONTRACT_NOBUDGET_CELL = CONTRACT_V2.replace("| min: playwright; max: 1 run |", "|  |")

def features_md(files=True, n_extra=0):
    def feat(fid, asserts, fl):
        s = f"### {fid} — feature {fid}\n- **Assertions:** {asserts}\n"
        if files: s += f"- **Files:** {fl}\n"
        s += "- **Procedures:** make test-unit\n- **Depends on:** —\n- **Status:** pending\n\n"
        return s
    body = "# Features — demo\n\n## M1 — first\n\n" + feat("F001", "A001, A002", "`analytics/service.py`, `tests/unit/test_a.py`") \
         + feat("F002", "A002", "`analytics/service.py`") + feat("F003", "A003", "`ui/src/Filters.tsx`")
    for i in range(n_extra):
        body += feat(f"F{4+i:03d}", "A002", "`analytics/service.py`")
    return body

def followups_md(entries):
    out = "# Follow-ups — demo\n\n"
    for fu, title, a, cluster, disp in entries:
        out += f"## {fu} — {title}\n- **Assertion:** {a}\n- **Found by:** mission-reviewer\n- **Severity:** high\n"
        if cluster: out += f"- **Cluster:** {cluster}\n"
        out += "- **Blocking:** yes\n"
        if disp: out += f"- **Disposition:** {disp}\n"
        out += "\n"
    return out

def agent_payload(agent, prompt, tool_use_id="toolu_1", session="s1", transcript="transcript.jsonl", event="PreToolUse", response=None):
    d = {"session_id": session, "transcript_path": transcript, "hook_event_name": event, "tool_name": "Agent",
         "tool_use_id": tool_use_id, "tool_input": {"subagent_type": agent, "prompt": prompt, "description": "x"}}
    if response is not None: d["tool_response"] = response
    return d
def bash_payload(cmd): return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
def write_payload(path): return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}

WORKER_PROMPT = "Mission: demo. Feature: F002 — tenant scoping.\n\nMission state (digest):\nresume_next: dispatch F002\n\nAssertions: A002"
WORKER_PROMPT_DIGEST_FIRST = "Mission: demo. Feature: F002 — tenant scoping.\n\nDigest mentions queued F037 and F010 before anything else.\nAssertions: A002"
REVIEWER_OK = "Mission: demo. Feature: F001 — x.\nReview the patch for F001.\nPatch: .missions/demo/patches/F001.patch (base abc, head def)"

TRANSCRIPT = "\n".join(json.dumps(x) for x in [
    {"type": "cost-state", "sessionId": "s1", "totalCostUSD": 0},
    {"type": "assistant", "message": {"usage": {"input_tokens": 1}}},
    {"type": "cost-state", "sessionId": "s1", "totalCostUSD": 197.20, "modelUsage": {"claude-opus-5": {"costUSD": 162.72}}},
]) + "\n"

def lock(agent, feature, did="toolu_old", epoch=None):
    import time
    ep = epoch if epoch is not None else int(time.time())
    return f"agent={agent} feature={feature} dispatch_id={did} session=s0 ts=2026-08-31T10:00:00Z epoch={ep}\n"

# ---------------------------------------------------------------- writer
def case(script, name, expect, stdin=None, missions=None, files=None):
    d = ROOT / script / name; d.mkdir(parents=True)
    (d / "expect").write_text(textwrap.dedent(expect).strip() + "\n")
    if stdin is not None: (d / "stdin.json").write_text(json.dumps(stdin))
    for slug, tree in (missions or {}).items():
        for rel, content in tree.items():
            p = d / "missions" / slug / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
    for rel, content in (files or {}).items():
        p = d / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)

DEMO = {"state.md": state_block(), "mission.md": mission_md(), "contract.md": CONTRACT_V2, "features.md": features_md(), "followups.md": "# Follow-ups — demo\n", "journal.jsonl": ""}
def demo(**over):
    d = dict(DEMO); d.update(over); return d
OLD = {"state.md": state_legacy(), "mission.md": mission_md(legacy_tokens=True), "contract.md": CONTRACT_LEGACY, "features.md": features_md(files=False)}

# ================================================================ inertness
for hook in ["mission-serial-guard", "mission-blind-review", "mission-commit-discipline", "mission-crosscheck-seal",
             "mission-contract-first", "mission-handoff-schema", "mission-journal", "mission-release", "mission-rehydrate"]:
    for pname, payload in [("agent", agent_payload("mission-worker", WORKER_PROMPT)), ("bash", bash_payload("git push origin main")),
                           ("write", write_payload("/x/app/service.py")), ("empty", {})]:
        case("inertness", f"{hook}--no-missions--{pname}", f"script=hooks/{hook}.sh\nrc=0\nstderr_empty=1", stdin=payload)
    case("inertness", f"{hook}--phase-done", f"script=hooks/{hook}.sh\nrc=0\nstderr_empty=1",
         stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo(**{"state.md": state_block(phase="done")})})
    case("inertness", f"{hook}--phase-halted-legacy", f"script=hooks/{hook}.sh\nrc=0\nstderr_empty=1",
         stdin=bash_payload("git push"), missions={"old": {**OLD, "state.md": state_legacy(phase="halted")}})

# ================================================================ serial guard
G = "mission-serial-guard"
case(G, "writer-allowed-takes-locks-journals", """
    rc=0
    postcheck=test -f .missions/demo/.writer && grep -q 'feature=F002' .missions/demo/.writer
    postcheck=test -f .missions/demo/.lease && grep -q 'dispatch_id=toolu_1' .missions/demo/.lease
    postcheck=grep -q '"event":"dispatch"' .missions/demo/journal.jsonl && grep -q '"class":"writer"' .missions/demo/journal.jsonl
    postcheck=grep -q '"event":"session_cost"' .missions/demo/journal.jsonl && grep -q '"usd":197.2' .missions/demo/journal.jsonl
    """, stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo()}, files={"transcript.jsonl": TRANSCRIPT})
case(G, "writer-blocked-by-writer-lock", "rc=2\nstderr~=writing agent is already active -- mission-worker F001",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_2"), missions={"demo": demo(**{".writer": lock("mission-worker", "F001")})})
case(G, "stale-writer-lock-released-holder-returned", """
    rc=0
    stderr~=stale writer lock
    postcheck=grep -q 'feature=F002' .missions/demo/.writer
    postcheck=grep -q '"event":"writer_lock_cleared"' .missions/demo/journal.jsonl
    """, stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_2"),
     missions={"demo": demo(**{".writer": lock("mission-worker", "F001", did="toolu_old"), ".lease": lock("mission-worker", "F001", did="toolu_old"),
                              "journal.jsonl": json.dumps({"ts": "2026-08-31T10:30:00Z", "event": "agent_return", "agent": "mission-worker", "dispatch_id": "toolu_old"}) + "\n"})})
case(G, "stale-writer-lock-released-agent-stopped", "rc=0\nstderr~=stale writer lock",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_2"),
     missions={"demo": demo(**{".writer": lock("mission-worker", "F001", did="toolu_old").rstrip("\n") + " agent_id=ag1\n", ".lease": lock("mission-worker", "F001", did="toolu_old").rstrip("\n") + " agent_id=ag1\n",
                              "journal.jsonl": json.dumps({"ts": "2026-08-31T10:30:00Z", "event": "agent_stopped", "agent": "mission-worker", "agent_id": "ag1"}) + "\n"})})
case(G, "stale-writer-lock-released-ttl", "rc=0\nstderr~=stale writer lock",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_2"), missions={"demo": demo(**{".writer": lock("mission-worker", "F001", epoch=1000), ".lease": lock("mission-worker", "F001", epoch=1000)})})
case(G, "digest-names-other-features-first", "rc=0\npostcheck=grep -q 'feature=F002' .missions/demo/.writer",
     stdin=agent_payload("mission-worker", WORKER_PROMPT_DIGEST_FIRST), missions={"demo": demo()})
case(G, "legacy-marker-none-with-prose-below", "rc=0",
     stdin=agent_payload("mission-worker", "Mission: old. Feature: F061 — x."), missions={"old": OLD})
case(G, "legacy-marker-other-feature-blocks", "rc=2\nstderr~=legacy marker",
     stdin=agent_payload("mission-worker", "Mission: old. Feature: F061 — x."), missions={"old": {**OLD, "state.md": state_legacy(writer="mission-worker F010")}})
case(G, "legacy-marker-same-feature-allowed", "rc=0",
     stdin=agent_payload("mission-worker", "Mission: old. Feature: F010 — x."), missions={"old": {**OLD, "state.md": state_legacy(writer="mission-worker F010")}})
case(G, "legacy-unknown-phase-warns-but-active", "rc=2\nstderr~=not one of \\[|legacy marker",
     stdin=agent_payload("mission-worker", "Mission: old. Feature: F061 — x."), missions={"old": {**OLD, "state.md": state_legacy(phase="wandering", writer="mission-worker F010")}})
case(G, "researcher-never-blocked", "rc=0\npostcheck=test ! -f .missions/demo/.lease.new; grep -q '\"class\":\"static\"' .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-researcher", "Mission: demo. Question: where is X?"), missions={"demo": demo(**{".writer": lock("mission-worker", "F001"), ".lease": lock("mission-worker", "F001")})})
case(G, "explore-builtin-static", "rc=0", stdin=agent_payload("Explore", "find things"), missions={"demo": demo(**{".writer": lock("mission-worker", "F001"), ".lease": lock("mission-worker", "F001")})})
case(G, "unknown-agent-is-writer-default-deny", "rc=2\nstderr~=writing agent is already active",
     stdin=agent_payload("some-new-agent", "Mission: demo. Feature: F003 — x."), missions={"demo": demo(**{".writer": lock("mission-worker", "F001")})})
case(G, "namespaced-agent-type", "rc=0\npostcheck=grep -q 'agent=mission-worker' .missions/demo/.writer",
     stdin=agent_payload("missions:mission-worker", WORKER_PROMPT), missions={"demo": demo()})
case(G, "reviewer-blocked-by-lease-journals-wait", "rc=2\nstderr~=execution lease held\npostcheck=grep -q '\"event\":\"lease_wait\"' .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-reviewer", REVIEWER_OK, tool_use_id="toolu_9"), missions={"demo": demo(**{".lease": lock("mission-validator-scrutiny", "")})})
case(G, "reviewer-takes-lease-no-writer-lock", "rc=0\npostcheck=test -f .missions/demo/.lease && test ! -f .missions/demo/.writer",
     stdin=agent_payload("mission-reviewer", REVIEWER_OK, tool_use_id="toolu_9"), missions={"demo": demo()})
case(G, "scrutiny-blocked-by-lease", "rc=2\nstderr~=execution lease held",
     stdin=agent_payload("mission-validator-scrutiny", "Mission: demo. Run the gate."), missions={"demo": demo(**{".lease": lock("mission-reviewer", "F001")})})
case(G, "open-issue-blocks-writer", "rc=2\nstderr~=unresolved handoff issues",
     stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo(**{"state.md": state_block().replace("- none\n\n## Standing", "- F001 handoff: integration test skipped\n\n## Standing")})})
case(G, "open-issue-does-not-block-reviewer", "rc=0",
     stdin=agent_payload("mission-reviewer", REVIEWER_OK), missions={"demo": demo(**{"state.md": state_block().replace("- none\n\n## Standing", "- F001 handoff: integration test skipped\n\n## Standing")})})
big = state_block(cap=30, extra="".join(f"- fact {i}\n" for i in range(40)))
case(G, "state-cap-blocks-writer", "rc=2\nstderr~=over its cap", stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo(**{"state.md": big})})
case(G, "state-cap-ignores-reviewer", "rc=0", stdin=agent_payload("mission-reviewer", REVIEWER_OK), missions={"demo": demo(**{"state.md": big})})
case(G, "state-cap-not-applied-to-legacy", "rc=0", stdin=agent_payload("mission-worker", "Mission: old. Feature: F061 — x."),
     missions={"old": {**OLD, "state.md": state_legacy(extra="".join(f"- fact {i}\n" for i in range(300)))}})
j60 = "".join(json.dumps({"ts": "2026-08-31T10:00:00Z", "event": "dispatch", "agent": "mission-worker", "class": "writer", "feature": f"F{i:03d}"}) + "\n" for i in range(60))
case(G, "dispatch-cap-reached", "rc=2\nstderr~=dispatch cap reached -- 60 of 60", stdin=agent_payload("mission-worker", WORKER_PROMPT),
     missions={"demo": demo(**{"mission.md": mission_md(dispatch=60), "journal.jsonl": j60})})
case(G, "dispatch-cap-under", "rc=0", stdin=agent_payload("mission-worker", WORKER_PROMPT),
     missions={"demo": demo(**{"mission.md": mission_md(dispatch=61), "journal.jsonl": j60})})
case(G, "dispatch-cap-static-exempt", "rc=0", stdin=agent_payload("mission-researcher", "Mission: demo. Q"),
     missions={"demo": demo(**{"mission.md": mission_md(dispatch=60), "journal.jsonl": j60})})
case(G, "no-caps-informational-warning", "rc=0\nstderr~=informational only", stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo(**{"mission.md": mission_md()})})
jw = json.dumps({"ts": "2026-08-31T10:00:00Z", "event": "agent_return", "agent": "mission-worker", "duration_s": 5 * 3600}) + "\n"
case(G, "wall-clock-cap-reached", "rc=2\nstderr~=wall-clock cap", stdin=agent_payload("mission-worker", WORKER_PROMPT), missions={"demo": demo(**{"mission.md": mission_md(wall=4), "journal.jsonl": jw})})
fu_repairs = followups_md([("FU001", "a (from M1-review-F001)", "A002", "C01", "repair as F004"), ("FU002", "b (from M1-review-F002)", "A002", "C02", "repair as F005"), ("FU003", "c (from M1-review-F003)", "A002", "C03", "repair as F006")])
case(G, "repair-rounds-exceeded", "rc=2\nstderr~=repair-round cap", stdin=agent_payload("mission-worker", "Mission: demo. Feature: F006 — third repair."),
     missions={"demo": demo(**{"mission.md": mission_md(repair=2), "followups.md": fu_repairs})})
fu_two = followups_md([("FU001", "a (from M1-review-F001)", "A002", "C01", "repair as F004"), ("FU002", "b (from M1-review-F002)", "A002", "C02", "repair as F005")])
case(G, "repair-rounds-within", "rc=0", stdin=agent_payload("mission-worker", "Mission: demo. Feature: F005 — second repair."),
     missions={"demo": demo(**{"mission.md": mission_md(repair=2), "followups.md": fu_two})})
case(G, "dollar-cap-reached", "rc=2\nstderr~=dollar cap -- spent \\$197.20 of \\$200", stdin=agent_payload("mission-worker", WORKER_PROMPT),
     missions={"demo": demo(**{"mission.md": mission_md(dollar=200, reserve=15)})}, files={"transcript.jsonl": TRANSCRIPT})
case(G, "dollar-cap-under", "rc=0", stdin=agent_payload("mission-worker", WORKER_PROMPT),
     missions={"demo": demo(**{"mission.md": mission_md(dollar=300, reserve=15)})}, files={"transcript.jsonl": TRANSCRIPT})
case(G, "dollar-cap-reviewer-exempt-in-pr", "rc=0", stdin=agent_payload("mission-reviewer", REVIEWER_OK),
     missions={"demo": demo(**{"mission.md": mission_md(dollar=200), "state.md": state_block(phase="pr")})}, files={"transcript.jsonl": TRANSCRIPT})
case(G, "dollar-cap-unknown-spend-allows", "rc=0", stdin=agent_payload("mission-worker", WORKER_PROMPT, transcript="missing.jsonl"),
     missions={"demo": demo(**{"mission.md": mission_md(dollar=10)})})
case(G, "dollar-cap-sums-other-sessions", "rc=2\nstderr~=spent \\$247.20", stdin=agent_payload("mission-worker", WORKER_PROMPT),
     missions={"demo": demo(**{"mission.md": mission_md(dollar=240), "journal.jsonl": json.dumps({"ts": "x", "event": "session_cost", "session_id": "s0", "usd": 50}) + "\n"})}, files={"transcript.jsonl": TRANSCRIPT})

# ================================================================ release
R = "mission-release"
POST_DONE = {"agentId": "ag1", "agentType": "mission-worker", "status": "completed"}
POST_ASYNC = {"agentId": "ag1", "agentType": "mission-worker", "status": "async_launched"}
def stop_payload(agent_type, agent_id="ag1", active=False):
    return {"session_id": "s1", "hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_type": agent_type, "stop_hook_active": active}
case(R, "posttooluse-completed-matching-id-clears-both", "rc=0\npostcheck=test ! -f .missions/demo/.writer && test ! -f .missions/demo/.lease\npostcheck=grep -q writer_lock_cleared .missions/demo/journal.jsonl && grep -q lease_released .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_1", event="PostToolUse", response=POST_DONE), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1"), ".lease": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "posttooluse-other-id-keeps", "rc=0\npostcheck=test -f .missions/demo/.writer && test -f .missions/demo/.lease",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_2", event="PostToolUse", response=POST_DONE), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1"), ".lease": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "async-launch-keeps-and-annotates-agent-id", "rc=0\npostcheck=test -f .missions/demo/.writer && grep -q 'agent_id=ag1' .missions/demo/.writer && grep -q 'agent_id=ag1' .missions/demo/.lease",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_1", event="PostToolUse", response=POST_ASYNC), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1"), ".lease": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "subagentstop-by-agent-id", "rc=0\npostcheck=test ! -f .missions/demo/.writer && test ! -f .missions/demo/.lease",
     stdin=stop_payload("mission-worker", "ag1"), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1") .rstrip("\n") + " agent_id=ag1\n", ".lease": lock("mission-worker", "F002", did="toolu_1").rstrip("\n") + " agent_id=ag1\n"})})
case(R, "subagentstop-other-agent-id-keeps", "rc=0\npostcheck=test -f .missions/demo/.writer",
     stdin=stop_payload("mission-worker", "ag9"), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1").rstrip("\n") + " agent_id=ag1\n"})})
case(R, "subagentstop-by-type-when-no-agent-id-recorded", "rc=0\npostcheck=test ! -f .missions/demo/.writer",
     stdin=stop_payload("mission-worker"), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "subagentstop-other-type-keeps", "rc=0\npostcheck=test -f .missions/demo/.writer",
     stdin=stop_payload("mission-reviewer"), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "stop-hook-active-noop", "rc=0\npostcheck=test -f .missions/demo/.writer",
     stdin=stop_payload("mission-worker", active=True), missions={"demo": demo(**{".writer": lock("mission-worker", "F002", did="toolu_1")})})
case(R, "no-locks-file-untouched", "rc=0\npostcheck=test ! -s .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, event="PostToolUse", response=POST_DONE), missions={"demo": demo()})

# ================================================================ journal
J = "mission-journal"
DISPATCH_LINE = json.dumps({"ts": "2026-08-31T10:00:00Z", "event": "dispatch", "agent": "mission-worker", "feature": "F002", "dispatch_id": "toolu_1"}) + "\n"
case(J, "waited-return-uses-harness-duration", "rc=0\npostcheck=grep '\"event\":\"agent_return\"' .missions/demo/journal.jsonl | grep -q '\"duration_s\":12' \npostcheck=grep -q '\"agent_id\":\"ag1\"' .missions/demo/journal.jsonl",
     stdin={**agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_1", event="PostToolUse", response=POST_DONE), "duration_ms": 12345}, missions={"demo": demo(**{"journal.jsonl": DISPATCH_LINE})})
case(J, "waited-return-no-duration-null", "rc=0\npostcheck=grep -q '\"duration_s\":null' .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_7", event="PostToolUse", response=POST_DONE), missions={"demo": demo()})
case(J, "async-launch-journals-agent-launched", "rc=0\npostcheck=grep -q '\"event\":\"agent_launched\"' .missions/demo/journal.jsonl && ! grep -q '\"event\":\"agent_return\"' .missions/demo/journal.jsonl",
     stdin=agent_payload("mission-worker", WORKER_PROMPT, tool_use_id="toolu_1", event="PostToolUse", response=POST_ASYNC), missions={"demo": demo(**{"journal.jsonl": DISPATCH_LINE})})
LAUNCHED = DISPATCH_LINE + json.dumps({"ts": "2026-08-31T10:00:01Z", "event": "agent_launched", "agent": "mission-worker", "dispatch_id": "toolu_1", "agent_id": "ag1"}) + "\n"
case(J, "subagentstop-after-launch-joins-duration", "rc=0\npostcheck=grep '\"event\":\"agent_stopped\"' .missions/demo/journal.jsonl | grep -q '\"duration_s\":[0-9]' \npostcheck=grep '\"event\":\"agent_stopped\"' .missions/demo/journal.jsonl | grep -q '\"feature\":\"F002\"'",
     stdin=stop_payload("mission-worker", "ag1"), missions={"demo": demo(**{"journal.jsonl": LAUNCHED})})
case(J, "subagentstop-unjoined-null-duration", "rc=0\npostcheck=grep '\"event\":\"agent_stopped\"' .missions/demo/journal.jsonl | grep -q '\"duration_s\":null'",
     stdin=stop_payload("mission-researcher", "agX"), missions={"demo": demo()})
case(J, "non-mission-agent-ignored", "rc=0\npostcheck=test ! -s .missions/demo/journal.jsonl", stdin=agent_payload("Explore", "x", event="PostToolUse", response={"agentType": "Explore", "status": "completed"}), missions={"demo": demo()})
case(J, "unwritable-journal-still-rc0", "rc=0\nsetup=chmod 000 .missions/demo/journal.jsonl", stdin=agent_payload("mission-worker", WORKER_PROMPT, event="PostToolUse", response=POST_DONE), missions={"demo": demo()})

# ================================================================ blind review
B = "mission-blind-review"
case(B, "reviewer-git-log-blocked", "rc=2\nstderr~=run git itself", stdin=agent_payload("mission-reviewer", REVIEWER_OK + "\nAlso run git log -3 for context"), missions={"demo": demo()})
case(B, "reviewer-three-dot-blocked", "rc=2\nstderr~=run git itself", stdin=agent_payload("mission-reviewer", "Mission: demo. Feature: F001.\nDiff: git diff origin/main...HEAD -- a.py"), missions={"demo": demo()})
case(B, "reviewer-no-patch-blocked", "rc=2\nstderr~=names no patch file", stdin=agent_payload("mission-reviewer", "Mission: demo. Feature: F001.\nReview against A001."), missions={"demo": demo()})
case(B, "reviewer-with-patch-ok", "rc=0", stdin=agent_payload("mission-reviewer", REVIEWER_OK), missions={"demo": demo()})
case(B, "reviewer-handoff-leak-blocked", "rc=2\nstderr~=handoff content", stdin=agent_payload("mission-reviewer", REVIEWER_OK + "\nSee handoffs/F001.md"), missions={"demo": demo()})
case(B, "behavior-diff-blocked", "rc=2\nstderr~=contains a diff", stdin=agent_payload("mission-validator-behavior", "Prove A003.\n```diff\n+x\n```"), missions={"demo": demo()})
case(B, "namespaced-reviewer-still-checked", "rc=2\nstderr~=names no patch file", stdin=agent_payload("missions:mission-reviewer", "Mission: demo. Feature: F001. Review A001."), missions={"demo": demo()})

# ================================================================ commit discipline
C = "mission-commit-discipline"
case(C, "push-blocked-while-implementing", "rc=2\nstderr~=no pushing", stdin=bash_payload("git push origin mission/demo"), missions={"demo": demo()})
case(C, "push-allowed-in-pr", "rc=0", stdin=bash_payload("git push origin mission/demo"), missions={"demo": demo(**{"state.md": state_block(phase="pr")})})
case(C, "merge-blocked", "rc=2\nstderr~=no merging", stdin=bash_payload("git merge main"), missions={"demo": demo()})
case(C, "no-verify-blocked", "rc=2\nstderr~=never allowed", stdin=bash_payload("git commit --no-verify -m 'F002: x'"), missions={"demo": demo()})
case(C, "commit-without-id-blocked", "rc=2\nstderr~=must start with the feature id", stdin=bash_payload("git commit -m 'fix stuff'"), missions={"demo": demo()})
case(C, "commit-with-id-ok", "rc=0", stdin=bash_payload("git commit -m 'F002: scope by tenant'"), missions={"demo": demo()})
case(C, "legacy-implementation-phase-now-enforced", "rc=2\nstderr~=must start with the feature id", stdin=bash_payload("git commit -m 'fix stuff'"), missions={"old": OLD})
OTHER = "mkdir -p other && git -C other init -q && git -C other commit -q --allow-empty -m init"
case(C, "push-in-other-repo-via-C-allowed", "rc=0\nsetup=" + OTHER, stdin=bash_payload("git -C $TMP/other push -u origin main"), missions={"demo": demo()})
case(C, "push-in-other-repo-via-cd-allowed", "rc=0\nsetup=" + OTHER, stdin=bash_payload("cd $TMP/other && git push origin main"), missions={"demo": demo()})
case(C, "push-in-mission-repo-via-cd-still-blocked", "rc=2\nstderr~=no pushing\nsetup=git init -q . && git commit -q --allow-empty -m init", stdin=bash_payload("cd $TMP && git push origin main"), missions={"demo": demo()})
case(C, "merge-in-other-repo-allowed", "rc=0\nsetup=" + OTHER, stdin=bash_payload("git -C $TMP/other merge feature"), missions={"demo": demo()})
case(C, "heredoc-mentioning-push-ok", "rc=0", stdin=bash_payload("cat > notes.md <<'EOF'\nnever git push here\nEOF"), missions={"demo": demo()})

# ================================================================ contract first
F = "mission-contract-first"
case(F, "planning-blocks-product-code", "rc=2\nstderr~=no product code yet", stdin=write_payload("$TMP/app/service.py"), missions={"demo": demo(**{"state.md": state_block(phase="planning")})})
case(F, "planning-allows-mission-files", "rc=0", stdin=write_payload("$TMP/.missions/demo/contract.md"), missions={"demo": demo(**{"state.md": state_block(phase="planning")})})
case(F, "implementing-allows-everything", "rc=0", stdin=write_payload("$TMP/app/service.py"), missions={"demo": demo()})
case(F, "outside-repo-allowed", "rc=0", stdin=write_payload("/somewhere/else/x.py"), missions={"demo": demo(**{"state.md": state_block(phase="planning")})})

# ================================================================ handoff schema
H = "mission-handoff-schema"
HANDOFF_OK = "# Handoff F002\n\n## Status\nblocked\n\n## Assertions claimed\n- A002 — no\n\n## Completed\nx\n\n## Left undone\ny\n\n## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test | 1 | fail |\n\n## Issues discovered\nnone\n\n## Procedures followed\nz\n\n## Commit\nnone — blocked\n"
case(H, "missing-handoff-rc2", "rc=2\nstderr~=no handoff at", stdin=agent_payload("mission-worker", WORKER_PROMPT, event="PostToolUse"), missions={"demo": demo()})
case(H, "blocked-handoff-without-sha-ok", "rc=0", stdin=agent_payload("mission-worker", WORKER_PROMPT, event="PostToolUse"), missions={"demo": demo(**{"handoffs/F002.md": HANDOFF_OK})})
case(H, "missing-section-rc2", "rc=2\nstderr~=missing sections", stdin=agent_payload("mission-worker", WORKER_PROMPT, event="PostToolUse"), missions={"demo": demo(**{"handoffs/F002.md": HANDOFF_OK.replace("## Left undone\ny\n\n", "")})})
case(H, "feature-from-first-line-not-digest", "rc=0", stdin=agent_payload("mission-worker", WORKER_PROMPT_DIGEST_FIRST, event="PostToolUse"), missions={"demo": demo(**{"handoffs/F002.md": HANDOFF_OK})})

# ================================================================ rehydrate
case("mission-rehydrate", "prints-digest-when-active", "rc=0\nstdout~=MISSION ACTIVE: demo\nstdout~=resume_next: dispatch F002\nstdout~=Standing constraints", stdin={"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}, missions={"demo": demo()})
case("mission-rehydrate", "legacy-warns-still-prints", "rc=0\nstdout~=MISSION ACTIVE: old\nstdout~=phase: implementing", stdin={"hook_event_name": "SessionStart"}, missions={"old": OLD})

# ================================================================ scripts: state digest
S = "mission-state"
case(S, "block-digest-under-cap", "rc=0\nargs=.missions/demo\nstdout~=phase: implementing\nstdout~=writer: none\nstdout~=lease: free\nstdout~=Never push\npostcheck=test $(bash \"$CLAUDE_PLUGIN_ROOT/scripts/mission-state.sh\" .missions/demo | wc -c) -le 2048", missions={"demo": demo()})
case(S, "shows-locks", "rc=0\nargs=.missions/demo\nstdout~=writer: mission-worker F002\nstdout~=lease: agent=mission-worker", missions={"demo": demo(**{".writer": lock("mission-worker", "F002"), ".lease": lock("mission-worker", "F002")})})
case(S, "legacy-warns", "rc=0\nargs=.missions/old\nstderr~=legacy state header\nstdout~=phase: implementing", missions={"old": OLD})
case(S, "too-big-rc2", "rc=2\nargs=.missions/demo\nstderr~=digest cannot fit", missions={"demo": demo(**{"state.md": state_block().replace("- Never push, merge", "- " + "x" * 2100 + "\n- Never push, merge")})})

# ================================================================ scripts: spend
case("mission-spend", "last-cost-state-wins", "rc=0\nargs=transcript.jsonl\nstdout~=session_usd: 197.2\nstdout~=spend_usd: 197.20", files={"transcript.jsonl": TRANSCRIPT})
case("mission-spend", "no-transcript-unknown", "rc=0\nargs=missing.jsonl\nstdout~=spend_usd: unknown")
case("mission-spend", "sums-other-sessions", "rc=0\nargs=transcript.jsonl journal.jsonl\nstdout~=spend_usd: 227.20\nstdout~=sessions: 2",
     files={"transcript.jsonl": TRANSCRIPT, "journal.jsonl": json.dumps({"event": "session_cost", "session_id": "s0", "usd": 10}) + "\n" + json.dumps({"event": "session_cost", "session_id": "s0", "usd": 30}) + "\n" + json.dumps({"event": "session_cost", "session_id": "s1", "usd": 999}) + "\n"})

# ================================================================ scripts: patch (git fixture)
GIT_SETUP = ("git init -q . && git config user.email t@t && git config user.name t && "
             "printf 'a1\\n' > a.py && printf 'b1\\n' > b.py && git add . && git commit -qm 'c1: base' && "
             "printf 'a2\\n' > a.py && printf 'b2\\n' > b.py && git add . && git commit -qm 'F001: change a and b' -m 'SECRET BODY reasoning' && "
             "printf 'a3\\n' > a.py && git add . && git commit -qm 'F002: later' && "
             "git rev-parse HEAD~2 > .c1 && git rev-parse HEAD~1 > .c2 && git rev-parse HEAD > .c3")
case("mission-patch", "exact-range-only", "rc=0\nsetup=" + GIT_SETUP + "\nargs=.missions/demo F001 $(cat $TMP/.c1) $(cat $TMP/.c2) -- a.py\nstdout~=patches/F001.patch\npostcheck=grep -q '^+a2' .missions/demo/patches/F001.patch && ! grep -q '^+a3' .missions/demo/patches/F001.patch && ! grep -q '^+b2' .missions/demo/patches/F001.patch\npostcheck=! grep -q 'SECRET BODY' .missions/demo/patches/F001.patch\npostcheck=grep -q '^base: ' .missions/demo/patches/F001.patch", missions={"demo": demo()})
case("mission-patch", "range-spans-two-commits", "rc=0\nsetup=" + GIT_SETUP + "\nargs=.missions/demo F002 $(cat $TMP/.c1) $(cat $TMP/.c3) -- a.py\npostcheck=grep -q '^+a3' .missions/demo/patches/F002.patch", missions={"demo": demo()})
case("mission-patch", "base-not-ancestor-rc2", "rc=2\nsetup=" + GIT_SETUP + "\nargs=.missions/demo F001 $(cat $TMP/.c3) $(cat $TMP/.c1) -- a.py\nstderr~=not an ancestor", missions={"demo": demo()})
case("mission-patch", "empty-diff-rc2", "rc=2\nsetup=" + GIT_SETUP + "\nargs=.missions/demo F002 $(cat $TMP/.c2) $(cat $TMP/.c3) -- b.py\nstderr~=empty diff", missions={"demo": demo()})

# ================================================================ scripts: archive
ARCH_STATE = state_block(extra="\n## M1 CLOSED 2026-08-30\n- closed stuff A001\n\n## M1b — repair pass\n- more closed stuff\n\n## M2 — current\n- in flight\n")
case("mission-archive", "moves-m1-sections", "rc=0\nargs=.missions/demo M1\nstdout~=archived 2 section\npostcheck=grep -q 'closed stuff' .missions/demo/archive/M1.md && ! grep -q 'closed stuff' .missions/demo/state.md && grep -q 'in flight' .missions/demo/state.md && grep -q 'M1 — archived' .missions/demo/state.md", missions={"demo": demo(**{"state.md": ARCH_STATE})})
case("mission-archive", "nothing-to-move", "rc=0\nargs=.missions/demo M7\nstderr~=no '## M7'", missions={"demo": demo(**{"state.md": ARCH_STATE})})

# ================================================================ scripts: converge
FU_MANY = followups_md([(f"FU{i:03d}", f"f{i} (from M1-review-F001)", "A002", f"C{i:02d}", "repair as F009") for i in range(1, 5)])
case("mission-converge", "followups-exceed-features", "rc=2\nargs=.missions/demo\nstdout~=follow-ups exceed features", missions={"demo": demo(**{"followups.md": FU_MANY})})
case("mission-converge", "converging-pass", "rc=0\nargs=.missions/demo\nstdout~=CONVERGE PASS", missions={"demo": demo(**{"followups.md": followups_md([("FU001", "x (from M1-review-F001)", "A002", "C01", "repair as F004")])})})
case("mission-converge", "interface-introduced-none-proven", "rc=2\nargs=.missions/demo M1\nstdout~=proof class introduced, none proven", missions={"demo": demo()})
case("mission-converge", "interface-proven-ok", "rc=0\nargs=.missions/demo M1", missions={"demo": demo(**{"contract.md": CONTRACT_V2.replace("| interface | F003 | unproven |", "| interface | F003 | proven |")})})
FEAT_3MS = features_md().replace("## M1 — first\n\n### F001", "## M1 — first\n\n### F001").replace("### F002", "## M2 — second\n\n### F002").replace("### F003", "## M3 — third\n\n### F003")
FU_RISING = followups_md([("FU001", "a (from M2-review-F002)", "A002", "C01", "accept"), ("FU002", "b (from M3-review-F003)", "A003", "C02", "accept"), ("FU003", "c (from M3-review-F003)", "A003", "C02", "accept")])
case("mission-converge", "ratio-rising-two-milestones", "rc=2\nargs=.missions/demo\nstdout~=ratio rising", missions={"demo": demo(**{"features.md": FEAT_3MS, "followups.md": FU_RISING})})

# ================================================================ scripts: check
K = "check"
case(K, "v2-mission-passes", "rc=0\nargs=.missions/demo\nstdout~=CHECK PASS", missions={"demo": demo()})
case(K, "legacy-contract-note-not-error", "rc=0\nargs=.missions/old\nstdout~=no Proof budget column", missions={"old": {**OLD, "state.md": state_legacy()}})
case(K, "budget-column-mid-position-still-parses", "rc=0\nargs=.missions/demo\nstdout~=CHECK PASS", missions={"demo": demo(**{"contract.md": CONTRACT_MIDCOL})})
case(K, "empty-budget-cell-fails", "rc=1\nargs=.missions/demo\nstdout~=A003 has no proof budget", missions={"demo": demo(**{"contract.md": CONTRACT_NOBUDGET_CELL})})
case(K, "feature-count-exceeds-files", "rc=1\nargs=.missions/demo\nstdout~=feature count exceeds files touched", missions={"demo": demo(**{"features.md": features_md(n_extra=3), "contract.md": CONTRACT_V2.replace("| F001, F002 |", "| F001, F002, F004, F005, F006 |")})})
case(K, "no-files-lines-note", "rc=0\nargs=.missions/demo\nstdout~=feature/file gate not applied", missions={"demo": demo(**{"features.md": features_md(files=False)})})
case(K, "unclustered-followup-fails", "rc=1\nargs=.missions/demo\nstdout~=FU002 unclustered", missions={"demo": demo(**{"followups.md": followups_md([("FU001", "a", "A002", "C01", "accept"), ("FU002", "b", "A002", None, "accept")])})})
case(K, "cluster-split-fails", "rc=1\nargs=.missions/demo\nstdout~=cluster split across features: C01", missions={"demo": demo(**{"followups.md": followups_md([("FU001", "a", "A002", "C01", "repair as F004"), ("FU002", "b", "A002", "C01", "repair as F005")])})})
case(K, "archive-scanned-for-ids", "rc=0\nargs=.missions/demo\nstdout~=archive/M1.md: A005 — retired", missions={"demo": demo(**{"archive/M1.md": "## M1 closed\n- A005 was retired\n", "contract.md": CONTRACT_V2 + "\n## Amendments after `/missions:mission-plan`\n\n| When | What | Why |\n|---|---|---|\n| now | A005 retired | dup |\n"})})

n = sum(1 for _ in ROOT.glob("*/*/expect"))
print(f"generated {n} cases under {ROOT}")
