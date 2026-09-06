"""Post-exit grading (#4): one verdict per task, after the process is gone.

The schema function is the existing script, hooks/mission-handoff-schema.sh, called with a
synthetic payload and `MISSION_DIR` pinned. The driver's verdict (`grade_feature`) and the worker's
self-check (`self_check`, behind `missions grade <mission-dir> F0nn --self`) run the same checks out
of one body, `_check`, so the two agree by construction rather than by being kept in step; `advise`
is the only difference, and it only phrases the problems for the one who can still fix them.

Beyond the schema the grade checks what a hook at dispatch time never could: that the handoff was
written by THIS attempt (content changed since launch), that its commit is on the mission branch
(the branch's own ref, not HEAD: a detached checkout cannot make a commit count by sitting on it),
that the tree is clean, that the checkout is still on the mission branch, that every claimed
assertion belongs to the feature and that a `complete` handoff claims every one of them, and
whether the harness reported a quota. When a commit landed and no handoff was written,
`reconstruct` writes one from the commit, marked as such -- complete when the run ended on its
own terms, partial when it was cut off.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from . import files
from .outcome import Grade, Outcome

# The harness's own words for "you may not run right now". `overloaded` (a 529) is deliberately
# not here: it is transient, and a crash the streak rule retries is the right shape for it.
_QUOTA_PHRASES = re.compile(
    r"(rate[ _-]?limit|usage limit|session limit|hit your (?:\w+ )?limit|quota (?:exceeded|exhausted|reached)|"
    r"too many requests|resets? (?:at|in) [^\n.]{1,40})", re.I)
_QUOTA_CODES = re.compile(r"\b(429|resource_exhausted|insufficient_quota)\b", re.I)
_TAIL = 4096


def handoff_problems(mission_dir: Path, fid: str, checkout: Path, plugin: Path) -> List[str]:
    """Run the schema function against the feature's handoff. Empty list = valid evidence."""
    script = plugin / "hooks" / "mission-handoff-schema.sh"
    payload = json.dumps({"tool_input": {
        "subagent_type": "mission-worker",
        "prompt": "Mission: %s. Feature: %s — graded by the driver" % (mission_dir.name, fid)}})
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


def branch_ref(checkout: Path, branch: str) -> str:
    """The ref the grade measures against: the mission branch when it exists, else HEAD. Ancestry
    of HEAD is what a detached checkout, or one on another branch, would trivially satisfy."""
    if branch and files.git(checkout, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch,
                            check=False).returncode == 0:
        return "refs/heads/" + branch
    return "HEAD"


def commit_on_branch(checkout: Path, sha: str, branch: str = "") -> bool:
    return files.git(checkout, "merge-base", "--is-ancestor", sha, branch_ref(checkout, branch),
                     check=False).returncode == 0


def new_commit_since(checkout: Path, fid: str, head_before: str, branch: str = "") -> Optional[str]:
    """The newest commit that appeared on the mission branch during the run, preferring one whose
    subject carries the feature's prefix. None when the branch did not move."""
    if not head_before:
        return None
    out = files.git_out(checkout, "log", "--format=%H %s", "%s..%s" % (head_before, branch_ref(checkout, branch)))
    newest = None
    for line in out.splitlines():
        sha, _, subject = line.partition(" ")
        if newest is None:
            newest = sha
        if subject.startswith(fid + ":"):
            return sha
    return newest


def claimed_ids(handoff_raw: str) -> List[str]:
    """Assertion ids at the head of each bullet under `## Assertions claimed` (`- A001 — ...`,
    `- A001, A002 — both ...`), minus every bullet the handoff itself marks `not satisfied` --
    the template's own phrasing for a claim it is not making."""
    ids: List[str] = []
    inblock = False
    for ln in handoff_raw.split("\n"):
        if re.match(r"^##\s*assertions claimed", ln, re.I):
            inblock = True
            continue
        if inblock and re.match(r"^##\s", ln):
            break
        m = re.match(r"^\s*-\s*(.+)$", ln) if inblock else None
        if not m or re.search(r"\bnot\s+satisfied\b", ln, re.I):
            continue
        head = re.split(r"\s[—–-]\s|[:;]", m.group(1), 1)[0]
        for a in re.findall(r"\b(A\d{3})\b", head):
            if a not in ids:
                ids.append(a)
    return ids


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def quota_signature(outcome: Outcome) -> Optional[str]:
    """The harness's own quota/limit text, when the run ended on one: matched in the harness's
    error detail and the stderr tail, where the harness speaks. The worker's transcript on stdout
    is never read for this -- a test named after a rate limit is not a rate limit."""
    if outcome.rc == 0 and not outcome.detail:
        return None
    text = (outcome.detail or "") + "\n" + _tail(outcome.stderr_path)
    m = _QUOTA_PHRASES.search(text) or _QUOTA_CODES.search(text)
    if m:
        start = max(0, m.start() - 60)
        snippet = text[start:m.end() + 80].replace("\n", " ").strip()
        return re.sub(r"\s+", " ", snippet)[:200]
    return None


def _check(mission_dir: Path, fid: str, checkout: Path, plugin: Path, h: files.Handoff, g: Grade,
           feature_assertions: Optional[List[str]], branch: str, advise: bool) -> Grade:
    """Every check the driver's verdict and the worker's self-check share, on a handoff that exists
    and counts as the checker's own. `advise` phrases it for the one who can still fix it and adds
    the problem the driver leaves to `classify`."""
    g.problems = handoff_problems(mission_dir, fid, checkout, plugin)
    if h.sha:
        full = files.git_out(checkout, "rev-parse", "--verify", "--quiet", h.sha + "^{commit}")
        g.sha = full or h.sha
        g.commit_on_branch = bool(full) and commit_on_branch(checkout, full, branch)
        if advise and full and not g.commit_on_branch:
            g.problems.append("commit %s is not on the mission branch%s" % (h.sha[:7], (" " + branch) if branch else ""))
    g.claimed = claimed_ids(h.raw)
    if feature_assertions is not None:
        foreign = [a for a in g.claimed if a not in feature_assertions]
        if foreign:
            g.problems.append("claims %s, not among %s's assertions (%s)" % (
                ", ".join(foreign), fid, ", ".join(feature_assertions) or "none"))
        if h.status == "complete":
            missing = [a for a in feature_assertions if a not in g.claimed]
            if missing:
                g.problems.append("status complete but %s not claimed%s" % (
                    ", ".join(missing), " -- claim it, or set the status to partial and say what remains" if advise else ""))
    if g.tree_dirty and h.status != "blocked":
        g.problems.append("uncommitted changes %s the tree: %s%s%s" % (
            "in" if advise else "left in", ", ".join(g.tree_dirty[:4]),
            "..." if len(g.tree_dirty) > 4 else "",
            " -- commit them or discard them" if advise else ""))
    return g


def grade_feature(mission_dir: Path, fid: str, checkout: Path, plugin: Path, head_before: str,
                  handoff_before: Optional[str] = None, feature_assertions: Optional[List[str]] = None,
                  task: str = "", outcome: Optional[Outcome] = None, branch: str = "") -> Grade:
    """The verdict for one task. `handoff_before` is the handoff's fingerprint at launch (None when
    there was none); the handoff counts as this attempt's only when its content differs. `outcome`,
    when given, is the run the grade is keyed to -- its quota signature belongs to the grade.
    `branch` is the mission branch; commits count only on its ref."""
    h = files.read_handoff(mission_dir, fid)
    g = Grade(handoff_exists=h.exists, status=h.status, sha=h.sha, issues=list(h.issues),
              undone=list(h.undone), task=task)
    g.handoff_written = h.exists and files.fingerprint(files.handoff_path(mission_dir, fid)) != handoff_before
    g.tree_dirty = files.dirty_paths(checkout)
    g.branch_after = files.git_out(checkout, "branch", "--show-current")
    g.new_commit = new_commit_since(checkout, fid, head_before, branch)
    g.quota = quota_signature(outcome) if outcome is not None else None
    if h.exists and g.handoff_written:
        _check(mission_dir, fid, checkout, plugin, h, g, feature_assertions, branch, advise=False)
    elif h.exists:
        g.problems.append("handoffs/%s.md is unchanged since launch: written by an earlier attempt, not this one" % fid)
    return g


# ---------------------------------------------------------------- reconstruction

def reconstruct(mission_dir: Path, fid: str, checkout: Path, head_before: str, commit: str, task: str,
                feature_assertions: List[str], how: str, finished: bool = True) -> Path:
    """Write handoffs/F0nn.md from the commit(s) the run left on the branch. The file says on its
    first lines that the driver wrote it and what it could not verify; a reconstructed handoff is
    weaker evidence than a written one and the VALIDATE reviewers see that marker.

    `finished` is whether the run ended on its own terms -- a clean exit, or the watchdog ending a
    worker that went idle after its commit. Then the record says `complete` and claims the
    feature's assertions on the strength of the commit. A run cut off by its deadline, a crash or
    a quota is recorded `partial` and claims nothing: what the commit finished is unknown, and the
    next attempt is told to continue from it."""
    path = files.handoff_path(mission_dir, fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stale = Path(str(path) + ".stale")
        stale.write_bytes(path.read_bytes())
    subjects = files.git_out(checkout, "log", "--format=%h %s", "%s..%s" % (head_before, commit)) if head_before else ""
    stat = files.git_out(checkout, "show", "--stat", "--format=", commit)
    subject = files.git_out(checkout, "log", "-1", "--format=%s", commit)
    clean = not files.dirty_paths(checkout)
    lines = [
        "# Handoff %s — reconstructed by the driver (%s)" % (fid, task),
        "",
        "**Reconstructed.** The worker committed `%s` and %s without writing this file. The driver wrote it "
        "from the commit. No test run is recorded here: %s" % (
            commit[:7], how,
            "the assertions below are claimed on the strength of the commit alone, and the reviewers must not "
            "take them at face value." if finished else
            "the run did not end on its own terms, so nothing is claimed and the feature is not done."),
        "",
        "## Status",
        "complete" if finished else "partial",
        "",
        "## Assertions claimed",
    ]
    for a in feature_assertions or []:
        if finished:
            lines.append("- %s — claimed by the driver from commit `%s`; not verified by the worker" % (a, commit[:7]))
        else:
            lines.append("- %s — NOT satisfied as far as the driver can tell: the run was cut off before a handoff" % a)
    if not feature_assertions:
        lines.append("- none named for %s in features.md" % fid)
    lines += ["", "## Completed", "Commits this run left on the branch:"]
    lines += ["- " + s for s in subjects.splitlines()] or ["- `%s` %s" % (commit[:7], subject)]
    if stat:
        lines += ["", "```", stat, "```"]
    lines += ["", "## Left undone"]
    if finished:
        lines.append("unknown — the worker left no record. Test evidence for %s is missing; a reviewer must run the "
                     "feature's procedures before the assertions count as more than claimed." % fid)
    else:
        lines.append("the run %s after commit `%s` landed and before a handoff was written; what remains of %s is "
                     "unknown. Continue from that commit, finish the feature, and write the handoff yourself." % (
                         how, commit[:7], fid))
    lines += [
        "",
        "## Commands run",
        "| Command | Exit | Note |",
        "|---|---|---|",
        "| git merge-base --is-ancestor %s HEAD | 0 | driver: the commit is on the mission branch |" % commit[:7],
        "| git status --porcelain | 0 | driver: tree %s after the worker exited |" % ("clean" if clean else "NOT clean"),
        "",
        "## Issues discovered",
        "none",
        "",
        "## Procedures followed",
        "- unknown — not recorded by the worker (reconstructed handoff)",
        "",
        "## Commit",
        "`%s` %s" % (commit, subject),
        "",
    ]
    files.write_text(path, "\n".join(lines))
    return path


# ---------------------------------------------------------------- self-check

def self_check(mission_dir: Path, fid: str, checkout: Path, plugin: Path, branch: str = "") -> Grade:
    """The worker's view of the same verdict: everything the driver checks after exit that the
    worker can fix before it exits. No launch fingerprint (the file is its own), no task."""
    h = files.read_handoff(mission_dir, fid)
    feats = {f.id: f for f in files.read_features(mission_dir)} if (mission_dir / "features.md").exists() else {}
    feat = feats.get(fid)
    g = Grade(handoff_exists=h.exists, handoff_written=h.exists, status=h.status, sha=h.sha,
              issues=list(h.issues), undone=list(h.undone))
    g.tree_dirty = files.dirty_paths(checkout)
    g.branch_after = files.git_out(checkout, "branch", "--show-current")
    if not h.exists:
        g.problems.append("no handoff at %s" % files.handoff_path(mission_dir, fid))
        return g
    return _check(mission_dir, fid, checkout, plugin, h, g,
                  feat.assertions if feat is not None else None, branch, advise=True)
