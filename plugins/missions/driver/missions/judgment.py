"""Judgment-step output (design §6.3): the model proposes, the driver applies.

A judgment run (negotiate, triage) answers with exactly one JSON object and edits nothing. This
module turns the run's final message into that object (`extract_json`) and checks it against the
step's schema in code (`validate_negotiate`, `validate_triage`): required keys and types, the
values the applier can act on, unknown keys ignored -- no jsonschema dependency. A schema verdict
is a list of problems, not an exception: the failure policy appends it to the prompt for the one
re-run allowed, then stops with `error`. Applying the object (follow-ups, repair features, open
issues) lives with the steps; nothing here touches a mission file.

Optional string fields (`where`, `why`, `cluster_label`, `procedures`, `out_of_scope`, `reason`)
may be absent; the applier reads them with `.get(key, "")`. Everything else listed in the
schemas is required.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_FENCE = re.compile(r"```(?:json)?[ \t]*\n(.*?)\n[ \t]*```", re.S)
_AID = re.compile(r"^A\d{3}[a-z]?$")
_CID = re.compile(r"^C\d{2,3}$")
SEVERITIES = ("high", "medium", "low")
NEGOTIATE_DISPOSITIONS = ("repair", "accept", "waive")
TRIAGE_DISPOSITIONS = ("resolved", "defer", "repair", "escalate")


class JudgmentError(Exception):
    """The reply carries no JSON object the driver can read. The message is what the re-run
    prompt quotes back, so it names the candidate and the parser's own words."""


def _first_object(text: str) -> Optional[str]:
    """The first `{` to its matching `}`, string contents skipped -- a brace inside a quoted
    finding title must not close the object early."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str) -> Dict[str, Any]:
    """The reply's one JSON object: a fenced block when there is one, else the first `{` to its
    matching `}`. Both are tried in that order, so a fence that holds something other than the
    object (a list, a snippet) does not hide the object after it. Raises JudgmentError with the
    parser's own message -- the re-run prompt quotes it."""
    candidates: List[Tuple[str, str]] = []
    m = _FENCE.search(text)
    if m:
        candidates.append(("the fenced block", m.group(1)))
    span = _first_object(text)
    if span:
        candidates.append(("the first {...} span", span))
    if not candidates:
        head = text.strip().replace("\n", " ")[:80]
        raise JudgmentError("no JSON object in the reply (%d chars, starting %r)" % (len(text), head))
    errors: List[str] = []
    for label, cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError as e:
            errors.append("%s: %s" % (label, e))
            continue
        if not isinstance(obj, dict):
            errors.append("%s: the top-level value is a %s, not an object" % (label, type(obj).__name__))
            continue
        return obj
    raise JudgmentError("; ".join(errors))


# ---------------------------------------------------------------- schema checks

def _field(problems: List[str], obj: Dict, key: str, types: tuple, where: str, required: bool = True,
           choices: Optional[tuple] = None, nullable: bool = False) -> Any:
    """One typed lookup. Appends the problem and returns None when the value is unusable;
    the caller then skips the checks that would have needed it."""
    if key not in obj:
        if required:
            problems.append("%s: missing %r" % (where, key))
        return None
    v = obj[key]
    if v is None and nullable:
        return None
    # bool is an int to isinstance(); an issue index of `true` is not an index
    if not isinstance(v, types) or (isinstance(v, bool) and bool not in types):
        want = " or ".join(t.__name__ for t in types) + (" or null" if nullable else "")
        problems.append("%s: %r must be %s, got %s" % (where, key, want, type(v).__name__))
        return None
    if choices is not None and v not in choices:
        problems.append("%s: %r must be one of %s, got %r" % (where, key, ", ".join(choices), v))
        return None
    return v


def _id_list(problems: List[str], obj: Dict, key: str, where: str, pattern: "re.Pattern[str]",
             what: str) -> List[str]:
    vals = _field(problems, obj, key, (list,), where)
    out: List[str] = []
    for v in vals or []:
        if not isinstance(v, str) or not pattern.match(v):
            problems.append("%s: %r contains %r, not an %s id" % (where, key, v, what))
        else:
            out.append(v)
    return out


def _str_list(problems: List[str], obj: Dict, key: str, where: str) -> List[str]:
    vals = _field(problems, obj, key, (list,), where)
    bad = [v for v in vals or [] if not isinstance(v, str)]
    if bad:
        problems.append("%s: %r must hold strings, got %r" % (where, key, bad[0]))
    return [v for v in vals or [] if isinstance(v, str)]


def _assertion(problems: List[str], obj: Dict, where: str) -> Optional[str]:
    a = _field(problems, obj, "assertion", (str,), where, nullable=True)
    if a is not None and not _AID.match(a):
        problems.append("%s: assertion %r is not an A00n id" % (where, a))
        return None
    return a


def _cluster(problems: List[str], obj: Dict, where: str) -> Optional[str]:
    c = _field(problems, obj, "cluster", (str,), where)
    if c is not None and not _CID.match(c):
        problems.append("%s: cluster %r is not a C0n id" % (where, c))
        return None
    return c


def validate_negotiate(obj: Any) -> List[str]:
    """Problems with a negotiate reply; empty when the driver can apply it. Beyond types: a
    finding dispositioned `repair` needs a repair for its cluster, a repair needs such a finding,
    and one cluster gets one repair -- the registry rule check.sh enforces after the fact."""
    if not isinstance(obj, dict):
        return ["the reply is not a JSON object"]
    problems: List[str] = []
    findings = _field(problems, obj, "findings", (list,), "reply")
    repairs = _field(problems, obj, "repairs", (list,), "reply")
    wrong = _field(problems, obj, "contract_wrong", (bool,), "reply")
    reason = _field(problems, obj, "reason", (str,), "reply", required=False)
    if wrong and not (reason or "").strip():
        problems.append("reply: contract_wrong is true but reason is empty -- the halt needs the reason")
    want_repair = set()
    for i, f in enumerate(findings or []):
        where = "findings[%d]" % i
        if not isinstance(f, dict):
            problems.append(where + ": not an object")
            continue
        _field(problems, f, "title", (str,), where)
        _assertion(problems, f, where)
        _field(problems, f, "found_by", (str,), where)
        _field(problems, f, "where", (str,), where, required=False)
        _field(problems, f, "severity", (str,), where, choices=SEVERITIES)
        c = _cluster(problems, f, where)
        _field(problems, f, "cluster_label", (str,), where, required=False)
        _field(problems, f, "blocking", (bool,), where)
        d = _field(problems, f, "disposition", (str,), where, choices=NEGOTIATE_DISPOSITIONS)
        _field(problems, f, "why", (str,), where, required=False)
        if d == "repair" and c:
            want_repair.add(c)
    have_repair = set()
    for i, r in enumerate(repairs or []):
        where = "repairs[%d]" % i
        if not isinstance(r, dict):
            problems.append(where + ": not an object")
            continue
        c = _cluster(problems, r, where)
        _field(problems, r, "title", (str,), where)
        _id_list(problems, r, "assertions", where, _AID, "A00n")
        _str_list(problems, r, "files", where)
        _field(problems, r, "procedures", (str,), where, required=False)
        _field(problems, r, "out_of_scope", (str,), where, required=False)
        if c in have_repair:
            problems.append("%s: cluster %s already has a repair -- one cluster, one repair feature" % (where, c))
        if c:
            have_repair.add(c)
    for c in sorted(want_repair - have_repair):
        problems.append("a finding in cluster %s is dispositioned repair, but no repair names that cluster" % c)
    for c in sorted(have_repair - want_repair):
        problems.append("a repair names cluster %s, but no finding in it is dispositioned repair" % c)
    return problems


def validate_triage(obj: Any) -> List[str]:
    """Problems with a triage reply; empty when the driver can apply it. `issue` is the 1-based
    index the prompt printed; whether it exists is the applier's check (it knows the list)."""
    if not isinstance(obj, dict):
        return ["the reply is not a JSON object"]
    problems: List[str] = []
    resolutions = _field(problems, obj, "resolutions", (list,), "reply")
    seen = set()
    for i, r in enumerate(resolutions or []):
        where = "resolutions[%d]" % i
        if not isinstance(r, dict):
            problems.append(where + ": not an object")
            continue
        issue = _field(problems, r, "issue", (int,), where)
        if issue is not None and issue < 1:
            problems.append("%s: issue %d is not a 1-based index" % (where, issue))
        elif issue in seen:
            problems.append("%s: issue %d already has a resolution" % (where, issue))
        seen.add(issue)
        d = _field(problems, r, "disposition", (str,), where, choices=TRIAGE_DISPOSITIONS)
        _field(problems, r, "why", (str,), where, required=False)
        fu = _field(problems, r, "followup", (dict,), where, required=False, nullable=True)
        rp = _field(problems, r, "repair", (dict,), where, required=False, nullable=True)
        if fu is not None:
            w = where + ".followup"
            _field(problems, fu, "title", (str,), w)
            _assertion(problems, fu, w)
            _field(problems, fu, "severity", (str,), w)
            _cluster(problems, fu, w)
            _field(problems, fu, "cluster_label", (str,), w, required=False)
            _field(problems, fu, "blocking", (bool,), w)
        if rp is not None:
            w = where + ".repair"
            _field(problems, rp, "title", (str,), w)
            _id_list(problems, rp, "assertions", w, _AID, "A00n")
            _str_list(problems, rp, "files", w)
            _field(problems, rp, "procedures", (str,), w, required=False)
        if d in ("defer", "repair") and fu is None:
            problems.append("%s: disposition %s needs a followup" % (where, d))
        if d == "repair" and rp is None:
            problems.append("%s: disposition repair needs a repair" % where)
    return problems
