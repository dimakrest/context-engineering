"""Prep (design §7): the run's environment is built, never inherited; git is shaped through that
environment, never through the repo's config; a reviewer is blind by having nothing to look at;
one executor at a time per host.

The guarantee model is layered on purpose. Credentials make a push IMPOSSIBLE: the child's env
carries no token, no agent socket, no askpass, and its global gitconfig has an empty credential
helper, so git has nothing to authenticate with. Hooks make what is still possible REFUSED: a
commit off the mission branch, a message without the feature prefix, a push over a transport
that needs no credential (a local path, as the trace fixture has) meet hooks that exit 1 -- the
pre-push unless it holds the driver's own push token. Under the claude harness the plugin's own
hooks stay installed and keep working; they are a bonus, never what the driver relies on.

Git sees all of it only through `GIT_CONFIG_*` variables in the child's environment. The repo's
`core.hooksPath` is repository config, shared by every worktree of the repo, and would outlive a
crash -- so it is never written. Everything here lands in `<mdir>/githooks/` and `runs/<task>/`,
gitignored runtime files rewritten before every run.

Not here: the driver's own push subprocess (phase pr, #10). The plain push token lives only in
driver memory and nothing sets it in a child today, so the pre-push hook refuses everything.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from . import files, journal
from .outcome import RunRequest

# ---------------------------------------------------------------- environment

KEEP_EXACT = frozenset((
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TZ", "LANG", "LANGUAGE",
    "COLORTERM", "NO_COLOR",
    # proxies and certificate bundles: the harness cannot reach its API without them
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
))
KEEP_PREFIXES = ("LC_", "XDG_", "MISSIONS_")
HARNESS_ENV = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
               "ANTHROPIC_MODEL", "CLAUDE_CONFIG_DIR", "DISABLE_TELEMETRY", "DISABLE_AUTOUPDATER"),
    "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME"),
    "stub": (),
}
# Never, whatever the lists say. GIT_CONFIG_* because ours are set below; CLAUDECODE/CLAUDE_CODE_*
# because a child `claude` that sees them routes through the parent session's socket and hangs;
# MISSIONS_PUSH_TOKEN because the MISSIONS_ prefix passes and a worker must never carry one.
NEVER_EXACT = frozenset(("GH_TOKEN", "GITHUB_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
                         "GIT_SSH", "GIT_SSH_COMMAND", "CLAUDECODE", "MISSIONS_PUSH_TOKEN"))
NEVER_PREFIXES = ("CLAUDE_CODE_", "GIT_CONFIG_")


def githooks_dir(mission_dir: Path) -> Path:
    return mission_dir / "githooks"


def _passthrough(name: str, patterns: Sequence[str]) -> bool:
    """driver.json `env.passthrough`: exact names, or `PREFIX_*` globs -- the operator's explicit choice."""
    for pat in patterns:
        if pat.endswith("*"):
            if name.startswith(pat[:-1]):
                return True
        elif name == pat:
            return True
    return False


def build_env(mission_dir: Path, run_dir: Path, role: str, feature: str, task: str, phase: str,
              harness: str, branch: str = "", feature_files: Sequence[str] = (),
              passthrough: Sequence[str] = (), base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The child's environment, built from `base_env` (the driver's own by default): the exact
    names and prefixes above, the harness's own variables, the operator's passthrough, and
    nothing else -- every `*_TOKEN *_SECRET *_PASSWORD *_KEY` that is not on a list is gone. The
    never-list wins over every list. Then the mission's `MISSIONS_*` and the `GIT_*` that point git
    at the driver-written config and hooks. Writes nothing; `prepare` writes what these point at."""
    src = os.environ if base_env is None else base_env
    allowed = set(KEEP_EXACT) | set(HARNESS_ENV.get(harness, ()))
    env: Dict[str, str] = {}
    for k, v in src.items():
        if k in NEVER_EXACT or k.startswith(NEVER_PREFIXES):
            continue
        if k in allowed or k.startswith(KEEP_PREFIXES) or _passthrough(k, passthrough):
            env[k] = v
    hooks = githooks_dir(mission_dir.resolve())
    env.update({
        "MISSIONS_ROLE": role,
        "MISSIONS_FEATURE": feature,
        "MISSIONS_TASK": task,
        "MISSIONS_DIR": str(mission_dir),
        "MISSIONS_RUN_DIR": str(run_dir),
        "MISSIONS_PHASE": phase,
        "MISSIONS_HARNESS": harness,
        "MISSIONS_BIN": str(files.plugin_root() / "bin" / "missions"),
        "MISSIONS_BRANCH": branch,
        "GIT_CONFIG_GLOBAL": str(hooks / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(hooks / "no-credentials"),
        "GIT_SSH_COMMAND": str(hooks / "no-credentials"),
        # the env override is what makes the hooks exist only for processes the driver launched;
        # the empty credential.helper resets the helper list even when the repo-local config names
        # one (verified on git 2.43)
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks),
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
    })
    if role == "worker":
        env["MISSIONS_FILES"] = ",".join(f.strip() for f in feature_files if f.strip())
    return env


# ---------------------------------------------------------------- git files

NO_CREDENTIALS = (
    "#!/bin/bash\n"
    "# missions: written by the driver. GIT_ASKPASS and GIT_SSH_COMMAND point here, so git can\n"
    "# neither ask for a credential nor open a transport that would use one.\n"
    "echo 'missions: this run has no push credentials' >&2\n"
    "exit 1\n")

# The repo's own hook (pre-commit framework, husky, ...) runs first, with the same args and stdin,
# and keeps its exit code. `git rev-parse --git-path hooks` cannot be used here: under the env
# override it returns OUR directory. `git config --local` reads only the repo's config, so it
# still sees the repo's own core.hooksPath.
_CHAIN = ('orig=$(git config --local core.hooksPath); [ -n "$orig" ] || orig="$(git rev-parse --git-common-dir)/hooks"\n'
          'if [ -x "$orig/%(name)s" ]; then "$orig/%(name)s" "$@" </dev/stdin || exit 1; fi\n')

_PRE_COMMIT_WORKER = r'''cur=$(git symbolic-ref --short -q HEAD)
if [ "$cur" != "$MISSIONS_BRANCH" ]; then
  echo "missions: commits go on the mission branch $MISSIONS_BRANCH, not ${cur:-a detached HEAD}" >&2
  exit 1
fi
# paths staged outside the feature's Files: a warning, never a refusal -- the post-exit grade is the gate
if [ -n "${MISSIONS_FILES:-}" ]; then
  IFS=, read -r -a allowed <<<"$MISSIONS_FILES"
  outside=""
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in .missions/*) continue ;; esac
    ok=0
    for f in "${allowed[@]}"; do
      f=${f%/}
      if [ "$p" = "$f" ] || [ "${p#"$f"/}" != "$p" ]; then ok=1; break; fi
    done
    [ "$ok" = 1 ] || outside="$outside $p"
  done < <(git diff --cached --name-only)
  if [ -n "$outside" ]; then
    echo "missions: staged outside $MISSIONS_FEATURE's Files:$outside -- name them in the handoff with the reason" >&2
  fi
fi
exit 0
'''

_COMMIT_MSG_WORKER = r'''first=$(sed -e 's/^[[:space:]]*//' "$1" | grep -v -m1 -e '^#' -e '^$')
case "$first" in
  "$MISSIONS_FEATURE:"*) exit 0 ;;
esac
echo "missions: the commit message must start with \"$MISSIONS_FEATURE:\" -- got: ${first:0:80}" >&2
exit 1
'''

_PRE_PUSH = r'''if [ -n "${MISSIONS_PUSH_TOKEN:-}" ] && [ "$(printf %%s "$MISSIONS_PUSH_TOKEN" | sha256sum | cut -d' ' -f1)" = "%(hash)s" ]; then
  exit 0
fi
echo "missions: workers never push; the driver pushes in phase pr" >&2
exit 1
'''


# Under a core.hooksPath override git looks up EVERY hook in our directory, so the repo's other
# client-side hooks (husky's prepare-commit-msg, a post-checkout that installs dependencies, ...)
# would silently stop running for the worker. These carry only the chaining line.
PASSTHROUGH_HOOKS = ("applypatch-msg", "pre-applypatch", "post-applypatch", "prepare-commit-msg", "pre-merge-commit",
                     "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-auto-gc", "post-rewrite",
                     "push-to-checkout", "sendemail-validate")


def push_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hook_scripts(role: str, task: str, token_hash: str) -> Dict[str, str]:
    """The hooks for one run. Worker: chain, then the mission checks. Any other role: a
    pre-commit and commit-msg that refuse outright -- without chaining, because the repo's own
    pre-commit may rewrite files, and a reviewer run writes nothing. pre-push: chain, then refuse
    unless the caller holds the driver's push token (its sha256 is what the script carries). The
    rest of the client-side hook names chain and nothing more."""
    head = "#!/bin/bash\n# missions: written by the driver for %s (role %s); scoped to this run's env via GIT_CONFIG_*\n" % (task, role)
    refuse = 'echo "missions: a %s run does not commit" >&2\nexit 1\n' % role
    if role == "worker":
        pre_commit = head + _CHAIN % {"name": "pre-commit"} + _PRE_COMMIT_WORKER
        commit_msg = head + _CHAIN % {"name": "commit-msg"} + _COMMIT_MSG_WORKER
    else:
        pre_commit = head + refuse
        commit_msg = head + refuse
    pre_push = head + _CHAIN % {"name": "pre-push"} + _PRE_PUSH % {"hash": token_hash}
    hooks = {"pre-commit": pre_commit, "commit-msg": commit_msg, "pre-push": pre_push}
    for name in PASSTHROUGH_HOOKS:
        hooks[name] = head + _CHAIN % {"name": name} + "exit 0\n"
    return hooks


def write_gitconfig(mission_dir: Path, checkout: Path) -> Path:
    """The child's whole global config: the checkout's identity (so commits carry the same author
    the operator's own commits do) and an empty credential helper. Rewritten every run."""
    name = files.git_out(checkout, "config", "user.name") or "missions-worker"
    email = files.git_out(checkout, "config", "user.email") or "worker@missions.invalid"
    path = githooks_dir(mission_dir) / "gitconfig"
    path.parent.mkdir(parents=True, exist_ok=True)
    files.write_text(path, "[user]\n\tname = %s\n\temail = %s\n[credential]\n\thelper =\n" % (name, email))
    return path


def _write_exec(path: Path, text: str) -> None:
    files.write_text(path, text)
    os.chmod(path, 0o755)


def prepare(ctx, req: RunRequest) -> None:
    """Before every run: the git files the env points at, then the record of which variable NAMES
    the run had (never values) -- the harness smoke and a curious operator read it after."""
    hooks = githooks_dir(ctx.mission_dir)
    hooks.mkdir(parents=True, exist_ok=True)
    write_gitconfig(ctx.mission_dir, ctx.checkout)
    _write_exec(hooks / "no-credentials", NO_CREDENTIALS)
    for name, text in hook_scripts(req.role, req.task, push_hash(ctx.push_token)).items():
        _write_exec(hooks / name, text)
    req.run_dir.mkdir(parents=True, exist_ok=True)
    files.write_text(req.run_dir / "env-names.txt", "\n".join(sorted(req.env)) + "\n")


# ---------------------------------------------------------------- blindness

BLIND_DIRS = ("handoffs", "validation", "decisions")


def _merge_move(src: Path, dst: Path) -> None:
    """Move `src` to `dst`. When `dst` grew back while `src` was hidden (a crash, then a run before
    preflight restored it), directories merge and a file already at `dst` is the newer one: it wins."""
    if not dst.exists():
        os.rename(src, dst)
        return
    if src.is_dir() and dst.is_dir():
        for child in list(src.iterdir()):
            _merge_move(child, dst / child.name)
        os.rmdir(src)
        return
    src.unlink()


def _restore_cell(cell: Path) -> None:
    mdir = cell.parent.parent
    os.chmod(cell, 0o755)
    for child in list(cell.iterdir()):
        if child.name == "runs":
            (mdir / "runs").mkdir(exist_ok=True)
            for run in list(child.iterdir()):
                _merge_move(run, mdir / "runs" / run.name)
            os.rmdir(child)
        else:
            _merge_move(child, mdir / child.name)
    os.rmdir(cell)
    blind = cell.parent
    if blind.is_dir() and not any(blind.iterdir()):
        os.rmdir(blind)


def restore_blind(mission_dir: Path) -> List[str]:
    """Bring back whatever a crashed driver left under `.blind/` -- the task names restored."""
    blind = mission_dir / ".blind"
    if not blind.is_dir():
        return []
    restored: List[str] = []
    for cell in sorted(blind.iterdir()):
        if cell.is_dir():
            _restore_cell(cell)
            restored.append(cell.name)
    return restored


@contextmanager
def blind(ctx, task: str) -> Iterator[None]:
    """For a reviewer run: handoffs/, validation/, decisions/ and every other run's dir move into
    `.blind/<task>/` (mode 000) for the process lifetime and come back in `finally`. Hidden, not
    forbidden -- the prompt names one patch and nothing else, and `patches/` stays."""
    mdir = ctx.mission_dir
    cell = mdir / ".blind" / task
    cell.mkdir(parents=True, exist_ok=True)
    for name in BLIND_DIRS:
        if (mdir / name).exists():
            os.rename(mdir / name, cell / name)
    runs = mdir / "runs"
    if runs.is_dir():
        (cell / "runs").mkdir(exist_ok=True)
        for entry in list(runs.iterdir()):
            if entry.name != task:
                os.rename(entry, cell / "runs" / entry.name)
    os.chmod(cell, 0)
    try:
        yield
    finally:
        _restore_cell(cell)


# ---------------------------------------------------------------- host lease

def host_lock_path() -> Path:
    """`~/.missions/host.lock`, or MISSIONS_HOST_LOCK -- the trace runner points it under the case's
    tmp dir so a test never blocks on, or blocks, a real mission."""
    env = os.environ.get("MISSIONS_HOST_LOCK")
    return Path(env) if env else Path.home() / ".missions" / "host.lock"


@contextmanager
def host_lease(ctx, task: str) -> Iterator[None]:
    """One executor per host (design §7.1): two missions in two worktrees share the laptop, the
    ports and the test database, and cannot see each other's `.lease`. fcntl on one file; when it
    is held, `lease_wait{task, holder}` once, then block -- a signal raises through the wait. The
    holder line names the mission, the task and the pid. A dead holder's lock is released by the
    kernel, so there is nothing stale to break. `host_lease: false` in driver.json skips it."""
    if not ctx.cfg.get("host_lease", True):
        yield
        return
    path = host_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            holder = fh.read().strip().split("\n")[0] or "unknown holder"
            journal.append(ctx.mission_dir, "lease_wait", task=task, holder=holder)
            ctx.log("   waiting for the host lease: %s" % holder)
            fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        fh.truncate()
        fh.write("mission=%s task=%s pid=%d at=%s\n" % (ctx.mission_dir, task, os.getpid(), journal.now_iso()))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
