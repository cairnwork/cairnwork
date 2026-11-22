"""The oracle probe in the real sandbox (Issue #1039 step 2): AC2 (owner mode), AC4, AC7.

Account mode is not covered here (it uses the shared verifier account); bwrap runs wherever the
selected backend is bwrap (Linux CI)."""

import json
import os
import socket

import pytest
from oracle_support import BUGGY, EXPECTED, FIXED, PROBE, lay, run, verdict

from experimental.oracle_observer import FAIL, MISMATCH, NO_OUTPUT, PASS
from experimental.oracle_observer.expected import load_expected
from experimental.oracle_observer.run import judge, run_probe

pytestmark = pytest.mark.sandbox


def test_a_correct_implementation_passes_and_a_wrong_one_is_a_mismatch(tmp_path):
    right = verdict(tmp_path / "a", FIXED)
    wrong = verdict(tmp_path / "b", BUGGY)
    assert right.verdict == PASS
    assert (wrong.verdict, wrong.code) == (FAIL, MISMATCH)


def test_a_probe_that_exits_non_zero_after_a_correct_document_is_no_output(tmp_path):
    probe = PROBE + "import os\nos._exit(3)\n"
    result = verdict(tmp_path, FIXED, probe)
    assert (result.verdict, result.code) == (FAIL, NO_OUTPUT)


@pytest.mark.parametrize(
    "probe",
    [
        "raise RuntimeError('crash')\n",
        "import time\ntime.sleep(600)\n",
        "import os\nos._exit(0)\n",  # exits 0 without writing anything
        "observe('a', 1)\nobserve('a', 2)\n",  # duplicate observation key
        "observe('a', float('nan'))\n",  # not JSON
        "observe('a', object())\n",
    ],
)
def test_a_probe_that_crashes_times_out_or_emits_nothing_is_no_output(tmp_path, probe):
    timeout = 5 if "sleep" in probe else 60
    out = run(tmp_path, FIXED, probe, timeout=timeout)
    result = judge(out, load_expected(json.dumps(EXPECTED).encode()))
    assert (result.verdict, result.code) == (FAIL, NO_OUTPUT)


def test_a_candidate_that_forges_the_pytest_report_has_nothing_to_forge(tmp_path):
    """In-process patching of the pytest family cannot matter: no pytest runs, and the runner
    reads only the emitted values (AC1, minimal form; the full classes are in step 3)."""
    cheat = (
        BUGGY + "\nimport sys\nsys.modules['_pytest'] = None\nsys.pycache_prefix = '/nonexistent'\n"
    )
    result = verdict(tmp_path, cheat)
    assert (result.verdict, result.code) == (FAIL, MISMATCH)


def test_the_mismatch_verdict_carries_no_expected_value_key_or_count(tmp_path):
    expected = {"values": {"zq_secret_key_81": 8123456, "other": "q9z8x7"}}
    probe = "observe('zq_secret_key_81', 1)\nobserve('other', 'no')\n"
    out = run(tmp_path, FIXED, probe)
    result = judge(out, load_expected(json.dumps(expected).encode()))
    text = repr(result)  # the raw document is the runner's own; only the verdict leaves it
    assert out.output
    assert result.code == MISMATCH
    for planted in ("8123456", "zq_secret_key_81", "q9z8x7"):
        assert planted not in text
    assert not any(ch.isdigit() for ch in result.code)


def test_the_probe_finds_no_expected_value_anywhere_it_can_reach(tmp_path):
    """AC2 (owner mode): the probe tries the expected file, the runner's private directory, its
    inherited fds, its environment, argv and the run tmp and log dirs, and finds nothing."""
    private = tmp_path / "private"
    private.mkdir()
    marker = "ZZ_ORACLE_MARKER_7731"
    (private / "expected.json").write_text(json.dumps({"values": {"k": marker + "x" * 5}}))
    probe = (
        "import os, sys, glob\n"
        "hits = []\n"
        f"p = {str(private)!r}\n"
        "try:\n    hits.append(open(p + '/expected.json').read())\nexcept OSError:\n    pass\n"
        "try:\n    hits.append(str(os.listdir(p)))\nexcept OSError:\n    pass\n"
        "for fd in range(3, 64):\n"
        "    try:\n        os.fstat(fd)\n        hits.append(f'fd{fd}')\n"
        "    except OSError:\n        pass\n"
        "hits.append(str(dict(os.environ)))\nhits.append(str(sys.argv))\n"
        "for d in (os.environ.get('TMPDIR', ''),):\n"
        "    for f in glob.glob(d + '/**', recursive=True):\n"
        "        if os.path.isfile(f):\n            hits.append(open(f, errors='replace').read())\n"
        "observe('seen', 'ZZ_ORACLE_MARKER_7731' in ''.join(hits))\n"
        "observe('fds', [h for h in hits if h.startswith('fd')])\n"
    )
    checkout, probe_file = lay(tmp_path, {"calc.py": FIXED}, probe)
    work = tmp_path / "oracle"
    work.mkdir()
    out = run_probe(probe_file, checkout, work, timeout=60)
    assert out.output
    doc = json.loads(out.output)
    assert doc["v"]["seen"] is False
    assert doc["v"]["fds"] == []
    # the run is still a FAIL when the implementation is wrong
    wrong = verdict(tmp_path / "w", BUGGY)
    assert wrong.verdict == FAIL


def test_the_probe_has_no_network(tmp_path):
    probe = (
        "import socket\n"
        "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=2)\n    ok = True\n"
        "except OSError:\n    ok = False\n"
        "observe('reached', [ok, 'net-denied-check'])\n"
    )
    out = run(tmp_path, FIXED, probe)
    assert out.output
    assert json.loads(out.output)["v"]["reached"][0] is False
    assert socket and os  # keep the imports used
