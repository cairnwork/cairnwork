"""Bytecode planted anywhere the run may write, not only in its own cache prefix (Issue #276).

Candidate code runs inside the pytest process and may reassign ``sys.pycache_prefix`` at runtime
to any directory the sandbox lets it write: the run directory (its HOME, its TMPDIR with the
run's own prefix, anything else in it) or the work directory around the checkout (the checkout's
top-level .git, which the final comparison does not cover, and the checkout). Bytecode planted as
``<P>/<source dir>/<name>.<tag>.pyc``, or pytest's ``<name>.<tag>-pytest-<ver>.pyc``, is then
read instead of a protected test, conftest or helper, as in the run's own prefix
(pycache_guard.py). The sandbox lets the run write nothing else that can hold a file:
``/dev/null``, ``/dev/tty`` and ``/dev/dtracehelper`` are devices, and bwrap's tmpfs mounts on
Linux end with the sandbox (the first gap below).

After every run, before HOME and TMPDIR are removed, bytecode_walk.py lists both roots, the run's
own prefix included (a prefix pointed inside it is not where pycache_guard looks), and walks every
prefix P and source directory D a pyc-like entry could be read through, identifying directories
by device and inode so that no spelling (case, NFC/NFD, firmlinks, symlinks, ``..``) hides one.
It runs as the run's own user (below). The owner fails the run (FORGED_BYTECODE) on:

* an existing source a pyc can stand for that is a protected input of the checkout (in any case,
  .git included), or that lies outside the run's write roots (the interpreter, site-packages,
  the trusted guard plugin, the account's own files); a missing source is no finding, since
  Python never loads bytecode for it, which is what lets honest bytecode pass. Legacy
  ``__pycache__`` bytecode next to such a source counts too (read once the prefix is cleared);
* a pytest-rewritten pyc (``-pytest-``) anywhere outside the checkout: the run sets
  PYTHONDONTWRITEBYTECODE, so an honest pytest writes none (in the checkout, the comparison has
  already shown each file to be the candidate's own committed content, judged as above);
* a non-ASCII pyc name, an unreadable directory, more than ``MAX_ENTRIES`` entries, more than
  ``MAX_STATES`` walk steps, or a scan that fails.

Precise, not "any .pyc fails": honest suites write bytecode outside the prefix (py_compile with
an explicit cfile, compileall's legacy layout, a child Python with a scrubbed environment caching
a tmp_path module) and pytest links ``pytest-current``; none of that stands for an existing
protected or outside source. A directory the run closed to itself (a test of permission errors)
is opened (u+rwx) before it is listed; only modes of scratch removed next are changed.

Under the verifier account the owner cannot list the account's 0700 directories (pytest's
``pytest-of-<user>``), and what the account can open is what matters, so there the helper runs
AS THE ACCOUNT, by the verifier's Python with ``-I -S`` through the sudo path
account_checkout.py uses, once ``kill_all`` confirms no process of the account is left. In owner
mode it runs as the owner. The owner judges its output.

Known gaps. Bytecode planted and removed again during the run is not seen (as for the checkout
and the run's prefix). Candidate code that changes the import path itself (``sys.path``,
``sys.meta_path``, path hooks, a ``.pth`` or a zip archive on the path) can load its own
``helper.py`` or bytecode from HOME without any of this: that in-process import hijack is not
claimed here or in pycache_guard; the report guard and the verifier account are the boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cairnwork.account_checkout import _as_account
from cairnwork.pycache_guard import (
    PYCACHE_DIR,
    _protected_in_checkout,
    _shown,
    _target_dirs,
    _within,
    forged_bytecode,
)
from cairnwork.verifier_account import VerifierAccountError

MAX_ENTRIES = 100_000  # wider than the prefix's 10,000: the checkout and tmp_path trees count
MAX_STATES = 100_000  # walk steps beyond one per directory; symlink loops end here, closed
SCAN_TIMEOUT_SECONDS = 120
HELPER = Path(__file__).with_name("bytecode_walk.py")  # run as a program, never imported here
_SHAPES = {
    "pycs": (int, str),
    "errors": (int, str, str),
    "hits": (int, str, str, int, str, list),
}


def _rows_ok(listing: dict, roots: int) -> bool:
    """The helper's output has the expected shape; root indexes name a root (-1: none)."""
    for key, kinds in _SHAPES.items():
        for row in listing[key]:
            if len(row) != len(kinds) or not all(map(isinstance, row, kinds)):
                return False
            if not 0 <= row[0] < roots or (key == "hits" and not -1 <= row[3] < roots):
                return False
            if key == "hits" and not all(isinstance(name, str) for name in row[5]):
                return False
    return isinstance(listing["over"], bool) and isinstance(listing["steps_over"], bool)


def _listing(spec: dict, account, python: str) -> tuple[dict | None, str]:
    """Run the helper (as the account when there is one); (listing, "") or (None, why not)."""
    arg = json.dumps(spec)
    try:
        source = HELPER.read_text()
        if account is not None:
            if account.kill_all() != 0:
                return None, "the account's processes could not be confirmed ended"
            done = _as_account(account, python, source, [arg], SCAN_TIMEOUT_SECONDS)
        else:
            done = subprocess.run(  # noqa: S603 - fixed helper, paths made by the verifier
                [python, "-I", "-S", "-c", source, arg],
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
        if not _rows_ok(listing, len(spec["roots"])):
            raise TypeError
    except (ValueError, TypeError, KeyError):
        return None, "bytecode scan failed (unreadable listing)"
    return listing, ""


def _display(root: Path, rel: str, checkouts) -> str:
    """Checkout-relative inside the checkout, else relative to the write root's parent, cut
    in the middle so the reason after it survives the reason text's length cap."""
    path = os.path.join(str(root), rel) if rel else str(root)
    if _within(path, checkouts) is not None:
        return _shown(path, checkouts)
    parts = [root.name, *rel.split("/")] if rel else [root.name]
    text = "/".join(parts)
    return text if len(text) <= 80 else "/".join([parts[0], "...", *parts[-2:]])


def _judged_hits(hits, roots: list[str], checkout: Path, target_dirs) -> list[str]:
    """A hit is an existing source a pyc can stand for, with the directory holding it: inside
    the write roots (by identity) only a protected input of the checkout fails, outside them
    every one does."""
    found = []
    top = str(checkout)
    for _, _, d_path, d_index, d_rel, names in hits:
        if d_index < 0:
            found.extend(f"{os.path.join(d_path, name)} (outside the run)" for name in names)
            continue
        where = os.path.join(roots[d_index], d_rel) if d_rel else roots[d_index]
        if _within(where, (top,)) is None:
            continue  # a place the run may write: its sources are the run's own
        for name in names:
            rel = os.path.join(where[len(top) :].strip("/"), name)
            if _protected_in_checkout(rel, target_dirs):
                found.append(f"{rel} (protected input)")
    return found


def redirected_bytecode(
    checkout: Path, write_roots: tuple[Path, ...], targets: tuple[str, ...] = (),
    account=None, python: str = sys.executable,
) -> tuple[str, ...]:  # fmt: skip
    """What the run left where it may write (``write_roots``: the work directory around the
    checkout and the run directory) that could stand for code it may not supply (empty:
    nothing). ``targets`` as for forged_bytecode."""
    roots = [str(root) for root in write_roots]
    spec = {"roots": roots, "limit": MAX_ENTRIES, "max_states": MAX_STATES}
    listing, failure = _listing(spec, account, python)
    if listing is None:
        return (failure,)
    checkouts = (str(checkout), os.path.realpath(checkout))
    git_dirs = (str(checkout / ".git"), os.path.realpath(checkout / ".git"))

    def shown(index: int, rel: str) -> str:
        return _display(write_roots[index], rel, checkouts)

    found: list[str] = []
    for index, rel in listing["pycs"]:
        path, name = os.path.join(roots[index], rel), os.path.basename(rel)
        committed = _within(path, checkouts) is not None and _within(path, git_dirs) is None
        if not name.isascii():
            found.append(f"{shown(index, rel)} (non-ASCII name)")
        elif "-pytest-" in name.lower() and not committed:
            found.append(f"{shown(index, rel)} (pytest-rewritten bytecode)")
    found.extend(_judged_hits(listing["hits"], roots, checkout, _target_dirs(targets)))
    found.extend(f"{shown(index, rel)} unreadable ({why})" for index, rel, why in listing["errors"])
    if listing["over"]:
        found.append(f"more than {MAX_ENTRIES} entries where the run may write")
    if listing["steps_over"]:
        found.append(f"more than {MAX_STATES} steps to judge the run's bytecode")
    return tuple(dict.fromkeys(found))


def bytecode_findings(tmp: Path, checkout: Path, run_dir: Path, targets, account, python):
    """Everything that fails a finished run as FORGED_BYTECODE: the run's own prefix in its
    TMPDIR (pycache_guard.py), then everything it may write (above)."""
    roots = (checkout.parent, run_dir)
    own = forged_bytecode(tmp / PYCACHE_DIR, checkout, roots, targets)
    return tuple(
        dict.fromkeys((*own, *redirected_bytecode(checkout, roots, targets, account, python)))
    )
