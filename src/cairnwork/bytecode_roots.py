"""Bytecode planted anywhere the run may write, not only in its own cache prefix (Issue #276).

Candidate code runs inside the pytest process and may reassign ``sys.pycache_prefix`` at runtime
to any directory the sandbox lets it write: the run directory (its HOME, its TMPDIR with the
run's own prefix, anything else in it) or the work directory around the checkout (the checkout's
top-level .git, which the final comparison does not cover, and the checkout). Bytecode planted as
``<P>/<source dir>/<name>.<tag>.pyc``, or pytest's ``<name>.<tag>-pytest-<ver>.pyc``, is then
read instead of a protected test, conftest or helper, as in the run's own prefix
(pycache_guard.py). The sandbox lets the run write nothing else that can hold a file:
``/dev/null``, ``/dev/tty`` and ``/dev/dtracehelper`` are devices, and bwrap's tmpfs mounts on
Linux end with the sandbox (the gap below).

After every run, before HOME and TMPDIR are removed, both roots are listed (names only, symlinks
never followed), the run's own prefix included (a prefix pointed at a directory inside it is
not where pycache_guard looks). Every entry whose name reads as a ``.pyc`` (Unicode compatibility
and case folded, non-ASCII characters dropped) is judged against every prefix P it could sit
under: for a directory P and an existing directory D, Python reads ``<P>/<D>/<name>`` for a
source in D. So the scan walks the listing from every directory P that leads to such an entry,
along names that, appended to ``/``, keep D an existing directory, through subdirectories and
through symlinks that resolve to a directory of the run (a symlink, a loop included, spells the
same place another way). Where the walk reaches the entry's directory with D, D's sources are
judged as pycache_guard judges them, by D as spelled and by its real path. An entry in a
``__pycache__`` directory is also judged as the legacy layout next to its source, which Python
reads once candidate code clears ``sys.pycache_prefix``. Each finding fails the run
(FORGED_BYTECODE):

* an existing source that is a protected input of the checkout (in any case) or lies outside the
  run's write roots (the interpreter, site-packages, the trusted guard plugin, the account's own
  files). A source or directory the owner cannot stat counts as existing; a missing one is no
  finding, since Python never loads bytecode for it, which is what lets honest bytecode pass;
* a pytest-rewritten pyc (``-pytest-``) anywhere outside the checkout: the run sets
  PYTHONDONTWRITEBYTECODE, so an honest pytest writes none (in the checkout, the comparison has
  already shown each file to be the candidate's own committed content, judged as above);
* a non-ASCII pyc name, or a non-ASCII name the walk would follow (APFS folds long s, ligatures,
  the Kelvin sign and NFC/NFD forms onto ASCII), an unreadable directory, more than
  ``MAX_ENTRIES`` entries, more than ``MAX_STATES`` walk steps, or a scan that fails.

Precise, not "any .pyc fails": honest suites write bytecode outside the prefix (py_compile with
an explicit cfile, compileall's legacy layout, a child Python with a scrubbed environment caching
a tmp_path module) and pytest links ``pytest-current``; none of that spells an existing protected
or outside source. A directory the run closed to itself (a test of permission errors) is opened
(u+rwx) before it is listed; only modes of scratch removed next are changed.

Under the verifier account the owner cannot list the account's 0700 directories (pytest's
``pytest-of-<user>``), so there the listing runs AS THE ACCOUNT: a fixed helper, run by the
verifier's Python with ``-I -S`` through the sudo path account_checkout.py uses, once
``kill_all`` confirms no process of the account is left. In owner mode the same helper runs as
the owner. The owner judges the listing. Known gap, as for the checkout and the run's prefix:
bytecode planted and removed again during the run is not seen.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
import sys
from collections import deque
from pathlib import Path

from cairnwork.account_checkout import _as_account
from cairnwork.pycache_guard import (
    PYCACHE_DIR,
    _protected_in_checkout,
    _shown,
    _sources,
    _target_dirs,
    _variants,
    _within,
    forged_bytecode,
)
from cairnwork.verifier_account import VerifierAccountError

MAX_ENTRIES = 100_000  # wider than the prefix's 10,000: the checkout and tmp_path trees count
MAX_STATES = 100_000  # walk steps beyond one per directory; symlink loops end here, closed
MAX_PATH = 4096  # Linux PATH_MAX (macOS: 1024): no longer path can be opened
SCAN_TIMEOUT_SECONDS = 120
_MISSING = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ENAMETOOLONG, errno.ELOOP})

# Runs as ``python -I -S -c LIST_HELPER <spec json>`` and prints a JSON listing: every directory,
# every symlink that resolves to a directory of the run, every pyc-like name, and every error
# (never skipped silently), each as [root index, path relative to that root, ...]. A symlink is
# never entered; a directory closed to its owner is opened (u+rwx) before it is listed.
LIST_HELPER = r"""
import json, os, stat, sys, unicodedata

spec = json.loads(sys.argv[1])
roots = spec["roots"]
areas = [os.path.realpath(root).rstrip("/") for root in roots]
out = {"dirs": [], "links": [], "pycs": [], "errors": [], "over": False}
nofollow = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
count = 0

def pyc_like(name):
    folded = unicodedata.normalize("NFKD", name).casefold()
    return "".join(c for c in folded if c.isascii()).endswith(".pyc")

def located(real):
    for index, area in enumerate(areas):
        if real == area or real.startswith(area + "/"):
            return [index, real[len(area):].strip("/")]
    return None

def entries(top):
    info = os.lstat(top)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(20, "not a directory")
    if info.st_mode & 0o700 != 0o700:
        os.chmod(top, stat.S_IMODE(info.st_mode) | 0o700, **nofollow)
    with os.scandir(top) as listing:
        return list(listing)

stack = [(index, "") for index in range(len(roots))]
while stack and not out["over"]:
    index, rel = stack.pop()
    try:
        found = entries(os.path.join(roots[index], rel) if rel else roots[index])
    except OSError as exc:
        out["errors"].append([index, rel, exc.strerror or str(exc)])
        continue
    out["dirs"].append([index, rel])
    for entry in found:
        count += 1
        if count > spec["limit"]:
            out["over"] = True
            break
        sub = rel + "/" + entry.name if rel else entry.name
        if pyc_like(entry.name):
            out["pycs"].append([index, sub])
        try:
            if entry.is_symlink():
                real = os.path.realpath(entry.path)
                place = located(real)
                if place is not None and os.path.isdir(real):
                    out["links"].append([index, sub, *place])
            elif entry.is_dir(follow_symlinks=False):
                stack.append((index, sub))
        except OSError as exc:
            out["errors"].append([index, sub, exc.strerror or str(exc)])
print(json.dumps(out))
"""


def _exists(path: str) -> bool:
    """Whether ``path`` may exist for the run: only a definite "no such file" is False."""
    try:
        os.lstat(path)
    except OSError as error:
        return error.errno not in _MISSING
    return True


def _may_be_dir(path: str, cache: dict[str, bool]) -> bool:
    """Whether Python could open ``path`` as a directory (symlinks followed, as it would)."""
    if path not in cache:
        try:
            cache[path] = len(path) < MAX_PATH and stat.S_ISDIR(os.stat(path).st_mode)
        except OSError as error:
            cache[path] = error.errno not in _MISSING  # cannot tell (EACCES): as if it were one
    return cache[path]


def _verdict(source: str, checkouts, writable, target_dirs) -> str:
    """'' if bytecode for ``source`` may sit where the run may write, else why not. As
    pycache_guard's _judge, but only for a source that exists (Python never loads bytecode for a
    missing one; the run cannot remove a protected one unseen), and judged by its real directory
    too: the walk follows symlinks such as /var -> /private/var, so one place has several
    spellings."""
    if not _exists(source):
        return ""
    real = os.path.join(os.path.realpath(os.path.dirname(source)), os.path.basename(source))
    spellings = (source, real)
    for spelled in spellings:
        if _within(spelled, checkouts, fold=True) is not None:
            rel = _shown(spelled, checkouts)
            if _protected_in_checkout(rel, target_dirs):
                return f"{rel} (protected input)"
    if any(_within(spelled, writable) for spelled in spellings):
        return ""
    return f"{source} (outside the run)"


def _listing(spec: dict, account, python: str) -> tuple[dict | None, str]:
    """Run LIST_HELPER (as the account when there is one); (listing, "") or (None, why not)."""
    arg = json.dumps(spec)
    try:
        if account is not None:
            if account.kill_all() != 0:
                return None, "the account's processes could not be confirmed ended"
            done = _as_account(account, python, LIST_HELPER, [arg], SCAN_TIMEOUT_SECONDS)
        else:
            done = subprocess.run(  # noqa: S603 - fixed helper, paths made by the verifier
                [python, "-I", "-S", "-c", LIST_HELPER, arg],
                capture_output=True,
                text=True,
                timeout=SCAN_TIMEOUT_SECONDS,
                check=False,
                env={},
            )
    except (OSError, subprocess.SubprocessError, VerifierAccountError) as exc:
        return None, f"bytecode scan failed ({type(exc).__name__})"
    if done.returncode != 0:
        return None, f"bytecode scan failed (exit {done.returncode})"
    try:
        listing = json.loads(done.stdout)
        shapes = {"dirs": (int, str), "pycs": (int, str), "links": (int, str, int, str)}
        for key, kinds in shapes.items():
            for row in listing[key]:
                if len(row) != len(kinds) or not all(map(isinstance, row, kinds)):
                    raise TypeError(key)
                if not all(0 <= row[i] < len(spec["roots"]) for i in range(0, len(kinds), 2)):
                    raise TypeError(key)  # a root index, at 0 (and 2 for links)
        for row in listing["errors"]:
            if len(row) != 3 or not all(map(isinstance, row, (int, str, str))):
                raise TypeError("errors")
            if not 0 <= row[0] < len(spec["roots"]):
                raise TypeError("errors")
        if not isinstance(listing["over"], bool):
            raise TypeError("over")
    except (ValueError, TypeError, KeyError):
        return None, "bytecode scan failed (unreadable listing)"
    return listing, ""


def _graph(listing) -> tuple[dict, dict]:
    """The listing as edges (name, child node, rel of the entry) and pyc names per directory."""
    children: dict[tuple, list] = {tuple(node): [] for node in listing["dirs"]}
    for index, rel in listing["dirs"]:
        if rel:
            parent = (index, os.path.dirname(rel))
            children.setdefault(parent, []).append((os.path.basename(rel), (index, rel), rel))
    for index, rel, target, target_rel in listing["links"]:
        edge = (os.path.basename(rel), (target, target_rel), rel)
        children.setdefault((index, os.path.dirname(rel)), []).append(edge)
    pycs: dict[tuple, list[str]] = {}
    for index, rel in listing["pycs"]:
        pycs.setdefault((index, os.path.dirname(rel)), []).append(os.path.basename(rel))
    return children, pycs


def _walk(listing, judge, shown) -> list[str]:
    """Walk the listing from every directory P along names that keep ``/<D>`` an existing
    directory (subdirectories and symlinks to directories of the run alike), and judge each pyc
    where the walk reaches it with D (``judge(D, name)``). Only directories from which a pyc can
    be reached are walked, so a run without bytecode outside the checkout costs nothing."""
    children, pycs = _graph(listing)
    parents: dict[tuple, set] = {}
    for node, edges in children.items():
        for _, child, _ in edges:
            parents.setdefault(child, set()).add(node)
    useful, todo = set(pycs), list(pycs)
    while todo:
        for parent in parents.get(todo.pop(), ()):
            if parent not in useful:
                useful.add(parent)
                todo.append(parent)
    found: list[str] = []
    cache: dict[str, bool] = {}
    states = deque((node, "/") for node in useful)
    seen, budget = set(states), len(states) + MAX_STATES
    while states:
        node, source_dir = states.popleft()
        for name in pycs.get(node, ()):
            found.extend(judge(source_dir, name))
        for name, child, rel in children.get(node, ()):
            step = os.path.join(source_dir, name)
            if child not in useful or (child, step) in seen or not _may_be_dir(step, cache):
                continue
            if not name.isascii():
                found.append(f"{shown(node[0], rel)} (non-ASCII name)")
                continue
            seen.add((child, step))
            if len(seen) > budget:
                return [*found, f"more than {MAX_STATES} steps to judge the run's bytecode"]
            states.append((child, step))
    return found


def _display(root: Path, rel: str, checkouts) -> str:
    """Checkout-relative inside the checkout, else relative to the write root's parent, cut
    in the middle so the reason after it survives the reason text's length cap."""
    path = os.path.join(str(root), rel) if rel else str(root)
    if _within(path, checkouts) is not None:
        return _shown(path, checkouts)
    parts = [root.name, *rel.split("/")] if rel else [root.name]
    text = "/".join(parts)
    return text if len(text) <= 80 else "/".join([parts[0], "...", *parts[-2:]])


def redirected_bytecode(
    checkout: Path, write_roots: tuple[Path, ...], targets: tuple[str, ...] = (),
    account=None, python: str = sys.executable,
) -> tuple[str, ...]:  # fmt: skip
    """What the run left where it may write (``write_roots``: the work directory around the
    checkout and the run directory) that could stand for code it may not supply (empty:
    nothing). ``targets`` as for forged_bytecode."""
    spec = {"roots": [str(root) for root in write_roots], "limit": MAX_ENTRIES}
    listing, failure = _listing(spec, account, python)
    if listing is None:
        return (failure,)
    checkouts, writable = _variants(checkout), _variants(*write_roots)
    git_dirs, target_dirs = _variants(checkout / ".git"), _target_dirs(targets)

    def shown(index: int, rel: str) -> str:
        return _display(write_roots[index], rel, checkouts)

    def judge(source_dir: str, name: str) -> list[str]:
        dirs = [source_dir]
        if os.path.basename(source_dir).lower() == "__pycache__":  # the legacy layout
            dirs.append(os.path.dirname(source_dir))
        why = [
            _verdict(s, checkouts, writable, target_dirs) for d in dirs for s in _sources(d, name)
        ]
        return [reason for reason in why if reason]

    found: list[str] = []
    for index, rel in listing["pycs"]:
        path, name = os.path.join(spec["roots"][index], rel), os.path.basename(rel)
        committed = _within(path, checkouts) is not None and _within(path, git_dirs) is None
        if not name.isascii():
            found.append(f"{shown(index, rel)} (non-ASCII name)")
        elif "-pytest-" in name.lower() and not committed:
            found.append(f"{shown(index, rel)} (pytest-rewritten bytecode)")
        elif os.path.basename(os.path.dirname(rel)).lower() == "__pycache__":
            # read in place once candidate code clears sys.pycache_prefix: judge the source
            # next to it (committed bytecode of a protected input in the checkout fails)
            beside = os.path.dirname(os.path.dirname(path))
            why = (_verdict(s, checkouts, writable, target_dirs) for s in _sources(beside, name))
            found.extend(reason for reason in why if reason)
    found.extend(_walk(listing, judge, shown))
    found.extend(f"{shown(index, rel)} unreadable ({why})" for index, rel, why in listing["errors"])
    if listing["over"]:
        found.append(f"more than {MAX_ENTRIES} entries where the run may write")
    return tuple(dict.fromkeys(found))


def bytecode_findings(tmp: Path, checkout: Path, run_dir: Path, targets, account, python):
    """Everything that fails a finished run as FORGED_BYTECODE: the run's own prefix in its
    TMPDIR (pycache_guard.py), then everything it may write (above)."""
    roots = (checkout.parent, run_dir)
    own = forged_bytecode(tmp / PYCACHE_DIR, checkout, roots, targets)
    return tuple(
        dict.fromkeys((*own, *redirected_bytecode(checkout, roots, targets, account, python)))
    )
