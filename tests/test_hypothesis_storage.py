"""Hypothesis keeps its store in a per-run directory, not in the checkout (#273, with C2).

Hypothesis writes ``.hypothesis/`` (examples database, constants cache, a .gitignore) under the
current directory, which for a verifier is the checkout, and the final comparison then fails the
run. Verifier runs set HYPOTHESIS_STORAGE_DIRECTORY inside the run's own TMPDIR, removed with it.
Nothing is ignored by the checkout comparison: a hypothesis that ignored the variable, or a
candidate that writes ``.hypothesis/`` itself, still fails the run.
"""

import sys

import pytest
from conftest import commit_change

from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox
pytest.importorskip("hypothesis")

CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)


def verify(repo, tmp_path, files, *targets, run_id="r1"):
    head = commit_change(repo, {"tests/conftest.py": CONFTEST, **files})
    specs = tuple(
        parse_verifier(f"v{i}", ["python3", "-m", "pytest", target])
        for i, target in enumerate(targets)
    )
    return run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, specs, run_id
    )


def test_a_hypothesis_test_leaves_the_checkout_clean(repo, tmp_path):
    code = (
        "import os\n"
        "from pathlib import Path\n"
        "from hypothesis import given, strategies as st\n"
        "@given(st.integers())\n"
        "def test_prop(n):\n"
        "    assert n == n\n"
        "def test_store_is_per_run():\n"
        "    store = Path(os.environ['HYPOTHESIS_STORAGE_DIRECTORY'])\n"
        "    assert store.parent == Path(os.environ['TMPDIR'])\n"
        "    assert not Path('.hypothesis').exists()\n"
    )
    runs = verify(repo, tmp_path, {"tests/test_prop.py": code}, *["tests/test_prop.py"] * 2)
    for run in runs:
        assert run.observation.passed, (run.why, run.log_path.read_text())
        assert not run.mutated


def test_a_hypothesis_dir_written_into_the_checkout_still_fails_the_run(repo, tmp_path):
    code = (
        "from pathlib import Path\n"
        "def test_plant():\n"
        "    Path('.hypothesis/examples').mkdir(parents=True)\n"
        "    Path('.hypothesis/examples/x').write_bytes(b'x')\n"
    )
    (run,) = verify(repo, tmp_path, {"tests/test_plant.py": code}, "tests/test_plant.py")
    assert run.returncode == 0
    assert not run.observation.passed
    assert any(".hypothesis" in path for path in run.mutated), run.mutated
    assert "checkout changed" in run.why
