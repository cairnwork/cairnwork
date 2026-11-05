"""Rules of tamper_scan.py for a pytest plugin module the candidate can change (#821, L1).

A protected conftest can load a library file as a plugin (``pytest_plugins = ("myplug",)``), so
pytest hooks, pytest and pluggy internals and the ways to make a function the scan never read
matter in every candidate ``.py``, not only in test code. This module holds the patterns and the
two checks that read whole files; tamper_scan.py wires them in. A raised bar, not a boundary:
pytest's verdict is made in the process that runs the candidate (``same_process_only``), and a
regular expression over single lines misses indirect forms (a name bound by assignment, a string
run through ``exec``/``eval``, a helper in another file). Refused with ``VERIFIER_TAMPER_API``:

- ``pytest_internals`` (extended): ``_pytest``, ``pluginmanager``, the hook execution entry
  points, ``.hook.``/``ihook``, call monitoring and tracing, pluggy and its hook classes.
- ``pytest_option_write``: an assignment to ``config.option.<name>`` or a ``setattr``/``vars``
  write on it.
- ``code_object_forge``: building a function from a code object, rewriting ``__code__``,
  ``__globals__`` or ``co_filename``, the builtin ``compile``, and the wrappers ``source_to_code``
  and ``codeop``.
- ``dynamic_loader``: loading source from a path (``SourceFileLoader``, ``runpy``).
- ``sourceless_binary``: a changed ``.pyc``, ``.pyo``, ``.so``, ``.pyd``, ``.dylib``, ``.zip``,
  ``.egg``, ``.whl`` or ``.pyz`` file, which no line scan can read.
- ``hook_file_touched``: a changed ``.py`` file that binds a known pytest or pluggy hook name, at
  any depth, or holds a string equal to one (``globals()["pytest_x"] = f``), in its base or its
  head version (an unchanged line may then change what runs). Every identifier field is read
  (parameters, keywords, ``match`` captures, type parameters, ``global`` names), bytes literals
  too. The file is parsed by the interpreter that will import it (the verifier's, one batched
  subprocess, tamper_scan_walk.py), so its grammar and its NFKC identifier rules are the
  importer's; there is no text fallback. Only a plain ``SyntaxError`` (not an encoding one) makes
  a file "unparseable by the verifier": no finding, a note. Anything else (``RecursionError``,
  ``MemoryError``, a bad cookie or codec, a crash, a timeout, malformed output) is
  ``scan_budget``, because those depend on the importing process's state. A codec registered at
  run time is covered only by the ``source_encoding`` rule. Accepted false block: a task that
  fixes a pytest plugin project itself. Not refused: ``pytest_my_helper``, or a log text that
  merely holds a name.
- Not followed: a name put together from string pieces (``getattr(cfg, "plugin" + "manager")``),
  reversed, ``replace``d, or used through an alias. A scan for those needs its own bounded design
  (STRING-BUILT-v1, not part of this change). A refused name handed whole to a dynamic lookup is
  not read either; a library file that holds an internals name in an ordinary string is an
  accepted false block. Hook ownership is judged at run time (PR-2) by
  which file holds the function, however its name was made.

Not scanned for (measured too common in honest code, DESIGN of #821): ``exec(`` and ``eval(``.
A data string read at run time and passed to ``exec`` is not followed (an unparseable ``.py`` file
as little as a ``.txt`` one).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from cairnwork.gitcmd import OID, GitCommandError, run_git_bytes
from cairnwork.tamper_scan_budget import ScanBudget

_ASSIGN = r"(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"
PYTEST_INTERNALS = (
    r"\b_pytest\b|\bpluginmanager\b|\b_inner_hookexec\b|\b_hookexec\b|\.\s*hook\s*\.|\bihook\b"
    r"|\badd_hookcall_monitoring\b|\benable_tracing\b|\bpluggy\b|\b_?HookCaller\b|\bHookImpl\b"
)
OPTION_WRITE = (
    rf"\boption\s*\.\s*\w+\s*{_ASSIGN}|\b(?:set|del)attr\s*\(\s*[\w.]*\boption\b"
    r"|\bvars\s*\(\s*[\w.]*\boption\s*\)|\boption\s*\.\s*__dict__"
)
CODE_FORGE = (
    r"\bFunctionType\s*\(|\bCodeType\s*\(|\.\s*replace\s*\(\s*co_|\bco_filename\s*=(?!=)"
    rf"|\b__code__\s*{_ASSIGN}|\b__globals__\s*(?:\[[^\]]*\]\s*)?{_ASSIGN}"
    r"|\b__globals__\s*\.\s*(?:update|setdefault|pop|clear)\s*\("
    r"|(?<![\w.])(?<!def\s)compile\s*\(|\bbuiltins\s*\.\s*compile\b"
    r"|=\s*compile\s*(?:[;#,)]|$)"  # an alias of the builtin
    r"|\bsource_to_code\b|\bcodeop\b"  # stdlib wrappers that build a code object
)
_FORGE_TOKENS = re.compile(r"""\bsetattr\s*\(|['"]__(?:code|globals)__['"]|[#;]""")


def setattr_forge(line: str) -> bool:
    """``setattr(`` then ``'__code__'``/``'__globals__'`` with no ``#`` or ``;`` between, one pass.

    The same lines as the old ``[^#;]*`` pattern between them, without its quadratic cost.
    """
    seen = False
    for match in _FORGE_TOKENS.finditer(line):
        token = match.group()
        if token in "#;":
            seen = False
        elif token.startswith("setattr"):
            seen = True
        elif seen:
            return True
    return False


DYNAMIC_LOADER = r"\b(?:SourceFileLoader|spec_from_file_location|run_path|run_module)\b"

HOOK_NAMES = frozenset(  # _pytest.hookspec of pytest 9.1.1 (pluggy 1.7.0), plus pytest_plugins
    """pytest_addhooks pytest_addoption pytest_assertion_pass pytest_assertrepr_compare
    pytest_cmdline_main pytest_cmdline_parse pytest_collect_directory pytest_collect_file
    pytest_collection pytest_collection_finish pytest_collection_modifyitems pytest_collectreport
    pytest_collectstart pytest_configure pytest_deselected pytest_enter_pdb
    pytest_exception_interact pytest_fixture_post_finalizer pytest_fixture_setup
    pytest_generate_tests pytest_ignore_collect pytest_internalerror pytest_itemcollected
    pytest_keyboard_interrupt pytest_leave_pdb pytest_load_initial_conftests
    pytest_make_collect_report pytest_make_parametrize_id pytest_markeval_namespace
    pytest_plugin_registered pytest_pycollect_makeitem pytest_pycollect_makemodule
    pytest_pyfunc_call pytest_report_collectionfinish pytest_report_from_serializable
    pytest_report_header pytest_report_teststatus pytest_report_to_serializable
    pytest_runtest_call pytest_runtest_logfinish pytest_runtest_logreport pytest_runtest_logstart
    pytest_runtest_makereport pytest_runtest_protocol pytest_runtest_setup
    pytest_runtest_teardown pytest_runtestloop pytest_sessionfinish pytest_sessionstart
    pytest_terminal_summary pytest_unconfigure pytest_warning_recorded pytest_plugins""".split()
)
SOURCELESS = (".pyc", ".pyo", ".so", ".pyd", ".dylib", ".zip", ".egg", ".whl", ".pyz")

WALKER = Path(__file__).with_name("tamper_scan_walk.py")  # run as a program, never imported here
MAX_OUTPUT = 1 << 20  # bytes of walker output read; more is not a listing
_KINDS = ("ok", "syntax", "fail")
log = logging.getLogger(__name__)


def _frame(blobs: list[bytes]) -> bytes:
    parts = [len(blobs).to_bytes(4, "big")]
    for blob in blobs:
        parts += [len(blob).to_bytes(4, "big"), blob]
    return b"".join(parts)


def _rows_ok(listing: object, count: int) -> bool:
    """The walker's output has the expected shape: one row per blob, in order (bytecode_roots)."""
    rows = listing.get("rows") if isinstance(listing, dict) else None
    if not isinstance(rows, list) or len(rows) != count or set(listing) != {"rows"}:  # type: ignore[arg-type]
        return False
    for row in rows:
        if not (isinstance(row, list) and len(row) == 2 and row[0] in _KINDS):
            return False
        names = row[1]
        if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            return False
        if not set(names) <= HOOK_NAMES or (row[0] != "ok" and names):
            return False
    return True


def parse_blobs(
    blobs: list[bytes], python: str, budget: ScanBudget
) -> list[tuple[str, set[str]]] | None:
    """``(kind, names)`` per blob from the verifier's ``python`` (``ok``, ``syntax`` or ``fail``).

    One subprocess for all blobs, as the owner (it only parses; the interpreter is owner-written)
    with ``-I -S -B``, an empty environment and ``/`` as the directory, as bytecode_roots does.
    The time it takes is paid from the patch's budget. ``None`` is a run that did not end well
    (exit, timeout, crash, malformed or oversized output): the caller refuses with scan_budget.
    """
    if not blobs:
        return []
    started = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 - fixed walker, candidate bytes only on stdin
            [python, "-I", "-S", "-B", "-c", WALKER.read_text(), json.dumps(sorted(HOOK_NAMES))],
            input=_frame(blobs),
            capture_output=True,
            timeout=max(budget.seconds_left, 0.001),
            check=False,
            env={},
            cwd="/",
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        budget.spend_time(time.monotonic() - started, over=True)
        return None
    budget.spend_time(time.monotonic() - started)
    if done.returncode != 0 or len(done.stdout) > MAX_OUTPUT:
        return None
    try:
        listing = json.loads(done.stdout)
    except ValueError:
        return None
    if not _rows_ok(listing, len(blobs)):
        return None
    return [(row[0], set(row[1])) for row in listing["rows"]]


def _batch(repo: Any, oids: list[str]) -> dict[str, bytes]:
    """Contents of blobs from one ``git cat-file --batch`` (oids are 40/64-hex, checked)."""
    if not oids:
        return {}
    raw = run_git_bytes(repo, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in oids).encode())
    out: dict[str, bytes] = {}
    at = 0
    for oid in oids:
        end = raw.index(b"\n", at)
        head = raw[at:end].split(b" ")
        if len(head) != 3 or head[1] != b"blob":
            raise GitCommandError("git cat-file did not return a blob")
        size = int(head[2])
        out[oid] = raw[end + 1 : end + 1 + size]
        at = end + 1 + size + 1
    return out


def file_rules(
    repo: Any,
    base: str,
    head: str,
    budget: ScanBudget,
    python: str,
    notes: list[str] | None = None,
) -> list[tuple[str, str]]:
    """``(path, rule)`` for the changed files: sourceless binaries and hook files touched.

    Parsing is paid from the patch's budget (``scan_budget`` when it runs out), by ``python``, the
    interpreter the verifier will run (``verify_context.verifier_interpreter``). A blob that is
    a plain ``SyntaxError`` there is logged once (``unparseable_by_verifier``; also in ``notes``).
    """
    if not (OID.fullmatch(head) and OID.fullmatch(base)):
        raise GitCommandError("base or head is not a full commit id")
    raw = run_git_bytes(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
        "--raw", "--no-abbrev", "-z", base, head,
    )  # fmt: skip
    fields = raw.decode("utf-8", "surrogateescape").split("\0")[:-1]
    found: list[tuple[str, str]] = []
    python_files: list[tuple[str, str, str]] = []  # path, old oid, new oid
    for meta, path in zip(fields[0::2], fields[1::2], strict=True):
        _, _, old, new, status = meta.lstrip(":").split(" ")
        if status[0] != "D" and path.lower().endswith(SOURCELESS):
            found.append((path, "sourceless_binary"))
        elif path.endswith(".py"):
            python_files.append((path, old, new))
    zero = {"0" * 40, "0" * 64}
    wanted = list(dict.fromkeys(o for _, a, b in python_files for o in (a, b) if o not in zero))
    if not all(OID.fullmatch(oid) for oid in wanted):
        raise GitCommandError("git diff returned an unexpected object name")
    blobs = _batch(repo, wanted)
    work: list[tuple[str, bytes]] = []  # path, blob: new side then old side, as before
    for path, old, new in python_files:
        for oid in (new, old):
            data = blobs.get(oid)
            if data is None:
                continue
            if not budget.tokenize(data):
                return [*found, (path, "scan_budget")]
            work.append((path, data))
    rows = parse_blobs([data for _, data in work], python, budget)
    if rows is None or budget.over:
        return [*found, *([(work[0][0], "scan_budget")] if work else [])]
    flagged: set[str] = set()
    noted: set[str] = set()
    for (path, _), (kind, names) in zip(work, rows, strict=True):
        if kind == "fail":
            return [*found, (path, "scan_budget")]
        if kind == "syntax":
            if path not in noted:  # both sides: once
                noted.add(path)
                if notes is not None:  # tests read it; production only logs
                    notes.append(f"{path}: unparseable_by_verifier")
                log.info("%r: unparseable_by_verifier", path)
        elif names and path not in flagged:
            flagged.add(path)
            found.append((path, "hook_file_touched"))
    return found
