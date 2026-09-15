"""Fail-closed transcript and filesystem evidence checks (stdlib, Python >= 3.9).

This audits what a CLI reports; it is not an OS security boundary against a malicious
executable. Unknown events/tools are deliberately rejected rather than guessed at.
"""
import hashlib
import glob
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
from urllib.parse import unquote


class Invalid(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Invalid(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    with open(path, "rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value):
    raise Invalid("non-JSON numeric constant")


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(), object_pairs_hook=unique_object, parse_constant=invalid_constant)
        require(isinstance(value, dict), "evidence must be a JSON object")
        return value
    except (OSError, ValueError) as exc:
        raise Invalid("missing or invalid evidence: " + str(path)) from exc


def within(path, root):
    return path == root or root in path.parents


def repo_root(mission):
    result = subprocess.run(["git", "-C", str(mission), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, "mission must be in a Git repository")
    return Path(result.stdout.strip()).resolve()


# Exact workflow artifacts only. Arbitrary files under crosscheck/ and VOID* in
# the mission remain protected. Raw evidence and snapshots belong outside the repo.
ARTIFACTS = {"progress.json", "progress.md", "report.html",
             "contract-pass1-report.md", "design-pass1-report.md", "design-pass2-report.md"}

# The two repository trees the reviewer may never reach, relative to the repo root.
FORBIDDEN = (".missions", "docs/plans")
FORBIDDEN_RE = re.compile("|".join(r"(?:^|/)" + re.escape(name) + r"(?:/|$)" for name in FORBIDDEN))
FORBIDDEN_MENTIONS = {name + suffix for name in FORBIDDEN for suffix in ("", "/**")}
TOKEN_RE = re.compile(r"[^\s<>\"'`\[\]()]+")
WARNING_RE = re.compile(r"(?i)^\s*(?:[-*]\s*)?(?:warning:\s*)?(?:do not|never|must not)\s+"
                        r"(?:read|open|search|inspect|access|enumerate|follow|cite)\b")
CITATION_RE = re.compile(r"(?:\[verified:\s*([^\]]+)\]|\]\(([^)]+)\))")
# Punctuation around a cited path, then its :line[:col][-end] suffix, then punctuation again.
LEADING, TRAILING = "`'\"[({", "`'\"])},;:."
SUFFIX_RE = re.compile(r":\d+(?::\d+)?(?:-\d+)?$")
INFORMATIONAL = {"status", "compact_boundary", "thinking_tokens", "api_retry", "informational", "notification"}

# Executables such as rg can themselves run code (--pre). Only the documented
# read/search subset is auditable, including its options. Combined short options
# (-rn, -nC3) are split; an attached value (-C3, -n20) is separated.
COMMANDS = ("cat", "head", "tail", "nl", "wc", "rg", "grep", "ls", "pwd", "sed", "cd")
SEARCH = ("rg", "grep")
SWITCHES = {
    "cat": {"-n", "-b", "-s", "-v", "-A", "--number"},
    "head": set(), "tail": set(), "nl": set(),
    "wc": {"-l", "-c", "-w", "-m", "-L"},
    "pwd": {"-L", "-P"}, "sed": {"-n"},
    "ls": {"-a", "-l", "-A", "-1", "-d"},
    "rg": {"--files", "--hidden", "--no-ignore", "--no-ignore-vcs", "--no-ignore-parent",
           "--no-ignore-global", "--line-number", "--no-heading", "--files-with-matches",
           "--count", "--fixed-strings", "--ignore-case", "--smart-case", "--only-matching",
           "--with-filename", "-n", "-H", "-i", "-l", "-S", "-F", "-w", "-c", "-o"},
    "grep": {"-r", "-n", "-H", "-i", "-l", "-F", "-E", "-w", "-c", "-o",
             "--recursive", "--line-number", "--files-with-matches", "--fixed-strings"},
}
VALUES = {
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
    "nl": {"-b", "-n", "-w"}, "sed": {"-e"},
    "rg": {"-g", "--glob", "--iglob", "-e", "--regexp", "-f", "--file", "-m", "--max-count",
           "-A", "-B", "-C", "--context", "--max-depth", "-t", "--type", "-T", "--type-not"},
    "grep": {"--exclude", "--exclude-dir", "-e", "--regexp", "-f", "--file", "-m", "-A", "-B", "-C"},
}
FILE_VALUES = {"-f", "--file"}
PATTERN_FLAGS = {"-e", "--regexp", "-f", "--file", "--files"}


def walk_error(error):
    raise Invalid("cannot enumerate repository content for audit") from error


def content_state(repo, mission):
    files = {}
    for directory, dirs, names in os.walk(repo, onerror=walk_error):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if base / d != repo / ".git")
        for name in sorted(names + [d for d in dirs if (base / d).is_symlink()]):
            path = base / name
            if path == repo / ".git" or (path.parent == mission / "crosscheck" and name in ARTIFACTS):
                continue
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if path.is_symlink():
                value = {"link": os.readlink(path), "resolved": str(path.resolve()), "mode": mode}
                if path.is_file():
                    value["sha256"] = file_hash(path)
            else:
                require(stat.S_ISREG(info.st_mode), "unauditable special file: " + str(path))
                value = {"sha256": file_hash(path), "mode": mode}
            files[str(path.relative_to(repo))] = value
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                          capture_output=True, text=True, timeout=15)
    index = subprocess.run(["git", "-C", str(repo), "ls-files", "--stage", "-z"],
                           capture_output=True, timeout=15, check=True)
    branch = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "-q", "HEAD"],
                            capture_output=True, text=True, timeout=15)
    return {"version": 1, "repo": str(repo), "mission": str(mission), "files": files,
            "head": head.stdout.strip(), "branch": branch.stdout.strip(),
            "index_sha256": digest(index.stdout)}


def check_snapshot(snapshot, repo, mission):
    baseline = read_json(Path(snapshot) / "snapshot.json")
    require(baseline == content_state(repo, mission), "repository or protected mission content changed")


class Access:
    def __init__(self, repo, package, cwd=None, extra_reads=()):
        self.repo, self.package = Path(repo).resolve(), Path(package).resolve()
        self.cwd = Path(cwd or repo).resolve()
        self.extra_reads = {Path(p).resolve() for p in extra_reads}
        self.forbidden = [self.repo / name for name in FORBIDDEN]
        self.git = self.repo / ".git"
        # The audited tree is static, so path verdicts and discovery walks are memoized.
        self.verdicts, self.discovered = {}, set()
        self.inodes = set()
        for root in self.forbidden:
            for directory, _, names in os.walk(root):
                for name in names:
                    p = Path(directory) / name
                    if p.is_file():
                        info = p.stat()
                        self.inodes.add((info.st_dev, info.st_ino))

    def absolute(self, raw, cwd=None):
        p = Path(raw)
        if raw.startswith("~"):
            try:
                p = p.expanduser()
            except RuntimeError:
                pass  # bash leaves an unknown ~user literal
        return p if p.is_absolute() else (cwd or self.cwd) / p

    def allowed(self, resolved):
        return any(within(resolved, root) for root in (self.repo, self.package, self.cwd)) or resolved in self.extra_reads

    def forbidden_path(self, raw, cwd=None):
        key = (raw, cwd)
        if key not in self.verdicts:
            self.verdicts[key] = self._forbidden_path(raw, cwd)
        return self.verdicts[key]

    def _forbidden_path(self, raw, cwd):
        raw = unquote(raw).removeprefix("file://").lstrip(LEADING).rstrip(TRAILING)
        raw = SUFFIX_RE.sub("", raw).rstrip(TRAILING)
        if not raw:
            return False
        if FORBIDDEN_RE.search(raw):
            return True
        p = self.absolute(raw, cwd).resolve()
        if any(within(p, root.resolve()) for root in self.forbidden):
            return True
        if p.is_file():
            info = p.stat()
            return (info.st_dev, info.st_ino) in self.inodes
        return False

    def names_sealed(self, raw):
        # Relative citations are checked against both the launch directory and the repo.
        return self.forbidden_path(raw) or (not Path(raw).is_absolute() and self.forbidden_path(raw, self.repo))

    def readable(self, resolved, reason):
        require(not within(resolved, self.git), "access to repository internals")
        require(self.allowed(resolved), reason)

    def path(self, raw, cwd=None, discovery=False):
        require(isinstance(raw, str) and raw, "missing access path")
        require("{" not in raw and "}" not in raw, "unauditable brace expansion in path")
        require(not self.forbidden_path(raw, cwd), "access to sealed material")
        expanded = self.absolute(raw, cwd)
        self.readable(expanded.resolve(), "access outside repository and designated review inputs")
        if glob.has_magic(str(expanded)):
            for match in glob.glob(str(expanded), recursive=True):
                require(not self.forbidden_path(match, cwd), "glob reaches sealed material")
                self.readable(Path(match).resolve(), "glob reaches an unsealed external input")
                if discovery and Path(match).is_dir():
                    self.discover(Path(match).resolve())
        elif discovery and expanded.is_dir():
            self.discover(expanded.resolve())

    def discover(self, root):
        # Searches and listings enumerate a directory; a hardlink or symlink inside it
        # aliases sealed material even without spelling a sealed root. External symlink
        # targets are not followed by rg/grep/ls without -L/-R, which are rejected, and a
        # direct read through one is refused by path().
        if root in self.discovered:
            return
        for directory, dirs, names in os.walk(root):
            base = Path(directory)
            dirs[:] = [d for d in dirs if d not in (".git", ".missions") and base / d != self.repo / "docs/plans"]
            for name in dirs + names:
                target = base / name
                if target.is_symlink() or (target.is_file() and target.stat().st_nlink > 1):
                    require(not self.forbidden_path(str(target)), "search can reach a sealed alias")
        self.discovered.add(root)

    def citations(self, text, output=False):
        # Plain warnings naming the two forbidden trees are allowed. Paths to files,
        # verified tags and Markdown links are evidence of contamination. In tool output
        # a colon-free relative token inside prose is a mention, not a read: rg/grep print
        # `path:content` and listings print one path per line, and those stay strict.
        require(isinstance(text, str), "invalid tool output text")
        for pair in CITATION_RE.findall(text):
            for raw in pair:
                require(not raw or not self.names_sealed(raw), "citation or output names sealed material")
        for line in text.splitlines():
            warning = WARNING_RE.match(line)
            tokens = TOKEN_RE.findall(line)
            for raw in tokens:
                if raw.rstrip("/.,:;") in FORBIDDEN_MENTIONS:
                    continue
                if warning and not re.search(r":\d+", raw):
                    continue
                if output and ":" not in raw and len(tokens) > 1 and not raw.startswith(("/", "~")):
                    continue
                require(not self.names_sealed(raw), "citation or output names sealed material")

    def shell(self, command, cwd=None):
        require(isinstance(command, str) and command, "missing shell command")
        cwd = Path(cwd or self.cwd).resolve()
        # Codex wraps commands in a shell. Unwrap only a single literal -c/-lc.
        try:
            words = shlex.split(command)
        except ValueError as exc:
            raise Invalid("malformed shell command") from exc
        if len(words) == 3 and Path(words[0]).name in ("bash", "sh", "zsh") and words[1] in ("-c", "-lc"):
            return self.shell(words[2], cwd)
        require(not re.search(r"[$`<>{}\n]", command), "unauditable shell expansion or redirection")
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        lex.commenters = ""
        parts, part = [], []
        for word in lex:
            if word in (";", "&&", "||", "|"):
                require(part, "empty shell command")
                parts.append(part)
                part = []
            else:
                require(not re.fullmatch(r"[;&|()]+", word), "unauditable shell control flow")
                part.append(word)
        if part:
            parts.append(part)
        for words in parts:
            name, args = words[0], words[1:]
            require(name in COMMANDS, "unknown or non-read-only shell command")
            if name == "cd":
                require(len(args) == 1, "unauditable cd")
                self.path(args[0], cwd)
                cwd = self.absolute(args[0], cwd).resolve()
            else:
                self.command(name, args, cwd)

    def options(self, name, args):
        """Split argv into (flag, value) options and positional operands."""
        switches, values = SWITCHES[name], VALUES.get(name, set())
        options, positional, queue = [], [], list(args)
        while queue:
            arg = queue.pop(0)
            if arg == "--":
                positional.extend(queue)
                break
            if arg in values:
                require(queue, "missing option value")
                options.append((arg, queue.pop(0)))
            elif arg.startswith("--") and "=" in arg:
                flag, value = arg.split("=", 1)
                require(flag in values, "unauditable shell option")
                options.append((flag, value))
            elif name in ("head", "tail") and re.fullmatch(r"-\d+", arg):
                options.append(("-n", arg[1:]))
            elif re.fullmatch(r"-[A-Za-z].+", arg):
                flag, rest = arg[:2], arg[2:]
                if flag in values:
                    options.append((flag, rest))
                else:
                    require(flag in switches, "unauditable shell option")
                    options.append((flag, None))
                    queue.insert(0, "-" + rest)
            elif arg.startswith("-") and arg != "-":
                require(arg in switches, "unauditable shell option")
                options.append((arg, None))
            else:
                positional.append(arg)
        return options, positional

    def command(self, name, args, cwd):
        options, positional = self.options(name, args)
        if name == "sed":
            scripts = [value for flag, value in options if flag == "-e"]
            if not scripts:
                require(positional, "unauditable sed expression")
                scripts, positional = positional[:1], positional[1:]
            require(all(re.fullmatch(r"\d+(?:,\d+)?p", script) for script in scripts), "unauditable sed expression")
        excluded, includes = [], []
        for index, (flag, value) in enumerate(options):
            if value is None:
                continue
            if flag in FILE_VALUES:
                self.path(value, cwd)
            elif name == "rg" and flag in ("-g", "--glob", "--iglob"):
                (excluded if value.startswith("!") else includes).append((index, value.lstrip("!")))
            elif name == "grep" and flag == "--exclude-dir":
                excluded.append((index, value))
        for _, value in includes:
            self.path(value, cwd)
        if name in SEARCH and positional and not any(flag in PATTERN_FLAGS for flag, _ in options):
            positional = positional[1:]  # the first operand is the regex, not a file
        listing = name in SEARCH or name == "ls"
        for arg in positional:
            self.path(arg, cwd, discovery=listing)
        if name in SEARCH:
            self.search(name, positional, includes, excluded, cwd)

    def search(self, name, positional, includes, excluded, cwd):
        # Broad searches must effectively exclude both protected trees; rg applies globs
        # to the path as it prints it, and grep's --exclude-dir matches base names only.
        roots = [arg for arg in positional if self.absolute(arg, cwd).is_dir()]
        if not roots and not any(self.absolute(arg, cwd).is_file() for arg in positional):
            roots = [None]  # no operand: the working directory is searched
        for operand in roots:
            root = (cwd if operand is None else self.absolute(operand, cwd)).resolve()
            self.discover(root)
            for tree in self.forbidden:
                if within(tree, root):
                    require(all(i < j for i, _ in includes for j, _ in excluded), "include glob after a sealed-tree exclusion")
                    require(self.excludes(name, excluded, operand, cwd, root, tree),
                            "unbounded discovery without both sealed-tree exclusions")

    def excludes(self, name, excluded, operand, cwd, root, tree):
        rel = tree.relative_to(root)
        chain = [Path(*rel.parts[:i + 1]) for i in range(len(rel.parts))]
        if operand is None or operand == ".":
            printed = ""
        elif operand.startswith("~") or Path(operand).is_absolute():
            expanded = self.absolute(operand, cwd)
            printed = "" if expanded == cwd else str(expanded.relative_to(cwd)) if within(expanded, cwd) else str(expanded)
        else:
            printed = operand[2:] if operand.startswith("./") else operand
        # rg prints operands verbatim, so anchored globs cannot be trusted past `.`/`..` segments.
        literal = not re.search(r"(?:^|/)\.\.?(?:/|$)", printed)

        def prints(directory):
            return str(directory) if not printed else printed.rstrip("/") + "/" + str(directory)

        for _, text in excluded:
            if name == "grep" or "/" not in text:
                if not glob.has_magic(text) and "/" not in text and text in rel.parts:
                    return True
                continue
            anchored = not text.startswith("**/")
            body = text if anchored else text[3:]
            body = body[:-3] if body.endswith("/**") else body.rstrip("/")
            if not body or body.startswith("/") or glob.has_magic(body) or "{" in body:
                continue
            if anchored and literal and any(prints(d) == body for d in chain):
                return True
            if not anchored and any(prints(d) == body or prints(d).endswith("/" + body) for d in chain):
                return True
        return False

    def native(self, name, args):
        require(isinstance(args, dict), "invalid tool arguments")
        if name == "Read":
            self.path(args.get("file_path"))
        elif name in ("Glob", "Grep"):
            root = args.get("path", str(self.cwd))
            self.path(root, discovery=True)
            pattern = args.get("pattern")
            require(isinstance(pattern, str) and pattern, "missing search pattern")
            if name == "Glob":
                self.path(pattern, self.absolute(root))
            else:
                self.citations(pattern)
            # Claude tools have no reliable per-call directory exclusion contract.
            # Restrict them to explicit safe subtrees/files, named in TASK.md.
            resolved = self.absolute(root).resolve()
            require(not any(within(f, resolved) for f in self.forbidden), "native discovery includes sealed trees")
        elif name == "Bash":
            self.shell(args.get("command"))
        else:
            raise Invalid("unknown or non-read-only tool")


def events(raw):
    require(raw.endswith("\n"), "truncated stream")
    out = []
    for line in raw[:-1].split("\n"):  # not splitlines(): JSON strings may carry U+2028
        require(bool(line.strip()), "empty stream event")
        try:
            ev = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        except ValueError as exc:
            raise Invalid("malformed JSON stream") from exc
        require(isinstance(ev, dict) and isinstance(ev.get("type"), str), "invalid stream event")
        out.append(ev)
    return out


def claude_report(raw, access, expected_session=None):
    stream = events(raw)
    first = stream[0]
    require(first.get("type") == "system" and first.get("subtype") == "init", "missing Claude init")
    session = first.get("session_id")
    require(isinstance(session, str) and session, "missing Claude session")
    require(expected_session is None or session == expected_session, "unexpected Claude session")
    require(isinstance(first.get("tools"), list) and all(isinstance(t, str) for t in first["tools"]) and
            set(first["tools"]) <= {"Read", "Grep", "Glob"}, "unexpected enabled Claude tools")
    require(isinstance(first.get("model"), str) and first["model"].startswith("claude-"), "unknown Claude model provider")
    pending, seen, report, terminal = set(), set(), "", False
    for ev in stream[1:]:
        require(not terminal, "events after terminal result")
        kind = ev["type"]
        if kind == "system":
            # Progress metadata the CLI emits on honest runs; it carries no tool activity.
            require(ev.get("subtype") in INFORMATIONAL and ev.get("session_id", session) == session, "unknown Claude event")
            continue
        require(ev.get("session_id") == session and not ev.get("parent_tool_use_id"), "inconsistent Claude session")
        if kind in ("assistant", "user"):
            require(not ev.get("error") and not ev.get("origin"), "errored or injected Claude message")
            message = ev.get("message")
            require(isinstance(message, dict) and message.get("role") == kind and isinstance(message.get("content"), list), "invalid Claude message")
            for block in message["content"]:
                require(isinstance(block, dict), "invalid Claude content")
                t = block.get("type")
                if t == "tool_use" and kind == "assistant":
                    key = block.get("id")
                    require(isinstance(key, str) and key and key not in seen, "duplicate or missing tool id")
                    access.native(block.get("name"), block.get("input"))
                    require(block["name"] in first["tools"], "tool activity outside enabled tools")
                    seen.add(key)
                    pending.add(key)
                elif t == "tool_result" and kind == "user":
                    key = block.get("tool_use_id")
                    require(key in pending and "content" in block, "unresolved or duplicate tool result")
                    content = block["content"]
                    require(isinstance(content, (str, list)), "invalid tool result content")
                    if isinstance(content, list):
                        require(all(isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
                                    for b in content), "unauditable tool result")
                        content = "\n".join(b["text"] for b in content)
                    access.citations(content, output=True)
                    pending.remove(key)
                elif t == "text" and kind == "assistant" and isinstance(block.get("text"), str):
                    access.citations(block["text"])
                elif t == "thinking" and kind == "assistant":
                    access.citations(block.get("thinking"))
                elif t == "redacted_thinking" and kind == "assistant" and isinstance(block.get("data"), str):
                    continue
                else:
                    raise Invalid("unknown Claude content block")
        elif kind == "result":
            require(ev.get("subtype") == "success" and ev.get("is_error") is False and not pending,
                    "unsuccessful result or unresolved tools")
            require(not ev.get("deferred_tool_use") and not ev.get("permission_denials") and not ev.get("errors"),
                    "incomplete or denied tool activity")
            usage = ev.get("modelUsage", {})
            require(isinstance(usage, dict) and all(isinstance(value, dict) and value.get("provider", "firstParty") == "firstParty"
                                                  for value in usage.values()), "unverified Claude serving provider")
            require(seen, "review read nothing: no tool activity")
            report = ev.get("result")
            require(isinstance(report, str) and report.strip(), "empty Claude report")
            access.citations(report)
            terminal = True
        elif kind == "rate_limit_event":
            # Documented CLI metadata, not tool activity. A terminal success is
            # still required even if a transient limit was subsequently resolved.
            info = ev.get("rate_limit_info")
            require(isinstance(info, dict) and info.get("status") in ("allowed", "allowed_warning", "rejected") and
                    isinstance(ev.get("uuid"), str), "invalid rate limit event")
        else:
            raise Invalid("unknown Claude event")
    require(terminal, "missing Claude terminal result")
    return report


def codex_report(raw, access):
    require(raw.startswith("{"), "legacy Codex text cannot establish structured completion; rerun with JSONL")
    stream = events(raw)
    require(stream[0]["type"] == "thread.started" and isinstance(stream[0].get("thread_id"), str) and
            stream[0]["thread_id"], "missing Codex thread")
    session = stream[0]["thread_id"]
    pending, finished, report, turn, terminal, succeeded = {}, set(), "", False, False, False
    for ev in stream[1:]:
        require(not terminal, "events after terminal result")
        require(ev.get("thread_id", session) == session, "inconsistent Codex thread")
        kind = ev["type"]
        if kind == "turn.started":
            require(not turn, "duplicate Codex turn")
            turn = True
        elif kind == "error":
            # Transport retries ("Reconnecting..."); a completed turn is still required.
            require(isinstance(ev.get("message"), str), "invalid Codex error event")
        elif kind in ("item.started", "item.updated", "item.completed"):
            require(turn, "item outside turn")
            item = ev.get("item")
            require(isinstance(item, dict) and isinstance(item.get("id"), str), "invalid Codex item")
            key, t = item["id"], item.get("type")
            require(key not in finished, "repeated completed item")
            require(t in ("command_execution", "agent_message", "reasoning", "todo_list"), "unknown or non-read-only Codex item")
            if t == "command_execution":
                access.shell(item.get("command"))
                access.citations(item.get("aggregated_output", ""), output=True)
            elif t == "agent_message":
                require(isinstance(item.get("text"), str), "invalid agent message")
                access.citations(item["text"])
            elif t == "reasoning":
                access.citations(item.get("text"))
            else:
                entries = item.get("items", [])
                require(isinstance(entries, list) and all(isinstance(e, dict) and isinstance(e.get("text"), str) for e in entries),
                        "invalid Codex todo list")
                for entry in entries:
                    access.citations(entry["text"])
            if kind == "item.started":
                require(key not in pending, "duplicate item start")
                pending[key] = (t, item.get("command"))
            elif kind == "item.updated":
                require(key in pending and pending[key] == (t, item.get("command")), "unmatched item update")
            else:
                if t == "command_execution":
                    require(pending.get(key) == (t, item.get("command")) and
                            type(item.get("exit_code")) is int and item.get("status") in ("completed", "failed"),
                            "unresolved command")
                    succeeded = succeeded or (item["status"] == "completed" and item["exit_code"] == 0)
                if t == "agent_message":
                    report = item["text"]
                pending.pop(key, None)
                finished.add(key)
        elif kind == "turn.completed":
            require(turn and not pending and isinstance(ev.get("usage"), dict), "unresolved Codex turn")
            terminal = True
        else:
            raise Invalid("unknown or failed Codex event")
    require(terminal and report.strip(), "missing Codex completion/report")
    require(succeeded, "review read nothing: no command succeeded")
    return report
