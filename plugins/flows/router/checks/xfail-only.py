#!/usr/bin/env python3
"""xfail-only.py <worktree> <from> <to> [<path prefix> ...]        (default prefix: tests/)

Prints one line and exits 0 (OK) or 1 (NOT OK). OK when, between the commits <from> and <to>, the only change
under the prefixes is the removal of pytest xfail markers, plus imports the removal left unused.

Why: the implementer may remove the test writer's xfail markers and make no other test edit. The coordinator
reads this line; it does not read the diff.

How it decides:
  - an uncommitted change under the prefixes is NOT OK: the check reads commits, and that edit is in neither;
  - a test file that was added, deleted or renamed is NOT OK; so is a changed file that is not Python;
  - both versions of a changed file are parsed, every xfail marker is taken out of both (decorators,
    `pytestmark`, `marks=` in pytest.param, a `pytest.xfail(...)` statement, `request.applymarker(<xfail>)`),
    import statements are taken out of both, and the two trees must then be equal. `pytest.param(x)` with
    nothing left but its values counts as `x`, and a one-marker `pytestmark` list as that marker;
  - the new version may not have an xfail marker the old one lacked, nor an import statement the old one
    lacked: the same module, name, alias and relative level. Removing an import is fine.
Comments are not part of the tree, so a changed comment passes. A changed docstring does not.

Not covered: pytest.ini and other config outside the prefixes (guard those with files-untouched.sh).
"""
from __future__ import annotations

import ast
import subprocess
import sys
from collections import Counter


def git(wt: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", wt] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr.strip().splitlines() or ["git failed"])[0])
    return p.stdout


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def is_xfail(node: ast.AST) -> bool:
    return dotted(node).split(".")[-1] == "xfail"


class Strip(ast.NodeTransformer):
    """Takes xfail markers and imports out of a tree, and remembers both."""

    def __init__(self) -> None:
        self.markers: "Counter[str]" = Counter()
        self.imports: "set[str]" = set()
        self.scope: "list[str]" = []

    def _mark(self, node: ast.AST) -> None:
        self.markers[".".join(self.scope) + " " + ast.dump(node)] += 1

    def _keep(self, nodes: "list[ast.expr]") -> "list[ast.expr]":
        kept = []
        for n in nodes:
            if is_xfail(n):
                self._mark(n)
            else:
                kept.append(n)
        return kept

    def _scoped(self, node: ast.AST) -> ast.AST:
        self.scope.append(getattr(node, "name", "?"))
        node.decorator_list = self._keep(node.decorator_list)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.scope.pop()
        return node

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    # An import is remembered whole: `import bot.lenient as impl` in place of `import bot.strict as impl` binds
    # the same name to other code, and `from ..fakes` is another module than `from .fakes`.
    def visit_Import(self, node: ast.Import) -> None:
        self.imports |= {f"import {a.name} as {a.asname or a.name}" for a in node.names}
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports |= {f"from {'.' * node.level}{node.module or ''} import {a.name} as {a.asname or a.name}" for a in node.names}
        return None

    def visit_Assign(self, node: ast.Assign) -> "ast.AST | None":
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            if is_xfail(node.value):
                self._mark(node.value)
                return None
            if isinstance(node.value, (ast.List, ast.Tuple)):
                node.value.elts = self._keep(node.value.elts)
                if not node.value.elts:
                    return None
                if len(node.value.elts) == 1:   # [xfail, asyncio] may become asyncio
                    node.value = node.value.elts[0]
        return self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> "ast.AST | None":
        call = node.value
        if isinstance(call, ast.Call):
            if dotted(call.func) in ("pytest.xfail", "xfail"):   # the imperative form
                self._mark(call)
                return None
            if dotted(call.func).split(".")[-1] == "applymarker" and len(call.args) == 1 and is_xfail(call.args[0]):
                self._mark(call.args[0])
                return None
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if dotted(node.func).split(".")[-1] == "param":
            kept = []
            for kw in node.keywords:
                if kw.arg == "marks":
                    if is_xfail(kw.value):
                        self._mark(kw.value)
                        continue
                    if isinstance(kw.value, (ast.List, ast.Tuple)):
                        kw.value.elts = self._keep(kw.value.elts)
                        if not kw.value.elts:
                            continue
                kept.append(kw)
            node.keywords = kept
            node = self.generic_visit(node)
            # pytest.param(2, marks=xfail) may become 2: with only values left, the wrapper says nothing.
            if dotted(node.func) == "pytest.param" and not node.keywords and node.args:
                return node.args[0] if len(node.args) == 1 else ast.Tuple(elts=node.args, ctx=ast.Load())
            return node
        return self.generic_visit(node)


def normal(source: str) -> "tuple[str, Counter[str], set[str]]":
    s = Strip()
    tree = s.visit(ast.parse(source))
    return ast.dump(tree), s.markers, s.imports


def judge(wt: str, a: str, b: str, path: str) -> "tuple[str, int]":
    """('' when fine, else the reason; markers removed)."""
    if not path.endswith(".py"):
        return "changed, and it is not a Python file", 0
    try:
        old, old_marks, old_imports = normal(git(wt, "show", f"{a}:{path}"))
        new, new_marks, new_imports = normal(git(wt, "show", f"{b}:{path}"))
    except SyntaxError as e:
        return f"does not parse: {e.msg} at line {e.lineno}", 0
    if new_marks - old_marks:
        return f"{sum((new_marks - old_marks).values())} xfail marker(s) added or changed", 0
    if new_imports - old_imports:
        return f"new or changed import: {sorted(new_imports - old_imports)[0]}", 0
    if old != new:
        return "changed beyond its xfail markers", 0
    return "", sum((old_marks - new_marks).values())


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(__doc__.strip())
        return 0
    if len(sys.argv) < 4:
        print("usage: xfail-only.py <worktree> <from> <to> [<path prefix> ...]", file=sys.stderr)
        return 2
    wt, a, b = sys.argv[1:4]
    prefixes = sys.argv[4:] or ["tests/"]
    try:
        dirty = [l[3:] for l in git(wt, "status", "--porcelain", "--", *prefixes).splitlines() if l]
        if dirty:
            more = f" (+{len(dirty) - 1} more)" if len(dirty) > 1 else ""
            print(f"NOT OK xfail-only: uncommitted change under {' '.join(prefixes)}: {dirty[0]}{more}")
            return 1
        rows = [l.split("\t") for l in git(wt, "diff", "--name-status", "-M", a, b, "--", *prefixes).splitlines() if l]
        problems, removed, files = [], 0, 0
        for row in rows:
            status, path = row[0], row[-1]
            if status != "M":
                kind = {"A": "added", "D": "deleted", "R": "renamed", "C": "copied"}.get(status[0], status)
                problems.append(f"{path}: {kind}")
                continue
            why, n = judge(wt, a, b, path)
            files += 1
            removed += n
            if why:
                problems.append(f"{path}: {why}")
    except RuntimeError as e:
        print(f"NOT OK xfail-only: {e}")
        return 1
    span = f"{a[:9]}..{b[:9]}"
    if problems:
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        print(f"NOT OK xfail-only {span}: {problems[0]}{more}")
        return 1
    print(f"OK xfail-only {span}: {files} test file(s) changed, {removed} xfail marker(s) removed, nothing else")
    return 0


if __name__ == "__main__":
    sys.exit(main())
