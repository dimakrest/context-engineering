"""Prompt rendering.

`agents/<role>.md` is the single source for every role that has one: its body is the system
prompt, its frontmatter maps model/effort/tools onto the RunRequest. The judgment role has no
agent file. Its system prompt is the constant below, because what it must do -- propose, edit
nothing, answer with one JSON object -- is the driver's contract (design §6.3), not a persona
anyone dispatches by hand.

The user parts are the dispatch templates from skills/mission-run/SKILL.md, rendered verbatim in
shape: the worker's ("Dispatching a worker") with the digest, the feature's assertions from
contract.md, its design section from design.md and its procedures; the reviewer's (VALIDATE step
2) with the patch path and nothing that came after the code -- hooks/mission-blind-review.sh's
rules are the test of that prompt, and a selftest runs the hook over it. The first line is
`Mission: <slug>. Feature: F0nn — <title>.` -- the 0.2 hooks take the feature id from there, so it
does not change. The judgment prompts quote the SKILL's own rules (`skill_section`) rather than
paraphrase them, and end with the JSON shape the driver parses.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import files

# role -> the agent whose definition is its system prompt and whose name the journal records
AGENTS = {
    "worker": "mission-worker",
    "reviewer": "mission-reviewer",
    "scrutiny": "mission-validator-scrutiny",
    "behavior": "mission-validator-behavior",
    "judgment": "mission-judgment",
}
JUDGMENT_TOOLS = ["Read", "Glob", "Grep"]

JUDGMENT_SYSTEM = """# Mission judgment — the model proposes, the driver applies

You are the judgment step of a mission driver. The driver is a program: it runs the mission's
workers and validators, applies the rules, and edits the mission files. You do not. You read what
the prompt gives you, apply the rules it quotes verbatim from the mission-run skill, and propose
what the driver should do next.

You edit nothing: no file in the repository, no file under `.missions/`. You run nothing that
changes state. Reading is allowed when the prompt points you at a path; git history is not your
input and you do not go looking for the authors' reasoning.

Answer with exactly one JSON object in the shape the prompt gives, and nothing else -- no prose
before it, none after it. A ```json fence around the object is tolerated. Every string you put in
it is read by a human later: say why in plain words. A reply the driver cannot parse or apply is
sent back to you once with the error; a second such reply ends the run.
"""

ANSWER_LINE = "Answer with the JSON object only."

# the shapes the driver parses (judgment.validate_negotiate / validate_triage), pasted verbatim
NEGOTIATE_SHAPE = '''{"findings": [{"title": str, "assertion": "A00n"|null, "found_by": str, "where": str,
               "severity": "high"|"medium"|"low", "cluster": "C0n", "cluster_label": str,
               "blocking": bool, "disposition": "repair"|"accept"|"waive", "why": str}],
 "repairs":  [{"cluster": "C0n", "title": str, "assertions": ["A00n"], "files": [str],
               "procedures": str, "out_of_scope": str}],
 "contract_wrong": bool, "reason": str}'''

TRIAGE_SHAPE = '''{"resolutions": [{"issue": int, "disposition": "resolved"|"defer"|"repair"|"escalate", "why": str,
                  "followup": {"title": str, "assertion": "A00n"|null, "severity": str,
                               "cluster": "C0n", "cluster_label": str, "blocking": bool} | null,
                  "repair": {"title": str, "assertions": [...], "files": [...], "procedures": str} | null}]}'''


class DigestError(Exception):
    """scripts/mission-state.sh refused (the digest does not fit, or the mission is unreadable)."""


def agent_definition(plugin: Path, name: str) -> Tuple[Dict, str]:
    """(frontmatter, body). Frontmatter keys are strings; `tools` is a list, empty when the YAML
    list is broken -- the same reading hooks/mission-lib.sh and lint-agents.sh apply."""
    text = files.read_text(plugin / "agents" / ("%s.md" % name))
    meta: Dict = {"tools": []}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            fm = text[4:end].split("\n")
            body = text[end + 4:].lstrip("\n")
            i = 0
            while i < len(fm):
                m = re.match(r"^([A-Za-z_]+):\s*(.*)$", fm[i])
                if not m:
                    i += 1
                    continue
                key, val = m.group(1), m.group(2).strip()
                if key == "tools":
                    if val:
                        meta["tools"] = [t.strip() for t in val.strip("[]").split(",") if t.strip()]
                    else:
                        items: List[str] = []
                        j = i + 1
                        while j < len(fm) and re.match(r"^\s*-\s*\S", fm[j]):
                            items.append(re.sub(r"^\s*-\s*", "", fm[j]).strip())
                            j += 1
                        meta["tools"] = items
                        i = j
                        continue
                else:
                    meta[key] = val
                i += 1
    return meta, body


def system_prompt(plugin: Path, role: str = "worker") -> Tuple[Dict, str]:
    """(frontmatter, system text) for a role -- or for an agent named outright, which is what
    the callers from before the roles existed pass. Judgment has no agent file: the constant, with
    the read-only tool list the sandbox gives it."""
    if role == "judgment":
        return {"name": AGENTS["judgment"], "tools": list(JUDGMENT_TOOLS)}, JUDGMENT_SYSTEM
    meta, body = agent_definition(plugin, AGENTS.get(role, role))
    return meta, body.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin))


def skill_section(plugin: Path, name: str) -> str:
    """The text of skills/mission-run/SKILL.md from the `## <name>` heading to the next `## `,
    heading included; empty when there is no such heading. The judgment prompts carry the SKILL's
    rules this way so the driver and the prose loop cannot drift apart on what VALIDATE decides
    or what a BLOCK halt is."""
    out: List[str] = []
    for ln in files.read_text(plugin / "skills" / "mission-run" / "SKILL.md").split("\n"):
        if out:
            if re.match(r"^##\s", ln):
                break
            out.append(ln)
        elif re.match(r"^##\s+" + re.escape(name) + r"\b", ln):
            out.append(ln)
    return ("\n".join(out).rstrip("\n") + "\n") if out else ""


def digest(mission_dir: Path, plugin: Path) -> str:
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    res = subprocess.run(["bash", str(plugin / "scripts" / "mission-state.sh"), str(mission_dir)],
                         capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise DigestError(res.stderr.strip() or "mission-state.sh exited %d" % res.returncode)
    return res.stdout


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + ln if ln.strip() else ln for ln in text.rstrip("\n").split("\n"))


def _assertion_line(a: files.Assertion, with_class: bool, budget_label: str) -> str:
    """`  A003 — <text>  [structural]  proof: <budget>` -- the SKILL's own row shape; the class
    and the budget label differ per template."""
    line = "  %s \u2014 %s" % (a.id, a.text)
    if with_class and a.proof_class:
        line += "  [%s]" % a.proof_class
    if budget_label and a.budget and a.budget not in ("\u2014", "-"):
        line += "  %s %s" % (budget_label, a.budget)
    return line


def _design_lines(feature_id: str, design: Tuple[str, List[str]]) -> List[str]:
    section, rows = design
    out: List[str] = []
    if rows:
        out.append(_indent("\n".join(rows)))
    if section:
        out.append(_indent(section))
    if not rows and not section:
        out.append("  (design.md has no section for %s)" % feature_id)
    return out


def worker_prompt(mission_dir: Path, feature: files.Feature, digest_text: str,
                  assertions: List[files.Assertion], design: Tuple[str, List[str]],
                  plugin: Path, rejection: Optional[Dict] = None,
                  inherited: Optional[List[str]] = None) -> str:
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Feature: %s \u2014 %s." % (slug, feature.id, feature.title or feature.id))
    parts.append("")
    parts.append("Mission state (digest \u2014 this is your briefing; do not read state.md wholesale):")
    parts.append(_indent(digest_text))
    parts.append("")
    parts.append("Assertions you must satisfy (verbatim from contract.md, with their proof budget):")
    if assertions:
        for a in assertions:
            parts.append(_assertion_line(a, True, "proof:"))
    else:
        parts.append("  (contract.md names no assertion for %s \u2014 say so in the handoff)" % feature.id)
    parts.append("")
    parts.append("Design guidelines that bind you (verbatim from design.md, with exemplars):")
    parts += _design_lines(feature.id, design)
    parts.append("Deviating from a guideline is allowed only if declared in the handoff with the reason.")
    parts.append("")
    parts.append("Procedures that apply: %s" % (feature.procedures or "as in the standing constraints above"))
    parts.append("Files worth starting from: %s" % (", ".join("`%s`" % f for f in feature.files) or "none named"))
    parts.append("Out of scope: %s" % (feature.out_of_scope or "everything not named above"))
    parts.append("")
    parts.append("Deliverables: working code, tests at the layer named above, one commit whose message")
    parts.append("starts with \"%s:\", and .missions/%s/handoffs/%s.md written to the schema in" % (feature.id, slug, feature.id))
    parts.append("%s/templates/MISSIONS_TEMPLATES.md. Do not push." % plugin)
    parts.append("Changes outside the files named above are allowed only when the handoff names them under Completed with the reason.")
    parts.append("Do not spawn background work or sub-agents; the driver waits only for this process.")
    parts.append("Before you exit, run `bash %s/bin/missions grade %s %s --self` and fix what it reports:" % (
        plugin, mission_dir, feature.id))
    parts.append("the driver runs the same check after you exit, and a handoff it rejects costs another run.")
    if inherited:
        parts.append("")
        parts.append("The working tree carries uncommitted changes from a previous attempt (%s%s)." % (
            ", ".join("`%s`" % f for f in inherited[:6]), ", ..." if len(inherited) > 6 else ""))
        parts.append("Review them before you start: keep what is right and commit it with your work, discard the rest.")
    if rejection:
        parts.append("")
        parts.append("Your previous attempt (%s) was rejected after it exited:" % rejection.get("step", "?"))
        for p in rejection.get("problems") or ["it left no usable handoff"]:
            parts.append("  - %s" % p)
        parts.append("Its commits, if any, are already on the branch: build on them, do not redo them.")
    return "\n".join(parts) + "\n"


def reviewer_prompt(mission_dir: Path, feature: files.Feature, assertions: List[files.Assertion],
                    design: Tuple[str, List[str]], patch_path: Path, base: str, head: str,
                    intelligence_line: str) -> str:
    """VALIDATE step 2's brief, in the SKILL's shape. It names the patch and what was written
    before the code (assertions, guidelines) and nothing after it: no handoff, no section of one,
    no git command -- the words hooks/mission-blind-review.sh rejects are the words that would
    leak the author's reasoning."""
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Feature: %s \u2014 %s." % (slug, feature.id, feature.title or feature.id))
    parts.append("Review the patch for %s against these assertions. You have not seen how or why it was" % feature.id)
    parts.append("written and you should not go looking.")
    if assertions:
        for a in assertions:
            parts.append(_assertion_line(a, False, "proof budget:"))
    else:
        parts.append("  (contract.md names no assertion for %s \u2014 say so in your verdicts)" % feature.id)
    parts.append("Design guidelines this feature was bound to (pre-code, from design.md):")
    parts += _design_lines(feature.id, design)
    parts.append("Patch: %s (base %s, head %s) \u2014 read this file; it is your only diff, and you do not run git yourself." % (
        patch_path, base[:7], head[:7]))
    parts.append("Codebase intelligence: %s \u2014 for every public symbol the patch changes, find its callers" % (
        intelligence_line or "none"))
    parts.append("(graphify affected \"<symbol>\" when graphify is named; grep otherwise) and grade them in your Impact table.")
    parts.append("Return a per-assertion verdict (satisfied / not satisfied / cannot tell from the diff),")
    parts.append("a per-guideline conformance verdict, the impact table, plus defects with file:line and a")
    parts.append("root-cause cluster hint. \"cannot tell\" is a legitimate and useful answer.")
    parts.append("Write nothing to the repository. Your final message is the review, in the format your instructions give.")
    return "\n".join(parts) + "\n"


def scrutiny_prompt(mission_dir: Path, milestone: str, features: List[files.Feature],
                    assertions: List[files.Assertion], digest_text: str) -> str:
    """VALIDATE step 1's brief: the milestone's features with their files and procedures, every
    assertion with its class, and the digest -- the standing constraints carry the test
    invocation, which is why the digest goes in and no command is invented here."""
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Milestone: %s \u2014 scrutiny." % (slug, milestone))
    parts.append("Run the deterministic checks for %s -- the repo's test layers, linters and type checkers -- and" % milestone)
    parts.append("report exactly what happened, exit codes included. You make no repairs.")
    parts.append("")
    parts.append("Mission state (digest \u2014 the standing constraints carry the test invocation; use the repo's own):")
    parts.append(_indent(digest_text))
    parts.append("")
    parts.append("Features of %s, each with the files it touched and its procedures:" % milestone)
    for f in features:
        parts.append("  %s \u2014 %s: files %s; procedures: %s" % (
            f.id, f.title or f.id, ", ".join("`%s`" % p for p in f.files) or "none named",
            f.procedures or "as in the standing constraints"))
    parts.append("")
    parts.append("Assertions of %s, with their proof class (a structural assertion no test exercises is unproven," % milestone)
    parts.append("however green the suite -- say so in the coverage table):")
    for a in assertions:
        parts.append(_assertion_line(a, True, ""))
    parts.append("")
    parts.append("Report, in the format your instructions give: ## Commands (command, exit code, duration), ## Failures,")
    parts.append("## Coverage of milestone assertions (assertion, the test that exercises it, result), ## Health delta.")
    parts.append("Write nothing to the repository. Your final message is the report.")
    return "\n".join(parts) + "\n"


def behavior_prompt(mission_dir: Path, milestone: str, tagged_assertions: List[files.Assertion],
                    digest_text: str, cap: Optional[int]) -> str:
    """VALIDATE step 3's brief: only the interface/conversational assertions, the milestone's
    live-run cap, the digest. No diff and no path of the implementation -- the validator proves
    what the contract requires by using the system, not by reading what the code does."""
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Milestone: %s \u2014 behavior validation." % (slug, milestone))
    parts.append("Prove these assertions by using the running system -- the UI for `interface`, the real conversational")
    parts.append("channel for `conversational`. Do not read the implementation: you are testing what the contract")
    parts.append("requires, which is not the same thing as what the code does.")
    parts.append("")
    parts.append("Assertions to prove (only those a test suite cannot see):")
    for a in tagged_assertions:
        parts.append(_assertion_line(a, True, ""))
    parts.append("")
    parts.append("Live-run cap for this milestone: %s (mission.md). An assertion you cannot reach within it is" % (
        ("%d live runs" % cap) if cap else "none set"))
    parts.append("`not reached`, never proven.")
    parts.append("")
    parts.append("Mission state (digest \u2014 the standing constraints name the environment and the instrument):")
    parts.append(_indent(digest_text))
    parts.append("")
    parts.append("Report, in the format your instructions give: ## Assertion results (proven / FAILED / not reached, each")
    parts.append("with the transcript or trace that shows it), ## Defects. Write nothing to the repository. Your final")
    parts.append("message is the report.")
    return "\n".join(parts) + "\n"


def _rules_and_shape(parts: List[str], followups_text: str, rules: str, shape: str) -> str:
    """The tail every judgment prompt shares: the registry and the rules pasted whole (verbatim,
    not indented -- their headings stay headings), then the shape and the one-line instruction."""
    parts.append("")
    parts.append("followups.md as it stands:")
    parts.append(followups_text.rstrip("\n") if followups_text.strip() else "  (empty)")
    parts.append("")
    parts.append("The rules (verbatim from the mission-run skill):")
    parts.append(rules.rstrip("\n") if rules.strip() else "  (none)")
    parts.append("")
    parts.append("Answer with exactly this JSON shape:")
    parts.append(shape)
    parts.append(ANSWER_LINE)
    return "\n".join(parts) + "\n"


def negotiate_prompt(mission_dir: Path, milestone: str, round_no: int, verdict_summary: str,
                     validation_texts: Dict[str, str], followups_text: str, rules: str) -> str:
    """VALIDATE step 4's brief: the latest verdict per assertion, every validation file of the
    round pasted whole (`validation_texts`: file name -> text, in step order), the registry as
    it stands, and the SKILL's VALIDATE section. Ends with the shape judgment.validate_negotiate
    checks."""
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Milestone: %s \u2014 negotiate, validation round %d." % (slug, milestone, round_no))
    parts.append("Read every verdict of this round and propose what the driver does next: which findings go to")
    parts.append("followups.md (clustered by root cause, dispositioned), which clusters become repair features")
    parts.append("(one cluster, one repair), and whether the contract itself is wrong. Proven marks are not yours")
    parts.append("to give: the driver writes them from the validators' verdicts. You edit nothing.")
    parts.append("")
    parts.append("Verdict summary (the latest verdict per assertion, from the driver's journal):")
    parts.append(_indent(verdict_summary) if verdict_summary.strip() else "  (no verdicts were journaled)")
    parts.append("")
    parts.append("Validation files of this round:")
    for name, text in validation_texts.items():
        parts.append("--- %s ---" % name)
        parts.append(text.rstrip("\n"))
    if not validation_texts:
        parts.append("  (none)")
    return _rules_and_shape(parts, followups_text, rules, NEGOTIATE_SHAPE)


def triage_prompt(mission_dir: Path, issues: List[str], handoff_texts: Dict[str, str],
                  followups_text: str, rules: str) -> str:
    """The open-issue brief: the issues as `- [i] text` (the index is what the reply names), the
    handoffs that raised them (`handoff_texts`: feature id -> text), the registry, and the
    SKILL's Halts section -- what is a BLOCK halt and what proceeds. Ends with the shape
    judgment.validate_triage checks."""
    slug = mission_dir.name
    parts: List[str] = []
    parts.append("Mission: %s. Triage of %d open issue(s)." % (slug, len(issues)))
    parts.append("A worker's handoff raised the issues below, and the loop starts no new feature while they stand.")
    parts.append("For each, propose one disposition: `resolved` (already answered by what is on the branch or in the")
    parts.append("mission files -- say why), `defer` (register it as a follow-up and move on), `repair` (register it")
    parts.append("and schedule a repair feature for it), `escalate` (a human must decide: a BLOCK halt by the rules")
    parts.append("below, with your why on the decision card). The driver applies your answer; you edit nothing.")
    parts.append("")
    parts.append("Open issues:")
    for i, text in enumerate(issues, 1):
        parts.append("- [%d] %s" % (i, text))
    parts.append("")
    parts.append("Handoffs that raised them:")
    for fid, text in handoff_texts.items():
        parts.append("--- handoffs/%s.md ---" % fid)
        parts.append(text.rstrip("\n"))
    if not handoff_texts:
        parts.append("  (none found)")
    parts.append("")
    parts.append("`issue` in your answer is the number in brackets above.")
    return _rules_and_shape(parts, followups_text, rules, TRIAGE_SHAPE)
