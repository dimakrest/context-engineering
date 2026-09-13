"""missions -- the out-of-process mission driver.

A `while True:` that owns the run loop: select the pending feature, render its prompt, run a
worker as a blocking subprocess under the configured harness (claude, codex, or a stub for tests),
grade what it left behind after it exits, write the mission files, journal, and go again. It
exits only through a typed stop reason. Nothing is awaited that the driver did not launch.

Stdlib only; python >= 3.9 (no `match`, no `X | Y` at runtime).
"""

__version__ = "0.3.0"

# what a plugin installation never carries: a host copies plugins/missions minus these. Both the
# packaging selftest (which copies) and the Codex discovery smoke test (which enumerates) read it
# from here, so the two cannot describe different installations.
INSTALL_IGNORE = ("tests", "__pycache__", "*.pyc")

# the issue that tracks the driver's pr phase (the terminal steps, the merge, the push). Every
# runtime message that hands the operator back to the session skill names it from here, so the
# number cannot go stale in one message and not another.
PR_PHASE_ISSUE = 30
