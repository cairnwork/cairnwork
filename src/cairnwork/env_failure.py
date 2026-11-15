"""Which failed test calls failed from the environment (#808). report_guard's plugin marks a
failed call ``env`` when its exception, or anything in its cause/context chain, is: a MemoryError
or RecursionError (resource limits), an OSError, or a ``subprocess.CalledProcessError`` of a child
that could not run (exit 126/127, a signal, or stderr/output saying not found / permission denied
/ operation not permitted / cannot allocate memory). Any other nonzero exit stays a test failure:
a child that ran the code under test and exited nonzero looks the same as an environment failure.
#797: an OSError whose innermost frame is in the checkout and sits on an explicit ``raise`` is
the code under test speaking, not the environment (teeth); an OSError raised in stdlib or
site-packages, by a C call such as open() made from checkout code, with no readable traceback, or
carrying a denial errno stays an environment failure. An AssertionError is a test's own judgement
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
import dis
import os
import re
import subprocess
import sysconfig

_ENV_TEXT = re.compile(
    rb"not found|permission denied|operation not permitted|cannot allocate memory", re.I
)


_LIB_KEYS = ("stdlib", "platstdlib", "purelib", "platlib")
_LIB_DIRS = {sysconfig.get_path(k) or "/nonexistent" for k in _LIB_KEYS}
_LIB = tuple(os.path.realpath(p) + os.sep for p in _LIB_DIRS)
_VENV = ("site-packages", "dist-packages", ".venv", "venv", "node_modules")
_DENY = (errno.EACCES, errno.EPERM, errno.EROFS, errno.ENETUNREACH)


def _raised_in_checkout(exc, root):
    # #797: True only when the innermost traceback frame is checkout code on an explicit raise;
    # anything unreadable or unclear is False (the old behaviour: environment).
    try:
        if isinstance(exc, socket.gaierror) or exc.errno in _DENY:
            return False
        tb = exc.__traceback__
        while tb.tb_next is not None:
            tb = tb.tb_next
        name = os.path.realpath(tb.tb_frame.f_code.co_filename)
        top = os.path.realpath(root) + os.sep
        if not name.startswith(top) or name.startswith(_LIB):
            return False
        if set(name[len(top):].split(os.sep)) & set(_VENV):
            return False
        code, last = tb.tb_frame.f_code, tb.tb_lasti
        return any(i.offset == last and i.opname == "RAISE_VARARGS"
                   for i in dis.get_instructions(code))
    except BaseException:
        return False


def _env_link(exc, root=None, _type=type, _sub=issubclass):
    cls = _type(exc)
    if _sub(cls, OSError):
        return not _raised_in_checkout(exc, root)
    if _sub(cls, (MemoryError, RecursionError)):
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
    # root: the checkout (#797).
    # a call's exception info -> True when it, or a link of its cause/context chain (at most 64),
    # failed from the environment. Read through type() and the built-in descriptors, so a test's
    # exception class (properties, __eq__) runs no code here; an AssertionError is not walked;
    # a cycle ends the walk; an unreadable chain or one past the bound is an environment failure
    # (fail closed).
    try:
        exc = top = getattr(info, "value", None)
        if not _sub(_type(exc), _base):
            return False
        if isinstance(exc, OSError) and not _raised_in_checkout(exc, root):
            return True
        if _sub(_type(exc), AssertionError):
            return _denied(exc)
        seen = set()
        for _ in range(64):
            if _link(exc, root):
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
