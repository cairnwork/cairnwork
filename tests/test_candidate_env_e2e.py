"""#962 step 2b: real Seatbelt report records feed the candidate-env decision."""

import sys
from pathlib import Path

import pytest
from conftest import commit_change, git
from verify_helpers import verify

from cairnwork.base_teeth import base_teeth
from cairnwork.candidate_env import classify
from cairnwork.pipeline import TaskSpec
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = [pytest.mark.sandbox, pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt")]
DENIED = "/private/tmp/cw962-e2e-denied"
FIXTURE = (
    "import pytest\n\n\n@pytest.fixture\ndef held():\n"
    f"    open({DENIED!r}, 'w')\n\n\ndef test_a(held):\n    pass\n"
)
CALL = f"def test_a():\n    open({DENIED!r}, 'w')\n"


def _runs(
    repo, root: Path, test_source: str, candidate_source: str, base_source="def write(): pass\n"
):
    base = commit_change(
        repo,
        {
            "src/app.py": base_source,
            "tests/conftest.py": (
                "import pathlib, sys\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
            ),
            "tests/test_a.py": "def test_a(): pass\n"
            if "conftest.py" in test_source
            else test_source,
        },
    )
    git(repo, "checkout", "-q", "--detach", base)
    candidate = commit_change(repo, {"src/app.py": candidate_source})
    git(repo, "checkout", "-q", "main")
    task = TaskSpec(
        "e2e",
        "card",
        "title",
        "instructions",
        repo,
        base,
        ("src/app.py",),
        (("v", ("python3", "-m", "pytest", "-q", "tests/test_a.py")),),
    )
    specs = tuple(parse_verifier(vid, list(argv), task.test_roots) for vid, argv in task.verifiers)
    ctx = VerifyContext.of(task, root.resolve(), "e2e-attempt")
    candidate_run = run_verifiers(ctx, candidate, specs, "e2e-v")[0]
    control_run = run_verifiers(ctx, base, specs, "e2e-c", deny=True)[0]
    return task, candidate_run, control_run


def test_ac1_and_n13_setup_record_reaches_the_decision(tmp_path, repo):
    task, candidate, control = _runs(repo, tmp_path / "runs", FIXTURE, "def write(): return 2\n")
    assert candidate.setup_denied and control.setup_denied
    assert classify((candidate,), (control,), (candidate.observation,), task)


def test_ac1b_call_phase_denial_is_failed(tmp_path, repo):
    task, candidate, control = _runs(repo, tmp_path / "runs", CALL, "def write(): return 2\n")
    assert candidate.setup_denied == control.setup_denied == {}
    assert not classify((candidate,), (control,), (candidate.observation,), task)


HANDLED = (  # #1030 r1 (Claude): the fixture tolerates the denial, then candidate code fails
    "import pytest\nimport app\n\n\n@pytest.fixture\ndef held():\n    try:\n"
    f"        open({DENIED!r}, 'w')\n    except OSError:\n        return app.write()\n\n\n"
    "def test_a(held):\n    assert held == 2\n"
)


def test_a_handled_denial_then_a_failing_candidate_is_failed(tmp_path, repo):
    """The denial is only the context of the candidate's error: no setup record, so FAILED."""
    wrong = "def write():\n    raise ValueError('still broken')\n"
    task, candidate, control = _runs(repo, tmp_path / "runs", HANDLED, wrong)
    assert not candidate.observation.passed and candidate.fail_kind
    assert candidate.setup_denied == {}
    assert not classify((candidate,), (control,), (candidate.observation,), task)


def test_ac2_allowed_writer_stays_failed_and_its_fixed_pair_passes(tmp_path, repo, state):
    write = f"def write():\n    open({DENIED!r}, 'w')\n"
    task, wrong, control = _runs(
        repo,
        tmp_path / "wrong",
        "from app import write\n\ndef test_a():\n    write()\n",
        write + "# the candidate leaves the write in place\n",
        base_source=write,
    )
    assert not wrong.observation.passed
    assert not classify((wrong,), (control,), (wrong.observation,), task)

    git(repo, "checkout", "-q", "--detach", task.base_commit)
    fixed = commit_change(repo, {"src/app.py": "def write(): return None\n"})
    git(repo, "checkout", "-q", "main")
    fixed_run = run_verifiers(
        VerifyContext.of(task, (tmp_path / "fixed").resolve(), "e2e-fixed"),
        fixed,
        (parse_verifier("v", ["python3", "-m", "pytest", "-q", "tests/test_a.py"], ("tests",)),),
        "e2e-fixed-v",
    )[0]
    assert fixed_run.observation.passed
    code, result = verify(
        state,
        repo,
        task.base_commit,
        fixed,
        allowed=("src/app.py",),
        tests=("python3 -m pytest -q tests/test_a.py",),
    )
    assert code == 0 and result["status"] == "VERIFIED", result["reason"]


def test_ac3_hand_raised_permission_error_has_no_setup_evidence(tmp_path, repo):
    fake = f"def test_a():\n    raise PermissionError(1, 'x', {DENIED!r})\n"
    task, candidate, control = _runs(repo, tmp_path / "runs", fake, "def write(): return 2\n")
    assert candidate.setup_denied == {}
    assert not classify((candidate,), (control,), (candidate.observation,), task)


def test_ac4_base_env_error_classification_is_unchanged(tmp_path, repo):
    task, _candidate, control = _runs(repo, tmp_path / "runs", CALL, "def write(): return None\n")
    kind, code, _ = base_teeth((control,), frozenset({"tests/test_a.py::test_a"}))
    assert (kind, code) == ("UNCOMPARABLE", "BASE_ENV_ERROR")


def test_ac5_seatbelt_profile_is_present_and_setup_residual_stays_uncomparable(tmp_path, repo):
    task, candidate, control = _runs(repo, tmp_path / "runs", FIXTURE, "def write(): return 0\n")
    assert candidate.notes == control.notes
    assert classify((candidate,), (control,), (candidate.observation,), task)


@pytest.mark.parametrize(
    "candidate_source",
    [
        "def fallback_dir():\n    changed = True\n    raise\n",
        "import sys\ndef fallback_dir():\n    raise sys.exc_info()[1]\n",
        "import sys\ndef fallback_dir():\n    raise RuntimeError('x') from sys.exc_info()[1]\n",
    ],
    ids=["bare-reraise", "raise-current-exception", "candidate-explicit-chain"],
)
def test_ac1_ac2_ac3_candidate_cannot_rethrow_setup_denial(tmp_path, repo, candidate_source):
    test_source = (
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "import pytest, app\n\n\n@pytest.fixture\ndef held():\n    try:\n"
        f"        open({DENIED!r}, 'w')\n"
        "    except OSError:\n        return app.fallback_dir()\n\n\n"
        "def test_a(held):\n    assert held is not None\n"
    )
    base_source = "def fallback_dir():\n    raise\n"
    task, candidate, control = _runs(
        repo, tmp_path / "runs", test_source, candidate_source, base_source=base_source
    )
    assert candidate.setup_denied == {}
    assert not classify((candidate,), (control,), (candidate.observation,), task)


def test_ac4_fixture_chains_denial_after_candidate_query_stays_uncomparable(tmp_path, repo):
    test_source = (
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "import pytest, app\n\n\n@pytest.fixture\ndef held():\n    try:\n"
        f"        open({DENIED!r}, 'w')\n"
        "    except OSError as denied:\n"
        "        if not app.has_fallback():\n"
        "            raise RuntimeError('no cache') from denied\n"
        "        return app.fallback_dir()\n\n\n"
        "def test_a(held):\n    assert held is not None\n"
    )
    base_source = "def has_fallback(): return False\ndef fallback_dir(): return 'cache'\n"
    wrong = "def has_fallback(): return 0\ndef fallback_dir(): return 'cache'\n"
    task, candidate, control = _runs(
        repo, tmp_path / "runs", test_source, wrong, base_source=base_source
    )
    assert candidate.setup_denied and candidate.setup_denied == control.setup_denied
    assert classify((candidate,), (control,), (candidate.observation,), task)


def test_ac5_fixture_bare_reraise_in_same_frame_keeps_setup_record(tmp_path, repo):
    source = (
        "import pytest\n\n\n@pytest.fixture\ndef held():\n    try:\n"
        f"        open({DENIED!r}, 'w')\n"
        "    except OSError:\n        raise\n\n\n"
        "def test_a(held):\n    pass\n"
    )
    task, candidate, control = _runs(repo, tmp_path / "runs", source, "def write(): return 2\n")
    assert candidate.setup_denied == control.setup_denied
    assert classify((candidate,), (control,), (candidate.observation,), task)
