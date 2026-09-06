"""Harness adapters: claude, codex, and a stub that lets a shell script play the agent."""
from __future__ import annotations

from typing import Dict

from .base import Adapter

NAMES = ("stub", "claude", "codex")


def make_adapter(name: str, cfg: Dict) -> Adapter:
    section = (cfg.get("adapters") or {}).get(name) or {}
    if name == "claude":
        from .claude import ClaudeAdapter
        return ClaudeAdapter(section)
    if name == "codex":
        from .codex import CodexAdapter
        return CodexAdapter(section)
    if name == "stub":
        from .stub import StubAdapter
        return StubAdapter(section)
    raise ValueError("unknown harness %r (one of %s)" % (name, ", ".join(NAMES)))
