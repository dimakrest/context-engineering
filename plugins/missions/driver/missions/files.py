"""Parsers and writers for the mission files.

Every regex here mirrors an existing parser in the plugin -- hooks/mission-lib.sh (awk),
scripts/check.sh and scripts/mission-converge.sh (python) -- so the driver reads exactly what the
hooks read and writes exactly what they expect. The driver is a second writer of the same files,
not a new format (design §2). Edits are line-surgical: the surrounding prose is never rewritten.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .journal import epoch_now, now_iso

PHASES = ("planning", "implementing", "validating", "negotiating", "pr", "halted", "done")
_PHASE_ALIASES = {
    "implementation": "implementing", "implement": "implementing",
    "validation": "validating", "validate": "validating",
    "negotiation": "negotiating", "negotiate": "negotiating",
    "plan": "planning", "planned": "planning",
    "complete": "done", "completed": "done",
    "halt": "halted", "paused": "halted",
}

REQUIRED_FILES = ("state.md", "mission.md", "contract.md", "features.md", "design.md")


class MissionFileError(Exception):
    """A mission file is missing or not in the shape the driver can edit."""


def plugin_root() -> Path:
    env = os.environ.get("MISSIONS_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- git

def git(checkout: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args], text=True, capture_output=True,
                          check=check, encoding="utf-8", errors="replace")


def git_out(checkout: Path, *args: str) -> str:
    res = git(checkout, *args, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def dirty_paths(checkout: Path) -> List[str]:
    """Uncommitted paths (modified, staged, untracked) outside .missions/ -- the driver rewrites
    .missions/ constantly, so it never counts."""
    out: List[str] = []
    for ln in git_out(checkout, "status", "--porcelain", "--untracked-files=normal").splitlines():
        p = ln[3:].split(" -> ")[-1].strip().strip('"')
        if p and not p.startswith(".missions/") and p != ".missions":
            out.append(p)
    return out


# ---------------------------------------------------------------- state.md

_FENCE_OPEN = re.compile(r"^```mission-state[ \t]*$")
_FENCE_CLOSE = re.compile(r"^```")


@dataclass
class State:
    phase: str
    milestone: str
    spend_usd: str
    resume_next: str
    state_cap_lines: int
    branch: str
    open_issues: List[str]
    has_block: bool
    raw: str


def _fence_span(lines: List[str]) -> Optional[Tuple[int, int]]:
    """(first line inside the fence, index of the closing fence) or None."""
    for i, ln in enumerate(lines):
        if _FENCE_OPEN.match(ln):
            for j in range(i + 1, len(lines)):
                if _FENCE_CLOSE.match(lines[j]):
                    return i + 1, j
            return i + 1, len(lines)
    return None


def _block_field(lines: List[str], key: str) -> Optional[str]:
    """mission_field(): first `key:` inside the fence, trailing ` # comment` stripped."""
    span = _fence_span(lines)
    if span is None:
        return None
    for ln in lines[span[0]:span[1]]:
        ln = re.sub(r"\s+#.*$", "", ln)
        m = re.match(r"^" + re.escape(key) + r":[ \t]*", ln)
        if m:
            return ln[m.end():].rstrip()
    return None


def _legacy_field(lines: List[str], label: str) -> Optional[str]:
    for ln in lines:
        if re.match(r"^\*\*" + re.escape(label) + r":\*\*", ln, re.I):
            return re.sub(r"^[^:]*:\**\s*", "", ln).rstrip()
    return None


def normalise_phase(value: str) -> str:
    v = re.sub(r"[^a-z].*$", "", value.lower())
    return _PHASE_ALIASES.get(v, v)


def _open_issues(lines: List[str]) -> List[str]:
    out: List[str] = []
    inblock = False
    for ln in lines:
        if re.match(r"^##\s+[Oo]pen issues", ln):
            inblock = True
            continue
        if inblock and re.match(r"^##\s", ln):
            inblock = False
        if inblock and re.match(r"^\s*-\s*", ln):
            text = re.sub(r"^\s*-\s*", "", ln)
            if text.strip().lower() == "none" or text.strip() == "":
                continue
            out.append(text)
    return out


def read_state(mission_dir: Path) -> State:
    raw = read_text(mission_dir / "state.md")
    lines = raw.split("\n")
    has_block = _fence_span(lines) is not None
    phase = _block_field(lines, "phase")
    if not phase:
        legacy = _legacy_field(lines, "Phase") or ""
        phase = legacy.split()[0] if legacy.split() else ""
    milestone = _block_field(lines, "milestone") or (_legacy_field(lines, "Milestone") or "")[:120]
    cap = _block_field(lines, "state_cap_lines") or ""
    branch = ""
    for ln in lines:
        m = re.match(r"^\*\*Branch:\*\*\s*(.+?)\s*$", ln)
        if m:
            branch = m.group(1).strip("`").strip()
            break
    return State(
        phase=normalise_phase(phase),
        milestone=milestone.strip(),
        spend_usd=_block_field(lines, "spend_usd") or "unknown",
        resume_next=_block_field(lines, "resume_next") or "",
        state_cap_lines=int(cap) if cap.isdigit() else 200,
        branch=branch,
        open_issues=_open_issues(lines),
        has_block=has_block,
        raw=raw,
    )


def write_state_fields(mission_dir: Path, **fields: str) -> None:
    """Rewrite `key:` lines inside the fence in place (comments and order kept); missing keys are
    added before the closing fence. Values must not contain ` #` -- the hooks read that as a comment."""
    path = mission_dir / "state.md"
    lines = read_text(path).split("\n")
    span = _fence_span(lines)
    if span is None:
        raise MissionFileError("state.md has no ```mission-state block; the driver needs the v2 block")
    start, end = span
    pending = {k: str(v).replace(" #", " no.") for k, v in fields.items()}
    for i in range(start, end):
        m = re.match(r"^([A-Za-z_]+):([ \t]*)(.*?)([ \t]+#.*)?$", lines[i])
        if m and m.group(1) in pending:
            lines[i] = "%s: %s%s" % (m.group(1), pending.pop(m.group(1)), m.group(4) or "")
    for key, val in pending.items():
        lines.insert(end, "%s: %s" % (key, val))
        end += 1
    write_text(path, "\n".join(lines))


def add_open_issues(mission_dir: Path, bullets: List[str]) -> None:
    """Append bullets under `## Open issues`, replacing a lone `- none`."""
    if not bullets:
        return
    path = mission_dir / "state.md"
    lines = read_text(path).split("\n")
    head = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+[Oo]pen issues", ln):
            head = i
            break
    if head is None:
        raise MissionFileError("state.md has no `## Open issues` section")
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if re.match(r"^##\s", lines[i]):
            end = i
            break
    existing = [i for i in range(head + 1, end) if re.match(r"^\s*-\s*", lines[i])]
    new = ["- " + b.strip() for b in bullets]
    if len(existing) == 1 and re.sub(r"^\s*-\s*", "", lines[existing[0]]).strip().lower() in ("none", ""):
        lines[existing[0]:existing[0] + 1] = new
    elif existing:
        at = existing[-1] + 1
        lines[at:at] = new
    else:
        lines[head + 1:head + 1] = new
    write_text(path, "\n".join(lines))


# ---------------------------------------------------------------- features.md

_MILESTONE_RE = re.compile(r"^##\s+(M\d+[a-z]?)\b")
_FEATURE_RE = re.compile(r"^###\s+(F\d{3})\b\s*(?:[—–-]+\s*)?(.*)$")
_BULLET_RE = re.compile(r"^-\s+\*\*([^*]+?):\*\*\s*(.*)$")
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")


@dataclass
class Feature:
    id: str
    title: str
    milestone: str
    assertions: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    seat: Optional[str] = None
    procedures: str = ""
    depends: List[str] = field(default_factory=list)
    out_of_scope: str = ""
    status: str = "pending"
    commit: Optional[str] = None
    range: Optional[str] = None
    start: int = 0      # line index of the `### F0nn` heading
    end: int = 0        # exclusive line index of the section end


def _section_end(lines: List[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if re.match(r"^##", lines[i]):
            return i
    return len(lines)


def _split_files(value: str) -> List[str]:
    quoted = re.findall(r"`([^`]+)`", value)
    if quoted:
        return [q.strip() for q in quoted if q.strip()]
    return [p.strip() for p in value.split(",") if p.strip() and p.strip() not in ("—", "-")]


def read_features(mission_dir: Path) -> List[Feature]:
    lines = read_text(mission_dir / "features.md").split("\n")
    feats: List[Feature] = []
    milestone = ""
    cur: Optional[Feature] = None
    for i, ln in enumerate(lines):
        m = _MILESTONE_RE.match(ln)
        if m:
            milestone = m.group(1)
            cur = None
            continue
        m = _FEATURE_RE.match(ln)
        if m:
            cur = Feature(id=m.group(1), title=m.group(2).strip().rstrip("."), milestone=milestone,
                          start=i, end=_section_end(lines, i))
            feats.append(cur)
            continue
        if cur is None:
            continue
        m = _BULLET_RE.match(ln)
        if not m:
            continue
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if key == "assertions":
            cur.assertions = re.findall(r"A\d{3}[a-z]?", val)
        elif key == "files":
            cur.files = _split_files(val)
        elif key == "seat":
            tok = re.match(r"[A-Za-z0-9.\-]+", val)
            cur.seat = tok.group(0) if tok else None
        elif key == "procedures":
            cur.procedures = val
        elif key == "depends on":
            head = re.split(r"[—(]", val)[0]
            cur.depends = re.findall(r"F\d{3}", head)
        elif key == "out of scope":
            cur.out_of_scope = val
        elif key == "status":
            first = val.split()[0].lower() if val.split() else "pending"
            cur.status = re.sub(r"[^a-z]", "", first) or "pending"
            sha = _SHA_RE.search(val.split("commit", 1)[1]) if "commit" in val else None
            cur.commit = sha.group(0) if sha else None
        elif key == "range":
            m2 = re.search(r"([0-9a-f]{7,40})`?\.\.`?([0-9a-f]{7,40})", val)
            cur.range = "%s..%s" % (m2.group(1), m2.group(2)) if m2 else None
    return feats


def set_feature(mission_dir: Path, fid: str, status: Optional[str] = None,
                commit: Optional[str] = None, rng: Optional[str] = None) -> None:
    """Rewrite the feature's `- **Status:**` line and add or replace `- **Range:**`."""
    path = mission_dir / "features.md"

    def _bullet_line(key: str) -> str:
        if key == "status":
            text = "- **Status:** " + (status or "pending")
            if commit:
                text += " · commit `%s`" % commit[:7]
            return text
        base, head = (rng or "..").split("..", 1)
        return "- **Range:** `%s`..`%s`" % (base[:7], head[:7])

    def _put(key: str, after_key: Optional[str]) -> None:
        lines = read_text(path).split("\n")
        feat = next((f for f in read_features(mission_dir) if f.id == fid), None)
        if feat is None:
            raise MissionFileError("features.md has no `### %s` section" % fid)
        idx = {}
        for i in range(feat.start + 1, feat.end):
            m = _BULLET_RE.match(lines[i])
            if m:
                idx[m.group(1).strip().lower()] = i
        if key in idx:
            lines[idx[key]] = _bullet_line(key)
        else:
            anchor = idx.get(after_key or "", None)
            if anchor is None:
                anchor = max(idx.values(), default=feat.start)
            lines.insert(anchor + 1, _bullet_line(key))
        write_text(path, "\n".join(lines))

    if status is not None:
        _put("status", None)
    if rng is not None:
        _put("range", "status")


# ---------------------------------------------------------------- contract.md

@dataclass
class Assertion:
    id: str
    text: str
    proof_class: str
    features: List[str]
    status: str
    evidence: str
    budget: str
    line: int


def _table_cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _contract_columns(lines: List[str]) -> Tuple[Optional[int], Dict[str, int]]:
    """check.sh locates the columns by header name; so do we."""
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("|"):
            continue
        cells = [c.lower() for c in _table_cells(ln)]
        if "id" not in cells:
            continue
        cols: Dict[str, int] = {}
        for j, c in enumerate(cells):
            if c == "id":
                cols["id"] = j
            elif c.startswith("assertion"):
                cols["text"] = j
            elif c.startswith("proof class"):
                cols["class"] = j
            elif c.startswith("feature"):
                cols["features"] = j
            elif c == "status":
                cols["status"] = j
            elif c.startswith("evidence"):
                cols["evidence"] = j
            elif c.startswith("proof budget"):
                cols["budget"] = j
        if "status" in cols:
            return i, cols
    return None, {}


def read_contract(mission_dir: Path) -> List[Assertion]:
    lines = read_text(mission_dir / "contract.md").split("\n")
    header, cols = _contract_columns(lines)
    if header is None:
        return []
    rows: List[Assertion] = []

    def cell(cells: List[str], key: str) -> str:
        j = cols.get(key)
        return cells[j] if j is not None and j < len(cells) else ""

    for i in range(header + 1, len(lines)):
        ln = lines[i]
        if not ln.lstrip().startswith("|"):
            if rows:
                break
            continue
        cells = _table_cells(ln)
        aid = cell(cells, "id")
        if not re.match(r"^A\d{3}[a-z]?$", aid):
            continue
        rows.append(Assertion(
            id=aid, text=cell(cells, "text"), proof_class=cell(cells, "class").lower(),
            features=re.findall(r"F\d{3}", cell(cells, "features")),
            status=cell(cells, "status").lower(), evidence=cell(cells, "evidence"),
            budget=cell(cells, "budget"), line=i))
    return rows


def claim_assertions(mission_dir: Path, ids: List[str]) -> List[str]:
    """`unproven` -> `claimed` for the given ids. Never touches `proven`. Returns what changed."""
    path = mission_dir / "contract.md"
    lines = read_text(path).split("\n")
    _, cols = _contract_columns(lines)
    changed: List[str] = []
    if "status" not in cols:
        return changed
    for row in read_contract(mission_dir):
        if row.id in ids and row.status == "unproven":
            parts = lines[row.line].split("|")
            k = cols["status"] + 1
            if k < len(parts):
                parts[k] = " claimed "
                lines[row.line] = "|".join(parts)
                changed.append(row.id)
    if changed:
        write_text(path, "\n".join(lines))
    return changed


# ---------------------------------------------------------------- mission.md

_BUDGET_LABELS = {
    "dollar_cap": "Dollar cap",
    "dispatch_cap": "Dispatch cap",
    "wall_cap_h": "Active wall-clock cap",
    "repair_rounds": "Repair rounds per assertion",
    "terminal_reserve_pct": "Terminal-review reserve",
}


def read_budget(mission_dir: Path) -> Dict[str, Optional[float]]:
    """mission_budget(): `- [**]Label...: <first number>`; a missing cap is None (informational)."""
    path = mission_dir / "mission.md"
    lines = read_text(path).split("\n") if path.exists() else []
    out: Dict[str, Optional[float]] = {}
    for key, label in _BUDGET_LABELS.items():
        out[key] = None
        for ln in lines:
            if re.match(r"^-\s*(\*\*)?" + re.escape(label), ln, re.I):
                rest = ln.split(":", 1)[1] if ":" in ln else ""
                m = re.search(r"[0-9]+([.][0-9]+)?", rest)
                if m:
                    out[key] = float(m.group(0))
                break
    return out


# ---------------------------------------------------------------- design.md

def design_section(mission_dir: Path, fid: str) -> Tuple[str, List[str]]:
    """The feature's `### F0nn` block of design.md, verbatim, and the `| D0nn |` rows it cites."""
    path = mission_dir / "design.md"
    if not path.exists():
        return "", []
    lines = read_text(path).split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^###\s+" + fid + r"\b", ln)), None)
    if start is None:
        return "", []
    end = _section_end(lines, start)
    section = "\n".join(lines[start:end]).strip()
    ids = set(re.findall(r"D\d{3}", section))
    rows = [ln for ln in lines
            if re.match(r"^\|\s*(D\d{3})\s*\|", ln) and re.match(r"^\|\s*(D\d{3})\s*\|", ln).group(1) in ids]
    return section, rows


# ---------------------------------------------------------------- handoffs/F0nn.md

@dataclass
class Handoff:
    exists: bool
    status: str = ""
    issues: List[str] = field(default_factory=list)
    sha: Optional[str] = None
    raw: str = ""


def handoff_path(mission_dir: Path, fid: str) -> Path:
    return mission_dir / "handoffs" / ("%s.md" % fid)


def read_handoff(mission_dir: Path, fid: str) -> Handoff:
    path = handoff_path(mission_dir, fid)
    if not path.exists():
        return Handoff(exists=False)
    raw = read_text(path)
    lines = raw.split("\n")
    h = Handoff(exists=True, raw=raw)
    # status: first non-blank line after `## Status`, lowercased, whitespace removed (the hook's rule)
    for i, ln in enumerate(lines):
        if re.match(r"^##\s*status\b", ln, re.I):
            for ln2 in lines[i + 1:]:
                if ln2.strip():
                    h.status = re.sub(r"\s+", "", ln2.lower())
                    break
            break
    # issues: the `## Issues discovered` section; bullets, or prose lines when there are none
    inblock = False
    bullets: List[str] = []
    prose: List[str] = []
    for ln in lines:
        if re.match(r"^##\s*issues discovered", ln, re.I):
            inblock = True
            continue
        if inblock and re.match(r"^##\s", ln):
            break
        if not inblock or not ln.strip():
            continue
        if re.match(r"^\s*-\s*", ln):
            bullets.append(re.sub(r"^\s*-\s*", "", ln).strip())
        else:
            prose.append(ln.strip())
    items = bullets if bullets else prose
    h.issues = [t for t in items if t and t.lower().strip("*_`. ") != "none"]
    # commit: first sha on a line after `## Commit`
    after = False
    for ln in lines:
        if re.match(r"^##\s*[Cc]ommit", ln):
            after = True
            continue
        if after:
            m = re.search(r"\b[0-9a-f]{7,40}\b", ln)
            if m:
                h.sha = m.group(0)
                break
    return h


# ---------------------------------------------------------------- driver.json

def config_path(mission_dir: Path) -> Path:
    return mission_dir / "driver.json"


def read_config(mission_dir: Path) -> Dict:
    p = config_path(mission_dir)
    if not p.exists():
        raise MissionFileError("driver.json missing -- run `missions init %s`" % mission_dir)
    try:
        return json.loads(read_text(p))
    except ValueError as e:
        raise MissionFileError("driver.json is not valid JSON: %s" % e)


def write_config(mission_dir: Path, cfg: Dict) -> None:
    write_text(config_path(mission_dir), json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def checkout_of(mission_dir: Path, cfg: Dict) -> Path:
    """`.missions/<slug>/` lives inside the checkout; `checkout` in driver.json is relative to it."""
    return (mission_dir.resolve().parent.parent / cfg.get("checkout", ".")).resolve()


# ---------------------------------------------------------------- .writer / .lease

def write_lock(path: Path, agent: str, feature: str, dispatch_id: str, session: str) -> str:
    """mission_lock_write()'s exact format: one line of space-separated k=v, values without spaces."""
    line = "agent=%s feature=%s dispatch_id=%s session=%s ts=%s epoch=%d\n" % (
        agent, feature, dispatch_id, session, now_iso(), epoch_now())
    write_text(path, line)
    return line.rstrip("\n")


def remove_lock(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    line = read_text(path).strip().split("\n")[0]
    path.unlink()
    return line
