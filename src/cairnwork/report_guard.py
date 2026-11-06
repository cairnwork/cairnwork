"""Trusted verdict for a verifier run (preserved behavior #14; b063 report-forgery research).

The pass/fail comes from a pytest plugin that records what pytest itself collected and how
each test ended, written once at ``sessionfinish`` — not from a JUnit file the candidate can
write. verifiers.py used to pass ``--junitxml=<path>`` in argv and read that file, but the
candidate's own code runs in the same process and could read the path from ``sys.argv``, write
a passing report and ``os._exit(0)`` before the real tests ran. The parent now accepts a run
only when our record is present (so an exit before ``sessionfinish`` fails), it names at least
one collected test, every collected test has a result, all of them passed, no collector was
skipped or failed, and every item kept the node id and file it had when pytest collected it
(recorded in ``pytest_itemcollected``, before ``pytest_collection_modifyitems``; an item renamed,
moved or added later fails the run). Tests and outcomes are keyed by that collected node id, and
the caller also requires each target it named to own a test that ran (``Verdict.not_run``: the
test's collected file is the target's file and its collected node id is under it): a module
skipped at collection (``importorskip``, a module-level skip) yields no test at all, so it counts
as skipped, not as passed.

The plugin is a small standalone module (``_PLUGIN_SOURCE``) materialised into a per-run
directory by ``install_plugin`` and loaded with ``-p``. It carries no cairnwork import, so it
loads even when cairnwork is not importable from inside the sandbox (an editable checkout under
a 0700 home the verifier account cannot traverse); that directory is read-only to the sandbox,
so the candidate cannot replace it. The plugin file starts with the bytecode hook
(bytecode_hook.py), installed and probed before anything else in it runs.

What this does and does not do. It removes the old trivial attack (read ``--junitxml`` from
argv, write a passing report) and makes the verdict reflect what pytest itself collected and
ran. It defeats a candidate that only writes a forged JUnit (now ignored) or only calls
``os._exit`` without writing a record. It does NOT stop a candidate that reads the nonce and
path from the environment, writes a matching record and exits — reading the nonce from the
environment is no harder than reading the old argv path, so this is partial mitigation, not a
boundary.

Raising the bar (this module, ``report_channel`` and ``tamper_scan``): when the verifier runs as
the owner (no verifier account) the nonce and the report destination no longer sit in the
environment. The parent hands the child two inherited file descriptors (``pass_fds``): a pipe
holding the nonce and an anonymous (already unlinked) file for the record; only their numbers
travel in ``CAIRNWORK_GUARD_FDS``. The plugin reads the nonce, closes that pipe and removes every
guard variable from ``os.environ`` when its module is imported, which is before any conftest or
test module (interpreter start-up hooks such as ``sitecustomize.py`` and ``.pth`` files run
earlier; they are protected verifier inputs a candidate cannot add). The parent reads the record
from its own descriptor, so there is no path to guess, replace or plant. Candidate patches are
also scanned for the APIs such a forgery needs (tamper_scan.py, reason ``VERIFIER_TAMPER_API``).
Both are a higher hurdle, not a boundary: a process that shares the interpreter can still scan its
open descriptors or build the API names from strings. Under a verifier account sudo closes
descriptors above 2, so that path keeps the environment hand-over (path and nonce); the plugin
removes it from ``os.environ`` once read, but the process's original environment stays readable
(``/proc/self/environ`` on Linux, ``KERN_PROCARGS2`` for the same uid on macOS), so on that path
the nonce is still within reach of the code under test.

The record also carries pytest's own exit status and failure count (#276): a run passes only
when both are 0 and the process exits with status 0 too, so a process whose exit status
disagrees with what pytest recorded fails. The functions the hooks call on the record path
(``json.dumps``, the ``os`` functions that write the record, the reporter's own helpers) are
bound when the plugin is imported, so rebinding those module or class attributes later does not
change the record. Code that reaches the reporter instance, pytest's report objects or the
encoder behind ``json.dumps`` can still change what is recorded: there the exit-status agreement
and tamper_scan are hurdles, not a boundary, and a pytest verdict stays ``same_process_only``
(protection.py).

A real boundary needs the verdict produced outside the candidate's process (a subprocess per
test, or an external observer), tracked as Tier-3 in the report-forgery research
(knowledge nexus/research/2026-10-20-forgeable-test-report.md).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cairnwork.bytecode_hook import HOOK_SOURCE
from cairnwork.collect_import import import_errors
from cairnwork.confined import MAX_REPORT_BYTES
from cairnwork.data_exec_hook import plugin_source, refused
from cairnwork.pytest_config import PLUGINS
from cairnwork.report_guard_parts import PARTS_SOURCE
from cairnwork.skip_hint import module_names as _module_names
from cairnwork.verdict_record import Verdict as Verdict
from cairnwork.verdict_record import _sources, denied_paths, well_formed

FDS_ENV = "CAIRNWORK_GUARD_FDS"
PATH_ENV = "CAIRNWORK_VERDICT_PATH"
NONCE_ENV = "CAIRNWORK_VERDICT_NONCE"
MODULE = "_cairnwork_report_guard"
PLUGIN_KINDS = frozenset(f"plugin:{module}" for _, module, _ in PLUGINS)

_PLUGIN_SOURCE = (
    '''\
"""Materialised by cairnwork.report_guard into each verifier run; edit it there."""

import errno
import json
import os
import socket

import pytest

_FDS_ENV = "CAIRNWORK_GUARD_FDS"
_PATH_ENV = "CAIRNWORK_VERDICT_PATH"
_NONCE_ENV = "CAIRNWORK_VERDICT_NONCE"
'''
    + PARTS_SOURCE
    + '''

def _take():
    """Read the hand-over once and scrub it from os.environ. Runs at import: pytest loads
    this module (-p) before any conftest or test module."""
    spec = os.environ.pop(_FDS_ENV, None)
    path = os.environ.pop(_PATH_ENV, None)
    nonce = os.environ.pop(_NONCE_ENV, None)
    if spec:
        try:
            nonce_fd, report_fd = (int(part) for part in spec.split(","))
            chunks = []
            while True:
                data = os.read(nonce_fd, 4096)
                if not data:
                    break
                chunks.append(data)
            os.close(nonce_fd)
            return None, report_fd, b"".join(chunks).decode()
        except (OSError, ValueError):
            return None
    return (path, None, nonce) if path and nonce else None


_HANDOVER = _take()


def _denied_inside(exc, _codes=(errno.EACCES, errno.EPERM, errno.EROFS, errno.ENETUNREACH),
                   _lookup=socket.gaierror):
    """#854: a denial-shaped OSError wrapped in a failure (group member, cause, unsuppressed
    context), or #932 a failed host-name lookup (a closed network); past the bound, yes (fail
    closed). Other wrapped OSErrors stay plain failures."""
    todo, seen = [exc], set()
    while todo:
        if not isinstance(cur := todo.pop(), BaseException) or id(cur) in seen:
            continue
        if len(seen) >= 64:  # the bound: a 65th distinct exception, not an empty cause slot
            return True
        seen.add(id(cur))
        if cur is not exc and (isinstance(cur, _lookup) or isinstance(cur, OSError)
                               and cur.errno in _codes):  # #932: a lookup's errno is EAI_*
            return True
        members = getattr(cur, "exceptions", None)  # BaseExceptionGroup, or the backport's
        todo.extend(members if isinstance(members, tuple) else ())
        todo.append(cur.__cause__)
        if not cur.__suppress_context__:
            todo.append(cur.__context__)
    return False


class _Reporter:
    # the hooks call their helpers and the json/os functions through default arguments bound at
    # import: rebinding a class or module attribute later does not change what is recorded

    def __init__(self, path, report_fd, nonce):
        self._path = path
        self._fd = report_fd
        self._nonce = nonce
        self._collected = []
        self._outcomes = {}
        self._sources = {}  # id(item) -> (item, node id, file) when collected
        self._files = []
        self._file_of = []  # per collected item: its index in _files, -1 if it has none
        self._renamed = []  # items whose node id or file changed after collection, or never seen
        self._collect_errors = []
        self._collect_skipped = []
        self._import_errors = []  # #796: collectors that failed on "cannot import name"
        self._skipped_deps = []  # #813: per skipped collector, the module it could not import
        self._teardown_errors = []
        self._setup_failed = []  # #775: fixture raised before the body
        self._plugin_missing = {}  # #804: node -> the table plugin whose fixture was not found
        self._env_ok = {}  # #775: node -> every failed call raised an OSError so far
        self._denied = {}  # #962: node -> the denied path, while every failed call has one
        self._finished = False  # #793: collection ran to its end (not stopped part way)

    def pytest_collectreport(self, report):
        if report.failed:
            self._collect_errors.append(report.nodeid)
        elif report.skipped:  # a module or directory skipped whole while collecting
            self._collect_skipped.append(report.nodeid)
            self._skipped_deps.append(_missing(report))

    def pytest_exception_interact(self, node, call, report, _record=_import_error):
        self._import_errors += filter(None, [_record(node, call, report)])

    def _original(self, item, nodeid, _source=_source):
        """The node id ``item`` had when collected; an item that was not seen collected, or whose
        node id or file has changed since, is recorded as renamed (the run cannot pass)."""
        seen = self._sources.get(id(item))
        if not seen or seen[0] is not item:
            self._renamed.append(nodeid)
            return nodeid, ""
        if nodeid != seen[1] or _source(item) != seen[2]:
            self._renamed.append(nodeid)
        return seen[1], seen[2]

    def _keep_worst(self, nodeid, outcome):
        """A node's outcome never improves (failed > skipped > passed): a later report for the
        same node (the parent's after its sub-tests', a duplicate run) cannot undo a worse one."""
        rank = {"passed": 0, "skipped": 1}
        if rank.get(outcome, 2) >= rank.get(self._outcomes.get(nodeid, "passed"), 2):
            self._outcomes[nodeid] = outcome

    @pytest.hookimpl(tryfirst=True)
    def pytest_itemcollected(self, item, _source=_source):
        self._sources[id(item)] = (item, item.nodeid, _source(item))

    @pytest.hookimpl(wrapper=True)
    def pytest_collection(self, session):
        # #793: pytest calls collection_finish even when collection is interrupted; only a
        # collection that returned (no exception) ran to its end
        result = yield
        self._finished = True
        return result

    def pytest_collection_finish(self, session, _original=_original):
        index = {}
        for item in session.items:
            nodeid, source = _original(self, item, item.nodeid)
            self._collected.append(nodeid)
            self._file_of.append(index.setdefault(source, len(index)) if source else -1)
        self._files = list(index)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call, _original=_original, _keep_worst=_keep_worst,
                                  _fixtures=_PLUGIN_FIXTURES, _denied_inside=_denied_inside):
        report = yield  # outermost: the report as every other hook left it
        nodeid, _ = _original(self, item, report.nodeid)
        if report.when == "call":  # #570: one per sub-test, then the test's own
            _keep_worst(self, nodeid, report.outcome)
            if report.failed:  # #775: an OSError says "environment", never a plain test failure
                info = call.excinfo
                oserr = info is not None and (info.errisinstance(OSError)
                                              or _denied_inside(info.value))
                self._env_ok[nodeid] = self._env_ok.get(nodeid, True) and oserr
                self._denied[nodeid] = self._denied.get(nodeid, True) and _denied_path(info)
        elif report.when == "setup" and report.outcome in ("skipped", "failed"):
            _keep_worst(self, nodeid, report.outcome)
            if report.failed:  # a skip in a fixture is not an error
                self._setup_failed.append(nodeid)
                module = _fixtures.get(getattr(report.longrepr, "argname", None))
                if module:
                    self._plugin_missing[nodeid] = module
        elif report.when == "teardown" and report.failed:  # #478: a fixture raised after yield
            self._outcomes[nodeid] = "failed"
            self._teardown_errors.append(nodeid)
        return report

    def pytest_sessionfinish(self, session, exitstatus, _io=(
        json.dumps, json.dump, os.ftruncate, os.pwrite, os.open, os.fdopen, os.chmod,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    )):
        dumps, dump, ftruncate, pwrite, open_, fdopen, chmod, flags = _io
        kind = {n: "env" for n, ok in self._env_ok.items() if ok}
        kind.update(dict.fromkeys(self._setup_failed, "setup"))
        kind.update({n: "plugin:" + m for n, m in self._plugin_missing.items()})
        payload = {"nonce": self._nonce, "fail_kind": kind,
                   "denied": {n: p for n, p in self._denied.items() if p and n in kind},
                   "collected": self._collected, "outcomes": self._outcomes,
                   "files": self._files, "file_of": self._file_of,
                   "renamed": list(dict.fromkeys(self._renamed)),
                   "collect_errors": self._collect_errors,
                   "collect_skipped": self._collect_skipped,
                   "collect_import_errors": self._import_errors,
                   "collect_skipped_deps": self._skipped_deps,
                   "teardown_errors": self._teardown_errors,
                   "collection_finished": self._finished,
                   # pytest's own status: the parent requires it and the process's to agree
                   "exitstatus": int(exitstatus), "testsfailed": session.testsfailed}
        if self._fd is not None:
            data = dumps(payload).encode()
            ftruncate(self._fd, 0)
            written = 0
            while written < len(data):
                step = pwrite(self._fd, data[written:], written)
                if step <= 0:  # no progress: leave a short record, which the parent refuses
                    break
                written += step
            return
        fd = open_(self._path, flags, 0o600)
        with fdopen(fd, "w") as handle:
            dump(payload, handle)
        chmod(self._path, 0o644)  # the owner reads it (a different user under the account)


def pytest_configure(config):
    global _HANDOVER
    if _HANDOVER:
        config.pluginmanager.register(_Reporter(*_HANDOVER), "cairnwork_report_guard")
        _HANDOVER = None
'''
)


def install_plugin(directory: Path, data_paths=()) -> None:
    """Write the standalone plugin into directory (which must be on the run's PYTHONPATH and a
    sandbox read root, and must not be writable by the candidate). The bytecode hook comes first
    (bytecode_hook.py): installed and probed before the plugin reads its hand-over, so a run
    whose hook is not live has no record and fails. ``data_paths`` (#850) adds the data-exec
    hook at the end (data_exec_hook.py); none adds nothing."""
    path = directory / f"{MODULE}.py"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w") as handle:
        handle.write(HOOK_SOURCE + _PLUGIN_SOURCE + plugin_source(data_paths))
    os.chmod(path, 0o640)  # not the umask-masked open() mode: the verifier account reads by group


def read_verdict(path: Path, nonce: str) -> Verdict:
    """Read the plugin's record from a path (the verifier-account hand-over). See
    ``parse_verdict`` for what makes ``ran`` true."""
    try:
        # a candidate controls this path in run_dir: no symlink, and O_NONBLOCK + a regular-file
        # check so a FIFO planted in its place cannot hang the parent open().
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return Verdict(False, {})
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)  # cap the read
    except OSError:
        return Verdict(False, {})
    return parse_verdict(raw, nonce)


def read_verdict_fd(fd: int, nonce: str) -> Verdict:
    """Read the record from the parent's own descriptor of the anonymous report file."""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return Verdict(False, {})
        raw = os.pread(fd, MAX_REPORT_BYTES + 1, 0)
    except OSError:
        return Verdict(False, {})
    return parse_verdict(raw, nonce)


def parse_verdict(raw: bytes, nonce: str) -> Verdict:
    """``ran`` is true only when sessionfinish wrote the record, it matches the nonce, at least
    one test was collected, every collected test produced a result, all results are ``passed``,
    pytest's own exit status and failure count are 0, no collector failed or was skipped, no
    teardown failed and no item's node id or file changed after collection (``renamed``). A
    record without that exit status and count (exact ints) is not a record. Outcomes,
    ``recorded`` and the collector and teardown lists are returned whenever the nonce matches
    (even when not all passed) so base_comparison can see a test that failed. A record over
    MAX_REPORT_BYTES (a suite too large for one report, #184) is ``too_big`` so the caller can
    say why nothing was read."""
    if len(raw) > MAX_REPORT_BYTES:
        return Verdict(False, {}, too_big=True)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return Verdict(False, {})
    if not isinstance(data, dict) or data.get("nonce") != nonce:
        return Verdict(False, {})
    collected = data.get("collected")
    outcomes = data.get("outcomes")
    # fail closed on any malformed shape (a candidate controls the file): node ids and outcomes
    # must be strings, else set()/membership/str compares could misbehave or crash the parent.
    if not isinstance(collected, list) or not all(isinstance(node, str) for node in collected):
        return Verdict(False, {})
    if not isinstance(outcomes, dict) or not all(
        isinstance(node, str) and isinstance(result, str) for node, result in outcomes.items()
    ):
        return Verdict(False, {})
    shape, data_refused = well_formed(data, collected, outcomes, PLUGIN_KINDS), refused(data)
    if shape is None or data_refused is None:
        return Verdict(False, {})
    errors, skipped, renamed, teardown, files, kind, file_of, status = shape
    ran = (
        not data_refused  # #850: a declared data file was executed, refused or not
        and bool(collected)
        and not errors
        and not skipped
        and not renamed
        and not teardown
        and status == (0, 0)
        and set(outcomes) == set(collected)
        and all(outcome == "passed" for outcome in outcomes.values())
    )
    return Verdict(
        ran,
        outcomes,
        recorded=True,
        collect_errors=tuple(errors),
        collect_skipped=tuple(skipped),
        skipped_deps=_module_names(data),
        import_errors=import_errors(data),
        renamed=tuple(renamed),
        teardown_errors=tuple(teardown),
        fail_kind=kind,
        denied=denied_paths(data, kind),
        paths=_sources(collected, files, file_of or ()),
        exitstatus=status[0],
        testsfailed=status[1],
        collected=tuple(collected),
        collection_finished=data.get("collection_finished") is True,
        data_refused=data_refused,
    )
