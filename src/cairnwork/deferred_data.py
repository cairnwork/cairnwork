"""#850 stage A: test DATA files that a test commit declares (lint fixtures and the like).

A ``.py`` file of a test commit that is data, not a pytest target, is declared in the last
paragraph of the commit message: ``Cairnwork-Data: <path> [<path>...]``. The claim is narrow: a
declared file is not a test (it does not match the base's ``python_files``), is never read for
tests, and is never named by a verifier; that it was not run in-process (import, runpy, eval or
exec of a code object under its own name) is the signer's word under ``DATA_RULE``. Pure: no git."""

from __future__ import annotations

from collections.abc import Callable, Collection

from cairnwork.deferred_overlay import TestCommitRejected

DATA_TRAILER = "Cairnwork-Data"
DATA_RULE = "exec-refuse-v1"
MAX_DATA_PATHS = 64
_NEVER = frozenset({"conftest.py", "__init__.py"})


def path_problem(path: object) -> str:
    """Why ``path`` is not a safe checkout-relative ``.py`` path ('' when it is)."""
    if not isinstance(path, str) or not path:
        return "not a non-empty string"
    if path.startswith("/") or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "absolute, backslash or control character"
    if {"", ".", ".."} & set(path.split("/")):
        return "empty, '.' or '..' part"
    return "" if path.endswith(".py") else "not a .py file"


def declared_data(message: str) -> tuple[str, ...]:
    """The sorted paths a commit message declares; () when it has no trailer line. Refuses
    (DATA_DECLARATION) a CR, a second trailer line, a trailer outside the last paragraph (or in a
    one-paragraph message), an empty value, a duplicate, too many paths or a bad path."""
    if "\r" in message:
        raise TestCommitRejected("DATA_DECLARATION", "carriage return in the message")
    prefix = DATA_TRAILER + ":"
    lines = [ln for ln in message.split("\n") if ln.startswith(prefix)]
    if not lines:
        return ()
    paragraphs = message.rstrip("\n").split("\n\n")
    if len(lines) > 1 or len(paragraphs) < 2 or lines[0] not in paragraphs[-1].split("\n"):
        raise TestCommitRejected("DATA_DECLARATION", "one trailer line, in the last paragraph")
    value = lines[0][len(prefix) :].strip(" ")
    paths = value.split(" ") if value else []
    if not paths or len(paths) > MAX_DATA_PATHS or len(set(paths)) != len(paths):
        raise TestCommitRejected("DATA_DECLARATION", "empty, duplicate or too many paths")
    for path in paths:
        if why := path_problem(path):
            raise TestCommitRejected("DATA_DECLARATION", f"{path[:100]!r}: {why}")
    return tuple(sorted(paths))


def data_rule_problem(
    data: Collection[str],
    paths: Collection[str],
    conftests: Collection[str],
    is_test_file: Callable[[str], bool],
) -> str:
    """Why a declared set breaks the rule ('' = ok): each path is among the test commit's
    ``paths``, not a conftest, not named ``conftest.py`` or ``__init__.py`` (case folded) and not
    matched by the base's ``python_files`` (``is_test_file``)."""
    for path in data:
        if path not in paths:
            return f"{path[:100]!r} is not among the test commit's files"
        if path in conftests or path.rsplit("/", 1)[-1].casefold() in _NEVER:
            return f"{path[:100]!r} is a conftest or __init__ file"
        if is_test_file(path):
            return f"{path[:100]!r} matches the base's python_files"
    return ""
