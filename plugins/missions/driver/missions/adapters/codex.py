"""codex exec as a worker. Flags verified on codex-cli 0.153.4.

    codex exec -C <cwd> --sandbox workspace-write --json -o output.md [-m M] --skip-git-repo-check -
           < system + user prompt

Codex has no system-prompt flag, so the system part is prepended to the user prompt. `-` reads
the prompt from stdin; `-o` writes the agent's last message. Non-interactive exec fails commands
outside the sandbox instead of prompting, so the sandbox mode is the approval policy. `--json`
prints JSONL events: `turn.completed` carries token usage (no dollars -- unit is tokens, never
zero), `turn.failed`/`error` carry the failure text, `thread.started` the session id. Nothing in
the stream names a model, so a codex run journals `model: null` -- verified against 0.153.4's own
output, not an unparsed field.

On Linux codex sandboxes every model-run command with bubblewrap, which needs an unprivileged
user namespace it can write a uid map in. Plenty of hosts do not allow that -- an unprivileged
container, a hardened kernel -- and the failure is quiet and expensive: bwrap exits before the
shell for EVERY command, so the worker reads no file and runs no test, then reports the blockage
in prose and exits 0. The driver grades that `no_op`, truthfully, and tells the operator the
brief is not landing. `preflight_problems` catches it before anything is spent.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from typing import Any, Dict, List, Optional

from ..outcome import Outcome, RunRequest, unknown_cost
from . import base

# codex's own words for its sandbox policies; the first two are the ones bubblewrap implements
SANDBOX_NEEDS_USERNS = ("read-only", "workspace-write")
SANDBOX_POLICIES = SANDBOX_NEEDS_USERNS + ("danger-full-access",)
CLONE_NEWUSER = 0x10000000


def user_namespaces_usable() -> Optional[bool]:
    """Can this host make the kind of user namespace bubblewrap needs -- one whose uid map it may
    write? True/False, or None when the question could not be asked (not Linux, no libc to call).
    None is never reported as a problem: an unknown is not a failure.

    Probed rather than read off `/proc/sys/kernel/unprivileged_userns_clone`, because that knob
    says 1 on hosts where the map write is still refused. Done in a fork, since a process that
    has entered a user namespace cannot leave it."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None
    try:
        pid = os.fork()
    except OSError:
        return None
    if pid == 0:                                     # pragma: no cover - the child never returns
        # BaseException, and os._exit rather than sys.exit: the child shares the parent's open
        # file descriptions, so ANY exception that escaped here would unwind the parent's stack
        # inside the child -- including the `with DriverLock(...)` the loop holds, whose __exit__
        # calls flock(LOCK_UN) and would free the lock the parent still believes it holds.
        try:
            if libc.unshare(CLONE_NEWUSER) != 0:
                os._exit(1)
            with open("/proc/self/uid_map", "w") as fh:
                fh.write("0 %d 1\n" % os.getuid())
        except BaseException:
            os._exit(1)
        os._exit(0)
    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        return None
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


class CodexAdapter:
    name = "codex"

    def __init__(self, cfg: Dict):
        self.bin = cfg.get("bin", "codex")
        self.sandbox = cfg.get("sandbox", "workspace-write")
        self.extra_args: List[str] = list(cfg.get("extra_args") or [])

    def capabilities(self) -> Dict:
        return {"cost_unit": "tokens", "budget": False, "model": True, "read_only": True}

    def preflight_problems(self) -> List[str]:
        """Refuse a run that cannot do any work. A sandbox codex cannot start is not a degraded
        run, it is a guaranteed `no_op` with the tokens spent, so this is a problem and not a
        warning -- the whole point of asking is that it costs nothing and the run does not."""
        if self.sandbox not in SANDBOX_POLICIES:
            # a typo or a null sails past here otherwise and fails per-run, mid-mission, holding
            # the host lease -- `--sandbox None` raises inside the adapter
            return ["adapters.codex.sandbox is %r, not one of %s" % (
                self.sandbox, ", ".join(SANDBOX_POLICIES))]
        # `read-only` is always reachable -- steps.py gives it to the reviewer and judgment roles
        # whatever `sandbox` says -- so a configured policy that needs no namespace only helps if
        # it also covers those roles, which `command` now makes true.
        if self.sandbox not in SANDBOX_NEEDS_USERNS or user_namespaces_usable() is not False:
            return []
        return ["codex sandbox %r needs an unprivileged user namespace, and this host refuses one "
                "(bubblewrap will fail before the shell for every command, so the worker would "
                "read nothing, run nothing and cost tokens to say so). Either run where user "
                "namespaces are allowed, or -- only when the host is ALREADY a sandbox, such as a "
                "container or VM you accept the worker having the run of -- set "
                "adapters.codex.sandbox to \"danger-full-access\" in driver.json, which leaves the "
                "driver's own env whitelist, git hooks, blindness and post-exit grade as the "
                "enforcement" % self.sandbox]

    def command(self, req: RunRequest) -> List[str]:
        # a read-only role asks codex for its `read-only` policy -- but not when the operator has
        # turned codex's sandbox off altogether: `read-only` is bubblewrap too, so forcing it there
        # would bwrap-kill every reviewer and judgment run on the very host the operator escaped
        # for. Read-onlyness is then the driver's to enforce (blindness, hooks that refuse a
        # commit for any non-worker role, the post-exit grade), which is what was opted into.
        policy = self.sandbox if self.sandbox not in SANDBOX_NEEDS_USERNS else (
            "read-only" if req.read_only else self.sandbox)
        cmd = [self.bin, "exec", "-C", str(req.cwd),
               "--sandbox", policy,
               "--json", "-o", str(req.output_path), "--skip-git-repo-check"]
        if req.model:
            cmd += ["-m", req.model]
        cmd += self.extra_args
        cmd.append("-")
        return cmd

    def run(self, req: RunRequest) -> Outcome:
        full = req.run_dir / "codex-prompt.md"
        system = req.system_path.read_text(encoding="utf-8") if req.system_path and req.system_path.exists() else ""
        user = req.prompt_path.read_text(encoding="utf-8")
        full.write_text((system.rstrip() + "\n\n---\n\n" if system else "") + user, encoding="utf-8")
        res = base.run_process(self.command(req), req, stdin_path=full)
        stdout = base.read_output(req.run_dir / "stdout")
        parsed = parse_events(stdout)
        cost: Dict[str, Any] = unknown_cost("codex:no-turn.completed")
        if parsed["usage"]:
            u = parsed["usage"]
            value = float(_num(u.get("input_tokens")) + _num(u.get("output_tokens")))
            cost = {"unit": "tokens", "value": value, "source": "codex:turn.completed", "usage": u}
        if not req.output_path.exists() and parsed["last_message"]:
            req.output_path.write_text(parsed["last_message"], encoding="utf-8")
        return Outcome(task=req.task, rc=res.rc, elapsed_s=res.elapsed_s, timed_out=res.timed_out,
                       killed_by=res.killed_by, cost=cost, harness=self.name, model=parsed["model"],
                       stdout_path=req.run_dir / "stdout", stderr_path=req.run_dir / "stderr",
                       detail=parsed["error"], session_id=parsed["thread_id"],
                       orphans_killed=res.orphans_killed)


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def parse_events(stdout: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"usage": None, "error": "", "thread_id": None, "last_message": "", "model": None}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "thread.started":
            out["thread_id"] = ev.get("thread_id")
            if isinstance(ev.get("model"), str):
                out["model"] = ev["model"]
        elif t == "turn.completed" and isinstance(ev.get("usage"), dict):
            out["usage"] = ev["usage"]
        elif t in ("turn.failed", "error"):
            err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
            msg = err.get("message") if isinstance(err, dict) else None
            out["error"] = ("%s: %s" % (t, msg)) if msg else t
        elif t == "item.completed":
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                out["last_message"] = item["text"]
    return out
