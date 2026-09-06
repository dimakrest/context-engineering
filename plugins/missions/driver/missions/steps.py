"""The worker step and the minimal ingest (design §6, D1 subset).

step_worker: render -> take the 0.2 locks -> journal dispatch -> adapter.run() BLOCKS -> journal
return and cost -> release locks -> grade after exit -> classify -> journal step_done.
ingest_minimal: on `done`, range + patch + features.md + contract.md (claimed, never proven) +
open issues + resume_next + handoff_ingested. D2 (#4) owns the full ingest.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import files, journal, prompts
from .adapters import base
from .grade import grade_feature
from .outcome import Grade, Outcome, RunRequest, classify_minimal

WORKER_DEFAULTS = {"timeout_s": 2400, "budget_usd": 8.0}


@dataclass
class Context:
    mission_dir: Path
    checkout: Path
    plugin: Path
    cfg: Dict
    adapter: base.Adapter
    run_id: str
    harness: str
    log: Callable[[str], None] = print
    usd_this_run: float = 0.0

    @property
    def slug(self) -> str:
        return self.mission_dir.name

    @property
    def session(self) -> str:
        return "driver:" + self.run_id


def role_cfg(cfg: Dict, role: str) -> Dict:
    return dict((cfg.get("roles") or {}).get(role) or {})


def build_request(ctx: Context, feature: files.Feature, task: str, run_dir: Path, meta: Dict,
                  phase: str) -> RunRequest:
    """Everything the adapter needs, from the seat, driver.json and the agent definition, in that
    order of precedence. Writes nothing."""
    rc = role_cfg(ctx.cfg, "worker")
    # Seats (features.md) and the agent frontmatter speak Claude's model vocabulary (sonnet, opus,
    # fable, claude-...); they bind only under the claude harness. driver.json's role model is the
    # operator's choice for whichever harness is configured and always applies.
    claude_vocab = ctx.harness == "claude"
    model = (feature.seat if claude_vocab else None) or rc.get("model") or (meta.get("model") if claude_vocab else None) or None
    effort = rc.get("effort") or (meta.get("effort") if claude_vocab else None) or None
    timeout = int(rc.get("timeout_s", WORKER_DEFAULTS["timeout_s"]))
    budget = rc.get("budget_usd", WORKER_DEFAULTS["budget_usd"])
    budget = float(budget) if budget is not None else None
    env = base.build_env(ctx.mission_dir, run_dir, "worker", feature.id, task, phase, ctx.harness)
    return RunRequest(
        role="worker", task=task, prompt_path=run_dir / "prompt.md", cwd=ctx.checkout, env=env,
        timeout_s=timeout, budget_usd=budget, model=model, effort=effort, read_only=False,
        output_path=run_dir / "output.md", system_path=run_dir / "system.md", run_dir=run_dir,
        tools=list(meta.get("tools") or []), feature=feature.id, mission_dir=ctx.mission_dir)


def _write_outcome(run_dir: Path, outcome: Outcome, grade: Optional[Grade]) -> None:
    doc = outcome.to_json()
    if grade is not None:
        doc["grade"] = {"handoff_exists": grade.handoff_exists, "problems": grade.problems,
                        "status": grade.status, "sha": grade.sha, "commit_on_branch": grade.commit_on_branch,
                        "new_commit": grade.new_commit, "issues": grade.issues}
    (run_dir / "outcome.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")


def step_worker(ctx: Context, feature: files.Feature, state: files.State) -> Tuple[str, Outcome, Grade]:
    mdir = ctx.mission_dir
    fid = feature.id
    task = "%s#%d" % (fid, journal.attempts(mdir, fid) + 1)
    run_dir = mdir / "runs" / task
    run_dir.mkdir(parents=True, exist_ok=True)
    (mdir / "handoffs").mkdir(exist_ok=True)

    # render: system part from the agent definition, user part from the dispatch template
    meta, system = prompts.system_prompt(ctx.plugin)
    digest_text = prompts.digest(mdir, ctx.plugin)
    assertions = [a for a in files.read_contract(mdir) if a.id in feature.assertions]
    design = files.design_section(mdir, fid)
    rejection = journal.last_rejection(mdir, fid)
    user = prompts.worker_prompt(mdir, feature, digest_text, assertions, design, ctx.plugin, rejection)
    files.write_text(run_dir / "prompt.md", user)
    files.write_text(run_dir / "system.md", system)
    req = build_request(ctx, feature, task, run_dir, meta, state.phase)

    # launch: nothing is graded here -- a launch grades nothing
    head_before = files.git_out(ctx.checkout, "rev-parse", "HEAD")
    files.set_feature(mdir, fid, status="active")
    files.write_lock(mdir / ".writer", "mission-worker", fid, task, ctx.session)
    files.write_lock(mdir / ".lease", "mission-worker", fid, task, ctx.session)
    journal.append(mdir, "dispatch", agent="mission-worker", **{"class": "writer"}, model=req.model,
                   feature=fid, dispatch_id=task, session_id=ctx.session, task=task, harness=ctx.harness)
    ctx.log(">> %s  %s%s  timeout %ds%s  in %s" % (
        task, ctx.harness, (" " + req.model) if req.model else "", req.timeout_s,
        (" budget $%g" % req.budget_usd) if req.budget_usd is not None else "", ctx.checkout))

    try:
        outcome = ctx.adapter.run(req)            # BLOCKS until the process is gone
    finally:
        for name, event in ((".writer", "writer_lock_cleared"), (".lease", "lease_released")):
            line = files.remove_lock(mdir / name)
            if line is not None:
                journal.append(mdir, event, reason="returned", lock=line)

    status = "timed_out" if outcome.timed_out else ("completed" if outcome.rc == 0 else "error")
    journal.append(mdir, "agent_return", agent="mission-worker", feature=fid, dispatch_id=task,
                   duration_s=int(round(outcome.elapsed_s)), status=status, rc=outcome.rc,
                   model=outcome.model, task=task)
    journal.append(mdir, "cost", task=task, unit=outcome.cost.get("unit"), value=outcome.cost.get("value"),
                   source=outcome.cost.get("source"), harness=outcome.harness, model=outcome.model)
    if outcome.cost.get("unit") == "usd" and isinstance(outcome.cost.get("value"), (int, float)):
        ctx.usd_this_run += float(outcome.cost["value"])
        journal.append(mdir, "session_cost", session_id=ctx.session, usd=round(ctx.usd_this_run, 4))
    if outcome.orphans_killed:
        journal.append(mdir, "orphans_killed", task=task,
                       text="processes were still alive in the run's process group after it exited")
    _write_outcome(run_dir, outcome, None)

    # grade after exit, exactly once
    grade = grade_feature(mdir, fid, ctx.checkout, ctx.plugin, head_before)
    cls = classify_minimal(outcome, grade)
    outcome.cls = cls
    _write_outcome(run_dir, outcome, grade)
    problems = list(grade.problems)
    if cls == "no_op":
        problems.append("the run exited %d but produced no commit and no handoff" % outcome.rc)
    journal.append(mdir, "step_done", step=task, feature=fid, cls=cls, elapsed_s=round(outcome.elapsed_s, 1),
                   rc=outcome.rc, problems=problems or None, task=task)
    mark = "ok" if cls == "done" else "xx"
    ctx.log("%s %s  %s  rc=%d  %.1fs  cost=%s%s" % (
        mark, task, cls, outcome.rc, outcome.elapsed_s, _fmt_cost(outcome.cost),
        ("  " + "; ".join(problems[:2])) if problems else ""))
    if outcome.detail:
        ctx.log("   harness: " + outcome.detail[:200])
    return cls, outcome, grade


def _fmt_cost(cost: Dict) -> str:
    if cost.get("unit") == "usd" and cost.get("value") is not None:
        return "$%.2f" % cost["value"]
    if cost.get("unit") == "tokens" and cost.get("value") is not None:
        return "%d tokens" % cost["value"]
    return "unknown"


def _previous_head(ctx: Context) -> Optional[str]:
    rec = journal.last(ctx.mission_dir, "handoff_ingested")
    if rec and isinstance(rec.get("commit"), str):
        return files.git_out(ctx.checkout, "rev-parse", "--verify", "--quiet", rec["commit"] + "^{commit}") or None
    return None


def _base_for(ctx: Context, head: str) -> Tuple[str, str]:
    """(base sha, how it was chosen). The previous feature's head, else the merge-base with the
    main branch, else the commit's parent."""
    prev = _previous_head(ctx)
    if prev:
        return prev, "previous handoff_ingested commit"
    for ref in ("origin/main", "main", "master"):
        if files.git(ctx.checkout, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
            continue
        mb = files.git_out(ctx.checkout, "merge-base", head, ref)
        if mb and mb != head:
            return mb, "merge-base with " + ref
    parent = files.git_out(ctx.checkout, "rev-parse", "--verify", "--quiet", head + "~1")
    return (parent or head), "the commit's first parent (no previous feature, no main branch)"


def ingest_minimal(ctx: Context, feature: files.Feature, grade: Grade) -> Dict:
    mdir = ctx.mission_dir
    fid = feature.id
    head = grade.sha or ""
    base_sha, how = _base_for(ctx, head)
    rng = "%s..%s" % (base_sha, head)

    patch: Optional[str] = None
    cmd = ["bash", str(ctx.plugin / "scripts" / "mission-patch.sh"), str(mdir), fid, base_sha, head, "--"]
    cmd += feature.files
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(ctx.plugin)
    res = subprocess.run(cmd, cwd=str(ctx.checkout), capture_output=True, text=True, env=env,
                         encoding="utf-8", errors="replace")
    if res.returncode == 0:
        patch = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else str(mdir / "patches" / (fid + ".patch"))
    else:
        journal.append(mdir, "note", feature=fid, text="mission-patch.sh exited %d for %s: %s" % (
            res.returncode, rng[:15], (res.stderr.strip().splitlines() or ["no output"])[-1][:200]))

    files.set_feature(mdir, fid, status="done", commit=head, rng=rng)
    claimed = files.claim_assertions(mdir, feature.assertions)
    if grade.issues:
        files.add_open_issues(mdir, ["%s handoff: %s" % (fid, i) for i in grade.issues])

    feats = files.read_features(mdir)
    same = [f for f in feats if f.milestone == feature.milestone]
    pending = [f for f in same if f.status == "pending"]
    issues = files.read_state(mdir).open_issues
    tail = "%s handoff ingested, %s" % (fid, ("%d open issue(s)" % len(issues)) if issues else "no open issues")
    if pending:
        nxt = pending[0]
        k = same.index(nxt) + 1
        resume = "dispatch %s (%s feature %d of %d); %s" % (nxt.id, feature.milestone, k, len(same), tail)
    else:
        resume = "validate %s; %s" % (feature.milestone, tail)
    spend = journal.spend_usd(mdir)
    files.write_state_fields(mdir, resume_next=resume,
                             spend_usd=("%.2f" % spend) if spend is not None else "unknown")
    rec = journal.append(mdir, "handoff_ingested", feature=fid, status=grade.status or "complete",
                         commit=head[:7], range="%s..%s" % (base_sha[:7], head[:7]),
                         claimed=claimed or None, patch=patch, base_from=how)
    ctx.log("   ingested %s: range %s..%s (%s)%s%s" % (
        fid, base_sha[:7], head[:7], how, (", claimed " + ", ".join(claimed)) if claimed else "",
        (", %d issue(s) -> open issues" % len(grade.issues)) if grade.issues else ""))
    return rec
