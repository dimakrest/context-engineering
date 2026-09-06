"""The steps (design §6): the worker step and the ingest, the generic run for the other roles,
the judgment run with its one retry, triage, and the typed stop.

step_worker: render -> prep (the env, the git files it points at) -> host lease -> take the 0.2
locks -> journal dispatch -> adapter.run() BLOCKS, with the watchdog watching the branch -> journal
return and cost -> release locks and the lease -> grade after exit,
exactly once, keyed by task -> classify -> journal step_done. A commit without a handoff is
reconstructed here and graded once more before the loop sees a class.
ingest: on `done`, range + patch + features.md + contract.md (claimed, never proven) + open issues
+ code-index refresh + resume_next + handoff_ingested.
run_role: reviewer, scrutiny, behavior (executors: the host lease and `.lease`; the reviewer
inside the blind window) and judgment (static: neither) -- prep, dispatch, run, return, cost,
step_done, and the validation file when the caller names one. Nothing is graded here: what a
validator's message says is the caller's to parse (verdicts.py).
run_judgment: a judgment run whose reply must be one JSON object the step's schema accepts --
re-run once with the error appended, then stop("error"). The model proposes, the driver applies.
step_triage / apply_triage / register: the open issues a handoff raised, dispositioned by a
judgment run and applied here to state.md, followups.md, features.md and contract.md.
stop: the typed stops (design §6.4). They live here, next to Context, because every step that
can end the run needs one and the loop imports the steps.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from . import files, journal, judgment, prep, prompts, watchdog
from .adapters import base
from .grade import grade_feature, reconstruct
from .outcome import Grade, Outcome, RunRequest, classify

# design §3: what a role gets when driver.json does not say
ROLE_DEFAULTS = {
    "worker": {"timeout_s": 2400, "budget_usd": 8},
    "reviewer": {"timeout_s": 1500, "budget_usd": 6},
    "scrutiny": {"timeout_s": 1800, "budget_usd": 4},
    "behavior": {"timeout_s": 2400, "budget_usd": 10},
    "judgment": {"timeout_s": 300, "budget_usd": 2},
}
# design §7.1: the roles that run things take the host lease and hold `.lease`; judgment reads
# and answers. design §7 item 4: the roles that may not write to the tree.
EXECUTOR_ROLES = ("worker", "reviewer", "scrutiny", "behavior")
READ_ONLY_ROLES = ("reviewer", "judgment")

EXIT_CODES = {
    "done": 0, "error": 1, "preflight-failed": 2, "limit-reached": 3, "budget": 4,
    "gate-blocked": 5, "authority": 6, "contract": 7, "provider-quota": 8, "interrupted": 130,
}
ALWAYS_HALT = ("budget", "contract", "authority")
# what the repair-round cap's halt asks for (hooks/mission-serial-guard.sh says the same)
REPAIR_CAP_NEEDS = ("root-cause classification (contract ambiguity / implementation defect / inadequate evidence / "
                    "bad brief / environment), then re-plan with /missions:mission-amend -- never a cap raise")


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
    # the pre-push hook carries this token's sha256; the plain value never leaves driver memory
    push_token: str = field(default_factory=lambda: secrets.token_hex(16))

    @property
    def slug(self) -> str:
        return self.mission_dir.name

    @property
    def session(self) -> str:
        return "driver:" + self.run_id


# ---------------------------------------------------------------- stop

def stop(ctx: Context, reason: str, detail: str = "", needs: str = "", halt: bool = False,
         resume_next: Optional[str] = None, phase: Optional[str] = None) -> int:
    """Journal `stop`, set `resume_next` and the phase, and return the exit code the reason maps
    to (design §6.4). A halt journals the block-class `halt` with what the human must decide and
    writes `halted`; any other stop leaves the phase where the caller put it unless `phase` names
    one -- `done` included, so the last milestone's close can leave `validating` for the operator
    who runs the terminal steps to see where the mission stands."""
    code = EXIT_CODES[reason]
    mdir = ctx.mission_dir
    if reason in ALWAYS_HALT:
        halt = True
    try:
        if halt:
            journal.append(mdir, "halt", **{"class": "block"}, reason=("%s: %s" % (reason, detail))[:300],
                           decision_needed=needs or None)
            files.write_state_fields(mdir, phase="halted")
        elif phase:
            files.write_state_fields(mdir, phase=phase)
        files.write_state_fields(mdir, resume_next=(resume_next or ("%s: %s" % (reason, needs or detail)))[:240])
    except files.MissionFileError as e:
        ctx.log("warning: could not update state.md: %s" % e)
    journal.append(mdir, "stop", reason=reason, detail=detail or None, needs=needs or None, exit=code,
                   run_id=ctx.run_id)
    ctx.log("-- stopped: %s%s" % (reason, (" -- " + detail) if detail else ""))
    if needs:
        ctx.log("   next: " + needs)
    return code


# ---------------------------------------------------------------- requests

def role_cfg(cfg: Dict, role: str) -> Dict:
    return dict((cfg.get("roles") or {}).get(role) or {})


def build_request(ctx: Context, feature: Optional[files.Feature], task: str, run_dir: Path, meta: Dict,
                  phase: str, role: str = "worker", step: str = "") -> RunRequest:
    """Everything the adapter needs for one run of `role`, from the seat, driver.json and the
    agent definition, in that order of precedence. `feature` is the worker's (its Seat, its
    Files) or the review's subject; None for a milestone- or issue-scoped run. Writes nothing."""
    rc = role_cfg(ctx.cfg, role)
    # Seats (features.md for the worker, mission.md's Reviewer seat for the reviewer) and the agent
    # frontmatter speak Claude's model vocabulary (sonnet, opus, fable, claude-...); they bind only
    # under the claude harness. driver.json's role model is the operator's choice for whichever
    # harness is configured and always applies.
    claude_vocab = ctx.harness == "claude"
    seat = None
    if claude_vocab and role == "worker" and feature is not None:
        seat = feature.seat
    elif claude_vocab and role == "reviewer":
        seat = files.read_reviewer_seat(ctx.mission_dir)
    model = seat or rc.get("model") or (meta.get("model") if claude_vocab else None) or None
    effort = rc.get("effort") or (meta.get("effort") if claude_vocab else None) or None
    defaults = ROLE_DEFAULTS[role]
    timeout = int(rc.get("timeout_s", defaults["timeout_s"]))
    budget = rc.get("budget_usd", defaults["budget_usd"])
    budget = float(budget) if budget is not None else None
    branch = ctx.cfg.get("branch") or files.read_state(ctx.mission_dir).branch
    fid = feature.id if feature is not None else ""
    env = base.build_env(ctx.mission_dir, run_dir, role, fid, task, phase, ctx.harness,
                         branch=branch, feature_files=feature.files if feature is not None else (),
                         passthrough=list((ctx.cfg.get("env") or {}).get("passthrough") or []))
    return RunRequest(
        role=role, task=task, prompt_path=run_dir / "prompt.md", cwd=ctx.checkout, env=env,
        timeout_s=timeout, budget_usd=budget, model=model, effort=effort, read_only=role in READ_ONLY_ROLES,
        output_path=run_dir / "output.md", system_path=run_dir / "system.md", run_dir=run_dir,
        tools=list(meta.get("tools") or []), feature=fid, mission_dir=ctx.mission_dir, step=step)


def design_for(mission_dir: Path, feature: files.Feature) -> Tuple[str, List[str]]:
    """The feature's design section -- or, for a repair feature without one, its first origin
    feature's: a repair re-enters the code its origin wrote and is bound by the same guidelines."""
    section, rows = files.design_section(mission_dir, feature.id)
    if not section and not rows and feature.repairs:
        return files.design_section(mission_dir, feature.repairs[0])
    return section, rows


def _write_outcome(run_dir: Path, outcome: Outcome, grade: Optional[Grade]) -> None:
    doc = outcome.to_json()
    if grade is not None:
        doc["grade"] = grade.to_json()
    (run_dir / "outcome.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")


def plugin_script(ctx: Context, name: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of scripts/ the way the hooks run it: CLAUDE_PLUGIN_ROOT set, the checkout as cwd."""
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(ctx.plugin)
    return subprocess.run(["bash", str(ctx.plugin / "scripts" / name)] + list(args), cwd=str(ctx.checkout),
                          capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")


def materialise_patch(ctx: Context, fid: str, base: str, head: str, paths: List[str]) -> Optional[str]:
    """`mission-patch.sh` for the blind reviewer's packet: the path it wrote, or None with a `note`
    journaled. The reviewer then gets no diff and answers `cannot tell` -- never a git command of
    its own, which would show it the whole branch and the author's commit bodies."""
    res = plugin_script(ctx, "mission-patch.sh", str(ctx.mission_dir), fid, base, head, "--", *paths)
    if res.returncode == 0:
        out = res.stdout.strip()
        return out.splitlines()[-1] if out else str(ctx.mission_dir / "patches" / (fid + ".patch"))
    journal.append(ctx.mission_dir, "note", feature=fid, text="mission-patch.sh exited %d for %s..%s: %s" % (
        res.returncode, base[:7], head[:7], (res.stderr.strip().splitlines() or ["no output"])[-1][:200]))
    return None


def _log_dispatch(ctx: Context, req: RunRequest) -> None:
    ctx.log(">> %s  %s%s  timeout %ds%s%s  in %s" % (
        req.task, ctx.harness, (" " + req.model) if req.model else "", req.timeout_s,
        (" budget $%g" % req.budget_usd) if req.budget_usd is not None else "",
        "  read-only" if req.read_only else "", ctx.checkout))


def _journal_return(ctx: Context, agent: str, task: str, outcome: Outcome, feature: Optional[str] = None) -> None:
    """agent_return, cost, session_cost, orphans_killed -- one writer for every role, so the
    shapes scripts/mission-spend.sh and journal-metrics.sh read never differ by role."""
    mdir = ctx.mission_dir
    if outcome.timed_out:
        status = "timed_out"
    elif outcome.killed_by:
        status = "killed"
    else:
        status = "completed" if outcome.rc == 0 else "error"
    journal.append(mdir, "agent_return", agent=agent, feature=feature, dispatch_id=task,
                   duration_s=int(round(outcome.elapsed_s)), status=status, rc=outcome.rc,
                   model=outcome.model, task=task, killed_by=outcome.killed_by)
    journal.append(mdir, "cost", task=task, unit=outcome.cost.get("unit"), value=outcome.cost.get("value"),
                   source=outcome.cost.get("source"), harness=outcome.harness, model=outcome.model)
    if outcome.cost.get("unit") == "usd" and isinstance(outcome.cost.get("value"), (int, float)):
        ctx.usd_this_run += float(outcome.cost["value"])
        journal.append(mdir, "session_cost", session_id=ctx.session, usd=round(ctx.usd_this_run, 4))
    if outcome.orphans_killed:
        journal.append(mdir, "orphans_killed", task=task,
                       text="processes were still alive in the run's process group after it exited")


# ---------------------------------------------------------------- worker

def step_worker(ctx: Context, feature: files.Feature, state: files.State) -> Tuple[str, Outcome, Grade]:
    mdir = ctx.mission_dir
    fid = feature.id
    task = "%s#%d" % (fid, journal.attempts(mdir, fid) + 1)
    run_dir = mdir / "runs" / task
    run_dir.mkdir(parents=True, exist_ok=True)
    (mdir / "handoffs").mkdir(exist_ok=True)

    # render: system part from the agent definition, user part from the dispatch template
    meta, system = prompts.system_prompt(ctx.plugin, "worker")
    digest_text = prompts.digest(mdir, ctx.plugin)
    assertions = [a for a in files.read_contract(mdir) if a.id in feature.assertions]
    design = design_for(mdir, feature)
    rejection = journal.last_rejection(mdir, fid)
    inherited = files.dirty_paths(ctx.checkout)
    user = prompts.worker_prompt(mdir, feature, digest_text, assertions, design, ctx.plugin, rejection,
                                 inherited=inherited)
    files.write_text(run_dir / "prompt.md", user)
    files.write_text(run_dir / "system.md", system)
    req = build_request(ctx, feature, task, run_dir, meta, state.phase)
    prep.prepare(ctx, req)

    # launch: nothing is graded here -- a launch grades nothing. What is recorded is the identity
    # the grade will be keyed to: HEAD and the handoff's content as they were when this task began.
    head_before = files.git_out(ctx.checkout, "rev-parse", "HEAD")
    handoff_before = files.fingerprint(files.handoff_path(mdir, fid))
    req.watchdog = watchdog.Watchdog(mdir, ctx.checkout, fid, task, head_before, handoff_before, run_dir,
                                     log=ctx.log, **watchdog.config(ctx.cfg))
    with prep.host_lease(ctx, task):
        files.set_feature(mdir, fid, status="active")
        files.write_lock(mdir / ".writer", "mission-worker", fid, task, ctx.session)
        files.write_lock(mdir / ".lease", "mission-worker", fid, task, ctx.session)
        journal.append(mdir, "dispatch", agent="mission-worker", **{"class": "writer"}, model=req.model,
                       feature=fid, dispatch_id=task, session_id=ctx.session, task=task, harness=ctx.harness)
        _log_dispatch(ctx, req)
        try:
            outcome = ctx.adapter.run(req)            # BLOCKS until the process is gone
        finally:
            for name, event in ((".writer", "writer_lock_cleared"), (".lease", "lease_released")):
                line = files.remove_lock(mdir / name)
                if line is not None:
                    journal.append(mdir, event, reason="returned", lock=line)

    _journal_return(ctx, "mission-worker", task, outcome, feature=fid)
    _write_outcome(run_dir, outcome, None)

    # grade after exit, exactly once, keyed to this task; commits count on the mission branch's ref
    branch = ctx.cfg.get("branch") or state.branch
    grade = grade_feature(mdir, fid, ctx.checkout, ctx.plugin, head_before, handoff_before,
                          feature.assertions, task=task, outcome=outcome, branch=branch, feature_files=feature.files)
    cls = classify(outcome, grade)
    if cls == "handoff_missing":
        # the work is on the branch, the record is not: write the record from the work, then grade
        # it like any other. A run that ended on its own terms (a clean exit, or the watchdog ending
        # a worker that went idle after its commit) is recorded complete; one cut off by its
        # deadline or a crash is recorded partial, and comes back as a re-dispatch, not as done.
        commit = grade.new_commit or ""
        finished = outcome.killed_by == watchdog.COMMIT_NO_HANDOFF or (not outcome.killed and outcome.rc == 0)
        how = ("was ended by the driver (%s)" % outcome.killed_by) if outcome.killed else ("exited %d" % outcome.rc)
        reconstruct(mdir, fid, ctx.checkout, head_before, commit, task, feature.assertions, how, finished=finished)
        journal.append(mdir, "handoff_reconstructed", task=task, feature=fid, commit=commit[:7],
                       status="complete" if finished else "partial", killed_by=outcome.killed_by, rc=outcome.rc)
        ctx.log("   %s: commit %s landed without a handoff -- reconstructed it from the commit as %s" % (
            task, commit[:7], "complete" if finished else "partial"))
        grade = grade_feature(mdir, fid, ctx.checkout, ctx.plugin, head_before, handoff_before,
                              feature.assertions, task=task, outcome=outcome, branch=branch, feature_files=feature.files)
        grade.reconstructed = True
        cls = classify(outcome, grade)
    outcome.cls = cls
    _write_outcome(run_dir, outcome, grade)
    problems = list(grade.problems)
    if cls == "no_op":
        problems.append("the run exited %d but produced no commit and no handoff" % outcome.rc)
    if cls == "infra_quota":
        problems.append("the harness reported a quota or rate limit: %s" % grade.quota)
    if cls == "tests_failed":
        problems.append("the handoff reports status %s%s" % (
            grade.status, (": " + "; ".join(grade.undone[:3])) if grade.undone else " and says nothing under Left undone"))
    journal.append(mdir, "step_done", step=task, feature=fid, cls=cls, elapsed_s=round(outcome.elapsed_s, 1),
                   rc=outcome.rc, problems=problems or None, task=task, killed_by=outcome.killed_by,
                   reconstructed=True if grade.reconstructed else None)
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


# ---------------------------------------------------------------- the other roles

def validation_path(mission_dir: Path, name: str) -> Path:
    return mission_dir / "validation" / name


def write_validation(mission_dir: Path, name: str, task: str, validator: str, model: str, text: str) -> Path:
    """`validation/<name>`: the agent's final message under the two-line header
    `<!-- <task> · <validator> · <model or harness> · <ts> -->` that says where it came from."""
    path = validation_path(mission_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    files.write_text(path, "<!-- %s · %s · %s · %s -->\n\n%s" % (
        task, validator, model, journal.now_iso(), text.rstrip("\n") + "\n"))
    return path


def run_role(ctx: Context, role: str, step: str, task: str, prompt: str,
             feature: Optional[files.Feature] = None, milestone: str = "",
             validation_file: Optional[str] = None) -> Tuple[Outcome, str]:
    """One run of a non-worker role, blocking: (the outcome, its cls set to ok | error |
    no_output; the agent's final message). Executors (reviewer, scrutiny, behavior) run under the
    host lease and hold `.lease`; the reviewer runs inside prep.blind, so a caller must not wrap
    it again (a nested blind cell is mode 000 and cannot be entered). Judgment runs take neither:
    they read and answer. `validation_file` names the file under validation/ that gets the
    message with its header once the run is over and the blind window closed -- only for an `ok`
    run, so a retried step never leaves a half-answer behind. Nothing here parses the message."""
    mdir = ctx.mission_dir
    agent = prompts.AGENTS[role]
    run_dir = mdir / "runs" / task
    run_dir.mkdir(parents=True, exist_ok=True)
    meta, system = prompts.system_prompt(ctx.plugin, role)
    files.write_text(run_dir / "prompt.md", prompt)
    files.write_text(run_dir / "system.md", system)
    req = build_request(ctx, feature, task, run_dir, meta, files.read_state(mdir).phase, role=role, step=step)
    prep.prepare(ctx, req)
    executor = role in EXECUTOR_ROLES
    fid = feature.id if feature is not None else ""
    lease = prep.host_lease(ctx, task) if executor else contextlib.nullcontext()
    blind = prep.blind(ctx, task) if role == "reviewer" else contextlib.nullcontext()
    with lease:
        if executor:
            # the lease line names what runs, as the hooks' own would: the feature, else the milestone
            files.write_lock(mdir / ".lease", agent, fid or milestone, task, ctx.session)
        journal.append(mdir, "dispatch", agent=agent, **{"class": "executor" if executor else "static"},
                       model=req.model, feature=fid or None, milestone=milestone or None, dispatch_id=task,
                       session_id=ctx.session, task=task, harness=ctx.harness, step=step)
        _log_dispatch(ctx, req)
        try:
            with blind:
                outcome = ctx.adapter.run(req)        # BLOCKS until the process is gone
        finally:
            if executor:
                line = files.remove_lock(mdir / ".lease")
                if line is not None:
                    journal.append(mdir, "lease_released", reason="returned", lock=line)
    _journal_return(ctx, agent, task, outcome, feature=fid or None)
    text = base.read_output(req.output_path)
    if outcome.killed or outcome.rc != 0:
        cls = "error"
    elif not text.strip():
        cls = "no_output"
    else:
        cls = "ok"
    outcome.cls = cls
    _write_outcome(run_dir, outcome, None)
    # no `feature` on this record: journal.last_rejection reads a feature's latest step_done as the
    # worker's verdict, and a review of the feature is not one
    journal.append(mdir, "step_done", step=task, role=role, cls=cls, elapsed_s=round(outcome.elapsed_s, 1),
                   rc=outcome.rc, task=task, killed_by=outcome.killed_by, milestone=milestone or None)
    ctx.log("%s %s  %s  rc=%d  %.1fs  cost=%s" % (
        "ok" if cls == "ok" else "xx", task, cls, outcome.rc, outcome.elapsed_s, _fmt_cost(outcome.cost)))
    if outcome.detail:
        ctx.log("   harness: " + outcome.detail[:200])
    if validation_file and cls == "ok":
        write_validation(mdir, validation_file, task, agent, outcome.model or req.model or ctx.harness, text)
    return outcome, text


# ---------------------------------------------------------------- judgment

def run_judgment(ctx: Context, step: str, prefix: str, prompt: str,
                 validate: Callable[[Dict], List[str]], milestone: str = "") -> Union[int, Tuple[str, Dict]]:
    """A judgment run whose reply must be one JSON object that `validate` (the step's schema check:
    problems, or none) accepts: (task, object) -- or the exit code of stop("error") after the one
    re-run allowed. The re-run's prompt is the same prompt with the previous task's error appended
    (a parse error, the schema's problems, or how the run ended), so the model answers the
    complaint rather than the question again from scratch. Tasks are `<prefix>#<n>`, n counted
    over the whole journal."""
    mdir = ctx.mission_dir
    error = ""
    prev = ""
    for _ in range(2):
        task = "%s#%d" % (prefix, journal.task_attempts(mdir, prefix) + 1)
        text = prompt
        if error:
            text = prompt.rstrip("\n") + "\n\nYour previous reply (%s) could not be applied: %s\n%s\n" % (
                prev, error, prompts.ANSWER_LINE)
        outcome, reply = run_role(ctx, "judgment", step, task, text, milestone=milestone)
        if outcome.cls != "ok":
            how = ("was ended by the driver (%s)" % outcome.killed_by) if outcome.killed else "exited %d" % outcome.rc
            error = "the run %s%s" % (how, " with no reply" if not reply.strip() else "")
        else:
            try:
                obj = judgment.extract_json(reply)
            except judgment.JudgmentError as e:
                error = str(e)
            else:
                problems = validate(obj)
                if not problems:
                    return task, obj
                error = "; ".join(problems)
        journal.append(mdir, "note", task=task, step=step, text="%s: reply rejected -- %s" % (task, error[:300]))
        ctx.log("   %s: reply rejected -- %s" % (task, error[:160]))
        prev = task
    return stop(ctx, "error", detail="%s: two replies could not be applied; last: %s" % (step, error[:200]),
                needs="look at runs/%s/output.md and stderr; fix the prompt or the model, then missions run again" % prev)


def repair_cap_problem(mission_dir: Path, assertions: List[str], cap: int) -> Optional[str]:
    """The serial guard's rule (hooks/mission-serial-guard.sh): an assertion that already has `cap`
    repair features gets no more. The text of the block, or None."""
    by_assertion: Dict[str, set] = {}
    for fu in files.read_followups(mission_dir):
        if fu.assertion and fu.repair_as:
            by_assertion.setdefault(fu.assertion, set()).add(fu.repair_as)
    for aid in assertions:
        have = by_assertion.get(aid, set())
        if len(have) >= cap:
            return ("repair-round cap (%d per assertion) exceeded by a repair of %s, which already has %d repair "
                    "feature(s) (%s) -- a third repair for the same assertion means the diagnosis is wrong, not the code"
                    % (cap, aid, len(have), ", ".join(sorted(have))))
    return None


def register(ctx: Context, milestone: str, followups: List[Dict], repairs: List[Dict]) -> Union[int, Tuple[List[str], List[str]]]:
    """Apply what a judgment step proposed to the registry: `followups` in files.append_followups'
    shape (source set, disposition repair | accept | waive, the cluster), `repairs` as {cluster,
    title, assertions, files, procedures, out_of_scope, origins: [F0nn]}. One cluster, one repair
    feature: every follow-up of a repaired cluster is dispositioned `repair as` that feature, and
    the feature's Repairs line names them -- so the ids are computed before anything is written.
    Each assertion a repair claims is routed to it in contract.md (check.sh's coverage rules). The
    repair-round cap is checked first: over it, nothing is written and the mission halts. Returns
    (follow-up ids, feature ids), or the stop's exit code."""
    mdir = ctx.mission_dir
    cap = int(files.read_budget(mdir).get("repair_rounds") or 2)
    for r in repairs:
        over = repair_cap_problem(mdir, list(r.get("assertions") or []), cap)
        if over:
            return stop(ctx, "gate-blocked", halt=True, detail=over, needs=REPAIR_CAP_NEEDS)
    first_fu = int(files.next_followup_id(mdir)[2:])
    first_f = int(files.next_feature_id(mdir)[1:])
    feature_of = {r["cluster"]: "F%03d" % (first_f + i) for i, r in enumerate(repairs)}
    for fu in followups:
        if fu.get("disposition") == "repair":
            fu["repair_as"] = feature_of[fu["cluster"]]   # the validators paired every repair finding with a repair
    fu_ids = files.append_followups(mdir, followups)
    if fu_ids and fu_ids[0] != "FU%03d" % first_fu:
        raise files.MissionFileError("followups.md changed under the driver: expected FU%03d, wrote %s" % (first_fu, fu_ids[0]))
    fids: List[str] = []
    for r in repairs:
        mine = [fid for fid, fu in zip(fu_ids, followups) if fu.get("cluster") == r["cluster"] and fu.get("disposition") == "repair"]
        line = "%s (%s) of %s" % (r["cluster"], ", ".join(mine) or "—", ", ".join(r.get("origins") or []) or "—")
        fid = files.append_feature(mdir, milestone, r["title"], list(r["assertions"]), list(r["files"]),
                                   r.get("procedures") or "", r.get("out_of_scope") or "", line)
        if fid != feature_of[r["cluster"]]:
            raise files.MissionFileError("features.md changed under the driver: expected %s, wrote %s" % (feature_of[r["cluster"]], fid))
        for aid in r["assertions"]:
            files.route_assertion(mdir, aid, fid)
        fids.append(fid)
    if fu_ids:
        journal.append(mdir, "followups_added", ids=fu_ids, milestone=milestone)
    if fids:
        journal.append(mdir, "features_added", ids=fids, milestone=milestone)
    return fu_ids, fids


# ---------------------------------------------------------------- triage

_ISSUE_ORIGIN = re.compile(r"^(F\d{3}) handoff:\s*")


def _handoffs_for(mission_dir: Path, issues: List[str]) -> Dict[str, str]:
    """The handoffs that raised the open issues, by feature id: the `F0nn handoff:` prefix the
    ingest wrote, else any handoff whose text carries the issue's words."""
    out: Dict[str, str] = {}
    hdir = mission_dir / "handoffs"
    if not hdir.is_dir():
        return out
    for path in sorted(hdir.glob("F*.md")):
        raw = files.read_text(path)
        for text in issues:
            tail = _ISSUE_ORIGIN.sub("", text).strip()
            if text.startswith(path.stem + " handoff:") or (tail and tail in raw):
                out[path.stem] = raw
                break
    return out


def _index_problems(obj: Dict, count: int) -> List[str]:
    """`issue` must be one of the indexes the prompt printed -- the applier's half of the schema."""
    out: List[str] = []
    for i, r in enumerate(obj.get("resolutions") or []):
        n = r.get("issue") if isinstance(r, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and n > count:
            out.append("resolutions[%d]: issue %d does not exist; the prompt listed %d issue(s)" % (i, n, count))
    return out


def apply_triage(ctx: Context, state: files.State, issues: List[str], task: str, obj: Dict) -> Optional[int]:
    """Apply a triage reply to the mission files. `resolved`: the bullet goes, a `decision` says
    why. `defer`: the bullet goes and a follow-up records it, accepted as a known limitation until
    someone re-plans. `repair`: the follow-up plus a repair feature in the current milestone,
    routed to the assertions it claims. `escalate`: nothing changes for that issue and the mission
    halts on its why. What can be applied is applied first, so a halt leaves the registry telling
    the truth about the rest; an issue the reply skipped halts the mission too, named. None when
    the loop may go on."""
    mdir = ctx.mission_dir
    by_index = {r["issue"]: r for r in obj["resolutions"]}
    cleared: List[str] = []
    escalated: List[Tuple[str, str]] = []
    skipped: List[str] = []
    followups: List[Dict] = []
    repairs: List[Dict] = []
    counts = {"resolved": 0, "deferred": 0, "repaired": 0}
    for i, text in enumerate(issues, 1):
        r = by_index.get(i)
        if r is None:
            skipped.append(text)
            continue
        d = r["disposition"]
        why = (r.get("why") or "").strip()
        if d == "escalate":
            escalated.append((text, why))
            continue
        if d == "resolved":
            journal.append(mdir, "decision", step="triage", task=task, what="open issue resolved: %s" % text[:200], why=why or None)
            counts["resolved"] += 1
        else:
            m = _ISSUE_ORIGIN.match(text)
            origin = m.group(1) if m else ""
            fu = r["followup"]
            followups.append({
                "title": fu["title"], "source": "%s-triage" % state.milestone, "assertion": fu.get("assertion"),
                "found_by": ("%s handoff" % origin) if origin else "a handoff", "where": _ISSUE_ORIGIN.sub("", text).strip(),
                "severity": fu.get("severity") or "low", "cluster": fu["cluster"], "cluster_label": fu.get("cluster_label") or "",
                "blocking": bool(fu.get("blocking")), "disposition": "repair" if d == "repair" else "accept",
                "why": why if d == "repair" else "deferred by the triage step" + ((": " + why) if why else "")})
            if d == "repair":
                rp = r["repair"]
                same = next((x for x in repairs if x["cluster"] == fu["cluster"]), None)
                if same is None:
                    repairs.append({"cluster": fu["cluster"], "title": rp["title"], "assertions": list(rp["assertions"]),
                                    "files": list(rp["files"]), "procedures": rp.get("procedures") or "",
                                    "out_of_scope": "", "origins": [origin] if origin else []})
                else:
                    # one cluster, one repair feature: a second issue of the same cluster joins it
                    same["assertions"] += [a for a in rp["assertions"] if a not in same["assertions"]]
                    same["files"] += [f for f in rp["files"] if f not in same["files"]]
                    if origin and origin not in same["origins"]:
                        same["origins"].append(origin)
                counts["repaired"] += 1
            else:
                counts["deferred"] += 1
        cleared.append(text)
    fu_ids: List[str] = []
    fids: List[str] = []
    if followups:
        r = register(ctx, state.milestone, followups, repairs)
        if isinstance(r, int):
            return r
        fu_ids, fids = r
    if cleared:
        files.remove_open_issues(mdir, cleared)
    summary = "%d issue(s): %d resolved, %d deferred, %d repaired%s%s%s%s" % (
        len(issues), counts["resolved"], counts["deferred"], counts["repaired"],
        (" (%s)" % ", ".join(fu_ids)) if fu_ids else "", (", repair feature %s" % ", ".join(fids)) if fids else "",
        (", %d escalated" % len(escalated)) if escalated else "", (", %d left unresolved" % len(skipped)) if skipped else "")
    journal.append(mdir, "judgment", step="triage", task=task, milestone=state.milestone, summary=summary)
    ctx.log("   triage: " + summary)
    if escalated:
        text, why = escalated[0]
        return stop(ctx, "gate-blocked", halt=True,
                    detail="triage escalates %d issue(s): %s -- %s" % (len(escalated), why or "no reason given", text[:160]),
                    needs="decide it; then clear the issue from state.md's open issues (or defer it to followups.md) and set phase implementing")
    if skipped:
        return stop(ctx, "gate-blocked", halt=True,
                    detail="triage left %d issue(s) without a resolution: %s" % (len(skipped), skipped[0][:160]),
                    needs="resolve or defer it yourself, clear it from state.md's open issues, and set phase implementing")
    return None


def step_triage(ctx: Context, state: files.State) -> Optional[int]:
    """The open issues a handoff raised go through one judgment run and its application. None when
    the loop may go on; else the exit code of the stop that ended it."""
    mdir = ctx.mission_dir
    issues = list(state.open_issues)
    followups_path = mdir / "followups.md"
    prompt = prompts.triage_prompt(mdir, issues, _handoffs_for(mdir, issues),
                                   files.read_text(followups_path) if followups_path.exists() else "",
                                   prompts.skill_section(ctx.plugin, "Halts"))
    ctx.log("triage: %d open issue(s) before the next feature" % len(issues))
    r = run_judgment(ctx, "triage", "triage", prompt,
                     lambda obj: judgment.validate_triage(obj) + _index_problems(obj, len(issues)),
                     milestone=state.milestone)
    if isinstance(r, int):
        return r
    task, obj = r
    return apply_triage(ctx, state, issues, task, obj)


# ---------------------------------------------------------------- ingest

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


def ingest(ctx: Context, feature: files.Feature, grade: Grade) -> Dict:
    mdir = ctx.mission_dir
    fid = feature.id
    head = grade.sha or ""
    base_sha, how = _base_for(ctx, head)
    rng = "%s..%s" % (base_sha, head)
    patch = materialise_patch(ctx, fid, base_sha, head, feature.files)

    files.set_feature(mdir, fid, status="done", commit=head, rng=rng)
    # what the handoff claims, within the feature's assertions -- never the feature's list wholesale
    claimed = files.claim_assertions(mdir, [a for a in grade.claimed if a in feature.assertions])
    if grade.issues:
        files.add_open_issues(mdir, ["%s handoff: %s" % (fid, i) for i in grade.issues])
    if grade.reconstructed:
        journal.append(mdir, "note", feature=fid, task=grade.task,
                       text="%s's handoff was reconstructed by the driver from commit %s; its claims carry no "
                            "worker test evidence -- the VALIDATE reviewers must run the procedures themselves" % (fid, head[:7]))
    _refresh_index(ctx)

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
                         claimed=claimed or None, patch=patch, base_from=how, task=grade.task or None,
                         reconstructed=True if grade.reconstructed else None)
    ctx.log("   ingested %s: range %s..%s (%s)%s%s%s" % (
        fid, base_sha[:7], head[:7], how, (", claimed " + ", ".join(claimed)) if claimed else "",
        (", %d issue(s) -> open issues" % len(grade.issues)) if grade.issues else "",
        " -- RECONSTRUCTED handoff" if grade.reconstructed else ""))
    return rec


def _refresh_index(ctx: Context) -> None:
    """`graphify update .` after every handoff when state.md's intelligence line names graphify
    (SKILL.md "Ingesting a handoff"): AST-only, no LLM. Best effort; a failure is a note."""
    line = files.intelligence_line(ctx.mission_dir)
    if "graphify=" not in line or "graphify=none" in line:
        return
    try:
        res = subprocess.run(["graphify", "update", "."], cwd=str(ctx.checkout), capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        journal.append(ctx.mission_dir, "note", text="graphify update failed: %s" % str(e)[:200])
        return
    if res.returncode != 0:
        tail = (res.stderr.strip().splitlines() or res.stdout.strip().splitlines() or ["no output"])[-1]
        journal.append(ctx.mission_dir, "note", text="graphify update exited %d: %s" % (res.returncode, tail[:200]))
    else:
        ctx.log("   graphify update: ok")
