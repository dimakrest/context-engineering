"""VALIDATE (design §6.2) and the milestone close: scrutiny -> a blind review per feature ->
behavior (only when the milestone has interface/conversational assertions) -> negotiate ->
proven marks -> converge -> archive -> the next milestone. One round per call: the loop calls
again once the repairs a round scheduled are done.

Every validator is an executor run through steps.run_role -- the host lease, `.lease`, the
reviewer's blind window -- and its final message lands under validation/ with a header naming
the task that wrote it. What the message says is parsed here (verdicts.py) and journaled as
`verdict`; the journal, not the file, is what the proven rule and the negotiate prompt read, so a
driver re-entering an interrupted round never re-parses a verdict it recorded and never re-runs
a step whose file exists. A validator that crashes or answers nothing runs once more, then the
run stops with `error` and the round stays open for the next driver.

proven is mechanical and written only from validator verdicts (design §6.1): structural from the
latest reviewer verdict, interface and conversational from the latest behavior verdict -- never
from a handoff, never by the negotiate step, which proposes follow-ups and repair features
(steps.register applies them) or says the contract is wrong, and marks nothing. An assertion a
repair was just scheduled for is not marked either, whatever a later review of another feature
said about it: the repair round's verdict proves it.

Not here: the pr phase (#10). The last milestone's close stops with `done` and names the
terminal steps for a human; the phase is left at `validating` so the operator sees where the
mission stands.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from . import files, journal, judgment, prompts, steps, verdicts
from .steps import Context, stop

TAGGED = ("interface", "conversational")     # the classes only a behavior run can prove


# ---------------------------------------------------------------- the pieces

def file_name(milestone: str, step: str, round_no: int, feature: Optional[str] = None) -> str:
    """`M1-scrutiny.md`, `M1-review-F001.md`, `M1-behavior.md`; round k >= 2 appends `-r<k>`, so
    a repair round never overwrites the file that found the defect."""
    stem = "%s-%s" % (milestone, ("review-" + feature) if step == "reviewer" else step)
    if round_no >= 2:
        stem += "-r%d" % round_no
    return stem + ".md"


def milestone_assertions(mission_dir: Path, milestone: str,
                         feats: Optional[List[files.Feature]] = None) -> List[files.Assertion]:
    """The contract rows routed to a feature of the milestone, in contract order -- repair
    features included once they are routed."""
    feats = feats if feats is not None else files.read_features(mission_dir)
    ids = {f.id for f in feats if f.milestone == milestone}
    return [a for a in files.read_contract(mission_dir) if any(f in ids for f in a.features)]


def _latest_for(latest: Dict[str, Dict[str, Tuple[str, str]]], a: files.Assertion) -> Optional[Tuple[str, str]]:
    """The latest (verdict, file) on the assertion from the validator that can prove its class
    (verdicts.latest_verdicts' buckets); None when that validator never graded it."""
    return latest["behavior" if a.proof_class in TAGGED else "reviews"].get(a.id)


def proven_evidence(mission_dir: Path, milestone: str) -> Dict[str, str]:
    """The proven rule: {assertion: validation file} for every assertion of the milestone whose
    latest verdict proves it -- structural when the reviewer's is `satisfied`, interface and
    conversational when the behavior validator's is `proven`. A reviewer's `satisfied` on an
    interface assertion proves nothing: a diff cannot show a chip on a dashboard. Writes nothing."""
    latest = verdicts.latest_verdicts(mission_dir, milestone)
    out: Dict[str, str] = {}
    for a in milestone_assertions(mission_dir, milestone):
        got = _latest_for(latest, a)
        if got and got[0] == ("proven" if a.proof_class in TAGGED else "satisfied"):
            out[a.id] = got[1]
    return out


def verdict_of(latest: Dict[str, Dict[str, Tuple[str, str]]], a: files.Assertion) -> str:
    """The latest verdict on one assertion from the validator that can prove its class, in that
    validator's own word; `no verdict` when none was journaled."""
    got = _latest_for(latest, a)
    return got[0] if got else "no verdict"


def verdict_summary(mission_dir: Path, milestone: str, assertions: List[files.Assertion]) -> str:
    """One line per assertion for the negotiate prompt: the latest verdict from every validator
    that graded it -- per reviewed feature, since a review is per feature and two reviews of one
    assertion may disagree -- and the contract's current status."""
    latest: Dict[str, Dict[str, Tuple[str, str]]] = {a.id: {} for a in assertions}
    for role, rec in verdicts.assertion_verdicts(mission_dir, milestone):
        who = role + ((" " + str(rec["feature"])) if rec.get("feature") else "")
        for aid, v in rec["assertions"].items():
            if aid in latest:
                latest[aid][who] = (str(v), str(rec.get("file") or ""))
    lines: List[str] = []
    for a in assertions:
        parts = ["%s: %s (%s)" % (who, v, f or "no file") for who, (v, f) in latest[a.id].items()]
        lines.append("%s [%s] \u2014 %s \u2014 contract: %s" % (
            a.id, a.proof_class or "?", "; ".join(parts) or "no verdict", a.status))
    return "\n".join(lines)


def _round(mission_dir: Path, milestone: str) -> Tuple[int, bool]:
    """(round number, resumed). A validate_start without its validate_done is an interrupted
    round: it is resumed under its own number, not restarted -- a crash between two validators
    must not cost the validators that already answered."""
    started = done = 0
    for rec in journal.events(mission_dir):      # one pass: the journal grows for the mission's life
        if rec.get("milestone") == milestone:
            started += rec.get("event") == "validate_start"
            done += rec.get("event") == "validate_done"
    if started > done:
        return started, True
    return started + 1, False


def _done_steps(mission_dir: Path, milestone: str, round_no: int) -> Dict[Tuple[str, str], Dict]:
    """The validate_step records of this round whose file still exists: (step, feature) ->
    record. A step whose file went missing is run again; its verdict in the journal stands."""
    out: Dict[Tuple[str, str], Dict] = {}
    for rec in journal.events(mission_dir):
        if rec.get("event") == "validate_step" and rec.get("milestone") == milestone and rec.get("round") == round_no:
            f = rec.get("file")
            if f and (mission_dir / str(f)).exists():
                out[(str(rec.get("step")), str(rec.get("feature") or ""))] = rec
    return out


def _round_files(mission_dir: Path, milestone: str, round_no: int) -> Dict[str, str]:
    """The round's validation files in step order (file name -> text), for the negotiate prompt."""
    return {Path(str(rec["file"])).name: files.read_text(mission_dir / str(rec["file"]))
            for rec in _done_steps(mission_dir, milestone, round_no).values()}


def _validator(ctx: Context, role: str, milestone: str, round_no: int, prompt: str,
               feature: Optional[files.Feature] = None) -> Union[int, Tuple[str, str, str]]:
    """One validator step: (task, mission-relative file, text), or the exit code of stop("error")
    after the one retry allowed. A run that crashed, was killed or answered nothing runs once
    more under the next task number; a second such run is the driver's error, not a verdict, and
    the round stays open for the next driver to resume."""
    mdir = ctx.mission_dir
    fid = feature.id if feature is not None else ""
    prefix = "%s-%s" % ("review" if role == "reviewer" else role, fid or milestone)
    name = file_name(milestone, role, round_no, fid or None)
    task = detail = ""
    for attempt in range(2):
        task = "%s#%d" % (prefix, journal.task_attempts(mdir, prefix) + 1)
        outcome, text = steps.run_role(ctx, role, role, task, prompt, feature=feature, milestone=milestone,
                                       validation_file=name)
        if outcome.cls == "ok":
            return task, "validation/" + name, text
        how = ("was ended by the driver (%s)" % outcome.killed_by) if outcome.killed else ("exited %d" % outcome.rc)
        detail = "%s %s%s" % (task, how, " with no report" if not text.strip() else "")
        if attempt == 0:
            ctx.log("   %s -- running it once more" % detail)
    return stop(ctx, "error", detail="%s: two runs ended without a report; last: %s" % (prefix, detail),
                needs="look at runs/%s/stderr; fix the environment or the validator, then missions run again "
                      "(the round resumes where it stopped)" % task)


def _patch_for(ctx: Context, feature: files.Feature) -> Tuple[Optional[Path], str, str]:
    """(the feature's patch or None, base, head). The patch the ingest materialised, else one made
    now from the feature's Range. The shas come from the patch's own header: that file is the only
    diff the reviewer sees, so the prompt names what it says."""
    path = ctx.mission_dir / "patches" / (feature.id + ".patch")
    if not path.exists() and feature.range:
        base, head = feature.range.split("..", 1)
        steps.materialise_patch(ctx, feature.id, base, head, feature.files)
    if not path.exists():
        return None, "", ""
    shas = {"base": "", "head": ""}
    for ln in files.read_text(path).split("\n")[:6]:
        m = re.match(r"^(base|head):\s*([0-9a-f]{7,40})\s*$", ln)
        if m:
            shas[m.group(1)] = m.group(2)
    if not shas["base"] and feature.range:
        shas["base"], shas["head"] = feature.range.split("..", 1)
    return path, shas["base"], shas["head"]


def _review(ctx: Context, milestone: str, round_no: int, feature: files.Feature,
            assertions: List[files.Assertion], intelligence: str) -> Optional[int]:
    """One blind review: the patch, the feature's assertions and its design section go in; a
    `verdict` per assertion comes out, `cannot tell` for any the table does not name. No patch to
    review is a `cannot tell` on every assertion, journaled -- never a review of git."""
    mdir = ctx.mission_dir
    mine = [a for a in assertions if feature.id in a.features]
    patch, base, head = _patch_for(ctx, feature)
    if patch is None:
        journal.append(mdir, "note", feature=feature.id, milestone=milestone,
                       text="no patch for %s (patches/%s.patch is missing and there is no Range to make one from): "
                            "its assertions are `cannot tell` this round" % (feature.id, feature.id))
        journal.append(mdir, "verdict", validator=prompts.AGENTS["reviewer"], feature=feature.id, milestone=milestone,
                       round=round_no, assertions={a.id: "cannot tell" for a in mine}, file="")
        ctx.log("   review %s: no patch -- cannot tell" % feature.id)
        return None
    prompt = prompts.reviewer_prompt(mdir, feature, mine, steps.design_for(mdir, feature), patch, base, head, intelligence)
    r = _validator(ctx, "reviewer", milestone, round_no, prompt, feature=feature)
    if isinstance(r, int):
        return r
    task, rel, text = r
    got = verdicts.parse_reviewer(text)
    table = {a.id: got.get(a.id, "cannot tell") for a in mine}
    journal.append(mdir, "verdict", validator=prompts.AGENTS["reviewer"], feature=feature.id, milestone=milestone,
                   round=round_no, assertions=table, file=rel)
    journal.append(mdir, "validate_step", milestone=milestone, round=round_no, step="reviewer", feature=feature.id,
                   task=task, file=rel)
    ctx.log("   %s: %s" % (task, ", ".join("%s %s" % kv for kv in table.items()) or "no assertions"))
    return None


# ---------------------------------------------------------------- negotiate

def _source(found_by: str, assertion: Optional[str], routes: Dict[str, List[str]]) -> str:
    """`review-F001` | `scrutiny` | `behavior` from a finding's found_by (the validator's name, its
    task or the feature it reviewed) -- the `(from M1-...)` tag the convergence gate attributes
    by. A reviewer finding that names no feature is attributed through its assertion when that
    routes to exactly one feature of the milestone."""
    low = found_by.lower()
    m = re.search(r"F\d{3}", found_by)
    if "review" in low and m:
        return "review-" + m.group(0)
    if "scrutiny" in low:
        return "scrutiny"
    if "behavior" in low:
        return "behavior"
    if "review" in low:
        fids = routes.get(assertion or "", [])
        return ("review-" + fids[0]) if len(fids) == 1 else "review"
    return "negotiate"


def proposals(obj: Dict, milestone: str, mfeats: List[files.Feature],
              assertions: List[files.Assertion]) -> Tuple[List[Dict], List[Dict]]:
    """A negotiate reply's findings and repairs in steps.register's shape. A repair's origins are
    the reviewed features its cluster's findings name, else the milestone features its assertions
    route to -- the `of F001` half of the Repairs line."""
    ids = {f.id for f in mfeats}
    routes = {a.id: [f for f in a.features if f in ids] for a in assertions}
    followups: List[Dict] = []
    origins: Dict[str, List[str]] = {}
    for f in obj["findings"]:
        src = _source(f["found_by"], f.get("assertion"), routes)
        followups.append({
            "title": f["title"], "source": "%s-%s" % (milestone, src), "assertion": f.get("assertion"),
            "found_by": f["found_by"], "where": f.get("where") or "", "severity": f["severity"],
            "cluster": f["cluster"], "cluster_label": f.get("cluster_label") or "", "blocking": bool(f["blocking"]),
            "disposition": f["disposition"], "why": f.get("why") or ""})
        m = re.match(r"review-(F\d{3})$", src)
        if m and f["disposition"] == "repair" and m.group(1) not in origins.setdefault(f["cluster"], []):
            origins[f["cluster"]].append(m.group(1))
    repairs: List[Dict] = []
    for r in obj["repairs"]:
        org = list(origins.get(r["cluster"]) or [])
        if not org:
            for aid in r["assertions"]:
                org += [fid for fid in routes.get(aid, []) if fid not in org]
        repairs.append({"cluster": r["cluster"], "title": r["title"], "assertions": list(r["assertions"]),
                        "files": list(r["files"]), "procedures": r.get("procedures") or "",
                        "out_of_scope": r.get("out_of_scope") or "", "origins": org})
    return followups, repairs


def _negotiate(ctx: Context, milestone: str, round_no: int, assertions: List[files.Assertion],
               mfeats: List[files.Feature]) -> Union[int, List[Dict]]:
    """The negotiate judgment and its application: the round's validation files, the verdict
    summary, the registry and the SKILL's VALIDATE rules go in; follow-ups and repair features
    come out through steps.register. Returns the repairs it applied (proposals' dicts, plus `id`:
    the repair feature), or the exit code of the stop that ended the run: the contract found
    wrong, the repair-round cap -- both close the round as halted first -- or a reply that could
    not be applied twice, which leaves the round open for the next driver to resume."""
    mdir = ctx.mission_dir
    fu_path = mdir / "followups.md"
    prompt = prompts.negotiate_prompt(mdir, milestone, round_no, verdict_summary(mdir, milestone, assertions),
                                      _round_files(mdir, milestone, round_no),
                                      files.read_text(fu_path) if fu_path.exists() else "",
                                      prompts.skill_section(ctx.plugin, "VALIDATE"))
    r = steps.run_judgment(ctx, "negotiate", "negotiate-" + milestone, prompt, judgment.validate_negotiate, milestone=milestone)
    if isinstance(r, int):
        return r
    task, obj = r
    if obj["contract_wrong"]:
        reason = (obj.get("reason") or "").strip()
        journal.append(mdir, "judgment", step="negotiate", task=task, milestone=milestone, round=round_no,
                       summary="contract wrong: " + reason[:200])
        journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="halted")
        return stop(ctx, "contract", detail="%s: the negotiate step finds the contract wrong: %s" % (milestone, reason),
                    needs="/missions:mission-amend")
    followups, repairs = proposals(obj, milestone, mfeats, assertions)
    fu_ids: List[str] = []
    fids: List[str] = []
    if followups or repairs:
        r = steps.register(ctx, milestone, followups, repairs)
        if isinstance(r, str):
            # the cap refused a repair and nothing was written: the round closes as halted before
            # the stop, so the next driver does not resume it
            journal.append(mdir, "judgment", step="negotiate", task=task, milestone=milestone, round=round_no,
                           summary="%d finding(s), %d repair(s) proposed -- refused: %s" % (len(followups), len(repairs), r[:200]))
            journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="halted")
            return stop(ctx, "gate-blocked", halt=True, detail=r, needs=steps.REPAIR_CAP_NEEDS)
        fu_ids, fids = r
    counts = {d: sum(1 for f in followups if f["disposition"] == d) for d in judgment.NEGOTIATE_DISPOSITIONS}
    summary = ("%d finding(s): %d repair, %d accept, %d waive (%s)%s" % (
        len(followups), counts["repair"], counts["accept"], counts["waive"], ", ".join(fu_ids),
        (", repair feature(s) %s" % ", ".join(fids)) if fids else "")) if followups else "no findings; nothing to register"
    journal.append(mdir, "judgment", step="negotiate", task=task, milestone=milestone, round=round_no, summary=summary)
    ctx.log("   negotiate: " + summary)
    return [dict(rp, id=fid) for fid, rp in zip(fids, repairs)]


# ---------------------------------------------------------------- the round

def run_validate(ctx: Context, milestone: str, until: Optional[str] = None) -> Union[int, str]:
    """One validation round of the milestone. Returns "repairs" (repair features were scheduled;
    the loop dispatches them and comes back), "closed" (the milestone closed and the next one is
    current), or the exit code of the stop that ended the run. `until` is the CLI's --until:
    `milestone` stops after the close."""
    mdir = ctx.mission_dir
    cap = files.repair_rounds(mdir)
    round_no, resumed = _round(mdir, milestone)
    if round_no > cap + 1:
        return stop(ctx, "gate-blocked", halt=True,
                    detail="validation round %d of %s exceeds the repair-round cap (%d) plus the first pass -- a third "
                           "repair for the same assertion means the diagnosis is wrong, not the code" % (round_no, milestone, cap),
                    needs=steps.REPAIR_CAP_NEEDS)
    feats = files.read_features(mdir)
    mfeats = [f for f in feats if f.milestone == milestone]
    assertions = milestone_assertions(mdir, milestone, feats)
    if resumed:
        journal.append(mdir, "note", milestone=milestone,
                       text="resuming validation round %d of %s: steps whose file exists are not run again" % (round_no, milestone))
    else:
        journal.append(mdir, "validate_start", milestone=milestone, round=round_no,
                       features=[f.id for f in mfeats], assertions=[a.id for a in assertions])
    files.write_state_fields(mdir, phase="validating",
                             resume_next="validate %s round %d: scrutiny, a blind review per feature, negotiate" % (milestone, round_no))
    ctx.log("validate %s: round %d%s -- %d feature(s), %d assertion(s)" % (
        milestone, round_no, " (resumed)" if resumed else "", len(mfeats), len(assertions)))
    done = _done_steps(mdir, milestone, round_no)
    digest_text = prompts.digest(mdir, ctx.plugin)

    # 1. scrutiny: the deterministic checks, once
    if ("scrutiny", "") in done:
        ctx.log("   scrutiny: %s exists -- skipped" % done[("scrutiny", "")]["file"])
    else:
        r = _validator(ctx, "scrutiny", milestone, round_no, prompts.scrutiny_prompt(mdir, milestone, mfeats, assertions, digest_text))
        if isinstance(r, int):
            return r
        task, rel, text = r
        s = verdicts.parse_scrutiny(text)
        failed = [c["command"] for c in s["commands"] if c["exit"] not in (0, None)]
        journal.append(mdir, "verdict", validator=prompts.AGENTS["scrutiny"], milestone=milestone, round=round_no, file=rel,
                       commands=len(s["commands"]), failed=failed, failures=s["failures"][:300] or None)
        journal.append(mdir, "validate_step", milestone=milestone, round=round_no, step="scrutiny", task=task, file=rel)
        ctx.log("   %s: %d command(s), %d failed" % (task, len(s["commands"]), len(failed)))

    # 2. a blind review per done feature, serial (run_role holds the host lease for each)
    intelligence = files.intelligence_line(mdir)
    for f in mfeats:
        if f.status != "done":
            continue
        if ("reviewer", f.id) in done:
            ctx.log("   review %s: %s exists -- skipped" % (f.id, done[("reviewer", f.id)]["file"]))
            continue
        r = _review(ctx, milestone, round_no, f, assertions, intelligence)
        if r is not None:
            return r

    # 3. behavior, only for what a test suite cannot see
    tagged = [a for a in assertions if a.proof_class in TAGGED]
    if tagged and ("behavior", "") in done:
        ctx.log("   behavior: %s exists -- skipped" % done[("behavior", "")]["file"])
    elif tagged:
        r = _validator(ctx, "behavior", milestone, round_no,
                       prompts.behavior_prompt(mdir, milestone, tagged, digest_text, files.read_behavior_cap(mdir)))
        if isinstance(r, int):
            return r
        task, rel, text = r
        got = verdicts.parse_behavior(text)
        table = {a.id: got.get(a.id, "not reached") for a in tagged}
        journal.append(mdir, "verdict", validator=prompts.AGENTS["behavior"], milestone=milestone, round=round_no,
                       assertions=table, file=rel)
        journal.append(mdir, "validate_step", milestone=milestone, round=round_no, step="behavior", task=task, file=rel)
        ctx.log("   %s: %s" % (task, ", ".join("%s %s" % kv for kv in table.items())))

    # 4. negotiate: the model proposes, the driver applies
    files.write_state_fields(mdir, phase="negotiating",
                             resume_next="negotiate %s round %d: every validator has answered" % (milestone, round_no))
    r = _negotiate(ctx, milestone, round_no, assertions, mfeats)
    if isinstance(r, int):
        return r
    repairs = r

    # 5. proven marks, from the verdicts alone -- withheld for what a repair was just scheduled for
    withheld = {aid for rp in repairs for aid in rp["assertions"]}
    evidence = {aid: f for aid, f in proven_evidence(mdir, milestone).items() if aid not in withheld}
    changed = files.prove_assertions(mdir, evidence)
    if changed:
        journal.append(mdir, "decision", step="proven", milestone=milestone, round=round_no, assertions=changed,
                       evidence={a: evidence[a] for a in changed})
        ctx.log("   proven: %s" % ", ".join("%s (%s)" % (a, evidence[a]) for a in changed))

    # 6. repairs scheduled: advisory, the loop dispatches them
    if repairs:
        journal.append(mdir, "halt", **{"class": "advisory"},
                       reason=("first-pass validation failure in %s" % milestone) if round_no == 1
                       else "validation round %d failure in %s" % (round_no, milestone),
                       assumption="repairs scheduled: %s" % ", ".join(rp["id"] for rp in repairs))
        journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="repairs")
        resume = "; ".join("dispatch %s (repair of %s)" % (rp["id"], rp["cluster"]) for rp in repairs)
        files.write_state_fields(mdir, phase="implementing", resume_next="%s; then validate %s round %d" % (resume, milestone, round_no + 1))
        ctx.log("validate %s: round %d scheduled %s -- back to implementing" % (
            milestone, round_no, ", ".join(rp["id"] for rp in repairs)))
        return "repairs"

    # 7. unproven with nothing scheduled: a human decides
    unproven = [a for a in milestone_assertions(mdir, milestone, feats) if a.status != "proven"]
    if unproven:
        latest = verdicts.latest_verdicts(mdir, milestone)
        journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="halted")
        return stop(ctx, "gate-blocked", halt=True,
                    detail="%s: %s and no repair proposed" % (milestone, "; ".join("%s %s" % (a.id, verdict_of(latest, a)) for a in unproven[:3])),
                    needs="sharpen the assertion or re-plan (/missions:mission-amend)")

    # 8. every assertion proven: converge, archive, advance
    return _close(ctx, milestone, round_no, until)


def _close(ctx: Context, milestone: str, round_no: int, until: Optional[str]) -> Union[int, str]:
    mdir = ctx.mission_dir
    res = steps.plugin_script(ctx, "mission-converge.sh", str(mdir), milestone)
    out = " ".join(ln.strip() for ln in (res.stdout + res.stderr).splitlines() if ln.strip())
    if res.returncode == 2:
        journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="halted")
        return stop(ctx, "gate-blocked", halt=True, detail="%s: %s" % (milestone, out[:400]),
                    needs="re-plan (/missions:mission-amend); never a cap raise")
    if res.returncode != 0:
        return stop(ctx, "error", detail="mission-converge.sh exited %d: %s" % (res.returncode, out[:300]),
                    needs="look at the mission files")
    converge = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
    res = steps.plugin_script(ctx, "mission-archive.sh", str(mdir), milestone)
    archived = ((res.stdout.strip() or res.stderr.strip()).splitlines() or [""])[-1][:200]
    nxt = files.next_milestone(mdir, milestone)
    resume = ""
    if nxt:
        feats = files.read_features(mdir)
        coming = [f for f in feats if f.milestone == nxt]
        pending = [f for f in coming if f.status == "pending"]
        resume = ("dispatch %s (%s feature 1 of %d); %s closed" % (pending[0].id, nxt, len(coming), milestone)) if pending \
            else "validate %s; %s closed" % (nxt, milestone)
        files.write_state_fields(mdir, milestone=nxt, phase="implementing", resume_next=resume)
    journal.append(mdir, "milestone_closed", milestone=milestone, next=nxt, round=round_no, converge=converge, archive=archived)
    journal.append(mdir, "validate_done", milestone=milestone, round=round_no, result="closed")
    ctx.log("validate %s: closed after round %d (%s)%s" % (milestone, round_no, converge, (" -> " + nxt) if nxt else ""))
    if nxt is None:
        total = len(files.milestones(mdir))
        # resume_next spells the issue out: state.md reads ` #` as a comment marker
        return stop(ctx, "done", detail="all %d milestone(s) validated; every assertion proven" % total,
                    needs="terminal steps 1-6 of /missions:mission-run (the driver's pr phase is #10)", phase="validating",
                    resume_next="terminal steps 1-6 of /missions:mission-run: all %d milestone(s) validated, every assertion "
                                "proven (the driver's pr phase is issue 10)" % total)
    if files.read_autonomy_ceiling(mdir) == "halt at every milestone":
        return stop(ctx, "gate-blocked", halt=True, detail="%s closed; autonomy ceiling: halt at every milestone" % milestone,
                    needs="review validation/%s-*.md, then set phase implementing" % milestone)
    if until == "milestone":
        return stop(ctx, "limit-reached", detail="milestone %s closed; --until milestone" % milestone,
                    needs="re-run missions run", resume_next=resume)
    return "closed"
