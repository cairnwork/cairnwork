"""#1324: exception text that reaches a refusal is escaped (newline, ::error::, bidi)."""

import ast
from pathlib import Path

import pytest

from cairnwork import intake as intake_mod
from cairnwork import verify_candidate_checks as checks
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.pipeline import TaskRejected

HOSTILE = "\n::error::x‮"


def _assert_escaped(text: str) -> None:
    assert "\\x0a" in text and "\\u202e" in text
    assert "\n" not in text and "‮" not in text


def _raiser(exc):
    def raise_it(*_args, **_kwargs):
        raise exc

    return raise_it


def test_verifier_environment_error_text_is_escaped(monkeypatch):
    monkeypatch.setattr(checks, "check_task", _raiser(OSError(HOSTILE)))
    with pytest.raises(TaskRejected) as info:
        checks.check_task_for_verify(object(), "python")
    assert info.value.code == "VERIFIER_ENVIRONMENT_ERROR"
    _assert_escaped(str(info.value))


def test_bad_import_path_text_is_escaped(monkeypatch):
    monkeypatch.setattr(checks, "check_verify_import_paths", _raiser(ValueError(HOSTILE)))
    with pytest.raises(TaskRejected) as info:
        checks.validate_import_paths(None, "base", [], ())
    assert info.value.code == "BAD_IMPORT_PATH"
    _assert_escaped(str(info.value))


def test_bad_config_text_is_escaped(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text("{}")
    monkeypatch.setattr(intake_mod.json, "loads", _raiser(ValueError(HOSTILE)))
    with pytest.raises(IntakeRefused) as info:
        Intake(state).config()
    assert info.value.code == "BAD_CONFIG"
    _assert_escaped(str(info.value))


# Source-level cover for every site of #1324: exception text must pass through shown() before
# it reaches an output sink. Track the exception name and one direct alias assignment.
_MODULES = (
    "taskpaths conftest_check conftest_names deferred_tests deferred_nodes intake "
    "verify_candidate_checks verify_cli p2p pipeline"
).split()
_EXPECTED_SITES = 50
# Each exception is already code-prefixed, and the named source has shown() the detail.
_ALLOWED_SITES: set[tuple[str, str, str]] = {
    # _attempt relays deferred_tests.py:108/137/258 and snapshot.py:65, whose details use shown().
    ("pipeline", "_attempt", "Call"),
    # base_env_check relays P2PRejected from p2p.py:105, whose detail uses shown().
    ("p2p", "base_env_check", "Call"),
    ("p2p", "base_env_check", "Raise"),
    # _import_tests relays deferred_store._raise_git_failure; the error tracks raw vs shown detail.
    ("verify_cli", "_import_tests", "Call"),
}
_ALREADY_SHOWN = {"TestCommitRejected", "CandidateRejected", "P2PRejected"}


def _mentions(node, names):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "shown":
        return any(_mentions(arg, names) for arg in node.args)
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute) and node.attr == "__name__":
        return False  # type(exc).__name__ reports a type, not exception text
    if isinstance(node, ast.Attribute) and node.attr == "code":
        return False  # stable refusal code, not exception text
    return any(_mentions(child, names) for child in ast.iter_child_nodes(node))


def _is_shown(node, names):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "shown"
        and any(_mentions(arg, names) for arg in node.args)
    )


def _sink_nodes(handler, names):
    for node in ast.walk(handler):
        if isinstance(node, ast.FormattedValue) and _mentions(node.value, names):
            yield node, node.value
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and _mentions(node.right, names)
        ):
            yield node, node.right
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "repr"
        ):
            if any(_mentions(arg, names) for arg in node.args):
                yield node, node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "format" and any(
                _mentions(arg, names) for arg in (*node.args, *(kw.value for kw in node.keywords))
            ):
                yield node, node
        elif isinstance(node, ast.Call) and any(
            isinstance(arg, ast.Attribute) and arg.attr == "args"
            for arg in (*node.args, *(kw.value for kw in node.keywords))
        ):
            if any(
                _mentions(arg, names) for arg in (*node.args, *(kw.value for kw in node.keywords))
            ):
                yield node, node
        elif isinstance(node, ast.Raise) and node.exc is not None and _mentions(node.exc, names):
            yield node, node.exc
        elif (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "reason"
                for target in node.targets
            )
            and _mentions(node.value, names)
        ):
            calls = [
                child
                for child in ast.walk(node.value)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "str"
                and any(_mentions(arg, names) for arg in child.args)
            ]
            for call in calls or [node.value]:
                yield call, call
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr == "reason"
            and node.value is not None
            and _mentions(node.value, names)
        ):
            yield node.value, node.value


def _raw(node, names):
    if _is_shown(node, names):
        return False
    if isinstance(node, ast.FormattedValue):
        return _raw(node.value, names)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return any(_raw(arg, names) for arg in (*node.args, *(kw.value for kw in node.keywords)))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "repr":
        return any(_raw(arg, names) for arg in node.args)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        return any(_raw(arg, names) for arg in node.args)
    if isinstance(node, ast.Attribute) and node.attr == "__name__":
        return False
    if isinstance(node, ast.Attribute) and node.attr == "code":
        return False
    if isinstance(node, ast.Attribute) and node.attr == "safe_reason":
        return False
    if isinstance(node, ast.Name):
        return node.id in names
    return any(_raw(child, names) for child in ast.iter_child_nodes(node))


def _shown_calls(node, names):
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "shown"
        and any(_mentions(arg, names) for arg in call.args)
    ]


def _double_shown(node, names):
    return any(
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "shown"
        and any(_mentions(inner, names) for inner in arg.args)
        for call in _shown_calls(node, names)
        for arg in call.args
    )


class _ExceptionTextScan(ast.NodeVisitor):
    def __init__(self, module):
        self.module = module
        self.functions = []
        self.sites = []
        self.violations = []

    def visit_ExceptHandler(self, handler):
        if handler.name:
            exception_names = {handler.name}
            aliases = set()
            for node in ast.walk(handler):
                if isinstance(node, ast.Assign) and _mentions(node.value, exception_names):
                    aliases.update(
                        target.id for target in node.targets if isinstance(target, ast.Name)
                    )
                elif (
                    isinstance(node, ast.AnnAssign)
                    and node.value
                    and _mentions(node.value, exception_names)
                ):
                    if isinstance(node.target, ast.Name):
                        aliases.add(node.target.id)
            names = exception_names | aliases
            already_shown = isinstance(handler.type, ast.Name) and handler.type.id in _ALREADY_SHOWN
            for call in _shown_calls(handler, names):
                if already_shown or _double_shown(call, names):
                    key = (
                        self.module,
                        self.functions[-1] if self.functions else "<module>",
                        "Call",
                    )
                    self.sites.append(key)
                    self.violations.append((key, call.lineno))
            for node, value in _sink_nodes(handler, names):
                key = (
                    self.module,
                    self.functions[-1] if self.functions else "<module>",
                    type(node).__name__,
                )
                self.sites.append(key)
                double = _double_shown(value, names)
                shown_twice = already_shown and bool(_shown_calls(value, names))
                if double or shown_twice or (_raw(value, names) and key not in _ALLOWED_SITES):
                    self.violations.append((key, getattr(node, "lineno", 0)))
        self.generic_visit(handler)

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self._check_exception_parameters(node)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _check_exception_parameters(self, function):
        args = (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
        for arg in args:
            annotation = ast.unparse(arg.annotation) if arg.annotation else ""
            if not any(word in annotation for word in ("Error", "Exception", "BaseException")):
                continue
            names = {arg.arg}
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Raise)
                    and node.exc is not None
                    and _mentions(node.exc, names)
                ):
                    calls = _shown_calls(node.exc, names)
                    sites = calls or [node.exc]
                    for sink in sites:
                        key = (self.module, function.name, type(sink).__name__)
                        self.sites.append(key)
                        if (
                            _double_shown(node.exc, names) or _raw(node.exc, names)
                        ) and key not in _ALLOWED_SITES:
                            self.violations.append((key, sink.lineno))


def _scan(source, module):
    checker = _ExceptionTextScan(module)
    checker.visit(ast.parse(source))
    return checker.sites, checker.violations


def test_checker_rejects_each_raw_exception_text_pattern():
    examples = (
        'try:\n pass\nexcept Exception as err:\n log("%s" % err)\n',
        "try:\n pass\nexcept Exception as err:\n log(repr(err))\n",
        "try:\n pass\nexcept Exception as err:\n log(err.args)\n",
        'try:\n pass\nexcept Exception as err:\n log("{}".format(err))\n',
        'try:\n pass\nexcept Exception as err:\n detail = str(err)\n log(f"{detail}")\n',
        "try:\n pass\nexcept Exception as err:\n message = err\n raise RuntimeError(message)\n",
    )
    for source in examples:
        sites, violations = _scan(source, "synthetic")
        assert sites and violations, source


def test_checker_accepts_shown_exception_text():
    sites, violations = _scan(
        'try:\n pass\nexcept Exception as err:\n log("{}".format(shown(err)))\n', "synthetic"
    )
    assert sites
    assert not violations


def test_checker_rejects_double_shown_and_shown_already_shown_exception():
    double, double_bad = _scan(
        "try:\n pass\nexcept Exception as err:\n log(shown(shown(err)))\n", "synthetic"
    )
    repeated, repeated_bad = _scan(
        "try:\n pass\nexcept TestCommitRejected as err:\n log(shown(err))\n", "synthetic"
    )
    assert double and double_bad
    assert repeated and repeated_bad


def test_checker_scans_exception_parameters_in_raise_sites():
    sites, violations = _scan(
        "def relay(error: OSError):\n    raise RuntimeError(error)\n", "synthetic"
    )
    assert sites and violations
    sites, violations = _scan(
        "def relay(error: OSError):\n    raise RuntimeError(shown(error))\n", "synthetic"
    )
    assert sites and not violations


def test_conftest_absent_base_raise_is_scanned():
    path = Path(__file__).parent.parent / "src" / "cairnwork" / "conftest_check.py"
    sites, violations = _scan(path.read_text(), "conftest_check")
    assert ("conftest_check", "_empty_for_absent_base", "Call") in sites
    assert not violations


def test_allowlist_is_bound_to_module_function_and_node_kind():
    source = "def handle():\n try:\n  pass\n except Exception as err:\n  log(repr(err))\n"
    sites, violations = _scan(source, "synthetic")
    assert sites and violations
    allowed = violations[0][0]
    assert allowed == ("synthetic", "handle", "Call")
    _ALLOWED_SITES.add(allowed)
    try:
        assert not _scan(source, "synthetic")[1]
    finally:
        _ALLOWED_SITES.remove(allowed)


def test_allowlist_for_str_does_not_allow_shown_again():
    raw_source = (
        "def _attempt():\n try:\n  pass\n except TestCommitRejected as err:\n"
        "  result.reason = str(err)\n"
    )
    shown_source = (
        "def _attempt():\n try:\n  pass\n except TestCommitRejected as err:\n"
        "  result.reason = shown(err)\n"
    )
    assert not _scan(raw_source, "pipeline")[1]
    sites, violations = _scan(shown_source, "pipeline")
    assert sites and violations


@pytest.mark.parametrize("module", _MODULES)
def test_no_raw_exception_text_in_refusals(module):
    path = Path(__file__).parent.parent / "src" / "cairnwork" / f"{module}.py"
    sites, violations = _scan(path.read_text(), module)
    assert len(sites) > 0, f"{module}.py has no checked exception-text sinks"
    assert not violations, f"{module}.py raw exception text at {violations}"


def test_ten_module_scan_is_non_vacuous():
    root = Path(__file__).parent.parent / "src" / "cairnwork"
    sites = [
        site
        for module in _MODULES
        for site in _scan((root / f"{module}.py").read_text(), module)[0]
    ]
    assert len(sites) == _EXPECTED_SITES
