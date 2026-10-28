"""#384, #390 end to end: ``check_task`` refuses a forged oracle in an installed namespace or in
an editable package's place, accepts an honest fix next to it, and the refused shape is a real
false green under the sandboxed verifier when nothing refuses it.

The project's tests compare ``compute`` with a reference the verifier interpreter provides
(``nsco.somelib.oracle``, a PEP 420 namespace; ``mylib.oracle``, found only by an editable
install's meta-path finder). The base's ``pytest.ini`` sets ``pythonpath = src``, so the gate's
import roots come from the base config, as in a real task.
"""

import sys

import pytest
from conftest import commit_change
from namespace_site import make_site, site_dir

from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

TARGET = "tests/test_compute.py"
VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", TARGET)),)
BUG = "def compute(x):\n    return x * x + 1\n"
FIX = "def compute(x):\n    return x * x\n"
FORGED = "def reference(x):\n    return x * x + 1\n"
ORACLES = {"namespace": "nsco.somelib.oracle", "editable": "mylib.oracle"}


@pytest.fixture(scope="module")
def python(tmp_path_factory):
    return make_site(tmp_path_factory.mktemp("e2e"), pytest_from=str(site_dir(sys.executable)))


def project(repo, oracle):
    test = (
        "from myproj.compute import compute\n"
        f"from {oracle} import reference\n\n"
        "def test_compute():\n"
        "    assert compute(3) == reference(3)\n"
    )
    files = {
        "pytest.ini": "[pytest]\npythonpath = src\n",
        "src/myproj/__init__.py": "",
        "src/myproj/compute.py": BUG,
        TARGET: test,
    }
    return commit_change(repo, files, message="base")


def forged_path(oracle):
    return "src/" + oracle.replace(".", "/") + ".py"


def task(repo, base, allowed):
    return TaskSpec("key", "card", "title", "fix", repo, base, allowed, VERIFY)


@pytest.mark.parametrize("oracle", ORACLES.values(), ids=ORACLES.keys())
def test_check_task_refuses_a_forged_oracle(repo, python, oracle):
    base = project(repo, oracle)
    forged = forged_path(oracle)
    with pytest.raises(TaskRejected, match=f"PROTECTED_VERIFIER_INPUT: {forged}$"):
        check_task(task(repo, base, ("src/myproj/compute.py", forged)), verifier_python=python)


def test_check_task_accepts_a_fix_with_an_own_namespace_portion(repo, python):
    base = project(repo, ORACLES["namespace"])
    allowed = ("src/myproj/compute.py", "src/nsco/myproj/helper.py")
    assert check_task(task(repo, base, allowed), verifier_python=python)


def run(repo, tmp_path, python, base, head, name):
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "-q", TARGET])
    (result,) = run_verifiers(
        repo, head, (spec,), tmp_path / name, name, python=python, base_commit=base
    )
    return result


@pytest.mark.sandbox
@pytest.mark.parametrize("oracle", ORACLES.values(), ids=ORACLES.keys())
def test_the_refused_shape_is_a_reachable_false_green(repo, tmp_path, python, oracle):
    base = project(repo, oracle)
    unfixed = run(repo, tmp_path, python, base, base, "unfixed")
    assert not unfixed.observation.passed, unfixed.log_path.read_text()
    forged = commit_change(repo, {forged_path(oracle): FORGED})
    shadowed = run(repo, tmp_path, python, base, forged, "forged")
    assert shadowed.observation.passed, shadowed.log_path.read_text()


@pytest.mark.sandbox
def test_the_accepted_shape_passes_only_when_fixed(repo, tmp_path, python):
    base = project(repo, ORACLES["namespace"])
    head = commit_change(repo, {"src/myproj/compute.py": FIX, "src/nsco/myproj/helper.py": ""})
    fixed = run(repo, tmp_path, python, base, head, "fixed")
    assert fixed.observation.passed, fixed.log_path.read_text()
