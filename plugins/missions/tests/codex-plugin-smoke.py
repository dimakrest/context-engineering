#!/usr/bin/env python3
"""Check native discovery after installing missions with `codex plugin add`.

Uses only app-server initialize and skills/list: no model calls or paid dispatches.
Does not install plugins or change Codex configuration. Run from any directory.
"""
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time


PLUGIN = Path(__file__).resolve().parent.parent
PLUGIN_ID = "missions@dimakrest-context-engineering"


def main():
    messages = queue.Queue()
    with tempfile.TemporaryDirectory(prefix="missions-discovery-") as cwd:
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )

        def read_messages():
            try:
                for line in process.stdout:
                    messages.put(json.loads(line))
            finally:
                messages.put(None)

        threading.Thread(target=read_messages, daemon=True).start()

        def request(request_id, method, params):
            process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 30
            while True:
                try:
                    message = messages.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty:
                    raise RuntimeError("Codex app-server timed out during %s" % method) from None
                if message is None:
                    raise RuntimeError("Codex app-server exited during %s" % method)
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(str(message["error"]))
                    return message["result"]

        try:
            request(1, "initialize", {
                "clientInfo": {"name": "missions-discovery-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            })
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            result = request(2, "skills/list", {"cwds": [cwd], "forceReload": True})
            discovered = [skill for entry in result["data"] for skill in entry["skills"]
                          if skill.get("pluginId") == PLUGIN_ID]
            expected = {"missions:" + path.parent.name: path
                        for path in (PLUGIN / "skills").glob("*/SKILL.md")}
            if {skill["name"] for skill in discovered} != set(expected):
                raise RuntimeError("Installed missions skills differ from source; install %s first" % PLUGIN_ID)
            for skill in discovered:
                if not skill["enabled"]:
                    raise RuntimeError("Skill is disabled: %s" % skill["name"])
                installed = Path(skill["path"])
                canonical = expected[skill["name"]]
                for resource in canonical.parent.rglob("*"):
                    if resource.is_file():
                        bundled = installed.parent / resource.relative_to(canonical.parent)
                        if not bundled.is_file() or bundled.read_bytes() != resource.read_bytes():
                            raise RuntimeError("Installed skill resources are stale; reinstall %s" % PLUGIN_ID)
                root = installed.parent.parent.parent
                if (root / "docs/RUNTIMES.md").read_bytes() != (PLUGIN / "docs/RUNTIMES.md").read_bytes():
                    raise RuntimeError("Installed runtime guide is stale; reinstall %s" % PLUGIN_ID)
                if (root / "hooks/hooks.json").exists():
                    raise RuntimeError("Codex would auto-discover Claude hooks")
            print("PASS: Codex discovered %d enabled missions skills outside the source checkout; "
                  "installed instructions match source." % len(discovered))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
