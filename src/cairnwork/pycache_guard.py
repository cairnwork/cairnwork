"""The per-run bytecode cache of a verifier run, and the check that nothing in it was forged.

Council action 3, item C2 (#273). A verifier run sets PYTHONPYCACHEPREFIX to ``PYCACHE_DIR``
inside its own TMPDIR, so a test that writes bytecode (``python3 -m py_compile``, compileall)
no longer puts ``__pycache__`` into the checkout, where the final comparison fails the run.

The prefix is outside that comparison, and Python *reads* bytecode from it: the import system
loads ``<prefix>/<source dir>/<name>.<tag>.pyc`` whose header matches the source's mtime and
size, and pytest's assertion rewriter loads ``<name>.<tag>-pytest-<version>.pyc`` from the same
place even with PYTHONDONTWRITEBYTECODE set (``_pytest/assertion/rewrite.py``: ``get_cache_dir``
uses ``sys.pycache_prefix``; ``_read_pyc`` checks only magic, flags, mtime and size). So
candidate code could plant bytecode that runs instead of a protected test, conftest or helper,
or instead of a module outside the checkout, and the checkout would still compare clean.

After the run, therefore, the prefix may hold only plain bytecode for files the run itself may
write and that are not protected verifier inputs. Each finding fails the run (FORGED_BYTECODE):

* any pytest-rewritten pyc (``-pytest-`` in the name): pytest writes none here, because the
  run sets PYTHONDONTWRITEBYTECODE;
* a pyc whose source would be outside the run's write roots (the interpreter, site-packages,
  the trusted guard plugin) or a protected input inside the checkout (``verifier_spec``'s
  protected names, the test roots, the directories of the run's targets, root-level modules,
  and test-named files anywhere). This also fails a test that starts Python without
  PYTHONDONTWRITEBYTECODE so that it caches the standard library here: fail closed, since such
  bytecode cannot be told apart from a forgery;
* a symlink or other non-regular entry, an entry that cannot be read, a prefix that is not a
  plain directory, or more than ``MAX_ENTRIES`` entries.

The checkout and its protected names are matched ignoring ASCII case (APFS is usually
case-insensitive, so ``Tests/helper.cpython-312.pyc`` is found for ``tests/helper.py``), and
any non-ASCII name in the prefix fails the run: APFS also folds names such as ``teſts`` (long s),
ligatures, the Kelvin sign and NFC/NFD spellings onto ASCII ones. The run's own paths are ASCII
(the sandbox profile accepts no other), so only names the run created can be non-ASCII.

Candidate code may also point ``sys.pycache_prefix`` at another writable directory at runtime
(#276): bytecode_roots.py scans everything else the run may write, with rules built on the ones
here. Known gap, as with the checkout comparison: bytecode planted and removed again during the
run is not seen. The separate verifier account and the report guard remain the boundary.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cairnwork.issue_task import DEFAULT_TEST_ROOTS
from cairnwork.verifier_spec import protected_verifier_inputs

PYCACHE_DIR = "pycache"
CODE = "FORGED_BYTECODE"
MAX_ENTRIES = 10_000
_TEST_DIRS = frozenset({"tests", "test", "testing"})


def _protected_in_checkout(rel: str, target_dirs: tuple[str, ...] = ()) -> bool:
    """rel is checkout-relative. Stricter than protected_verifier_inputs alone (no base commit,
    so every root module stays protected): test-named files and test dirs count anywhere, and so
    does anything under a directory holding one of the run's targets (``target_dirs``, lower case:
    a configured test root ends in tests/ or test/ already, this does not rely on it)."""
    rel = rel.lower()  # case-insensitive APFS: Tests/ and CONFTEST.py are tests/ and conftest.py
    parts = rel.split("/")
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py") or _TEST_DIRS & set(parts[:-1]):
        return True
    if any(rel.startswith(f"{directory}/") for directory in target_dirs):
        return True
    return bool(protected_verifier_inputs((rel,), DEFAULT_TEST_ROOTS))


def _sources(source_dir: str, name: str) -> list[str]:
    """Every source path the pyc ``name`` in ``source_dir`` could stand for: the stem is the
    part before any one dot (``a.b.cpython-312.opt-1.pyc`` may be ``a.py`` or ``a.b.py``)."""
    stem = name[: -len(".pyc")]  # the caller matched ".pyc" in any case
    cuts = [i for i, char in enumerate(stem) if char == "."] or [len(stem)]
    return [os.path.join(source_dir, stem[:cut] + ".py") for cut in cuts]


def _within(path: str, roots: tuple[str, ...], fold: bool = False) -> str | None:
    """The root ``path`` lies under (lexically; ignoring case with fold), or None."""
    for root in roots:
        here, top = (path.lower(), root.lower()) if fold else (path, root)
        if here == top or here.startswith(top.rstrip("/") + "/"):
            return root
    return None


def _shown(path: str, checkouts: tuple[str, ...]) -> str:
    """Checkout-relative when inside the checkout, in any case (reasons cut long names).
    The checkout counts case-insensitively, so a case variant is judged as protected, not
    waved through as a writable sibling of the checkout."""
    checkout = _within(path, checkouts, fold=True)
    return path[len(checkout) :].lstrip("/") or "." if checkout is not None else path


def _judge(source: str, checkouts, writable, target_dirs: tuple[str, ...]) -> str:
    """'' if bytecode for ``source`` may sit in the prefix, else why not."""
    if _within(source, checkouts, fold=True) is not None:
        rel = _shown(source, checkouts)
        if _protected_in_checkout(rel, target_dirs):
            return f"{rel} (protected input)"
        if _within(source, checkouts) is not None:
            return ""
        # a case variant that is not protected: on a case-sensitive filesystem it may be a
        # different place, so it must be inside a write root as spelled
    return "" if _within(source, writable) else f"{source} (outside the run)"


def _variants(*paths: Path) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p for path in paths for p in (str(path), os.path.realpath(path))))


def _target_dirs(targets: tuple[str, ...]) -> tuple[str, ...]:
    """The directories (lower case) of the run's pytest targets (``tests/test_a.py[::name]``)."""
    return tuple(
        {d.lower() for t in targets if (d := os.path.dirname(t.split("::", 1)[0])) not in ("", ".")}
    )


def forged_bytecode(
    prefix: Path, checkout: Path, write_roots: tuple[Path, ...], targets: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """What in ``prefix`` fails the run (empty: nothing). Never follows a symlink. ``targets``
    are the verifier's pytest targets (``tests/test_a.py[::name]``): their directories are
    protected too."""
    target_dirs = _target_dirs(targets)
    try:
        top = os.lstat(prefix)
    except FileNotFoundError:
        return ()  # the run wrote no bytecode
    except OSError as error:
        return (f"{PYCACHE_DIR} unreadable ({error.strerror})",)
    if not stat.S_ISDIR(top.st_mode):
        return (f"{PYCACHE_DIR} is not a plain directory",)
    checkouts, writable = _variants(checkout), _variants(*write_roots)
    found: list[str] = []
    entries = 0
    errors: list[OSError] = []
    for directory, dirs, files in os.walk(prefix, onerror=errors.append):
        entries += len(dirs) + len(files)
        if entries > MAX_ENTRIES:
            return (*found, f"{PYCACHE_DIR} has more than {MAX_ENTRIES} entries")
        rel = os.path.relpath(directory, prefix)
        source_dir = "/" if rel == "." else "/" + rel
        for name in (*dirs, *files):
            try:
                mode = os.lstat(os.path.join(directory, name)).st_mode
            except OSError as error:
                errors.append(error)
                continue
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                shown = _shown(os.path.join(source_dir, name), checkouts)
                found.append(f"{shown} (not a plain file or directory)")
            if not name.isascii():  # APFS folds long s, ligatures, Kelvin sign, NFC/NFD forms
                shown = _shown(os.path.join(source_dir, name), checkouts)
                found.append(f"{shown} (non-ASCII name)")
        for name in files:
            if "-pytest-" in name.lower():
                shown = _shown(os.path.join(source_dir, name), checkouts)
                found.append(f"{shown} (pytest-rewritten bytecode)")
            elif name.lower().endswith(".pyc"):
                why = [
                    _judge(s, checkouts, writable, target_dirs) for s in _sources(source_dir, name)
                ]
                found.extend(reason for reason in why if reason)
    found.extend(f"{PYCACHE_DIR} unreadable ({error.strerror})" for error in errors)
    return tuple(dict.fromkeys(found))
