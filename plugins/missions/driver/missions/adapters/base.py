"""The adapter base: the blocking process runner (the environment is prep's; `build_env` here
is the name the callers know).

`run_process` starts exactly one process in its own session, streams stdout/stderr to files in
the run dir (a file, not a pipe: a grandchild that keeps the descriptor open cannot block the
parent's exit from being observed), honours the deadline with SIGTERM -> grace -> SIGKILL, and
never returns before the process is gone. After the parent exits it sweeps the process group, so
nothing the driver launched outlives the run. An interrupt takes the same path, and so does the
watchdog's verdict (#4): the watchdog observes, the runner kills.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import prep
from ..outcome import Outcome, RunRequest

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - python < 3.8
    Protocol = object  # type: ignore

TERM_GRACE_S = 60.0
SWEEP_GRACE_S = 2.0
POLL_S = 0.5


class Adapter(Protocol):
    name: str

    def capabilities(self) -> Dict: ...

    def command(self, req: RunRequest) -> List[str]: ...

    def run(self, req: RunRequest) -> Outcome: ...


# The run's environment is prep's -- built from a whitelist, never inherited (design §7). This is
# the name the adapters' callers know.
build_env = prep.build_env


@dataclass
class ProcResult:
    rc: int
    elapsed_s: float
    timed_out: bool
    killed_by: Optional[str]
    orphans_killed: bool


def _signal_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _group_alive(pgid: int) -> bool:
    return _signal_group(pgid, 0)


def _sweep_group(pgid: int, grace: float) -> bool:
    """After the leader was reaped: SIGTERM whatever is left in the group, SIGKILL after grace.
    True when something was still alive."""
    if not _group_alive(pgid):
        return False
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return True
        time.sleep(0.1)
    _signal_group(pgid, signal.SIGKILL)
    return True


def _end_group(proc: "subprocess.Popen", pgid: int, grace: float) -> int:
    """SIGTERM the group, wait up to `grace`, SIGKILL what is left; return the leader's rc."""
    _signal_group(pgid, signal.SIGTERM)
    try:
        return proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(pgid, signal.SIGKILL)
        return proc.wait()


def run_process(cmd: Sequence[str], req: RunRequest, stdin_path: Optional[Path] = None,
                extra_env: Optional[Dict[str, str]] = None) -> ProcResult:
    """Run the command to completion. The deadline is `req.timeout_s`; `req.watchdog`, when set,
    is started after the process and stopped after it is reaped, and its verdict ends the run the
    same way the deadline does. Polling at half-second steps is what lets one place own the kill."""
    req.run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = req.run_dir / "stdout"
    stderr_path = req.run_dir / "stderr"
    env = dict(req.env)
    if extra_env:
        env.update(extra_env)
    (req.run_dir / "command.txt").write_text("\n".join(cmd) + "\n", encoding="utf-8")
    started = time.monotonic()
    timed_out = False
    killed_by: Optional[str] = None
    watchdog = req.watchdog
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(list(cmd), cwd=str(req.cwd), env=env, stdout=out, stderr=err,
                                    stdin=stdin, start_new_session=True)
        finally:
            if stdin_path:
                stdin.close()  # type: ignore[union-attr]
        pgid = proc.pid
        if watchdog is not None:
            watchdog.start()
        try:
            deadline = started + float(req.timeout_s)
            while True:
                verdict = watchdog.verdict if watchdog is not None else None
                if verdict:
                    killed_by = verdict
                    rc = _end_group(proc, pgid, TERM_GRACE_S)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    killed_by = "timeout"
                    rc = _end_group(proc, pgid, TERM_GRACE_S)
                    break
                try:
                    rc = proc.wait(timeout=min(POLL_S, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            # interrupt or any driver failure: nothing outlives the driver
            _end_group(proc, pgid, 5.0)
            _sweep_group(pgid, 1.0)
            raise
        finally:
            if watchdog is not None:
                watchdog.stop()
    orphans = _sweep_group(pgid, SWEEP_GRACE_S)
    elapsed = time.monotonic() - started
    return ProcResult(rc=rc, elapsed_s=elapsed, timed_out=timed_out, killed_by=killed_by,
                      orphans_killed=orphans)


def read_output(path: Path, limit: int = 2_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""
