"""The adapter contract (design §4) and the post-exit outcome classes (design §5).

`RunRequest` is what an adapter receives; `Outcome` is what it returns after the process is gone.
`Grade` is what the driver found on disk and in git afterwards, keyed to the task that ran.
`classify` turns the pair into exactly one of the eight classes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RunRequest:
    role: str                       # worker | reviewer | scrutiny | behavior | judgment
    task: str                       # "F012#2" -- feature id + attempt
    prompt_path: Path               # rendered user prompt
    cwd: Path                       # the checkout
    env: Dict[str, str]             # built by the adapter base, not inherited blindly
    timeout_s: int
    budget_usd: Optional[float]
    model: Optional[str]
    effort: Optional[str]
    read_only: bool
    output_path: Path               # where the adapter leaves the agent's final message
    # D1 additions to §4 (the adapters need them; none change an existing field)
    system_path: Optional[Path] = None   # rendered system prompt (agents/<role>.md body)
    run_dir: Path = Path(".")            # runs/<task>/
    tools: List[str] = field(default_factory=list)
    feature: str = ""
    mission_dir: Path = Path(".")
    watchdog: Any = None                 # watchdog.Watchdog; started and stopped by run_process


@dataclass
class Outcome:
    task: str
    rc: int
    elapsed_s: float
    timed_out: bool
    killed_by: Optional[str]        # "timeout" | "watchdog:commit_no_handoff" | "watchdog:silence" | None
    cost: Dict[str, Any]            # {"unit": "usd"|"tokens"|"unknown", "value": float|None, "source": str}
    harness: str
    model: Optional[str]            # what the harness reports it ran, else None -- never a default
    stdout_path: Path
    stderr_path: Path
    cls: str = ""                   # set by classify
    detail: str = ""                # the harness's own words on how it ended, when it said
    session_id: Optional[str] = None
    orphans_killed: bool = False    # something was still alive in the process group after exit

    @property
    def killed(self) -> bool:
        return self.timed_out or self.killed_by is not None

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stdout_path"] = str(self.stdout_path)
        d["stderr_path"] = str(self.stderr_path)
        return d


def unknown_cost(source: str = "") -> Dict[str, Any]:
    return {"unit": "unknown", "value": None, "source": source}


@dataclass
class Grade:
    """What the driver could verify after the process exited, keyed to one task.

    `handoff_exists` is the file on disk; `handoff_written` is whether THIS attempt wrote it (its
    content differs from what was there at launch). A stale handoff from an earlier attempt is
    evidence about that attempt, not this one."""
    handoff_exists: bool
    handoff_written: bool = False
    problems: List[str] = field(default_factory=list)   # from the schema function and the checks below
    status: str = ""                                     # complete | partial | blocked | ""
    sha: Optional[str] = None
    commit_on_branch: bool = False
    new_commit: Optional[str] = None                     # a commit that appeared on the branch during the run
    issues: List[str] = field(default_factory=list)
    undone: List[str] = field(default_factory=list)      # the handoff's "Left undone" lines
    claimed: List[str] = field(default_factory=list)     # assertion ids the handoff claims
    tree_dirty: List[str] = field(default_factory=list)  # uncommitted paths outside .missions/ after exit
    branch_after: str = ""                               # the checkout's branch after exit
    quota: Optional[str] = None                          # the harness's quota/limit text, when seen
    reconstructed: bool = False                          # the driver wrote the handoff from the commit
    task: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


CLASSES = ("done", "handoff_missing", "malformed_handoff", "tests_failed",
           "infra_quota", "infra_crash", "stalled", "no_op")


def classify(outcome: Outcome, grade: Grade) -> str:
    """One class per run. Evidence outranks the claim: a handoff this attempt did not write is not
    its handoff; a handoff written by a worker that was killed or exited non-zero is not `done`.

    The order matters. Quota is recognised only when the run left no handoff (the text alone is
    not the outcome) but before any commit is weighed: a quota after a WIP commit is still a quota,
    not a finished feature. A commit without a handoff is then `handoff_missing` however the run
    ended, unless the tree is also dirty, which is a shape no reconstruction can honestly record;
    how the run ended decides what the reconstruction says (complete or partial), not whether one
    is made.

    `grade.reconstructed` marks a handoff the driver wrote from the commit after the run was
    already over. The kill or the crash that left the record missing is what the reconstruction
    explains (a cut-off run is reconstructed `partial`, so it lands in `tests_failed` above), so it
    is not also held against it: such a grade is judged on its own evidence."""
    if grade.handoff_written:
        if grade.status in ("partial", "blocked"):
            return "tests_failed"
        if grade.problems:
            return "malformed_handoff"
        if not grade.sha or not grade.commit_on_branch:
            grade.problems.append("commit %s is not on the mission branch" % (grade.sha or "(none)"))
            return "malformed_handoff"
        if outcome.killed and not grade.reconstructed:
            grade.problems.append("the run was ended by the driver (%s) after it wrote a complete handoff" % (
                outcome.killed_by or "timeout"))
            return "malformed_handoff"
        if outcome.rc != 0 and not grade.reconstructed:
            grade.problems.append("the worker exited %d after writing a complete handoff%s" % (
                outcome.rc, (" -- " + outcome.detail) if outcome.detail else ""))
            return "malformed_handoff"
        return "done"
    if grade.quota:
        return "infra_quota"
    if grade.new_commit:
        if grade.tree_dirty:
            grade.problems.append("commit %s landed with no handoff, and the tree is not clean: %s" % (
                grade.new_commit[:7], ", ".join(grade.tree_dirty[:4])))
            return "malformed_handoff"
        return "handoff_missing"
    if outcome.timed_out or (outcome.killed_by or "").startswith("watchdog:"):
        return "stalled"
    if outcome.rc != 0:
        return "infra_crash"
    return "no_op"
