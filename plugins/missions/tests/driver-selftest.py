#!/usr/bin/env python3
"""Unit checks for the driver that need no harness: the mission-file parsers and writers against
the trace base fixture, the claude/codex envelope parsers on captured shapes, and the command
builders. Run by tests/traces/run.sh; standalone: python3 tests/driver-selftest.py"""
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

import subprocess  # noqa: E402
import time  # noqa: E402

from missions import cli, files, grade as grading, journal, judgment, loop, prep, prompts, verdicts, watchdog  # noqa: E402
from missions.adapters.claude import ClaudeAdapter, parse_envelope  # noqa: E402
from missions.adapters.codex import CodexAdapter, parse_events  # noqa: E402
from missions.adapters.stub import StubAdapter  # noqa: E402
from missions.outcome import Grade, Outcome, RunRequest, classify  # noqa: E402

BASE = HERE / "traces" / "_base" / "mission"


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


class BudgetAndDesignTests(Fixture):
    def test_budget(self):
        b = files.read_budget(self.m)
        self.assertEqual(b["dollar_cap"], 100.0)
        self.assertEqual(b["dispatch_cap"], 20.0)
        self.assertEqual(b["wall_cap_h"], 10.0)
        self.assertEqual(b["repair_rounds"], 2.0)
        self.assertEqual(b["terminal_reserve_pct"], 15.0)

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
                                     rejection={"step": "F001#1", "problems": ["missing sections: ## Left undone"]})
        self.assertTrue(text.startswith("Mission: demo. Feature: F001 — feature F001.\n"))
        self.assertIn("A001 — Omitting the window equals the whole day  [structural]  proof: min: named test", text)
        self.assertIn("| D001 |", text)
        self.assertIn('starts with "F001:"', text)
        self.assertIn("was rejected", text)
        self.assertIn("Do not spawn background work", text)


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


class RequestTests(Fixture):
    def ctx(self, harness, cfg=None):
        from missions.steps import Context
        return Context(mission_dir=self.m, checkout=self.tmp, plugin=PLUGIN, cfg=cfg or {"roles": {}},
                       adapter=None, run_id="r1", harness=harness)

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
    def out(self, rc=0, timed_out=False, killed_by=None):
        return Outcome(task="F001#1", rc=rc, elapsed_s=1.0, timed_out=timed_out, killed_by=killed_by,
                       cost={"unit": "unknown", "value": None, "source": ""}, harness="stub", model=None,
                       stdout_path=Path("/x"), stderr_path=Path("/y"))

    def written(self, **kw):
        g = Grade(handoff_exists=True, handoff_written=True, status="complete", sha="a" * 40, commit_on_branch=True)
        for k, v in kw.items():
            setattr(g, k, v)
        return g

    def test_eight_classes(self):
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
        d = verdicts.parse_defects(REVIEW)
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["severity"], d[0]["location"]), ("high", "analytics/service.py:3"))
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
    "MISSIONS_PUSH_TOKEN": "z", "PYTHONPATH": "/x",
}


class PrepEnvTests(Fixture):
    def env(self, role="worker", harness="stub", passthrough=(), src=DRIVER_ENV):
        return prep.build_env(self.m, self.m / "runs" / "F001#1", role, "F001", "F001#1", "implementing", harness,
                              branch="mission/demo", feature_files=["analytics/service.py", "tests/unit/test_a.py"],
                              passthrough=list(passthrough), base_env=src)

    def test_whitelist_keeps_drops_and_passthrough(self):
        env = self.env(passthrough=["KEEP_ME", "MYAPP_*", "GH_TOKEN", "CLAUDE_CODE_*", "GIT_CONFIG_COUNT"])
        for k in ("PATH", "HOME", "LC_ALL", "XDG_RUNTIME_DIR", "MISSIONS_TEST", "HTTPS_PROXY", "TMPDIR",
                  "KEEP_ME", "MYAPP_URL", "MYAPP_KEY"):
            self.assertIn(k, env, k)
        # not on any list: gone, whatever the name pattern
        for k in ("MY_SECRET", "FOO_TOKEN", "AWS_ACCESS_KEY", "DB_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PYTHONPATH"):
            self.assertNotIn(k, env, k)
        # the never-list beats the passthrough
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "SSH_AUTH_SOCK", "MISSIONS_PUSH_TOKEN"):
            self.assertNotIn(k, env, k)
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
        names = files.read_text(self.m / "runs" / "F001#1" / "env-names.txt").split()
        self.assertIn("MISSIONS_FILES", names)
        self.assertNotIn("GH_TOKEN", names)
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


class HostLeaseTests(Fixture):
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
    def test_preflight_restores_blind_and_warns_on_no_host_lease(self):
        files.write_config(self.m, {"harness": "stub", "checkout": ".", "branch": "mission/demo", "host_lease": False,
                                    "adapters": {"stub": {"script_dir": str(self.tmp)}}})
        cell = self.m / ".blind" / "review-F001#1"
        (cell / "handoffs").mkdir(parents=True)
        (cell / "handoffs" / "F001.md").write_text("hidden", encoding="utf-8")
        os.chmod(cell, 0)
        problems, warnings, cfg = loop.preflight(self.m, PLUGIN)
        self.assertEqual(problems, [])
        self.assertTrue(any("restored .blind/review-F001#1" in w for w in warnings), warnings)
        self.assertTrue(any("host_lease is false" in w for w in warnings), warnings)
        self.assertEqual(files.read_text(self.m / "handoffs" / "F001.md"), "hidden")
        self.assertFalse((self.m / ".blind").exists())
        self.assertIn("restored .blind/review-F001#1", journal.last(self.m, "note")["text"])


class CliInitTests(Fixture):
    def test_init_writes_roles_lease_and_env(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init", str(self.m), "--harness", "stub", "--stub-dir", str(self.tmp)]), 0)
        cfg = files.read_config(self.m)
        self.assertEqual(sorted(cfg["roles"]), ["behavior", "judgment", "reviewer", "scrutiny", "worker"])
        self.assertEqual(cfg["roles"]["reviewer"], {"timeout_s": 1500, "budget_usd": 6, "model": None, "effort": None})
        self.assertEqual(cfg["roles"]["judgment"], {"timeout_s": 300, "budget_usd": 2, "model": None})
        self.assertEqual(files.read_text(self.m / "driver.json").count('"budget_usd": 8,'), 1)
        self.assertNotIn("effort", cfg["roles"]["worker"])
        self.assertIs(cfg["host_lease"], True)
        self.assertEqual(cfg["env"], {"passthrough": []})
        self.assertEqual(cfg["adapters"]["stub"]["script_dir"], str(self.tmp))

    def test_until_choices(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["run", str(self.m), "--until", "bogus"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
