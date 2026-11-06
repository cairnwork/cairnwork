"""#714: a verifier target may name one case of a parametrized test (``test_x.py::test_y[case]``),
one group at the end; #722: the case text is printable only (``str.isprintable()`` plus the
space) and holds no square bracket."""

import sys
from types import SimpleNamespace

import pytest
from conftest import commit_change, git

from cairnwork import p2p
from cairnwork.deferred_tests import base_comparison, untargeted
from cairnwork.issue_task import IssueRefused, pytest_argv
from cairnwork.verifiers import VerifierRejected, parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

RUN = ["python3", "-m", "pytest", "-q"]
GOOD = (
    "tests/test_x.py::test_y[1536-ARABIC NUMBER SIGN]",
    "tests/test_x.py::TestK::test_m[a/b-False]",
    "tests/test_x.py::test_y[k.pem0]",
    "tests/test_x.py::test_y[a b]",
    "tests/test_x.py::test_y[\u00e9]",
    "tests/test_x.py::test_y[a/b-1.5,x]",
    "tests/test_x.py::test_y[a::b]",
)
BAD = (
    "tests/test_x.py::test_y[open",
    "tests/test_x.py::test_y[a]b]",
    "tests/test_x.py::test_y[a\nb]",
    "tests/test_x.py::test_y[a\0b]",
    "tests/test_x.py::test_y[a]::test_z",
    "tests/test_x.py::test_y[a][b]",
    "tests/test_x.py[a]",
    "tests/test_x.py::test_y[a[b]]",
    "tests/test_x.py::test_y[a[b]",
    "tests/test_x.py::test_y[a\x1bb]",
    "tests/test_x.py::test_y[a\x7fb]",
    "tests/test_x.py::test_y[a\tb]",
    "tests/test_x.py::test_y[a\rb]",
    "tests/test_x.py::test_y[a\u202eb]",
    "tests/test_x.py::test_y[a\u200bb]",
    "tests/test_x.py::test_y[a\ufeffb]",
    "tests/test_x.py::test_y[a\xa0b]",
    "tests/test_x.py::test_y[a\x85b]",
    "tests/test_x.py::test_y[a\u2028b]",
    "tests/test_x.py::test_y[a\u2029b]",
    "tests/test_x.py::test_y[a\U000e0001b]",
    "tests/test_x.py::test_y[a\U000f0000b]",
)


@pytest.mark.parametrize("target", GOOD)
def test_one_parametrized_case_is_a_target_unchanged(target):
    assert parse_verifier("v", [*RUN, target]).targets == (target,)
    quoted = "python3 -m pytest -q " + "'" + target + "'"  # the Issue text goes through shlex
    assert pytest_argv(quoted) == (*RUN, target)


@pytest.mark.parametrize("target", BAD)
def test_a_malformed_case_is_refused(target):
    with pytest.raises(VerifierRejected, match="VERIFIER_NOT_ALLOWED"):
        parse_verifier("v", [*RUN, target])
    with pytest.raises(IssueRefused, match="TASK_VERIFICATION_UNSUPPORTED"):
        pytest_argv("python3 -m pytest -q '" + target + "'")


def _run(outcomes, rc=1):
    return SimpleNamespace(
        returncode=rc, outcomes=outcomes, timed_out=False, report_too_big=False, recorded=True,
        collect_errors=(), mutated=(), teardown_errors=(), fail_kind={}, survivors=0, forged=(),
        log_too_big=False, spec=SimpleNamespace(verifier_id="v"),
    )  # fmt: skip


def test_teeth_of_a_parametrized_case_count_for_its_function():
    new = frozenset({"tests/test_x.py::test_y"})
    assert base_comparison([_run({"tests/test_x.py::test_y[case]": "failed"})], new).kind == "TEETH"
    other = {"tests/test_x.py::test_y[case]": "passed", "tests/test_x.py::test_z[c]": "failed"}
    assert base_comparison([_run(other)], new).kind == "NO_TEETH"


def test_a_parametrized_target_names_its_test_for_the_target_rule():
    paths, nodes = ("tests/test_x.py",), {"tests/test_x.py::test_y"}
    assert untargeted(paths, nodes, ("tests/test_x.py::test_y[case one]",)) == ()
    # the receiver in confidential mode lists the target itself as the node (verify_coverage)
    assert untargeted(paths, ["tests/test_x.py::test_y[1]"], ("tests/test_x.py::test_y[1]",)) == ()
    # a [..] on the file itself (refused by the producer grammar) names nothing on either side
    assert untargeted(paths, nodes, ("tests/test_x.py[x]",)) == paths
    two = {"tests/test_x.py::test_y", "tests/test_x.py::test_z"}
    assert untargeted(paths, two, ("tests/test_x.py::test_y[case one]",)) == paths


def test_p2p_base_tests_stay_plain_files(repo):
    commit_change(repo, {"tests/test_a.py": "def test_a():\n    pass\n",
                         "tests/test_[b].py": "def test_b():\n    pass\n"})  # fmt: skip
    task = SimpleNamespace(repo=repo, base_commit=git(repo, "rev-parse", "HEAD"),
                           test_roots=("tests",))  # fmt: skip
    with pytest.raises(p2p.BaseUncomparable) as got:  # #930: never a target, never dropped silently
        p2p._base_tests(task, frozenset())
    assert got.value.comparison.code == "P2P_BASE_UNTARGETABLE"
    assert "tests/test_[b].py" in got.value.comparison.detail


CASES = (
    "import pytest\n@pytest.mark.parametrize('v', ['a b/c.d', 'other'])\n"
    "def test_y(v):\n    assert v == 'other'\n"
)


@pytest.mark.sandbox
def test_a_parametrized_target_runs_exactly_that_case(repo, tmp_path):
    head = commit_change(repo, {"tests/test_x.py": CASES})
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    one = parse_verifier("v", [*RUN, "tests/test_x.py::test_y[a b/c.d]"])
    (run,) = run_verifiers(ctx, head, (one,), "r1")
    assert run.returncode == 1 and run.outcomes == {"tests/test_x.py::test_y[a b/c.d]": "failed"}
    assert base_comparison([run], frozenset({"tests/test_x.py::test_y"})).kind == "TEETH"
    gone = parse_verifier("g", [*RUN, "tests/test_x.py::test_y[none]"])
    (run,) = run_verifiers(ctx, head, (gone,), "r2")
    assert run.returncode not in (0, 1), run.returncode
    assert base_comparison([run], frozenset({"tests/test_x.py::test_y"})).kind == "UNCOMPARABLE"


def test_the_case_text_is_printable_by_the_repository_definition():
    """Every code point the grammar lets into the case text is str.isprintable() or a space;
    everything else is refused (#722), one code point at a time over the first 65,536."""
    from cairnwork.issue_task import test_target

    target = test_target(("tests",))
    for code in range(0x10000):
        ch = chr(code)
        expected = (ch == " " or ch.isprintable()) and ch not in "[]"
        assert target.fullmatch(f"tests/test_x.py::test_y[a{ch}b]") is expected, hex(code)


@pytest.mark.parametrize(
    ("ini", "want"),
    [
        ("", ("tests/a_test.py", "tests/test_a.py")),
        ("[pytest]\npython_files = tests.py\n", ("tests/tests.py",)),
        ("[pytest]\npython_files = check_*.py tests.py\n", ("tests/check_x.py", "tests/tests.py")),
        ("[pytest]\npython_files =\n    t_*.py\n    *_t.py\n", ("tests/b_t.py", "tests/t_a.py")),
    ],
)
def test_p2p_base_tests_follow_the_configured_python_files(repo, ini, want):
    files = {n: "def test_a():\n    pass\n" for n in (
        "tests/test_a.py", "tests/a_test.py", "tests/tests.py", "tests/check_x.py",
        "tests/t_a.py", "tests/b_t.py")}  # fmt: skip
    commit_change(repo, {**files, **({"pytest.ini": ini} if ini else {})})
    task = SimpleNamespace(repo=repo, base_commit=git(repo, "rev-parse", "HEAD"),
                           test_roots=("tests",))  # fmt: skip
    got = p2p._base_tests(task, frozenset())
    assert got == want
