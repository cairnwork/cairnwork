"""Run git with a fixed argv, no shell, and no inherited GIT_* environment.

Stripping GIT_* keeps a hook environment (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE)
from pointing a command at another repository or index. Hooks, the fsmonitor
command, configuration-declared hooks and the untracked cache are disabled and replace refs
are ignored.
These options are a second line of defence: workers never get a checkout that
shares Cairnwork's repository (see workspace.py), and Cairnwork never runs git
inside a worker's repository except to fetch its HEAD.
"""

from __future__ import annotations

import contextvars
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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


# Options that config files cannot override: no hooks, no fsmonitor command, no
# untracked cache, no password prompts, and replace refs ignored so a commit is
# always what it is.
# Programs deliberately kept: credential helper and ssh command (operator configuration,
# used only by network fetch/push of origin; local fetches from paths and bundles never
# call them). Diff drivers, filters and submodules are follow-ups of #759.
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
)


# hook.<friendly-name>.<key> (git 2.54 and later): hooks declared in configuration, which
# core.hooksPath does not turn off (#737). The friendly name may itself hold dots.
# The name may also be empty (``[hook ""]``) or hold bytes that are not UTF-8.
_HOOK_KEY = re.compile(r"hook\.(.*)\.[a-z][a-z0-9-]*", re.DOTALL)


def config_hooks_off(repo: Path | str, env: dict[str, str]) -> tuple[str, ...]:
    """``-c hook.<name>.enabled=false`` for every hook declared in the configuration git reads
    in repo with env (system, global, repository and GIT_CONFIG_* pairs), to put after
    _SAFE_OPTIONS. A name that ``-c`` cannot carry (it holds ``=``) is refused, and so is a
    listing that does not finish (timeout, git not started). The listing is every key, filtered
    here (a regex in git, under a UTF-8 locale, skips a key that is not UTF-8). When it exits
    non-zero (no such directory, a malformed file) nothing is added: the command
    itself reads the same configuration and fails the same way before any hook could run.
    Names are decoded with surrogateescape, so ``-c`` carries the same bytes back to git (a name
    that is not UTF-8 is still matched). Listing runs no hook, so it needs no _SAFE_OPTIONS.
    """
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            ["git", "-C", str(repo), "config", "--null", "--name-only", "--list"],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, env={**env, "LC_ALL": "C"},
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCommandError("git config (hook listing) failed; refusing to run git") from exc
    if done.returncode != 0:
        return ()
    names = sorted({m.group(1) for k in done.stdout.decode("utf-8", "surrogateescape").split("\0")
                    if (m := _HOOK_KEY.fullmatch(k))})  # fmt: skip
    if any("=" in n for n in names):
        raise GitCommandError("a configuration-declared hook name holds '='; refusing to run git")
    return tuple(x for n in names for x in ("-c", f"hook.{n}.enabled=false"))


# Greedy to the last '@' in the run free of space and tab (newlines count as part of the run, so
# one planted in the userinfo cannot split it): a password may itself hold '@' or '/'.
# Fail-closed: an '@' later in a path also removes the text before it.
_URL_USERINFO = re.compile(r"(?<=://)[^ \t]*@")


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
    text = _URL_USERINFO.sub("", replace_hidden(raw or "", "", keep="\n")).strip()
    return text.replace("\n", " | ")[-STDERR_TAIL_CHARS:]


def _subcommand(args: tuple[str, ...]) -> str:
    """The git subcommand in args, skipping ``-c <name=value>``, ``-C <path>`` and other options."""
    it = iter(args)
    for arg in it:
        if arg in ("-c", "-C"):
            next(it, None)
        elif not arg.startswith("-"):
            return arg
    return args[0] if args else "command"


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


def run_git_bytes(
    repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS, stdin: bytes | None = None
) -> bytes:
    """Like run_git, but stdout is raw bytes: no decoding and no newline translation.

    stdin, when given, is git's standard input (``cat-file --batch``).
    A failure's ``CalledProcessError`` (``__cause__``) then carries bytes in ``stdout``.
    """
    return _exec(repo, args, None, False, timeout, text=False, stdin=stdin)


def _child_env() -> dict[str, str]:
    """Base environment for git: no inherited GIT_*, no askpass, terminal prompts off."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


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
) -> Any:  # str when text, else bytes
    if not timeout or timeout <= 0:
        raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
    env = _child_env()
    mark = _MARK.get()
    if mark is not None:
        var, val = mark
        env[var] = val
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    if isolate_config:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            ["git", *_SAFE_OPTIONS, *config_hooks_off(repo, env), "-C", str(repo), *args],
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
                "git",
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
