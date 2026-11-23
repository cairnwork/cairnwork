"""Runs inside the sandbox: ``python -I -B probe_runner.py <checkout> <probe file> <out path>
<sys.path entries as JSON>``. No pytest, conftest, ini or plugins.

The probe is plain Python run with two names bound: ``observe(key, value)`` and
``observe_raises(key, call)``. On a clean finish the observations are written as one JSON document
to <out path> (created by the runner beforehand) and the exit code is 0. Any exception, a
duplicate key or a value JSON cannot hold ends the run non-zero with no document.

Everything the runner does after the probe ran uses names bound before it ran: the observations are
serialized when ``observe`` is called, from exact JSON types only (a subclass is refused), so a
probe that patches ``json``, ``os`` or ``builtins`` changes nothing in the document. An exception
class is named only when the name leads back to that very class (a class that sets its own
``__module__`` to ``builtins`` is not ``builtins.KeyError``)."""

import builtins
import json
import json.encoder
import math
import os
import sys

sys.dont_write_bytecode = True

MAX_DEPTH = 32
os_write, os_close, os_exit, os_open = os.write, os.close, os._exit, os.open
quote = json.encoder.encode_basestring_ascii
int_repr, float_repr, is_finite = int.__repr__, float.__repr__, math.isfinite
dict_items, join, get_attr = dict.items, str.join, getattr
BUILTINS = dict(vars(builtins))


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
    checkout, probe, out, extra = argv[1:5]
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
    main(sys.argv)
