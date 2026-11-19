"""#1397: ``-I -m cairnwork...`` in a test child needs an editable install."""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
# -I drops PYTHONPATH, so "-I -m <package>" only works when the package is installed (editable),
# and then it may be another checkout's code. Use _child_python.run_module instead.
BAD = re.compile(r"""["']-I["']\s*,\s*["']-m["']\s*,\s*["']cairnwork""")


def test_no_isolated_dash_m_cairnwork_in_tests():
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(HERE.glob("*.py"))
        if path.name != Path(__file__).name
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if BAD.search(line)
    ]
    assert not offenders, f"use _child_python.run_module, not -I -m cairnwork: {offenders}"


def test_guard_pattern_matches_the_old_shape():
    assert BAD.search('[sys.executable, "-I", "-m", "cairnwork_attest.cli", *args]')
    assert not BAD.search('[sys.executable, "-I", "-B", "-c", launcher]')
