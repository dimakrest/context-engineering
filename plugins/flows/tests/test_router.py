#!/usr/bin/env python3
"""Tests for router.py and the check scripts. No Orca, no network: tests/fake-orca stands in for the CLI.

    python3 tests/test_router.py            # all
    python3 tests/test_router.py -k check   # by name

Each test gets its own state directory and its own fake Orca, and stops every daemon it started.
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KIT = TESTS.parent / "router"
ROUTER = str(KIT / "router.py")
CHECKS = KIT / "checks"
TEMPLATES = KIT / "templates"
PROFILES = KIT / "profiles"
PROFILE_VARS = ("RULES", "TEST_CMD", "UNIT_DIRS", "TEST_PATHS", "TEST_CONFIG", "COPY_SETUP", "LINT_CMD", "FROZEN_PATHS",
                "EXTRA_SUITE", "COMMIT_CMD")


def profile(name):
    return json.loads((PROFILES / f"{name}.json").read_text())


class RouterCase(unittest.TestCase):
    silent_min = "30"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="router-test-"))
        self.state, self.fake, self.kit, self.out = (self.tmp / n for n in ("state", "fake", "kit", "out"))
        for d in (self.fake, self.kit / "specs", self.out):
            d.mkdir(parents=True)
        (self.kit / "checks").symlink_to(CHECKS)
        (self.kit / "specs" / "w.md").write_text("Target: {OUT}\nChange: write {OUT}/{STEP}.txt (attempt {ATTEMPT})\n")
        self.env = dict(os.environ, ROUTER_STATE=str(self.state), FAKE_ORCA_DIR=str(self.fake),
                        ORCA_CLI_COMMAND=str(TESTS / "fake-orca"), ROUTER_WAIT_MS="300", ROUTER_POLL_S="0.05",
                        ROUTER_SILENT_MIN=self.silent_min, ROUTER_REGISTRY_WAIT_S="0.3",
                        ORCA_TERMINAL_HANDLE="term_fake", ORCA_PANE_KEY="pane_fake")
        self.env.pop("SCRATCH", None)

    def tearDown(self):
        self.R("stop", "--all")
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers
    def R(self, *args, ok=None):
        p = subprocess.run([sys.executable, getattr(self, "router", ROUTER)] + list(args), capture_output=True, text=True,
                           env=self.env, timeout=60)
        if ok is True:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.returncode, p.stdout + p.stderr

    def scenario(self, *rules):
        (self.fake / "scenario.json").write_text(json.dumps({"rules": list(rules)}))

    def chain(self, steps, pr="t1", **variables):
        path = self.kit / f"chain-{pr}.json"
        path.write_text(json.dumps({"name": pr, "steps": steps}))
        kv = [f"{k}={v}" for k, v in dict(OUT=str(self.out), **variables).items()]
        return self.R("chain", pr, "--def", str(path), *kv)

    def bell(self, minutes=0.25):
        """Ring once: what the coordinator would read."""
        return self.R("wait", "--timeout-min", str(minutes))[1]

    def bell_for(self, needle, tries=3):
        """Ring until a screen contains the needle: an unrelated ring may be older."""
        text = ""
        for _ in range(tries):
            text = self.bell()
            if needle in text:
                break
        return text

    def chain_state(self, pr="t1"):
        return json.loads((self.state / "chains" / pr / "state.json").read_text())

    def until(self, cond, seconds=15, what="condition"):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {what}")

    def starts(self):
        f = self.fake / "starts.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def journal(self):
        f = self.state / "journal.md"
        return f.read_text() if f.exists() else ""

    @staticmethod
    def done(after=0.2, outcome="succeeded", **kw):
        return dict({"after": after, "type": "worker_done", "outcome": outcome}, **kw)

    def worker(self, sid, **kw):
        return dict({"id": sid, "agent": "claude", "model": "m-sonnet", "spec": "w.md"}, **kw)

    def repo(self):
        """A git checkout with one test file, to stand for the PR's worktree."""
        wt = self.tmp / "wt"
        (wt / "tests").mkdir(parents=True)
        self.git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(self.git + ["init", "-q"], check=True)
        (wt / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
        subprocess.run(self.git + ["add", "-A"], check=True)
        subprocess.run(self.git + ["commit", "-q", "-m", "base"], check=True)
        return wt

    def gitcmd(self, *args):
        return " ".join(shlex.quote(x) for x in self.git + list(args))

    def kill_runner(self, pr="t1"):
        os.kill(int((self.state / "chains" / pr / "runner.pid").read_text()), signal.SIGKILL)

    def until_act(self):
        """Ring until the doorbell says ACT (exit 3); an older ring may come first."""
        rc, out = 0, ""
        for _ in range(4):
            rc, out = self.R("wait", "--timeout-min", "0.1")
            if rc == 3:
                break
        self.assertEqual(rc, 3, out)
        return out


class HappyPath(RouterCase):
    def test_runs_to_the_gate_without_ringing_then_finishes(self):
        self.scenario({"match": "t1 a$", "events": [{"after": 0.05, "type": "heartbeat", "phase": "writing"}, self.done(0.3)],
                       "effect": f"echo OK > {self.out}/a.txt"},
                      {"match": "t1 b$", "events": [self.done(0.6)], "effect": f"echo OK > {self.out}/b.txt"},
                      {"match": "t1 c$", "events": [self.done(0.3)]})
        self.R("init", ok=True)
        rc, out = self.chain([
            self.worker("a", checks=["{CHECKS}/file-exists.sh {OUT}/a.txt"]),
            self.worker("b", group="g"), self.worker("c", group="g"),
            {"id": "note", "type": "script", "exports": ["WORD"], "run": "echo VAR WORD=$(cat {OUT}/b.txt); echo OK noted"},
            {"id": "gate", "type": "gate", "title": "merge", "show": ["echo OK the word is {WORD}"]},
            self.worker("z"),
        ])
        self.assertEqual(rc, 0, out)
        text = self.bell()
        self.assertIn("WAKE gate · t1 · gate: merge", text)          # the first and only ring so far
        self.assertIn("OK the word is OK", text)                      # a script step's VAR reached the gate
        self.assertIn("router.py resume t1", text)
        self.assertEqual([s["title"] for s in self.starts()], ["t1 a", "t1 b", "t1 c"])
        b, c = self.starts()[1:3]
        self.assertLess(abs(b["t"] - c["t"]), 0.5)                    # the group started together
        self.assertEqual(len((self.fake / "releases.log").read_text().split()), 3)   # every settled terminal released
        self.assertEqual(len(list((self.state / "liveness").iterdir())), 1)          # the heartbeat became a file
        self.assertEqual(self.chain_state()["status"], "paused")
        self.assertIn("check: OK a.txt: 1 lines", self.journal())
        self.assertIn("OK quiet", self.bell(0.01))                    # nothing else is waiting

        self.scenario({"match": "t1 z$", "events": [self.done(0.1)]})
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done", what="the chain to finish")
        self.assertIn("chain complete", self.journal())
        self.assertIn("OK quiet", self.bell(0.01))                    # finishing does not ring

    def test_spec_is_rendered_and_the_run_is_named(self):
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", effort="high", worktree="path:{OUT}")])
        self.until(lambda: self.chain_state()["status"] == "done")
        s = self.starts()[0]
        self.assertEqual(s["spec"], f"Target: {self.out}\nChange: write {self.out}/a.txt (attempt 1)\n")
        self.assertEqual((s["agent"], s["model"], s["effort"], s["worktree"], s["run"]),
                         ("claude", "m-sonnet", "high", f"path:{self.out}", "run_fake"))

    def test_a_step_whose_condition_is_false_is_skipped(self):
        self.scenario({"match": "t1 b$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", when="test -n {GREEN}"), self.worker("b")], GREEN="")
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual([s["title"] for s in self.starts()], ["t1 b"])
        self.assertEqual(self.chain_state()["steps"][0]["status"], "skipped")


class Exceptions(RouterCase):
    def test_a_failed_worker_pauses_and_a_retry_carries_the_note(self):
        self.scenario({"match": "t1 a", "events": [self.done(0.1, "failed", subject="could not", body="The gate was red.",
                                                           reportPath="/tmp/report.md")]},
                      {"match": "t1 a", "events": [self.done(0.1)]},
                      {"match": "t1 b", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a"), self.worker("b")])
        text = self.bell()
        self.assertIn("WAKE failed · t1 · a (attempt 1)", text)
        self.assertIn("worker_done failed: could not", text)
        self.assertIn("summary: The gate was red.", text)
        self.assertIn("report: /tmp/report.md", text)
        self.assertIn("released (closed_agent_terminal)", text)
        self.assertEqual(len(self.starts()), 1)                       # b did not start
        rc, out = self.R("resume", "t1")
        self.assertEqual(rc, 1)
        self.assertIn("step a is failed", out)                        # a failure is never resumed past by accident
        self.R("retry", "t1", "--note", "use the other fixture", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        second = self.starts()[1]
        self.assertEqual(second["title"], "t1 a (attempt 2)")
        self.assertTrue(second["spec"].endswith("Note from the coordinator (attempt 2): use the other fixture"))
        self.assertIn("(attempt 2)", second["spec"])
        self.assertEqual(self.starts()[2]["title"], "t1 b")

    def test_a_check_that_is_not_ok_pauses_and_resume_rechecks(self):
        self.scenario({"match": "t1 a", "events": [self.done(0.1)]}, {"match": "t1 b", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", checks=["echo OK first", "{CHECKS}/verdict-line.sh {OUT}/v.md"]), self.worker("b")])
        text = self.bell()
        self.assertIn("WAKE check · t1 · a (attempt 1): the worker reported success, a check is not OK", text)
        self.assertIn("OK first", text)
        self.assertIn(f"NOT OK verdict: no file {self.out}/v.md", text)
        (self.out / "v.md").write_text("VERDICT: FAIL 2 survivors\n")
        self.R("resume", "t1", ok=True)
        self.assertIn("NOT OK v.md: VERDICT: FAIL 2 survivors", self.bell())   # still not OK: it rings again
        (self.out / "v.md").write_text("VERDICT: PASS\n")
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual([s["title"] for s in self.starts()], ["t1 a", "t1 b"])   # a was not run again

    def test_accept_overrides_and_is_journaled(self):
        self.scenario({"match": "t1 a", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", checks=["echo NOT OK always"])])
        self.assertIn("NOT OK always", self.bell())
        self.R("resume", "t1", "--accept", "known flake, issue 12", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertIn("coordinator: accepted check_failed: known flake, issue 12", self.journal())

    def test_a_question_rings_without_pausing_and_the_reply_reaches_the_worker(self):
        self.scenario({"match": "t1 a", "events": [{"after": 0.1, "type": "question", "question": "Keep the old name?",
                                                    "options": ["yes", "no"], "on_reply": [self.done(0.1)]}]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        text = self.bell()
        self.assertIn("WAKE question · t1 · a", text)
        self.assertIn("question: Keep the old name?", text)
        self.assertIn("options: yes | no", text)
        mid = text.split("message ")[1].split()[0]
        self.assertIn(f'router.py reply {mid} "<answer>"', text)
        self.assertEqual(self.chain_state()["status"], "running")      # the chain did not pause
        self.R("reply", mid, "yes", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(json.loads((self.fake / "replies.jsonl").read_text())["body"], "yes")

    def test_a_worker_that_cannot_start_pauses_and_can_be_retried_as_another_agent(self):
        self.scenario({"match": "t1 a", "start": "fail"}, {"match": "t1 a", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", agent="codex", model="gpt-x")])
        text = self.bell()
        self.assertIn("WAKE start · t1 · a (attempt 1): the worker did not start", text)
        self.assertIn("fake: the agent did not become ready", text)
        self.assertTrue((self.state / "chains" / "t1" / "start-a-1.json").exists())   # the receipt is kept
        self.R("retry", "t1", "--agent", "claude", "--model", "fable", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual([(s["agent"], s["model"]) for s in self.starts()], [("codex", "gpt-x"), ("claude", "fable")])

    def test_a_failed_script_step_pauses(self):
        self.R("init", ok=True)
        self.chain([{"id": "ci", "type": "script", "run": "echo NOT OK ci: 1 fail; exit 1"}])
        text = self.bell()
        self.assertIn("WAKE script · t1 · ci: exit 1", text)
        self.assertIn("NOT OK ci: 1 fail", text)

    def test_resume_from_a_step_runs_it_again_with_the_note(self):
        self.scenario({"match": "t1 a", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.chain([self.worker("a"), {"id": "accept", "type": "gate", "title": "contract"}])
        self.assertIn("WAKE gate · t1 · accept: contract", self.bell())
        self.R("resume", "t1", "--from", "a", "--note", "row 3 has no evidence", ok=True)
        self.assertIn("WAKE gate", self.bell())
        self.assertTrue(self.starts()[1]["spec"].endswith("Note from the coordinator (attempt 2): row 3 has no evidence"))


class ReadOnly(RouterCase):
    def test_a_read_only_step_that_moves_head_is_caught(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True)
        commit = " ".join(git) + " commit -q --allow-empty -m sneaky"
        self.scenario({"match": "t1 a", "events": [self.done(0.1)]},
                      {"match": "t1 b", "events": [self.done(0.1)], "effect": commit},
                      {"match": "t1 c", "events": [self.done(0.1)], "effect": f"touch {wt}/stray.txt"})
        self.R("init", ok=True)
        self.chain([self.worker("a", readonly=True), self.worker("b", readonly=True), self.worker("c", readonly=True)], WT=str(wt))
        text = self.bell()
        self.assertIn("WAKE check · t1 · b", text)                    # a passed without ringing
        self.assertIn("NOT OK read-only step moved HEAD", text)
        self.assertIn("OK read-only: HEAD", self.journal())
        self.R("resume", "t1", "--accept", "test", ok=True)
        self.assertIn("NOT OK read-only step changed uncommitted files", self.bell())

    def test_readonly_without_a_worktree_is_refused_before_anything_starts(self):
        self.R("init", ok=True)
        rc, out = self.chain([self.worker("a", readonly=True)])
        self.assertEqual(rc, 1)
        self.assertIn("readonly needs the WT variable", out)
        rc, out = self.chain([self.worker("a", readonly=True)], WT=str(self.out))
        self.assertEqual(rc, 1)
        self.assertIn(f"readonly needs WT to be a git checkout, and {self.out} is not one", out)   # it would pass on nothing
        self.assertFalse((self.state / "chains" / "t1").exists())


class Silence(RouterCase):
    silent_min = "0.01"   # 0.6 s

    def test_a_silent_worker_rings_once_and_is_not_touched(self):
        self.scenario({"match": "t1 a", "events": [self.done(2.5)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        text = self.bell()
        self.assertIn("WAKE silent · t1 · a", text)
        self.assertIn("silence is not exit", text)
        self.assertFalse((self.fake / "releases.log").exists())        # nothing was stopped or released on silence
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(list((self.state / "wake" / "seen").glob("*-silent.txt"))), 1)
        self.assertEqual(len(list((self.state / "wake").glob("*-silent.txt"))), 0)   # once per silence


class Daemons(RouterCase):
    def test_a_replayed_delivery_is_handled_once(self):
        self.scenario({"match": "t1 a", "events": [{"after": 0.1, "type": "question", "question": "q?", "on_reply": [self.done(0.1)]}]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        text = self.bell()
        self.assertIn("WAKE question", text)
        self.R("reply", text.split("message ")[1].split()[0], "yes", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        # Orca replays a delivery that was not acknowledged (the daemon died after handling it): hand both messages out again
        events = sorted((self.state / "events").glob("*/*.json"))
        self.assertEqual(len(events), 2)
        for i, ev in enumerate(events):
            msg = json.loads(ev.read_text())["message"]
            (self.fake / "queue" / f"{i:020d}-{msg['id']}.json").write_text(json.dumps(msg))
        self.until(lambda: not list((self.fake / "queue").iterdir()), what="the replay to be taken")
        self.until(lambda: not (self.fake / "delivery.json").exists(), what="the replay to be acknowledged")
        self.assertIn("OK quiet", self.bell(0.01))                     # no second ring for the same question
        self.assertEqual(self.journal().count("question:"), 1)
        self.assertEqual(self.journal().count("worker_done succeeded"), 1)
        self.assertEqual(len((self.fake / "releases.log").read_text().split()), 1)   # and no second release

    def test_the_doorbell_reports_a_dead_mailbox_daemon_and_a_dead_runner(self):
        self.scenario({"match": "t1 a", "events": [self.done(1.5)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "running")
        os.kill(int((self.state / "chains" / "t1" / "runner.pid").read_text()), signal.SIGKILL)
        rc, out = self.R("wait", "--timeout-min", "0.2")
        self.assertEqual(rc, 3)
        self.assertIn("ACT: chain t1 is marked running but its runner is gone: router.py resume t1", out)
        self.R("resume", "t1", ok=True)                                # picks the running worker up again
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(self.starts()), 1)                        # the worker was not started twice
        os.kill(int((self.state / "mailbox.pid").read_text()), signal.SIGKILL)
        rc, out = self.R("wait", "--timeout-min", "0.2")
        self.assertEqual(rc, 3)
        self.assertIn("ACT: the mailbox daemon is not running", out)

    def test_the_doorbell_looks_twice_before_it_calls_a_runner_gone(self):
        self.scenario()
        self.R("init", ok=True)
        self.chain([{"id": "accept", "type": "gate", "title": "contract"}])
        self.assertIn("WAKE gate", self.bell())
        # A runner that pauses between the doorbell's two reads: the state still says running, the pid is gone,
        # and its ring lands a moment later. The ring must win; "resume" here would walk through the gate.
        path = self.state / "chains" / "t1" / "state.json"
        path.write_text(json.dumps(dict(self.chain_state(), status="running")))
        p = subprocess.Popen([sys.executable, ROUTER, "wait", "--timeout-min", "0.2"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, env=self.env)
        time.sleep(0.4)
        (self.state / "wake" / "1-t1-gate.txt").write_text("WAKE gate · t1 · accept: late ring\n")
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("late ring", out)
        self.assertNotIn("ACT:", out)
        # A runner that is only just starting: the state says running a moment before its pid file is there.
        p = subprocess.Popen([sys.executable, ROUTER, "wait", "--timeout-min", "0.05"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, env=self.env)
        time.sleep(0.4)
        shutil.copy(self.state / "mailbox.pid", self.state / "chains" / "t1" / "runner.pid")   # any live router.py process
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("OK quiet", out)

    def test_orca_unreachable_rings_after_five_failed_checks(self):
        self.scenario()
        (self.fake / "fail_checks").write_text("5")
        self.R("init", ok=True)
        text = self.bell(0.5)
        self.assertIn("WAKE daemon · the mailbox check failed 5 times in a row: runtime_unavailable", text)
        self.assertIn("contact loss is not a worker's death", text)
        self.assertTrue(self.R("status")[1].startswith("router  run_fake · mailbox alive"))   # and it kept going

    def test_a_stranger_and_an_ad_hoc_worker(self):
        self.scenario({"match": "arbiter", "events": [self.done(0.1, subject="row 4 is wrong", body="The test asks more than the row.")]})
        self.R("init", ok=True)
        subprocess.run([str(TESTS / "fake-orca"), "orchestration", "inject", "--event",
                        json.dumps(self.done(0, subject="hello"))], env=self.env, capture_output=True, check=True)
        text = self.bell()
        self.assertIn("WAKE unrouted · worker_done succeeded from a worker the router did not start", text)
        self.assertFalse((self.fake / "releases.log").exists())        # not ours: not released
        rc, out = self.R("worker", "b3.1 arbiter", "--spec", "compare", "--agent", "claude", "--pr", "b3.1")
        self.assertEqual(rc, 0, out)
        text = self.bell()
        self.assertIn("WAKE done · ad hoc worker b3.1 arbiter · succeeded", text)
        self.assertIn("summary: The test asks more than the row.", text)
        self.assertEqual(len((self.fake / "releases.log").read_text().split()), 1)

    def test_chain_limits_and_validation(self):
        self.scenario({"match": ".", "events": [self.done(5)], "repeat": True})
        self.R("init", ok=True)
        rc, out = self.chain([self.worker("a", spec="missing.md"), self.worker("B"), self.worker("c", checks=["x {NOPE}"])], pr="bad")
        self.assertEqual(rc, 1)
        for needle in ("spec file missing", "step id 'B'", "no value for {NOPE}", "nothing created"):
            self.assertIn(needle, out)
        self.assertEqual(self.chain([self.worker("a")], pr="p1")[0], 0)
        self.assertEqual(self.chain([self.worker("a")], pr="p2")[0], 0)
        rc, out = self.chain([self.worker("a")], pr="p3")
        self.assertEqual(rc, 1)
        self.assertIn("2 chains are open (p1, p2) and ROUTER_MAX_CHAINS is 2", out)
        rc, out = self.chain([self.worker("a")], pr="p1")
        self.assertIn("a chain already exists", out)
        status = self.R("status")[1]
        self.assertIn("p1      running · 0/1 steps · a running (attempt 1)", status)

    def test_a_runner_killed_during_a_start_does_not_start_a_second_worker(self):
        self.scenario({"match": "t1 a", "start_delay": 1.5, "events": [self.done(1.0)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "starting", what="the write-ahead")
        os.kill(int((self.state / "chains" / "t1" / "runner.pid").read_text()), signal.SIGKILL)
        self.until(lambda: self.starts() and (self.fake / "queue").exists() and list((self.fake / "queue").iterdir()) or
                   (self.state / "events").exists() and list((self.state / "events").glob("*/*.json")), what="the fake to finish the start")
        rc = 0
        for _ in range(3):   # the orphan's own worker_done may ring first, as a stranger's; then ACT: the runner is gone
            rc = self.R("wait", "--timeout-min", "0.1")[0]
            if rc == 3:
                break
        self.assertEqual(rc, 3)
        self.R("resume", "t1", ok=True)
        text = self.bell_for("WAKE start")
        self.assertIn("WAKE start · t1 · a (attempt 1): it is not known whether the worker started", text)
        self.assertIn('a worker titled "t1 a" may be running', text)
        self.assertEqual(len(self.starts()), 1)                                    # resume did not start a second one
        rc, out = self.R("resume", "t1")
        self.assertEqual(rc, 1)                                                    # nor does a plain resume
        rc, out = self.R("resume", "t1", "--from", "a")
        self.assertEqual(rc, 1)                                                    # nor running the step again
        self.assertIn("it is not known whether step a's worker started", out)
        self.assertEqual(len(self.starts()), 1)
        dispatch = self.starts()[0]["dispatch"]
        self.until(lambda: list((self.state / "events").glob("*/*-worker_done-*.json")), what="the orphan to finish")
        self.R("resume", "t1", "--adopt", dispatch, ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(self.starts()), 1)
        self.assertIn(dispatch, (self.fake / "releases.log").read_text())           # the adopted worker's terminal is released
        reg = json.loads((self.state / "dispatches" / f"{dispatch}.json").read_text())
        self.assertTrue(reg.get("settled"))                                        # or it would ring as silent later

    def test_stop_during_a_start_lets_the_start_finish(self):
        self.scenario({"match": "t1 a", "start_delay": 1.5, "events": [self.done(0.3)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "starting")
        self.R("stop", "t1")
        self.until(lambda: not (self.state / "chains" / "t1" / "runner.pid").exists(), what="the runner to end")
        st = self.chain_state()
        self.assertEqual((st["status"], st["steps"][0]["status"]), ("stopped", "running"))   # the receipt was recorded
        self.assertTrue(st["steps"][0]["attempts"][-1]["dispatch"].startswith("ctx_"))
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(self.starts()), 1)

    def test_a_step_with_a_live_worker_is_not_run_again(self):
        self.scenario({"match": "t1 a", "events": [self.done(2.0)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "running")
        self.R("stop", "t1", ok=True)
        rc, out = self.R("resume", "t1", "--from", "a")
        self.assertEqual(rc, 1)
        self.assertIn("step a still has a live worker", out)
        self.R("resume", "t1", ok=True)                                # a plain resume waits for the same worker
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(self.starts()), 1)

    def test_an_unanswered_question_stays_in_status_and_a_ring_can_be_read_again(self):
        self.scenario({"match": "t1 a", "events": [{"after": 0.1, "type": "question", "question": "Which base?", "on_reply": [self.done(0.1)]}]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        text = self.bell()
        mid = text.split("message ")[1].split()[0]
        self.assertIn(f"question {mid} · t1 a", self.R("status")[1])
        self.assertIn("NOT ANSWERED", self.R("status")[1])
        again = self.R("last")[1]
        self.assertIn("question: Which base?", again)                  # the ring is not lost once it was shown
        self.R("reply", mid, "pipecat-1.11", ok=True)
        self.assertNotIn("NOT ANSWERED", self.R("status")[1])
        self.until(lambda: self.chain_state()["status"] == "done")

    def test_a_paused_chain_keeps_its_slot(self):
        self.scenario({"match": ".", "events": [self.done(5)], "repeat": True})
        self.R("init", ok=True)
        self.assertEqual(self.chain([{"id": "accept", "type": "gate"}], pr="p1")[0], 0)
        self.assertIn("WAKE gate · p1", self.bell())                   # p1 waits at its gate: no runner, still a slot
        self.assertEqual(self.chain([self.worker("a")], pr="p2")[0], 0)
        rc, out = self.chain([self.worker("a")], pr="p3")
        self.assertEqual(rc, 1)
        self.assertIn("2 chains are open (p1, p2)", out)

    def test_values_are_quoted_in_commands_and_shell_variables_are_left_alone(self):
        (self.kit / "specs" / "s.md").write_text("Use ${HOME} and $SCRATCH as they are; the label is {LABEL}.\n")
        self.scenario({"match": "t1 a", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        evil = "a b; touch pwned $(touch pwned2)"                      # short and fixed: a long one is cut on the screen
        rc, out = self.chain([self.worker("a", spec="s.md", checks=["echo OK label: {LABEL}"]),
                              {"id": "accept", "type": "gate", "show": ["echo ${HOME:+home is set} {LABEL}"]}], LABEL=evil)
        self.assertEqual(rc, 0, out)
        text = self.bell()
        self.assertIn(f"home is set {evil}", text)                     # ${HOME} is the shell's, {LABEL} is one quoted word
        cwd = Path(self.chain_state()["def_dir"])                      # where the checks and the gate's show lines run
        self.assertEqual(cwd, self.kit.resolve())
        self.assertFalse((cwd / "pwned").exists() or (cwd / "pwned2").exists())
        self.assertIn(f"check: OK label: {evil}", self.journal())
        self.assertEqual(self.starts()[0]["spec"], f"Use ${{HOME}} and $SCRATCH as they are; the label is {evil}.\n")

    def test_quoting_holds_under_a_120_character_tmpdir(self):
        """A sandbox gives a long TMPDIR: the quoting test, run with one, must not depend on how long its paths are."""
        name = "test_values_are_quoted_in_commands_and_shell_variables_are_left_alone"
        long_tmp = self.tmp / "t"
        long_tmp = long_tmp.with_name("t" * max(1, 120 - len(str(long_tmp)) + 1))
        long_tmp.mkdir()
        self.assertGreaterEqual(len(str(long_tmp)), 120)
        p = subprocess.run([sys.executable, str(TESTS / "test_router.py"), "-k", name], capture_output=True, text=True,
                           env=dict(os.environ, TMPDIR=str(long_tmp)), timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Ran 1 test", p.stderr)

    def test_a_word_no_command_takes_is_refused(self):
        rc, out = self.R("status", "--bogus")
        self.assertEqual(rc, 2, out)
        self.assertIn("unrecognized arguments: --bogus", out)
        rc, out = self.R("chain", "t1", "--def", "nowhere.json", "--dry-rum", "K=V")   # a misspelt flag is not a variable
        self.assertEqual(rc, 2, out)
        self.assertIn("unrecognized arguments", out)

    def test_without_the_mailbox_daemon_no_chain_starts(self):
        rc, out = self.chain([self.worker("a")])
        self.assertEqual(rc, 1)
        self.assertIn("the mailbox daemon is not running: router.py init", out)


class Review(RouterCase):
    """One test per defect the independent review found on 2026-10-04."""

    FENCE = "{CHECKS}/files-untouched.sh {WT} {HEAD_BEFORE} {HEAD_AFTER} tests/"

    def test_a_retry_keeps_the_range_its_checks_look_at(self):
        wt = self.repo()
        edit = f"echo '# weakened' >> {wt}/tests/test_x.py && " + self.gitcmd("commit", "-q", "-am", "sneaky")
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)], "effect": edit},
                      {"match": r"t1 a \(attempt 2\)", "events": [self.done(0.1)]},                      # changes nothing
                      {"match": r"t1 a \(attempt 3\)", "events": [self.done(0.1)], "effect": self.gitcmd("revert", "--no-edit", "HEAD")},
                      {"match": "t1 b$", "events": [self.done(0.1)], "effect": self.gitcmd("commit", "-q", "--allow-empty", "-m", "stray")},
                      {"match": r"t1 b \(attempt 2\)", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", checks=[self.FENCE]), self.worker("b", readonly=True)], WT=str(wt))
        self.assertIn("NOT OK files-untouched: 1 guarded file(s) changed", self.bell())
        self.R("retry", "t1", ok=True)
        self.assertIn("NOT OK files-untouched: 1 guarded file(s) changed", self.bell())   # attempt 1's commit is still in the range
        self.R("retry", "t1", ok=True)                                                    # the retry that reverts it passes
        self.assertIn("NOT OK read-only step moved HEAD", self.bell())                    # a passed; b moved HEAD
        self.R("retry", "t1", ok=True)
        self.assertIn("NOT OK read-only step moved HEAD", self.bell())                    # an idle retry does not forgive it
        self.assertEqual([s["title"] for s in self.starts()],
                         ["t1 a", "t1 a (attempt 2)", "t1 a (attempt 3)", "t1 b", "t1 b (attempt 2)"])

    def test_resume_rechecks_against_the_tree_as_it_is_now(self):
        wt = self.repo()
        edit = f"echo '# weakened' >> {wt}/tests/test_x.py && " + self.gitcmd("commit", "-q", "-am", "sneaky")
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)], "effect": edit})
        self.R("init", ok=True)
        self.chain([self.worker("a", checks=[self.FENCE])], WT=str(wt))
        self.assertIn("NOT OK files-untouched", self.bell())
        subprocess.run(self.git + ["revert", "--no-edit", "HEAD"], check=True, capture_output=True)   # a fixer's commit
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(len(self.starts()), 1)

    def test_a_worker_that_ended_without_worker_done_can_be_written_off_when_orca_agrees(self):
        self.scenario({"match": "t1 a$", "events": []}, {"match": "attempt 2", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "running")
        d = self.starts()[0]["dispatch"]
        self.assertEqual(self.R("retry", "t1")[0], 1)                  # nothing failed, as far as the router knows
        rc, out = self.R("fail", "t1", "--why", "no heartbeat for an hour")
        self.assertEqual(rc, 1)
        self.assertIn(f"Orca does not list {d}; nothing was changed", out)
        row = {"dispatchId": d, "workerState": "ready", "projection": {"liveness": {"verdict": "live"},
               "nextAction": {"kind": "inspect", "argv": ["orca", "orchestration", "worker-read", "--dispatch", d]}}}
        (self.fake / "workers.json").write_text(json.dumps([row]))
        rc, out = self.R("fail", "t1", "--why", "no heartbeat for an hour")
        self.assertEqual(rc, 1)                                        # silence is not enough: Orca says it is live
        self.assertIn(f"Orca shows {d} as ready, liveness live; nothing was changed", out)
        self.assertIn(f"Orca's next action: orca orchestration worker-read --dispatch {d}", out)
        st = self.chain_state()
        self.assertEqual((st["status"], st["steps"][0]["status"]), ("running", "running"))
        self.assertIn("OK quiet", self.bell(0.02))                     # and its runner is still there
        row["projection"]["liveness"]["verdict"] = "exited"
        (self.fake / "workers.json").write_text(json.dumps([row]))
        rc, out = self.R("fail", "t1", "--why", "the transcript ends without worker_done")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK t1: a is marked failed (Orca: ready, liveness exited)", out)
        st = self.chain_state()
        self.assertEqual((st["status"], st["steps"][0]["status"]), ("paused", "failed"))
        self.assertTrue(json.loads((self.state / "dispatches" / f"{d}.json").read_text()).get("settled"))
        self.R("retry", "t1", "--note", "start over", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual([s["title"] for s in self.starts()], ["t1 a", "t1 a (attempt 2)"])

    def test_a_gate_whose_ring_was_lost_is_not_passed_by_resume(self):
        self.scenario()
        self.R("init", ok=True)
        self.chain([{"id": "accept", "type": "gate", "title": "contract", "show": ["sleep 2; echo OK shown"]}])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "blocked")
        self.kill_runner()                                             # it dies while it builds the screen
        self.assertIn("router.py resume t1", self.until_act())         # the doorbell's advice is to resume
        rc, out = self.R("resume", "t1")
        self.assertEqual(rc, 0, out)
        self.assertIn("gate accept had not rung yet; it rings now, and nothing was passed", out)
        text = self.bell()
        self.assertIn("WAKE gate · t1 · accept: contract", text)
        self.assertIn("OK shown", text)
        self.assertEqual(self.chain_state()["steps"][0]["status"], "blocked")
        self.assertNotIn("gate passed", self.journal())
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(self.journal().count("gate passed"), 1)

    def test_accept_on_a_step_that_owed_a_variable_asks_for_it(self):
        self.scenario()
        self.R("init", ok=True)
        self.chain([{"id": "draft", "type": "script", "exports": ["PR_URL"], "run": "echo NOT OK no pr; exit 1"},
                    {"id": "ci", "type": "script", "run": "echo OK ci for {PR_URL}"}])
        self.assertIn("WAKE script · t1 · draft", self.bell())
        rc, out = self.R("resume", "t1", "--accept", "opened by hand")
        self.assertEqual(rc, 1)
        self.assertIn("step draft was to set PR_URL, and a later step uses it", out)
        self.assertIn("--set PR_URL=<value>", out)
        self.R("resume", "t1", "--accept", "opened by hand", "--set", "PR_URL=https://x/pull/1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertIn("script exit 0: OK ci for https://x/pull/1", self.journal())

    def test_a_variable_nobody_set_rings_instead_of_killing_the_runner(self):
        self.scenario()
        self.R("init", ok=True)
        self.chain([{"id": "skipped", "type": "script", "when": "false", "exports": ["V"], "run": "echo VAR V=1"},
                    {"id": "bytes", "type": "script", "run": "printf '\\377\\376 junk\\n'; echo OK after bytes that are not UTF-8"},
                    {"id": "use", "type": "script", "run": "echo OK {V}"}])
        text = self.bell()
        self.assertIn("WAKE runner · t1 · use: the runner could not go on: no value for {V}", text)
        self.assertIn("router.py resume t1 --set NAME=<value>", text)
        self.assertEqual(self.chain_state()["status"], "paused")
        self.assertIn("OK quiet", self.bell(0.03))                     # paused, not a dead runner in a loop
        self.assertIn("OK after bytes that are not UTF-8", self.journal())
        self.R("resume", "t1", "--set", "V=7", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertIn("script exit 0: OK 7", self.journal())

    def test_init_speaks_to_orca_as_the_terminal_that_runs_it(self):
        self.scenario()
        self.R("init", ok=True)
        env = dict(self.env, ORCA_TERMINAL_HANDLE="term_new", ORCA_PANE_KEY="pane_new")
        p = subprocess.run([sys.executable, ROUTER, "init", "--run", "run_fake"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = (self.fake / "callers.log").read_text().split("\n")
        self.assertEqual(calls[-3:-1], ["run-use term_new", "run-current term_new"])   # not as the terminal in the old run.json
        self.assertEqual(json.loads((self.state / "run.json").read_text())["terminal"], "term_new")

    def test_a_stop_between_two_starts_of_a_group_starts_no_second_worker(self):
        self.scenario({"match": "t1 a", "start_delay": 1.5, "events": [self.done(0.2)]}, {"match": "t1 b", "events": [self.done(0.2)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", group="g"), self.worker("b", group="g")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "starting")
        self.R("stop", "t1")
        self.until(lambda: not (self.state / "chains" / "t1" / "runner.pid").exists(), what="the runner to end")
        self.assertEqual([s["title"] for s in self.starts()], ["t1 a"])
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual([s["title"] for s in self.starts()], ["t1 a", "t1 b"])

    def test_an_unknown_start_in_a_group_rings_without_waiting_for_the_other_worker(self):
        self.scenario({"match": "t1 a", "start_delay": 1.5, "events": [self.done(60)]}, {"match": "t1 b", "events": [self.done(60)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", group="g"), self.worker("b", group="g")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "starting")
        self.kill_runner()
        self.until(lambda: list((self.fake / "queue").iterdir()), what="the fake to finish the start")
        self.until_act()
        self.R("resume", "t1", ok=True)
        text = self.bell_for("WAKE start")                             # b's worker_done is a minute away
        self.assertIn("it is not known whether the worker started", text)
        self.assertIn("still running in the same group: b", text)

    def test_a_lost_connection_is_a_failed_check_not_an_empty_mailbox(self):
        self.scenario()
        (self.fake / "lost_checks").write_text("5")
        self.R("init", ok=True)
        self.assertIn("WAKE daemon · the mailbox check failed 5 times in a row: connection_lost", self.bell(0.5))

    def test_two_doorbells_show_one_ring_once(self):
        self.scenario()
        self.R("init", ok=True)
        self.chain([{"id": "accept", "type": "gate", "title": "contract"}])
        self.until(lambda: self.chain_state()["status"] == "paused")
        bells = [subprocess.Popen([sys.executable, ROUTER, "wait", "--timeout-min", "0.05"], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, env=self.env) for _ in range(2)]
        outs = [b.communicate(timeout=30)[0] for b in bells]
        self.assertEqual(sum("WAKE gate" in o for o in outs), 1, outs)
        self.assertEqual([b.returncode for b in bells], [0, 0], outs)


class Progress(RouterCase):
    """The owner's view: `router.py progress`, and progress.html, which the daemons rewrite."""

    def setUp(self):
        super().setUp()
        # An idle daemon rewrites the page once per long-poll. With a long one, a page that changes at once was
        # rewritten by the code under test, not by that idle pass.
        self.env["ROUTER_WAIT_MS"] = "20000"

    def plan(self, *ids):
        f = self.tmp / "plan.json"
        f.write_text(json.dumps({"title": "the run", "prs": [{"id": i, "part": "part " + i[0].lower(), "title": f"what {i} does"} for i in ids]}))
        return self.R("plan", str(f))

    def page(self):
        f = self.state / "progress.html"
        return f.read_text() if f.exists() else ""

    def test_the_view_names_the_step_and_the_worker_that_runs_it(self):
        self.scenario({"match": "T1 a$", "events": [self.done(0.1)]},
                      {"match": "T1 b$", "events": [{"after": 0.05, "type": "heartbeat", "phase": "writing the fold"}]})
        self.R("init", ok=True)
        rc, out = self.plan("t1", "t2", "u1")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK plan: 3 inner PRs in 2 part(s)", out)
        self.chain([self.worker("a"), self.worker("s", when="test -n {NOPE}"), self.worker("b", effort="xhigh"),
                    {"id": "gate", "type": "gate", "title": "merge"}], pr="T1", NOPE="")
        self.until(lambda: "writing the fold" in self.page(), what="the daemon to put the heartbeat on the page")   # nobody ran `progress`
        text = self.R("progress", ok=True)[1]
        self.assertIn("the run · run_fake", text)
        self.assertIn("0 of 3 inner PRs done (1 running · 2 not started)", text)
        self.assertRegex(text, r"▶ T1\s+running · step 3 of 4: b · <1 min so far · what t1 does")   # the plan's t1 is the chain T1
        self.assertIn("✓ a  – s  ▶ b  · gate", text)                            # a skipped step counts as passed
        self.assertRegex(text, r"worker  T1 b · claude m-sonnet xhigh · started just now · heartbeat just now \(writing the fold\) · ctx_")
        self.assertRegex(text, r"· t2\s+not started · what t2 does")
        self.assertLess(text.index("part t"), text.index("part u"))             # the plan's order
        self.assertNotIn("not in the plan", text)
        self.assertNotIn("no plan recorded", text)
        page = self.page()
        self.assertIn("<b>T1 b</b>", page)
        self.assertIn("claude m-sonnet xhigh", page)
        self.assertIn('data-alive="1"', page)

    def test_a_gate_shows_as_waiting_until_the_coordinator_picks_the_ring_up(self):
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a"), {"id": "merge", "type": "gate", "title": "ready to merge {PR}"}])
        self.until(lambda: "◆ at a gate" in self.page(), what="the runner to put the gate on the page")   # nobody ran `progress`
        text = self.R("progress")[1]
        self.assertRegex(text, r"◆ t1\s+at a gate · step 2 of 2: merge")
        self.assertIn("the coordinator decides; it has NOT picked the ring up yet · merge: ready to merge t1", text)
        self.assertIn("rings the coordinator has not picked up", text)
        self.assertIn("WAKE gate · t1 · merge", text)
        self.assertIn("no plan recorded", text)
        self.bell()
        self.assertNotIn("NOT picked", self.page())                             # the doorbell rewrote the page
        text = self.R("progress")[1]
        self.assertIn("the coordinator decides · merge: ready to merge t1", text)
        self.assertNotIn("rings the coordinator has not picked up", text)
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        text = self.R("progress")[1]
        self.assertIn("1 of 1 inner PRs done", text)
        self.assertRegex(text, r"✓ t1\s+done · took <1 min")

    def test_a_later_ring_of_another_kind_does_not_make_a_seen_gate_look_unseen(self):
        self.scenario({"match": "helper", "events": [{"after": 0.1, "type": "question", "question": "Which base?"}]})
        self.R("init", ok=True)
        self.chain([{"id": "merge", "type": "gate", "title": "merge"}])
        self.assertIn("WAKE gate · t1 · merge", self.bell())
        self.R("worker", "helper", "--spec", "look", "--agent", "claude", "--pr", "t1", ok=True)
        self.until(lambda: list((self.state / "wake").glob("*-t1-question.txt")), what="the helper's question to ring")
        text = self.R("progress")[1]
        self.assertIn("the coordinator decides · merge: merge", text)           # the gate's ring was picked up
        self.assertIn("rings the coordinator has not picked up", text)          # the question's was not
        self.assertIn("WAKE question", text)

    def test_a_started_worker_is_on_the_page_before_it_says_anything(self):
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)]}, {"match": "t1 b$", "start_delay": 1.5, "events": []})
        self.R("init", ok=True)
        self.chain([self.worker("a"), self.worker("b")])
        self.until(lambda: self.chain_state()["steps"][1]["status"] == "starting", what="b's start to begin")
        self.until(lambda: 'class="step ok" title="claude m-sonnet"><span class="n">✓ a' in self.page(), seconds=1.2,
                   what="a's result on the page while b's start is still running")
        self.until(lambda: "<b>t1 b</b>" in self.page(), what="b on the page, with no heartbeat and no message from it")

    def test_a_script_step_shows_while_it_runs_and_runs_again_after_its_runner_died(self):
        log, go = self.out / "runs.txt", self.out / "go"
        self.R("init", ok=True)
        self.assertEqual(self.plan("T1")[0], 0)                                 # the plan's T1 is the chain t1
        self.chain([{"id": "ci", "type": "script", "run": f"echo run >> {log}; for i in $(seq 300); do [ -e {go} ] && break; sleep 0.05; done; echo OK ci green"}])   # it ends by itself
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "script", what="the script to be marked as running")
        self.until(log.exists, what="the script to start")
        self.assertIn("a script the router runs: no worker", self.page())       # on the page before the script began; nobody ran `progress`
        self.assertIn("ci script (attempt 1)", self.R("status")[1])
        self.assertIn("script  ci · no worker: the router runs it · started just now", self.R("progress")[1])
        self.kill_runner()
        text = self.R("progress")[1]
        self.assertNotIn("not in the plan", text)
        self.assertRegex(text, r"✗ t1\s+runner gone")
        self.assertIn("its runner is gone: router.py resume t1", text)
        self.R("resume", "t1", ok=True)
        self.until(lambda: len(self.chain_state()["steps"][0]["attempts"]) == 2, what="the script to start again")
        go.touch()
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(log.read_text().split(), ["run", "run"])
        first, second = self.chain_state()["steps"][0]["attempts"]
        self.assertEqual(first["line"], "the runner ended while this script ran")
        self.assertEqual(second["line"], "OK ci green")

    def test_a_failure_shows_why_and_a_worker_cannot_put_markup_on_the_page(self):
        self.scenario({"match": "t1 a$", "events": [self.done(0.1, outcome="failed", subject="<script>alert(1)</script> the lock does not resolve")]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["status"] == "paused")
        text = self.R("progress")[1]
        self.assertRegex(text, r"✗ t1\s+paused · step 1 of 1: a")
        self.assertIn("a: worker_done failed: <script>alert(1)</script> the lock does not resolve", text)
        page = self.page()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; the lock does not resolve", page)
        self.assertNotIn("<script>alert", page)

    def test_a_step_the_coordinator_writes_off_is_on_the_page_when_the_command_returns(self):
        self.scenario({"match": "t1 a$", "events": []})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "running")
        row = {"dispatchId": self.starts()[0]["dispatch"], "workerState": "ready", "projection": {"liveness": {"verdict": "exited"}}}
        (self.fake / "workers.json").write_text(json.dumps([row]))
        self.R("fail", "t1", "--why", "the transcript ends without worker_done", ok=True)
        page = self.page()                                                      # `fail` stopped the runner: only the command itself can have written this
        self.assertIn("a: written off: no worker_done; Orca shows ready, liveness exited", page)
        self.assertNotIn("worker_done gone", page)
        self.assertRegex(self.R("progress")[1], r"✗ t1\s+paused · step 1 of 1: a")

    def test_a_plan_is_checked_and_a_chain_outside_it_is_still_listed(self):
        bad = self.tmp / "bad.json"
        bad.write_text('{"prs": [{"title": "no id"}]}')
        rc, out = self.R("plan", str(bad))
        self.assertEqual(rc, 2, out)
        self.assertIn("not a plan", out)
        rc, out = self.plan("t1", "T1")
        self.assertEqual(rc, 2, out)
        self.assertIn("in the plan twice: t1", out)
        self.assertFalse((self.state / "plan.json").exists())
        self.scenario({"match": "x9 a$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.until(lambda: 'data-alive="1"' in self.page(), what="the daemon's first page")
        self.assertEqual(self.plan("t1")[0], 0)
        self.assertIn("what t1 does", self.page())                              # recording the plan rewrote the page
        self.chain([self.worker("a")], pr="x9")
        self.until(lambda: self.chain_state("x9")["status"] == "done")
        text = self.R("progress")[1]
        self.assertIn("1 of 2 inner PRs done (1 done · 1 not started)", text)
        self.assertLess(text.index("not in the plan"), text.index("✓ x9"))

    def test_the_page_says_when_the_daemon_is_not_running(self):
        self.R("init", ok=True)
        self.until(lambda: 'data-alive="1"' in self.page(), what="the daemon to write the page")
        self.assertNotIn("The mailbox daemon is not running", self.page())
        self.R("stop", "--all", ok=True)
        self.until(lambda: 'data-alive="0"' in self.page(), what="the daemon's last page")
        self.assertIn("The mailbox daemon is not running", self.page())
        self.assertIn("mailbox daemon NOT RUNNING: router.py init", self.R("progress")[1])

    def test_an_idle_daemon_still_rewrites_the_page(self):
        self.env["ROUTER_WAIT_MS"] = "300"
        self.R("init", ok=True)
        written = lambda: self.page().split('data-written="')[1][:20] if self.page() else ""
        self.until(written, what="the daemon's first page")
        first = written()
        self.until(lambda: written() not in ("", first), what="a later page, with nothing happening")

    def test_a_broken_view_does_not_stop_a_chain(self):
        copy = self.tmp / "copy"
        copy.mkdir()
        shutil.copy(ROUTER, copy / "router.py")
        (copy / "progress.py").write_text("raise RuntimeError('broken view')\n")
        self.router = str(copy / "router.py")
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.chain([self.worker("a"), {"id": "s", "type": "script", "run": "echo OK s"}, {"id": "g", "type": "gate", "title": "merge"}])
        self.assertIn("WAKE gate · t1 · g", self.bell())
        self.R("resume", "t1", ok=True)
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertEqual(self.page(), "")
        self.assertIn("progress.html was not rewritten", (self.state / "mailbox.log").read_text())
        rc, out = self.R("progress")
        self.assertEqual(rc, 1, out)
        self.assertIn("the view could not be rendered", out)
        self.assertNotIn("Traceback", out)

    def test_a_stopped_chain_is_on_the_page_when_stop_returns(self):
        copy = self.tmp / "copy"
        copy.mkdir()
        shutil.copy(ROUTER, copy / "router.py")
        shutil.copy(KIT / "progress.py", copy / "view.py")
        (copy / "progress.py").write_text(   # a slow view: a page written after the runner's pid file went would come after `stop` returned
            "import time\nfrom view import *\nfrom view import html_page as fast\n\n\ndef html_page(d):\n    time.sleep(1)\n    return fast(d)\n")
        self.router = str(copy / "router.py")
        self.scenario({"match": "t1 a$", "events": []})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["steps"][0]["status"] == "running")
        self.R("stop", "t1", ok=True)
        self.assertIn("stopped with router.py stop: router.py resume t1", self.page())

    def test_an_ad_hoc_worker_and_an_open_question_are_listed(self):
        self.scenario({"match": "arbiter", "events": [{"after": 0.8, "type": "question", "question": "Which base?", "on_reply": [self.done(0.8)]}]})
        self.R("init", ok=True)
        self.R("worker", "arbiter B3.4", "--spec", "decide", "--agent", "claude", "--model", "m-fable", "--effort", "high", ok=True)
        self.assertIn("<b>arbiter B3.4</b>", self.page())                       # starting it rewrote the page
        mid = self.bell().split("message ")[1].split()[0]
        text = self.R("progress")[1]
        self.assertIn("workers outside a chain", text)
        self.assertIn("worker  arbiter B3.4 · claude m-fable high · started just now", text)
        self.assertRegex(text, r"questions not answered\n  - arbiter B3\.4 · asked just now · Which base\?")
        self.assertIn("Which base?", self.page())
        self.R("reply", mid, "main", ok=True)
        self.assertNotIn("Which base?", self.page())                            # the reply rewrote the page
        self.assertIn("WAKE done · ad hoc worker arbiter B3.4", self.bell_for("WAKE done"))
        self.assertNotIn("workers outside a chain", self.R("progress")[1])


class Templates(RouterCase):
    def test_inner_pr_without_a_base_branch_is_refused_before_anything_starts(self):
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        given = [f"WT={self.repo()}", "ISSUE=1", "TITLE=t", f"SCRATCH={self.out}"] + [f"{k}={v}" for k, v in profile("python")["vars"].items()]
        rc, out = self.R("chain", "demo", "--def", str(TEMPLATES / "inner-pr.json"), *given)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT OK demo:", out)
        self.assertIn("no value for {BASE_BRANCH}", out)
        self.assertFalse((self.state / "chains" / "demo").exists())
        time.sleep(0.3)
        self.assertEqual(self.starts(), [])                            # the fake Orca saw no worker-start
        rc, out = self.R("chain", "demo", "--def", str(TEMPLATES / "inner-pr.json"), "--dry-run", "BASE_BRANCH=dev", *given)
        self.assertEqual(rc, 0, out)                                   # the branch was all it lacked
        self.assertIn("OK demo: 17 steps", out)

    def test_a_definition_in_a_subdirectory_finds_specs_and_checks_in_its_kit(self):
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)], "effect": f"echo OK > {self.out}/a.txt"})
        self.R("init", ok=True)
        (self.kit / "templates").mkdir()
        path = self.kit / "templates" / "c.json"
        path.write_text(json.dumps({"name": "t1", "kit": "..", "steps": [
            self.worker("a", checks=["{CHECKS}/file-exists.sh {OUT}/a.txt", "echo OK kit: {KIT}"])]}))
        rc, out = self.R("chain", "t1", "--def", str(path), f"OUT={self.out}")
        self.assertEqual(rc, 0, out)
        self.until(lambda: self.chain_state()["status"] == "done", what="the chain to finish")
        self.assertEqual(self.starts()[0]["spec"], f"Target: {self.out}\nChange: write {self.out}/a.txt (attempt 1)\n")   # the kit's specs/w.md
        self.assertEqual(self.chain_state()["kit_dir"], str(self.kit.resolve()))
        self.assertIn(f"check: OK kit: {self.kit.resolve()}", self.journal())
        path.write_text(json.dumps({"name": "t2", "kit": "../nowhere", "steps": [self.worker("a")]}))
        rc, out = self.R("chain", "t2", "--def", str(path), "--dry-run", f"OUT={self.out}")
        self.assertEqual(rc, 1, out)
        self.assertIn("t2: the definition's kit '../nowhere' is not a directory", out)


class Sweep(unittest.TestCase):
    """The specs and templates serve any repository: nothing of the run they were written for may come back."""
    FORBIDDEN = re.compile(r"pipecat|\b\d+\.\d+\.\d+\b|\b1\.(?:8|11)\b|\bB\d+\.\d+[a-z]?\b|\bM0\b", re.IGNORECASE)

    def test_the_pattern_catches_what_the_last_run_left(self):
        for bad in ("Pipecat", "pipecat-1.11", "1.8.1", "moved in 1.11", "B1.1", "B4.2a", "the M0 PR"):
            self.assertTrue(self.FORBIDDEN.search(bad), bad)
        for fine in ("claude-opus-5-5", "ledger-r1.md", "(B3) a group", "M01", "--tb=line"):
            self.assertFalse(self.FORBIDDEN.search(fine), fine)

    # One repository's tools and paths: the specs and templates reach them through a profile's variables. "make" is
    # also an English verb ("make every row true"), so it counts only where a command starts.
    TOOLS = re.compile(r"pytest|\bruff\b|pyright|tests/unit|tests/integration|\bnpm\b|eslint|\btsc\b|(?:^|[`$(;&|]\s*)make\s|"
                       r"\.venv|locked-commit|rules-worker|snapshots/", re.IGNORECASE)
    # (file, text on the line): why that line may name a tool.
    ALLOWED = {("specs/test-writer.md", "@pytest.mark.xfail(strict=True"):
               "the blind test writer and the xfail-only check are pytest-only today (README); a flow for another test "
               "runner sets TESTS= and the step is skipped"}

    def test_the_pattern_catches_what_the_last_run_left(self):
        for bad in ("Pipecat", "pipecat-1.11", "1.8.1", "moved in 1.11", "B1.1", "B4.2a", "the M0 PR"):
            self.assertTrue(self.FORBIDDEN.search(bad), bad)
        for fine in ("claude-opus-5-5", "ledger-r1.md", "(B3) a group", "M01", "--tb=line"):
            self.assertFalse(self.FORBIDDEN.search(fine), fine)

    def test_the_tool_pattern_catches_one_repositorys_commands(self):
        for bad in ("Pyright", "run pytest", "pytest.ini", "ruff check", "tests/unit/x", "tests/integration/bot", "npm test",
                    "npx eslint", "npx tsc", "`make test`", "make lint", ".venv/bin/python", "locked-commit.sh",
                    "briefs/rules-worker.md", "snapshots/a.txt"):
            self.assertTrue(self.TOOLS.search(bad), bad)
        for fine in ("Change: make every row of the contract true", "to make it pass", "{TEST_CMD} {UNIT_DIRS}", "tests/",
                     "the test runner's settings", "rustc", "a ruffle"):
            self.assertFalse(self.TOOLS.search(fine), fine)

    def files(self, with_contract_template=True):
        files = sorted(p for d in (KIT / "specs", TEMPLATES) for p in d.rglob("*") if p.is_file())
        return files + ([KIT / "contract-template.md"] if with_contract_template else [])

    def test_specs_and_templates_name_no_project_version_or_pr_of_a_past_run(self):
        files = self.files()
        self.assertGreater(len(files), 15)
        hits = [f"{p.relative_to(KIT)}:{n}: {m.group(0)}" for p in files
                for n, line in enumerate(p.read_text().splitlines(), 1) for m in self.FORBIDDEN.finditer(line)]
        hits += [f"{p.relative_to(KIT)}: in the name" for p in files if self.FORBIDDEN.search(p.name)]
        self.assertEqual(hits, [])

    def test_specs_and_templates_name_no_test_runner_linter_or_script_of_one_repository(self):
        hits, used = [], set()
        for p in self.files(with_contract_template=False):
            rel = str(p.relative_to(KIT))
            for n, line in enumerate(p.read_text().splitlines(), 1):
                allowed = [key for key in self.ALLOWED if key[0] == rel and key[1] in line]
                used.update(allowed)
                hits += [] if allowed else [f"{rel}:{n}: {m.group(0)}" for m in self.TOOLS.finditer(line)]
        self.assertEqual(hits, [])
        self.assertEqual(used, set(self.ALLOWED))                     # an allowance nothing needs any more goes


class Profiles(RouterCase):
    """A profile carries what differs between repositories; the specs read it through variables."""
    RUN = {"PR": "p1", "WT": "/wt", "SCRATCH": "/s", "ISSUE": "7", "TITLE": "p1: t", "BASE_BRANCH": "dev", "RUN_CONTEXT": "Ctx.",
           "STATE": "/st", "DEF_DIR": "/k/templates", "KIT": "/k", "CHECKS": "/k/checks", "STEP": "step", "ATTEMPT": "1",
           "HEAD_BEFORE": "hb", "NOTE": "", "CONTRACT_SHA": "sha", "PR_URL": "https://example.invalid/pr/1", "OUT": "/out",
           "ANSWER": "PASS"}
    NAMES = ("python", "typescript", "bell")
    SNAPSHOTS = TESTS / "fixtures" / "profiles"

    @classmethod
    def setUpClass(cls):
        spec = spec_from_file_location("router_under_test", ROUTER)
        cls.rt = module_from_spec(spec)
        spec.loader.exec_module(cls.rt)

    def vars_of(self, name, **override):
        """The profile's values as a run sees them: its {SCRATCH}, {WT} and {PR} filled in first."""
        own = {k: self.RUN[k] for k in ("SCRATCH", "WT", "PR")}
        return {k: self.rt.render(v, own) for k, v in dict(profile(name)["vars"], **override).items()}

    def rendered(self, template, name, **override):
        """Every worker spec of the template, rendered as the runner would: {spec name: text}."""
        defn = json.loads((TEMPLATES / template).read_text())
        variables = dict(self.RUN, **{k: str(v) for k, v in defn.get("vars", {}).items()})
        variables.update(self.vars_of(name, **override))
        for s in defn["steps"]:
            sid = self.rt.step_var(s["id"])
            variables.update({f"HEAD_BEFORE_{sid}": f"b-{s['id']}", f"HEAD_AFTER_{sid}": f"a-{s['id']}"})
        self.assertTrue(set(self.rt.STEP_VARS_EARLY) <= set(variables))
        out = {}
        for s in defn["steps"]:
            if s.get("type", "worker") == "worker":
                missing = set()
                out[s["spec"]] = self.rt.render((KIT / "specs" / s["spec"]).read_text(), variables, missing)
                self.assertEqual(missing, set(), f"{name}: {s['spec']}")
        return out

    def test_each_profile_has_a_name_an_about_and_every_profile_variable(self):
        self.assertEqual(sorted(p.stem for p in PROFILES.glob("*.json")), sorted(self.NAMES))
        for name in self.NAMES:
            p = profile(name)
            self.assertEqual(sorted(p), ["about", "name", "vars"], name)
            self.assertEqual(p["name"], name)
            self.assertGreaterEqual(p["about"].count(". "), 1, name)  # two sentences: the repository shape it fits
            self.assertEqual(sorted(p["vars"]), sorted(PROFILE_VARS), name)
            for k, v in p["vars"].items():
                self.assertTrue(isinstance(v, str) and v.strip(), f"{name}: {k} is empty; write none to skip a gate")
                self.assertTrue(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", v)) <= {"SCRATCH", "WT", "PR"}, f"{name}: {k}={v}")
                self.assertNotRegex(v, r"/Users/|/home/", f"{name}: {k} names a path of one machine")
            for k in ("TEST_PATHS", "TEST_CONFIG"):               # a script step gets each as one shell word
                self.assertNotIn(" ", p["vars"][k], f"{name}: {k}")

    def test_the_template_documents_every_profile_variable_and_defaults_none(self):
        defn = json.loads((TEMPLATES / "inner-pr.json").read_text())
        self.assertEqual([k for k in PROFILE_VARS if k in defn["vars"]], [])   # a default would hide a missing profile
        self.assertEqual([k for k in PROFILE_VARS if k not in defn["variables"]], [])
        self.assertNotIn("GOLDENS", json.dumps(defn))                         # FROZEN_PATHS replaced it

    def test_every_spec_renders_with_each_profile_and_leaves_no_placeholder(self):
        for name in self.NAMES:
            for template in ("inner-pr.json", "smoke.json"):
                for spec, text in self.rendered(template, name).items():
                    self.assertEqual(self.rt.VAR_RE.findall(text), [], f"{name}: {spec}")

    def test_the_validator_and_the_implementer_carry_the_profiles_commands(self):
        for name in self.NAMES:
            v, specs = self.vars_of(name), self.rendered("inner-pr.json", name)
            for spec in ("validator.md", "implementer.md"):
                self.assertIn(f"`{v['TEST_CMD']}", specs[spec], f"{name}: {spec}")
                for k in ("LINT_CMD", "FROZEN_PATHS"):
                    if v[k] != "none":
                        self.assertIn(v[k], specs[spec], f"{name}: {spec}: {k}")
            self.assertIn(f"{v['TEST_CMD']} {v['UNIT_DIRS']}", specs["implementer.md"])
            if v["RULES"] == "none":
                self.assertIn("Rules file: none. When that is a path, read it", specs["validator.md"])
            else:
                self.assertIn(f"Rules file: {v['RULES']}. When that is a path, read it", specs["validator.md"])

    def test_a_gate_set_to_none_is_skipped_and_reported_never_run(self):
        specs = self.rendered("inner-pr.json", "python", LINT_CMD="none", FROZEN_PATHS="none", EXTRA_SUITE="none")
        validator, implementer = specs["validator.md"], specs["implementer.md"]
        self.assertIn('(3) the extra suite, `none`: when that is none, skip the gate and write "skipped: no extra suite"', validator)
        self.assertIn('(4) the lint gate, `none`: when that is none, skip the gate and write "skipped: no lint command"', validator)
        self.assertIn('(5) the frozen paths, none: when that is none, skip the gate and write "skipped: no frozen paths"', validator)
        self.assertIn("the lint gate the validator runs, `none`, unless that is none", implementer)
        self.assertIn("Frozen paths: none. When that is not none, leave them unchanged", implementer)
        for text in (validator, implementer):                         # every `none` stands right before its guard
            for m in re.finditer(r"`none`", text):
                around = text[m.start() - 7:m.end() + 30]
                self.assertRegex(around, r"^unless `none` is none|`none`(?::|,) (?:when|unless) that is none", around)

    def test_rendered_validator_and_implementer_match_their_snapshots(self):
        update = os.environ.get("FLOWS_UPDATE_SNAPSHOTS") == "1"
        for name in self.NAMES:
            specs = self.rendered("inner-pr.json", name)
            for spec in ("validator.md", "implementer.md"):
                path = self.SNAPSHOTS / name / spec
                if update:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(specs[spec].encode())
                self.assertEqual(specs[spec].encode(), path.read_bytes(),
                                 f"{path} differs: FLOWS_UPDATE_SNAPSHOTS=1 python3 tests/test_router.py -k snapshots")

    def test_a_chain_without_a_profile_is_refused_naming_test_cmd_and_passes_with_one(self):
        given = [f"WT={self.repo()}", "ISSUE=1", "TITLE=t", "BASE_BRANCH=dev", f"SCRATCH={self.out}"]
        rc, out = self.R("chain", "demo", "--def", str(TEMPLATES / "inner-pr.json"), "--dry-run", *given)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT OK demo:", out)
        self.assertIn("no value for {TEST_CMD}", out)
        named = set(re.findall(r"no value for \{([A-Z_]+)\}", out))
        self.assertTrue(named <= set(PROFILE_VARS), named)              # nothing but the profile is missing
        self.assertGreaterEqual(len(named), 8, named)                   # router.py prints the first 20 problems only
        rc, out = self.R("chain", "demo", "--def", str(TEMPLATES / "inner-pr.json"), "--dry-run", *given,
                         *[f"{k}={v}" for k, v in profile("python")["vars"].items()])
        self.assertEqual(rc, 0, out)
        self.assertIn("OK demo: 17 steps, dry run, nothing created", out)
        self.assertFalse((self.state / "chains" / "demo").exists())


class ProgressView(unittest.TestCase):
    """progress.py alone: it renders a dict and reads nothing."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(KIT))
        import progress
        cls.p = progress

    @staticmethod
    def stamp(minutes_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes_ago * 60))

    def data(self, **kw):
        step = lambda sid, status, group="": {"id": sid, "type": "worker", "status": status, "group": group, "n": 1,
                                              "started": self.stamp(30), "ended": self.stamp(10), "who": "codex", "note": ""}
        row = {"id": "B3.4", "title": "the sweep", "part": "B3", "state": "running", "created": self.stamp(200), "ended": "",
               "done": 2, "total": 6, "at": ["review_codex", "review_claude"], "why": "", "since": "", "picked_up": True, "url": "",
               "steps": [step("draft_pr", "done"), step("simplify", "done"), step("review_codex", "running", "review"),
                         step("review_claude", "running", "review"), step("triage", "pending"), step("fix_code", "pending")]}
        worker = {"kind": "worker", "pr": "B3.4", "step": "review_codex", "title": "B3.4 review_codex", "who": "codex", "n": 1,
                  "started": self.stamp(30), "dispatch": "ctx_1", "heartbeat": self.stamp(11), "phase": "reading the diff"}
        d = {"written": self.stamp(0), "run": "run_1", "objective": "o", "title": "t", "has_plan": True, "state_dir": "/s",
             "page": "/s/progress.html", "refresh_s": 120.0, "mailbox": {"alive": 4242, "last": self.stamp(1)}, "rows": [row],
             "workers": [worker], "questions": [], "events": [], "rings": []}
        d.update(kw)
        return d

    def test_durations(self):
        self.assertEqual([self.p.dur(s) for s in (0, 59, 60, 7199, 7200, 9000)],
                         ["<1 min", "<1 min", "1 min", "119 min", "2 h 0 min", "2 h 30 min"])

    def test_steps_that_run_at_once_are_boxed_together(self):
        page = self.p.html_page(self.data())
        box = page.split('<div class="grp"')[1].split("</div></div>")[0]
        self.assertEqual(page.count('<div class="grp"'), 1)                     # two steps in a row without a group are not a box
        self.assertIn("review_codex", box)
        self.assertIn("review_claude", box)
        self.assertNotIn("triage", box)
        self.assertNotIn("simplify", box)

    def test_a_late_heartbeat_and_a_stale_page_are_flagged(self):
        page = self.p.html_page(self.data())
        self.assertIn('data-warn="600" class="late">11 min ago', page)          # a heartbeat is due every 5 min
        self.assertIn('data-stale-after="360"', page)                           # three long-polls
        self.assertIn('id="stale" hidden', page)                                # shown by the page's script, from its age
        fresh = self.data()
        fresh["workers"][0]["heartbeat"] = self.stamp(3)
        self.assertNotIn('class="late"', self.p.html_page(fresh))
        self.assertIn('data-stale-after="300"', self.p.html_page(self.data(refresh_s=1.0)))

    def test_a_ring_nobody_picked_up_for_five_minutes_is_a_banner(self):
        ring = {"rang": self.stamp(7), "pr": "B3.4", "kind": "gate", "line": "WAKE gate · B3.4 · merge"}
        self.assertIn("the coordinator has not picked it up", self.p.html_page(self.data(rings=[ring])))
        self.assertNotIn("the coordinator has not picked it up", self.p.html_page(self.data(rings=[dict(ring, rang=self.stamp(2))])))
        self.assertIn("rang 7 min ago · WAKE gate · B3.4 · merge", self.p.text(self.data(rings=[ring])))

    def test_the_text_and_the_page_agree_on_the_count(self):
        d = self.data()
        d["rows"].append(dict(d["rows"][0], id="B3.5", state="done", ended=self.stamp(100), at=[]))
        d["rows"].append(dict(d["rows"][0], id="B3.6", state="not started", steps=[], created="", done=0, total=0, at=[]))
        self.assertIn("1 of 3 inner PRs done (1 done · 1 running · 1 not started)", self.p.text(d))
        self.assertIn('<div class="big">1 of 3</div>', self.p.html_page(d))
        self.assertIn("took 100 min", self.p.text(d))


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="router-checks-"))
        self.git = ["git", "-C", str(self.tmp), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(self.git + ["init", "-q"], check=True)
        (self.tmp / "tests").mkdir()
        (self.tmp / "bot").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, files):
        for path, text in files.items():
            if text is None:
                (self.tmp / path).unlink()
            else:
                (self.tmp / path).write_text(text)
        subprocess.run(self.git + ["add", "-A"], check=True)
        subprocess.run(self.git + ["commit", "-q", "--allow-empty", "-m", "c"], check=True)
        return subprocess.run(self.git + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def run_check(self, name, *args):
        p = subprocess.run([str(CHECKS / name)] + [str(a) for a in args], capture_output=True, text=True)
        lines = p.stdout.strip().splitlines()
        return p.returncode, (lines[-1] if lines else p.stderr.strip())

    RED = ("import pytest\nfrom x import y\n\n\n@pytest.mark.xfail(strict=True, reason='red first')\n"
           "def test_a():\n    assert y() == 1\n\n\n@pytest.mark.parametrize('v', [1, pytest.param(2, marks=pytest.mark.xfail(strict=True))])\n"
           "def test_b(v):\n    assert v\n")
    GREEN = ("from x import y\nimport pytest\n\n\n# now green\ndef test_a():\n    assert y() == 1\n\n\n"
             "@pytest.mark.parametrize('v', [1, pytest.param(2)])\ndef test_b(v):\n    assert v\n")

    def xfail(self, new_files):
        a = self.commit({"tests/test_x.py": self.RED, "bot/x.py": "def y():\n    return 0\n"})
        b = self.commit(dict({"bot/x.py": "def y():\n    return 1\n"}, **new_files))
        return self.run_check("xfail-only.py", self.tmp, a, b)

    def test_xfail_only_accepts_removed_markers_and_a_comment(self):
        rc, line = self.xfail({"tests/test_x.py": self.GREEN})
        self.assertEqual(rc, 0, line)
        self.assertRegex(line, r"^OK xfail-only \w+\.\.\w+: 1 test file\(s\) changed, 2 xfail marker\(s\) removed, nothing else$")

    def test_xfail_only_accepts_no_test_change_at_all(self):
        rc, line = self.xfail({})
        self.assertEqual((rc, line.split(":")[1].strip()), (0, "0 test file(s) changed, 0 xfail marker(s) removed, nothing else"))

    def test_xfail_only_rejects_a_weakened_assertion(self):
        rc, line = self.xfail({"tests/test_x.py": self.GREEN.replace("assert y() == 1", "assert y() >= 0")})
        self.assertEqual(rc, 1)
        self.assertIn("NOT OK xfail-only", line)
        self.assertIn("tests/test_x.py: changed beyond its xfail markers", line)

    def test_xfail_only_rejects_a_new_marker_a_new_file_a_deleted_file_and_a_new_import(self):
        rc, line = self.xfail({"tests/test_x.py": self.RED.replace("def test_b", "@pytest.mark.xfail\ndef test_b")})
        self.assertIn("1 xfail marker(s) added or changed", line)
        rc, line = self.xfail({"tests/test_new.py": "def test_n():\n    pass\n"})
        self.assertIn("tests/test_new.py: added", line)
        rc, line = self.xfail({"tests/test_x.py": None})
        self.assertIn("tests/test_x.py: deleted", line)
        rc, line = self.xfail({"tests/test_x.py": "import os\n" + self.GREEN})
        self.assertIn("new or changed import: import os", line)
        rc, line = self.xfail({"tests/golden.json": "{}"})
        self.assertIn("tests/golden.json: added", line)

    def pair(self, old, new):
        a = self.commit({"tests/test_x.py": old})
        b = self.commit({"tests/test_x.py": new})
        return self.run_check("xfail-only.py", self.tmp, a, b)

    def test_xfail_only_rejects_an_import_that_binds_the_same_name_to_other_code(self):
        old = "import pytest\nimport bot.strict as impl\nfrom .fakes import f\n\n\n@pytest.mark.xfail\ndef test_a():\n    assert impl.y(f) == 1\n"
        green = old.replace("@pytest.mark.xfail\n", "")
        self.assertEqual(self.pair(old, green)[0], 0)
        rc, line = self.pair(old, green.replace("bot.strict", "bot.lenient"))
        self.assertEqual(rc, 1)
        self.assertIn("new or changed import: import bot.lenient as impl", line)
        rc, line = self.pair(old, green.replace("from .fakes", "from ..fakes"))
        self.assertEqual(rc, 1)
        self.assertIn("new or changed import: from ..fakes import f", line)
        rc, line = self.pair(old, green.replace("import pytest\n", "import tests.shim as pytest\n"))
        self.assertEqual(rc, 1)
        self.assertIn("new or changed import: import tests.shim as pytest", line)

    def test_xfail_only_rejects_an_uncommitted_test_edit(self):
        a = self.commit({"tests/test_x.py": self.RED})
        b = self.commit({"tests/test_x.py": self.GREEN})
        (self.tmp / "tests" / "test_x.py").write_text(self.GREEN.replace("assert y() == 1", "assert True"))
        rc, line = self.run_check("xfail-only.py", self.tmp, a, b)
        self.assertEqual((rc, line), (1, "NOT OK xfail-only: uncommitted change under tests/: tests/test_x.py"))

    def test_xfail_only_accepts_the_other_ways_a_marker_is_removed(self):
        old = ("import pytest\n\npytestmark = [pytest.mark.xfail(strict=True), pytest.mark.slow]\n\n\n"
               "@pytest.mark.parametrize('v', [1, pytest.param(2, marks=pytest.mark.xfail)])\ndef test_a(v, request):\n"
               "    request.applymarker(pytest.mark.xfail(reason='later'))\n    assert v\n\n\n"
               "def test_b():\n    pytest.xfail('not yet')\n    assert 1\n")
        new = ("import pytest\n\npytestmark = pytest.mark.slow\n\n\n"
               "@pytest.mark.parametrize('v', [1, 2])\ndef test_a(v, request):\n    assert v\n\n\n"
               "def test_b():\n    assert 1\n")
        rc, line = self.pair(old, new)
        self.assertEqual(rc, 0, line)
        self.assertIn("4 xfail marker(s) removed, nothing else", line)
        rc, line = self.pair(new, new.replace("    assert 1\n", "    pytest.xfail('gave up')\n    assert 1\n"))
        self.assertEqual(rc, 1)                                        # the imperative form counts as a marker too
        self.assertIn("1 xfail marker(s) added or changed", line)
        rc, line = self.pair(old, new.replace("[1, 2]", "[1, 3]"))
        self.assertEqual(rc, 1)                                        # the unwrapped value is still compared
        self.assertIn("changed beyond its xfail markers", line)

    def test_xfail_only_rejects_a_marker_loosened_in_place(self):
        rc, line = self.xfail({"tests/test_x.py": self.RED.replace("strict=True, reason='red first'", "strict=False")})
        self.assertEqual(rc, 1)
        self.assertIn("xfail marker(s) added or changed", line)

    def test_verdict_line_contract_sha_and_file_exists(self):
        f = self.tmp / "v.md"
        f.write_text("# Validator\nVERDICT: PASS 7 gates\nVERDICT: FAIL later\n")
        self.assertEqual(self.run_check("verdict-line.sh", f), (0, "OK v.md: VERDICT: PASS 7 gates"))
        self.assertEqual(self.run_check("verdict-line.sh", f, "^VERDICT: KILLED")[0], 1)
        self.assertEqual(self.run_check("verdict-line.sh", self.tmp / "none.md")[0], 1)
        (self.tmp / "n.md").write_text("no verdict\n")
        self.assertIn("has no line starting with VERDICT:", self.run_check("verdict-line.sh", self.tmp / "n.md")[1])
        p = subprocess.run([str(CHECKS / "contract-sha.sh"), "record", str(f)], capture_output=True, text=True)
        sha = p.stdout.splitlines()[0].split("=")[1]
        self.assertEqual(len(sha), 64)
        self.assertEqual(self.run_check("contract-sha.sh", "check", f, sha)[0], 0)
        f.write_text("changed\n")
        rc, line = self.run_check("contract-sha.sh", "check", f, sha)
        self.assertEqual(rc, 1)
        self.assertIn("NOT OK contract v.md", line)
        self.assertEqual(self.run_check("file-exists.sh", f), (0, "OK v.md: 1 lines"))
        self.assertEqual(self.run_check("file-exists.sh", f, 5)[0], 1)
        f.write_text("OK")                                             # no trailing newline: still one line
        self.assertEqual(self.run_check("file-exists.sh", f), (0, "OK v.md: 1 lines"))
        f.write_text("")
        self.assertEqual(self.run_check("file-exists.sh", f), (1, "NOT OK v.md: 0 lines, expected at least 1"))

    def test_head_unmoved_and_files_untouched(self):
        a = self.commit({"tests/test_x.py": "def test_a():\n    pass\n", "bot/x.py": "A = 1\n"})
        b = self.commit({"bot/x.py": "A = 2\n"})
        c = self.commit({"tests/test_x.py": "def test_a():\n    assert True\n"})
        self.assertEqual(self.run_check("head-unmoved.sh", self.tmp, c)[0], 0)
        rc, line = self.run_check("head-unmoved.sh", self.tmp, a)
        self.assertEqual(rc, 1)
        self.assertIn("NOT OK head moved", line)
        self.assertEqual(self.run_check("files-untouched.sh", self.tmp, a, b, "tests/")[0], 0)
        rc, line = self.run_check("files-untouched.sh", self.tmp, b, c, "tests/")
        self.assertEqual(rc, 1)
        self.assertIn("tests/test_x.py", line)
        # the guarded files are the ones changed in b..c (the test file): a..b left it alone, b..c did not
        self.assertEqual(self.run_check("files-untouched.sh", self.tmp, a, b, "--changed-in", b, c)[0], 0)
        rc, line = self.run_check("files-untouched.sh", self.tmp, b, c, "--changed-in", b, c)
        self.assertEqual(rc, 1)
        self.assertIn("1 guarded file(s) changed", line)
        (self.tmp / "tests" / "test_x.py").write_text("def test_a():\n    pass  # edited, not committed\n")
        for args in (("tests/",), ("--changed-in", b, c)):
            rc, line = self.run_check("files-untouched.sh", self.tmp, a, b, *args)
            self.assertEqual(rc, 1, args)
            self.assertIn("1 guarded file(s) have uncommitted changes: tests/test_x.py", line)

    def test_pr_head_wants_the_pr_and_the_worktree_on_one_commit(self):
        head = self.commit({"bot/x.py": "A = 1\n"})
        bin_dir = self.tmp / ".bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text('#!/usr/bin/env bash\n[ "$1 $2" = "pr view" ] && cat "$GH_HEAD" 2>/dev/null\n')
        (bin_dir / "gh").chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GH_HEAD=str(self.tmp / ".gh_head"))

        def check():
            p = subprocess.run([str(CHECKS / "pr-head.sh"), str(self.tmp), "7"], capture_output=True, text=True, env=env)
            return p.returncode, p.stdout.strip()
        self.assertEqual(check(), (1, "NOT OK pr-head: gh could not read the head of 7"))
        (self.tmp / ".gh_head").write_text(head + "\n")
        self.assertEqual(check(), (0, f"OK pr-head: the PR and the worktree are both at {head[:9]}"))
        (self.tmp / "bot" / "x.py").write_text("A = 2\n")
        self.assertIn("NOT OK pr-head: 1 tracked file(s) have uncommitted changes: bot/x.py", check()[1])
        new = self.commit({})                                          # /simplify committed and did not push
        rc, line = check()
        self.assertEqual((rc, line), (1, f"NOT OK pr-head: the PR is at {head[:9]}, the worktree at {new[:9]} (not pushed, or pushed from elsewhere)"))

    def test_every_script_prints_help_and_rejects_bad_usage(self):
        for name in ("verdict-line.sh", "file-exists.sh", "contract-sha.sh", "head-unmoved.sh", "files-untouched.sh",
                     "ci-green.sh", "pr-head.sh", "xfail-only.py"):
            p = subprocess.run([str(CHECKS / name), "--help"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, name)
            self.assertIn(name, p.stdout, name)
            p = subprocess.run([str(CHECKS / name)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, name)


class DraftPr(unittest.TestCase):
    """draft-pr.sh against a local bare origin and a stand-in gh that only records what it was asked."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="router-pr-"))
        self.origin, self.wt, self.bin = self.tmp / "origin.git", self.tmp / "wt", self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text('#!/usr/bin/env bash\necho "$*" >> "$GH_LOG"\n'
                      'if [ "$1 $2" = "pr list" ]; then cat "$GH_OPEN" 2>/dev/null; exit 0; fi\n'
                      'if [ "$1 $2" = "pr create" ]; then echo "Creating draft pull request"; echo "https://github.com/o/r/pull/7"; exit 0; fi\nexit 1\n')
        gh.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", GH_LOG=str(self.tmp / "gh.log"), GH_OPEN=str(self.tmp / "open"))
        self.env.pop("DRAFT_PR_DRY_RUN", None)
        self.git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(self.git + ["init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run(self.git + ["clone", "-q", str(self.origin), str(self.wt)], check=True, capture_output=True)
        self.g("checkout", "-q", "-b", "pipecat-1.11")
        self.g("commit", "-q", "--allow-empty", "-m", "base")
        self.g("push", "-q", "origin", "pipecat-1.11")
        self.g("checkout", "-q", "-b", "feat/x")
        self.g("commit", "-q", "--allow-empty", "-m", "work")
        self.body = self.tmp / "body.md"
        self.body.write_text("the contract\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def g(self, *args):
        subprocess.run(self.git + ["-C", str(self.wt)] + list(args), check=True, capture_output=True)

    def pr(self, base="pipecat-1.11", **env):
        p = subprocess.run([str(KIT / "draft-pr.sh"), str(self.wt), base, "B3.1: category", str(self.body)],
                           capture_output=True, text=True, env=dict(self.env, **env))
        return p.returncode, p.stdout.strip()

    def gh_log(self):
        f = self.tmp / "gh.log"
        return f.read_text() if f.exists() else ""

    def test_refusals_never_reach_gh_create(self):
        self.assertEqual(self.pr(base="main"), (1, "NOT OK draft PR: the base is main; inner PRs go into the integration branch"))
        self.assertEqual(self.pr(), (1, "NOT OK draft PR: feat/x is not pushed to origin"))
        self.g("push", "-q", "origin", "feat/x")
        self.g("commit", "-q", "--allow-empty", "-m", "not pushed yet")
        rc, out = self.pr()
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"^NOT OK draft PR: origin/feat/x is \w{9}, the worktree is \w{9}$")
        self.g("checkout", "-q", "pipecat-1.11")
        self.assertEqual(self.pr(), (1, "NOT OK draft PR: the worktree is on the base branch pipecat-1.11"))
        self.assertNotIn("pr create", self.gh_log())

    def test_opens_a_draft_once(self):
        self.g("push", "-q", "origin", "feat/x")
        rc, out = self.pr(DRAFT_PR_DRY_RUN="1")
        self.assertEqual(rc, 0)
        self.assertNotIn("pr create", self.gh_log())                  # a dry run creates nothing
        rc, out = self.pr()
        self.assertEqual((rc, out), (0, "VAR PR_URL=https://github.com/o/r/pull/7\nOK draft PR: https://github.com/o/r/pull/7"))
        self.assertIn(f"pr create --draft --base pipecat-1.11 --head feat/x --title B3.1: category --body-file {self.body}", self.gh_log())
        (self.tmp / "open").write_text("pipecat-1.11\thttps://github.com/o/r/pull/7\n")   # from now on gh lists it as open
        rc, out = self.pr()
        self.assertEqual((rc, out), (0, "VAR PR_URL=https://github.com/o/r/pull/7\nOK draft PR: already open, https://github.com/o/r/pull/7"))
        self.assertEqual(self.gh_log().count("pr create"), 1)         # a retry opened no second PR

    def test_an_open_pr_into_another_base_is_a_refusal_not_a_second_pr(self):
        self.g("push", "-q", "origin", "feat/x")
        (self.tmp / "open").write_text("pipecat-1.8\thttps://github.com/o/r/pull/3\n")
        rc, out = self.pr()
        self.assertEqual((rc, out), (1, "NOT OK draft PR: feat/x already has an open PR into another base: "
                                        "https://github.com/o/r/pull/3 (into pipecat-1.8)"))
        self.assertNotIn("pr create", self.gh_log())


if __name__ == "__main__":
    unittest.main(verbosity=2)
