"""Runs inside the sandbox: ``python -I -B probe_runner.py <checkout> <probe file> <out path>
<sys.path entries as JSON> <chain path> <nonce file>``. No pytest, conftest, ini or plugins.

The probe is plain Python run with two names bound: ``observe(key, value)`` and
``observe_raises(key, call)``. On a clean finish the observations are written as one JSON document
to <out path> (created by the runner beforehand) and the exit code is 0. Any exception, a
duplicate key or a value JSON cannot hold ends the run non-zero with no document, and the
exception chain is written to <chain path> (a file the runner created) as
``ORACLE_CHAIN <env> <module>.<QualName>`` lines (outermost first; <env> is 1 for an
environment failure, the classes of env_failure.py) closed by ``ORACLE_CHAIN_END <nonce>``. The
nonce is read into memory from <nonce file>, which the runner deletes before the probe starts
(it is in neither argv, ``sys.orig_argv`` nor the environment). A base run has no candidate
code, so the base classifier (base.py) trusts a chain file that ends with its nonce; stderr text is
never read for it, and a candidate run's chain is noise. The observations are serialized here by
code that only uses functions bound before the probe ran, at the moment ``observe`` is called."""

import builtins
import json
import json.encoder
import math
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True


ENV_TEXT = re.compile(
    rb"not found|permission denied|operation not permitted|cannot allocate memory", re.I
)
CHAIN_LIMIT = 64
MAX_DEPTH = 32
# Bound before any probe code runs: a probe that patches json, os or the builtins later changes
# nothing here.
os_write, os_close, os_exit, os_open = os.write, os.close, os._exit, os.open
quote = json.encoder.encode_basestring_ascii
int_repr, float_repr, is_finite = int.__repr__, float.__repr__, math.isfinite
dict_items, join, get_attr = dict.items, str.join, getattr
BUILTINS = dict(vars(builtins))


def env_link(exc):
    """True for the environment classes #808 names: resource limits, an OSError, or a child
    process that could not run (exit 126 or 127, a signal, or text saying so)."""
    if isinstance(exc, (MemoryError, RecursionError, OSError)):
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        parts = [p if isinstance(p, bytes) else str(p).encode() for p in (exc.stderr, exc.output)]
        code = exc.returncode
        bad_code = isinstance(code, int) and (code in (126, 127) or code < 0)
        return bad_code or bool(ENV_TEXT.search(b" ".join(parts)))
    return False


def chain_lines(exc):
    """One line per link of the cause/context chain (cycle-safe, at most CHAIN_LIMIT). A chain
    that cannot be read, or runs past the limit, counts as an environment failure."""
    lines, seen = [], set()
    try:
        while exc is not None and id(exc) not in seen:
            if len(lines) >= CHAIN_LIMIT:
                return ["ORACLE_CHAIN 1 <chain too long>"]
            seen.add(id(exc))
            name = re.sub(r"[^\w.<>]", "?", class_name(type(exc)))
            lines.append(f"ORACLE_CHAIN {int(env_link(exc))} {name}")
            exc = exc.__cause__ or exc.__context__
    except BaseException:  # noqa: BLE001 - unreadable chain: fail closed
        lines.append("ORACLE_CHAIN 1 <unreadable>")
    return lines


def fail(exc, chain, nonce):
    text = "\n".join(chain_lines(exc)) + "\nORACLE_CHAIN_END " + nonce + "\n"
    try:
        fd = os_open(chain, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        os_write(fd, text.encode())
        os_close(fd)
    finally:
        os_exit(1)


def dump(value, depth=0):
    """JSON text of an exact JSON value (None, bool, int, float, str, list, tuple, dict with str
    keys); anything else, a subclass included, is refused instead of being asked to describe
    itself."""
    if depth > MAX_DEPTH:
        raise ValueError("too deep")
    kind = type(value)
    if value is None:
        return "null"
    if kind is bool:
        return "true" if value else "false"
    if kind is int:
        return int_repr(value)
    if kind is float:
        if not is_finite(value):
            raise ValueError("not finite")
        return float_repr(value)
    if kind is str:
        return quote(value)
    if kind is list or kind is tuple:
        return "[" + join(",", [dump(item, depth + 1) for item in value]) + "]"
    if kind is dict:
        pairs = []
        for key, item in dict_items(value):
            if type(key) is not str:
                raise ValueError("object keys must be str")
            pairs.append(quote(key) + ":" + dump(item, depth + 1))
        return "{" + join(",", pairs) + "}"
    raise TypeError("not a JSON value")


def class_name(cls):
    """``module.QualName`` when that name resolves back to ``cls`` itself, else ``<unresolved>``."""
    try:
        module, qualname = cls.__module__, cls.__qualname__
        if type(module) is not str or type(qualname) is not str:
            return "<unresolved>"
        if module == "builtins":
            found = BUILTINS.get(qualname)
        else:
            found = sys.modules.get(module)
            for part in qualname.split("."):
                found = get_attr(found, part, None)
        return f"{module}.{qualname}" if found is cls else "<unresolved>"
    except BaseException:  # noqa: BLE001 - unreadable: unresolved
        return "<unresolved>"


def main(argv):
    checkout, probe, out, extra, chain, nonce_file = argv[1:7]
    with open(nonce_file, encoding="ascii") as handle:
        nonce = handle.read().strip()
    os.unlink(nonce_file)
    sys.argv[:] = [probe]
    try:
        run(checkout, probe, out, extra)
    except BaseException as error:  # noqa: BLE001 - every way out other than os._exit(0) fails
        fail(error, chain, nonce)


def run(checkout, probe, out, extra):
    sys.path[:0] = [checkout, *json.loads(extra)]
    values, raises = {}, {}

    def claim(key):
        if type(key) is not str or key in values or key in raises:
            raise ValueError("observation key must be a new string")

    def observe(key, value):
        claim(key)
        values[key] = dump(value)

    def observe_raises(key, call):
        claim(key)
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - recording the class is the point
            raises[key] = class_name(type(exc))
        else:
            raises[key] = "<no exception>"

    with open(probe, encoding="utf-8") as handle:
        source = handle.read()
    scope = {"__name__": "__probe__", "__file__": probe, "observe": observe}
    scope["observe_raises"] = observe_raises
    exec(compile(source, probe, "exec"), scope)  # noqa: S102 - the probe is the task's own text
    parts = [quote(k) + ":" + v for k, v in dict_items(values)]
    names = [quote(k) + ":" + quote(v) for k, v in dict_items(raises)]
    document = "{" + '"v":{' + join(",", parts) + '},"r":{' + join(",", names) + "}}"
    fd = os_open(out, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    os_write(fd, document.encode())
    os_close(fd)
    os_exit(0)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except BaseException:  # noqa: BLE001 - a bad command line: no chain, no document
        os_exit(1)
