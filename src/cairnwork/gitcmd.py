"""Run git with a fixed argv, no shell, and no inherited GIT_* environment.

Stripping GIT_* keeps a hook environment (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE)
from pointing a command at another repository or index. Hooks, the fsmonitor
command, configuration-declared hooks and the untracked cache are disabled and replace refs
are ignored. Only the file, https and ssh transports are allowed, and configured transport
programs (upload-pack, receive-pack, git proxy, alternate refs command) are not run (#759).
These options are a second line of defence: workers never get a checkout sharing Cairnwork's
repository (see workspace.py), and Cairnwork runs git in a worker's repository only to fetch HEAD.
"""

from __future__ import annotations

import contextvars
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cairnwork.gitcmd_capped import capped
from cairnwork.gitversion import GitVersionError, git_executable, require_no_lazy_fetch
from cairnwork.textclean import replace_hidden

GIT_TIMEOUT_SECONDS = 60
NETWORK_TIMEOUT_SECONDS = 300  # fetch and push over a network; local commands keep the default
STDERR_TAIL_CHARS = 400
OID = re.compile(r"^[0-9a-f]{40}$")
# A private clone of a local repository for one attempt or verification run. ``--template=``
# copies no template directory (hook samples, info/exclude, description; nor an operator's
# init.templateDir hooks): about 20 files fewer per clone and nothing executable in .git (#417).
CLONE_ISOLATED = ("clone", "--quiet", "--no-local", "--no-checkout", "--template=")
_MARK: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "_MARK", default=None
)


@contextmanager
def marked(var: str, value: str):
    """Mark git child processes in this context with an environment variable.

    Uses a context variable, not os.environ: worker threads and other tasks are unaffected,
    and new threads do not inherit the marker.
    """
    token = _MARK.set((var, value))
    try:
        yield
    finally:
        _MARK.reset(token)


# Options that config files cannot override: no hooks, filters, fsmonitor command,
# submodule recursion, external diff, untracked cache, or password prompts; replace
# refs are ignored so a commit is always what it is. Transports: file, https, ssh only.
# Programs deliberately kept: credential helper and ssh command (operator configuration).
# They serve ssh and https remotes (network fetch/push); a local fetch from a path or bundle
# normally does not call them, but a repo's url.<ssh>.insteadOf can still route one through
# core.sshCommand. Diff drivers remain disabled per call site.
_SAFE_OPTIONS = (
    "--no-replace-objects",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.askPass=",
    "-c",
    "submodule.recurse=false",
    "-c",
    "diff.external=",
    # ext:: runs any command; a repo's protocol.ext.allow=always must not turn it on (#1106).
    # -c beats config files and GIT_CONFIG_COUNT/KEY/VALUE; it holds only while callers drop
    # GIT_ALLOW_PROTOCOL and the other GIT_* variables (_child_env, cairnwork_attest.publish).
    "-c",
    "protocol.ext.allow=never",
    # A repo's alternate-refs program is not run (#759). Transport programs (git:// through
    # core.gitProxy, <helper>:: remotes) are blocked by GIT_ALLOW_PROTOCOL, set in the child env.
    "-c",
    "core.alternateRefsCommand=",
    # log/show must print only the commit: no signature-check output (gpg) in a parsed %B (#1126).
    "-c",
    "log.showSignature=false",
)


def _safe_transport_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """Pin fetch and push to Git's built-in transport programs."""
    subcommand = _subcommand(args)
    if subcommand not in ("fetch", "push"):
        return args
    index = args.index(subcommand)
    option = (
        "--upload-pack=git-upload-pack"
        if subcommand == "fetch"
        else "--receive-pack=git-receive-pack"
    )
    return (*args[: index + 1], option, *args[index + 1 :])


# hook.<friendly-name>.<key> (git 2.54 and later): hooks declared in configuration, which
# core.hooksPath does not turn off (#737). The friendly name may itself hold dots.
# The name may also be empty (``[hook ""]``) or hold bytes that are not UTF-8.
_HOOK_KEY = re.compile(r"hook\.(.*)\.[a-z][a-z0-9-]*", re.DOTALL)
_FILTER_KEY = re.compile(r"filter\.(.*)\.(clean|smudge|process)", re.DOTALL)


def config_execution_off(repo: Path | str, env: dict[str, str]) -> tuple[str, ...]:
    """Return ``-c`` overrides disabling declared hooks and filters from one config listing.

    A name that ``-c`` cannot carry (it holds ``=``) is refused, and so is a listing that does
    not finish (timeout, git not started). The listing is every key, filtered here (a regex in
    git, under a UTF-8 locale, skips a key that is not UTF-8). A non-zero exit means the
    configuration could not be enumerated, so the command is refused. Names are decoded
    with surrogateescape, so ``-c`` carries the same bytes back to git (a name that is not UTF-8
    is still matched). Listing runs no hook, so it needs no _SAFE_OPTIONS.
    """
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            [git_executable(), "-C", str(repo), "config", "--null", "--name-only", "--list"],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, env={**env, "LC_ALL": "C"},
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCommandError(
            "git config (hook listing for safety options) failed; refusing to run git"
        ) from exc
    if done.returncode != 0:
        raise GitCommandError(
            "git config (hook listing for safety options) failed; refusing to run git"
        )
    keys = done.stdout.decode("utf-8", "surrogateescape").split("\0")
    hooks = sorted({m.group(1) for k in keys if (m := _HOOK_KEY.fullmatch(k))})
    filters = sorted(
        {m.groups() for k in keys if (m := _FILTER_KEY.fullmatch(k))},
    )
    names = {n for n, _ in filters}
    if any("=" in n for n in set(hooks) | names):
        raise GitCommandError(
            "a configuration-declared hook or filter name holds '='; refusing to run git"
        )
    options = [x for n in hooks for x in ("-c", f"hook.{n}.enabled=false")]
    options.extend(x for n, key in filters for x in ("-c", f"filter.{n}.{key}="))
    options.extend(x for n in sorted(names) for x in ("-c", f"filter.{n}.required=false"))
    return tuple(options)


def config_hooks_off(repo: Path | str, env: dict[str, str]) -> tuple[str, ...]:
    """Compatibility name for callers that need all configuration-declared commands off."""
    return config_execution_off(repo, env)


# Greedy to the last '@' in the run free of space and tab (newlines count as part of the run, so
# one planted in the userinfo cannot split it): a password may itself hold '@' or '/'.
# Fail-closed: an '@' later in a path also removes the text before it.
_SPLIT_RUNS = re.compile(r"([ \t]+)")


def _mask_userinfo(text: str) -> str:
    """Cut from the first '://' to the last '@' of each run free of space and tab, in one pass.

    Same text the greedy regex ``(?<=://)[^ \\t]*@`` removed, without rescanning to the end of the
    run from every '://' (quadratic on a long run, #1145).
    """
    parts = _SPLIT_RUNS.split(text)
    for i in range(0, len(parts), 2):
        run = parts[i]
        idx = run.find("://")
        if idx != -1:
            last_at = run.rfind("@")
            if last_at >= idx + 3:
                parts[i] = run[: idx + 3] + run[last_at + 1 :]
    return "".join(parts)


def stderr_tail(raw: str | bytes | None) -> str:
    """Last STDERR_TAIL_CHARS of git's stderr, one line per line, safe to put in an error.

    URL credentials (``https://user:token@host``) are removed and control characters
    (terminal escapes, CR overwrites) are dropped, so the text can go to a log or an Issue.
    Control characters go first, so none can split a credential before it is redacted. The
    redaction is greedy to the last '@' of a token, so a URL with '@' in its path loses the
    text before it: an accepted trade-off, redact rather than leak.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    text = _mask_userinfo(replace_hidden(raw or "", "", keep="\n")).strip()
    return text.replace("\n", " | ")[-STDERR_TAIL_CHARS:]


def _subcommand_index(args: tuple[str, ...]) -> int | None:
    """Index of the git subcommand, skipping ``-c <name=value>``, ``-C <path>`` and options."""
    it = enumerate(args)
    for i, arg in it:
        if arg in ("-c", "-C"):
            next(it, None)
        elif not arg.startswith("-"):
            return i
    return None


def _subcommand(args: tuple[str, ...]) -> str:
    i = _subcommand_index(args)
    return args[i] if i is not None else (args[0] if args else "command")


def _no_external_diff(args: tuple[str, ...]) -> tuple[str, ...]:
    """Keep diff.external and diff.<x>.textconv from running; the internal diff output stays."""
    i = _subcommand_index(args)
    if i is None or args[i] != "diff":
        return args
    return (*args[: i + 1], "--no-ext-diff", "--no-textconv", *args[i + 1 :])  # always insert


class GitCommandError(Exception):
    code = "GIT_COMMAND_FAILED"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


def run_git(
    repo: Path,
    *args: str,
    index_file: Path | None = None,
    isolate_config: bool = False,
    timeout: float = GIT_TIMEOUT_SECONDS,
    ok_codes: tuple[int, ...] = (0,),
) -> str:
    """Run git in repo; index_file points git at a private index (GIT_INDEX_FILE).

    isolate_config ignores the operator's global and system git config for this call
    (``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` → /dev/null). Stripping GIT_* does not
    stop git reading ``~/.gitconfig``/``/etc/gitconfig``, where a setting such as
    ``gc.recentObjectsHook`` runs a command during a prune — which ``core.hooksPath``
    does not cover. Use it for maintenance commands (gc, prune) on a worker's clone.

    timeout is in seconds; pass NETWORK_TIMEOUT_SECONDS for a fetch or push over a network.
    A failure carries a bounded, sanitised tail of git's stderr (see stderr_tail).
    """
    return _exec(repo, args, index_file, isolate_config, timeout, text=True, ok_codes=ok_codes)


def commit_present(repo: Path, oid: str) -> bool:
    """Return whether oid names a commit; a broken supplied repository stays unreadable.

    A health-check failure after rev-parse fails returns False: supplied-repo damage is
    the candidate's fault and is reported as TEST_COMMIT_UNREADABLE (#1185).
    """
    try:
        found = run_git(
            repo, "rev-parse", "--verify", "--quiet", f"{oid}^{{commit}}", ok_codes=(0, 1)
        )
    except GitCommandError as exc:
        try:
            run_git(repo, "rev-parse", "--git-dir")
        except GitCommandError as health:
            if isinstance(health.__cause__, subprocess.CalledProcessError):
                return False
            raise OSError(f"Cairnwork could not run git: {health}") from health
        raise OSError(f"Cairnwork could not run git: {exc}") from exc
    return bool(found.strip())


def run_git_bytes(
    repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS, stdin: bytes | None = None,
    max_bytes: int | None = None,
) -> bytes:  # fmt: skip
    """Like run_git, but stdout is raw bytes: no decoding and no newline translation.

    stdin, when given, is git's standard input (``cat-file --batch``).
    A failure's ``CalledProcessError`` (``__cause__``) then carries bytes in ``stdout``.
    """
    return _exec(repo, args, None, False, timeout, text=False, stdin=stdin, max_bytes=max_bytes)


# GIT_ALLOW_PROTOCOL beats every protocol.*.allow setting, so git:// (core.gitProxy) and other
# <helper>:: remotes never run (#759). Set in _child_env and in cairnwork_attest.publish._git.
ALLOWED_PROTOCOLS = "file:https:ssh"
# Subcommands that may reach a remote, so ssh runs in BatchMode for them (#759). A credential
# helper that opens its own dialog still runs there: operator configuration, kept on purpose.
NETWORK_SUBCOMMANDS = ("fetch", "push", "pull", "clone", "ls-remote")


def _child_env() -> dict[str, str]:
    """Base environment for git: no inherited GIT_*, no askpass, terminal prompts off, and
    only the file, https and ssh transports."""
    try:
        require_no_lazy_fetch()
    except GitVersionError as exc:
        raise GitCommandError(str(exc)) from exc
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ALLOW_PROTOCOL"] = ALLOWED_PROTOCOLS
    env["GIT_NO_LAZY_FETCH"] = "1"  # #489: no lazy fetch in a partial clone
    return env


def ssh_batch_mode(env: dict[str, str], repo: Path) -> None:
    """Make ssh fail instead of prompting (password, a key passphrase) on a network fetch or
    push, unless the operator configured ``core.sshCommand``, which then decides prompting
    (#759). The caller's GIT_SSH and GIT_SSH_COMMAND stay dropped with the other GIT_*
    variables. When the setting cannot be read, BatchMode is set (it outranks the setting)."""
    try:
        configured = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            [git_executable(), "-C", str(repo), "config", "--get", "core.sshCommand"],
            capture_output=True, env=env, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )  # fmt: skip
        if configured.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"


def _exec(
    repo: Path,
    args: tuple[str, ...],
    index_file: Path | None,
    isolate_config: bool,
    timeout: float,
    *,
    text: bool,
    stdin: bytes | None = None,
    ok_codes: tuple[int, ...] = (0,),
    max_bytes: int | None = None,
) -> Any:  # str when text, else bytes
    if not timeout or timeout <= 0:
        raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
    env = _child_env()
    mark = _MARK.get()
    if mark is not None:  # before ssh_batch_mode: its config read is a git call to mark too
        var, val = mark
        env[var] = val
    if _subcommand(args) in NETWORK_SUBCOMMANDS:  # any of them may reach origin over ssh; a
        ssh_batch_mode(env, repo)  # local path or bundle never calls ssh, so this is harmless
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    if isolate_config:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
    try:
        run = subprocess.run if max_bytes is None else capped(max_bytes)
        done = run(  # noqa: S603 - fixed git argv, no shell
            [
                git_executable(),
                *_SAFE_OPTIONS,
                *config_hooks_off(repo, env),
                "-C",
                str(repo),
                *_no_external_diff(_safe_transport_args(args)),
            ],
            check=True,
            input=stdin,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.returncode in ok_codes:
            if not exc.stderr:  # a status the caller reads, only when git said nothing (#1011)
                return exc.stdout
        tail = stderr_tail(getattr(exc, "stderr", None))
        raise GitCommandError(
            f"git {_subcommand(args)} failed" + (f": {tail}" if tail else "")
        ) from exc
    return done.stdout


def hash_blob(repo: Path, data: bytes) -> str:
    """Store data as a blob in repo with no filters, from bytes the caller already read."""
    env = _child_env()
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            [
                git_executable(),
                *_SAFE_OPTIONS,
                *config_hooks_off(repo, env),
                "-C",
                str(repo),
                "hash-object",
                "-w",
                "--no-filters",
                "--stdin",
            ],  # noqa: S607
            input=data,
            capture_output=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCommandError("git hash-object failed") from exc
    oid = done.stdout.decode("ascii", "replace").strip()
    if not OID.fullmatch(oid):
        raise GitCommandError(f"git hash-object returned {oid!r}")
    return oid
