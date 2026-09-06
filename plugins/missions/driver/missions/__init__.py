"""missions -- the out-of-process mission driver.

A `while True:` that owns the run loop: select the pending feature, render its prompt, run a
worker as a blocking subprocess under the configured harness (claude, codex, or a stub for tests),
grade what it left behind after it exits, write the mission files, journal, and go again. It
exits only through a typed stop reason. Nothing is awaited that the driver did not launch.

Stdlib only; python >= 3.9 (no `match`, no `X | Y` at runtime).
"""

__version__ = "0.2.5"
