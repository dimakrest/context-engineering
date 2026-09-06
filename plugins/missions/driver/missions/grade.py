"""Post-exit grading (#4): one verdict per task, after the process is gone.

The schema function is the existing script, hooks/mission-handoff-schema.sh, called with a
synthetic payload and `MISSION_DIR` pinned; the driver's verdict and the worker's self-check
(`missions grade <mission-dir> F0nn --self`) call the same function, so the two agree.

Beyond the schema the grade checks what a hook at dispatch time never could: that the handoff was
written by THIS attempt (content changed since launch), that its commit is on the mission branch,
that the tree is clean, that the checkout is still on the mission branch, that every claimed
assertion belongs to the feature, and whether the harness reported a quota. When a commit landed
and no handoff was written, `reconstruct` writes one from the commit, marked as such.
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

_QUOTA_PHRASES = re.compile(
    r"(rate[ -]?limit|usage limit|session limit|hit your (?:\w+ )?limit|quota (?:exceeded|exhausted|reached)|"
    r"too many requests|overloaded_error|resets? (?:at|in) [^\n.]{1,40})", re.I)
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


def claimed_ids(handoff_raw: str) -> List[str]:
    """Assertion ids named as bullets under `## Assertions claimed`, minus those the handoff itself
    marks as not satisfied."""
    ids: List[str] = []
    inblock = False
    for ln in handoff_raw.split("\n"):
        if re.match(r"^##\s*assertions claimed", ln, re.I):
            inblock = True
            continue
        if inblock and re.match(r"^##\s", ln):
            break
        if not inblock:
            continue
        m = re.match(r"^\s*-\s*\**(A\d{3})\**\b(.*)$", ln)
        if m and not re.search(r"\bnot\s+satisfied\b", m.group(2), re.I):
            ids.append(m.group(1))
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
    """The harness's own quota/limit text, when the run ended on one. Phrases are matched in the
    harness's error detail, stderr and the stdout tail; bare codes (429 and friends) only where
    the harness speaks, not in the worker's transcript."""
    if outcome.rc == 0 and not outcome.detail:
        return None
    spoken = (outcome.detail or "") + "\n" + _tail(outcome.stderr_path)
    for text, codes in ((spoken, True), (_tail(outcome.stdout_path), False)):
        m = _QUOTA_PHRASES.search(text)
        if m is None and codes:
            m = _QUOTA_CODES.search(text)
        if m:
            start = max(0, m.start() - 60)
            snippet = text[start:m.end() + 80].replace("\n", " ").strip()
            return re.sub(r"\s+", " ", snippet)[:200]
    return None


def grade_feature(mission_dir: Path, fid: str, checkout: Path, plugin: Path, head_before: str,
                  handoff_before: Optional[str] = None, feature_assertions: Optional[List[str]] = None,
                  task: str = "") -> Grade:
    """The verdict for one task. `handoff_before` is the handoff's fingerprint at launch (None when
    there was none); the handoff counts as this attempt's only when its content differs."""
    from .watchdog import fingerprint  # local import: watchdog imports journal, not grade
    h = files.read_handoff(mission_dir, fid)
    g = Grade(handoff_exists=h.exists, status=h.status, sha=h.sha, issues=list(h.issues), task=task)
    g.handoff_written = h.exists and fingerprint(files.handoff_path(mission_dir, fid)) != handoff_before
    g.tree_dirty = files.dirty_paths(checkout)
    g.branch_after = files.git_out(checkout, "branch", "--show-current")
    g.new_commit = new_commit_since(checkout, fid, head_before)
    if h.exists and g.handoff_written:
        g.problems = handoff_problems(mission_dir, fid, checkout, plugin)
        if h.sha:
            full = files.git_out(checkout, "rev-parse", "--verify", "--quiet", h.sha + "^{commit}")
            g.sha = full or h.sha
            g.commit_on_branch = bool(full) and commit_on_branch(checkout, full)
        g.claimed = claimed_ids(h.raw)
        if feature_assertions is not None:
            foreign = [a for a in g.claimed if a not in feature_assertions]
            if foreign:
                g.problems.append("claims %s, not among %s's assertions (%s)" % (
                    ", ".join(foreign), fid, ", ".join(feature_assertions) or "none"))
        if g.tree_dirty and h.status != "blocked":
            g.problems.append("uncommitted changes left in the tree: %s%s" % (
                ", ".join(g.tree_dirty[:4]), "..." if len(g.tree_dirty) > 4 else ""))
    elif h.exists:
        g.problems.append("handoffs/%s.md is unchanged since launch: written by an earlier attempt, not this one" % fid)
    return g


# ---------------------------------------------------------------- reconstruction

def reconstruct(mission_dir: Path, fid: str, checkout: Path, head_before: str, commit: str, task: str,
                feature_assertions: List[str], how: str) -> Path:
    """Write handoffs/F0nn.md from the commit(s) the run left on the branch. The file says on its
    first lines that the driver wrote it and what it could not verify; a reconstructed handoff is
    weaker evidence than a written one and the VALIDATE reviewers see that marker."""
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
        "from the commit. No test run is recorded here: the assertions below are claimed on the strength of "
        "the commit alone, and the reviewers must not take them at face value." % (commit[:7], how),
        "",
        "## Status",
        "complete",
        "",
        "## Assertions claimed",
    ]
    for a in feature_assertions or []:
        lines.append("- %s — claimed by the driver from commit `%s`; not verified by the worker" % (a, commit[:7]))
    if not feature_assertions:
        lines.append("- none named for %s in features.md" % fid)
    lines += ["", "## Completed", "Commits this run left on the branch:"]
    lines += ["- " + s for s in subjects.splitlines()] or ["- `%s` %s" % (commit[:7], subject)]
    if stat:
        lines += ["", "```", stat, "```"]
    lines += [
        "",
        "## Left undone",
        "unknown — the worker left no record. Test evidence for %s is missing; a reviewer must run the "
        "feature's procedures before the assertions count as more than claimed." % fid,
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

def self_check(mission_dir: Path, fid: str, checkout: Path, plugin: Path) -> Grade:
    """The worker's view of the same verdict: everything the driver checks after exit that the
    worker can fix before it exits. No launch fingerprint (the file is its own), no task."""
    h = files.read_handoff(mission_dir, fid)
    feats = {f.id: f for f in files.read_features(mission_dir)} if (mission_dir / "features.md").exists() else {}
    feat = feats.get(fid)
    g = Grade(handoff_exists=h.exists, handoff_written=h.exists, status=h.status, sha=h.sha,
              issues=list(h.issues))
    g.tree_dirty = files.dirty_paths(checkout)
    g.branch_after = files.git_out(checkout, "branch", "--show-current")
    if not h.exists:
        g.problems.append("no handoff at %s" % files.handoff_path(mission_dir, fid))
        return g
    g.problems = handoff_problems(mission_dir, fid, checkout, plugin)
    if h.sha:
        full = files.git_out(checkout, "rev-parse", "--verify", "--quiet", h.sha + "^{commit}")
        g.sha = full or h.sha
        g.commit_on_branch = bool(full) and commit_on_branch(checkout, full)
        if full and not g.commit_on_branch:
            g.problems.append("commit %s is not on the current branch" % h.sha[:7])
    elif h.status != "blocked":
        g.problems.append("no commit sha under ## Commit")
    g.claimed = claimed_ids(h.raw)
    if feat is not None:
        foreign = [a for a in g.claimed if a not in feat.assertions]
        if foreign:
            g.problems.append("claims %s, not among %s's assertions (%s)" % (
                ", ".join(foreign), fid, ", ".join(feat.assertions) or "none"))
    if g.tree_dirty and h.status != "blocked":
        g.problems.append("uncommitted changes in the tree: %s%s -- commit them or discard them" % (
            ", ".join(g.tree_dirty[:4]), "..." if len(g.tree_dirty) > 4 else ""))
    return g
