"""Measure AF_UNIX socket reachability from inside a sandbox; never disclose paths."""

import errno
import json
import os
import socket
import stat
import time

SKIP = {"/proc", "/sys", "/dev"}
MAX_ENTRIES = 5_000_000
SUN_PATH_LIMIT = 104  # smallest sun_path (macOS); Linux allows 108
BUDGET_SECONDS = 60  # report says `truncated` when the walk stopped early


def location(path: str) -> str:
    """Map a filesystem path to its non-sensitive location class."""
    path = os.path.normpath(path)
    if any(path == root or path.startswith(root + "/") for root in ("/tmp", "/run", "/var/run")):
        return "private-trees"
    if path == "/var/tmp" or path.startswith("/var/tmp/"):
        return "var-tmp"
    if path == "/var/lib" or path.startswith("/var/lib/"):
        return "var-lib"
    home = os.path.expanduser("~")
    if path == "/home" or path.startswith("/home/") or path == home or path.startswith(home + "/"):
        return "home"
    for root in ("/srv", "/opt"):
        if path == root or path.startswith(root + "/"):
            return root[1:]
    if path.startswith(("/bin/", "/sbin/", "/lib/", "/lib64/", "/etc/", "/usr/")):
        return "usr-and-other-system"
    return "other"


def _probe(path: str) -> tuple[str, str]:
    """Return (socket type, result); the first type that connects wins, else the stream result.

    A path longer than sun_path is probed by its basename from its directory and the result is
    prefixed `pathtoolong_`, so it is not read as an ordinary failure.
    """
    if len(os.fsencode(path)) >= SUN_PATH_LIMIT:
        old = -1
        try:
            old = os.open(".", os.O_RDONLY)
            os.chdir(os.path.dirname(path))
            kind, result = _probe_short(os.path.basename(path))
        except OSError:
            return "stream", "pathtoolong_other"
        finally:
            if old >= 0:
                os.fchdir(old)
                os.close(old)
        return kind, f"pathtoolong_{result}"
    return _probe_short(path)


def _probe_short(path: str) -> tuple[str, str]:
    kinds = [("stream", socket.SOCK_STREAM), ("dgram", socket.SOCK_DGRAM)]
    if hasattr(socket, "SOCK_SEQPACKET"):
        kinds.append(("seqpacket", socket.SOCK_SEQPACKET))
    first = ""
    for label, kind in kinds:
        result = _connect(path, kind)
        if result == "connectable":
            return label, result
        first = first or result
    return "stream", first


def _connect(path: str, kind: int) -> str:
    """Attempt one nonblocking connection and close immediately.

    A successful connect is visible to the listener; that is inherent to measuring reachability.
    """
    try:
        probe = socket.socket(socket.AF_UNIX, kind)
    except OSError:
        return "unsupported"
    try:
        probe.setblocking(False)
        probe.connect(path)
        return "connectable"
    except OSError as exc:
        if exc.errno in (errno.EINPROGRESS, errno.EALREADY, errno.EISCONN):
            return "connectable"
        if exc.errno in (errno.ECONNREFUSED,):
            return "refused"
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "denied"
        return errno.errorcode.get(exc.errno or 0, "other")
    finally:
        probe.close()


def collect(top: str = "/") -> dict[str, int]:
    """Walk visible files within fixed time and entry bounds, returning counts only.

    A directory that cannot be listed is counted as `<location>|-|unlistable`, so an empty report
    is not read as "no sockets".
    """
    counts: dict[str, int] = {}

    def unlistable(exc: OSError) -> None:
        key = f"{location(str(exc.filename or top))}|-|unlistable"
        counts[key] = counts.get(key, 0) + 1

    deadline, entries = time.monotonic() + BUDGET_SECONDS, 0
    for root, dirs, files in os.walk(top, followlinks=False, onerror=unlistable):
        if time.monotonic() >= deadline:  # directories without files also count against the budget
            counts["truncated|-|-"] = 1
            return counts
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in SKIP]
        for name in files:
            entries += 1
            if entries > MAX_ENTRIES or time.monotonic() >= deadline:
                counts["truncated|-|-"] = 1
                return counts
            path = os.path.join(root, name)
            try:
                if not stat.S_ISSOCK(os.lstat(path).st_mode):
                    continue
            except OSError:
                continue
            loc = location(path)
            kind, result = _probe(path)
            key = f"{loc}|{kind}|{result}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> None:
    report = collect()
    output = json.dumps(report, sort_keys=True)
    print(output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    try:  # the sandbox filesystem is read-only: the JSON above is the report
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n| Location | Socket type | Result | Count |\n|---|---|---|---:|\n")
            for key, count in sorted(report.items()):
                loc, kind, result = key.split("|")
                handle.write(f"| {loc} | {kind} | {result} | {count} |\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
