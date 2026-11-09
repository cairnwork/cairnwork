"""Which failed test calls failed from the environment (#808). report_guard's plugin marks a
failed call ``env`` when its exception, or anything in its cause/context chain, is: a MemoryError
or RecursionError (resource limits), an OSError, or a ``subprocess.CalledProcessError`` of a child
that could not run (exit 126/127, a signal, or stderr/output saying not found / permission denied
/ operation not permitted / cannot allocate memory). Any other nonzero exit stays a test failure:
a child that ran the code under test and exited nonzero looks the same as an environment failure.
A subclass defined by checkout code counts too, as on main (errisinstance(OSError)); loosening
that belongs to #797's own gate. An AssertionError is a test's own judgement
and is not walked. Only moves a base from TEETH toward UNCOMPARABLE.

Plugin text, not parent code (the plugin cannot import cairnwork): report_guard concatenates
ENV_SOURCE, which starts with report_denied's _denied_inside and defines ``_env_exc`` (an
environment failure, or a denial-shaped OSError wrapped in the failure, #854).
``is_env_failure`` runs the same source in the parent, for tests."""

from __future__ import annotations

import errno
import os
import socket
import sys
from typing import Any

from cairnwork.report_denied import DENIED_SOURCE

ENV_SOURCE = (
    DENIED_SOURCE
    + """\
import re
import subprocess

_ENV_TEXT = re.compile(
    rb"not found|permission denied|operation not permitted|cannot allocate memory", re.I
)


def _env_link(exc, _type=type, _sub=issubclass):
    cls = _type(exc)
    if _sub(cls, (MemoryError, RecursionError, OSError)):
        return True
    if _sub(cls, subprocess.CalledProcessError):
        code = exc.returncode
        parts = [p if isinstance(p, bytes) else str(p).encode() for p in (exc.stderr, exc.output)]
        return _type(code) is int and (code in (126, 127) or code < 0) or bool(
            _ENV_TEXT.search(b" ".join(parts)))
    return False


def _env_exc(info, root, _denied=_denied_inside, _type=type, _base=BaseException,
             _sub=issubclass, _link=_env_link,
             _cause=BaseException.__cause__.__get__, _ctx=BaseException.__context__.__get__,
             _quiet=BaseException.__suppress_context__.__get__):
    # root is kept for signature compatibility (callers pass it); it is not read.
    # a call's exception info -> True when it, or a link of its cause/context chain (at most 64),
    # failed from the environment. Read through type() and the built-in descriptors, so a test's
    # exception class (properties, __eq__) runs no code here; an AssertionError is not walked;
    # a cycle ends the walk; an unreadable chain or one past the bound is an environment failure
    # (fail closed).
    try:
        exc = top = getattr(info, "value", None)
        if not _sub(_type(exc), _base):
            return False
        if isinstance(exc, OSError):
            return True
        if _sub(_type(exc), AssertionError):
            return _denied(exc)
        seen = set()
        for _ in range(64):
            if _link(exc):
                return True
            seen.add(id(exc))
            cause = _cause(exc)
            exc = cause if cause is not None else (None if _quiet(exc) else _ctx(exc))
            if exc is None or id(exc) in seen or not _sub(_type(exc), _base):
                return _denied(top)
        return True
    except BaseException:
        return True


"""
)


def is_env_failure(info: Any, root: Any) -> bool:
    """The plugin's ``_env_exc`` for a pytest ExceptionInfo (anything with ``.value``)."""
    namespace: dict[str, Any] = {"errno": errno, "os": os, "socket": socket, "sys": sys}
    exec(ENV_SOURCE, namespace)
    return bool(namespace["_env_exc"](info, root))
