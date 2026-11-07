"""#962 step 2a: the guard records a setup-phase denial (EPERM) with the frame that raised it.

Data only: no verdict reads ``setup_denied`` yet (step 2b, candidate_env.py, will). Real pytest
runs of the guard (no sandbox: the EPERM is raised by the test code itself)."""

import sys

import pytest
from conftest import commit_change
from test_teardown_errors import guard_run

from cairnwork.verdict_record import setup_denials
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

DENIED = "/private/tmp/cw962-setup-probe"
RAISE = f"raise PermissionError(1, 'Operation not permitted', {DENIED!r})"
CONFTEST = f"import pytest\n\n\n@pytest.fixture\ndef held():\n    {RAISE}\n"  # line 6
TEST = "def test_uses(held):\n    pass\n"
NODE = "tests/test_a.py::test_uses"


def test_a_setup_denial_records_path_origin_and_line(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/conftest.py": CONFTEST, "tests/test_a.py": TEST}, "tests")
    assert rc == 1 and v.fail_kind == {NODE: "setup"}
    assert v.setup_denied == {NODE: (DENIED, "tests/conftest.py", 6)}
    assert v.denied == {}  # step 1's call-phase map and its wording are unchanged


def test_the_origin_is_the_innermost_frame_in_the_checkout(tmp_path):
    helper = f"def write():\n    {RAISE}\n"  # line 2
    conftest = "import pytest\nfrom helper import write\n\n\n@pytest.fixture\n"
    conftest += "def held():\n    write()\n"
    files = {"tests/helper.py": helper, "tests/conftest.py": conftest, "tests/test_a.py": TEST}
    rc, v = guard_run(tmp_path, files, "tests")
    assert v.setup_denied == {NODE: (DENIED, "tests/helper.py", 2)}


def test_a_lambda_or_comprehension_origin_is_empty(tmp_path):
    """The innermost frame in the checkout is a generator expression: no origin (R2 fails)."""
    body = f"    (lambda: (_ for _ in ()).throw(PermissionError(1, 'x', {DENIED!r})))()\n"
    conftest = "import pytest\n\n\n@pytest.fixture\ndef held():\n" + body
    rc, v = guard_run(tmp_path, {"tests/conftest.py": conftest, "tests/test_a.py": TEST}, "tests")
    assert v.setup_denied == {NODE: (DENIED, "", 6)}


@pytest.mark.parametrize(
    "where",
    [
        "def test_uses():\n    " + RAISE + "\n",  # the call phase: step 1's map only
        "import pytest\n\n\n@pytest.fixture\ndef held():\n    yield\n    " + RAISE + "\n\n\n"
        "def test_uses(held):\n    pass\n",  # teardown
    ],
    ids=["call", "teardown"],
)
def test_only_the_setup_phase_is_recorded(tmp_path, where):
    rc, v = guard_run(tmp_path, {"tests/test_a.py": where}, "tests")
    assert rc == 1 and v.setup_denied == {}


def test_another_setup_error_records_nothing(tmp_path):
    other = CONFTEST.replace("PermissionError(1,", "PermissionError(13,")  # EACCES: file modes
    rc, v = guard_run(tmp_path, {"tests/conftest.py": other, "tests/test_a.py": TEST}, "tests")
    assert v.fail_kind == {NODE: "setup"} and v.setup_denied == {}


def test_a_checkout_reached_through_a_link_gives_the_same_origin(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    files = {"tests/conftest.py": CONFTEST, "tests/test_a.py": TEST}
    rc, v = guard_run(tmp_path / "link", files, "tests")
    assert v.setup_denied == {NODE: (DENIED, "tests/conftest.py", 6)}


def test_the_origin_is_found_through_a_link_to_the_checkout(tmp_path):
    """The checkout is named through a link, the frame by its real path: both are resolved."""
    import os
    from types import SimpleNamespace

    from cairnwork.report_guard_parts import PARTS_SOURCE

    (tmp_path / "real" / "tests").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    plugin: dict = {"os": os}
    exec(PARTS_SOURCE, plugin)  # plugin text, run as the guard runs it
    held: dict = {}
    real = str(tmp_path / "real/tests/conftest.py")
    exec(compile(f"def held():\n    {RAISE}\n", real, "exec"), held)
    with pytest.raises(PermissionError) as info:
        held["held"]()
    origin = plugin["_denied_origin"](SimpleNamespace(value=info.value), tmp_path / "link")
    assert origin == [DENIED, "tests/conftest.py", 2]


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt denials")
def test_a_real_denial_in_setup_reaches_the_verifier_run(repo, tmp_path):
    """Real Seatbelt: a conftest fixture writes a fixed path outside the write roots."""
    write = f"import pytest\n\n\n@pytest.fixture\ndef held():\n    open({DENIED!r}, 'w').close()\n"
    head = commit_change(repo, {"tests/conftest.py": write, "tests/test_a.py": TEST})
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py"], ("tests",))
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    (run,) = run_verifiers(ctx, head, (spec,), "r1")
    assert run.fail_kind == {NODE: "setup"}, run.why
    assert run.setup_denied == {NODE: (DENIED, "tests/conftest.py", 6)}


SETUP = {"n": "setup"}


@pytest.mark.parametrize(
    "found",
    [
        {"n": [DENIED, "tests/conftest.py", True]},  # bool is no line
        {"n": [DENIED, "tests/conftest.py", -1]},
        {"n": [DENIED, "/abs/conftest.py", 6]},
        {"n": ["relative", "tests/conftest.py", 6]},
        {"n": [DENIED, "tests/conftest.py"]},
        {"n": (DENIED, "tests/conftest.py", 6)},  # JSON gives lists only
        {"n": [DENIED, None, 6]},
        {"n": ["/" + "x" * 500, "tests/conftest.py", 6]},
        {"other": [DENIED, "tests/conftest.py", 6]},  # not a setup node
        [[DENIED, "tests/conftest.py", 6]],
    ],
)
def test_any_malformed_entry_drops_the_whole_map(found):
    ok = {"ok": [DENIED, "c.py", 1]}
    data = {"setup_denied": {**found, **ok} if isinstance(found, dict) else found}
    assert setup_denials(data, {**SETUP, "ok": "setup"}) == {}


def test_a_well_formed_map_is_kept():
    data = {"setup_denied": {"n": [DENIED, "", 0]}}
    assert setup_denials(data, SETUP) == {"n": (DENIED, "", 0)}
    assert setup_denials({}, SETUP) == {}
