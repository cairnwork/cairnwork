"""#1833: the refusal of a class with an imported base names the cause. Message only: every kind,
code and verdict stays what main gives (pure functions, a throwaway git repo and fake runs; no
sandbox, no pytest run of the tested files)."""

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import deferred_base_hint, pipeline_judge
from cairnwork import deferred_tests as dt
from cairnwork.deferred_base_hint import base_hint, unread_classes

TOP = "class I(Base):\n    def test_x(self):\n        assert 1\n"
IMPORTS = [
    "from helpers import Base\n",
    "from helpers import Other as Base\n",
    "from . import Base\n",
    "import helpers\nBase = helpers.Base\n",  # not an import binding: main reads no id either
    "import helpers as Base\n",
]
SHAPES = {
    "one imported base": (IMPORTS[0] + TOP, 1),
    "imported as alias": (IMPORTS[1] + TOP, 1),
    "relative import": (IMPORTS[2] + TOP, 1),
    "attribute base": ("import helpers\nclass I(helpers.Base):\n    pass\n", 1),
    "import as": (IMPORTS[4] + "class I(Base.Sub):\n    pass\n", 1),
    "two classes": (IMPORTS[0] + TOP + TOP.replace("I(", "J("), 2),
    "Test name, imported base": (IMPORTS[0] + TOP.replace("I(", "TestI("), 0),
    "TestCase base": ("from m import MyTestCase\n" + TOP.replace("Base", "MyTestCase"), 0),
    "attribute TestCase base": ("import m\n" + TOP.replace("(Base)", "(m.MyTestCase)"), 0),
    "local base": ("class Base:\n    pass\n" + TOP, 0),
    "base from a def in file": ("def Base():\n    pass\n" + TOP, 0),
    "star import": ("from helpers import *\n" + TOP, 0),
    "no base": (TOP.replace("(Base)", ""), 0),
    "nested class": (
        IMPORTS[0] + "class TestA:\n" + "".join(" " + ln + "\n" for ln in TOP.split("\n")),
        0,
    ),
    "name rebound later": (IMPORTS[0] + TOP + "I = Other\n", 1),  # verdict is main's either way
    "conditional rebind": (IMPORTS[0] + TOP + "if x:\n    I = Other\n", 1),
    "function with imported default": (IMPORTS[0] + "def f(a=Base):\n    pass\n", 0),
}


@pytest.mark.parametrize(("source", "count"), SHAPES.values(), ids=SHAPES.keys())
def test_only_a_top_level_non_test_class_with_an_imported_base_counts(source, count):
    assert unread_classes(ast.parse(source)) == count


def _repo(tmp_path: Path, files: dict[str, str | bytes]) -> tuple[Path, str]:
    repo = tmp_path / "r"
    repo.mkdir(parents=True)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(text if isinstance(text, bytes) else text.encode())
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
        check=True, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    run("init", "-q")
    run("add", "-A")
    run("commit", "-q", "-m", "t")
    return repo, run("rev-parse", "HEAD").stdout.strip()


CODES = [
    "TESTS_DO_NOT_FAIL_ON_BASE",
    "DEFERRED_TESTS_NOT_RUN",
    "DEFERRED_TESTS_UNREADABLE",
    "DEFERRED_TESTS_NOT_TARGETED",
    "BASE_COLLECTION_ERROR",
    "BASE_SEED_SENSITIVE",
    "",
]


@pytest.mark.parametrize("code", CODES)
def test_the_hint_is_for_one_code_only(tmp_path, code):
    repo, commit = _repo(tmp_path, {"t_a.py": IMPORTS[0] + TOP})
    hint = base_hint(repo, commit, ("t_a.py",), code)
    assert bool(hint) == (code == "TESTS_DO_NOT_FAIL_ON_BASE")
    assert "1 test class" in hint or not hint


def test_the_hint_gives_a_count_and_never_a_name_or_path(tmp_path):
    src = IMPORTS[0] + TOP.replace("I(", "SecretClass(").replace("test_x", "test_secret_name")
    repo, commit = _repo(tmp_path, {"tests/t_secret_file.py": src, "t_b.py": IMPORTS[0] + TOP})
    hint = base_hint(
        repo, commit, ("tests/t_secret_file.py", "t_b.py"), deferred_base_hint.HINT_CODE
    )
    assert "2 test class(es)" in hint and "rename such a class to Test*" in hint
    assert not {"SecretClass", "secret", "test_x", "Base", "helpers"} & set(hint.split())
    assert "secret" not in hint and "Base" not in hint


@pytest.mark.parametrize(
    "files",
    [
        {"t_a.py": b"def broken(:\n"},
        {"t_a.py": b"\xff\xfe\x00"},
        {"t_a.py": "x = 1\n"},
        {},  # the path is not in the commit (a deletion or a new file elsewhere)
    ],
    ids=["syntax error", "not utf-8", "no class", "missing"],
)
def test_an_unreadable_file_gives_no_hint_and_no_error(tmp_path, files):
    repo, commit = _repo(tmp_path, {"keep.txt": "k", **files})
    assert base_hint(repo, commit, ("t_a.py", "keep.txt"), deferred_base_hint.HINT_CODE) == ""


def test_a_bad_commit_gives_no_hint_and_no_error(tmp_path):
    repo, _ = _repo(tmp_path, {"t_a.py": IMPORTS[0] + TOP})
    assert base_hint(repo, "0" * 40, ("t_a.py",), deferred_base_hint.HINT_CODE) == ""


# the verdict never moves: tests_with_teeth, fake runs, the comparison decided by main's function
COMPARISONS = [
    dt.Comparison("TEETH"),
    dt.Comparison("NO_TEETH", "TESTS_DO_NOT_FAIL_ON_BASE", "pytest exit codes [0]"),
    dt.Comparison("NO_TEETH", "OTHER_CODE", "x"),
    dt.Comparison("NOT_RUN", "DEFERRED_TESTS_NOT_RUN", "no verifier collected or ran 2"),
    dt.Comparison("UNCOMPARABLE", "BASE_COLLECTION_ERROR", "collect error"),
    dt.Comparison("UNCOMPARABLE", "TESTS_DO_NOT_FAIL_ON_BASE", "same code, other kind"),
]


@pytest.fixture(scope="module")
def repos(tmp_path_factory):
    made: dict[str, tuple[Path, str]] = {}

    def get(source: str) -> tuple[Path, str]:
        if source not in made:
            made[source] = _repo(tmp_path_factory.mktemp("w"), {"t_a.py": source})
        return made[source]

    return get


def _judge(monkeypatch, repos, comparison, source, *, with_hint=True):
    repo, commit = repos(source)
    task = SimpleNamespace(
        repo=repo, base_commit=commit, test_commit=commit, test_roots=(), test_store=None,
        origin_url=None,
    )  # fmt: skip
    result = SimpleNamespace(attempt_id="a", logs=[], failure_evidence=[])
    ctx = SimpleNamespace(hash_seed="1")
    pj = pipeline_judge
    monkeypatch.setattr(pj, "check_roots_on_base", lambda *_: None)
    monkeypatch.setattr(pj, "ensure_test_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(pj, "test_paths", lambda *_: (("t_a.py",), ()))
    monkeypatch.setattr(pj, "new_tests", lambda *_: frozenset({"t_a.py::TestI::test_x"}))
    monkeypatch.setattr(pj.deferred_collected, "check_with_base", lambda *_: None)
    monkeypatch.setattr(pj, "untargeted", lambda *_: [])
    monkeypatch.setattr(pj, "run_verifiers", lambda *_a, **_k: [])
    monkeypatch.setattr(pj, "as_failed", lambda run, *_: run)
    monkeypatch.setattr(pj, "base_comparison", lambda *_: comparison)
    monkeypatch.setattr(pj, "_check_seed_replay", lambda *_a: None)
    if not with_hint:  # main's text
        monkeypatch.setattr(pj, "base_hint", lambda *_: "")
    try:
        got = pj.tests_with_teeth(task, (), result, ctx)
    except (dt.TestCommitRejected, dt.BaseUncomparable) as exc:
        comp = getattr(exc, "comparison", None)
        return type(exc).__name__, getattr(exc, "code", comp and comp.code), str(exc)
    assert isinstance(got, dt.TestFiles)
    return "dt.TestFiles", None, ""


@pytest.mark.parametrize("source", [s for s, _ in SHAPES.values()], ids=SHAPES.keys())
@pytest.mark.parametrize("comparison", COMPARISONS, ids=lambda c: f"{c.kind}-{c.code or 'none'}")
def test_every_outcome_matches_main_and_only_the_detail_may_grow(
    monkeypatch, repos, comparison, source
):
    mine = _judge(monkeypatch, repos, comparison, source)
    main = _judge(monkeypatch, repos, comparison, source, with_hint=False)
    assert mine[:2] == main[:2]  # same exception class, same code
    assert mine[2].startswith(main[2])
    grew = mine[2] != main[2]
    expect = (
        comparison.kind == "NO_TEETH"
        and comparison.code == "TESTS_DO_NOT_FAIL_ON_BASE"
        and unread_classes(ast.parse(source)) > 0
    )
    assert grew == expect


def test_the_refusal_of_one_class_with_an_imported_base_names_the_cause(monkeypatch, repos):
    comp = dt.Comparison("NO_TEETH", "TESTS_DO_NOT_FAIL_ON_BASE", "pytest exit codes [0]")
    kind, code, text = _judge(monkeypatch, repos, comp, IMPORTS[0] + TOP)
    assert (kind, code) == ("TestCommitRejected", "TESTS_DO_NOT_FAIL_ON_BASE")
    assert "imported from another file" in text and "rename such a class to Test*" in text
