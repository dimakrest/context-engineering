#!/usr/bin/env python3
"""Check native discovery after installing missions with `codex plugin add`.

Uses only app-server initialize and skills/list: no model calls or paid dispatches.
Does not install plugins or change Codex configuration. Run from any directory.
"""
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


PLUGIN = Path(__file__).resolve().parent.parent
PLUGIN_ID = "missions@dimakrest-context-engineering"
sys.path.insert(0, str(PLUGIN / "driver"))  # as tests/driver-selftest.py and tests/gen-cases.py do
from missions import INSTALL_IGNORE  # noqa: E402  -- what an installation never carries


def installed_files():
    """Every source file an installation carries, relative to the plugin root, in a stable order.
    Everything else -- bin/, scripts/, driver/, hooks/, docs/, the manifests -- is what the
    runtime guide tells a Codex session to execute, so a stale copy of any of it is a failure."""
    def ignored(name):
        return any(fnmatch(name, pattern) for pattern in INSTALL_IGNORE)
    for directory, subdirs, names in os.walk(PLUGIN):
        subdirs[:] = sorted(d for d in subdirs if not ignored(d))
        for name in sorted(names):
            if not ignored(name):
                yield (Path(directory) / name).relative_to(PLUGIN)


class AppServer:
    """The JSON-RPC stream over a `codex app-server --stdio` process's pipes."""

    def __init__(self, process):
        self.process = process
        self.messages = queue.Queue()
        self.unparsed = []  # non-JSON stdout lines, kept for the failure message
        self.seen = []      # server requests and notifications, kept for the failure message
        threading.Thread(target=self.read_messages, daemon=True).start()

    def read_messages(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    message = None
                if isinstance(message, dict):
                    self.messages.put(message)
                else:
                    self.unparsed.append(line.rstrip("\n"))
        finally:
            self.messages.put(None)

    def request(self, request_id, method, params):
        self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError("Codex app-server timed out during %s%s" % (method, self.diagnostics())) from None
            if message is None:
                raise RuntimeError("Codex app-server exited during %s%s" % (method, self.diagnostics()))
            if "method" in message:
                # A server-to-client request or a notification, whatever id it carries: not our reply.
                self.seen.append(message["method"])
            elif message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def diagnostics(self):
        notes = []
        if self.seen:
            notes.append("server messages seen: %s" % ", ".join(self.seen))
        if self.unparsed:
            notes.append("%d non-JSON stdout line(s), last: %r" % (len(self.unparsed), self.unparsed[-1]))
        return " (%s)" % "; ".join(notes) if notes else ""


def main():
    with tempfile.TemporaryDirectory(prefix="missions-discovery-") as cwd:
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        server = AppServer(process)
        try:
            server.request(1, "initialize", {
                "clientInfo": {"name": "missions-discovery-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            })
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            result = server.request(2, "skills/list", {"cwds": [cwd], "forceReload": True})
            discovered = [skill for entry in result["data"] for skill in entry["skills"]
                          if skill.get("pluginId") == PLUGIN_ID]
            expected = {"missions:" + path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md")}
            if {skill["name"] for skill in discovered} != expected:
                raise RuntimeError("Installed missions skills differ from source; install %s first" % PLUGIN_ID)
            installed_roots = set()
            for skill in discovered:
                if not skill["enabled"]:
                    raise RuntimeError("Skill is disabled: %s" % skill["name"])
                installed = Path(skill["path"]).resolve()
                root = installed.parent.parent.parent
                if installed.name != "SKILL.md" or "missions:" + installed.parent.name != skill["name"] \
                        or root == PLUGIN or PLUGIN in root.parents:
                    raise RuntimeError("%s was discovered at an unexpected path: %s" % (skill["name"], installed))
                installed_roots.add(root)
            for root in sorted(installed_roots):
                for relative in installed_files():
                    bundled = root / relative
                    if not bundled.is_file():
                        raise RuntimeError("Installed tree lacks %s; reinstall %s" % (relative, PLUGIN_ID))
                    if bundled.read_bytes() != (PLUGIN / relative).read_bytes():
                        raise RuntimeError("Installed %s is stale; reinstall %s" % (relative, PLUGIN_ID))
                if (root / "hooks/hooks.json").exists():
                    raise RuntimeError("Codex would auto-discover Claude hooks")
            print("PASS: Codex discovered %d enabled missions skills outside the source checkout; "
                  "the whole installed tree matches source." % len(discovered))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
