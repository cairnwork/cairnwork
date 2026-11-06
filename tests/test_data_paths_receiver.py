"""#850 PR1: the receiver accepts test DATA files a test commit declares in its message
(``Cairnwork-Data: <path>...``), only in full mode under ``allow_data_paths: true``."""

from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from conftest_scenarios import PLAIN
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

from cairnwork import deferred_data as dd
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork_attest import binding_extras as bx
from cairnwork_attest import verify_coverage as vc

DATA = "tests/data/lint_case.py"
NEW_TEST = "tests/test_new.py"
TRAILER = f"\n\n{dd.DATA_TRAILER}: {DATA}\n"
ALLOW = {"allow_data_paths": True}
BAD_DATA = "def test_x():\n    assert False\n"


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _commit(project, files, message="tests"):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files, message="tests")
    git(project, "commit", "-q", "--amend", "--cleanup=verbatim", "-m", message)  # CRs kept
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


def _case(project, *, message="tests" + TRAILER, data_text="x = 1\n", paths=(DATA, NEW_TEST),
          data_paths=(DATA,), verifier=NEW_TEST, full=True, policy=ALLOW):  # fmt: skip
    base, commit = _commit(project, {DATA: data_text, NEW_TEST: PLAIN}, message)
    binding = {"base": base, "test_commit": commit, "test_paths": sorted(paths),
               "verifiers": [["v1", ["python3", "-m", "pytest", "-q", verifier]]]}  # fmt: skip
    if data_paths:
        binding |= {"data_paths": list(data_paths), "data_rule": dd.DATA_RULE}
    request = {"acceptance_contract": {"required_verifier_ids": ["v1"]}}
    return SimpleNamespace(binding=binding, request=request, full=full, policy=policy, repo=project)


def _code(ctx):
    result = vc.coverage_problem(ctx)
    return None if result is None else result.code


def test_ac1_a_declared_data_file_needs_no_target_and_the_policy_must_allow(project):  # AC1
    ctx = _case(project)
    assert _code(ctx) is None
    ctx.policy = {}
    assert _code(ctx) == "DATA_PATHS_NOT_ALLOWED"
    ctx.policy = {"allow_data_paths": False}
    assert _code(ctx) == "DATA_PATHS_NOT_ALLOWED"


def test_ac2_a_confidential_receiver_refuses_data_paths_even_when_allowed(project):  # AC2
    assert _code(_case(project, full=False)) == "DATA_PATHS_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("message", "data_paths"),
    [
        ("tests" + TRAILER.rstrip() + " tests/data/b.py\n", (DATA,)),  # one extra declared
        ("tests" + TRAILER, (DATA, "tests/data/b.py")),  # one missing in the commit
        ("tests\n\nbody\n", (DATA,)),  # no trailer at all
        ("tests\n\nCairnwork-Data: ../x.py\n", (DATA,)),  # a refused declaration
    ],
)
def test_ac3_the_binding_must_equal_the_commits_declaration(project, message, data_paths):  # AC3
    paths = (*data_paths, NEW_TEST)
    ctx = _case(project, message=message, data_paths=data_paths, paths=paths)
    assert _code(ctx) == "DATA_PATHS_MISMATCH"


@pytest.mark.parametrize("name", ["tests/test_y.py", "tests/data/conftest.py", "tests/__init__.py"])
def test_ac4_a_test_conftest_or_init_file_cannot_be_declared(project, name):  # AC4: INVALID
    ctx = _case(project, message="tests\n\n" + f"{dd.DATA_TRAILER}: {name}\n")
    ctx.binding |= {"data_paths": [name], "test_paths": sorted({name, NEW_TEST})}
    base, commit = _commit(project, {name: "x = 1\n", NEW_TEST: PLAIN},
                           f"tests\n\n{dd.DATA_TRAILER}: {name}\n")  # fmt: skip
    ctx.binding |= {"base": base, "test_commit": commit}
    assert _code(ctx) == "DATA_PATHS_INVALID"


@pytest.mark.parametrize("target", [DATA, DATA + "::test_x"])
def test_ac5_a_verifier_target_naming_the_data_path_fails(project, target):  # AC5
    ctx = _case(project)
    ctx.binding["verifiers"] = [["v1", ["python3", "-m", "pytest", "-q", NEW_TEST, target]]]
    assert _code(ctx) == "DATA_PATHS_INVALID"


def _ok():
    return {"test_paths": [DATA, NEW_TEST], "data_paths": [DATA], "data_rule": dd.DATA_RULE}


@pytest.mark.parametrize(
    "change",
    [
        {"data_paths": DATA},  # not a list
        {"data_paths": [1]},
        {"data_paths": [None]},
        {"data_paths": [NEW_TEST, DATA]},  # unsorted
        {"data_paths": [DATA, DATA]},  # duplicate
        {"data_paths": ["/tests/a.py"], "test_paths": ["/tests/a.py"]},
        {"data_paths": ["tests/../a.py"], "test_paths": ["tests/../a.py"]},
        {"data_paths": ["tests/d.txt"], "test_paths": ["tests/d.txt"]},
        {"data_paths": ["tests\\d.py"], "test_paths": ["tests\\d.py"]},
        {"data_rule": "exec-refuse-v2"},
        {"data_rule": 1},
        {"data_rule": None},
        {"data_paths": ["tests/other.py"]},  # not in test_paths
        {"data_paths": [f"d{i}.py" for i in range(dd.MAX_DATA_PATHS + 1)]},
        {"test_paths": [DATA, NEW_TEST, "tests/conftest.py"], "conftest_paths": [DATA]},
        {
            "data_paths": ["tests/conftest.py"],
            "conftest_paths": ["tests/conftest.py"],
            "test_paths": ["tests/conftest.py"],
        },  # fmt: skip
    ],
)
def test_ac6_a_malformed_data_binding_is_a_problem_not_an_exception(change):  # AC6
    assert bx.binding_problem(_ok()) == ""
    assert bx.binding_problem(_ok() | change)


def test_ac6_data_paths_and_data_rule_come_together():  # AC6
    without_rule = {k: v for k, v in _ok().items() if k != "data_rule"}
    without_paths = {k: v for k, v in _ok().items() if k != "data_paths"}
    assert bx.binding_problem(without_rule) and bx.binding_problem(without_paths)


def test_ac7_the_trailer_alone_grants_nothing(project):  # AC7
    ctx = _case(project, data_paths=())
    ctx.binding["test_paths"] = sorted([DATA, NEW_TEST])
    assert _code(ctx) == "TEST_PATH_NOT_TARGETED"
    assert "data_rule" not in bx.assurance_extras(ctx.binding)


def test_ac8_a_declared_data_file_with_a_test_function_counts_for_no_teeth(project):  # AC8
    assert _code(_case(project, data_text=BAD_DATA)) is None
    ctx = _case(project, data_text=BAD_DATA, data_paths=())
    assert _code(ctx) == "TEST_PATH_NOT_TARGETED"  # not declared: a new test nobody targets


def test_the_assurance_names_the_signers_word_only_when_data_paths_are_present():
    assert bx.assurance_extras(_ok()) == {"data_rule": "signer_claim"}
    assert bx.assurance_extras({"test_paths": [NEW_TEST]}) == {}


def _msg(*lines):
    return "subject\n\n" + "\n".join(lines) + "\n"


T = dd.DATA_TRAILER
LONG = " ".join(f"d{i}.py" for i in range(dd.MAX_DATA_PATHS + 1))


@pytest.mark.parametrize(
    "message",
    [
        f"s\r\n\n{T}: a.py\n",  # CR in the subject only
        _msg(f"{T}: a.py", f"{T}: b.py"),  # two lines
        f"s\n\n{T}: a.py\n\nbody\n",  # not in the last paragraph
        f"{T}: a.py\n",  # one paragraph only
        _msg(f"{T}:"),  # empty value
        _msg(f"{T}:   "),
        _msg(f"{T}: a.py a.py"),  # duplicate
        _msg(f"{T}: {LONG}"),  # more than MAX
        _msg(f"{T}: /a.py"),  # absolute
        _msg(f"{T}: x/../a.py"),  # ..
        _msg(f"{T}: x/./a.py"),
        _msg(f"{T}: x\\a.py"),  # backslash
        _msg(f"{T}: a.txt"),  # not .py
        _msg(f"{T}: a.py  b.py"),  # an empty path between two spaces
    ],
)
def test_ac9_the_parser_refuses_a_loose_declaration(message):  # AC9
    with pytest.raises(TestCommitRejected, match="DATA_DECLARATION"):
        dd.declared_data(message)


def test_ac9_the_parser_reads_one_trailer_sorted_and_none_as_empty():
    assert dd.declared_data("subject\n\nbody\n") == ()
    assert dd.declared_data(_msg(f"{T}: b.py a.py")) == ("a.py", "b.py")
    assert dd.declared_data(_msg("Other: x", f"{T}: a/b.py")) == ("a/b.py",)


def test_ac10_the_policy_key_must_be_a_boolean():  # AC10
    assert bx.policy_problem({"allow_data_paths": "yes"})
    assert not bx.policy_problem({"allow_data_paths": True})


@pytest.mark.parametrize(
    "message",
    ["tests\r\n\r\n" + TRAILER.strip() + "\r\n", "tests\n\nbody\r" + TRAILER.strip() + "\n"],
)
def test_a_cr_in_the_commit_message_is_refused_end_to_end(project, message):  # review r1 #1
    assert _code(_case(project, message=message)) == "DATA_PATHS_MISMATCH"


def test_an_unparsable_declared_data_file_is_accepted(project):  # review r1 #4: never read
    assert _code(_case(project, data_text="def broken(:\n")) is None


def test_a_case_variant_target_of_a_data_path_fails(project):  # review r1 #3
    ctx = _case(project, verifier="tests/data/LINT_CASE.py")
    assert _code(ctx) == "DATA_PATHS_INVALID"


def test_a_base_config_that_does_not_parse_is_a_result_not_an_exception(project):  # r1 #2
    commit_change(project, {"pyproject.toml": "[tool.pytest.ini_options\n"}, message="broken")
    assert _code(_case(project)) == "DATA_PATHS_INVALID"


def test_a_base_tree_git_cannot_read_is_a_result_not_an_exception(project, monkeypatch):  # r2
    def broken(repo, base):
        raise bx.GitCommandError("ls-tree failed")

    monkeypatch.setattr(bx, "test_file_rule", broken)
    assert _code(_case(project)) == "DATA_PATHS_INVALID"
