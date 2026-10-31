"""Council action 3, item C2: bytecode goes to a per-run cache prefix, and forged bytecode fails.

A test that runs ``python3 -m py_compile`` used to write ``__pycache__`` into the checkout, and
the final comparison failed the run. Verifier runs now set PYTHONPYCACHEPREFIX inside the run's
own TMPDIR. Python and pytest also *read* bytecode from that prefix (pytest's assertion rewriter
loads ``<prefix>/<dir>/<name>.<tag>-pytest-<ver>.pyc`` whenever the header matches the source's
mtime and size, even with PYTHONDONTWRITEBYTECODE set), and the prefix is outside the checkout
comparison. The counterexamples below plant such bytecode from candidate library code and must
fail the run with FORGED_BYTECODE (under Seatbelt the planted bytecode is not even read, #368).
All of them run the real sandbox through run_verifiers.
"""

import os
import sys

import pytest
from conftest import commit_change, git

from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox
# pytest's exit code once the forgery is planted: 1 on both platforms, because the verifier's pytest
# process refuses to load bytecode from files (bytecode_hook.py; Seatbelt also denies reading it
# under a write root, #368), so the real test fails as written. The scan must name the planted file.
PLANTED_RC = 1

CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
# Candidate library code: writes `code` as the cached bytecode of `target` (a path relative to
# the checkout), as a pytest-rewritten pyc (pytest_pyc) or a plain one, under the cache prefix;
# upper_tests spells the tests/ directory Tests/ in the cache path, upper_name the file name in
# upper case, long_s every "s" below tests/ (and tests/ itself) as U+017F, which APFS folds to "s".
FORGE = (
    "import importlib.util, marshal, os, sys\n"
    "from pathlib import Path\n"
    "def forge(target, code, pytest_pyc, upper_tests=False, upper_name=False, long_s=False):\n"
    "    import pytest\n"
    "    root = Path(__file__).parent.parent\n"
    "    source = Path(target) if target.startswith('/') else root / target\n"
    "    st = os.stat(source)\n"
    "    header = (importlib.util.MAGIC_NUMBER + bytes(4)\n"
    "              + (int(st.st_mtime) & 0xFFFFFFFF).to_bytes(4, 'little')\n"
    "              + (st.st_size & 0xFFFFFFFF).to_bytes(4, 'little'))\n"
    "    data = header + marshal.dumps(compile(code, str(source), 'exec'))\n"
    "    tag = f'{sys.implementation.cache_tag}-pytest-{pytest.__version__}'\n"
    "    for path in {str(source), os.path.realpath(source)}:\n"
    "        pyc = importlib.util.cache_from_source(path)\n"
    "        if pytest_pyc:\n"
    "            pyc = str(Path(pyc).parent / (Path(path).name[:-3] + f'.{tag}.pyc'))\n"
    "        if long_s:\n"
    "            head, tail = pyc.split('/tests/', 1)\n"
    "            pyc = head + '/te\\u017fts/' + tail.replace('s', '\\u017f')\n"
    "        if upper_name:\n"
    "            pyc = os.path.join(os.path.dirname(pyc), os.path.basename(pyc).upper())\n"
    "        if upper_tests:\n"
    "            pyc = pyc.replace('/tests/', '/Tests/')\n"
    "        assert sys.pycache_prefix and pyc.startswith(sys.pycache_prefix), pyc\n"
    "        os.makedirs(os.path.dirname(pyc), exist_ok=True)\n"
    "        Path(pyc).write_bytes(data)\n"
)


@pytest.fixture
def project(repo):
    commit_change(repo, {"tests/conftest.py": CONFTEST, "src/forger.py": FORGE})
    return repo


def verify(repo, tmp_path, files, *targets, run_id="r1"):
    head = commit_change(repo, files) if files else git(repo, "rev-parse", "HEAD")
    specs = tuple(
        parse_verifier(f"v{i}", ["python3", "-m", "pytest", target])
        for i, target in enumerate(targets)
    )
    return run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, specs, run_id
    )


def test_py_compile_no_longer_changes_the_checkout(project, tmp_path):
    code = (
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "def test_compile():\n"
        "    done = subprocess.run([sys.executable, '-m', 'py_compile', 'src/a.py'],\n"
        "                          capture_output=True, text=True)\n"
        "    assert done.returncode == 0, done.stderr\n"
        "    assert not Path('src/__pycache__').exists()\n"
        "    prefix = Path(os.environ['PYTHONPYCACHEPREFIX'])\n"
        "    assert list(prefix.rglob('a.*.pyc')), sorted(prefix.rglob('*'))\n"
    )
    (run,) = verify(project, tmp_path, {"tests/test_compile.py": code}, "tests/test_compile.py")
    assert run.observation.passed, (run.why, run.log_path.read_text())
    assert not run.mutated


def test_the_prefix_is_per_run_and_never_shared(project, tmp_path):
    """Each verifier run gets a fresh prefix inside its own TMPDIR: the second run must not see
    what the first wrote, and neither may find anything there when it starts."""
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "def test_fresh():\n"
        "    prefix = os.environ['PYTHONPYCACHEPREFIX']\n"
        "    assert sys.pycache_prefix == prefix\n"
        "    assert Path(prefix).parent == Path(os.environ['TMPDIR'])\n"
        "    assert not Path(prefix).exists() or not any(Path(prefix).iterdir())\n"
        "    Path(prefix).mkdir(parents=True, exist_ok=True)\n"
        "    (Path(prefix) / 'seen.txt').write_text(prefix)\n"
    )
    files = {"tests/test_fresh.py": code}
    first = verify(project, tmp_path, files, "tests/test_fresh.py", "tests/test_fresh.py")
    second = verify(project, tmp_path, {}, "tests/test_fresh.py", run_id="r2")
    for run in (*first, *second):
        assert run.observation.passed, (run.why, run.log_path.read_text())


def test_a_forged_pytest_pyc_for_a_protected_test_fails_the_run(project, tmp_path):
    """The deferred test fails as written; candidate library code imported by an earlier test
    plants a rewritten pyc that replaces it. pytest really runs the forgery (exit 0), and the
    guard must still fail the run."""
    files = {
        "src/evil.py": (
            "from forger import forge\n"
            "forge('tests/test_z.py', 'def test_z():\\n    pass\\n', pytest_pyc=True)\n"
        ),
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "def test_z():\n    assert False, 'the real deferred test'\n",
    }
    head = commit_change(project, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (run,) = run_verifiers(
        VerifyContext(project, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed and not run.plain_failure
    assert "FORGED_BYTECODE" in run.why and "tests/test_z" in run.why, run.why


def test_an_upper_case_pytest_pyc_name_fails_the_run_and_is_bound(project, tmp_path, monkeypatch):
    """Case-insensitive APFS: pytest opening test_z.<tag>-pytest-<v>.pyc finds TEST_Z.<TAG>-
    PYTEST-<V>.PYC. The findings are also bound into the observation's digest."""
    import cairnwork.verifiers as verifiers

    bound = []
    real = verifiers._record_digest
    monkeypatch.setattr(verifiers, "_record_digest", lambda **f: bound.append(f) or real(**f))
    files = {
        "src/evil.py": (
            "from forger import forge\n"
            "forge('tests/test_z.py', 'def test_z():\\n    pass\\n', True, upper_name=True)\n"
        ),
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "def test_z():\n    assert False, 'the real deferred test'\n",
    }
    head = commit_change(project, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (run,) = run_verifiers(
        VerifyContext(project, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    if os.path.exists(str(tmp_path).upper()):
        assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "PYTEST" in run.why, run.why
    assert len(bound) == 1 and any("PYTEST" in f for f in bound[0]["forged_bytecode"])


@pytest.mark.parametrize("pytest_pyc", [True, False])
def test_a_long_s_spelling_fails_the_run(project, tmp_path, pytest_pyc):
    """APFS folds U+017F onto "s": te\u017fts/te\u017ft_z.<tag>-pyte\u017ft-<v>.pyc is found for
    tests/test_z.py, and te\u017fts/helper.<tag>.pyc for tests/helper.py."""
    target, code = (
        ("tests/test_z.py", "def test_z():\\n    pass\\n")
        if pytest_pyc
        else ("tests/helper.py", "OK = True\\n")
    )
    files = {
        "src/evil.py": (
            f"from forger import forge\nforge({target!r}, '{code}', {pytest_pyc}, long_s=True)\n"
        ),
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": (
            "from helper import OK\n\ndef test_z():\n    assert OK, 'the real deferred test'\n"
        ),
    }
    head = commit_change(project, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (run,) = run_verifiers(
        VerifyContext(project, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    if os.path.exists(str(tmp_path).replace("s", "\u017f")):
        assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "non-ASCII name" in run.why, run.why


def test_a_forged_plain_pyc_for_a_protected_helper_fails_the_run(project, tmp_path):
    files = {
        "src/evil.py": (
            "from forger import forge\nforge('tests/helper.py', 'OK = True\\n', False)\n"
        ),
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "from helper import OK\n\ndef test_z():\n    assert OK\n",
    }
    head = commit_change(project, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (run,) = run_verifiers(
        VerifyContext(project, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "tests/helper" in run.why, run.why


def test_a_case_variant_cache_path_for_a_protected_helper_fails_the_run(project, tmp_path):
    """On case-insensitive APFS the import system finds <prefix>/.../Tests/helper.*.pyc for
    tests/helper.py; the guard must judge Tests/ as tests/."""
    files = {
        "src/evil.py": (
            "from forger import forge\n"
            "forge('tests/helper.py', 'OK = True\\n', False, upper_tests=True)\n"
        ),
        "tests/helper.py": "OK = False\n",
        "tests/test_a.py": "import evil\n\ndef test_a():\n    assert evil\n",
        "tests/test_z.py": "from helper import OK\n\ndef test_z():\n    assert OK\n",
    }
    head = commit_change(project, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (run,) = run_verifiers(
        VerifyContext(project, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    if os.path.exists(str(tmp_path).upper()):
        assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "Tests/helper" in run.why, run.why


def test_forged_bytecode_for_a_module_outside_the_checkout_fails_the_run(project, tmp_path):
    files = {
        "src/evil.py": (
            "import importlib.util\nfrom forger import forge\n"
            "forge(importlib.util.find_spec('colorsys').origin, 'FORGED = True\\n', False)\n"
        ),
        "tests/test_a.py": ("import evil, colorsys\n\ndef test_a():\n    assert colorsys.FORGED\n"),
    }
    (run,) = verify(project, tmp_path, files, "tests/test_a.py")
    assert run.returncode == PLANTED_RC, run.log_path.read_text()
    assert not run.observation.passed
    assert "FORGED_BYTECODE" in run.why and "colorsys" in run.why, run.why
