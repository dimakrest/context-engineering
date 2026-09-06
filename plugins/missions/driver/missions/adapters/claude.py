"""claude -p as a worker. Flags verified on Claude Code 2.1.263.

    claude -p --output-format json --permission-mode acceptEdits --allowedTools <tools>
           [--max-budget-usd B] [--model M] [--effort E] --append-system-prompt "<system.md>"
           < prompt.md

The prompt goes in on stdin (non-interactive `-p` skips the trust dialog). The result is one JSON
object on stdout: `total_cost_usd` is the cost, `modelUsage` names what ran, `is_error`/`subtype`/
`result` say how it ended -- and the `result` text is the CLI's own words on why, which beat any
mapping (a 529 arrives as subtype "success" with is_error true).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..outcome import Outcome, RunRequest, unknown_cost
from . import base

READ_ONLY_DISALLOWED = "Write,Edit,NotebookEdit,MultiEdit"


class ClaudeAdapter:
    name = "claude"

    def __init__(self, cfg: Dict):
        self.bin = cfg.get("bin", "claude")
        self.permission_mode = cfg.get("permission_mode", "acceptEdits")
        self.extra_args: List[str] = list(cfg.get("extra_args") or [])

    def capabilities(self) -> Dict:
        return {"cost_unit": "usd", "budget": True, "model": True, "read_only": True}

    def command(self, req: RunRequest) -> List[str]:
        cmd = [self.bin, "-p", "--output-format", "json", "--permission-mode", self.permission_mode]
        if req.tools:
            cmd += ["--allowedTools", ",".join(req.tools)]
        if req.read_only:
            cmd += ["--disallowedTools", READ_ONLY_DISALLOWED]
        if req.budget_usd is not None:
            cmd += ["--max-budget-usd", ("%g" % req.budget_usd)]
        if req.model:
            cmd += ["--model", req.model]
        if req.effort:
            cmd += ["--effort", req.effort]
        if req.system_path is not None and req.system_path.exists():
            cmd += ["--append-system-prompt", req.system_path.read_text(encoding="utf-8")]
        cmd += self.extra_args
        return cmd

    def run(self, req: RunRequest) -> Outcome:
        res = base.run_process(self.command(req), req, stdin_path=req.prompt_path)
        stdout = base.read_output(req.run_dir / "stdout")
        envelope = parse_envelope(stdout)
        cost: Dict[str, Any] = unknown_cost("claude:no-json-envelope")
        model: Optional[str] = None
        detail = ""
        session_id: Optional[str] = None
        if envelope is not None:
            usd = envelope.get("total_cost_usd")
            if isinstance(usd, (int, float)):
                cost = {"unit": "usd", "value": float(usd), "source": "claude:total_cost_usd"}
            usage = envelope.get("modelUsage")
            if isinstance(usage, dict) and usage:
                model = next(iter(usage.keys()))
            session_id = envelope.get("session_id") if isinstance(envelope.get("session_id"), str) else None
            result = envelope.get("result")
            if isinstance(result, str):
                req.output_path.write_text(result, encoding="utf-8")
            subtype = str(envelope.get("subtype") or "")
            if envelope.get("is_error") or subtype.startswith("error"):
                text = (result or "").strip().replace("\n", " ") if isinstance(result, str) else ""
                detail = ("%s: %s" % (subtype or "error", text[:200])).strip(": ")
        elif stdout.strip():
            detail = "stdout was not a JSON envelope: %s" % stdout.strip().replace("\n", " ")[:160]
        return Outcome(task=req.task, rc=res.rc, elapsed_s=res.elapsed_s, timed_out=res.timed_out,
                       killed_by=res.killed_by, cost=cost, harness=self.name, model=model,
                       stdout_path=req.run_dir / "stdout", stderr_path=req.run_dir / "stderr",
                       detail=detail, session_id=session_id, orphans_killed=res.orphans_killed)


def parse_envelope(stdout: str) -> Optional[Dict[str, Any]]:
    """The single result object; tolerate leading noise by trying the last JSON-looking line."""
    text = stdout.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    return None
