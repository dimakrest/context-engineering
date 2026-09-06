"""Prompt rendering.

`agents/mission-worker.md` is the single source: its body is the system prompt, its frontmatter
maps model/effort/tools onto the RunRequest. The user part is the dispatch template from
skills/mission-run/SKILL.md ("Dispatching a worker"), rendered with the digest, the feature's
assertions verbatim from contract.md, its design section verbatim from design.md, and its
procedures. The first line is `Mission: <slug>. Feature: F0nn — <title>.` -- the 0.2 hooks take
the feature id from there, so it does not change.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import files


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


def system_prompt(plugin: Path, role: str = "mission-worker") -> Tuple[Dict, str]:
    meta, body = agent_definition(plugin, role)
    return meta, body.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin))


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


def worker_prompt(mission_dir: Path, feature: files.Feature, digest_text: str,
                  assertions: List[files.Assertion], design: Tuple[str, List[str]],
                  plugin: Path, rejection: Optional[Dict] = None) -> str:
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
            line = "  %s \u2014 %s" % (a.id, a.text)
            if a.proof_class:
                line += "  [%s]" % a.proof_class
            if a.budget and a.budget not in ("\u2014", "-"):
                line += "  proof: %s" % a.budget
            parts.append(line)
    else:
        parts.append("  (contract.md names no assertion for %s \u2014 say so in the handoff)" % feature.id)
    parts.append("")
    parts.append("Design guidelines that bind you (verbatim from design.md, with exemplars):")
    section, rows = design
    if rows:
        parts.append(_indent("\n".join(rows)))
    if section:
        parts.append(_indent(section))
    if not rows and not section:
        parts.append("  (design.md has no section for %s)" % feature.id)
    parts.append("Deviating from a guideline is allowed only if declared in the handoff with the reason.")
    parts.append("")
    parts.append("Procedures that apply: %s" % (feature.procedures or "as in the standing constraints above"))
    parts.append("Files worth starting from: %s" % (", ".join("`%s`" % f for f in feature.files) or "none named"))
    parts.append("Out of scope: %s" % (feature.out_of_scope or "everything not named above"))
    parts.append("")
    parts.append("Deliverables: working code, tests at the layer named above, one commit whose message")
    parts.append("starts with \"%s:\", and .missions/%s/handoffs/%s.md written to the schema in" % (feature.id, slug, feature.id))
    parts.append("%s/templates/MISSIONS_TEMPLATES.md. Do not push." % plugin)
    parts.append("Do not spawn background work or sub-agents; the driver waits only for this process.")
    if rejection:
        parts.append("")
        parts.append("Your previous attempt (%s) was rejected after it exited:" % rejection.get("step", "?"))
        for p in rejection.get("problems") or ["it left no usable handoff"]:
            parts.append("  - %s" % p)
        parts.append("Its commits, if any, are already on the branch: build on them, do not redo them.")
    return "\n".join(parts) + "\n"
