"""The commit watchdog (#4, design §5): a thread that watches the branch, the handoff and the
run's output while the worker runs, and hands the process runner a verdict when the run must end.

Two shapes end a run early:

- `watchdog:commit_no_handoff` -- a commit landed on the mission branch and then nothing happened
  for `commit_no_handoff_s` (no handoff, no further commit, no tree or output change). This is the S3
  shape (F003, F012, F014): the work is on the branch, the record is not, and the worker is idle.
  The first commit is journaled as `commit_observed` the moment it is seen, task-keyed.
- `watchdog:silence` -- nothing at all changed for `silence_s`. Off by default: `claude -p
  --output-format json` prints only at the end, so for that harness a quiet run is not evidence.

The watchdog never signals a process. It observes and sets `verdict`; `run_process` polls it and
owns the kill (SIGTERM, grace, SIGKILL), so process control stays in one place. Its window is
exactly the process lifetime: the runner starts it after Popen and stops it after the reap, and
the loop re-checks the stop flag after every observation, so a `commit_observed` is never written
after the run's grade even when one slow `git status` outlives `stop()`'s join.
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from . import files, journal

COMMIT_NO_HANDOFF = "watchdog:commit_no_handoff"
SILENCE = "watchdog:silence"
DEFAULTS: Dict[str, Optional[float]] = {"poll_s": 30, "commit_no_handoff_s": 300, "silence_s": None}


def config(cfg: Dict) -> Dict[str, Optional[float]]:
    """driver.json's `watchdog` section over the defaults; unknown keys ignored, null keeps a
    rule off."""
    out: Dict[str, Optional[float]] = dict(DEFAULTS)
    for k, v in (cfg.get("watchdog") or {}).items():
        if k in DEFAULTS:
            out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if not out["poll_s"] or out["poll_s"] <= 0:
        out["poll_s"] = DEFAULTS["poll_s"]
    return out


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


class Watchdog:
    def __init__(self, mission_dir: Path, checkout: Path, feature: str, task: str, head_before: str,
                 handoff_before: Optional[str], run_dir: Path, poll_s: float = 30.0,
                 commit_no_handoff_s: Optional[float] = 300.0, silence_s: Optional[float] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.mission_dir = mission_dir
        self.checkout = checkout
        self.feature = feature
        self.task = task
        self.head_before = head_before
        self.handoff_before = handoff_before
        self.handoff_path = files.handoff_path(mission_dir, feature)
        self.run_dir = run_dir
        self.poll_s = max(0.05, float(poll_s))
        self.commit_no_handoff_s = commit_no_handoff_s
        self.silence_s = silence_s
        self.log = log or (lambda s: None)
        self.verdict: Optional[str] = None
        self.commit: Optional[str] = None            # first new HEAD seen during the run
        self.commit_seen_at: Optional[float] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="missions-watchdog-" + task, daemon=True)

    # -- lifecycle (called by the process runner)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=15.0)
        if self._thread.is_alive():
            # a slow git on a big tree; the loop checks the flag after every observation, so
            # it writes nothing more, but the grade will not wait for it
            self.log("   watchdog: still observing after stop (git took longer than 15 s)")

    # -- observation

    def observe(self) -> Dict[str, object]:
        """One snapshot of everything a live worker changes: HEAD, the handoff, the run's output
        files, and the working tree (status plus the content of tracked changes -- a worker editing
        the same file over and over is alive, and the status line alone would not show it).
        `--no-optional-locks`: an observer must never take the index lock from under the worker's
        own `git add`."""
        status = files.git(self.checkout, "--no-optional-locks", "status", "--porcelain",
                           "--untracked-files=normal", check=False)
        diff = files.git(self.checkout, "--no-optional-locks", "diff", "HEAD", check=False)
        tree = hashlib.sha1()
        tree.update((status.stdout or "").encode("utf-8", "replace"))
        tree.update((diff.stdout or "").encode("utf-8", "replace"))
        return {
            "head": files.git_out(self.checkout, "rev-parse", "HEAD"),
            "handoff": files.fingerprint(self.handoff_path),
            "stdout": _size(self.run_dir / "stdout"),
            "stderr": _size(self.run_dir / "stderr"),
            "tree": tree.hexdigest(),
        }

    def handoff_written(self, obs: Dict[str, object]) -> bool:
        return obs["handoff"] is not None and obs["handoff"] != self.handoff_before

    def _loop(self) -> None:
        last: Optional[Dict[str, object]] = None
        last_change = time.monotonic()
        while not self._stop.is_set():
            try:
                obs = self.observe()
            except Exception as e:  # a git hiccup must not kill the watchdog
                self.log("   watchdog: observe failed: %s" % e)
                self._stop.wait(self.poll_s)
                continue
            if self._stop.is_set():
                return  # the run is over and graded: nothing observed now may be journaled after its grade
            now = time.monotonic()
            if last is not None and obs != last:
                last_change = now
            head = obs["head"]
            if self.commit is None and head and self.head_before and head != self.head_before:
                self.commit = str(head)
                self.commit_seen_at = now
                if not self.handoff_written(obs):
                    journal.append(self.mission_dir, "commit_observed", task=self.task, feature=self.feature,
                                   commit=self.commit[:7], handoff="pending")
                    self.log("   watchdog: commit %s observed, handoff pending" % self.commit[:7])
            if (self.commit is not None and self.commit_no_handoff_s is not None and not self.handoff_written(obs)
                    and now - max(self.commit_seen_at or now, last_change) >= self.commit_no_handoff_s):
                self.verdict = COMMIT_NO_HANDOFF
                self.log("   watchdog: no handoff %ds after commit %s and no activity since -- ending the run" % (
                    int(self.commit_no_handoff_s), self.commit[:7]))
                return
            if self.silence_s is not None and last is not None and now - last_change >= self.silence_s:
                self.verdict = SILENCE
                self.log("   watchdog: no output, commit, handoff or tree change for %ds -- ending the run" % int(self.silence_s))
                return
            last = obs
            self._stop.wait(self.poll_s)
