#!/usr/bin/env python3
"""Sealed cross-vendor planning reviews. Independent of the implementation driver.

CLI executables are trusted local installations, identified by their version and
capabilities and pinned by hash. Custom gateways/providers/extra CLI arguments are
unsupported. Never print reviewer output before the audit succeeds.
"""
import argparse
from contextlib import contextmanager
import fcntl
import functools
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

from review_audit import (Access, Invalid, check_snapshot, claude_report, codex_report,
                          content_state, digest, file_hash, read_json, repo_root, require, within)

VERSION = 1
SOURCES = {"contract.md": "SPEC-1-contract.md", "mission.md": "SPEC-2-scope.md",
           "features.md": "SPEC-3-decomposition.md"}
REPORTS = {"blind": "pass1-report.md", "sighted": "pass2-report.md"}


def claude_argv(binary, model, cwd, session):
    return [binary, "--print", "--output-format", "stream-json", "--verbose", "--safe-mode",
            "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
            "--no-session-persistence", "--session-id", session, "--tools", "Read,Grep,Glob",
            "--allowedTools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--model", model]


def codex_argv(binary, model, cwd, session):
    return [binary, "exec", "--cd", str(cwd), "--skip-git-repo-check", "--sandbox", "read-only",
            "--json", "--ephemeral", "--ignore-user-config", "--model", model,
            "--config", 'model_provider="openai"', "--config", 'project_doc_max_bytes=0', "-"]


def claude_auth(text):
    try:
        status = json.loads(text)
    except ValueError as exc:
        raise Invalid("unrecognized Claude authentication status") from exc
    require(isinstance(status, dict) and status.get("loggedIn") is True and
            status.get("apiProvider") == "firstParty", "Claude first-party authentication unavailable")


def codex_auth(text):
    require("Logged in using" in text, "unrecognized Codex authentication status")


# Everything vendor-specific lives here; the workflow below only indexes it.
PROVIDERS = {
    "claude": {"model": "opus", "model_pattern": r"claude-[a-z0-9.-]+|opus|sonnet|haiku|fable",
               "version_pattern": r"\S+ \(Claude Code\)", "help": ["--help"],
               "auth": ["--safe-mode", "--setting-sources", "", "auth", "status"], "auth_check": claude_auth,
               "argv": claude_argv, "report": claude_report},
    "codex": {"model": "gpt-5.4", "model_pattern": r"gpt-[0-9][a-z0-9.-]*|o[1-9][a-z0-9.-]*",
              "version_pattern": r"codex-cli \S+", "help": ["exec", "--help"],
              "auth": ["login", "status"], "auth_check": codex_auth,
              "argv": codex_argv, "report": lambda raw, access, session: codex_report(raw, access)},
}


def save(path, data):
    """Atomic evidence updates, including the process receipt before progress."""
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=".crosscheck-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@functools.lru_cache(maxsize=None)
def repo_aliases(repo):
    """Resolved symlink targets in the repository. Cleared after a reviewer runs."""
    targets = []
    for directory, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if Path(directory) / d != repo / ".git"]
        for name in dirs + names:
            alias = Path(directory) / name
            if alias.is_symlink():
                targets.append(alias.resolve())
    return tuple(targets)


def outside(path, repo):
    path = Path(path).resolve()
    require(not within(path, repo), "package and raw evidence must be outside the repository")
    # A repo symlink to the package (or its ancestor) defeats the seal too.
    for target in repo_aliases(repo):
        require(not within(path, target) and not within(target, path), "external package/evidence reachable through repository alias")
    return path


def scratch_root(repo=None):
    if repo is None:
        try:
            repo = repo_root(Path.cwd())
        except Invalid:
            pass
    for candidate in (Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp")):
        candidate = candidate.resolve()
        if candidate.is_dir() and (repo is None or not within(candidate, Path(repo))):
            return candidate
    raise Invalid("no scratch directory outside the repository")


def inputs(mode):
    return list(SOURCES) + (["design.md"] if mode == "design" else [])


def mission_at(path, mode):
    mission = Path(path).resolve()
    require(mission.is_dir(), "missing mission directory")
    repo = repo_root(mission)
    require(within(mission, repo), "mission must belong to repository")
    for name in inputs(mode):
        require((mission / name).is_file(), "missing mission input: " + name)
    state = (mission / "state.md").read_text()
    require(re.search(r"(?im)^\s*(?:[-*] )?(?:\*\*)?phase(?::\*\*|\*\*:|:)\s*`?planning`?\s*$", state),
            "crosscheck is planning phase only")
    return mission, repo


def source_hashes(mission, mode):
    return {name: file_hash(mission / name) for name in inputs(mode)}


def strip_source(name, text):
    text = re.sub(r"(?ms)^## Amendments\b[^\n]*\n.*?(?=^## |\Z)", "", text)
    if name == "features.md":
        text = re.sub(r"(?m)^- \*\*Procedures:\*\*[^\n]*(?:\n|\Z)(?:[ \t]+[^\n]*(?:\n|\Z)|\n)*", "", text)
        text = re.sub(r"(?m)^- \*\*Seat:\*\*.*\n?", "", text)
    if name == "mission.md":
        text = re.sub(r"(?im)^[-* ]*(?:\*\*)?Reviewer seat:(?:\*\*)?.*\n?", "", text)
    return text


def task_text(repo, package, mode):
    paths = "\n".join("- " + str(package / name) for name in SOURCES.values())
    roots = [p for p in sorted(repo.iterdir()) if p.name not in (".missions", ".git", "docs") and not p.is_symlink()]
    if (repo / "docs").is_dir():
        roots += [p for p in sorted((repo / "docs").iterdir()) if p.name != "plans" and not p.is_symlink()]
    return f"""# Independent {mode} derivation
Repository: {repo}
Read these exact sealed inputs:
{paths}

Longer annotated copies are decoys under .missions/ and docs/plans/. Both trees
are entirely out of bounds: no reads, discovery, aliases or citations. Never follow
a filename search to the originals. Do not load project instructions or memory.
Another team independently produced an architecture for this specification. You
have not seen theirs and they have not seen yours. Derive your answer; the point
is divergence. Read-only: no writes, branch, commit, database, network, MCP or agents.

Start with the sealed inputs. For source evidence use explicit absolute files or
these safe search roots (do not search the repository root or docs as a whole):
{chr(10).join('- ' + str(p) for p in roots)}
Shell commands, if available, are limited to literal cat/head/tail/nl/wc/sed reads,
pwd/cd, and rg/grep/ls. No scripts, expansion or redirection. Broad shell searches
must exclude both .missions/** and docs/plans/**. Native search paths must be
explicit safe subtrees. Never follow symlinks into forbidden trees.

Attack the contract: is each assertion observable and provable at its stated class?
Could implementation satisfy it literally and still be wrong? What behavior is
uncovered? What would you refuse to build? Critique the decomposition and scope.
{"Also derive the forced architectural decisions: options, your choice, what would have to be true for it to be wrong, and whether the decomposition carries it." if mode == "design" else ""}
Return Markdown, one section per task. Cite file:line for every repository claim.
Tag substantive claims [verified: citation], [inferred] or [uncertain]. Do not
manufacture findings to appear thorough: 'no issues found in this section' is a
legitimate answer. Do not hedge into uselessness; commit to a choice.
"""


def sighted_task_text(run_dir, repo, package):
    return f"""# Sighted divergence (not a blind pass)
Read {run_dir / 'SIGHTED-design.md'} and {run_dir / 'BLIND-report.md'}.
Repository: {repo}. Original sealed inputs: {package}.
Compare the question at stake, each design's choice, which is right and the
observation that would settle it. Identify shared assumptions and what each lacks.
Do not defer, concede because the other team wrote more, or invent divergences.
Read-only: no writes, branch, commit, database, network, MCP or agents. No project
instructions or memory. The .missions/ and docs/plans/ trees are entirely out of
bounds, including discovery and aliases. The two external copies above are the
only authorized disclosure of the other team's conclusions. Use explicit absolute
source files or safe subtrees for searches; do not search the repository root.
Use literal read/search shell commands only, with both .missions/** and docs/plans/**
excluded from broad searches. No scripts, shell expansion or redirection.
Return Markdown, one section per task. Cite file:line for repository claims and tag
substantive claims [verified: citation], [inferred] or [uncertain]. No issues found
is legitimate; do not hedge into uselessness. Commit to a choice.
"""


def leak_hits(package, extra_pattern):
    pattern = re.compile(r"paginat|twin|parity|D0[0-9][0-9]" + ("|" + extra_pattern if extra_pattern else ""), re.I)
    hits = []
    for name in SOURCES.values():
        for line, text in enumerate((package / name).read_text().splitlines(), 1):
            if pattern.search(text):
                hits.append({"id": str(len(hits) + 1), "file": name, "line": line, "text": text})
    return hits


def seal(args):
    mission, repo = mission_at(args.mission, args.mode)
    package = outside(args.package, repo)
    package.mkdir(parents=True, exist_ok=True)
    allowed = set(SOURCES.values()) | {"TASK.md", "SEAL.json", "leak-hits.json"}
    require(all(p.name in allowed and p.is_file() and not p.is_symlink() and p.stat().st_nlink == 1 for p in package.iterdir()),
            "seal requires a dedicated package directory")
    for name, dest in SOURCES.items():
        (package / dest).write_text(strip_source(name, (mission / name).read_text()))
    (package / "TASK.md").write_text(task_text(repo, package, args.mode))
    hits = leak_hits(package, args.leak_pattern)
    save(package / "leak-hits.json", {"hits": hits})
    assessment = read_json(args.leak_assessment) if args.leak_assessment else {}
    for hit in hits:
        decision = assessment.get(hit["id"], {})
        require(decision.get("disposition") == "keep" and isinstance(decision.get("reason"), str) and decision["reason"].strip(),
                "leak assessment required; inspect external leak-hits.json and supply --leak-assessment")
    save(package / "SEAL.json", {"version": VERSION, "repo": str(repo), "mission": str(mission),
                                "package": str(package), "mode": args.mode,
                                "sources": source_hashes(mission, args.mode),
                                "files": {p.name: file_hash(p) for p in package.iterdir() if p.name != "SEAL.json"},
                                "leak_pattern": args.leak_pattern, "leak_assessment": assessment})
    print("SEALED: " + str(package))


def verify_package(package, mission, repo, mode):
    package = outside(package, repo)
    manifest = read_json(package / "SEAL.json")
    require(manifest.get("version") == VERSION and manifest.get("repo") == str(repo) and
            manifest.get("mission") == str(mission) and manifest.get("mode") == mode and
            manifest.get("package") == str(package), "package identity mismatch")
    require(manifest.get("sources") == source_hashes(mission, mode), "mission inputs changed; reseal package")
    files = manifest.get("files", {})
    require(set(files) == set(SOURCES.values()) | {"TASK.md", "leak-hits.json"}, "invalid sealed file set")
    require({p.name for p in package.iterdir()} == set(files) | {"SEAL.json"}, "unexpected package contents")
    for name, expected in files.items():
        p = package / name
        require(p.is_file() and not p.is_symlink() and p.stat().st_nlink == 1 and file_hash(p) == expected,
                "package tampering: " + name)
    for name, dest in SOURCES.items():
        require((package / dest).read_text() == strip_source(name, (mission / name).read_text()), "stripped input mismatch")
    require((package / "TASK.md").read_text() == task_text(repo, package, mode), "sealed task differs from the read-only review contract")
    hits = read_json(package / "leak-hits.json").get("hits")
    require(isinstance(hits, list) and hits == leak_hits(package, manifest.get("leak_pattern", "")), "invalid leak record")
    for hit in hits:
        decision = manifest.get("leak_assessment", {}).get(hit["id"], {})
        require(decision.get("disposition") == "keep" and decision.get("reason"), "unassessed leak")
    return package, file_hash(package / "SEAL.json")


def selection(args):
    require(args.author in PROVIDERS, "unknown author provider; independence cannot be established")
    reviewer = args.reviewer or next(name for name in PROVIDERS if name != args.author)
    require(reviewer in PROVIDERS and reviewer != args.author, "reviewer must be the other known provider")
    model = args.model or PROVIDERS[reviewer]["model"]
    require(re.fullmatch(PROVIDERS[reviewer]["model_pattern"], model), "model does not identify the selected provider")
    binary = shutil.which(args.executable or reviewer)
    require(binary is not None, "reviewer executable missing: " + (args.executable or reviewer))
    binary = str(Path(binary).resolve())
    return {"author": args.author, "reviewer": reviewer, "model": model,
            "executable": binary, "executable_sha256": file_hash(binary)}


def reviewer_env():
    # Endpoint, routing and inherited session overrides make independence ambiguous.
    routing = ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE", "AZURE_OPENAI_ENDPOINT",
               "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
               "ANTHROPIC_CUSTOM_HEADERS", "OPENAI_CUSTOM_HEADERS")
    require(not any(os.environ.get(k) for k in routing), "custom provider routing is unsupported")
    keys = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT",
            "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"}
    return {k: v for k, v in os.environ.items() if k in keys}


def probe(command, cwd, env, timeout):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise Invalid("reviewer preflight timed out: " + command[1]) from exc
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()
    require(process.returncode == 0, "reviewer preflight failed: " + " ".join(command[1:3]))
    return stdout + stderr


def preflight(chosen, timeout):
    require(0 < timeout <= 60, "preflight timeout must be between 0 and 60 seconds")
    env = reviewer_env()
    binary, provider = chosen["executable"], PROVIDERS[chosen["reviewer"]]
    # Outside the target repo: even help/auth commands must not discover its config.
    with tempfile.TemporaryDirectory(prefix="crosscheck-preflight-", dir=scratch_root(chosen.get("repo"))) as cwd:
        version = probe([binary, "--version"], cwd, env, timeout).strip()
        require(re.fullmatch(provider["version_pattern"], version), "executable provider identity is unknown")
        help_text = probe([binary, *provider["help"]], cwd, env, timeout)
        # The capability check covers exactly the flags the dispatch command uses.
        flags = {arg for arg in command(chosen, cwd, "session") if arg.startswith("--")}
        require(all(flag in help_text for flag in flags), "reviewer lacks required transcript/session CLI capabilities")
        provider["auth_check"](probe([binary, *provider["auth"]], cwd, env, timeout))
    require(file_hash(binary) == chosen["executable_sha256"], "reviewer executable changed during preflight")
    return dict(chosen, version=version), env


def command(chosen, cwd, session):
    return PROVIDERS[chosen["reviewer"]]["argv"](chosen["executable"], chosen["model"], cwd, session)


def snapshot(mission, directory, repo=None):
    mission = Path(mission).resolve()
    repo = repo or repo_root(mission)
    directory = outside(directory, repo)
    directory.mkdir(parents=True, exist_ok=True)
    state = content_state(repo, mission)
    save(directory / "snapshot.json", state)
    # Retain the historical shell helper's artifacts, now using SHA-256.
    (directory / "mission.sha").write_text(mission_checksums(state, mission, repo))
    result = subprocess.run(["git", "-C", str(repo), "status", "--short"], capture_output=True, text=True, check=True)
    (directory / "git.txt").write_text(result.stdout)


def mission_checksums(state, mission, repo):
    return "".join(value.get("sha256", digest(value.get("link", "").encode())) + "  " + str(repo / name) + "\n"
                   for name, value in sorted(state["files"].items()) if within(repo / name, mission))


def identity(args, mission, repo):
    chosen = selection(args)
    package, seal_hash = verify_package(args.package, mission, repo, args.mode)
    return dict(chosen, mode=args.mode, mission=str(mission), repo=str(repo), package=str(package),
                seal_sha256=seal_hash, task_sha256=file_hash(package / "TASK.md"))


def audit_record(record, mission, repo, identity, kind="blind"):
    require(record.get("kind") == kind and record.get("audit_status") in ("PENDING", "PASS"), "pass kind/status mismatch")
    require(set(record.get("sighted_inputs", {})) == ({"SIGHTED-design.md", "BLIND-report.md"} if kind == "sighted" else set()),
            "blind/sighted input boundary mismatch")
    require(record.get("identity") == identity, "saved run identity changed")
    require(file_hash(identity["executable"]) == identity["executable_sha256"], "reviewer executable changed")
    run_dir = outside(record["run_dir"], repo)
    transcript = run_dir / "transcript.jsonl"
    require(record.get("transcript") == str(transcript) and record.get("snapshot") == str(run_dir / "snapshot") and
            record.get("process") == str(run_dir / "process.json"), "saved evidence references changed")
    outcome = read_json(run_dir / "process.json")
    require(outcome.get("identity") == identity and outcome.get("run_id") == record.get("run_id") and
            outcome.get("kind") == kind, "process identity mismatch")
    require(type(outcome.get("exit_code")) is int and outcome["exit_code"] == 0 and
            outcome.get("completed") is True and outcome.get("timed_out") is False,
            "reviewer process did not complete successfully")
    require(file_hash(transcript) == outcome.get("transcript_sha256"), "transcript changed")
    require(file_hash(run_dir / "snapshot/snapshot.json") == record.get("snapshot_sha256"), "snapshot evidence changed")
    require(file_hash(run_dir / "task.md") == record.get("task_sha256"), "dispatched task changed")
    require(record.get("sighted_inputs") or record["task_sha256"] == identity["task_sha256"], "blind task identity changed")
    require(file_hash(run_dir / "stderr") == outcome.get("stderr_sha256"), "stderr evidence changed")
    verify_package(identity["package"], mission, repo, identity["mode"])
    check_snapshot(run_dir / "snapshot", repo, mission)
    extras = [run_dir / name for name in record.get("sighted_inputs", {})]
    access = Access(repo, identity["package"], run_dir / "cwd", extras)
    raw = transcript.read_text()
    report = PROVIDERS[identity["reviewer"]]["report"](raw, access, record["session_id"])
    require(record.get("audit_status") != "PASS" or record.get("report_sha256") == digest(report.encode()), "saved report hash changed")
    if record.get("audit_status") == "PASS":
        require(file_hash(record["report"]) == record["report_sha256"], "saved report changed or missing")
    for name, expected in record.get("sighted_inputs", {}).items():
        require(file_hash(run_dir / name) == expected, "sighted evidence changed")
    return report


def void(record, reason, mission, key):
    record.update(audit_status="VOID", audit_reason=reason)
    directory = Path(record.get("run_dir", ""))
    # Do not follow corrupted progress references into arbitrary directories.
    if not (directory.is_absolute() and directory.name.startswith("mission-crosscheck-") and directory.is_dir()):
        return
    for path in (directory / "transcript.jsonl", directory / "stderr", mission / "crosscheck" / REPORTS[key]):
        if path.is_file():
            dest = directory / ("VOID-" + path.name)
            shutil.move(str(path), str(dest))
            record.setdefault("quarantine", {})[path.name] = str(dest)


def fail(progress, keys, reason, mission, state_path):
    """Quarantine the affected passes and persist the reason before propagating."""
    changed = False
    for key in keys:
        record = progress.get(key)
        if record and record.get("audit_status") != "VOID":
            void(record, reason, mission, key)
            changed = True
    if changed:
        save(state_path, progress)


@contextmanager
def run_lock(mission, repo):
    name = "crosscheck-" + digest(str(mission).encode()) + ".lock"
    with open(scratch_root(repo) / name, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Invalid("a crosscheck is already running") from exc
        yield


def execute(args):
    mission, repo = mission_at(args.mission, args.mode)
    with run_lock(mission, repo):
        return execute_locked(args, mission, repo)


def execute_locked(args, mission, repo):
    state_path = mission / "crosscheck/progress.json"
    progress = read_json(state_path) if state_path.exists() else {"version": VERSION, "history": []}
    legacy = progress if progress.get("version") != VERSION else None
    require(not legacy or args.new_pass, "legacy progress lacks completion evidence; use --new-pass")
    if legacy:
        progress = {"version": VERSION, "history": []}
    require(0 < args.timeout, "reviewer timeout must be positive")
    key = "sighted" if args.sighted else "blind"
    previous = progress.get(key)
    if previous and previous.get("run_dir") and not (Path(previous["run_dir"]) / "process.json").exists() and previous.get("pid"):
        try:
            os.kill(previous["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise Invalid("saved reviewer process is still alive; wait for it before resuming")
    try:
        current = identity(args, mission, repo)
        if args.sighted:
            require(args.mode == "design" and progress.get("blind"), "sighted pass requires an audited design blind pass")
            blind = audit_record(progress["blind"], mission, repo, current)
            if progress["blind"].get("audit_status") != "PASS":
                finish(progress, "blind", progress["blind"], blind, state_path)
            report_path = mission / "crosscheck/pass1-report.md"
            require(report_path.is_file() and file_hash(report_path) == digest(blind.encode()), "save blind report before sighted pass")
        if previous and not args.new_pass:
            require(previous.get("audit_status") != "VOID", "saved pass is VOID; use --new-pass after fixing the cause")
            if args.sighted:
                require(previous.get("blind_run_id") == progress["blind"]["run_id"] and
                        previous.get("blind_report_sha256") == progress["blind"]["report_sha256"], "sighted pass's blind input changed")
            report = audit_record(previous, mission, repo, current, key)
            finish(progress, key, previous, report, state_path)
            print("AUDIT PASS: verified saved " + key + " report (no dispatch)")
            return
    except (Invalid, OSError, KeyError, TypeError) as exc:
        fail(progress, ("blind", "sighted") if args.sighted else (key,), str(exc), mission, state_path)
        raise Invalid(str(exc)) from exc

    chosen, env = preflight(current, args.preflight_timeout)
    # Only now may the workflow write mission artifacts. Amendment calls use
    # preflight alone and therefore cannot create a progress/state/journal file.
    state_path.parent.mkdir(exist_ok=True)
    if previous:
        if previous.get("audit_status") != "VOID":
            void(previous, "superseded by explicit new pass", mission, key)
        progress.setdefault("history", []).append(previous)
    if key == "blind" and progress.get("sighted"):
        stale = progress.pop("sighted")
        void(stale, "blind pass replaced", mission, "sighted")
        progress.setdefault("history", []).append(stale)
    run_dir = outside(tempfile.mkdtemp(prefix="mission-crosscheck-", dir=scratch_root(repo)), repo)
    if legacy:
        save(run_dir / "VOID-legacy-progress.json", legacy)
        progress["history"].append({"audit_status": "VOID", "audit_reason": "legacy progress lacks evidence",
                                    "quarantine": str(run_dir / "VOID-legacy-progress.json")})
    task = (Path(current["package"]) / "TASK.md").read_text()
    if args.sighted:
        # Copy the single permitted design outside the repository; the general
        # contamination rules still forbid reading original mission documents.
        (run_dir / "SIGHTED-design.md").write_bytes((mission / "design.md").read_bytes())
        (run_dir / "BLIND-report.md").write_bytes((mission / "crosscheck/pass1-report.md").read_bytes())
        task = sighted_task_text(run_dir, repo, current["package"])
    (run_dir / "task.md").write_text(task)
    record = {"identity": current, "kind": key, "run_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
              "run_dir": str(run_dir), "transcript": str(run_dir / "transcript.jsonl"),
              "snapshot": str(run_dir / "snapshot"), "process": str(run_dir / "process.json"),
              "task_sha256": file_hash(run_dir / "task.md"), "audit_status": "PENDING",
              "audit_reason": "process not yet completed", "cli_version": chosen["version"]}
    if args.sighted:
        record["sighted_inputs"] = {name: file_hash(run_dir / name) for name in ("SIGHTED-design.md", "BLIND-report.md")}
        record["blind_run_id"] = progress["blind"]["run_id"]
        record["blind_report_sha256"] = progress["blind"]["report_sha256"]
    snapshot(mission, run_dir / "snapshot", repo)
    record["snapshot_sha256"] = file_hash(run_dir / "snapshot/snapshot.json")
    progress[key] = record
    save(state_path, progress)
    # A fresh external working directory prevents project CLI configuration from
    # changing the selected vendor or injecting automatic context.
    launch = run_dir / "cwd"
    launch.mkdir()
    (launch / "TASK.md").write_text(task)
    args_cmd = command(chosen, launch, record["session_id"])
    timed_out, rc = False, None
    try:
        with open(run_dir / "task.md") as inp, open(run_dir / "transcript.jsonl", "w") as out, open(run_dir / "stderr", "w") as err:
            process = subprocess.Popen(args_cmd, cwd=launch, env=env, stdin=inp, stdout=out, stderr=err, start_new_session=True)
            record["pid"] = process.pid
            save(state_path, progress)
            try:
                rc = process.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                timed_out = True
            finally:
                # Never leave descendants writing evidence after the exit receipt.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                rc = process.wait()
        repo_aliases.cache_clear()  # the reviewer ran; re-enumerate before auditing
        outcome = {"identity": current, "kind": key, "run_id": record["run_id"], "completed": True,
                   "exit_code": rc, "timed_out": timed_out,
                   "transcript_sha256": file_hash(run_dir / "transcript.jsonl"),
                   "stderr_sha256": file_hash(run_dir / "stderr")}
        save(run_dir / "process.json", outcome)
        record["process_outcome"] = outcome
        save(state_path, progress)
        report = audit_record(record, mission, repo, current, key)
        finish(progress, key, record, report, state_path)
        print("AUDIT PASS: saved " + key + " report")
    except (Invalid, OSError, KeyError, TypeError) as exc:
        fail(progress, (key,), str(exc), mission, state_path)
        raise Invalid(str(exc)) from exc


def finish(progress, key, record, report, state_path):
    """Save a report that audit_record has just verified, including its sighted inputs."""
    record.update(audit_status="PASS", audit_reason="structured completion, process exit, seal and snapshot verified",
                  report_sha256=digest(report.encode()))
    destination = state_path.parent / REPORTS[key]
    destination.write_text(report)
    record["report"] = str(destination)
    record["process_outcome"] = read_json(Path(record["run_dir"]) / "process.json")
    save(state_path, progress)
    markdown = state_path.parent / "progress.md"
    if not markdown.exists():
        markdown.write_text("# Crosscheck findings\n\nMachine evidence: progress.json. Checkboxes cannot establish completion.\n\n"
                            "| # | Finding | Bucket | Verified | Disposition |\n|---|---|---|---|---|\n")


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run", "seal"):
        sub = commands.add_parser(name)
        sub.add_argument("--mission", required=name != "preflight")
        sub.add_argument("--mode", choices=("contract", "design"), default="contract")
        sub.add_argument("--package", required=name in ("run", "seal"))
        if name != "seal":
            sub.add_argument("--author", required=True)
            sub.add_argument("--reviewer")
            sub.add_argument("--executable")
            sub.add_argument("--model")
            sub.add_argument("--preflight-timeout", type=float, default=15)
        if name == "run":
            sub.add_argument("--timeout", type=float, default=3600)
            sub.add_argument("--new-pass", action="store_true")
            sub.add_argument("--sighted", action="store_true")
        if name == "seal":
            sub.add_argument("--leak-pattern", default="")
            sub.add_argument("--leak-assessment")
    sub = commands.add_parser("snapshot")
    sub.add_argument("mission")
    sub.add_argument("directory", nargs="?")
    sub.add_argument("--print", action="store_true", dest="print_only")
    sub = commands.add_parser("audit")
    sub.add_argument("transcript")
    sub.add_argument("mission")
    sub.add_argument("snapshot")
    args = parser.parse_args()
    try:
        if args.command == "seal":
            seal(args)
        elif args.command == "preflight":
            chosen = selection(args)
            if args.mission:
                mission, repo = mission_at(args.mission, args.mode)
                chosen["repo"] = str(repo)
                if args.package:
                    verify_package(args.package, mission, repo, args.mode)
            preflight(chosen, args.preflight_timeout)
            print("PREFLIGHT PASS: " + chosen["author"] + " → " + chosen["reviewer"])
        elif args.command == "run":
            execute(args)
        elif args.command == "snapshot":
            if args.print_only:
                mission = Path(args.mission).resolve()
                repo = repo_root(mission)
                print(mission_checksums(content_state(repo, mission), mission, repo), end="")
            else:
                require(args.directory, "missing snapshot directory")
                snapshot(args.mission, args.directory)
                print("SNAPSHOT: repository contents and mission checksums saved")
        else:
            mission = Path(args.mission).resolve()
            progress = read_json(mission / "crosscheck/progress.json")
            saved = next(((k, progress[k]) for k in ("blind", "sighted") if progress.get(k, {}).get("transcript") == str(Path(args.transcript).resolve())), None)
            require(saved is not None, "legacy progress lacks process/package evidence; rerun through crosscheck.py")
            kind, record = saved
            require(Path(args.snapshot).resolve() == Path(record["snapshot"]), "snapshot reference mismatch")
            audit_record(record, mission, repo_root(mission), record["identity"], kind)
            print("AUDIT PASS: no sealed material opened, nothing written, report present")
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("CROSSCHECK FAIL: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
