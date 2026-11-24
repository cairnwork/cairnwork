"""Measure AF_UNIX socket reachability from inside a sandbox; never disclose paths."""

import errno
import json
import os
import socket
import stat
import sys
import threading
import time

# /usr and the library trees hold hundreds of thousands of files and no daemon sockets; CI runners
# stopped at the time budget inside them (230k entries in 60 s) before reaching /home or /var.
# After this prune the location class `usr-and-other-system` is effectively only /etc.
SKIP = {"/proc", "/sys", "/dev", "/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin"}
# Walked last: on the CI runners /opt holds ~240k files of toolchains (all of the time budget) and
# no daemon sockets, while the trees the witnesses use (home, /var/tmp) were never reached.
DEFER = {"/opt"}
PRIORITY_DONE = "priority-complete|-|-"  # everything except DEFER was walked before any stop
PRIORITY_ROOTS = ("~", "/var/tmp", "/tmp")  # trees the witnesses read; each must really be walked
MAX_ENTRIES = 5_000_000
SUN_PATH_LIMIT = 104  # smallest sun_path (macOS); Linux allows 108
_cwd_lost = False  # set when the working directory could not be restored
BUDGET_SECONDS = 80  # report says `truncated` when the walk stopped early
HANG_GRACE_SECONDS = 5  # a call still blocked this long after the budget is abandoned


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


def _priority_paths() -> set[str]:
    """The priority roots as walked paths: as written and with symlinks resolved."""
    paths: set[str] = set()
    for root in PRIORITY_ROOTS:
        root = os.path.normpath(os.path.expanduser(root))
        paths.update((root, os.path.realpath(root)))
    return paths


def _exists_or_unknown(path: str) -> bool:
    """False only when the path is certainly absent (ENOENT/ENOTDIR); a link counts as existing."""
    try:
        os.lstat(path)
    except OSError as exc:
        return exc.errno not in (errno.ENOENT, errno.ENOTDIR)
    return True


def _priority_complete(failed: list[str], entered: set[str]) -> bool:
    """True when every existing priority root was entered and nothing failed in or above one.

    `failed` holds the paths of directories that could not be listed and files whose lstat failed
    for any reason but "gone". A failure inside a priority root, or on a directory above one (the
    root may then be unreachable), withholds the mark; a failure elsewhere (a root-only directory
    under /etc, say) stays in the report but cannot hide the priority trees. A root that exists
    or cannot be told apart from existing, yet was never entered (a symlink is not followed),
    also withholds it.
    """
    roots = _priority_paths()
    for path in failed:
        base = path.rstrip("/")
        if any(
            path == r or path.startswith(r.rstrip("/") + "/") or r.startswith(base + "/")
            for r in roots
        ):
            return False
    for root in PRIORITY_ROOTS:
        root = os.path.normpath(os.path.expanduser(root))
        if _exists_or_unknown(root) and not {root, os.path.realpath(root)} & entered:
            return False
    return True


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
                _restore_cwd(old)
        if _cwd_lost:
            return "stream", "pathtoolong_other"
        return kind, f"pathtoolong_{result}"
    return _probe_short(path)


def _restore_cwd(old: int) -> None:
    """Return to the saved directory; on failure the walk must stop (see collect)."""
    global _cwd_lost
    try:
        os.fchdir(old)
    except OSError:
        _cwd_lost = True
        try:
            os.chdir("/")
        except OSError:
            pass
    finally:
        os.close(old)


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


def _stop(
    counts: dict[str, int], reason: str, entries: int, walked: dict[str, int]
) -> dict[str, int]:
    """Mark the walk as stopped early; partial counts stay, with the reason and entries seen."""
    counts["truncated|-|-"] = 1
    counts[f"stopped-by-{reason}|-|-"] = 1
    return _finish(counts, entries, walked)


def _finish(counts: dict[str, int], entries: int, walked: dict[str, int]) -> dict[str, int]:
    """Add entries seen overall and per location class (counts only), so a slow walk can be read."""
    counts["entries-seen|-|-"] = entries
    for loc, number in walked.items():
        counts[f"walked-{loc}|-|-"] = number
    return counts


def _walk(
    top: str,
    counts: dict[str, int],
    walked: dict[str, int],
    progress: dict[str, int],
    outcome: dict[str, object],
) -> None:
    """Walk on a worker thread; `outcome["stop"]` names why it stopped early, if it did."""

    def unlistable(exc: OSError) -> None:
        key = f"{location(str(exc.filename or top))}|-|unlistable"
        counts[key] = counts.get(key, 0) + 1
        failed.append(str(exc.filename or top))

    deadline = time.monotonic() + BUDGET_SECONDS
    priority, entered = _priority_paths(), set[str]()
    failed: list[str] = []
    outcome["failed"] = failed
    for root, dirs, files in os.walk(top, followlinks=False, onerror=unlistable):
        if time.monotonic() >= deadline:  # directories without files also count against the budget
            outcome["stop"] = "time"
            return
        if root == top:  # visit the deferred trees after every other tree
            kept = [d for d in dirs if os.path.join(root, d) not in SKIP]
            late = [d for d in kept if os.path.join(root, d) in DEFER]
            dirs[:] = [d for d in kept if d not in late] + late
        else:
            dirs[:] = [d for d in dirs if os.path.join(root, d) not in SKIP]
        if root in priority:
            entered.add(root)
        if root in DEFER and _priority_complete(failed, entered):
            counts[PRIORITY_DONE] = 1
        here = location(root)
        walked[here] = walked.get(here, 0) + len(files)
        for name in files:
            progress["entries"] += 1
            if progress["entries"] > MAX_ENTRIES or time.monotonic() >= deadline:
                outcome["stop"] = "entries" if progress["entries"] > MAX_ENTRIES else "time"
                return
            path = os.path.join(root, name)
            try:
                if not stat.S_ISSOCK(os.lstat(path).st_mode):
                    continue
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTDIR):  # a file that vanished is fine
                    key = f"{location(path)}|-|unstatable"
                    counts[key] = counts.get(key, 0) + 1
                    failed.append(path)
                continue
            loc = location(path)
            kind, result = _probe(path)
            key = f"{loc}|{kind}|{result}"
            if _cwd_lost:  # the working directory is no longer the one we started in
                outcome["stop"] = "cwd"
                return
            counts[key] = counts.get(key, 0) + 1
    outcome["entered"] = entered


def _run(
    top: str,
    counts: dict[str, int],
    walked: dict[str, int],
    progress: dict[str, int],
    outcome: dict[str, object],
) -> None:
    try:
        _walk(top, counts, walked, progress, outcome)
    except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
        outcome["error"] = exc


def collect(top: str = "/") -> dict[str, int]:
    """Walk visible files within fixed time and entry bounds, returning counts only.

    A directory that cannot be listed is counted as `<location>|-|unlistable`, so an empty report
    is not read as "no sockets". The walk runs on a worker thread and the caller waits at most
    BUDGET_SECONDS + HANG_GRACE_SECONDS: one filesystem call that never returns (a hung mount)
    cannot hold the report past that, which then says `stopped-by-hang` with the partial counts.
    After a hang the worker thread is still blocked in its call (a daemon thread, left behind);
    a caller other than main() must not rely on the process exiting by itself.
    """
    counts: dict[str, int] = {}
    walked: dict[str, int] = {}
    progress = {"entries": 0}
    outcome: dict[str, object] = {}
    worker = threading.Thread(
        target=_run, args=(top, counts, walked, progress, outcome), daemon=True
    )
    worker.start()
    worker.join(BUDGET_SECONDS + HANG_GRACE_SECONDS)
    if worker.is_alive():  # the worker is stuck in a call; report what it had, never wait for it
        return _stop(dict(counts), "hang", progress["entries"], dict(walked))
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    if outcome.get("stop"):
        return _stop(counts, str(outcome["stop"]), progress["entries"], walked)
    entered = outcome.get("entered")
    failed = outcome.get("failed")
    if (
        isinstance(entered, set)
        and isinstance(failed, list)
        and _priority_complete(failed, entered)
    ):
        counts[PRIORITY_DONE] = 1  # a full walk also completed the priority trees
    return _finish(counts, progress["entries"], walked)


def main() -> None:
    report = collect()
    output = json.dumps(report, sort_keys=True)
    print(output, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        _write_summary(summary, report)
    if "stopped-by-hang|-|-" in report:  # a worker is stuck in a call: do not wait for it at exit
        sys.stdout.flush()
        os._exit(0)


def _write_summary(summary: str, report: dict[str, int]) -> None:
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
