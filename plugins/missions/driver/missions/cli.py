"""missions -- command line.

    missions init      <mission-dir> [--harness stub|claude|codex] [--stub-dir DIR] [--force]
    missions preflight <mission-dir>
    missions run       <mission-dir> [--harness H] [--milestone M] [--limit N] [--dry-run]
    missions grade     <mission-dir> F0nn [--self] [--json]

Exit codes of `run` are the typed stop reasons (design §6.4): 0 done, 1 error, 2 preflight-failed,
3 limit-reached, 4 budget, 5 gate-blocked, 6 authority, 7 contract, 8 provider-quota, 130 interrupted.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, files, grade as grading, loop, watchdog
from .adapters import NAMES


def cmd_init(args) -> int:
    mdir = Path(args.mission_dir).resolve()
    if not (mdir / "state.md").exists():
        print("init: %s has no state.md -- point me at .missions/<slug>" % mdir, file=sys.stderr)
        return 2
    if files.config_path(mdir).exists() and not args.force:
        print("init: %s exists; pass --force to overwrite" % files.config_path(mdir), file=sys.stderr)
        return 2
    st = files.read_state(mdir)
    stub_dir = str(Path(args.stub_dir).resolve()) if args.stub_dir else "stub"
    cfg = {
        "harness": args.harness,
        "checkout": ".",
        "branch": st.branch,
        "roles": {
            "worker": {"timeout_s": 2400, "budget_usd": 8, "model": None},
        },
        "watchdog": dict(watchdog.DEFAULTS),
        "adapters": {
            "claude": {"bin": "claude", "permission_mode": "acceptEdits"},
            "codex": {"bin": "codex", "sandbox": "workspace-write"},
            "stub": {"script_dir": stub_dir},
        },
    }
    files.write_config(mdir, cfg)
    print("wrote %s (harness %s, branch %s)" % (files.config_path(mdir), args.harness, st.branch or "unset"))
    return 0


def cmd_preflight(args) -> int:
    mdir = Path(args.mission_dir).resolve()
    problems, warnings, _ = loop.preflight(mdir, files.plugin_root(), getattr(args, "harness", None))
    for w in warnings:
        print("warning: " + w)
    for p in problems:
        print("problem: " + p)
    if problems:
        print("PREFLIGHT FAIL: %d problem(s)" % len(problems))
        return loop.EXIT_CODES["preflight-failed"]
    print("PREFLIGHT PASS")
    return 0


def cmd_run(args) -> int:
    return loop.run(Path(args.mission_dir), args)


def cmd_grade(args) -> int:
    """The worker's pre-exit check on demand: the checks the driver applies after exit, minus the
    two only a finished run can answer (was this attempt's handoff written, did a commit land), and
    phrased for the one who can still fix them. `--self` adds that closing advice. Writes nothing."""
    mdir = Path(args.mission_dir).resolve()
    if not (mdir / "state.md").exists():
        print("grade: %s has no state.md" % mdir, file=sys.stderr)
        return 2
    branch = ""
    try:
        cfg = files.read_config(mdir)
        checkout = files.checkout_of(mdir, cfg)
        branch = str(cfg.get("branch") or "")
    except files.MissionFileError:
        checkout = mdir.parent.parent
    if not branch:
        try:
            branch = files.read_state(mdir).branch
        except files.MissionFileError:
            branch = ""
    g = grading.self_check(mdir, args.feature, checkout, files.plugin_root(), branch=branch)
    if args.json:
        print(json.dumps(g.to_json(), indent=2, ensure_ascii=False))
        return 0 if not g.problems else 2
    if g.problems:
        print("%s: the handoff is not valid evidence yet:" % args.feature)
        for p in g.problems:
            print("  - " + p)
        if args.self:
            print("Fix these before you exit; the driver applies the same checks after exit.")
        return 2
    print("%s: handoff valid -- status %s, commit %s on the branch%s%s" % (
        args.feature, g.status or "?", (g.sha or "")[:7],
        (", claims " + ", ".join(g.claimed)) if g.claimed else "",
        (", %d issue(s) listed" % len(g.issues)) if g.issues else ""))
    if args.self and g.status in ("partial", "blocked"):
        # valid evidence, but not of a finished feature: say what the driver does with it
        print("status %s: the driver will %s, citing your Left undone%s" % (
            g.status,
            "halt the mission for a human decision" if g.status == "blocked" else "re-dispatch this feature",
            " -- which is empty: say what remains" if not g.undone else ""))
    return 0


def _raise_interrupt(signum, frame):  # pragma: no cover - signal path
    raise KeyboardInterrupt()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="missions", description="the missions driver: a program continues the mission, not a model")
    p.add_argument("--version", action="version", version="missions " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="write driver.json for a mission directory")
    i.add_argument("mission_dir")
    i.add_argument("--harness", choices=NAMES, default="claude")
    i.add_argument("--stub-dir", help="directory of <role>.sh / <feature>.sh stub scripts (harness stub)")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    pf = sub.add_parser("preflight", help="check the mission files, the checkout and the adapter")
    pf.add_argument("mission_dir")
    pf.add_argument("--harness", choices=NAMES)
    pf.set_defaults(fn=cmd_preflight)

    r = sub.add_parser("run", help="run the loop until a typed stop")
    r.add_argument("mission_dir")
    r.add_argument("--harness", choices=NAMES)
    r.add_argument("--milestone", help="run only this milestone's features")
    r.add_argument("--limit", type=int, help="stop after N worker runs")
    r.add_argument("--dry-run", action="store_true", help="print the queue and the commands; touch nothing")
    r.set_defaults(fn=cmd_run)

    g = sub.add_parser("grade", help="check a feature's handoff the way the worker should before it exits")
    g.add_argument("mission_dir")
    g.add_argument("feature", help="F0nn")
    g.add_argument("--self", action="store_true", help="worker mode: add the 'fix these before you exit' line")
    g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cmd_grade)

    args = p.parse_args(argv)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        return int(args.fn(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return loop.EXIT_CODES["interrupted"]
