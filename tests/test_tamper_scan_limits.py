"""#1039 step 1: tamper_scan_limits.py lists every residual class of the static scan with its
tracking Issues, its run-time defence (or ``none``) and its pinning tests, and the list stays true.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^\s*(tests/\S+\.py)::(\S+)$", re.M)


def _entries() -> dict[str, str]:
    from cairnwork import tamper_scan_limits

    parts = re.split(r"^(Class \d+):", tamper_scan_limits.__doc__ or "", flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _defence(body: str) -> str:
    match = re.search(r"^\s*Defence:(.*?)(?=^\s*Pin:)", body, re.M | re.S)
    assert match, body
    return " ".join(match.group(1).split())


def _is_none(body: str) -> bool:
    """A class with no run-time defence, in full or in part ("otherwise none")."""
    return re.search(r"\bnone\b", _defence(body)) is not None


DEFENCE_FILES = {
    "Class 1": {"src/cairnwork/report_guard.py"},
    "Class 2": {"src/cairnwork/report_guard.py"},
    "Class 3": {
        "src/cairnwork/pycache_guard.py",
        "src/cairnwork/bytecode_roots.py",
        "src/cairnwork/bytecode_hook.py",
        "src/cairnwork_sandbox/seatbelt.py",
    },
    "Class 4": {"src/cairnwork/report_guard.py", "src/cairnwork/bytecode_hook.py"},
    "Class 5": {"src/cairnwork/bytecode_hook.py"},
    "Class 6": set(),
    "Class 7": {"src/cairnwork/verdict_record.py", "src/cairnwork/report_guard.py"},
    "Class 8": {"src/cairnwork/report_guard.py"},
}
NO_DEFENCE = {"Class 1", "Class 2", "Class 5", "Class 6"}


def test_every_class_has_issues_a_defence_or_none_and_a_pin():
    entries = _entries()
    assert len(entries) >= 8
    for name, body in entries.items():
        assert re.search(r"^\s*Issues:.*#\d+", body, re.M), name
        defence = _defence(body)
        assert _is_none(body) or re.search(r"src/\S+\.py", defence), name
        assert PIN.search(body), name


def test_every_pin_names_a_test_that_exists():
    pins = PIN.findall("\n".join(_entries().values()))
    assert pins
    for file, name in pins:
        tree = ast.parse((ROOT / file).read_text())
        owner, _, func = name.rpartition("::")
        scope = tree.body
        if owner:
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner]
            assert classes, (file, name)
            scope = classes[0].body
        found = {n.name for n in scope if isinstance(n, ast.FunctionDef)}
        assert func in found, (file, name)


def test_a_class_with_no_defence_is_pinned_by_the_same_process_label():
    from cairnwork import protection

    none_classes = {k: b for k, b in _entries().items() if _is_none(b)}
    assert none_classes
    for name, body in none_classes.items():
        assert "tests/test_protection.py::test_levels" in body, name
    # a pytest-only verdict claims no protection beyond the same-process hurdle
    assert protection.level(("pytest-0",), macos=False) == protection.NONE == "same_process_only"


def test_every_named_defence_file_exists():
    entries = _entries()
    named = {k: set(re.findall(r"src/\S+?\.py", _defence(b))) for k, b in entries.items()}
    for name, files in named.items():
        for file in files:
            assert (ROOT / file).is_file(), (name, file)
    # which files defend which class, and which classes have none, change only on purpose
    assert named == DEFENCE_FILES
    assert {k for k, b in entries.items() if _is_none(b)} == NO_DEFENCE
