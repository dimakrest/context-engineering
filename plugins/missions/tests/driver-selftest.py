#!/usr/bin/env python3
"""Unit checks for the driver that need no harness: the mission-file parsers and writers against
the trace base fixture, the claude/codex envelope parsers on captured shapes, and the command
builders. Run by tests/traces/run.sh; standalone: python3 tests/driver-selftest.py"""
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

from missions import files, grade as grading, journal, prompts, watchdog  # noqa: E402
from missions.adapters.claude import ClaudeAdapter, parse_envelope  # noqa: E402
from missions.adapters.codex import CodexAdapter, parse_events  # noqa: E402
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
