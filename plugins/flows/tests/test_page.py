#!/usr/bin/env python3
"""The page (router/page/) in a browser: headless Chromium driven by Playwright, against the mailbox daemon on
tests/fake-orca, as test_router.py runs it.

    python3 tests/test_page.py
    FLOWS_UPDATE_SNAPSHOTS=1 python3 tests/test_page.py -k screenshot      # rewrite the reference screenshots

It needs Playwright and its Chromium. Without them every test is skipped, and the reason is the line that installs
them; test_router.py needs neither:

    python3 -m pip install playwright && python3 -m playwright install chromium

The reference screenshots, tests/fixtures/page/view-1400-light.png and view-900-dark.png, are view mode drawn from
tests/fixtures/page/state.json (a synthetic run) with the clock fixed at FIXED_NOW and the timezone UTC. A screenshot
matches when it has the same size and at most 1% of its pixels differ by more than 48 (of 255) in a colour channel:
fonts are drawn a little differently by another Chromium or another machine; a moved box or a changed colour is not.
"""
import base64
import datetime
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_router as tr   # noqa: E402  (the daemon on the stand-in Orca, as test_router.py starts it)

INSTALL = "python3 -m pip install playwright && python3 -m playwright install chromium"
PAGE = tr.KIT / "page"
FIXTURES = TESTS / "fixtures" / "page"
FIXED_NOW = datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
TOLERANCE = (48, 0.01)   # a channel differs by more than 48; at most 1% of the pixels

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:
    sync_playwright = None

_pw = {}


def browser():
    """One Chromium for the whole run; unittest.SkipTest with the install line when there is none."""
    if "browser" not in _pw:
        if sync_playwright is None:
            raise unittest.SkipTest(f"Playwright is not installed: {INSTALL}")
        pw = sync_playwright().start()
        try:
            _pw["browser"] = pw.chromium.launch()
        except PlaywrightError as e:
            pw.stop()
            raise unittest.SkipTest(f"Playwright has no Chromium ({str(e).splitlines()[0]}): {INSTALL}")
        _pw["pw"] = pw
    return _pw["browser"]


def tearDownModule():
    if "browser" in _pw:
        _pw["browser"].close()
        _pw["pw"].stop()


class Live(tr.FlowCase):
    """A 2-PR flow on the daemon: A1 has started (a done, b running, c and d pending), B1 comes after A1."""

    def setUp(self):
        self.browser = browser()
        super().setUp()
        w = lambda sid: {"id": sid, "agent": "claude", "model": "m-sonnet", "spec": "smoke-write.md"}
        (self.kit / "templates" / "two.json").write_text(json.dumps({"name": "two", "kit": str(tr.KIT), "steps": [w(x) for x in "abcd"]}))
        self.scenario({"match": "A1 a$", "events": [self.done(0.1)]},
                      {"match": "A1 b$", "events": [{"after": 0.1, "type": "heartbeat", "phase": "writing b"}]})
        self.R("init", ok=True)
        rc, out = self.apply(self.pr("A1", template="two"), self.pr("B1", template="two", after=["A1"]), slots=2,
                             templates={"two": "kit/templates/two.json"})
        self.assertEqual(rc, 0, out)
        self.until(lambda: self.statuses("A1") == ["done", "running", "pending", "pending"], what="A1 at step b")
        self.url = json.loads((self.state / "page.json").read_text())["url"]
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 900})
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(self.url)
        self.page.wait_for_selector("#pr-A1 [data-step]")

    def tearDown(self):
        self.page.close()
        super().tearDown()
        self.assertEqual(self.errors, [])

    def statuses(self, pr):
        f = self.state / "chains" / pr / "state.json"
        return [s["status"] for s in json.loads(f.read_text())["steps"]] if f.exists() else []

    def flow(self):
        with urllib.request.urlopen(self.url + "flow", timeout=10) as r:
            return json.loads(r.read())

    def chips(self, pr, what="step"):
        return self.page.locator(f"#pr-{pr} [data-step]").evaluate_all(f"es => es.map(e => e.dataset.{what})")

    def edit(self):
        self.page.click("#edit-toggle")
        self.page.wait_for_selector("#palette")

    def apply_and_confirm(self):
        """Apply: the dry run's lines, then the apply. Returns the lines the page showed."""
        self.page.click("#apply")
        self.page.wait_for_selector("#review")
        lines = self.page.locator("#review li").all_inner_texts()
        self.page.click("#confirm")
        self.page.wait_for_selector("#notice >> text=Applied")
        return lines

    def test_the_canvas_shows_both_prs_and_their_steps(self):
        self.assertEqual(self.page.locator(".node").evaluate_all("es => es.map(e => e.dataset.pr)"), ["A1", "B1"])
        self.assertEqual(self.chips("A1"), ["a", "b", "c", "d"])
        self.assertEqual(self.chips("A1", "status"), ["done", "running", "pending", "pending"])
        self.assertEqual(self.chips("B1"), ["a", "b", "c", "d"])
        self.assertEqual(self.page.locator("#pr-A1 .sc").all_inner_texts(), ["✓ a", "▶ b", "· c", "· d"])
        self.assertEqual(self.page.locator("svg.edges path[data-from]").evaluate_all("es => es.map(e => e.dataset.from + '>' + e.dataset.to)"), ["A1>B1"])
        self.assertIn("after A1", self.page.inner_text("#pr-B1"))
        self.page.wait_for_selector("#pr-A1 .wline >> text=writing b")   # the worker on the running step, from a later poll
        self.assertIn("claude m-sonnet", self.page.inner_text("#pr-A1 .wline"))
        self.assertIn("flow v1 · slots 1/2 · start auto", self.page.inner_text("h1 + .sub"))

    def test_a_pending_step_dragged_before_another_is_one_version_and_nothing_else(self):
        before = self.flow()
        self.edit()
        self.page.locator('#pr-A1 [data-step="d"]').drag_to(self.page.locator('#pr-A1 [data-step="c"]'), target_position={"x": 3, "y": 8})
        self.assertEqual(self.chips("A1"), ["a", "b", "d", "c"])
        lines = self.apply_and_confirm()
        self.assertEqual(lines, ["A1: its own steps now", "A1: steps reordered: a, b, d, c"])
        after = self.flow()
        self.assertEqual(after["version"], 2)
        self.assertEqual(after["history"][-1]["changes"], lines)        # what the page showed is what the router wrote
        self.assertEqual([s["id"] for s in after["prs"][0]["steps"]], ["a", "b", "d", "c"])
        mine = lambda f: {k: v for k, v in f.items() if k not in ("version", "history", "resolved", "removed")}
        del after["prs"][0]["steps"]
        self.assertEqual(mine(after), mine(before))                    # nothing else changed
        self.page.wait_for_selector("text=Editing v2: no local edit")   # drawn again from the router
        self.assertEqual(self.chips("A1"), ["a", "b", "d", "c"])

    def test_a_role_from_the_palette_becomes_a_step_of_the_pr(self):
        self.edit()
        row = self.page.locator('#pr-B1 [data-steps]')
        box = row.bounding_box()
        self.page.locator('#palette [data-name="review-claude"]').drag_to(row, target_position={"x": box["width"] - 4, "y": box["height"] / 2})
        self.assertEqual(self.chips("B1"), ["a", "b", "c", "d", "review_claude"])
        self.assertEqual(self.apply_and_confirm(), ["B1: its own steps now", "B1: step review_claude added after d"])
        step = self.flow()["prs"][1]["steps"][-1]
        self.assertEqual((step["id"], step["spec"], step["agent"]), ("review_claude", "review-claude.md", "claude"))
        self.assertEqual(step["model"], "claude-opus-5-5")             # as inner-pr.json runs the role

    def test_a_drag_onto_a_done_step_is_refused_on_the_canvas(self):
        self.edit()
        src, done = self.page.locator('#pr-A1 [data-step="c"]').bounding_box(), self.page.locator('#pr-A1 [data-step="a"]').bounding_box()
        m = self.page.mouse
        m.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
        m.down()
        m.move(done["x"] + done["width"] / 2, done["y"] + done["height"] / 2, steps=5)
        self.assertIn("no-drop", self.page.get_attribute('#pr-A1 [data-step="a"]', "class"))   # drawn as refused while dragged
        m.up()
        self.assertIn("Not dropped: step a is done", self.page.inner_text("#notice"))
        self.assertEqual(self.chips("A1"), ["a", "b", "c", "d"])
        self.assertTrue(self.page.locator("#apply").is_disabled())     # no Apply offered for it
        self.assertEqual(self.page.locator('#pr-A1 [data-drag="step"]').evaluate_all("es => es.map(e => e.dataset.step)"), ["c", "d"])
        self.assertEqual(self.flow()["version"], 1)

    def test_a_refused_apply_shows_the_routers_problems_and_keeps_the_edits(self):
        self.edit()
        self.page.click("#pr-B1 >> text=+ PR after B1")
        self.page.fill("#pr-form #id", "C1")
        self.page.fill("#pr-form #base", "main")
        self.page.click("#pr-form button[type=submit]")
        self.page.click("#apply")
        self.page.wait_for_selector("#problems")
        self.assertIn("C1: base main: inner PRs go into the integration branch, never main or master", self.page.inner_text("#problems"))
        self.assertEqual(self.page.locator(".node").evaluate_all("es => es.map(e => e.dataset.pr)"), ["A1", "B1", "C1"])
        self.assertTrue(self.page.locator("#apply").is_enabled())
        self.assertEqual(self.flow()["version"], 1)

    def test_someone_elses_apply_first_is_a_reload(self):
        self.edit()
        self.page.locator('#pr-B1 [data-step="d"]').drag_to(self.page.locator('#pr-B1 [data-step="a"]'), target_position={"x": 3, "y": 8})
        rc, out = self.apply(self.pr("A1", template="two"), self.pr("B1", template="two", after=["A1"]), slots=3,
                             templates={"two": "kit/templates/two.json"})                  # another terminal: v2
        self.assertIn("OK flow v2", out)
        self.page.click("#apply")
        self.page.wait_for_selector("#notice >> text=Someone else changed the flow (v2): reload and redo")
        self.page.wait_for_selector("text=Editing v2: no local edit")
        self.assertEqual(self.chips("B1"), ["a", "b", "c", "d"])
        self.assertEqual(self.page.input_value("#slots"), "3")
        self.assertEqual(self.flow()["version"], 2)


class Screenshots(unittest.TestCase):
    """View mode at 1400 px light and 900 px dark, from a fixed state, against the reference screenshots."""

    def setUp(self):
        self.browser = browser()

    def shot(self, width, scheme):
        ctx = self.browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, timezone_id="UTC", locale="en-GB")
        page = ctx.new_page()
        page.clock.set_fixed_time(FIXED_NOW)
        files = {"/": PAGE / "index.html", "/page/page.js": PAGE / "page.js", "/page/page.css": PAGE / "page.css", "/state": FIXTURES / "state.json"}
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json"}

        def serve(route):
            path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
            f = files.get(path)
            route.fulfill(status=200 if f else 404, body=f.read_bytes() if f else b"", content_type=types[f.suffix] if f else "text/plain")
        page.route("**/*", serve)   # nothing leaves the test: the page and its state come from the files above
        page.goto("http://127.0.0.1:9/")
        page.wait_for_selector("#pr-A1")
        png = page.screenshot(full_page=True)
        ctx.close()
        return png

    def differ(self, a, b):
        """(size of a, size of b, pixels that differ by more than the tolerance, pixels) drawn in a canvas."""
        page = self.browser.new_page()
        try:
            return page.evaluate("""async ([a, b, limit]) => {
              const load = src => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; });
              const px = i => { const c = document.createElement('canvas'); c.width = i.width; c.height = i.height;
                                const g = c.getContext('2d'); g.drawImage(i, 0, 0); return g.getImageData(0, 0, i.width, i.height).data; };
              const [x, y] = await Promise.all([load(a), load(b)]);
              if (x.width !== y.width || x.height !== y.height) return [[x.width, x.height], [y.width, y.height], -1, 0];
              const p = px(x), q = px(y);
              let n = 0;
              for (let i = 0; i < p.length; i += 4)
                if (Math.abs(p[i] - q[i]) > limit || Math.abs(p[i + 1] - q[i + 1]) > limit || Math.abs(p[i + 2] - q[i + 2]) > limit) n++;
              return [[x.width, x.height], [y.width, y.height], n, p.length / 4];
            }""", ["data:image/png;base64," + base64.b64encode(a).decode(), "data:image/png;base64," + base64.b64encode(b).decode(), TOLERANCE[0]])
        finally:
            page.close()

    def check(self, name, width, scheme):
        ref = FIXTURES / name
        png = self.shot(width, scheme)
        if os.environ.get("FLOWS_UPDATE_SNAPSHOTS") == "1" or not ref.exists():
            ref.write_bytes(png)
            if os.environ.get("FLOWS_UPDATE_SNAPSHOTS") != "1":
                self.fail(f"{ref.name} was missing and has been written: look at it, then run again")
            return
        size, ref_size, n, total = self.differ(png, ref.read_bytes())
        out = Path(tempfile.gettempdir()) / f"flows-page-actual-{name}"
        if n < 0 or n > TOLERANCE[1] * total:
            out.write_bytes(png)
            self.fail(f"{name}: " + (f"size {size} is not the reference's {ref_size}" if n < 0 else
                                     f"{n} of {total} pixels differ ({100 * n / total:.2f}%, at most {100 * TOLERANCE[1]:.0f}%)")
                      + f"; this run's screenshot: {out}")
        out.unlink(missing_ok=True)

    def test_screenshot_view_1400_light(self):
        self.check("view-1400-light.png", 1400, "light")

    def test_screenshot_view_900_dark(self):
        self.check("view-900-dark.png", 900, "dark")


if __name__ == "__main__":
    unittest.main(verbosity=2)
