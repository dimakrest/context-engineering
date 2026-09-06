"""A shell script plays the agent (tests, design §9).

    bash <script_dir>/<script>   -- the first of these that exists:
        <role>-<feature>.sh   (reviewer-F001.sh: one feature's review)
        <feature>.sh          (F001.sh; worker runs only -- a reviewer must not pick up the worker's script)
        <step>.sh             (negotiate.sh, triage.sh, reviewer.sh, scrutiny.sh, behavior.sh)
        <role>.sh             (worker.sh, judgment.sh)

with MISSIONS_ROLE MISSIONS_STEP MISSIONS_FEATURE MISSIONS_TASK MISSIONS_DIR MISSIONS_RUN_DIR
MISSIONS_PROMPT MISSIONS_STUB_SCRIPT MISSIONS_READ_ONLY MISSIONS_BIN in the environment. The script
may commit, write or omit a handoff, sleep, spawn a background child, write
`$MISSIONS_RUN_DIR/cost.json` ({"unit": "usd", "value": 1.5}), write `$MISSIONS_RUN_DIR/output.md`
as its final message (that is `req.output_path`), and exit with any rc. The driver treats it
exactly like a real harness.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from ..outcome import Outcome, RunRequest, unknown_cost
from . import base


class StubAdapter:
    name = "stub"

    def __init__(self, cfg: Dict):
        self.script_dir = Path(cfg.get("script_dir", "stub"))

    def capabilities(self) -> Dict:
        return {"cost_unit": "unknown", "budget": False, "model": False, "read_only": False}

    def script_for(self, req: RunRequest) -> Path:
        names: List[str] = []
        if req.feature:
            names.append("%s-%s.sh" % (req.role, req.feature))
            if req.role == "worker":
                names.append("%s.sh" % req.feature)
        if req.step:
            names.append("%s.sh" % req.step)
        names.append("%s.sh" % req.role)
        for name in names:
            if (self.script_dir / name).exists():
                return self.script_dir / name
        return self.script_dir / names[-1]

    def command(self, req: RunRequest) -> List[str]:
        return ["bash", str(self.script_for(req))]

    def run(self, req: RunRequest) -> Outcome:
        script = self.script_for(req)
        extra = {"MISSIONS_STUB_SCRIPT": str(script), "MISSIONS_PROMPT": str(req.prompt_path),
                 "MISSIONS_READ_ONLY": "1" if req.read_only else "0", "MISSIONS_STEP": req.step}
        res = base.run_process(self.command(req), req, extra_env=extra)
        cost = unknown_cost("stub:no-cost.json")
        cost_file = req.run_dir / "cost.json"
        if cost_file.exists():
            try:
                c = json.loads(cost_file.read_text(encoding="utf-8"))
                if isinstance(c, dict) and c.get("unit") in ("usd", "tokens"):
                    cost = {"unit": c["unit"], "value": float(c.get("value") or 0.0), "source": "stub:cost.json"}
            except (ValueError, TypeError):
                pass
        return Outcome(task=req.task, rc=res.rc, elapsed_s=res.elapsed_s, timed_out=res.timed_out,
                       killed_by=res.killed_by, cost=cost, harness=self.name, model=None,
                       stdout_path=req.run_dir / "stdout", stderr_path=req.run_dir / "stderr",
                       orphans_killed=res.orphans_killed)
