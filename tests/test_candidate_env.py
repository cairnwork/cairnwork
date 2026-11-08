"""#962 step 2b decision table: candidate setup denials never imply VERIFIED."""

from types import SimpleNamespace

import pytest
from conftest import commit_change

from cairnwork import candidate_env
from cairnwork.candidate_env import CODE, RULE, classify

DENIED = "/private/tmp/cw962-decision"
NODE = "tests/test_a.py::test_a"


def _run(verifier_id="v", *, origin="tests/conftest.py", line=1, path=DENIED, **changes):
    values = {
        "spec": SimpleNamespace(verifier_id=verifier_id, targets=("tests/test_a.py",)),
        "observation": SimpleNamespace(passed=False),
        "outcomes": {NODE: "failed"},
        "setup_denied": {NODE: (path, origin, line)},
        "fail_kind": {NODE: "setup"},
        "plain_failure": True,
        "recorded": True,
        "collection_finished": True,
        "timed_out": False,
        "mutated": (),
        "survivors": 0,
        "forged": (),
        "log_too_big": False,
        "report_too_big": False,
        "data_refused": (),
        "teardown_errors": (),
        "collect_errors": (),
        "collect_skipped": (),
        "not_run": (),
        "renamed": (),
        "why": "pytest exit code 1",
        "collected": (NODE,),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _task(repo, control, roots=("tests",), allowed=("src/app.py",)):
    return SimpleNamespace(
        repo=repo,
        base_commit=control,
        test_commit="",
        test_roots=roots,
        allowed_files=allowed,
    )


def _classify(task, candidate=None, control=None, other=()):
    candidate = candidate or (_run(),)
    control = control or (_run(),)
    observations = (candidate[0].observation, *other)
    return classify(candidate, control, observations, task)


@pytest.fixture
def control_tree(repo):
    sha = commit_change(
        repo,
        {
            "src/app.py": "def work(): return 1\n",
            "tests/conftest.py": f"open({DENIED!r}, 'w')\n",
            "tests/test_a.py": "def test_a(): pass\n",
        },
    )
    return repo, sha


def test_ac1_literal_conftest_setup_write_is_uncomparable(control_tree):
    repo, commit = control_tree
    assert _classify(_task(repo, commit))
    assert (CODE, RULE) == ("CANDIDATE_ENV_DENIED", "candidate-env-v1")


def test_ac1b_call_phase_denial_is_failed(control_tree):
    repo, commit = control_tree
    candidate = _run(setup_denied={})
    assert not _classify(_task(repo, commit), candidate=(candidate,))


@pytest.mark.parametrize(
    "origin,line,source",
    [
        ("tests/conftest.py", 1, f"open({DENIED!r}, 'w')\n"),
        ("tests/conftest.py", 1, f"from pathlib import Path\nPath({DENIED!r}).write_text('x')\n"),
        ("tests/conftest.py", 1, f"from pathlib import Path\nPath({DENIED!r}).mkdir()\n"),
    ],
    ids=["ac1", "ac1c-write-text", "ac1c-mkdir"],
)
def test_r2_accepts_the_specified_literal_writers(repo, origin, line, source):
    commit = commit_change(
        repo,
        {"tests/conftest.py": source, "tests/test_a.py": "def test_a(): pass\n"},
    )
    actual_line = 2 if "Path(" in source and line == 1 else line
    pair = (_run(origin=origin, line=actual_line),)
    assert _classify(_task(repo, commit), candidate=pair, control=pair)


@pytest.mark.parametrize(
    "source",
    [
        f"import os\nos.makedirs({DENIED!r})\n",
        f"import os\nos.open({DENIED!r}, os.O_WRONLY | os.O_CREAT)\n",
        f"import logging\nlogging.FileHandler({DENIED!r})\n",
        f"open({DENIED!r}, 'w')\n",
    ],
    ids=["makedirs", "os-open", "file-handler", "open"],
)
def test_r2_supports_literal_writer_forms(repo, source):
    commit = commit_change(repo, {"tests/conftest.py": source})
    line = 2 if source.startswith(("import", "from")) else 1
    pair = (_run(line=line),)
    assert _classify(_task(repo, commit), candidate=pair, control=pair)


def test_accepted_single_line_synthetic_permission_error_residual(repo):
    source = (
        "def held():\n    "
        f"open({DENIED!r}, 'w', opener=lambda p, f: "
        f"(_ for _ in ()).throw(PermissionError(1, 'x', p)))\n"
    )
    commit = commit_change(repo, {"tests/conftest.py": source})
    pair = (_run(line=2),)
    assert _classify(_task(repo, commit), candidate=pair, control=pair)


def test_ac1d_test_commit_control_and_candidate_nodes_compare_per_node(control_tree):
    repo, commit = control_tree
    task = _task(repo, commit)
    task.test_commit = commit
    control = _run(
        outcomes={NODE: "failed", "tests/test_a.py::test_fixed": "failed"},
        setup_denied={NODE: (DENIED, "tests/conftest.py", 1)},
        fail_kind={NODE: "setup", "tests/test_a.py::test_fixed": "setup"},
    )
    assert _classify(task, control=(control,))


@pytest.mark.parametrize(
    "origin,source,allowed",
    [
        ("src/app.py", f"open({DENIED!r}, 'w')\n", ("src/app.py",)),  # AC2
        ("src/app.py", f"import os\nos.makedirs({DENIED!r})\n", ("src/app.py",)),  # AC2b
        ("src/storage.py", f"open({DENIED!r}, 'w')\n", ("src/app.py",)),  # AC2c
        ("tests/conftest.py", "open(dynamic_path(), 'w')\n", ("src/app.py",)),  # AC2d
        (
            "tests/conftest.py",
            f"raise PermissionError(1, 'x', {DENIED!r})\n",
            ("src/app.py",),
        ),  # AC2e
    ],
    ids=["ac2", "ac2b", "ac2c", "ac2d", "ac2e"],
)
def test_allowed_or_nonliteral_or_synthetic_writer_fails_r2(repo, origin, source, allowed):
    files = {origin: source, "tests/test_a.py": "def test_a(): pass\n"}
    commit = commit_change(repo, files)
    line = 2 if source.startswith(("import", "from")) else 1
    task = _task(repo, commit, allowed=allowed)
    assert not _classify(task, candidate=(_run(origin=origin, line=line),))


def test_ac2c_nonallowed_src_helper_is_not_test_origin(repo):
    commit = commit_change(repo, {"src/storage.py": f"open({DENIED!r}, 'w')\n"})
    assert not _classify(_task(repo, commit), candidate=(_run(origin="src/storage.py"),))


def test_n6_allowed_file_constant_is_not_a_literal_path(repo):
    source = "from app import CACHE_PATH\nopen(CACHE_PATH, 'w')\n"
    commit = commit_change(repo, {"tests/conftest.py": source})
    pair = (_run(line=2),)
    assert not _classify(_task(repo, commit), candidate=pair, control=pair)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mutated", ("src/app.py",)),
        ("forged", ("cache.pyc",)),
        ("survivors", 1),
        ("timed_out", True),
        ("log_too_big", True),
        ("report_too_big", True),
        ("data_refused", ("tests/data.py",)),
        ("not_run", ("tests/test_a.py",)),
        ("teardown_errors", (NODE,)),
        ("collect_skipped", ("tests/test_a.py",)),
        ("recorded", False),
        ("collection_finished", False),
        ("renamed", (NODE,)),
        ("why", "process exit status 1 disagrees with the recorded session status"),
    ],
)
def test_r0b_candidate_or_control_not_clean_fails(control_tree, field, value):
    repo, commit = control_tree
    dirty = _run(**{field: value})
    assert not _classify(_task(repo, commit), candidate=(dirty,))
    assert not _classify(_task(repo, commit), control=(dirty,))


def test_r1_missing_node_or_different_path_fails(control_tree):
    repo, commit = control_tree
    absent = _run(setup_denied={})
    other_path = _run(path="/private/tmp/other")
    assert not _classify(_task(repo, commit), candidate=(absent,))
    assert not _classify(_task(repo, commit), control=(other_path,))


def test_r1_candidate_path_must_equal_the_control_literal(control_tree):
    """#1030 r1 (M4): the control line writes the literal, the candidate's denial is elsewhere."""
    repo, commit = control_tree
    assert not _classify(_task(repo, commit), candidate=(_run(path="/private/tmp/other"),))


def test_r2_an_allowed_file_under_a_test_root_is_not_a_test_origin(control_tree):
    """#1030 r1 (M2): tests/conftest.py writes the literal on the control, but it is allowed."""
    repo, commit = control_tree
    allowed = ("src/app.py", "tests/conftest.py")
    assert not _classify(_task(repo, commit, allowed=allowed))


def test_r2_candidate_origin_must_equal_control_origin(control_tree):
    repo, commit = control_tree
    assert not _classify(_task(repo, commit), candidate=(_run(origin="tests/helper.py"),))


def test_ac7_nonverifier_failed_observation_keeps_failed(control_tree):
    repo, commit = control_tree
    assert not _classify(_task(repo, commit), other=(SimpleNamespace(passed=False),))


def test_ac6_a_matched_denial_with_deferred_tests_not_run_stays_failed(control_tree):
    repo, commit = control_tree
    candidate = _run(not_run=("tests/test_a.py",))
    assert not _classify(_task(repo, commit), candidate=(candidate,))


def test_r3_control_error_preserves_step_one_failed_reason(monkeypatch, control_tree):
    repo, commit = control_tree
    candidate = (_run(),)
    result = SimpleNamespace(attempt_id="attempt", logs=[], reason="step-one reason")
    ctx = SimpleNamespace(account=object())

    def explode(*args):
        raise OSError("control unavailable")

    monkeypatch.setattr(candidate_env, "run_control", explode)
    candidate_env.decide(_task(repo, commit), candidate, (candidate[0].observation,), ctx, result)
    assert result.reason == "step-one reason"
    assert result.logs == ["candidate environment control run failed: OSError"]


def test_r0_control_tree_prefers_test_commit():
    task = SimpleNamespace(base_commit="base", test_commit="tests")
    assert candidate_env.control_tree(task) == "tests"


def test_only_candidate_env_uses_its_own_json_rule():
    assert candidate_env.uncomparable_rule(CODE) == RULE
    assert candidate_env.uncomparable_rule("BASE_ENV_ERROR") == "base-compare-v1"


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        (DENIED, "", 1),
        (DENIED, "../bad.py", 1),
        ("relative", "tests/conftest.py", 1),
        (DENIED, "tests/conftest.py", True),
    ],
)
def test_malformed_setup_records_fail_closed(control_tree, record):
    repo, commit = control_tree
    bad = _run(setup_denied={} if record is None else {NODE: record})
    assert not _classify(_task(repo, commit), candidate=(bad,))


@pytest.mark.parametrize("shape", ["sbh", "sb2", "ac13c-wrong"])
def test_accepted_wrong_candidate_shapes_stay_failed(control_tree, shape):
    repo, commit = control_tree
    if shape == "sbh":  # fixture's candidate assertion fails before its denied write
        candidate = _run(setup_denied={}, outcomes={NODE: "failed"})
    elif shape == "sb2":  # candidate itself writes the denied path from allowed source
        candidate = _run(origin="src/app.py")
    else:  # AC13c wrong candidate fails its pre-write fixture assertion
        candidate = _run(setup_denied={})
    assert not _classify(_task(repo, commit), candidate=(candidate,))


@pytest.mark.parametrize("shape", ["sba", "sx", "ac13", "ac13c-correct"])
def test_accepted_candidate_env_residuals_are_uncomparable(control_tree, shape):
    """N12 / AC13: a setup denial can hide what its fixture did before the write.

    For SX the fixture's branch follows unchanged candidate behavior; for SBa it reruns its own
    code object. The check may also only be in the test body (AC13/13b), or run before a denied
    write with a correct candidate (AC13c). These accepted cases never become VERIFIED.
    """
    repo, commit = control_tree
    assert _classify(_task(repo, commit))


def test_ac13b_a_denied_node_beside_a_plain_failure_stays_failed(control_tree):
    """AC13b: the masked node qualifies, but another node fails without a denial -> FAILED."""
    repo, commit = control_tree
    other = "tests/test_a.py::test_b"
    candidate = _run(outcomes={NODE: "failed", other: "failed"}, fail_kind={NODE: "setup"})
    assert not _classify(_task(repo, commit), candidate=(candidate,))


def test_decide_runs_the_control_for_the_failed_specs_only(monkeypatch, control_tree):
    repo, commit = control_tree
    passed = _run("w", observation=SimpleNamespace(passed=True))
    failed = _run()
    seen = []
    monkeypatch.setattr(candidate_env, "run_control", lambda t, specs, c, r: seen.append(specs))
    ctx, result = SimpleNamespace(account=object()), SimpleNamespace(attempt_id="a", logs=[])
    candidate_env.decide(_task(repo, commit), (passed, failed), (), ctx, result)
    assert seen == [(failed.spec,)]


def test_owner_mode_runs_no_control(monkeypatch, control_tree):
    """R0 / AC11: without the isolated account there is no step 2."""
    repo, commit = control_tree
    monkeypatch.setattr(candidate_env, "run_control", lambda *a: pytest.fail("control ran"))
    result = SimpleNamespace(attempt_id="a", logs=[], reason="step-one reason")
    candidate_env.decide(_task(repo, commit), (_run(),), (), SimpleNamespace(account=None), result)
    assert result.reason == "step-one reason"
