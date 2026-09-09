#!/usr/bin/env python3
"""Unit checks for the driver that need no harness: the mission-file parsers and writers against
the trace base fixture, the claude/codex envelope parsers on captured shapes, and the command
builders. Run by tests/traces/run.sh; standalone: python3 tests/driver-selftest.py"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
sys.path.insert(0, str(PLUGIN / "driver"))
os.environ.setdefault("MISSIONS_PLUGIN_ROOT", str(PLUGIN))

import re  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from missions import cli, files, grade as grading, journal, judgment, loop, prep, prompts, steps, validate, verdicts, watchdog  # noqa: E402
from missions.adapters.claude import ClaudeAdapter, parse_envelope  # noqa: E402
from missions.adapters.codex import CodexAdapter, parse_events  # noqa: E402
from missions.adapters.stub import StubAdapter  # noqa: E402
from missions.outcome import Grade, Outcome, RunRequest, classify  # noqa: E402

BASE = HERE / "traces" / "_base" / "mission"
STUBS = HERE / "traces" / "_base" / "stub"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = self.tmp / ".missions" / "demo"
        shutil.copytree(BASE, self.m)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class StateTests(Fixture):
    def test_read_state(self):
        st = files.read_state(self.m)
        self.assertEqual(st.phase, "implementing")
        self.assertEqual(st.milestone, "M1")
        self.assertEqual(st.branch, "mission/demo")
        self.assertEqual(st.open_issues, [])
        self.assertTrue(st.has_block)
        self.assertEqual(st.state_cap_lines, 200)

    def test_phase_aliases_and_comments(self):
        text = files.read_text(self.m / "state.md").replace("phase: implementing", "phase: Implementation — M1 in flight   # note")
        files.write_text(self.m / "state.md", text)
        self.assertEqual(files.read_state(self.m).phase, "implementing")

    def test_write_fields_keeps_comments_and_adds_missing(self):
        text = files.read_text(self.m / "state.md").replace("phase: implementing", "phase: implementing   # planning | implementing")
        files.write_text(self.m / "state.md", text)
        files.write_state_fields(self.m, phase="validating", resume_next="validate M1", extra_key="x")
        raw = files.read_text(self.m / "state.md")
        self.assertIn("phase: validating   # planning | implementing", raw)
        self.assertIn("resume_next: validate M1\n", raw)
        self.assertIn("extra_key: x\n```", raw)
        self.assertEqual(files.read_state(self.m).phase, "validating")

    def test_open_issues_replace_none_then_append(self):
        files.add_open_issues(self.m, ["F001 handoff: stack down"])
        self.assertEqual(files.read_state(self.m).open_issues, ["F001 handoff: stack down"])
        files.add_open_issues(self.m, ["second"])
        self.assertEqual(files.read_state(self.m).open_issues, ["F001 handoff: stack down", "second"])
        self.assertNotIn("- none", files.read_text(self.m / "state.md"))


class FeatureTests(Fixture):
    def test_read_features(self):
        feats = files.read_features(self.m)
        self.assertEqual([f.id for f in feats], ["F001", "F002", "F003"])
        self.assertEqual([f.milestone for f in feats], ["M1", "M1", "M2"])
        self.assertEqual(feats[0].assertions, ["A001", "A002"])
        self.assertEqual(feats[0].files, ["analytics/service.py", "tests/unit/test_a.py"])
        self.assertEqual(feats[1].depends, ["F001"])
        self.assertEqual(feats[0].depends, [])
        self.assertEqual(feats[0].title, "feature F001")
        self.assertEqual(feats[0].status, "pending")

    def test_set_feature_status_and_range(self):
        files.set_feature(self.m, "F001", status="active")
        self.assertEqual(files.read_features(self.m)[0].status, "active")
        files.set_feature(self.m, "F001", status="done", commit="abcdef0123456789", rng="1111111aaaa..abcdef0123456789")
        f = files.read_features(self.m)[0]
        self.assertEqual(f.status, "done")
        self.assertEqual(f.commit, "abcdef0")
        self.assertEqual(f.range, "1111111..abcdef0")
        raw = files.read_text(self.m / "features.md")
        self.assertIn("- **Status:** done · commit `abcdef0`\n- **Range:** `1111111`..`abcdef0`\n", raw)
        self.assertEqual(raw.count("**Range:**"), 1)
        files.set_feature(self.m, "F001", rng="2222222..3333333")
        self.assertEqual(files.read_text(self.m / "features.md").count("**Range:**"), 1)
        self.assertEqual(files.read_features(self.m)[1].status, "pending")

    def test_files_line_without_backticks_reads_like_check_sh(self):
        # the no-backtick branch is check.sh rule 1c's split: commas and whitespace, keeping a
        # token that holds a `/` or ends in a source suffix; `(new)` and a placeholder are nothing
        self.assertEqual(files._split_files("alerting/service.py (new), alerting/repository.py"),
                         ["alerting/service.py", "alerting/repository.py"])
        self.assertEqual(files._split_files("README.md docs/ Makefile, setup.json hooks/run.sh"),
                         ["README.md", "docs/", "setup.json", "hooks/run.sh"])
        self.assertEqual(files._split_files("\u2014"), [])
        self.assertEqual(files._split_files("-"), [])
        self.assertEqual(files._split_files(""), [])
        # backticks are read verbatim, spaces and all: the template's shape and append_feature's
        self.assertEqual(files._split_files("`docs/my note.md`, `alerting/service.py` (new)"), ["docs/my note.md", "alerting/service.py"])
        # on a real features.md the driver counts the files check.sh's feature/file gate counts
        path = self.m / "features.md"
        files.write_text(path, files.read_text(path).replace(
            "- **Files:** `analytics/service.py`, `tests/unit/test_a.py`", "- **Files:** analytics/service.py (new), tests/unit/test_a.py"))
        self.assertEqual(files.read_features(self.m)[0].files, ["analytics/service.py", "tests/unit/test_a.py"])
        self.assertEqual(check_sh(self.m)[0], 0)
        files.write_text(path, files.read_text(path).replace(
            "- **Files:** analytics/service.py (new), tests/unit/test_a.py", "- **Files:** analytics/service.py (new)"))
        self.assertEqual(len({p for f in files.read_features(self.m) for p in f.files}), 2)
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 1)
        self.assertIn("3 features over 2 distinct files", out)     # `(new)` is not a file for check.sh either


class ContractTests(Fixture):
    def test_read_and_claim(self):
        rows = files.read_contract(self.m)
        self.assertEqual([r.id for r in rows], ["A001", "A002", "A003"])
        self.assertEqual(rows[1].features, ["F001", "F002"])
        self.assertEqual(rows[2].proof_class, "interface")
        self.assertEqual(files.claim_assertions(self.m, ["A001", "A002"]), ["A001", "A002"])
        rows = files.read_contract(self.m)
        self.assertEqual([r.status for r in rows], ["claimed", "claimed", "unproven"])
        self.assertEqual(files.claim_assertions(self.m, ["A001"]), [])
        raw = files.read_text(self.m / "contract.md")
        self.assertIn("| A001 | Omitting the window equals the whole day | structural | F001 | claimed | — |", raw)

    def test_never_touches_proven(self):
        raw = files.read_text(self.m / "contract.md").replace("| F001 | unproven |", "| F001 | proven |")
        files.write_text(self.m / "contract.md", raw)
        self.assertEqual(files.claim_assertions(self.m, ["A001"]), [])
        self.assertEqual(files.read_contract(self.m)[0].status, "proven")

    def test_a_note_between_rows_and_a_second_table(self):
        # check.sh reads every `| A0nn |` row of the file: a note between two rows and a second
        # table further down end nothing for it, so they end nothing for the driver either
        raw = files.read_text(self.m / "contract.md")
        raw = raw.replace("| A002 |", "A002 below is the tenancy assertion; F002 shares it.\n\n| A002 |", 1)
        raw += ("\n## Later additions\n\n"
                "| ID | Assertion | Proof class | Feature(s) | Status | Evidence | Proof budget |\n"
                "|---|---|---|---|---|---|---|\n"
                "| A004 | The chip survives a reload | interface | F003 | unproven | \u2014 | min: playwright; max: 1 run |\n"
                "| ~~A005~~ | retired before the plan | \u2014 | \u2014 | \u2014 | \u2014 | \u2014 |\n")
        files.write_text(self.m / "contract.md", raw)
        files.write_text(self.m / "features.md", files.read_text(self.m / "features.md").replace(
            "- **Assertions:** A003\n", "- **Assertions:** A003, A004\n"))
        rows = files.read_contract(self.m)
        self.assertEqual([r.id for r in rows], ["A001", "A002", "A003", "A004"])
        self.assertEqual((rows[3].proof_class, rows[3].features, rows[3].status), ("interface", ["F003"], "unproven"))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        self.assertIn("assertions 4", out)                     # the same four rows check.sh counts
        # each row's line is its own, so a write lands on it and nowhere else -- the note included
        self.assertEqual(files.prove_assertions(self.m, {"A002": "validation/M1-review-F001.md", "A004": "validation/M2-behavior.md"}),
                         ["A002", "A004"])
        after = files.read_text(self.m / "contract.md")
        self.assertIn("| A002 | Tenant A never sees tenant B | structural | F001, F002 | proven | validation/M1-review-F001.md |", after)
        self.assertIn("| A004 | The chip survives a reload | interface | F003 | proven | validation/M2-behavior.md |", after)
        self.assertEqual(after.count("| proven |"), 2)
        self.assertIn("A002 below is the tenancy assertion; F002 shares it.\n\n| A002 |", after)
        self.assertEqual(files.claim_assertions(self.m, ["A001", "A004"]), ["A001"])
        self.assertEqual([r.status for r in files.read_contract(self.m)], ["claimed", "proven", "unproven", "proven"])


class BudgetAndDesignTests(Fixture):
    def test_budget(self):
        b = files.read_budget(self.m)
        self.assertEqual(b["dollar_cap"], 100.0)
        self.assertEqual(b["dispatch_cap"], 20.0)
        self.assertEqual(b["wall_cap_h"], 10.0)
        self.assertEqual(b["repair_rounds"], 2.0)
        self.assertEqual(b["terminal_reserve_pct"], 15.0)
        self.assertEqual(files.repair_rounds(self.m), 2)
        text = files.read_text(self.m / "mission.md")
        files.write_text(self.m / "mission.md", text.replace("Repair rounds per assertion: 2", "Repair rounds per assertion: 1"))
        self.assertEqual(files.repair_rounds(self.m), 1)
        files.write_text(self.m / "mission.md", text.replace("- Repair rounds per assertion: 2\n", ""))
        self.assertEqual(files.repair_rounds(self.m), 2)                 # the default when mission.md does not say

    def test_design_section(self):
        section, rows = files.design_section(self.m, "F001")
        self.assertTrue(section.startswith("### F001"))
        self.assertEqual(len(rows), 1)
        self.assertIn("D001", rows[0])
        self.assertEqual(files.design_section(self.m, "F009"), ("", []))


class HandoffTests(Fixture):
    def test_read_handoff(self):
        (self.m / "handoffs").mkdir()
        files.write_text(self.m / "handoffs" / "F001.md",
                         "# Handoff F001\n\n## Status\n Complete \n\n## Issues discovered\n- port busy\n- none of the tests ran\n\n"
                         "## Commit\n`abc1234def` F001: x\n")
        h = files.read_handoff(self.m, "F001")
        self.assertTrue(h.exists)
        self.assertEqual(h.status, "complete")
        self.assertEqual(h.issues, ["port busy", "none of the tests ran"])
        self.assertEqual(h.sha, "abc1234def")
        self.assertFalse(files.read_handoff(self.m, "F002").exists)

    def test_issues_none(self):
        (self.m / "handoffs").mkdir()
        files.write_text(self.m / "handoffs" / "F001.md", "## Status\nblocked\n## Issues discovered\nnone\n## Commit\n")
        h = files.read_handoff(self.m, "F001")
        self.assertEqual(h.issues, [])
        self.assertEqual(h.status, "blocked")
        self.assertIsNone(h.sha)


class JournalTests(Fixture):
    def test_task_attempts_by_prefix(self):
        for task in ("review-F001#1", "review-F001#2", "review-F0011#1", "scrutiny-M1#1"):
            journal.append(self.m, "dispatch", task=task, agent="x")
        self.assertEqual(journal.task_attempts(self.m, "review-F001"), 2)
        self.assertEqual(journal.task_attempts(self.m, "scrutiny-M1"), 1)
        self.assertEqual(journal.task_attempts(self.m, "triage"), 0)

    def test_append_and_spend(self):
        journal.append(self.m, "session_cost", session_id="a", usd=1.5)
        journal.append(self.m, "session_cost", session_id="a", usd=2.5)
        journal.append(self.m, "session_cost", session_id="b", usd=1.0)
        journal.append(self.m, "agent_return", duration_s=3600, agent="x")
        journal.append(self.m, "note", text=None, keep="y", duration_s=None)
        self.assertEqual(journal.spend_usd(self.m), 3.5)
        self.assertEqual(journal.wall_hours(self.m), 1.0)
        recs = list(journal.events(self.m))
        self.assertEqual(recs[-1]["via"], "driver")
        self.assertNotIn("text", recs[-1])
        self.assertIsNone(recs[-1]["duration_s"])
        with open(self.m / "journal.jsonl", "a") as fh:
            fh.write("not json\n")
        self.assertEqual(len(list(journal.events(self.m))), 5)


class PromptTests(Fixture):
    def test_agent_definition_tools(self):
        meta, body = prompts.system_prompt(PLUGIN)
        self.assertEqual(meta.get("name"), "mission-worker")
        self.assertIn("Bash", meta["tools"])
        self.assertIn("Write", meta["tools"])
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", body)

    def test_worker_prompt_first_line(self):
        feats = files.read_features(self.m)
        rows = [a for a in files.read_contract(self.m) if a.id in feats[0].assertions]
        text = prompts.worker_prompt(self.m, feats[0], "```mission-state\nphase: implementing\n```", rows,
                                     files.design_section(self.m, "F001"), PLUGIN,
                                     prior={"step": "F001#1", "cls": "malformed_handoff",
                                            "problems": ["missing sections: ## Left undone"]})
        self.assertTrue(text.startswith("Mission: demo. Feature: F001 — feature F001.\n"))
        self.assertIn("A001 — Omitting the window equals the whole day  [structural]  proof: min: named test", text)
        self.assertIn("| D001 |", text)
        self.assertIn('starts with "F001:"', text)
        self.assertIn("was rejected", text)
        self.assertIn("Do not spawn background work", text)

    def test_worker_prompt_does_not_call_a_limit_a_rejection(self):
        """#21: an attempt the driver's own cap cut off did not earn the word. Telling the next
        worker it was rejected is how committed, graded-clean work gets redone."""
        feats = files.read_features(self.m)
        rows = [a for a in files.read_contract(self.m) if a.id in feats[0].assertions]

        def text(cls):
            return prompts.worker_prompt(
                self.m, feats[0], "```mission-state\nphase: implementing\n```", rows,
                files.design_section(self.m, "F001"), PLUGIN,
                prior={"step": "F001#1", "cls": cls, "problems": ["the driver's own $8.8 cap ended the run"]})

        for cls in ("budget_exhausted", "infra_quota"):
            t = text(cls)
            self.assertNotIn("was rejected", t, cls)
            self.assertIn("did not finish", t)
            self.assertIn("cut off before the work was done, not rejected", t)
            self.assertIn("the driver's own $8.8 cap ended the run", t)
            self.assertIn("build on them, do not redo them", t)
        self.assertIn("was rejected", text("tests_failed"))


class AdapterTests(unittest.TestCase):
    def req(self, **over):
        d = dict(role="worker", task="F001#1", prompt_path=Path("/tmp/p.md"), cwd=Path("/tmp"), env={},
                 timeout_s=10, budget_usd=2.5, model="sonnet", effort=None, read_only=False,
                 output_path=Path("/tmp/o.md"), system_path=Path("/nonexistent/system.md"),
                 run_dir=Path("/tmp/run"), tools=["Read", "Write", "Bash"])
        d.update(over)
        return RunRequest(**d)

    def test_claude_command(self):
        cmd = ClaudeAdapter({}).command(self.req())
        self.assertEqual(cmd[:4], ["claude", "-p", "--output-format", "json"])
        self.assertIn("--permission-mode", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Read,Write,Bash")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "2.5")
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertNotIn("--disallowedTools", cmd)
        self.assertNotIn("--effort", cmd)
        ro = ClaudeAdapter({"permission_mode": "plan"}).command(self.req(read_only=True, budget_usd=None, effort="high"))
        self.assertIn("--disallowedTools", ro)
        self.assertNotIn("--max-budget-usd", ro)
        self.assertEqual(ro[ro.index("--effort") + 1], "high")

    def test_claude_budget_grace(self):
        """--max-budget-usd carries the HARD cap, not the budget: a cut exactly at the budget lands
        on whatever the run was doing, which is often writing the handoff (#21)."""
        cmd = ClaudeAdapter({}).command(self.req(budget_usd=2.5, budget_grace_pct=10))
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "2.75")
        # no grace configured is exactly today's behaviour, and no budget is still no flag
        plain = ClaudeAdapter({}).command(self.req(budget_usd=2.5))
        self.assertEqual(plain[plain.index("--max-budget-usd") + 1], "2.5")
        self.assertNotIn("--max-budget-usd", ClaudeAdapter({}).command(
            self.req(budget_usd=None, budget_grace_pct=10)))

    def test_claude_reports_its_own_cap(self):
        """Both halves must hold before a run counts as capped: the driver passed a cap, AND the
        harness named the cap as why it stopped. A budgeted run that failed some other way is not
        one, and neither is `error_max_budget_usd` from a run we never capped."""
        tmp = Path(tempfile.mkdtemp())
        fake = tmp / "fake-claude"
        fake.write_text("#!/bin/bash\ncat >/dev/null\nprintf '%s' \"$FAKE_ENVELOPE\"\nexit \"$FAKE_RC\"\n",
                        encoding="utf-8")
        fake.chmod(0o755)
        prompt = tmp / "p.md"
        prompt.write_text("go", encoding="utf-8")
        n = [0]

        def run(subtype, rc, **over):
            n[0] += 1
            rd = tmp / ("run%d" % n[0])
            envelope = json.dumps({"type": "result", "subtype": subtype, "is_error": rc != 0,
                                   "result": "stopped", "total_cost_usd": 0.55})
            kw = dict(budget_usd=0.5, budget_grace_pct=10)
            kw.update(over)
            req = self.req(prompt_path=prompt, run_dir=rd, output_path=rd / "o.md", cwd=tmp,
                           env={"FAKE_ENVELOPE": envelope, "FAKE_RC": str(rc)}, **kw)
            return ClaudeAdapter({"bin": str(fake)}).run(req)

        capped = run("error_max_budget_usd", 1)
        self.assertEqual((capped.rc, capped.capped_usd), (1, 0.55))
        self.assertEqual(capped.cost["value"], 0.55)
        self.assertIn("error_max_budget_usd", capped.detail)
        self.assertIsNone(run("error_during_execution", 1).capped_usd, "a crash is not a cap")
        self.assertIsNone(run("success", 0).capped_usd)
        self.assertIsNone(run("error_max_budget_usd", 1, budget_usd=None).capped_usd,
                          "we passed no cap, so this exit is not ours to claim")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_claude_envelope(self):
        env = parse_envelope('{"type":"result","subtype":"success","is_error":false,"duration_ms":1234,'
                             '"num_turns":3,"result":"done","session_id":"s1","total_cost_usd":0.42,'
                             '"modelUsage":{"claude-sonnet-5":{"costUSD":0.42}}}')
        self.assertEqual(env["total_cost_usd"], 0.42)
        self.assertIsNone(parse_envelope("not json at all"))
        self.assertEqual(parse_envelope('noise\n{"a":1}')["a"], 1)

    def test_codex_command_and_events(self):
        cmd = CodexAdapter({}).command(self.req(model="gpt-5"))
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-5")
        self.assertEqual(CodexAdapter({}).command(self.req(read_only=True))[cmd.index("--sandbox") + 1], "read-only")
        ev = parse_events('{"type":"thread.started","thread_id":"t1"}\n'
                          '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"hello"}}\n'
                          '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":25}}\n')
        self.assertEqual(ev["thread_id"], "t1")
        self.assertEqual(ev["usage"]["output_tokens"], 25)
        self.assertEqual(ev["last_message"], "hello")
        err = parse_events('{"type":"turn.failed","error":{"message":"rate limit exceeded"}}')
        self.assertIn("rate limit", err["error"])
        self.assertIsNone(err["usage"])

    def test_codex_sandbox_preflight(self):
        """A sandbox codex cannot start is a guaranteed no_op with the tokens spent, so preflight
        refuses it -- but only for the policies bubblewrap implements, and never on an unknown."""
        from missions.adapters import codex as cx

        def with_probe(value, cfg):
            saved = cx.user_namespaces_usable
            cx.user_namespaces_usable = lambda: value
            try:
                return cx.CodexAdapter(cfg).preflight_problems()
            finally:
                cx.user_namespaces_usable = saved

        for policy in cx.SANDBOX_NEEDS_USERNS:
            problems = with_probe(False, {"sandbox": policy})
            self.assertEqual(len(problems), 1, policy)
            self.assertIn("user namespace", problems[0])
            self.assertIn("danger-full-access", problems[0])   # the message names the way out
            self.assertEqual(with_probe(True, {"sandbox": policy}), [], policy)
            self.assertEqual(with_probe(None, {"sandbox": policy}), [], policy)   # unknown != failure
        # the policy that needs no bubblewrap is never refused, whatever the host says
        for probe in (False, True, None):
            self.assertEqual(with_probe(probe, {"sandbox": "danger-full-access"}), [])
        # the default is one that needs it, so the check is reachable without configuration
        self.assertIn(cx.CodexAdapter({}).sandbox, cx.SANDBOX_NEEDS_USERNS)
    def test_codex_probe_child_never_escapes(self):
        """The probe forks. A child that let an exception escape would unwind the PARENT's stack
        inside the child -- including the `with DriverLock(...)` the loop holds, whose __exit__
        calls flock(LOCK_UN) on the inherited file description and would free the lock the parent
        still believes it owns. It must os._exit instead, whatever goes wrong.

        Run in a subprocess on purpose: inside unittest an escaped child is swallowed by
        _Outcome's bare `except` and then goes on running the rest of the suite as a second
        process, so the parent still prints OK and only the doubled runtime hints at it. Here a
        child that reaches the end prints a second line, which no exit code can hide."""
        script = (
            "import ctypes, os, sys\n"
            "sys.path.insert(0, %r)\n"
            "from missions.adapters import codex as cx\n"
            "class Exploding:\n"
            "    def __getattr__(self, n): raise AttributeError(n)   # e.g. musl behind libc.so.6\n"
            "ctypes.CDLL = lambda *a, **k: Exploding()\n"
            "class Marker:\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *a): print('unwound', os.getpid())   # what DriverLock does\n"
            "with Marker():\n"
            "    r = cx.user_namespaces_usable()\n"
            "print('returned', r, os.getpid())\n"
        ) % str(PLUGIN / "driver")
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        returned = [ln for ln in lines if ln.startswith("returned")]
        self.assertEqual(len(returned), 1, "a forked child escaped and ran on: %r" % lines)
        self.assertTrue(returned[0].startswith("returned False"), returned[0])
        self.assertEqual([ln for ln in lines if ln.startswith("unwound")].__len__(), 1, lines)
        self.assertEqual(res.returncode, 0, res.stderr[-400:])

    def test_outcome_classes_partition(self):
        """The smoke splits CLASSES into productive / nothing-happened / not-our-fault and judges a
        paid run by it. Assert the split here, where it is free: a ninth class must fail a suite
        someone runs, not a $2 run someone might not."""
        from missions.outcome import CLASSES
        productive = ("done", "handoff_missing", "malformed_handoff", "tests_failed")
        nothing = ("no_op", "infra_crash", "stalled")
        # a limit ended the run, not a defect in it: the provider's (exit 8) or the driver's own (4)
        limits = ("infra_quota", "budget_exhausted")
        self.assertEqual(set(productive) | set(nothing) | set(limits), set(CLASSES))
        self.assertEqual(len(productive) + len(nothing) + len(limits), len(CLASSES))


class RequestTests(Fixture):
    def ctx(self, harness, cfg=None):
        from missions.steps import Context
        return Context(mission_dir=self.m, checkout=self.tmp, plugin=PLUGIN, cfg=cfg or {"roles": {}},
                       adapter=None, run_id="r1", harness=harness)

    def test_the_cap_is_shown_only_where_it_binds(self):
        """codex passes no budget flag, so a cap printed or journalled for it is a promise nothing
        keeps. capabilities()["budget"] has always said which is which; now something asks."""
        from missions import steps
        feats = files.read_features(self.m)
        req = steps.build_request(self.ctx("claude"), feats[0], "F001#1", self.m / "runs" / "F001#1",
                                  {"tools": []}, "implementing")

        class Fake:
            def __init__(self, budget):
                self.b = budget

            def capabilities(self):
                return {"budget": self.b}

        ctx = self.ctx("claude")
        ctx.adapter = Fake(True)
        self.assertEqual(steps._budget_line(ctx, req), " budget $8 (cut at $8.8)")
        self.assertEqual(steps._cap_journal(ctx, req), {"budget_usd": 8.0, "cap_usd": 8.8})
        ctx.adapter = Fake(False)
        self.assertEqual(steps._budget_line(ctx, req), "")
        self.assertEqual(steps._cap_journal(ctx, req), {})
        # the real ones, so a change to either adapter's declaration fails here
        from missions.adapters.claude import ClaudeAdapter
        from missions.adapters.codex import CodexAdapter
        self.assertTrue(ClaudeAdapter({}).capabilities()["budget"])
        self.assertFalse(CodexAdapter({}).capabilities()["budget"])

    def test_budget_grace(self):
        """driver.json's budget_grace_pct, applied to every role: the budget is what the work is
        meant to cost, the cap is where the harness stops it, and the gap is the wrap-up (#21)."""
        from missions import steps
        feats = files.read_features(self.m)
        meta = {"tools": ["Read"]}

        def req(cfg, role="worker"):
            return steps.build_request(self.ctx("claude", cfg), feats[0], "F001#1",
                                       self.m / "runs" / "F001#1", meta, "implementing", role=role)

        r = req({"roles": {}})
        self.assertEqual((r.budget_usd, r.budget_grace_pct, r.hard_budget_usd), (8.0, 10.0, 8.8))
        self.assertEqual(req({"roles": {}}, role="reviewer").hard_budget_usd, 6.6)
        self.assertEqual(steps.grace_pct({}), 10.0)
        self.assertEqual((steps.grace_pct({"budget_grace_pct": 0}), steps.grace_pct({"budget_grace_pct": None})),
                         (0.0, 0.0))
        # the operator's own number wins, and 0 or null turns the grace off entirely
        r = req({"roles": {"worker": {"budget_usd": 4}}, "budget_grace_pct": 25})
        self.assertEqual((r.budget_usd, r.hard_budget_usd), (4.0, 5.0))
        self.assertEqual(req({"roles": {}, "budget_grace_pct": 0}).hard_budget_usd, 8.0)
        self.assertEqual(req({"roles": {}, "budget_grace_pct": None}).hard_budget_usd, 8.0)
        # no budget means no cap to grace
        self.assertIsNone(req({"roles": {"worker": {"budget_usd": None}}}).hard_budget_usd)

    def test_model_vocabulary_per_harness(self):
        from missions import steps
        feats = files.read_features(self.m)
        meta = {"model": "sonnet", "effort": "high", "tools": ["Read"]}
        req = steps.build_request(self.ctx("claude"), feats[0], "F001#1", self.m / "runs" / "F001#1", meta, "implementing")
        self.assertEqual((req.model, req.effort, req.timeout_s, req.budget_usd), ("sonnet", "high", 2400, 8.0))
        req = steps.build_request(self.ctx("codex"), feats[0], "F001#1", self.m / "runs" / "F001#1", meta, "implementing")
        self.assertEqual((req.model, req.effort), (None, None))
        cfg = {"roles": {"worker": {"model": "gpt-5", "timeout_s": 60, "budget_usd": None}}}
        req = steps.build_request(self.ctx("codex", cfg), feats[0], "F001#1", self.m / "runs" / "F001#1", meta, "implementing")
        self.assertEqual((req.model, req.timeout_s, req.budget_usd), ("gpt-5", 60, None))
        files.write_text(self.m / "features.md", files.read_text(self.m / "features.md").replace(
            "- **Procedures:** make test-unit\n- **Depends on:** —", "- **Seat:** opus — rationale\n- **Procedures:** make test-unit\n- **Depends on:** —", 1))
        feats = files.read_features(self.m)
        self.assertEqual(feats[0].seat, "opus")
        self.assertEqual(steps.build_request(self.ctx("claude"), feats[0], "F001#2", self.m, meta, "implementing").model, "opus")
        self.assertIsNone(steps.build_request(self.ctx("codex"), feats[0], "F001#2", self.m, meta, "implementing").model)
        self.assertEqual(req.env["MISSIONS_TASK"], "F001#1")
        self.assertNotIn("CLAUDECODE", req.env)


class ClassifyTests(unittest.TestCase):
    def out(self, rc=0, timed_out=False, killed_by=None, capped_usd=None):
        return Outcome(task="F001#1", rc=rc, elapsed_s=1.0, timed_out=timed_out, killed_by=killed_by,
                       cost={"unit": "unknown", "value": None, "source": ""}, harness="stub", model=None,
                       stdout_path=Path("/x"), stderr_path=Path("/y"), capped_usd=capped_usd)

    def written(self, **kw):
        g = Grade(handoff_exists=True, handoff_written=True, status="complete", sha="a" * 40, commit_on_branch=True)
        for k, v in kw.items():
            setattr(g, k, v)
        return g

    def test_nine_classes(self):
        self.assertEqual(classify(self.out(), self.written()), "done")
        self.assertEqual(classify(self.out(), self.written(status="partial")), "tests_failed")
        self.assertEqual(classify(self.out(), self.written(status="blocked", sha=None)), "tests_failed")
        self.assertEqual(classify(self.out(), self.written(problems=["x"])), "malformed_handoff")
        self.assertEqual(classify(self.out(), self.written(commit_on_branch=False)), "malformed_handoff")
        self.assertEqual(classify(self.out(rc=3), self.written()), "malformed_handoff")
        self.assertEqual(classify(self.out(), Grade(False, new_commit="b" * 40)), "handoff_missing")
        self.assertEqual(classify(self.out(rc=1), Grade(False, quota="usage limit")), "infra_quota")
        # a quota after a WIP commit is still a quota, not a finished feature
        self.assertEqual(classify(self.out(rc=1), Grade(False, new_commit="b" * 40, quota="usage limit")), "infra_quota")
        self.assertEqual(classify(self.out(rc=1), Grade(False)), "infra_crash")
        self.assertEqual(classify(self.out(rc=1, capped_usd=8.8), Grade(False)), "budget_exhausted")
        self.assertEqual(classify(self.out(timed_out=True), Grade(False)), "stalled")
        self.assertEqual(classify(self.out(), Grade(False)), "no_op")

    def test_killed_runs(self):
        # a complete handoff written by a worker the driver had to kill is not done
        g = self.written()
        self.assertEqual(classify(self.out(timed_out=True, killed_by="timeout"), g), "malformed_handoff")
        self.assertIn("ended by the driver", g.problems[0])
        self.assertEqual(classify(self.out(rc=143, killed_by=watchdog.COMMIT_NO_HANDOFF), Grade(False, new_commit="c" * 40)), "handoff_missing")
        self.assertEqual(classify(self.out(rc=143, killed_by=watchdog.SILENCE), Grade(False)), "stalled")
        # a cut-off run's reconstruction is partial: tests_failed, never done
        g = self.written(status="partial", reconstructed=True)
        self.assertEqual(classify(self.out(rc=143, timed_out=True, killed_by="timeout"), g), "tests_failed")
        # the watchdog's own verdict reconstructs complete, and that kill is not held against it
        g = self.written(reconstructed=True)
        self.assertEqual(classify(self.out(rc=143, killed_by=watchdog.COMMIT_NO_HANDOFF), g), "done")

    def test_stale_handoff_is_not_this_attempts(self):
        stale = Grade(handoff_exists=True, handoff_written=False, status="complete", sha="a" * 40)
        self.assertEqual(classify(self.out(rc=1), stale), "infra_crash")
        self.assertEqual(classify(self.out(), stale), "no_op")
        self.assertEqual(classify(self.out(), Grade(True, handoff_written=False, new_commit="d" * 40)), "handoff_missing")

    def test_quota_needs_no_handoff(self):
        self.assertEqual(classify(self.out(rc=1), self.written(quota="rate limit")), "malformed_handoff")

    def test_quota_under_a_partial_handoff_is_still_a_quota(self):
        """The handoff branch returns before the quota arm below it, so a partial handoff from a
        quota-limited run used to be re-dispatched as tests_failed -- into the same quota."""
        self.assertEqual(classify(self.out(rc=1), self.written(status="partial", quota="usage limit")), "infra_quota")
        # the worker's own `blocked` is its answer, not the provider's: a bigger quota does not change it
        self.assertEqual(classify(self.out(rc=1), self.written(status="blocked", quota="usage limit")), "tests_failed")

    def test_the_drivers_own_cap_is_not_a_defect(self):
        """#21. The claude adapter passes --max-budget-usd, so the cap's own exit is rc 1 -- which
        the generic non-zero arm read as a malformed handoff and the loop re-dispatched, three
        times, into the identical wall."""
        capped = self.out(rc=1, capped_usd=8.8)
        # complete, committed, nothing wrong with it: the grace is the wrap-up window, and it worked
        g = self.written()
        self.assertEqual(classify(capped, g), "done")
        self.assertEqual(g.problems, [], "a capped run that finished has nothing held against it")
        # cut off with the work unfinished: stop, do not retry -- the same cap ends the next attempt
        self.assertEqual(classify(capped, self.written(status="partial")), "budget_exhausted")
        # the worker's own verdict is untouched by how much money was left
        self.assertEqual(classify(capped, self.written(status="blocked", sha=None)), "tests_failed")
        # nothing landed at all
        self.assertEqual(classify(capped, Grade(False)), "budget_exhausted")
        # our own structured cap outranks a quota GUESSED from the harness's error prose: telling
        # the operator to wait for a reset that is not coming is the #21 loop in another costume
        self.assertEqual(classify(capped, Grade(False, quota="usage limit")), "budget_exhausted")
        # ...but not at the price of losing a commit: that still reconstructs first
        self.assertEqual(classify(capped, Grade(False, new_commit="b" * 40, quota="usage limit")), "handoff_missing")
        # a commit but no handoff is still reconstructed first: the work is recorded before the stop
        self.assertEqual(classify(capped, Grade(False, new_commit="b" * 40)), "handoff_missing")
        self.assertEqual(classify(capped, self.written(status="partial", reconstructed=True)), "budget_exhausted")
        # and a cap does not excuse a handoff that is actually bad, or a run the driver killed
        self.assertEqual(classify(capped, self.written(problems=["x"])), "malformed_handoff")
        self.assertEqual(classify(capped, self.written(commit_on_branch=False)), "malformed_handoff")
        self.assertEqual(classify(self.out(rc=1, capped_usd=8.8, timed_out=True, killed_by="timeout"),
                                  self.written()), "malformed_handoff")
        # the regression guard: an uncapped non-zero exit is what it always was
        self.assertEqual(classify(self.out(rc=1), self.written()), "malformed_handoff")


class QuotaTests(unittest.TestCase):
    def test_signature(self):
        tmp = Path(tempfile.mkdtemp())
        err = tmp / "stderr"
        out = tmp / "stdout"
        out.write_text("", encoding="utf-8")
        err.write_text("Error: You've hit your usage limit \u00b7 resets 3am (UTC)\n", encoding="utf-8")
        o = Outcome(task="F001#1", rc=1, elapsed_s=1, timed_out=False, killed_by=None, cost={}, harness="claude",
                    model=None, stdout_path=out, stderr_path=err)
        sig = grading.quota_signature(o)
        self.assertIsNotNone(sig)
        self.assertIn("resets 3am", sig)
        err.write_text("Traceback: boom\n", encoding="utf-8")
        self.assertIsNone(grading.quota_signature(o))
        # a clean exit with no detail never reads as quota, whatever the transcript says
        out.write_text("the tests mention a rate limit here\n", encoding="utf-8")
        o2 = Outcome(task="F001#1", rc=0, elapsed_s=1, timed_out=False, killed_by=None, cost={}, harness="stub",
                     model=None, stdout_path=out, stderr_path=err)
        self.assertIsNone(grading.quota_signature(o2))
        o2.detail = "error_during_execution: HTTP 429 too many requests"
        self.assertIsNotNone(grading.quota_signature(o2))
        # the API's own error type, with the underscore
        o2.detail = "error_during_execution: rate_limit_error"
        self.assertIsNotNone(grading.quota_signature(o2))
        # a 529 is transient, not a quota; and the worker's transcript on stdout is never read
        o2.detail = "error_during_execution: overloaded_error"
        self.assertIsNone(grading.quota_signature(o2))
        o3 = Outcome(task="F001#1", rc=1, elapsed_s=1, timed_out=False, killed_by=None, cost={}, harness="claude",
                     model=None, stdout_path=out, stderr_path=err)
        out.write_text('{"result": "test_rate_limit_backoff failed: 429 too many requests"}\n', encoding="utf-8")
        self.assertIsNone(grading.quota_signature(o3))


class RepoFixture(Fixture):
    """The base mission inside a real git checkout, on the mission branch."""

    def setUp(self):
        super().setUp()
        subprocess.run(["bash", str(HERE / "traces" / "_base" / "repo.sh"), str(self.tmp / "r")], check=True,
                       capture_output=True)
        self.repo = self.tmp / "r" / "repo"
        shutil.rmtree(self.m)
        self.m = self.repo / ".missions" / "demo"
        shutil.copytree(BASE, self.m)
        files.write_config(self.m, {"harness": "stub", "checkout": ".", "branch": "mission/demo",
                                    "adapters": {"stub": {"script_dir": "x"}}})

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, fid="F001", text="work"):
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# %s %s\n" % (fid, text))
        self.git("add", "analytics/service.py")
        self.git("commit", "-qm", "%s: %s" % (fid, text))
        return self.git("rev-parse", "HEAD")

    def handoff(self, fid="F001", sha=None, status="complete", claims="- A001 \u2014 yes\n- A002 \u2014 yes"):
        (self.m / "handoffs").mkdir(exist_ok=True)
        sha = sha or self.git("rev-parse", "HEAD")
        (self.m / "handoffs" / (fid + ".md")).write_text(
            "# Handoff %s\n\n## Status\n%s\n\n## Assertions claimed\n%s\n\n## Completed\nx\n\n## Left undone\nnothing\n\n"
            "## Commands run\n| Command | Exit | Note |\n|---|---|---|\n| make test-unit | 0 | ok |\n\n"
            "## Issues discovered\nnone\n\n## Procedures followed\n- D001\n\n## Commit\n`%s` %s: x\n" % (fid, status, claims, sha, fid),
            encoding="utf-8")


class GradeTests(RepoFixture):
    def test_task_keyed_handoff(self):
        head0 = self.git("rev-parse", "HEAD")
        self.commit()
        self.handoff()
        fp = files.fingerprint(files.handoff_path(self.m, "F001"))
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertTrue(g.handoff_written)
        self.assertEqual(g.problems, [])
        self.assertTrue(g.commit_on_branch)
        self.assertEqual(g.claimed, ["A001", "A002"])
        self.assertEqual(classify(ClassifyTests().out(), g), "done")
        # the next attempt finds the same file: it is not this attempt's handoff
        head1 = self.git("rev-parse", "HEAD")
        g2 = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head1, fp, ["A001"], task="F001#2")
        self.assertFalse(g2.handoff_written)
        self.assertIn("unchanged since launch", g2.problems[0])
        self.assertEqual(classify(ClassifyTests().out(rc=1), g2), "infra_crash")

    def test_foreign_claim_and_dirty_tree(self):
        head0 = self.git("rev-parse", "HEAD")
        self.commit()
        self.handoff(claims="- A001 \u2014 yes\n- A003 \u2014 also")
        (self.repo / "analytics" / "leftover.py").write_text("x\n", encoding="utf-8")
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertTrue(any("A003" in p for p in g.problems))
        self.assertTrue(any("uncommitted" in p and "leftover.py" in p for p in g.problems))
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")

    def test_reconstruct_passes_the_same_grade(self):
        head0 = self.git("rev-parse", "HEAD")
        sha = self.commit()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertEqual(classify(ClassifyTests().out(), g), "handoff_missing")
        path = grading.reconstruct(self.m, "F001", self.repo, head0, sha, "F001#1", ["A001", "A002"], "exited 0")
        text = path.read_text(encoding="utf-8")
        self.assertIn("reconstructed by the driver (F001#1)", text.splitlines()[0])
        self.assertIn("## Left undone", text)
        g2 = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertEqual(g2.problems, [])
        self.assertEqual(classify(ClassifyTests().out(), g2), "done")
        self.assertEqual(g2.claimed, ["A001", "A002"])
        # the worker's self-check agrees
        self.assertEqual(grading.self_check(self.m, "F001", self.repo, PLUGIN).problems, [])

    def test_self_check_reports_what_the_driver_would(self):
        g = grading.self_check(self.m, "F001", self.repo, PLUGIN)
        self.assertIn("no handoff", g.problems[0])
        self.commit()
        self.handoff(status="complete", sha="deadbeef")
        g = grading.self_check(self.m, "F001", self.repo, PLUGIN)
        self.assertTrue(any("not in this repository" in p for p in g.problems))


    def test_fingerprint_is_content_not_mtime(self):
        self.commit()
        self.handoff()
        p = files.handoff_path(self.m, "F001")
        fp = files.fingerprint(p)
        head1 = self.git("rev-parse", "HEAD")
        p.touch()                        # a touch is not a write
        p.write_bytes(p.read_bytes())    # neither is a byte-identical rewrite
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head1, fp, ["A001", "A002"], task="F001#2")
        self.assertFalse(g.handoff_written)
        p.write_text(p.read_text(encoding="utf-8") + "\n<!-- attempt 2 -->\n", encoding="utf-8")
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head1, fp, ["A001", "A002"], task="F001#2")
        self.assertTrue(g.handoff_written)

    def test_underclaim_is_rejected_and_self_check_agrees(self):
        head0 = self.git("rev-parse", "HEAD")
        self.commit()
        self.handoff(claims="- A001 \u2014 satisfied\n- A002 \u2014 NOT satisfied; no tenancy test written")
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertEqual(g.claimed, ["A001"])
        self.assertTrue(any("A002 not claimed" in p for p in g.problems))
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")
        s = grading.self_check(self.m, "F001", self.repo, PLUGIN)
        self.assertTrue(any("A002 not claimed" in p and "partial" in p for p in s.problems))
        # a bullet naming two ids claims both; a partial handoff claims what it says and no more is asked
        self.handoff(claims="- A001, A002 \u2014 both satisfied by `analytics/service.py:1`")
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertEqual((g.claimed, g.problems), (["A001", "A002"], []))
        self.handoff(status="partial", claims="- A001 \u2014 satisfied")
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        self.assertEqual(g.problems, [])
        self.assertEqual(classify(ClassifyTests().out(), g), "tests_failed")

    def test_cut_off_run_reconstructs_partial(self):
        head0 = self.git("rev-parse", "HEAD")
        sha = self.commit()
        path = grading.reconstruct(self.m, "F001", self.repo, head0, sha, "F001#1", ["A001", "A002"],
                                   "was ended by the driver (timeout)", finished=False)
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Status\npartial", text)
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1")
        g.reconstructed = True
        self.assertEqual(g.claimed, [])
        self.assertIn("timeout", g.undone[0])
        self.assertEqual(classify(ClassifyTests().out(rc=143, timed_out=True, killed_by="timeout"), g), "tests_failed")
        self.assertEqual(grading.self_check(self.m, "F001", self.repo, PLUGIN).problems, [])

    def test_detached_head_never_counts(self):
        head0 = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach")
        self.commit()
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1",
                                  branch="mission/demo")
        self.assertEqual(g.branch_after, "")
        self.assertFalse(g.commit_on_branch)
        self.assertIsNone(g.new_commit)
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")
        s = grading.self_check(self.m, "F001", self.repo, PLUGIN, branch="mission/demo")
        self.assertTrue(any("not on the mission branch mission/demo" in p for p in s.problems))

    def test_merges_and_unprefixed_commits_are_rejected(self):
        head0 = self.git("rev-parse", "HEAD")
        self.git("commit", "-q", "--allow-empty", "-m", "wip")           # slipped past commit-msg (--no-verify)
        wip = self.git("rev-parse", "--short=7", "HEAD")
        self.commit()
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head0, None, ["A001", "A002"], task="F001#1",
                                  branch="mission/demo")
        self.assertEqual(g.problems, ["commit(s) %s do not start with F001: -- every commit of a run carries the feature prefix" % wip])
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")
        # the self-check measures from the cited commit's first parent: the wip commit lies before it
        self.assertEqual(grading.self_check(self.m, "F001", self.repo, PLUGIN, branch="mission/demo").problems, [])
        # a merge: the pre-merge-commit hook refuses it for a worker that runs the hooks; plain git
        # here stands for --no-verify, and the grade is the gate that does not depend on the worker
        head1 = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "side")
        (self.repo / "analytics" / "side.py").write_text("s\n", encoding="utf-8")
        self.git("add", "analytics/side.py")
        self.git("commit", "-qm", "side: work")
        side = self.git("rev-parse", "--short=7", "HEAD")
        self.git("checkout", "-q", "mission/demo")
        self.git("merge", "-q", "--no-ff", "-m", "F001: merge side", "side")
        merge = self.git("rev-parse", "HEAD")
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head1, None, ["A001", "A002"], task="F001#2",
                                  branch="mission/demo", feature_files=["analytics/service.py"])
        self.assertIn("the run merged: %s are merge commits in %s..%s -- a worker never merges" % (merge[:7], head1[:7], merge[:7]),
                      g.problems)
        self.assertIn("commit(s) %s do not start with F001: -- every commit of a run carries the feature prefix" % side, g.problems)
        self.assertTrue(any("side.py" in p and "outside the feature's Files" in p for p in g.problems), g.problems)
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")
        s = grading.self_check(self.m, "F001", self.repo, PLUGIN, branch="mission/demo")
        self.assertTrue(any("a worker never merges" in p for p in s.problems), s.problems)
        self.assertTrue(any("do not start with F001:" in p for p in s.problems), s.problems)
        self.assertEqual(grading.range_problems(self.repo, "F001", head0, head1), [
            "commit(s) %s do not start with F001: -- every commit of a run carries the feature prefix" % wip])

    def test_rewritten_branch_is_flagged(self):
        head0 = self.git("rev-parse", "HEAD")
        launched = self.commit("F001", "before")                          # HEAD when the run was launched
        self.git("reset", "-q", "--hard", head0)                           # the worker rewound the branch under it
        self.commit()
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, launched, None, ["A001", "A002"], task="F001#1",
                                  branch="mission/demo")
        self.assertTrue(g.rewritten)
        self.assertEqual(g.head_before, launched)
        self.assertEqual((g.to_json()["rewritten"], g.to_json()["head_before"]), (True, launched))
        self.assertEqual(g.problems, [])                                   # its own commit is fine: the loop halts on the rewrite
        # an ordinary run, and a detached commit that left the branch's ref where it was: not rewrites
        head2 = self.git("rev-parse", "HEAD")
        self.commit("F001", "more")
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, head2, None, ["A001", "A002"], task="F001#2",
                                  branch="mission/demo")
        self.assertFalse(g.rewritten)
        tip = self.git("rev-parse", "mission/demo")
        self.git("checkout", "-q", "--detach")
        self.commit("F001", "detached")
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, tip, None, ["A001", "A002"], task="F001#3",
                                  branch="mission/demo")
        self.assertFalse(g.rewritten)
        self.assertFalse(g.commit_on_branch)


class WatchdogTests(RepoFixture):
    def wd(self, **kw):
        head = self.git("rev-parse", "HEAD")
        run_dir = self.m / "runs" / "F001#1"
        run_dir.mkdir(parents=True)
        (run_dir / "stdout").write_text("", encoding="utf-8")
        (run_dir / "stderr").write_text("", encoding="utf-8")
        args = dict(poll_s=0.1, commit_no_handoff_s=0.6, silence_s=None)
        args.update(kw)
        return watchdog.Watchdog(self.m, self.repo, "F001", "F001#1", head, None, run_dir, **args)

    def wait_for(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.05)
        return pred()

    def test_commit_without_handoff_ends_the_run(self):
        w = self.wd()
        w.start()
        try:
            time.sleep(0.3)
            self.assertIsNone(w.verdict)
            self.commit()
            self.assertTrue(self.wait_for(lambda: w.commit is not None))
            rec = journal.last(self.m, "commit_observed")
            self.assertEqual(rec["task"], "F001#1")
            self.assertEqual(rec["handoff"], "pending")
            self.assertTrue(self.wait_for(lambda: w.verdict is not None))
            self.assertEqual(w.verdict, watchdog.COMMIT_NO_HANDOFF)
        finally:
            w.stop()

    def test_handoff_after_commit_stands_down(self):
        w = self.wd()
        w.start()
        try:
            self.commit()
            self.assertTrue(self.wait_for(lambda: w.commit is not None))
            self.handoff()
            time.sleep(1.0)
            self.assertIsNone(w.verdict)
        finally:
            w.stop()
        self.assertEqual(journal.count(self.m, "commit_observed"), 1)

    def test_activity_after_commit_resets_the_grace(self):
        w = self.wd(commit_no_handoff_s=0.5)
        w.start()
        try:
            self.commit()
            self.assertTrue(self.wait_for(lambda: w.commit is not None))
            for i in range(6):
                time.sleep(0.2)
                (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# tick %d\n" % i)
            self.assertIsNone(w.verdict)
        finally:
            w.stop()

    def test_silence(self):
        w = self.wd(silence_s=0.5)
        w.start()
        try:
            self.assertTrue(self.wait_for(lambda: w.verdict is not None, timeout=3.0))
            self.assertEqual(w.verdict, watchdog.SILENCE)
        finally:
            w.stop()


class WatchdogConfigTests(unittest.TestCase):
    """Pure dict work -- deliberately not a RepoFixture: it needs no checkout and no mission tree."""

    def test_config(self):
        c = watchdog.config({"watchdog": {"poll_s": 1, "commit_no_handoff_s": None, "silence_s": 2, "bogus": 9}})
        self.assertEqual(c, {"poll_s": 1.0, "commit_no_handoff_s": None, "silence_s": 2.0})
        self.assertEqual(watchdog.config({}), watchdog.DEFAULTS)

    def test_defaults_keep_the_two_minute_promise(self):
        # #4: a commit without a handoff is journaled within 2 minutes -- the poll is the bound
        self.assertLessEqual(watchdog.DEFAULTS["poll_s"], 120)
        self.assertIsNotNone(watchdog.DEFAULTS["commit_no_handoff_s"])


class MissionLineTests(Fixture):
    def test_reviewer_seat(self):
        self.assertIsNone(files.read_reviewer_seat(self.m))
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md") + "- **Reviewer seat:** fable   # optional — auth boundary\n")
        self.assertEqual(files.read_reviewer_seat(self.m), "fable")
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("fable   #", "claude-opus-5 — because"))
        self.assertEqual(files.read_reviewer_seat(self.m), "claude-opus-5")
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("claude-opus-5 — because", "none"))
        self.assertIsNone(files.read_reviewer_seat(self.m))

    def test_behavior_cap_and_ceiling(self):
        self.assertEqual(files.read_behavior_cap(self.m), 3)
        self.assertEqual(files.read_autonomy_ceiling(self.m), "advisory")
        text = files.read_text(self.m / "mission.md")
        files.write_text(self.m / "mission.md", text.replace("- Behavior-validation cap: 3 live runs per milestone\n", ""))
        self.assertIsNone(files.read_behavior_cap(self.m))
        # the template's own line names both options; only the head of the line is the choice
        files.write_text(self.m / "mission.md", text.replace(
            "- Autonomy ceiling: advisory",
            "- Autonomy ceiling: advisory (default — the loop proceeds under stated assumptions) | halt at every milestone"))
        self.assertEqual(files.read_autonomy_ceiling(self.m), "advisory")
        files.write_text(self.m / "mission.md", text.replace("- Autonomy ceiling: advisory", "- Autonomy ceiling: Halt at every milestone  # ask"))
        self.assertEqual(files.read_autonomy_ceiling(self.m), "halt at every milestone")

    def test_intelligence_line(self):
        self.assertEqual(files.intelligence_line(self.m), "none")
        text = files.read_text(self.m / "state.md")
        files.write_text(self.m / "state.md", text.replace(
            "- Codebase intelligence: none", "- Codebase intelligence: graphify=cli+mcp (graphify-out/, 2026-09-01) · repowise=none"))
        self.assertEqual(files.intelligence_line(self.m), "graphify=cli+mcp (graphify-out/, 2026-09-01) · repowise=none")
        files.write_text(self.m / "state.md", text.replace("- Codebase intelligence: none\n", ""))
        self.assertEqual(files.intelligence_line(self.m), "none")


class MilestoneTests(Fixture):
    def test_milestones_and_next(self):
        self.assertEqual(files.milestones(self.m), ["M1", "M2"])
        self.assertEqual(files.next_milestone(self.m, "M1"), "M2")
        self.assertIsNone(files.next_milestone(self.m, "M2"))
        self.assertIsNone(files.next_milestone(self.m, "M9"))


def check_sh(mission_dir):
    res = subprocess.run(["bash", str(PLUGIN / "scripts" / "check.sh"), str(mission_dir)], capture_output=True, text=True)
    return res.returncode, res.stdout


class FollowupTests(Fixture):
    def entry(self, **over):
        e = dict(title="cross-tenant leak", source="M1-review-F001", assertion="A002", found_by="mission-reviewer",
                 where="`analytics/service.py:3` — no tenant filter", severity="high", cluster="C01",
                 cluster_label="repository queries missing the tenant predicate", blocking=True,
                 disposition="accept", why="beyond the proof budget")
        e.update(over)
        return e

    def test_append_followups_numbering_and_shape(self):
        ids = files.append_followups(self.m, [self.entry(), self.entry(
            title="lint debt", source="M1-scrutiny", assertion=None, found_by="mission-validator-scrutiny", where="",
            severity="low", cluster="C02", cluster_label="", blocking=False, disposition="waive", why="not in scope")])
        self.assertEqual(ids, ["FU001", "FU002"])
        self.assertEqual(files.append_followups(self.m, [self.entry()]), ["FU003"])
        raw = files.read_text(self.m / "followups.md")
        self.assertIn("\n\n## FU001 — cross-tenant leak (from M1-review-F001)\n- **Assertion:** A002\n"
                      "- **Found by:** mission-reviewer, `analytics/service.py:3` — no tenant filter\n- **Severity:** high\n"
                      "- **Cluster:** C01 — repository queries missing the tenant predicate\n- **Blocking:** yes\n"
                      "- **Disposition:** accept as known limitation — beyond the proof budget\n\n## FU002", raw)
        self.assertIn("## FU002 — lint debt (from M1-scrutiny)\n- **Assertion:** —\n- **Found by:** mission-validator-scrutiny\n"
                      "- **Severity:** low\n- **Cluster:** C02\n- **Blocking:** no\n- **Disposition:** waived by the negotiate step, not in scope\n", raw)
        self.assertTrue(raw.startswith("# Follow-ups — demo\n\n## FU001"))
        self.assertTrue(raw.endswith("beyond the proof budget\n"))
        fus = files.read_followups(self.m)
        self.assertEqual([f.id for f in fus], ["FU001", "FU002", "FU003"])
        self.assertEqual((fus[0].cluster, fus[0].cluster_label, fus[0].assertion, fus[0].source, fus[0].blocking, fus[0].repair_as),
                         ("C01", "repository queries missing the tenant predicate", "A002", "M1-review-F001", True, None))
        self.assertEqual((fus[1].cluster, fus[1].assertion, fus[1].blocking, fus[1].severity), ("C02", None, False, "low"))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        # a fresh file gets the template header; a repair needs the feature id it repairs as
        (self.m / "followups.md").unlink()
        self.assertEqual(files.append_followups(self.m, [self.entry()]), ["FU001"])
        self.assertTrue(files.read_text(self.m / "followups.md").startswith("# Follow-ups — demo\n\n## FU001 —"))
        with self.assertRaises(ValueError):
            files.append_followups(self.m, [self.entry(disposition="repair")])

    def test_append_feature_and_route_pass_check(self):
        fid = files.append_feature(self.m, "M1", "tenancy filter", ["A002"], ["analytics/service.py"],
                                   "make test-unit", "the summary query", "C01 (FU001) of F001")
        self.assertEqual(fid, "F004")
        files.append_followups(self.m, [self.entry(disposition="repair", repair_as=fid, why="")])
        self.assertEqual(files.read_followups(self.m)[0].repair_as, "F004")
        self.assertIn("- **Disposition:** repair as F004\n", files.read_text(self.m / "followups.md"))
        feats = {f.id: f for f in files.read_features(self.m)}
        self.assertEqual((feats["F004"].milestone, feats["F004"].repairs, feats["F004"].status, feats["F004"].assertions,
                          feats["F004"].files, feats["F004"].out_of_scope), ("M1", ["F001"], "pending", ["A002"],
                                                                             ["analytics/service.py"], "the summary query"))
        self.assertEqual(feats["F001"].repairs, [])
        raw = files.read_text(self.m / "features.md")
        self.assertIn("- **Depends on:** F001\n- **Status:** pending\n\n### F004 — tenancy filter\n- **Assertions:** A002\n"
                      "- **Files:** `analytics/service.py`\n- **Procedures:** make test-unit\n- **Depends on:** —\n"
                      "- **Out of scope:** the summary query\n- **Repairs:** C01 (FU001) of F001\n- **Status:** pending\n\n## M2 — second\n", raw)
        # the contract routes A002 -> F001, F002 only: check.sh rejects the claim until the row is re-routed
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 1)
        self.assertIn("F004 claims A002", out)
        self.assertTrue(files.route_assertion(self.m, "A002", "F004"))
        self.assertFalse(files.route_assertion(self.m, "A002", "F004"))
        self.assertFalse(files.route_assertion(self.m, "A009", "F004"))
        self.assertEqual(files.read_contract(self.m)[1].features, ["F001", "F002", "F004"])
        self.assertIn("| A002 | Tenant A never sees tenant B | structural | F001, F002, F004 | unproven |", files.read_text(self.m / "contract.md"))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        # ids never reuse: the next feature is F005 even in the last milestone; a missing milestone is refused
        self.assertEqual(files.append_feature(self.m, "M2", "x", ["A003"], [], "", "", "C02 (FU002) of F003"), "F005")
        self.assertTrue(files.read_text(self.m / "features.md").endswith("- **Repairs:** C02 (FU002) of F003\n- **Status:** pending\n"))
        with self.assertRaises(files.MissionFileError):
            files.append_feature(self.m, "M9", "x", [], [], "", "", "")

    def test_remove_open_issues_restores_none(self):
        files.add_open_issues(self.m, ["F001 handoff: stack down", "F001 handoff: port busy"])
        self.assertEqual(files.remove_open_issues(self.m, ["F001 handoff: stack down "]), ["F001 handoff: stack down"])
        self.assertEqual(files.read_state(self.m).open_issues, ["F001 handoff: port busy"])
        self.assertNotIn("- none", files.read_text(self.m / "state.md"))
        self.assertEqual(files.remove_open_issues(self.m, ["nothing like this"]), [])
        self.assertEqual(files.remove_open_issues(self.m, ["F001 handoff: port busy"]), ["F001 handoff: port busy"])
        self.assertEqual(files.read_state(self.m).open_issues, [])
        self.assertIn("## Open issues — these block the next feature\n- none\n\n## Standing constraints", files.read_text(self.m / "state.md"))


class ProveTests(Fixture):
    def test_prove_never_downgrades_or_reattributes(self):
        files.claim_assertions(self.m, ["A001"])
        self.assertEqual(files.prove_assertions(self.m, {"A001": "validation/M1-review-F001.md", "A002": "validation/M1-review-F002.md"}),
                         ["A001", "A002"])
        rows = {r.id: r for r in files.read_contract(self.m)}
        self.assertEqual((rows["A001"].status, rows["A001"].evidence), ("proven", "validation/M1-review-F001.md"))
        self.assertEqual((rows["A002"].status, rows["A003"].status), ("proven", "unproven"))
        self.assertIn("| A001 | Omitting the window equals the whole day | structural | F001 | proven | validation/M1-review-F001.md |",
                      files.read_text(self.m / "contract.md"))
        # a later round cannot move it, re-attribute it, or claim it back down
        self.assertEqual(files.prove_assertions(self.m, {"A001": "validation/M1-review-F001-r2.md"}), [])
        self.assertEqual(files.claim_assertions(self.m, ["A001"]), [])
        self.assertEqual(files.read_contract(self.m)[0].evidence, "validation/M1-review-F001.md")
        self.assertEqual(files.prove_assertions(self.m, {"A009": "x"}), [])

    def test_reopen_is_the_one_move_down_and_only_from_proven(self):
        files.prove_assertions(self.m, {"A001": "validation/M1-review-F001.md"})
        files.claim_assertions(self.m, ["A002"])
        # a claimed or unproven row is not re-opened (its claim was never proof); an unknown id is ignored
        self.assertEqual(files.reopen_assertions(self.m, {"A002": "validation/M2-review-F003.md", "A003": "x", "A009": "y"}), [])
        self.assertEqual(files.reopen_assertions(self.m, {"A001": "validation/M2-review-F003.md"}), ["A001"])
        rows = {r.id: r for r in files.read_contract(self.m)}
        self.assertEqual((rows["A001"].status, rows["A001"].evidence, rows["A002"].status, rows["A003"].status),
                         ("claimed", "validation/M2-review-F003.md", "claimed", "unproven"))
        self.assertIn("| A001 | Omitting the window equals the whole day | structural | F001 | claimed | validation/M2-review-F003.md |",
                      files.read_text(self.m / "contract.md"))
        self.assertEqual(files.reopen_assertions(self.m, {"A001": "again"}), [])
        # a later round proves it again, with its own evidence
        self.assertEqual(files.prove_assertions(self.m, {"A001": "validation/M2-review-F004-r2.md"}), ["A001"])
        self.assertEqual(files.read_contract(self.m)[0].evidence, "validation/M2-review-F004-r2.md")
        self.assertEqual(check_sh(self.m)[0], 0)


REVIEW = """# Review F001

## Assertion verdicts
| ID | Verdict | Evidence / breaking case |
|---|---|---|
| A001 | satisfied | `analytics/service.py:3` |
| A002 | **not satisfied** | tenant B rows come back for tenant A |
| A003 | cannot tell from the diff | needs a live run |
| A004 | Satisfied — `tests/unit/test_a.py::test_a` | |
| A005 | probably fine | — |

## Design conformance
| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |
|---|---|---|
| D001 | conforms | pure |

## Defects
| Severity | file:line | What breaks, and the concrete input that breaks it |
|---|---|---|
| high | analytics/service.py:3 | no tenant filter; input: tenant A, rows of B |

## Not covered by any assertion
A002 is probably fine in practice.
"""
BEHAVIOR = """## Assertion results
| ID | Verdict | Evidence |
| A012 | proven | call 8f3a, turn 4 |
| A013 | FAILED | call 8f3a, turn 7 |
| A014 | not reached | budget cap hit |
| A015 | ✅ Proven | screenshot |
| A016 | maybe | |

## Defects
none
"""
SCRUTINY = """## Commands
| Command | Exit code | Duration |
|---|---|---|
| make test-unit | 0 | 12s |
| ruff check . | 1 | 2s |

## Failures
tests/unit/test_b.py::test_b — AssertionError: 2 != 3

## Coverage of milestone assertions
| Assertion | Test that exercises it | Result |
|---|---|---|
| A001 | tests/unit/test_a.py::test_a | pass |
"""


class VerdictTests(unittest.TestCase):
    def test_reviewer(self):
        self.assertEqual(verdicts.parse_reviewer(REVIEW), {
            "A001": "satisfied", "A002": "not satisfied", "A003": "cannot tell", "A004": "satisfied", "A005": "cannot tell"})
        self.assertEqual(verdicts.parse_reviewer("no table here"), {})
        # `satisfied` is a substring of `not satisfied`: the order of the two tests decides every failure
        self.assertEqual(verdicts.reviewer_verdict("satisfied for tenant A, not satisfied for tenant B"), "not satisfied")
        self.assertEqual(verdicts.reviewer_verdict("NOT SATISFIED"), "not satisfied")
        self.assertEqual(verdicts.reviewer_verdict("`satisfied`"), "satisfied")
        self.assertEqual(verdicts.reviewer_verdict("unsatisfied"), "cannot tell")
        # a level-3 heading and a table with no separator row still parse; the header is not a row
        text = REVIEW.replace("## Assertion verdicts", "### Assertion Verdicts (final)").replace("|---|---|---|\n| A001", "| A001")
        self.assertEqual(verdicts.parse_reviewer(text)["A002"], "not satisfied")
        self.assertNotIn("ID", verdicts.parse_reviewer(text))

    def test_behavior_and_scrutiny(self):
        self.assertEqual(verdicts.parse_behavior(BEHAVIOR), {
            "A012": "proven", "A013": "FAILED", "A014": "not reached", "A015": "proven", "A016": "not reached"})
        self.assertEqual(verdicts.behavior_verdict("Failed at turn 3"), "FAILED")
        self.assertEqual(verdicts.behavior_verdict("not proven"), "not reached")
        s = verdicts.parse_scrutiny(SCRUTINY)
        self.assertEqual(s["commands"], [{"command": "make test-unit", "exit": 0, "duration": "12s"},
                                         {"command": "ruff check .", "exit": 1, "duration": "2s"}])
        self.assertTrue(s["failures"].startswith("tests/unit/test_b.py::test_b"))
        self.assertEqual(verdicts.parse_scrutiny("## Commands\n| Command | Exit code | Duration |\n|---|---|---|\n\n## Failures\nnone\n"),
                         {"commands": [], "failures": "none"})

    def test_an_escaped_pipe_is_one_cell(self):
        # GFM: `\|` inside a cell is the cell's own pipe, so a piped command is one Command cell
        text = SCRUTINY.replace("| ruff check . | 1 | 2s |", "| pytest x 2>&1 \\| tail -20 | 1 | 3s |")
        s = verdicts.parse_scrutiny(text)
        self.assertEqual(s["commands"][1], {"command": "pytest x 2>&1 | tail -20", "exit": 1, "duration": "3s"})
        self.assertEqual(s["commands"][0], {"command": "make test-unit", "exit": 0, "duration": "12s"})
        self.assertEqual(verdicts.table_rows("| a \\| b | c |\n|---|---|\n| `x \\| y` | 1 |"), [["`x | y`", "1"]])
        # a reviewer's evidence cell that quotes a pipe keeps the verdict in its own column
        review = REVIEW.replace("| A001 | satisfied | `analytics/service.py:3` |",
                                "| A001 | satisfied | `grep -c tenant analytics/service.py \\| head -1` prints 1 |")
        self.assertEqual(verdicts.parse_reviewer(review), verdicts.parse_reviewer(REVIEW))
        rows = verdicts.table_rows(files.section(review, "Assertion verdicts"))
        self.assertEqual(rows[0], ["A001", "satisfied", "`grep -c tenant analytics/service.py | head -1` prints 1"])
        # the contract's splitter is untouched: it mirrors check.sh, where no cell escapes a pipe
        self.assertEqual(files.table_cells("| a \\| b | c |"), ["a \\", "b", "c"])


class LatestVerdictTests(Fixture):
    def test_latest_verdict_wins_per_validator(self):
        journal.append(self.m, "verdict", validator="mission-reviewer", feature="F001", milestone="M1", round=1,
                       assertions={"A001": "satisfied", "A002": "not satisfied"}, file="validation/M1-review-F001.md")
        journal.append(self.m, "verdict", validator="mission-reviewer", feature="F002", milestone="M1", round=1,
                       assertions={"A002": "cannot tell"}, file="validation/M1-review-F002.md")
        journal.append(self.m, "verdict", validator="mission-validator-behavior", milestone="M1", round=1,
                       assertions={"A002": "proven"}, file="validation/M1-behavior.md")
        journal.append(self.m, "verdict", validator="mission-reviewer", feature="F004", milestone="M1", round=2,
                       assertions={"A002": "satisfied"}, file="validation/M1-review-F004-r2.md")
        journal.append(self.m, "verdict", validator="mission-reviewer", feature="F003", milestone="M2", round=1,
                       assertions={"A003": "satisfied"}, file="validation/M2-review-F003.md")
        journal.append(self.m, "verdict", validator="mission-validator-scrutiny", milestone="M1", round=1, assertions="n/a")
        v = verdicts.latest_verdicts(self.m, "M1")
        self.assertEqual(v["reviews"], {"A001": ("satisfied", "validation/M1-review-F001.md"),
                                        "A002": ("satisfied", "validation/M1-review-F004-r2.md")})
        self.assertEqual(v["behavior"], {"A002": ("proven", "validation/M1-behavior.md")})
        self.assertEqual(verdicts.latest_verdicts(self.m, "M2")["reviews"], {"A003": ("satisfied", "validation/M2-review-F003.md")})
        self.assertEqual(verdicts.latest_verdicts(self.m, "M3"), {"reviews": {}, "behavior": {}})
        # one round whole: every verdict on an assertion, in journal order, with the feature it came from
        rv = verdicts.round_verdicts(self.m, "M1", 1)
        self.assertEqual(rv["reviews"], {"A001": [("F001", "satisfied", "validation/M1-review-F001.md")],
                                         "A002": [("F001", "not satisfied", "validation/M1-review-F001.md"),
                                                  ("F002", "cannot tell", "validation/M1-review-F002.md")]})
        self.assertEqual(rv["behavior"], {"A002": [(None, "proven", "validation/M1-behavior.md")]})
        self.assertEqual(verdicts.round_verdicts(self.m, "M1", 2),
                         {"reviews": {"A002": [("F004", "satisfied", "validation/M1-review-F004-r2.md")]}, "behavior": {}})
        self.assertEqual(verdicts.round_verdicts(self.m, "M1", 3), {"reviews": {}, "behavior": {}})
        self.assertEqual(verdicts.round_verdicts(self.m, "M2", 1)["reviews"], {"A003": [("F003", "satisfied", "validation/M2-review-F003.md")]})


NEGOTIATE_OK = {"findings": [
    {"title": "leak", "assertion": "A002", "found_by": "mission-reviewer", "where": "analytics/service.py:3", "severity": "high",
     "cluster": "C01", "cluster_label": "tenant predicate", "blocking": True, "disposition": "repair", "why": "defect"},
    {"title": "debt", "assertion": None, "found_by": "mission-validator-scrutiny", "where": "", "severity": "low",
     "cluster": "C02", "cluster_label": "lint", "blocking": False, "disposition": "accept", "why": "beyond max"}],
    "repairs": [{"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
                 "procedures": "make test-unit", "out_of_scope": "the summary query"}],
    "contract_wrong": False, "reason": "one defect"}
TRIAGE_OK = {"resolutions": [
    {"issue": 1, "disposition": "resolved", "why": "the stack was down; the tests ran on retry", "followup": None, "repair": None},
    {"issue": 2, "disposition": "defer", "why": "cosmetic", "followup": {"title": "spacing", "assertion": None, "severity": "low",
                                                                          "cluster": "C09", "cluster_label": "triage", "blocking": False}},
    {"issue": 3, "disposition": "repair", "why": "real", "followup": {"title": "leak", "assertion": "A002", "severity": "high",
                                                                       "cluster": "C01", "cluster_label": "tenant", "blocking": True},
     "repair": {"title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"], "procedures": "make test-unit"}}]}


class JudgmentTests(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(judgment.extract_json('Here you go:\n```json\n{"a": 1}\n```\nthanks'), {"a": 1})
        self.assertEqual(judgment.extract_json('```\n{"a": [1, {"b": 2}]}\n```'), {"a": [1, {"b": 2}]})
        # unfenced, with a brace inside a string and prose on both sides
        self.assertEqual(judgment.extract_json('Sure. {"reason": "x}", "n": {"k": 1}} -- done'), {"reason": "x}", "n": {"k": 1}})
        # a fence that holds something other than the object does not hide the object after it
        self.assertEqual(judgment.extract_json('```json\n[1]\n```\nthen {"a": 2}'), {"a": 2})
        for bad in ("not json", "", "[1, 2]", "{\"a\": 1", "```json\n[1]\n```"):
            with self.assertRaises(judgment.JudgmentError):
                judgment.extract_json(bad)
        try:
            judgment.extract_json("{oops}")
        except judgment.JudgmentError as e:
            self.assertIn("Expecting property name", str(e))
        # a truncated reply is reported as the delimiter it is missing, not as "no JSON object"
        with self.assertRaises(judgment.JudgmentError) as cm:
            judgment.extract_json('{"findings": [{"title": "x"')
        self.assertIn("the first {...} span: Expecting ',' delimiter", str(cm.exception))

    def test_validate_negotiate(self):
        self.assertEqual(judgment.validate_negotiate(NEGOTIATE_OK), [])
        self.assertEqual(judgment.validate_negotiate({"findings": [], "repairs": [], "contract_wrong": False}), [])
        self.assertEqual(judgment.validate_negotiate([]), ["the reply is not a JSON object"])
        bad = json.loads(json.dumps(NEGOTIATE_OK))
        bad["findings"][0]["severity"] = "critical"
        bad["findings"][0]["blocking"] = "yes"
        bad["findings"][1]["assertion"] = "F001"
        del bad["repairs"][0]["assertions"]
        bad["repairs"].append({"cluster": "C03", "title": "x", "assertions": ["A001"], "files": []})
        bad["contract_wrong"] = True
        del bad["reason"]
        problems = judgment.validate_negotiate(bad)
        for want in ("findings[0]: 'severity' must be one of high, medium, low", "findings[0]: 'blocking' must be bool",
                     "findings[1]: assertion 'F001' is not an A00n id", "repairs[0]: missing 'assertions'",
                     "a repair names cluster C03, but no finding", "contract_wrong is true but reason is empty"):
            self.assertTrue(any(want in p for p in problems), (want, problems))
        # a finding dispositioned repair without its repair, and two repairs for one cluster
        bad = json.loads(json.dumps(NEGOTIATE_OK))
        bad["repairs"] = []
        self.assertEqual(judgment.validate_negotiate(bad), ["a finding in cluster C01 is dispositioned repair, but no repair names that cluster"])
        bad = json.loads(json.dumps(NEGOTIATE_OK))
        bad["repairs"].append(dict(bad["repairs"][0]))
        self.assertTrue(any("one cluster, one repair feature" in p for p in judgment.validate_negotiate(bad)))

    def test_validate_triage(self):
        self.assertEqual(judgment.validate_triage(TRIAGE_OK), [])
        bad = json.loads(json.dumps(TRIAGE_OK))
        bad["resolutions"][0]["issue"] = True
        bad["resolutions"][1]["followup"] = None
        del bad["resolutions"][2]["repair"]
        bad["resolutions"].append({"issue": 2, "disposition": "escalate"})
        bad["resolutions"].append({"issue": 0, "disposition": "ignore"})
        problems = judgment.validate_triage(bad)
        for want in ("resolutions[0]: 'issue' must be int", "resolutions[1]: disposition defer needs a followup",
                     "resolutions[2]: disposition repair needs a repair", "resolutions[3]: issue 2 already has a resolution",
                     "resolutions[4]: issue 0 is not a 1-based index", "resolutions[4]: 'disposition' must be one of"):
            self.assertTrue(any(want in p for p in problems), (want, problems))
        self.assertEqual(judgment.validate_triage({}), ["reply: missing 'resolutions'"])
        # two resolutions that both omit `issue` are two problems, never a crash on formatting None
        self.assertEqual(judgment.validate_triage({"resolutions": [{"disposition": "resolved"}, {"disposition": "resolved"}]}),
                         ["resolutions[0]: missing 'issue'", "resolutions[1]: missing 'issue'"])

    def test_unknown_assertion_problems(self):
        # one walk over both shapes; only well-formed ids the contract lacks, once per field --
        # a malformed value is the schema check's complaint, and a null assertion is no id
        known = {"A001", "A002", "A003"}
        neg = {"findings": [{"assertion": "A099"}, {"assertion": None}, {"assertion": "A001"}, "not an object"],
               "repairs": [{"assertions": ["A002", "A098", "A098", "bogus", 7]}, {"assertions": "A097"}, {}],
               "contract_wrong": False}
        self.assertEqual(judgment.unknown_assertion_problems(neg, known),
                         ["findings[0]: A099 is not in the contract", "repairs[0]: A098 is not in the contract"])
        tri = {"resolutions": [{"issue": 1, "followup": {"assertion": "A096"}, "repair": {"assertions": ["A003", "A095"]}},
                               {"issue": 2, "followup": None, "repair": None}, 3]}
        self.assertEqual(judgment.unknown_assertion_problems(tri, known),
                         ["resolutions[0].followup: A096 is not in the contract", "resolutions[0].repair: A095 is not in the contract"])
        self.assertEqual(judgment.unknown_assertion_problems({"findings": "x", "repairs": None, "resolutions": 4}, known), [])
        self.assertEqual(judgment.unknown_assertion_problems("nope", known), [])
        self.assertEqual(judgment.unknown_assertion_problems(neg, known | {"A099", "A098"}), [])
        self.assertEqual(judgment.unknown_assertion_problems(NEGOTIATE_OK, known), [])
        self.assertEqual(judgment.unknown_assertion_problems(TRIAGE_OK, known), [])
        # what the negotiate step hands run_judgment: the schema's complaints, then the contract's
        both = judgment.validate_negotiate(neg) + judgment.unknown_assertion_problems(neg, known)
        self.assertIn("findings[3]: not an object", both)
        self.assertTrue(any("'assertions' contains 'bogus'" in p for p in both), both)
        self.assertEqual(both[-2:], ["findings[0]: A099 is not in the contract", "repairs[0]: A098 is not in the contract"])


# ---------------------------------------------------------------- prep (D3)

def make_ctx(mission_dir, checkout, cfg=None, harness="stub"):
    from missions.steps import Context
    return Context(mission_dir=mission_dir, checkout=checkout, plugin=PLUGIN, cfg=cfg if cfg is not None else {"roles": {}},
                   adapter=None, run_id="r1", harness=harness, log=lambda line: None)


DRIVER_ENV = {
    "PATH": "/bin", "HOME": "/h", "LC_ALL": "C", "XDG_RUNTIME_DIR": "/r", "MISSIONS_TEST": "1", "HTTPS_PROXY": "p",
    "TMPDIR": "/t", "GH_TOKEN": "t", "GITHUB_TOKEN": "t", "MY_SECRET": "s", "FOO_TOKEN": "f", "AWS_ACCESS_KEY": "k",
    "DB_PASSWORD": "p", "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "KEEP_ME": "1", "MYAPP_URL": "u",
    "MYAPP_KEY": "k", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "GIT_CONFIG_GLOBAL": "/elsewhere",
    "GIT_CONFIG_COUNT": "9", "SSH_AUTH_SOCK": "/s", "GIT_SSH_COMMAND": "ssh -i key", "GIT_ASKPASS": "/ask",
    "MISSIONS_PUSH_TOKEN": "z", "PYTHONPATH": "/x", "GH_CONFIG_DIR": "/elsewhere/gh", "GH_ENTERPRISE_TOKEN": "e",
    "GITHUB_ENTERPRISE_TOKEN": "e",
}


class PrepEnvTests(Fixture):
    def env(self, role="worker", harness="stub", passthrough=(), src=DRIVER_ENV):
        return prep.build_env(self.m, self.m / "runs" / "F001#1", role, "F001", "F001#1", "implementing", harness,
                              branch="mission/demo", feature_files=["analytics/service.py", "tests/unit/test_a.py"],
                              passthrough=list(passthrough), base_env=src)

    def test_whitelist_keeps_drops_and_passthrough(self):
        env = self.env(passthrough=["KEEP_ME", "MYAPP_*", "GH_TOKEN", "CLAUDE_CODE_*", "GIT_CONFIG_COUNT", "GH_CONFIG_DIR",
                                    "GH_ENTERPRISE_TOKEN"])
        for k in ("PATH", "HOME", "LC_ALL", "XDG_RUNTIME_DIR", "MISSIONS_TEST", "HTTPS_PROXY", "TMPDIR",
                  "KEEP_ME", "MYAPP_URL", "MYAPP_KEY"):
            self.assertIn(k, env, k)
        # not on any list: gone, whatever the name pattern
        for k in ("MY_SECRET", "FOO_TOKEN", "AWS_ACCESS_KEY", "DB_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PYTHONPATH"):
            self.assertNotIn(k, env, k)
        # the never-list beats the passthrough
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "SSH_AUTH_SOCK", "MISSIONS_PUSH_TOKEN",
                  "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
            self.assertNotIn(k, env, k)
        # gh reads its hosts.yml from GH_CONFIG_DIR: ours, empty, never the operator's (~/.config/gh or /elsewhere)
        self.assertEqual(env["GH_CONFIG_DIR"], str(self.m / "githooks" / "gh"))
        # ours replace the driver's, never inherit them
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], str(self.m / "githooks" / "gitconfig"))
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], str(self.m / "githooks"))
        self.assertEqual((env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]), ("credential.helper", ""))
        self.assertTrue(env["GIT_SSH_COMMAND"].endswith("/githooks/no-credentials"))
        self.assertEqual(env["GIT_ASKPASS"], env["GIT_SSH_COMMAND"])
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["MISSIONS_FILES"], "analytics/service.py,tests/unit/test_a.py")
        self.assertEqual((env["MISSIONS_BRANCH"], env["MISSIONS_ROLE"], env["MISSIONS_TASK"]), ("mission/demo", "worker", "F001#1"))
        self.assertTrue(env["MISSIONS_BIN"].endswith("/bin/missions"))
        self.assertNotIn("MISSIONS_FILES", self.env(role="reviewer"))

    def test_harness_lists(self):
        claude = self.env(harness="claude")
        self.assertIn("ANTHROPIC_API_KEY", claude)
        self.assertNotIn("OPENAI_API_KEY", claude)
        codex = self.env(harness="codex")
        self.assertIn("OPENAI_API_KEY", codex)
        self.assertNotIn("ANTHROPIC_API_KEY", codex)
        stub = self.env(harness="stub")
        self.assertNotIn("ANTHROPIC_API_KEY", stub)
        self.assertNotIn("OPENAI_API_KEY", stub)

    def test_base_build_env_is_the_same_thing(self):
        from missions.adapters import base
        env = base.build_env(self.m, self.m / "runs" / "F001#1", "worker", "F001", "F001#1", "implementing", "stub",
                             base_env=DRIVER_ENV, passthrough=["KEEP_ME"])
        self.assertIn("KEEP_ME", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")


class GitFilesTests(RepoFixture):
    """The gitconfig, the hooks and the no-credentials script as git actually applies them under
    the built environment -- a real checkout, real commits, a real (local) push."""

    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.m, self.repo)
        feats = files.read_features(self.m)
        self.req = RunRequest(role="worker", task="F001#1", prompt_path=self.m / "p.md", cwd=self.repo,
                              env=prep.build_env(self.m, self.m / "runs" / "F001#1", "worker", "F001", "F001#1", "implementing",
                                                 "stub", branch="mission/demo", feature_files=feats[0].files,
                                                 base_env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"),
                                                           "GH_TOKEN": "x"}),
                              timeout_s=10, budget_usd=None, model=None, effort=None, read_only=False,
                              output_path=self.m / "o.md", run_dir=self.m / "runs" / "F001#1", feature="F001", mission_dir=self.m)
        prep.prepare(self.ctx, self.req)

    def wgit(self, *args, env=None, **kw):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=env or self.req.env, capture_output=True, text=True, **kw)

    def test_gitconfig_content(self):
        text = files.read_text(self.m / "githooks" / "gitconfig")
        self.assertIn("[user]\n\tname = driver\n\temail = driver@test\n", text)
        self.assertIn("[credential]\n\thelper =\n", text)
        # no identity anywhere (an empty global config, no system one, a checkout that is no repo): the fallback names
        other = self.tmp / "nothing"
        other.mkdir()
        (self.tmp / "empty-gitconfig").write_text("", encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ.update(GIT_CONFIG_GLOBAL=str(self.tmp / "empty-gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        try:
            prep.write_gitconfig(self.m, other)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        text = files.read_text(self.m / "githooks" / "gitconfig")
        self.assertIn("name = missions-worker\n", text)
        self.assertIn("email = worker@missions.invalid\n", text)

    def test_hook_scripts_text(self):
        h = prep.push_hash("tok")
        worker = prep.hook_scripts("worker", "F001#1", h)
        chain = 'orig=$(git config --local core.hooksPath); [ -n "$orig" ] || orig="$(git rev-parse --git-common-dir)/hooks"'
        for name in ("pre-commit", "commit-msg", "pre-push"):
            self.assertTrue(worker[name].startswith("#!/bin/bash\n"), name)
            self.assertIn(chain, worker[name], name)
            self.assertIn('"$orig/%s" "$@" </dev/stdin || exit 1' % name, worker[name], name)
        self.assertIn(h, worker["pre-push"])
        self.assertIn("sha256sum", worker["pre-push"])
        self.assertIn("never push", worker["pre-push"])
        self.assertIn("$MISSIONS_BRANCH", worker["pre-commit"])
        self.assertIn('"$MISSIONS_FEATURE:"*', worker["commit-msg"])
        self.assertTrue(worker["post-checkout"].endswith(
            chain + '\nif [ -x "$orig/post-checkout" ]; then "$orig/post-checkout" "$@" </dev/stdin || exit 1; fi\nexit 0\n'))
        reviewer = prep.hook_scripts("reviewer", "review-F001#1", h)
        self.assertIn("a reviewer run does not commit", reviewer["pre-commit"])
        self.assertIn("a reviewer run does not commit", reviewer["commit-msg"])
        self.assertNotIn(chain, reviewer["pre-commit"])
        self.assertIn(h, reviewer["pre-push"])
        for name in ("pre-commit", "commit-msg", "pre-push", "no-credentials"):
            self.assertTrue(os.access(self.m / "githooks" / name, os.X_OK), name)
        self.assertIn("MISSIONS_FILES", self.req.env)
        self.assertNotIn("GH_TOKEN", self.req.env)

    def test_env_names_record_is_what_the_child_got(self):
        """env-names.txt is written where the environment is final, not where prep built it: a
        name an adapter adds through extra_env is in the record, so a credential arriving that
        way could not hide from the traces that read this file."""
        from missions.adapters import base as adapters_base
        adapters_base.run_process(["/bin/true"], self.req, extra_env={"ADAPTER_ADDED": "1"})
        names = files.read_text(self.req.run_dir / "env-names.txt").split()
        self.assertIn("MISSIONS_FILES", names)      # prep put this in
        self.assertIn("ADAPTER_ADDED", names)       # the adapter put this in, after prep
        self.assertNotIn("GH_TOKEN", names)         # the whitelist kept this out
        self.assertFalse(any("=" in n for n in names))

    def test_env_resolves_our_config(self):
        self.git("config", "credential.helper", "store")     # the repo-local helper is reset by the env
        self.assertEqual(self.wgit("config", "credential.helper").stdout.strip(), "")
        self.assertEqual(self.wgit("config", "core.hooksPath").stdout.strip(), str(self.m / "githooks"))
        self.assertEqual(self.wgit("config", "user.email").stdout.strip(), "driver@test")

    def test_commit_rules(self):
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# x\n")
        self.wgit("add", "analytics/service.py")
        res = self.wgit("commit", "-qm", "wrong subject")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('must start with "F001:"', res.stderr)
        res = self.wgit("commit", "-qm", "F001: fine")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>"), "driver <driver@test>")
        # outside the Files line: a warning on stderr, the commit still lands
        (self.repo / "analytics" / "other.py").write_text("y\n", encoding="utf-8")
        self.wgit("add", "analytics/other.py")
        res = self.wgit("commit", "-qm", "F001: other")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("staged outside F001's Files: analytics/other.py", res.stderr)
        # off the mission branch: refused
        self.git("checkout", "-qb", "side")
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# z\n")
        self.wgit("add", "analytics/service.py")
        res = self.wgit("commit", "-qm", "F001: on side")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("commits go on the mission branch mission/demo, not side", res.stderr)
        self.git("checkout", "-q", "--detach")
        res = self.wgit("commit", "-qm", "F001: detached")
        self.assertIn("not a detached HEAD", res.stderr)

    def test_push_refused_without_the_token(self):
        res = self.wgit("push", "origin", "HEAD:refs/heads/mission/demo")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("workers never push; the driver pushes in phase pr", res.stderr)
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.tmp / "r" / "origin.git"), "show-ref", "mission/demo"],
                                           capture_output=True).returncode, 0)
        # the driver's own push subprocess (not built yet) will carry the plain token; a wrong one is refused
        env = dict(self.req.env, MISSIONS_PUSH_TOKEN="not-it")
        self.assertNotEqual(self.wgit("push", "origin", "HEAD:refs/heads/mission/demo", env=env).returncode, 0)
        env = dict(self.req.env, MISSIONS_PUSH_TOKEN=self.ctx.push_token)
        res = self.wgit("push", "origin", "HEAD:refs/heads/mission/demo", env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(subprocess.run(["git", "-C", str(self.tmp / "r" / "origin.git"), "show-ref", "mission/demo"],
                                        capture_output=True).returncode, 0)

    def test_repo_hook_runs_first_and_keeps_its_exit_code(self):
        hooks = self.repo / ".git" / "hooks"
        marker = self.tmp / "ran"
        (hooks / "pre-commit").write_text("#!/bin/bash\necho ran >> %s\nexit 1\n" % marker, encoding="utf-8")
        os.chmod(hooks / "pre-commit", 0o755)
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# x\n")
        self.wgit("add", "analytics/service.py")
        res = self.wgit("commit", "-qm", "F001: fine")
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(marker.exists())
        self.assertNotIn("missions:", res.stderr)      # the repo's verdict, not ours
        (hooks / "pre-commit").write_text("#!/bin/bash\necho ran >> %s\nexit 0\n" % marker, encoding="utf-8")
        self.assertEqual(self.wgit("commit", "-qm", "F001: fine").returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8").count("ran"), 2)
        # a repo that sets its own core.hooksPath: that directory is the original, not .git/hooks
        custom = self.tmp / "customhooks"
        custom.mkdir()
        (custom / "commit-msg").write_text("#!/bin/bash\necho custom >> %s\nexit 0\n" % marker, encoding="utf-8")
        os.chmod(custom / "commit-msg", 0o755)
        (custom / "post-commit").write_text("#!/bin/bash\necho post >> %s\n" % marker, encoding="utf-8")
        os.chmod(custom / "post-commit", 0o755)
        self.git("config", "core.hooksPath", str(custom))
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# y\n")
        self.wgit("add", "analytics/service.py")
        self.assertEqual(self.wgit("commit", "-qm", "F001: again").returncode, 0)
        self.assertIn("custom", marker.read_text(encoding="utf-8"))
        self.assertIn("post", marker.read_text(encoding="utf-8"))     # a hook we add nothing to still reaches the repo's
        self.assertNotEqual(self.wgit("commit", "--allow-empty", "-qm", "nope").returncode, 0)

    def test_merge_and_rebase_refused_and_gh_has_no_config(self):
        gh = self.m / "githooks" / "gh"
        self.assertTrue(gh.is_dir())
        self.assertEqual(list(gh.iterdir()), [])
        self.assertEqual(self.req.env["GH_CONFIG_DIR"], str(gh))
        for role in ("worker", "reviewer", "scrutiny"):
            hooks = prep.hook_scripts(role, "t#1", "h")
            self.assertIn("a worker never merges; the driver merges main in phase pr (#10)", hooks["pre-merge-commit"])
            self.assertIn("a worker never rebases", hooks["pre-rebase"])
            for name in ("pre-merge-commit", "pre-rebase"):
                self.assertNotIn("orig=", hooks[name], name)          # a refusal chains nothing
                self.assertNotIn(name, prep.PASSTHROUGH_HOOKS)
        # a side branch with one commit (plain git: no hooks), then the merge under the run's env
        self.git("checkout", "-qb", "side")
        (self.repo / "analytics" / "side.py").write_text("s\n", encoding="utf-8")
        self.git("add", "analytics/side.py")
        self.git("commit", "-qm", "side: work")
        self.git("checkout", "-q", "mission/demo")
        res = self.wgit("merge", "--no-ff", "-m", "F001: merge side", "side")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("a worker never merges", res.stderr)
        # git leaves the refused merge staged with MERGE_HEAD; the worker aborts it and the tree is back
        self.assertTrue((self.repo / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.wgit("merge", "--abort").returncode, 0)
        self.assertEqual(self.git("log", "--merges", "--oneline"), "")
        self.assertEqual(files.dirty_paths(self.repo), [])
        res = self.wgit("rebase", "side")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("a worker never rebases", res.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "mission/demo"))
        self.assertEqual(self.git("branch", "--show-current"), "mission/demo")


class StubResolutionTests(unittest.TestCase):
    def test_script_order(self):
        d = Path(tempfile.mkdtemp())
        try:
            for n in ("reviewer-F001.sh", "F001.sh", "reviewer.sh", "negotiate.sh", "judgment.sh", "worker.sh"):
                (d / n).write_text("", encoding="utf-8")
            a = StubAdapter({"script_dir": str(d)})

            def req(role, feature="", step=""):
                return RunRequest(role=role, task="t", prompt_path=d, cwd=d, env={}, timeout_s=1, budget_usd=None, model=None,
                                  effort=None, read_only=False, output_path=d, feature=feature, step=step)
            self.assertEqual(a.script_for(req("worker", "F001")).name, "F001.sh")
            self.assertEqual(a.script_for(req("worker", "F002")).name, "worker.sh")
            self.assertEqual(a.script_for(req("reviewer", "F001", "reviewer")).name, "reviewer-F001.sh")
            self.assertEqual(a.script_for(req("reviewer", "F002", "reviewer")).name, "reviewer.sh")   # never F002.sh
            (d / "F002.sh").write_text("", encoding="utf-8")
            self.assertEqual(a.script_for(req("reviewer", "F002", "reviewer")).name, "reviewer.sh")
            self.assertEqual(a.script_for(req("judgment", "", "negotiate")).name, "negotiate.sh")
            self.assertEqual(a.script_for(req("judgment", "", "triage")).name, "judgment.sh")
            self.assertEqual(a.script_for(req("scrutiny", "", "scrutiny")).name, "scrutiny.sh")   # the default, absent or not
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PathsOutsideTests(RepoFixture):
    def test_paths_outside_and_the_grade(self):
        base = self.git("rev-parse", "HEAD")
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# F001\n")
        (self.repo / "analytics" / "other.py").write_text("y\n", encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "note.md").write_text("n\n", encoding="utf-8")
        (self.m / "scratch.txt").write_text("s\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("add", "-f", ".missions/demo/scratch.txt")
        self.git("commit", "-qm", "F001: x")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics/service.py"]), ["analytics/other.py", "docs/note.md"])
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics", "docs/"]), [])
        self.assertEqual(grading.paths_outside(self.repo, base, head, []), ["analytics/other.py", "analytics/service.py", "docs/note.md"])
        # the driver's verdict (base = HEAD at launch) and the worker's (the commit's parent) agree
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1",
                                  feature_files=["analytics/service.py", "tests/unit/test_a.py"])
        self.assertEqual(len(g.problems), 1, g.problems)
        self.assertIn("analytics/other.py, docs/note.md outside the feature's Files and the handoff does not mention them", g.problems[0])
        self.assertNotIn("name them under", g.problems[0])
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")
        s = grading.self_check(self.m, "F001", self.repo, PLUGIN)
        self.assertEqual(len(s.problems), 1, s.problems)
        self.assertIn("outside the feature's Files", s.problems[0])
        self.assertIn("-- name them under Completed with the reason, or leave them out", s.problems[0])
        # named in the handoff (a basename is enough): believed
        text = files.read_text(files.handoff_path(self.m, "F001")).replace("## Completed\nx", "## Completed\nx; also other.py and docs/note.md for the helper")
        files.write_text(files.handoff_path(self.m, "F001"), text)
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1",
                                  feature_files=["analytics/service.py"])
        self.assertEqual(g.problems, [])
        self.assertEqual(grading.self_check(self.m, "F001", self.repo, PLUGIN).problems, [])
        # no Files line known: nothing to measure against
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1")
        self.assertEqual(g.problems, [])

    def test_non_ascii_paths_come_back_verbatim(self):
        # git prints a non-ASCII path quoted and octal-escaped unless core.quotePath is off;
        # files.git turns it off on every call, so a path compares to the Files line as written
        base = self.git("rev-parse", "HEAD")
        (self.repo / "analytics" / "caf\u00e9.py").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "F001: accent")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("-c", "core.quotePath=true", "diff", "--name-only", base, head), '"analytics/caf\\303\\251.py"')
        self.assertEqual(files.git_out(self.repo, "diff", "--name-only", base, head), "analytics/caf\u00e9.py")
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics/service.py"]), ["analytics/caf\u00e9.py"])
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics/caf\u00e9.py"]), [])
        (self.repo / "analytics" / "na\u00efve.py").write_text("y\n", encoding="utf-8")
        self.assertEqual(files.dirty_paths(self.repo), ["analytics/na\u00efve.py"])
        (self.repo / "analytics" / "na\u00efve.py").unlink()
        # the grade names the file as the handoff would spell it, and believes the handoff that does
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1",
                                  feature_files=["analytics/service.py"])
        self.assertEqual(len(g.problems), 1, g.problems)
        self.assertIn("analytics/caf\u00e9.py outside the feature's Files", g.problems[0])
        text = files.read_text(files.handoff_path(self.m, "F001")).replace("## Completed\nx", "## Completed\nx; also analytics/caf\u00e9.py")
        files.write_text(files.handoff_path(self.m, "F001"), text)
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1",
                                  feature_files=["analytics/service.py"])
        self.assertEqual(g.problems, [])

    def test_a_rename_reports_its_source(self):
        # a `git mv` from outside the Files into them deletes a path outside; rename detection
        # folds the two sides into one entry named after the destination and hides the deletion
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "note.md").write_text("n\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "F001: a note")
        base = self.git("rev-parse", "HEAD")
        self.git("mv", "docs/note.md", "analytics/note.md")
        self.git("commit", "-qm", "F001: move it")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(files.git_out(self.repo, "diff", "--name-only", "-M", base, head), "analytics/note.md")
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics"]), ["docs/note.md"])
        self.assertEqual(grading.paths_outside(self.repo, base, head, ["analytics", "docs"]), [])
        self.handoff()
        g = grading.grade_feature(self.m, "F001", self.repo, PLUGIN, base, None, ["A001", "A002"], task="F001#1",
                                  feature_files=["analytics"])
        self.assertEqual(len(g.problems), 1, g.problems)
        self.assertIn("docs/note.md outside the feature's Files", g.problems[0])
        self.assertEqual(classify(ClassifyTests().out(), g), "malformed_handoff")


class BlindTests(Fixture):
    def test_blind_hides_and_restores(self):
        ctx = make_ctx(self.m, self.tmp)
        for rel in ("handoffs/F001.md", "validation/M1-scrutiny.md", "decisions/d.md", "runs/F001#1/prompt.md",
                    "runs/review-F001#1/prompt.md", "patches/F001.patch"):
            (self.m / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.m / rel).write_text(rel, encoding="utf-8")
        with prep.blind(ctx, "review-F001#1"):
            for gone in ("handoffs", "validation", "decisions", "runs/F001#1"):
                self.assertFalse((self.m / gone).exists(), gone)
            self.assertTrue((self.m / "runs" / "review-F001#1" / "prompt.md").exists())
            self.assertTrue((self.m / "patches" / "F001.patch").exists())
            cell = self.m / ".blind" / "review-F001#1"
            self.assertEqual(os.stat(cell).st_mode & 0o777, 0)
            (self.m / "runs" / "review-F001#1" / "output.md").write_text("review", encoding="utf-8")
        for rel in ("handoffs/F001.md", "validation/M1-scrutiny.md", "decisions/d.md", "runs/F001#1/prompt.md",
                    "runs/review-F001#1/prompt.md", "runs/review-F001#1/output.md", "patches/F001.patch"):
            self.assertTrue((self.m / rel).exists(), rel)
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "handoffs/F001.md")
        self.assertFalse((self.m / ".blind").exists())
        # an exception inside the window restores too
        with self.assertRaises(RuntimeError):
            with prep.blind(ctx, "review-F002#1"):
                self.assertFalse((self.m / "handoffs").exists())
                raise RuntimeError("boom")
        self.assertTrue((self.m / "handoffs" / "F001.md").exists())
        self.assertFalse((self.m / ".blind").exists())

    def test_restore_after_a_crash_merges_what_grew_back(self):
        cell = self.m / ".blind" / "review-F001#1"
        (cell / "handoffs").mkdir(parents=True)
        (cell / "handoffs" / "F001.md").write_text("hidden", encoding="utf-8")
        (cell / "runs" / "F001#1").mkdir(parents=True)
        (cell / "runs" / "F001#1" / "outcome.json").write_text("{}", encoding="utf-8")
        os.chmod(cell, 0)
        (self.m / "handoffs").mkdir()
        (self.m / "handoffs" / "F002.md").write_text("newer", encoding="utf-8")
        (self.m / "runs" / "F002#1").mkdir(parents=True)
        self.assertEqual(prep.restore_blind(self.m), ["review-F001#1"])
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "hidden")
        self.assertEqual(files.read_text(self.m / "handoffs" / "F002.md"), "newer")
        self.assertTrue((self.m / "runs" / "F001#1" / "outcome.json").exists())
        self.assertTrue((self.m / "runs" / "F002#1").is_dir())
        self.assertFalse((self.m / ".blind").exists())
        self.assertEqual(prep.restore_blind(self.m), [])
        self.assertIsNone(journal.last(self.m, "note"))                   # nothing strayed: no note, no .strays
        self.assertFalse((self.m / ".strays").exists())

    def test_the_hidden_original_wins_and_the_stray_goes_aside(self):
        ctx = make_ctx(self.m, self.tmp)
        (self.m / "handoffs").mkdir()
        (self.m / "handoffs" / "F001.md").write_text("original", encoding="utf-8")
        (self.m / "validation").mkdir()
        (self.m / "validation" / "M1-scrutiny.md").write_text("v", encoding="utf-8")
        with prep.blind(ctx, "review-F001#1"):
            # the reviewer writes a handoff where the hidden one belongs, a new one, and a FILE named validation
            (self.m / "handoffs").mkdir()
            (self.m / "handoffs" / "F001.md").write_text("reviewer wrote this", encoding="utf-8")
            (self.m / "handoffs" / "F009.md").write_text("new", encoding="utf-8")
            (self.m / "validation").write_text("not a dir", encoding="utf-8")
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "original")
        self.assertEqual(files.read_text(self.m / "handoffs" / "F009.md"), "new")   # nothing hidden there: it stays
        self.assertTrue((self.m / "validation").is_dir())
        self.assertEqual(files.read_text(self.m / "validation" / "M1-scrutiny.md"), "v")
        aside = self.m / ".strays" / "review-F001#1"
        self.assertEqual(files.read_text(aside / "F001.md"), "reviewer wrote this")
        self.assertEqual(files.read_text(aside / "validation"), "not a dir")
        self.assertFalse((self.m / ".blind").exists())
        note = journal.last(self.m, "note")
        self.assertEqual(note["text"], "moved aside files written while hidden: handoffs/F001.md, validation -> .strays/review-F001#1/")
        self.assertEqual(note["task"], "review-F001#1")
        with prep.blind(ctx, "review-F002#1"):
            pass
        self.assertEqual(journal.count(self.m, "note"), 1)
        self.assertFalse((self.m / ".strays" / "review-F002#1").exists())
        # the window's finally tolerates a cell that is already gone
        with prep.blind(ctx, "review-F003#1"):
            self.assertEqual(prep.restore_blind(self.m), ["review-F003#1"])
            self.assertTrue((self.m / "handoffs" / "F001.md").exists())
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "original")
        self.assertFalse((self.m / ".blind").exists())

    def test_restore_after_a_crash_moves_strays_aside(self):
        cell = self.m / ".blind" / "review-F001#1"
        (cell / "handoffs").mkdir(parents=True)
        (cell / "handoffs" / "F001.md").write_text("hidden", encoding="utf-8")
        (cell / "decisions").mkdir()
        (cell / "decisions" / "F001.md").write_text("hidden decision", encoding="utf-8")
        os.chmod(cell, 0)
        (self.m / "handoffs").mkdir()
        (self.m / "handoffs" / "F001.md").write_text("stray", encoding="utf-8")
        (self.m / "decisions").mkdir()
        (self.m / "decisions" / "F001.md").write_text("stray decision", encoding="utf-8")
        self.assertEqual(prep.restore_blind(self.m), ["review-F001#1"])
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "hidden")
        self.assertEqual(files.read_text(self.m / "decisions" / "F001.md"), "hidden decision")
        aside = self.m / ".strays" / "review-F001#1"
        # two strays with one basename: the later one (handoffs sorts after decisions) gets a suffix
        self.assertEqual(files.read_text(aside / "F001.md"), "stray decision")
        self.assertEqual(files.read_text(aside / "F001.md.2"), "stray")
        self.assertEqual(journal.last(self.m, "note")["text"],
                         "moved aside files written while hidden: decisions/F001.md, handoffs/F001.md -> .strays/review-F001#1/")
        self.assertFalse((self.m / ".blind").exists())


class LockEnv:
    """Points the host lease at a lock under the case's tmp dir, so a test never waits on, or
    blocks, a real mission. Mix in before the fixture class."""

    def setUp(self):
        super().setUp()
        self.lock = self.tmp / "host.lock"
        self.saved = os.environ.get("MISSIONS_HOST_LOCK")
        os.environ["MISSIONS_HOST_LOCK"] = str(self.lock)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("MISSIONS_HOST_LOCK", None)
        else:
            os.environ["MISSIONS_HOST_LOCK"] = self.saved
        super().tearDown()


class HostLeaseTests(LockEnv, Fixture):
    def try_lock(self):
        """0 when the lock is free, 1 when held -- from another process, as a second driver would be."""
        code = ("import fcntl,sys; f=open(sys.argv[1],'a+')\n"
                "try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB); sys.exit(0)\n"
                "except OSError: sys.exit(1)\n")
        return subprocess.run([sys.executable, "-c", code, str(self.lock)]).returncode

    def test_lease_holder_line_and_opt_out(self):
        ctx = make_ctx(self.m, self.tmp, cfg={"host_lease": True})
        with prep.host_lease(ctx, "F001#1"):
            self.assertEqual(self.try_lock(), 1)
            line = files.read_text(self.lock).strip()
            self.assertTrue(line.startswith("mission=%s task=F001#1 pid=%d at=" % (self.m, os.getpid())), line)
        self.assertEqual(self.try_lock(), 0)
        self.assertIsNone(journal.last(self.m, "lease_wait"))
        self.lock.unlink()
        with prep.host_lease(make_ctx(self.m, self.tmp, cfg={"host_lease": False}), "F001#1"):
            self.assertFalse(self.lock.exists())

    def test_waits_and_journals_the_holder(self):
        code = ("import fcntl,sys,time; f=open(sys.argv[1],'a+'); fcntl.flock(f, fcntl.LOCK_EX)\n"
                "f.write('mission=/elsewhere/.missions/other task=F009#1 pid=1 at=t\\n'); f.flush(); time.sleep(1.5)\n")
        holder = subprocess.Popen([sys.executable, "-c", code, str(self.lock)])
        try:
            for _ in range(50):
                if self.lock.exists() and self.lock.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.05)
            t0 = time.monotonic()
            with prep.host_lease(make_ctx(self.m, self.tmp, cfg={}), "F001#1"):
                waited = time.monotonic() - t0
            self.assertGreater(waited, 0.5)
            rec = journal.last(self.m, "lease_wait")
            self.assertEqual((rec["task"], rec["holder"]), ("F001#1", "mission=/elsewhere/.missions/other task=F009#1 pid=1 at=t"))
            self.assertEqual(journal.count(self.m, "lease_wait"), 1)
        finally:
            holder.wait()


class PreflightPrepTests(RepoFixture):
    def cfg(self, **over):
        cfg = {"harness": "stub", "checkout": ".", "branch": "mission/demo", "adapters": {"stub": {"script_dir": str(self.tmp)}}}
        cfg.update(over)
        files.write_config(self.m, cfg)

    def ns(self, **over):
        kw = dict(mission_dir=str(self.m), harness=None, milestone=None, limit=None, until=None, dry_run=False)
        kw.update(over)
        return argparse.Namespace(**kw)

    def test_preflight_restores_blind_and_warns_on_no_host_lease(self):
        self.cfg(host_lease=False)
        cell = self.m / ".blind" / "review-F001#1"
        (cell / "handoffs").mkdir(parents=True)
        (cell / "handoffs" / "F001.md").write_text("hidden", encoding="utf-8")
        os.chmod(cell, 0)
        # without `restore`: a warning, and the cell is left where it is
        problems, warnings, cfg = loop.preflight(self.m, PLUGIN)
        self.assertEqual(problems, [])
        self.assertIn("found .blind/review-F001#1: a reviewer run is in progress, or a driver crashed inside one; "
                      "`missions run` restores it once it holds the driver lock", warnings)
        self.assertTrue(cell.is_dir())
        self.assertFalse((self.m / "handoffs").exists())
        self.assertIsNone(journal.last(self.m, "note"))
        problems, warnings, cfg = loop.preflight(self.m, PLUGIN, restore=True)
        self.assertEqual(problems, [])
        self.assertTrue(any("restored .blind/review-F001#1" in w for w in warnings), warnings)
        self.assertFalse(any("found .blind" in w for w in warnings), warnings)
        self.assertTrue(any("host_lease is false" in w for w in warnings), warnings)
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "hidden")
        self.assertFalse((self.m / ".blind").exists())
        self.assertIn("restored .blind/review-F001#1", journal.last(self.m, "note")["text"])

    def test_the_window_is_restored_only_under_the_lock(self):
        self.cfg()
        self.handoff("F001")
        ctx = make_ctx(self.m, self.repo, cfg=files.read_config(self.m))
        out = io.StringIO()
        with prep.blind(ctx, "review-F001#1"):
            cell = self.m / ".blind" / "review-F001#1"
            # `missions preflight` and a dry run look, warn, and leave the window alone
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_preflight(self.ns()), 0)
                self.assertEqual(loop.run(self.m, self.ns(dry_run=True)), 0)
            self.assertEqual(out.getvalue().count("warning: found .blind/review-F001#1: a reviewer run is in progress"), 2)
            self.assertTrue(cell.is_dir())
            self.assertFalse((self.m / "handoffs").exists())
            self.assertFalse((self.m / ".driver.lock").exists())
            # a second driver: the lock is held, so it never reaches preflight, let alone the restore
            with loop.DriverLock(self.m):
                with contextlib.redirect_stdout(out):
                    self.assertEqual(loop.run(self.m, self.ns()), loop.EXIT_CODES["preflight-failed"])
            self.assertIn("another driver holds", out.getvalue())
            self.assertTrue(cell.is_dir())
            self.assertFalse((self.m / "handoffs").exists())
            self.assertIsNone(journal.last(self.m, "stop"))
            self.assertIsNone(journal.last(self.m, "note"))
            # the driver that holds the lock restores: what `run` does inside DriverLock
            problems, warnings, _ = loop.preflight(self.m, PLUGIN, restore=True)
            self.assertEqual(problems, [])
            self.assertTrue(any("restored .blind/review-F001#1" in w for w in warnings), warnings)
            self.assertTrue((self.m / "handoffs" / "F001.md").exists())
            self.assertFalse(cell.exists())
        # the window's own finally found its cell gone and returned
        self.assertTrue((self.m / "handoffs" / "F001.md").exists())
        self.assertFalse((self.m / ".blind").exists())
        # a path that is no directory: preflight says so, and there is nothing to lock
        with contextlib.redirect_stdout(out):
            self.assertEqual(loop.run(self.tmp / "nowhere", self.ns()), loop.EXIT_CODES["preflight-failed"])
        self.assertIn("is not a mission directory", out.getvalue())

    def test_budget_grace_pct_is_validated_before_it_is_spent(self):
        """An operator-typed number the driver multiplies money by. Unchecked, a string takes the
        run down with a traceback one dispatch in -- past the point preflight exists to stop at --
        and a negative one silently inverts the knob, cutting BELOW the budget."""
        self.cfg()                                                       # absent: the default
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0], [])
        for value in (0, None, 25, 12.5):
            self.cfg(budget_grace_pct=value)
            self.assertEqual(loop.preflight(self.m, PLUGIN)[0], [], "rejected a valid %r" % (value,))
        self.cfg(budget_grace_pct="ten")
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0], ["budget_grace_pct must be a number, not 'ten'"])
        self.cfg(budget_grace_pct=True)      # a bool is an int in python, and is not a percentage
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0], ["budget_grace_pct must be a number, not True"])
        self.cfg(budget_grace_pct=-50)
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0],
                         ["budget_grace_pct is -50: a grace is headroom over the budget, never under it "
                          "(0 or null turns it off)"])

    def test_unset_branch_fails_preflight(self):
        self.cfg(branch="")
        text = files.read_text(self.m / "state.md")
        files.write_text(self.m / "state.md", "\n".join(ln for ln in text.split("\n") if not ln.startswith("**Branch:**")))
        self.assertEqual(files.read_state(self.m).branch, "")
        problems, _, _ = loop.preflight(self.m, PLUGIN)
        self.assertEqual(problems, ["no mission branch: state.md has no **Branch:** line and driver.json has no branch -- set one; "
                                    "the worker hooks refuse commits off the mission branch"])
        # either source naming one is enough
        self.cfg(branch="mission/demo")
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0], [])
        self.cfg(branch="")
        files.write_text(self.m / "state.md", text)
        self.assertEqual(loop.preflight(self.m, PLUGIN)[0], [])


class CliInitTests(Fixture):
    def test_init_writes_roles_lease_and_env(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["init", str(self.m), "--harness", "stub", "--stub-dir", str(self.tmp)]), 0)
        # an adapter with no preflight_problems() is asked nothing and warns nothing -- the probe
        # is a getattr, so a new adapter that never grew one must not make init noisy or raise
        self.assertNotIn("warning:", out.getvalue())
        cfg = files.read_config(self.m)
        self.assertEqual(sorted(cfg["roles"]), ["behavior", "judgment", "reviewer", "scrutiny", "worker"])
        self.assertEqual(cfg["roles"]["reviewer"], {"timeout_s": 1500, "budget_usd": 6, "model": None, "effort": None})
        self.assertEqual(cfg["roles"]["judgment"], {"timeout_s": 300, "budget_usd": 2, "model": None})
        self.assertEqual(files.read_text(self.m / "driver.json").count('"budget_usd": 8,'), 1)
        self.assertNotIn("effort", cfg["roles"]["worker"])
        self.assertIs(cfg["host_lease"], True)
        self.assertEqual(cfg["env"], {"passthrough": []})
        self.assertEqual(cfg["adapters"]["stub"]["script_dir"], str(self.tmp))

    def test_init_warns_when_the_adapter_cannot_work_here(self):
        """init writes the config, then asks the adapter whether this host can run it. A
        `workspace-write` codex on a host that refuses user namespaces is refused by preflight
        seconds later -- saying so here costs nothing, and the fix is a line in the file just
        written. It stays a warning: init reports what the host said, it does not choose
        `danger-full-access` for the operator."""
        from missions.adapters import codex as cx
        saved = cx.user_namespaces_usable
        try:
            cx.user_namespaces_usable = lambda: False
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["init", str(self.m), "--harness", "codex"]), 0)
            text = out.getvalue()
            self.assertIn("warning:", text)
            self.assertIn("user namespace", text)
            self.assertIn("danger-full-access", text)      # the message names the way out
            self.assertIn("preflight", text)               # and what happens if it is ignored
            # rc is still 0 and the config is written exactly as asked: a warning, not a refusal
            self.assertEqual(files.read_config(self.m)["adapters"]["codex"]["sandbox"], "workspace-write")

            cx.user_namespaces_usable = lambda: True
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["init", str(self.m), "--harness", "codex", "--force"]), 0)
            self.assertNotIn("warning:", out.getvalue())
        finally:
            cx.user_namespaces_usable = saved

    def test_until_choices(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["run", str(self.m), "--until", "bogus"])
        self.assertEqual(cli.parser().parse_args(["run", str(self.m), "--until", "validate"]).until, "validate")
        self.assertEqual(cli.parser().parse_args(["run", str(self.m), "--until", "milestone", "--limit", "2"]).until, "milestone")
        self.assertIsNone(cli.parser().parse_args(["run", str(self.m)]).until)


class RolePromptTests(Fixture):
    def test_system_prompt_role_mapping(self):
        for role, name in (("worker", "mission-worker"), ("reviewer", "mission-reviewer"),
                           ("scrutiny", "mission-validator-scrutiny"), ("behavior", "mission-validator-behavior")):
            meta, body = prompts.system_prompt(PLUGIN, role)
            self.assertEqual(meta.get("name"), name)
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", body)
        self.assertEqual(prompts.system_prompt(PLUGIN, "mission-worker")[0].get("name"), "mission-worker")   # an agent name still works
        meta, body = prompts.system_prompt(PLUGIN, "judgment")
        self.assertEqual((meta["name"], meta["tools"]), ("mission-judgment", ["Read", "Glob", "Grep"]))
        self.assertIs(body, prompts.JUDGMENT_SYSTEM)
        self.assertIn("exactly one JSON object", body)
        self.assertIn("You edit nothing", body)
        self.assertEqual(sorted(prompts.AGENTS), ["behavior", "judgment", "reviewer", "scrutiny", "worker"])

    def test_skill_section(self):
        v = prompts.skill_section(PLUGIN, "VALIDATE")
        self.assertTrue(v.startswith("## VALIDATE \u2014 at every milestone\n"))
        self.assertIn("**4. Negotiate.**", v)
        self.assertIn("| Contract turned out to be wrong |", v)
        self.assertNotIn("## Halts", v)
        h = prompts.skill_section(PLUGIN, "Halts")
        self.assertTrue(h.startswith("## Halts"))
        self.assertIn("**BLOCK**", h)
        self.assertIn("**ADVISORY**", h)
        self.assertNotIn("## Terminal steps", h)
        self.assertTrue(h.endswith("\n"))
        self.assertEqual(prompts.skill_section(PLUGIN, "No such section"), "")
        # the one slicer: any heading ends a validator's section, only a `##` ends the SKILL's
        text = "## A\nx\n### B\ny\n## C\nz"
        self.assertEqual(files.section(text, "a"), "x")
        self.assertEqual(files.section(text, "a", keep_heading=True, level=r"##"), "## A\nx\n### B\ny")

    def blind_review_hook(self, prompt, agent="mission-reviewer"):
        """hooks/mission-blind-review.sh's verdict on an Agent call: 0 allows, 2 blocks."""
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN), MISSION_DIR=str(self.m))
        res = subprocess.run(["bash", str(PLUGIN / "hooks" / "mission-blind-review.sh")], input=json.dumps(
            {"tool_name": "Agent", "tool_input": {"subagent_type": agent, "prompt": prompt}}),
            capture_output=True, text=True, env=env)
        return res.returncode, res.stderr

    def test_reviewer_prompt_passes_the_blind_review_hook(self):
        feats = files.read_features(self.m)
        rows = [a for a in files.read_contract(self.m) if a.id in feats[0].assertions]
        patch = self.m / "patches" / "F001.patch"
        text = prompts.reviewer_prompt(self.m, feats[0], rows, files.design_section(self.m, "F001"), patch,
                                       "0123456789abcdef", "fedcba9876543210", files.intelligence_line(self.m))
        self.assertTrue(text.startswith(
            "Mission: demo. Feature: F001 \u2014 feature F001.\nReview the patch for F001 against these assertions. "
            "You have not seen how or why it was\nwritten and you should not go looking.\n"
            "  A001 \u2014 Omitting the window equals the whole day  proof budget: min: named test; max: 1 pinning feature\n"
            "  A002 \u2014 Tenant A never sees tenant B  proof budget: min: mutation (tenancy); max: 1 pinning feature\n"
            "Design guidelines this feature was bound to (pre-code, from design.md):\n  | D001 |"), text)
        self.assertIn("\nPatch: %s (base 0123456, head fedcba9) \u2014 read this file; it is your only diff, and you do not "
                      "run git yourself.\nCodebase intelligence: none \u2014 for every public symbol" % patch, text)
        self.assertTrue(text.endswith("Write nothing to the repository. Your final message is the review, in the format "
                                      "your instructions give.\n"))
        # the 0.2 hook's rules, as regexes and as the hook itself
        for pat in (r"handoffs?/F[0-9]{3}", r"(?m)^#+[ \t]*Handoff", r"Assertions claimed|Procedures followed|Left undone",
                    r"origin/main\.\.\.|git[ \t\n]+(log|show)([ \t\n]|$)|git[ \t\n]+diff[ \t\n]"):
            self.assertIsNone(re.search(pat, text), pat)
        self.assertRegex(text, r"patches/F[0-9]{3}\.patch")
        rc, err = self.blind_review_hook(text)
        self.assertEqual(rc, 0, err)
        # the hook is live: the same prompt with a git command, or without the patch line, is blocked
        self.assertEqual(self.blind_review_hook(text + "Also run git log -1.\n")[0], 2)
        self.assertEqual(self.blind_review_hook(text.replace("patches/F001.patch", "diff.txt"))[0], 2)
        # a repair feature without a design section borrows its origin's
        fid = files.append_feature(self.m, "M1", "tenancy filter", ["A002"], ["analytics/service.py"], "", "", "C01 (FU001) of F001")
        repair = next(f for f in files.read_features(self.m) if f.id == fid)
        self.assertEqual(steps.design_for(self.m, repair), files.design_section(self.m, "F001"))
        self.assertEqual(steps.design_for(self.m, feats[2]), files.design_section(self.m, "F003"))

    def test_scrutiny_and_behavior_prompts(self):
        feats = files.read_features(self.m)
        rows = files.read_contract(self.m)
        text = prompts.scrutiny_prompt(self.m, "M1", feats[:2], rows[:2], "```mission-state\nphase: validating\n```")
        self.assertTrue(text.startswith("Mission: demo. Milestone: M1 \u2014 scrutiny.\n"))
        self.assertIn("  F001 \u2014 feature F001: files `analytics/service.py`, `tests/unit/test_a.py`; procedures: make test-unit\n", text)
        self.assertIn("  A002 \u2014 Tenant A never sees tenant B  [structural]\n", text)
        self.assertIn("  phase: validating\n", text)
        self.assertIn("## Coverage of milestone assertions", text)
        text = prompts.behavior_prompt(self.m, "M2", [rows[2]], "```mission-state\nphase: validating\n```", 3)
        self.assertTrue(text.startswith("Mission: demo. Milestone: M2 \u2014 behavior validation.\n"))
        self.assertIn("  A003 \u2014 The filter chip is visible on the dashboard  [interface]\n", text)
        self.assertIn("Live-run cap for this milestone: 3 live runs (mission.md).", text)
        self.assertNotIn("A001", text)
        self.assertNotIn("ui/src/Filters.tsx", text)
        self.assertEqual(self.blind_review_hook(text, "mission-validator-behavior")[0], 0)
        self.assertIn("none set", prompts.behavior_prompt(self.m, "M2", [rows[2]], "", None))

    def test_judgment_prompts_end_with_the_shape(self):
        rules = prompts.skill_section(PLUGIN, "VALIDATE")
        text = prompts.negotiate_prompt(self.m, "M1", 2, "A001: satisfied (validation/M1-review-F001.md)",
                                        {"validation/M1-scrutiny.md": "## Commands\n| make test-unit | 0 | 1s |\n",
                                         "validation/M1-review-F001.md": "## Assertion verdicts\n| A001 | satisfied | x |\n"},
                                        "# Follow-ups \u2014 demo\n", rules)
        self.assertTrue(text.startswith("Mission: demo. Milestone: M1 \u2014 negotiate, validation round 2.\n"))
        self.assertIn("  A001: satisfied (validation/M1-review-F001.md)\n", text)
        self.assertIn("--- validation/M1-scrutiny.md ---\n## Commands\n| make test-unit | 0 | 1s |\n--- validation/M1-review-F001.md ---\n", text)
        self.assertIn("\nfollowups.md as it stands:\n# Follow-ups \u2014 demo\n", text)
        self.assertIn("\nThe rules (verbatim from the mission-run skill):\n## VALIDATE", text)
        self.assertTrue(text.endswith("Answer with exactly this JSON shape:\n" + prompts.NEGOTIATE_SHAPE + "\n" + prompts.ANSWER_LINE + "\n"))
        self.assertEqual(text.count(prompts.ANSWER_LINE), 1)
        issues = ["F001 handoff: the test stack would not start on port 5435", "F002 handoff: fixture row missing"]
        text = prompts.triage_prompt(self.m, issues, {"F001": "# Handoff F001\n\nbody\n"}, "", prompts.skill_section(PLUGIN, "Halts"))
        self.assertTrue(text.startswith("Mission: demo. Triage of 2 open issue(s).\n"))
        self.assertIn("\nOpen issues:\n- [1] %s\n- [2] %s\n" % tuple(issues), text)
        self.assertIn("\n--- handoffs/F001.md ---\n# Handoff F001\n\nbody\n", text)
        self.assertIn("\nfollowups.md as it stands:\n  (empty)\n", text)
        self.assertIn("\n## Halts", text)
        self.assertTrue(text.endswith("Answer with exactly this JSON shape:\n" + prompts.TRIAGE_SHAPE + "\n" + prompts.ANSWER_LINE + "\n"))
        # the shapes are what the schema checks accept, so a model that copies them is not rejected on shape
        self.assertIn('"disposition": "resolved"|"defer"|"repair"|"escalate"', prompts.TRIAGE_SHAPE)
        self.assertIn('"contract_wrong": bool', prompts.NEGOTIATE_SHAPE)


class RoleRequestTests(Fixture):
    def test_per_role_resolution(self):
        feats = files.read_features(self.m)
        run = self.m / "runs" / "x"
        rmeta = prompts.system_prompt(PLUGIN, "reviewer")[0]     # model opus, effort xhigh, its tool list
        claude = make_ctx(self.m, self.tmp, cfg={"roles": {}}, harness="claude")
        req = steps.build_request(claude, feats[0], "review-F001#1", run, rmeta, "validating", role="reviewer", step="reviewer")
        self.assertEqual((req.role, req.step, req.model, req.effort, req.timeout_s, req.budget_usd, req.read_only, req.feature),
                         ("reviewer", "reviewer", "opus", "xhigh", 1500, 6.0, True, "F001"))
        self.assertIn("mcp__graphify__query_graph", req.tools)
        self.assertEqual((req.env["MISSIONS_ROLE"], req.env["MISSIONS_FEATURE"], req.env["MISSIONS_TASK"]), ("reviewer", "F001", "review-F001#1"))
        self.assertNotIn("MISSIONS_FILES", req.env)
        # driver.json's role model and effort apply under any harness; the frontmatter only under claude
        cfg = {"roles": {"reviewer": {"model": "opus-4", "effort": "high", "timeout_s": 10, "budget_usd": None}}}
        req = steps.build_request(make_ctx(self.m, self.tmp, cfg=cfg, harness="codex"), feats[0], "review-F001#1", run, rmeta, "validating", role="reviewer", step="reviewer")
        self.assertEqual((req.model, req.effort, req.timeout_s, req.budget_usd, req.read_only), ("opus-4", "high", 10, None, True))
        req = steps.build_request(make_ctx(self.m, self.tmp, cfg={"roles": {}}, harness="codex"), feats[0], "review-F001#1", run, rmeta, "validating", role="reviewer", step="reviewer")
        self.assertEqual((req.model, req.effort), (None, None))
        # mission.md's Reviewer seat beats driver.json under claude and is ignored under codex
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace(
            "- Autonomy ceiling: advisory\n", "- Autonomy ceiling: advisory\n- Reviewer seat: sonnet \u2014 cheap\n"))
        req = steps.build_request(make_ctx(self.m, self.tmp, cfg=cfg, harness="claude"), feats[0], "review-F001#1", run, rmeta, "validating", role="reviewer", step="reviewer")
        self.assertEqual((req.model, req.effort), ("sonnet", "high"))
        req = steps.build_request(make_ctx(self.m, self.tmp, cfg=cfg, harness="codex"), feats[0], "review-F001#1", run, rmeta, "validating", role="reviewer", step="reviewer")
        self.assertEqual(req.model, "opus-4")
        # scrutiny and behavior run things: not read-only, their own tools and defaults, no feature
        smeta = prompts.system_prompt(PLUGIN, "scrutiny")[0]
        req = steps.build_request(claude, None, "scrutiny-M1#1", run, smeta, "validating", role="scrutiny", step="scrutiny")
        self.assertEqual((req.model, req.effort, req.timeout_s, req.budget_usd, req.read_only, req.feature, req.tools),
                         ("sonnet", None, 1800, 4.0, False, "", ["Read", "Glob", "Grep", "Bash"]))
        self.assertEqual(req.env["MISSIONS_FEATURE"], "")
        bmeta = prompts.system_prompt(PLUGIN, "behavior")[0]
        req = steps.build_request(claude, None, "behavior-M1#1", run, bmeta, "validating", role="behavior", step="behavior")
        self.assertEqual((req.model, req.timeout_s, req.budget_usd, req.read_only), ("opus", 2400, 10.0, False))
        self.assertIn("mcp__playwright__browser_click", req.tools)
        # judgment: read-only, the constant's tools, no model unless driver.json names one
        jmeta = prompts.system_prompt(PLUGIN, "judgment")[0]
        req = steps.build_request(claude, None, "triage#1", run, jmeta, "implementing", role="judgment", step="triage")
        self.assertEqual((req.model, req.effort, req.timeout_s, req.budget_usd, req.read_only, req.tools, req.step),
                         (None, None, 300, 2.0, True, ["Read", "Glob", "Grep"], "triage"))
        cfg = {"roles": {"judgment": {"model": "opus"}}}
        self.assertEqual(steps.build_request(make_ctx(self.m, self.tmp, cfg=cfg, harness="codex"), None, "triage#1", run, jmeta, "implementing", role="judgment", step="triage").model, "opus")
        # the worker's shape did not move
        req = steps.build_request(claude, feats[0], "F001#1", run, {"model": "sonnet", "tools": ["Read"]}, "implementing")
        self.assertEqual((req.role, req.step, req.read_only, req.model, req.timeout_s), ("worker", "", False, "sonnet", 2400))
        self.assertIn("MISSIONS_FILES", req.env)
        self.assertEqual(steps.EXECUTOR_ROLES, ("worker", "reviewer", "scrutiny", "behavior"))
        self.assertEqual(steps.READ_ONLY_ROLES, ("reviewer", "judgment"))


class RunRoleTests(LockEnv, RepoFixture):
    """Runs through the base stubs: what the journal, the locks and validation/ look like after."""

    def setUp(self):
        super().setUp()
        files.write_config(self.m, {"harness": "stub", "checkout": ".", "branch": "mission/demo",
                                    "adapters": {"stub": {"script_dir": str(STUBS)}}})
        cfg = files.read_config(self.m)
        self.ctx = make_ctx(self.m, self.repo, cfg=cfg)
        self.ctx.adapter = StubAdapter(cfg["adapters"]["stub"])

    def test_reviewer_run_is_blind_leased_and_filed(self):
        feats = files.read_features(self.m)
        self.handoff("F001")
        (self.m / "validation").mkdir()
        files.write_text(self.m / "validation" / "M0-scrutiny.md", "old")
        (self.m / "runs" / "F001#1").mkdir(parents=True)
        files.write_text(self.m / "runs" / "F001#1" / "output.md", "the worker's words")
        prompt = "Mission: demo. Feature: F001 \u2014 x.\n  A001 \u2014 t  proof budget: b\n  A002 \u2014 u\n"
        outcome, text = steps.run_role(self.ctx, "reviewer", "reviewer", "review-F001#1", prompt, feature=feats[0],
                                       milestone="M1", validation_file="M1-review-F001.md")
        self.assertEqual((outcome.cls, outcome.rc), ("ok", 0))
        self.assertEqual(verdicts.parse_reviewer(text), {"A001": "satisfied", "A002": "satisfied"})
        run = self.m / "runs" / "review-F001#1"
        self.assertEqual(files.read_text(run / "handoffs-visible.txt").strip(), "absent")
        self.assertTrue((self.m / "handoffs" / "F001.md").exists())                     # restored
        self.assertEqual(files.read_text(self.m / "runs" / "F001#1" / "output.md"), "the worker's words")
        self.assertFalse((self.m / ".blind").exists())
        self.assertFalse((self.m / ".lease").exists())
        self.assertFalse((self.m / ".writer").exists())
        self.assertTrue(self.lock.exists())                                              # the host lease was taken
        self.assertEqual(files.read_text(self.m / "validation" / "M0-scrutiny.md"), "old")
        v = files.read_text(self.m / "validation" / "M1-review-F001.md")
        self.assertRegex(v, r"^<!-- review-F001#1 \u00b7 mission-reviewer \u00b7 stub \u00b7 \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ -->\n\n## Assertion verdicts\n")
        self.assertTrue(v.endswith("none\n"))
        self.assertTrue((run / "prompt.md").exists() and (run / "system.md").exists() and (run / "env-names.txt").exists())
        self.assertTrue(files.read_text(run / "system.md").startswith("# Mission Reviewer"))
        self.assertEqual(json.loads(files.read_text(run / "outcome.json"))["cls"], "ok")
        evs = [r for r in journal.events(self.m)]
        names = [r["event"] for r in evs]
        self.assertEqual(names, ["dispatch", "lease_released", "agent_return", "cost", "step_done"])
        d = evs[0]
        self.assertEqual((d["agent"], d["class"], d["feature"], d["milestone"], d["dispatch_id"], d["task"], d["harness"], d["step"], d["session_id"]),
                         ("mission-reviewer", "executor", "F001", "M1", "review-F001#1", "review-F001#1", "stub", "reviewer", "driver:r1"))
        self.assertTrue(evs[1]["lock"].startswith("agent=mission-reviewer feature=F001 dispatch_id=review-F001#1 session=driver:r1 "))
        self.assertEqual((evs[2]["agent"], evs[2]["feature"], evs[2]["status"], evs[2]["rc"]), ("mission-reviewer", "F001", "completed", 0))
        self.assertEqual((evs[3]["task"], evs[3]["unit"]), ("review-F001#1", "unknown"))
        self.assertEqual((evs[4]["step"], evs[4]["role"], evs[4]["cls"], evs[4]["rc"], evs[4]["milestone"]), ("review-F001#1", "reviewer", "ok", 0, "M1"))
        self.assertNotIn("feature", evs[4])
        self.assertIsNone(journal.prior_attempt(self.m, "F001"))
        self.assertEqual(journal.attempts(self.m, "F001"), 0)
        self.assertEqual(journal.task_attempts(self.m, "review-F001"), 1)

    def test_scrutiny_behavior_and_judgment_runs(self):
        prompt = "Mission: demo. Milestone: M1 \u2014 scrutiny.\n  A001 \u2014 t  [structural]\n"
        outcome, text = steps.run_role(self.ctx, "scrutiny", "scrutiny", "scrutiny-M1#1", prompt, milestone="M1",
                                       validation_file="M1-scrutiny.md")
        self.assertEqual(outcome.cls, "ok")
        self.assertEqual(verdicts.parse_scrutiny(text)["commands"][0]["exit"], 0)
        d = journal.last(self.m, "dispatch")
        self.assertEqual((d["agent"], d["class"], d["milestone"], d["step"]), ("mission-validator-scrutiny", "executor", "M1", "scrutiny"))
        self.assertNotIn("feature", d)
        self.assertTrue(journal.last(self.m, "lease_released")["lock"].startswith("agent=mission-validator-scrutiny feature=M1 "))
        self.assertTrue((self.m / "validation" / "M1-scrutiny.md").exists())
        outcome, text = steps.run_role(self.ctx, "behavior", "behavior", "behavior-M2#1",
                                       "Mission: demo. Milestone: M2 \u2014 behavior validation.\n  A003 \u2014 t  [interface]\n", milestone="M2")
        self.assertEqual((outcome.cls, verdicts.parse_behavior(text)), ("ok", {"A003": "proven"}))
        self.assertEqual(journal.dispatches(self.m), 2)
        # judgment: static, no lease of either kind, read-only, counted by nobody's cap
        self.lock.unlink()
        outcome, text = steps.run_role(self.ctx, "judgment", "negotiate", "negotiate-M1#1", "Mission: demo. x\n", milestone="M1")
        self.assertEqual(outcome.cls, "ok")
        self.assertEqual(judgment.extract_json(text), {"findings": [], "repairs": [], "contract_wrong": False, "reason": "all proven"})
        d = journal.last(self.m, "dispatch")
        self.assertEqual((d["agent"], d["class"], d["step"], d["dispatch_id"]), ("mission-judgment", "static", "negotiate", "negotiate-M1#1"))
        self.assertFalse(self.lock.exists())
        self.assertEqual(journal.count(self.m, "lease_released"), 2)
        self.assertEqual(journal.dispatches(self.m), 2)
        self.assertIn("MISSIONS_ROLE", files.read_text(self.m / "runs" / "negotiate-M1#1" / "env-names.txt"))
        self.assertIn("a judgment run does not commit", files.read_text(self.m / "githooks" / "pre-commit"))
        # no output, and a crash: the classes the caller retries on; no validation file either way
        d = self.tmp / "stubs2"
        d.mkdir()
        files.write_text(d / "scrutiny.sh", "#!/bin/bash\nexit 0\n")
        files.write_text(d / "behavior.sh", "#!/bin/bash\necho 'half' > \"$MISSIONS_RUN_DIR/output.md\"; exit 3\n")
        self.ctx.adapter = StubAdapter({"script_dir": str(d)})
        outcome, text = steps.run_role(self.ctx, "scrutiny", "scrutiny", "scrutiny-M1#2", "x\n", milestone="M1", validation_file="M1-scrutiny-r2.md")
        self.assertEqual((outcome.cls, text), ("no_output", ""))
        self.assertFalse((self.m / "validation" / "M1-scrutiny-r2.md").exists())
        outcome, text = steps.run_role(self.ctx, "behavior", "behavior", "behavior-M2#2", "x\n", milestone="M2", validation_file="M2-behavior-r2.md")
        self.assertEqual((outcome.cls, outcome.rc, text.strip()), ("error", 3, "half"))
        self.assertFalse((self.m / "validation" / "M2-behavior-r2.md").exists())
        self.assertEqual(journal.last(self.m, "step_done")["cls"], "error")
        self.assertFalse((self.m / ".lease").exists())

    def test_negotiate_returns_its_repairs_and_the_cap_closes_the_round(self):
        # the judgment repairs A002 every time; the cap (1) admits one repair feature
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("Repair rounds per assertion: 2", "Repair rounds per assertion: 1"))
        reply = {"findings": [{"title": "leak", "assertion": "A002", "found_by": "mission-reviewer (review-F001)", "where": "analytics/service.py",
                               "severity": "high", "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "x"}],
                 "repairs": [{"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
                              "procedures": "make test-unit", "out_of_scope": ""}],
                 "contract_wrong": False, "reason": ""}
        d = self.tmp / "stubs2"
        d.mkdir()
        files.write_text(d / "negotiate.sh", "#!/bin/bash\ncat > \"$MISSIONS_RUN_DIR/output.md\" <<'EOF'\n%s\nEOF\n" % json.dumps(reply))
        self.ctx.adapter = StubAdapter({"script_dir": str(d)})
        feats = files.read_features(self.m)
        mfeats = [f for f in feats if f.milestone == "M1"]
        rows = validate.milestone_assertions(self.m, "M1", feats)
        # round 1: the repair is registered and comes back with its assertions -- what the proven
        # marks are withheld for, straight from the reply rather than re-read from features.md
        self.assertEqual(validate._negotiate(self.ctx, "M1", 1, rows, mfeats),
                         [{"id": "F004", "cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
                           "procedures": "make test-unit", "out_of_scope": "", "origins": ["F001"]}])
        self.assertIn("repair feature(s) F004", journal.last(self.m, "judgment")["summary"])
        self.assertIsNone(journal.last(self.m, "validate_done"))                # run_validate closes a round that goes on
        # round 2: the same repair is over the cap -- nothing is written, and the round is closed
        # as halted BEFORE the stop, so the next driver does not resume it
        before = tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md"))
        self.assertEqual(validate._negotiate(self.ctx, "M1", 2, rows, mfeats), 5)
        self.assertEqual(before, tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md")))
        j = journal.last(self.m, "judgment")
        self.assertEqual((j["task"], j["round"]), ("negotiate-M1#2", 2))
        self.assertIn("1 finding(s), 1 repair(s) proposed -- refused: repair-round cap (1 per assertion) exceeded by a repair of A002, "
                      "which already has 1 repair feature(s) (F004)", j["summary"])
        vd = journal.last(self.m, "validate_done")
        self.assertEqual((vd["milestone"], vd["round"], vd["result"]), ("M1", 2, "halted"))
        self.assertEqual([r["event"] for r in journal.events(self.m)][-4:], ["judgment", "validate_done", "halt", "stop"])
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["exit"]), ("gate-blocked", 5))
        self.assertIn("the diagnosis is wrong", s["detail"])
        self.assertIn("never a cap raise", s["needs"])
        self.assertEqual(files.read_state(self.m).phase, "halted")

    def test_negotiate_rejects_an_assertion_the_contract_lacks(self):
        # attempt 1 repairs A099, which contract.md does not have: the complaint goes back in the
        # re-run prompt and attempt 2, which names A002, is applied -- nothing written in between
        good = {"findings": [{"title": "leak", "assertion": "A002", "found_by": "mission-reviewer (review-F001)", "where": "",
                              "severity": "high", "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "x"}],
                "repairs": [{"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
                             "procedures": "", "out_of_scope": ""}], "contract_wrong": False, "reason": ""}
        bad = json.loads(json.dumps(good))
        bad["findings"][0]["assertion"] = "A099"
        bad["repairs"][0]["assertions"] = ["A099", "A002"]
        d = self.tmp / "stubs2"
        d.mkdir()
        files.write_text(d / "negotiate.sh", "#!/bin/bash\ncase \"$MISSIONS_TASK\" in\n  'negotiate-M1#1') echo '%s' ;;\n  *) echo '%s' ;;\n"
                         "esac > \"$MISSIONS_RUN_DIR/output.md\"\n" % (json.dumps(bad), json.dumps(good)))
        self.ctx.adapter = StubAdapter({"script_dir": str(d)})
        feats = files.read_features(self.m)
        mfeats = [f for f in feats if f.milestone == "M1"]
        rows = validate.milestone_assertions(self.m, "M1", feats)
        r = validate._negotiate(self.ctx, "M1", 1, rows, mfeats)
        self.assertEqual([x["id"] for x in r], ["F004"])
        self.assertEqual([x["task"] for x in journal.events(self.m) if x["event"] == "dispatch"], ["negotiate-M1#1", "negotiate-M1#2"])
        second = files.read_text(self.m / "runs" / "negotiate-M1#2" / "prompt.md")
        self.assertIn("could not be applied: findings[0]: A099 is not in the contract; repairs[0]: A099 is not in the contract\n", second)
        self.assertIn("reply rejected -- findings[0]: A099 is not in the contract",
                      journal.last(self.m, "note", lambda x: x.get("task") == "negotiate-M1#1")["text"])
        self.assertEqual(journal.last(self.m, "judgment")["task"], "negotiate-M1#2")
        self.assertEqual([(f.id, f.assertion) for f in files.read_followups(self.m)], [("FU001", "A002")])
        self.assertNotIn("A099", "".join(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md")))
        self.assertEqual(check_sh(self.m)[0], 0)
        # two such replies: stop error with the complaint, the round left open, nothing written
        files.write_text(d / "negotiate.sh", "#!/bin/bash\necho '%s' > \"$MISSIONS_RUN_DIR/output.md\"\n" % json.dumps(bad))
        before = tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md"))
        self.assertEqual(validate._negotiate(self.ctx, "M1", 2, rows, mfeats), 1)
        self.assertEqual(before, tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md")))
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["exit"]), ("error", 1))
        self.assertIn("negotiate: two replies could not be applied; last: findings[0]: A099 is not in the contract", s["detail"])
        self.assertEqual(journal.count(self.m, "judgment"), 1)
        self.assertIsNone(journal.last(self.m, "validate_done"))
        self.assertIsNone(journal.last(self.m, "halt"))


class RepairCapTests(Fixture):
    """The milestone-level half of the repair-round cap (steps.register): repair rounds counted
    from the journal's validate_done records, the ceiling from the milestone's assertion count --
    and the caps check that moved next to it."""

    FU = {"title": "leak", "source": "M1-review-F001", "assertion": "A002", "found_by": "mission-reviewer", "where": "",
          "severity": "high", "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "x"}
    RP = {"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
          "procedures": "", "out_of_scope": "", "origins": ["F001"]}

    def test_milestone_repair_rounds_and_ceiling(self):
        self.assertEqual(steps.milestone_repair_rounds(self.m, "M1"), 0)
        journal.append(self.m, "validate_done", milestone="M1", round=1, result="repairs")
        journal.append(self.m, "validate_done", milestone="M1", round=2, result="halted")      # a human halt never counts
        journal.append(self.m, "validate_done", milestone="M2", round=1, result="repairs")
        journal.append(self.m, "validate_done", milestone="M1", round=3, result="closed")
        self.assertEqual((steps.milestone_repair_rounds(self.m, "M1"), steps.milestone_repair_rounds(self.m, "M2")), (1, 1))
        # M1 has two assertions: at 2 per assertion its ceiling is 4 rounds; M2 has one: 2
        self.assertIsNone(steps.milestone_cap_problem(self.m, "M1", 2))
        self.assertIsNone(steps.milestone_cap_problem(self.m, "M2", 2))
        journal.append(self.m, "validate_done", milestone="M2", round=2, result="repairs")
        self.assertEqual(steps.milestone_cap_problem(self.m, "M2", 2),
                         "M2 has had 2 repair round(s), the ceiling for 1 assertion(s) at 2 per assertion -- the diagnosis is wrong, not the code")
        self.assertIsNone(steps.milestone_cap_problem(self.m, "M1", 1))
        journal.append(self.m, "validate_done", milestone="M1", round=4, result="repairs")
        self.assertEqual(steps.milestone_cap_problem(self.m, "M1", 1),
                         "M1 has had 2 repair round(s), the ceiling for 2 assertion(s) at 1 per assertion -- the diagnosis is wrong, not the code")
        # a milestone with no routed assertion still gets `cap` rounds
        self.assertIsNone(steps.milestone_cap_problem(self.m, "M9", 2))
        # register refuses on the ceiling with nothing written, though no assertion is at its own cap
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("Repair rounds per assertion: 2", "Repair rounds per assertion: 1"))
        ctx = make_ctx(self.m, self.tmp)
        before = tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md"))
        r = steps.register(ctx, "M1", [dict(self.FU)], [dict(self.RP)], 5)
        self.assertIsInstance(r, str)
        self.assertIn("M1 has had 2 repair round(s), the ceiling for 2 assertion(s) at 1 per assertion", r)
        self.assertEqual(before, tuple(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md")))
        self.assertIsNone(journal.last(self.m, "features_added"))
        # findings without a repair still register: the ceiling is on repairs
        self.assertEqual(steps.register(ctx, "M1", [dict(self.FU, disposition="accept")], [], 5), (["FU001"], []))
        # and under the ceiling the per-assertion cap is the one that speaks
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("Repair rounds per assertion: 1", "Repair rounds per assertion: 3"))
        self.assertEqual(steps.register(ctx, "M1", [dict(self.FU)], [dict(self.RP)], 5), (["FU002"], ["F004"]))
        self.assertEqual(journal.last(self.m, "features_added")["round"], 5)
        self.assertEqual(check_sh(self.m)[0], 0)

    def test_check_caps(self):
        ctx = make_ctx(self.m, self.tmp)
        budget = files.read_budget(self.m)
        self.assertIsNone(steps.check_caps(ctx, budget))
        for i in range(3):
            journal.append(self.m, "dispatch", agent="mission-worker", **{"class": "writer"}, task="F001#%d" % (i + 1))
        journal.append(self.m, "dispatch", agent="mission-judgment", **{"class": "static"}, task="negotiate-M1#1")
        self.assertIsNone(steps.check_caps(ctx, dict(budget, dispatch_cap=4.0)))      # a static dispatch is not counted
        self.assertEqual(steps.check_caps(ctx, dict(budget, dispatch_cap=3.0)), 4)
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["detail"]), ("budget", "dispatch cap reached: 3 of 3"))
        self.assertEqual(files.read_state(self.m).phase, "halted")
        self.assertFalse(hasattr(loop, "_check_caps"))


class RegisterTests(Fixture):
    """steps.register's registry shape: one cluster, one repair feature -- a second reply for a
    cluster joins its pending feature or, once that feature ran, gets a fresh cluster id -- and a
    repair with nothing to claim writes no Assertions line. check.sh agrees with every result."""

    def finding(self, **over):
        fu = {"title": "leak", "source": "M1-review-F001", "assertion": "A002", "found_by": "mission-reviewer", "where": "",
              "severity": "high", "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "x"}
        fu.update(over)
        return fu

    def repair(self, **over):
        rp = {"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
              "procedures": "", "out_of_scope": "", "origins": ["F001"]}
        rp.update(over)
        return rp

    def test_reuse_joins_the_pending_repair_feature(self):
        ctx = make_ctx(self.m, self.tmp)
        self.assertEqual(steps.register(ctx, "M1", [self.finding()], [self.repair()], 1), (["FU001"], ["F004"]))
        self.assertIsNone(journal.last(self.m, "note"))                                  # a first cluster needs no normalising
        # the same cluster again while F004 is pending: the follow-up joins it, no F005
        fus, rp = [self.finding(title="also the summary query", assertion="A001")], [self.repair(title="again", assertions=["A001"])]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, 2), (["FU002"], ["F004"]))
        self.assertEqual(rp[0]["assertions"], ["A001", "A002"])                          # the caller's withheld set covers F004
        self.assertEqual(fus[0]["repair_as"], "F004")
        self.assertEqual([(x.id, x.cluster, x.repair_as) for x in files.read_followups(self.m)],
                         [("FU001", "C01", "F004"), ("FU002", "C01", "F004")])
        feats = {x.id: x for x in files.read_features(self.m)}
        self.assertNotIn("F005", feats)
        self.assertEqual((feats["F004"].assertions, feats["F004"].cluster, feats["F004"].repairs, feats["F004"].status),
                         (["A002"], "C01", ["F001"], "pending"))
        self.assertIn("- **Repairs:** C01 (FU001, FU002) of F001\n", files.read_text(self.m / "features.md"))
        self.assertEqual(next(a for a in files.read_contract(self.m) if a.id == "A001").features, ["F001"])   # nothing new routed
        # the join is journaled too, as `reused`: a round resumed past its negotiate reads it back
        self.assertEqual(journal.count(self.m, "features_added"), 2)
        rec = journal.last(self.m, "features_added")
        self.assertEqual((rec["ids"], rec["reused"], rec["round"]), ([], ["F004"], 2))
        self.assertEqual(validate.applied_repairs(self.m, "M1", 2, files.read_features(self.m)),
                         [{"id": "F004", "cluster": "C01", "assertions": ["A002"]}])
        self.assertEqual(journal.last(self.m, "followups_added")["ids"], ["FU002"])
        n = journal.last(self.m, "note")
        self.assertEqual((n["text"], n["feature"], n["cluster"], n["milestone"], n["round"]),
                         ("cluster C01 already has repair feature F004 (pending): the new follow-up(s) join it", "F004", "C01", "M1", 2))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        # findings without a repair keep their label untouched; an active feature is joined like a pending one
        files.set_feature(self.m, "F004", status="active")
        self.assertEqual(steps.register(ctx, "M1", [self.finding(title="third", disposition="accept", why="later")], [], None), (["FU003"], []))
        self.assertEqual((files.read_followups(self.m)[2].cluster, files.read_followups(self.m)[2].repair_as), ("C01", None))
        self.assertEqual(journal.count(self.m, "note"), 1)
        fus, rp = [self.finding(title="fourth")], [self.repair(title="fourth")]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, 3), (["FU004"], ["F004"]))
        self.assertEqual(rp[0]["assertions"], ["A002"])
        self.assertIn("repair feature F004 (active)", journal.last(self.m, "note")["text"])
        self.assertIn("- **Repairs:** C01 (FU001, FU002, FU004) of F001\n", files.read_text(self.m / "features.md"))
        self.assertEqual(journal.count(self.m, "features_added"), 3)                     # the accept-only call added none
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)

    def test_fresh_cluster_when_the_repair_is_done(self):
        ctx = make_ctx(self.m, self.tmp)
        self.assertEqual(steps.register(ctx, "M1", [self.finding()], [self.repair()], 1), (["FU001"], ["F004"]))
        files.set_feature(self.m, "F004", status="done", commit="0123456789abcdef")
        # the reply re-uses the label C01 (and accepts something in C02): a new root cause -> C03,
        # in the findings and the repair alike; the accepted finding keeps C02
        fus = [self.finding(title="again"), self.finding(title="lint", assertion=None, cluster="C02", disposition="accept", why="later")]
        rp = [self.repair(title="again")]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, 2), (["FU002", "FU003"], ["F005"]))
        self.assertEqual((rp[0]["cluster"], fus[0]["cluster"], fus[1]["cluster"], fus[0]["repair_as"]), ("C03", "C03", "C02", "F005"))
        self.assertEqual([(x.id, x.cluster, x.cluster_label, x.repair_as) for x in files.read_followups(self.m)],
                         [("FU001", "C01", "tenant", "F004"), ("FU002", "C03", "tenant", "F005"), ("FU003", "C02", "tenant", None)])
        feats = {x.id: x for x in files.read_features(self.m)}
        self.assertEqual((feats["F005"].cluster, feats["F005"].repairs, feats["F005"].assertions, feats["F005"].status),
                         ("C03", ["F001"], ["A002"], "pending"))
        raw = files.read_text(self.m / "features.md")
        self.assertIn("- **Repairs:** C03 (FU002) of F001\n", raw)
        self.assertIn("- **Repairs:** C01 (FU001) of F001\n", raw)                          # F004 untouched
        self.assertEqual(next(a for a in files.read_contract(self.m) if a.id == "A002").features, ["F001", "F002", "F004", "F005"])
        self.assertEqual(journal.last(self.m, "features_added")["ids"], ["F005"])
        n = journal.last(self.m, "note")
        self.assertEqual((n["text"], n["feature"], n["cluster"], n["round"]),
                         ("cluster C01 was repaired by F004 (done); this round's repair is registered as C03", "F004", "C03", 2))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("cluster split", out)
        # a blocked repair feature, and one features.md no longer has, are fresh clusters too
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("Repair rounds per assertion: 2", "Repair rounds per assertion: 9"))
        files.set_feature(self.m, "F005", status="blocked")
        fus, rp = [self.finding(title="third", cluster="C03")], [self.repair(title="third", cluster="C03")]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, 3), (["FU004"], ["F006"]))
        self.assertEqual(rp[0]["cluster"], "C04")
        self.assertEqual(journal.last(self.m, "note")["text"], "cluster C03 was repaired by F005 (blocked); this round's repair is registered as C04")
        files.append_followups(self.m, [self.finding(title="stray", cluster="C05", repair_as="F009")])
        fus, rp = [self.finding(title="fifth", cluster="C05")], [self.repair(title="fifth", cluster="C05")]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, None), (["FU006"], ["F007"]))
        self.assertEqual(rp[0]["cluster"], "C06")
        self.assertEqual(journal.last(self.m, "note")["text"], "cluster C05 was repaired by F009 (not in features.md); this round's repair is registered as C06")
        self.assertNotIn("round", journal.last(self.m, "features_added"))                   # triage passes None
        self.assertEqual([x.id for x in files.read_features(self.m) if x.milestone == "M1"], ["F001", "F002", "F004", "F005", "F006", "F007"])

    def test_repair_without_assertions_writes_no_assertions_line(self):
        ctx = make_ctx(self.m, self.tmp)
        fus = [self.finding(title="log noise", assertion=None, severity="low", blocking=False)]
        rp = [self.repair(title="quiet the logger", assertions=[])]
        self.assertEqual(steps.register(ctx, "M1", fus, rp, 1), (["FU001"], ["F004"]))
        raw = files.read_text(self.m / "features.md")
        self.assertIn("### F004 — quiet the logger\n- **Files:** `analytics/service.py`\n- **Procedures:** —\n", raw)
        self.assertNotIn("- **Assertions:** —", raw)
        feat = next(f for f in files.read_features(self.m) if f.id == "F004")
        self.assertEqual((feat.assertions, feat.repairs, feat.cluster, feat.files), ([], ["F001"], "C01", ["analytics/service.py"]))
        self.assertEqual([a.features for a in files.read_contract(self.m)], [["F001"], ["F001", "F002"], ["F003"]])   # nothing routed
        prompt = prompts.worker_prompt(self.m, feat, "digest", [], steps.design_for(self.m, feat), PLUGIN)
        self.assertIn("(contract.md names no assertion for F004 — say so in the handoff)", prompt)
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        # the section still takes its Status and Range lines where set_feature puts them
        files.set_feature(self.m, "F004", status="done", commit="0123456789abcdef", rng="abc1234..def5678")
        self.assertIn("- **Repairs:** C01 (FU001) of F001\n- **Status:** done · commit `0123456`\n- **Range:** `abc1234`..`def5678`\n",
                      files.read_text(self.m / "features.md"))


class PatchTests(RepoFixture):
    def test_patch_covers_the_whole_range(self):
        base = self.git("rev-parse", "HEAD")
        (self.repo / "docs").mkdir()
        files.write_text(self.repo / "docs" / "NOTE.md", "declared in the handoff\n")
        (self.repo / "analytics" / "service.py").open("a", encoding="utf-8").write("# F001\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "F001: work")
        head = self.git("rev-parse", "HEAD")
        ctx = make_ctx(self.m, self.repo, cfg=files.read_config(self.m))
        self.assertEqual(steps.materialise_patch(ctx, "F001", base, head), str(self.m / "patches" / "F001.patch"))
        text = files.read_text(self.m / "patches" / "F001.patch")
        self.assertTrue(text.startswith("feature: F001\nbase: %s\nhead: %s\npaths: <all>\n\n" % (base, head)))
        self.assertIn("diff --git a/docs/NOTE.md", text)              # outside F001's Files, and in the patch
        self.assertIn("diff --git a/analytics/service.py", text)
        self.assertIsNone(journal.last(self.m, "note"))
        # an empty range is no patch, and a note
        self.assertIsNone(steps.materialise_patch(ctx, "F002", head, head))
        self.assertIn("empty diff for F002", journal.last(self.m, "note")["text"])
        self.assertFalse((self.m / "patches" / "F002.patch").exists())


class TriageTests(LockEnv, Fixture):
    ISSUES = ["F001 handoff: the test stack would not start on port 5435", "F001 handoff: the fixture row for tenant B is missing"]

    def setUp(self):
        super().setUp()
        self.scripts = self.tmp / "stub"
        self.scripts.mkdir()
        files.write_config(self.m, {"harness": "stub", "checkout": ".", "branch": "mission/demo",
                                    "adapters": {"stub": {"script_dir": str(self.scripts)}}})
        files.add_open_issues(self.m, self.ISSUES)
        (self.m / "handoffs").mkdir()
        files.write_text(self.m / "handoffs" / "F001.md", "# Handoff F001\n\n## Issues discovered\n- the test stack would not "
                                                          "start on port 5435\n- the fixture row for tenant B is missing\n")
        files.write_text(self.m / "handoffs" / "F002.md", "# Handoff F002\n\n## Issues discovered\nnone\n")
        cfg = files.read_config(self.m)
        self.ctx = make_ctx(self.m, self.tmp, cfg=cfg)
        self.ctx.adapter = StubAdapter(cfg["adapters"]["stub"])

    def script(self, body):
        files.write_text(self.scripts / "triage.sh", "#!/bin/bash\n" + body)

    def followup(self, **over):
        fu = {"title": "tenant B fixture row", "assertion": "A002", "severity": "medium", "cluster": "C01",
              "cluster_label": "test fixtures", "blocking": True}
        fu.update(over)
        return fu

    def test_apply_resolved_defer_and_repair(self):
        st = files.read_state(self.m)
        obj = {"resolutions": [
            {"issue": 1, "disposition": "resolved", "why": "the port is documented read-only; the worker used the mocked layer"},
            {"issue": 2, "disposition": "repair", "why": "the fixture is part of the feature", "followup": self.followup(),
             "repair": {"title": "add the tenant B fixture", "assertions": ["A002"], "files": ["tests/unit/test_a.py"], "procedures": "make test-unit"}}]}
        self.assertEqual(judgment.validate_triage(obj), [])
        self.assertIsNone(steps.apply_triage(self.ctx, st, st.open_issues, "triage#1", obj))
        self.assertEqual(files.read_state(self.m).open_issues, [])
        self.assertIn("\n- none\n", files.read_text(self.m / "state.md"))
        fus = files.read_followups(self.m)
        self.assertEqual([(f.id, f.source, f.assertion, f.cluster, f.cluster_label, f.repair_as, f.blocking, f.severity) for f in fus],
                         [("FU001", "M1-triage", "A002", "C01", "test fixtures", "F004", True, "medium")])
        raw = files.read_text(self.m / "followups.md")
        self.assertIn("## FU001 \u2014 tenant B fixture row (from M1-triage)\n- **Assertion:** A002\n"
                      "- **Found by:** F001 handoff, the fixture row for tenant B is missing\n- **Severity:** medium\n"
                      "- **Cluster:** C01 \u2014 test fixtures\n- **Blocking:** yes\n"
                      "- **Disposition:** repair as F004 \u2014 the fixture is part of the feature\n", raw)
        feats = {f.id: f for f in files.read_features(self.m)}
        self.assertEqual((feats["F004"].milestone, feats["F004"].title, feats["F004"].assertions, feats["F004"].files,
                          feats["F004"].procedures, feats["F004"].repairs, feats["F004"].status),
                         ("M1", "add the tenant B fixture", ["A002"], ["tests/unit/test_a.py"], "make test-unit", ["F001"], "pending"))
        self.assertIn("- **Repairs:** C01 (FU001) of F001\n", files.read_text(self.m / "features.md"))
        self.assertEqual(next(a for a in files.read_contract(self.m) if a.id == "A002").features, ["F001", "F002", "F004"])
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        dec = journal.last(self.m, "decision")
        self.assertEqual((dec["step"], dec["task"]), ("triage", "triage#1"))
        self.assertIn("port 5435", dec["what"])
        self.assertIn("mocked layer", dec["why"])
        self.assertEqual(journal.last(self.m, "followups_added")["ids"], ["FU001"])
        self.assertEqual(journal.last(self.m, "features_added")["ids"], ["F004"])
        j = journal.last(self.m, "judgment")
        self.assertEqual((j["step"], j["task"], j["milestone"]), ("triage", "triage#1", "M1"))
        self.assertEqual(j["summary"], "2 issue(s): 1 resolved, 0 deferred, 1 repaired (FU001), repair feature F004")
        self.assertIsNone(journal.last(self.m, "stop"))
        # the repair feature is the loop's next pending feature of M1, with its origin's design section
        self.assertEqual(steps.design_for(self.m, feats["F004"]), files.design_section(self.m, "F001"))

    def test_defer_then_escalate_then_skipped(self):
        st = files.read_state(self.m)
        obj = {"resolutions": [{"issue": 2, "disposition": "defer", "why": "later", "followup": self.followup(assertion=None, blocking=False)},
                               {"issue": 1, "disposition": "escalate", "why": "port 5435 needs an operator"}]}
        self.assertEqual(steps.apply_triage(self.ctx, st, st.open_issues, "triage#1", obj), 5)
        # the deferred one was applied, the escalated one stays, the mission is halted on the why
        self.assertEqual(files.read_state(self.m).open_issues, [self.ISSUES[0]])
        self.assertEqual(files.read_state(self.m).phase, "halted")
        h = journal.last(self.m, "halt")
        self.assertEqual(h["class"], "block")
        self.assertIn("port 5435 needs an operator", h["reason"])
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["exit"]), ("gate-blocked", 5))
        self.assertIn("triage escalates 1 issue(s): port 5435 needs an operator -- F001 handoff: the test stack", s["detail"])
        fu = files.read_followups(self.m)[0]
        self.assertEqual((fu.id, fu.assertion, fu.blocking, fu.disposition), ("FU001", None, False, "accept as known limitation \u2014 deferred by the triage step: later"))
        self.assertEqual(journal.last(self.m, "judgment")["summary"], "2 issue(s): 0 resolved, 1 deferred, 0 repaired (FU001), 1 escalated")
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)
        # a reply that skips an issue halts too, naming it; nothing else changes
        files.write_state_fields(self.m, phase="implementing")
        st = files.read_state(self.m)
        self.assertEqual(steps.apply_triage(self.ctx, st, st.open_issues, "triage#2", {"resolutions": []}), 5)
        s = journal.last(self.m, "stop")
        self.assertIn("without a resolution: F001 handoff: the test stack would not start on port 5435", s["detail"])
        self.assertEqual(files.read_state(self.m).open_issues, [self.ISSUES[0]])
        self.assertEqual(len(files.read_followups(self.m)), 1)

    def test_repair_cap_refuses_before_writing(self):
        files.write_text(self.m / "mission.md", files.read_text(self.m / "mission.md").replace("Repair rounds per assertion: 2", "Repair rounds per assertion: 1"))
        files.append_followups(self.m, [{"title": "earlier", "source": "M1-review-F001", "assertion": "A002", "found_by": "mission-reviewer",
                                         "severity": "high", "cluster": "C01", "blocking": True, "disposition": "repair", "repair_as": "F004"}])
        before = (files.read_text(self.m / "features.md"), files.read_text(self.m / "contract.md"), files.read_text(self.m / "followups.md"))
        st = files.read_state(self.m)
        obj = {"resolutions": [{"issue": 1, "disposition": "resolved", "why": "x"},
                               {"issue": 2, "disposition": "repair", "why": "y", "followup": self.followup(cluster="C02"),
                                "repair": {"title": "again", "assertions": ["A002"], "files": ["tests/unit/test_a.py"], "procedures": ""}}]}
        self.assertEqual(steps.apply_triage(self.ctx, st, st.open_issues, "triage#1", obj), 5)
        self.assertEqual(before, (files.read_text(self.m / "features.md"), files.read_text(self.m / "contract.md"), files.read_text(self.m / "followups.md")))
        self.assertEqual(files.read_state(self.m).open_issues, self.ISSUES)          # nothing was cleared either
        s = journal.last(self.m, "stop")
        self.assertIn("repair-round cap (1 per assertion) exceeded by a repair of A002, which already has 1 repair feature(s) (F004)", s["detail"])
        self.assertIn("the diagnosis is wrong", s["detail"])
        self.assertIn("never a cap raise", s["needs"])
        self.assertIsNone(journal.last(self.m, "judgment"))
        self.assertIsNone(steps.repair_cap_problem(self.m, ["A002"], 2))
        self.assertIsNone(steps.repair_cap_problem(self.m, ["A001"], 1))

    def test_step_triage_retries_a_bad_reply_once_then_errors(self):
        self.script("echo 'not json at all' > \"$MISSIONS_RUN_DIR/output.md\"\n")
        st = files.read_state(self.m)
        self.assertEqual(steps.step_triage(self.ctx, st), 1)
        self.assertEqual([r["task"] for r in journal.events(self.m) if r["event"] == "dispatch"], ["triage#1", "triage#2"])
        d = journal.last(self.m, "dispatch")
        self.assertEqual((d["agent"], d["class"], d["step"], d["milestone"]), ("mission-judgment", "static", "triage", "M1"))
        first = files.read_text(self.m / "runs" / "triage#1" / "prompt.md")
        second = files.read_text(self.m / "runs" / "triage#2" / "prompt.md")
        self.assertNotIn("could not be applied", first)
        self.assertTrue(second.startswith(first.rstrip("\n") + "\n\nYour previous reply (triage#1) could not be applied: "))
        self.assertIn("no JSON object in the reply", second)
        self.assertTrue(second.endswith(prompts.ANSWER_LINE + "\n"))
        self.assertIn("- [1] %s\n- [2] %s\n" % tuple(self.ISSUES), first)
        self.assertIn("--- handoffs/F001.md ---", first)
        self.assertNotIn("Handoff F002", first)                                     # F002 raised nothing
        self.assertEqual(journal.count(self.m, "note", lambda r: "reply rejected" in r.get("text", "")), 2)
        self.assertEqual(journal.count(self.m, "step_done", lambda r: r.get("role") == "judgment" and r.get("cls") == "ok"), 2)
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["exit"]), ("error", 1))
        self.assertIn("triage: two replies could not be applied", s["detail"])
        self.assertIn("runs/triage#2/output.md", s["needs"])
        self.assertEqual(files.read_state(self.m).open_issues, self.ISSUES)        # untouched
        self.assertEqual(files.read_state(self.m).phase, "implementing")           # an error is not a halt
        self.assertFalse(self.lock.exists())                                        # no host lease for a judgment run
        self.assertFalse((self.m / ".lease").exists())
        self.assertIsNone(journal.last(self.m, "lease_released"))
        self.assertEqual(journal.dispatches(self.m), 0)

    def test_step_triage_applies_the_corrected_second_reply(self):
        # attempt 1 names an issue the prompt never listed and a defer without a follow-up; the
        # complaint goes back verbatim and attempt 2 is applied
        self.script('case "$MISSIONS_TASK" in\n'
                    '  "triage#1") echo \'{"resolutions":[{"issue":7,"disposition":"resolved","why":"x"},{"issue":1,"disposition":"defer","why":"later"}]}\' ;;\n'
                    '  *) echo \'```json\n{"resolutions":[{"issue":1,"disposition":"resolved","why":"documented"},{"issue":2,"disposition":"resolved","why":"present"}]}\n```\' ;;\n'
                    'esac > "$MISSIONS_RUN_DIR/output.md"\n')
        st = files.read_state(self.m)
        self.assertIsNone(steps.step_triage(self.ctx, st))
        second = files.read_text(self.m / "runs" / "triage#2" / "prompt.md")
        self.assertIn("could not be applied: resolutions[1]: disposition defer needs a followup; "
                      "resolutions[0]: issue 7 does not exist; the prompt listed 2 issue(s)\n", second)
        self.assertEqual(files.read_state(self.m).open_issues, [])
        self.assertEqual(journal.count(self.m, "decision", lambda r: r.get("step") == "triage"), 2)
        self.assertEqual(journal.last(self.m, "judgment")["task"], "triage#2")
        self.assertIsNone(journal.last(self.m, "stop"))
        self.assertEqual(len(files.read_followups(self.m)), 0)
        # a crash counts as a bad reply too
        self.script("exit 9\n")
        files.add_open_issues(self.m, ["F002 handoff: later"])
        self.assertEqual(steps.step_triage(self.ctx, files.read_state(self.m)), 1)
        self.assertIn("could not be applied: the run exited 9 with no reply", files.read_text(self.m / "runs" / "triage#4" / "prompt.md"))
        self.assertEqual(journal.last(self.m, "step_done")["cls"], "error")

    def test_step_triage_rejects_an_assertion_the_contract_lacks(self):
        # attempt 1 repairs A099, which the contract does not have -- a reply the schema accepts,
        # and the applier could not have routed; attempt 2 names A002 and is applied
        self.script('case "$MISSIONS_TASK" in\n'
                    '  "triage#1") echo \'{"resolutions":[{"issue":1,"disposition":"resolved","why":"x"},{"issue":2,"disposition":"repair","why":"y",'
                    '"followup":{"title":"t","assertion":"A099","severity":"low","cluster":"C01","blocking":false},'
                    '"repair":{"title":"r","assertions":["A099"],"files":["tests/unit/test_a.py"]}}]}\' ;;\n'
                    '  *) echo \'{"resolutions":[{"issue":1,"disposition":"resolved","why":"x"},{"issue":2,"disposition":"repair","why":"y",'
                    '"followup":{"title":"t","assertion":"A002","severity":"low","cluster":"C01","blocking":false},'
                    '"repair":{"title":"r","assertions":["A002"],"files":["tests/unit/test_a.py"]}}]}\' ;;\n'
                    'esac > "$MISSIONS_RUN_DIR/output.md"\n')
        self.assertIsNone(steps.step_triage(self.ctx, files.read_state(self.m)))
        second = files.read_text(self.m / "runs" / "triage#2" / "prompt.md")
        self.assertIn("could not be applied: resolutions[1].followup: A099 is not in the contract; "
                      "resolutions[1].repair: A099 is not in the contract\n", second)
        self.assertEqual([(f.id, f.assertion, f.repair_as) for f in files.read_followups(self.m)], [("FU001", "A002", "F004")])
        self.assertEqual(journal.last(self.m, "features_added")["ids"], ["F004"])
        self.assertEqual(files.read_state(self.m).open_issues, [])
        self.assertNotIn("A099", "".join(files.read_text(self.m / n) for n in ("features.md", "contract.md", "followups.md")))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)


class ValidateRuleTests(Fixture):
    """The mechanical half of VALIDATE on the base fixture: the proven rule, the round bookkeeping,
    the summary the negotiate step reads and the shape its reply is applied in. No run."""

    def verdict(self, validator, assertions, file, feature=None, milestone="M1", round_no=1):
        journal.append(self.m, "verdict", validator=validator, feature=feature, milestone=milestone, round=round_no,
                       assertions=assertions, file=file)

    def test_proven_rule_reads_the_round_whole(self):
        feats = files.read_features(self.m)
        rows = {r.id: r for r in files.read_contract(self.m)}
        files.claim_assertions(self.m, ["A001", "A002"])
        # round 1: one negative among two reviews of A002 refutes it, whatever the later one said
        self.verdict("mission-reviewer", {"A001": "satisfied", "A002": "not satisfied"}, "validation/M1-review-F001.md", "F001")
        self.verdict("mission-reviewer", {"A002": "satisfied"}, "validation/M1-review-F002.md", "F002")
        self.assertEqual(validate.proven_evidence(self.m, "M1", 1, feats),
                         ({"A001": "validation/M1-review-F001.md"}, {"A002": "validation/M1-review-F001.md"}))
        rv = verdicts.round_verdicts(self.m, "M1", 1)
        self.assertEqual((validate.verdict_of(rv, rows["A001"], feats), validate.verdict_of(rv, rows["A002"], feats)),
                         ("satisfied", "not satisfied"))
        # round 2: a `cannot tell` blocks without refuting
        self.verdict("mission-reviewer", {"A001": "satisfied", "A002": "cannot tell"}, "validation/M1-review-F001-r2.md", "F001", round_no=2)
        self.verdict("mission-reviewer", {"A002": "satisfied"}, "validation/M1-review-F002-r2.md", "F002", round_no=2)
        self.assertEqual(validate.proven_evidence(self.m, "M1", 2, feats), ({"A001": "validation/M1-review-F001-r2.md"}, {}))
        self.assertEqual(validate.verdict_of(verdicts.round_verdicts(self.m, "M1", 2), rows["A002"], feats), "cannot tell")
        # round 3: a repair feature routed to A002 was reviewed -- only its review counts on A002
        files.append_feature(self.m, "M1", "tenancy filter", ["A002"], ["analytics/service.py"], "", "", "C01 (FU001) of F001")
        files.route_assertion(self.m, "A002", "F004")
        feats = files.read_features(self.m)
        self.assertEqual((feats[2].id, feats[2].repairs, feats[2].cluster), ("F004", ["F001"], "C01"))
        self.verdict("mission-reviewer", {"A001": "satisfied", "A002": "not satisfied"}, "validation/M1-review-F001-r3.md", "F001", round_no=3)
        self.verdict("mission-reviewer", {"A002": "satisfied"}, "validation/M1-review-F004-r3.md", "F004", round_no=3)
        self.assertEqual(validate.proven_evidence(self.m, "M1", 3, feats),
                         ({"A001": "validation/M1-review-F001-r3.md", "A002": "validation/M1-review-F004-r3.md"}, {}))
        # round 4: the repair's own negative refutes, whatever the origin's review said
        self.verdict("mission-reviewer", {"A002": "satisfied"}, "validation/M1-review-F001-r4.md", "F001", round_no=4)
        self.verdict("mission-reviewer", {"A002": "not satisfied"}, "validation/M1-review-F004-r4.md", "F004", round_no=4)
        self.assertEqual(validate.proven_evidence(self.m, "M1", 4, feats), ({}, {"A002": "validation/M1-review-F004-r4.md"}))
        # a round that said nothing leaves everything in neither; the summary still reads the latest
        self.assertEqual(validate.proven_evidence(self.m, "M1", 9, feats), ({}, {}))
        self.assertEqual(verdicts.latest_verdicts(self.m, "M1")["reviews"]["A002"], ("not satisfied", "validation/M1-review-F004-r4.md"))
        # an interface assertion is proven by the behavior validator, never by a diff
        self.verdict("mission-reviewer", {"A003": "satisfied"}, "validation/M2-review-F003.md", "F003", milestone="M2")
        self.assertEqual(validate.proven_evidence(self.m, "M2", 1, feats), ({}, {}))
        self.verdict("mission-validator-behavior", {"A003": "FAILED"}, "validation/M2-behavior.md", milestone="M2")
        self.assertEqual(validate.proven_evidence(self.m, "M2", 1, feats), ({}, {"A003": "validation/M2-behavior.md"}))
        self.assertEqual(validate.verdict_of(verdicts.round_verdicts(self.m, "M2", 1), rows["A003"], feats), "FAILED")
        self.verdict("mission-validator-behavior", {"A003": "not reached"}, "validation/M2-behavior-r2.md", milestone="M2", round_no=2)
        self.assertEqual(validate.proven_evidence(self.m, "M2", 2, feats), ({}, {}))
        self.verdict("mission-validator-behavior", {"A003": "proven"}, "validation/M2-behavior-r3.md", milestone="M2", round_no=3)
        self.assertEqual(validate.proven_evidence(self.m, "M2", 3, feats), ({"A003": "validation/M2-behavior-r3.md"}, {}))
        # a behavior `proven` on a structural assertion proves nothing either
        self.verdict("mission-validator-behavior", {"A001": "proven"}, "validation/M1-behavior-r5.md", round_no=5)
        self.assertEqual(validate.proven_evidence(self.m, "M1", 5, feats), ({}, {}))
        # end to end: the marks land in contract.md with the file as evidence, and a later
        # round's negative is the one thing that moves one back
        self.assertEqual(files.prove_assertions(self.m, validate.proven_evidence(self.m, "M1", 3, feats)[0]), ["A001", "A002"])
        self.assertEqual(files.prove_assertions(self.m, validate.proven_evidence(self.m, "M2", 3, feats)[0]), ["A003"])
        rows = {r.id: r for r in files.read_contract(self.m)}
        self.assertEqual((rows["A001"].status, rows["A002"].evidence, rows["A003"].evidence),
                         ("proven", "validation/M1-review-F004-r3.md", "validation/M2-behavior-r3.md"))
        self.assertEqual(files.reopen_assertions(self.m, validate.proven_evidence(self.m, "M1", 4, feats)[1]), ["A002"])
        rows = {r.id: r for r in files.read_contract(self.m)}
        self.assertEqual((rows["A001"].status, rows["A002"].status, rows["A002"].evidence),
                         ("proven", "claimed", "validation/M1-review-F004-r4.md"))
        rc, out = check_sh(self.m)
        self.assertEqual(rc, 0, out)

    def test_closed_milestone_and_the_done_stop(self):
        self.assertFalse(validate.closed(self.m, "M1"))
        journal.append(self.m, "validate_start", milestone="M1", round=1)
        journal.append(self.m, "milestone_closed", milestone="M1", next="M2", round=1)
        # the close journals milestone_closed before validate_done: in between, the round is open
        self.assertFalse(validate.closed(self.m, "M1"))
        journal.append(self.m, "validate_done", milestone="M1", round=1, result="closed")
        self.assertTrue(validate.closed(self.m, "M1"))
        self.assertFalse(validate.closed(self.m, "M2"))
        # a round opened on it afterwards is an open round, and the loop resumes it
        journal.append(self.m, "validate_start", milestone="M1", round=2)
        self.assertFalse(validate.closed(self.m, "M1"))
        ctx = make_ctx(self.m, self.tmp)
        files.write_state_fields(self.m, phase="validating", milestone="M2")
        self.assertEqual(validate.done_stop(ctx), 0)
        st = files.read_state(self.m)
        self.assertEqual(st.phase, "validating")
        self.assertTrue(st.resume_next.startswith("terminal steps 1-6 of /missions:mission-run: all 2 milestone(s) validated"))
        s = journal.last(self.m, "stop")
        self.assertEqual((s["reason"], s["exit"], s["detail"]), ("done", 0, "all 2 milestone(s) validated; every assertion proven"))
        self.assertIn("terminal steps 1-6", s["needs"])

    def test_done_steps_count_a_no_patch_review(self):
        journal.append(self.m, "validate_start", milestone="M1", round=1)
        journal.append(self.m, "validate_step", milestone="M1", round=1, step="reviewer", feature="F001", task="", file="", nopatch=True)
        journal.append(self.m, "validate_step", milestone="M1", round=1, step="reviewer", feature="F002", task="review-F002#1",
                       file="validation/M1-review-F002.md")
        self.assertEqual(sorted(validate._done_steps(self.m, "M1", 1)), [("reviewer", "F001")])     # F002's file is missing
        self.assertEqual(validate._round_files(self.m, "M1", 1), {})                                # nothing to paste from it
        (self.m / "validation").mkdir()
        files.write_text(self.m / "validation" / "M1-review-F002.md", "x")
        self.assertEqual(sorted(validate._done_steps(self.m, "M1", 1)), [("reviewer", "F001"), ("reviewer", "F002")])
        self.assertEqual(validate._round_files(self.m, "M1", 1), {"M1-review-F002.md": "x"})

    def test_negotiate_is_applied_once_per_round(self):
        feats = files.read_features(self.m)
        self.assertFalse(validate.negotiated(self.m, "M1", 1))
        journal.append(self.m, "judgment", step="triage", task="triage#1", milestone="M1", summary="x")
        journal.append(self.m, "judgment", step="negotiate", task="negotiate-M1#1", milestone="M1", round=1, summary="x")
        self.assertTrue(validate.negotiated(self.m, "M1", 1))
        self.assertFalse(validate.negotiated(self.m, "M1", 2))
        self.assertFalse(validate.negotiated(self.m, "M2", 1))
        self.assertEqual(validate.applied_repairs(self.m, "M1", 1, feats), [])
        ctx = make_ctx(self.m, self.tmp)
        fu = {"title": "leak", "source": "M1-review-F001", "assertion": "A002", "found_by": "mission-reviewer", "where": "",
              "severity": "high", "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "x"}
        rp = {"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
              "procedures": "", "out_of_scope": "", "origins": ["F001"]}
        self.assertEqual(steps.register(ctx, "M1", [fu], [rp], 1), (["FU001"], ["F004"]))
        rec = journal.last(self.m, "features_added")
        self.assertEqual((rec["ids"], rec["milestone"], rec["round"]), (["F004"], "M1", 1))
        feats = files.read_features(self.m)
        self.assertEqual(validate.applied_repairs(self.m, "M1", 1, feats), [{"id": "F004", "cluster": "C01", "assertions": ["A002"]}])
        self.assertEqual(validate.applied_repairs(self.m, "M1", 2, feats), [])
        # triage registers with no round: its features are nobody's round
        self.assertEqual(steps.register(ctx, "M1", [dict(fu, cluster="C02")], [dict(rp, cluster="C02", assertions=["A001"])])[1], ["F005"])
        self.assertNotIn("round", journal.last(self.m, "features_added"))
        self.assertEqual(validate.applied_repairs(self.m, "M1", 1, files.read_features(self.m)),
                         [{"id": "F004", "cluster": "C01", "assertions": ["A002"]}])

    def test_milestone_assertions_follow_the_routing(self):
        self.assertEqual([a.id for a in validate.milestone_assertions(self.m, "M1")], ["A001", "A002"])
        self.assertEqual([a.id for a in validate.milestone_assertions(self.m, "M2")], ["A003"])
        files.append_feature(self.m, "M2", "repair", ["A002"], ["analytics/service.py"], "", "", "C01 (FU001) of F001")
        files.route_assertion(self.m, "A002", "F004")
        self.assertEqual([a.id for a in validate.milestone_assertions(self.m, "M2")], ["A002", "A003"])
        self.assertEqual([a.id for a in validate.milestone_assertions(self.m, "M1")], ["A001", "A002"])

    def test_round_resumes_an_open_one(self):
        self.assertEqual(validate._round(self.m, "M1"), (1, False))
        journal.append(self.m, "validate_start", milestone="M1", round=1)
        self.assertEqual(validate._round(self.m, "M1"), (1, True))
        self.assertEqual(validate._round(self.m, "M2"), (1, False))
        journal.append(self.m, "validate_done", milestone="M1", round=1, result="repairs")
        self.assertEqual(validate._round(self.m, "M1"), (2, False))
        # a step counts as done only while its file exists
        journal.append(self.m, "validate_start", milestone="M1", round=2)
        journal.append(self.m, "validate_step", milestone="M1", round=2, step="scrutiny", task="scrutiny-M1#2", file="validation/M1-scrutiny-r2.md")
        journal.append(self.m, "validate_step", milestone="M1", round=2, step="reviewer", feature="F001", task="review-F001#2", file="validation/M1-review-F001-r2.md")
        journal.append(self.m, "validate_step", milestone="M1", round=1, step="reviewer", feature="F002", task="review-F002#1", file="validation/M1-review-F002.md")
        (self.m / "validation").mkdir()
        files.write_text(self.m / "validation" / "M1-scrutiny-r2.md", "x")
        files.write_text(self.m / "validation" / "M1-review-F002.md", "x")
        self.assertEqual(sorted(validate._done_steps(self.m, "M1", 2)), [("scrutiny", "")])
        self.assertEqual(sorted(validate._done_steps(self.m, "M1", 1)), [("reviewer", "F002")])
        self.assertEqual(validate._round_files(self.m, "M1", 2), {"M1-scrutiny-r2.md": "x"})
        self.assertEqual(validate.file_name("M1", "reviewer", 1, "F001"), "M1-review-F001.md")
        self.assertEqual(validate.file_name("M1", "reviewer", 2, "F004"), "M1-review-F004-r2.md")
        self.assertEqual((validate.file_name("M2", "scrutiny", 1), validate.file_name("M2", "behavior", 3)), ("M2-scrutiny.md", "M2-behavior-r3.md"))

    def test_summary_lists_every_reviewed_feature(self):
        rows = validate.milestone_assertions(self.m, "M1")
        self.assertEqual(validate.verdict_summary(self.m, "M1", rows).split("\n"),
                         ["A001 [structural] \u2014 no verdict \u2014 contract: unproven", "A002 [structural] \u2014 no verdict \u2014 contract: unproven"])
        self.verdict("mission-reviewer", {"A001": "satisfied", "A002": "not satisfied"}, "validation/M1-review-F001.md", "F001")
        self.verdict("mission-reviewer", {"A002": "satisfied"}, "validation/M1-review-F002.md", "F002")
        self.verdict("mission-validator-scrutiny", "n/a", "validation/M1-scrutiny.md")
        self.verdict("mission-reviewer", {"A002": "cannot tell"}, "validation/M1-review-F001-r2.md", "F001", round_no=2)
        self.assertEqual(validate.verdict_summary(self.m, "M1", rows).split("\n")[1],
                         "A002 [structural] \u2014 reviewer F001: cannot tell (validation/M1-review-F001-r2.md); "
                         "reviewer F002: satisfied (validation/M1-review-F002.md) \u2014 contract: unproven")

    def test_proposals_sources_and_origins(self):
        feats = files.read_features(self.m)
        rows = validate.milestone_assertions(self.m, "M1")
        obj = {"findings": [
            {"title": "leak", "assertion": "A002", "found_by": "mission-reviewer (review-F001)", "where": "x", "severity": "high",
             "cluster": "C01", "cluster_label": "tenant", "blocking": True, "disposition": "repair", "why": "defect"},
            {"title": "same leak, summary", "assertion": "A002", "found_by": "review-F002#1", "severity": "high",
             "cluster": "C01", "blocking": True, "disposition": "repair"},
            {"title": "lint", "assertion": None, "found_by": "mission-validator-scrutiny", "severity": "low",
             "cluster": "C02", "blocking": False, "disposition": "accept", "why": "debt"},
            {"title": "slow", "assertion": "A001", "found_by": "the reviewer", "severity": "low",
             "cluster": "C03", "blocking": False, "disposition": "waive", "why": "beyond max"},
            {"title": "odd", "assertion": "A002", "found_by": "reviewer", "severity": "low", "cluster": "C04", "blocking": False, "disposition": "accept"},
            {"title": "ui", "assertion": None, "found_by": "mission-validator-behavior", "severity": "low", "cluster": "C05", "blocking": False, "disposition": "accept"}],
            "repairs": [{"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"], "procedures": "make test-unit"}],
            "contract_wrong": False}
        self.assertEqual(judgment.validate_negotiate(obj), [])
        fus, reps = validate.proposals(obj, "M1", [f for f in feats if f.milestone == "M1"], rows)
        self.assertEqual([f["source"] for f in fus], ["M1-review-F001", "M1-review-F002", "M1-scrutiny", "M1-review-F001", "M1-review", "M1-behavior"])
        self.assertEqual((fus[0]["disposition"], fus[0]["why"], fus[2]["where"], fus[3]["blocking"]), ("repair", "defect", "", False))
        self.assertEqual(reps, [{"cluster": "C01", "title": "tenancy filter", "assertions": ["A002"], "files": ["analytics/service.py"],
                                 "procedures": "make test-unit", "out_of_scope": "", "origins": ["F001", "F002"]}])
        # no reviewed feature named: the origins are what the assertion routes to in the milestone
        obj["findings"] = [dict(obj["findings"][2], disposition="repair", cluster="C01", assertion="A001")]
        fus, reps = validate.proposals(obj, "M1", [f for f in feats if f.milestone == "M1"], rows)
        self.assertEqual(reps[0]["origins"], ["F001", "F002"])
        obj["repairs"][0]["assertions"] = ["A001"]
        self.assertEqual(validate.proposals(obj, "M1", [f for f in feats if f.milestone == "M1"], rows)[1][0]["origins"], ["F001"])
        # applied through register, the registry names both sides and passes check.sh
        ctx = make_ctx(self.m, self.tmp)
        fu_ids, fids = steps.register(ctx, "M1", fus, reps)
        self.assertEqual((fu_ids, fids), (["FU001"], ["F004"]))
        self.assertIn("## FU001 \u2014 lint (from M1-scrutiny)\n", files.read_text(self.m / "followups.md"))
        self.assertIn("- **Repairs:** C01 (FU001) of F001, F002\n", files.read_text(self.m / "features.md"))
        self.assertEqual(check_sh(self.m)[0], 0)

    def test_stop_phase_kwarg(self):
        ctx = make_ctx(self.m, self.tmp)
        files.write_state_fields(self.m, phase="validating")
        self.assertEqual(steps.stop(ctx, "done", detail="all done", needs="terminal steps", phase="validating"), 0)
        self.assertEqual(files.read_state(self.m).phase, "validating")
        self.assertEqual(steps.stop(ctx, "limit-reached", detail="x", needs="re-run"), 3)
        self.assertEqual(files.read_state(self.m).phase, "validating")
        self.assertEqual(steps.stop(ctx, "done", detail="x", phase="done"), 0)
        self.assertEqual(files.read_state(self.m).phase, "done")
        self.assertEqual(steps.stop(ctx, "gate-blocked", detail="x", needs="y", halt=True), 5)
        self.assertEqual(files.read_state(self.m).phase, "halted")
        self.assertEqual(journal.count(self.m, "halt"), 1)
        self.assertEqual(journal.count(self.m, "stop"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=1)
