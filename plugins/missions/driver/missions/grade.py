"""Post-exit grading, D1 subset.

The handoff schema function is the existing hook script, hooks/mission-handoff-schema.sh, called
as a function with a synthetic payload and `MISSION_DIR` pinned -- #4 keeps the script as the one
schema function the driver and the worker's self-check both call. D2 (#4) grows this module into
the full verdict (tree state, task-keyed identity, claims with falsifiers).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from . import files
from .outcome import Grade


def handoff_problems(mission_dir: Path, fid: str, checkout: Path, plugin: Path) -> List[str]:
    """Run the schema hook against the feature's handoff. Empty list = valid evidence."""
    script = plugin / "hooks" / "mission-handoff-schema.sh"
    payload = json.dumps({"tool_input": {
        "subagent_type": "mission-worker",
        "prompt": "Mission: %s. Feature: %s \u2014 graded by the driver" % (mission_dir.name, fid)}})
    env = dict(os.environ)
    env.update({"CLAUDE_PROJECT_DIR": str(checkout), "CLAUDE_PLUGIN_ROOT": str(plugin),
                "MISSION_DIR": str(mission_dir)})
    res = subprocess.run(["bash", str(script)], input=payload, text=True, capture_output=True,
                         env=env, encoding="utf-8", errors="replace")
    if res.returncode == 0:
        return []
    stderr_lines = [ln.rstrip() for ln in res.stderr.splitlines()]
    if res.returncode == 2:
        problems = [ln.strip()[2:].strip() for ln in stderr_lines if ln.strip().startswith("- ")]
        if problems:
            return problems
        if not files.handoff_path(mission_dir, fid).exists():
            return ["handoff missing: %s" % files.handoff_path(mission_dir, fid)]
        first = next((ln.strip() for ln in stderr_lines if ln.strip()), "rejected by the schema function")
        return [first]
    first = next((ln.strip() for ln in stderr_lines if ln.strip()), "rc %d" % res.returncode)
    return ["grader failed: " + first]


def commit_on_branch(checkout: Path, sha: str) -> bool:
    return files.git(checkout, "merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode == 0


def new_commit_since(checkout: Path, fid: str, head_before: str) -> Optional[str]:
    """The newest commit that appeared on the branch during the run, preferring one whose subject
    carries the feature's prefix. None when the branch did not move."""
    if not head_before:
        return None
    out = files.git_out(checkout, "log", "--format=%H %s", "%s..HEAD" % head_before)
    newest = None
    for line in out.splitlines():
        sha, _, subject = line.partition(" ")
        if newest is None:
            newest = sha
        if subject.startswith(fid + ":"):
            return sha
    return newest


def grade_feature(mission_dir: Path, fid: str, checkout: Path, plugin: Path, head_before: str) -> Grade:
    h = files.read_handoff(mission_dir, fid)
    g = Grade(handoff_exists=h.exists, status=h.status, sha=h.sha, issues=list(h.issues))
    if h.exists:
        g.problems = handoff_problems(mission_dir, fid, checkout, plugin)
        if h.sha:
            full = files.git_out(checkout, "rev-parse", "--verify", "--quiet", h.sha + "^{commit}")
            g.sha = full or h.sha
            g.commit_on_branch = bool(full) and commit_on_branch(checkout, full)
    g.new_commit = new_commit_since(checkout, fid, head_before)
    return g
