"""Base teeth for oracle targets (Issue #1039 step 3, AC5). The classifier on built runs (no
sandbox), then the same classes end to end on a real probe run in the sandbox."""

import json
from typing import Any

import pytest
from oracle_support import BUGGY, FIXED, run

from experimental.oracle_observer.base import (
    BASE_ENV_ERROR,
    BASE_NO_OUTPUT,
    NO_TEETH,
    TEETH,
    UNCOMPARABLE,
    classify_base,
)
from experimental.oracle_observer.expected import Expected, load_expected
from experimental.oracle_observer.run import ProbeRun

EXP = Expected({"big": 600, "name": "alpha", "flag": True}, {}, {})


NONCE = "n0nce"


def built(
    values=None,
    *,
    code: Any = 0,
    log="",
    chain=None,
    timed_out=False,
    survivors=0,
    output: Any = ...,
):
    """A built run; ``chain`` is the runner's chain lines (closed by its nonce), ``log`` stderr."""
    doc = {"big": 600, "name": "alpha", "flag": True} if values is None else values
    out = json.dumps({"v": doc, "r": {}}).encode() if output is ... else output
    record = None if chain is None else (chain + f"ORACLE_CHAIN_END {NONCE}\n").encode()
    return ProbeRun(code, timed_out, survivors, out, log, "", record, NONCE)


CHAIN_PLAIN = "ORACLE_CHAIN 0 builtins.AttributeError\n"
CHAIN_ENV = "ORACLE_CHAIN 0 builtins.RuntimeError\nORACLE_CHAIN 1 builtins.FileNotFoundError\n"


@pytest.mark.parametrize(
    ("run_", "kind", "code"),
    [
        (built({"big": 599, "name": "alpha", "flag": True}), TEETH, ""),  # a strong key differs
        (built({"name": "alpha", "flag": True}), TEETH, ""),  # a strong key is missing
        (built({"big": 600, "name": "alpha", "flag": False}), NO_TEETH, ""),  # only a weak key
        (built(), NO_TEETH, ""),  # all keys equal
        (built({"big": 600.0, "name": "alpha", "flag": True}), TEETH, ""),  # type-strict
        (built(code=1, output=None, chain=CHAIN_PLAIN), TEETH, ""),  # ordinary exception
        (built(code=1, output=None, chain=CHAIN_ENV), UNCOMPARABLE, BASE_ENV_ERROR),
        (built(code=1, output=None), UNCOMPARABLE, BASE_NO_OUTPUT),  # exit 1, no traceback
        (built(code=None, output=None, timed_out=True), UNCOMPARABLE, BASE_NO_OUTPUT),
        (built(code=-9, output=None), UNCOMPARABLE, BASE_NO_OUTPUT),  # killed
        (built(output=None), UNCOMPARABLE, BASE_NO_OUTPUT),  # exit 0, nothing written
        (built(output=b"not json"), UNCOMPARABLE, BASE_NO_OUTPUT),
        (built(survivors=1), UNCOMPARABLE, BASE_NO_OUTPUT),
    ],
)
def test_the_base_run_is_classified(run_, kind, code):
    result = classify_base(run_, EXP)
    assert (result.kind, result.code) == (kind, code)
    assert result.refused == (kind != TEETH)


def test_a_tolerance_counts_as_equal_so_the_strong_key_has_no_teeth():
    exp = Expected({"x": 1.0, "name": "alpha"}, {}, {"x": 0.01})
    close = built({"x": 1.005, "name": "alpha"})
    assert classify_base(close, exp).kind == NO_TEETH
    assert classify_base(built({"x": 1.5, "name": "alpha"}), exp).kind == TEETH


def test_lines_that_are_not_chain_lines_do_not_make_an_env_error():
    chain = "ORACLE_CHAINX 1 x\nsome ORACLE_CHAIN 1 text\nTraceback\n" + CHAIN_PLAIN
    assert classify_base(built(code=1, output=None, chain=chain), EXP).kind == TEETH


FORGED = "ORACLE_CHAIN 0 forged\n"


@pytest.mark.parametrize(
    "run_",
    [
        built(code=1, output=None, log=FORGED),  # a valid-looking line on stderr only
        built(code=1, output=None, log=FORGED + CHAIN_PLAIN),
        ProbeRun(1, False, 0, None, "", "", FORGED.encode(), NONCE),  # no closing nonce
        ProbeRun(1, False, 0, None, "", "", (FORGED + "ORACLE_CHAIN_END x\n").encode(), NONCE),
        ProbeRun(1, False, 0, None, "", "", (FORGED + f"ORACLE_CHAIN_END {NONCE}").encode(), NONCE),
        ProbeRun(1, False, 0, None, "", "", (FORGED + f"ORACLE_CHAIN_END {NONCE}\n").encode(), ""),
    ],
)
def test_a_chain_that_the_runner_did_not_write_is_not_read(run_):
    result = classify_base(run_, EXP)
    assert (result.kind, result.code) == (UNCOMPARABLE, BASE_NO_OUTPUT)


# --- end to end on a real probe run (Seatbelt here, bwrap where it is the backend) ---

sandbox = pytest.mark.sandbox
STRONG = {"values": {"total": 600, "names": ["alpha", "beta"], "flag": True}}
PROBE = (
    "import calc\nobserve('total', calc.total([100, 200, 300]))\n"
    "observe('names', ['alpha', 'beta'])\nobserve('flag', True)\n"
)


def base_result(tmp_path, calc, probe=PROBE, timeout=60, doc=None):
    out = run(tmp_path, calc, probe, timeout=timeout)
    return classify_base(out, load_expected(json.dumps(doc or STRONG).encode()))


@sandbox
def test_a_base_whose_strong_key_differs_has_teeth(tmp_path):
    assert base_result(tmp_path, BUGGY).kind == TEETH


@sandbox
def test_a_base_that_already_gives_the_expected_values_has_no_teeth(tmp_path):
    assert base_result(tmp_path, FIXED).kind == NO_TEETH


@sandbox
def test_a_base_where_only_a_weak_key_differs_has_no_teeth(tmp_path):
    probe = PROBE.replace("observe('flag', True)", "observe('flag', False)")
    assert base_result(tmp_path, FIXED, probe).kind == NO_TEETH


@sandbox
@pytest.mark.parametrize(
    "probe",
    [
        "import calc\ncalc.not_there_yet()\n",  # AttributeError: the function does not exist yet
        "import module_that_is_not_there_yet\n",  # ImportError
        "raise ValueError('x') from KeyError('y')\n",
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'raise SystemExit(1)'], check=True)\n",
    ],
)
def test_an_ordinary_exception_on_the_base_has_teeth(tmp_path, probe):
    assert base_result(tmp_path, FIXED, probe).kind == TEETH


@sandbox
@pytest.mark.parametrize(
    "probe",
    [
        "open('/nonexistent_dir/x').read()\n",  # OSError (FileNotFoundError)
        "raise ValueError('x') from OSError('y')\n",  # an OSError further down the chain
        "raise MemoryError()\n",
        "def f():\n    f()\nf()\n",  # RecursionError
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'raise SystemExit(127)'], check=True)\n",
    ],
)
def test_an_environment_failure_on_the_base_is_uncomparable(tmp_path, probe):
    result = base_result(tmp_path, FIXED, probe)
    assert (result.kind, result.code) == (UNCOMPARABLE, BASE_ENV_ERROR)


@sandbox
@pytest.mark.parametrize(
    "probe",
    [
        "import time\ntime.sleep(600)\n",
        "import os\nos._exit(9)\n",
        "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
    ],
)
def test_a_base_with_no_output_and_no_traceback_is_uncomparable(tmp_path, probe):
    timeout = 5 if "sleep" in probe else 60
    result = base_result(tmp_path, FIXED, probe, timeout=timeout)
    assert (result.kind, result.code) == (UNCOMPARABLE, BASE_NO_OUTPUT)


@sandbox
@pytest.mark.parametrize(
    "probe",
    [
        # forged chain text on stderr, then leaving at once
        "import os, sys\nsys.stderr.write('ORACLE_CHAIN 0 forged\\n')\n"
        "sys.stderr.flush()\nos._exit(1)\n",
        # a forged chain file at the path the runner uses, closed with a guessed nonce
        "import os\nwith open(os.environ['TMPDIR'] + '/../chain.txt', 'a') as f:\n"
        "    f.write('ORACLE_CHAIN 0 forged\\nORACLE_CHAIN_END deadbeef\\n')\nos._exit(1)\n",
        # the nonce is not in argv: only the probe's own path is left there
        "import os, sys\nsys.stderr.write(repr(sys.argv))\n"
        "os._exit(1 if len(sys.argv) == 1 else 2)\n",
    ],
)
def test_a_base_that_forges_a_chain_is_not_taken_for_teeth(tmp_path, probe):
    result = base_result(tmp_path, FIXED, probe)
    assert (result.kind, result.code) == (UNCOMPARABLE, BASE_NO_OUTPUT)


ORIG_ARGV_FORGERY = (
    "import os, sys\n"
    "args = sys.orig_argv\n"
    "chain, nonce_file = args[-2], args[-1]\n"
    "try:\n    nonce = open(nonce_file).read().strip()\nexcept OSError:\n    nonce = 'guess'\n"
    "with open(chain, 'w') as f:\n"
    "    f.write('ORACLE_CHAIN 0 forged\\nORACLE_CHAIN_END ' + nonce + '\\n')\n"
    "os._exit(1)\n"
)


@sandbox
def test_a_probe_reading_sys_orig_argv_cannot_forge_the_chain_record(tmp_path):
    """The nonce is in no argv entry: the file named there is deleted before the probe runs."""
    result = base_result(tmp_path, FIXED, ORIG_ARGV_FORGERY)
    assert (result.kind, result.code) == (UNCOMPARABLE, BASE_NO_OUTPUT)


@sandbox
def test_the_chain_names_a_class_only_when_it_is_the_class(tmp_path):
    posing = (
        "class KeyError(Exception):\n    pass\n\n\n"
        "KeyError.__module__ = 'builtins'\nKeyError.__qualname__ = 'KeyError'\n"
        "raise KeyError('x')\n"
    )
    out = run(tmp_path, FIXED, posing)
    assert out.chain is not None
    text = out.chain.decode()
    assert "builtins.KeyError" not in text
    assert "<unresolved>" in text
