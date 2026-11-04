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
  ``__globals__`` or ``co_filename``, and the builtin ``compile``.
- ``dynamic_loader``: loading source from a path (``SourceFileLoader``, ``runpy``).
- a name from the rules above, written as one string or built from string pieces, next to
  ``getattr``/``setattr``/``globals()``/``vars()``/``import_module`` (``getattr(cfg, "plugin" +
  "manager")``), read like the plain name.
- ``sourceless_binary``: a changed ``.pyc``, ``.pyo``, ``.so``, ``.pyd``, ``.dylib``, ``.zip``,
  ``.egg``, ``.whl`` or ``.pyz`` file, which no line scan can read.
- ``hook_file_touched``: a changed ``.py`` file that binds a known pytest or pluggy hook name, at
  any depth, or holds a string equal to one (``globals()["pytest_x"] = f``), in its base or its
  head version (an unchanged line may then change what runs). A version that does not parse is
  searched as text for the names (whole words). Accepted false block: a task that fixes a pytest
  plugin project itself. Not refused: ``pytest_my_helper``, or a log text that merely holds a name.
- Not followed: a name that is reversed, ``replace``d, or put together on one line and used on the
  next, or an alias used later. Hook ownership is judged at run time (PR-2) by which file holds
  the function, however its name was made.

Not scanned for (measured too common in honest code, DESIGN of #821): ``exec(`` and ``eval(``.
"""

from __future__ import annotations

import ast
import re
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
    r"|\bsetattr\s*\([^#;]*['\"]__(?:code|globals)__['\"]"
    r"|(?<![\w.])(?<!def\s)compile\s*\(|\bbuiltins\s*\.\s*compile\b"
    r"|=\s*compile\s*(?:[;#,)]|$)"  # an alias of the builtin
)
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

_LITERAL = re.compile(r"""(?:[rRuUbBfF]{0,2})(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
_FSTRING_PIECE = re.compile(r"""\{\s*['"]([^'"{}]*)['"]\s*\}""")
_DYNAMIC_ACCESS = re.compile(
    r"\b(?:getattr|setattr|delattr|vars|globals|__import__|import_module)\s*\("
)
_TEXT_NAMES = re.compile(rf"\b(?:{'|'.join(sorted(HOOK_NAMES))})\b")
_NAMES = (  # a name from the rules above, as a bare word, by the rule that refuses it
    ("pytest_hook_impl", re.compile(rf"\b(?:{'|'.join(sorted(HOOK_NAMES))})\b")),
    ("pytest_internals", re.compile(PYTEST_INTERNALS.replace(r"\.\s*hook\s*\.|", ""))),
    (
        "code_object_forge",
        re.compile(r"\b(?:FunctionType|CodeType|__code__|__globals__|compile)\b"),
    ),
)


def string_built_rules(text: str) -> list[str]:
    """Rule names for a line that gives a refused name as string(s) to a dynamic lookup."""
    if not _DYNAMIC_ACCESS.search(text):
        return []
    pieces = [_FSTRING_PIECE.sub(r"\1", a or b) for a, b in _LITERAL.findall(text)]
    runs = {"".join(pieces[i:j]) for i in range(len(pieces)) for j in range(i + 1, len(pieces) + 1)}
    return [name for name, pattern in _NAMES if any(pattern.search(run) for run in runs)]


def hook_names_bound(source: bytes) -> set[str]:
    """Hook names the module binds or writes as a whole string; a parse failure is raised."""
    tree = ast.parse(source)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)  # assign, annotated and augmented assign, for, with, walrus
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.attr)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            bound.add(node.value)  # a hook name written out: globals()["pytest_x"] = f
    return bound & HOOK_NAMES


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


def file_rules(repo: Any, base: str, head: str, budget: ScanBudget) -> list[tuple[str, str]]:
    """``(path, rule)`` for the changed files: sourceless binaries and hook files touched.

    Parsing is paid from the patch's budget (``scan_budget`` when it runs out).
    """
    if not (OID.fullmatch(head) and OID.fullmatch(base)):
        raise GitCommandError("base or head is not a full commit id")
    raw = run_git_bytes(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
        "--raw", "--no-abbrev", "-z", base, head,
    )  # fmt: skip
    fields = raw.decode("utf-8", "surrogateescape").split("\0")[:-1]
    found: list[tuple[str, str]] = []
    python: list[tuple[str, str, str]] = []  # path, old oid, new oid
    for meta, path in zip(fields[0::2], fields[1::2], strict=True):
        _, _, old, new, status = meta.lstrip(":").split(" ")
        if status[0] != "D" and path.lower().endswith(SOURCELESS):
            found.append((path, "sourceless_binary"))
        elif path.endswith(".py"):
            python.append((path, old, new))
    zero = {"0" * 40, "0" * 64}
    wanted = list(dict.fromkeys(o for _, a, b in python for o in (a, b) if o not in zero))
    if not all(OID.fullmatch(oid) for oid in wanted):
        raise GitCommandError("git diff returned an unexpected object name")
    blobs = _batch(repo, wanted)
    for path, old, new in python:
        for oid in (new, old):
            data = blobs.get(oid)
            if data is None:
                continue
            if not budget.tokenize(data):
                return [*found, (path, "scan_budget")]
            if _touches_hooks(data):
                found.append((path, "hook_file_touched"))
                break
    return found


def _touches_hooks(source: bytes) -> bool:
    try:
        return bool(hook_names_bound(source))
    except Exception:  # noqa: BLE001  # does not parse here (newer syntax, Python 2, too deep)
        return bool(_TEXT_NAMES.search(source.decode("utf-8", "replace")))
