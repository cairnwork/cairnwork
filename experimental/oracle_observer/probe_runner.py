"""Runs inside the sandbox: ``python -I -B probe_runner.py <checkout> <probe file> <out path>
<sys.path entries as JSON>``. No pytest, conftest, ini or plugins.

The probe is plain Python run with two names bound: ``observe(key, value)`` and
``observe_raises(key, call)``. On a clean finish the observations are written as one JSON document
to <out path> (created by the runner beforehand) and the exit code is 0. Any exception, a
duplicate key or a value JSON cannot hold ends the run non-zero with no document."""

import json
import os
import sys

sys.dont_write_bytecode = True


def main(argv):
    checkout, probe, out, extra = argv[1:5]
    sys.path[:0] = [checkout, *json.loads(extra)]
    values, raises = {}, {}

    def claim(key):
        if not isinstance(key, str) or key in values or key in raises:
            raise ValueError("observation key must be a new string")

    def observe(key, value):
        claim(key)
        values[key] = value

    def observe_raises(key, call):
        claim(key)
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - recording the class is the point
            cls = type(exc)
            raises[key] = f"{cls.__module__}.{cls.__qualname__}"
        else:
            raises[key] = "<no exception>"

    with open(probe, encoding="utf-8") as handle:
        source = handle.read()
    scope = {"__name__": "__probe__", "__file__": probe, "observe": observe}
    scope["observe_raises"] = observe_raises
    exec(compile(source, probe, "exec"), scope)  # noqa: S102 - the probe is the task's own text
    document = json.dumps({"v": values, "r": raises}, allow_nan=False)
    fd = os.open(out, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(document)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
