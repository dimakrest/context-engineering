"""The adapter contract (design §4) and the post-exit outcome classes (design §5, D1 subset).

`RunRequest` is what an adapter receives; `Outcome` is what it returns after the process is gone.
`Grade` is what the driver found on disk and in git afterwards. `classify_minimal` turns the pair
into exactly one class. D2 (#4) replaces the classifier with the full eight-class version and the
watchdog's `killed_by` values; the shapes here are the ones it extends.
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


@dataclass
class Outcome:
    task: str
    rc: int
    elapsed_s: float
    timed_out: bool
    killed_by: Optional[str]        # "timeout" | None (watchdog values arrive in D2)
    cost: Dict[str, Any]            # {"unit": "usd"|"tokens"|"unknown", "value": float|None, "source": str}
    harness: str
    model: Optional[str]            # what the harness reports it ran, else None -- never a default
    stdout_path: Path
    stderr_path: Path
    cls: str = ""                   # set by classify
    detail: str = ""                # the harness's own words on how it ended, when it said
    session_id: Optional[str] = None
    orphans_killed: bool = False    # something was still alive in the process group after exit

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stdout_path"] = str(self.stdout_path)
        d["stderr_path"] = str(self.stderr_path)
        return d


def unknown_cost(source: str = "") -> Dict[str, Any]:
    return {"unit": "unknown", "value": None, "source": source}


@dataclass
class Grade:
    """What the driver could verify after the process exited."""
    handoff_exists: bool
    problems: List[str] = field(default_factory=list)   # from the schema function
    status: str = ""                                     # complete | partial | blocked | ""
    sha: Optional[str] = None
    commit_on_branch: bool = False
    new_commit: Optional[str] = None                     # an `F0nn:` commit that appeared during the run
    issues: List[str] = field(default_factory=list)


CLASSES = ("done", "handoff_missing", "malformed_handoff", "tests_failed",
           "infra_quota", "infra_crash", "stalled", "no_op")


def classify_minimal(outcome: Outcome, grade: Grade) -> str:
    """One class per run. Evidence outranks the claim: a handoff written by a worker that was
    killed, or that exited non-zero, is not `done`."""
    if outcome.timed_out:
        return "stalled"
    if grade.handoff_exists:
        if grade.status in ("partial", "blocked"):
            return "tests_failed"
        if grade.problems:
            return "malformed_handoff"
        if not grade.sha or not grade.commit_on_branch:
            grade.problems.append("commit %s is not on the mission branch" % (grade.sha or "(none)"))
            return "malformed_handoff"
        if outcome.rc != 0:
            grade.problems.append("the worker exited %d after writing a complete handoff%s" % (
                outcome.rc, (" -- " + outcome.detail) if outcome.detail else ""))
            return "malformed_handoff"
        return "done"
    if grade.new_commit:
        return "handoff_missing"
    if outcome.rc != 0:
        return "infra_crash"
    return "no_op"
