"""journal.jsonl -- append and query.

One compact JSON object per line, `ts` and `event` guaranteed, unknown keys tolerated
(templates/MISSIONS_TEMPLATES.md "journal.jsonl"). The driver writes the 0.2 event shapes so the
hooks' caps, lock staleness, scripts/mission-spend.sh and scripts/journal-metrics.sh keep working
unchanged; every driver-written record carries `"via":"driver"`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def path(mission_dir: Path) -> Path:
    return mission_dir / "journal.jsonl"


def append(mission_dir: Path, event: str, **fields: Any) -> Dict[str, Any]:
    """Append one record. Keys with a None value are omitted, never written as null --
    except `duration_s`, whose null is a documented value ("never estimated")."""
    rec: Dict[str, Any] = {"ts": now_iso(), "event": event}
    for k, v in fields.items():
        if v is None and k != "duration_s":
            continue
        rec[k] = v
    rec.setdefault("via", "driver")
    line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n"
    fd = os.open(str(path(mission_dir)), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return rec


def events(mission_dir: Path) -> Iterator[Dict[str, Any]]:
    """Every parseable record, in file order. Bad lines are skipped, as every other reader does."""
    p = path(mission_dir)
    if not p.exists():
        return
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and "event" in rec:
                yield rec


def count(mission_dir: Path, event: str, pred: Optional[Callable[[Dict[str, Any]], bool]] = None) -> int:
    n = 0
    for rec in events(mission_dir):
        if rec.get("event") == event and (pred is None or pred(rec)):
            n += 1
    return n


def last(mission_dir: Path, event: str, pred: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Optional[Dict[str, Any]]:
    found = None
    for rec in events(mission_dir):
        if rec.get("event") == event and (pred is None or pred(rec)):
            found = rec
    return found


def spend_usd(mission_dir: Path) -> Optional[float]:
    """Dollars spent across sessions: the LAST `session_cost.usd` per session id, summed --
    the same rule as scripts/mission-spend.sh. None when nothing was ever recorded."""
    by_session: Dict[str, float] = {}
    for rec in events(mission_dir):
        if rec.get("event") != "session_cost":
            continue
        sid = rec.get("session_id")
        usd = rec.get("usd")
        if isinstance(sid, str) and isinstance(usd, (int, float)):
            by_session[sid] = float(usd)
    if not by_session:
        return None
    return round(sum(by_session.values()), 4)


def wall_hours(mission_dir: Path) -> float:
    """Agent wall clock: sum of `duration_s` over agent_return and agent_stopped, as the serial
    guard's wall-clock cap counts it. Safe only because the driver never writes agent_stopped."""
    total = 0.0
    for rec in events(mission_dir):
        if rec.get("event") in ("agent_return", "agent_stopped"):
            d = rec.get("duration_s")
            if isinstance(d, (int, float)):
                total += float(d)
    return total / 3600.0


def dispatches(mission_dir: Path) -> int:
    """Non-static dispatches, hook- or driver-written (the dispatch cap's count)."""
    return count(mission_dir, "dispatch", lambda r: r.get("class") != "static")


def attempts(mission_dir: Path, feature: str) -> int:
    """How many times a worker was dispatched for this feature, by anyone."""
    return count(mission_dir, "dispatch",
                 lambda r: r.get("feature") == feature and r.get("agent") == "mission-worker")


def last_rejection(mission_dir: Path, feature: str) -> Optional[Dict[str, Any]]:
    """The most recent step_done for the feature whose class rejects the handoff, if it is also
    the most recent step_done for the feature at all."""
    rec = last(mission_dir, "step_done", lambda r: r.get("feature") == feature)
    if rec and rec.get("cls") in ("malformed_handoff", "tests_failed"):
        return rec
    return None
