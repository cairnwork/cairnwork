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


# Source-level cover for every site of #1324: in these modules no raw exception text may be
# interpolated into a refusal/report. A name bound by `except ... as NAME` must not appear as
# str(NAME), f"{NAME}", or f"{NAME!r}" unless it is in ALLOWED (comparisons, not output).
_MODULES = (
    "taskpaths conftest_check conftest_names deferred_tests deferred_nodes intake "
    "verify_candidate_checks verify_cli p2p pipeline"
).split()
ALLOWED = {("intake", "str(exc).startswith")}  # a comparison on the raw text, not output


def _raw_uses(path):
    tree = ast.parse(path.read_text())
    found = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name):
        for node in ast.walk(handler):
            raw = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "str"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == handler.name
            ) or (
                isinstance(node, ast.FormattedValue)
                and isinstance(node.value, ast.Name)
                and node.value.id == handler.name
            )
            if raw:
                found.append(node.lineno)
    return found


@pytest.mark.parametrize("module", _MODULES)
def test_no_raw_exception_text_in_refusals(module):
    path = Path(__file__).parent.parent / "src" / "cairnwork" / f"{module}.py"
    lines = path.read_text().splitlines()
    bad = [
        n
        for n in _raw_uses(path)
        if not any(key == module and frag in lines[n - 1] for key, frag in ALLOWED)
    ]
    assert not bad, f"{module}.py raw exception text at lines {bad}"
