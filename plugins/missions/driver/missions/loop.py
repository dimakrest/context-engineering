"""The loop (design §6): preflight, then `while True:` until a typed stop.

Every iteration reloads state from disk, checks the caps and the gates, sends open issues through
the triage judgment step, picks the first ready feature of the current milestone, and runs the
worker step; a milestone whose features are all done goes to VALIDATE (validate.run_validate: one
round per call, the loop comes back for the repairs it scheduled), and a driver started in
`validating` or `negotiating` resumes the round it finds. It exits only through stop(reason) --
steps.stop, re-exported here with EXIT_CODES. `--until validate` stops where VALIDATE would begin,
`--until milestone` after a milestone closes. The pr phase is not driven (#10): the loop stops with
`gate-blocked` and names the terminal steps. A quota stops the loop with `provider-quota`; the
sleep-and-resume is #7.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from . import __version__, files, journal, prep, prompts, steps, validate
from .adapters import NAMES, make_adapter
from .steps import EXIT_CODES, Context, stop   # re-exported: cli reads EXIT_CODES from here


class LockHeld(Exception):
    pass


class DriverLock:
    """One driver per mission directory (fcntl). The file is never unlinked: flock is the lock."""

    def __init__(self, mission_dir: Path):
        self.path = mission_dir / ".driver.lock"
        self.fh = None

    def __enter__(self) -> "DriverLock":
        self.fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.seek(0)
            holder = self.fh.read().strip()
            self.fh.close()
            raise LockHeld(holder or "unknown holder")
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write("pid=%d host=%s at=%s\n" % (os.getpid(), socket.gethostname(), journal.now_iso()))
        self.fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        except OSError:
            pass


def _log(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------- preflight

def preflight(mission_dir: Path, plugin: Path, harness: Optional[str] = None,
              restore: bool = False) -> Tuple[List[str], List[str], Optional[Dict]]:
    """(problems, warnings, config). Any problem refuses the run. `restore` brings back what a
    crashed driver left under `.blind/`; only `run` passes it, from inside the driver lock --
    to `missions preflight`, a dry run or a second driver, a cell under `.blind/` is a reviewer
    window that a live driver holds open, and taking it apart would show that reviewer the
    handoffs. Those callers get a warning instead."""
    problems: List[str] = []
    warnings: List[str] = []
    if not mission_dir.is_dir() or not (mission_dir / "state.md").exists():
        return ["%s is not a mission directory (no state.md)" % mission_dir], warnings, None
    for name in files.REQUIRED_FILES:
        if not (mission_dir / name).exists():
            if name == "design.md":
                problems.append("design.md is missing -- a mission does not implement without a design; run /missions:mission-design")
            else:
                problems.append("%s is missing" % name)
    st = files.read_state(mission_dir)
    if not st.has_block:
        problems.append("state.md has no ```mission-state block (legacy header); the driver needs the v2 block")
    # a driver that died inside a reviewer's blind window left the handoffs hidden under .blind/
    blind = mission_dir / ".blind"
    cells = sorted(c.name for c in blind.iterdir() if c.is_dir()) if blind.is_dir() else []
    if restore:
        restored = prep.restore_blind(mission_dir)
        if restored:
            journal.append(mission_dir, "note", text="restored .blind/%s left by a crashed driver" % ", .blind/".join(restored))
            warnings.append("restored .blind/%s (files hidden for a reviewer run by a driver that did not return)" % ", .blind/".join(restored))
    elif cells:
        warnings.append("found .blind/%s: a reviewer run is in progress, or a driver crashed inside one; "
                        "`missions run` restores it once it holds the driver lock" % ", .blind/".join(cells))
    cfg: Optional[Dict] = None
    try:
        cfg = files.read_config(mission_dir)
    except files.MissionFileError as e:
        problems.append(str(e))
    if cfg is not None and not cfg.get("host_lease", True):
        warnings.append("host_lease is false in driver.json: executor runs take no host lease, so another mission "
                        "on this machine may run its tests at the same time")

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    if (mission_dir / "contract.md").exists() and (mission_dir / "features.md").exists():
        res = subprocess.run(["bash", str(plugin / "scripts" / "check.sh"), str(mission_dir)],
                             capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            tail = [ln for ln in (res.stdout + res.stderr).splitlines() if ln.strip()]
            problems.append("check.sh failed: %s" % (tail[-1].strip() if tail else "rc %d" % res.returncode))
    if st.has_block:
        try:
            prompts.digest(mission_dir, plugin)
        except prompts.DigestError as e:
            problems.append("mission-state.sh: %s" % str(e).splitlines()[0])

    if cfg is not None:
        checkout = files.checkout_of(mission_dir, cfg)
        want = cfg.get("branch") or st.branch
        if not want:
            # the worker hooks compare against MISSIONS_BRANCH and the grade against the branch's
            # ref: with neither named, every commit is refused and nothing can be measured
            problems.append("no mission branch: state.md has no **Branch:** line and driver.json has no branch -- set one; "
                            "the worker hooks refuse commits off the mission branch")
        top = files.git_out(checkout, "rev-parse", "--show-toplevel")
        if not top:
            problems.append("%s is not a git checkout" % checkout)
        else:
            branch = files.git_out(checkout, "branch", "--show-current")
            if not branch:
                problems.append("HEAD is detached in %s -- check out the mission branch first" % checkout)
            if want and branch and branch != want:
                problems.append("checked out branch is %s; the mission branch is %s" % (branch, want))
            dirty = files.dirty_paths(checkout)
            if dirty:
                problems.append("working tree is dirty outside .missions/ (%s%s) -- a worker died mid-feature or "
                                "someone is editing; commit, stash or discard first" % (
                                    ", ".join(dirty[:3]), "..." if len(dirty) > 3 else ""))
            if files.git(checkout, "ls-files", "--error-unmatch", ".missions", check=False).returncode == 0:
                warnings.append(".missions/ is tracked by git; the driver rewrites it constantly, so every "
                                "worker commit will see it dirty -- consider ignoring it")
        h = harness or cfg.get("harness")
        if h not in NAMES:
            problems.append("harness %r is not one of %s" % (h, ", ".join(NAMES)))
        else:
            section = (cfg.get("adapters") or {}).get(h) or {}
            if h == "stub":
                sd = Path(section.get("script_dir", "stub"))
                if not sd.is_dir():
                    problems.append("stub script dir %s does not exist" % sd)
            else:
                binary = section.get("bin", h)
                if shutil.which(binary) is None:
                    problems.append("%s binary %r is not on PATH" % (h, binary))
                else:
                    # the adapter's own "can I actually work on this host" question, asked before
                    # anything is spent: a harness that cannot run a command is a paid no-op
                    try:
                        ask = getattr(make_adapter(h, cfg), "preflight_problems", None)
                        if ask is not None:
                            problems.extend(ask())
                    except Exception as e:
                        # constructing the adapter is itself a config check now; reporting every
                        # problem is preflight's job, so a bad `adapters` section is one of them
                        problems.append("adapters.%s is not usable: %s: %s" % (h, type(e).__name__, e))
    return problems, warnings, cfg


# ---------------------------------------------------------------- run

def run(mission_dir: Path, args) -> int:
    plugin = files.plugin_root()
    mission_dir = mission_dir.resolve()
    if getattr(args, "dry_run", False) or not mission_dir.is_dir():
        # no lock: a dry run touches nothing, `.blind/` included, and a path that is no directory
        # has nothing to lock -- preflight names the problem
        r = _start(mission_dir, plugin, args, restore=False)
        return r if isinstance(r, int) else dry_run(r, args)
    try:
        with DriverLock(mission_dir):
            # the lock first, preflight inside it: `.blind/` is restored by the one driver that
            # holds the mission, so a reviewer window of a live driver is never taken apart by a
            # second one that mistook it for a crash
            r = _start(mission_dir, plugin, args, restore=True)
            return r if isinstance(r, int) else _run_locked(r, args)
    except LockHeld as e:
        _log("preflight: another driver holds %s (%s)" % (mission_dir / ".driver.lock", e))
        return EXIT_CODES["preflight-failed"]


def _start(mission_dir: Path, plugin: Path, args, restore: bool) -> Union[int, Context]:
    """Preflight, then the Context a run needs -- or the preflight-failed exit code, journaled
    when there is a mission to journal into."""
    log = _log
    harness_arg = getattr(args, "harness", None)
    problems, warnings, cfg = preflight(mission_dir, plugin, harness_arg, restore=restore)
    for w in warnings:
        log("warning: " + w)
    if problems or cfg is None:
        for p in problems:
            log("preflight: " + p)
        if (mission_dir / "state.md").exists():
            journal.append(mission_dir, "stop", reason="preflight-failed", detail="; ".join(problems)[:500],
                           exit=EXIT_CODES["preflight-failed"])
        log("-- stopped: preflight-failed (%d problem(s)); fix the mission files, then re-run" % len(problems))
        return EXIT_CODES["preflight-failed"]
    harness = harness_arg or cfg["harness"]
    return Context(mission_dir=mission_dir, checkout=files.checkout_of(mission_dir, cfg), plugin=plugin,
                   cfg=cfg, adapter=make_adapter(harness, cfg), run_id=uuid.uuid4().hex[:8],
                   harness=harness, log=log)


def _run_locked(ctx: Context, args) -> int:
    mdir = ctx.mission_dir
    st = files.read_state(mdir)
    if st.phase == "planning":
        files.write_state_fields(mdir, phase="implementing")
        journal.append(mdir, "decision", what="phase planning -> implementing",
                       why="missions run started: design.md present, check.sh passing")
        ctx.log("phase: planning -> implementing")
    until = getattr(args, "until", None)
    journal.append(mdir, "driver_start", run_id=ctx.run_id, pid=os.getpid(), harness=ctx.harness,
                   limit=getattr(args, "limit", None), milestone=getattr(args, "milestone", None), until=until,
                   version=__version__)
    ctx.log("missions %s  run %s  mission %s  harness %s  checkout %s" % (
        __version__, ctx.run_id, ctx.slug, ctx.harness, ctx.checkout))
    budget = files.read_budget(mdir)
    repair_rounds = files.repair_rounds(mdir)
    limit = getattr(args, "limit", None)
    attempts = 0
    crash_streak = 0
    noop_streak = 0
    try:
        while True:
            st = files.read_state(mdir)
            feats = files.read_features(mdir)
            if st.phase == "done":
                return stop(ctx, "done", detail="phase is done")
            if st.phase == "halted":
                return stop(ctx, "gate-blocked", detail="the mission is halted; last: %s" % st.resume_next[:160],
                            needs="a human decision, then set `phase: implementing` in state.md")
            if limit and attempts >= limit:
                return stop(ctx, "limit-reached", detail="--limit %d worker run(s) reached" % limit,
                            needs="re-run missions run")
            r = steps.check_caps(ctx, budget)
            if r is not None:
                return r
            milestone = getattr(args, "milestone", None) or st.milestone
            if st.phase == "pr":
                # ahead of the closed-milestone check: a `done` stop puts the phase back to
                # validating, and an operator who moved it to pr for the terminal steps keeps it
                return stop(ctx, "gate-blocked", detail="phase pr is not driven by this version",
                            needs="terminal steps via /missions:mission-run (driver pr phase: #10)")
            if validate.closed(mdir, milestone):
                # a closed milestone is never re-entered: a finished mission re-run is a no-op,
                # --milestone stops once its milestone closes, and a state.md that names a closed
                # one is a human's edit to fix, not a round to run again
                nxt = files.next_milestone(mdir, milestone)
                if nxt is None:
                    return validate.done_stop(ctx)
                if getattr(args, "milestone", None) == milestone:
                    return stop(ctx, "limit-reached", detail="milestone %s is closed; --milestone %s" % (milestone, milestone),
                                needs="re-run missions run without --milestone (state.md is at %s)" % st.milestone)
                return stop(ctx, "gate-blocked", detail="state.md names %s, which closed" % milestone,
                            needs="set `milestone:` in state.md to %s" % nxt)
            if st.phase in ("validating", "negotiating"):
                # the one way into VALIDATE, behind the caps: a milestone that just completed
                # (below) and a round left open by a stop (an error, an interrupt) both come here
                if until == "validate":
                    return stop(ctx, "limit-reached", detail="phase %s: VALIDATE %s is next; --until validate" % (st.phase, milestone),
                                needs="re-run missions run without --until validate")
                r = validate.run_validate(ctx, milestone, until=until)
                if isinstance(r, int):
                    return r
                continue
            if st.phase != "implementing":
                return stop(ctx, "gate-blocked", detail="phase %r is not one the driver drives" % st.phase,
                            needs="set `phase:` in state.md to implementing, validating or done")
            if st.open_issues:
                # a handoff's issues go through triage before anything new starts: resolved,
                # deferred or repaired by the driver on a judgment's proposal, or escalated to a halt
                r = steps.step_triage(ctx, st)
                if r is not None:
                    return r
                continue
            mfeats = [f for f in feats if f.milestone == milestone]
            if not mfeats:
                return stop(ctx, "gate-blocked", detail="features.md has no features under ## %s" % milestone,
                            needs="set `milestone:` in state.md, or pass --milestone")
            blocked = [f.id for f in mfeats if f.status == "blocked"]
            if blocked:
                return stop(ctx, "gate-blocked", detail="%s blocked; nothing later in %s starts" % (", ".join(blocked), milestone),
                            needs="re-plan it (/missions:mission-amend) or set its Status back to pending")
            active = [f.id for f in mfeats if f.status == "active"]
            if active:
                return stop(ctx, "gate-blocked",
                            detail="%s is marked active: another driver holds it, or one was killed mid-worker" % ", ".join(active),
                            needs="reconcile it against its handoff and git, then set Status pending or done "
                                  "(missions resume is D4); the queue never steps over it")
            done_ids = {f.id for f in feats if f.status == "done"}
            if all(f.status == "done" for f in mfeats):
                files.write_state_fields(mdir, phase="validating")
                journal.append(mdir, "decision", what="phase implementing -> validating",
                               why="milestone %s complete: all %d feature(s) done" % (milestone, len(mfeats)))
                ctx.log("milestone %s complete: all %d feature(s) done -> validating" % (milestone, len(mfeats)))
                if until == "validate":
                    return stop(ctx, "limit-reached", detail="milestone %s complete; --until validate" % milestone,
                                needs="re-run missions run to VALIDATE %s" % milestone,
                                resume_next="validate %s (all %d features done); --until validate stopped the run" % (
                                    milestone, len(mfeats)))
                continue          # the top of the loop drives the round, behind the caps
            pending = [f for f in mfeats if f.status == "pending"]
            ready = [f for f in pending if all(d in done_ids for d in f.depends)]
            if not ready:
                waiting = pending[0] if pending else mfeats[0]
                missing = [d for d in waiting.depends if d not in done_ids]
                return stop(ctx, "gate-blocked", detail="%s waits on %s" % (waiting.id, ", ".join(missing) or "an unresolvable state"),
                            needs="finish or re-plan the dependency")
            feat = ready[0]
            attempts += 1
            cls, outcome, grade = steps.step_worker(ctx, feat, st)

            mission_branch = ctx.cfg.get("branch") or st.branch
            if mission_branch and grade.branch_after != mission_branch:
                # another branch, or none at all: a detached HEAD is not "no change"
                files.set_feature(mdir, feat.id, status="pending")
                where = ("on branch " + grade.branch_after) if grade.branch_after else "detached from any branch"
                return stop(ctx, "gate-blocked",
                            detail="%s left the checkout %s, not on the mission branch %s" % (outcome.task, where, mission_branch),
                            needs="check out the mission branch, reconcile the worker's commits, then missions run again")
            if grade.rewritten:
                # the branch no longer carries the commit it was launched from: a rebase, a reset,
                # an amend. Every earlier feature's range and patch point at history that is gone,
                # so nothing after this can be graded against what was there -- a human reconciles
                files.set_feature(mdir, feat.id, status="pending")
                return stop(ctx, "gate-blocked", halt=True,
                            detail="%s rewrote the mission branch: HEAD at launch %s is no longer on %s (a rebase or a reset) "
                                   "-- reconcile the branch, then missions run again" % (
                                       outcome.task, grade.head_before[:7], mission_branch or grade.branch_after or "the branch"),
                            needs="git reflog shows the launch head; put the branch back on it or accept the rewrite and "
                                  "fix features.md's ranges, then set `phase: implementing` and missions run again")
            if cls == "done":
                steps.ingest(ctx, feat, grade)
                crash_streak = noop_streak = 0
                continue
            if cls == "tests_failed" and grade.status == "blocked":
                # design §5: worker says blocked -> blocked. Its own reason goes on the decision card;
                # a re-dispatch would only buy the same answer again.
                files.set_feature(mdir, feat.id, status="blocked")
                return stop(ctx, "gate-blocked", halt=True,
                            detail="%s reports %s blocked: %s" % (
                                outcome.task, feat.id, "; ".join(grade.undone[:3]) or "no reason given under Left undone"),
                            needs="decide: fix the brief or the contract (/missions:mission-amend), or set %s back to pending" % feat.id)
            if cls in ("malformed_handoff", "tests_failed"):
                n = journal.attempts(mdir, feat.id)
                if n > repair_rounds:
                    files.set_feature(mdir, feat.id, status="blocked")
                    return stop(ctx, "gate-blocked", halt=True,
                                detail="%s rejected %d times; last: %s" % (feat.id, n, "; ".join(grade.problems[:3]) or cls),
                                needs="decide: fix the brief or the contract (/missions:mission-amend), or set %s back to pending" % feat.id)
                files.set_feature(mdir, feat.id, status="pending")
                ctx.log("   re-dispatching %s with the rejection (%d of %d)" % (feat.id, n, repair_rounds + 1))
                continue
            if cls == "infra_quota":
                files.set_feature(mdir, feat.id, status="pending")
                return stop(ctx, "provider-quota",
                            detail="%s: the harness reported a quota or rate limit: %s" % (outcome.task, grade.quota),
                            needs="wait for the reset, then missions run again (the driver's own sleep-and-resume is #7)")
            if cls in ("infra_crash", "stalled"):
                crash_streak += 1
                files.set_feature(mdir, feat.id, status="pending")
                if crash_streak >= 2:
                    return stop(ctx, "error",
                                detail="%s: %d consecutive runs ended without a handoff or a commit (%s)" % (
                                    feat.id, crash_streak, outcome.detail or ("rc %d" % outcome.rc)),
                                needs="look at %s" % outcome.stderr_path)
                continue
            # no_op
            noop_streak += 1
            if noop_streak >= 2:
                files.set_feature(mdir, feat.id, status="blocked")
                return stop(ctx, "gate-blocked", halt=True,
                            detail="%s: two runs produced neither a commit nor a handoff" % feat.id,
                            needs="the brief is not landing: read runs/%s/output.md, fix the feature or the prompt, set Status pending" % outcome.task)
            files.set_feature(mdir, feat.id, status="pending")
            continue
    except KeyboardInterrupt:
        return stop(ctx, "interrupted", detail="interrupted",
                    needs="check the active feature's tree and handoff, then missions run again (missions resume is D4)")
    except prompts.DigestError as e:
        return stop(ctx, "preflight-failed", detail="mission-state.sh: %s" % str(e).splitlines()[0],
                    needs="fix the mission files (the state.md digest must fit 2 KB)")
    except files.MissionFileError as e:
        return stop(ctx, "error", detail=str(e), needs="look at the mission files")


# ---------------------------------------------------------------- dry run

def dry_run(ctx: Context, args) -> int:
    """Walk the queue without touching anything: no journal line, no state change, no lock."""
    mdir = ctx.mission_dir
    st = files.read_state(mdir)
    feats = files.read_features(mdir)
    milestone = getattr(args, "milestone", None) or st.milestone
    queue = [f for f in feats if f.milestone == milestone and f.status == "pending"]
    limit = getattr(args, "limit", None)
    if limit:
        queue = queue[:limit]
    meta, system = prompts.system_prompt(ctx.plugin)
    # the system prompt goes to a temp file so the printed argv is the real one; runs/ stays untouched
    tmp_system = Path(tempfile.mkstemp(prefix="missions-system-", suffix=".md")[1])
    files.write_text(tmp_system, system)
    ctx.log("dry run: mission %s  phase %s  milestone %s  harness %s  checkout %s" % (
        ctx.slug, st.phase, milestone, ctx.harness, ctx.checkout))
    if st.open_issues:
        ctx.log("note: %d open issue(s) would block the first dispatch" % len(st.open_issues))
    for i, f in enumerate(queue, 1):
        task = "%s#%d" % (f.id, journal.attempts(mdir, f.id) + 1)
        req = steps.build_request(ctx, f, task, mdir / "runs" / task, meta, st.phase)
        req.system_path = tmp_system
        cmd = ctx.adapter.command(req)
        shown = " ".join((c if len(c) < 60 else c[:57] + "...") for c in cmd)
        ctx.log("[%d/%d] %s -- %s\n      %s" % (i, len(queue), task, f.title, shown))
    tmp_system.unlink()
    ctx.log("dry run: %d feature(s) would run, nothing executed" % len(queue))
    return 0
