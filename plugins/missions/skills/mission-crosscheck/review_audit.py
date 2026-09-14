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
ARTIFACTS = {"progress.json", "progress.md", "pass1-report.md", "pass2-report.md", "report.html"}

# The two repository trees the reviewer may never reach, relative to the repo root.
FORBIDDEN = (".missions", "docs/plans")
FORBIDDEN_RE = re.compile("|".join(r"(?:^|/)" + re.escape(name) + r"(?:/|$)" for name in FORBIDDEN))
FORBIDDEN_MENTIONS = {name + suffix for name in FORBIDDEN for suffix in ("", "/**")}
TOKEN_RE = re.compile(r"[^\s<>\"'`\[\]()]+")
WARNING_RE = re.compile(r"(?i)^\s*(?:[-*]\s*)?(?:warning:\s*)?(?:do not|never|must not)\s+"
                        r"(?:read|open|search|inspect|access|enumerate|follow|cite)\b")
CITATION_RE = re.compile(r"(?:\[verified:\s*([^\]]+)\]|\]\(([^)]+)\))")

# Executables such as rg can themselves run code (--pre). Only the documented
# read/search subset is auditable, including its options.
SWITCHES = {
    "cat": {"-n", "-b", "-s", "-v", "-A", "--number"},
    "head": set(), "tail": set(),
    "nl": {"-ba", "-bt"}, "wc": {"-l", "-c", "-w", "-m", "-L"},
    "pwd": {"-L", "-P"}, "sed": {"-n"},
    "ls": {"-a", "-l", "-la", "-al", "-A", "-1", "-d"},
    "rg": {"--files", "--hidden", "--no-ignore", "--no-ignore-vcs", "--no-ignore-parent",
           "--no-ignore-global", "--line-number", "--no-heading", "--files-with-matches",
           "--count", "--fixed-strings", "--ignore-case", "--smart-case", "--only-matching",
           "--with-filename", "-n", "-H", "-i", "-l", "-S", "-F", "-w", "-c", "-o"},
    "grep": {"-r", "-n", "-H", "-i", "-l", "-F", "-E", "-w", "-c", "-o",
             "--recursive", "--line-number", "--files-with-matches", "--fixed-strings"},
}
VALUES = {
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"}, "sed": {"-e"},
    "rg": {"-g", "--glob", "--iglob", "-e", "--regexp", "-f", "--file", "-m", "--max-count",
           "-A", "-B", "-C", "--context", "--max-depth", "-t", "--type", "-T", "--type-not"},
    "grep": {"--exclude", "--exclude-dir", "-e", "--regexp", "-f", "--file", "-m", "-A", "-B", "-C"},
}


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
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (cwd or self.cwd) / p

    def allowed(self, resolved):
        return any(within(resolved, root) for root in (self.repo, self.package, self.cwd)) or resolved in self.extra_reads

    def forbidden_path(self, raw, cwd=None):
        key = (raw, cwd)
        if key not in self.verdicts:
            self.verdicts[key] = self._forbidden_path(raw, cwd)
        return self.verdicts[key]

    def _forbidden_path(self, raw, cwd):
        raw = unquote(raw).removeprefix("file://")
        raw = re.sub(r":\d+(?::\d+)?(?:-\d+)?$", "", raw).strip("`'\"[](),;")
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

    def path(self, raw, cwd=None, discovery=False):
        require(isinstance(raw, str) and raw, "missing access path")
        require(not self.forbidden_path(raw, cwd), "access to sealed material")
        expanded = self.absolute(raw, cwd)
        require(self.allowed(expanded.resolve()), "access outside repository and designated review inputs")
        if glob.has_magic(str(expanded)):
            for match in glob.glob(str(expanded), recursive=True):
                require(not self.forbidden_path(match, cwd), "glob reaches sealed material")
                require(self.allowed(Path(match).resolve()), "glob reaches an unsealed external input")
        if discovery:
            self.discover(Path(re.split(r"[?*\[]", str(self.absolute(raw, cwd)), maxsplit=1)[0]))

    def discover(self, root):
        # Globs and searches can reach aliases even without spelling a sealed root.
        if not root.is_dir():
            root = root.parent
        if root in self.discovered:
            return
        for directory, dirs, names in os.walk(root):
            base = Path(directory)
            dirs[:] = [d for d in dirs if d not in (".git", ".missions") and base / d != self.repo / "docs/plans"]
            for name in dirs + names:
                target = base / name
                if target.is_symlink() or (target.is_file() and target.stat().st_nlink > 1):
                    require(not self.forbidden_path(str(target)), "search can reach a sealed alias")
                    require(any(within(target.resolve(), r) for r in (self.repo, self.package, self.cwd)),
                            "search can reach an unsealed external alias")
        self.discovered.add(root)

    def citations(self, text):
        # Plain warnings naming the two forbidden trees are allowed. Paths to files,
        # verified tags and Markdown links are evidence of contamination.
        require(isinstance(text, str), "invalid tool output text")
        for pair in CITATION_RE.findall(text):
            for raw in pair:
                require(not raw or not self.names_sealed(raw), "citation or output names sealed material")
        for line in text.splitlines():
            warning = WARNING_RE.match(line)
            for raw in TOKEN_RE.findall(line):
                if raw.rstrip("/.,:;") in FORBIDDEN_MENTIONS:
                    continue
                if warning and not re.search(r":\d+", raw):
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
        require(not re.search(r"[$`<>{}\n\\]", command), "unauditable shell expansion or redirection")
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
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
            require(name in ("cat", "head", "tail", "nl", "wc", "rg", "grep", "ls", "pwd", "sed", "cd"),
                    "unknown or non-read-only shell command")
            if name == "cd":
                require(len(args) == 1, "unauditable cd")
                self.path(args[0], cwd)
                cwd = (cwd / args[0]).resolve()
                continue
            switches, values = SWITCHES[name], VALUES.get(name, set())
            normalized, i = [], 0
            while i < len(args):
                arg = args[i]
                if arg == "--":
                    normalized.extend(args[i + 1:])
                    break
                if arg in values:
                    require(i + 1 < len(args), "missing option value")
                    normalized.extend([arg, args[i + 1]])
                    i += 2
                    continue
                if arg.startswith("--") and "=" in arg:
                    flag, value = arg.split("=", 1)
                    require(flag in values, "unauditable shell option")
                    normalized.extend([flag, value])
                elif name == "rg" and arg.startswith("-g") and len(arg) > 2:
                    normalized.extend(["-g", arg[2:]])
                elif re.fullmatch(r"-(?:n|c)\d+", arg) and name in ("head", "tail"):
                    normalized.extend([arg[:2], arg[2:]])
                else:
                    require(not arg.startswith("-") or arg in switches, "unauditable shell option")
                    normalized.append(arg)
                i += 1
            args = normalized
            # Drop only real exclusion operands, never the entire command containing one.
            effective, excluded, i = [], [], 0
            while i < len(args):
                arg = args[i]
                if name in ("rg", "grep") and arg in ("--glob", "-g", "--iglob", "--exclude", "--exclude-dir"):
                    require(i + 1 < len(args), "missing search operand")
                    value = args[i + 1]
                    if arg.startswith("--exclude") or value.startswith("!"):
                        excluded.append(value.lstrip("!"))
                        i += 2
                        continue
                if name in ("rg", "grep") and (arg.startswith(("--exclude=", "--exclude-dir=", "--glob=!", "--iglob=!", "-g!"))):
                    excluded.append(arg.split("=", 1)[1].lstrip("!") if "=" in arg else arg[3:])
                    i += 1
                    continue
                effective.append(arg)
                i += 1
            if name == "sed":
                require(args and not any(a.startswith("-i") for a in args), "sed writes are forbidden")
                expr = next((a for a in args if not a.startswith("-")), "")
                require(re.fullmatch(r"\d+(?:,\d+)?p", expr) is not None, "unauditable sed expression")
            searching = name in ("rg", "grep", "ls")
            for arg in effective:
                if not arg.startswith("-"):
                    self.path(arg, cwd, discovery=searching and (cwd / arg).exists())
            if name in ("rg", "grep"):
                # Broad searches must explicitly exclude both protected trees.
                roots = [(cwd / a).resolve() for a in effective if not a.startswith("-") and (cwd / a).is_dir()]
                if not roots and not any((cwd / a).is_file() for a in effective if not a.startswith("-")):
                    roots = [cwd]
                if any(within(f, root) for root in roots for f in self.forbidden):
                    require(all(any(x in excluded for x in (tree + "/**", "**/" + tree + "/**")) for tree in FORBIDDEN),
                            "unbounded discovery without both sealed-tree exclusions")

    def native(self, name, args):
        require(isinstance(args, dict), "invalid tool arguments")
        if name == "Read":
            self.path(args.get("file_path"))
        elif name in ("Glob", "Grep"):
            self.path(args.get("path", str(self.cwd)), discovery=True)
            pattern = args.get("pattern")
            require(isinstance(pattern, str), "missing search pattern")
            self.path(pattern)
            # Claude tools have no reliable per-call directory exclusion contract.
            # Restrict them to explicit safe subtrees/files, named in TASK.md.
            root = Path(args.get("path", str(self.cwd)))
            if not root.is_absolute():
                root = self.cwd / root
            root = root.resolve()
            require(not any(within(f, root) for f in self.forbidden), "native discovery includes sealed trees")
        elif name == "Bash":
            self.shell(args.get("command"))
        else:
            raise Invalid("unknown or non-read-only tool")


def events(raw):
    require(raw.endswith("\n"), "truncated stream")
    out = []
    for line in raw.splitlines():
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
        require(ev.get("session_id") == session and not ev.get("parent_tool_use_id"), "inconsistent Claude session")
        kind = ev["type"]
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
                    access.citations(content)
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
    pending, finished, report, turn, terminal = {}, set(), "", False, False
    for ev in stream[1:]:
        require(not terminal, "events after terminal result")
        require(ev.get("thread_id", session) == session, "inconsistent Codex thread")
        kind = ev["type"]
        if kind == "turn.started":
            require(not turn, "duplicate Codex turn")
            turn = True
        elif kind in ("item.started", "item.updated", "item.completed"):
            require(turn, "item outside turn")
            item = ev.get("item")
            require(isinstance(item, dict) and isinstance(item.get("id"), str), "invalid Codex item")
            key, t = item["id"], item.get("type")
            require(key not in finished, "repeated completed item")
            require(t in ("command_execution", "agent_message", "reasoning"), "unknown or non-read-only Codex item")
            if t == "command_execution":
                access.shell(item.get("command"))
                access.citations(item.get("aggregated_output", ""))
            elif t == "agent_message":
                require(isinstance(item.get("text"), str), "invalid agent message")
                access.citations(item["text"])
            elif t == "reasoning":
                access.citations(item.get("text"))
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
    return report
