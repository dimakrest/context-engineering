"""codex exec as a worker. Flags verified on codex-cli 0.153.4.

    codex exec -C <cwd> --sandbox workspace-write --json -o output.md [-m M] --skip-git-repo-check -
           < system + user prompt

Codex has no system-prompt flag, so the system part is prepended to the user prompt. `-` reads
the prompt from stdin; `-o` writes the agent's last message. Non-interactive exec fails commands
outside the sandbox instead of prompting, so the sandbox mode is the approval policy. `--json`
prints JSONL events: `turn.completed` carries token usage (no dollars -- unit is tokens, never
zero), `turn.failed`/`error` carry the failure text, `thread.started` the session id.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..outcome import Outcome, RunRequest, unknown_cost
from . import base


class CodexAdapter:
    name = "codex"

    def __init__(self, cfg: Dict):
        self.bin = cfg.get("bin", "codex")
        self.sandbox = cfg.get("sandbox", "workspace-write")
        self.extra_args: List[str] = list(cfg.get("extra_args") or [])

    def capabilities(self) -> Dict:
        return {"cost_unit": "tokens", "budget": False, "model": True, "read_only": True}

    def command(self, req: RunRequest) -> List[str]:
        cmd = [self.bin, "exec", "-C", str(req.cwd),
               "--sandbox", "read-only" if req.read_only else self.sandbox,
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
