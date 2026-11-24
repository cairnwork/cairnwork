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

The owner-mode hand-over details are documented in ``report_channel``.

When collection returns, every run also writes a collection record (the same fields plus ``stage:
"collected"``, no outcomes) to its own destination (#1671). Only the parent reads it, and only for
a quiet collect run (collect_stop.py): it ends that run's process group on seeing it. The child
holds no bit saying so, and the final record never carries a ``stage``.

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
from cairnwork.confined import MAX_REPORT_BYTES
from cairnwork.data_exec_hook import plugin_source
from cairnwork.env_failure import ENV_SOURCE
from cairnwork.report_guard_exposed import EXPOSED_SOURCE
from cairnwork.report_guard_parts import PARTS_SOURCE
from cairnwork.verdict_build import PLUGIN_KINDS as PLUGIN_KINDS
from cairnwork.verdict_build import build_verdict
from cairnwork.verdict_record import Verdict as Verdict

FDS_ENV = "CAIRNWORK_GUARD_FDS"
PATH_ENV = "CAIRNWORK_VERDICT_PATH"
NONCE_ENV = "CAIRNWORK_VERDICT_NONCE"
MODULE = "_cairnwork_report_guard"
COLLECTED_SUFFIX = ".collected"  # #1671: beside the account path's report: the collection record

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
_COLLECTED_SUFFIX = ".collected"  # report_guard.COLLECTED_SUFFIX (a test pins the two equal)
'''
    + PARTS_SOURCE
    + EXPOSED_SOURCE.replace("_MAX_REPORT_BYTES = 0", f"_MAX_REPORT_BYTES = {MAX_REPORT_BYTES}")
    + '''

def _take():
    """Read the hand-over once and scrub it from os.environ. Runs at import: pytest loads
    this module (-p) before any conftest or test module. Every run gets the same bytes (#1671):
    nothing here says whether the parent will end the run after collection."""
    spec = os.environ.pop(_FDS_ENV, None)
    path = os.environ.pop(_PATH_ENV, None)
    nonce = os.environ.pop(_NONCE_ENV, None)
    if spec:
        try:
            nonce_fd, report_fd, collected_fd = (int(part) for part in spec.split(","))
            chunks = []
            while True:
                data = os.read(nonce_fd, 4096)
                if not data:
                    break
                chunks.append(data)
            os.close(nonce_fd)
            return (None, report_fd, b"".join(chunks).decode(), collected_fd)
        except (OSError, ValueError):
            return None
    return (path, None, nonce, None) if path and nonce else None


_HANDOVER = _take()


'''
    + ENV_SOURCE
    + '''def _collected_status(session):
    """The exit status pytest itself would end with once collection returned (#1671): a run
    stopped here and a run that goes on to the tests agree on it."""
    if session.testsfailed:
        return 1 if session.config.option.continue_on_collection_errors else 2
    return 0 if session.items else 5


class _Reporter:
    # the hooks call their helpers and the json/os functions through default arguments bound at
    # import: rebinding a class or module attribute later does not change what is recorded

    def __init__(self, path, report_fd, nonce, collected_fd=None):
        self._path = path
        self._fd = report_fd
        self._nonce = nonce
        # #1671: where the collection record goes: its own descriptor (owner run) or a name
        # beside the report (account run); written in every run, read only for a quiet one
        self._cfd = collected_fd
        self._cpath = path + _COLLECTED_SUFFIX if path else None
        self._collected = []
        self._outcomes, self._skips = {}, {}
        self._sources = {}  # id(item) -> (item, node id, file) when collected
        self._files = []
        self._file_of = []  # per collected item: its index in _files, -1 if it has none
        self._renamed = []  # items whose node id or file changed after collection, or never seen
        self._collect_errors = []
        self._collect_skipped = []
        self._hints = {"collect_import_errors": [], "collect_missing_submodules": []}  # #796
        self._skipped_deps = []  # #813: per skipped collector, the module it could not import
        self._teardown_errors = []
        self._setup_failed = []  # #775: fixture raised before the body
        self._plugin_missing = {}  # #804: node -> the table plugin whose fixture was not found
        self._env_ok = {}  # #775: node -> every failed call raised an OSError so far
        self._denied = {}  # #962: node -> the denied path, while every failed call has one
        self._setup_denied = {}  # #962 step 2a: node -> [path, origin, line] of a setup denial
        self._finished = False  # #793: collection ran to its end (not stopped part way)
        self._uncollected_exposed = None

    _IO = (  # what the record is written with, bound at import (data_exec_hook wraps the first two)
        json.dumps, json.dump, os.ftruncate, os.pwrite, os.open, os.fdopen, os.chmod,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    )

    def _payload(self, exitstatus, testsfailed, stage=None):
        """The record as a dict; ``stage`` (#1671) marks the collection record, which carries
        no outcomes yet. The final record has no ``stage`` key at all."""
        kind = {n: "env" for n, ok in self._env_ok.items() if ok}
        kind.update(dict.fromkeys(self._setup_failed, "setup"))
        kind.update({n: "plugin:" + m for n, m in self._plugin_missing.items()})
        payload = {"nonce": self._nonce, "fail_kind": kind,
                   "denied": {n: p for n, p in self._denied.items() if p and n in kind},
                   "setup_denied": {n: d for n, d in self._setup_denied.items()
                                    if d and kind.get(n) == "setup"},
                   "collected": self._collected, "outcomes": self._outcomes,
                   "files": self._files, "file_of": self._file_of,
                   "renamed": list(dict.fromkeys(self._renamed)),
                   "collect_errors": self._collect_errors,
                   "collect_skipped": self._collect_skipped,
                   **self._hints,  # #796: collect_import_errors, collect_missing_submodules
                   "collect_skipped_deps": self._skipped_deps,
                   "teardown_errors": self._teardown_errors,
                   "collection_finished": self._finished, "skips": self._skips,
                   # pytest's own status: the parent requires it and the process's to agree
                   "exitstatus": _exit_status(exitstatus),
                   "testsfailed": _tests_failed(testsfailed)}
        if stage:
            payload["stage"] = stage
        if self._uncollected_exposed is not None:
            payload["uncollected_exposed"] = self._uncollected_exposed
        return payload

    def _write(self, payload, fd, path, _io):
        """Write ``payload`` to the anonymous report ``fd`` or, without one, to a new file."""
        dumps, _dump, ftruncate, pwrite, open_, fdopen, chmod, flags = _io
        data = dumps(payload).encode()  # the optional fact never makes the record too big
        if len(data) > _MAX_REPORT_BYTES and payload.pop("uncollected_exposed", None) is not None:
            data = dumps(payload).encode()
        if fd is not None:
            ftruncate(fd, 0)
            written = 0
            while written < len(data):
                step = pwrite(fd, data[written:], written)
                if step <= 0:  # no progress: leave a short record, which the parent refuses
                    break
                written += step
        elif path is not None:
            with fdopen(open_(path, flags, 0o600), "wb") as handle:
                handle.write(data)
            chmod(path, 0o644)  # the owner reads it (a different user under the account)

    def pytest_collectreport(self, report): _collect(self, report)

    def pytest_exception_interact(self, node, call, report, _hints=_collect_hints):
        _hints(node, call, report, self._hints)

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
    def pytest_collection(self, session, _status=_collected_status, _payload=_payload,
                          _write=_write, _io=_IO):
        # #793: pytest calls collection_finish even when collection is interrupted; only a
        # collection that returned (no exception) ran to its end
        result = yield
        self._finished = True
        try:  # #1671: the collection record, in every run; a failed write leaves none
            record = _payload(self, _status(session), session.testsfailed, "collected")
            _write(self, record, self._cfd, self._cpath, _io)
        except OSError:
            pass
        return result

    def pytest_collection_finish(self, session, _original=_original):
        self._uncollected_exposed = _exposed_or_none(session)
        index = {}
        self._item_marks = _marker_items(session)
        for item in session.items:
            nodeid, source = _original(self, item, item.nodeid)
            self._collected.append(nodeid)
            self._file_of.append(index.setdefault(source, len(index)) if source else -1)
        self._files = list(index)

    def pytest_runtest_logreport(self, report): _record_skip(self, report)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call, _original=_original, _keep_worst=_keep_worst,
                                  _fixtures=_PLUGIN_FIXTURES, _env=_env_exc,
                                  _origin=_denied_origin, _report=TestReport, _type=type):
        report = yield  # outermost: the report as every other hook left it
        if _type(report) is not _report:
            return report
        outcome, when, rid = report.outcome, report.when, report.nodeid
        if not all(_type(v) is str for v in (outcome, when, rid)):
            self._renamed.append("")  # #1374: a hostile field fails closed; no method of it runs
            return report
        nodeid, _ = _original(self, item, rid)
        if when == "call":  # #570: one per sub-test, then the test's own
            _keep_worst(self, nodeid, outcome)
            if outcome == "failed":  # #775: an OSError says "environment", not a test failure
                info = call.excinfo
                oserr = info is not None and _env(info, item.config.rootpath)
                self._env_ok[nodeid] = self._env_ok.get(nodeid, True) and oserr
                self._denied[nodeid] = self._denied.get(nodeid, True) and _denied_path(info)
        elif when == "setup" and outcome in ("skipped", "failed"):
            _keep_worst(self, nodeid, outcome)
            if outcome == "failed":  # a skip in a fixture is not an error
                self._setup_failed.append(nodeid)
                self._setup_denied[nodeid] = _origin(call.excinfo, item.config.rootpath)
                module = _fixture_plugin(_fixtures, report.longrepr)
                if module:
                    self._plugin_missing[nodeid] = module
        elif when == "teardown" and outcome == "failed":  # #478: a fixture raised after yield
            self._outcomes[nodeid] = "failed"
            self._teardown_errors.append(nodeid)
        return report

    def pytest_sessionfinish(self, session, exitstatus, _io=_IO, _payload=_payload, _write=_write):
        _write(self, _payload(self, exitstatus, session.testsfailed), self._fd, self._path, _io)


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


def read_verdict(path: Path, nonce: str, collected: bool = False) -> Verdict:
    """Read the plugin's record from a path (the verifier-account hand-over). See
    ``parse_verdict`` for what makes ``ran`` true and for ``collected``."""
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
    return parse_verdict(raw, nonce, collected)


def read_verdict_fd(fd: int, nonce: str, collected: bool = False) -> Verdict:
    """Read the record from the parent's own descriptor of the anonymous report file."""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return Verdict(False, {})
        raw = os.pread(fd, MAX_REPORT_BYTES + 1, 0)
    except OSError:
        return Verdict(False, {})
    return parse_verdict(raw, nonce, collected)


def parse_verdict(raw: bytes, nonce: str, collected: bool = False) -> Verdict:
    """``ran`` is true only when sessionfinish wrote the record, it matches the nonce, at least
    one test was collected, every collected test produced a result, all results are ``passed``,
    pytest's own exit status and failure count are 0, no collector failed or was skipped, no
    teardown failed and no item's node id or file changed after collection (``renamed``). A
    record without that exit status and count (exact ints) is not a record. Outcomes,
    ``recorded`` and the collector and teardown lists are returned whenever the nonce matches
    (even when not all passed) so base_comparison can see a test that failed. A record over
    MAX_REPORT_BYTES (a suite too large for one report, #184) is ``too_big`` so the caller can
    say why nothing was read. ``collected`` (#1671) reads the collection record instead: the one
    that carries ``stage: "collected"`` and no outcome. Each read takes only its own kind, so a
    collection record is never taken for the final one (a run killed after collection has no
    final record) and the reverse."""
    if len(raw) > MAX_REPORT_BYTES:
        return Verdict(False, {}, too_big=True)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return Verdict(False, {})
    if not isinstance(data, dict) or data.get("nonce") != nonce:
        return Verdict(False, {})
    if collected:
        if data.get("stage") != "collected" or data.get("outcomes") != {}:
            return Verdict(False, {})
    elif "stage" in data:  # a collection record is never the final one
        return Verdict(False, {})
    return build_verdict(data)
