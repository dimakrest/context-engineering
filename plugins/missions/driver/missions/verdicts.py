"""Validator output parsing (design §6.1, "proven is written only from a validator verdict").

Each validator's final message carries a per-assertion table in the shape its agent definition
fixes: agents/mission-reviewer.md `## Assertion verdicts`, agents/mission-validator-behavior.md
`## Assertion results`, agents/mission-validator-scrutiny.md `## Commands` and `## Failures`. The
parsers read those tables and nothing else -- a verdict is a cell in a table, never a sentence in
the prose, so a reviewer that writes "A002 is probably fine" in a paragraph has said nothing about
A002. An assertion the table does not name is left to the caller, which treats a missing verdict
as the weakest one. A reviewer's `## Defects` table has no parser: the negotiate prompt pastes
the whole validation file, and the judgment reads the defects there.

`latest_verdicts` reads the journal's `verdict` events rather than the validation files: they are
the source of truth after a run, so a driver re-entering VALIDATE after a crash never re-parses a
file it already journaled.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import files, journal, prompts

_SEP_CELL = re.compile(r"^:?-+:?$")
# a table whose header row has no separator under it is still a table; its first cell says so
_HEADER_WORDS = ("id", "assertion", "command", "severity", "d-id", "changed symbol", "file")


def _is_separator(cells: List[str]) -> bool:
    return any(cells) and all(_SEP_CELL.match(c) for c in cells if c)


def table_rows(sec: str) -> List[List[str]]:
    """Data rows of the markdown table(s) in a section: cells stripped, separators dropped, the
    header dropped -- the row a separator follows, or one whose first cell is a column name."""
    lines = [ln for ln in sec.split("\n") if ln.lstrip().startswith("|")]
    rows: List[List[str]] = []
    for i, ln in enumerate(lines):
        cells = files.table_cells(ln)
        if _is_separator(cells):
            continue
        if i + 1 < len(lines) and _is_separator(files.table_cells(lines[i + 1])):
            continue
        if cells and cells[0].strip("*`_ ").lower() in _HEADER_WORDS:
            continue
        rows.append(cells)
    return rows


def _id_cell(cell: str) -> Optional[str]:
    m = re.match(r"^[*`_\s]*(A\d{3}[a-z]?)\b", cell)
    return m.group(1) if m else None


# ---------------------------------------------------------------- reviewer

def reviewer_verdict(cell: str) -> str:
    """`not satisfied` is tested before `satisfied`: the second is a substring of the first, and
    the naive order reads every failure as a pass. Anything that is not one of the two is
    `cannot tell` -- the reviewer's own third answer, and the only safe reading of a hedge."""
    c = cell.strip("*`_ ").lower()
    if "not satisfied" in c:
        return "not satisfied"
    if c.startswith("satisfied"):
        return "satisfied"
    return "cannot tell"


def parse_reviewer(text: str) -> Dict[str, str]:
    """`## Assertion verdicts` rows -> {A001: satisfied | not satisfied | cannot tell}."""
    out: Dict[str, str] = {}
    for cells in table_rows(files.section(text, "Assertion verdicts")):
        aid = _id_cell(cells[0]) if cells else None
        if aid and len(cells) > 1:
            out[aid] = reviewer_verdict(cells[1])
    return out


# ---------------------------------------------------------------- behavior

def behavior_verdict(cell: str) -> str:
    """proven | FAILED | not reached, case-insensitive; anything else is `not reached` -- the
    validator's own word for "never checked", which is what an unreadable cell amounts to."""
    c = cell.strip("*`_ ").lower()
    if re.match(r"^\W*proven\b", c):
        return "proven"
    if "failed" in c:
        return "FAILED"
    return "not reached"


def parse_behavior(text: str) -> Dict[str, str]:
    """`## Assertion results` rows -> {A012: proven | FAILED | not reached}."""
    out: Dict[str, str] = {}
    for cells in table_rows(files.section(text, "Assertion results")):
        aid = _id_cell(cells[0]) if cells else None
        if aid and len(cells) > 1:
            out[aid] = behavior_verdict(cells[1])
    return out


# ---------------------------------------------------------------- scrutiny

def parse_scrutiny(text: str) -> Dict[str, Any]:
    """`commands`: the `## Commands` rows as {command, exit (int or None), duration};
    `failures`: the `## Failures` section's text. Enough to journal a summary and to say whether
    the gate was green; the negotiate prompt pastes the file itself."""
    commands: List[Dict[str, Any]] = []
    for cells in table_rows(files.section(text, "Commands")):
        cells = cells + ["", "", ""]
        m = re.search(r"-?\d+", cells[1])
        commands.append({"command": cells[0], "exit": int(m.group(0)) if m else None, "duration": cells[2]})
    return {"commands": commands, "failures": files.section(text, "Failures").strip()}


# ---------------------------------------------------------------- journal

# the validators whose `verdict` record carries an assertion table, by the agent name the record
# names; scrutiny's carries commands and is a verdict on no assertion
VALIDATOR_ROLES = {prompts.AGENTS["reviewer"]: "reviewer", prompts.AGENTS["behavior"]: "behavior"}


def assertion_verdicts(mission_dir: Path, milestone: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """(role, record) for every journal `verdict` of the milestone that carries an assertion
    table, oldest first -- so a reader that keeps the last one seen holds the latest."""
    for rec in journal.events(mission_dir):
        if rec.get("event") != "verdict" or rec.get("milestone") != milestone:
            continue
        role = VALIDATOR_ROLES.get(str(rec.get("validator")))
        if role is not None and isinstance(rec.get("assertions"), dict):
            yield role, rec


def latest_verdicts(mission_dir: Path, milestone: str) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """From the journal's `verdict` events for the milestone, the LATEST verdict per assertion id
    with the file it came from: {"reviews": {A001: (verdict, file)}, "behavior": {...}}. The two
    validators answer different questions, so a behavior `proven` never overwrites a reviewer's
    `not satisfied` or the other way round."""
    out: Dict[str, Dict[str, Tuple[str, str]]] = {"reviews": {}, "behavior": {}}
    bucket = {"reviewer": "reviews", "behavior": "behavior"}
    for role, rec in assertion_verdicts(mission_dir, milestone):
        for aid, v in rec["assertions"].items():
            out[bucket[role]][str(aid)] = (str(v), str(rec.get("file") or ""))
    return out
