#!/usr/bin/env python3
"""Tests for router.py and the check scripts. No Orca, no network: tests/fake-orca stands in for the CLI.

    python3 tests/test_router.py            # all
    python3 tests/test_router.py -k check   # by name

Each test gets its own state directory and its own fake Orca, and stops every daemon it started.
"""
import fcntl
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
COLLECTOR = str(KIT / "collector.py")
FIXTURES = TESTS / "fixtures" / "collector"
SENTINEL = "SENTINEL-c0ffee-not-for-the-index"   # in every fixture transcript; never in index.jsonl or a meta.json
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
                        ORCA_TERMINAL_HANDLE="term_fake", ORCA_PANE_KEY="pane_fake",
                        # a released worker's logs are collected in every test: never from this machine's sessions
                        FLOWS_CLAUDE_PROJECTS=str(self.tmp / "sessions" / "claude"),
                        FLOWS_CODEX_SESSIONS=str(self.tmp / "sessions" / "codex"), FLOWS_ORCA_RETRY_S="0.05")
        self.env.pop("SCRATCH", None)

    def tearDown(self):
        self.R("stop", "--all")
        subprocess.run(["pkill", "-f", f"collector.py collect --state {self.state}"], capture_output=True)
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

    def test_a_definition_that_names_a_kit_runs_its_commands_from_the_kit(self):
        kit2 = self.tmp / "kit2"
        (kit2 / "checks").mkdir(parents=True)
        (kit2 / "templates").mkdir()
        (kit2 / "checks" / "x.sh").write_text('#!/usr/bin/env bash\necho "OK $1 ran in $(pwd -P)"\n')
        (kit2 / "checks" / "x.sh").chmod(0o755)
        shutil.copytree(self.kit / "specs", kit2 / "specs")
        path = kit2 / "templates" / "c.json"
        path.write_text(json.dumps({"name": "t1", "kit": "..", "steps": [
            self.worker("a", when="test -x checks/x.sh", checks=["checks/x.sh check"]),
            {"id": "s", "type": "script", "when": "test -x checks/x.sh", "run": "checks/x.sh script"},
            {"id": "g", "type": "gate", "title": "hold", "show": ["checks/x.sh show"]}]}))
        self.scenario({"match": "t1 a$", "events": [self.done(0.1)]})
        self.R("init", ok=True)
        self.assertEqual(self.R("chain", "t1", "--def", str(path), f"OUT={self.out}")[0], 0)
        text = self.bell()
        where = kit2.resolve()
        self.assertIn(f"OK show ran in {where}", text)                 # the gate's show line
        self.assertIn(f"check: OK check ran in {where}", self.journal())   # the worker's check, after its when
        self.assertIn(f"script exit 0: OK script ran in {where}", self.journal())


class FlowCase(RouterCase):
    """A run planned as a flow file: tmp/flow.json, its template kit/templates/one.json, the fake Orca."""

    def setUp(self):
        super().setUp()
        (self.kit / "templates").mkdir()
        self.template("one", [self.worker("a"), self.worker("b")])

    def template(self, name, steps, **extra):
        path = self.kit / "templates" / f"{name}.json"
        path.write_text(json.dumps(dict({"name": name, "kit": "..", "steps": steps}, **extra)))
        return path

    @staticmethod
    def pr(pid, **kw):
        return dict({"id": pid, "part": "Part " + pid[0], "title": f"what {pid} does", "base": "dev", "template": "one"}, **kw)

    def write_flow(self, *prs, **top):
        f = dict({"title": "the run", "slots": 1, "start": "auto", "templates": {"one": "kit/templates/one.json"},
                  "vars": {"OUT": str(self.out)}, "prs": list(prs)}, **top)
        path = self.tmp / "flow.json"
        path.write_text(json.dumps(f))
        return path

    def apply(self, *prs, args=(), **top):
        return self.R("flow", "apply", str(self.write_flow(*prs, **top)), "--by", "tester", *args)

    def refused(self, *prs, **top):
        """Apply, expect a refusal, and return its reason lines."""
        rc, out = self.apply(*prs, **top)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"NOT OK flow: \d+ problem\(s\), nothing changed")
        return [l[4:] for l in out.splitlines() if l.startswith("  - ")]

    def copy(self):
        return json.loads((self.state / "flow.json").read_text())

    def show(self):
        return self.R("flow", "show", ok=True)[1]

    def chains(self):
        return sorted(p.parent.name for p in (self.state / "chains").glob("*/state.json"))


class FlowValidation(FlowCase):
    """Every refusal of item 2 of the flow file, with its reason line. Nothing is written on a refusal."""

    def test_an_unknown_template_name(self):
        self.assertIn("A1: template 'nope' is not one of the flow's templates (one)", self.refused(self.pr("A1", template="nope")))
        self.assertIn("A1: no template; the flow's templates: one", self.refused({"id": "A1", "base": "dev"}))
        self.assertFalse((self.state / "flow.json").exists())

    def test_a_template_or_profile_path_that_is_not_a_file(self):
        lines = self.refused(self.pr("A1"), templates={"one": "kit/templates/one.json", "gone": "kit/templates/gone.json"},
                             profile="profiles/none.json")
        self.assertIn(f"templates.gone: not a file: {(self.tmp / 'kit/templates/gone.json').resolve()}", lines)
        self.assertIn(f"profile: not a file: {(self.tmp / 'profiles/none.json').resolve()}", lines)

    def test_a_cycle_in_after_and_an_after_that_names_no_pr(self):
        lines = self.refused(self.pr("A1", after=["A2"]), self.pr("A2", after=["A1"]), self.pr("A3", after=["Z9"]))
        self.assertIn("after: a cycle: A1 -> A2 -> A1", lines)
        self.assertIn("A3: after names Z9, which is not in the flow", lines)
        self.assertIn("after: a cycle: B1 -> B1", self.refused(self.pr("B1", after=["b1"])))

    def test_two_prs_with_one_id_and_an_id_chain_would_refuse(self):
        self.assertIn("A1: the id is in the flow 2 times (A1, a1); upper and lower case are the same",
                      self.refused(self.pr("A1"), self.pr("a1")))
        bad = "'a b': a PR id is letters, digits, '.', '_' and '-', and starts with a letter or digit"
        self.assertIn(bad, self.refused(self.pr("a b")))
        rc, out = self.R("chain", "a b", "--def", str(self.kit / "templates" / "one.json"), "--dry-run")
        self.assertEqual((rc, out.strip()), (2, bad))                 # the same rule, from chain

    def test_a_base_of_main_or_master(self):
        lines = self.refused(self.pr("A1", base="main"), self.pr("A2", base="master"))
        self.assertIn("A1: base main: inner PRs go into the integration branch, never main or master", lines)
        self.assertIn("A2: base master: inner PRs go into the integration branch, never main or master", lines)

    def test_a_value_that_names_an_unknown_variable(self):
        lines = self.refused(self.pr("A1", vars={"TITLE": "{NOPE}: x"}), self.pr("A2"), vars={"OUT": str(self.out), "X": "{ALSO_NOPE}"})
        self.assertIn("A1: vars.TITLE: {NOPE} is not a variable", lines)
        self.assertEqual(lines.count("vars.X: {ALSO_NOPE} is not a variable"), 1)   # a flow variable is named once, not per PR
        self.assertIn("vars.A: its value names itself: A -> B -> A", self.refused(self.pr("A1"), vars={"OUT": "o", "A": "{B}", "B": "{A}"}))
        (self.tmp / "mine.json").write_text(json.dumps({"name": "mine", "vars": {"X": "{NOPE}/x"}}))
        self.template("one", [self.worker("a")], vars={"Y": "{NOPE}/y"})
        lines = self.refused(self.pr("A1"), self.pr("A2"), profile="mine.json")
        self.assertEqual([l for l in lines if "NOPE" in l], ["profile mine: vars.X: {NOPE} is not a variable",   # its layer, once
                                                             "template one: vars.Y: {NOPE} is not a variable"])

    def test_a_steps_override_the_definition_validator_rejects(self):
        lines = self.refused(self.pr("A1", steps=[{"id": "a", "spec": "w.md"}, self.worker("b", spec="gone.md")]))
        self.assertIn("A1: step a: a worker step needs an agent", lines)
        self.assertIn(f"A1: step b: spec file missing: {self.kit.resolve() / 'specs' / 'gone.md'}", lines)

    def test_slots_and_start(self):
        lines = self.refused(self.pr("A1"), slots=0, start="later")
        self.assertIn("slots: 0 is not a positive integer", lines)
        self.assertIn("start: 'later' is not auto or manual", lines)
        self.assertIn("slots: True is not a positive integer", self.refused(self.pr("A1"), slots=True))

    def test_variables_the_router_sets_and_values_that_are_not_text(self):
        lines = self.refused(self.pr("A1", vars={"BASE_BRANCH": "dev", "PR": "x", "N": None}))
        self.assertIn("A1: vars.BASE_BRANCH: the router sets it from the PR's base", lines)
        self.assertIn("A1: vars.PR: the router sets it", lines)
        self.assertIn("A1: vars.N: the value must be a string or a number", lines)

    def test_a_missing_variable_is_accepted_and_shown_as_waited_for(self):
        self.template("one", [self.worker("a", worktree="path:{WT}"), self.worker("b", checks=["echo OK {ISSUE}"])])
        rc, out = self.apply(self.pr("A1"), self.pr("A2", vars={"WT": "{OUT}/wt", "ISSUE": "7"}),
                             version=41, history=[{"v": 41}])           # a file's version and history are the router's to write
        self.assertEqual(rc, 0, out)
        self.assertIn("OK flow v1:", out)
        self.assertIn("nothing was started: the mailbox daemon is not running (router.py init)", out)
        text = self.show()
        self.assertIn("A1 · Part A · what A1 does · after - · waiting for: ISSUE, WT", text)
        self.assertIn("A2 · Part A · what A2 does · after - · ready", text)   # {OUT}/wt was filled in
        self.assertEqual((self.copy()["version"], len(self.copy()["history"])), (1, 1))


class FlowVersions(FlowCase):
    def test_versions_history_and_a_stale_base(self):
        self.assertIn("OK flow v1: 3 changes", self.apply(self.pr("A1"), self.pr("A2", after=["A1"]))[1])
        rc, out = self.apply(self.pr("A1"), self.pr("A2", after=["A1"]))
        self.assertEqual(rc, 0, out)
        self.assertIn("OK flow v1: no change; the file is v1 as it is, so the version stays", out)
        before = (self.state / "flow.json").read_bytes()
        rc, out = self.apply(self.pr("A1"), self.pr("A2", after=["A1"]), slots=2, args=("--base", "0"))
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT OK flow: 1 problem(s), nothing changed\n  - stale: the run is at v1", out)
        self.assertEqual((self.state / "flow.json").read_bytes(), before)
        rc, out = self.apply(self.pr("A1"), self.pr("A2", after=["A1"]), slots=2, args=("--base", "v1", "--note", "more room"))
        self.assertIn("OK flow v2: 1 changes\n  - slots 1 -> 2", out)
        self.assertEqual([h["v"] for h in self.copy()["history"]], [1, 2])
        lines = [l for l in self.journal().splitlines() if "| flow v" in l]
        self.assertEqual(len(lines), 2)                                # one journal line per accepted apply
        self.assertIn("flow v2 by tester: 1 changes; more room · slots 1 -> 2", lines[1])
        text = self.R("flow", "history", ok=True)[1]
        self.assertRegex(text, r"v1 · \S+ · by tester · 3 changes\n  - flow created: 2 PRs · slots 1 · start auto\n  - A1 added\n  - A2 added after A1")
        self.assertRegex(text, r"v2 · \S+ · by tester · 1 changes · more room\n  - slots 1 -> 2")
        self.assertNotIn("v1 ·", self.R("flow", "history", "1", ok=True)[1])

    def test_a_reorder_of_the_prs_is_a_new_version(self):
        self.apply(self.pr("A1"), self.pr("B1"))
        rc, out = self.apply(self.pr("B1"), self.pr("A1"))             # which ready PR takes the next free slot
        self.assertEqual(rc, 0, out)
        self.assertIn("OK flow v2: 1 changes\n  - PRs reordered: B1, A1", out)
        self.assertEqual(self.copy()["history"][-1]["changes"], ["PRs reordered: B1, A1"])
        self.assertEqual([p["id"] for p in self.copy()["prs"]], ["B1", "A1"])
        text = self.show()
        self.assertLess(text.index("  B1 · "), text.index("  A1 · "))

    def test_scratch_from_the_environment_fills_in_and_stays(self):
        self.template("one", [self.worker("a", worktree="path:{SCRATCH}")])
        self.env["SCRATCH"] = str(self.out)
        rc, out = self.apply(self.pr("A1"))
        self.assertEqual(rc, 0, out)
        self.assertIn("A1 · Part A · what A1 does · after - · ready", self.show())
        self.env.pop("SCRATCH")                                        # another terminal, without SCRATCH
        self.assertIn("no change", self.apply(self.pr("A1"))[1])
        self.assertEqual(self.copy()["resolved"]["env"], {"SCRATCH": str(self.out)})
        self.env["SCRATCH"] = str(self.tmp)
        self.assertIn("  - SCRATCH from the environment ", self.apply(self.pr("A1"))[1])

    def test_changes_are_listed_in_plain_words(self):
        self.apply(self.pr("A2"), self.pr("A3"), self.pr("B1"))
        rc, out = self.apply(self.pr("A2", steps=[self.worker("a"), self.worker("b", effort="xhigh")]), self.pr("A3"),
                             self.pr("A4", after=["A3"]), slots=3, start="manual")
        self.assertEqual(rc, 0, out)
        for line in ("slots 1 -> 3", "start auto -> manual", "B1 removed (not started)", "A4 added after A3",
                     "A2: its own steps now", "A2: b effort set to xhigh"):
            self.assertIn(f"  - {line}\n", out)
        self.assertEqual(self.copy()["history"][-1]["changes"], [l[4:] for l in out.splitlines() if l.startswith("  - ")])
        self.assertIn("B1 · Part B · what B1 does · removed in v2", self.show())


class FlowScheduler(FlowCase):
    def test_a_pr_starts_when_the_pr_it_is_after_is_done_with_no_command(self):
        self.scenario({"match": "A1 ", "events": [self.done(0.2)], "repeat": True}, {"match": "A2 a$", "events": []})
        self.R("init", ok=True)
        rc, out = self.apply(self.pr("A1"), self.pr("A2", after=["A1"]))
        self.assertIn("started A1 (v1)", out)                         # the apply's own scheduler pass
        self.assertIn("A2 · Part A · what A2 does · after A1 · not started · after A1", self.show())
        self.until(lambda: any(s["title"] == "A2 a" for s in self.starts()), what="A2 to start by itself")
        self.assertEqual(self.chain_state("A1")["status"], "done")
        self.assertIn("flow: started A2 (v1)", self.journal())
        self.assertEqual([s["title"] for s in self.starts()], ["A1 a", "A1 b", "A2 a"])
        self.assertIn("A2 · Part A · what A2 does · after A1 · running a", self.show())

    def test_slots_are_respected(self):
        self.scenario({"match": "A1 ", "events": [self.done(0.3)], "repeat": True}, {"match": "B1 ", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("B1"), self.pr("A1"), slots=1)
        self.until(lambda: self.chains() == ["B1"])
        time.sleep(1.0)                                                # several ticks: still one chain
        self.assertEqual(self.chains(), ["B1"])
        self.assertIn("flow v1 · slots 1/1 · start auto", self.show())
        self.assertIn("A1 · Part A · what A1 does · after - · ready · no free slot", self.show())
        rc, out = self.R("flow", "start", "A1")
        self.assertEqual(rc, 1, out)
        self.assertIn("A1 is not ready: no free slot (1 of 1 open: B1)", out)
        self.apply(self.pr("B1"), self.pr("A1"), slots=2)
        self.until(lambda: self.chains() == ["A1", "B1"], what="the second slot to be used")

    def test_under_manual_only_flow_start_starts_a_pr(self):
        self.scenario({"match": ".", "events": [self.done(0.2)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"), self.pr("A2", after=["A1"]), start="manual")
        time.sleep(1.0)
        self.assertEqual(self.chains(), [])
        self.assertIn("A1 · Part A · what A1 does · after - · ready · start manual: router.py flow start A1", self.show())
        rc, out = self.R("flow", "start", "A2")
        self.assertEqual(rc, 1, out)
        self.assertIn("A2 is not ready: after A1", out)
        rc, out = self.R("flow", "start", "A1")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"OK started A1 \(v1\), runner pid \d+")
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        time.sleep(1.0)
        self.assertEqual(self.chains(), ["A1"])                        # A2 is ready now, and still waits for its command
        rc, out = self.R("flow", "start", "A1")
        self.assertIn("A1: its chain exists (done)", out)

    def test_a_pr_missing_a_variable_never_starts_until_an_apply_gives_it(self):
        self.template("one", [self.worker("a", worktree="path:{WT}")])
        self.scenario({"match": ".", "events": [self.done(0.2)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("B1"))
        time.sleep(1.0)
        self.assertEqual(self.chains(), [])
        self.assertIn("B1 · Part B · what B1 does · after - · waiting for: WT", self.show())
        rc, out = self.apply(self.pr("B1", vars={"WT": str(self.out)}))
        self.assertIn("  - B1: vars.WT set\n", out)                    # a value too long for the line is not shown
        self.assertIn("started B1 (v2)", out)
        self.until(lambda: self.chain_state("B1")["status"] == "done")
        self.assertEqual(self.starts()[0]["worktree"], f"path:{self.out}")

    def test_a_value_that_names_a_declared_variable_nobody_gives_waits_for_it(self):
        self.template("one", [self.worker("a", checks=["echo OK {TEST_CMD}"])], variables={"WT": "the worktree"})   # no step names {WT}
        self.scenario({"match": ".", "events": [self.done(0.2)], "repeat": True})
        self.R("init", ok=True)
        flow_vars = {"OUT": str(self.out), "TEST_CMD": "run in {WT}"}
        self.apply(self.pr("B1"), vars=flow_vars)
        time.sleep(1.0)
        self.assertEqual(self.chains(), [])
        self.assertIn("B1 · Part B · what B1 does · after - · waiting for: WT", self.show())
        rc, out = self.apply(self.pr("B1", vars={"WT": str(self.out)}), vars=flow_vars)
        self.assertIn("started B1 (v2)", out)
        self.assertEqual(self.chain_state("B1")["vars"]["TEST_CMD"], f"run in {self.out}")

    def test_a_runner_whose_chain_completes_starts_the_next_pr_itself(self):
        self.env["ROUTER_WAIT_MS"] = "30000"                           # the daemon ticks at init, then not for 30 s
        self.template("one", [self.worker("a"), {"id": "s", "type": "script", "run": "sleep 1; echo OK done"}])
        self.scenario({"match": "A1 a$", "events": [self.done(0.2)]}, {"match": "A2 a$", "events": []})
        self.R("init", ok=True)
        self.apply(self.pr("A1"), self.pr("A2", after=["A1"]))
        self.until(lambda: self.chain_state("A1")["status"] == "done", what="the fake Orca to finish A1")
        self.until(lambda: "flow: started A2" in self.journal(), seconds=5, what="A1's runner to start A2")

    def test_scheduler_passes_that_race_start_one_pr_once(self):
        self.template("one", [{"id": "g", "type": "gate", "title": "hold"}])
        self.apply(self.pr("A1"), self.pr("B1"))                       # no daemon yet: the apply starts nothing
        sitter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "router.py"])   # a live router.py pid
        self.addCleanup(sitter.kill)
        (self.state / "mailbox.pid").write_text(f"{sitter.pid}\n")
        go = self.tmp / "go"
        code = (f"import os, sys, time\nsys.path.insert(0, {str(KIT)!r})\nimport router\n"
                f"while not os.path.exists({str(go)!r}):\n    time.sleep(0.001)\nprint(router.flow_schedule(router.State()))\n")
        passes = [subprocess.Popen([sys.executable, "-c", code], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                  for _ in range(3)]
        time.sleep(1.0)
        held = open(self.state / "flow.lock", "a")                     # an apply, say, holds the lock
        fcntl.flock(held, fcntl.LOCK_EX)
        go.touch()
        time.sleep(1.5)
        self.assertEqual([p.poll() for p in passes], [None, None, None])   # every pass waits for it
        self.assertEqual(self.chains(), [])
        held.close()
        outs = [p.communicate(timeout=60)[0] for p in passes]
        self.assertEqual(self.journal().count("flow: started"), 1, outs)
        self.assertEqual(self.chains(), ["A1"])                        # then they take turns: once, and one slot

    def test_a_flow_the_daemon_cannot_read_rings_and_the_daemon_goes_on(self):
        self.scenario()
        self.R("init", ok=True)
        self.apply(self.pr("A1"), start="manual")
        (self.state / "flow.json").write_text("{ not json")
        text = self.bell_for("WAKE daemon")
        self.assertIn("WAKE daemon · the flow could not be read: flow.json: ", text)
        self.assertIn("router.py flow apply", text)
        self.assertTrue(self.R("status")[1].startswith("router  run_fake · mailbox alive"))
        self.assertIn("OK quiet", self.bell(0.02))                     # it rang once, not at every tick


class FlowReread(FlowCase):
    """A running chain follows the flow's latest version at each step boundary, for the steps not settled."""

    def setUp(self):
        super().setUp()
        self.steps = [self.worker("a"), {"id": "g", "type": "gate", "title": "hold"}, self.worker("b"), self.worker("c")]
        self.template("one", self.steps)

    def at_gate(self):
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"))
        self.assertIn("WAKE gate · A1 · g: hold", self.bell())

    def test_an_edited_pending_step_starts_as_edited(self):
        self.at_gate()
        steps = [dict(s, model="m-opus") if s["id"] == "b" else s for s in self.steps]
        rc, out = self.apply(self.pr("A1", steps=steps), args=("--note", "b needs more"))
        self.assertIn("  - A1: b model m-sonnet -> m-opus", out)
        self.R("resume", "A1", ok=True)
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        starts = {s["title"]: s for s in self.starts()}
        self.assertEqual((starts["A1 a"]["model"], starts["A1 b"]["model"], starts["A1 c"]["model"]), ("m-sonnet", "m-opus", "m-sonnet"))
        att = {s["id"]: s["attempts"][-1] for s in self.chain_state("A1")["steps"] if s["attempts"]}
        self.assertEqual((att["a"]["flow_version"], att["a"].get("edited"), att["a"]["cause"]), (1, None, "first"))
        self.assertEqual((att["b"]["flow_version"], att["b"].get("edited"), att["b"]["cause"]), (2, True, "first"))
        self.assertEqual(att["c"].get("edited"), None)
        self.assertIn("A1 | b | - | - | flow v2: step edited: model m-sonnet -> m-opus", self.journal())
        own = json.loads((self.state / "chains" / "A1" / "def.json").read_text())
        self.assertEqual(([s["id"] for s in own["steps"]], own["kit"]), (["a", "g", "b", "c"], str(self.kit.resolve())))

    def test_an_added_step_runs_in_its_place_and_a_removed_one_never_starts(self):
        self.at_gate()
        rc, out = self.apply(self.pr("A1", steps=[self.steps[0], self.steps[1], self.worker("x"), self.steps[3]]))
        self.assertEqual(rc, 0, out)
        self.assertIn("  - A1: step b removed\n", out)
        self.assertIn("  - A1: step x added after g\n", out)
        self.R("resume", "A1", ok=True)
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        self.assertEqual([s["title"] for s in self.starts()], ["A1 a", "A1 x", "A1 c"])
        self.assertIn("A1 | b | - | - | flow v2: step removed; it will not run", self.journal())
        self.assertIn("A1 | x | - | - | flow v2: step added after g", self.journal())
        self.assertEqual([s["id"] for s in self.chain_state("A1")["steps"]], ["a", "g", "x", "c"])

    def test_a_running_step_and_a_done_step_cannot_change(self):
        self.scenario({"match": "A1 a$", "events": [self.done(0.1)]}, {"match": "A1 g", "events": []})
        self.template("one", [self.worker("a"), self.worker("g"), self.worker("b")])
        self.R("init", ok=True)
        self.apply(self.pr("A1"))
        self.until(lambda: self.chain_state("A1")["steps"][1]["status"] == "running", what="g to run")
        files = [self.state / "chains" / "A1" / "state.json", self.state / "flow.json"]
        before = [f.read_bytes() for f in files]
        lines = self.refused(self.pr("A1", steps=[self.worker("a"), self.worker("g", effort="xhigh"), self.worker("b", effort="xhigh")]))
        self.assertEqual(lines, ["A1: step g is running: its definition cannot change (effort set to xhigh)"])   # b is pending: free
        lines = self.refused(self.pr("A1", steps=[self.worker("a", model="m-opus"), self.worker("g"), self.worker("b")]))
        self.assertEqual(lines, ["A1: step a is done: its definition cannot change (model m-sonnet -> m-opus)"])
        lines = self.refused(self.pr("A1", steps=[self.worker("g"), self.worker("b")]))
        self.assertIn("A1: step a is done: it cannot be removed", lines)
        lines = self.refused(self.pr("A1", steps=[self.worker("b"), self.worker("a"), self.worker("g")]))
        self.assertIn("A1: step a is done: the steps up to it cannot be reordered, and no step can go before it", lines)
        self.assertEqual([f.read_bytes() for f in files], before)

    def test_a_variable_a_settled_step_used_cannot_change_and_one_only_pending_steps_use_can(self):
        self.template("one", [self.worker("a"), {"id": "g", "type": "gate", "title": "hold"},
                              self.worker("b", when="test -n {LATER}")])
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1", vars={"LATER": "1"}), self.pr("A2", vars={"LATER": "1"}), slots=2)
        self.assertIn("WAKE gate", self.bell_for("WAKE gate"))
        other = self.tmp / "other"
        lines = self.refused(self.pr("A1", vars={"LATER": "1"}), self.pr("A2", vars={"LATER": "1"}), slots=2,
                             vars={"OUT": str(other)})
        self.assertIn("A1: OUT changed, and settled step(s) a used it", lines)   # a's spec named {OUT}
        rc, out = self.apply(self.pr("A1", vars={"LATER": ""}), self.pr("A2", vars={"LATER": "1"}), slots=2)
        self.assertEqual(rc, 0, out)                                   # only b, which is pending, uses LATER
        self.assertIn("  - A1: vars.LATER 1 -> ''", out)
        lines = self.refused(self.pr("A1", vars={"LATER": ""}, after=["A2"]), self.pr("A2", vars={"LATER": "1"}), slots=2)
        self.assertIn("A1: its chain exists, so its after cannot change (- -> A2)", lines)
        rc, out = self.R("resume", "A1", "--set", "LATER=1")
        self.assertEqual(rc, 1, out)
        self.assertIn("A1: the flow sets LATER, and its runner takes the flow's value at the next step: change it with router.py flow apply", out)

    def test_a_step_about_to_start_cannot_change(self):
        self.template("one", [self.worker("b", when="sleep 2")])        # its when runs before it leaves pending
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"))
        self.until(lambda: self.chain_state("A1")["flow"]["claimed"] == ["b"], what="the runner to claim b")
        self.assertEqual(self.chain_state("A1")["steps"][0]["status"], "pending")
        lines = self.refused(self.pr("A1", steps=[self.worker("b", when="sleep 2", model="m-opus")]))
        self.assertEqual(lines, ["A1: step b is about to start: its definition cannot change (model m-sonnet -> m-opus)"])
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        self.assertEqual(self.starts()[0]["model"], "m-sonnet")        # what ran is what the flow says
        self.assertEqual(self.chain_state("A1")["flow"]["claimed"], [])

    def test_the_worktree_and_the_kit_of_a_settled_worker_step_cannot_change(self):
        kit2 = self.tmp / "kit2"
        shutil.copytree(self.kit / "specs", kit2 / "specs")
        (kit2 / "checks").symlink_to(CHECKS)
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1", vars={"WT": "wt1"}))
        self.assertIn("WAKE gate · A1 · g: hold", self.bell())
        self.assertEqual(self.refused(self.pr("A1", vars={"WT": "wt2"})), ["A1: WT wt1 -> wt2, and settled step(s) a used it"])
        self.template("one", self.steps, kit="../../kit2")              # a's spec text is the same in both kits
        lines = self.refused(self.pr("A1", vars={"WT": "wt1"}))
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^A1: KIT (changed|\S+ -> \S+), and settled step\(s\) a used it$")

    def test_the_runner_waits_for_the_flow_lock_at_a_step_boundary(self):
        self.at_gate()
        held = open(self.state / "flow.lock", "a")                     # an apply, say, holds the lock
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX)
        self.R("resume", "A1", ok=True)
        time.sleep(1.5)
        self.assertEqual([s["title"] for s in self.starts()], ["A1 a"])   # b waits for the boundary's re-read
        held.close()
        self.until(lambda: len(self.starts()) > 1, what="b to start once the lock is free")
        self.assertEqual(self.starts()[1]["title"], "A1 b")

    def test_what_a_step_exported_survives_the_re_read(self):
        self.template("one", [{"id": "s", "type": "script", "exports": ["V"], "run": "echo VAR V=7; echo OK set"},
                              {"id": "g", "type": "gate", "title": "hold", "show": ["echo OK V is {V}"]},
                              {"id": "u", "type": "script", "run": "echo OK still {V}"}])
        self.scenario()
        self.R("init", ok=True)
        self.apply(self.pr("A1", vars={"V": "from the flow"}))
        self.assertIn("OK V is 7", self.bell())
        self.apply(self.pr("A1", vars={"V": "from the flow, again"}))    # no settled step uses the flow's V: it is the run's
        self.R("resume", "A1", ok=True)
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        self.assertIn("script exit 0: OK still 7", self.journal())

    def test_attempts_record_why_they_ran(self):
        self.template("one", [self.worker("a", checks=["test -e {OUT}/ok && echo OK ok || echo NOT OK no ok"]),
                              {"id": "g", "type": "gate", "title": "hold"}])
        self.scenario({"match": "A1 a$", "events": [self.done(0.1, "failed")]}, {"match": r"A1 a \(", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"))
        self.assertIn("WAKE failed · A1 · a", self.bell())
        self.R("retry", "A1", ok=True)
        self.assertIn("NOT OK no ok", self.bell())
        (self.out / "ok").touch()
        self.R("resume", "A1", ok=True)
        self.assertIn("WAKE gate", self.bell())
        self.R("resume", "A1", "--from", "a", ok=True)
        self.assertIn("WAKE gate", self.bell())
        atts = self.chain_state("A1")["steps"][0]["attempts"]
        self.assertEqual([x["cause"] for x in atts], ["first", "retry", "resume_from"])
        self.assertEqual([x["flow_version"] for x in atts], [1, 1, 1])
        self.assertEqual([r["cause"] for r in atts[1]["rechecks"]], ["recheck"])
        self.assertNotIn("rechecks", atts[2])

    def test_a_flow_that_cannot_be_read_pauses_the_chain(self):
        self.at_gate()
        good = (self.state / "flow.json").read_bytes()
        (self.state / "flow.json").write_text("{ not json")
        self.R("resume", "A1", ok=True)
        text = self.bell_for("WAKE runner")
        self.assertIn("WAKE runner · A1: the flow could not be read: flow.json: ", text)
        self.until(lambda: self.chain_state("A1")["status"] == "paused")
        self.assertEqual([s["title"] for s in self.starts()], ["A1 a"])   # nothing was started on a guess
        (self.state / "flow.json").write_bytes(good)
        self.R("resume", "A1", ok=True)
        self.until(lambda: self.chain_state("A1")["status"] == "done")


class FlowRemoval(FlowCase):
    def test_a_pr_not_started_is_dropped_and_never_starts(self):
        self.scenario({"match": "A1 ", "events": [self.done(0.5)], "repeat": True}, {"match": "B1", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"), self.pr("B1", after=["A1"]))
        rc, out = self.apply(self.pr("A1"), args=("--note", "B1 is not needed"))
        self.assertIn("OK flow v2: 1 changes\n  - B1 removed (not started)", out)
        self.assertIn("flow v2 by tester: 1 changes; B1 is not needed · B1 removed (not started)", self.journal())
        self.until(lambda: self.chain_state("A1")["status"] == "done")
        time.sleep(1.0)
        self.assertEqual(self.chains(), ["A1"])
        self.assertNotIn("B1", " ".join(s["title"] for s in self.starts()))

    def test_a_started_pr_is_not_dropped(self):
        self.scenario({"match": ".", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"), self.pr("B1"), slots=2)
        self.until(lambda: self.chains() == ["A1", "B1"])
        self.assertIn("B1: its chain exists (running), so it stays in the flow", self.refused(self.pr("A1"), slots=2))
        self.assertEqual(self.copy()["version"], 1)


class FlowCompat(FlowCase):
    def test_with_a_flow_chain_and_plan_leave_its_prs_to_it(self):
        self.scenario({"match": ".", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1"), slots=2, start="manual")
        rc, out = self.R("chain", "a1", "--def", str(self.kit / "templates" / "one.json"), f"OUT={self.out}")
        self.assertEqual((rc, out.strip()), (1, "the flow owns a1: router.py flow start a1"))
        rc, out = self.plan("x")
        self.assertEqual(rc, 1, out)
        self.assertIn("the flow is the plan", out)
        self.assertEqual(self.chain([self.worker("a")], pr="x1")[0], 0)   # a PR the flow does not name
        self.until(lambda: self.chain_state("x1")["steps"][0]["status"] == "running")
        att = self.chain_state("x1")["steps"][0]["attempts"][-1]
        self.assertFalse({"flow_version", "cause", "edited"} & set(att), att)   # a chain outside the flow is as it was
        self.assertNotIn("flow", self.chain_state("x1"))
        self.assertEqual(self.chain([self.worker("a")], pr="x2")[0], 0)
        rc, out = self.chain([self.worker("a")], pr="x3")
        self.assertIn("2 chains are open (x1, x2) and the flow has 2 slots: x3 was not started", out)
        self.assertIn("x1: its chain was started with router.py chain, so the flow cannot take it over",
                      self.refused(self.pr("A1"), self.pr("x1"), slots=2, start="manual")[0])

    def test_a_flow_of_inner_prs_takes_its_variables_from_a_profile(self):
        """inner-pr.json defaults no profile variable: the flow's "profile" field gives them all."""
        self.env["SCRATCH"] = str(self.out)
        pr = {"id": "A1", "part": "Part A", "title": "the first", "base": "dev", "template": "inner-pr",
              "vars": {"WT": str(self.repo()), "ISSUE": "1", "TITLE": "A1: the first"}}
        f = self.tmp / "flow.json"
        f.write_text(json.dumps({"templates": {"inner-pr": str(TEMPLATES / "inner-pr.json")}, "prs": [pr]}))
        self.R("flow", "apply", str(f), ok=True)
        self.assertRegex(self.show(), r"A1 · Part A · the first · after - · waiting for: COMMIT_CMD, COPY_SETUP, .*TEST_CMD")
        f.write_text(json.dumps({"templates": {"inner-pr": str(TEMPLATES / "inner-pr.json")}, "prs": [pr],
                                 "profile": str(KIT / "profiles" / "python.json")}))
        rc, out = self.R("flow", "apply", str(f))
        self.assertIn("  - profile: vars.TEST_CMD set to python -m pytest -q\n", out)
        self.assertIn("A1 · Part A · the first · after - · ready", self.show())
        self.assertEqual(self.copy()["resolved"]["profile"]["name"], "python")

    def test_a_profiles_scratch_wt_and_pr_are_filled_by_the_flow(self):
        self.template("one", [self.worker("a", worktree="path:{WT}", checks=["echo OK {TEST_CMD}"])], variables={"WT": "the worktree"})
        self.env["SCRATCH"] = str(self.out)
        self.scenario({"match": ".", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1", vars={"WT": "{SCRATCH}/wt/{PR}"}), self.pr("A2"), slots=2, profile=str(PROFILES / "bell.json"))
        self.until(lambda: self.chains() == ["A1"])
        wt, v = f"{self.out}/wt/A1", self.chain_state("A1")["vars"]
        self.assertEqual(v["TEST_CMD"], f"PYTEST_PY={wt}/.venv/bin/python {self.out}/bin/bounded.sh 600 {self.out}/bin/pytest-limited.sh -q")
        self.assertEqual(v["RULES"], f"{self.out}/briefs/rules-worker.md")
        self.until(lambda: self.starts(), what="A1's worker to start")
        self.assertEqual(self.starts()[0]["worktree"], f"path:{wt}")
        self.assertIn("A2 · Part A · what A2 does · after - · waiting for: WT", self.show())   # bell's TEST_CMD names {WT}

    def test_a_run_without_a_flow_is_as_it_was(self):
        self.scenario({"match": ".", "events": [self.done(0.1)], "repeat": True})
        self.R("init", ok=True)
        self.assertEqual(self.plan("t1")[0], 0)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["status"] == "done")
        self.assertFalse({"flow_version", "cause", "edited"} & set(self.chain_state()["steps"][0]["attempts"][-1]))
        self.assertNotIn("flow", self.chain_state())
        self.assertEqual(sorted(p.name for p in self.state.iterdir() if p.name.startswith("flow")), [])   # no flow.json, no flow.lock
        rc, out = self.R("flow", "show")
        self.assertEqual((rc, out.strip()), (1, "no flow: router.py flow apply <file>"))

    def plan(self, *ids):
        f = self.tmp / "plan.json"
        f.write_text(json.dumps({"prs": [{"id": i} for i in ids]}))
        return self.R("plan", str(f))

    def test_progress_lists_the_flow(self):
        self.env["ROUTER_WAIT_MS"] = "20000"
        self.template("one", [self.worker("a", worktree="path:{WT}")])
        self.scenario({"match": ".", "events": [], "repeat": True})
        self.R("init", ok=True)
        self.apply(self.pr("A1", vars={"WT": str(self.out)}), self.pr("A2", after=["A1"], vars={"WT": str(self.out)}), self.pr("B1"))
        self.until(lambda: self.chains() == ["A1"])
        text = self.R("progress", ok=True)[1]
        self.assertIn("the run · run_fake · flow v1", text)
        self.assertIn("0 of 3 inner PRs done (1 running · 2 not started)", text)
        self.assertRegex(text, r"· A2\s+not started · after A1 · what A2 does")
        self.assertRegex(text, r"· B1\s+not started · waiting for: WT · what B1 does")
        self.assertLess(text.index("Part A"), text.index("Part B"))
        self.assertNotIn("no plan recorded", text)
        page = (self.state / "progress.html").read_text()
        self.assertIn("flow v1", page)
        self.assertIn("not started · waiting for: WT", page)


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

    def test_the_tool_pattern_catches_one_repositorys_commands(self):
        for bad in ("Pyright", "run pytest", "pytest.ini", "ruff check", "tests/unit/x", "tests/integration/bot", "npm test",
                    "npx eslint", "npx tsc", "`make test`", "make lint", ".venv/bin/python", "locked-commit.sh",
                    "briefs/rules-worker.md", "snapshots/a.txt"):
            self.assertTrue(self.TOOLS.search(bad), bad)
        for fine in ("Change: make every row of the contract true", "to make it pass", "{TEST_CMD} {UNIT_DIRS}", "tests/",
                     "the test runner's settings", "rustc", "a ruffle"):
            self.assertFalse(self.TOOLS.search(fine), fine)

    def files(self):
        return sorted(p for d in (KIT / "specs", TEMPLATES) for p in d.rglob("*") if p.is_file()) + [KIT / "contract-template.md"]

    def test_specs_and_templates_name_no_project_version_or_pr_of_a_past_run(self):
        files = self.files()
        self.assertGreater(len(files), 15)
        hits = [f"{p.relative_to(KIT)}:{n}: {m.group(0)}" for p in files
                for n, line in enumerate(p.read_text().splitlines(), 1) for m in self.FORBIDDEN.finditer(line)]
        hits += [f"{p.relative_to(KIT)}: in the name" for p in files if self.FORBIDDEN.search(p.name)]
        self.assertEqual(hits, [])

    def test_specs_and_templates_name_no_test_runner_linter_or_script_of_one_repository(self):
        hits, used = [], set()
        for p in self.files():
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

    def variables(self, defn, name, **override):
        """What a step of the template sees: the run's values, the template's defaults, the profile's vars."""
        variables = dict(self.RUN, **{k: str(v) for k, v in defn.get("vars", {}).items()})
        variables.update(self.vars_of(name, **override))
        for s in defn["steps"]:
            sid = self.rt.step_var(s["id"])
            variables.update({f"HEAD_BEFORE_{sid}": f"b-{s['id']}", f"HEAD_AFTER_{sid}": f"a-{s['id']}"})
        return variables

    def rendered(self, template, name, **override):
        """Every worker spec of the template, rendered as the runner would: {spec name: text}."""
        defn = json.loads((TEMPLATES / template).read_text())
        variables = self.variables(defn, name, **override)
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
                self.assertRegex(around, r"`none`(?::|,) (?:when|unless) that is none", around)

    # A profile may set these to none. A spec that names one must guard it where it stands ("`X`: when that is none,
    # skip", "Rules file: X. When that is a path, read it"), or the worker is told to run, read or edit "none".
    NONEABLE = ("RULES", "COPY_SETUP", "LINT_CMD", "FROZEN_PATHS", "EXTRA_SUITE")
    GUARD = re.compile(r"`?[:,.;] (?:when|When|unless) that is (?:none|not none|a path)\b")

    def test_no_spec_tells_the_worker_to_run_read_or_edit_a_variable_set_to_none(self):
        mark = "\x00none\x00"                                       # where a none was put in, unlike a "none" of prose
        cases = [(name, {}) for name in self.NAMES] + [("python", {k: "none" for k in self.NONEABLE})]
        for name, override in cases:
            v = self.vars_of(name, **override)
            nones = {k: mark for k in self.NONEABLE if v[k] == "none"}
            seen = 0
            for template in ("inner-pr.json", "smoke.json"):
                for spec, text in self.rendered(template, name, **dict(override, **nones)).items():
                    for m in re.finditer(mark, text):
                        seen += 1
                        around = text[m.start() - 60:m.end() + 40].replace(mark, "none")
                        self.assertRegex(text[m.end():m.end() + 40].replace(mark, "none"), "^" + self.GUARD.pattern,
                                         f"{name}: {spec}: an unguarded none: ...{around}...")
                    plain = text.replace(mark, "none")
                    rules = f"Rules file: {v['RULES']}. When that is a path, read it before anything else"
                    self.assertEqual(plain.count("Rules file:"), plain.count(rules), f"{name}: {spec}")
            self.assertGreater(seen, 0, name)                             # each case puts at least one none in

    def test_the_template_checks_guard_the_profiles_test_files(self):
        defn = json.loads((TEMPLATES / "inner-pr.json").read_text())
        steps = {s["id"]: s for s in defn["steps"]}
        before = {"simplify": "{CHECKS}/files-untouched.sh {WT} {HEAD_BEFORE} {HEAD_AFTER} tests/ pytest.ini",
                  "fix_code": "{CHECKS}/files-untouched.sh {WT} {HEAD_BEFORE} {HEAD_AFTER} tests/ pytest.ini",
                  "implement": "{CHECKS}/files-untouched.sh {WT} {HEAD_BEFORE} {HEAD_AFTER} pytest.ini"}
        typescript = {"simplify": " hb ha ':(glob)**/*.test.ts' vitest.config.ts",
                      "fix_code": " hb ha ':(glob)**/*.test.ts' vitest.config.ts", "implement": " hb ha vitest.config.ts"}
        for name in self.NAMES:
            variables = dict(self.variables(defn, name), HEAD_AFTER="ha")
            for sid in before:
                missing = set()
                untouched = [self.rt.render(c, variables, missing, shell=True) for c in steps[sid]["checks"]
                             if "files-untouched.sh" in c]
                self.assertEqual((len(untouched), missing), (1, set()), f"{name}: {sid}")
                if name == "typescript":
                    self.assertTrue(untouched[0].endswith(typescript[sid]), f"{name}: {sid}: {untouched[0]}")
                else:                                                 # byte for byte the check before profiles
                    self.assertEqual(untouched[0], self.rt.render(before[sid], variables, shell=True), f"{name}: {sid}")

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


class Collector(RouterCase):
    """collector.py: a settled dispatch's logs, its Orca archive and its agent session file, under logs/."""
    CLAUDE_WT, CODEX_WT = "/work/wt-fixture", "/work/wt-codex"
    MAIN = "claude/-work-wt-fixture/11111111-aaaa-4aaa-8aaa-000000000001.jsonl"
    CODEX = "codex/2026/01/10/rollout-2026-01-10T11-00-05-55555555-eeee-4eee-8eee-000000000005.jsonl"

    def test_fake_orca_is_executable(self):
        self.assertTrue(os.access(TESTS / "fake-orca", os.X_OK),          # without it every test that runs Orca fails
                        "tests/fake-orca lost its executable bit: git update-index --chmod=+x plugins/flows/tests/fake-orca")

    def setUp(self):
        super().setUp()
        self.sessions = self.tmp / "sessions"
        shutil.copytree(FIXTURES, self.sessions)
        self.state.mkdir()
        self.scenario()

    def seed(self, dispatch, pr="p1", step="implement", wt=CLAUDE_WT, agent="claude", model="claude-opus-5-5",
             started="2026-01-10T10:00:00Z", ended="2026-01-10T10:30:00Z", outcome="succeeded", checks=("OK tests pass",)):
        """The files the router leaves for one settled worker step, as router.py writes them."""
        st_path = self.state / "chains" / pr / "state.json"
        st = json.loads(st_path.read_text()) if st_path.exists() else {"pr": pr, "status": "running", "vars": {"WT": wt}, "steps": []}
        att = {"n": 1, "started": started, "head_before": "a" * 40, "head_at_start": "a" * 40, "flow_version": 2, "cause": "first",
               "title": f"{pr} {step}", "task": f"task_{dispatch}", "dispatch": dispatch, "ended": ended, "outcome": outcome,
               "report": "", "head_after": "b" * 40, "subject": f"done {SENTINEL}", "summary": f"I did it. {SENTINEL}",
               "release": "released (closed_agent_terminal)", "checks": list(checks)}
        st["steps"].append({"id": step, "status": "done", "attempts": [att],
                            "def": {"id": step, "agent": agent, "model": model, "effort": "high", "worktree": "path:{WT}", "spec": "w.md"}})
        st_path.parent.mkdir(parents=True, exist_ok=True)
        st_path.write_text(json.dumps(st))
        (self.state / "dispatches").mkdir(exist_ok=True)
        (self.state / "dispatches" / f"{dispatch}.json").write_text(json.dumps(
            {"dispatch": dispatch, "task": f"task_{dispatch}", "pr": pr, "step": step, "title": f"{pr} {step}", "started": started,
             "settled": ended, "outcome": outcome}))
        ev = self.state / "events" / dispatch
        ev.mkdir(parents=True)
        (ev / f"{re.sub('[^0-9]', '', ended)}-worker_done-msg_{dispatch}.json").write_text(json.dumps(
            {"message": {"id": f"msg_{dispatch}", "type": "worker_done", "subject": "done", "body": f"Summary. {SENTINEL}",
                         "created_at": ended}, "payload": {"outcome": outcome}, "received": ended, "release": "released (closed_agent_terminal)"}))
        (self.state / "liveness").mkdir(exist_ok=True)
        (self.state / "liveness" / dispatch).write_text(f"{started}\timplementing\n")

    def collect(self, *args, code=None):
        """collector.py collect, or the same arguments to `code` (python -c, which runs collector.main itself)."""
        p = subprocess.run([sys.executable] + (["-c", code] if code else [COLLECTOR]) + ["collect", "--state", str(self.state), "--claude-projects",
                            str(self.sessions / "claude"), "--codex-sessions", str(self.sessions / "codex"),
                            "--orca", str(TESTS / "fake-orca")] + list(args), capture_output=True, text=True, env=self.env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def logs(self, dispatch, pr="p1", step="implement"):
        return self.state / "logs" / pr / step / dispatch

    def meta(self, dispatch, **kw):
        return json.loads((self.logs(dispatch, **kw) / "meta.json").read_text())

    def index(self):
        return [json.loads(l) for l in (self.state / "logs" / "index.jsonl").read_text().splitlines()]

    def reads(self):
        return (self.fake / "reads.log").read_text().splitlines()

    def test_a_three_page_transcript_is_collected_whole_and_in_order(self):
        msgs = lambda *ids: [{"id": i, "role": "assistant", "blocks": [{"type": "text", "text": SENTINEL}]} for i in ids]
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": "ctx_three", "pages": [{"messages": msgs("m1", "m2")}, {"messages": msgs("m3", "m4")},
                                             {"messages": msgs("m5"), "clipping": ["message_limit_or_scan_window", "transcript_payload"]}]},
            {"match": "ctx_loop", "loop": True, "pages": [{"messages": msgs("m1")}, {"messages": msgs("m2")}]}]}))
        self.seed("ctx_three")
        self.seed("ctx_loop", step="validator")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        read = json.loads((self.logs("ctx_three") / "orca-read.json").read_text())
        self.assertEqual([[m["id"] for m in p["transcript"]["messages"]] for p in read["pages"]], [["m1", "m2"], ["m3", "m4"], ["m5"]])
        self.assertEqual((read["status"], read["messages"], read["contentComplete"]), ("ok", 5, False))
        self.assertEqual(read["clipping"], ["message_limit_or_scan_window", "transcript_payload"])
        self.assertEqual([l for l in self.reads() if l.startswith("ctx_three")], ["ctx_three -", "ctx_three cur1", "ctx_three cur2"])
        loop = json.loads((self.logs("ctx_loop", step="validator") / "orca-read.json").read_text())
        self.assertEqual((len(loop["pages"]), loop["stopped"]), (2, "a cursor repeated"))   # a cursor that names itself ends it
        self.assertEqual(next(r for r in self.index() if r["dispatch"] == "ctx_three")["orca"], {"contentComplete": False, "clipping": ["message_limit_or_scan_window", "transcript_payload"]})

    def test_an_archive_not_ready_is_asked_again_then_recorded_as_not_available(self):
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": "ctx_late", "not_ready": 2, "pages": [{"messages": [{"id": "m1", "role": "user", "blocks": []}]}]},
            {"match": "ctx_never", "not_ready": 99}]}))
        self.seed("ctx_late")
        self.seed("ctx_never", step="validator")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        late = json.loads((self.logs("ctx_late") / "orca-read.json").read_text())
        self.assertEqual((late["status"], late["messages"]), ("ok", 1))
        self.assertEqual(len([l for l in self.reads() if l.startswith("ctx_late")]), 3)
        never = json.loads((self.logs("ctx_never", step="validator") / "orca-read.json").read_text())
        self.assertEqual((never["status"], never["error"], never["pages"]), ("not available", "archive_not_ready", []))
        self.assertEqual(len([l for l in self.reads() if l.startswith("ctx_never")]), 4)       # the first try and 3 more
        meta = self.meta("ctx_never", step="validator")
        self.assertEqual(meta["files"]["orca-read.json"]["note"], "orca-read: not available (archive_not_ready)")
        self.assertEqual(meta["orca"]["status"], "not available")
        self.assertEqual(meta["session"]["match"], "unique")        # the session file does not depend on the archive

    def test_the_session_that_overlaps_the_dispatch_is_copied_byte_for_byte_and_its_tokens_summed(self):
        self.seed("ctx_cl", checks=("OK tests pass", "OK read-only: HEAD aaaaaaaaa and the working tree are unchanged"))
        self.seed("ctx_cx", pr="p2", step="review_codex", wt=self.CODEX_WT, agent="codex", model="",
                  started="2026-01-10T11:00:00Z", ended="2026-01-10T11:20:00Z", checks=("OK verdict", "NOT OK no ledger"))
        self.seed("ctx_cx2", pr="p3", step="review_codex", wt=self.CODEX_WT, agent="codex", model="",
                  started="2026-01-10T12:00:00Z", ended="2026-01-10T12:10:00Z")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK collected 3 · skipped 0", out)
        d = self.logs("ctx_cl")
        self.assertEqual((d / "session.jsonl").read_bytes(), (self.sessions / self.MAIN).read_bytes())
        meta = self.meta("ctx_cl")
        # the decoy is two hours earlier, the sidechain-only file and the file whose cwd is another worktree are no candidates
        self.assertEqual((meta["session"]["match"], meta["session"]["candidates"], meta["session"]["path"]),
                         ("unique", 1, str(self.sessions / self.MAIN)))
        sub = (self.sessions / self.MAIN).with_suffix("") / "subagents"
        self.assertEqual(meta["session"]["subagent_files"], 1)
        self.assertEqual(sorted(p.name for p in (d / "session.subagents").iterdir()), ["agent-0001.jsonl", "agent-0001.meta.json"])
        for f in ("agent-0001.jsonl", "agent-0001.meta.json"):
            self.assertEqual((d / "session.subagents" / f).read_bytes(), (sub / f).read_bytes())
        self.assertEqual(meta["files"]["session.subagents/"], {"source": "claude", "path": str(sub), "of": str(self.sessions / self.MAIN),
                                                               "files": ["agent-0001.jsonl", "agent-0001.meta.json"], "summed": ["agent-0001.jsonl"]})
        self.assertEqual((meta["n"], meta["agent"], meta["model"], meta["effort"], meta["worktree"], meta["flow_version"], meta["cause"]),
                         (1, "claude", "claude-opus-5-5", "high", self.CLAUDE_WT, 2, "first"))
        self.assertEqual(sorted(p.name for p in (d / "events").iterdir()), ["20260110103000-worker_done-msg_ctx_cl.json", "liveness"])
        # by hand from the fixture: msg_01's two lines are one response (its last usage counts), msg_03 is a sidechain.
        #   the parent:   msg_01 100/20/1000/0 + msg_02 50/30/0/1000 + msg_04 7/40/100/1100 (opus) = 157/90/1100/2100, 3 turns
        #                 msg_03 10/5/200/0 (haiku, inline sidechain)                                  =  10/5/200/0,     1 turn
        #   subagents/agent-0001.jsonl (haiku): msg_s1's two lines are one response 4/60/300/0, msg_s2 6/80/0/300,
        #                 and its msg_03 is the parent's, counted once                                 =  10/140/300/300, 2 turns
        #   totals: input 157+10+10 = 177, output 90+5+140 = 235, cache_creation 1100+200+300 = 1600,
        #           cache_read 2100+0+300 = 2400, turns 3+1+2 = 6, sidechain turns 1+2 = 3
        tok = json.loads((d / "tokens.json").read_text())
        self.assertEqual({k: tok[k] for k in ("input", "output", "cache_creation", "cache_read", "turns", "sidechain_turns")},
                         {"input": 177, "output": 235, "cache_creation": 1600, "cache_read": 2400, "turns": 6, "sidechain_turns": 3})
        self.assertEqual(tok["subagents"], {"files": 1, "turns": 2, "input": 10, "output": 140, "cache_creation": 300, "cache_read": 300})
        self.assertEqual(tok["by_model"], {
            "claude-opus-5-5": {"input": 157, "output": 90, "cache_creation": 1100, "cache_read": 2100, "turns": 3},
            "claude-haiku-5-5": {"input": 20, "output": 145, "cache_creation": 500, "cache_read": 300, "turns": 3}})
        self.assertEqual((tok["first"], tok["last"]), ("2026-01-10T10:00:40.000Z", "2026-01-10T10:29:10.000Z"))
        # the rollout from /work/wt-elsewhere began 5 s after this one and overlaps the window: another cwd, no candidate
        self.assertEqual(self.meta("ctx_cx", pr="p2", step="review_codex")["session"]["candidates"], 1)
        cx = json.loads((self.logs("ctx_cx", pr="p2", step="review_codex") / "tokens.json").read_text())
        self.assertEqual({k: cx[k] for k in ("input", "output", "cache_creation", "cache_read", "turns")},
                         {"input": 3000, "output": 700, "cache_creation": "not recorded", "cache_read": 2000, "turns": 2})
        self.assertEqual((self.logs("ctx_cx", pr="p2", step="review_codex") / "session.jsonl").read_bytes(),
                         (self.sessions / self.CODEX).read_bytes())
        cx2 = json.loads((self.logs("ctx_cx2", pr="p3", step="review_codex") / "tokens.json").read_text())
        self.assertEqual({k: cx2[k] for k in ("input", "output", "cache_creation", "cache_read", "turns")},
                         {"input": "not recorded", "output": "not recorded", "cache_creation": "not recorded",
                          "cache_read": "not recorded", "turns": 1})
        rows = {r["dispatch"]: r for r in self.index()}
        self.assertEqual({k: rows["ctx_cl"]["tokens"][k] for k in ("input", "output", "cache_creation", "cache_read", "turns")},
                         {"input": 177, "output": 235, "cache_creation": 1600, "cache_read": 2400, "turns": 6})   # the totals
        self.assertEqual(rows["ctx_cl"]["tokens"]["subagents"], tok["subagents"])
        self.assertEqual((rows["ctx_cl"]["duration_s"], rows["ctx_cl"]["checks_ok"], rows["ctx_cx"]["checks_ok"]), (1800, True, False))
        self.assertEqual(rows["ctx_cx"]["session"], {"provider": "codex", "path": str(self.sessions / self.CODEX), "match": "unique", "candidates": 1})
        self.assertEqual((rows["ctx_cl"]["head_before"], rows["ctx_cl"]["head_after"], rows["ctx_cl"]["step"]), ("a" * 40, "b" * 40, "implement"))

    def test_only_the_matched_sessions_own_subagents_are_copied(self):
        # 22222222…/subagents is a decoy: its session is two hours early and never matched. A session a day later has none.
        plain = self.sessions / "claude" / "-work-wt-fixture" / "99999999-aaaa-4aaa-8aaa-000000000009.jsonl"
        plain.write_text((self.sessions / self.MAIN).read_text().replace("2026-01-10", "2026-01-11")
                         .replace("11111111-aaaa-4aaa-8aaa-000000000001", plain.stem))
        self.seed("ctx_a")
        self.seed("ctx_plain", pr="p2", started="2026-01-11T10:00:00Z", ended="2026-01-11T10:30:00Z")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.meta("ctx_plain", pr="p2")["session"]["path"], str(plain))
        self.assertFalse((self.logs("ctx_plain", pr="p2") / "session.subagents").exists())
        tok = json.loads((self.logs("ctx_plain", pr="p2") / "tokens.json").read_text())
        self.assertEqual((tok["subagents"]["files"], tok["output"], tok["turns"]), (0, 95, 4))   # the parent's own, as worked out above
        self.assertNotIn("subagents", next(r for r in self.index() if r["dispatch"] == "ctx_plain")["tokens"])
        self.assertEqual(sorted(p.name for p in (self.logs("ctx_a") / "session.subagents").iterdir()),
                         ["agent-0001.jsonl", "agent-0001.meta.json"])

    def test_two_overlapping_candidates_are_ambiguous_and_nothing_is_copied(self):
        twin = self.sessions / "claude" / "-work-wt-fixture" / "77777777-aaaa-4aaa-8aaa-000000000007.jsonl"
        shutil.copy(self.sessions / self.MAIN, twin)
        self.seed("ctx_amb")
        self.seed("ctx_none", pr="p2", started="2026-02-01T10:00:00Z", ended="2026-02-01T10:30:00Z")
        self.seed("ctx_nowt", pr="p3", wt="")
        self.seed("ctx_gem", pr="p4", agent="gemini")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        meta = self.meta("ctx_amb")
        self.assertEqual((meta["session"]["match"], meta["session"]["why"]), ("ambiguous", "ambiguous: 2 candidates"))
        self.assertEqual(sorted(meta["session"]["paths"]), sorted([str(self.sessions / self.MAIN), str(twin)]))
        self.assertFalse((self.logs("ctx_amb") / "session.jsonl").exists())
        self.assertFalse((self.logs("ctx_amb") / "tokens.json").exists())
        self.assertEqual(self.index()[0]["tokens"], "not recorded")
        self.assertEqual(self.meta("ctx_none", pr="p2")["session"]["why"], "no candidate")
        self.assertEqual(self.meta("ctx_nowt", pr="p3")["session"]["match"], "none")
        self.assertIn("worktree unknown", self.meta("ctx_nowt", pr="p3")["session"]["why"])
        self.assertEqual(self.meta("ctx_gem", pr="p4")["session"]["why"], "provider not supported: gemini")
        # a second candidate that began well after the start does not make the match ambiguous
        twin.write_text(twin.read_text().replace("2026-01-10T10:00:40.000Z", "2026-01-10T10:12:00.000Z"))
        rc, out = self.collect("--dispatch", "ctx_amb", "--force")
        self.assertEqual(rc, 0, out)
        meta = self.meta("ctx_amb")
        self.assertEqual((meta["session"]["match"], meta["session"]["candidates"], meta["session"]["path"]),
                         ("unique", 2, str(self.sessions / self.MAIN)))
        self.assertEqual([r["session"]["match"] for r in self.index() if r["dispatch"] == "ctx_amb"], ["unique"])   # replaced, not added

    def test_collecting_twice_changes_nothing(self):
        self.seed("ctx_a")
        self.seed("ctx_b", pr="p2", step="review_codex", wt=self.CODEX_WT, agent="codex", model="",
                  started="2026-01-10T11:00:00Z", ended="2026-01-10T11:20:00Z")
        tree = lambda: {str(p.relative_to(self.state / "logs")): p.read_bytes()
                        for p in sorted((self.state / "logs").rglob("*")) if p.is_file() and not p.name.startswith(".")}
        self.assertEqual(self.collect("--all")[0], 0)
        first = tree()
        self.assertEqual(len([k for k in first if Path(k).name == "meta.json"]), 2)
        rc, out = self.collect("--all")
        self.assertEqual((rc, tree()), (0, first))
        self.assertIn("skipped 2", out)
        rc, out = self.collect("--all", "--force")                       # collected again from the same sources: the same bytes
        self.assertEqual((rc, tree()), (0, first))
        (self.state / "logs" / "index.jsonl").unlink()                   # a row lost between the tree and the index comes back
        self.assertEqual(self.collect("--all")[0], 0)
        self.assertEqual(tree(), first)
        rc, out = self.R("collect", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry run: 2 settled dispatches · unique 2 · ambiguous 0 · none 0", out)

    def test_index_and_meta_hold_no_transcript_text(self):
        self.seed("ctx_a")
        self.seed("ctx_b", pr="p2", step="review_codex", wt=self.CODEX_WT, agent="codex", model="",
                  started="2026-01-10T11:00:00Z", ended="2026-01-10T11:20:00Z")
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": ".", "pages": [{"messages": [{"id": "m1", "role": "user", "blocks": [{"type": "text", "text": SENTINEL}]}]}]}]}))
        self.assertEqual(self.collect("--all")[0], 0)
        self.assertIn(SENTINEL, (self.logs("ctx_a") / "session.jsonl").read_text())          # the copies are whole
        self.assertIn(SENTINEL, (self.logs("ctx_a") / "orca-read.json").read_text())
        files = [self.state / "logs" / "index.jsonl"] + sorted((self.state / "logs").rglob("meta.json"))
        self.assertEqual(len(files), 3)
        self.assertEqual([str(f) for f in files if SENTINEL in f.read_text()], [])

    def test_dry_run_copies_nothing(self):
        self.seed("ctx_a")
        rc, out = self.collect("--all", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("ctx_a · p1 implement · claude · unique", out)
        self.assertFalse((self.state / "logs").exists())
        self.assertFalse((self.fake / "reads.log").exists())

    def test_a_page_that_repeats_under_a_new_cursor_ends_the_read(self):
        msgs = lambda *ids: [{"id": i, "role": "assistant", "blocks": []} for i in ids]
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": "ctx_rep", "pages": [{"messages": msgs("m1", "m2")}, {"messages": msgs("m3")}, {"messages": msgs("m3")},
                                           {"messages": msgs("m3")}, {"messages": msgs("m4")}]}]}))
        self.seed("ctx_rep")
        rc, out = self.collect("--dispatch", "ctx_rep")
        self.assertEqual(rc, 0, out)
        read = json.loads((self.logs("ctx_rep") / "orca-read.json").read_text())
        self.assertEqual([[m["id"] for m in p["transcript"]["messages"]] for p in read["pages"]], [["m1", "m2"], ["m3"]])
        self.assertEqual((read["stopped"], read["messages"]), ("a page repeated", 3))
        self.assertEqual(self.reads(), ["ctx_rep -", "ctx_rep cur1", "ctx_rep cur2"])     # each cursor new, the third page not kept

    def test_source_changed_starts_the_read_afresh_once(self):
        msgs = lambda *ids: [{"id": i, "role": "assistant", "blocks": []} for i in ids]
        pages = [{"messages": msgs("m1")}, {"messages": msgs("m2")}, {"messages": msgs("m3")}]
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": "ctx_once", "source_changed": 1, "pages": pages}, {"match": "ctx_twice", "source_changed": 2, "pages": pages}]}))
        self.seed("ctx_once")
        self.seed("ctx_twice", step="validator")
        rc, out = self.collect("--all")
        self.assertEqual(rc, 0, out)
        once = json.loads((self.logs("ctx_once") / "orca-read.json").read_text())
        self.assertEqual([[m["id"] for m in p["transcript"]["messages"]] for p in once["pages"]], [["m1"], ["m2"], ["m3"]])
        self.assertEqual((once["status"], once["restarted"], once.get("error")), ("ok", "source_changed", None))
        self.assertEqual([l for l in self.reads() if l.startswith("ctx_once")],
                         ["ctx_once -", "ctx_once cur1", "ctx_once -", "ctx_once cur1", "ctx_once cur2"])
        twice = json.loads((self.logs("ctx_twice", step="validator") / "orca-read.json").read_text())   # a second one is recorded
        self.assertEqual([[m["id"] for m in p["transcript"]["messages"]] for p in twice["pages"]], [["m1"]])
        self.assertEqual((twice["restarted"], twice["error"]), ("source_changed", "page 2: source_changed"))

    def test_tries_counts_the_calls_made(self):
        (self.fake / "scenario.json").write_text(json.dumps({"rules": [], "reads": [
            {"match": "ctx_gone", "error": "dispatch_not_found"}, {"match": "ctx_never", "not_ready": 99}]}))
        self.seed("ctx_gone")
        self.seed("ctx_never", step="validator")
        self.assertEqual(self.collect("--all")[0], 0)
        gone = json.loads((self.logs("ctx_gone") / "orca-read.json").read_text())
        self.assertEqual((gone["status"], gone["error"], gone["tries"]), ("not available", "dispatch_not_found", 1))   # not retryable
        never = json.loads((self.logs("ctx_never", step="validator") / "orca-read.json").read_text())
        self.assertEqual((never["error"], never["tries"]), ("archive_not_ready", 4))
        self.assertEqual([l.split()[0] for l in self.reads()].count("ctx_gone"), 1)

    def test_a_dispatch_that_fails_in_any_way_is_journaled_and_the_rest_are_collected(self):
        self.seed("ctx_ok")
        bad = self.state / "chains" / "p0" / "state.json"           # a step that is not an object: describe() raises AttributeError
        bad.parent.mkdir(parents=True)
        bad.write_text(json.dumps({"pr": "p0", "steps": ["not a step"]}))
        (self.state / "dispatches" / "ctx_bad.json").write_text(json.dumps(
            {"dispatch": "ctx_bad", "pr": "p0", "step": "implement", "started": "2026-01-10T10:00:00Z", "settled": "2026-01-10T10:30:00Z"}))
        rc, out = self.collect("--all")
        self.assertEqual(rc, 1, out)
        self.assertIn("collected ctx_ok", out)
        self.assertIn("NOT OK collected 1 · skipped 0 · locked 0 · not settled 0 · failed 1", out)
        line = "collector: ctx_bad: AttributeError: 'str' object has no attribute 'get'"
        self.assertEqual(self.journal().count(line), 1, self.journal())
        rc, out = self.collect("--dispatch", "ctx_bad", "--wait-settled", "0.2")   # the daemon's hook: describe() inside the wait
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.journal().count(line), 2, self.journal())

    def test_a_held_lock_is_reported_and_journaled_once(self):
        self.seed("ctx_a")
        lock = self.state / "logs" / ".locks" / "ctx_a"
        lock.mkdir(parents=True)
        held = time.time() - 600                                         # taken 10 minutes ago: still live
        os.utime(lock, (held, held))
        for _ in range(2):
            rc, out = self.collect("--dispatch", "ctx_a")
            self.assertEqual(rc, 0, out)
            self.assertIn("skip ctx_a: locked by another collector", out)
            self.assertIn("OK collected 0 · skipped 0 · locked 1 · not settled 0 · failed 0", out)
        self.assertEqual(self.journal().count("collector: ctx_a: locked by another collector since"), 1, self.journal())
        self.assertEqual(int(lock.stat().st_mtime), int(held))          # the "reported" marker does not make the lock younger
        self.assertFalse(self.logs("ctx_a").exists())
        old = time.time() - 3 * 3600                                     # a lock older than 2 hours is a dead collector's
        os.utime(lock, (old, old))
        rc, out = self.collect("--dispatch", "ctx_a")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK collected 1 · skipped 0 · locked 0", out)
        self.assertFalse(lock.exists())

    def test_a_lock_its_owner_removes_before_the_stat_is_taken_and_the_dispatch_is_collected(self):
        self.seed("ctx_a")
        lock = self.state / "logs" / ".locks" / "ctx_a"
        lock.mkdir(parents=True)                                         # held when the mkdir fails, gone by the stat
        code = (f"import shutil, sys\nsys.path.insert(0, {str(KIT)!r})\nimport collector\nstat, lock, fired = collector.Path.stat, "
                f"collector.Path({str(lock)!r}), []\n"
                "def raced(p, *a, **k):\n"
                "    if p == lock and not fired:\n"
                "        fired.append(p)\n        shutil.rmtree(p)\n        raise FileNotFoundError(2, 'No such file or directory', str(p))\n"
                "    return stat(p, *a, **k)\n"
                "collector.Path.stat = raced\nsys.exit(collector.main())\n")
        rc, out = self.collect("--dispatch", "ctx_a", code=code)
        self.assertEqual(rc, 0, out)
        self.assertIn("OK collected 1 · skipped 0 · locked 0 · not settled 0 · failed 0", out)
        self.assertTrue((self.logs("ctx_a") / "meta.json").exists())
        self.assertFalse(lock.exists())
        self.assertNotIn("collector: ctx_a:", self.journal())

    def test_a_failed_collection_leaves_no_temporary_directory(self):
        self.seed("ctx_a")
        ev = next((self.state / "events" / "ctx_a").iterdir())
        ev.chmod(0)                                                      # the events copy fails after orca-read.json is written
        rc, out = self.collect("--dispatch", "ctx_a")
        self.assertEqual(rc, 1, out)
        self.assertIn("collector: ctx_a: [Errno 13] Permission denied", out)
        self.assertEqual(list((self.state / "logs" / "p1" / "implement").iterdir()), [])   # no .ctx_a.<pid>.tmp
        ev.chmod(0o644)
        rc, out = self.collect("--dispatch", "ctx_a")
        self.assertEqual(rc, 0, out)
        self.assertEqual([p.name for p in (self.state / "logs" / "p1" / "implement").iterdir()], ["ctx_a"])

    # -- the daemon's hook
    def session_for(self, wt, sid="99999999-aaaa-4aaa-8aaa-000000000009", base=None, at=(1, 2)):
        """A Claude session that ran in wt, its two lines at base + at seconds (base: now), with the sentinel in it."""
        t = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime((base or time.time()) + s))
        line = lambda typ, ts, **kw: json.dumps(dict({"type": typ, "timestamp": ts, "cwd": str(wt), "sessionId": sid,
                                                      "isSidechain": False}, **kw))
        path = self.tmp / "live-claude" / str(wt).replace("/", "-") / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line("user", t(at[0]), message={"role": "user", "content": SENTINEL}) + "\n" + line(
            "assistant", t(at[1]), message={"id": "msg_1", "model": "m-sonnet", "usage": {"input_tokens": 3, "output_tokens": 4}}) + "\n")
        return path

    def test_a_released_worker_is_collected_once_after_its_checks(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        session = self.session_for(wt)
        self.env["FLOWS_CLAUDE_PROJECTS"] = str(self.tmp / "live-claude")
        self.scenario({"match": "t1 a", "events": [self.done(0.2)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", worktree="path:{WT}", checks=["echo OK the check ran"])], WT=str(wt))
        self.until(lambda: self.chain_state()["status"] == "done")
        dispatch = self.chain_state()["steps"][0]["attempts"][0]["dispatch"]
        meta = self.state / "logs" / "t1" / "a" / dispatch / "meta.json"
        self.until(meta.exists, what="the collector's meta.json")
        m = json.loads(meta.read_text())
        self.assertEqual((m["session"]["match"], m["session"]["path"]), ("unique", str(session)))
        self.assertEqual((m["checks"], m["worktree"], m["agent"], m["model"]), (["OK the check ran"], str(wt), "claude", "m-sonnet"))
        self.assertIsNone(m["effective"])                                   # Orca launched what was asked
        self.assertEqual(json.loads((meta.parent / "tokens.json").read_text())["output"], 4)
        time.sleep(1.0)
        self.assertEqual([r["dispatch"] for r in self.index()], [dispatch])                 # exactly one collection
        self.assertEqual((self.fake / "reads.log").read_text().split(), [dispatch, "-"])
        self.assertIn("logs    1/1 settled dispatches collected", self.R("status")[1])

    def test_a_collector_that_fails_is_journaled_and_the_daemon_goes_on(self):
        not_a_dir = self.tmp / "projects-file"
        not_a_dir.write_text("")
        self.env["FLOWS_CLAUDE_PROJECTS"] = str(not_a_dir)
        wt = self.tmp / "wt"
        wt.mkdir()
        self.scenario({"match": "t1 a", "events": [self.done(0.2)]}, {"match": "t1 b", "events": [self.done(0.2)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", worktree="path:{WT}"), self.worker("b", worktree="path:{WT}")], WT=str(wt))
        self.until(lambda: self.chain_state()["status"] == "done", what="the second worker to settle")
        first = self.chain_state()["steps"][0]["attempts"][0]["dispatch"]
        self.until(lambda: f"collector: {first}: the Claude projects root is not a directory" in self.journal(), what="the journal line")
        self.assertTrue(self.R("status")[1].startswith("router  run_fake · mailbox alive"))
        self.assertIn("logs    0/2 settled dispatches collected · router.py collect --all", self.R("status")[1])

    def test_a_step_records_what_it_asked_for_and_what_orca_launched(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        self.env["FLOWS_CLAUDE_PROJECTS"] = str(self.tmp / "live-claude")
        self.scenario({"match": "t1 a", "effective": {"model": "m-haiku"}, "events": [self.done(0.2)]})
        self.R("init", ok=True)
        self.chain([self.worker("a", worktree="path:{WT}", effort="high")], WT=str(wt))
        self.until(lambda: self.chain_state()["status"] == "done")
        att = self.chain_state()["steps"][0]["attempts"][0]
        self.assertEqual({k: att[k] for k in ("agent", "model", "effort", "worktree")},
                         {"agent": "claude", "model": "m-sonnet", "effort": "high", "worktree": f"path:{wt}"})
        self.assertEqual(att["effective"], {"agent": "claude", "model": "m-haiku", "effort": "high"})
        meta = self.state / "logs" / "t1" / "a" / att["dispatch"] / "meta.json"
        self.until(meta.exists, what="the collector's meta.json")
        m = json.loads(meta.read_text())
        self.assertEqual((m["model"], m["effective"]), ("m-sonnet", att["effective"]))

    def test_an_ad_hoc_worker_beside_the_coordinators_session_is_matched_unique_under_adhoc(self):
        wt = self.repo()                                                 # `--worktree current`: the checkout the command runs in
        top = subprocess.run(["git", "-C", str(wt), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        self.env["FLOWS_CLAUDE_PROJECTS"] = str(self.tmp / "live-claude")
        self.scenario({"match": "helper", "start_delay": 4, "events": [self.done(3)]})
        self.R("init", ok=True)
        t0 = time.time()
        p = subprocess.run([sys.executable, ROUTER, "worker", "helper", "--spec", "look", "--agent", "claude", "--model", "m-sonnet",
                            "--effort", "low", "--pr", "b9"], cwd=wt, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        dispatch = re.search(r"dispatch (\S+)", p.stdout).group(1)
        reg = json.loads((self.state / "dispatches" / f"{dispatch}.json").read_text())
        self.assertEqual({k: reg[k] for k in ("adhoc", "agent", "model", "effort", "worktree")},
                         {"adhoc": True, "agent": "claude", "model": "m-sonnet", "effort": "low", "worktree": f"path:{top}"})
        self.assertLessEqual(reg["started"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 + 1)))   # before worker-start returned
        # the worker's session began 1.5 s into the 4 s worker-start; the coordinator's own session spans the whole hour
        worker = self.session_for(top, base=t0, at=(1.5, 2.5))
        self.session_for(top, sid="88888888-aaaa-4aaa-8aaa-000000000008", base=t0, at=(-3600, 3600))
        meta = self.state / "logs" / "b9" / "_adhoc" / dispatch / "meta.json"
        self.until(meta.exists, seconds=30, what="the collector's meta.json")
        m = json.loads(meta.read_text())
        self.assertEqual((m["session"]["match"], m["session"]["candidates"], m["session"]["path"]), ("unique", 2, str(worker)))
        self.assertEqual((m["adhoc"], m["agent"], m["model"], m["effort"], m["worktree"], m["worktree_source"]),
                         (True, "claude", "m-sonnet", "low", top, f"registry: path:{top}"))
        self.assertEqual([(r["dispatch"], r["pr"], r["step"], r["agent"], r["model"], r["effort"], r["session"]["match"],
                           r["tokens"]["output"]) for r in self.index()], [(dispatch, "b9", "", "claude", "m-sonnet", "low", "unique", 4)])

    def test_collection_can_be_turned_off(self):
        self.env["ROUTER_COLLECT"] = "0"
        self.scenario({"match": "t1 a", "events": [self.done(0.2)]})
        self.R("init", ok=True)
        self.chain([self.worker("a")])
        self.until(lambda: self.chain_state()["status"] == "done")
        time.sleep(0.5)
        self.assertFalse((self.state / "logs").exists())
        self.assertIn("(ROUTER_COLLECT=0: nothing is collected by itself)", self.R("status")[1])


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


class Report(unittest.TestCase):
    """report.py: the run report, from the records alone. The fixture tests/fixtures/report-run/ is a synthetic state
    directory: 3 PRs (A1, B1 under flow v1, A2 under v2), 10 chain dispatches and 2 ad hoc ones, a retry, a check NOT OK,
    gate waits, questions, a silent ring, a Codex session that wrote no token counts, a session with subagent files, an
    attempt Orca launched on another model, and two collector journal lines."""
    REPORT = str(KIT / "report.py")
    RUN = TESTS / "fixtures" / "report-run"
    SENTINEL = "SENTINEL-c0ffee-not-for-the-report"   # in every fake session line, summary, subject, answer and note

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="report-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self, state=None, *extra):
        out, met = self.tmp / "r.html", self.tmp / "m.json"
        p = subprocess.run([sys.executable, self.REPORT, "--state", str(state or self.RUN), "--out", str(out), "--metrics", str(met)]
                           + list(extra), capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return out.read_text(), json.loads(met.read_text()), p.stdout

    def copy(self):
        state = self.tmp / "state"
        shutil.copytree(self.RUN, state)
        return state

    @staticmethod
    def first_difference(a, b, path="$"):
        if type(a) is not type(b):
            return f"{path}: {a!r} != {b!r}"
        if isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a or k not in b:
                    return f"{path}.{k}: {'missing in the metrics' if k not in a else 'not in the golden'}"
                d = Report.first_difference(a[k], b[k], f"{path}.{k}")
                if d:
                    return d
            return ""
        if isinstance(a, list):
            if len(a) != len(b):
                return f"{path}: {len(a)} items != {len(b)}"
            for i, (x, y) in enumerate(zip(a, b)):
                d = Report.first_difference(x, y, f"{path}[{i}]")
                if d:
                    return d
            return ""
        return "" if a == b else f"{path}: {a!r} != {b!r}"

    @staticmethod
    def numbers(obj):
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from Report.numbers(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from Report.numbers(v)

    def golden(self):
        return json.loads((self.RUN / "metrics.golden.json").read_text())

    def test_the_metrics_equal_the_golden(self):
        _page, m, out = self.report()
        self.assertEqual(self.first_difference(m, self.golden()), "")
        self.assertIn("3 PRs · 12 dispatches (2 ad hoc) · 11 collected sessions", out)

    def test_the_golden_numbers_worked_out_by_hand(self):
        """The arithmetic behind the golden, from the fixture's timestamps and tokens.json files."""
        g = self.golden()
        t = g["time"]
        # A1: created 09:00:00, chain complete 11:00:00. Workers: contract 09:00:10-09:20:10 (1200), implement #1
        # 09:50:20-10:10:20 (1200), implement #2 10:15:30-10:35:30 (1200), review 10:45:40-10:55:40 (600); ci is a script.
        # Gates: accept paused 09:20:15 .. gate passed 09:50:15 (1800), merge 10:55:45 .. 10:59:45 (240).
        # Paused: implement failed 10:10:25 .. coordinator retry 10:15:25 (300).
        self.assertEqual((t["A1"]["wall_s"], t["A1"]["worker_s"], t["A1"]["gate_s"], t["A1"]["paused_s"]),
                         (7200, 1200 + 1200 + 1200 + 600, 1800 + 240, 300))
        self.assertEqual(t["A1"]["worker_covered_s"], 4200)                    # no two of A1's workers ran at once
        self.assertEqual(t["A1"]["rest_s"], 7200 - 4200 - 2040 - 300)          # 660: ci 600 and 60 s of starts and checks
        self.assertEqual(t["A1"]["adhoc_s"], 120)                               # the arbiter, 10:12:00 .. 10:14:00
        # B1: 09:05:00 .. 10:20:00. Workers 1200 + 1200 + 600; gates accept 09:25:15 .. 09:35:15 (600), merge 10:15:45 ..
        # 10:19:45 (240); the check NOT OK paused 09:55:30 .. accepted 10:00:30 (300).
        self.assertEqual((t["B1"]["wall_s"], t["B1"]["worker_s"], t["B1"]["gate_s"], t["B1"]["paused_s"], t["B1"]["rest_s"]),
                         (4500, 3000, 600 + 240, 300, 4500 - 3000 - 840 - 300))
        # A2 is still at its merge gate: no wall clock, no rest; workers 900 + 1800 + 600; accept 11:15:20 .. 11:55:20.
        self.assertEqual((t["A2"]["wall_s"], t["A2"]["rest_s"], t["A2"]["worker_s"], t["A2"]["gate_s"], t["A2"]["open_waits"]),
                         ("not recorded", "not recorded", 900 + 1800 + 600, 2400, 1))
        self.assertEqual(t["total"]["worker_s"], 4200 + 3000 + 3300)
        self.assertEqual(g["outside"]["adhoc_s"], 300)                         # ctx_adhoc_none 12:50:00 .. 12:55:00
        s = g["steps"]
        # implement per PR: A1 1200 + 1200, B1 1200, A2 1800 -> median 1800, max 2400 in A1; contract 1200, 1200, 900.
        self.assertEqual((s["implement"]["median_s"], s["implement"]["max_s"], s["implement"]["max_pr"]), (1800, 2400, "A1"))
        self.assertEqual((s["implement"]["attempts"], s["implement"]["by_cause"]), (4, {"first": 3, "retry": 1}))
        self.assertEqual((s["contract"]["median_s"], s["ci"]["median_s"], s["ci"]["max_s"]), (1200, 600, 600))
        self.assertEqual((s["implement"]["check_not_ok"], s["implement"]["check_not_ok_prs"]), (1, ["B1"]))
        i = g["interruptions"]
        self.assertEqual(i["rings"]["gate"], {"A1": 2, "B1": 2, "A2": 2, "total": 6})   # A2's merge rang and is open
        self.assertEqual((i["rings"]["failed"]["total"], i["rings"]["check"]["total"], i["rings"]["silent"]), (1, 1, {"A2": 1, "total": 1}))
        # A1's question 10:20:00 .. replied 10:26:40 (400); A2's 12:00:00 .. 12:01:40 (100); median (400 + 100) / 2.
        self.assertEqual([q["answer_s"] for q in i["questions"]], [400, 100, "not recorded"])
        self.assertEqual((i["answer_median_s"], i["answer_max_s"]), ((400 + 100) // 2, 400))
        self.assertEqual(i["questions_by_role"], {"implementer.md": 2, "review-claude.md": 1})
        lab = g["tokens"]["by_label"]
        # fable: A1 contract 1000/2000/3000/40000/10, B1 contract 1000/1500/2000/24000/10, A2 contract 600/900/1100/15000/6,
        # the arbiter 100/200/300/5000/2.
        self.assertEqual([lab["claude-fable-5-1"][k] for k in ("input", "output", "cache_creation", "cache_read", "turns")],
                         [1000 + 1000 + 600 + 100, 2000 + 1500 + 900 + 200, 3000 + 2000 + 1100 + 300, 40000 + 24000 + 15000 + 5000, 10 + 10 + 6 + 2])
        # opus: A1 implement #1 500/1500/2000/30000/8 and #2 700/2500/1000/50000/12, A2 implement 900/3000/2500/60000/15,
        # A2 review 300/800/600/9000/5.
        self.assertEqual([lab["claude-opus-5-5"][k] for k in ("input", "output", "cache_creation", "cache_read", "turns")],
                         [500 + 700 + 900 + 300, 1500 + 2500 + 3000 + 800, 2000 + 1000 + 2500 + 600, 30000 + 50000 + 60000 + 9000, 8 + 12 + 15 + 5])
        # codex: A1 review counted 20000/3000/-/15000/6; B1 review wrote no counts; the ad hoc one matched no session.
        self.assertEqual((lab["codex"]["dispatches"], lab["codex"]["tokens_files"], lab["codex"]["input"], lab["codex"]["cache_creation"]),
                         (3, 2, 20000, "not recorded"))
        bym = g["tokens"]["by_model"]
        # the session-reported models: B1 contract's parent ran fable (800/1200/1500/20000/7), its subagents haiku.
        self.assertEqual(bym["claude-fable-5-1"]["input"], 1000 + 800 + 600 + 100)
        self.assertEqual(bym["claude-haiku-5-5"], {"input": 200, "output": 300, "cache_creation": 500, "cache_read": 4000, "turns": 3})
        self.assertEqual(g["review"]["by_step"]["review"], {"findings": 3 + 2, "recorded": 2, "not_recorded": 1})

    def test_the_page_shows_every_number_of_the_golden_and_names_what_is_missing(self):
        page, _m, _ = self.report()
        text = re.sub(r"<[^>]+>", " ", page)
        missing = sorted({str(n) for n in self.numbers(self.golden()) if str(n) not in text})
        self.assertEqual(missing, [])
        codex = [r for r in page.split("<tr>") if "ctx_b1_review" in r and "unique" in r][0]
        self.assertEqual(codex.count("not recorded"), 5)                       # the Codex session that wrote no token counts
        self.assertIn("not priced", page)
        self.assertIn("<td><code>ctx_adhoc_none</code></td><td>-</td><td>_adhoc</td><td>none</td>",
                      page.split("Dispatches with no session match")[1].split("</table>")[0])
        for chart in ("a · Where the time went", "b · Steps", "c · Interruptions", "d · Tokens", "e · Review", "f · The flow", "g · Lessons"):
            self.assertIn(chart, page)
        self.assertEqual(page.count("<svg"), 5 + 3 * 2)                       # sections a to e; per PR a timeline and its time
        self.assertNotIn("<script", page)
        self.assertIn("prefers-color-scheme:dark", page)

    def test_with_a_price_file_the_cost_table_appears(self):
        prices = self.tmp / "prices.json"
        prices.write_text(json.dumps({"claude-opus-5-5": {"input": 15, "output": 75, "cache_creation": 18.75, "cache_read": 1.5},
                                      "gpt-5-codex": {"input": 1.25, "output": 10, "cache_read": 0.125}}))
        page, m, _ = self.report(None, "--prices", str(prices))
        c = m["tokens"]["cost"]
        # opus: 2400 x 15 + 7800 x 75 + 6100 x 18.75 + 149000 x 1.5 per million = 0.036 + 0.585 + 0.114375 + 0.2235
        self.assertEqual(c["claude-opus-5-5"]["total"], 0.958875)
        self.assertEqual(c["claude-opus-5-5"]["cache_creation"], 0.114375)
        # Codex counts cached input inside input: (20000 - 15000) x 1.25 + 3000 x 10 + 15000 x 0.125 per million
        self.assertEqual(c["gpt-5-codex"]["input"], 0.00625)
        self.assertEqual((c["gpt-5-codex"]["total"], c["gpt-5-codex"]["unpriced"]), (0.038125, ["cache_creation"]))
        self.assertEqual(c["claude-sonnet-5-5"], "not priced")
        self.assertIn("<td>0.958875</td>", page)
        self.assertIn("<td>0.038125</td>", page)

    def test_the_page_and_the_metrics_hold_no_session_text(self):
        page, m, _ = self.report()
        metrics = json.dumps(m)
        texts = set()

        def strings(o, text=False):
            if isinstance(o, str) and text:
                yield o
            elif isinstance(o, dict):
                for k, v in o.items():
                    yield from strings(v, text or k in ("text", "content", "instructions"))
            elif isinstance(o, list):
                for v in o:
                    yield from strings(v, text)

        files = sorted(self.RUN.glob("logs/*/*/*/session.jsonl")) + sorted(self.RUN.glob("logs/*/*/*/session.subagents/*.jsonl"))
        self.assertEqual(len(files), 11 + 2)
        for f in files:
            for line in f.read_text().splitlines():
                texts.update(s for s in strings(json.loads(line)) if " " in s)  # the text content of each line
        self.assertGreater(len(texts), 30)
        for body in (page, metrics):
            self.assertNotIn(self.SENTINEL, body)
            self.assertEqual([s for s in texts if s in body], [])

    def test_a_run_whose_logs_were_never_collected_still_has_a_page(self):
        state = self.copy()
        shutil.rmtree(state / "logs")
        page, m, out = self.report(state)
        g = self.golden()
        self.assertEqual(m["time"], g["time"])                                  # from state.json and journal.md
        self.assertEqual(m["interruptions"], g["interruptions"])
        self.assertEqual(m["steps"], g["steps"])
        self.assertEqual(m["tokens"], {k: "not recorded" for k in ("by_label", "by_model", "dispatches", "cost", "no_session")})
        self.assertEqual((m["run"]["logs"], m["run"]["collected_sessions"], m["run"]["dispatches"]), (False, 0, 12))
        self.assertIn("not recorded: there is no logs/ directory", page)
        self.assertIn("no logs/: tokens not recorded", out)

    def test_router_report_writes_report_html_in_the_state_by_default(self):
        state = self.copy()
        env = dict(os.environ, ROUTER_STATE=str(state))
        p = subprocess.run([sys.executable, ROUTER, "report"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(str(state / "report.html"), p.stdout)
        self.assertIn("A synthetic run for the report", (state / "report.html").read_text())
        p = subprocess.run([sys.executable, ROUTER, "--help"], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn("router.py report [--out <file>] [--pr <pr>]", p.stdout)
        p = subprocess.run([sys.executable, ROUTER, "report", "--pr", "B1", "--out", str(self.tmp / "b1.html")], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        b1 = (self.tmp / "b1.html").read_text()
        self.assertIn('id="pr-B1"', b1)
        self.assertNotIn('id="pr-A1"', b1)

    def test_ad_hoc_workers_are_listed_in_their_prs_section_and_counted_apart(self):
        page, m, _ = self.report()
        self.assertEqual((m["run"]["chain_dispatches"], m["run"]["adhoc_dispatches"]), (10, 2))
        a1 = page.split('id="pr-A1"')[1].split("</section>")[0]
        self.assertIn("ctx_adhoc_arbiter", a1)
        outside = page.split('id="pr-outside"')[1].split("</section>")[0]
        self.assertIn("ctx_adhoc_none", outside)
        self.assertNotIn("ctx_adhoc_none", a1)
        self.assertEqual(m["tokens"]["no_session"], [{"dispatch": "ctx_adhoc_none", "pr": "-", "step": "_adhoc", "match": "none"}])

    def test_totals_include_the_subagents_and_their_share_is_shown(self):
        tok = json.loads((self.RUN / "logs/B1/contract/ctx_b1_contract/tokens.json").read_text())
        parent, sub = tok["by_model"]["claude-fable-5-1"], tok["subagents"]
        for k in ("input", "output", "cache_creation", "cache_read", "turns"):
            self.assertEqual(tok[k], parent[k] + sub[k])                       # the fixture: totals = parent + subagents
        page, m, _ = self.report()
        self.assertEqual(m["tokens"]["by_label"]["claude-fable-5-1"]["subagents"],
                         {"files": 2, "turns": 3, "input": 200, "output": 300, "cache_creation": 500, "cache_read": 4000})
        self.assertEqual(m["tokens"]["by_label"]["codex"]["subagents"], "not recorded")
        self.assertIn("The subagents' share of those totals", page)

    def test_a_model_orca_launched_instead_is_named_requested_to_effective(self):
        page, m, _ = self.report()
        self.assertIn("claude-opus-5-5 -&gt; claude-sonnet-5-5", page)
        self.assertEqual(m["tokens"]["by_label"]["claude-opus-5-5 -> claude-sonnet-5-5"]["dispatches"], 1)
        state = self.copy()                                                     # make B1's implement the slowest step
        st = json.loads((state / "chains/B1/state.json").read_text())
        st["steps"][2]["attempts"][0]["ended"] = "2026-01-12T10:35:20Z"
        (state / "chains/B1/state.json").write_text(json.dumps(st))
        _page, m, _ = self.report(state)
        slow = m["lessons"][0]
        self.assertEqual((slow["lesson"], slow["pr"], slow["step"], slow["value"], slow["model"]),
                         ("slowest step", "B1", "implement", 3600, "claude-opus-5-5 -> claude-sonnet-5-5"))

    def test_collector_lines_are_counted_apart_from_failed_steps(self):
        _page, m, _ = self.report()
        r = m["interruptions"]["rings"]
        self.assertEqual(r["collector"], {"-": 1, "total": 1})                 # "collector: ctx_gone: the router has no record ..."
        self.assertEqual(r["locked"], {"B1": 1, "total": 1})                   # "... locked by another collector since ..."
        self.assertEqual(r["failed"], {"A1": 1, "total": 1})                   # only A1's implement

    def test_the_lessons_name_their_records(self):
        _page, m, _ = self.report()
        got = [(x["lesson"], x["value"], x["pr"], x["step"], x["dispatch"]) for x in m["lessons"]]
        self.assertEqual(got, [("slowest step", 2400, "A1", "implement", "ctx_a1_impl1, ctx_a1_impl2"),
                               ("most-retried step", 2, "A1", "implement", "ctx_a1_impl1, ctx_a1_impl2"),
                               ("role that asked the most questions", 2, "A1", "implement", "ctx_a1_impl2"),
                               ("longest wait at a gate", 2400, "A2", "accept", "-")])

    def test_workers_that_run_at_once_are_covered_once_in_the_rest(self):
        state = self.copy()                                                     # B1's review in a group with its implement
        st = json.loads((state / "chains/B1/state.json").read_text())
        st["steps"][4]["attempts"][0].update(started="2026-01-12T09:45:20Z", ended="2026-01-12T09:55:20Z")
        (state / "chains/B1/state.json").write_text(json.dumps(st))
        _page, m, _ = self.report(state)
        t = m["time"]["B1"]
        # implement 09:35:20 .. 09:55:20 (1200) and review 09:45:20 .. 09:55:20 (600): summed 3000, covered 1200 + 1200
        self.assertEqual((t["worker_s"], t["worker_covered_s"]), (1200 + 1200 + 600, 1200 + 1200))
        self.assertEqual(t["rest_s"], 4500 - 2400 - 840 - 300)

    def test_a_gate_wait_and_a_pause_never_include_each_other(self):
        state = self.copy()                                                     # a pause the coordinator never ended
        lines = (state / "journal.md").read_text().splitlines()
        lines = [l for l in lines if "coordinator: retry" not in l]
        (state / "journal.md").write_text("\n".join(lines) + "\n")
        _page, m, _ = self.report(state)
        self.assertEqual((m["time"]["A1"]["paused_s"], m["time"]["A1"]["gate_s"], m["time"]["A1"]["open_waits"]), (0, 2040, 1))
        self.assertEqual(m["time"]["A1"]["rest_s"], 7200 - 4200 - 2040)

    def test_an_attempt_without_a_cause_is_first_only_when_it_is_the_first(self):
        state = self.copy()                                                     # a chain started before flows recorded causes
        for pr in ("A1", "B1", "A2"):
            st = json.loads((state / f"chains/{pr}/state.json").read_text())
            for step in st["steps"]:
                for a in step["attempts"]:
                    a.pop("cause", None)
            (state / f"chains/{pr}/state.json").write_text(json.dumps(st))
        _page, m, _ = self.report(state)
        self.assertEqual(m["steps"]["implement"]["by_cause"], {"first": 3, "not recorded": 1})
        self.assertEqual(m["steps"]["contract"]["by_cause"], {"first": 3})

    def test_report_py_writes_only_the_files_it_is_given(self):
        state = self.copy()
        self.addCleanup(shutil.rmtree, state, True)                             # after the modes below are restored
        for f in list(state.glob("logs/*/*/*/session.jsonl")) + list(state.glob("logs/*/*/*/session.subagents")):
            f.chmod(0)                                                          # a session file the report opened would fail it
        self.addCleanup(lambda: [f.chmod(0o755) for f in state.glob("logs/*/*/*/session.subagents")])
        before = sorted((p.relative_to(state), p.stat().st_mtime_ns) for p in state.rglob("*"))
        self.report(state)
        self.assertEqual(sorted((p.relative_to(state), p.stat().st_mtime_ns) for p in state.rglob("*")), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["m.json", "r.html", "state"])
        src = Path(self.REPORT).read_text()
        self.assertNotIn("session.jsonl\")", src)                               # never opened: tokens.json and meta.json only
        self.assertNotIn("subprocess", src)                                    # and Orca is never called


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
